#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance for neatening (spec 003 FR-013 to FR-017, FR-020, FR-022, FR-023,
FR-035 to FR-037, FR-043), with a random-weight model and synthetic samples: strength 0 gives the
strokes back byte for byte; any strength keeps the strokes, their order, every point's time,
pressure and tilt as sent; the same request gives the same ink; the four levels do what they
say (Light no letter's shape, Medium every letter half way to its glyph, Strong further,
Neatest the word set in the font, FR-043: its points on the glyphs where hb-shape puts
them, a character the font lacks moved only, a word read without the text set only when
read with high confidence); the target is the glyph the font
draws there, the variant itself, not its best version; a letter is pulled when written in as
many strokes as that glyph; a pulled letter whose new ink comes nearer another character's
versions (whatever their strokes) than it was written is put back, the others stay pulled,
and the reply names each character's reason; a word the dictionary lacks doesn't hold back the
line; at strength 1 the covered characters pulled lie on their glyph, closer to it (most of
them are); no new step back or kink where strokes start or letters join, or where the ink
and its versions differ (a loop one has); without a letter model the baseline is straightened; drawing passes unchanged; the session
is unchanged, so a confirm stores the same sample; a private field keeps nothing to neaten.

    HW_BIN=path/to/glassine-handwriting python3 tests/test_neaten.py

The correspondence along the stroke (monotonic, hooks to the ends), joined letters, a pair the
font joins, and the font's choice of glyphs (cycle, neighbour rules, joined pairs) are tested
in tests/test-engine.c. The recognizer's reading of real ink against neatened ink is measured on a store of the
person's samples with tools/eval-neaten.py (only aggregate numbers).
"""
import importlib.util
import json
import math
import random
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
import sys  # noqa: E402

sys.path.insert(0, str(HERE))
import test_letters as TL  # noqa: E402
import test_recognizer as TR  # noqa: E402

_spec = importlib.util.spec_from_file_location("eval_neaten", HERE.parent / "tools" / "eval-neaten.py")
EV = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(EV)
# the font's choice of glyphs as the preview has it (the same as ttf.c and neaten.c)
_spec = importlib.util.spec_from_file_location("fontview", HERE.parent.parent / "samples" / "glassine_hwsamples" / "fontview.py")
FV = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FV)


def points(stroke):
    return " ".join(f"{p[0]},{p[1]},{round(p[2] * 1000, 3)},{p[3]}" for p in stroke)


def radius(strokes):
    pts = [p for st in strokes for p in st]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return math.sqrt(sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2 for p in pts) / len(pts))


def shape_dist(strokes, v):
    """the distance of a character's ink to a variant as neaten.c measures it, the ink
    scaled to the variant's size (the shape, whatever x-height the line has)"""
    rv = radius(v["strokes"])
    return EV.vdist(strokes, radius(strokes) / rv if rv > 0 else 1, v)


GUARD_POINTS = 48      # neaten.c HW_NEAT_GUARD_POINTS


def guard_seq(strokes, scale=1.0, flip=False):
    """a character as one sequence, as the shape guard has it (neaten.c guard_seq)"""
    lens = [sum(math.hypot(st[i][0] - st[i - 1][0], st[i][1] - st[i - 1][1]) for i in range(1, len(st))) for st in strokes]
    tot, left, xs, ys = sum(lens), GUARD_POINTS, [], []
    for j, st in enumerate(strokes):
        m = left if j + 1 == len(strokes) else max(1, round(GUARD_POINTS * (lens[j] / tot if tot > 0 else 1 / len(strokes))))
        m = min(m, left - (len(strokes) - 1 - j))
        if m <= 0:
            continue
        if m == 1 or len(st) < 2:
            x, y = [st[0][0]] * m, [st[0][1]] * m
        else:
            x, y = EV.resample([p[0] for p in st], [p[1] for p in st], m)
        xs += x
        ys += y
        left -= m
    xs = [v * scale for v in xs]
    ys = [v * (-scale if flip else scale) for v in ys]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    return [(x - cx, y - cy) for x, y in zip(xs, ys)]


def guard_dist(ink, xh, v):
    a, b = guard_seq(ink, 1 / xh, True), guard_seq(v["strokes"])
    if len(a) != len(b):
        return math.inf
    return math.sqrt(sum((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 for p, q in zip(a, b)) / len(a))



def lean_of(strokes):
    """the downstrokes' lean (as the letter model measures it: tan, > 0 leaning right)"""
    n = d = 0.0
    for st in strokes:
        for i in range(1, len(st)):
            dx, dy = st[i][0] - st[i - 1][0], -(st[i][1] - st[i - 1][1])
            if dy < 0 and -dy > 2 * abs(dx):
                n += dx * dy
                d += dy * dy
    return n / d if d > 0 else 0.0


def EV_fit(pts):
    """least squares y = a + b x"""
    mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    b = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx if sxx > 0 else 0
    return my - b * mx, b

def on_curve(ink, v, rounds=200, cover=False):
    """how far a character's points lie from a variant's strokes (RMS, x-heights; each
    stroke's from its stroke) once moved and scaled onto them at best (no stretch, no shear):
    0 when they lie on it; with cover, per stroke the share of the variant's stroke the
    points reach from its start and from its end (0 0: all of it)"""
    pts = [(p[0], -p[1]) for st in ink for p in st]
    sid = [j for j, st in enumerate(ink) for _ in st]
    segs = [[(a, b) for a, b in zip(st, st[1:])] or [(st[0], st[0])] for st in v["strokes"]]

    def near(x, y, j):
        best, bp = math.inf, None
        for (ax, ay), (bx, by) in segs[j]:
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / ll)) if ll > 0 else 0
            qx, qy = ax + t * dx, ay + t * dy
            d = (qx - x) ** 2 + (qy - y) ** 2
            if d < best:
                best, bp = d, (qx, qy)
        return bp

    vx = [p[0] for st in v["strokes"] for p in st]
    vy = [p[1] for st in v["strokes"] for p in st]
    r = radius([pts])
    sc = radius([list(zip(vx, vy))]) / r if r > 0 else 1
    mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    tx, ty = sum(vx) / len(vx) - sc * mx, sum(vy) / len(vy) - sc * my
    for _ in range(rounds):
        q = [near(sc * x + tx, sc * y + ty, j) for (x, y), j in zip(pts, sid)]
        # least squares scale and move of pts onto q
        qx, qy = sum(a for a, _ in q) / len(q), sum(b for _, b in q) / len(q)
        num = sum((x - mx) * (a - qx) + (y - my) * (b - qy) for (x, y), (a, b) in zip(pts, q))
        den = sum((x - mx) ** 2 + (y - my) ** 2 for x, y in pts)
        sc = num / den if den > 0 else sc
        tx, ty = qx - sc * mx, qy - sc * my
    q = [near(sc * x + tx, sc * y + ty, j) for (x, y), j in zip(pts, sid)]
    if cover:
        out = []
        for j, st in enumerate(v["strokes"]):
            cum = [0.0]
            for a, b in zip(st, st[1:]):
                cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
            L = cum[-1] or 1

            def frac(p):
                best, f = math.inf, 0
                for i, (a, b) in enumerate(zip(st, st[1:])):
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    ll = dx * dx + dy * dy
                    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ll)) if ll > 0 else 0
                    d = (a[0] + t * dx - p[0]) ** 2 + (a[1] + t * dy - p[1]) ** 2
                    if d < best:
                        best, f = d, (cum[i] + t * math.sqrt(ll)) / L
                return f

            fs = [frac(qq) for qq, jj in zip(q, sid) if jj == j]
            out.append((min(fs), 1 - max(fs)) if fs else (1, 1))
        return out
    return math.sqrt(sum((sc * x + tx - a) ** 2 + (sc * y + ty - b) ** 2 for (x, y), (a, b) in zip(pts, q)) / len(pts))


def flow(raw, out, xh):
    """new steps back and new kinks of a stroke: where the raw ink has none within 5 points
    (a step back at a cusp of the raw ink isn't new either)"""
    n, w = len(out), 3

    def back(p, i):
        d = (p[i + w][0] - p[i - w][0], p[i + w][1] - p[i - w][1])
        st = (p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1])
        nd, ns = math.hypot(*d), math.hypot(*st)
        return nd > 0.15 * xh and ns > 0.01 * xh and d[0] * st[0] + d[1] * st[1] < -0.5 * nd * ns

    def turn(p, i):
        a = (p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1])
        b = (p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1])
        na, nb = math.hypot(*a), math.hypot(*b)
        if na < 0.01 * xh or nb < 0.01 * xh:
            return 0
        return math.degrees(math.acos(max(-1, min(1, (a[0] * b[0] + a[1] * b[1]) / (na * nb)))))

    rb = [w <= i < n - 1 - w and back(raw, i) for i in range(n)]
    ra = [0 < i < n - 1 and turn(raw, i) > 45 for i in range(n)]
    near = lambda f, i: any(f[max(0, i - 5):i + 6])
    nb = sum(back(out, i) and not near(rb, i) and not near(ra, i) for i in range(w, n - 1 - w))
    nk = sum(turn(out, i) > 90 and not near(ra, i) for i in range(1, n - 1))
    return nb, nk


def add_loop(st, xh=30.0, r=0.12, n=10):
    """the stroke with a small loop in its middle (to the left of the way it goes)"""
    m = len(st) // 2
    if m < 1 or m >= len(st) - 1:
        return st
    (x0, y0), (x1, y1) = st[m][:2], st[m + 1][:2]
    d = math.hypot(x1 - x0, y1 - y0) or 1
    ux, uy = (x1 - x0) / d, (y1 - y0) / d
    cx, cy = x0 - uy * r * xh, y0 + ux * r * xh
    loop = [[round(cx + uy * r * xh * math.cos(a) + ux * r * xh * math.sin(a), 3),
             round(cy - ux * r * xh * math.cos(a) + uy * r * xh * math.sin(a), 3), st[m][2], 0.5]
            for a in (2 * math.pi * k / n for k in range(1, n))]
    return st[:m + 1] + loop + st[m + 1:]


def retime(s):
    t = 0.0
    for st in s["strokes"]:
        for p in st:
            p[2] = round(t, 3)
            t += 0.01


def joined_line(rnd, words, shapes=None):
    """a line of words, each word's letters joined into one stroke through them (a short
    bridge from one letter's last stroke to the next one's first): the sample, per letter
    its points as (stroke, first, last), and the same letters written apart"""
    text = " ".join(words)
    s = TL.sample(text, rnd, shapes=shapes)
    strokes, at, k = [], [], 0
    for w in words:
        pj = None
        for i, ch in enumerate(w):
            sp = s["spans"][k]
            pts = [[list(p) for p in s["strokes"][z]] for z in sp]
            mine = []
            if pj is not None:
                prev = strokes[pj]
                a, b = prev[-1], pts[0][0]
                bridge = [[round(a[0] + (b[0] - a[0]) * z / 4, 3), round(a[1] + (b[1] - a[1]) * z / 4, 3), a[2], 0.5]
                          for z in range(1, 4)]
                start = len(prev) + len(bridge)
                prev += bridge + pts[0]
                mine.append((pj, start, len(prev) - 1))
                rest = pts[1:]
            else:
                rest = pts
            for st in rest:
                strokes.append(st)
                mine.append((len(strokes) - 1, 0, len(st) - 1))
            # (the pen runs on from the letter's last stroke; from an i's or j's stem, the
            # dot set after the word)
            pj = mine[0][0] if ch in "ij" and len(pts) > 1 else mine[-1][0]
            at.append(mine)
            k += 1
        if k < len(text):
            at.append([])
            k += 1
    for z, st in enumerate(strokes):
        for i, p in enumerate(st):
            p[2] = round(z * 1.0 + 0.01 * i, 3)
    return dict(s, strokes=strokes), at, s


def mark_offset(xy, stem, mark):
    """a mark's offset (x, y) from its stem: its points' middle less the stem's points'
    median x and their top"""
    j, a, b = stem
    sx = sorted(xy[j][z][0] for z in range(a, b + 1))
    top = min(xy[j][z][1] for z in range(a, b + 1))
    j, a, b = mark
    pts = [xy[j][z] for z in range(a, b + 1)]
    return (sum(p[0] for p in pts) / len(pts) - sx[len(sx) // 2], sum(p[1] for p in pts) / len(pts) - top)


WHY = {"pulled", "level", "not-covered", "no-structure", "shape-guard", "low-confidence", "drawing",
       "gesture", "no-ink", "space", "set"}


def parse(r, why=False):
    """(text, [(stroke, how, POINTS)]), and with why the characters' reasons (FR-041)"""
    assert r[0] == "neaten" and r[2] == "ok", r
    k = int(r[4])
    assert len(r) == 6 + 3 * k, r
    text = TR.unesc(r[3])
    reasons = r[5 + 3 * k].split(" ") if r[5 + 3 * k] else []
    assert len(reasons) == (len(text) if text else 0), (text, reasons)
    assert all(w in WHY for w in reasons), reasons
    out = [(int(r[5 + 3 * i]), r[6 + 3 * i], r[7 + 3 * i]) for i in range(k)]
    return (text, out, reasons) if why else (text, out)


def near_seg(x, y, segs):
    """the nearest point to (x, y) on segments [((ax, ay), (bx, by))]"""
    best, bp = math.inf, None
    for (ax, ay), (bx, by) in segs:
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / ll)) if ll > 0 else 0
        qx, qy = ax + t * dx, ay + t * dy
        d = (qx - x) ** 2 + (qy - y) ** 2
        if d < best:
            best, bp = d, (qx, qy)
    return bp


