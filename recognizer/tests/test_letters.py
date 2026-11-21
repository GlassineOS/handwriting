#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance for the letter model and the handwriting font (spec 003 FR-006 to
FR-012, SC-001), on synthetic samples: the model is the same on every run and in any import
order; a character needs 3 usable samples; the font passes fontTools with checksums, maps
exactly the covered characters and the space, alternates the same way every time when
shaped with no feature asked for (and as hw_letters_step says); installed, the font cache
finds it in a test user; a rebuild keeps the name and changes the version and the file;
erasing the samples takes the model and the font. The three styles (FR-034): the profiles
of how each version was written, Calligraphy's and Brush's widths on made-up strokes, the
same shaping in all three, installed, updated and removed together. Neighbour rules, joined
pairs and random alternates (FR-038 to FR-040): a way written after a neighbour often enough
is shaped there and nowhere else, too little support gives no rule, a pair written joined often
enough is one glyph in every style at its letters' gaps with its neighbours as without it, and
random alternates only when asked for.

    HW_BIN=path/to/glassine-handwriting python3 tests/test_letters.py

Needs python3; the font checks also need fontTools (py3-fonttools), hb-shape
(harfbuzz-utils) and fontconfig (fc-match, fc-list): without them those checks fail,
unless HW_LETTERS_NO_FONT_TOOLS=1 skips them.
"""

import hashlib
import json
import math
import os
import random
import statistics
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = Path(os.environ.get("HW_BIN") or HERE.parent / "glassine-handwriting").resolve()
SKIP_TOOLS = os.environ.get("HW_LETTERS_NO_FONT_TOOLS") == "1"

ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


SLOTS = 10      # letters.h HW_LETTERS_SLOTS


def step(prev):
    """the step of the font's cycle at a character or space (letters.h hw_letters_step);
    prev None at a run's start or after a character the font lacks"""
    return 0 if prev is None else (prev + 1) % SLOTS


def shape(c, style, rnd):
    """strokes of one character in x-heights (x from 0, y up from the baseline): one of a
    few made-up shapes per character and style, with the pen's jitter"""
    h = int(hashlib.sha256(f"{c}{style}".encode()).hexdigest()[:8], 16)
    r = random.Random(h)
    top = 1.0 if c.islower() and c not in "bdfhklt" else 1.5
    bot = -0.6 if c in "gjpqy" else 0.0
    nst = 1 + h % 2
    out = []
    for k in range(nst):
        a0 = r.uniform(0, 2 * math.pi)
        span = r.uniform(2.5, 4.5)
        pts = []
        for i in range(12):
            t = i / 11
            x = 0.3 + 0.25 * math.cos(a0 + t * span) + 0.1 * k
            y = bot + (top - bot) * (0.5 + 0.5 * math.sin(a0 + t * span + k))
            pts.append((x + rnd.gauss(0, 0.01), y + rnd.gauss(0, 0.01)))
        out.append(pts)
    return out


def sample(text, rnd, styles=None, written=None, line=150.0, xh=30.0, shapes=None):
    """a prompted sample (sample format v1) of separate letters, device units; shapes: per
    character a function in place of shape()"""
    strokes, spans = [], []
    x = 20.0
    base = line * 2 / 3
    t = 0.0
    for c in text:
        if c == " ":
            spans.append([])
            x += 0.9 * xh
            continue
        sp = []
        for st in (shapes or {}).get(c, shape)(c, (styles or {}).get(c, 0), rnd):
            pts = []
            for px, py in st:
                pts.append([round(x + px * xh, 3), round(base - py * xh, 3), round(t, 3), 0.5])
                t += 0.01
            sp.append(len(strokes))
            strokes.append(pts)
        spans.append(sp)
        x += 0.9 * xh
    s = {"version": 1, "text": text, "lang": "en", "strokes": strokes, "spans": spans, "spans_source": "exact",
         "source": "prompted", "units": {"xy": "device", "per_mm": 4.0, "rate": 200},
         "area": {"box": [0, 0, max(x + 20, 200), line * 3], "line": line, "orientation": 0},
         "reading": [{"text": text, "score": -1.0}]}
    if written:
        s["written"] = written
    return s


BANDS, Y0, Y1 = 8, -0.6, 1.6      # letters.h HW_PROF_*


def profile(strokes):
    """an ink profile as spacing.c makes it (x-heights, y up): per height band the ink's
    leftmost and rightmost x, every band a segment crosses"""
    lo, hi = [math.inf] * BANDS, [-math.inf] * BANDS
    bw = (Y1 - Y0) / BANDS

    def band(y):
        return max(0, min(BANDS - 1, int(math.floor((y - Y0) / bw))))

    def put(b, x):
        lo[b], hi[b] = min(lo[b], x), max(hi[b], x)

    for st in strokes:
        for i, (x1, y1) in enumerate(st):
            x0, y0 = st[i - 1] if i else (x1, y1)
            y0, y1 = (min(max(y, Y0 + 1e-9), Y1 - 1e-9) for y in (y0, y1))
            b0, b1 = band(y0), band(y1)
            put(b0, x0)
            put(b1, x1)
            for b in range(min(b0, b1) + 1, max(b0, b1) + 1):
                xe = x0 + (Y0 + b * bw - y0) / (y1 - y0) * (x1 - x0)
                put(b - 1, xe)
                put(b, xe)
    return lo, hi


def profile_gap(a, b, shift=0.0):
    ds = [b[0][k] + shift - a[1][k] for k in range(BANDS) if math.isfinite(a[1][k]) and math.isfinite(b[0][k])]
    if ds:
        return min(ds)
    return min(x for x in b[0] if math.isfinite(x)) + shift - max(x for x in a[1] if math.isfinite(x))


def spaced_sample(text, rnd, gaps=None, gap=0.35, word=1.2, line=150.0, xh=30.0):
    """a word sample whose letters stand at the given profile gaps (x-heights; gaps by pair,
    else gap) and whose words stand word apart (their boxes)"""
    strokes, spans = [], []
    base = line * 2 / 3
    t, x, prev = 0.0, 20.0 / xh, None
    for k, c in enumerate(text):
        if c == " ":
            spans.append([])
            continue
        sh = shape(c, 0, rnd)
        # its full height, so the sample's x-height comes out as written
        top = 1.0 if c.islower() and c not in "bdfhklt" else 1.5
        bot = -0.6 if c in "gjpqy" else 0.0
        y0, y1 = min(p[1] for st in sh for p in st), max(p[1] for st in sh for p in st)
        sh = [[(px, bot + (py - y0) / (y1 - y0) * (top - bot)) for px, py in st] for st in sh]
        pr = profile(sh)
        if prev is not None and text[k - 1] != " ":
            want = (gaps or {}).get(text[k - 1] + c, gap)
            x = prev[1] + want - profile_gap(prev[0], pr, 0)
        elif prev is not None:
            x = prev[2] + word - min(p[0] for st in sh for p in st)
        sp = []
        for st in sh:
            pts = []
            for px, py in st:
                pts.append([round((x + px) * xh, 3), round(base - py * xh, 3), round(t, 3), 0.5])
                t += 0.01
            sp.append(len(strokes))
            strokes.append(pts)
        spans.append(sp)
        prev = (pr, x, x + max(p[0] for st in sh for p in st))
    s = {"version": 1, "text": text, "lang": "en", "strokes": strokes, "spans": spans, "spans_source": "exact",
         "source": "prompted", "units": {"xy": "device", "per_mm": 4.0, "rate": 200},
         "area": {"box": [0, 0, (x + 3) * xh, line * 3], "line": line, "orientation": 0},
         "reading": [{"text": text, "score": -1.0}]}
    return s


MID_Y0, MID_Y1 = 0.0, 1.0       # letters.h HW_MID_*


def middle(strokes):
    """a letter's middle as spacing.c hw_mid_of takes it: the mean x of its ink between the
    baseline and the x-height, each piece weighted by how far it runs up or down"""
    sx = sw = 0.0
    for st in strokes:
        for (x0, y0), (x1, y1) in zip(st, st[1:]):
            if y0 == y1:
                continue
            t0, t1 = sorted(((MID_Y0 - y0) / (y1 - y0), (MID_Y1 - y0) / (y1 - y0)))
            t0, t1 = max(t0, 0.0), min(t1, 1.0)
            if t1 <= t0:
                continue
            w = abs(y1 - y0) * (t1 - t0)
            sx += w * (x0 + (x1 - x0) * (t0 + t1) / 2)
            sw += w
    return sx / sw if sw > 1e-9 else float("nan")


def overhang_shape(c, where, rnd):
    """strokes (x-heights, y up) of letters whose overhang differs between words and alone,
    as a person's does: an f's bar low in words (meeting the next letter) and above the
    x-height alone; a T's bar short in words and long alone; an o and an l the same both ways"""
    def line(x0, y0, x1, y1, n=12):
        return [(x0 + (x1 - x0) * i / (n - 1) + rnd.gauss(0, 0.005), y0 + (y1 - y0) * i / (n - 1) + rnd.gauss(0, 0.005))
                for i in range(n)]
    if c == "f":
        stem = line(0.15, -0.5, 0.15, 1.4) + line(0.2, 1.48, 0.6, 1.55, 4)
        bar = line(-0.1, 0.55, 0.75, 0.55) if where == "word" else line(-0.1, 1.15, 0.75, 1.15)
        return [stem, bar]
    if c == "T":
        stem = line(0.5, 0.0, 0.5, 1.5)
        bar = line(0.1, 1.55, 0.9, 1.55) if where == "word" else line(-0.5, 1.55, 1.9, 1.55)
        return [stem, bar]
    if c == "o":
        return [[(0.35 + 0.35 * math.cos(2 * math.pi * i / 23) + rnd.gauss(0, 0.005),
                  0.5 + 0.5 * math.sin(2 * math.pi * i / 23) + rnd.gauss(0, 0.005)) for i in range(24)]]
    if c == "l":
        return [line(0.1, 1.5, 0.1, 0.1) + line(0.15, 0.03, 0.35, 0.0, 4)]
    raise ValueError(c)


def ink_sample(text, letters, line=150.0, xh=30.0):
    """a prompted sample of the given letters: per character its strokes (x-heights, y up)
    and its x (x-heights), None for a space"""
    strokes, spans = [], []
    base, t = line * 2 / 3, 0.0
    for c, lt in zip(text, letters):
        if lt is None:
            spans.append([])
            continue
        sh, x = lt
        sp = []
        for st in sh:
            pts = []
            for px, py in st:
                pts.append([round((x + px) * xh + 20, 3), round(base - py * xh, 3), round(t, 3), 0.5])
                t += 0.01
            sp.append(len(strokes))
            strokes.append(pts)
        spans.append(sp)
    right = max(p[0] for st in strokes for p in st)
    return {"version": 1, "text": text, "lang": "en", "strokes": strokes, "spans": spans, "spans_source": "exact",
            "source": "prompted", "units": {"xy": "device", "per_mm": 4.0, "rate": 200},
            "area": {"box": [0, 0, right + 40, line * 3], "line": line, "orientation": 0},
            "reading": [{"text": text, "score": -1.0}]}


def overhang_word(text, rnd, dist, where="word"):
    """a word of overhang_shape letters whose middles stand dist apart (by pair)"""
    letters, prev = [], None
    for c in text:
        sh = overhang_shape(c, where, rnd)
        m = middle(sh)
        x = 0.0 if prev is None else prev[0] + prev[1] + dist[prev[2] + c] - m
        letters.append((sh, x))
        prev = (x, m, c)
    return ink_sample(text, letters)


class Env:
    """a test user; base: Autograph's base font file (docs/recognizer.md), none by default"""

    def __init__(self, base=None):
        self.dir = Path(tempfile.mkdtemp(prefix="hw-letters-"))
        self.home = self.dir / "home"
        self.home.mkdir()
        self.env = dict(os.environ, XDG_DATA_HOME=str(self.dir / "data"), XDG_CONFIG_HOME=str(self.dir / "conf"),
                        XDG_CACHE_HOME=str(self.dir / "cache"), HOME=str(self.home),
                        GLASSINE_HANDWRITING_MODELS=str(self.dir / "models"),
                        GLASSINE_HANDWRITING_SYSFS=str(self.dir / "power"),
                        GLASSINE_HANDWRITING_LOADAVG=str(self.dir / "load"),
                        GLASSINE_HANDWRITING_BASE=str(base) if base else "off")
        self.env.pop("FONTCONFIG_FILE", None)
        (self.dir / "load").write_text("0.10 0.20 0.30 1/100 1\n")
        bat = self.dir / "power" / "battery"
        bat.mkdir(parents=True)
        (bat / "type").write_text("Battery\n")
        (bat / "status").write_text("Charging\n")

    @property
    def store(self):
        return self.dir / "data" / "glassine" / "handwriting"

    @property
    def fonts(self):
        return self.dir / "data" / "fonts" / "glassine-handwriting"

    def run(self, *args, check=True):
        p = subprocess.run([str(BIN), *args], env=self.env, capture_output=True, text=True, timeout=300)
        if check and p.returncode:
            raise AssertionError(f"{args}: {p.returncode} {p.stderr}")
        return p

    def put(self, samples):
        """the samples, stored through an export file"""
        f = self.dir / "in.jsonl"
        lines = [json.dumps({"format": "glassine-handwriting-export", "version": 1})]
        for i, s in enumerate(samples):
            lines.append(json.dumps({"sample": s, "client": "org.example.test", "confirmed": 1700000000 + i,
                                     "source": s["source"]}, ensure_ascii=False))
        f.write_text("\n".join(lines) + "\n")
        self.run("samples", "import", str(f))

    def model(self):
        return (self.store / "letters" / "model").read_text()

    def status(self):
        out = {"chars": {}}
        for ln in self.run("letters", "status").stdout.splitlines():
            f = ln.split("\t")
            if f[0] == "char":
                out["chars"][f[1]] = (int(f[2]), int(f[3]))
            else:
                out[f[0]] = f[1:]
        return out

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def alphabet_samples(seed=1, per=4, careful=None, styles=None, shapes=None):
    """samples of separate letters that hold every letter and digit `per` times"""
    rnd = random.Random(seed)
    chars = list(ALNUM) * per
    return [sample("".join(chars[i:i + 5]), rnd, styles=styles, written=careful, shapes=shapes) for i in range(0, len(chars), 5)]


