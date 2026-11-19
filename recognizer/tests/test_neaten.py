#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance for neatening (spec 003 FR-013 to FR-017, FR-020, FR-022, FR-023,
FR-035 to FR-037), with a random-weight model and synthetic samples: strength 0 gives the
strokes back byte for byte; any strength keeps the strokes, their order, every point's time,
pressure and tilt as sent; the same request gives the same ink; the four levels do what they
say (Light no letter's shape, Medium the close letters, Strong also a badly written one,
Neatest the best version's shape); the best version is the target; a pulled letter whose
new ink comes nearer another character's versions is put back, the others stay pulled, and
the reply names each character's reason; a word the dictionary lacks doesn't hold back the
line; at strength 1 the covered characters pulled are closer to their version (most of
them are); no new step back or kink where strokes start or letters join; without a letter
model the baseline is straightened; drawing passes unchanged; the session is unchanged, so
a confirm stores the same sample; a private field keeps nothing to neaten.

    HW_BIN=path/to/glassine-handwriting python3 tests/test_neaten.py

The correspondence along the stroke (monotonic, hooks to the ends) and joined letters are
tested in tests/test-engine.c. The recognizer's reading of real ink against neatened ink is measured on a store of the
person's samples with tools/eval-neaten.py (only aggregate numbers).
"""
import importlib.util
import json
import math
import random
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


WHY = {"pulled", "level", "not-covered", "no-structure", "not-near", "shape-guard", "low-confidence", "drawing",
       "gesture", "no-ink", "space"}


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


class Neaten(unittest.TestCase):
    def setUp(self):
        self.e = TR.Env()
        self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"

    def tearDown(self):
        TR.shutil.rmtree(self.e.dir, ignore_errors=True)

    def letters(self, seed=1):
        """a letter model from synthetic prompted samples of separate letters"""
        ss = TL.alphabet_samples(seed=seed, per=4)
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

    def test_closer_to_the_variant(self):
        L = self.letters()
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
            for k, ch in enumerate(text):
                le = L.get(ch)
                if not le or not le["covered"]:
                    continue
                vs = [v for v in le["variants"] if v["members"] >= 2]
                before = [[(p[0], p[1]) for p in s["strokes"][j]] for j in s["spans"][k]]
                db = min(shape_dist(before, v) for v in vs)
                if not math.isfinite(db):
                    continue
                m += 1
                if not all(out[j][1] == "pulled" for j in s["spans"][k]):
                    continue
                after = [xy[j] for j in s["spans"][k]]
                da = min(shape_dist(after, v) for v in vs)
                self.assertLess(da, db, (text, ch))
                n += 1
        # (a letter whose ink doesn't match its version's structure stays at the line level)
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

    @staticmethod
    def target(v):
        return {"strokes": v.get("best") or v["strokes"]}

    def test_levels(self):
        """the four levels (FR-014): Light moves no letter's shape, only the line; Medium
        pulls the letters already close to their best version; Strong every covered letter
        whose strokes match its version, also a badly written one; Neatest gives each the
        best version's shape"""
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
        res = {lv: self.neaten_letters(s, lv) for lv in (0.25, 0.5, 0.75, 1)}
        ks = [k for k, ch in enumerate(s["text"]) if L.get(ch, {}).get("covered")]

        def dist(xy, k, size=False):
            """to its nearest best version: in shape, or (size) in x-heights, as written"""
            vs = [v for v in L[s["text"][k]]["variants"] if v["members"] >= 2 and not v.get("left_out")]
            ink = [xy[j] for j in s["spans"][k]]
            return min(EV.vdist(ink, 30.0, self.target(v)) if size else shape_dist(ink, self.target(v)) for v in vs)

        def pulled(how, k):
            return all(how[j] == "pulled" for j in s["spans"][k])

        before = {k: dist(raw, k) for k in ks}
        how, xy = res[0.25]
        self.assertNotIn("pulled", how)
        for k in ks:
            # the line only: each letter's ink keeps its shape (moved, scaled, sheared)
            self.assertAlmostEqual(dist(xy, k), before[k], delta=0.05)
        how5, _ = res[0.5]
        self.assertFalse(pulled(how5, bad))
        self.assertTrue(any(pulled(how5, k) for k in ks if k != bad))
        how7, xy7 = res[0.75]
        self.assertTrue(pulled(how7, bad))
        self.assertLess(dist(xy7, bad, True), 0.6 * dist(raw, bad, True))
        how1, xy1 = res[1]
        n = 0
        for k in ks:
            if pulled(how1, k):
                # (a letter already that near keeps its few points: on the version, chords between)
                self.assertLess(dist(xy1, k), max(before[k], 0.08))
                if pulled(how7, k):
                    self.assertLessEqual(dist(xy1, k), dist(xy7, k) + 0.01)
                n += 1
        self.assertGreaterEqual(n, 4)

    def test_best_version_is_the_target(self):
        """the letters are pulled to the best version of their version (FR-035), the one
        the favourite sample leans it to, not its plain average"""
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
        for k in range(2):
            ink = [xy[j] for j in s["spans"][k]]
            self.assertLess(shape_dist(ink, {"strokes": v["best"]}), shape_dist(ink, v) + 1e-9)

    def test_letter_check(self):
        """a pulled letter whose new ink would come closer to another character is put back
        to the line level, the other letters keep their pull, and the reply says why
        (FR-037, FR-041; the knob's guard= has those characters fail the check)"""
        self.letters()
        s = TL.sample("hello", random.Random(24))
        how0, _ = self.neaten_letters(s, 1)
        why0 = self.why
        for mis in ("o", "hel", "lo"):
            how, _ = self.neaten_letters(s, 1, knob="guard=" + mis)
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
        (SC-004), at every level"""
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
                for k, ch in enumerate(text):
                    if self.why[k] != "pulled":
                        continue
                    ink = [xy[j] for j in s["spans"][k]]
                    ln = len(ink)
                    mine = min((EV.vdist(ink, xh, w) for v in L[ch]["variants"] if not v.get("left_out")
                                for w in (v, {"strokes": v.get("best") or v["strokes"]}) if len(v["strokes"]) == ln),
                               default=math.inf)
                    other = min((EV.vdist(ink, xh, v) for c2, le in L.items() if c2 != ch for v in le["variants"]
                                 if not v.get("left_out") and len(v["strokes"]) == ln), default=math.inf)
                    self.assertLess(mine, other + 0.01, (text, ch, lv))
                    n += 1
        self.assertGreater(n, 20)

    def test_word_left_out_of_the_dictionary(self):
        """a word the dictionary lacks (a name) doesn't hold back the line: the other words
        are neatened; only a word read with low confidence gets the line level (FR-017)"""
        self.letters()
        s = TL.sample("hello my name is Qxzvk", random.Random(26))
        # with the text and its spans: the name and the other words are pulled
        how, _ = self.neaten_letters(s, 1)
        words = "".join(" " if w == "space" else "p" if w == "pulled" else "-" for w in self.why).split(" ")
        self.assertTrue(all("p" in w for w in words), self.why)
        # without it (the reading of this random-weight model is low confidence throughout):
        # a reason for every character read
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
        c = self.e.start()
        self.begin(c, s)
        parse(c.ask("neaten", "s", "1"), why=True)
        c.close()

    def test_flow_at_starts_and_joins(self):
        """at every level, no new step back and no new kink where a stroke starts (with an
        entry stroke or a hook the version lacks) or where it passes from one letter to the
        next (FR-036), over every character and every joined pair of small letters"""
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
        for li, (s, align) in enumerate(lines):
            for lv in (0.25, 0.5, 0.75, 1):
                self.e.env["GLASSINE_HANDWRITING_NEATEN"] = ""
                c = self.e.start()
                self.begin(c, s)
                _, out, why = parse(c.ask("neaten", "s", str(lv), TR.esc(s["text"]), align), why=True)
                c.close()
                pulled += why.count("pulled")
                for j, o in enumerate(out):
                    raw = [(p[0], p[1]) for p in s["strokes"][j]]
                    b, k = flow(raw, EV.to_xy(o[2]), xh)
                    new += b + k
                    if b + k:
                        key = (lv, "single" if li < 8 else "joined")
                        where[key] = where.get(key, 0) + b + k
        self.assertGreater(pulled, 1000)
        self.assertEqual(new, 0, where)

    def test_same_strokes_variant(self):
        """a t written in one stroke goes to the one-stroke variant, one in two strokes to a
        two-stroke one (neatening keeps the strokes, FR-013, FR-014)"""
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
        for forms, n in ((None, 2), ({"t": (TL.ONE_T, [])}, 1)):
            s = TL.late_sample("ato", random.Random(41), "next", forms=forms)
            for st in s["strokes"]:
                for p in st:
                    p[0] += rnd.gauss(0, 1.5)
                    p[1] += rnd.gauss(0, 1.5)
            c = self.e.start()
            self.begin(c, s)
            align = ";".join(",".join(map(str, sp)) for sp in s["spans"])
            _, out = parse(c.ask("neaten", "s", "1", "ato", align))
            c.close()
            xy = [EV.to_xy(o[2]) for o in out]
            mine = [v for v in vt if len(v["strokes"]) == n and v["members"] >= 2]
            # the t's strokes: its own, and the bar written after the word (in the o's span)
            ts = sorted(set(s["spans"][1]) | ({len(s["strokes"]) - 1} if n == 2 else set()))
            before = [[(p[0], p[1]) for p in s["strokes"][j]] for j in ts]
            after = [xy[j] for j in ts]
            self.assertEqual(len(after), n)
            self.assertTrue(all(out[j][1] == "pulled" for j in ts), n)
            self.assertLess(min(shape_dist(after, v) for v in mine), min(shape_dist(before, v) for v in mine))

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
