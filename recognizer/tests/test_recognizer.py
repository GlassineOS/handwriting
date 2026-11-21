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
import threading
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


def ast_chars():
    """the trainer's character set (text.py CHARS)"""
    import ast
    src = (HERE.parent.parent / "train" / "src" / "glassine_train" / "text.py").read_text()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CHARS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("text.py: no CHARS")


def pts(stroke, t0=0.0):
    """sample-format points (t in s) -> the contract's POINTS (t in ms)"""
    return " ".join(f"{p[0]:.4f},{p[1]:.4f},{(p[2] - t0) * 1000:.1f}" for p in stroke)


def fnv(b):
    """a sample's id: the first 16 hex digits of the 64-bit FNV-1a of its line"""
    h = 1469598103934665603
    for x in b:
        h = ((h ^ x) * 1099511628211) & (2 ** 64 - 1)
    return "%016x" % h


def esc(s):
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def unesc(s):
    return s.replace("\\t", "\t").replace("\\n", "\n").replace("\\r", "\r").replace("\\\\", "\\")


class Env:
    """A model directory and a person's home of their own (ref: a random model reading the
    reference-height features, ghm version 2)."""

    def __init__(self, model_dir=None, ref=False):
        self.dir = Path(tempfile.mkdtemp(prefix="hw-test-"))
        self.models = self.dir / "models"
        (self.models / "en").mkdir(parents=True)
        if model_dir:
            shutil.copy(Path(model_dir) / "en" / "recognizer.ghm", self.models / "en" / "recognizer.ghm")
        else:
            subprocess.run([sys.executable, str(HERE / "mkmodel.py"), str(self.models / "en" / "recognizer.ghm")]
                           + (["--ref"] if ref else []), check=True)
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
        # a random model's readings, compared exactly: no look-alike lean (test-engine tests it)
        self.env["GLASSINE_HANDWRITING_LOOKALIKE"] = "off"
        self.env["GLASSINE_HANDWRITING_BASE"] = "off"          # no Autograph base unless a test gives one

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

    def chars(self, lines, order=4, lang="en"):
        """a character model (gclm, the trainer's charlm.py) counted from these lines"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "charlm", HERE.parent.parent / "train" / "src" / "glassine_train" / "charlm.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        cs = ast_chars()
        data = m.encode(m.prune(m.count(lines, order), 1), cs, order)
        (self.models / lang / "chars.gclm").write_bytes(data)

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
        # (written from a thread: the replies fill the pipe before the lines are all in)
        w = threading.Thread(target=self.c.send, args=[f"clear\t{i}\tz" for i in range(6000)])
        w.start()
        got = [self.c.get() for _ in range(6000)]
        w.join()
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
        # writing (contract 1.11): the same strike read as writing, kept in the session
        self.c.ask("begin", "w", "en", "normal", "device:10", "")
        self.c.ask("stroke", "w", " ".join(f"{100 + 4 * i},{200 + (i % 2)},{5 * i}" for i in range(40)))
        r = self.c.ask("read", "w", "3", "detail", "writing")
        self.assertEqual(r[2], "text", r)
        self.assertEqual(self.c.ask("read", "w", "1", "writing")[2], "text")
        self.assertEqual(self.c.ask("read", "w")[2:4], ["gesture", "strike"])   # it stayed
        self.assertEqual(self.c.ask("read", "w", "1", "writing", "detail")[2:], ["bad-line"])

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

    def test_chars(self):
        """the character model weighs the readings in prose, name and letter fields only
        (FR-019): with a model that likes the ink's other readings, those fields read
        differently from `read … raw`; `read … chars` reads with it and without words;
        every other field, a missing model and the setting off read as raw"""
        c = self.env.start("-i", "0")
        raws = []
        for s in INK[:6]:
            c.write_sample(s)
            raws.append([t for t, _, _ in parse_text(c.ask("read", "s", "8", "raw"))])
            c.ask("end", "s")
        c.close()
        self.env.env["GLASSINE_HANDWRITING_CHARS"] = "weight=3,floor=-40,margin=0"

        def reads(field, *how):
            c = self.env.start("-i", "0")
            out = []
            for s in INK[:6]:
                c.write_sample(s, field=field)
                out.append([t for t, _, _ in parse_text(c.ask("read", "s", "8", *how))])
                self.assertEqual(c.ask("read", "s", "8", "raw", "chars")[2], "bad-line")
                self.assertEqual(c.ask("read", "s", "8", "chars", "raw")[2], "bad-line")
                self.assertEqual(c.ask("read", "s", "8", "detail", "chars")[2], "bad-line")
                c.ask("end", "s")
            c.close()
            return out

        self.assertEqual(reads("normal"), raws, "no character model: as raw")
        self.assertEqual(reads("normal", "chars"), raws, "no character model: chars as raw")
        # the ink's other readings, many times: the model likes them
        self.env.chars([t for r in raws for t in r[1:] if t] * 20)
        prose = reads("normal")
        self.assertNotEqual(prose, raws, "the character model changes a prose reading")
        self.assertEqual(reads("normal", "raw"), raws, "read … raw: the ink alone")
        self.assertEqual(reads("normal", "chars"), prose, "no words: chars reads as the field does")
        for field in ("-", "alpha", "name", "normal,lowercase"):
            self.assertEqual(reads(field), prose, field)
        for field in ("digits", "number", "phone", "url", "email", "terminal", "date", "time",
                      "normal,someunknownhint", "someunknownpurpose"):
            self.assertEqual(reads(field), raws, field)
            self.assertEqual(reads(field, "chars"), raws, field + " chars")
        self.env.conf_set("handwriting.conf", "chars = off\n")
        self.assertEqual(reads("normal"), raws, "chars = off")
        self.env.conf_set("handwriting.conf", "")
        (self.env.models / "en" / "chars.gclm").write_bytes(b"GHCM not a model")
        self.assertEqual(reads("normal"), raws, "a broken model: as raw")

    def test_chars_best_always_offered(self):
        """the character model's search's best reading is among the candidates also when the
        ink reads it far less well than its best (beyond the near gate), never first, within
        COUNT"""
        c = self.env.start("-i", "0")
        raws = []
        for s in INK[:6]:
            c.write_sample(s)
            raws.append([t for t, _, _ in parse_text(c.ask("read", "s", "8", "raw"))])
            c.ask("end", "s")
        c.close()
        self.env.chars([t for r in raws for t in r[1:] if t] * 20)
        self.env.env["GLASSINE_HANDWRITING_CHARS"] = "weight=3,floor=-40,margin=0"
        self.env.env["GLASSINE_HANDWRITING_WORDS"] = "near=-1e9"     # every reading but the ink's own is cut
        c = self.env.start("-i", "0")
        seen = 0
        for s, raw in zip(INK[:6], raws):
            c.write_sample(s)
            r = c.ask("read", "s", "3", "detail")
            n = int(r[3])
            cands = [(r[4 + 5 * i], r[7 + 5 * i]) for i in range(n)]
            self.assertLessEqual(n, 3)
            self.assertIn(cands[0][1], ("ink", "beam"), "a reading of the ink first")
            chars = [i for i, (t, f) in enumerate(cands) if f == "chars"]
            self.assertLessEqual(len(chars), 1)
            if chars:
                self.assertGreater(chars[0], 0)
                seen += 1
            one = c.ask("read", "s", "1", "detail")
            self.assertEqual((one[3], one[4]), ("1", cands[0][0]), "COUNT 1: the first alone")
            c.ask("end", "s")
        c.close()
        self.assertGreater(seen, 0, "the character model's best offered beyond the gate")

    def test_pick_from_chars_must_fit(self):
        """a pick the character model put first (FROM chars) makes a sample only when its
        strokes fit it; `detail` says where it came from"""
        c = self.env.start("-i", "0")
        raws = []
        for s in INK[:6]:
            c.write_sample(s)
            raws.append([t for t, _, _ in parse_text(c.ask("read", "s", "8", "raw"))])
            c.ask("end", "s")
        c.close()
        self.env.chars([t for r in raws for t in r[1:] if t] * 20)
        self.env.env["GLASSINE_HANDWRITING_CHARS"] = "weight=3,floor=-40,margin=0"
        self.env.env["GLASSINE_HANDWRITING_FIT_OK"] = "2"   # no fit passes
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.app")
        seen = 0
        for s, raw in zip(INK[:6], raws):
            c.write_sample(s)
            r = c.ask("read", "s", "8", "detail")
            n = int(r[3])
            cands = [(r[4 + 5 * i], r[7 + 5 * i], r[8 + 5 * i]) for i in range(n)]
            for text, frm, ok in cands:
                if frm == "chars":
                    self.assertNotEqual(text, raw[0], "the ink's best is not the character model's")
                    self.assertEqual(ok, "check")
                    self.assertEqual(c.ask("confirm", "s", "picked", esc(text))[2:4], ["skip", "fit"])
                    seen += 1
                    break
            c.ask("end", "s")
        self.assertGreater(seen, 0, "the character model put a reading among the candidates")
        self.assertFalse(self.env.store.exists())
        c.close()

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

    def test_pick_from_chars_reads_back(self):
        """a sample picked from the character model's search (`pick` from `chars`) is a sample
        like any other: it imports and is read back (adaptation, size, letters use it)"""
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        self.assertEqual(self.confirm(c, INK[0])[2], "ok")
        c.close()
        exp = self.env.dir / "export.jsonl"
        self.env.run("samples", "export", str(exp))
        head, line = exp.read_text().splitlines()[:2]
        o = json.loads(line)
        o["sample"]["source"] = "confirmed"
        o["sample"]["pick"] = {"rank": 2, "from": "chars"}
        f = self.env.dir / "chars-pick.jsonl"
        f.write_text(head + "\n" + json.dumps(o, separators=(",", ":")) + "\n")
        other = Env()
        try:
            self.assertEqual(other.run("samples", "import", str(f)).stdout, "1\n")
            sid = other.run("samples", "list").stdout.split("\t")[0]
            c = other.start("-i", "0")
            c.ask("hello", "1", "org.example.keyboard")
            smp = json.loads(unesc(c.ask("sample", sid)[2]))
            c.close()
            self.assertEqual(smp["pick"], {"rank": 2, "from": "chars"})
        finally:
            other.close()

    def test_impossible_reading_candidate(self):
        """a candidate the reading scored minus infinity (written as -inf, not JSON, by older
        versions) is left out; the sample still imports"""
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        self.assertEqual(self.confirm(c, INK[0])[2], "ok")
        c.close()
        exp = self.env.dir / "export.jsonl"
        self.env.run("samples", "export", str(exp))
        head, line = exp.read_text().splitlines()[:2]
        o = json.loads(line)
        o["sample"]["reading"] = [{"text": "cat", "score": -1.5}, {"text": "__", "score": -7.0}]
        line = json.dumps(o, separators=(",", ":")).replace('"score":-7.0', '"score":-inf')
        self.assertIn("-inf", line)
        bad = self.env.dir / "inf.jsonl"
        bad.write_text(head + "\n" + line + "\n")
        other = Env()
        try:
            self.assertEqual(other.run("samples", "import", str(bad)).stdout, "1\n")
            again = other.dir / "again.jsonl"
            other.run("samples", "export", str(again))
            r = json.loads(again.read_text().splitlines()[1])["sample"]["reading"]
            self.assertEqual(r, [{"text": "cat", "score": -1.5}])
        finally:
            other.close()

    def test_marks_and_preferences(self):
        """spec 003: a session's marking goes into the samples it keeps; a preference goes with
        its samples: listed, removed with either, exported and imported with both, donated
        only with both, naming the samples by their ids in that file"""
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        pair = "0123456789abcdef"
        c.write_sample(INK[0], session="q")
        self.assertEqual(c.ask("mark", "q", "sloppy", "-")[2:], ["bad-mark"])
        self.assertEqual(c.ask("mark", "q", "quick", "x" * 16)[2:], ["bad-mark"])
        self.assertEqual(c.ask("mark", "q", "quick", pair)[2:], ["ok"])
        q = c.ask("confirm", "q", "picked", esc(INK[0]["text"]))[3]
        c.write_sample(INK[1], session="k")
        c.ask("mark", "k", "careful", pair)
        k = c.ask("confirm", "k", "picked", esc(INK[1]["text"]))[3]
        plain = self.confirm(c, INK[2], session="p")[3]
        line = lambda sid: json.loads(unesc(c.ask("sample", sid)[2]))
        self.assertEqual((line(q)["written"], line(q)["pair"]), ("quick", pair))
        self.assertEqual((line(k)["written"], line(k)["pair"]), ("careful", pair))
        self.assertFalse({"written", "pair"} & set(line(plain)))
        self.assertEqual(c.ask("marked")[2:], ["2", q, "quick", pair, k, "careful", pair])
        # preferences: of the client's own samples, a character by its place in the text
        self.assertEqual(c.ask("prefer", k, q)[2:], ["ok"])
        self.assertEqual(c.ask("prefer", k, plain, "0", "1")[2:], ["ok"])
        self.assertEqual(c.ask("prefer", k, plain, "99", "0")[2:], ["bad-char"])
        self.assertEqual(c.ask("prefer", k, k)[2:], ["bad-char"])
        self.assertEqual(c.ask("prefer", k, "f" * 16)[2:], ["unknown-sample"])
        self.assertEqual(c.ask("preferences")[2:], ["2", k, q, "-", "-", k, plain, "0", "1"])
        o = self.env.start("-i", "0")
        o.ask("hello", "1", "org.example.other")
        self.assertEqual(o.ask("prefer", q, k)[2:], ["denied"])
        self.assertEqual(o.ask("preferences")[2:], ["0"])
        self.assertEqual(o.ask("marked")[2:], ["0"])
        o.close()
        c.close()
        # the index made again from the file keeps the marking
        (self.env.store / "samples.index").unlink()
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        self.assertEqual(c.ask("marked")[2:], ["2", q, "quick", pair, k, "careful", pair])
        c.close()
        # a donation holds a preference only with both of its samples
        self.env.run("donation", "start", "CC0-1.0")
        self.env.run("donation", "add", k)
        self.env.run("donation", "add", q)
        out = self.env.dir / "donation.jsonl"
        self.env.run("donation", "write", str(out))
        lines = out.read_text().splitlines()
        rows = [json.loads(x) for x in lines]
        smp = [x for x in rows if "preference" not in x]
        prefs = [x["preference"] for x in rows if "preference" in x]
        self.assertEqual({(x["written"], x["pair"]) for x in smp}, {("quick", pair), ("careful", pair)})
        ids = {fnv(x.encode()): json.loads(x) for x in lines if '"preference"' not in x}
        self.assertEqual(len(prefs), 1)
        self.assertEqual((ids[prefs[0]["chosen"]]["written"], ids[prefs[0]["other"]]["written"]), ("careful", "quick"))
        self.assertNotIn("chosen_char", prefs[0])
        # export and import carry them with their samples
        exp = self.env.dir / "export.jsonl"
        self.env.run("samples", "export", str(exp))
        other = Env()
        try:
            other.run("samples", "import", str(exp))
            oc = other.start("-i", "0")
            oc.ask("hello", "1", "org.example.samples")
            self.assertEqual(oc.ask("preferences")[2:], ["2", k, q, "-", "-", k, plain, "0", "1"])
            self.assertEqual(oc.ask("marked")[2:], ["2", q, "quick", pair, k, "careful", pair])
            oc.close()
        finally:
            other.close()
        # deleting a sample takes its preferences; its pair partner stays
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        self.assertEqual(c.ask("forget", q)[2:], ["ok", "samples,donation"])
        self.assertEqual(c.ask("preferences")[2:], ["1", k, plain, "0", "1"])
        self.assertEqual(c.ask("marked")[2:], ["1", k, "careful", pair])
        c.close()
        self.env.run("samples", "erase", "-c", "org.example.samples")
        self.assertFalse((self.env.store / "preferences").read_text().strip() if (self.env.store / "preferences").exists() else "")

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
        # a delete leaves the damage as it is (no sample in it); a running recognizer sees it
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        self.assertEqual(c.ask("samples")[2:], ["3", *ids])
        self.env.run("samples", "delete", ids[0])
        self.assertEqual(c.ask("samples")[2:], ["2", *ids[1:]])
        self.assertEqual(c.ask("sample", ids[0])[2:], ["unknown-sample"])
        c.close()
        self.assertEqual([x.split("\t")[0] for x in self.env.run("samples", "list").stdout.splitlines()], ids[1:])

    def kept(self, n, client="org.example.samples", base=0):
        """n samples kept by one client; their ids"""
        c = self.env.start("-i", "0")
        c.ask("hello", "1", client)
        ids = []
        for i in range(n):
            s = dict(INK[i % len(INK)], strokes=[[(p[0] + base + i, *p[1:]) for p in q] for q in INK[i % len(INK)]["strokes"]])
            r = self.confirm(c, s, session="k")
            self.assertEqual(r[2], "ok")
            ids.append(r[3])
            c.ask("end", "k")
        c.close()
        return ids

    def test_list_from_the_index(self):
        """list: per sample its id, time, source, language, text and first reading, the same as
        the command line's list, without reading strokes; an index lost or damaged is made again"""
        ids = self.kept(4)
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        r = c.ask("list")
        self.assertEqual(r[2], "4")
        rows = [r[3 + 6 * i:9 + 6 * i] for i in range(4)]
        self.assertEqual([x[0] for x in rows], ids)
        cli = [x.split("\t") for x in self.env.run("samples", "list").stdout.splitlines()]
        self.assertEqual([(x[0], x[2], x[3], x[4], x[5]) for x in cli], [(x[0], x[1], x[2], x[3], x[4]) for x in rows])
        self.assertEqual(c.ask("list", "all")[2:], ["denied"])
        c.close()
        ix = self.env.store / "samples.index"
        self.assertEqual(stat.S_IMODE(ix.stat().st_mode), 0o600)
        before = self.env.run("samples", "list").stdout
        for damage in (None, b"garbage\n", ix.read_bytes()[:-30]):
            if damage is None:
                ix.unlink()
            else:
                ix.write_bytes(damage)
            self.assertEqual(self.env.run("samples", "list").stdout, before)
        self.assertTrue(ix.read_text().startswith("glassine-handwriting-index 2 "))

    def test_store_of_an_earlier_version(self):
        """a store without an index, and lines an earlier version appended behind the index's
        back, keep working: listed with their client and time"""
        ids = self.kept(2)
        st = self.env.store
        (st / "samples.index").unlink()
        self.assertEqual([x.split("\t")[0] for x in self.env.run("samples", "list").stdout.splitlines()], ids)
        # an earlier version appends a line and its meta, not the index
        line = json.dumps(dict(json.loads((st / "samples.jsonl").read_text().splitlines()[0]), text="older"),
                          separators=(",", ":"))
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        self.assertEqual(c.ask("samples")[2], "2")
        with open(st / "samples.jsonl", "a") as f:
            f.write(line + "\n")
        sid = self.env.run("samples", "list").stdout.splitlines()[-1].split("\t")[0]
        with open(st / "samples.meta", "a") as f:
            f.write(f"{sid}\torg.example.samples\t1700000000\tprompted\ten\n")
        (st / "samples.index").write_text((st / "samples.index").read_text()[:-1])    # and the index cut short
        self.assertEqual(c.ask("samples")[2:], ["3", *ids, sid])
        r = c.ask("list")
        self.assertEqual(r[-6:-2], [sid, "1700000000", "prompted", "en"])
        self.assertEqual(r[-2], "older")
        c.close()

    def test_delete_overwrites_in_place(self):
        """a deleted sample's line is gone from the file when the request returns: cut off at
        the end, else overwritten where it is; the file is written anew once the overwritten
        bytes are a quarter of it; a deletion a crash cut short is finished at the next look"""
        ids = self.kept(6)
        st = self.env.store
        data = lambda: (st / "samples.jsonl").read_bytes()
        size = len(data())
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        lines = data().decode().splitlines()
        self.assertEqual(c.ask("forget", ids[1])[2:], ["ok", "samples"])
        self.assertNotIn(lines[1].encode(), data())
        self.assertEqual(len(data()), size)                       # overwritten where it was
        self.assertEqual(c.ask("forget", ids[5])[2:], ["ok", "samples"])
        self.assertLess(len(data()), size - len(lines[5]))        # cut off the end
        self.assertEqual(c.ask("samples")[2:], ["4", ids[0], *ids[2:5]])
        self.assertEqual(json.loads(unesc(c.ask("sample", ids[3])[2]))["text"], json.loads(lines[3])["text"])
        c.close()
        self.assertEqual([x.split("\t")[0] for x in self.env.run("samples", "list").stdout.splitlines()],
                         [ids[0], *ids[2:5]])
        # a crash after the index (for the next erase count) but before the file: the sample
        # stays, with its client
        ix = st / "samples.index"
        meta = (st / "samples.meta").read_text()
        rows = ix.read_text().splitlines()
        head = rows[0].split(" ")
        head[-1] = str(int(head[-1]) + 1)
        k = next(i for i, x in enumerate(rows) if x.startswith(ids[2] + "\t"))
        f = rows[k].split("\t")
        rows[k] = "-\t%s\t%d" % (f[1], int(f[2]) + 1)
        ix.write_text("\n".join([" ".join(head), *rows[1:]]) + "\n")
        listed = [x.split("\t") for x in self.env.run("samples", "list").stdout.splitlines()]
        self.assertEqual([x[0] for x in listed], [ids[0], *ids[2:5]])
        self.assertEqual({x[1] for x in listed}, {"org.example.samples"})
        # a crash after the file, before the meta: the sample is gone
        line = lines[2].encode()
        b = data()
        at = b.index(line)
        (st / "samples.jsonl").write_bytes(b[:at] + b" " * len(line) + b[at + len(line):])
        self.assertEqual((st / "samples.meta").read_text(), meta)
        self.assertEqual([x.split("\t")[0] for x in self.env.run("samples", "list").stdout.splitlines()],
                         [ids[0], *ids[3:5]])
        # enough blanks: the file is written without them
        big = self.kept(30, base=100)
        for i in big[:-1:2]:
            self.env.run("samples", "delete", i)
        left = [ids[0], *ids[3:5], *big[1::2]]
        self.assertEqual([x.split("\t")[0] for x in self.env.run("samples", "list").stdout.splitlines()], left)
        blank = sum(len(x) + 1 for x in data().splitlines() if not x.strip())
        self.assertLessEqual(blank * 4, len(data()))
        self.assertLess(len(data().splitlines()), len(left) + 15)

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
        self.env.run("donation", "add", r[3])            # once
        self.assertEqual(len(self.env.run("donation", "samples").stdout.split()), 2)
        self.assertEqual(self.env.run("donation", "samples").stdout.split()[1], r[3])
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
        # one donor ID: random (version 4), the same in every sample and every later donation
        donor = self.env.run("donation", "donor").stdout.strip()
        self.assertRegex(donor, "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
        self.assertEqual({x["donor"] for x in rows}, {donor})
        self.assertIn("donor\t" + donor + "\n", self.env.run("donation", "list").stdout)
        f = self.env.store / "donation" / "donor-id"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.env.run("donation", "erase")
        self.assertEqual(self.env.run("donation", "donor").stdout.strip(), donor)     # erasing keeps it
        self.env.run("donation", "start", "CC0-1.0")
        self.env.run("donation", "add", r[3])
        self.env.run("donation", "write", str(out))
        self.assertEqual(json.loads(out.read_text())["donor"], donor)
        new = self.env.run("donation", "donor", "new").stdout.strip()
        self.assertNotEqual(new, donor)
        self.assertEqual(self.env.run("donation", "donor").stdout.strip(), new)
        self.env.run("donation", "write", str(out))
        self.assertEqual(json.loads(out.read_text())["donor"], new)
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



class Size(unittest.TestCase):
    """the ink's size (FR-017): lone marks and case by the person's letters or the line; words
    as without it; the measurements kept beside the samples go with an erase"""

    def setUp(self):
        self.env = Env(REAL)

    def tearDown(self):
        self.env.close()

    def client(self, size=None):
        if size:
            self.env.env["GLASSINE_HANDWRITING_SIZE"] = size
        else:
            self.env.env.pop("GLASSINE_HANDWRITING_SIZE", None)
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.samples")
        return c

    @staticmethod
    def circle(x, y, r, n=24):
        return [(x + r * math.cos(2 * math.pi * i / (n - 1)), y + r * math.sin(2 * math.pi * i / (n - 1)), i * 0.01)
                for i in range(n)]

    @staticmethod
    def line(x0, y0, x1, y1, n=24):
        return [(x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1), i * 0.01) for i in range(n)]

    def read(self, c, strokes, area=None):
        c.ask("begin", "s", "en", "normal", "em", "")
        if area:
            self.assertEqual(c.ask("area", "s", area)[2:], ["ok"])
        for q in strokes:
            c.ask("stroke", "s", pts(q))
        r = parse_text(c.ask("read", "s", "3"))
        c.ask("end", "s")
        return [t for t, _, _ in r]

    def test_area_base(self):
        c = self.client()
        c.ask("begin", "s", "en", "normal", "em", "")
        self.assertEqual(c.ask("area", "s", "0,0,10,4,1,0,0.62")[2:], ["ok"])
        self.assertEqual(c.ask("area", "s", "0,0,10,4,1,0,0.62,1")[2:], ["bad-area"])
        self.assertEqual(c.ask("area", "s", "0,0,10,4,1,0,")[2:], ["bad-area"])
        c.close()

    def test_marks(self):
        """a dot and a hyphen at their size next to the person's letters (x-height 0.1 em):
        read as marks whatever the model makes of them blown up"""
        marks = set(".,'-")
        c = self.client("0.1,0.25,0.14,0.35")
        self.assertIn(self.read(c, [self.circle(0.5, 0.5, 0.01)])[0], marks)
        self.assertEqual(self.read(c, [self.line(0.4, 0.5, 0.5, 0.505)])[:1], ["-"])
        # the line alone (em: 1) is enough for a dot
        c.close()
        c = self.client()
        self.assertIn(self.read(c, [self.circle(0.5, 0.5, 0.01)])[0], marks)
        c.close()

    def test_fit_never_lower(self):
        """prompted writing of a lone character: the size only ever helps its fit"""
        fits = []
        for size in ("off", "0.1,0.25,0.14,0.35"):
            c = self.client(size)
            c.ask("begin", "s", "en", "normal", "em", "")
            c.ask("stroke", "s", pts(self.circle(0.5, 0.5, 0.01)))
            fits.append(float(c.ask("align", "s", ".")[2]))
            c.close()
        self.assertGreaterEqual(fits[1], fits[0])

    def test_words_unchanged(self):
        """words read the same with and without the size (ink the model reads as three
        characters or more: the random model reads some words as two)"""
        out = []
        for size in ("off", "0.35,0.6,0.45,0.8"):
            c = self.client(size)
            got = []
            for s in INK:
                got.append(c.ask("recognize", "en", "normal", "em", "3", "",
                                 ";".join(pts(q) for q in s["strokes"]))[2:])
            c.close()
            out.append(got)
        words = [i for i, r in enumerate(out[0]) if r[0] == "text" and int(r[1]) and len(unesc(r[2])) >= 3]
        self.assertGreater(len(words), 5)
        self.assertEqual([out[0][i] for i in words], [out[1][i] for i in words])

    def test_measured_letters_go_with_an_erase(self):
        c = self.client()
        for i, s in enumerate(INK * 2):
            c.write_sample(s, session="k")
            self.assertEqual(c.ask("confirm", "k", "corrected", esc(s["text"]))[2], "ok")
            c.ask("end", "k")
        self.read(c, [self.circle(0.5, 0.5, 0.01)])
        c.close()
        f = self.env.store / "size"
        self.assertTrue(f.exists())
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertTrue(f.read_text().startswith("glassine-handwriting-size 1\n"))
        # a sample taken back takes its measurement with it, at once
        ids = [ln.split(" ")[0] for ln in f.read_text().splitlines()[1:]]
        self.assertTrue(ids)
        c = self.client()
        self.assertEqual(c.ask("forget", ids[-1])[2], "ok")
        c.close()
        self.assertNotIn(ids[-1], f.read_text())
        self.env.run("samples", "delete", ids[0])
        self.assertNotIn(ids[0], f.read_text())
        self.env.run("samples", "erase")
        self.assertFalse(f.exists())