def on_set(pts, glyphs, rounds=60, size=None):
    """how far points (device units, y down) lie from a set word's glyph strokes (x-heights,
    y up; per point the strokes it should lie on) once scaled and moved onto them at best (one
    scale, no stretch; near size, device units per x-height, when given, else near the
    boxes'): (RMS in x-heights, the scale: device units per x-height, the baseline's y, the x
    of the glyphs' 0)"""
    P = [(x, -y) for x, y in pts]
    segs = [[(a, b) for st in g for a, b in (zip(st, st[1:]) if len(st) > 1 else [(st[0], st[0])])] for g in glyphs]
    gx = [p[0] for g in glyphs for st in g for p in st]
    gy = [p[1] for g in glyphs for st in g for p in st]
    px, py = [p[0] for p in P], [p[1] for p in P]
    sc = 1 / size if size else (max(gx) - min(gx) + max(gy) - min(gy)) / ((max(px) - min(px) + max(py) - min(py)) or 1)
    tx, ty = min(gx) - sc * min(px), min(gy) - sc * min(py)
    sc0 = sc
    mx, my = sum(px) / len(P), sum(py) / len(P)
    for _ in range(rounds):
        q = [near_seg(sc * x + tx, sc * y + ty, sg) for (x, y), sg in zip(P, segs)]
        qx, qy = sum(a for a, _ in q) / len(q), sum(b for _, b in q) / len(q)
        num = sum((x - mx) * (a - qx) + (y - my) * (b - qy) for (x, y), (a, b) in zip(P, q))
        den = sum((x - mx) ** 2 + (y - my) ** 2 for x, y in P)
        # (the scale kept near the boxes': moved freely it would shrink the points onto one)
        sc = min(max(num / den if den > 0 else sc, 0.95 * sc0), 1.05 * sc0)
        tx, ty = qx - sc * mx, qy - sc * my
    q = [near_seg(sc * x + tx, sc * y + ty, sg) for (x, y), sg in zip(P, segs)]
    rms = math.sqrt(sum((sc * x + tx - a) ** 2 + (sc * y + ty - b) ** 2 for (x, y), (a, b) in zip(P, q)) / len(P))
    return rms, 1 / sc if sc else math.inf, ty / sc if sc else math.inf, -tx / sc if sc else math.inf


