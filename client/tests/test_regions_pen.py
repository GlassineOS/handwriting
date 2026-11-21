#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance of writing regions end to end (spec 005): the stand-in compositor
(GlassineOS/sway test/handwriting-standin.c, with sway's own glassine-handwriting.c), the
handwriting service, the recognizer with a random-weight model, and ghw-regions-print on
libglassine-handwriting as the app. A stand-in pen writes.

    HW_BIN=… HW_STANDIN=…/handwriting-standin GHW_REGIONS=…/ghw-regions-print python3 tests/test_regions_pen.py

Checks: the regions capability; each stroke arrives in the region it starts in, in region
coordinates, also when it ends in another; each region gets its own value made of its own
strokes; a move reads nothing again; a keyboard edit gives a value with set words; the
strokes, value and saved state handed back to a fresh region give the same value and word
layout; a private region still gets its raw strokes and nothing else; a report on a value is
answered."""
import os
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_pen as TP  # noqa: E402

REGIONS = os.environ.get("GHW_REGIONS")
CAP_REGIONS = 16


def ink(n=3, seed=1, x0=20.0, y0=40.0, t0=5000):
    """n strokes along a line, surface coordinates: [(x, y, t, p, tx, ty)]"""
    rng = random.Random(seed)
    out, t = [], t0
    for i in range(n):
        x, y, s = x0 + 24 * i, y0, []
        for _ in range(16):
            x += rng.randrange(0, 512) / 256
            y += rng.randrange(-384, 384) / 256
            s.append((x, y, t, 0.5, 0.0, 0.0))
            t += 7
        t += 150
        out.append(s)
    return out


@unittest.skipUnless(TP.STANDIN and REGIONS, "needs HW_STANDIN and GHW_REGIONS")
class Regions(unittest.TestCase):
    start = TP.Pen.start
    pen = TP.Pen.pen

    def app(self, *args):
        a = TP.Proc([REGIONS, *args], self.env)
        self.addCleanup(a.close)
        first = a.line()
        self.assertTrue(first.startswith("caps "), first)
        a.until(lambda line: line == "ready")
        return a, int(first.split()[1])

    def values(self, out, rid):
        """the value lines of region rid, each with its words: [(serial, text, [word fields])]"""
        vals, words = [], []
        for line in out:
            f = line.split(" ")
            if f[0] == "word" and int(f[1]) == rid:
                words.append(f[2:])
            elif f[0] == "value" and int(f[1]) == rid:
                vals.append((int(f[2]), " ".join(f[4:]).replace("\\n", "\n"), words))
                words = []
        return vals

    def sync(self, a):
        a.send("sync")
        return a.until(lambda line: line == "synced")

    def test_regions_end_to_end(self):
        self.start()
        self.comp.send("per-mm 6.25")
        self.assertEqual(self.comp.line(), "ok")
        a, caps = self.app()
        self.assertTrue(caps & CAP_REGIONS, caps)
        # two fields one above the other, the surface's origin as the stand-in places it
        a.send("region 0 first")
        a.send("rect 0 0 0 400 100")
        a.send("region 1 second")
        a.send("rect 1 0 100 400 100")
        a.send("neaten 1 1 0.5")
        self.sync(a)
        st0 = ink(3, seed=1, y0=40)
        st1 = ink(2, seed=2, y0=140)
        for s in st0:
            self.pen(s)
        # a stroke that starts in the first field and ends in the second is the first's
        cross = [(330 + i, 60 + 6 * i, 9000 + 7 * i, 0.5, 0.0, 0.0) for i in range(16)]
        self.pen(cross)
        for s in st1:
            self.pen(s)
        seen = set()

        def both(line):
            if line.startswith("done "):
                seen.add(line.split()[1])
            return seen >= {"0", "1"}
        out = a.until(both)
        strokes = {}
        for line in out:
            f = line.split(" ")
            if f[0] == "stroke":
                strokes.setdefault(int(f[1]), {})[int(f[2])] = TP.parse_points(f[4:])
        self.assertEqual(sorted(strokes[0]), [0, 1, 2, 3])
        self.assertEqual(sorted(strokes[1]), [0, 1])
        # region coordinates: the second field's strokes from its own top
        self.assertAlmostEqual(strokes[1][0][0][1], st1[0][0][1] - 100, places=3)
        self.assertAlmostEqual(strokes[0][3][-1][1], cross[-1][1], places=3)
        v0, v1 = self.values(out, 0)[-1], self.values(out, 1)[-1]
        ks0 = sorted(int(k) for w in v0[2] if w[2] == "ink" for k in w[3].split(",") if k != "-")
        ks1 = sorted(int(k) for w in v1[2] if w[2] == "ink" for k in w[3].split(",") if k != "-")
        self.assertTrue(set(ks0) <= {0, 1, 2, 3} and ks0, v0)
        self.assertTrue(set(ks1) <= {0, 1} and ks1, v1)
        self.assertTrue(any(line.startswith("neat 1 ") for line in out))
        self.assertFalse(any(line.startswith("neat 0 ") for line in out))
        # a move (a scroll) reads nothing again: no new value comes
        a.send("rect 0 0 -60 400 100")
        a.send("rect 1 0 40 400 100")
        later = self.sync(a)
        while (line := a.line(timeout=2)) is not None:
            later.append(line)
        self.assertFalse(any(line.startswith("value ") for line in later), later)
        # a report on the second field's value: no permission to learn yet
        a.send("report 1 %d 1 0" % v1[0])
        r = a.until(lambda line: line.startswith("report 1 "))[-1]
        self.assertEqual(r.split()[2:], [str(v1[0]), "2"])
        # a keyboard edit of the first field: a value with set words
        a.send("edit 0 hello größe, what?")
        ev = a.until(lambda line: line.startswith("value 0 "))
        val = self.values(ev, 0)[-1]
        self.assertEqual(val[1], "hello größe, what?")
        kinds = [w[2] for w in val[2]]
        self.assertTrue(kinds and all(k in ("ink", "set") for k in kinds), val)
        self.assertIn("set", kinds)
        text = val[1].encode()
        for w in val[2]:
            self.assertLessEqual(int(w[0]) + int(w[1]), len(text))
        # handed back to a fresh field: the same value and word layout
        a.send("region 2 copy")
        a.send("rect 2 0 300 400 100")
        a.send("restore 2 0")
        ev = a.until(lambda line: line.startswith("value 2 "))
        copy = self.values(ev, 2)[-1]
        self.assertEqual(copy[1], val[1])
        self.assertEqual(len(copy[2]), len(val[2]))
        for w, c in zip(val[2], copy[2]):       # the same, to the wire's 1/256
            self.assertEqual(w[:3], c[:3])
            if w[2] == "set":
                for x, y in zip(w[3:7], c[3:7]):
                    self.assertAlmostEqual(float(x), float(y), delta=0.02)
            else:
                self.assertEqual(w[3], c[3])

    def test_private_region(self):
        self.start()
        a, caps = self.app()
        a.send("region 0 secret")
        a.send("rect 0 0 0 400 100")
        a.send("context 0 8 0")
        self.sync(a)
        for s in ink(2, seed=3):
            self.pen(s)
        out = a.until(lambda line: line.startswith("stroke 0 1 "))
        out += self.sync(a)
        self.assertIsNone(a.line(timeout=2.5))
        self.assertFalse(any(line.split(" ")[0] in ("value", "candidate", "neat", "gesture") for line in out))


if __name__ == "__main__":
    unittest.main()