class Lookalikes(unittest.TestCase):
    """the person's look-alikes (FR-019): a letter picked for a digit (or the reverse) often
    enough leans like O and 0, by the field; kept in the store, removed with the samples,
    never exported, never a writer's store's"""

    SHARED = set("OoIlG016")

    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def first(self, c, s, field, *how):
        c.write_sample(s, field=field)
        r = parse_text(c.ask("read", "s", "3", *how))
        c.ask("end", "s")
        return r[0][0] if r else ""

    def ink_with_letter(self, c):
        """an ink whose first reading has a letter outside the shared look-alikes: the ink, its
        reading, that letter's place"""
        for s in INK:
            t = self.first(c, s, "-")
            for i, ch in enumerate(t):
                if ch.isalpha() and ch.isascii() and ch not in self.SHARED:
                    return s, t, i
        self.skipTest("no reading with a plain letter")

    def pick(self, c, s, text, field="normal"):
        c.write_sample(s, field=field)
        c.ask("read", "s", "3")
        r = c.ask("confirm", "s", "corrected", esc(text))
        c.ask("end", "s")
        return r

    def lines(self):
        f = self.env.store / "lookalikes"
        return [ln.split("\t") for ln in f.read_text().splitlines()] if f.exists() else []

    def test_recorded_and_removed(self):
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        s, t, i = self.ink_with_letter(c)
        r = self.pick(c, s, t[:i] + "7" + t[i + 1:])
        self.assertEqual(r[2], "ok", r)
        sid = r[3]
        self.assertEqual(self.lines(), [[sid, "org.example.keyboard", "%x" % ord(t[i]), "37"]])
        self.assertEqual(stat.S_IMODE((self.env.store / "lookalikes").stat().st_mode), 0o600)
        # a letter for another letter, a longer text, a prompted sample: no look-alike
        other = "b" if t[i] != "b" else "c"
        self.assertEqual(self.pick(c, s, t[:i] + other + t[i + 1:])[2], "ok")
        self.assertEqual(self.pick(c, s, t + "7")[2], "ok")
        c.write_sample(s)
        c.ask("confirm", "s", "prompted", esc(t[:i] + "7" + t[i + 1:]), "keep")
        c.ask("end", "s")
        self.assertEqual(len(self.lines()), 1)
        # another client's pick, then the samples go: their look-alikes with them
        c.ask("hello", "2", "org.example.notes")
        r2 = self.pick(c, s, t[:i] + "4" + t[i + 1:])
        self.assertEqual(r2[2], "ok", r2)
        c.close()
        self.assertEqual(len(self.lines()), 2)
        exp = self.env.dir / "export.jsonl"
        self.env.run("samples", "export", str(exp))
        for ln in exp.read_text().splitlines()[1:]:
            self.assertEqual(set(json.loads(ln)) - {"sample", "client", "confirmed", "source", "adaptation",
                                                    "preference"}, set())
        self.assertNotIn("lookalike", exp.read_text())
        self.env.run("samples", "delete", sid)
        self.assertEqual([ln[1] for ln in self.lines()], ["org.example.notes"])
        self.env.run("samples", "erase", "-c", "org.example.notes")
        self.assertEqual(self.lines(), [])
        self.assertFalse((self.env.store / "lookalikes").exists())

    def test_private_and_learning_off(self):
        """nothing is kept from a field that doesn't learn, nor with learning off"""
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        s, t, i = self.ink_with_letter(c)
        text = t[:i] + "7" + t[i + 1:]
        for field in ("number", "terminal", "email"):
            self.pick(c, s, text, field)
        c.close()
        self.assertEqual(self.lines(), [])
        self.env.run("learning", "off")
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        self.assertEqual(self.pick(c, s, text)[2:4], ["skip", "learning"])
        c.close()
        self.assertFalse(self.env.store.exists())

    def test_lean_by_field(self):
        """picked often enough, the pair leans like O and 0: in a field for numbers the letter
        gives way; in one for text, raw readings and a writer's store, nothing changes"""
        self.env.env["GLASSINE_HANDWRITING_LOOKALIKE"] = "0.000001"     # a lean the random model can't miss
        c = self.env.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        s, t, i = self.ink_with_letter(c)
        letter = t[i]
        w = self.env.run("writer", "new").stdout.strip()
        before = {f: self.first(c, s, f) for f in ("number", "normal")}
        self.assertIn(letter, before["number"])
        for k in range(3):
            self.assertEqual(self.pick(c, s, t[:i] + "7" + t[i + 1:])[2], "ok")
            if k < 2:   # not yet the person's
                self.assertEqual(self.first(c, s, "number"), before["number"])
        after = self.first(c, s, "number")
        self.assertNotIn(letter, after)
        self.assertEqual(self.first(c, s, "normal"), before["normal"])
        self.assertIn(letter, self.first(c, s, "number", "raw"))
        c.close()
        g = self.env.start("--writer", w, "-i", "0")
        self.assertEqual(self.first(g, s, "number"), before["number"])
        g.close()


