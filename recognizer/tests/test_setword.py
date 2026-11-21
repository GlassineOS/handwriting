#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""`set` (contract 1.7, spec 005 FR-018, FR-021, FR-024): a text set in the person's font as
pen strokes, for an app that draws an edited word without the font. Without a letter model
each character is an advance and nothing is drawn; with one, the strokes are the glyphs the
font draws there (the variants of the letters, at their places), deterministic, and a wider
text is wider.

    HW_BIN=./glassine-handwriting python3 tests/test_setword.py
"""
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_letters as TL  # noqa: E402


def ask(env, *lines):
    p = subprocess.run([str(TL.BIN), "-i", "0"], env=env.env, input="".join(l + "\n" for l in lines),
                       capture_output=True, text=True, timeout=120)
    return [ln.split("\t") for ln in p.stdout.splitlines()]


def parse(r):
    """set ID ok WIDTH XH_MM INK K POINTS... -> width, xh_mm, ink, strokes"""
    assert r[0] == "set" and r[2] == "ok", r
    k = int(r[6])
    st = [[tuple(float(v) for v in p.split(",")) for p in s.split(" ")] for s in r[7:7 + k]]
    assert len(r) == 7 + k, r
    return float(r[3]), float(r[4]), float(r[5]), st


class SetWord(unittest.TestCase):
    def setUp(self):
        self.e = TL.Env()
        self.addCleanup(self.e.close)

    def test_without_a_letter_model(self):
        r = ask(self.e, "hello\tq0\t1\torg.example.test", "set\tq1\tone", "set\tq2\t", "set\tq3\ta\\qb")
        w, xh, ink, st = parse(r[1])
        self.assertEqual((w, xh, ink, st), (3.0, 0.0, 0.0, []))
        self.assertEqual(r[2][2:], ["bad-text"])
        self.assertEqual(r[3][2:], ["bad-escape"])

    def test_with_a_letter_model(self):
        self.e.put(TL.alphabet_samples(seed=4, per=4))
        self.e.run("letters", "build", "-f", "-r")
        r = ask(self.e, "hello\tq0\t1\torg.example.test", "set\tq1\tone", "set\tq2\tone", "set\tq3\tonce more",
                "set\tq4\to")
        w, xh, ink, st = parse(r[1])
        self.assertEqual(r[1][2:], r[2][2:])                    # the same text, the same strokes
        self.assertGreater(len(st), 0)
        xs = [p[0] for s in st for p in s]
        ys = [p[1] for s in st for p in s]
        self.assertAlmostEqual(min(xs), 0, places=3)            # the ink's left at 0
        self.assertAlmostEqual(max(xs), w, places=2)
        self.assertTrue(-1 < min(ys) < 0.5 and 0.5 < max(ys) < 2.5, (min(ys), max(ys)))   # on the baseline
        self.assertGreater(ink, 0)
        self.assertGreater(parse(r[3])[0], w)                   # a longer text is wider
        self.assertLess(parse(r[4])[0], w)


if __name__ == "__main__":
    unittest.main()
