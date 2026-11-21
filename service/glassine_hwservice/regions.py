# SPDX-License-Identifier: GPL-2.0-or-later
"""Writing regions (spec 005): fields on an app surface, each with its own area, ruled lines,
field type, languages, reading and neatening, whose reading is the field's value
(docs/service.md, Writing regions; docs/pen-interface.md).

A region holds strokes in its own coordinates (the compositor routes each stroke to the region
it starts in and moves it there), and words:

- **read** words: the current reading of the region's free strokes (those of no kept word),
  made again whenever its writing changes;
- **kept** words: words the person confirmed (a pick, an acceptance, a word left unchanged by an
  edit) with their ink, which no later reading changes (FR-026);
- **set** words: words the person typed (an edit), with no ink: their shape and place come from
  the person's font (the recognizer's `set`) at the person's size on the region's line (FR-018
  to FR-021).

The value is the words in reading order: lines top to bottom, words left to right; a caret
gesture places the writing after it where it points. With each change of it the region sends
the value whole, which strokes make each word (and how far kept ink moved along its line), the
shape of each set word, the strokes that left the region, and the saved state: the kept and set
words and their places, opaque to the app (FR-008 to FR-010). The reading of a region is one
recognizer session, begun while it is written in and ended at the pause after it (FR-007: an
empty or idle region holds none). Only raw pen input from the compositor becomes a sample, and
only words with ink of their own that fits their text (FR-014); ink an app hands back (stored
ink) never does."""
import asyncio
import base64
import difflib
import hashlib
import json
import math

from . import area, perms
from .engine import Error, esc, map_align, parse_text, points

READ_MS = 250
PAUSE_MS = 900
CANDIDATES = 5
FIT_MIN = 0.6           # a word's ink fits its text: the recognizer's prompted check (align SCORE)
STATE_VERSION = 1
SMALL = set("acemnorsuvwxz")
DESC = set("gjpqy")


# ---------------------------------------------------------------------- geometry

def _median(v):
    v = sorted(v)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def fingerprint(pts):
    """a stroke named by its points (x and y as the compositor sends them): the saved state
    finds its words' strokes again among strokes handed back, whatever their numbers"""
    h = hashlib.sha1(";".join("%.3f,%.3f" % (p[0], p[1]) for p in pts).encode()).hexdigest()
    return h[:16]


def tops(text):
    """a word's top and bottom in x-heights, from its letters (small letters 0 to 1, tall
    letters, capitals and digits to about 1.6, descenders to -0.6)"""
    top, bot = 0.0, 0.0
    for c in text:
        if c in SMALL:
            t = 1.0
        elif c in "ij":
            t = 1.45
        elif c == "t":
            t = 1.35
        elif c.isalnum():
            t = 1.6
        else:
            continue
        top = max(top, t)
        if c in DESC:
            bot = min(bot, -0.6)
    return top, bot


def split_words(text, align, sizes):
    """the reading's words with their strokes: [(text, strokes)]. Each stroke goes to the one
    word that uses most of it (a stroke the alignment shares between two words, a long
    connecting stroke, isn't the reason to make them one); a word left with no stroke joins
    the word before it (else after it). sizes: stroke number -> its number of points"""
    per = []                    # per character: {stroke: weight}
    for e in align.split(";") if align else []:
        d = {}
        for part in e.split(","):
            if not part:
                continue
            n, _, rng = part.partition(":")
            n = int(n)
            if rng:
                a, _, b = rng.partition("-")
                w = int(b) - int(a) + 1
            else:
                w = sizes.get(n, 1)
            d[n] = d.get(n, 0) + w
        per.append(d)
    per += [{}] * (len(text) - len(per))
    ws, cur, weight = [], [], {}
    for c, d in zip(text, per):
        if c == " ":
            if cur:
                ws.append(["".join(cur), weight])
            cur, weight = [], {}
            continue
        cur.append(c)
        for n, w in d.items():
            weight[n] = weight.get(n, 0) + w
    if cur:
        ws.append(["".join(cur), weight])
    best = {}
    for i, (_, weight) in enumerate(ws):
        for n, w in weight.items():
            if n not in best or w > best[n][0]:
                best[n] = (w, i)
    out = [[t, {n for n, (_, j) in best.items() if j == i}] for i, (t, _) in enumerate(ws)]
    merged = []
    for t, ks in out:
        if not ks and merged:
            merged[-1][0] += " " + t
        elif merged and not merged[-1][1]:
            merged[-1] = [merged[-1][0] + " " + t, ks]
        else:
            merged.append([t, ks])
    return [(t, ks) for t, ks in merged if ks]