class ModelsSetting(unittest.TestCase):
    """the setting `models`: another set of base models installed beside the default one,
    taken at the next request; the environment's directory comes first"""

    def test_switch(self):
        env = Env(REAL)
        try:
            other = env.dir / "other"
            (other / "en").mkdir(parents=True)          # a set without English
            del env.env["GLASSINE_HANDWRITING_MODELS"]
            stroke = pts(INK[13]["strokes"][0])

            def recognize(c):
                return c.ask("recognize", "en", "normal", "em", "1", "", stroke)

            env.conf_set("handwriting.conf", f"models = {env.models}\n")
            c = env.start("-i", "0")
            c.ask("hello", "1", "org.example.test")
            self.assertEqual(recognize(c)[0], "recognize")
            env.conf_set("handwriting.conf", f"models = {other}\n")
            self.assertEqual(recognize(c)[2:], ["no-model"])
            env.conf_set("handwriting.conf", f"models = {env.models}\nmodels = relative/not/taken\n")
            self.assertEqual(recognize(c)[2:], ["no-model"])  # not a full path: the default directory
            env.conf_set("handwriting.conf", f"models = {env.models}\n")
            self.assertEqual(recognize(c)[0], "recognize")
            c.close()
            env.env["GLASSINE_HANDWRITING_MODELS"] = str(other)
            c = env.start("-i", "0")
            c.ask("hello", "1", "org.example.test")
            self.assertEqual(recognize(c)[2:], ["no-model"])  # the environment wins
            c.close()
        finally:
            env.close()

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

    def test_calibrated(self):
        """a new head carries its temperature (gha byte 52, FR-022); one made before
        calibration gets one at the next run with nothing new, nothing else in it changed;
        readings change with it, fits don't"""
        import struct
        cur = self.env.store / "adaptation" / "en" / "current"
        self.teach(150)
        self.assertIn("kept", self.env.run("adapt", *self.ARGS).stdout)
        good = cur.read_bytes()
        t = struct.unpack_from("<f", good, 52)[0]
        self.assertTrue(0.5 <= t <= 4, t)
        # as an older version wrote it: no temperature
        old = good[:52] + b"\0\0\0\0" + good[56:]
        cur.write_bytes(old)
        self.env.run("adaptation", "show")
        out = self.env.run("adapt", *self.ARGS[1:]).stdout
        self.assertIn("nothing new", out)
        now = cur.read_bytes()
        self.assertEqual(now[:52] + now[56:], good[:52] + good[56:], "only the temperature written")
        self.assertAlmostEqual(struct.unpack_from("<f", now, 52)[0], t, places=5)
        self.assertNotIn("calibrated", self.env.run("adapt", *self.ARGS[1:]).stdout)   # once
        # readings at another temperature; the prompted fit stays the ink's own
        reads = {}
        for temp in ("off", "3"):
            self.env.env["GLASSINE_HANDWRITING_CALIB"] = temp
            c = self.env.start("-i", "0")
            c.ask("begin", "s", "en", "normal", "device:10", "")
            for q in shapes("x", random.Random(5)):
                c.ask("stroke", "s", q)
            reads[temp] = (parse_text(c.ask("read", "s", "3")), c.ask("align", "s", "x")[2:])
            c.close()
        self.env.env.pop("GLASSINE_HANDWRITING_CALIB")
        self.assertNotEqual([x[1] for x in reads["off"][0]], [x[1] for x in reads["3"][0]], "scores calibrated")
        self.assertEqual(reads["off"][1], reads["3"][1], "the fit is the ink's own")

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

    def test_models_setting_keeps_its_own(self):
        """with the setting `models` on another set, an adaptation goes to adaptation/en@NAME:
        the default set's head stays and is used again when the setting goes back"""
        ad = self.env.store / "adaptation"
        self.teach(120)
        self.assertNotIn("not kept", self.env.run("adapt", *self.ARGS).stdout)
        first = (ad / "en" / "current").read_bytes()
        trial = self.env.dir / "handwriting-trial"
        shutil.copytree(self.env.models, trial)
        del self.env.env["GLASSINE_HANDWRITING_MODELS"]
        self.env.conf_set("handwriting.conf", f"models = {trial}/\n")
        self.assertEqual(self.env.run("adaptation", "show").stdout.splitlines()[0], "current\tnone")
        self.teach(20)
        self.assertNotIn("not kept", self.env.run("adapt", *self.ARGS).stdout)
        self.assertTrue((ad / "en@handwriting-trial" / "current").exists())
        self.assertEqual((ad / "en" / "current").read_bytes(), first)
        self.assertFalse((ad / "en" / "previous").exists())
        self.env.conf_set("handwriting.conf", "")
        self.env.env["GLASSINE_HANDWRITING_MODELS"] = str(self.env.models)
        self.assertTrue(self.env.run("adaptation", "show").stdout.startswith("current\t1"))

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