class LetterModel(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

    def test_variant_sits_where_its_versions_do(self):
        """a variant sits on the line where its versions sit on average (FR-006), not where
        one of them happens to: an o written a little high or low in turn comes out on the
        baseline"""
        rnd = random.Random(41)
        ss = []
        for k in range(8):
            dy = 0.12 if k % 2 else -0.12
            letters = []
            for i, c in enumerate("aoe"):
                sh = shape(c, 0, rnd)
                y0, y1 = min(p[1] for st in sh for p in st), max(p[1] for st in sh for p in st)
                sh = [[(px, (py - y0) / (y1 - y0) + (dy if c == "o" else 0)) for px, py in st] for st in sh]
                letters.append((sh, 0.9 * i))
            ss.append(ink_sample("aoe", letters))
        self.e.put(ss)
        self.e.run("letters", "build", "-r")
        o = next(d for d in map(json.loads, self.e.model().splitlines()) if d.get("char") == "o")
        for v in o["variants"]:
            ys = [p[1] for st in v["strokes"] for p in st]
            self.assertLess(abs(min(ys)), 0.05, (min(ys), max(ys)))

    def test_variant_keeps_its_versions_size(self):
        """a variant is as tall and as wide as its versions on average, within 2 % (FR-006):
        versions that put the top of their curve a little earlier or later along the stroke
        average to a flatter one, which making it doesn't let shrink"""
        rnd = random.Random(43)

        def wavy(c, dphi):
            # one stroke, its height from the phase of its curve, its width from x alone
            out = []
            for i in range(24):
                t = i / 23
                x = 0.1 + 0.6 * t + rnd.gauss(0, 0.004)
                y = 0.5 + 0.5 * math.sin(2.4 * math.pi * t + dphi + (0.6 if c == "o" else 0)) + rnd.gauss(0, 0.004)
                out.append((x, y))
            y0, y1 = min(p[1] for p in out), max(p[1] for p in out)
            return [[(px, (py - y0) / (y1 - y0)) for px, py in out]]

        ss, widths = [], {}
        for k in range(10):
            letters = []
            for i, c in enumerate("aon"):
                sh = wavy(c, -0.3 + 0.6 * ((k * 7) % 10) / 9)
                widths.setdefault(c, []).append(max(p[0] for p in sh[0]) - min(p[0] for p in sh[0]))
                letters.append((sh, 1.2 * i))
            ss.append(ink_sample("aon", letters))
        self.e.put(ss)
        self.e.run("letters", "build", "-r")
        chars = {d["char"]: d for d in map(json.loads, self.e.model().splitlines()) if "char" in d}
        for c in "aon":
            for v in chars[c]["variants"]:
                ys = [p[1] for st in v["strokes"] for p in st]
                xs = [p[0] for st in v["strokes"] for p in st]
                # the versions are one x-height tall (their samples' x-height) and about as wide
                self.assertAlmostEqual(max(ys) - min(ys), 1, delta=0.02, msg=c)
                self.assertAlmostEqual((max(xs) - min(xs)) / statistics.mean(widths[c]), 1, delta=0.02, msg=c)

    def test_registered_before_averaging(self):
        """the samples are matched point to point before they are averaged (FR-006): a v
        written after a lead-in of a different length each time has its point at a different
        place along the stroke; averaged as they come the point rounds off and the v comes
        out shorter, registered it keeps its depth (the build's measures, before the height
        is stretched back)"""
        rnd = random.Random(7)

        def vee(lead):
            pts = [(0.3 * lead * i / 7, 1.0) for i in range(8)]
            pts += [(0.3 * lead + 0.3 * i / 12, 1.0 - i / 12) for i in range(1, 13)]
            pts += [(0.3 * lead + 0.3 + 0.3 * i / 12, i / 12) for i in range(1, 13)]
            return [[(x + rnd.gauss(0, 0.003), y + rnd.gauss(0, 0.003)) for x, y in pts]]

        ss = []
        for k in range(12):
            lead = 0.2 + 2.0 * ((k * 5) % 12) / 11
            ss.append(ink_sample("vo", [(vee(lead), 0.0), (shape("o", 0, rnd), 1.4)]))
        self.e.put(ss)

        def depth(avg):
            stats = self.e.dir / f"stats-{avg or 'dba'}"
            env = dict(self.e.env, GLASSINE_HANDWRITING_LETTERS_STATS=str(stats))
            if avg:
                env["GLASSINE_HANDWRITING_LETTERS_AVG"] = avg
            p = subprocess.run([str(BIN), "letters", "build", "-f"], env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stderr)
            rows = [ln.split("\t") for ln in stats.read_text().splitlines() if ln.split("\t")[1] == str(ord("v"))]
            self.assertTrue(rows)
            # HV over HM: the version's height as averaged over its samples'
            return min(float(r[8]) / float(r[7]) for r in rows), self.e.model()

        registered, model = depth("")
        unregistered, model_mean = depth("mean")
        # (spaced evenly along the stroke again after each round, the point itself can fall
        # between two of the version's points: a little of its depth goes)
        self.assertGreater(registered, 0.96)
        self.assertGreater(registered, unregistered + 0.01)
        # the way of averaging is part of the model's inputs
        self.assertNotEqual(json.loads(model.splitlines()[0])["inputs"], json.loads(model_mean.splitlines()[0])["inputs"])

    def test_registered_stays_fluid(self):
        """matching the samples point by point doesn't make a fluid curve wobble (FR-006):
        an s after lead-ins of different lengths, with a little or a lot of the pen's jitter,
        comes out no rougher against its samples registered than averaged as they come"""
        for jitter in (0.004, 0.008, 0.015):
            with self.subTest(jitter=jitter):
                self.fluid_s(jitter)

    def fluid_s(self, jitter):
        self.e.close()
        self.e = Env()
        rnd = random.Random(11)

        def ess(lead):
            # a straight lead-in along the curve's first direction, so the only turns are the
            # curve's own
            d = math.hypot(0.4 * math.pi, 1)
            pts = [(-0.3 * lead * (7 - i) / 7 * 0.4 * math.pi / d, -0.3 * lead * (7 - i) / 7 / d) for i in range(8)]
            for i in range(1, 41):
                y = i / 40
                pts.append((0.2 * math.sin(2 * math.pi * y), y))
            return [[(x + rnd.gauss(0, jitter), y + rnd.gauss(0, jitter)) for x, y in pts]]

        ss = []
        for k in range(12):
            lead = 0.2 + 2.0 * ((k * 5) % 12) / 11
            ss.append(ink_sample("so", [(ess(lead), 0.0), (shape("o", 0, rnd), 1.4)]))
        self.e.put(ss)

        def rough(avg):
            stats = self.e.dir / f"stats-{avg or 'dba'}"
            env = dict(self.e.env, GLASSINE_HANDWRITING_LETTERS_STATS=str(stats))
            if avg:
                env["GLASSINE_HANDWRITING_LETTERS_AVG"] = avg
            p = subprocess.run([str(BIN), "letters", "build", "-f"], env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stderr)
            rows = [ln.split("\t") for ln in stats.read_text().splitlines() if ln.split("\t")[1] == str(ord("s"))]
            self.assertTrue(rows)
            # RV over RM: the version's roughness against its samples'
            return max(float(r[12]) / float(r[11]) for r in rows)

        registered, unregistered = rough(""), rough("mean")
        self.assertLessEqual(registered, unregistered)

    def test_deterministic_and_order_free(self):
        ss = alphabet_samples(per=3)
        self.e.put(ss)
        self.assertEqual(self.e.run("letters", "build", "-r").stdout.split("\t")[0], "built")
        a = self.e.model()
        self.e.run("letters", "build", "-r", "-f")
        self.assertEqual(self.e.model(), a)
        other = Env()
        try:
            other.put(list(reversed(ss)))
            other.run("letters", "build", "-r")
            # the same letters (the header's newest time differs: the samples' times do)
            self.assertEqual(other.model().split("\n", 1)[1], a.split("\n", 1)[1])
        finally:
            other.close()
        # nothing changed: nothing to do
        self.assertEqual(self.e.run("letters", "build", "-r").stdout.strip(), "skip\tnothing-new")
        self.assertEqual(self.e.run("letters", "show").stdout, a)

    def test_three_usable_samples(self):
        rnd = random.Random(3)
        ss = [sample("ab", rnd) for _ in range(3)] + [sample("c", rnd) for _ in range(2)]
        # a word kept although its check failed (inferred spans) doesn't count
        bad = sample("cd", rnd)
        bad["spans_source"] = "inferred"
        self.e.put(ss + [bad])
        self.e.run("letters", "build", "-r")
        st = self.e.status()
        self.assertEqual(st["state"], ["current"])
        self.assertEqual(st["chars"]["a"][0], 3)
        self.assertGreater(st["chars"]["a"][1], 0)
        self.assertEqual(st["chars"]["c"], (2, 0))
        self.assertEqual(st["covered"], ["2"])

    def test_joined_letters_not_cut(self):
        rnd = random.Random(4)
        ss = []
        for _ in range(4):
            s = sample("xy", rnd)
            # one stroke for both letters, as joined writing has
            s["strokes"] = [s["strokes"][0] + s["strokes"][-1]]
            s["spans"] = [[0], [0]]
            ss.append(s)
        self.e.put(ss)
        self.e.run("letters", "build", "-r")
        self.assertEqual(self.e.status()["chars"], {})

    def test_joined_letters_join_their_variants(self):
        """letters written joined (FR-007): a stroke shared by the letters of a word is cut
        where its parts match those letters' variants from their samples written apart; each
        letter's part joins the variant it is written like (more members), with the joining
        stroke as that variant's entry and exit; the covered characters, the samples, the
        ways and the marks' samples stay those written apart, a character written only joined
        isn't covered; the same store gives the same model; without joined ink the model is
        the one a single pass makes"""
        rnd = random.Random(41)
        apart = alphabet_samples(seed=1, per=4)
        words = ["mane", "sun", "ace", "rose", "nose", "can", "ßa"]
        ink = []
        for w in words * 3:
            ink.append(joined(sample(w, rnd), range(len(w) - 1)))
        self.e.put(apart)
        stats = self.e.dir / "stats"
        self.e.env["GLASSINE_HANDWRITING_LETTERS_STATS"] = str(stats)
        self.e.run("letters", "build", "-r")
        two = self.e.model()
        del self.e.env["GLASSINE_HANDWRITING_LETTERS_STATS"]
        self.e.run("letters", "build", "-r")
        one = self.e.model()
        # (no joined ink: the second pass makes what the first does)
        self.assertEqual(one, two)
        before = {d["char"]: d for d in map(json.loads, one.splitlines()) if "char" in d}
        st0 = self.e.status()
        self.e.put(ink)
        self.e.run("letters", "build", "-r")
        after_text = self.e.model()
        after = {d["char"]: d for d in map(json.loads, after_text.splitlines()) if "char" in d}
        st1 = self.e.status()
        self.assertEqual({c for c, d in before.items() if d.get("covered")}, {c for c, d in after.items() if d.get("covered")})
        self.assertNotIn("ß", {c for c, d in after.items() if d.get("covered")})
        self.assertEqual(st0["chars"], {c: v for c, v in st1["chars"].items() if c != "ß"})
        gained = stubs = 0
        for c, d in after.items():
            b0 = before.get(c)
            if not b0 or not d.get("covered"):
                continue
            self.assertEqual(len(d["variants"]), len(b0["variants"]), c)
            for v1, v0 in zip(d["variants"], b0["variants"]):
                self.assertEqual((v1["ids"], v1["use"]), (v0["ids"], v0["use"]), c)
                self.assertGreaterEqual(v1["members"], v0["members"], c)
                gained += v1["members"] - v0["members"]
                for e in ("entry", "exit"):
                    if e not in v1:
                        continue
                    t = v1[e]
                    self.assertTrue(0 <= t["stroke"] < len(v1["strokes"]) and t["n"] >= 1, (c, e))
                    self.assertEqual(len(t["points"]), 8)
                    # an entry ends at the variant's stroke, an exit starts there
                    end = t["points"][-1] if e == "entry" else t["points"][0]
                    self.assertLess(math.hypot(*end), 1e-3, (c, e))
                    stubs += 1
        self.assertGreater(gained, 10)
        self.assertGreater(stubs, 3)
        self.e.run("letters", "build", "-r")
        self.assertEqual(self.e.model(), after_text)
        # one character learned again (its neighbours cut by the model's variants): the others
        # as they were, its own samples and ways, its entry and exit kept
        lines = lambda t: {json.loads(x).get("char"): x for x in t.splitlines() if x.startswith('{"char"')}
        for c in "as":
            was = lines(self.e.model())
            self.e.run("letters", "build", "-c", c)
            now = lines(self.e.model())
            self.assertEqual({k: v for k, v in now.items() if k != c}, {k: v for k, v in was.items() if k != c})
            d = json.loads(now[c])
            self.assertTrue(any("entry" in v or "exit" in v for v in d["variants"]), c)
            self.assertEqual([(v["ids"], v["use"]) for v in d["variants"]], [(v["ids"], v["use"]) for v in after[c]["variants"]])

    def test_sc001_forty_careful_samples(self):
        rnd = random.Random(5)
        # 40 samples of 7 characters, the alphabet in both cases and the digits over and over:
        # each one in 4 or 5 samples
        chars = ALNUM * 5
        ss = [sample(chars[i * 7:i * 7 + 7], rnd, written="careful") for i in range(40)]
        self.e.put(ss)
        self.e.run("letters", "build", "-r")
        st = self.e.status()
        self.assertEqual(sorted(c for c, v in st["chars"].items() if v[1] > 0), sorted(ALNUM))

    def test_variants_from_styles(self):
        # two ways of writing 'a', four times each: two variants, never mixed
        rnd = random.Random(6)
        self.e.put([sample("a", rnd, styles={"a": k % 2}) for k in range(8)])
        self.e.run("letters", "build", "-r")
        self.assertEqual(self.e.status()["chars"]["a"], (8, 2))
        model = [json.loads(x) for x in self.e.model().splitlines()]
        a = [x for x in model if x.get("char") == "a"][0]
        self.assertEqual(sum(v["members"] for v in a["variants"]), 8)

    def test_outdated_after_a_change(self):
        rnd = random.Random(7)
        self.e.put([sample("ab", rnd) for _ in range(3)])
        self.e.run("letters", "build", "-r")
        self.assertEqual(self.e.status()["state"], ["current"])
        self.e.put([sample("ab", rnd)])
        self.assertEqual(self.e.status()["state"], ["outdated"])
        self.e.run("letters", "build", "-r")
        sid = self.e.run("samples", "list").stdout.splitlines()[0].split("\t")[0]
        self.e.run("samples", "delete", sid)
        self.assertEqual(self.e.status()["state"], ["outdated"])

    def test_charging_unless_asked(self):
        rnd = random.Random(8)
        self.e.put([sample("ab", rnd) for _ in range(3)])
        (self.e.dir / "power" / "battery" / "status").write_text("Discharging\n")
        self.assertEqual(self.e.run("letters", "build").stdout.strip(), "skip\tcharging")
        self.assertEqual(self.e.run("letters", "build", "-r").stdout.split("\t")[0], "built")


# Characters written with a stroke that comes after the rest (a dot, a bar, an arm): their
# first strokes and the late ones, in x-heights (x from 0, y up from the baseline)
LATE = {
    "t": ([[(0.3, 1.5), (0.3, 0.0), (0.45, 0.05)]], [[(0.05, 1.0), (0.6, 1.0)]]),
    "f": ([[(0.65, 1.4), (0.4, 1.5), (0.3, 1.2), (0.3, 0.0)]], [[(0.05, 0.95), (0.6, 0.95)]]),
    "i": ([[(0.2, 1.0), (0.2, 0.0)]], [[(0.3, 1.45), (0.32, 1.47)]]),
    "j": ([[(0.35, 1.0), (0.35, -0.5), (0.05, -0.6)]], [[(0.45, 1.45), (0.47, 1.47)]]),
    "k": ([[(0.1, 1.5), (0.1, 0.0)]], [[(0.6, 1.0), (0.15, 0.5), (0.6, 0.0)]]),
    "x": ([[(0.0, 1.0), (0.6, 0.0)]], [[(0.6, 1.0), (0.0, 0.0)]]),
    "E": ([[(0.0, 1.5), (0.0, 0.0)]], [[(0.0, 1.5), (0.6, 1.5)], [(0.0, 0.75), (0.45, 0.75)], [(0.0, 0.0), (0.6, 0.0)]]),
    "H": ([[(0.0, 1.5), (0.0, 0.0)], [(0.6, 1.5), (0.6, 0.0)]], [[(0.0, 0.75), (0.6, 0.75)]]),
}
# written in turn, with a second stroke beside the first and too long to be a late one
BESIDE = {"K": [[(0.0, 1.5), (0.0, 0.0)], [(1.0, 1.5), (0.15, 0.75), (1.0, 0.0)]],
          # body first, close to the letter before it, then the bar above
          "5": [[(-0.25, 1.2), (-0.3, 0.7), (0.05, 0.65), (0.1, 0.2), (-0.25, 0.0)], [(-0.25, 1.25), (0.35, 1.25)]]}
ROUND = [[(0.3 + 0.3 * math.cos(a / 11 * 2 * math.pi), 0.5 + 0.5 * math.sin(a / 11 * 2 * math.pi)) for a in range(12)]]


# a t written in one stroke: the bar, back and up the stem, down
ONE_T = [[(0.0, 1.0), (0.6, 1.0), (0.3, 1.0), (0.3, 1.4), (0.3, 0.0), (0.5, 0.05)]]


def late_sample(word, rnd, where, line=150.0, xh=30.0, forms=None):
    """a prompted sample of word as a person writes it: each letter in turn, the late strokes
    of LATE's characters after the whole word, and spans as the alignment gives them, which
    follow the order of writing: a late stroke in the span of the word's last letter
    (where "next"), or in both its own letter's span and that one's ("shared"); a full stop's
    span also holds its neighbour's stroke"""
    strokes, spans, late = [], [[] for _ in word], []
    base, x, t = line * 2 / 3, 20.0, 0.0

    def put(st, x0):
        nonlocal t
        pts = []
        for px, py in st:
            pts.append([round(x0 + px * xh + rnd.gauss(0, 0.3), 3), round(base - py * xh + rnd.gauss(0, 0.3), 3),
                        round(t, 3), 0.5])
            t += 0.01
        # a few points more along each segment, as a pen gives them
        dense = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            for k in range(1, 6):
                dense.append([round(a[0] + (b[0] - a[0]) * k / 5, 3), round(a[1] + (b[1] - a[1]) * k / 5, 3),
                              round(a[2] + (b[2] - a[2]) * k / 5, 3), 0.5])
        strokes.append(dense)
        return len(strokes) - 1

    for k, c in enumerate(word):
        if c == ".":
            spans[k] = [put([(0.0, 0.0), (0.03, 0.02)], x)]
            spans[k].insert(0, spans[k - 1][-1])
            x += 0.4 * xh
            continue
        first, rest = (forms or {}).get(c) or LATE.get(c, (BESIDE.get(c, ROUND), []))
        for st in first:
            spans[k].append(put(st, x))
        if c in BESIDE and where == "shared" and k + 1 < len(word):   # its second stroke with the next letter too
            spans[k + 1].append(spans[k][-1])
        late += [(k, st, x) for st in rest]
        x += 1.0 * xh
    for k, st, x0 in late:
        j = put(st, x0)
        spans[-1].append(j)
        if where == "shared":
            spans[k].append(j)
    for sp in spans:
        sp.sort()
    return {"version": 1, "text": word, "lang": "en", "strokes": strokes, "spans": spans, "spans_source": "exact",
            "source": "prompted", "units": {"xy": "device", "per_mm": 4.0, "rate": 200},
            "area": {"box": [0, 0, max(x + 20, 200), line * 3], "line": line, "orientation": 0},
            "reading": [{"text": word, "score": -1.0}]}


class LateStrokes(unittest.TestCase):
    """FR-007: strokes go by where they lie; FR-030: counted without a model"""

    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

    def versions(self):
        out = {}
        for x in self.e.model().splitlines():
            d = json.loads(x)
            if "char" in d:
                out[d["char"]] = [len(v["strokes"]) for v in d["variants"]]
        return out

    def test_late_strokes_stay_with_their_letter(self):
        rnd = random.Random(21)
        ss = []
        for c in LATE:
            for k in range(4):
                ss.append(late_sample("a" + c + "o", rnd, "next" if k % 2 else "shared"))
            ss.append(late_sample(c, rnd, "next"))
        self.e.put(ss)
        self.e.run("letters", "build", "-r")
        v, st = self.versions(), self.e.status()
        for c, (first, rest) in LATE.items():
            with self.subTest(c=c):
                self.assertEqual(st["chars"][c][0], 5)
                self.assertTrue(v[c], f"{c} not covered")
                self.assertEqual(set(v[c]), {len(first) + len(rest)})
        # the letters the late strokes were put with count too
        self.assertEqual(st["chars"]["o"][0], 4 * len(LATE))
        self.assertEqual(st["chars"]["a"][0], 4 * len(LATE))

    def model_chars(self):
        return {d["char"]: d for d in map(json.loads, self.e.model().splitlines()) if "char" in d}

    def test_each_way_of_writing_in_proportion(self):
        """a t written in two strokes in two thirds of the samples and in one in a third gets a
        variant for each, weighted and in the font's cycle about as often"""
        rnd = random.Random(30)
        self.e.put([late_sample("ato", rnd, "next") for _ in range(8)] +
                   [late_sample("ato", rnd, "next", forms={"t": (ONE_T, [])}) for _ in range(4)])
        self.e.run("letters", "build", "-r")
        t = self.model_chars()["t"]
        ways = {}
        for v in t["variants"]:
            ways[len(v["strokes"])] = ways.get(len(v["strokes"]), 0) + v["use"]
        self.assertEqual(ways, {2: 8, 1: 4})
        self.assertEqual(len(t["variants"][0]["strokes"]), 2)          # the most used first
        per = {}
        for k in t["slots"]:
            n = len(t["variants"][k]["strokes"])
            per[n] = per.get(n, 0) + 1
        self.assertLessEqual(abs(per[2] - 2 * SLOTS / 3), 1)
        self.assertLessEqual(abs(per[1] - SLOTS / 3), 1)
        # the same samples, the same model
        before = self.e.model()
        self.e.run("letters", "build", "-r", "-f")
        self.assertEqual(self.e.model(), before)

    def test_a_one_off_way_gives_no_variant(self):
        rnd = random.Random(31)
        self.e.put([late_sample("ato", rnd, "next") for _ in range(12)] +
                   [late_sample("ato", rnd, "next", forms={"t": (ONE_T, [])})])
        self.e.run("letters", "build", "-r")
        t = self.model_chars()["t"]
        self.assertEqual({len(v["strokes"]) for v in t["variants"]}, {2})
        self.assertEqual(sum(v["use"] for v in t["variants"]), 12)

    def test_stroke_order_is_a_way(self):
        """an x written left stroke first and right stroke first: a variant for each order"""
        rnd = random.Random(32)
        a = ([[(0.0, 1.0), (0.7, 0.0)]], [[(0.7, 1.0), (0.0, 0.0)]])
        b = ([[(0.7, 1.0), (0.0, 0.0)]], [[(0.0, 1.0), (0.7, 0.0)]])
        self.e.put([late_sample("axo", rnd, "next", forms={"x": a}) for _ in range(5)] +
                   [late_sample("axo", rnd, "next", forms={"x": b}) for _ in range(5)])
        self.e.run("letters", "build", "-r")
        x = self.model_chars()["x"]
        firsts = {tuple(round(c) for c in v["strokes"][0][0]) for v in x["variants"]}
        starts = {(v["strokes"][0][0][0] < v["strokes"][0][-1][0]) for v in x["variants"]}
        self.assertEqual(starts, {True, False}, firsts)
        self.assertEqual(sum(v["use"] for v in x["variants"]), 10)

    def test_a_stroke_beside_two_stays_shared(self):
        """a K's long arm shared with the next letter, beside both, is not given to either"""
        rnd = random.Random(25)
        self.e.put([late_sample("aKo", rnd, "next") for _ in range(3)] +
                   [late_sample("aKo", rnd, "shared") for _ in range(3)])
        self.e.run("letters", "build", "-r")
        st = self.e.status()
        self.assertEqual(st["chars"]["K"][0], 3)          # only the three with their own arm
        self.assertEqual(set(self.versions()["K"]), {2})

    def test_a_body_below_its_bar_stays(self):
        """a 5's body, beside the 6 before it and below its own bar, is the 5's"""
        rnd = random.Random(26)
        self.e.put([late_sample("ao65", rnd, "next") for _ in range(3)])
        self.e.run("letters", "build", "-r")
        st, v = self.e.status(), self.versions()
        self.assertEqual(st["chars"]["5"][0], 3)
        self.assertEqual(set(v["5"]), {2})
        self.assertEqual(set(v["6"]), {1})

    def test_lone_marks_confirmed(self):
        """a full stop or comma written alone and kept on confirmation (spans inferred)
        counts; in a word only with its check passed"""
        rnd = random.Random(27)
        ss = []
        for c in ".,":
            for _ in range(3):
                d = late_sample("a" + c, rnd, "next")
                d.update(text=c, spans=[list(range(len(d["strokes"]) - 1, len(d["strokes"])))],
                         strokes=d["strokes"][-1:], spans_source="inferred")
                d["spans"] = [[0]]
                ss.append(d)
        for _ in range(3):
            d = late_sample("ao.", rnd, "next")
            d["spans_source"] = "inferred"
            ss.append(d)
        self.e.put(ss)
        self.e.run("letters", "build", "-r")
        st = self.e.status()
        self.assertEqual(st["chars"]["."][0], 3)
        self.assertEqual(st["chars"][","][0], 3)
        self.assertNotIn("a", st["chars"])

    def test_full_stop_and_its_neighbour(self):
        rnd = random.Random(22)
        self.e.put([late_sample("ao.", rnd, "next") for _ in range(4)])
        self.e.run("letters", "build", "-r")
        st = self.e.status()
        self.assertEqual(st["chars"]["."][0], 4)
        self.assertEqual(st["chars"]["o"][0], 4)
        self.assertEqual(self.versions()["."], [1] * len(self.versions()["."]))

    def test_counted_without_a_model(self):
        rnd = random.Random(23)
        ss = [late_sample("a" + c + "o", rnd, "shared") for c in "tix" for _ in range(3)]
        self.e.put(ss)
        st = self.e.status()
        self.assertEqual(st["state"], ["outdated"])      # the empty model hasn't learned them
        before = (st["samples"], st["covered"], {c: n for c, (n, _) in st["chars"].items()})
        self.assertEqual(before[2]["t"], 3)
        self.assertTrue(all(nv == 0 for _, nv in st["chars"].values()))
        self.e.run("letters", "build", "-r")
        st = self.e.status()
        self.assertEqual((st["samples"], st["covered"], {c: n for c, (n, _) in st["chars"].items()}), before)
        model = {d["char"]: d["samples"] for d in map(json.loads, self.e.model().splitlines()) if "char" in d}
        self.assertEqual(model, before[2])
        # more samples: the counts follow at once, the model is out of date
        self.e.put([late_sample("ako", rnd, "next") for _ in range(3)])
        st = self.e.status()
        self.assertEqual(st["state"], ["outdated"])
        self.assertEqual(st["chars"]["k"], (3, 0))
        self.assertEqual(st["chars"]["o"][0], 12)
        # the counts come from the derived file next time; erasing takes it out
        self.assertTrue((self.e.store / "letters" / "usable").exists())
        self.assertEqual(self.e.status(), st)
        self.e.run("samples", "erase")
        self.assertFalse((self.e.store / "letters").exists())
        self.assertEqual(self.e.status()["chars"], {})

    def test_build_progress(self):
        rnd = random.Random(24)
        self.e.put([late_sample("ato", rnd, "next") for _ in range(3)])
        out = self.e.run("letters", "build", "-r", "--progress").stdout.splitlines()
        self.assertTrue(out[0].startswith("progress\t"))
        self.assertTrue(all(x.startswith("progress\t") for x in out[:-1]))
        self.assertEqual(out[-1].split("\t")[0], "built")


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class Spacing(unittest.TestCase):
    """spec 003 FR-006, FR-007, FR-009: one spacing from the letters' profiles and middles: a
    pair's own gap and distance, a rare pair its side shapes' gap, an unseen pair one from
    the profiles; letters written the same in words and alone come out at their gap, words
    at the word spacing"""
    GAPS = {"lo": 0.05, "ol": 0.7, "ab": 0.5}

    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(21)
        ss = alphabet_samples(seed=21, per=3)
        for k in range(5):
            ss.append(spaced_sample("lol abc", rnd, cls.GAPS))
            ss.append(spaced_sample("ab cde", rnd, cls.GAPS))
        ss.append(spaced_sample("zq", rnd, cls.GAPS))           # written once
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        lines = [json.loads(x) for x in cls.e.model().splitlines()]
        cls.sp = next(d["spacing"] for d in lines if "spacing" in d)
        cls.chars = {d["char"]: d for d in lines if "char" in d}
        cls.ttf = cls.e.dir / "f.ttf"
        cls.e.run("font", "build", "-n", "Spaced", str(cls.ttf))

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def test_learned(self):
        pairs = {(p[0], p[1]): (p[2], p[3]) for p in self.sp["pairs"]}
        # in the letter model's x-height, which the made-up letters' heights set a little
        # differently from the writing's: gaps scale with it alike
        r = pairs[("b", "c")][0] / 0.35
        self.assertAlmostEqual(r, 1, delta=0.15)
        for ab, g in self.GAPS.items():
            self.assertAlmostEqual(pairs[(ab[0], ab[1])][0], g * r, delta=0.02, msg=ab)
        self.assertNotIn(("z", "q"), pairs)                    # too rare for a gap of its own
        self.assertAlmostEqual(self.sp["word"], 1.2 * r, delta=0.03)
        for d in self.chars.values():
            for v in d["variants"]:
                self.assertEqual(len(v["left"]), BANDS)
                self.assertGreaterEqual(v["lc"], 0)
                self.assertGreaterEqual(v["rc"], 0)

    def placed(self, text):
        """per glyph of text as shaped: its variant and pen x, in x-heights"""
        if SKIP_TOOLS:
            self.skipTest("no hb-shape")
        p = subprocess.run(["hb-shape", "--no-glyph-names", "--output-format=json", str(self.ttf), text], capture_output=True, text=True,
                           check=True)
        gl = font_glyphs(self.e.model())
        out, x = [], 0
        for g in json.loads(p.stdout):
            k = gl[g["g"]]
            # a step's variant by the cycle, or the variant a neighbour rule shows (FR-038)
            c = " " if k[0] == "space" else k[1]
            v = None if c == " " else self.chars[c]["variants"][self.chars[c]["slots"][k[2]] if k[0] == "step" else k[2]]
            out.append((c, v, (x + g["dx"]) / 500))
            x += g["ax"]
        return out

    def prof(self, v):
        nan = float("nan")
        return ([nan if x is None else x for x in v["left"]], [nan if x is None else x for x in v["right"]])

    def gap_of(self, a, b):
        pa, pb = self.prof(a[1]), self.prof(b[1])
        ds = [pb[0][k] - pa[1][k] for k in range(BANDS) if pa[1][k] == pa[1][k] and pb[0][k] == pb[0][k]]
        return min(ds) + b[2] - a[2]

    def test_font_places_the_pairs(self):
        pairs = {p[0] + p[1]: p[2] for p in self.sp["pairs"]}
        for text in ("lol", "olo", "abc", "lolol ab abc"):
            pl = self.placed(text)
            for a, b in zip(pl, pl[1:]):
                if a[0] == " " or b[0] == " ":
                    continue
                self.assertAlmostEqual(self.gap_of(a, b), pairs[a[0] + b[0]], delta=0.02, msg=text)
        # words at the word spacing: box to box
        pl = self.placed("ab cd")
        a, b = pl[1], pl[3]
        right = max(p[0] for st in a[1]["strokes"] for p in st)
        left = min(p[0] for st in b[1]["strokes"] for p in st)
        gap = self.sp["letter"]
        self.assertAlmostEqual((b[2] + gap / 2 + left) - (a[2] + gap / 2 + right), self.sp["word"], delta=0.02)
        # a pair never written: a gap from its side shapes or its profiles, near the typical one
        pl = self.placed("qz")
        self.assertLess(abs(self.gap_of(pl[0], pl[1]) - self.sp["letter"]), 0.31)


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class Overhangs(Spacing):
    """spec 003 FR-007, FR-009: letters with an overhang (a bar, a hook) that sits differently
    in the person's words than in a variant come out at the pair's real distance (the
    letters' middles), not at the gap the overhang happened to set in the words: an o not
    tucked under an f's bar, an l not pushed out past a long T bar"""
    DIST = {"fo": 1.0, "Tl": 0.6}

    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(31)
        ss = []
        for _ in range(6):
            ss += [overhang_word(w, rnd, cls.DIST) for w in ("fo", "Tl")]
        for _ in range(4):
            ss += [overhang_word(c, rnd, cls.DIST, "alone") for c in "fTfTfTol"]
        cls.words = ss[:12]
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        lines = [json.loads(x) for x in cls.e.model().splitlines()]
        cls.sp = next(d["spacing"] for d in lines if "spacing" in d)
        cls.chars = {d["char"]: d for d in lines if "char" in d}
        cls.ttf = cls.e.dir / "f.ttf"
        cls.e.run("font", "build", "-n", "Overhangs", str(cls.ttf))

    def scale(self):
        """the letter model's x-height against the writing's (the o's height sets it)"""
        return 1 / max(p[1] for v in self.chars["o"]["variants"] for st in v["strokes"] for p in st)

    def test_learned(self):
        pairs = {p[0] + p[1]: p for p in self.sp["pairs"]}
        r = self.scale()
        for ab, d in self.DIST.items():
            self.assertAlmostEqual(pairs[ab][4], d * r, delta=0.05, msg=ab)
        # the variants alone carry their long overhang
        self.assertTrue(any(max(p[0] for st in v["strokes"] for p in st) > 2 for v in self.chars["T"]["variants"]))
        self.assertTrue(any(min(p[1] for p in v["strokes"][1]) > 1 for v in self.chars["f"]["variants"]))
        # in words before their neighbour the way written there, by a rule (FR-038)
        for c, nb in (("T", "l"), ("f", "o")):
            r = [r for r in self.chars[c].get("rules", []) if r.get("next") == nb]
            self.assertEqual(len(r), 1, c)
            self.assertEqual((r[0]["n"], r[0]["same"]), (6, 6))

    def test_font_places_the_pairs(self):
        r = self.scale()
        letter = self.sp["letter"]
        for text in ("fo", "Tl", "fo Tl Tl fo"):
            pl = self.placed(text)
            for a, b in zip(pl, pl[1:]):
                if a[0] == " " or b[0] == " ":
                    continue
                d = (b[2] + b[1]["middle"]) - (a[2] + a[1]["middle"])
                self.assertAlmostEqual(d, self.DIST[a[0] + b[0]] * r, delta=0.08, msg=(text, a[0] + b[0]))
                # between the baseline and the x-height they never come closer than their
                # gap, or half the typical gap
                pa, pb = self.prof(a[1]), self.prof(b[1])
                body = [pb[0][k] + b[2] - pa[1][k] - a[2] for k in range(BANDS)
                        if Y0 + (k + 1) * (Y1 - Y0) / BANDS > MID_Y0 and Y0 + k * (Y1 - Y0) / BANDS < MID_Y1
                        and pa[1][k] == pa[1][k] and pb[0][k] == pb[0][k]]
                gap = {p[0] + p[1]: p[2] for p in self.sp["pairs"]}[a[0] + b[0]]
                self.assertGreaterEqual(min(body), min(gap, letter / 2) - 0.02, msg=(text, a[0] + b[0]))


def raised_mark(c, style, rnd):
    """a mark with little or no ink running up or down between the baseline and the
    x-height: "=" written above it, "?" a crown over a dot on the line (pen jitter added)"""
    def line(x0, y0, x1, y1, n=12):
        return [(x0 + (x1 - x0) * i / (n - 1) + rnd.gauss(0, 0.01), y0 + (y1 - y0) * i / (n - 1) + rnd.gauss(0, 0.005))
                for i in range(n)]
    if c == "=":
        return [line(0.0, 0.98, 1.0, 0.98), line(0.0, 1.35, 1.0, 1.35)]
    crown = [(0.5 + 0.5 * math.cos(math.pi * (1 - i / 11)), 1.9 + 0.5 * math.sin(math.pi * (1 - i / 11)))
             for i in range(12)]
    crown += line(1.0, 1.9, 0.9, 1.0)[1:]
    return [[(x + rnd.gauss(0, 0.01), y + rnd.gauss(0, 0.01)) for x, y in crown], [(0.9, 0.0)]]


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class RaisedMarks(unittest.TestCase):
    """spec 003 FR-007, FR-009: a mark the person writes mostly outside the x-height band (a
    raised "=", a "?" whose crown stands over a dot), never written beside the letters, is
    set beside a small letter, not over it: where the two have ink at about the same height
    (within a band) they keep at least half the typical gap"""

    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(41)
        ss = alphabet_samples(seed=41, per=3)
        ss += [sample(c, rnd, shapes={"=": raised_mark, "?": raised_mark}) for c in "=?" * 4]
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        lines = [json.loads(x) for x in cls.e.model().splitlines()]
        cls.sp = next(d["spacing"] for d in lines if "spacing" in d)
        cls.chars = {d["char"]: d for d in lines if "char" in d}
        cls.ttf = cls.e.dir / "f.ttf"
        cls.e.run("font", "build", "-n", "Marks", str(cls.ttf))

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def test_beside_not_over(self):
        self.assertTrue(self.chars["="]["covered"] and self.chars["?"]["covered"])
        # (the marks' own pairs with the letters were never written: set at the gap)
        self.assertFalse([p for p in self.sp["pairs"] if "=" in p[:2] or "?" in p[:2]])
        gl = font_glyphs(self.e.model())
        letter, band = self.sp["letter"], (Y1 - Y0) / BANDS
        seen = 0
        for text in ("o=o", "c=x", "a=e", "o?o", "c?x", "a?u", "x=s?n"):
            out, x = [], 0
            for g in hb(self.ttf, text):
                k = gl[g["g"]]
                d = self.chars[k[1]]
                v = d["variants"][d["slots"][k[2]] if k[0] == "step" else k[2]]
                at = (x + g["dx"]) / 500 + letter / 2
                out.append([(at + p[0], p[1]) for st in v["strokes"] for p in st])
                x += g["ax"]
            for a, b in zip(out, out[1:]):
                near = [q[0] - p[0] for p in a for q in b if abs(q[1] - p[1]) < band]
                if near:
                    self.assertGreater(min(near), letter / 2 - 0.05, text)
                    seen += 1
        self.assertGreaterEqual(seen, 8)


class Marks(unittest.TestCase):
    """the person's marks on the letter model (spec 003 FR-031) and one character learned again
    (FR-032)"""

    def setUp(self):
        self.e = Env()
        rnd = random.Random(11)
        ss = alphabet_samples(seed=11, per=3)
        ss += [sample("l", rnd, styles={"l": k % 3}) for k in range(9)]
        self.e.put(ss)
        self.e.run("letters", "build", "-r")

    def tearDown(self):
        self.e.close()

    def chars(self):
        return {d["char"]: d for d in map(json.loads, self.e.model().splitlines()) if "char" in d}

    def test_variants_list_their_samples(self):
        l = self.chars()["l"]
        self.assertGreaterEqual(len(l["variants"]), 2)
        for v in l["variants"]:
            self.assertEqual(len(v["ids"]), v["use"])
            self.assertTrue(all(len(i) == 16 for i in v["ids"]))

    def test_favourite_shown_more_often(self):
        l = self.chars()["l"]
        k = len(l["variants"]) - 1
        before = l["slots"].count(k)
        self.e.run("letters", "mark", "favourite", "l", *l["variants"][k]["ids"])
        self.assertEqual(self.e.status()["state"], ["outdated"])
        out = self.e.run("letters", "build", "-c", "l").stdout
        self.assertTrue(out.startswith("relearned\t1\t"), out)
        self.assertEqual(self.e.status()["state"], ["current"])
        l = self.chars()["l"]
        self.assertTrue(l["variants"][k].get("favourite"))
        self.assertGreater(l["slots"].count(k), before)
        # one favourite per character
        self.e.run("letters", "mark", "favourite", "l", *l["variants"][0]["ids"])
        kinds = [ln.split("\t")[3] for ln in self.e.run("letters", "marks").stdout.splitlines()]
        self.assertEqual(kinds.count("favourite"), len(l["variants"][0]["ids"]))

    def test_best_version(self):
        """each version's best version (spec 003 FR-035): stored only where the samples it is
        made of count unlike (no marks: none), leaned to the favourite sample, of the
        version's strokes and points; a left-out version has none"""
        l = self.chars()["l"]
        self.assertFalse(any("best" in v for v in l["variants"]))
        k = max(range(len(l["variants"])), key=lambda i: l["variants"][i]["members"])
        ids = l["variants"][k]["ids"]
        self.e.run("letters", "mark", "favourite", "l", ids[0])
        self.e.run("letters", "build", "-c", "l")
        v = self.chars()["l"]["variants"][k]
        self.assertIn("best", v)
        self.assertEqual([len(st) for st in v["best"]], [len(st) for st in v["strokes"]])
        self.assertNotEqual(v["best"], v["strokes"])
        # another favourite, another best version
        self.e.run("letters", "mark", "favourite", "l", ids[-1])
        self.e.run("letters", "build", "-c", "l")
        self.assertNotEqual(self.chars()["l"]["variants"][k]["best"], v["best"])
        self.e.run("letters", "mark", "way-out", "l", *self.chars()["l"]["variants"][k]["ids"])
        self.e.run("letters", "build", "-c", "l")
        self.assertNotIn("best", self.chars()["l"]["variants"][k])

    def test_best_version_careful(self):
        """samples written to "Write it your best" count more in the best version than quick ones"""
        e = Env()
        try:
            rnd = random.Random(31)
            careful = [sample("l", rnd, written="careful") for _ in range(4)]
            for c in careful:
                # written more upright than the quick ones
                for st in c["strokes"]:
                    for p in st:
                        p[0] = round(p[0] + 0.05 * (p[1] - 100), 3)
            e.put(alphabet_samples(seed=31, per=3) + careful + [sample("l", rnd, written="quick") for _ in range(4)])
            e.run("letters", "build", "-r")
            L = {d["char"]: d for d in map(json.loads, e.model().splitlines()) if "char" in d}
            self.assertTrue(any("best" in v for v in L["l"]["variants"]))
            # the other letters: all written alike, no best version
            self.assertFalse(any("best" in v for c, d in L.items() if c != "l" for v in d["variants"]))
        finally:
            e.close()

    def test_variant_left_out_and_put_back(self):
        l = self.chars()["l"]
        ids = l["variants"][0]["ids"]
        self.e.run("letters", "mark", "way-out", "l", *ids)
        self.e.run("letters", "build", "-c", "l")
        m = self.chars()["l"]
        self.assertTrue(m["variants"][0].get("left_out"))
        self.assertNotIn(0, m["slots"])
        self.assertTrue(m["covered"])
        states = {f[1]: f[3] for f in (ln.split("\t") for ln in self.e.run("letters", "samples", "l").stdout.splitlines())}
        self.assertTrue(all(states[i] == "way-out" for i in ids))
        self.e.run("font", "install")
        self.assertEqual(self.e.status()["font-letters"], ["same"])
        self.e.run("letters", "mark", "clear", "l", *ids)
        out = self.e.run("letters", "build", "-c", "l", "--progress").stdout.splitlines()
        self.assertTrue(out[0].startswith("progress\t"), out)
        self.assertTrue(out[-1].startswith("relearned\t"), out)
        # the installed font stays as it was until it is made again
        self.assertEqual(self.e.status()["font-letters"], ["changed"])
        self.assertFalse(self.chars()["l"]["variants"][0].get("left_out"))
        self.assertIn(0, self.chars()["l"]["slots"])

    def test_sample_left_out(self):
        before = self.e.status()["chars"]["l"][0]
        sid = self.chars()["l"]["variants"][0]["ids"][0]
        self.e.run("letters", "mark", "out", "l", sid)
        self.assertEqual(self.e.status()["chars"]["l"][0], before - 1)
        self.e.run("letters", "build", "-c", "l")
        l = self.chars()["l"]
        self.assertFalse(any(sid in v["ids"] for v in l["variants"]))
        self.assertEqual(l["samples"], before - 1)
        st = [ln.split("\t") for ln in self.e.run("letters", "samples", "l").stdout.splitlines()]
        self.assertIn([sid, "out"], [[f[1], f[3]] for f in st])
        # still a sample: it stays in the store; deleting it drops its marks
        self.assertIn(sid, self.e.run("samples", "list").stdout)
        self.e.run("samples", "delete", sid)
        self.assertEqual(self.e.run("letters", "marks").stdout, "")

    def test_marks_erased_with_the_samples(self):
        sid = self.chars()["l"]["variants"][0]["ids"][0]
        self.e.run("letters", "mark", "out", "l", sid)
        self.e.run("samples", "erase")
        self.assertEqual(self.e.run("letters", "marks").stdout, "")
        self.assertFalse((self.e.store / "marks").exists() and (self.e.store / "marks").read_text())

    def test_marks_are_not_exported(self):
        sid = self.chars()["l"]["variants"][0]["ids"][0]
        self.e.run("letters", "mark", "out", "l", sid)
        f = self.e.dir / "out.jsonl"
        self.e.run("samples", "export", str(f))
        self.assertNotIn("way-out", f.read_text())
        self.assertNotIn('"out"', f.read_text())

    def test_unknown_sample_refused(self):
        p = self.e.run("letters", "mark", "out", "l", "0" * 16, check=False)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(p.stdout.strip(), "error\tno-such-sample")

    def test_relearn_leaves_the_rest(self):
        before = {k: v for k, v in self.chars().items() if k != "l"}
        head = self.e.model().splitlines()[0]
        self.e.run("letters", "mark", "out", "l", self.chars()["l"]["variants"][0]["ids"][0])
        self.e.run("letters", "build", "-c", "l")
        after = {k: v for k, v in self.chars().items() if k != "l"}
        self.assertEqual(before, after)
        self.assertEqual(self.e.model().splitlines()[0], head)

    def test_relearn_as_a_whole_build(self):
        """one character learned again with nothing changed: its line as the whole build made it"""
        rnd = random.Random(3)
        self.e.put([sample("hello", rnd) for _ in range(3)] + [sample("l", rnd) for _ in range(2)])
        self.e.run("letters", "build", "-r")
        for c in "lhoa":
            before = [ln for ln in self.e.model().splitlines() if ln.startswith('{"char":"%s"' % c)]
            self.e.run("letters", "build", "-c", c)
            after = [ln for ln in self.e.model().splitlines() if ln.startswith('{"char":"%s"' % c)]
            self.assertEqual(before, after, c)

    def test_model_without_ids_outdated(self):
        """a model built before the marks (no sample ids per version) is out of date"""
        lines = []
        for ln in self.e.model().splitlines():
            d = json.loads(ln)
            for v in d.get("variants", []):
                v.pop("ids", None)
            lines.append(json.dumps(d))
        (self.e.store / "letters" / "model").write_text("\n".join(lines) + "\n")
        self.assertEqual(self.e.status()["state"], ["outdated"])
        self.assertTrue(self.e.run("letters", "build").stdout.startswith("built"))

    def test_relearn_is_fast(self):
        import time
        t = time.monotonic()
        self.e.run("letters", "build", "-c", "l")
        self.assertLess(time.monotonic() - t, 5.0)


class Font(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(11)
        ss = alphabet_samples(seed=11, per=3)
        # 'l' and 'o' in three and two styles
        ss += [sample("l", rnd, styles={"l": k % 3}) for k in range(9)]
        ss += [sample("o", rnd, styles={"o": k % 2}) for k in range(6)]
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        cls.st = cls.e.status()
        cls.nvar = {c: v[1] for c, v in cls.st["chars"].items() if v[1] > 0}
        cls.covered = set(cls.nvar)
        cls.slots = {}
        for x in cls.e.model().splitlines():
            d = json.loads(x)
            if d.get("covered") and d.get("variants"):
                cls.slots[d["char"]] = d["slots"]
        cls.ttf = cls.e.dir / "f.ttf"
        cls.e.run("font", "build", "-n", "Test hand", str(cls.ttf))

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def test_valid_and_exact_characters(self):
        from fontTools.ttLib import TTFont
        f = TTFont(str(self.ttf), checkChecksums=2)
        for t in f.keys():
            if t != "GlyphOrder":
                f[t].compile(f)                     # every table decodes and compiles again
        cmap = f.getBestCmap()
        self.assertEqual({chr(c) for c in cmap}, self.covered | {" "})
        self.assertEqual(f["name"].getDebugName(1), "Test hand")
        self.assertIn("calt", [r.FeatureTag for r in f["GSUB"].table.FeatureList.FeatureRecord])
        # .notdef, the space and each character at each step of the cycle (a reference to
        # one of its variants), then the variants, which have no character of their own
        self.assertEqual(len(f.getGlyphOrder()), 1 + (len(self.covered) + 1) * SLOTS + sum(self.nvar.values()))
        glyf = f["glyf"]
        for c in self.covered:
            g = glyf[cmap[ord(c)]]
            self.assertTrue(g.isComposite(), c)
            self.assertEqual(len(g.components), 1)
            self.assertGreater(glyf[g.components[0].glyphName].numberOfContours, 0, c)
        self.assertEqual(glyf[cmap[32]].numberOfContours, 0)
        # the same model, name and version: the same bytes
        again = self.e.dir / "g.ttf"
        self.e.run("font", "build", "-n", "Test hand", str(again))
        self.assertEqual(again.read_bytes(), self.ttf.read_bytes())

    def shaped(self, text):
        p = subprocess.run(["hb-shape", "--no-glyph-names", "--no-positions", "--no-clusters", str(self.ttf), text],
                           capture_output=True, text=True, check=True)
        return [int(x) for x in p.stdout.strip().strip("[]").split("|")]

    def expected(self, text):
        """the glyph ids the font gives text (hw_letters_step) and the variant each shows"""
        cov = sorted(self.covered)
        idx = {c: 1 + i for i, c in enumerate(cov)}
        idx[" "] = 0
        out, var, prev = [], [], None
        for c in text:
            st = step(prev)
            out.append(1 + idx[c] * SLOTS + st)
            var.append(self.slots[c][st] if c != " " else None)
            prev = st
        return out, var

    def test_alternates(self):
        self.assertEqual(self.nvar["l"], 3)
        self.assertEqual(self.nvar["o"], 2)
        text = "lllll ooo lol hello"
        a = self.shaped(text)
        self.assertEqual(a, self.shaped(text))
        want, var = self.expected(text)
        self.assertEqual(a, want)
        self.assertEqual(len(set(var[:5])), 3)    # 'lllll' isn't stamped
        # each step's glyph refers to the variant the model gives that step
        from fontTools.ttLib import TTFont
        f = TTFont(str(self.ttf))
        order = f.getGlyphOrder()
        outl = 1 + (len(self.covered) + 1) * SLOTS
        first = {}
        for c in sorted(self.covered):
            first[c] = outl
            outl += self.nvar[c]
        for c in ("l", "o"):
            for st in range(SLOTS):
                g = f["glyf"][order[1 + (1 + sorted(self.covered).index(c)) * SLOTS + st]]
                self.assertEqual(g.components[0].glyphName, order[first[c] + self.slots[c][st]])

    def test_variants_in_proportion(self):
        """along a long text each variant shows about as often as it is used"""
        text = " ".join(["hello world lol"] * 40)
        a = self.shaped(text)
        want, var = self.expected(text)
        self.assertEqual(a, want)
        use = {}
        for x in self.e.model().splitlines():
            d = json.loads(x)
            if d.get("char") in ("l", "o"):
                use[d["char"]] = [v["use"] for v in d["variants"]]
        for c in ("l", "o"):
            shown = [v for ch, v in zip(text, var) if ch == c]
            for k, u in enumerate(use[c]):
                self.assertAlmostEqual(shown.count(k) / len(shown), u / sum(use[c]), delta=0.12, msg=c)

    def test_install_found_rebuild_erase(self):
        e = Env()
        try:
            e.put(alphabet_samples(seed=12, per=3))
            e.run("letters", "build", "-r")
            out = e.run("font", "install").stdout.strip().split("\t")
            self.assertEqual(out[:3], ["installed", "Autograph", "1"])
            path = Path(out[3])
            self.assertEqual(path.parent, e.fonts)
            m = subprocess.run(["fc-match", "-f", "%{family}\n%{file}\n", "Autograph"], env=e.env,
                               capture_output=True, text=True, check=True).stdout.splitlines()
            self.assertEqual(m, ["Autograph", str(path)])
            # rebuilt: the same name, a new version and file, the old one gone
            out = e.run("font", "install").stdout.strip().split("\t")
            self.assertEqual(out[:3], ["installed", "Autograph", "2"])
            self.assertFalse(path.exists())
            path = Path(out[3])
            self.assertEqual(e.run("font", "status").stdout.splitlines()[0].split("\t"), ["Autograph", "2", str(path)])
            lst = subprocess.run(["fc-list", ":", "family", "file"], env=e.env, capture_output=True, text=True,
                                 check=True).stdout
            self.assertIn(str(path), lst)
            self.assertEqual(len(list(e.fonts.iterdir())), 3)     # the three styles
            # the name is Autograph's: a name is for an export (FR-012)
            self.assertEqual(e.run("font", "install", "-n", "Other", check=False).returncode, 2)
            self.assertEqual(e.run("font", "export", "-n", "bad\tname", str(e.dir), check=False).returncode, 2)
            # erasing the samples takes the letter model; without a base Autograph has nothing
            # to draw and goes (with one it goes back to the base: Autograph)
            e.run("samples", "erase")
            self.assertFalse(path.exists())
            self.assertFalse((e.store / "letters" / "model").exists())
            self.assertEqual(e.run("font", "status").stdout.strip(), "none")
            self.assertEqual(e.status()["state"], ["current"])
        finally:
            e.close()

    def test_client_erase_and_remove(self):
        e = Env()
        try:
            e.put(alphabet_samples(seed=13, per=3))
            e.run("letters", "build", "-r")
            path = Path(e.run("font", "install").stdout.strip().split("\t")[3])
            e.run("font", "remove")
            self.assertFalse(path.exists())
            self.assertEqual(e.run("font", "status").stdout.strip(), "none")
            path = Path(e.run("font", "install").stdout.strip().split("\t")[3])
            e.run("samples", "erase", "-c", "org.example.test")
            self.assertFalse(path.exists())
            self.assertFalse((e.store / "letters" / "model").exists())
        finally:
            e.close()

    def test_no_model_no_font(self):
        e = Env()
        try:
            # the empty model and no base: nothing to draw
            self.assertEqual(e.run("font", "install").stdout.strip(), "skip\tnothing-covered")
            self.assertFalse(e.fonts.exists())
        finally:
            e.close()


# ---------------------------------------------------------------- the styles (FR-034, FR-006)

K = 16          # letters.h HW_LETTERS_PROFILE
STYLES = ("Regular", "Calligraphy", "Brush")


def written_sample(text, rnd, pres=lambda u: 0.2 + 0.6 * u, speed=lambda u: 0.5 + u, line=150.0, xh=30.0):
    """a sample as sample() makes it, its pen's pressure and speed given along each stroke's
    arc length (u from 0 to 1): pres None, points without pressure (x, y, t)"""
    s = sample(text, rnd, line=line, xh=xh)
    t = 0.0
    for st in s["strokes"]:
        d = [0.0]
        for a, b in zip(st, st[1:]):
            d.append(d[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        total = d[-1] or 1.0
        for i, pt in enumerate(st):
            if i:
                # dt = ds / v at the step's middle
                t += (d[i] - d[i - 1]) / (40.0 * speed((d[i] + d[i - 1]) / 2 / total))
            pt[2] = round(t, 5)
            if pres is None:
                del pt[3]
            else:
                pt[3] = round(pres(d[i] / total), 5)
        t += 0.2
    return s


def hw_inputs(ids, rev):
    """letters.c hw_letters_inputs: FNV-1a over the build revision and the prompted samples
    (none written or paired here, no preferences)"""
    h = 1469598103934665603

    def fnv(h, b):
        for x in b:
            h = ((h ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        return h
    h = fnv(h, b"letters 1 %d\n" % rev)
    for k in sorted("%s  " % i for i in ids):
        h = fnv(h, k.encode() + b"\0")
    h = fnv(h, b"\npreferences\n")
    return "%016x" % h


class Profiles(unittest.TestCase):
    """how each version was written (FR-006): averaged pressure and speed along its strokes,
    relative to the person's median"""
    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(21)
        cls.e.put([written_sample("".join(ALNUM[i:i + 5]), rnd) for _ in range(3) for i in range(0, 20, 5)])
        cls.e.run("letters", "build", "-r")
        cls.lines = [json.loads(x) for x in cls.e.model().splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def variants(self):
        return [v for d in self.lines if d.get("covered") for v in d["variants"]]

    def test_averaged_along_the_strokes(self):
        m = self.lines[1]["metrics"]
        self.assertAlmostEqual(m["pressure_median"], 0.5, delta=0.01)
        self.assertGreater(m["speed_median"], 0)
        vs = self.variants()
        self.assertTrue(vs)
        for v in vs:
            self.assertEqual(len(v["pressure"]), len(v["strokes"]))
            self.assertEqual(len(v["speed"]), len(v["strokes"]))
            for pr, sp in zip(v["pressure"], v["speed"]):
                self.assertEqual((len(pr), len(sp)), (K, K))
                for q in range(K):
                    # 0.2 + 0.6u over its median 0.5
                    self.assertAlmostEqual(pr[q], 0.4 + 1.2 * q / (K - 1), delta=0.03)
                # the speed 0.5 + u over its median: rising throughout (over a window of the
                # pen's points, so a little flatter than written on these 12-point strokes)
                self.assertTrue(all(b >= a - 0.01 for a, b in zip(sp, sp[1:])), sp)
                self.assertGreater(sp[K - 3] / sp[2], 1.6)
        # relative to the person's median: about 1 in the middle of the strokes
        mids = sorted(sp[K // 2] for v in vs for sp in v["speed"])
        self.assertAlmostEqual(mids[len(mids) // 2], 1.0, delta=0.15)

    def test_speed_alone_without_pressure(self):
        """a pen without pressure (points of three values) or with one pressure throughout
        gives a speed profile and none of pressure"""
        e = Env()
        try:
            rnd = random.Random(22)
            ss = [written_sample("".join(ALNUM[i:i + 5]), rnd, pres=None) for _ in range(3) for i in range(0, 10, 5)]
            ss += [written_sample("".join(ALNUM[i:i + 5]), rnd, pres=lambda u: 0.5) for _ in range(3) for i in range(10, 20, 5)]
            e.put(ss)
            e.run("letters", "build", "-r")
            lines = [json.loads(x) for x in e.model().splitlines()]
            self.assertEqual(lines[1]["metrics"]["pressure_median"], 0)
            vs = [v for d in lines if d.get("covered") for v in d["variants"]]
            self.assertTrue(vs)
            for v in vs:
                self.assertNotIn("pressure", v)
                self.assertEqual(len(v["speed"]), len(v["strokes"]))
        finally:
            e.close()

    def test_mixed_pens(self):
        """a version whose samples are partly without pressure averages pressure over those with it"""
        e = Env()
        try:
            rnd = random.Random(23)
            ss = [written_sample("abcde", rnd) for _ in range(2)] + [written_sample("abcde", rnd, pres=None) for _ in range(2)]
            e.put(ss)
            e.run("letters", "build", "-r")
            for d in (json.loads(x) for x in e.model().splitlines()):
                for v in d.get("variants", []):
                    for pr in v["pressure"]:
                        self.assertAlmostEqual(pr[K // 2], 0.4 + 1.2 * (K // 2) / (K - 1), delta=0.05)
        finally:
            e.close()

    def test_relearn_keeps_the_medians(self):
        """one character learned again is relative to the whole model's medians"""
        before = [x for x in self.e.model().splitlines() if x.startswith('{"char":"c"')]
        self.e.run("letters", "build", "-c", "c")
        after = [x for x in self.e.model().splitlines() if x.startswith('{"char":"c"')]
        self.assertEqual(before, after)

    def test_revision_outdates_an_older_model(self):
        """the build revision is part of the inputs: a model of the revision before is out of
        date and made again"""
        e = Env()
        try:
            rnd = random.Random(24)
            e.put([written_sample("abc", rnd) for _ in range(3)])
            e.run("letters", "build", "-r")
            ids = [ln.split("\t")[0] for ln in e.run("samples", "list").stdout.splitlines()]
            head = json.loads(e.model().splitlines()[0])
            self.assertEqual(head["inputs"], hw_inputs(ids, 19))
            old = dict(head, inputs=hw_inputs(ids, 18))
            text = e.model().split("\n", 1)
            (e.store / "letters" / "model").write_text(json.dumps(old, separators=(",", ":")) + "\n" + text[1])
            self.assertEqual(e.status()["state"], ["outdated"])
            self.assertTrue(e.run("letters", "build").stdout.startswith("built"))
            self.assertEqual(e.status()["state"], ["current"])
        finally:
            e.close()


def made_model(chars, ink=0.07):
    """a letter model written by hand: chars {c: [(points, pressure or None, speed or None)]},
    one variant each (x-heights)"""
    lines = [{"format": "glassine-handwriting-letters", "version": 1, "inputs": "0" * 16, "samples": 3, "built": 0},
             {"metrics": {"x_height": 1, "cap_height": 1.4, "ascender": 1.5, "descender": -0.5, "slant": 0,
                          "letter_spacing": 0.15, "word_spacing": 0.7, "ink_width": ink, "x_height_per_line": 0,
                          "base_below_guide": 0, "x_height_mm": 0, "pressure_median": 0.5, "speed_median": 4}},
             {"spacing": {"letter": 0.15, "word": 0.7, "open": 0, "open0": 0, "pairs": [], "sides": [], "reach": [],
                          "left_shapes": [], "right_shapes": []}}]
    for c in sorted(chars):
        sts = chars[c]
        xs = [p[0] for st in sts for p in st[0]]
        v = {"width": max(xs) - min(xs), "weight": 3, "members": 3, "use": 3, "lc": -1, "rc": -1, "ids": [],
             "strokes": [[[round(x - min(xs), 4), round(y, 4)] for x, y in st[0]] for st in sts]}
        if all(st[1] for st in sts):
            v["pressure"] = [st[1] for st in sts]
        if all(st[2] for st in sts):
            v["speed"] = [st[2] for st in sts]
        lines.append({"char": c, "samples": 3, "covered": True, "variants": [v], "slots": [0] * SLOTS})
    return "\n".join(json.dumps(x) for x in lines) + "\n"


def line_pts(angle, length=1.0, n=26, y0=0.5):
    a = math.radians(angle)
    return [(0.1 + length * i / (n - 1) * math.cos(a) + (length * math.cos(a) < 0) * length * -math.cos(a),
             y0 + length * i / (n - 1) * math.sin(a)) for i in range(n)]


def ramp(a, b):
    return [round(a + (b - a) * q / (K - 1), 3) for q in range(K)]


class StyleWidths(unittest.TestCase):
    """Calligraphy and Brush on strokes made up for it (docs/recognizer.md, the font's styles)"""
    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        (cls.e.store / "letters").mkdir(parents=True)
        cls.e.run("samples", "stats")
        (cls.e.store / "letters" / "model").write_text(made_model({
            "-": [(line_pts(0), None, None)], "/": [(line_pts(35), None, None)], "\\": [(line_pts(125), None, None)],
            "p": [(line_pts(0), ramp(0.5, 2.0), ramp(1, 1))], "s": [(line_pts(0), ramp(1, 1), ramp(0.5, 2.0))],
            "n": [(line_pts(0), None, ramp(1, 1))]}))

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def widths(self, style, nib=None):
        out = self.e.run("font", "widths", "--style", style, *(["--nib", str(nib)] if nib is not None else [])).stdout
        lines = [json.loads(x) for x in out.splitlines()]
        return lines[0], {d["char"]: d["widths"] for d in lines[1:]}

    def test_calligraphy_follows_direction(self):
        head, w = self.widths("calligraphy")
        lo, hi = head["nib_min"], head["nib_max"]
        self.assertEqual(head["nib"], 35)
        self.assertAlmostEqual(lo, 0.4 * 0.07, places=6)
        self.assertAlmostEqual(hi, 0.154, places=6)
        mid = len(w["-"]) // 2
        self.assertAlmostEqual(w["/"][mid], lo, places=3)           # along the nib: the thinnest
        self.assertAlmostEqual(w["\\"][mid], hi, places=3)          # across it: the widest
        self.assertAlmostEqual(w["-"][mid], lo + (hi - lo) * math.sin(math.radians(35)), places=4)
        _, w2 = self.widths("calligraphy", 0)
        self.assertAlmostEqual(w2["-"][mid], lo, places=4)           # a level nib: thin across

    def test_calligraphy_outlines(self):
        """the outline's thickness across each stroke is the width the rule gives"""
        from fontTools.ttLib import TTFont
        ttf = self.e.dir / "c.ttf"
        self.e.run("font", "build", "--style", "calligraphy", str(ttf))
        f = TTFont(str(ttf))
        head, w = self.widths("calligraphy")
        cmap, glyf = f.getBestCmap(), f["glyf"]
        for c, ang in (("-", 0), ("/", 35), ("\\", 125)):
            g = glyf[glyf[cmap[ord(c)]].components[0].glyphName]
            coords, _, flags = g.getCoordinates(glyf)
            nx, ny = -math.sin(math.radians(ang)), math.cos(math.radians(ang))
            on = [x * nx + y * ny for (x, y), fl in zip(coords, flags) if fl & 1]
            self.assertAlmostEqual((max(on) - min(on)) / 500, w[c][len(w[c]) // 2], delta=0.008, msg=c)

    def test_brush_follows_pressure_and_speed(self):
        _, w = self.widths("brush")
        n = len(w["p"])
        a, b = n * 3 // 10, n * 6 // 10            # inside the tapers
        self.assertGreater(w["p"][b], 1.2 * w["p"][a])              # pressed harder: wider
        self.assertLess(w["s"][b], 0.85 * w["s"][a])                 # faster: thinner
        self.assertAlmostEqual(w["n"][a], 1.3 * 0.07, delta=0.004)   # speed 1, no pressure
        for c in "psn":
            self.assertLess(w[c][0], 0.5 * w[c][a], c)              # the start tapers
            self.assertLess(w[c][-1], 0.3 * w[c][a], c)             # the end more
        _, r = self.widths("regular")
        self.assertEqual(set(r["p"]), {0.07})

    def test_brush_outlines(self):
        """wider in the outline where the stroke was pressed harder"""
        from fontTools.ttLib import TTFont
        ttf = self.e.dir / "b.ttf"
        self.e.run("font", "build", "--style", "brush", str(ttf))
        f = TTFont(str(ttf))
        glyf = f["glyf"]
        g = glyf[glyf[f.getBestCmap()[ord("p")]].components[0].glyphName]
        coords, _, flags = g.getCoordinates(glyf)
        x0 = min(x for x, _ in coords)

        def thick(u):
            ys = [y for (x, y), fl in zip(coords, flags) if fl & 1 and abs(x - x0 - 500 * u) < 25]
            return max(ys) - min(ys)
        self.assertGreater(thick(0.62), 1.2 * thick(0.32))


def signed_areas(f, gname):
    """each contour's signed area (on- and off-curve points as a polygon; y up)"""
    glyf = f["glyf"]
    coords, ends, _ = glyf[gname].getCoordinates(glyf)
    out, s = [], 0
    for e in ends:
        pts = coords[s:e + 1]
        out.append(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                       for i in range(len(pts))) / 2)
        s = e + 1
    return out


class Styles(unittest.TestCase):
    """one family in three styles: everything but the outlines is the same (FR-034)"""
    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(31)
        ss = [written_sample("".join(ALNUM[i:i + 5]), rnd) for _ in range(3) for i in range(0, len(ALNUM), 5)]
        ss += [written_sample("l", rnd) for _ in range(6)]
        ss += [spaced_sample(w, rnd) for w in ("hello world", "lol ok", "the cat") * 3]
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        cls.out = cls.e.run("font", "install").stdout.strip().split("\t")
        cls.status = cls.e.run("font", "status").stdout.splitlines()
        cls.files = {f[1]: Path(f[2]) for f in (ln.split("\t") for ln in cls.status[1:]) if f[0] == "style"}

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def test_installed_together(self):
        self.assertEqual(self.out[:3], ["installed", "Autograph", "1"])
        self.assertEqual(self.status[0].split("\t"), ["Autograph", "1", self.out[3]])
        self.assertEqual(set(self.files), set(STYLES))
        self.assertEqual(str(self.files["Regular"]), self.out[3])
        self.assertEqual(self.status[-1], "nib\t35")
        self.assertEqual(sorted(p.name for p in self.e.fonts.iterdir()),
                         ["handwriting-1-brush.ttf", "handwriting-1-calligraphy.ttf", "handwriting-1.ttf"])

    def test_valid_and_named(self):
        from fontTools.ttLib import TTFont
        ps = set()
        for st, path in self.files.items():
            f = TTFont(str(path), checkChecksums=2)
            for t in f.keys():
                if t != "GlyphOrder":
                    f[t].compile(f)
            n = f["name"]
            ps.add(n.getDebugName(6))
            if st == "Regular":
                self.assertEqual((n.getDebugName(1), n.getDebugName(2), n.getDebugName(16)), ("Autograph", "Regular", None))
            else:
                self.assertEqual((n.getDebugName(1), n.getDebugName(2), n.getDebugName(16), n.getDebugName(17)),
                                 ("Autograph " + st, "Regular", "Autograph", st))
            self.assertEqual(n.getDebugName(6), "Autograph-" + st)
            # every contour turns the same way, so overlapping pieces fill as one ink
            for g in f.getGlyphOrder():
                if f["glyf"][g].numberOfContours > 0:
                    self.assertTrue(all(a < 0 for a in signed_areas(f, g)), (st, g))
        self.assertEqual(len(ps), 3)

    def test_same_but_the_outlines(self):
        from fontTools.ttLib import TTFont
        fs = {st: TTFont(str(p)) for st, p in self.files.items()}
        r = fs["Regular"]
        for st in ("Calligraphy", "Brush"):
            f = fs[st]
            self.assertEqual(f.getGlyphOrder(), r.getGlyphOrder())
            for t in ("GPOS", "GSUB", "cmap"):
                self.assertEqual(f.getTableData(t), r.getTableData(t), (st, t))
            self.assertEqual({g: m[0] for g, m in f["hmtx"].metrics.items()}, {g: m[0] for g, m in r["hmtx"].metrics.items()})
            for a in ("ascent", "descent", "lineGap"):
                self.assertEqual(getattr(f["hhea"], a), getattr(r["hhea"], a))
            for a in ("sTypoAscender", "sTypoDescender", "sTypoLineGap"):
                self.assertEqual(getattr(f["OS/2"], a), getattr(r["OS/2"], a))
            self.assertNotEqual(f.getTableData("glyf"), r.getTableData("glyf"))

    def test_shaped_alike(self):
        text = "lllll hello world, the cat lol ok"
        out = {st: subprocess.run(["hb-shape", "--no-glyph-names", str(p), text], capture_output=True, text=True,
                                  check=True).stdout for st, p in self.files.items()}
        self.assertEqual(out["Calligraphy"], out["Regular"])
        self.assertEqual(out["Brush"], out["Regular"])
        # kerned: some advances aren't the glyphs' own
        from fontTools.ttLib import TTFont
        f = TTFont(str(self.files["Regular"]))
        order = f.getGlyphOrder()
        got = [tuple(int(v) for v in g.replace("=", " ").replace("+", " ").split()) for g in out["Regular"].strip()[1:-1].split("|")]
        self.assertTrue(any(adv != f["hmtx"][order[gid]][0] for gid, _, adv in got))
        self.assertGreater(len({gid for gid, _, _ in got[:5]}), 1)         # lllll: alternates

    def test_regular_ignores_how_it_was_written(self):
        """Regular's outlines are the pen's width alone: the profiles change nothing in it"""
        model = self.e.model()
        a = self.e.dir / "a.ttf"
        self.e.run("font", "build", str(a))
        lines = [json.loads(x) for x in model.splitlines()]
        for d in lines:
            for v in d.get("variants", []):
                v.pop("pressure", None)
                v.pop("speed", None)
        (self.e.store / "letters" / "model").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")
        try:
            b = self.e.dir / "b.ttf"
            self.e.run("font", "build", str(b))
            self.assertEqual(a.read_bytes(), b.read_bytes())
            c = self.e.dir / "c.ttf"
            self.e.run("font", "build", "--style", "brush", str(c))
            self.assertNotEqual(c.read_bytes(), self.files["Brush"].read_bytes())    # Brush does use them
        finally:
            (self.e.store / "letters" / "model").write_text(model)

    def test_fontconfig_finds_the_family(self):
        m = subprocess.run(["fc-match", "-f", "%{file}\n", "Autograph"], env=self.e.env, capture_output=True,
                           text=True, check=True).stdout.strip()
        self.assertEqual(m, str(self.files["Regular"]))
        for st in ("Calligraphy", "Brush"):
            m = subprocess.run(["fc-match", "-f", "%{file}\n", "Autograph:style=" + st], env=self.e.env,
                               capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(m, str(self.files[st]))

    def test_nib_update_remove(self):
        e = Env()
        try:
            e.put(alphabet_samples(seed=32, per=3))
            e.run("letters", "build", "-r")
            e.run("font", "install", "--nib", "50")
            st = e.run("font", "status").stdout.splitlines()
            self.assertEqual(st[-1], "nib\t50")
            self.assertEqual(e.status()["font-nib"], ["50"])
            old = [Path(ln.split("\t")[2]) for ln in st[1:] if ln.startswith("style")]
            c50 = old[1].read_bytes()
            # an update keeps the angle; the files are new and the old ones gone
            e.run("font", "install")
            st = e.run("font", "status").stdout.splitlines()
            self.assertEqual(st[-1], "nib\t50")
            self.assertTrue(all(not p.exists() for p in old))
            self.assertEqual(len(list(e.fonts.iterdir())), 3)
            e.run("font", "install", "--nib", "20")
            new = [Path(ln.split("\t")[2]) for ln in e.run("font", "status").stdout.splitlines()[1:] if ln.startswith("style")]
            self.assertNotEqual(new[1].read_bytes()[200:], c50[200:])
            for bad in ("91", "-1", "x"):
                self.assertEqual(e.run("font", "install", "--nib", bad, check=False).returncode, 2)
            self.assertEqual(e.run("font", "status", "--nib", "30", check=False).returncode, 2)
            e.run("font", "remove")
            self.assertTrue(all(not p.exists() for p in new))
            self.assertFalse(e.fonts.exists())
            self.assertEqual(e.run("font", "status").stdout.strip(), "none")
        finally:
            e.close()

    def test_font_from_before_the_styles(self):
        """a font installed before the styles (Regular alone) counts as changed; Update makes
        all three and takes the old file"""
        e = Env()
        try:
            e.put(alphabet_samples(seed=33, per=3))
            e.run("letters", "build", "-r")
            e.run("font", "install")
            self.assertEqual(e.status()["font-letters"], ["same"])
            rec = e.store / "letters" / "font"
            first = rec.read_text().splitlines()[0]
            for p in e.fonts.iterdir():
                if p.name != "handwriting-1.ttf":
                    p.unlink()
            rec.write_text(first + "\n")
            self.assertEqual(e.run("font", "status").stdout.splitlines(), [first, "style\tRegular\t" + first.split("\t")[2]])
            self.assertEqual(e.status()["font-letters"], ["changed"])
            self.assertNotIn("font-nib", e.status())
            e.run("font", "install")
            self.assertEqual(sorted(p.name for p in e.fonts.iterdir()),
                             ["handwriting-2-brush.ttf", "handwriting-2-calligraphy.ttf", "handwriting-2.ttf"])
            self.assertEqual(e.status()["font-letters"], ["same"])
            e.run("samples", "erase")
            self.assertFalse(e.fonts.exists())
        finally:
            e.close()


# ---------------------------------------------------------------- neighbours (FR-038 to FR-040)

def join_strokes(s, a, bridge=True):
    """the sample s with strokes a and a + 1 written as one: the first, the pen's way across
    (points as densely as along a stroke) and the second, in the spans of both"""
    s = json.loads(json.dumps(s))
    b = a + 1
    p, q = s["strokes"][a][-1], s["strokes"][b][0]
    d = math.hypot(*(p[i] - s["strokes"][a][-2][i] for i in (0, 1))) or 1
    n = int(math.hypot(q[0] - p[0], q[1] - p[1]) / d) if bridge else 0
    way = [[round(p[0] + (q[0] - p[0]) * i / (n + 1), 3), round(p[1] + (q[1] - p[1]) * i / (n + 1), 3),
            round(p[2] + (q[2] - p[2]) * i / (n + 1), 3), 0.5] for i in range(1, n + 1)]
    s["strokes"][a] = s["strokes"][a] + way + s["strokes"][b]
    del s["strokes"][b]
    s["spans"] = [sorted({a if j == b else j - (j > b) for j in sp}) for sp in s["spans"]]
    return s


def joined(s, at, bridge=True):
    """the sample s written with each pair of characters at k, k + 1 (k in at) joined: the
    first's last stroke and the second's first one as one stroke (join_strokes)"""
    for k in sorted(at, reverse=True):
        a, b = s["spans"][k][-1], s["spans"][k + 1][0]
        assert b == a + 1
        s = join_strokes(s, a, bridge)
    return s


def font_glyphs(model):
    """glyph id -> ("space", step), ("step", char, step), ("variant", char, variant) or
    ("joined", pair), as ttf.c orders the glyphs (the variants left out not in it)"""
    lines = [json.loads(x) for x in model.splitlines()]
    chars = sorted((d for d in lines if "char" in d and d["char"] != " " and d.get("covered")
                    and any(not v.get("left_out") for v in d["variants"])), key=lambda d: d["char"])
    out = {1 + s: ("space", s) for s in range(SLOTS)}
    for i, d in enumerate(chars):
        for s in range(SLOTS):
            out[1 + (i + 1) * SLOTS + s] = ("step", d["char"], s)
    g = 1 + (len(chars) + 1) * SLOTS
    for d in chars:
        for v in range(sum(not x.get("left_out") for x in d["variants"])):
            out[g] = ("variant", d["char"], v)
            g += 1
    have = {d["char"] for d in chars}
    for d in lines:
        if "joined" in d and d["joined"][0] in have and d["joined"][1] in have:
            out[g] = ("joined", d["joined"])
            g += 1
    return out


def hb(ttf, text, features=None):
    """hb-shape's glyphs: [{"g", "cl", "ax", "dx", ...}]"""
    args = ["hb-shape", "--no-glyph-names", "--output-format=json", str(ttf), text]
    if features is not None:
        args.insert(1, "--features=" + features)
    return json.loads(subprocess.run(args, capture_output=True, text=True, check=True).stdout)


def gsub_features(ttf):
    from fontTools.ttLib import TTFont
    return sorted({r.FeatureTag for r in TTFont(str(ttf))["GSUB"].table.FeatureList.FeatureRecord})


def valid_font(test, ttf):
    """fontTools reads every table with its checksums and compiles it again"""
    from fontTools.ttLib import TTFont
    f = TTFont(str(ttf), checkChecksums=2)
    for t in f.keys():
        if t != "GlyphOrder":
            f[t].compile(f)
    test.assertEqual(f.getBestCmap().get(32) is not None, True)
    return f


def text_ids(e):
    """sample id -> its text"""
    return {f[0]: f[5] for f in (ln.split("\t") for ln in e.run("samples", "list").stdout.splitlines())}


def set_mid(e, ttf, pair):
    """the distance hb-shape sets the pair's letters' middles at (x-heights): their origins'
    distance plus where each one's variant has its middle"""
    lines = [json.loads(x) for x in e.model().splitlines()]
    chars = {d["char"]: d for d in lines if "char" in d}
    gl = font_glyphs(e.model())
    got = hb(ttf, pair, "-liga")
    mids, x = [], 0
    for g in got:
        k = gl[g["g"]]
        d = chars[k[1]]
        vs = [v for v in d["variants"] if not v.get("left_out")]
        v = vs[d["slots"][k[2]] if k[0] == "step" else k[2]]
        mids.append((x + g["dx"]) / 500 + v["middle"])
        x += g["ax"]
    return mids[1] - mids[0]


class ModelText(unittest.TestCase):
    """spec 003 FR-006, FR-015: a store with sentences and pairs whose gaps are left out (beyond
    what handwriting has) builds a model of valid UTF-8 whose pairs are real characters, the
    same byte for byte when built again; a damaged entry in a model is left out with a
    warning, the rest loads"""

    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(61)
        ss = alphabet_samples(seed=61, per=3)
        for k in range(4):
            ss.append(spaced_sample("an en in on", rnd, gap=0.3))
            ss.append(spaced_sample("ox ax ex", rnd, gaps={"ox": 2.2, "ax": -0.9}, gap=0.3))
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        cls.raw = (cls.e.store / "letters" / "model").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def test_valid_and_real_characters(self):
        text = self.raw.decode("utf-8")                       # strict
        sp = next(d["spacing"] for d in map(json.loads, text.splitlines()) if "spacing" in d)
        self.assertTrue(sp["pairs"])
        for p in sp["pairs"]:
            for c in p[:2]:
                self.assertEqual(len(c), 1, p)
                self.assertIn(c, ALNUM, p)
        for r in sp["reach"]:
            self.assertIn(r[0], ALNUM, r)

    def test_same_when_built_again(self):
        for _ in range(3):
            self.e.run("letters", "build", "-r", "-f")
            self.assertEqual((self.e.store / "letters" / "model").read_bytes(), self.raw)

    def test_damaged_entries_left_out(self):
        other = Env()
        try:
            lines = self.raw.split(b"\n")
            k = next(i for i, ln in enumerate(lines) if ln.startswith(b'{"spacing"'))
            lines[k] = lines[k].replace(b'"pairs":[[', b'"pairs":[["\xfc\x83\x80\xb0\x80\x80","a",0.3,3,null],[', 1)
            c = next(i for i, ln in enumerate(lines) if ln.startswith(b'{"char":"b"'))
            lines[c] = lines[c].replace(b'{"char":"b"', b'{"char":"\xed\xa0\x80"', 1)
            lines.insert(c, b'{"char": not json')
            (other.store / "letters").mkdir(parents=True)
            (other.store / "letters" / "model").write_bytes(b"\n".join(lines))
            ttf = other.dir / "f.ttf"
            p = other.run("font", "build", str(ttf))
            self.assertIn("isn't valid, left out", p.stderr)
            self.assertNotIn("\ufffd", p.stderr)
            self.assertTrue(ttf.exists())
            st = subprocess.run([str(BIN), "letters", "show"], env=other.env, capture_output=True, timeout=60)
            self.assertEqual(st.returncode, 0)
            self.assertIn(b'"char":"a"', st.stdout)
        finally:
            other.close()


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class RunningWidths(unittest.TestCase):
    """spec 003 FR-006: letters written a known factor narrower in sentences than in single
    words make every variant that much narrower, pairs still at their distances in the font;
    too few characters seen both ways, or sentences written wider, narrow nothing"""
    BOTH = "abcdefghijkl"
    F = 0.9

    @classmethod
    def store(cls, f=None, chars=BOTH):
        e = Env()
        rnd = random.Random(91)
        ss = alphabet_samples(seed=91, per=3)
        if f:
            def narrowed(c, style, r):
                return [[(x * f, y) for x, y in st] for st in shape(c, style, r)]
            shapes = {c: narrowed for c in chars}
            for k in range(4):
                for i in range(0, len(chars), 3):
                    ss.append(sample(" ".join(chars[i:i + 3]) + " " + chars[i:i + 3], rnd, shapes=shapes))
        e.put(ss)
        e.run("letters", "build", "-r")
        ttf = e.dir / "f.ttf"
        e.run("font", "build", str(ttf))
        lines = [json.loads(x) for x in e.model().splitlines()]
        sp = next(d["spacing"] for d in lines if "spacing" in d)
        chars = {d["char"]: d for d in lines if "char" in d}
        return e, ttf, sp, chars

    @classmethod
    def setUpClass(cls):
        cls.base = cls.store()
        cls.full = cls.store(cls.F)
        cls.few = cls.store(cls.F, chars=cls.BOTH[:6])
        cls.wider = cls.store(1.15)

    @classmethod
    def tearDownClass(cls):
        for s in (cls.base, cls.full, cls.few, cls.wider):
            s[0].close()

    @staticmethod
    def widths(chars, c):
        return [v["width"] for v in chars[c]["variants"]]

    def test_narrower_by_the_ratio(self):
        sp, chars = self.full[2], self.full[3]
        self.assertAlmostEqual(sp["narrow"], self.F, delta=0.02)
        self.assertEqual(self.base[2]["narrow"], 1)
        # a character written only in single words: its variants are its versions' width times
        # the ratio (the same versions as in the store without sentences)
        for c in "mnoprsuvwxz":
            for w0, w1 in zip(self.widths(self.base[3], c), self.widths(chars, c)):
                self.assertAlmostEqual(w1 / w0, sp["narrow"], delta=0.02, msg=c)

    def test_pairs_at_their_distances(self):
        e, ttf, sp, chars = self.full
        n = 0
        for a, b, gap, k, d in sp["pairs"]:
            if d is None or a not in chars or b not in chars:
                continue
            self.assertAlmostEqual(set_mid(e, ttf, a + b), d, delta=0.02, msg=a + b)
            n += 1
        self.assertGreater(n, 5)

    def test_too_few_or_wider_narrow_nothing(self):
        self.assertEqual(self.few[2]["narrow"], 1)
        self.assertEqual(self.wider[2]["narrow"], 1)
        for c in "mnoz":
            self.assertEqual(self.widths(self.wider[3], c), self.widths(self.base[3], c), c)


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class RunningText(unittest.TestCase):
    """spec 003 FR-007: spacing follows running text: pairs written 4 times each in sentences
    closer than in single words are set as in the sentences; pairs written only in single
    words come closer by the person's ratio, learned from the pairs written both ways; single
    words written wider don't widen it, and sentences written wider than single words don't
    widen the pairs seen only in single words"""
    BOTH = ["an", "en", "in", "on", "ar", "er", "or", "as", "es", "is", "os", "us"]
    ONLY = ["lt", "ut"]
    WORD, RUN = 0.45, 0.35

    @classmethod
    def store(cls, word=WORD, run=RUN, sentences=True):
        e = Env()
        rnd = random.Random(81)
        ss = alphabet_samples(seed=81, per=3)
        for p in cls.BOTH + cls.ONLY:
            ss += [spaced_sample(p, rnd, gap=word if p in cls.BOTH else cls.WORD) for _ in range(4)]
        if sentences:
            for k in range(0, len(cls.BOTH), 4):
                ss += [spaced_sample(" ".join(cls.BOTH[k:k + 4]), rnd, gap=run) for _ in range(4)]
        e.put(ss)
        e.run("letters", "build", "-r")
        ttf = e.dir / "f.ttf"
        e.run("font", "build", str(ttf))
        lines = [json.loads(x) for x in e.model().splitlines()]
        sp = next(d["spacing"] for d in lines if "spacing" in d)
        return e, ttf, sp, {p[0] + p[1]: (p[2], p[4]) for p in sp["pairs"]}

    @classmethod
    def setUpClass(cls):
        cls.base = cls.store(sentences=False)
        cls.full = cls.store()
        cls.wider_words = cls.store(word=0.75)
        cls.wider_run = cls.store(run=0.60)
        # the letter model's x-height against the made-up letters' (gaps scale with it)
        cls.r = statistics.median(cls.base[3][p][0] for p in cls.BOTH) / cls.WORD

    @classmethod
    def tearDownClass(cls):
        for s in (cls.base, cls.full, cls.wider_words, cls.wider_run):
            s[0].close()

    def test_as_in_the_sentences(self):
        e, ttf, sp, pairs = self.full
        for p in self.BOTH:
            self.assertAlmostEqual(pairs[p][0], self.RUN * self.r, delta=0.02, msg=p)
            self.assertAlmostEqual(set_mid(e, ttf, p), pairs[p][1], delta=0.02, msg=p)

    def test_single_words_closer_by_the_ratio(self):
        e, ttf, sp, pairs = self.full
        base = self.base[3]
        want = statistics.median((base[p][1] - (self.WORD - self.RUN) * self.r) / base[p][1] for p in self.BOTH)
        self.assertLess(sp["run"], 1)
        self.assertAlmostEqual(sp["run"], want, delta=0.02)
        self.assertEqual(self.base[2]["run"], 1)                # no running text: no scaling
        for p in self.ONLY:
            self.assertAlmostEqual(pairs[p][1], sp["run"] * base[p][1], delta=0.02, msg=p)
            self.assertAlmostEqual(set_mid(e, ttf, p), sp["run"] * base[p][1], delta=0.02, msg=p)
            self.assertLess(pairs[p][0], base[p][0] - 0.05, p)

    def test_wider_single_words_dont_widen(self):
        full, wider = self.full[3], self.wider_words[3]
        for p in self.BOTH:
            self.assertAlmostEqual(wider[p][0], full[p][0], delta=0.02, msg=p)
            self.assertAlmostEqual(wider[p][1], full[p][1], delta=0.02, msg=p)

    def test_wider_sentences_dont_widen_single_words(self):
        e, ttf, sp, pairs = self.wider_run
        self.assertEqual(sp["run"], 1)
        for p in self.ONLY:
            self.assertAlmostEqual(pairs[p][1], self.base[3][p][1], delta=0.02, msg=p)
        for p in self.BOTH:                                     # the sentences' own, as written
            self.assertAlmostEqual(pairs[p][0], 0.60 * self.r, delta=0.02, msg=p)


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class NeighbourRules(unittest.TestCase):
    """spec 003 FR-038: an o written its second way after an l (8 times) gets a rule: after
    an l the font shows that way, elsewhere the cycle; an e written its own way after a k only
    5 times, a u after an m 5 times one way and 3 the other, and an a after a q 6 times in a
    single sample (no rule left when that sample is held back) get none"""
    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(41)
        ss = alphabet_samples(seed=41, per=3)
        ss += [sample("lo", rnd, styles={"o": 1}) for _ in range(8)]
        ss += [sample("zxo", rnd) for _ in range(8)]
        ss += [sample("ke", rnd, styles={"e": 1}) for _ in range(5)]
        ss += [sample("mu", rnd, styles={"u": int(k < 5)}) for k in range(8)]
        ss.append(sample("qa" * 6, rnd, styles={"a": 1}))
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        cls.model = cls.e.model()
        cls.chars = {d["char"]: d for d in map(json.loads, cls.model.splitlines()) if "char" in d}
        cls.gl = font_glyphs(cls.model)
        cls.ttf = {}
        for st in ("Regular", "Calligraphy", "Brush"):
            cls.ttf[st] = cls.e.dir / ("rules-%s.ttf" % st)
            cls.e.run("font", "build", "-n", "Rules", "--style", st, str(cls.ttf[st]))

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def way(self, c, texts):
        """the variant of c whose samples are those with one of texts"""
        ids = text_ids(self.e)
        for k, v in enumerate(self.chars[c]["variants"]):
            if v["ids"] and all(ids[i] in texts for i in v["ids"]):
                return k
        self.fail("no variant of %s from %s" % (c, texts))

    def test_rule_learned(self):
        o = self.chars["o"]
        lo = self.way("o", ("lo",))
        rules = {(("prev", r["prev"]) if "prev" in r else ("next", r["next"])): r for r in o.get("rules", [])}
        self.assertIn(("prev", "l"), rules)
        r = rules[("prev", "l")]
        self.assertEqual((r["variant"], r["n"], r["same"]), (lo, 8, 8))
        self.assertGreater(r["hits"], r["cycle"])
        # the character page keeps the cycle's share: the rule's way is in it as before
        self.assertGreater(o["slots"].count(lo), 0)
        self.assertLess(o["slots"].count(lo), SLOTS)

    def test_no_rule_without_support(self):
        def has(c, nb):
            return any(r.get("prev") == nb for r in self.chars[c].get("rules", []))
        self.assertGreaterEqual(len(self.chars["e"]["variants"]), 2)
        self.assertFalse(has("e", "k"))         # 5 times: too few
        self.assertFalse(has("u", "m"))         # 5 of 8 one way: too mixed
        self.assertFalse(has("a", "q"))         # all in one sample: loses held back
        # a way written nearly everywhere is no way of writing it beside one neighbour: an l
        # before an o, an x after a z, all in their most common way, get none
        self.assertFalse(any(r.get("next") == "o" for r in self.chars["l"].get("rules", [])))
        self.assertFalse(any(r.get("prev") == "z" for r in self.chars["x"].get("rules", [])))
        # each of them has the way it would rule
        for c, texts in (("e", ("ke",)), ("a", ("qa" * 6,))):
            self.way(c, texts)

    def shown(self, ttf, text):
        return [self.gl[g["g"]] for g in hb(ttf, text)]

    def test_shaped_after_the_neighbour(self):
        lo = self.way("o", ("lo",))
        cov = sorted(c for c, d in self.chars.items() if d.get("covered") and d.get("variants"))
        for st, ttf in self.ttf.items():
            # after an l: its rule's way, as an o of its own glyph; the l and the rest keep the cycle
            text = "lo ao lolo"
            got, prev = self.shown(ttf, text), None
            for k, (c, g) in enumerate(zip(text, got)):
                prev = step(prev)
                if c == "o" and k and text[k - 1] == "l":
                    self.assertEqual(g, ("variant", "o", lo), (st, k))
                else:
                    # the cycle: the step it has without the rule, which counts on through it
                    self.assertEqual(g, ("space", prev) if c == " " else ("step", c, prev), (st, k))
            # before its neighbour: an m before a u (the m's first way, 8 of 8) is that way
            # wherever the cycle is, the u after it its step
            mu = next(r["variant"] for r in self.chars["m"]["rules"] if r.get("next") == "u")
            for text in ("mu", "a mu", "aa mu"):
                got = self.shown(ttf, text)
                self.assertEqual(got[-2], ("variant", "m", mu), (st, text))
                self.assertEqual(got[-1], ("step", "u", len(text) - 1), (st, text))
            self.assertEqual(self.shown(ttf, "ma")[0], ("step", "m", 0))
            # the same text, the same glyphs
            self.assertEqual(hb(ttf, text), hb(ttf, text))
        self.assertIn("o", cov)

    def test_valid(self):
        for ttf in self.ttf.values():
            f = valid_font(self, ttf)
            self.assertIn("calt", gsub_features(ttf))
            self.assertEqual(len(f.getGlyphOrder()), len(self.gl) + 1)


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class JoinedPairs(unittest.TestCase):
    """spec 003 FR-039: t and h written joined in 5 samples of 6 are one glyph in each style,
    a ligature shaped with no feature asked for, set at its letters' gaps, its neighbours at
    the steps they have without it, mapping back to its two characters; a and b joined 3
    times and c and d joined in half of their samples stay two glyphs"""
    GAPS = {"ot": 0.4, "ho": 0.25}

    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(51)
        ss = alphabet_samples(seed=51, per=3)
        ss += [joined(sample("th", rnd), [0]) for _ in range(5)] + [sample("th", rnd)]
        ss += [joined(sample("ab", rnd), [0]) for _ in range(3)]
        ss += [joined(sample("cd", rnd), [0]) for _ in range(4)] + [sample("cd", rnd) for _ in range(4)]
        ss += [spaced_sample("ot ho", rnd, cls.GAPS) for _ in range(4)]
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        cls.model = cls.e.model()
        lines = [json.loads(x) for x in cls.model.splitlines()]
        cls.chars = {d["char"]: d for d in lines if "char" in d}
        cls.pairs = {d["joined"]: d for d in lines if "joined" in d}
        cls.sp = next(d["spacing"] for d in lines if "spacing" in d)
        cls.gl = font_glyphs(cls.model)
        cls.ttf = {}
        for st in ("Regular", "Calligraphy", "Brush"):
            cls.ttf[st] = cls.e.dir / ("joined-%s.ttf" % st)
            cls.e.run("font", "build", "-n", "Joined", "--style", st, str(cls.ttf[st]))

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def test_learned(self):
        self.assertEqual(sorted(self.pairs), ["th"])
        th = self.pairs["th"]
        self.assertEqual((th["samples"], th["written"], th["of"]), (5, 5, 6))
        v = th["variant"]
        # one stroke more than t's and h's own less the one they share, on the line
        self.assertEqual(len(v["strokes"]), len(shape("t", 0, random.Random(0))) + len(shape("h", 0, random.Random(0))) - 1)
        self.assertAlmostEqual(min(p[1] for st in v["strokes"] for p in st), 0, delta=0.1)
        self.assertEqual(len(v["ids"]), 5)

    def test_one_glyph_in_each_style(self):
        lig = next(g for g, x in self.gl.items() if x == ("joined", "th"))
        for st, ttf in self.ttf.items():
            f = valid_font(self, ttf)
            self.assertEqual(gsub_features(ttf), ["calt", "liga"])
            self.assertGreater(f["glyf"][f.getGlyphOrder()[lig]].numberOfContours, 0, st)
            got = hb(ttf, "th")
            self.assertEqual([g["g"] for g in got], [lig], st)
            # it reads as its two characters: one cluster for both
            got = hb(ttf, "otho")
            self.assertEqual([self.gl[g["g"]][0] for g in got], ["step", "joined", "step"])
            self.assertEqual([g["cl"] for g in got], [0, 1, 3])
            # turned off, two letters again
            self.assertEqual(len(hb(ttf, "th", "-liga")), 2)

    def test_too_rare_stays_two(self):
        for ttf in self.ttf.values():
            for text in ("ab", "cd"):
                self.assertEqual([self.gl[g["g"]][0] for g in hb(ttf, text)], ["step", "step"], text)

    def test_neighbours_unchanged(self):
        """the letters around it show what they show without it: the cycle goes on through it"""
        text = "otho abc th"
        for ttf in self.ttf.values():
            got = [self.gl[g["g"]] for g in hb(ttf, text)]
            want, prev = [], None
            for k, c in enumerate(text):
                prev = step(prev)
                if text[k:k + 2] == "th":
                    want.append(("joined", "th"))
                elif k and text[k - 1:k + 1] == "th":
                    continue
                else:
                    want.append(("space", prev) if c == " " else ("step", c, prev))
            self.assertEqual(got, want)

    def prof(self, v):
        nan = float("nan")
        return ([nan if x is None else x for x in v["left"]], [nan if x is None else x for x in v["right"]])

    def test_spaced_at_its_letters_gaps(self):
        pairs = {p[0] + p[1]: p[2] for p in self.sp["pairs"]}
        self.assertIn("ot", pairs)
        self.assertIn("ho", pairs)
        for ttf in self.ttf.values():
            got = hb(ttf, "otho")
            x, pos = 0, []
            for g in got:
                pos.append((x + g["dx"]) / 500)
                x += g["ax"]
            kinds = [self.gl[g["g"]] for g in got]
            o1 = self.chars["o"]["variants"][self.chars["o"]["slots"][kinds[0][2]]]
            o2 = self.chars["o"]["variants"][self.chars["o"]["slots"][kinds[2][2]]]
            th = self.pairs["th"]["variant"]
            self.assertAlmostEqual(profile_gap(self.prof(o1), self.prof(th), pos[1] - pos[0]), pairs["ot"], delta=0.02)
            self.assertAlmostEqual(profile_gap(self.prof(th), self.prof(o2), pos[2] - pos[1]), pairs["ho"], delta=0.02)


def dotted_i(c, style, rnd):
    """an i as print writes it: a stem down from the x-height, then a dot above it"""
    stem = [(0.3 + rnd.gauss(0, 0.01), 1.0 - i / 11 + rnd.gauss(0, 0.01)) for i in range(12)]
    dot = [(0.32 + 0.03 * math.cos(a) + rnd.gauss(0, 0.005), 1.4 + 0.03 * math.sin(a)) for a in (0, 2, 4, 6)]
    return [stem, dot]


def shared(s, k, j):
    """the sample s with stroke j in character k's span as well (an alignment that gives a
    neighbour's stroke to both)"""
    s = json.loads(json.dumps(s))
    s["spans"][k] = sorted(s["spans"][k] + [j])
    return s


def tail(s, j, dx, xh=30.0):
    """the sample s with stroke j run on dx x-heights to the right, level, from its end (a
    descender's tail under the next letter)"""
    s = json.loads(json.dumps(s))
    p = s["strokes"][j][-1]
    s["strokes"][j] += [[round(p[0] + dx * xh * i / 12, 3), p[1], round(p[2] + 0.01 * i, 3), 0.5] for i in range(1, 13)]
    return s


def late(s, j):
    """the sample s with stroke j written last (a dot or a bar set after the word)"""
    s = json.loads(json.dumps(s))
    n = len(s["strokes"])
    order = [x for x in range(n) if x != j] + [j]
    t = s["strokes"][order[-2]][-1][2]
    st = s["strokes"][j]
    st = [[p[0], p[1], round(t + 0.1 + p[2] - st[0][2], 3), p[3]] for p in st]
    s["strokes"] = [s["strokes"][x] for x in order[:-1]] + [st]
    new = {x: i for i, x in enumerate(order)}
    s["spans"] = [sorted(new[x] for x in sp) for sp in s["spans"]]
    return s


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class JoinedPlainly(unittest.TestCase):
    """spec 003 FR-039, a joint only where the ink runs on: print letters whose alignment
    gives a stroke to both (the i's stem to the p before it, the g's tail run on under the r
    to the r after it) are no joined pair however often; an i joined to the n after it, its dot set after
    the word, is one glyph with the dot; a letter joined to a full stop or a digit never is"""
    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(57)
        sh = {"i": dotted_i}
        ss = alphabet_samples(seed=57, per=4, shapes=sh)
        for _ in range(8):
            s = sample("pi", rnd, shapes=sh)
            ss.append(shared(s, 0, s["spans"][1][0]))
            s = sample("gr", rnd)
            ss.append(shared(tail(s, s["spans"][0][-1], 1.2), 1, s["spans"][0][-1]))
            ss.append(joined(sample("y.", rnd), [0]))
            ss.append(joined(sample("a1", rnd), [0]))
        for _ in range(6):
            s = sample("in", rnd, shapes=sh)
            s = late(s, s["spans"][0][1])           # stem, the n, the dot
            ss.append(join_strokes(s, 0))
        cls.ss = ss
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        cls.lines = [json.loads(x) for x in cls.e.model().splitlines()]
        cls.pairs = {d["joined"]: d for d in cls.lines if "joined" in d}

    @classmethod
    def tearDownClass(cls):
        cls.e.close()


    def test_only_the_real_joint(self):
        self.assertEqual(sorted(self.pairs), ["in"])

    def test_alignment_shares_are_no_joint(self):
        """the shared strokes are there as written: 8 times each, all usable otherwise"""
        n = sum(1 for s in self.ss if s["text"] in ("pi", "gr") and set(s["spans"][0]) & set(s["spans"][1]))
        self.assertEqual(n, 16)
        self.assertNotIn("pi", sorted(self.pairs))
        self.assertNotIn("gr", sorted(self.pairs))

    def test_no_marks_or_digits(self):
        self.assertEqual([p for p in self.pairs if not all(c.isalpha() for c in p)], [])

    def test_late_dot_in_the_glyph(self):
        v = self.pairs["in"]["variant"]
        n = len(shape("n", 0, random.Random(0)))
        self.assertEqual(len(v["strokes"]), 2 + n - 1)          # the joined stroke, the dot, the n's rest
        dot = v["strokes"][-1]
        self.assertGreater(min(p[1] for p in dot), 1.2)
        self.assertLess(max(p[0] for p in dot) - min(p[0] for p in dot), 0.15)
        # it is a ligature in the font like any other
        ttf = self.e.dir / "plain.ttf"
        self.e.run("font", "build", str(ttf))
        gl = font_glyphs(self.e.model())
        self.assertEqual([gl[g["g"]] for g in hb(ttf, "in")], [("joined", "in")])
        f = valid_font(self, ttf)
        lig = next(g for g, x in gl.items() if x == ("joined", "in"))
        self.assertGreaterEqual(f["glyf"][f.getGlyphOrder()[lig]].numberOfContours, 2)


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class Joins(unittest.TestCase):
    """spec 003 FR-044: e and r, and r and s, written joined in 10 samples of 16 (too few of
    them for one glyph, FR-039; an r between them entering one join and leaving the other) are
    joined in the font in each
    style: their glyphs become the join's alternates (calt), the halves meet, nothing kerns
    between them; a ligature has no joins; the same store gives the same font; a store with no
    joined ink has no joins at all"""

    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(61)
        ss = alphabet_samples(seed=61, per=3)
        ss += [joined(sample("er", rnd), [0]) for _ in range(5)] + [sample("er", rnd) for _ in range(3)]
        ss += [joined(sample("ers", rnd), [0, 1]) for _ in range(5)] + [sample("ers", rnd) for _ in range(3)]
        ss += [joined(sample("rs", rnd), [0]) for _ in range(5)] + [sample("rs", rnd) for _ in range(3)]
        ss += [joined(sample("th", rnd), [0]) for _ in range(5)] + [sample("th", rnd)]
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        cls.model = cls.e.model()
        cls.lines = [json.loads(x) for x in cls.model.splitlines()]
        cls.joins = next((d["joins"] for d in cls.lines if "joins" in d), None)
        cls.gl = font_glyphs(cls.model)
        cls.ttf = {}
        for st in ("Regular", "Calligraphy", "Brush"):
            cls.ttf[st] = cls.e.dir / ("joins-%s.ttf" % st)
            cls.e.run("font", "build", "-n", "Joins", "--style", st, str(cls.ttf[st]))

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def test_learned(self):
        self.assertIsNotNone(self.joins)
        pairs = {a + b: (s, j) for a, b, s, j in self.joins["pairs"]}
        # (r and s are neighbours in the separate letters' samples too)
        self.assertEqual(pairs["er"], (16, 10))
        self.assertEqual(pairs["rs"][1], 10)
        er = [x for x in self.joins["shapes"] if x.get("pair") == "er"]
        self.assertEqual(len(er), 1)
        self.assertEqual(len(er[0]["points"]), 13)
        self.assertEqual(er[0]["points"][0], [0, 0])
        self.assertGreaterEqual(er[0]["n"], 5)

    def halves(self, ttf, text, features=None):
        """the shaped glyphs and, for each two that are a join's, how far apart their halves'
        outlines are (font units)"""
        from fontTools.ttLib import TTFont
        from fontTools.pens.recordingPen import DecomposingRecordingPen
        f = TTFont(str(ttf))
        gs, order, glyf = f.getGlyphSet(), f.getGlyphOrder(), f["glyf"]
        got = hb(ttf, text, features)

        def pts(name, dx):
            p = DecomposingRecordingPen(gs)
            gs[name].draw(p)
            return [(x + dx, y) for op, a in p.value for (x, y) in a]
        gaps, x = [], 0
        for i in range(len(got) - 1):
            a, b = glyf[order[got[i]["g"]]], glyf[order[got[i + 1]["g"]]]
            xa, xb = x + got[i]["dx"], x + got[i]["ax"] + got[i + 1]["dx"]
            x += got[i]["ax"]
            if not (a.isComposite() and len(a.components) >= 2 and b.isComposite() and b.components[0].x):
                continue
            pa = pts(a.components[1].glyphName, xa + a.components[1].x)
            pb = pts(b.components[-1].glyphName, xb + b.components[-1].x)
            gaps.append(min(math.hypot(p[0] - q[0], p[1] - q[1]) for p in pa for q in pb))
        return got, gaps

    def test_joined_in_each_style(self):
        for st, ttf in self.ttf.items():
            f = valid_font(self, ttf)
            self.assertEqual(gsub_features(ttf), ["calt", "liga"])
            self.assertGreater(len(f.getGlyphOrder()), len(self.gl) + 1, st)
            got, gaps = self.halves(ttf, "er")
            # the join's alternates, beyond the glyphs without joins
            self.assertTrue(all(g["g"] > max(self.gl) for g in got), st)
            # the halves meet: their outlines touch (within a unit's rounding each way)
            self.assertEqual(len(gaps), 1, st)
            self.assertLess(gaps[0], 2.0, st)
            # a letter in two joins: entered by one, leaving by the other, both meeting
            got, gaps = self.halves(ttf, "ers")
            self.assertTrue(all(g["g"] > max(self.gl) for g in got), st)
            mid = f["glyf"][f.getGlyphOrder()[got[1]["g"]]]
            self.assertEqual(len(mid.components), 3, st)
            self.assertEqual(len(gaps), 2, st)
            self.assertLess(max(gaps), 2.0, st)
            # without calt the letters stand apart as before
            got = hb(ttf, "er", "-calt")
            self.assertEqual([self.gl[g["g"]][0] for g in got], ["step", "step"], st)

    def test_no_kerning_inside_a_join(self):
        from fontTools.ttLib import TTFont
        ttf = self.ttf["Regular"]
        f = TTFont(str(ttf))
        got = hb(ttf, "er")
        hm = f["hmtx"]
        # the first glyph's advance is its own: where its half ends
        self.assertEqual(got[0]["ax"], hm[f.getGlyphOrder()[got[0]["g"]]][0])

    def test_ligature_has_no_joins(self):
        ttf = self.ttf["Regular"]
        got = hb(ttf, "ther")
        kinds = [self.gl.get(g["g"], ("join",))[0] for g in got]
        self.assertEqual(kinds[0], "joined")
        # e after the ligature is not entered from it; e and r still join
        self.assertEqual(kinds[1:], ["join", "join"])
        _, gaps = self.halves(ttf, "ther")
        self.assertEqual(len(gaps), 1)

    def test_same_font_again(self):
        again = self.e.dir / "joins-again.ttf"
        self.e.run("font", "build", "-n", "Joins", str(again))
        self.assertEqual(again.read_bytes(), self.ttf["Regular"].read_bytes())

    def test_no_joined_ink_no_joins(self):
        e = Env()
        try:
            rnd = random.Random(62)
            e.put(alphabet_samples(seed=62, per=3) + [sample("er", rnd) for _ in range(7)])
            e.run("letters", "build", "-r")
            self.assertFalse(any('"joins"' in x for x in e.model().splitlines()))
            ttf = e.dir / "nojoins.ttf"
            e.run("font", "build", str(ttf))
            from fontTools.ttLib import TTFont
            f = TTFont(str(ttf))
            self.assertEqual(len(f.getGlyphOrder()), len(font_glyphs(e.model())) + 1)
            self.assertEqual(f["maxp"].maxComponentElements, 1)
        finally:
            e.close()


# letters as a person writes them, in x-heights: the main body first, then its marks (a
# dot, a bar, a bowl); each stroke dense, as a pen gives it
WHOLE = {
    "i": [[(0.2, 1.0), (0.2, 0.0)], [(0.25, 1.45), (0.27, 1.47)]],
    "j": [[(0.35, 1.0), (0.35, -0.5), (0.05, -0.6)], [(0.4, 1.45), (0.42, 1.47)]],
    "t": [[(0.3, 1.5), (0.3, 0.0), (0.45, 0.05)], [(0.05, 1.0), (0.6, 1.0)]],
    "f": [[(0.65, 1.4), (0.4, 1.5), (0.3, 1.2), (0.3, 0.0)], [(0.05, 0.95), (0.6, 0.95)]],
    "F": [[(0.0, 1.5), (0.0, 0.0)], [(0.0, 1.5), (0.6, 1.5)], [(0.0, 0.75), (0.45, 0.75)]],
    "E": [[(0.0, 1.5), (0.0, 0.0)], [(0.0, 1.5), (0.6, 1.5)], [(0.0, 0.75), (0.45, 0.75)], [(0.0, 0.0), (0.6, 0.0)]],
    "T": [[(0.3, 1.5), (0.3, 0.0)], [(0.0, 1.5), (0.6, 1.5)]],
    "p": [[(0.1, 1.0), (0.1, -0.6)], [(0.1, 0.95), (0.45, 0.95), (0.6, 0.6), (0.45, 0.3), (0.1, 0.3)]],
    "b": [[(0.1, 1.5), (0.1, 0.0)], [(0.1, 0.65), (0.45, 0.7), (0.6, 0.35), (0.45, 0.0), (0.1, 0.05)]],
    "d": [[(0.5, 1.5), (0.5, 0.0)], [(0.5, 0.65), (0.15, 0.7), (0.0, 0.35), (0.15, 0.0), (0.5, 0.05)]],
}


def dense(st, rnd, k=6):
    out = [st[0]]
    for a, b in zip(st, st[1:]):
        out += [(a[0] + (b[0] - a[0]) * q / k, a[1] + (b[1] - a[1]) * q / k) for q in range(1, k + 1)]
    return [(x + rnd.gauss(0, 0.008), y + rnd.gauss(0, 0.008)) for x, y in out]


def whole(c, style, rnd):
    return [dense(st, rnd) for st in WHOLE[c]]


WHOLE_SHAPES = {c: whole for c in WHOLE}


def run_on(c, nxt, rnd, part):
    """a prompted sample of c and nxt written with c's stroke part (its last, a mark) run on
    into nxt: that stroke and nxt's first one a single stroke, as an f's bar is written into
    the next letter; the spans give it to nxt alone, as the alignment does"""
    s = sample(c + nxt, rnd, shapes=WHOLE_SHAPES)
    a, b = s["spans"][0][part], s["spans"][1][0]
    st = s["strokes"]
    st[b] = st[a] + st[b]
    del st[a]
    s["spans"] = [[j - (j > a) for j in s["spans"][0] if j != a], [j - (j > a) for j in s["spans"][1]]]
    retime_sample(s)
    return s


def joined_on(c, nxt, rnd):
    """c and nxt written with c's main body (its first stroke) joined into nxt's first stroke,
    c's marks after them"""
    s = sample(c + nxt, rnd, shapes=WHOLE_SHAPES)
    st, sp = s["strokes"], s["spans"]
    body, marks, rest = st[sp[0][0]], [st[j] for j in sp[0][1:]], [st[j] for j in sp[1]]
    s["strokes"] = [body + rest[0]] + rest[1:] + marks
    s["spans"] = [[0] + list(range(len(rest), len(rest) + len(marks))), list(range(len(rest)))]
    retime_sample(s)
    return s


def retime_sample(s):
    t = 0.0
    for st in s["strokes"]:
        for p in st:
            p[2] = round(t, 3)
            t += 0.01


class WholeLetters(unittest.TestCase):
    """spec 003 FR-007, FR-044: a mark (an f's or a t's bar, a p's, b's or d's bowl) written on
    into the next letter is a part of the letter the other's ink has: no variant is made
    without it; joins leave a letter's main body and enter the next one's, never a dot, a
    bar or another mark"""

    def test_a_mark_run_on_makes_no_variant_without_it(self):
        e = Env()
        try:
            rnd = random.Random(81)
            ss = alphabet_samples(seed=81, per=4, shapes=WHOLE_SHAPES)
            for c in "ftpbd":
                ss += [run_on(c, "o", rnd, 1) for _ in range(6)]
            e.put(ss)
            e.run("letters", "build", "-r")
            m = {d["char"]: d for d in map(json.loads, e.model().splitlines()) if "char" in d}
            for c in "ftpbd":
                with self.subTest(c=c):
                    self.assertEqual({len(v["strokes"]) for v in m[c]["variants"]}, {2})
                    # (the run-on samples count for neither letter)
                    self.assertEqual(sum(v["use"] for v in m[c]["variants"]), 4)
        finally:
            e.close()

    @unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
    def test_joins_leave_the_main_body(self):
        e = Env()
        try:
            rnd = random.Random(82)
            ss = alphabet_samples(seed=82, per=4, shapes=WHOLE_SHAPES)
            for c in "ijtfFET":
                ss += [joined_on(c, "n", rnd) for _ in range(5)] + [sample(c + "n", rnd, shapes=WHOLE_SHAPES) for _ in range(3)]
            e.put(ss)
            e.run("letters", "build", "-r")
            pairs = {a + b for a, b, _, _ in next(d["joins"] for d in map(json.loads, e.model().splitlines()) if "joins" in d)["pairs"]}
            ttf = e.dir / "body.ttf"
            e.run("font", "build", "-n", "Body", str(ttf))
            from fontTools.ttLib import TTFont
            from fontTools.pens.recordingPen import DecomposingRecordingPen
            f = TTFont(str(ttf))
            gs, order, glyf = f.getGlyphSet(), f.getGlyphOrder(), f["glyf"]
            for c in "ijtfFET":
                with self.subTest(c=c):
                    self.assertIn(c + "n", pairs)
                    a = glyf[order[hb(ttf, c + "n")[0]["g"]]]
                    self.assertTrue(a.isComposite() and len(a.components) >= 2)
                    pen = DecomposingRecordingPen(gs)
                    gs[a.components[1].glyphName].draw(pen)
                    ys = [y + a.components[1].y for op, q in pen.value for (x, y) in q]
                    # the exit's half starts at the body's end (the baseline or below it),
                    # nowhere near the marks (at three quarters of the x-height and above)
                    self.assertLess(min(ys), 0.3 * 500)
        finally:
            e.close()


@unittest.skipIf(SKIP_TOOLS, "HW_LETTERS_NO_FONT_TOOLS=1")
class RandomAlternates(unittest.TestCase):
    """spec 003 FR-040: without asking, no random alternates (no 'rand', the same shaping with
    it requested); built with them, 'rand' picks each letter's variant along a long text in
    about the cycle's proportions, the same each time, the rules and joined pairs still on"""
    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        rnd = random.Random(61)
        ss = alphabet_samples(seed=61, per=3)
        ss += [sample("l", rnd, styles={"l": k % 3}) for k in range(9)]
        ss += [sample("lo", rnd, styles={"o": 1}) for _ in range(8)]
        ss += [sample("zxo", rnd) for _ in range(8)]
        ss += [joined(sample("th", rnd), [0]) for _ in range(5)]
        cls.e.put(ss)
        cls.e.run("letters", "build", "-r")
        cls.model = cls.e.model()
        cls.chars = {d["char"]: d for d in map(json.loads, cls.model.splitlines()) if "char" in d}
        cls.gl = font_glyphs(cls.model)
        cls.plain = cls.e.dir / "plain.ttf"
        cls.e.run("font", "build", str(cls.plain))
        cls.rand = {}
        for st in ("Regular", "Calligraphy", "Brush"):
            cls.rand[st] = cls.e.dir / ("rand-%s.ttf" % st)
            cls.e.run("font", "build", "--random", "--style", st, str(cls.rand[st]))

    @classmethod
    def tearDownClass(cls):
        cls.e.close()

    def test_off_by_default(self):
        self.assertNotIn("rand", gsub_features(self.plain))
        text = "lllll hello lol " * 8
        self.assertEqual(hb(self.plain, text), hb(self.plain, text, "rand"))

    def test_random_in_proportion(self):
        lo = next(k for k, r in enumerate(self.chars["o"].get("rules", [])) if r.get("prev") == "l")
        lo = self.chars["o"]["rules"][lo]["variant"]
        text = " ".join(["hello world"] * 60) + " lo th"
        for st, ttf in self.rand.items():
            f = valid_font(self, ttf)
            self.assertEqual(gsub_features(ttf), ["calt", "liga", "rand"])
            a = hb(ttf, text)
            self.assertEqual(a, hb(ttf, text), st)
            got = [self.gl[g["g"]] for g in a]
            ls = [g[2] for g in got if g[:2] == ("variant", "l")]
            self.assertEqual(len(ls), text.count("l"))              # each l a variant of its own
            slots = self.chars["l"]["slots"]
            for v in set(slots):
                self.assertAlmostEqual(ls.count(v) / len(ls), slots.count(v) / SLOTS, delta=0.1, msg=(st, v))
            # not the cycle: the steps don't decide it
            self.assertNotEqual(got, [self.gl[g["g"]] for g in hb(self.plain, text)])
            # the rule and the joined pair apply all the same
            self.assertEqual(got[-4:], [("variant", "l", got[-4][2]), ("variant", "o", lo), ("space", got[-2][1]), ("joined", "th")])
            self.assertTrue(f)


# ---------------------------------------------------------------- Autograph (FR-009, FR-011, FR-012, FR-045)

BASE_CHARS = ALNUM + ".,;:!?'-()@/&+#%_=$\""


def base_strokes(c):
    """a made-up base letter (x-heights, y up from the baseline): a stem as tall as its class
    and a hook whose shape depends on the character, so every one differs; marks a dot or a
    short line"""
    k = ord(c)
    if c.isalnum():
        top = 1.8 if c.isupper() or c.isdigit() or c in "bdfhklt" else 1.0
        bot = -0.7 if c in "gjpqy" else 0.0
        w = 0.4 + (k % 5) / 10
        return [[[0, top], [0, bot]], [[0, 0.5 + (k % 3) / 10], [w, top * 0.8], [w, 0.1], [0.1 + (k % 4) / 20, 0]]]
    if c in ".,:;":
        return [[[0.05, 0.0]]] + ([[[0.05, 0.8]]] if c in ":;" else [])
    return [[[0, 1.0 + (k % 7) / 10], [0.3 + (k % 3) / 10, 0.2]]]


def write_base(path, chars=BASE_CHARS):
    """a base font file as tools/base-font.py writes it (docs/recognizer.md, The base font)"""
    head = {"format": "glassine-handwriting-base", "version": 1, "family": "Test Base",
            "copyright": "Copyright the test", "licence": "OFL-1.1", "licence_url": "https://openfontlicense.org",
            "source": "none", "sha256": "0", "slant": 0, "measured_slant": 0, "cap_height": 1.8,
            "ascender": 1.8, "descender": -0.7, "ink_width": 0.12, "letter_spacing": 0.3, "word_spacing": 1.0}
    lines = [json.dumps(head)] + [json.dumps({"char": c, "strokes": base_strokes(c)}) for c in chars]
    Path(path).write_text("\n".join(lines) + "\n")
    # its licence beside it, as the base is installed
    (Path(path).parent / "OFL.txt").write_text("SIL OPEN FONT LICENSE Version 1.1 (the test's stand-in)\n")
    return path


def font_files(e):
    """the installed Autograph: version, {style: path}"""
    st = e.run("font", "status").stdout.splitlines()
    if st == ["none"]:
        return None, {}
    first = st[0].split("\t")
    files = {"Regular": Path(first[2])}
    files.update({f[1]: Path(f[2]) for f in (ln.split("\t") for ln in st[1:]) if f[0] == "style"})
    return int(first[1]), files


class Autograph(unittest.TestCase):
    """Autograph: installed from the start as the base, the person's letters replacing the
    base's as they cover them, the same name on every rebuild, exported under a name of the
    person's, back to the base when the samples are erased, rebuilt in the background while
    charging"""

    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="hw-base-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.base = write_base(d / "base.jsonl")
        self.e = Env(base=self.base)
        self.addCleanup(self.e.close)

    def cmap(self, ttf):
        from fontTools.ttLib import TTFont
        return TTFont(str(ttf)).getBestCmap()

    def check_styles(self, files, family="Autograph"):
        """all three styles pass fontTools, carry the name and the base's licence, and shape alike"""
        from fontTools.ttLib import TTFont
        self.assertEqual(set(files), set(STYLES))
        shaped = set()
        for st, path in files.items():
            f = valid_font(self, path)
            n = f["name"]
            self.assertEqual(n.getDebugName(1), family if st == "Regular" else family + " " + st)
            self.assertIn("Copyright the test", n.getDebugName(0))
            self.assertIn("Open Font License", n.getDebugName(13))
            self.assertEqual(n.getDebugName(14), "https://openfontlicense.org")
            for g in f.getGlyphOrder():
                if f["glyf"][g].numberOfContours > 0:
                    self.assertTrue(all(a < 0 for a in signed_areas(f, g)), (st, g))
            shaped.add(subprocess.run(["hb-shape", "--no-glyph-names", str(path), "Hello, world! 42"],
                                      capture_output=True, text=True, check=True).stdout)
        self.assertEqual(len(shaped), 1)
        return TTFont(str(files["Regular"]))

    def test_fresh_person_has_the_base(self):
        """before any sample: installed, found by the font cache, every character the base's"""
        self.assertEqual(self.e.status()["state"], ["current"])
        out = self.e.run("font", "auto").stdout.strip().split("\t")
        self.assertEqual(out[:3], ["installed", "Autograph", "1"])
        v, files = font_files(self.e)
        f = self.check_styles(files)
        cmap = f.getBestCmap()
        self.assertEqual({chr(c) for c in cmap}, set(BASE_CHARS) | {" "})
        # .notdef, the space's steps, then one glyph per base character: nothing in the cycle
        self.assertEqual(len(f.getGlyphOrder()), 1 + SLOTS + len(BASE_CHARS))
        m = subprocess.run(["fc-match", "-f", "%{family}\n%{file}\n", "Autograph"], env=self.e.env,
                           capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(m, ["Autograph", str(files["Regular"])])
        # a second run has nothing to do; the empty model is up to date
        self.assertEqual(self.e.run("font", "auto").stdout.strip(), "skip\tcurrent")
        self.assertEqual(font_files(self.e)[0], 1)
        # the preview's model: the base's metrics, every character a base line
        show = [json.loads(x) for x in self.e.run("font", "show").stdout.splitlines()]
        self.assertEqual({d["base"] for d in show if "base" in d}, set(BASE_CHARS))
        met = next(d["metrics"] for d in show if "metrics" in d)
        self.assertEqual((met["cap_height"], met["ink_width"]), (1.8, 0.12))
        self.assertFalse(any(d.get("covered") for d in show if "char" in d))

    def test_covered_characters_replace_the_base(self):
        rnd = random.Random(41)
        self.e.put([sample("".join(c for c in "abcdefghij" * 3)[i:i + 5], rnd) for i in range(0, 30, 5)])
        self.e.run("letters", "build", "-r")
        st = self.e.status()
        covered = {c for c, (n, nv) in st["chars"].items() if nv > 0}
        self.assertEqual(covered, set("abcdefghij"))
        self.e.run("font", "install")
        v, files = font_files(self.e)
        f = self.check_styles(files)
        cmap = f.getBestCmap()
        order = f.getGlyphOrder()
        self.assertEqual({chr(c) for c in cmap}, set(BASE_CHARS) | {" "})
        cov = sorted(covered)
        nvar = sum(json.loads(x)["variants"].__len__() for x in self.e.model().splitlines()
                   if '"char"' in x and json.loads(x).get("covered"))
        first_base = 1 + (len(cov) + 1) * SLOTS + nvar
        for c in BASE_CHARS:
            gid = order.index(cmap[ord(c)])
            if c in covered:
                self.assertEqual(gid, 1 + (1 + cov.index(c)) * SLOTS, c)      # its step-0 glyph
            else:
                self.assertGreaterEqual(gid, first_base, c)                       # the base's, last
        self.assertEqual(len(order), first_base + len(BASE_CHARS) - len(covered))
        # a base character stands outside the cycle: after it the run starts again at step 0
        text = "abc!abc k,a"
        got = [g["g"] for g in hb(files["Regular"], text)]
        want, prev = [], None
        for c in text:
            if c in covered or c == " ":
                prev = step(prev)
                want.append(1 + ((1 + cov.index(c)) if c != " " else 0) * SLOTS + prev)
            else:
                prev = None
                want.append(order.index(cmap[ord(c)]))
        self.assertEqual(got, want)
        # the preview's model draws the same: a base line exactly for each character it lacks
        show = [json.loads(x) for x in self.e.run("font", "show").stdout.splitlines()]
        self.assertEqual({d["base"] for d in show if "base" in d}, set(BASE_CHARS) - covered)

    def test_export_and_erase(self):
        rnd = random.Random(42)
        self.e.put([sample("abcde", rnd) for _ in range(3)])
        self.e.run("letters", "build", "-r")
        self.e.run("font", "install")
        v, files = font_files(self.e)
        out = self.e.dir / "out"
        out.mkdir()
        ex = self.e.run("font", "export", "-n", "Mïne", str(out)).stdout.splitlines()
        got = {f[1]: Path(f[2]) for f in (ln.split("\t") for ln in ex) if f[0] == "exported"}
        self.assertEqual({p.name for p in got.values()}, {"Mne-Regular.ttf", "Mne-Calligraphy.ttf", "Mne-Brush.ttf"})
        # drawn over the base: its licence beside them, as the OFL asks (FR-012, FR-045)
        lic = self.base.parent / "OFL.txt"
        self.assertIn("licence\t%s" % (out / "OFL.txt"), ex)
        self.assertEqual((out / "OFL.txt").read_bytes(), lic.read_bytes())
        # without it nothing goes out
        out2 = self.e.dir / "out2"
        out2.mkdir()
        keep = lic.read_bytes()
        lic.unlink()
        self.assertEqual(self.e.run("font", "export", "-n", "Mine", str(out2), check=False).returncode, 1)
        self.assertEqual(list(out2.iterdir()), [])
        lic.write_bytes(keep)
        self.check_styles(got, family="Mïne")
        self.assertEqual(font_files(self.e), (v, files))             # the installed one stays
        self.assertEqual(self.e.run("font", "export", "-n", "x", str(self.e.dir / "none"), check=False).returncode, 1)
        # erased: back to the base under the same name, its version counting on
        self.e.run("samples", "erase")
        self.assertFalse((self.e.store / "letters" / "model").exists())
        v2, files2 = font_files(self.e)
        self.assertEqual(v2, v + 1)
        self.assertTrue(all(not p.exists() for p in files.values()))
        f = self.check_styles(files2)
        self.assertEqual(len(f.getGlyphOrder()), 1 + SLOTS + len(BASE_CHARS))
        self.assertEqual(self.e.status()["state"], ["current"])

    def test_rebuilt_while_charging(self):
        self.e.run("font", "auto")
        self.assertEqual(font_files(self.e)[0], 1)
        rnd = random.Random(43)
        self.e.put([sample("abcde", rnd) for _ in range(3)])
        bat = self.e.dir / "power" / "battery" / "status"
        bat.write_text("Discharging\n")
        self.assertEqual(self.e.run("font", "auto").stdout.strip(), "skip\tcharging")
        self.assertEqual(font_files(self.e)[0], 1)
        self.assertFalse((self.e.store / "letters" / "model").exists())
        bat.write_text("Charging\n")
        out = self.e.run("font", "auto").stdout.strip().split("\t")
        self.assertEqual(out[:3], ["installed", "Autograph", "2"])
        st = self.e.status()
        self.assertEqual((st["state"], st["font-letters"]), (["current"], ["same"]))
        self.assertEqual(self.cmap(font_files(self.e)[1]["Regular"]).keys(), self.cmap(font_files(self.e)[1]["Brush"]).keys())
        self.assertEqual(self.e.run("font", "auto").stdout.strip(), "skip\tcurrent")
        # learning off: the letters aren't built in the background (FR-008)
        self.e.put([sample("fghij", rnd) for _ in range(3)])
        self.e.run("learning", "off")
        self.assertEqual(self.e.run("font", "auto").stdout.strip(), "skip\tlearning")
        self.assertEqual(font_files(self.e)[0], 2)
        self.e.run("learning", "on")
        self.assertEqual(self.e.run("font", "auto").stdout.strip().split("\t")[:3], ["installed", "Autograph", "3"])
        # a writer's store has no font
        w = self.e.run("writer", "new").stdout.strip()
        self.assertEqual(self.e.run("--writer", w, "font", "auto", check=False).returncode, 2)

    def test_a_font_from_before_takes_the_name(self):
        """a font installed under another name (before Autograph) is made again as Autograph"""
        rnd = random.Random(44)
        self.e.put([sample("abcde", rnd) for _ in range(3)])
        self.e.run("letters", "build", "-r")
        self.e.run("font", "install")
        rec = self.e.store / "letters" / "font"
        rec.write_text(rec.read_text().replace("Autograph\t", "My handwriting\t", 1))
        (self.e.dir / "power" / "battery" / "status").write_text("Discharging\n")    # at once, charging or not
        self.assertEqual(self.e.run("font", "auto").stdout.strip().split("\t")[:3], ["installed", "Autograph", "2"])


class BaseLanguage(unittest.TestCase):
    """the base of the language the recognizer reads by default: its own (LANG/base.jsonl) when
    that language has one and a model, else base.jsonl (docs/recognizer.md, The base font)"""

    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="hw-baselang-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.dir = d / "autograph"
        self.dir.mkdir()
        write_base(self.dir / "base.jsonl", "abc")
        (self.dir / "de").mkdir()
        write_base(self.dir / "de" / "base.jsonl", "abcä")
        self.model = d / "model.ghm"
        subprocess.run([sys.executable, str(HERE / "mkmodel.py"), str(self.model)], check=True)

    def chars(self, langs=None, locale=None, models=("en",)):
        """the characters of a fresh person's Autograph"""
        from fontTools.ttLib import TTFont
        e = Env(base=self.dir)
        self.addCleanup(e.close)
        for m in models:
            (e.dir / "models" / m).mkdir(parents=True)
            shutil.copy(self.model, e.dir / "models" / m / "recognizer.ghm")
        for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
            e.env.pop(k, None)
        if locale:
            e.env["LANG"] = locale
        if langs is not None:
            (e.dir / "conf" / "glassine").mkdir(parents=True)
            (e.dir / "conf" / "glassine" / "handwriting.conf").write_text(f"languages = {langs}\n")
        self.assertEqual(e.run("font", "auto").stdout.split("\t")[0], "installed")
        _, files = font_files(e)
        return {chr(c) for c in TTFont(str(files["Regular"])).getBestCmap()} - {" "}

    def test_default_without_a_language_of_its_own(self):
        self.assertEqual(self.chars(), set("abc"))
        self.assertEqual(self.chars(langs="en,de", models=("en", "de")), set("abc"))

    def test_a_language_without_a_model_keeps_the_default(self):
        # a German locale, but only the English model: the recognizer reads English, so the base stays
        self.assertEqual(self.chars(locale="de_DE.UTF-8"), set("abc"))
        self.assertEqual(self.chars(langs="de,en"), set("abc"))

    def test_the_language_read_has_its_own(self):
        self.assertEqual(self.chars(locale="de_DE.UTF-8", models=("en", "de")), set("abcä"))
        self.assertEqual(self.chars(langs="de-AT,en", models=("en", "de")), set("abcä"))
        self.assertEqual(self.chars(langs="fr,de", models=("en", "de")), set("abcä"))


@unittest.skipUnless(os.environ.get("HW_BASE"), "HW_BASE: the packaged base font file (make base)")
class AutographBase(unittest.TestCase):
    """the base as packaged (HW_BASE, made by tools/base-font.py from Edu SA Beginner): a fresh
    person's Autograph and one with letters of their own, every style valid, every base
    character mapped, the base's copyright and licence carried on"""

    def test_packaged_base(self):
        from fontTools.ttLib import TTFont
        base = Path(os.environ["HW_BASE"])
        head = json.loads(base.read_text().splitlines()[0])
        chars = {json.loads(x)["char"] for x in base.read_text().splitlines()[1:]}
        self.assertEqual(head["licence"], "OFL-1.1")
        # every character the recognizer reads (FR-045; the trainer's set, tests/mkmodel.py)
        import re
        rec = re.search(r'^CHARS = "(.*)"$', (HERE / "mkmodel.py").read_text(), re.M).group(1)
        self.assertEqual(set(rec) - {" "} - chars, set())
        e = Env(base=base)
        self.addCleanup(e.close)
        for step in ("fresh", "letters"):
            if step == "letters":
                rnd = random.Random(45)
                e.put([sample("abcdefg", rnd) for _ in range(3)])
            self.assertEqual(e.run("font", "auto").stdout.split("\t")[0], "installed", step)
            _, files = font_files(e)
            self.assertEqual(set(files), set(STYLES))
            for st, path in files.items():
                f = valid_font(self, path)
                self.assertEqual({chr(c) for c in f.getBestCmap()}, chars | {" "}, (step, st))
                n = f["name"]
                self.assertEqual(n.getDebugName(0).split(". Characters")[0], head["copyright"])
                self.assertIn("Open Font License", n.getDebugName(13))
                self.assertNotIn("Edu", n.getDebugName(1))       # the base's name is not Autograph's
                # every glyph drawn (outlines or a composite of them) for each character but the space
                order = f.getGlyphOrder()
                for c, g in f.getBestCmap().items():
                    if c != 32:
                        self.assertNotEqual(f["glyf"][g].numberOfContours, 0, (step, st, chr(c), order.index(g)))
            if step == "letters":
                reg = TTFont(str(files["Regular"]))
                self.assertGreater(len(reg.getGlyphOrder()), 1 + SLOTS + len(chars))     # the letters' steps

    @unittest.skipUnless(os.environ.get("HW_BASE_DE"), "HW_BASE_DE: German's base as packaged")
    def test_packaged_german_base(self):
        """German's own base (HW_BASE_DE, made by tools/base-font.py from Playwrite DE Grund):
        every character of tools/base-de.chars, the recognizer's and German's letters among
        them; Autograph valid in every style with it, its copyright and licence carried on"""
        from fontTools.ttLib import TTFont
        import re
        base = Path(os.environ["HW_BASE_DE"])
        head = json.loads(base.read_text().splitlines()[0])
        chars = {json.loads(x)["char"] for x in base.read_text().splitlines()[1:]}
        self.assertEqual(head["licence"], "OFL-1.1")
        want = set((HERE.parent / "tools" / "base-de.chars").read_text(encoding="utf-8")) - {"\n"}
        self.assertEqual(chars, want)
        rec = re.search(r'^CHARS = "(.*)"$', (HERE / "mkmodel.py").read_text(), re.M).group(1)
        self.assertEqual((set(rec) | set("äöüÄÖÜß")) - {" "} - chars, set())
        # every dot of a mark made only of dots kept (a colon's two, an ellipsis' three)
        strokes = {json.loads(x)["char"]: json.loads(x)["strokes"] for x in base.read_text().splitlines()[1:]}
        for c, dots in ((":", 2), ("…", 3), ("¨" if "¨" in strokes else "ä", 2)):
            self.assertGreaterEqual(sum(len(t) == 1 for t in strokes[c]), dots, c)
        e = Env(base=base)
        self.addCleanup(e.close)
        self.assertEqual(e.run("font", "auto").stdout.split("\t")[0], "installed")
        _, files = font_files(e)
        self.assertEqual(set(files), set(STYLES))
        for st, path in files.items():
            f = valid_font(self, path)
            self.assertEqual({chr(c) for c in f.getBestCmap()}, chars | {" "}, st)
            n = f["name"]
            self.assertEqual(n.getDebugName(0).split(". Characters")[0], head["copyright"])
            self.assertIn("Open Font License", n.getDebugName(13))
            self.assertNotIn("Playwrite", n.getDebugName(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
