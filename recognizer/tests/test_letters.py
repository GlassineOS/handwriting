#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance for the letter model and the handwriting font (spec 003 FR-006 to
FR-012, SC-001), on synthetic samples: the model is the same on every run and in any import
order; a character needs 3 usable samples; the font passes fontTools with checksums, maps
exactly the covered characters and the space, alternates the same way every time when
shaped with no feature asked for (and as hw_letters_step says); installed, the font cache
finds it in a test user; a rebuild keeps the name and changes the version and the file;
erasing the samples takes the model and the font.

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
import shutil
import subprocess
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


def sample(text, rnd, styles=None, written=None, line=150.0, xh=30.0):
    """a prompted sample (sample format v1) of separate letters, device units"""
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
        for st in shape(c, (styles or {}).get(c, 0), rnd):
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


class Env:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hw-letters-"))
        self.home = self.dir / "home"
        self.home.mkdir()
        self.env = dict(os.environ, XDG_DATA_HOME=str(self.dir / "data"), XDG_CONFIG_HOME=str(self.dir / "conf"),
                        XDG_CACHE_HOME=str(self.dir / "cache"), HOME=str(self.home),
                        GLASSINE_HANDWRITING_MODELS=str(self.dir / "models"),
                        GLASSINE_HANDWRITING_SYSFS=str(self.dir / "power"),
                        GLASSINE_HANDWRITING_LOADAVG=str(self.dir / "load"))
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


def alphabet_samples(seed=1, per=4, careful=None, styles=None):
    """samples of separate letters that hold every letter and digit `per` times"""
    rnd = random.Random(seed)
    chars = list(ALNUM) * per
    return [sample("".join(chars[i:i + 5]), rnd, styles=styles, written=careful) for i in range(0, len(chars), 5)]


class LetterModel(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

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
        self.assertEqual(st["state"], ["none"])
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
    """spec 003 FR-006, FR-007, FR-009: one spacing from the letters' profiles: a pair's own
    gap, a rare pair its side shapes', an unseen pair one from the profiles; the font sets
    each shaped pair at its gap and words at the word spacing"""
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
        p = subprocess.run(["hb-shape", "--output-format=json", str(self.ttf), text], capture_output=True, text=True,
                           check=True)
        cov = sorted(c for c, d in self.chars.items() if d.get("covered") and d.get("variants"))
        out, x = [], 0
        for g in json.loads(p.stdout):
            i, st = (g["g"] - 1) // SLOTS - 1, (g["g"] - 1) % SLOTS
            c = cov[i] if i >= 0 else " "
            v = self.chars[c]["variants"][self.chars[c]["slots"][st]] if c != " " else None
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

    def test_font_sets_the_gaps(self):
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
            self.assertEqual(out[:3], ["installed", "My handwriting", "1"])
            path = Path(out[3])
            self.assertEqual(path.parent, e.fonts)
            m = subprocess.run(["fc-match", "-f", "%{family}\n%{file}\n", "My handwriting"], env=e.env,
                               capture_output=True, text=True, check=True).stdout.splitlines()
            self.assertEqual(m, ["My handwriting", str(path)])
            # renamed and rebuilt: a new version and file, the old one gone
            out = e.run("font", "install", "-n", "Ünïcode hand").stdout.strip().split("\t")
            self.assertEqual(out[:3], ["installed", "Ünïcode hand", "2"])
            self.assertFalse(path.exists())
            out = e.run("font", "install").stdout.strip().split("\t")
            self.assertEqual(out[:3], ["installed", "Ünïcode hand", "3"])
            path = Path(out[3])
            self.assertEqual(e.run("font", "status").stdout.strip().split("\t"), ["Ünïcode hand", "3", str(path)])
            lst = subprocess.run(["fc-list", ":", "family", "file"], env=e.env, capture_output=True, text=True,
                                 check=True).stdout
            self.assertIn(str(path), lst)
            self.assertEqual(len(list(e.fonts.iterdir())), 1)
            self.assertEqual(e.run("font", "install", "-n", "bad\tname", check=False).returncode, 2)
            # erasing the samples takes the letter model and the font
            e.run("samples", "erase")
            self.assertFalse(path.exists())
            self.assertFalse((e.store / "letters").exists())
            self.assertEqual(e.run("font", "status").stdout.strip(), "none")
            self.assertEqual(e.status()["state"], ["none"])
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
            self.assertEqual(e.run("font", "install").stdout.strip(), "skip\tno-letters")
            self.assertFalse(e.fonts.exists())
        finally:
            e.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