class ReferenceHeight(unittest.TestCase):
    """a model that reads the ink scaled by a reference height (ghm version 2, FR-018): it
    reads, its readings follow the ink's size (a version 1 model's don't), and it adapts"""

    def scores(self, ref, scale, area=None, size=None, units="em", prior=None, ink=None):
        env = Env(ref=ref)
        try:
            if size:
                env.env["GLASSINE_HANDWRITING_SIZE"] = size
            c = env.start("-i", "0")
            c.ask("hello", "1", "org.example.keyboard")
            out = []
            for s in ink or INK[:6]:
                c.ask("begin", "s", "en", "normal", units, "")
                if prior is not None:
                    self.assertEqual(c.ask("prior", "s", prior)[2:], ["ok"])
                if area:
                    self.assertEqual(c.ask("area", "s", area)[2:], ["ok"])
                for q in s["strokes"]:
                    c.ask("stroke", "s", pts([(p[0] * scale, p[1] * scale, p[2]) for p in q]))
                r = parse_text(c.ask("read", "s", "1", "raw"))
                c.ask("end", "s")
                out.append(r[0][1] if r else None)
            c.close()
            return out
        finally:
            env.close()

    def test_size_is_read(self):
        # version 1: the ink's own height, so the same ink twice as big reads the same
        self.assertEqual(self.scores(False, 1), self.scores(False, 2))
        one, two = self.scores(True, 1), self.scores(True, 2)
        self.assertTrue(all(x is not None for x in one))
        self.assertNotEqual(one, two)
        # the same reference and ink: the same reading
        self.assertEqual(one, self.scores(True, 1))
        # the person's letters replace the line's guess, and with them the reading
        self.assertNotEqual(one, self.scores(True, 1, size="0.05,0.12"))
        # the reference for the model alone, no weighing: still read at that size
        self.assertEqual(self.scores(True, 1, size="noweigh"), self.scores(True, 1, size="noweigh"))
        self.assertEqual(self.scores(True, 1, size="0.05,0.12"), self.scores(True, 1, size="0.05,0.12,noweigh"))
        # weighing a version 2 model is asked for only
        self.assertTrue(all(x is not None for x in self.scores(True, 1, size="0.05,0.12,weigh")))
        # the guide line the ink sits on moves its height
        self.assertNotEqual(self.scores(True, 1, area="-5,-5,10,10,1,0,0.62"),
                            self.scores(True, 1, area="-5,-5,10,10,1,0,0.2"))

    def test_prior(self):
        """the size prior (contract 1.6): the reference when neither the person's letters in
        the session's units nor an area line give one"""
        dev = dict(units="device")
        none = self.scores(True, 1, **dev)
        small, big = self.scores(True, 1, prior="0.08", **dev), self.scores(True, 1, prior="0.3", **dev)
        self.assertTrue(all(x is not None for x in small))
        # a prior far below the writing (more than four times as high) says nothing of it: it
        # is read as without one; a prior it fits is its reference
        self.assertEqual(small, none)
        self.assertNotEqual(big, none)
        # a line whose small letters can be measured (ten humps of 0.4, a little jitter) is
        # read at their height, whatever the prior
        rng = random.Random(5)
        hump = [[i * 0.05, 0.4 * abs(math.sin(i * math.pi / 20)) + rng.uniform(-0.004, 0.004), i * 5]
                for i in range(200)]
        line = [dict(INK[0], strokes=[hump])]
        a, b, n = (self.scores(True, 1, prior=p, units="device", ink=line) for p in ("0.05", "0.3", None))
        self.assertEqual(a, b)
        self.assertNotEqual(a, n)
        # a lone character can't be measured: a prior it fits is its reference, one far below
        # it (the character more than twice as high) is none
        lone = [dict(r, strokes=r["strokes"][:1]) for r in INK[:6]]
        fits, below, free = (self.scores(True, 1, prior=p, units="device", ink=lone) for p in ("0.5", "0.05", None))
        self.assertEqual(below, free)
        self.assertNotEqual(fits, free)
        # an area without a line keeps the prior; a ruled line replaces it
        self.assertEqual(self.scores(True, 1, prior="0.3", area="-5,-5,10,10,0,0", **dev), big)
        self.assertEqual(self.scores(True, 1, prior="0.3", area="-5,-5,10,10,1,0", **dev),
                         self.scores(True, 1, area="-5,-5,10,10,1,0", **dev))
        # the person's letters in the session's units replace it
        self.assertEqual(self.scores(True, 1, prior="0.3", size="0.05,0.12", **dev),
                         self.scores(True, 1, prior="0.08", size="0.05,0.12", **dev))
        env = Env()
        try:
            c = env.start("-i", "0")
            c.ask("hello", "1", "org.example.keyboard")
            c.ask("begin", "s", "en", "normal", "device", "")
            for bad in ("-1", "x", "nan", ""):
                self.assertEqual(c.ask("prior", "s", bad)[2:], ["bad-prior"])
            self.assertEqual(c.ask("prior", "nope", "1")[2:], ["unknown-session"])
            c.close()
        finally:
            env.close()

    def test_adapts(self):
        env = Env(ref=True)
        try:
            rng = random.Random(3)
            c = env.start("-i", "0")
            c.ask("hello", "1", "org.example.keyboard")
            for i in range(150):
                ch = "olxz"[i % 4]
                c.ask("begin", "s", "en", "normal", "device:10", "")
                for q in shapes(ch, rng):
                    c.ask("stroke", "s", q)
                self.assertEqual(c.ask("confirm", "s", "picked", ch)[2], "ok")
            c.close()
            out = env.run("adapt", *Adaptation.ARGS).stdout
            self.assertIn("kept", out)
            self.assertTrue((env.store / "adaptation" / "en" / "current").exists())
        finally:
            env.close()


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