class Neaten(unittest.TestCase):
    def setUp(self):
        self.e = TR.Env()
        self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"

    def tearDown(self):
        TR.shutil.rmtree(self.e.dir, ignore_errors=True)

    def letters(self, seed=1, change=None, shapes=None, more=(), per=4):
        """a letter model from synthetic prompted samples of separate letters, each `per`
        times (each stroke changed by change(stroke) first), and more samples"""
        ss = TL.alphabet_samples(seed=seed, per=per, shapes=shapes) + list(more)
        if change:
            for s in ss:
                s["strokes"] = [change(st) for st in s["strokes"]]
                retime(s)
        f = self.e.dir / "in.jsonl"
        lines = [json.dumps({"format": "glassine-handwriting-export", "version": 1})]
        for i, s in enumerate(ss):
            lines.append(json.dumps({"sample": s, "client": "org.example.test", "confirmed": 1700000000 + i,
                                     "source": "prompted"}))
        f.write_text("\n".join(lines) + "\n")
        self.e.run("samples", "import", str(f))
        self.e.run("letters", "build", "-r")
        return {m["char"]: m for m in map(json.loads, (self.e.store / "letters" / "model").read_text().splitlines())
                if "char" in m}

    def glyphs(self, text):
        """per character of text the glyph the font draws there (FR-035: the cycle, the
        neighbour rules, a joined pair), None for none or a joined pair's letter"""
        model = FV.parse((self.e.store / "letters" / "model").read_text())
        view = FV.font_view(model)
        return [view["chars"][c]["variants"][v] if v is not None and v >= 0 and c in view["chars"] else None
                for c, v in zip(text, FV.variants(model, text))]

    def begin(self, c, sample, units="device:4.0:200"):
        self.assertEqual(c.ask("begin", "s", "en", "-", units, "")[2], "ok")
        a = sample.get("area")
        if a:
            b = a["box"]
            c.ask("area", "s", f"{b[0]},{b[1]},{b[2]},{b[3]},{a['line']},0")
        return [c.ask("stroke", "s", points(st))[3] for st in sample["strokes"]]

    def test_strength_zero_byte_for_byte(self):
        c = self.e.start()
        c.ask("hello", "1")
        c.ask("begin", "s", "en", "-", "device", "")
        sent = ["1.50,2.0,0010,0.25 3,4.000,20,0.5 5e0,6,35,1", "10,10,0 11,12.5,8.0", "7,8,0,0.3,12.5,-4 9,9,5,0.3,13,-4"]
        for p in sent:
            c.ask("stroke", "s", p)
        for st in ("0", "0.0"):
            text, out = parse(c.ask("neaten", "s", st))
            self.assertEqual([o[2] for o in out], sent)
            self.assertEqual([o[0] for o in out], [0, 1, 2])
            self.assertTrue(all(o[1] == "kept" for o in out))
        # an empty session
        c.ask("begin", "e", "en", "-", "device", "")
        self.assertEqual(c.ask("neaten", "e", "1")[2:], ["ok", "", "0", ""])
        c.close()

    def test_keeps_strokes_order_time_pressure(self):
        self.letters()
        rnd = random.Random(9)
        s = TL.sample("abc de", rnd)
        c = self.e.start()
        c.ask("hello", "1")
        self.begin(c, s)
        sent = [points(st) for st in s["strokes"]]
        for st in ("0.25", "0.3", "0.5", "0.75", "1"):
            for args in ((), ("abc de", ";".join(",".join(map(str, sp)) for sp in s["spans"]))):
                _, out = parse(c.ask("neaten", "s", st, *args))
                self.assertEqual([o[0] for o in out], list(range(len(sent))))
                for (_, how, got), want in zip(out, sent):
                    self.assertIn(how, ("kept", "line", "pulled"))
                    g, w = got.split(" "), want.split(" ")
                    self.assertEqual(len(g), len(w))
                    for a, b in zip(g, w):
                        self.assertEqual(a.split(",")[2:], b.split(",")[2:])
        # the same request, the same ink; another process too
        r1 = c.ask("neaten", "s", "0.7")[2:]
        r2 = c.ask("neaten", "s", "0.7")[2:]
        self.assertEqual(r1, r2)
        c.close()
        c = self.e.start()
        self.begin(c, s)
        self.assertEqual(c.ask("neaten", "s", "0.7")[2:], r1)
        c.close()

    def test_ink_far_off_its_glyphs_place(self):
        """ink lying far above where its glyph lies on the line (a letter the alignment took
        for a mark above it) isn't pulled into the glyph (no-structure); the others are"""
        self.letters()
        s = TL.sample("ooooo", random.Random(30))
        for j in s["spans"][2]:
            for p in s["strokes"][j]:
                p[1] = round(p[1] - 1.3 * 30, 3)
        how, xy = self.neaten_letters(s, 0.75)
        self.assertEqual(self.why[2], "no-structure", self.why)
        self.assertGreaterEqual(self.why.count("pulled"), 3, self.why)

    def test_pulled_ink_is_its_own_character(self):
        """a pulled character is drawn from its own ink (FR-035): without ALIGN, the
        alignment and the letter model decide which ink is whose, and where they are off (a
        letter written in more strokes than its versions have, so the joints shift along the
        word) no character's glyph is drawn over another one's ink; texts with spaces and
        repeated letters, at the levels that pull (Neatest sets the word: FR-043)"""
        self.letters()
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        n = 0
        # (which characters this random-weight model's alignment leaves with ink of their own
        # to check moves with each change to how joined strokes are cut: enough texts that
        # more than ten are checked)
        for seed, text in enumerate(("the there", "he he", "see the tree", "then when", "her here",
                                     "wet ewe", "the wet hen", "three trees", "tee hee", "where were",
                                     "het the ether")):
            rnd = random.Random(70 + seed)
            s = TL.sample(text, rnd)
            # one letter in two strokes, as a hasty hand lifts the pen
            k0 = next(k for k, ch in enumerate(text) if ch != " " and len(s["spans"][k]) == 1)
            j0 = s["spans"][k0][0]
            st = s["strokes"][j0]
            s["strokes"][j0:j0 + 1] = [st[:len(st) // 2], st[len(st) // 2:]]
            s["spans"] = [[j + (j > j0) for j in sp] + ([j0 + 1] if k == k0 else []) for k, sp in enumerate(s["spans"])]
            glyphs = self.glyphs(text)
            for strength in ("0.5", "0.75"):
                c = self.e.start()
                self.begin(c, s)
                _, out = parse(c.ask("neaten", "s", strength, TR.esc(text)))
                c.close()
                xy = [EV.to_xy(o[2]) for o in out]
                own = {j: [k for k, sp in enumerate(s["spans"]) if j in sp] for j in range(len(s["strokes"]))}
                for k, ch in enumerate(text):
                    sp = s["spans"][k]
                    if ch == " " or glyphs[k] is None or not sp or any(own[j] != [k] for j in sp):
                        continue
                    if not all(out[j][1] == "pulled" for j in sp):
                        continue
                    before = [[(p[0], p[1]) for p in s["strokes"][j]] for j in sp]
                    after = [xy[j] for j in sp]

                    def near(ink):
                        mine = shape_dist(ink, glyphs[k])
                        others = [shape_dist(ink, g) for z, g in enumerate(glyphs) if g is not None and text[z] != ch]
                        return mine, min([d for d in others if math.isfinite(d)], default=math.inf)
                    mb, ob = near(before)
                    if not mb <= ob:
                        continue
                    n += 1
                    ma, oa = near(after)
                    self.assertLessEqual(ma, oa, (text, k, ch, strength, mb, ob))
        self.assertGreater(n, 10)

    def test_closer_to_the_glyph(self):
        """at Neatest the covered characters are closer to the glyph the font draws there,
        set on it (FR-043)"""
        self.letters()
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        rnd = random.Random(11)
        n = m = 0
        for text in ("hello", "World", "q7x2b"):
            s = TL.sample(text, rnd)
            # hastier than the samples the letters were made from
            for st in s["strokes"]:
                for p in st:
                    p[0] += rnd.gauss(0, 2.0)
                    p[1] += rnd.gauss(0, 2.0)
            c = self.e.start()
            self.begin(c, s)
            align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
            _, out = parse(c.ask("neaten", "s", "1", TR.esc(text), align))
            c.close()
            xy = [EV.to_xy(o[2]) for o in out]
            for k, (ch, g) in enumerate(zip(text, self.glyphs(text))):
                if g is None:
                    continue
                before = [[(p[0], p[1]) for p in s["strokes"][j]] for j in s["spans"][k]]
                db = shape_dist(before, g)
                if not math.isfinite(db):
                    continue
                m += 1
                if not all(out[j][1] == "pulled" for j in s["spans"][k]):
                    continue
                after = [xy[j] for j in s["spans"][k]]
                # its points on the glyph's strokes (a letter already that near keeps its few
                # points: on the glyph, chords between them)
                self.assertLess(on_set([p for st in after for p in st], [g["strokes"]] * sum(map(len, after)))[0], 0.003,
                                (text, ch))
                da = shape_dist(after, g)
                self.assertLess(da, max(db, 0.08), (text, ch))
                # all of the glyph, not a part of it on all its points
                self.assertTrue(all(a < 0.12 and b < 0.12 for a, b in on_curve(after, g, cover=True)), (text, ch))
                n += da < db
        # (a letter whose ink doesn't have its glyph's structure stays at the line level)
        self.assertGreater(n, 8)
        self.assertGreaterEqual(n, 0.75 * m)

    def neaten_letters(self, s, strength, text=None, align=True, knob=""):
        """the sample neatened at strength (with its text and spans), as (how, xy) per stroke"""
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = knob
        c = self.e.start()
        self.begin(c, s)
        text = text or s["text"]
        args = (TR.esc(text), ";".join(",".join(map(str, sp)) for sp in s["spans"])) if align else (TR.esc(text),)
        _, out, self.why = parse(c.ask("neaten", "s", str(strength), *args), why=True)
        c.close()
        return [o[1] for o in out], [EV.to_xy(o[2]) for o in out]

    def set_layout(self, text):
        """per character of text the strokes of the glyph the font sets there (x-heights from
        the text's start, y up; a joined pair's for both its letters), None where the font has
        none: from hb-shape and the font built (the pen x and the glyph), else as the samples
        app's preview lays the font out (the same advances and kerning)"""
        model_text = (self.e.store / "letters" / "model").read_text()
        view = FV.font_view(FV.parse(model_text))
        gap = (view.get("spacing") or {}).get("letter", 0.15)
        out = [None] * len(text)
        self.pens = [None] * len(text)
        if TL.SKIP_TOOLS or not TL.shutil.which("hb-shape"):
            # (a character the font lacks: the font's .notdef, one x-height wide, where the
            # preview has the system font's)
            k, more = 0, 0
            for c, strokes, x, adv, _ in FV.layout(view, text):
                for z in range(len(c)):
                    self.pens[k + z] = x + more
                    if strokes:
                        out[k + z] = [[(x + more + p[0], p[1]) for p in st] for st in strokes]
                if strokes is None:
                    more += 1 - adv
                k += len(c)
            return out, "preview"
        ttf = self.e.dir / "set.ttf"
        if not ttf.exists():
            self.e.run("font", "build", "-n", "Set Test", str(ttf))
        gl = TL.font_glyphs(model_text)
        joined = {tuple(d["joined"]): d for d in map(json.loads, model_text.splitlines()) if "joined" in d}
        g = TL.hb(ttf, text)
        x = 0
        for i, e in enumerate(g):
            k = gl.get(e["g"])
            cl = e["cl"]
            end = g[i + 1]["cl"] if i + 1 < len(g) else len(text)
            v = None
            if k and k[0] == "step":
                d = view["chars"][k[1]]
                v = d["variants"][FV.slots(d)[k[2]]]
            elif k and k[0] == "variant":
                v = view["chars"][k[1]]["variants"][k[2]]
            elif k and k[0] == "joined":
                v = joined[tuple(k[1])]["variant"]
            at = (x + e["dx"]) / 500
            for z in range(cl, end):
                self.pens[z] = at
            if v is not None:
                for z in range(cl, end):
                    out[z] = [[(at + gap / 2 + p[0], p[1]) for p in st] for st in v["strokes"]]
            x += e["ax"]
        return out, "hb-shape"

    def test_neatest_sets_the_word(self):
        """at Neatest a word whose text is known is set anew in the font (FR-043): every point
        of a character on the centre line of the glyph the font sets for it, the glyphs where
        hb-shape (or the preview's layout, without it) puts them, all at one size (the line's
        x-height) and on one baseline; HOW pulled, the characters' reason set. (With the text
        alone the alignment of this random-weight model decides whose ink is whose: set too,
        the strokes kept, tested in test_keeps_strokes_order_time_pressure.)"""
        self.letters()
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        rnd = random.Random(50)
        words = 0
        for text in ("hello world", "see the tree", "quick brown fox", "jumps over"):
            s = TL.sample(text, rnd)
            for st in s["strokes"]:
                for p in st:
                    p[0] += rnd.gauss(0, 1.0)
                    p[1] += rnd.gauss(0, 1.0)
            lay, how_laid = self.set_layout(text)
            align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
            c = self.e.start()
            self.begin(c, s)
            _, out, why = parse(c.ask("neaten", "s", "1", TR.esc(text), align), why=True)
            c.close()
            xy = [EV.to_xy(o[2]) for o in out]
            self.assertEqual([w for w in why if w != "space"], ["set"] * len(text.replace(" ", "")), (text, why))
            self.assertTrue(all(o[1] == "pulled" for o in out))
            # each word where it was written, set as the font sets it, all on one baseline
            k0, bases = 0, []
            for w in text.split(" "):
                ks = range(k0, k0 + len(w))
                k0 += len(w) + 1
                wp = [p for k in ks for j in s["spans"][k] for p in xy[j]]
                wg = [lay[k] for k in ks for j in s["spans"][k] for _ in xy[j]]
                rms, size, base, _ = on_set(wp, wg)
                self.assertLess(rms, 0.003, (text, w, how_laid))
                self.assertAlmostEqual(size / 30.0, 1, delta=0.15)
                bases.append(base)
                words += 1
            self.assertLess(max(bases) - min(bases), 0.3, (text, bases))
        self.assertEqual(words, 10)

    def test_conditioned_setting_keeps_the_lines_direction(self):
        """(measurements) with cond=1 Neatest sets the same words as by default, but each along
        its own baseline and lean as written: a line written on a slope and leaning further
        stays so, each word where it was written, every letter on one of its versions once its
        word's slope and lean are taken off; by default the words are set on the straight line
        at the font's lean, as before (cond=0 the same)"""
        self.letters()
        model = [json.loads(m) for m in (self.e.store / "letters" / "model").read_text().splitlines()]
        L = {m["char"]: m for m in model if "char" in m}
        slant = next(m["metrics"]["slant"] for m in model if "metrics" in m)
        rnd = random.Random(53)
        slope, lean = 0.2, 0.3
        for text in ("hello world", "come on now"):
            s = TL.sample(text, rnd)
            x0, y0 = s["strokes"][0][0][0], s["strokes"][0][0][1]
            for st in s["strokes"]:
                for p in st:
                    p[0] += lean * (y0 - p[1])
                    p[1] += slope * (p[0] - x0)
            written = [[(p[0], p[1]) for p in st] for st in s["strokes"]]
            how0, xy0 = self.neaten_letters(s, 1)
            why0 = self.why
            self.assertEqual(self.neaten_letters(s, 1, knob="cond=0")[1], xy0)
            how1, xy1 = self.neaten_letters(s, 1, knob="cond=1")
            self.assertEqual(self.why, why0)
            self.assertEqual(how1, how0)
            self.assertEqual([len(a) for a in xy1], [len(a) for a in written])
            # the baseline through the letters' lowest points: straight by default, as written
            # conditioned
            def base_slope(xy, ks):
                bot = [max(((p[1], p[0]) for j in s["spans"][k] for p in xy[j]))[::-1] for k in ks if text[k] in "aceomnuw"]
                return EV_fit(bot)[1]
            every = [k for k in range(len(text)) if text[k] != " "]
            self.assertLess(abs(base_slope(xy0, every)), 0.04, text)
            self.assertLess(abs(base_slope(xy1, every) - slope), 0.04, text)
            k0 = 0
            for w in text.split(" "):
                ks = range(k0, k0 + len(w))
                k0 += len(w) + 1
                if any(why0[k] != "set" for k in ks):
                    continue
                js = [j for k in ks for j in s["spans"][k]]
                # the lean of the downstrokes as written
                self.assertLess(abs(lean_of([xy1[j] for j in js]) - lean_of([written[j] for j in js])), 0.1, (text, w))
                # where it was written: its middle within half an x-height
                mx = lambda pts: (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
                self.assertLess(abs(mx([p for j in js for p in xy1[j]]) - mx([p for j in js for p in written[j]])), 15, (text, w))
                # each letter on one of its versions once its word's slope and lean are taken off
                b, ln = base_slope(xy1, every), lean_of([xy1[j] for j in js]) - slant
                ox, oy = xy1[js[0]][0]
                for k in ks:
                    ink = []
                    for j in s["spans"][k]:
                        st = [(x, y - b * (x - ox)) for x, y in xy1[j]]
                        ink.append([(x - ln * (oy - y), y) for x, y in st])
                    best = min(on_curve(ink, v) for v in L[w[k - ks[0]]]["variants"] if len(v["strokes"]) == len(ink))
                    self.assertLess(best, 0.05, (text, w, k))
            # cond=2: the same words along the line's slope, every set word at one lean (the line's)
            how2, xy2 = self.neaten_letters(s, 1, knob="cond=2")
            self.assertEqual(self.why, why0)
            self.assertEqual(how2, how0)
            self.assertEqual([len(a) for a in xy2], [len(a) for a in written])
            self.assertLess(abs(base_slope(xy2, every) - slope), 0.04, text)
            leans, k0 = [], 0
            for w in text.split(" "):
                ks = range(k0, k0 + len(w))
                k0 += len(w) + 1
                if all(why0[k] == "set" for k in ks):
                    leans.append(lean_of([xy2[j] for k in ks for j in s["spans"][k]]))
            line_lean = lean_of([written[j] for k in range(len(text)) if text[k] != " " for j in s["spans"][k]])
            for ln in leans:
                self.assertLess(abs(ln - line_lean), 0.1, text)

    def test_eval_setting(self):
        """tools/eval-setting.py: every way of neatening measured on the held-back samples,
        aggregate numbers only on stdout, the ink only in --dump (a private file)"""
        self.letters(more=[TL.sample(t, random.Random(60 + i)) for i, t in enumerate(("hello world", "come on now") * 6)])
        dump = self.e.dir / "dump.json"
        p = subprocess.run([sys.executable, str(HERE.parent / "tools" / "eval-setting.py"), str(TR.BIN), str(self.e.store),
                            "--models", str(self.e.models), "--tilt", "0.15", "--dump", str(dump)],
                           capture_output=True, text=True, env=self.e.env, check=True)
        rows = {ln.split("  ")[0].strip(): ln for ln in p.stdout.splitlines() if ln.startswith(("Light", "Medium", "Strong", "pull 1", "set"))}
        self.assertEqual(set(rows), {"Light", "Medium", "Strong", "pull 1", "set", "set, conditioned", "set, line-conditioned"})
        self.assertIn("tilted:", p.stdout)
        self.assertEqual(dump.stat().st_mode & 0o777, 0o600)
        d = json.loads(dump.read_text())
        self.assertTrue(d["lines"])
        self.assertEqual(set(d["lines"][0]["ways"]), set(rows))
        # (only numbers: no text of a sample on stdout)
        texts = {e["text"] for e in d["lines"]}
        self.assertFalse(any(t in p.stdout for t in texts if len(t) > 3))

    def test_neatest_draws_every_stroke_apart(self):
        """at Neatest (FR-043) a letter is set onto a variant with as many strokes as its ink
        has pieces, each piece along its own stroke: the dots of i and j stay dots, the bars of
        t, f, E and T bars and the bowls of p, b and d bowls (no line drawn from the stem to
        them, nothing of them lost), and an F written in
        two strokes, its glyph in three, keeps its ink as written (set over the glyph, its pen
        lift would be drawn from its top bar to its middle one)"""
        self.letters(seed=9, shapes=TL.WHOLE_SHAPES)
        rnd = random.Random(91)
        # (the line's spacing pulls the T's bar's start onto the end of the E's top bar; as
        # written they are apart, so neither keeps its ink)
        s = TL.sample("FipjtbdfET", rnd, shapes=TL.WHOLE_SHAPES)
        # the F in two: the stem up and on along the top bar, then the middle bar
        f0, f1, f2 = (s["strokes"][j] for j in s["spans"][0])
        s["strokes"][s["spans"][0][0]] = list(reversed(f0)) + f1
        del s["strokes"][s["spans"][0][1]]
        s["spans"] = [[0, 1]] + [[j - 1 for j in sp] for sp in s["spans"][1:]]
        TL.retime_sample(s)
        align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
        c = self.e.start()
        self.begin(c, s)
        _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("FipjtbdfET"), align), why=True)
        c.close()
        self.assertEqual(why, ["no-structure"] + ["set"] * 9)
        xy = {o[0]: EV.to_xy(o[2]) for o in out}

        def box(j):
            xs, ys = [p[0] for p in xy[j]], [p[1] for p in xy[j]]
            return max(xs) - min(xs), max(ys) - min(ys)

        def ink(j):
            xs, ys = [p[0] for p in s["strokes"][j]], [p[1] for p in s["strokes"][j]]
            return max(xs) - min(xs), max(ys) - min(ys)
        # each letter's strokes: stem (or body) first, then its mark
        marks = (("i", 2, 3), ("p", 4, 5), ("j", 6, 7), ("t", 8, 9), ("b", 10, 11), ("d", 12, 13),
                 ("f", 14, 15), ("E", 16, 17), ("E", 16, 18), ("E", 16, 19), ("T", 20, 21))
        for c, a, b in marks:
            with self.subTest(c=c, stroke=b):
                stem, mark = box(a), box(b)
                if c in "ij":       # a dot stays a dot
                    self.assertLess(max(mark), 0.2 * stem[1])
                elif c in "tfET":   # a bar stays a bar
                    self.assertGreater(mark[0], 0.25 * stem[1])
                    self.assertLess(mark[1], 0.15 * stem[1])
                else:               # a bowl stays a bowl
                    self.assertGreater(mark[0], 0.2 * stem[1])
                    self.assertGreater(mark[1], 0.2 * stem[1])
        # the F's ink as written (only moved, scaled and set upright with its line): its
        # strokes' sizes about as they were
        sc = box(0)[1] / ink(0)[1]
        for j in (0, 1):
            self.assertAlmostEqual(box(j)[0], sc * ink(j)[0], delta=0.15 * ink(0)[1])

    def test_neatest_sets_a_rare_version_on_the_ink_on_the_line(self):
        """a letter whose version there was made from fewer than 3 samples and sits far
        higher than its ink (here every M of the model written twice as tall): the letter is
        set, its top and bottom where Strong leaves its ink's, no higher or lower, and the
        next letter at the gap the person writes the pair at, not the font's tall M's
        (FR-043)"""
        n = [0]

        def tall(c, style, rnd):
            # (two ways of writing it, each twice: two versions of 2 samples each)
            n[0] += 1
            return [[(x, 2 * y) for x, y in st] for st in TL.shape(c, n[0] % 2, rnd)]
        model = self.letters(seed=9, shapes={"M": tall})
        # (Strong pulling nothing, so its ink is the ink as written, on the line)
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = "pull=0"
        s = TL.sample("Moon", random.Random(96))
        align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
        c = self.e.start()
        self.begin(c, s)
        _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("Moon"), align), why=True)
        c.close()
        m = [v["members"] for v in model["M"]["variants"]]
        self.assertLess(max(m), 3, m)
        self.assertEqual(why, ["set"] * 4)
        # (its ink where Strong leaves it, to a tenth of an x-height, top and bottom)
        c = self.e.start()
        self.begin(c, s)
        _, strong, _ = parse(c.ask("neaten", "s", "0.75", TR.esc("Moon"), align), why=True)
        c.close()
        xy = {o[0]: EV.to_xy(o[2]) for o in out}
        xs = {o[0]: EV.to_xy(o[2]) for o in strong}
        ys = [p[1] for j in s["spans"][0] for p in xy[j]]
        yi = [p[1] for j in s["spans"][0] for p in xs[j]]
        self.assertLess(abs(min(ys) - min(yi)), 0.1 * 30)
        self.assertLess(abs(max(ys) - max(yi)), 0.1 * 30)
        # (the o beside it: no further than the person's widest gap between letters, and
        # not on it)
        a = max(p[0] for j in s["spans"][0] for p in xy[j])
        b = min(p[0] for j in s["spans"][1] for p in xy[j])
        self.assertGreater(b - a, -0.1 * 30)
        self.assertLess(b - a, 1.0 * 30)

    def test_neatest_spaces_a_kept_capital_as_the_person_does(self):
        """an F written in two strokes (its versions have three: it keeps its ink) two and a
        half times as wide as usual: kept, it sits on the middle of its glyph and the font
        would leave the o further off than the person writes letters apart; the o is placed
        as the line-level spacing places letters (the person's distance between the two
        middles, never closer than half the typical gap where both have ink), so beside it,
        neither on it nor further off than the person's gap (FR-043)"""
        self.letters(seed=9, shapes=TL.WHOLE_SHAPES)
        gaps = []
        for scale in (1.0, 2.5):
            s = TL.sample("Fon", random.Random(93), shapes=TL.WHOLE_SHAPES)
            st, sp = s["strokes"], s["spans"]
            f = [st[j] for j in sp[0]]
            x0 = min(p[0] for q in f for p in q)
            wide = [[[round(x0 + scale * (p[0] - x0), 3)] + p[1:] for p in q] for q in f]
            rest = [[[round(p[0] + (scale - 1) * 18, 3)] + p[1:] for p in st[j]] for j in sp[1] + sp[2]]
            s["strokes"] = [list(reversed(wide[0])) + wide[1], wide[2]] + rest
            s["spans"] = [[0, 1], [2], list(range(3, 3 + len(sp[2])))]
            TL.retime_sample(s)
            align = ";".join(",".join(map(str, z)) for z in s["spans"])
            c = self.e.start()
            self.begin(c, s)
            _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("Fon"), align), why=True)
            c.close()
            self.assertEqual(why, ["no-structure", "set", "set"], scale)
            xy = {q[0]: EV.to_xy(q[2]) for q in out}
            # (between the baseline and the x-height, where both have ink)
            base = max(p[1] for p in xy[2])
            band = [p for j in (0, 1) for p in xy[j] if base - 30 <= p[1] <= base]
            gaps.append((min(p[0] for p in xy[2]) - max(p[0] for p in band)) / 30)
        sp = [json.loads(x) for x in (self.e.store / "letters" / "model").read_text().splitlines() if '"spacing"' in x]
        letter = sp[0]["spacing"]["letter"]
        self.assertGreater(min(gaps), 0, gaps)
        self.assertLess(gaps[1], letter, (gaps, letter))

    def test_neatest_draws_no_letter_twice(self):
        """the reading gives the o only the f's bar and its body to the n (which then has a
        piece more than any n has, and keeps its ink as written, the o's body in it): the o
        is not set from the bar as well, so it isn't drawn twice (FR-043)"""
        self.letters(seed=9, shapes=TL.WHOLE_SHAPES)
        s = TL.sample("fon", random.Random(92), shapes=TL.WHOLE_SHAPES)
        st, sp = s["strokes"], s["spans"]
        # (the bar a little long, out over where the o is: the word still fits the font's)
        bar = [[round(p[0] + 15, 3)] + p[1:] for p in st[sp[0][1]]]
        s["strokes"] = [st[sp[0][0]], bar, st[sp[1][0]]] + [st[j] for j in sp[2]]
        s["spans"] = [[0], [1], list(range(2, 3 + len(sp[2])))]
        TL.retime_sample(s)
        align = ";".join(",".join(map(str, z)) for z in s["spans"])
        c = self.e.start()
        self.begin(c, s)
        _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("fon"), align), why=True)
        c.close()
        self.assertEqual(why[2], "no-structure", why)
        self.assertNotEqual(why[1], "set", why)

    def test_neatest_keeps_the_word_gap(self):
        """two words, the first written narrower than the font sets it: set, it would reach
        into the second; the gap stays at least as wide as written or the font's word gap,
        whichever is less (FR-043)"""
        self.letters(seed=9)
        s = TL.sample("nun nun", random.Random(97))
        x0 = min(p[0] for j in s["spans"][0] for p in s["strokes"][j])
        for sp in s["spans"][:3]:
            for j in sp:
                s["strokes"][j] = [[round(x0 + 0.7 * (p[0] - x0), 3)] + p[1:] for p in s["strokes"][j]]
        # (the second word moved up to the first, a third of an x-height from it)
        r1 = max(p[0] for sp in s["spans"][:3] for j in sp for p in s["strokes"][j])
        l2 = min(p[0] for sp in s["spans"][4:] for j in sp for p in s["strokes"][j])
        for sp in s["spans"][4:]:
            for j in sp:
                s["strokes"][j] = [[round(p[0] - (l2 - r1) + 10.0, 3)] + p[1:] for p in s["strokes"][j]]
        align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
        c = self.e.start()
        self.begin(c, s)
        _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("nun nun"), align), why=True)
        c.close()
        self.assertEqual(why, ["set"] * 3 + ["space"] + ["set"] * 3)
        xy = {o[0]: EV.to_xy(o[2]) for o in out}
        a = max(p[0] for sp in s["spans"][:3] for j in sp for p in xy[j])
        b = min(p[0] for sp in s["spans"][4:] for j in sp for p in xy[j])
        self.assertGreater(b - a, 10.0 - 0.5)

    def test_neatest_keeps_a_stem_joined_into(self):
        """an a joined on into the stem of a b, p, k or t whose other stroke is its own just
        right of the stem (a t's bar written long, wholly right of it): the stem is the
        letter's (taller than its other stroke), no exit tail of the a, and both letters are
        set (FR-043)"""
        self.letters(seed=9, shapes=TL.WHOLE_SHAPES)
        for c in "bpkt":
            with self.subTest(c=c):
                s = TL.sample("a" + c, random.Random(95), shapes=TL.WHOLE_SHAPES)
                st, sp = s["strokes"], s["spans"]
                a, stem = st[sp[0][-1]], st[sp[1][0]]
                if c == "t":
                    # (the bar from just right of the stem, longer than it)
                    x0 = max(q[0] for q in stem) + 1.5
                    y = st[sp[1][1]][0][1]
                    st[sp[1][1]] = [[round(x0 + 3.0 * z, 3), y, 0, 0.5] for z in range(18)]
                u, v = a[-1], stem[0]
                bridge = [[round(u[0] + (v[0] - u[0]) * z / 4, 3), round(u[1] + (v[1] - u[1]) * z / 4, 3), 0, 0.5] for z in range(1, 4)]
                one = a + bridge + stem
                rest = [st[j] for j in sp[1][1:]]
                firsts = [st[j] for j in sp[0][:-1]]
                s["strokes"] = firsts + [one] + rest
                TL.retime_sample(s)
                k = len(firsts)
                na = len(a) + len(bridge)
                align = ";".join([",".join([f"{j}:0-{len(f) - 1}" for j, f in enumerate(firsts)] + [f"{k}:0-{na - 1}"]),
                                  ",".join([f"{k}:{na}-{len(one) - 1}"] + [f"{k + 1 + z}:0-{len(r) - 1}" for z, r in enumerate(rest)])])
                e = self.e.start()
                self.begin(e, s)
                _, out, why = parse(e.ask("neaten", "s", "1", TR.esc("a" + c), align), why=True)
                e.close()
                self.assertEqual(why, ["set", "set"])

    def test_neatest_runs_on_from_kept_ink_into_a_set_letter(self):
        """an F written in two strokes (its glyph in three: it keeps its ink), its middle bar
        run on into the o in one stroke: the o is set, and the stroke goes on from the F into
        the o as written, with no line drawn between where each was placed (FR-043)"""
        self.letters(seed=9, shapes=TL.WHOLE_SHAPES)
        s = TL.sample("Fo", random.Random(94), shapes=TL.WHOLE_SHAPES)
        st, sp = s["strokes"], s["spans"]
        f0, f1, f2 = (st[j] for j in sp[0])
        o = st[sp[1][0]]
        a, b = f2[-1], o[0]
        bridge = [[round(a[0] + (b[0] - a[0]) * z / 4, 3), round(a[1] + (b[1] - a[1]) * z / 4, 3), 0, 0.5] for z in range(1, 4)]
        one = f2 + bridge + o
        s["strokes"] = [list(reversed(f0)) + f1, one]
        TL.retime_sample(s)
        nf = len(f2) + len(bridge)
        align = f"0:0-{len(s['strokes'][0]) - 1},1:0-{nf - 1};1:{nf}-{len(one) - 1}"
        c = self.e.start()
        self.begin(c, s)
        _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("Fo"), align), why=True)
        c.close()
        self.assertEqual(why, ["no-structure", "set"])
        xy = {q[0]: EV.to_xy(q[2]) for q in out}
        raw = math.hypot(one[nf][0] - one[nf - 1][0], one[nf][1] - one[nf - 1][1])
        step = math.hypot(xy[1][nf][0] - xy[1][nf - 1][0], xy[1][nf][1] - xy[1][nf - 1][1])
        self.assertLess(step, raw + 0.25 * 30)
        # the F moved only along the line: its top and bottom where Strong leaves them
        c = self.e.start()
        self.begin(c, s)
        _, strong, _ = parse(c.ask("neaten", "s", "0.75", TR.esc("Fo"), align), why=True)
        c.close()
        xs = {q[0]: EV.to_xy(q[2]) for q in strong}
        fy = [p[1] for p in xy[0]] + [p[1] for p in xy[1][:nf]]
        sy = [p[1] for p in xs[0]] + [p[1] for p in xs[1][:nf]]
        self.assertLess(abs(min(fy) - min(sy)), 0.1 * 30)
        self.assertLess(abs(max(fy) - max(sy)), 0.1 * 30)

    def test_neatest_never_drops_a_letter(self):
        """the reading gives the o only an f's bar written on towards it (the o's own ink went
        to the letter after): the o is still drawn, set on its glyph, as tall as an o, never
        left as a bare bar on the f (FR-043)"""
        self.letters(seed=9, shapes=TL.WHOLE_SHAPES)
        s = TL.sample("fon", random.Random(98), shapes=TL.WHOLE_SHAPES)
        st, sp = s["strokes"], s["spans"]
        bar, o = st[sp[0][1]], st[sp[1][0]]
        a, b = bar[-1], o[0]
        on = [[round(a[0] + (b[0] - a[0]) * z / 6, 3), round(a[1] + (b[1] - a[1]) * z / 6, 3), 0, 0.5] for z in range(1, 7)]
        s["strokes"] = [st[sp[0][0]], bar + on, o[1:] + [p for j in sp[2] for p in st[j]]]
        s["spans"] = [[0], [1], [2]]
        TL.retime_sample(s)
        nb = len(bar)
        align = "0;1;2"
        c = self.e.start()
        self.begin(c, s)
        _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("fon"), align), why=True)
        c.close()
        self.assertEqual(why[:2], ["set", "set"], why)
        xy = {q[0]: EV.to_xy(q[2]) for q in out}
        h = max(p[1] for p in xy[1][nb:]) - min(p[1] for p in xy[1][nb:])
        self.assertGreater(h, 0.5 * 30)
        # given nothing but the bar (all of it over the f), the o keeps it: not left out
        s["strokes"] = [s["strokes"][0], bar, s["strokes"][2]]
        TL.retime_sample(s)
        c = self.e.start()
        self.begin(c, s)
        _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("fon"), align), why=True)
        c.close()
        self.assertNotIn("no-ink", why)

    def test_every_letter_keeps_ink_of_its_own(self):
        """words written in one stroke, ALIGN listing that stroke for every letter (the
        points split between them, a short run spilled over to a neighbour): no letter of
        the text is left without ink of its own, at any strength (FR-043)"""
        self.letters(seed=9)
        rnd = random.Random(99)
        for text in ("minimum.", "a.b.c", "it.is", "loop;", "fix.it"):
            s = TL.sample(text, rnd)
            one = [p for st in s["strokes"] for p in st]
            s["strokes"] = [one]
            TL.retime_sample(s)
            align = ";".join("0" for _ in text)
            for st in ("0.5", "1"):
                c = self.e.start()
                self.begin(c, s)
                _, _, why = parse(c.ask("neaten", "s", st, TR.esc(text), align), why=True)
                c.close()
                self.assertNotIn("no-ink", why, (text, st, why))

    def test_neatest_gives_an_exit_tail_back(self):
        """a's exit tail the reading gives the t, whose own strokes start after a pen lift:
        the tail is the a's, both letters are set and nothing joins them (FR-043)"""
        self.letters(seed=9)
        s = TL.sample("at", random.Random(93))
        sa, st = s["spans"]
        last = s["strokes"][sa[-1]]
        x, y, t = last[-1][0], last[-1][1], last[-1][2]
        tail = [[round(x + 1.2 * z, 3), round(y - 0.8 * z, 3), round(t + 0.01 * z, 3), 0.5] for z in range(1, 6)]
        n0 = len(last)
        last += tail
        TL.retime_sample(s)
        self.assertLess(max(p[0] for p in tail), min(p[0] for j in st for p in s["strokes"][j]))
        mine_a = [f"{j}:0-{len(s['strokes'][j]) - 1}" if j != sa[-1] else f"{j}:0-{n0 - 1}" for j in sa]
        mine_t = [f"{sa[-1]}:{n0}-{n0 + len(tail) - 1}"] + [f"{j}:0-{len(s['strokes'][j]) - 1}" for j in st]
        c = self.e.start()
        self.begin(c, s)
        _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("at"), ",".join(mine_a) + ";" + ",".join(mine_t)), why=True)
        c.close()
        self.assertEqual(why, ["set", "set"])
        # the a's last stroke stays left of the t: no join drawn into it
        xy = {o[0]: EV.to_xy(o[2]) for o in out}
        self.assertLess(max(p[0] for p in xy[sa[-1]]), min(p[0] for j in st for p in xy[j]))

    def test_neatest_keeps_a_bar_written_into_the_next_letter(self):
        """an f's bar written on into the next letter (the start of that one's stroke, which
        the reading gives it alone): set over the next letter's glyph it would be lost, so
        it is the f's, and both letters are set, the f with its bar (FR-043)"""
        self.letters(seed=9, shapes=TL.WHOLE_SHAPES)
        s = TL.run_on("f", "o", random.Random(92), 1)
        align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
        c = self.e.start()
        self.begin(c, s)
        _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("fo"), align), why=True)
        c.close()
        self.assertEqual(why, ["set", "set"])
        # the o's stroke still starts with the bar, from left of the f's stem at its height
        xy = {o[0]: EV.to_xy(o[2]) for o in out}
        stem, bar = xy[0], xy[1][0]
        h = max(p[1] for p in stem) - min(p[1] for p in stem)
        at = [p[0] for p in stem if abs(p[1] - bar[1]) < 0.15 * h]
        self.assertTrue(at)
        self.assertLess(bar[0], min(at))

    @unittest.skipIf(TL.SKIP_TOOLS or not TL.shutil.which("hb-shape"), "needs fontTools and hb-shape")
    def test_neatest_runs_along_the_join(self):
        """at Neatest the ink the person didn't lift between two letters the font joins runs
        along the font's join (FR-043, FR-044): the set word's points lie in the ink the font
        draws for it, its halves of the join with it (the font's glyphs for it the join's
        alternates); without the model's joins it is set otherwise"""
        rnd = random.Random(71)
        more = [TL.joined(TL.sample("er", rnd), [0]) for _ in range(5)] + [TL.sample("er", rnd) for _ in range(3)]
        self.letters(seed=7, more=more)
        model = self.e.store / "letters" / "model"
        self.assertIn('"pair":"er"', model.read_text())
        s = TL.joined(TL.sample("er", random.Random(72)), [0])
        align = ";".join(",".join(map(str, sp)) for sp in s["spans"])

        def inside():
            from fontTools.ttLib import TTFont
            from fontTools.pens.recordingPen import DecomposingRecordingPen
            ttf = self.e.dir / "join.ttf"
            self.e.run("font", "build", "-n", "Join Test", str(ttf))
            f = TTFont(str(ttf))
            gs, order = f.getGlyphSet(), f.getGlyphOrder()
            rings, x = [], 0
            for g in TL.hb(ttf, "er"):
                pen = DecomposingRecordingPen(gs)
                gs[order[g["g"]]].draw(pen)
                for op, a in pen.value:
                    if op == "moveTo":
                        rings.append([])
                    rings[-1] += [((x + g["dx"] + px) / 500, py / 500) for px, py in a]
                x += g["ax"]
            c = self.e.start()
            self.begin(c, s)
            _, out, why = parse(c.ask("neaten", "s", "1", TR.esc("er"), align), why=True)
            c.close()
            self.assertEqual(why, ["set", "set"])
            pts = [(px, -py) for o in out for px, py in EV.to_xy(o[2])]
            ink = next(json.loads(ln)["metrics"].get("ink_width", 0.07) for ln in model.read_text().splitlines() if '"metrics"' in ln)
            r = max(ink / 2, 0.012)
            # the points' box onto the ink's box less the ink's half width all round
            ox = [p[0] for rg in rings for p in rg]
            oy = [p[1] for rg in rings for p in rg]
            px, py = [p[0] for p in pts], [p[1] for p in pts]
            sc = ((max(ox) - min(ox) - 2 * r) / (max(px) - min(px)) + (max(oy) - min(oy) - 2 * r) / (max(py) - min(py))) / 2

            def wind(qx, qy):
                w = 0
                for rg in rings:
                    for (ax, ay), (bx, by) in zip(rg, rg[1:] + rg[:1]):
                        if ay <= qy < by and (bx - ax) * (qy - ay) - (qx - ax) * (by - ay) > 0:
                            w += 1
                        elif by <= qy < ay and (bx - ax) * (qy - ay) - (qx - ax) * (by - ay) < 0:
                            w -= 1
                return w
            mx, my = (min(ox) + max(ox)) / 2, (min(oy) + max(oy)) / 2
            cx, cy = (min(px) + max(px)) / 2, (min(py) + max(py)) / 2
            alts = all(g["g"] > max(TL.font_glyphs(model.read_text())) for g in TL.hb(ttf, "er"))
            return sum(wind(mx + (a - cx) * sc, my + (b - cy) * sc) != 0 for a, b in pts) / len(pts), alts, pts
        joined, alts, pj = inside()
        lines = model.read_text().splitlines()
        model.write_text("".join(ln + "\n" for ln in lines if not ln.startswith('{"joins"')))
        _, alts2, ps = inside()
        self.assertTrue(alts)
        self.assertFalse(alts2)
        self.assertGreater(joined, 0.95)
        self.assertGreater(max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(pj, ps)), 0.5)

    def test_neatest_keeps_what_the_font_lacks(self):
        """a character the letter model doesn't cover (here punctuation) keeps its ink as
        written in a set word (FR-043): moved, not reshaped, into the box the font has for it
        (its .notdef, one x-height wide), the word's other characters set around it"""
        self.letters()
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        rnd = random.Random(51)
        seen = 0
        for text in ("hi, you.", "a-b c;d"):
            s = TL.sample(text, rnd)
            lay, _ = self.set_layout(text)
            align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
            res = {}
            for st in ("0.75", "1"):
                c = self.e.start()
                self.begin(c, s)
                _, out, why = parse(c.ask("neaten", "s", st, TR.esc(text), align), why=True)
                c.close()
                res[st] = ([EV.to_xy(o[2]) for o in out], why)
            xy, why = res["1"]
            k0 = 0
            for w in text.split(" "):
                ks = range(k0, k0 + len(w))
                k0 += len(w) + 1
                miss = [k for k in ks if lay[k] is None]
                for k in ks:
                    self.assertEqual(why[k], "not-covered" if k in miss else "set", (text, k, why))
                wp = [p for k in ks if k not in miss for j in s["spans"][k] for p in xy[j]]
                wg = [lay[k] for k in ks if k not in miss for j in s["spans"][k] for _ in xy[j]]
                rms, size, _, x0 = on_set(wp, wg)
                self.assertLess(rms, 0.003, (text, w))
                for k in miss:
                    # moved only: as Strong left it, all its points by one step along the line
                    a = [p for j in s["spans"][k] for p in xy[j]]
                    b = [p for j in s["spans"][k] for p in res["0.75"][0][j]]
                    dx = [p[0] - q[0] for p, q in zip(a, b)]
                    self.assertLess(max(dx) - min(dx), 0.01)
                    self.assertTrue(all(abs(p[1] - q[1]) < 0.01 for p, q in zip(a, b)))
                    # in the middle of its box
                    mid = (min(p[0] for p in a) + max(p[0] for p in a)) / 2
                    self.assertAlmostEqual((mid - x0) / size, self.pens[k] + 0.5, delta=0.02)
                    seen += 1
        self.assertEqual(seen, 4)

    def test_neatest_puts_what_the_font_lacks_on_its_base_glyph(self):
        """with Autograph's base a character the letter model doesn't cover is in the box of
        the glyph the font draws there from the base (FR-043, FR-045): at that glyph's advance
        and kerning, as hb-shape puts it, not at .notdef's; its ink moved only"""
        if TL.SKIP_TOOLS or not TL.shutil.which("hb-shape"):
            self.skipTest("needs hb-shape")
        self.letters()
        self.e.env["GLASSINE_HANDWRITING_BASE"] = str(TL.write_base(self.e.dir / "base.jsonl"))
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        rnd = random.Random(51)
        seen = off = 0
        for text in ("hi, you.", "a-b c;d"):
            s = TL.sample(text, rnd)
            lay, how = self.set_layout(text)
            self.assertEqual(how, "hb-shape")
            ttf = self.e.dir / "set.ttf"
            # the ink's middle of each glyph as the font draws it (x-heights from the text's start)
            g = json.loads(subprocess.run(["hb-shape", "--no-glyph-names", "--output-format=json", "--show-extents",
                                           str(ttf), text], capture_output=True, text=True, check=True).stdout)
            mids, x = {}, 0
            for e in g:
                mids[e["cl"]] = (x + e["dx"] + e["xb"] + e["w"] / 2) / 500
                x += e["ax"]
            align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
            res = {}
            for st in ("0.75", "1"):
                c = self.e.start()
                self.begin(c, s)
                _, out, why = parse(c.ask("neaten", "s", st, TR.esc(text), align), why=True)
                c.close()
                res[st] = ([EV.to_xy(o[2]) for o in out], why)
            xy, why = res["1"]
            k0 = 0
            for w in text.split(" "):
                ks = range(k0, k0 + len(w))
                k0 += len(w) + 1
                miss = [k for k in ks if lay[k] is None]
                self.assertTrue(all(why[k] == ("not-covered" if k in miss else "set") for k in ks), (text, why))
                wp = [p for k in ks if k not in miss for j in s["spans"][k] for p in xy[j]]
                wg = [lay[k] for k in ks if k not in miss for j in s["spans"][k] for _ in xy[j]]
                rms, size, _, x0 = on_set(wp, wg)
                self.assertLess(rms, 0.003, (text, w))
                for k in miss:
                    a = [p for j in s["spans"][k] for p in xy[j]]
                    b = [p for j in s["spans"][k] for p in res["0.75"][0][j]]
                    dx = [p[0] - q[0] for p, q in zip(a, b)]
                    self.assertLess(max(dx) - min(dx), 0.01)
                    self.assertTrue(all(abs(p[1] - q[1]) < 0.01 for p, q in zip(a, b)))
                    # in the middle of the base glyph the font draws there
                    mid = (min(p[0] for p in a) + max(p[0] for p in a)) / 2
                    self.assertAlmostEqual((mid - x0) / size, mids[k], delta=0.02, msg=(text, k))
                    # (not where .notdef's box would have it)
                    off += abs(mids[k] - (self.pens[k] + 0.5)) > 0.05
                    seen += 1
        self.assertEqual(seen, 4)
        self.assertGreater(off, 0)

    def test_the_base_is_never_a_target(self):
        """Autograph's base (FR-045) is not the person's: a character only it draws is
        neatened as one the font lacks, at every level, whatever the base has for it; the
        ink is the same with the base as without, at Neatest moved only, into the base
        glyph's box (FR-014, FR-022, FR-035, FR-043)"""
        self.letters()
        base = TL.write_base(self.e.dir / "base.jsonl")
        rnd = random.Random(61)
        for text in ("hi, you.", "a-b c;d", "hello world"):
            s = TL.sample(text, rnd)
            align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
            got = {}
            for b in ("off", str(base)):
                self.e.env["GLASSINE_HANDWRITING_BASE"] = b
                self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
                c = self.e.start()
                self.begin(c, s)
                got[b] = [parse(c.ask("neaten", "s", st, TR.esc(text), align), why=True)
                          for st in ("0.25", "0.5", "0.75", "1")]
                c.close()
            # (below Neatest the same ink; at Neatest a character only the base draws sits in
            # the base glyph's box, so the ink moves along the line, each character's as one)
            self.assertEqual(got["off"][:3], got[str(base)][:3], text)
            (r0, o0, w0), (r1, o1, w1) = got["off"][3], got[str(base)][3]
            self.assertEqual((r0, w0), (r1, w1), text)
            for k, sp in enumerate(s["spans"]):
                pa = [p for j in sp for p in EV.to_xy(o0[j][2])]
                pb = [p for j in sp for p in EV.to_xy(o1[j][2])]
                if not pa:
                    continue
                self.assertEqual([o0[j][1] for j in sp], [o1[j][1] for j in sp], text)
                dx = [p[0] - q[0] for p, q in zip(pa, pb)]
                self.assertLess(max(dx) - min(dx), 0.01, (text, k))
                self.assertTrue(all(abs(p[1] - q[1]) < 0.01 for p, q in zip(pa, pb)), (text, k))
            for _, _, why in got[str(base)]:
                for k, ch in enumerate(text):
                    if ch in ",.;-":
                        self.assertIn(why[k], ("not-covered", "level"), (text, k, why))
        self.e.env["GLASSINE_HANDWRITING_BASE"] = "off"

    def test_neatest_without_the_text(self):
        """without the text no word is set (FR-043: a word read wrongly would be set as
        another word): the ink is Strong's; with the text (and ALIGN: this model's own
        alignment gives a letter no ink) the words are set"""
        self.letters()
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        for seed, text in enumerate(("hello world", "the tree")):
            s = TL.sample(text, random.Random(52 + seed))
            c = self.e.start()
            self.begin(c, s)
            read, out, why = parse(c.ask("neaten", "s", "1"), why=True)
            _, strong = parse(c.ask("neaten", "s", "0.75"))
            align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
            _, _, known = parse(c.ask("neaten", "s", "1", text, align), why=True)
            c.close()
            self.assertTrue(read)
            self.assertNotIn("set", why)
            self.assertEqual([o[2] for o in out], [o[2] for o in strong])
            self.assertIn("set", known)

    def test_levels(self):
        """the four levels (FR-014): Light moves no letter's shape, only the line; Medium
        pulls every covered letter whose glyph has its structure half way to the glyph, also
        a badly written one; Strong further; Neatest gives each the glyph's shape"""
        L = self.letters()
        for ax, ay, lean in ((1.5, 1.8, 0.4), (1.4, 1.9, 0.5)):
            rnd = random.Random(21)
            s = TL.sample("World", rnd)
            for st in s["strokes"]:
                for p in st:
                    p[0] += rnd.gauss(0, 0.5)
                    p[1] += rnd.gauss(0, 0.5)
            # the o written badly: larger, taller and leaning, its strokes still the o's
            for j in s["spans"][1]:
                pts = s["strokes"][j]
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                for p in pts:
                    dx, dy = p[0] - cx, p[1] - cy
                    p[0] = round(cx + ax * dx - lean * dy, 3)
                    p[1] = round(cy + ay * dy, 3)
            self.levels(L, s, 1)

    def levels(self, L, s, bad):
        raw = [[(p[0], p[1]) for p in st] for st in s["strokes"]]
        res = {}
        for lv in (0.25, 0.5, 0.75, 1):
            res[lv] = self.neaten_letters(s, lv)
            if lv == 0.5:
                self.why5 = self.why
        gl = self.glyphs(s["text"])
        ks = [k for k, g in enumerate(gl) if g is not None]

        def dist(xy, k, size=False):
            """to the font's glyph there: in shape, or (size) in x-heights, as written"""
            ink = [xy[j] for j in s["spans"][k]]
            return EV.vdist(ink, 30.0, gl[k]) if size else shape_dist(ink, gl[k])

        def pulled(how, k):
            return all(how[j] == "pulled" for j in s["spans"][k])

        before = {k: dist(raw, k) for k in ks}
        how, xy = res[0.25]
        self.assertNotIn("pulled", how)
        for k in ks:
            # the line only: each letter's ink keeps its shape (moved, scaled, sheared)
            self.assertAlmostEqual(dist(xy, k), before[k], delta=0.05)
        how5, xy5 = res[0.5]
        # every letter half way, the bad one too
        self.assertTrue(all(pulled(how5, k) for k in ks))
        self.assertLess(dist(xy5, bad, True), 0.8 * dist(raw, bad, True))
        how7, xy7 = res[0.75]
        self.assertTrue(pulled(how7, bad))
        self.assertLess(dist(xy7, bad, True), 0.6 * dist(raw, bad, True))
        self.assertLess(on_curve([xy7[j] for j in s["spans"][bad]], gl[bad]),
                        on_curve([xy5[j] for j in s["spans"][bad]], gl[bad]))
        how1, xy1 = res[1]
        n = 0
        for k in ks:
            if pulled(how1, k):
                # (a letter already that near keeps its few points: on the glyph, chords between)
                self.assertLess(dist(xy1, k), max(before[k], 0.09))
                if pulled(how7, k):
                    self.assertLessEqual(dist(xy1, k), dist(xy7, k) + 0.01)
                # the glyph itself, on its own points, all of it
                ink1 = [xy1[j] for j in s["spans"][k]]
                self.assertLess(on_curve(ink1, gl[k]), 0.003)
                self.assertTrue(all(a < 0.12 and b < 0.12 for a, b in on_curve(ink1, gl[k], cover=True)), k)
                n += 1
        self.assertGreaterEqual(n, 4)

    def test_the_fonts_glyph_is_the_target(self):
        """the letters are pulled to the glyph the font draws there (FR-035): the variant
        itself, not the favourite-weighted blend of it, at each place the one the font's
        cycle and rules give"""
        L = self.letters()
        model = self.e.store / "letters" / "model"
        e = self.e
        ids = L["o"]["variants"][0]["ids"]
        e.run("letters", "mark", "favourite", "o", ids[0])
        e.run("letters", "build", "-c", "o")
        L = {m["char"]: m for m in map(json.loads, model.read_text().splitlines()) if "char" in m}
        v = L["o"]["variants"][0]
        self.assertIn("best", v)
        rnd = random.Random(22)
        s = TL.sample("oo", rnd)
        _, xy = self.neaten_letters(s, 1)
        gl = self.glyphs("oo")
        for k in range(2):
            ink = [xy[j] for j in s["spans"][k]]
            if self.why[k] not in ("pulled", "set"):
                continue
            # its points on the glyph itself, moved and scaled only, not on the blend
            self.assertLess(on_curve(ink, gl[k]), 0.003)
            self.assertGreater(on_curve(ink, {"strokes": v["best"]}), 2 * on_curve(ink, gl[k]))
        self.assertIn("set", self.why)

    def test_letter_check(self):
        """a pulled letter whose new ink would come closer to another character is put back
        to the line level, the other letters keep their pull, and the reply says why
        (FR-037, FR-041; the knob's guard= has those characters fail the check)"""
        self.letters()
        s = TL.sample("hello", random.Random(24))
        how0, _ = self.neaten_letters(s, 0.75)
        why0 = self.why
        for mis in ("o", "hel", "lo"):
            how, _ = self.neaten_letters(s, 0.75, knob="guard=" + mis)
            for k, ch in enumerate(s["text"]):
                if ch in mis:
                    self.assertTrue(all(how[j] != "pulled" for j in s["spans"][k]), (mis, how))
                    if why0[k] == "pulled":
                        self.assertEqual(self.why[k], "shape-guard")
                elif why0[k] == "pulled":
                    self.assertEqual(self.why[k], "pulled")
                    self.assertTrue(all(how[j] == "pulled" for j in s["spans"][k]))
        self.assertGreater(why0.count("pulled"), 2)

    def test_shape_guard(self):
        """no pulled character comes out nearer another character's versions than its own
        (SC-004), at every level, whatever their strokes; unless written nearer it already,
        and then no nearer than as written"""
        L = self.letters()
        rnd = random.Random(25)
        n = 0
        for text in ("hello world", "quick brown", "Zz9 jump"):
            s = TL.sample(text, rnd)
            for st in s["strokes"]:
                for p in st:
                    p[0] += rnd.gauss(0, 1.5)
                    p[1] += rnd.gauss(0, 1.5)
            for lv in (0.5, 0.75, 1):
                how, xy = self.neaten_letters(s, lv)
                # the guard measures at the line's x-height: the neatened small letters' height
                xh = EV.line_xh(dict(s, strokes=[[list(p) for p in st] for st in xy])) or 30.0
                xr = EV.line_xh(s) or 30.0

                def ratio(ink, xh, ch):
                    mine = min((guard_dist(ink, xh, v) for v in L[ch]["variants"] if not v.get("left_out")),
                               default=math.inf)
                    other = min((guard_dist(ink, xh, v) for c2, le in L.items() if c2 != ch and le.get("covered")
                                 for v in le["variants"] if not v.get("left_out")), default=math.inf)
                    return mine / other

                for k, ch in enumerate(text):
                    if self.why[k] != "pulled":
                        continue
                    rn = ratio([xy[j] for j in s["spans"][k]], xh, ch)
                    if rn > 1.02:
                        rr = ratio([[(p[0], p[1]) for p in s["strokes"][j]] for j in s["spans"][k]], xr, ch)
                        self.assertLess(rn, rr + 0.05, (text, ch, lv))
                    n += 1
        self.assertGreater(n, 20)

    def test_word_left_out_of_the_dictionary(self):
        """a word the dictionary lacks (a name) doesn't hold back the line: the other words
        are neatened; only a word read with low confidence gets the line level (FR-017)"""
        self.letters()
        s = TL.sample("hello my name is Qxzvk", random.Random(26))
        # with the text and its spans: the name and the other words are pulled
        how, _ = self.neaten_letters(s, 1)
        words = "".join(" " if w == "space" else "p" if w in ("pulled", "set") else "-" for w in self.why).split(" ")
        self.assertTrue(all("p" in w for w in words), self.why)
        # without it (the reading of this random-weight model is low confidence throughout):
        # a reason for every character read
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        c = self.e.start()
        self.begin(c, s)
        parse(c.ask("neaten", "s", "1"), why=True)
        c.close()

    def test_flow_at_starts_and_joins(self):
        """at every level that pulls (Neatest sets the word: FR-043), no new step back and no new kink where a stroke starts (with an
        entry stroke or a hook the version lacks) or where it passes from one letter to the
        next (FR-036), over every character and every joined pair of small letters, also
        where the alignment cuts a joined stroke elsewhere than written"""
        self.letters()
        rnd = random.Random(27)
        xh = 30.0

        def lead_in(st, kind):
            # an entry stroke from below left, or a short hook back into the start
            (x0, y0), (x1, y1) = st[0][:2], st[1][:2]
            d = math.hypot(x1 - x0, y1 - y0) or 1
            ux, uy = (x1 - x0) / d, (y1 - y0) / d
            if kind == 0:
                pts = [(x0 - 0.45 * xh * (1 - i / 6) * 0.7 - 0.1 * xh * (1 - i / 6), y0 + 0.4 * xh * (1 - i / 6))
                       for i in range(6)]
            else:
                pts = [(x0 - ux * 0.12 * xh * math.sin(math.pi * i / 6) + uy * 0.06 * xh * (1 - i / 6),
                        y0 - uy * 0.12 * xh * math.sin(math.pi * i / 6) - ux * 0.06 * xh * (1 - i / 6)) for i in range(6)]
            t = st[0][2]
            return [[round(x, 3), round(y, 3), round(t - 0.01 * (6 - i), 3), 0.5] for i, (x, y) in enumerate(pts)] + st

        def joined(text):
            # the words' letters joined: the last stroke of one and the first of the next one
            # stroke, a short bridge between them; ALIGN with the points of each
            s = TL.sample(text, rnd)
            # each letter written a little off its versions (taller or wider, higher or
            # lower), so that the two letters' pulls differ where they meet
            for k, sp in enumerate(s["spans"]):
                pts = [p for z in sp for p in s["strokes"][z]]
                if not pts:
                    continue
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                sx, sy, dy = (1.4, 1.0, 0.35 * xh) if k % 3 == 0 else (1.0, 1.4, -0.35 * xh) if k % 3 == 1 else (1, 1, 0)
                for p in pts:
                    p[0] = round(cx + (p[0] - cx) * sx, 3)
                    p[1] = round(cy + (p[1] - cy) * sy + dy, 3)
            strokes, align = [], []
            for k, ch in enumerate(text):
                if ch == " ":
                    align.append([])
                    continue
                own = [str(len(strokes) + z) for z in range(len(s["spans"][k]))]
                if k and text[k - 1] != " ":
                    prev = strokes.pop()
                    first = [list(p) for p in s["strokes"][s["spans"][k][0]]]
                    a, b = prev[-1], first[0]
                    bridge = [[round(a[0] + (b[0] - a[0]) * i / 4, 3), round(a[1] + (b[1] - a[1]) * i / 4, 3), a[2], 0.5]
                              for i in range(1, 4)]
                    j = len(strokes)
                    cut = len(prev) + 1
                    align[-1][-1] = f"{j}:0-{cut}"
                    strokes.append(prev + bridge + first)
                    own = [f"{j}:{cut + 1}-{len(strokes[-1]) - 1}"] + [str(j + z) for z in range(1, len(s["spans"][k]))]
                    for z in range(1, len(s["spans"][k])):
                        strokes.append([list(p) for p in s["strokes"][s["spans"][k][z]]])
                else:
                    for z in s["spans"][k]:
                        strokes.append([list(p) for p in s["strokes"][z]])
                align.append(own)
            for z, st in enumerate(strokes):
                t0 = z * 1.0
                for i, p in enumerate(st):
                    p[2] = round(t0 + 0.01 * i, 3)
            return dict(s, strokes=strokes), ";".join(",".join(a) for a in align)

        lines = []
        for i in range(0, len(TL.ALNUM), 8):
            s = TL.sample(" ".join(TL.ALNUM[i:i + 8]), rnd)
            for k in range(len(s["text"])):
                if s["spans"][k]:
                    j = s["spans"][k][0]
                    s["strokes"][j] = lead_in(s["strokes"][j], (i + k) % 2)
            lines.append((s, ";".join(",".join(map(str, sp)) for sp in s["spans"])))
        small = "abcdefghijklmnopqrstuvwxyz"
        pairs = [a + b for a in small for b in small]
        for i in range(0, len(pairs), 12):
            lines.append(joined(" ".join(pairs[i:i + 12])))
        new, pulled, where = 0, 0, {}
        # with ALIGN, and (the joined lines) with the text alone: the strokes cut where the
        # alignment puts the letters' edges, which can give a letter part of its neighbour
        runs = [(li, s, align) for li, (s, align) in enumerate(lines)] + \
            [(li, s, None) for li, (s, align) in enumerate(lines) if li >= 8]
        for li, s, align in runs:
            for lv in (0.25, 0.5, 0.75):
                self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
                c = self.e.start()
                self.begin(c, s)
                _, out, why = parse(c.ask("neaten", "s", str(lv), TR.esc(s["text"]), *([align] if align else [])), why=True)
                c.close()
                pulled += why.count("pulled")
                for j, o in enumerate(out):
                    raw = [(p[0], p[1]) for p in s["strokes"][j]]
                    b, k = flow(raw, EV.to_xy(o[2]), xh)
                    new += b + k
                    if b + k:
                        key = (lv, "single" if li < 8 else "joined" if align else "joined, own alignment")
                        where[key] = where.get(key, 0) + b + k
        self.assertGreater(pulled, 1000)
        self.assertEqual(new, 0, where)

    def test_point_aligner(self):
        """with a point aligner beside the model (LANG/aligner.ghm, here of random weights) the
        joined strokes are cut by it, by ALIGN of whole strokes and by the text alone: every
        stroke comes back with its points, their times as sent, every letter with ink of its
        own, the same request the same ink; GLASSINE_HANDWRITING_ALIGNER=off gives what no
        aligner gives"""
        self.letters()
        rnd = random.Random(37)
        small = "abcdefghijklmnopqrstuvwxyz"
        lines = [joined_line(rnd, ["".join(rnd.choice(small) for _ in range(rnd.randint(2, 4))) for _ in range(3)]) for _ in range(4)]
        al = self.e.models / "en" / "aligner.ghm"

        def run(lv, text, align):
            outs = []
            for s, at, _ in lines:
                self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
                c = self.e.start()
                self.begin(c, s)
                a = [align(at)] if align else []
                r = parse(c.ask("neaten", "s", str(lv), TR.esc(s["text"]), *a), why=True)
                c.close()
                outs.append((s, r))
            return outs

        whole = lambda at: ";".join(",".join(sorted({str(j) for j, _, _ in m}, key=int)) for m in at)
        plain = {lv: run(lv, True, whole) for lv in (0.75, 1)}
        subprocess.run([sys.executable, str(HERE / "mkmodel.py"), str(al), "--strided", "1", "--seed", "5", "--kind", "aligner"],
                       check=True)
        changed = 0
        try:
            for lv in (0.75, 1):
                for align in (whole, None):
                    got = run(lv, True, align)
                    changed += align is whole and got != plain[lv]
                    self.assertEqual(got, run(lv, True, align))
                    for s, (_, out, why) in got:
                        self.assertEqual(len(out), len(s["strokes"]))
                        for st, o in zip(s["strokes"], out):
                            self.assertEqual(len(EV.to_xy(o[2])), len(st))
                        if align:
                            self.assertNotIn("no-ink", why, (s["text"], lv))
                self.e.env["GLASSINE_HANDWRITING_ALIGNER"] = "off"
                self.assertEqual(run(lv, True, whole), plain[lv])
                del self.e.env["GLASSINE_HANDWRITING_ALIGNER"]
        finally:
            al.unlink()
        self.assertGreater(changed, 0)   # (the aligner was used)

    def test_installed_aligner(self):
        """spec 006: an aligner the person installs (model install) neatens as the same file
        shipped does, while the shipped recognizer reads; chosen back to shipped (no aligner
        shipped here), neatening is as without one"""
        self.letters()
        rnd = random.Random(37)
        small = "abcdefghijklmnopqrstuvwxyz"
        lines = [joined_line(rnd, ["".join(rnd.choice(small) for _ in range(rnd.randint(2, 4))) for _ in range(3)])
                 for _ in range(4)]
        whole = lambda at: ";".join(",".join(sorted({str(j) for j, _, _ in m}, key=int)) for m in at)  # noqa: E731

        def run():
            outs = []
            for lv in ("0.75", "1"):
                for s, at, _ in lines:
                    self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
                    c = self.e.start()
                    self.begin(c, s)
                    outs.append(parse(c.ask("neaten", "s", lv, TR.esc(s["text"]), whole(at)), why=True))
                    c.close()
            return outs
        plain = run()
        al = self.e.dir / "installed-aligner.ghm"
        subprocess.run([sys.executable, str(HERE / "mkmodel.py"), str(al), "--aligner", "--seed", "5"], check=True)
        shipped = self.e.models / "en" / "aligner.ghm"
        shutil.copy(al, shipped)
        try:
            as_shipped = run()
        finally:
            shipped.unlink()
        self.assertTrue(as_shipped != plain, "the aligner changed nothing")
        self.assertEqual(self.e.run("model", "install", str(al)).stdout, "installed\taligner\ten\n")
        self.assertTrue(run() == as_shipped, "installed != shipped")
        self.e.run("model", "use", "aligner", "shipped")
        self.assertTrue(run() == plain, "shipped chosen != no aligner")

    def test_joined_words_keep_every_letter(self):
        """joined words of two to five letters (one stroke through them, as written in a
        hand), with ALIGN by points, with ALIGN that lists the joined stroke whole for each of
        its letters (it holds them, but not where one ends), and with the text alone: at every
        level no new step back or kink (at the joints or elsewhere), every letter keeps its ink
        at about its own size (its box at least half its glyph's) and in its own place (the
        letters' middles in the order written, none on a neighbour's); a word set at Neatest is
        the font's (FR-043); at Strong and Neatest the letters of joined words (ALIGN by points)
        are pulled about as often as the same letters written apart (FR-036: the joining stroke
        is drawn anew, a joint holds no letter back)"""
        self.letters()
        rnd = random.Random(31)
        xh = 30.0
        small = "abcdefghijklmnopqrstuvwxyz"

        lines = [joined_line(rnd, ["".join(rnd.choice(small) for _ in range(rnd.randint(2, 5))) for _ in range(4)]) for _ in range(8)]
        checked, new, where = 0, 0, {}
        rate = {}
        for s, at, apart in lines:
            text = s["text"]
            by_points = ";".join(",".join(f"{j}:{a}-{b}" for j, a, b in m) for m in at)
            whole = ";".join(",".join(sorted({str(j) for j, _, _ in m}, key=int)) for m in at)
            glyphs = self.glyphs(text)
            for align in (by_points, whole, None):
                for lv in (0.25, 0.5, 0.75, 1):
                    self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
                    c = self.e.start()
                    self.begin(c, s)
                    _, out, why = parse(c.ask("neaten", "s", str(lv), TR.esc(text), *([align] if align else [])), why=True)
                    c.close()
                    self.assertEqual(len(why), len(text))
                    xy = [EV.to_xy(o[2]) for o in out]
                    form = "points" if align == by_points else "whole" if align else "none"
                    for j, st in enumerate(s["strokes"]):
                        b, k = flow([(p[0], p[1]) for p in st], xy[j], xh)
                        new += b + k
                        if b + k:
                            where[(lv, form)] = where.get((lv, form), 0) + b + k
                    if lv >= 0.75:
                        r = rate.setdefault((lv, form), [0, 0])
                        r[0] += sum(w in ("pulled", "set") for w in why)
                        r[1] += sum(w != "space" for w in why)
                    mids, word = [], []
                    for k, m in enumerate(at):
                        if not m:
                            mids.append(word)
                            word = []
                            continue
                        pts = [xy[j][i] for j, a, b in m for i in range(a, b + 1)]
                        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
                        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
                        if why[k] != "set":
                            word.append((x0 + x1) / 2)
                        g = glyphs[k]
                        if g is None or why[k] == "set":
                            continue
                        gx = [p[0] for st in g["strokes"] for p in st]
                        gy = [p[1] for st in g["strokes"] for p in st]
                        gs = max(max(gx) - min(gx), max(gy) - min(gy))
                        # (a letter written smaller than half its glyph: half its own size)
                        raw = [s["strokes"][j][i] for j, a, b in m for i in range(a, b + 1)]
                        rs = max(max(p[0] for p in raw) - min(p[0] for p in raw), max(p[1] for p in raw) - min(p[1] for p in raw)) / xh
                        self.assertGreaterEqual(max(x1 - x0, y1 - y0) / xh, 0.5 * min(gs, rs),
                                                (lv, "points" if align == by_points else "whole" if align else "none", k, why[k], rs, gs))
                        checked += 1
                    mids.append(word)
                    for wm in mids:
                        self.assertEqual(wm, sorted(wm), (lv, align is None))
            # the same letters written apart
            for lv in (0.75, 1):
                self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
                c = self.e.start()
                self.begin(c, apart)
                _, out, why = parse(c.ask("neaten", "s", str(lv), TR.esc(text), ";".join(",".join(map(str, sp)) for sp in apart["spans"])), why=True)
                c.close()
                r = rate.setdefault((lv, "apart"), [0, 0])
                r[0] += sum(w in ("pulled", "set") for w in why)
                r[1] += sum(w != "space" for w in why)
        self.assertGreater(checked, 1000)
        self.assertEqual(new, 0, where)
        share = {key: p / n for key, (p, n) in rate.items()}
        # (with ALIGN by points: whole or without ALIGN the strokes are cut by the
        # recognizer's alignment, of a model with random weights here)
        # (at Strong within 0.06: a joined letter whose matched places stop short of its
        # glyph's end at the joint is traced by the length along it, and these synthetic
        # letters have 12 points to a stroke, about 0.2 x-heights apart; where the version
        # keeps its samples' sharp turn (FR-006) that trace turns by more than a right angle
        # at one point where the coarse ink turns by less, so the flow rule holds it back:
        # two t's in this set. Written apart the same points match the turn whole.)
        for lv, slack in ((0.75, 0.06), (1, 0.05)):
            self.assertGreaterEqual(share[(lv, "points")], share[(lv, "apart")] - slack, share)

    def test_joined_pairs_and_marks(self):
        """letters written joined in one stroke (st, ch, ri, as inside words), ALIGN by points:
        at Strong and Neatest each letter of such a pair is pulled or set (the stroke cut where
        its letters meet, the join drawn anew; at Strong the shape guard may hold back one whose
        pull part of the way passes nearer another letter's versions), with no new kink or step
        back and every letter kept and in order; at Neatest each word set in the font (FR-043:
        each letter's points on the glyph the font sets for it, each of the word's glyph strokes
        reached at both ends by the word's ink); the full pull (Neatest without setting the
        words, set=0, the place every pull is part of the way to) draws each glyph whole in its
        letter's ink; an i's dot, written aside, sits at Medium and Strong where the full pull
        has it on the pulled stem (FR-036)"""
        sh = {"i": TL.dotted_i}
        self.letters(shapes=sh)

        def aside(c, style, rnd):
            # (an i with its dot written a quarter x-height high and to the left)
            stem, dot = TL.dotted_i(c, style, rnd)
            return [stem, [(x - 0.25, y + 0.25) for x, y in dot]]
        rnd = random.Random(33)
        xh = 30.0
        words = ["past", "chin", "brim", "has", "rich", "cast", "arise", "chase", "first", "this"]
        pairs = ("st", "ch", "ri", "as")
        n = drawn = held = setw = 0
        mark = {}
        for i in range(0, len(words), 5):
            s, at, _ = joined_line(rnd, words[i:i + 5], shapes={"i": aside})
            text = s["text"]
            align = ";".join(",".join(f"{j}:{a}-{b}" for j, a, b in m) for m in at)
            glyphs = self.glyphs(text)
            lay, how_laid = self.set_layout(text)
            for lv, knob in (("0.5", ""), ("0.75", ""), ("1", ""), ("full", "set=0")):
                self.e.env["GLASSINE_HANDWRITING_NEATEN"] = knob
                c = self.e.start()
                self.begin(c, s)
                _, out, why = parse(c.ask("neaten", "s", "1" if lv == "full" else lv, TR.esc(text), align), why=True)
                c.close()
                xy = [EV.to_xy(o[2]) for o in out]
                for j, st in enumerate(s["strokes"]):
                    self.assertEqual(sum(flow([(p[0], p[1]) for p in st], xy[j], xh)), 0, (lv, j))
                for k in range(len(text) - 1):
                    if text[k:k + 2] not in pairs or lv == "0.5":
                        continue
                    for z in (k, k + 1):
                        # (the shape guard may hold back one whose pull, part of the way,
                        # passes nearer another letter's versions: one in this line)
                        if why[z] != ("set" if lv == "1" else "pulled"):
                            self.assertEqual((lv, why[z]), ("0.75", "shape-guard"), text[k:k + 2])
                            held += 1
                        else:
                            n += 1
                mids = []
                for k, m in enumerate(at):
                    if not m:
                        self.assertEqual(mids, sorted(mids), lv)
                        mids = []
                        continue
                    pts = [xy[j][z] for j, a, b in m for z in range(a, b + 1)]
                    mids.append((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2)
                    g = glyphs[k]
                    if g is None or why[k] != "pulled":
                        continue
                    # in the full pull its glyph is drawn whole in its ink (each point of the
                    # glyph on the ink, once moved and scaled onto it; the joining stroke drawn
                    # anew beside it isn't the glyph's)
                    full = [[(xy[j][z][0] / xh, -xy[j][z][1] / xh) for z in range(a, b + 1)] for j, a, b in m]
                    if lv == "full" and len(full) == len(g["strokes"]):
                        rev = on_curve([[(p[0], -p[1]) for p in st] for st in g["strokes"]], {"strokes": full})
                        self.assertLess(rev, 0.05, text[k])
                        drawn += 1
                    # the mark: a stroke of its own, not the glyph's tallest, its offset from
                    # the stem (its points' median x, their top)
                    if text[k] not in "ij" or len(m) < 2 or len(g["strokes"]) != len(m):
                        continue
                    lens = [max(p[1] for p in gs) - min(p[1] for p in gs) for gs in g["strokes"]]
                    an = lens.index(max(lens))
                    for z, (j2, a2, b2) in enumerate(m):
                        if z == an or any(j2 == j3 and k3 != k for k3, m3 in enumerate(at) for j3, _, _ in m3):
                            continue
                        offs = [mark_offset(pts, m[an], m[z]) for pts in (xy, s["strokes"])]
                        mark.setdefault((i, k), {})[lv] = offs
                self.assertEqual(mids, sorted(mids), lv)
                if lv != "1":
                    continue
                # each word set: its letters' points on their glyphs where the font sets them,
                # each glyph stroke's ends reached by the word's ink (all the line's words at
                # one size: fitted at the words' median, as a word's box can mislead its own)
                k0, fits = 0, []
                for w in text.split(" "):
                    ks = range(k0, k0 + len(w))
                    k0 += len(w) + 1
                    self.assertEqual([why[k] for k in ks], ["set"] * len(w), (w, why))
                    wp = [xy[j][z] for k in ks for j, a, b in at[k] for z in range(a, b + 1)]
                    wg = [lay[k] for k in ks for j, a, b in at[k] for z in range(a, b + 1)]
                    fits.append((w, ks, wp, wg, on_set(wp, wg)[1]))
                med = sorted(f[4] for f in fits)[len(fits) // 2]
                for w, ks, wp, wg, _ in fits:
                    rms, size, base, x0 = on_set(wp, wg, size=med)
                    self.assertLess(rms, 0.003, (w, how_laid))
                    ink = [((p[0] - x0) / size, (base - p[1]) / size)
                           for j in sorted({j for k in ks for j, _, _ in at[k]}) for p in xy[j]]
                    for k in ks:
                        for gs in lay[k]:
                            for e in (gs[0], gs[-1]):
                                self.assertLess(min(math.hypot(e[0] - p[0], e[1] - p[1]) for p in ink), 0.1, (w, text[k]))
                    setw += 1
        # at Medium and Strong where the full pull (its glyph drawn whole) has it, the dot
        # written aside (the dot's own pull part of the way would leave it a tenth of an
        # x-height off)
        dev = []
        for (i, k), o in mark.items():
            ft, raw = o["full"]
            for lv in ("0.5", "0.75"):
                if lv not in o:
                    continue
                st = o[lv][0]
                dev.append(math.hypot(st[0] - ft[0], st[1] - ft[1]) / xh)
                self.assertLess(dev[-1], 0.06, (i, k, lv))
            self.assertGreater(math.hypot(raw[0] - ft[0], raw[1] - ft[1]), 0.2 * xh, (i, k))
        self.assertLess(sum(dev) / len(dev), 0.04)
        self.assertGreater(n, 40)
        self.assertLessEqual(held, 1)
        self.assertGreater(drawn, 30)
        self.assertGreater(len(mark), 2)
        self.assertEqual(setw, 10)

    def test_joined_pairs_whole_stroke_align(self):
        """the same joined words with ALIGN of whole strokes (a joined stroke listed for each
        of its letters, as the store has them): the letters reach the pull with their own ink
        (the stroke cut where its parts match their glyphs), so at Strong and Neatest each
        letter of a pair is pulled as with ALIGN by points, with no new kink or step back"""
        sh = {"i": TL.dotted_i}
        self.letters(shapes=sh)
        words = ["past", "chin", "brim", "has", "rich", "cast", "arise", "chase", "first", "this"]
        pairs = ("st", "ch", "ri", "as")
        xh = 30.0
        for seed in (33, 1):
            rnd = random.Random(seed)
            for i in range(0, len(words), 5):
                s, at, _ = joined_line(rnd, words[i:i + 5], shapes=sh)
                text = s["text"]
                whole = ";".join(",".join(sorted({str(j) for j, _, _ in m})) for m in at)
                for path, args in (("whole", [TR.esc(text), whole]),):
                    for lv in ("0.75", "1"):
                        c = self.e.start()
                        self.begin(c, s)
                        _, out, why = parse(c.ask("neaten", "s", lv, *args), why=True)
                        c.close()
                        xy = [EV.to_xy(o[2]) for o in out]
                        for j, st in enumerate(s["strokes"]):
                            self.assertEqual(len(xy[j]), len(st), (path, lv))
                            self.assertEqual(sum(flow([(p[0], p[1]) for p in st], xy[j], xh)), 0, (path, lv, j))
                        n = sum(why[z] in ("pulled", "set") for k in range(len(text) - 1) if text[k:k + 2] in pairs for z in (k, k + 1))
                        m = sum(2 for k in range(len(text) - 1) if text[k:k + 2] in pairs)
                        self.assertGreaterEqual(n, m - 1, (path, lv, why))

    def test_flow_where_ink_and_versions_differ(self):
        """at every level that pulls (Neatest sets the word: FR-043), no new step back and no new kink where the versions have a part the
        ink lacks (a loop: not traced with too few points, another version or none) or the ink
        one the versions lack, over every character"""
        rnd = random.Random(28)
        for versions, ink in ((add_loop, None), (None, add_loop)):
            TR.shutil.rmtree(self.e.dir, ignore_errors=True)
            self.e = TR.Env()
            self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"
            self.letters(change=versions)
            new, pulled, where = 0, 0, {}
            for i in range(0, len(TL.ALNUM), 8):
                s = TL.sample(" ".join(TL.ALNUM[i:i + 8]), rnd)
                if ink:
                    s["strokes"] = [ink(st) for st in s["strokes"]]
                    retime(s)
                align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
                for lv in (0.25, 0.5, 0.75):
                    self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
                    c = self.e.start()
                    self.begin(c, s)
                    _, out, why = parse(c.ask("neaten", "s", str(lv), TR.esc(s["text"]), align), why=True)
                    c.close()
                    pulled += why.count("pulled")
                    for k, ch in enumerate(s["text"]):
                        for j in s["spans"][k]:
                            b, kk = flow([(p[0], p[1]) for p in s["strokes"][j]], EV.to_xy(out[j][2]), 30.0)
                            new += b + kk
                            if b + kk:
                                where[(lv, ch)] = where.get((lv, ch), 0) + b + kk
            # (with the loop added to the versions, registered versions keep it whole, so a few
            # characters written without it no longer share the versions' structure and are
            # left as written, "no-structure", where averaged ones were pulled: 35 then, 30 now)
            self.assertGreater(pulled, 25, versions)
            self.assertEqual(new, 0, (bool(versions), where))

    def test_same_strokes_as_the_glyph(self):
        """a t is pulled when written in as many strokes as the glyph the font draws there,
        towards that glyph; written in another number it keeps its shape (no-structure;
        neatening keeps the strokes, FR-013, FR-014)"""
        rnd = random.Random(40)
        ss = [TL.late_sample("ato", rnd, "next") for _ in range(8)]
        ss += [TL.late_sample("ato", rnd, "next", forms={"t": (TL.ONE_T, [])}) for _ in range(5)]
        f = self.e.dir / "in.jsonl"
        lines = [json.dumps({"format": "glassine-handwriting-export", "version": 1})]
        for i, s in enumerate(ss):
            lines.append(json.dumps({"sample": s, "client": "org.example.test", "confirmed": 1700000000 + i,
                                     "source": "prompted"}))
        f.write_text("\n".join(lines) + "\n")
        self.e.run("samples", "import", str(f))
        self.e.run("letters", "build", "-r")
        L = {m["char"]: m for m in map(json.loads, (self.e.store / "letters" / "model").read_text().splitlines())
             if "char" in m}
        vt = L["t"]["variants"]
        self.assertEqual({len(v["strokes"]) for v in vt}, {1, 2})
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        seen = 0
        for forms, n in ((None, 2), ({"t": (TL.ONE_T, [])}, 1)):
            s = TL.late_sample("ato", random.Random(41), "next", forms=forms)
            for st in s["strokes"]:
                for p in st:
                    p[0] += rnd.gauss(0, 1.5)
                    p[1] += rnd.gauss(0, 1.5)
            c = self.e.start()
            self.begin(c, s)
            align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
            _, out = parse(c.ask("neaten", "s", "0.75", "ato", align))
            c.close()
            xy = [EV.to_xy(o[2]) for o in out]
            g = self.glyphs("ato")[1]
            # the t's strokes: its own, and the bar written after the word (in the o's span)
            ts = sorted(set(s["spans"][1]) | ({len(s["strokes"]) - 1} if n == 2 else set()))
            before = [[(p[0], p[1]) for p in s["strokes"][j]] for j in ts]
            after = [xy[j] for j in ts]
            self.assertEqual(len(after), n)
            if len(g["strokes"]) == n:
                self.assertTrue(all(out[j][1] == "pulled" for j in ts), n)
                self.assertLess(shape_dist(after, g), shape_dist(before, g))
                seen += 1
            else:
                self.assertTrue(all(out[j][1] != "pulled" for j in ts), n)
        self.assertEqual(seen, 1)

    def test_spacing_towards_the_letter_model(self):
        """uneven gaps between letters go towards the gaps the person writes those pairs at
        (FR-014; spacing.c), measured on the ink neatening gives back"""
        rnd = random.Random(50)
        gaps = {"lo": 0.05, "ol": 0.7, "ab": 0.5, "bc": 0.3}
        ss = TL.alphabet_samples(seed=50, per=3)
        ss += [TL.spaced_sample("lol abc", rnd, gaps) for _ in range(5)]
        f = self.e.dir / "in.jsonl"
        lines = [json.dumps({"format": "glassine-handwriting-export", "version": 1})]
        for i, s in enumerate(ss):
            lines.append(json.dumps({"sample": s, "client": "org.example.test", "confirmed": 1700000000 + i,
                                     "source": "prompted"}))
        f.write_text("\n".join(lines) + "\n")
        self.e.run("samples", "import", str(f))
        self.e.run("letters", "build", "-r")
        sp = next(m["spacing"] for m in map(json.loads, (self.e.store / "letters" / "model").read_text().splitlines())
                  if "spacing" in m)
        learned = {(p[0], p[1]): p[2] for p in sp["pairs"]}
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        s = TL.spaced_sample("lol abc", random.Random(51), {"lo": 0.3, "ol": 0.5, "ab": 0.3, "bc": 0.5})
        c = self.e.start()
        self.begin(c, s)
        align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
        _, out = parse(c.ask("neaten", "s", "1", "lol abc", align))
        c.close()
        xy = [EV.to_xy(o[2]) for o in out]
        xh = EV.line_xh(s)
        base = EV.line_base(s, xh)
        before = EV.line_gaps(s["text"], s["spans"], [[(p[0], p[1]) for p in st] for st in s["strokes"]], xh, base)
        after = EV.line_gaps(s["text"], s["spans"], xy, xh, base)
        self.assertEqual(len(before), 4)
        off_b = [abs(g - learned[(a, b)]) for a, b, g in before]
        off_a = [abs(g - learned[(a, b)]) for a, b, g in after]
        self.assertLess(sum(off_a), sum(off_b) / 3)
        self.assertTrue(all(x < 0.06 for x in off_a), off_a)          # within the step limit

    def test_spacing_by_the_middles(self):
        """a pair with an overhang that sits differently in words than in its variant (an f's
        bar) goes towards the distance the person writes its letters' middles at (FR-007,
        FR-014), measured on the ink neatening gives back"""
        rnd = random.Random(52)
        dist = {"fo": 1.0, "Tl": 0.6}
        ss = []
        for _ in range(6):
            ss += [TL.overhang_word(w, rnd, dist) for w in ("fo", "Tl")]
        for _ in range(4):
            ss += [TL.overhang_word(c, rnd, dist, "alone") for c in "fTfTfTol"]
        f = self.e.dir / "in.jsonl"
        lines = [json.dumps({"format": "glassine-handwriting-export", "version": 1})]
        for i, s in enumerate(ss):
            lines.append(json.dumps({"sample": s, "client": "org.example.test", "confirmed": 1700000000 + i,
                                     "source": "prompted"}))
        f.write_text("\n".join(lines) + "\n")
        self.e.run("samples", "import", str(f))
        self.e.run("letters", "build", "-r")
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        # written with the bar above the x-height, as alone: the gap would set the o at the stem
        s = TL.overhang_word("fo", random.Random(53), {"fo": 0.75}, "alone")
        c = self.e.start()
        self.begin(c, s)
        align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
        _, out = parse(c.ask("neaten", "s", "1", "fo", align))
        c.close()
        xy = [EV.to_xy(o[2]) for o in out]
        xh = EV.line_xh(s)
        base = EV.line_base(s, xh)

        def dist_of(strokes):
            m = [TL.middle([[(p[0] / xh, (base - p[1]) / xh) for p in strokes[j]] for j in s["spans"][k]]) for k in range(2)]
            return m[1] - m[0]
        before = dist_of([[(p[0], p[1]) for p in st] for st in s["strokes"]])
        after = dist_of(xy)
        self.assertAlmostEqual(before, 0.75, delta=0.02)
        self.assertLess(abs(after - 1.0), 0.06, (before, after))

    def test_line_level_without_letter_model(self):
        # letters on a slanted baseline: straight after (FR-014, FR-022)
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        rnd = random.Random(12)
        s = TL.sample("aceoun", rnd)
        for j, st in enumerate(s["strokes"]):
            for p in st:
                p[1] += 0.25 * (p[0] - 20)
        c = self.e.start()
        self.begin(c, s)
        align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
        _, out = parse(c.ask("neaten", "s", "1", "aceoun", align))
        c.close()
        xy = [EV.to_xy(o[2]) for o in out]

        def slope(strokes):
            pts = []
            for sp in s["spans"]:
                q = [p for j in sp for p in strokes[j]]
                pts.append(((min(p[0] for p in q) + max(p[0] for p in q)) / 2, max(p[1] for p in q)))
            mx = sum(p[0] for p in pts) / len(pts)
            my = sum(p[1] for p in pts) / len(pts)
            return sum((p[0] - mx) * (p[1] - my) for p in pts) / sum((p[0] - mx) ** 2 for p in pts)

        self.assertGreater(slope([[(p[0], p[1]) for p in st] for st in s["strokes"]]), 0.2)
        self.assertLess(abs(slope(xy)), 0.02)
        self.assertTrue(all(o[1] == "line" for o in out))

    def test_drawing_stays(self):
        self.letters()
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        rnd = random.Random(13)
        s = TL.sample("abc", rnd)
        circle = [[200 + 120 * math.cos(a / 20 * 2 * math.pi), 100 + 120 * math.sin(a / 20 * 2 * math.pi), 5 + a * 0.01, 0.5]
                  for a in range(21)]
        s["strokes"].append(circle)
        s["spans"][-1].append(len(s["strokes"]) - 1)
        c = self.e.start()
        self.begin(c, s)
        align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
        _, out = parse(c.ask("neaten", "s", "1", "abc", align))
        c.close()
        self.assertEqual(out[-1][1], "kept")
        self.assertEqual(out[-1][2], points(circle))

    def test_session_unchanged_and_private(self):
        self.letters()
        rnd = random.Random(15)
        s = TL.sample("hi", rnd)
        ids = []
        for neaten in (False, True):
            c = self.e.start()
            c.ask("hello", "1", f"org.example.n{int(neaten)}")
            self.begin(c, s)
            if neaten:
                parse(c.ask("neaten", "s", "1"))
            r = c.ask("confirm", "s", "confirmed", "hi")
            self.assertEqual(r[2], "ok", r)
            ids.append(r[3])
            c.ask("forget", r[3])
            c.close()
        self.assertEqual(ids[0], ids[1])
        c = self.e.start()
        self.assertEqual(c.ask("begin", "p", "en", "password", "device", "")[2], "private")
        self.assertEqual(c.ask("neaten", "p", "1")[2], "unknown-session")
        c.ask("begin", "s", "en", "-", "device", "")
        c.ask("stroke", "s", "1,1,0 2,2,5")
        for bad in ("-0.1", "1.5", "x", "nan", ""):
            self.assertEqual(c.ask("neaten", "s", bad)[2], "bad-strength")
        self.assertEqual(c.ask("neaten", "s", "1", "ab", "0;zz")[2], "bad-align")
        c.close()


if __name__ == "__main__":
    unittest.main()
