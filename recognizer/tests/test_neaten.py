#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance for neatening (spec 003 FR-013 to FR-017, FR-020, FR-022, FR-023), with
a random-weight model and synthetic samples: strength 0 gives the strokes back byte for
byte; any strength keeps the strokes, their order, every point's time, pressure and tilt as
sent; the same request gives the same ink; at strength 1 every covered character with
strokes of its own is closer to its variant; without a letter model the baseline is
straightened; drawing passes unchanged; the neatened ink reads as the original did (or
is left as it was); the session is unchanged, so a confirm stores the same sample; a
private field keeps nothing to neaten.

    HW_BIN=path/to/glassine-handwriting python3 tests/test_neaten.py

The recognizer's reading of real ink against neatened ink is measured on a store of the
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


def parse(r):
    assert r[0] == "neaten" and r[2] == "ok", r
    k = int(r[4])
    assert len(r) == 5 + 3 * k, r
    return TR.unesc(r[3]), [(int(r[5 + 3 * i]), r[6 + 3 * i], r[7 + 3 * i]) for i in range(k)]


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
        self.assertEqual(c.ask("neaten", "e", "1")[2:], ["ok", "", "0"])
        c.close()

    def test_keeps_strokes_order_time_pressure(self):
        self.letters()
        rnd = random.Random(9)
        s = TL.sample("abc de", rnd)
        c = self.e.start()
        c.ask("hello", "1")
        self.begin(c, s)
        sent = [points(st) for st in s["strokes"]]
        for st in ("0.3", "1"):
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
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = "check=0"
        rnd = random.Random(11)
        n = 0
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
                after = [xy[j] for j in s["spans"][k]]
                da = min(shape_dist(after, v) for v in vs)
                self.assertLess(da, db, (text, ch))
                self.assertTrue(all(out[j][1] == "pulled" for j in s["spans"][k]))
                n += 1
        self.assertGreater(n, 8)

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
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = "check=0"
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
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = "check=0"
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
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = "check=0"
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
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = "check=0"
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
        self.e.env["GLASSINE_HANDWRITING_NEATEN"] = "check=0"
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

    def test_reads_as_the_original(self):
        # with the check on, the neatened ink reads as the original (or comes back as it was)
        self.letters()
        rnd = random.Random(14)
        for text in ("hello", "abc de", "Zz9"):
            s = TL.sample(text, rnd)
            c = self.e.start()
            self.begin(c, s)
            want = TR.parse_text(c.ask("read", "s", "1"))[0][0]
            used, out = parse(c.ask("neaten", "s", "1"))
            self.assertEqual(used, want)
            c.ask("begin", "n", "en", "-", "device:4.0:200", "")
            a = s["area"]["box"]
            c.ask("area", "n", f"{a[0]},{a[1]},{a[2]},{a[3]},{s['area']['line']},0")
            for o in out:
                c.ask("stroke", "n", o[2])
            self.assertEqual(TR.parse_text(c.ask("read", "n", "1"))[0][0], want)
            c.close()

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