class WriterStores(unittest.TestCase):
    """guest writers' stores (spec 001 FR-021, spec 002 FR-016, FR-023, FR-024; contract 1.3):
    beside the person's store, named only by --writer, refused for what a writer's store
    doesn't do, removed whole"""

    def setUp(self):
        self.env = Env()
        self.env.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"

    def tearDown(self):
        self.env.close()

    @property
    def writers(self):
        return self.env.data / "glassine" / "handwriting-writers"

    def new(self):
        w = self.env.run("writer", "new").stdout.strip()
        self.assertRegex(w, r"^[0-9a-f]{16}$")
        return w

    def keep(self, c, text="hello"):
        rng = random.Random(len(text))
        self.assertEqual(c.ask("begin", "s", "en", "normal", "device:4.0:200", "")[2], "ok")
        t = 1000
        for k in range(3):
            q = []
            for i in range(20):
                q.append(f"{rng.uniform(0, 300):.2f},{rng.uniform(0, 80):.2f},{t}")
                t += 5
            self.assertEqual(c.ask("stroke", "s", " ".join(q))[2], "ok")
        r = c.ask("confirm", "s", "prompted", text)
        self.assertEqual(r[2], "ok", r)
        return r[3]

    def stats(self, *pre):
        return dict(ln.split("\t", 1) for ln in self.env.run(*pre, "samples", "stats").stdout.splitlines()
                    if not ln.startswith("client"))

    def test_beside_the_persons_store(self):
        w = self.new()
        d = self.writers / w
        self.assertTrue(d.is_dir())
        self.assertEqual(d.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.writers.stat().st_mode & 0o777, 0o700)
        self.assertFalse(self.env.store.exists())               # making one makes nothing of the person's
        # the person's learning off: a writer's prompted samples are kept all the same
        self.env.conf_set("handwriting.conf", "learning = off\n")
        c = self.env.start("--writer", w, "-i", "0")
        c.ask("hello", "1", "org.example.samples")
        sid = self.keep(c)
        c.close()
        self.assertEqual(self.stats("--writer", w)["samples"], "1")
        self.assertEqual(self.stats()["samples"], "0")
        self.assertEqual(self.stats()["writers"], "1")
        self.assertNotIn("writers", self.stats("--writer", w))
        self.assertIn(sid, self.env.run("--writer", w, "samples", "list").stdout)
        self.assertNotIn(sid, self.env.run("samples", "list").stdout)
        for f in d.rglob("*"):
            self.assertEqual(f.stat().st_mode & 0o777, 0o700 if f.is_dir() else 0o600, f)
        lst = [ln.split("\t") for ln in self.env.run("writer", "list").stdout.splitlines()]
        self.assertEqual(lst, [[w, "1", "0"]])

    def test_erase_and_export_are_one_stores(self):
        a, b = self.new(), self.new()
        self.assertNotEqual(a, b)
        for w in (a, b, None):
            pre = ["--writer", w] if w else []
            c = self.env.start(*pre, "-i", "0")
            c.ask("hello", "1", "org.example.samples")
            self.keep(c, "water")
            c.close()
        self.env.run("samples", "erase")
        self.assertEqual(self.stats()["samples"], "0")
        self.assertEqual(self.stats("--writer", a)["samples"], "1")
        self.env.run("--writer", a, "samples", "erase")
        self.assertEqual(self.stats("--writer", a)["samples"], "0")
        self.assertEqual(self.stats("--writer", b)["samples"], "1")
        out = self.env.dir / "b.jsonl"
        self.env.run("--writer", b, "samples", "export", str(out))
        self.assertEqual(sum(1 for ln in out.read_text().splitlines() if '"sample"' in ln), 1)

    def test_own_contributor_number(self):
        a, b = self.new(), self.new()
        ids = []
        for pre in (["--writer", a], ["--writer", b], []):
            self.env.run(*pre, "donation", "start", "CC0-1.0")
            ids.append(self.env.run(*pre, "donation", "donor").stdout.strip())
        self.assertEqual(len(set(ids)), 3)
        self.assertNotIn(a, ids[0])
        self.env.run("--writer", a, "donation", "erase")
        self.assertEqual(self.env.run("--writer", a, "donation", "donor").stdout.strip(), ids[0])

    def test_refusals(self):
        w = self.new()
        for args in (["adapt"], ["adaptation"], ["letters", "build", "-r"], ["letters", "status"], ["font", "status"],
                     ["learning"], ["learning", "off"], ["samples", "import", str(self.env.dir / "x.jsonl")]):
            p = self.env.run("--writer", w, *args, check=False)
            self.assertEqual((p.returncode, p.stderr.strip()), (2, "writer-store"), args)
        c = self.env.start("--writer", w, "-i", "0")
        c.ask("hello", "1", "org.example.samples")
        self.assertEqual(c.ask("begin", "s", "en", "normal", "device:4.0:200", "")[2], "ok")
        self.assertEqual(c.ask("stroke", "s", "1,1,0 5,5,10")[2], "ok")
        self.assertEqual(c.ask("neaten", "s", "1")[2:], ["writer-store"])
        c.close()
        self.assertFalse((self.writers / w / "adaptation").exists())
        self.assertFalse((self.writers / w / "letters").exists())

    def test_unknown_and_removed(self):
        bad = "0123456789abcdef"
        p = self.env.run("--writer", bad, "samples", "stats", check=False)
        self.assertEqual((p.returncode, p.stderr.strip()), (1, "unknown-writer"))
        p = self.env.run("--writer", bad, "-i", "0", check=False, inp="hello\tr1\t1\n")
        self.assertEqual((p.returncode, p.stderr.strip(), p.stdout), (1, "unknown-writer", ""))
        self.assertFalse(self.writers.exists() and any(self.writers.iterdir()))
        self.assertEqual(self.env.run("writer", "remove", bad, check=False).returncode, 1)
        w = self.new()
        c = self.env.start("--writer", w, "-i", "0")
        c.ask("hello", "1", "org.example.samples")
        self.keep(c)
        self.assertEqual(self.env.run("writer", "remove", w).stdout.strip(), "removed")
        self.assertFalse((self.writers / w).exists())
        # its running process keeps nothing more and makes nothing again
        self.assertEqual(c.ask("begin", "s", "en", "normal", "device:4.0:200", "")[2:], ["unknown-writer"])
        self.assertEqual(c.ask("samples", "")[2:], ["unknown-writer"])
        c.close()
        self.assertEqual(list(self.writers.iterdir()), [])
        self.assertEqual(self.env.run("writer", "list").stdout, "")
        self.assertEqual(self.stats()["writers"], "0")

    def test_crashed_remove_is_finished(self):
        w = self.new()
        left = self.writers / (".removing-" + w)
        (self.writers / w).rename(left)
        (left / "samples.jsonl").write_text("{}\n")
        self.assertEqual(self.env.run("writer", "list").stdout, "")
        self.assertFalse(left.exists())

    def test_moved_in_by_hand(self):
        """a store kept elsewhere becomes a writer's store as docs/recognizer.md says, its
        samples and contributor number with it"""
        other = Env()
        try:
            other.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"
            c = other.start("-i", "0")
            c.ask("hello", "1", "org.example.samples")
            sid = WriterStores.keep(self, c, "water")
            c.close()
            other.run("donation", "start", "CC-BY-4.0")
            donor = other.run("donation", "donor").stdout.strip()
            w = self.new()
            dst = self.writers / w
            for f in other.store.iterdir():
                if f.name in ("lock", "adaptation", "letters"):
                    continue
                if f.is_dir():
                    shutil.copytree(f, dst / f.name)
                else:
                    shutil.copy2(f, dst / f.name)
            for f in dst.rglob("*"):
                f.chmod(0o700 if f.is_dir() else 0o600)
        finally:
            other.close()
        self.assertEqual(self.env.run("writer", "list").stdout.split(), [w, "1", "0"])
        self.assertIn(sid, self.env.run("--writer", w, "samples", "list").stdout)
        self.assertEqual(self.env.run("--writer", w, "donation", "donor").stdout.strip(), donor)
        self.assertIn("licence\tCC-BY-4.0", self.env.run("--writer", w, "donation", "list").stdout)

    def test_idle_exit_never_adapts(self):
        w = self.new()
        c = self.env.start("--writer", w, "-i", "1")
        c.ask("hello", "1", "org.example.samples")
        for k in range(3):
            self.keep(c, f"word{k}")
        self.assertEqual(c.p.wait(timeout=20), 0)
        c.close()
        time.sleep(1)
        self.assertFalse((self.writers / w / "adaptation").exists())
        self.assertFalse((self.writers / w / "letters").exists())
        self.assertFalse(self.env.store.exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
