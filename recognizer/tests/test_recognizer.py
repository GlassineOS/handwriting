#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance for glassine-handwriting (spec 001): the line contract against recorded
stroke files (recognize, streaming, prompted alignment, gestures), the sample rules (no
sample without confirmation, none from a private field, erase and export/import round
trip), the adaptation (a better one is kept, a worse one is rejected, an erase drops a
running one), the donation file, the idle exit.

    HW_BIN=path/to/glassine-handwriting python3 tests/test_recognizer.py [TEST...]

Python 3 standard library only. A random-weight model is built per run (tests/mkmodel.py):
its readings are nonsense, so text is checked only with HW_MODEL_DIR set (a directory with
en/recognizer.ghm, the trained model).
"""

import json
import math
import os
import random
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = Path(os.environ.get("HW_BIN") or HERE.parent / "glassine-handwriting").resolve()
INK = [json.loads(line) for line in (HERE / "data" / "ink.jsonl").read_text().splitlines()]
REAL = os.environ.get("HW_MODEL_DIR")


def edits(a, b):
    """character edit distance"""
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def pts(stroke, t0=0.0):
    """sample-format points (t in s) -> the contract's POINTS (t in ms)"""
    return " ".join(f"{p[0]:.4f},{p[1]:.4f},{(p[2] - t0) * 1000:.1f}" for p in stroke)


def esc(s):
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def unesc(s):
    return s.replace("\\t", "\t").replace("\\n", "\n").replace("\\r", "\r").replace("\\\\", "\\")


class Env:
    """A model directory and a person's home of their own."""

    def __init__(self, model_dir=None):
        self.dir = Path(tempfile.mkdtemp(prefix="hw-test-"))
        self.models = self.dir / "models"
        (self.models / "en").mkdir(parents=True)
        if model_dir:
            shutil.copy(Path(model_dir) / "en" / "recognizer.ghm", self.models / "en" / "recognizer.ghm")
        else:
            subprocess.run([sys.executable, str(HERE / "mkmodel.py"), str(self.models / "en" / "recognizer.ghm")],
                           check=True)
        (self.models / "en" / "prompts.txt").write_text("The quick brown fox jumps over the lazy dog.\n")
        (self.models / "en" / "prompts").mkdir()
        (self.models / "en" / "prompts" / "words.txt").write_text("hello\nwater\n")
        self.data = self.dir / "data"
        self.conf = self.dir / "conf"
        self.sysfs = self.dir / "power"
        self.charging(True)
        self.env = dict(os.environ, GLASSINE_HANDWRITING_MODELS=str(self.models), XDG_DATA_HOME=str(self.data),
                        XDG_CONFIG_HOME=str(self.conf), HOME=str(self.dir / "home"),
                        GLASSINE_HANDWRITING_SYSFS=str(self.sysfs), GLASSINE_HANDWRITING_LOADAVG=str(self.dir / "load"))
        (self.dir / "load").write_text("0.10 0.20 0.30 1/100 1\n")
        # the word predictor's models: none unless a test writes one (an installed one never counts)
        self.predict = self.dir / "predict"
        self.predict.mkdir()
        self.env["GLASSINE_PREDICT_MODELS"] = str(self.predict)
        self.env.pop("LC_ALL", None)
        self.env["LANG"] = "en_US.UTF-8"

    def words(self, words, lang="en"):
        """a word predictor model (format 1, shell docs/predictor.md) with these words, all
        likely, no context"""
        import struct
        ws = sorted({w.lower() for w in words})
        strs, keys = b"", []
        for w in ws:
            keys.append(len(strs))
            strs += w.encode() + b"\0"
        strs += b"\0" * (-len(strs) % 4)
        n, off = len(ws), 84
        vocab, off_str = off, off + 12 * n
        off_bi = off_str + len(strs)
        end = off_bi + 4 * (n + 2)
        hdr = struct.pack("<4sI8s13I4I", b"GLPM", 1, lang.encode(), n, vocab, off_str, len(strs), off_bi,
                          end, 0, end, 0, end, 0, end, 0, 0, 0, 0, 0)
        assert len(hdr) == off
        body = b"".join(struct.pack("<IIB3x", k, k, 24) for k in keys) + strs + b"\0" * (4 * (n + 2))
        (self.predict / f"{lang}.gpm").write_bytes(hdr + body)

    @property
    def store(self):
        return self.data / "glassine" / "handwriting"

    def charging(self, on):
        bat = self.sysfs / "battery"
        bat.mkdir(parents=True, exist_ok=True)
        (bat / "type").write_text("Battery\n")
        (bat / "status").write_text("Charging\n" if on else "Discharging\n")

    def conf_set(self, name, text):
        (self.conf / "glassine").mkdir(parents=True, exist_ok=True)
        (self.conf / "glassine" / name).write_text(text)

    def run(self, *args, check=True, inp=None):
        p = subprocess.run([str(BIN), *args], env=self.env, capture_output=True, text=True, input=inp, timeout=300)
        if check and p.returncode:
            raise AssertionError(f"{args}: {p.returncode} {p.stderr}")
        return p

    def start(self, *args):
        return Client(self, *args)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Client:
    def __init__(self, env, *args):
        self.p = subprocess.Popen([str(BIN), *args], env=env.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1)
        self.n = 0

    def send(self, *lines):
        self.p.stdin.write("".join(line + "\n" for line in lines))
        self.p.stdin.flush()

    def get(self):
        line = self.p.stdout.readline()
        assert line.endswith("\n"), f"no reply ({self.p.poll()}): {self.p.stderr.read() if self.p.poll() is not None else ''}"
        return line[:-1].split("\t")

    def ask(self, verb, *fields):
        self.n += 1
        rid = f"r{self.n}"
        self.send("\t".join([verb, rid, *fields]))
        r = self.get()
        assert r[1] == rid, (r, rid)
        return r

    def close(self):
        self.p.stdin.close()
        rc = self.p.wait(timeout=30)
        err = self.p.stderr.read()
        self.p.stdout.close()
        self.p.stderr.close()
        return rc, err

    def write_sample(self, s, session="s", field="normal", units="em", before=""):
        assert self.ask("begin", session, s["lang"], field, units, esc(before))[2] == "ok"
        ids = []
        for q in s["strokes"]:
            r = self.ask("stroke", session, pts(q))
            assert r[2] == "ok", r
            ids.append(int(r[3]))
        return ids


