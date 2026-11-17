# SPDX-License-Identifier: GPL-2.0-or-later
"""The pen interface's service side (spec 004 FR-006 to FR-010, spec 003 FR-018 to FR-029).
The compositor sends each surface's choices and raw strokes (docs/service.md, The
compositor's channel); this side reads them, neatens them word by word and sends back
neatened strokes, partial readings, candidates and the answers to reports. Raw strokes reach
the app from the compositor itself, never through here.

Per surface one recognizer session (`p<surface>`), begun for the app; it holds the writing
until the app clears it or reports on it. A short pause after a stroke reads the writing
(neatening needs the reading, text or not); a longer one gives the candidates. Each word of
the best reading is neatened on its own (a short request, under the 300 ms of SC-003 also
for a long line), from its raw strokes, and only again when its strokes, its text or the
strength change. The writing is neatened line by line: by the area's guides (set_guides), else
by the lines found in it (lines()).

Every session has a writing area (area.py): the app's box and guides when it set them, else
the box of the writing held with a line found from it; the guide line is the app's, else the
one found from the line of writing the session holds. Its units are the compositor's
(device:PER_MM when it knows the output's size) with the pen's rate added once measured."""
import asyncio
import json
import math

from . import area, ident, perms

from .engine import Error, align_strokes, esc, map_align, parse_text, points

READ_MS = 250          # after a stroke: read (partial reading, neatening)
PAUSE_MS = 900         # after a stroke: the candidates
PRIVATE = {"password", "pin"}
PRIVATE_HINTS = {"sensitive_data", "hidden_text"}
PURPOSES = ("normal", "alpha", "digits", "number", "phone", "url", "email", "name", "password", "pin",
            "date", "time", "datetime", "terminal")
# zwp_text_input_v3 content hints, by bit
HINTS = ("completion", "spellcheck", "auto_capitalization", "lowercase", "uppercase", "titlecase",
         "hidden_text", "sensitive_data", "latin", "multiline")
CANDIDATES = 5
WORDS = True            # word by word; False: the whole writing in one request (measurements)