class Word:
    """a word of the value: ink (ks, the strokes that make it) or set (no ink: place, shape)"""
    __slots__ = ("text", "ks", "kind", "dx", "place", "shape", "foreign")

    def __init__(self, text, ks=(), kind="read", dx=0.0, place=None):
        self.text = text
        self.ks = tuple(sorted(ks))
        self.kind = kind            # read, kept (confirmed or left by an edit), set (typed)
        self.dx = dx                # kept ink moved along its line (FR-021)
        self.place = place          # set: (x, base, size)
        self.shape = None           # set: the recognizer's `set` (width, xh_mm, ink, strokes)
        self.foreign = False        # some of its ink was handed back by the app: never a sample

    @property
    def ink(self):
        return bool(self.ks)


class Region:
    def __init__(self, surface, rid):
        self.surface, self.rid = surface, rid
        self.pen = surface.pen
        self.app = surface.app
        self.read_on, self.neaten, self.strength = False, False, 0.0
        self.purpose, self.hints, self.before, self.langs = 0, 0, "", ""
        self.units = "device"
        self.guides = (0.0, 0.0)
        self.size = None            # (w, h): the region's area in its own coordinates
        self.mode = "ink"
        self.rate = 0
        self.strokes = {}           # k -> points (region coordinates)
        self.order = []
        self.foreign = set()        # strokes the app handed back (stored ink)
        self.gone = set()           # strokes that left the region, not yet told
        self.gestures = set()
        self.words = []             # kept and set words
        self.read = []              # read words: the reading of the free strokes
        self.caret = None           # (x, y, after): writing after stroke `after` goes there
        self.session = None         # the engine generation the reading session was begun in
        self.num = {}               # k -> the session's stroke number
        self.area_sent = None
        self.line_read = {}         # (line's strokes, lengths) -> (text, align)
        self.neat_sent = {}
        self.offered = {}           # value serial -> (items, others, of): its candidates
        self.history = {}           # value serial -> (its read words [(text, strokes)], the strokes it was read from)
        self.serial = 0             # the newest value's
        self.sent = None            # the last value message (to send only changes)
        self.timers = []
        self.lock = asyncio.Lock()
        self.shapes = {}            # text -> the recognizer's `set` reply
        self.begun = 0

    # ------------------------------------------------------------------ names, choices

    @property
    def name(self):
        """the reading session's name: new with each session begun, so a session ended late
        (a region forgotten and declared again) never ends its successor"""
        return "g%d-%d.%d" % (self.surface.sid, self.rid, self.begun)

    begun_all = 0

    def private(self):
        from .pen import HINTS, PRIVATE, PRIVATE_HINTS, PURPOSES
        return (PURPOSES[self.purpose] if self.purpose < len(PURPOSES) else "") in PRIVATE or \
            any(self.hints >> HINTS.index(h) & 1 for h in PRIVATE_HINTS)

    def active(self):
        return (self.neaten or self.read_on) and not self.private()

    def set(self, m):
        was = (self.neaten, self.strength, self.read_on)
        fld = (self.purpose, self.hints, self.before, self.langs, self.units)
        geo = (self.guides, self.size)
        self.neaten = bool(m.get("neaten", self.neaten))
        self.strength = min(1.0, max(0.0, float(m.get("strength", self.strength))))
        self.read_on = bool(m.get("reading", self.read_on))
        self.purpose = int(m.get("purpose", self.purpose))
        self.hints = int(m.get("hints", self.hints))
        self.before = str(m.get("before", self.before))[-4000:]
        self.langs = str(m.get("languages", self.langs))
        self.units = str(m.get("units", self.units)) or "device"
        g = m.get("guides")
        if isinstance(g, list) and len(g) == 2:
            self.guides = (max(0.0, float(g[0])), float(g[1]))
        sz = m.get("size")
        if isinstance(sz, list) and len(sz) == 2 and sz[0] > 0 and sz[1] > 0:
            self.size = (float(sz[0]), float(sz[1]))
        if m.get("mode") in ("ink", "text"):
            self.mode = m["mode"]
            if self.mode == "text":
                self.cancel()
        if self.private():
            self.forget()                   # nothing of a private region stays here
            return
        if fld != (self.purpose, self.hints, self.before, self.langs, self.units):
            # a new field type or languages applies to writing that follows (FR-002): the
            # session is begun again for the strokes still being read
            self.end_session()
        if geo != (self.guides, self.size):
            # the region moved or was resized: its strokes and words stay, nothing is read
            # again for it (FR-003, FR-004); only a session begun from now on has the new area
            self.area_sent = None
        if (self.neaten, self.strength, self.read_on) != was and self.active() and self.order:
            self.soon(0)

    # ------------------------------------------------------------------ strokes in

    def stroke(self, k, pts, foreign=False):
        if not self.active() or self.mode != "ink" or k in self.strokes:
            return
        self.strokes[k] = pts
        self.order.append(k)
        if foreign:
            self.foreign.add(k)
        self.soon(READ_MS)

    def clear(self):
        """the app is done with the region's writing: it is forgotten"""
        gone = set(self.order) - self.gone
        self.forget()
        self.gone = gone

    def cancel(self):
        for t in self.timers:
            t.cancel()
        self.timers = []

    def end_session(self):
        if self.session is not None and self.session == self.pen.engine.gen:
            self.pen.spawn(self.pen.engine.ask(self.app, "end", self.name))
        self.session = None
        self.num = {}
        self.area_sent = None

    async def close_session(self):
        if self.session is not None and self.session == self.pen.engine.gen:
            await self.pen.engine.ask(self.app, "end", self.name)
        self.session = None
        self.num = {}
        self.area_sent = None

    def forget(self):
        self.cancel()
        self.end_session()
        self.strokes, self.order, self.foreign, self.gone, self.gestures = {}, [], set(), set(), set()
        self.words, self.read, self.caret = [], [], None
        self.line_read, self.neat_sent, self.offered, self.history, self.sent = {}, {}, {}, {}, None

    def soon(self, ms):
        self.cancel()
        loop = asyncio.get_running_loop()
        self.timers = [loop.call_later(ms / 1000, lambda: self.pen.spawn(self.update())),
                       loop.call_later(max(ms, PAUSE_MS) / 1000, lambda: self.pen.spawn(self.pause()))]

    # ------------------------------------------------------------------ what the region holds

    def sizes(self):
        return {k: len(p) for k, p in self.strokes.items()}

    def kept_ks(self):
        return {k for w in self.words for k in w.ks}

    def free(self):
        """strokes of no kept word, gestures and strokes that left out, in writing order"""
        kept = self.kept_ks()
        return [k for k in self.order if k not in kept and k not in self.gestures and k in self.strokes]

    def own_units(self):
        return self.units

    def session_units(self, ks):
        if not self.rate:
            self.rate = area.rate([self.strokes[k] for k in ks])
        return area.with_rate(self.own_units(), self.rate)

    def area_of(self, ks):
        """the area of a session holding strokes ks: the region's own box (its area in its own
        coordinates), else the writing's; its ruled line, else none; its guide line, else that
        of one line of writing (FR-028: never one from another region)"""
        from .pen import lines
        held = [self.strokes[k] for k in ks]
        if not held:
            return None
        bx = (0, 0, self.size[0], self.size[1]) if self.size else area.box(held)
        if self.guides[0] > 0:
            return area.text(bx, self.guides[0], self.guides[1])
        bs = area.base(held) if len(lines(dict(enumerate(held)))) == 1 else None
        return area.text(bx, 0, bs)

    async def begin(self, name, ks):
        from .pen import field
        e = self.pen.engine
        r = await e.ask(self.app, "begin", name, self.langs or "-", field(self.purpose, self.hints),
                        self.session_units(ks), esc(self.before))
        if r[1] != "ok":
            return False
        pr = area.prior(self.own_units())
        if pr:
            await e.ask(self.app, "prior", name, area._num(pr))
        return True

    async def temp(self, name, ks):
        """a session of its own holding strokes ks with their area; False when private"""
        e = self.pen.engine
        if not await self.begin(name, ks):
            return False
        for k in ks:
            await e.ask(self.app, "stroke", name, points(self.strokes[k]))
        a = self.area_of(ks)
        if a is not None:
            await e.ask(self.app, "area", name, a)
        return True

    async def ensure(self, free):
        """the reading session holding exactly the free strokes"""
        e = self.pen.engine
        if self.session != e.gen:
            Region.begun_all += 1
            self.begun = Region.begun_all
            if not await self.begin(self.name, free):
                return False
            self.session, self.num, self.area_sent = e.gen, {}, None
        extra = [k for k in self.num if k not in free]
        if extra:
            await e.ask(self.app, "remove", self.name, ",".join(str(self.num[k]) for k in extra))
            for k in extra:
                del self.num[k]
        for k in free:
            if k not in self.num:
                r = await e.ask(self.app, "stroke", self.name, points(self.strokes[k]))
                self.num[k] = int(r[2])
        a = self.area_of(free)
        if a != self.area_sent and a is not None:
            await e.ask(self.app, "area", self.name, a)
            self.area_sent = a
        return True

    # ------------------------------------------------------------------ reading

    async def update(self):
        async with self.lock:
            if self.active() and self.mode == "ink":
                await self._update()

    async def _update(self, count=1):
        """read the free strokes: gestures act, the read words are made again; the value
        and the neatened strokes follow. Returns the main reading (for the candidates)"""
        e = self.pen.engine
        rd = []
        for _ in range(8):
            free = self.free()
            if not free:
                self.read = []
                break
            if not await self.ensure(free):
                return []
            r = parse_text(await e.ask(self.app, "read", self.name, str(count), "detail"))
            if isinstance(r, tuple):
                await self.gesture(r[1])
                continue
            back = {v: k for k, v in self.num.items()}
            rd = [(t, s, map_align(a, back), f, ok) for t, s, a, f, ok in r]
            await self.make_read(free, rd)
            break
        if self.read_on:
            await self.deliver()
        if self.neaten:
            await self.neaten_all()
        return rd

    async def make_read(self, free, rd):
        """the read words from the reading of the free strokes (one line), else line by line"""
        from .pen import lines
        ls = lines({k: self.strokes[k] for k in free}, self.guides)
        out = []
        if len(ls) <= 1:
            if rd:
                out = [Word(t, ks) for t, ks in split_words(rd[0][0], rd[0][2], self.sizes())]
        else:
            e = self.pen.engine
            for ks in ls:
                key = (tuple(ks), tuple(len(self.strokes[k]) for k in ks))
                if key not in self.line_read:
                    w = "h%d-%d" % (self.surface.sid, self.rid)
                    try:
                        if not await self.temp(w, ks):
                            continue
                        r = parse_text(await e.ask(self.app, "read", w, "1", "detail"))
                    finally:
                        await e.ask(self.app, "end", w)
                    if isinstance(r, tuple) or not r:
                        self.line_read[key] = ("", "")
                        continue
                    self.line_read[key] = (r[0][0], map_align(r[0][2], dict(enumerate(ks))))
                t, a = self.line_read[key]
                if t:
                    out += [Word(x, s) for x, s in split_words(t, a, self.sizes())]
            keep = {(tuple(ks), tuple(len(self.strokes[k]) for k in ks)) for ks in ls}
            self.line_read = {k: v for k, v in self.line_read.items() if k in keep}
        for w in out:
            w.foreign = any(k in self.foreign for k in w.ks)
        self.read = out

    async def gesture(self, f):
        """KIND N BOX COVERED CHARS (docs/recognizer.md): the gesture stroke leaves the region;
        a strike or scratch deletes the words of the value it covers, a caret marks where the
        writing after it goes (FR-012). It never acts on another region"""
        kind, n, box, covered = f[0], int(f[1]), [float(v) for v in f[2].split(",")], f[3]
        back = {v: k for k, v in self.num.items()}
        g = back.get(n)
        if g is None:
            return
        del self.num[g]
        self.gestures.add(g)
        self.gone.add(g)
        self.pen.send({"op": "region_gesture", "s": self.surface.sid, "r": self.rid, "serial": self.serial + 1,
                       "kind": kind, "strokes": [g]})
        cov = {back[int(c)] for c in covered.split(",") if c not in ("", "-") and int(c) in back}
        if kind in ("strike", "scratch"):
            hit = set(cov)
            for w in self.read:
                if set(w.ks) & cov:
                    hit |= set(w.ks)
            keep = []
            for w in self.words:
                if (set(w.ks) & cov) or self.covers(box, w):
                    hit |= set(w.ks)
                    continue
                keep.append(w)
            self.words = keep
            self.read = [w for w in self.read if not set(w.ks) & hit]
            self.drop(hit)
        elif kind == "caret":
            self.caret = ((box[0] + box[2]) / 2, box[1], g)

    def covers(self, box, w):
        """a strike's box over most of a word's width and across its height"""
        x0, y0, x1, y1 = self.word_box(w)
        ov = min(x1, box[2]) - max(x0, box[0])
        return ov > 0.5 * max(x1 - x0, 1e-9) and box[1] <= y1 and box[3] >= y0

    def drop(self, ks):
        """strokes that leave the region for good (FR-010)"""
        for k in ks:
            if k in self.strokes:
                self.gone.add(k)
                del self.strokes[k]
        self.order = [k for k in self.order if k in self.strokes]

    # ------------------------------------------------------------------ the layout

    def word_box(self, w):
        if w.ink:
            xs = [p[0] for k in w.ks for p in self.strokes.get(k, [])]
            ys = [p[1] for k in w.ks for p in self.strokes.get(k, [])]
            if not xs:
                return (0, 0, 0, 0)
            return (min(xs) + w.dx, min(ys), max(xs) + w.dx, max(ys))
        x, base, size = w.place
        width = (w.shape[0] if w.shape else len(w.text)) * size
        return (x, base - 1.6 * size, x + width, base + 0.6 * size)

    def xh(self):
        """the size of the person's writing in this region: per word with ink, its height
        over its letters' (in x-heights), the median; else their letters in millimetres; else
        the typical size (FR-021, FR-028)"""
        v = []
        for w in self.words + self.read:
            if not w.ink:
                continue
            t, b = tops(w.text)
            if t - b <= 0:
                continue
            x0, y0, x1, y1 = self.word_box(w)
            v.append((y1 - y0) / (t - b))
        m = _median(v)
        if m:
            return m
        mm = self.person_mm
        pm = area.per_mm(self.own_units()) or area.DEFAULT_PX_PER_MM
        return (mm or area.TYPICAL_XH_MM) * pm

    person_mm = 0.0

    def base_of(self, w):
        """the line a word sits on"""
        if not w.ink:
            return w.place[1]
        x0, y0, x1, y1 = self.word_box(w)
        if self.guides[0] > 0:
            sp, first = self.guides
            i = max(0, math.ceil(((y0 + y1) / 2 - first - 0.25 * sp) / sp))
            return first + i * sp
        lows = [max(p[1] for p in self.strokes[k]) for k in w.ks if k in self.strokes]
        b = _median(lows) if lows else y1
        _, bot = tops(w.text)
        return min(b, y1 + bot * (y1 - y0) / max(1e-9, 1.6 - bot)) if bot < 0 else b

    def first_line(self, size):
        if self.guides[0] > 0:
            return self.guides[1]
        bases = [self.base_of(w) for w in self.words + self.read if w.ink]
        return min(bases) if bases else 1.8 * size

    def ordered(self):
        """the value's words in reading order: [[word]] by line"""
        ws = []
        for w in self.words + self.read:
            x0, y0, x1, y1 = self.word_box(w)
            y = self.base_of(w)
            if self.caret and w.ink and all(k > self.caret[2] for k in w.ks):
                ws.append((self.caret[1], self.caret[0], x0, w))
            else:
                ws.append((y, x0, x0, w))
        if not ws:
            return []
        size = self.xh()
        gap = self.guides[0] * 0.5 if self.guides[0] > 0 else 1.5 * size
        ws.sort(key=lambda t: t[0])
        lines, last = [], None
        for y, x, x2, w in ws:
            if last is None or y - last > gap:
                lines.append([])
            lines[-1].append((x, x2, w))
            last = y
        return [[w for _, _, w in sorted(ln, key=lambda t: (t[0], t[1]))] for ln in lines]

    def value(self):
        return "\n".join(" ".join(w.text for w in ln) for ln in self.ordered())

    def layout(self):
        """the value's text and its words: an ink word {start, length, strokes, dx, dy}, a
        set word {start, length, box, path}; start and length in UTF-8 bytes of the text"""
        text, out = b"", []
        for li, ln in enumerate(self.ordered()):
            if li:
                text += b"\n"
            for i, w in enumerate(ln):
                if i:
                    text += b" "
                t = w.text.encode()
                d = {"start": len(text), "length": len(t)}
                text += t
                if w.ink:
                    d.update(strokes=list(w.ks), dx=round(w.dx, 3), dy=0)
                else:
                    d.update(self.set_shape(w))
                out.append(d)
        return text.decode(), out

    def set_shape(self, w):
        """a set word's outline and box in region coordinates: its strokes (the recognizer's
        `set`, in x-heights, y up) drawn with the font's pen as filled polygons, a quad per
        segment and an octagon per point, all turning the same way (non-zero fill)"""
        x, base, size = w.place
        width, _, ink, st = w.shape or (len(w.text), 0, 0, [])
        r = max(ink if ink > 0 else 0.12, 0.04) * size / 2
        path, xs, ys = [], [], []

        def poly(pts):
            a = sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(pts, pts[1:] + pts[:1]))
            if a < 0:
                pts = pts[::-1]
            path.extend(["M", round(pts[0][0], 3), round(pts[0][1], 3)])
            for p in pts[1:]:
                path.extend(["L", round(p[0], 3), round(p[1], 3)])
            path.append("Z")
            xs.extend(p[0] for p in pts)
            ys.extend(p[1] for p in pts)
        for s_ in st:
            pts = [(x + px * size, base - py * size) for px, py in s_]
            for p in pts:
                poly([(p[0] + r * math.cos(math.pi * j / 4), p[1] + r * math.sin(math.pi * j / 4)) for j in range(8)])
            for p, q in zip(pts, pts[1:]):
                dx, dy = q[0] - p[0], q[1] - p[1]
                n = math.hypot(dx, dy)
                if n <= 0:
                    continue
                nx, ny = -dy / n * r, dx / n * r
                poly([(p[0] + nx, p[1] + ny), (q[0] + nx, q[1] + ny), (q[0] - nx, q[1] - ny), (p[0] - nx, p[1] - ny)])
        if xs:
            box = [min(xs), min(ys), max(xs), max(ys)]
        else:
            box = [x, base - size, x + width * size, base]
        return {"box": [round(v, 3) for v in box], "path": path}

    def state(self):
        """the saved state (FR-010, FR-025): the kept and set words and their places"""
        ws = []
        for w in self.words:
            if w.ink:
                ws.append({"t": w.text, "k": [fingerprint(self.strokes[k]) for k in w.ks if k in self.strokes],
                           "dx": round(w.dx, 3)})
            else:
                ws.append({"t": w.text, "at": [round(v, 3) for v in w.place]})
        s = json.dumps({"v": STATE_VERSION, "w": ws}, separators=(",", ":"), ensure_ascii=False)
        return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

    async def deliver(self):
        """the value whole, its word layout, the strokes that left, the saved state; only
        when something changed. Each value is kept a while with the writing it was read from,
        for the reports that name it (FR-013)"""
        text, words = self.layout()
        m = {"text": text, "words": words, "state": self.state()}
        gone = sorted(self.gone)
        if self.sent == m and not gone:
            return
        self.serial += 1
        self.sent = m
        self.gone = set()
        self.history[self.serial] = ([(w.text, w.ks) for w in self.read],
                                     {k for w in self.read for k in w.ks})
        for old in [k for k in self.history if k <= self.serial - 16]:
            del self.history[old]
            self.offered.pop(old, None)
        self.pen.send(dict({"op": "region_value", "s": self.surface.sid, "r": self.rid, "serial": self.serial,
                            "gone": gone}, **m))

    # ------------------------------------------------------------------ the pause

    async def pause(self):
        async with self.lock:
            if not self.read_on or not self.active() or self.mode != "ink":
                return
            await self._pause()
            await self.close_session()  # an idle region holds no session (FR-007)

    async def _pause(self):
        from .pen import lines
        free = self.free()
        if not free:
            return
        ls = lines({k: self.strokes[k] for k in free}, self.guides)
        line = next((ks for ks in ls if free[-1] in ks), free) if len(ls) > 1 else free
        e = self.pen.engine
        if len(ls) <= 1:
            rd = await self._update(CANDIDATES)
        else:
            await self._update()
            w = "h%d-%d" % (self.surface.sid, self.rid)
            try:
                if not await self.temp(w, line):
                    return
                r = parse_text(await e.ask(self.app, "read", w, str(CANDIDATES), "detail"))
            finally:
                await e.ask(self.app, "end", w)
            rd = [] if isinstance(r, tuple) else [(t, s, map_align(a, dict(enumerate(line))), f, ok)
                                                  for t, s, a, f, ok in r]
        line = set(line) & set(self.free())
        others = [w for w in self.read if not set(w.ks) & line]
        items = []
        for t, _, a, f, ok in rd:
            cand = [Word(x, ks) for x, ks in split_words(t, a, self.sizes())]
            saved = self.read
            self.read = others + cand
            v = self.value()
            self.read = saved
            items.append({"text": v, "from": f, "fit": ok, "words": [(w.text, list(w.ks)) for w in cand]})
        await self.deliver()
        self.offered[self.serial] = (items, others, set(k for w in self.read for k in w.ks) | line)
        self.pen.send({"op": "region_candidates", "s": self.surface.sid, "r": self.rid, "serial": self.serial,
                       "items": [{k: v for k, v in i.items() if k != "words"} for i in items]})

    # ------------------------------------------------------------------ reports, edits, samples

    async def report(self, m):
        async with self.lock:
            await self._report(m)

    async def _report(self, m):
        """a report on the value serial (accepted, corrected) or its candidates (picked)"""
        serial, action = int(m.get("serial", 0)), m.get("action")
        if serial not in self.history or (action == "picked" and serial not in self.offered):
            result = "stale"
        elif action == "discarded":
            result = "not_kept"
        else:
            snap, of = self.history[serial]
            held = set(self.strokes)
            if action == "picked":
                items, others, of = self.offered[serial]
                rank = int(m.get("rank", 0))
                if not 0 <= rank < len(items):
                    result = "stale"
                    self.pen.send({"op": "region_report", "s": self.surface.sid, "r": self.rid, "serial": serial,
                                   "result": result})
                    return
                ws = [(w.text, w.ks) for w in others] + [(t, tuple(sorted(ks))) for t, ks in items[rank]["words"]]
                result = await self.keep(ws, of, "picked")
            elif action == "accepted":
                result = await self.keep(snap, of, "confirmed")
            else:
                result = await self.edit(str(m.get("text", "")), of & held)
            await self.after_change()
        self.pen.send({"op": "region_report", "s": self.surface.sid, "r": self.rid, "serial": serial,
                       "result": result})

    async def keep(self, ws, of, source):
        """the reported words [(text, strokes)] kept, those whose strokes are all still the
        region's free writing, with their samples; read words sharing their strokes go (a
        report applies to the writing it was read from, FR-013)"""
        free = set(self.free())
        new = [Word(t, ks, "kept") for t, ks in ws if ks and set(ks) <= free and set(ks) <= of]
        taken = {k for w in new for k in w.ks}
        for w in new:
            w.foreign = any(k in self.foreign for k in w.ks)
        self.read = [w for w in self.read if not set(w.ks) & taken]
        self.words += new
        return await self.samples([(w, w.text) for w in new], source)

    async def edit(self, text, of=None):
        """the value made text (a correction, or a text edit back in ink mode: FR-017): words
        left unchanged keep their ink and are kept; changed and added words are set in the
        person's font at their place, kept ink after them on their line moving by the
        difference (FR-018, FR-021); removed words' ink leaves; when no word keeps its ink, the
        whole value is set (FR-020). Samples for the words whose ink fits their new text. of:
        the strokes the reported value was read from (FR-013), None: all"""
        old = self.ordered()
        if of is not None:
            old = [ln for ln in ([w for w in ln if not w.ink or set(w.ks) <= of] for ln in old) if ln]
        a, b = [], []
        for i, ln in enumerate(old):
            a += ([None] if i else []) + ln
        for i, ln in enumerate(text.split("\n")):
            b += ([None] if i else []) + ln.split()
        sm = difflib.SequenceMatcher(a=[w.text if w else "\n" for w in a], b=[t or "\n" for t in b], autojunk=False)
        plan = []               # per token of the text: ("keep", word), ("swap", ink word, text), ("add", text), None: a new line
        drop, learn = set(), []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                plan += [("keep", w) if w else None for w in a[i1:i2]]
                continue
            olds = iter([w for w in a[i1:i2] if w])
            for t in b[j1:j2]:
                if t is None:
                    plan.append(None)
                    continue
                w = next(olds, None)
                if w is not None and w.ink:
                    plan.append(("swap", w, t))
                    learn.append((w, t))
                    drop |= set(w.ks)
                else:
                    plan.append(("add", t))
            for w in olds:
                drop |= set(w.ks)
        kept = [p[1] for p in plan if p and p[0] == "keep" and p[1].ink]
        fallback = not kept and any(w and w.ink for w in a)
        # samples first, while the replaced ink is still there (FR-014, FR-017): the words
        # read and left as they were, and the words whose ink was given another text
        result = await self.samples([(w, w.text) for w in kept if w.kind == "read"] + learn, "corrected")
        await self.person()
        size = self.xh()
        gap, step = self.word_gap(size), self.line_step(size)
        if fallback:
            # FR-020: the words can't be matched to the ink: the whole value is set
            drop |= {k for w in a if w for k in w.ks}
            plan = [("add", t) if t is not None else None for t in b]
        out, prev, shift = [], None, 0.0
        base = self.first_line(size)
        lines_ = [[]]
        for p in plan:
            if p is None:
                lines_.append([])
            else:
                lines_[-1].append(p)
        for li, ln in enumerate(lines_):
            shift = 0.0
            if li:
                base = (self.base_of(prev) if prev else base) + step
                prev = None
            for i, p in enumerate(ln):
                if p[0] == "keep":
                    w = p[1]
                    if w.ink:
                        w.dx += shift
                        w.kind = "kept"
                    else:
                        x, bb, s = w.place
                        w.place = (x + shift, bb, s)
                    out.append(w)
                    prev = w
                    continue
                t = p[-1]
                shape = await self.shape(t)
                width = shape[0] * size
                if p[0] == "swap" and not fallback:
                    ox0, _, ox1, _ = self.word_box(p[1])
                    x0, bb = ox0 + shift, self.base_of(p[1])
                    shift += width - (ox1 - ox0)
                elif prev is not None:
                    x0, bb = self.word_box(prev)[2] + gap, self.base_of(prev)
                    shift += width + gap
                else:
                    nxt = next((q[1] for q in ln[i + 1:] if q[0] == "keep" and q[1].ink), None)
                    if nxt is not None and not fallback:
                        x0, bb = self.word_box(nxt)[0], self.base_of(nxt)
                        shift += width + gap
                    else:
                        x0, bb = 0.0, base
                w = Word(t, kind="set", place=(x0, bb, size))
                w.shape = shape
                out.append(w)
                prev = w
        self.drop(drop)
        later = [w for w in self.words if w not in out and not set(w.ks) & drop and w.ink and of is not None
                 and not set(w.ks) <= of]
        self.words = out + later
        self.read = []
        self.caret = None
        return result

    def word_gap(self, size):
        gs = []
        for ln in self.ordered():
            ink = [w for w in ln if w.ink]
            for a, b in zip(ink, ink[1:]):
                g = self.word_box(b)[0] - self.word_box(a)[2]
                if g > 0:
                    gs.append(g)
        return _median(gs) or 0.8 * size

    def line_step(self, size):
        if self.guides[0] > 0:
            return self.guides[0]
        bases = sorted({round(self.base_of(w), 1) for w in self.words + self.read if w.ink})
        steps = [b - a for a, b in zip(bases, bases[1:]) if b - a > size]
        return _median(steps) or 3.0 * size

    async def person(self):
        """the person's x-height in millimetres, from the letter model (the `set` reply)"""
        try:
            r = await self.shape(" ")
            Region.person_mm = r[1] or 0.0
        except Error:
            pass

    async def shape(self, text):
        """the recognizer's `set` of a text: (width, xh_mm, ink, strokes) in x-heights, y up"""
        if text not in self.shapes:
            r = await self.pen.engine.ask(self.app, "set", esc(text))
            k = int(r[5])
            st = [[tuple(float(v) for v in p.split(",")) for p in s.split(" ")] for s in r[6:6 + k]]
            self.shapes[text] = (float(r[2]), float(r[3]), float(r[4]), st)
        return self.shapes[text]

    async def samples(self, pairs, source):
        """the words with ink of their own, raw pen input only, that fit their text: each
        stored as a sample when the person lets this app learn (FR-014). The result of a report"""
        pairs = [(w, t) for w, t in pairs if w.ink and not w.foreign and t and all(k in self.strokes for k in w.ks)
                 and not any(k in self.foreign for k in w.ks)]
        if not pairs:
            return "not_kept"
        if perms.state(self.app, "learn") != "allowed":
            return "not_allowed"
        e = self.pen.engine
        stored = False
        name = "c%d-%d" % (self.surface.sid, self.rid)
        for w, t in pairs:
            try:
                if not await self.temp(name, list(w.ks)):
                    continue
                try:
                    a = await e.ask(self.app, "align", name, esc(t))
                    if a[1] == "short" or float(a[1]) < FIT_MIN:
                        continue
                    r = await e.ask(self.app, "confirm", name, source, esc(t))
                    stored = stored or r[1] == "ok"
                finally:
                    await e.ask(self.app, "end", name)
            except Error:
                continue
        return "stored" if stored else "not_kept"

    # ------------------------------------------------------------------ text edits, stored ink

    async def set_text(self, text):
        """the text the person edited in text mode, as the value back in ink mode (FR-017)"""
        async with self.lock:
            if self.private():
                return
            self.cancel()
            if self.free():
                await self._update()
            await self.edit(text)
            await self.after_change()

    async def after_change(self):
        """the value and the neatened strokes after a report or an edit (writing the report
        didn't cover is read again, where it is)"""
        if self.free() and self.active():
            await self._update()
            return
        if self.read_on:
            await self.deliver()
        if self.neaten:
            await self.neaten_all()

    async def restore(self, strokes, state):
        """stored ink handed back (FR-025): its strokes, and with a saved state its kept and set
        words; the rest is read with the model there is now (FR-026)"""
        async with self.lock:
            self.forget()
            if self.private():
                return
            for k, pts in strokes:
                self.strokes[k] = pts
                self.order.append(k)
                self.foreign.add(k)
            by_fp = {}
            for k in self.order:
                by_fp.setdefault(fingerprint(self.strokes[k]), []).append(k)
            for d in decode(state):
                if "k" in d:
                    ks = []
                    for fp in d["k"]:
                        cand = by_fp.get(fp)
                        if cand:
                            ks.append(cand.pop(0))
                    if len(ks) != len(d["k"]) or not ks:
                        continue        # its ink isn't all there: read again
                    w = Word(d["t"], ks, "kept", float(d.get("dx", 0)))
                    w.foreign = True
                    self.words.append(w)
                elif "at" in d and len(d["at"]) == 3:
                    w = Word(d["t"], kind="set", place=tuple(float(v) for v in d["at"]))
                    try:
                        w.shape = await self.shape(d["t"])
                    except Error:
                        w.shape = None
                    self.words.append(w)
            if self.active():
                await self._update()
                if self.read_on and not self.free():
                    await self.deliver()

    # ------------------------------------------------------------------ neatening

    async def neaten_all(self):
        """each word with ink neatened in a session of its own, with the area of its line;
        never with its text: kept ink stays ink at every strength (FR-019)"""
        e = self.pen.engine
        for w in self.words + self.read:
            ks = [k for k in w.ks if k in self.strokes]
            if not ks:
                continue
            key = (tuple(ks), self.strength, tuple(len(self.strokes[k]) for k in ks))
            if self.neat_sent.get(key):
                continue
            name = "n%d-%d" % (self.surface.sid, self.rid)
            try:
                if not await self.temp(name, ks):
                    return
                r = await e.ask(self.app, "neaten", name, repr(self.strength))
            finally:
                await e.ask(self.app, "end", name)
            n = int(r[3])
            for i in range(n):
                j, how, pts = int(r[4 + 3 * i]), r[5 + 3 * i], r[6 + 3 * i]
                if ks[j] not in self.strokes:
                    continue
                xy = [[float(v) for v in p.split(",")[:2]] for p in pts.split(" ")]
                self.pen.send({"op": "region_neat", "s": self.surface.sid, "r": self.rid, "k": ks[j], "how": how,
                               "points": xy})
            self.neat_sent = {k: v for k, v in self.neat_sent.items() if k[1] == self.strength}
            self.neat_sent[key] = True


def decode(state):
    """the saved state's words, [] for none or one that isn't this service's"""
    if not isinstance(state, str) or not state:
        return []
    try:
        d = json.loads(base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)).decode())
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(d, dict) or d.get("v") != STATE_VERSION or not isinstance(d.get("w"), list):
        return []
    out = []
    for w in d["w"]:
        if isinstance(w, dict) and isinstance(w.get("t"), str) and w["t"] and \
                (isinstance(w.get("k"), list) or isinstance(w.get("at"), list)):
            out.append(w)
    return out