def parse_text(r):
    """read/recognize reply -> [(text, score, [segments per char])]"""
    assert r[2] == "text", r
    n = int(r[3])
    out = []
    for i in range(n):
        text, score, align = r[4 + 3 * i: 7 + 3 * i]
        out.append((text, float(score), align.split(";") if text else []))
    assert len(r) == 4 + 3 * n, r
    return out


def seg_strokes(seg):
    return {int(x.split(":")[0]) for x in seg.split(",") if x}


class Contract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env(REAL)

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def setUp(self):
        self.c = self.env.start("-i", "0")

    def tearDown(self):
        rc, err = self.c.close()
        self.assertEqual(rc, 0, err)

    def test_hello_and_errors(self):
        self.assertEqual(self.c.ask("hello", "1", "org.example.notes"), ["hello", "r1", "1"])
        self.assertEqual(self.c.ask("hello", "9"), ["hello", "r2", "1"])
        self.c.send("nonsense\tx1")
        self.assertEqual(self.c.get(), ["error", "x1", "unknown-verb"])
        self.c.send("read\tbad id!\ts")
        self.assertEqual(self.c.get(), ["error", "-", "bad-id"])
        self.assertEqual(self.c.ask("read", "nosuch")[2:], ["unknown-session"])
        self.assertEqual(self.c.ask("begin", "s", "en", "normal", "em", "a\\qb")[2:], ["bad-escape"])
        self.assertEqual(self.c.ask("begin", "s", "en", "normal", "furlongs", "")[2:], ["bad-units"])
        self.assertEqual(self.c.ask("begin", "s", "EN!", "normal", "em", "")[2:], ["bad-lang"])
        self.assertEqual(self.c.ask("begin", "s", "en", "Normal", "em", "")[2:], ["bad-field"])
        self.assertEqual(self.c.ask("begin", "s", "en", "normal", "device:10:200", "")[2:], ["ok"])
        self.assertEqual(self.c.ask("stroke", "s", "1,2")[2:], ["bad-points"])
        self.assertEqual(self.c.ask("stroke", "s", "1,2,3 4,5,6,0.5")[2:], ["bad-points"])
        self.assertEqual(self.c.ask("read", "s")[2:], ["empty"])
        self.assertEqual(self.c.ask("begin", "t", "xx", "normal", "em", "")[2:], ["ok"])
        self.assertEqual(self.c.ask("stroke", "t", "0,0,0 1,1,10")[2:], ["ok", "0"])
        self.assertEqual(self.c.ask("read", "t")[2:], ["no-model"])
        self.c.send("x" * (5 << 20))
        self.assertEqual(self.c.get(), ["error", "-", "too-long"])
        self.assertEqual(self.c.ask("end", "s")[2:], ["ok"])
        # more lines at once than one batch: every one is answered, in order
        self.c.send(*(f"clear\t{i}\tz" for i in range(6000)))
        got = [self.c.get() for _ in range(6000)]
        self.assertEqual(got[0], ["error", "0", "unknown-session"])
        self.assertEqual([g[1] for g in got], [str(i) for i in range(6000)])

    def test_read_and_streaming(self):
        """the reading after the last stroke equals a one-shot recognize of the same strokes"""
        for s in (INK[13], INK[-2]):   # training fonts: the trained model reads them right
            ids = self.c.write_sample(s)
            self.assertEqual(ids, list(range(len(s["strokes"]))))
            r = parse_text(self.c.ask("read", "s", "3"))
            self.assertTrue(1 <= len(r) <= 3)
            self.assertEqual([x[1] for x in r], sorted([x[1] for x in r], reverse=True))
            for text, _, align in r:
                self.assertEqual(len(align), len(text))
                for ch, seg in zip(text, align):
                    self.assertTrue(ch == " " or seg_strokes(seg) <= set(ids), (text, seg))
                    if ch == " ":
                        self.assertEqual(seg, "")
            one = self.c.ask("recognize", "en", "normal", "em", "3", "", ";".join(pts(q) for q in s["strokes"]))
            self.assertEqual(one[0], "recognize")
            self.assertEqual(parse_text(one), r)
            if REAL:   # any trained model, not one model's exact output: at most 1 error in 10
                self.assertLessEqual(edits(r[0][0], s["text"]), max(1, len(s["text"]) // 10), r[0][0])
            self.c.ask("end", "s")

    def test_stream_reads_while_writing(self):
        s = INK[-2]
        self.c.ask("begin", "s", "en", "normal", "em", "")
        for q in s["strokes"]:
            self.c.ask("stroke", "s", pts(q))
            r = self.c.ask("read", "s", "1")
            self.assertIn(r[2], ("text", "gesture"))
        self.assertEqual(r[2], "text")
        # several reads read together: only the newest is answered
        self.c.send("read\ta1\ts", "read\ta2\ts", "stroke\ta3\ts\t0,0,0 0.01,0.3,40", "read\ta4\ts", "read\ta5\ts")
        got = [self.c.get() for _ in range(5)]
        self.assertEqual([g[:2] for g in got[:2]], [["drop", "a1"], ["drop", "a2"]])
        self.assertEqual(got[2][:3], ["stroke", "a3", "ok"])
        self.assertEqual(got[3][:2], ["drop", "a4"])
        self.assertEqual(got[4][:3], ["read", "a5", "text"])

    def test_remove_and_clear(self):
        ids = self.c.write_sample(INK[0])
        self.assertEqual(self.c.ask("remove", "s", f"{ids[0]},999")[2:], ["ok", "1"])
        r = parse_text(self.c.ask("read", "s"))
        for _, _, align in r:
            for seg in align:
                self.assertNotIn(ids[0], seg_strokes(seg))
        self.assertEqual(self.c.ask("stroke", "s", "0,0,0 0,0.4,50")[3], str(len(ids)))   # numbers never reused
        self.assertEqual(self.c.ask("clear", "s")[2:], ["ok"])
        self.assertEqual(self.c.ask("read", "s")[2:], ["empty"])

    def test_prompted_alignment(self):
        """one group of strokes per prompted character, for a pangram written in one go"""
        s = next(x for x in INK if x["text"].startswith("The quick"))
        ids = self.c.write_sample(s)
        r = self.c.ask("align", "s", esc(s["text"]))
        self.assertEqual(r[0], "align")
        n = int(r[3])
        self.assertEqual(n, len(s["text"]))
        self.assertEqual(len(r), 4 + 2 * n)
        groups = []
        for k, ch in enumerate(s["text"]):
            fit, seg = r[4 + 2 * k], r[5 + 2 * k]
            if ch == " ":
                self.assertEqual((fit, seg), ("-", ""))
                continue
            self.assertTrue(0 <= float(fit) <= 1)
            self.assertTrue(seg, f"no strokes for {ch!r}")
            self.assertTrue(seg_strokes(seg) <= set(ids))
            groups.append(seg_strokes(seg))
        self.assertTrue(set().union(*groups) == set(ids), "every stroke belongs to a character")
        if REAL:
            exact = [set(sp) for sp, ch in zip(s["spans"], s["text"]) if ch != " "]
            agree = sum(bool(a & b) for a, b in zip(groups, exact)) / len(exact)
            self.assertGreater(agree, 0.9)
        self.assertEqual(self.c.ask("align", "s", "x" * 400)[2:], ["short"])
        self.assertEqual(self.c.ask("align", "s", "")[2:], ["bad-prompt"])

    def strike(self, s, ids, y=None):
        xs = [p[0] for q in s["strokes"] for p in q]
        ys = [p[1] for q in s["strokes"] for p in q]
        y = y if y is not None else (min(ys) + max(ys)) / 2
        t = max(p[2] for q in s["strokes"] for p in q) + 0.5
        n = 30
        line = [[min(xs) - 0.1 + (max(xs) - min(xs) + 0.2) * i / n, y + 0.01 * math.sin(i), t + 0.01 * i] for i in range(n + 1)]
        return line

    def test_gestures(self):
        s = INK[1]   # hello
        ids = self.c.write_sample(s)
        ys = [p[1] for q in s["strokes"] for p in q]
        r = self.c.ask("stroke", "s", pts(self.strike(s, ids, y=-0.2)))
        g = self.c.ask("read", "s")
        self.assertEqual(g[2:4], ["gesture", "strike"], g)
        self.assertEqual(g[4], r[3])
        covered = {int(x) for x in g[6].split(",")}
        self.assertGreaterEqual(len(covered & set(ids)), len(ids) - 1)
        self.assertRegex(g[7], r"^\d+-\d+$")
        # the gesture stroke is gone from the session: the next reading is text
        self.assertEqual(self.c.ask("read", "s")[2], "text")
        # scratch-out over the first half
        xs = [p[0] for q in s["strokes"] for p in q]
        x0, x1 = min(xs), (min(xs) + max(xs)) / 2
        zig = [[x0 + (x1 - x0) * (i % 2), min(ys) + (max(ys) - min(ys)) * i / 12, 9 + 0.02 * i] for i in range(13)]
        self.c.ask("stroke", "s", pts(zig))
        g = self.c.ask("read", "s")
        self.assertEqual(g[2:4], ["gesture", "scratch"], g)
        self.assertTrue(g[6] != "-")
        # caret between letters, below the line
        mid = (min(xs) + max(xs)) / 2
        caret = [[mid - 0.15 + 0.15 * i / 10, 0.25 - 0.3 * i / 10, 12 + 0.01 * i] for i in range(11)] + \
                [[mid + 0.15 * i / 10, -0.05 + 0.3 * i / 10, 12.1 + 0.01 * i] for i in range(1, 11)]
        self.c.ask("stroke", "s", pts(caret))
        g = self.c.ask("read", "s")
        self.assertEqual(g[2:4], ["gesture", "caret"], g)
        self.assertRegex(g[7], r"^@\d+$")
        # a lone strike with nothing else: the pen's units decide it is long enough
        self.c.ask("begin", "u", "en", "normal", "device:10", "")
        self.c.ask("stroke", "u", " ".join(f"{100 + 4 * i},{200 + (i % 2)},{5 * i}" for i in range(40)))
        self.assertEqual(self.c.ask("read", "u")[2:4], ["gesture", "strike"])
        self.c.ask("stroke", "u", " ".join(f"{100 + 0.5 * i},{200},{5 * i}" for i in range(20)))   # 1 mm: a dash
        self.assertEqual(self.c.ask("read", "u")[2], "text")

    def test_no_gesture_in_writing(self):
        """no stroke of the recorded writing reads as a gesture while it is written"""
        for n, s in enumerate(INK):
            self.c.ask("begin", f"w{n}", "en", "normal", "em", "")
            for q in s["strokes"]:
                self.c.ask("stroke", f"w{n}", pts(q))
                r = self.c.ask("read", f"w{n}", "1")
                self.assertNotEqual(r[2], "gesture", (s["text"], r))
            self.c.ask("end", f"w{n}")


PURPOSES = {
    "password": "private", "pin": "private",
    "normal,sensitive_data": "private", "alpha,hidden_text": "private", "name,completion,sensitive_data": "private",
    "terminal": "nolearn", "digits": "nolearn", "number": "nolearn", "phone": "nolearn", "date": "nolearn",
    "time": "nolearn", "datetime": "nolearn", "email": "nolearn", "url": "nolearn", "somethingnew": "nolearn",
    "normal,someunknownhint": "nolearn",
    "normal": "learn", "alpha": "learn", "name": "learn", "normal,auto_capitalization,spellcheck": "learn", "-": "learn",
}


class Samples(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def confirm(self, c, s, field="normal", source="picked", text=None, session="s"):
        c.write_sample(s, session=session, field=field)
        return c.ask("confirm", session, source, esc(text if text is not None else s["text"]))

    def test_fields(self):
        """per purpose and hint: private fields hold nothing, others learn as the table says"""
        for field, rule in PURPOSES.items():
            c = self.env.start("-i", "0")
            c.ask("hello", "1", "org.example.app")
            if rule == "private":
                self.assertEqual(c.ask("begin", "s", "en", field, "em", "secret")[2:], ["private"], field)
                self.assertEqual(c.ask("stroke", "s", "0,0,0 1,1,10")[2:], ["unknown-session"])
                self.assertEqual(c.ask("read", "s")[2:], ["unknown-session"])
                self.assertEqual(c.ask("confirm", "s", "picked", "x")[2:], ["unknown-session"])
                r = c.ask("recognize", "en", field, "em", "3", "", pts(INK[0]["strokes"][0]))
                self.assertEqual(r[2:], ["private"], field)
            else:
                r = self.confirm(c, INK[0], field=field)
                self.assertEqual(r[2], "ok" if rule == "learn" else "skip", (field, r))
                if rule != "learn":
                    self.assertEqual(r[3], "field")
            c.close()
            if rule != "learn":
                self.assertFalse(self.env.store.exists(), f"{field}: store created")
            else:
                shutil.rmtree(self.env.store)
        # email and web fields learn only when the keyboard's setting says so
        self.env.conf_set("predict.conf", "addresses = learn\n")
        c = self.env.start("-i", "0")
        self.assertEqual(self.confirm(c, INK[0], field="email")[2], "ok")
        c.close()

    def test_words(self):
        """words rank the readings in prose fields only (FR-015): with the ink's other readings
        made words, a prose field reads differently from `read … raw`; every other field, and
        the setting off, reads as raw"""
        c = self.env.start("-i", "0")
        raws = []
        for s in INK[:6]:
            c.write_sample(s)
            raws.append([t for t, _, _ in parse_text(c.ask("read", "s", "8", "raw"))])
            c.ask("end", "s")
        c.close()
        # every all-letter reading but the ink's best is a likely word
        self.env.words([t for r in raws for t in r[1:] if t.isalpha()] + ["the"])
        nonprose = ["digits", "number", "phone", "url", "email", "terminal", "date", "alpha", "name",
                    "normal,someunknownhint"]

        def reads(field):
            c = self.env.start("-i", "0")
            out = []
            for s in INK[:6]:
                c.write_sample(s, field=field, before="I saw the")
                out.append([t for t, _, _ in parse_text(c.ask("read", "s", "8"))])
                self.assertEqual(c.ask("read", "s", "8", "bogus")[2], "bad-line")
                c.ask("end", "s")
            c.close()
            return out

        prose = reads("normal")
        self.assertNotEqual(prose, raws, "words change a prose reading")
        for a, b in zip(prose, raws):
            self.assertIn(b[0], a[:2], "the ink's best reading stays among the first two")
        self.assertEqual(reads("-"), prose, "outside a text field: prose")
        for field in nonprose:
            self.assertEqual(reads(field), raws, field)
        self.env.conf_set("handwriting.conf", "words = off\n")
        self.assertEqual(reads("normal"), raws, "words = off")

    def test_pick_recorded(self):
        """a picked candidate of the last reading records its place and origin (sample `pick`)"""
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.app")
        c.write_sample(INK[1])
        cands = [t for t, _, _ in parse_text(c.ask("read", "s", "3"))]
        r = c.ask("confirm", "s", "picked", esc(cands[-1]))
        self.assertEqual(r[2], "ok", r)
        smp = json.loads(unesc(c.ask("sample", r[3])[2]))
        self.assertEqual(smp["pick"], {"rank": len(cands), "from": "beam" if len(cands) > 1 else "ink"})
        # a text that wasn't a candidate: no pick
        c.write_sample(INK[1])
        r = c.ask("confirm", "s", "corrected", esc("not a candidate"))
        smp = json.loads(unesc(c.ask("sample", r[3])[2]))
        self.assertNotIn("pick", smp)
        c.close()

    def test_pick_from_words_must_fit(self):
        """a picked word that the word list proposed (not the ink) makes a sample only when
        the strokes fit it; `keep` doesn't override that; a pick of the ink's own reading
        isn't checked"""
        import re
        c = self.env.start("-i", "0")
        c.write_sample(INK[1])
        raw = [t for t, _, _ in parse_text(c.ask("read", "s", "8", "raw"))]
        c.close()
        m = re.search(r"[A-Za-z]+", raw[0])
        self.assertTrue(m, raw[0])
        near = m.group(0).lower() + "q"            # one letter added: one edit away
        proposed = raw[0][:m.start()] + (m.group(0) + "q") + raw[0][m.end():]
        self.env.words([near])
        self.env.env["GLASSINE_HANDWRITING_WORDS"] = "minlen=1,near=1e9,guided=0"
        self.env.env["GLASSINE_HANDWRITING_FIT_OK"] = "2"   # no fit passes
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.app")
        c.write_sample(INK[1])
        cands = [t for t, _, _ in parse_text(c.ask("read", "s", "8"))]
        self.assertIn(proposed, cands[1:], "the near word offered, not first")
        self.assertEqual(c.ask("confirm", "s", "picked", esc(proposed))[2:4], ["skip", "fit"])
        self.assertEqual(c.ask("confirm", "s", "picked", esc(proposed), "keep")[2:4], ["skip", "fit"])
        self.assertFalse(self.env.store.exists())
        r = c.ask("confirm", "s", "picked", esc(cands[0]))
        self.assertEqual(r[2], "ok", "a pick of the ink's reading isn't checked")
        c.close()

    def test_no_sample_without_confirmation(self):
        c = self.env.start("-i", "0")
        c.write_sample(INK[2])
        c.ask("read", "s")
        c.ask("align", "s", INK[2]["text"])
        c.ask("recognize", "en", "normal", "em", "3", "", pts(INK[2]["strokes"][0]))
        c.ask("end", "s")
        c.close()
        self.assertFalse(self.env.store.exists())

    def test_learning_off(self):
        for name in ("handwriting.conf", "predict.conf"):
            self.env.conf_set(name, "learning = off\n")
            c = self.env.start("-i", "0")
            self.assertEqual(self.confirm(c, INK[0])[2:], ["skip", "learning"])
            c.close()
            self.assertFalse(self.env.store.exists())
            (self.env.conf / "glassine" / name).unlink()
        self.assertEqual(self.env.run("learning").stdout, "on\n")
        self.env.run("learning", "off")
        self.assertEqual(self.env.run("learning").stdout, "off\n")
        self.env.run("learning", "on")
        self.assertEqual(self.env.run("learning").stdout, "on\n")

    def test_store_and_reading_back(self):
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        r = self.confirm(c, INK[0])
        self.assertEqual(r[2], "ok")
        sid = r[3]
        r2 = self.confirm(c, INK[3], source="corrected", text="quack", session="t")
        self.assertEqual(self.confirm(c, INK[4], source="nonsense", session="u")[2:], ["bad-source"])
        st = self.env.store
        self.assertEqual(stat.S_IMODE(st.stat().st_mode), 0o700)
        for f in ("samples.jsonl", "samples.meta"):
            self.assertEqual(stat.S_IMODE((st / f).stat().st_mode), 0o600)
        lines = [json.loads(x) for x in (st / "samples.jsonl").read_text().splitlines()]
        self.assertEqual([x["text"] for x in lines], ["cat", "quack"])
        for x, src in zip(lines, ("confirmed", "corrected")):
            self.assertEqual((x["version"], x["lang"], x["source"], x["spans_source"]), (1, "en", src, "inferred"))
            self.assertEqual(len(x["spans"]), len(x["text"]))
            self.assertEqual(min(p[2] for q in x["strokes"] for p in q), 0)
            for key in ("client", "time", "confirmed", "device"):
                self.assertNotIn(key, x)
        # its own samples; others' only when granted
        r = c.ask("samples")
        self.assertEqual(r[2:], ["2", sid, r2[3]])
        self.assertEqual(json.loads(c.ask("sample", sid)[2].replace("\\t", "\t"))["text"], "cat")
        c.close()
        o = self.env.start("-i", "0")
        self.assertEqual(o.ask("samples")[2:], ["no-client"])
        o.ask("hello", "1", "org.example.fonts")
        self.assertEqual(o.ask("samples")[2:], ["0"])
        self.assertEqual(o.ask("sample", sid)[2:], ["denied"])
        self.assertEqual(o.ask("samples", "all")[2:], ["denied"])
        self.env.conf_set("handwriting.conf", "readers = org.example.fonts\n")
        self.assertEqual(o.ask("samples", "all")[2], "2")
        self.assertEqual(o.ask("sample", sid)[0], "sample")
        o.close()
        out = self.env.run("samples", "stats").stdout
        self.assertIn("samples\t2\n", out)
        self.assertIn("client\torg.example.keyboard\t2\n", out)

    def test_erase_export_import(self):
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        for i in range(3):
            self.assertEqual(self.confirm(c, INK[i], session=f"s{i}")[2], "ok")
        c.ask("hello", "2", "org.example.notes")
        self.assertEqual(self.confirm(c, INK[5], session="n")[2], "ok")
        c.close()
        ad = self.env.store / "adaptation" / "en"
        ad.mkdir(parents=True)
        (ad / "current").write_bytes(b"GHWA-test-current")
        before = self.env.run("samples", "list").stdout
        exp = self.env.dir / "export.jsonl"
        self.env.run("samples", "export", str(exp))
        self.assertEqual(stat.S_IMODE(exp.stat().st_mode), 0o600)
        # erase one client: the others stay, adaptations go
        self.assertEqual(self.env.run("samples", "erase", "-c", "org.example.notes").stdout, "1\n")
        self.assertEqual(len(self.env.run("samples", "list").stdout.splitlines()), 3)
        self.assertFalse(ad.exists())
        # erase everything
        self.assertEqual(self.env.run("samples", "erase").stdout, "3\n")
        self.assertEqual(self.env.run("samples", "list").stdout, "")
        self.assertFalse((self.env.store / "samples.jsonl").exists())
        # import: the same samples, clients and times, and the adaptation
        self.assertEqual(self.env.run("samples", "import", str(exp)).stdout, "4\n")
        self.assertEqual(self.env.run("samples", "list").stdout, before)
        self.assertEqual((ad / "current").read_bytes(), b"GHWA-test-current")
        self.assertEqual(self.env.run("samples", "import", str(exp)).stdout, "0\n")   # idempotent
        # onto another device (a fresh home)
        other = Env()
        try:
            other.run("samples", "import", str(exp))
            self.assertEqual(other.run("samples", "list").stdout, before)
        finally:
            other.close()
        bad = self.env.dir / "bad.jsonl"
        bad.write_text('{"format":"something-else","version":1}\n')
        self.assertNotEqual(self.env.run("samples", "import", str(bad), check=False).returncode, 0)

    def test_store_survives_a_lost_append(self):
        """a crash can leave zero bytes where an append never reached the disk, or a line cut
        short: every other sample stays readable, and the next one starts on its own line"""
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        ids = [self.confirm(c, INK[0], session="a")[3]]
        st = self.env.store
        with open(st / "samples.jsonl", "ab") as f:
            f.write(b"\0" * 3688)                 # an append lost in a crash
        ids.append(self.confirm(c, INK[1], session="b")[3])
        with open(st / "samples.jsonl", "ab") as f:
            f.write(b'{"version":1,"text":"cut sh')  # a line cut short
        ids.append(self.confirm(c, INK[2], session="c")[3])
        self.assertEqual(c.ask("samples")[2:], ["3", *ids])
        c.close()
        self.assertIn("samples\t3\n", self.env.run("samples", "stats").stdout)
        self.assertEqual([x.split("\t")[0] for x in self.env.run("samples", "list").stdout.splitlines()], ids)
        # a delete rewrites the file without the damage; a running recognizer sees it
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        self.assertEqual(c.ask("samples")[2:], ["3", *ids])
        self.env.run("samples", "delete", ids[0])
        self.assertNotIn(b"\0", (st / "samples.jsonl").read_bytes())
        self.assertEqual(c.ask("samples")[2:], ["2", *ids[1:]])
        self.assertEqual(c.ask("sample", ids[0])[2:], ["unknown-sample"])
        c.close()
        self.assertEqual([x.split("\t")[0] for x in self.env.run("samples", "list").stdout.splitlines()], ids[1:])

    def test_erase_with_nothing_stored(self):
        self.assertEqual(self.env.run("samples", "erase").stdout, "0\n")
        self.assertFalse(self.env.store.exists())

    def test_donation(self):
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.donate")
        c.write_sample(INK[1])
        self.assertEqual(c.ask("donate", "s", "hello")[2:], ["skip", "off"])
        self.assertEqual(self.env.run("donation", "start", "MIT", check=False).returncode, 1)
        self.env.run("donation", "start", "CC-BY-4.0")
        # only texts shipped as prompts (the model's prompts.txt, the app's sets)
        self.assertEqual(c.ask("donate", "s", "my own words")[2:], ["skip", "prompt"])
        # the random model's alignment never fits: kept only when the person says so
        self.assertEqual(c.ask("donate", "s", "hello")[2:4], ["skip", "fit"])
        r = c.ask("donate", "s", "hello", "keep")
        self.assertEqual(r[2:4], ["ok", "1"])
        self.assertRegex(r[4], "^[0-9a-f]{16}$")
        r = self.confirm(c, INK[2], session="mine")
        c.close()
        self.assertEqual(self.env.run("samples", "list").stdout.count("\n"), 1)   # donations aren't samples
        self.env.run("donation", "add", r[3])            # one of the person's own, picked
        out = self.env.dir / "donation.jsonl"
        self.env.run("donation", "write", str(out))
        rows = [json.loads(x) for x in out.read_text().splitlines()]
        self.assertEqual([x["text"] for x in rows], ["hello", INK[2]["text"]])
        for x in rows:
            self.assertEqual(x["licence"], "CC-BY-4.0")
            self.assertEqual(x["version"], 1)
            self.assertEqual(min(p[2] for q in x["strokes"] for p in q), 0)
            for key in ("client", "time", "confirmed", "device", "id", "fonts", "provenance"):
                self.assertNotIn(key, x)
        self.assertIn("The quick brown fox", self.env.run("donation", "prompts").stdout)
        self.env.run("donation", "erase")
        self.assertEqual(self.env.run("samples", "list").stdout.count("\n"), 1)   # erasing a donation keeps samples
        self.assertIn("samples\t0", self.env.run("donation", "list").stdout)

    def test_forget(self):
        """a discarded sample goes from the samples and from the donation; only the client's own"""
        self.env.run("donation", "start", "CC0-1.0")
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        c.write_sample(INK[1])
        r = c.ask("confirm", "s", "prompted", "hello", "keep")
        self.assertEqual(r[2], "ok")
        sid = r[3]
        d = c.ask("donate", "s", "hello", "keep")
        self.assertEqual(d[4], sid)          # the same sample, the same id
        self.assertEqual(c.ask("forget", sid)[2:], ["ok", "samples,donation"])
        self.assertEqual(c.ask("forget", sid)[2:], ["unknown-sample"])
        self.assertEqual(self.env.run("samples", "list").stdout, "")
        self.assertIn("samples\t0", self.env.run("donation", "list").stdout)
        gen = (self.env.store / "samples.meta").read_text().split("\n")[0]
        self.assertEqual(c.ask("forget", "0123456789abcdef")[2:], ["unknown-sample"])
        self.assertEqual((self.env.store / "samples.meta").read_text().split("\n")[0], gen)   # nothing changed
        c.close()
        o = self.env.start("-i", "0")
        o.ask("hello", "1", "org.example.other")
        o.write_sample(INK[2])
        mine = o.ask("confirm", "s", "picked", esc(INK[2]["text"]))[3]
        o.close()
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        self.assertEqual(c.ask("forget", mine)[2:], ["denied"])
        c.close()
        self.env.run("samples", "delete", mine)
        self.assertEqual(self.env.run("samples", "list").stdout, "")
        self.assertEqual(self.env.run("samples", "delete", mine, check=False).returncode, 1)

    def test_export_one_client(self):
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        for s in INK[:3]:
            c.write_sample(s)
            self.assertEqual(c.ask("confirm", "s", "prompted", esc(s["text"]), "keep")[2], "ok")
        c.close()
        o = self.env.start("-i", "0")
        o.ask("hello", "1", "org.example.other")
        o.write_sample(INK[4])
        o.ask("confirm", "s", "picked", esc(INK[4]["text"]))
        o.close()
        out = self.env.dir / "mine.export"
        self.env.run("samples", "export", "-c", "org.example.samples", str(out))
        rows = [json.loads(x) for x in out.read_text().splitlines()]
        self.assertEqual(rows[0]["format"], "glassine-handwriting-export")
        self.assertEqual({r["client"] for r in rows[1:]}, {"org.example.samples"})
        self.assertEqual(len(rows), 4)
        self.assertFalse(any("adaptation" in r for r in rows))
        before = {json.dumps(r["sample"], sort_keys=True) for r in rows[1:]}
        self.env.run("samples", "erase", "-c", "org.example.samples")
        self.assertEqual(self.env.run("samples", "import", str(out)).stdout, "3\n")
        self.env.run("samples", "export", "-c", "org.example.samples", str(out))
        after = {json.dumps(r["sample"], sort_keys=True) for r in map(json.loads, out.read_text().splitlines()[1:])}
        self.assertEqual(before, after)


class Prompted(unittest.TestCase):
    """prompted writing (spec 002): raw points kept as sent, the check of the alignment,
    the writing area and the reading at the time of writing"""

    def setUp(self):
        self.env = Env(REAL)

    def tearDown(self):
        self.env.close()

    def client(self, fit_ok=None):
        if fit_ok is not None:
            self.env.env["GLASSINE_HANDWRITING_FIT_OK"] = str(fit_ok)
        else:
            self.env.env.pop("GLASSINE_HANDWRITING_FIT_OK", None)
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        return c

    def test_points_unaltered(self):
        """every value as the client sent it: surface fractions, pressure, tilt, a far time origin"""
        rng = random.Random(7)
        c = self.client(fit_ok=0)
        self.assertEqual(c.ask("begin", "s", "en", "normal", "device:7.5:240", "")[2], "ok")
        self.assertEqual(c.ask("area", "s", "0,0,1404,300,96,90")[2:], ["ok"])
        t = 1731658123456
        sent = []
        for k in range(4):
            q = []
            for i in range(40):
                q.append((rng.randrange(0, 1404 * 256) / 256, rng.randrange(0, 300 * 256) / 256, t,
                          rng.randrange(0, 65536) / 65535, rng.randrange(-9000, 9000) / 100, rng.randrange(-9000, 9000) / 100))
                t += rng.choice((4, 5))
            t += 200
            sent.append(q)
            self.assertEqual(c.ask("stroke", "s", " ".join(",".join(repr(v) for v in p) for p in q))[2], "ok")
        r = c.ask("confirm", "s", "prompted", "hello")
        self.assertEqual((r[2], r[4]), ("ok", "exact"))
        smp = json.loads(c.ask("sample", r[3])[2].replace("\\\\", "\\"))
        c.close()
        t0 = sent[0][0][2]
        self.assertEqual(smp["units"], {"xy": "device", "per_mm": 7.5, "rate": 240})
        self.assertEqual((smp["source"], smp["spans_source"], smp["text"]), ("prompted", "exact", "hello"))
        self.assertEqual(len(smp["spans"]), 5)
        self.assertEqual(smp["area"], {"box": [0, 0, 1404, 300], "line": 96, "orientation": 90})
        self.assertLessEqual(len(smp["reading"]), 3)
        for x in smp["reading"]:
            self.assertIsInstance(x["text"], str)
            self.assertLessEqual(x["score"], 0)
        self.assertEqual(len(smp["strokes"]), len(sent))
        for q, got in zip(sent, smp["strokes"]):
            self.assertEqual(len(q), len(got))
            for p, g in zip(q, got):
                self.assertEqual(g[0:2], list(p[0:2]))
                self.assertEqual(g[2], round((p[2] - t0) / 1000, 6))
                self.assertEqual(g[3:6], list(p[3:6]))

    def test_check_and_keep(self):
        """a sample that fails the alignment check is kept only when the person keeps it"""
        c = self.client()
        c.write_sample(INK[0], units="em")
        if not REAL:            # the random model's alignment fits nothing
            self.assertEqual(c.ask("confirm", "s", "prompted", esc(INK[0]["text"]))[2:4], ["skip", "fit"])
        self.assertEqual(c.ask("confirm", "s", "prompted", "dog")[2:4], ["skip", "fit"])
        r = c.ask("confirm", "s", "prompted", "dog", "keep")
        self.assertEqual((r[2], r[4]), ("ok", "inferred"))
        if REAL:                 # the trained model: the written text passes
            self.assertEqual(c.ask("confirm", "s", "prompted", esc(INK[0]["text"]))[4], "exact")
        self.assertEqual(c.ask("confirm", "s", "prompted", "x", "please")[2:], ["bad-line"])
        self.assertEqual(c.ask("area", "s", "0,0,10,10,1,45")[2:], ["bad-area"])
        self.assertEqual(c.ask("area", "s", "10,0,0,10,1,0")[2:], ["bad-area"])
        self.assertEqual(c.ask("area", "nosuch", "0,0,10,10,1,0")[2:], ["unknown-session"])
        # other sources keep their old reply and carry no reading
        r = c.ask("confirm", "s", "corrected", esc(INK[0]["text"]))
        self.assertEqual(len(r), 4)
        smp = json.loads(c.ask("sample", r[3])[2].replace("\\\\", "\\"))
        self.assertNotIn("reading", smp)
        self.assertEqual(smp["spans_source"], "inferred")
        c.close()


def shapes(ch, rng):
    """a few clearly different shapes (pen units, 10 per mm): a random model's frames
    separate them, so a head can learn them in seconds"""
    s = 1 + rng.uniform(-.2, .2)
    ox, oy = rng.uniform(-50, 50), rng.uniform(-50, 50)
    t = [0.0]

    def stroke(xy):
        out = []
        for x, y in xy:
            out.append(f"{ox + x * s * 100 + rng.gauss(0, 1):.1f},{oy + y * s * 100 + rng.gauss(0, 1):.1f},{t[0]:.0f}")
            t[0] += 5
        t[0] += 150
        return " ".join(out)

    n = 30
    if ch == "o":
        return [stroke([(0.5 + 0.5 * math.cos(2 * math.pi * i / n), 0.5 + 0.5 * math.sin(2 * math.pi * i / n)) for i in range(n + 1)])]
    if ch == "l":
        return [stroke([(0.5, i / n) for i in range(n + 1)])]
    if ch == "x":
        return [stroke([(i / n, i / n) for i in range(n + 1)]), stroke([(1 - i / n, i / n) for i in range(n + 1)])]
    return [stroke([(i / 10, 0) for i in range(11)] + [(1 - i / 10, i / 10) for i in range(11)] + [(i / 10, 1) for i in range(11)])]


class Adaptation(unittest.TestCase):
    ARGS = ["-f", "--min-train", "20", "--min-held", "5", "--epochs", "20", "--lr", "1e-2"]

    def setUp(self):
        self.env = Env()
        self.rng = random.Random(3)

    def tearDown(self):
        self.env.close()

    def teach(self, n):
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        for i in range(n):
            ch = "olxz"[i % 4]
            c.ask("begin", "s", "en", "normal", "device:10", "")
            for q in shapes(ch, self.rng):
                c.ask("stroke", "s", q)
            self.assertEqual(c.ask("confirm", "s", "picked", ch)[2], "ok")
        c.close()

    def test_kept_then_rejected(self):
        cur = self.env.store / "adaptation" / "en" / "current"
        prev = self.env.store / "adaptation" / "en" / "previous"
        self.teach(150)
        out = self.env.run("adapt", *self.ARGS).stdout
        self.assertIn("kept", out)
        self.assertNotIn("not kept", out)
        good = cur.read_bytes()
        show = self.env.run("adaptation", "show").stdout.splitlines()
        self.assertTrue(show[0].startswith("current\t") and show[1] == "previous\tnone", show)
        cer_before, cer_after = map(float, show[0].split("\t")[4:6])
        self.assertLess(cer_after, cer_before)
        self.assertLess(cer_after, 0.5)
        # nothing new: not run again unless forced
        self.assertIn("nothing new", self.env.run("adapt", *self.ARGS[1:]).stdout)
        # an adaptation that reads the held-back samples worse (here: a learning rate that
        # wrecks it) is not used, and the one in use stays as it was
        self.teach(40)
        out = self.env.run("adapt", "-f", "--min-train", "20", "--min-held", "5", "--epochs", "5", "--lr", "50").stdout
        self.assertIn("not kept", out)
        self.assertEqual(cur.read_bytes(), good)
        self.assertFalse(prev.exists())
        log = (self.env.store / "adaptation" / "en" / "log").read_text().split("\n")
        self.assertEqual([x.split(" ")[1] for x in log if x], ["kept", "rejected"])
        self.assertEqual(stat.S_IMODE(cur.stat().st_mode), 0o600)
        self.env.run("samples", "erase")
        self.assertFalse(cur.exists())

    def test_previous_and_revert(self):
        cur = self.env.store / "adaptation" / "en" / "current"
        prev = self.env.store / "adaptation" / "en" / "previous"
        self.teach(120)
        out = self.env.run("adapt", "-f", "--min-train", "20", "--min-held", "5", "--epochs", "16", "--lr", "1e-2").stdout
        self.assertNotIn("not kept", out)
        first = cur.read_bytes()
        self.teach(20)
        out = self.env.run("adapt", "-f", "--min-train", "20", "--min-held", "5", "--epochs", "40", "--lr", "1e-2").stdout
        self.assertNotIn("not kept", out)
        self.assertEqual(prev.read_bytes(), first)
        second = cur.read_bytes()
        self.env.run("adaptation", "revert")
        self.assertEqual((cur.read_bytes(), prev.read_bytes()), (first, second))

    def test_conditions(self):
        self.teach(20)
        self.env.charging(False)
        self.assertIn("waiting", self.env.run("adapt", "--min-train", "20", "--min-held", "5").stdout)
        self.env.charging(True)
        (self.env.dir / "load").write_text("3.5 2.0 1.0 1/100 1\n")
        self.assertIn("waiting", self.env.run("adapt", "--min-train", "20", "--min-held", "5").stdout)
        (self.env.dir / "load").write_text("0.1 0.1 0.1 1/100 1\n")
        self.assertIn("too few", self.env.run("adapt", "-f").stdout)
        self.env.conf_set("predict.conf", "learning = off\n")
        self.assertIn("learning is off", self.env.run("adapt", "-f").stdout)
        self.assertFalse((self.env.store / "adaptation" / "en" / "current").exists())

    def test_trained_until(self):
        """samples confirmed before the base model's training data ended are left out of an
        adaptation: not learned from, not held back, not in the plan"""
        ghm = self.env.models / "en" / "recognizer.ghm"
        raw = bytearray(ghm.read_bytes())

        def until(t):
            raw[48:56] = int(t).to_bytes(8, "little")
            ghm.write_bytes(raw)
        self.teach(60)
        until(time.time() + 3600)
        out = self.env.run("adapt", *self.ARGS).stdout
        self.assertIn("too few samples (0 to learn from, 0 held back", out)
        plan = self.env.run("adapt", "--plan", "--min-train", "20", "--min-held", "5").stdout.split("\t")
        self.assertEqual(plan[1:3], ["0", "0"])
        until(1)  # long before them: all count again
        out = self.env.run("adapt", *self.ARGS).stdout
        self.assertNotIn("too few", out)

    def test_requested_with_progress(self):
        """asked for in a client (spec 002): charging is enough, the plan and progress come as
        lines, the result has the held-back errors before and after"""
        self.teach(100)
        plan = self.env.run("adapt", "--plan", "--min-train", "20", "--min-held", "5").stdout.split("\t")
        self.assertEqual(plan[0], "plan")
        train, held, fresh = map(int, plan[1:4])
        self.assertEqual((train + held, fresh), (100, 100))
        self.assertEqual(plan[4:], ["20", "5", "charging\n"])
        (self.env.dir / "load").write_text("3.5 2.0 1.0 1/100 1\n")      # busy: requested runs anyway
        self.env.charging(False)
        self.assertEqual(self.env.run("adapt", "-r", "--progress").stdout, "skip\tcharging\n")
        self.assertEqual(self.env.run("charging").stdout, "battery\n")
        self.env.charging(True)
        self.assertEqual(self.env.run("charging").stdout, "charging\n")
        out = self.env.run("adapt", "-r", "--progress", "--min-train", "20", "--min-held", "5", "--epochs", "20",
                           "--lr", "1e-2").stdout.splitlines()
        self.assertEqual(out[0].split("\t")[0], "plan")
        self.assertEqual([x for x in out if x.startswith("epoch")][-1], "epoch\t20\t20")
        res = out[-1].split("\t")
        self.assertEqual(res[0:2], ["result", "kept"])
        self.assertLess(float(res[3]), float(res[2]))
        self.assertEqual(int(res[4]) + int(res[5]), 100)
        plan = self.env.run("adapt", "--plan").stdout.split("\t")
        self.assertEqual(plan[3], "0")                       # nothing since
        # a deletion is not "new", but it is a change
        sid = self.env.run("samples", "list").stdout.split("\t")[0]
        self.env.run("samples", "delete", sid)
        self.assertEqual(self.env.run("adapt", "--plan").stdout.split("\t")[3], "0")
        self.teach(3)
        self.assertEqual(self.env.run("adapt", "--plan").stdout.split("\t")[3], "3")

    def test_erase_during_adaptation(self):
        """an adaptation that started before an erase never writes what it learned"""
        self.teach(150)
        p = subprocess.Popen([str(BIN), "adapt", "-f", "--min-train", "20", "--min-held", "5", "--epochs", "400"],
                             env=self.env.env, stdout=subprocess.PIPE, text=True)
        time.sleep(1.0)
        self.env.run("samples", "erase")
        out, _ = p.communicate(timeout=300)
        self.assertIn("erased meanwhile", out)
        self.assertFalse((self.env.store / "adaptation").exists())


class Serving(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_idle_exit_and_background_adaptation(self):
        """stops after the idle time; the client sees the end at once while the adaptation
        runs on, detached"""
        rng = random.Random(5)
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        for i in range(60):
            c.ask("begin", "s", "en", "normal", "device:10", "")
            for q in shapes("olxz"[i % 4], rng):
                c.ask("stroke", "s", q)
            c.ask("confirm", "s", "picked", "olxz"[i % 4])
        c.close()
        t = time.time()
        p = subprocess.Popen([str(BIN), "-i", "1"], env=self.env.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True)
        p.stdin.write("hello\th\t1\n")
        p.stdin.flush()
        self.assertEqual(p.stdout.readline(), "hello\th\t1\n")
        self.assertEqual(p.stdout.read(), "")     # EOF: it stopped, and nothing holds the pipe
        self.assertLess(time.time() - t, 10)
        self.assertEqual(p.wait(timeout=10), 0)
        p.stdin.close()
        p.stdout.close()
        state = self.env.store / "adaptation" / "en" / "state"
        for _ in range(200):
            if state.exists():
                break
            time.sleep(0.1)
        self.assertTrue(state.exists(), "the background adaptation ran")

    def test_signals(self):
        c = self.env.start("-i", "0")
        c.ask("hello", "1")
        c.p.send_signal(signal.SIGTERM)
        self.assertEqual(c.p.wait(timeout=10), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