def lines(strokes, guides=None):
    """[[stroke number]] top to bottom: with guides (spacing, first) each stroke on the guide
    it is written on (its middle, a descender reaching a little below); without, strokes whose
    middles are closer than a line apart, by the writing's own size, are one line"""
    mids = {}
    for k, pts in strokes.items():
        ys = sorted(p[1] for p in pts)
        mids[k] = (ys[len(ys) // 2], ys[-1] - ys[0])
    if not mids:
        return []
    by = {}
    if guides and guides[0] > 0:
        sp, first = guides
        for k, (mid, _) in mids.items():
            by.setdefault(max(0, math.ceil((mid - first - 0.25 * sp) / sp)), []).append(k)
        return [sorted(by[i]) for i in sorted(by)]
    hs = sorted(h for _, h in mids.values())
    gap = max(hs[len(hs) // 2] * 1.5, 1e-9)          # a line: 1.5 median stroke heights
    out, last = [], None
    for k in sorted(mids, key=lambda k: mids[k][0]):
        if last is None or mids[k][0] - last > gap:
            out.append([])
        out[-1].append(k)
        last = mids[k][0]
    return [sorted(s) for s in out]


def words(text, align):
    """the reading's words: [(text, strokes)], words that share a stroke as one"""
    chars = align_strokes(align) if align else []
    groups, cur, ks = [], [], set()
    for c, s in zip(text, chars + [set()] * (len(text) - len(chars))):
        if c == " ":
            if cur:
                groups.append(("".join(cur), ks))
            cur, ks = [], set()
        else:
            cur.append(c)
            ks |= s
    if cur:
        groups.append(("".join(cur), ks))
    out = []
    for t, s in groups:
        if out and out[-1][1] & s:
            out[-1] = (out[-1][0] + " " + t, out[-1][1] | s)
        else:
            out.append((t, set(s)))
    return out


def field(purpose, hints):
    """the recognizer's FIELD for a purpose and a hints bitmask"""
    p = PURPOSES[purpose] if 0 <= purpose < len(PURPOSES) else "unknown"
    hs = [h for i, h in enumerate(HINTS) if hints >> i & 1]
    return ",".join([p] + hs)


class Surface:
    def __init__(self, pen, sid, app):
        self.pen, self.sid, self.app = pen, sid, app
        self.neaten, self.strength, self.text = False, 0.0, False
        self.purpose, self.hints, self.before, self.langs = 0, 0, "", ""
        self.units = "device"
        self.rate = 0               # the pen's points per second, once measured (area.rate)
        self.area_sent = None       # the area the main session has
        self.line = 0.0             # the line found from this surface's writing, kept (area.line)
        self.guides, self.box = (0.0, 0.0), None
        self.strokes = {}           # stroke number -> points (as the compositor sent them)
        self.order = []             # stroke numbers in writing order
        self.num = {}               # stroke number -> the session's number
        self.gestures = set()       # strokes read as a gesture: passed on unchanged
        self.session = None         # the engine generation it was begun in
        self.reading = None         # the newest reading [(text, score, align, from, ok)]
        self.read_of = None         # the strokes it was made of
        self.serial = 0
        self.offered = None         # (serial, candidates) of the newest candidates sent
        self.sent = {}              # word key -> True: its neatened strokes are out
        self.line_read = {}         # (line's strokes, their lengths) -> (text, align): its reading
        self.timers = []
        self.lock = asyncio.Lock()      # one piece of work at a time (they share the sessions)

    @property
    def name(self):
        return "p%d" % self.sid

    def private(self):
        return (PURPOSES[self.purpose] if self.purpose < len(PURPOSES) else "") in PRIVATE or \
            any(self.hints >> HINTS.index(h) & 1 for h in PRIVATE_HINTS)

    def active(self):
        return (self.neaten or self.text) and not self.private()

    # ------------------------------------------------------------------ from the compositor

    def set(self, m):
        was = (self.neaten, self.strength, self.text)
        fld = (self.purpose, self.hints, self.before, self.langs, self.units, self.guides, self.box)
        self.neaten = bool(m.get("neaten", self.neaten))
        self.strength = min(1.0, max(0.0, float(m.get("strength", self.strength))))
        self.text = bool(m.get("text", self.text))
        self.purpose = int(m.get("purpose", self.purpose))
        self.hints = int(m.get("hints", self.hints))
        self.before = str(m.get("before", self.before))[-4000:]
        self.langs = str(m.get("languages", self.langs))
        self.units = str(m.get("units", self.units)) or "device"
        g = m.get("guides")
        if isinstance(g, list) and len(g) == 2:
            self.guides = (max(0.0, float(g[0])), float(g[1]))
        b = m.get("box")
        self.box = tuple(int(v) for v in b) if isinstance(b, list) and len(b) == 4 and b[2] > b[0] and b[3] > b[1] \
            else None
        if self.private():
            self.forget()                  # nothing of a private field stays here
            return
        if not self.order:
            return
        if fld != (self.purpose, self.hints, self.before, self.langs, self.units, self.guides, self.box) \
                and self.session is not None:
            self.session = None            # the field changed: the session is begun again, as it is now
        if (self.neaten, self.strength, self.text) != was and self.active():
            self.soon(0)

    def stroke(self, k, pts):
        if not self.active():
            return
        self.strokes[k] = pts
        self.order.append(k)
        self.soon(READ_MS)

    def clear(self):
        self.forget()

    def forget(self):
        for t in self.timers:
            t.cancel()
        self.timers = []
        if self.session is not None and self.session == self.pen.engine.gen:
            self.pen.spawn(self.pen.engine.ask(self.app, "end", self.name))
        self.session = None
        self.strokes, self.order, self.num, self.gestures = {}, [], {}, set()
        self.reading = self.read_of = self.offered = None
        self.sent = {}
        self.line_read = {}
        self.area_sent = None

    # ------------------------------------------------------------------ the work

    def soon(self, ms):
        for t in self.timers:
            t.cancel()
        loop = asyncio.get_running_loop()
        self.timers = [loop.call_later(ms / 1000, lambda: self.pen.spawn(self.update())),
                       loop.call_later(max(ms, PAUSE_MS) / 1000, lambda: self.pen.spawn(self.pause()))]

    def ink(self):
        """the writing held, gestures left out"""
        return [self.strokes[k] for k in self.order if k not in self.gestures]

    def session_units(self):
        if not self.rate:
            self.rate = area.rate(self.ink())
        return area.with_rate(self.units, self.rate)

    def area_of(self, ks=None):
        """the area for a session holding strokes ks (None: all held): the app's box, else the
        writing's; the app's line, else the writing's (the last one found on this surface
        while the writing is too little to tell); the app's guide line, else that of the
        session's line of writing (none when the session holds more than one line). None
        while no line is known: the recognizer goes by the ink's own size."""
        held = self.ink()
        if not held:
            return None
        bx = self.box or area.box(held)
        if self.guides[0] > 0:
            return area.text(bx, self.guides[0], self.guides[1])
        self.line = area.line(held) or self.line
        if not self.line:
            return None
        own = held if ks is None else [self.strokes[k] for k in ks]
        bs = area.base(own) if len(lines(dict(enumerate(own)))) == 1 else None
        return area.text(bx, self.line, bs)

    async def begin(self, name):
        """a session for this surface's writing (its field and units; its area follows its
        strokes, set_area())"""
        e = self.pen.engine
        r = await e.ask(self.app, "begin", name, self.langs or "-", field(self.purpose, self.hints),
                        self.session_units(), self.before)
        return r[1] == "ok"

    async def set_area(self, name, ks=None):
        a = self.area_of(ks)
        if a is not None:
            await self.pen.engine.ask(self.app, "area", name, a)
        return a

    async def ensure(self):
        """the session, begun with every stroke held (again after the recognizer restarted)"""
        e = self.pen.engine
        if self.session == e.gen:
            missing = [k for k in self.order if k not in self.num and k not in self.gestures]
        else:
            if not await self.begin(self.name):
                return False
            self.session = e.gen
            self.num = {}
            self.area_sent = None
            missing = [k for k in self.order if k not in self.gestures]
        for k in missing:
            r = await e.ask(self.app, "stroke", self.name, points(self.strokes[k]))
            self.num[k] = int(r[2])
        if self.area_of() != self.area_sent:
            self.area_sent = await self.set_area(self.name)
        return True

    async def read(self, count):
        """the reading of the strokes held, best first; a gesture stroke is set aside"""
        e = self.pen.engine
        for _ in range(4):
            if not await self.ensure():
                return []
            r = parse_text(await e.ask(self.app, "read", self.name, str(count), "detail"))
            if isinstance(r, tuple):                       # the newest stroke is a gesture
                n = int(r[1][1])
                k = next((k for k, v in self.num.items() if v == n), None)
                if k is not None:
                    self.gestures.add(k)
                    del self.num[k]
                    if self.neaten:
                        self.pen.send({"op": "neat", "s": self.sid, "k": k, "how": "kept",
                                       "points": [p[:2] for p in self.strokes[k]]})
                continue
            back = {v: k for k, v in self.num.items()}
            return [(t, s, map_align(a, back), f, ok) for t, s, a, f, ok in r]
        return []

    async def update(self):
        async with self.lock:
            await self._update()

    async def _update(self):
        if not self.order or not self.active():
            return
        held = tuple(self.order)
        rd = await self.read(CANDIDATES if self.text else 1)
        if tuple(self.order) != held:
            return                                          # more ink came: its own update follows
        self.reading, self.read_of = rd, held
        if self.text and rd:
            self.pen.send({"op": "reading", "s": self.sid, "text": rd[0][0],
                           "strokes": [k for k in held if k not in self.gestures]})
        if self.neaten and rd:
            await self.neaten_lines(rd[0][0], rd[0][2])

    async def pause(self):
        async with self.lock:
            await self._pause()

    async def _pause(self):
        if not self.text or not self.order or self.private():
            return
        if self.read_of != tuple(self.order):
            await self._update()
            if self.read_of != tuple(self.order):
                return
        self.serial += 1
        rd = self.reading or []
        self.offered = (self.serial, rd)
        self.pen.send({"op": "candidates", "s": self.sid, "serial": self.serial,
                       "items": [{"text": t, "from": f, "fit": ok, "chars": a} for t, _, a, f, ok in rd]})

    async def neaten_lines(self, text, align):
        """each line of the writing neatened word by word: a line's words come from the reading
        of that line alone (the whole writing's when it is one line)"""
        held = tuple(self.order)
        ls = lines({k: self.strokes[k] for k in held if k not in self.gestures}, self.guides)
        if len(ls) <= 1:
            await self.neaten_words(text, align, held)
            return
        e = self.pen.engine
        for ks in ls:
            key = (tuple(ks), tuple(len(self.strokes[k]) for k in ks))
            if self.line_read.get(key) is None:
                w = "l%d" % self.sid
                if not await self.begin(w):
                    return
                try:
                    for k in ks:
                        await e.ask(self.app, "stroke", w, points(self.strokes[k]))
                    await self.set_area(w, ks)
                    r = parse_text(await e.ask(self.app, "read", w, "1", "detail"))
                finally:
                    await e.ask(self.app, "end", w)
                if isinstance(r, tuple) or not r:
                    continue
                self.line_read[key] = (r[0][0], map_align(r[0][2], dict(enumerate(ks))))
            if tuple(self.order) != held or not self.neaten:
                return
            lt, la = self.line_read[key]
            await self.neaten_words(lt, la, held, ks)
        keep = {(tuple(ks), tuple(len(self.strokes[k]) for k in ks)) for ks in ls}
        self.line_read = {k: v for k, v in self.line_read.items() if k in keep}

    async def neaten_words(self, text, align, held, line_ks=None):
        """each word neatened in a session of its own, with the area of its line (line_ks: the
        line's strokes; None: the whole writing is one line)"""
        e = self.pen.engine
        groups = words(text, align) if WORDS else [(text, set(k for k in held if k not in self.gestures))]
        for wt, ks in groups:
            ks = sorted(ks)
            if not ks:
                continue
            key = (tuple(ks), wt, self.strength, tuple(len(self.strokes[k]) for k in ks))
            if self.sent.get(key):
                continue
            w = "w%d" % self.sid
            if not await self.begin(w):
                return
            try:
                for k in ks:
                    await e.ask(self.app, "stroke", w, points(self.strokes[k]))
                await self.set_area(w, line_ks)
                r = await e.ask(self.app, "neaten", w, repr(self.strength), _esc(wt))
            finally:
                await e.ask(self.app, "end", w)
            if tuple(self.order) != held or not self.neaten:
                return
            n = int(r[3])
            for i in range(n):
                j, how, pts = int(r[4 + 3 * i]), r[5 + 3 * i], r[6 + 3 * i]
                xy = [[float(v) for v in p.split(",")[:2]] for p in pts.split(" ")]
                self.pen.send({"op": "neat", "s": self.sid, "k": ks[j], "how": how, "points": xy})
            self.sent = {k: v for k, v in self.sent.items() if k[2] == self.strength}
            self.sent[key] = True

    async def report(self, m):
        async with self.lock:
            await self._report(m)

    async def _report(self, m):
        serial, action = int(m.get("serial", 0)), m.get("action")
        result = "not_kept"
        if not self.offered or serial != self.offered[0]:
            result = "stale"
        elif action == "discarded":
            self.forget()
        elif perms.state(self.app, "learn") != "allowed":
            result = "not_allowed"
            self.forget()
        else:
            rd = self.offered[1]
            if action == "picked":
                rank = int(m.get("rank", 0))
                text, src = (rd[rank][0] if 0 <= rank < len(rd) else str(m.get("text", ""))), "picked"
            elif action == "accepted":
                text, src = (rd[0][0] if rd else ""), "confirmed"
            else:
                text, src = str(m.get("text", "")), "corrected"
            try:
                if text and await self.ensure():
                    r = await self.pen.engine.ask(self.app, "confirm", self.name, src, _esc(text))
                    result = "stored" if r[1] == "ok" else "not_kept"
            except Error:
                result = "not_kept"
            self.forget()
        self.pen.send({"op": "report", "s": self.sid, "serial": serial, "result": result})


def _esc(s):
    return esc(s)


class Pen:
    """the compositor's channel: JSON lines both ways (docs/service.md)"""

    def __init__(self, engine, features):
        self.engine, self.features = engine, features
        self.surfaces = {}
        self.writer = None
        self.tasks = set()

    def spawn(self, coro):
        t = asyncio.ensure_future(self._guard(coro))
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def _guard(self, coro):
        try:
            await coro
        except Error:
            pass                    # the recognizer failed: this surface's work is dropped, raw ink flows on

    def send(self, m):
        if self.writer and not self.writer.is_closing():
            self.writer.write((json.dumps(m, separators=(",", ":")) + "\n").encode())

    def handle(self, m):
        op, sid = m.get("op"), m.get("s")
        if op == "surface":
            # who the app is: the compositor's sandbox id, else its process (ident)
            app = ident.clean(m["sandbox"]) if m.get("sandbox") else ident.identity(int(m.get("pid", 0)))[0]
            self.surfaces[sid] = Surface(self, sid, app)
            return
        s = self.surfaces.get(sid)
        if s is None:
            return
        if op == "set":
            s.set(m)
        elif op == "stroke":
            s.stroke(int(m["k"]), m["points"])
        elif op == "clear":
            s.clear()
        elif op == "report":
            self.spawn(s.report(m))
        elif op == "destroy":
            s.forget()
            del self.surfaces[sid]

    async def serve(self, reader, writer):
        self.writer = writer
        self.send({"op": "hello", "version": 1, **self.features()})
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict):
                self.handle(m)
        for s in list(self.surfaces.values()):
            s.session = None        # the recognizer goes with the service: nothing to end
            s.forget()
        self.surfaces.clear()
