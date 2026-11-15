#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance of the handwriting samples app (spec 002): prompt choice by coverage and
error, a kept sample with exact spans and unaltered points, Redo, Skip, Discard and delete,
the donation rules, the export and import round trip. Runs the app's toolkit-free modules
against the real recognizer:

    HW_BIN=path/to/glassine-handwriting python3 tests/test_samples.py

Python 3 standard library only. The recognizer gets a random-weight model
(../recognizer/tests/mkmodel.py): its readings are nonsense and its alignment fits nothing,
so the check's threshold is set per test (GLASSINE_HANDWRITING_FIT_OK)."""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from glassine_hwsamples import APP_ID, client, flow, prompts as P  # noqa: E402

BIN = str(Path(os.environ.get("HW_BIN") or ROOT.parent / "recognizer" / "glassine-handwriting").resolve())
MKMODEL = ROOT.parent / "recognizer" / "tests" / "mkmodel.py"
CHARSET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,;:!?'-() "


def ink(rng, n=3, t=1731658123456):
    """pen points as a toolkit gives them: surface fractions, pressure, tilt, ms times"""
    out = []
    for _ in range(n):
        s = []
        x, y = rng.uniform(20, 600), rng.uniform(40, 120)
        for _ in range(30):
            x += rng.randrange(-512, 512) / 256
            y += rng.randrange(-512, 512) / 256
            s.append((x, y, t, rng.randrange(0, 65536) / 65535, rng.randrange(-6000, 6000) / 100,
                      rng.randrange(-6000, 6000) / 100))
            t += 5
        t += 180
        out.append(s)
    return out


class Env:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hw-samples-test-"))
        models = self.dir / "models"
        (models / "en").mkdir(parents=True)
        subprocess.run([sys.executable, str(MKMODEL), str(models / "en" / "recognizer.ghm")], check=True)
        (models / "en" / "prompts.txt").write_text("The quick brown fox jumps over the lazy dog.\n")
        shutil.copytree(ROOT / "prompts" / "en", models / "en" / "prompts")
        self.env = dict(os.environ, GLASSINE_HANDWRITING_MODELS=str(models), XDG_DATA_HOME=str(self.dir / "data"),
                        XDG_CONFIG_HOME=str(self.dir / "conf"), XDG_STATE_HOME=str(self.dir / "state"),
                        HOME=str(self.dir / "home"), GLASSINE_HANDWRITING_SYSFS=str(self.dir / "power"),
                        GLASSINE_HANDWRITING_LOADAVG=str(self.dir / "load"), GLASSINE_HANDWRITING_FIT_OK="0")
        bat = self.dir / "power" / "battery"
        bat.mkdir(parents=True)
        (bat / "type").write_text("Battery\n")
        (bat / "status").write_text("Charging\n")
        (self.dir / "load").write_text("0.1 0.1 0.1 1/100 1\n")
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)       # the modules read the XDG paths from the environment
        self.rec = client.Recognizer(BIN, env=self.env)
        self.cli = client.Cli(BIN, env=self.env)

    def writer(self, state=None, **kw):
        return flow.Writer(self.rec, self.cli, state or flow.State(), **kw)

    def close(self):
        self.rec.close()
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)


class Case(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.rng = random.Random(11)

    def tearDown(self):
        self.e.close()

    def write(self, w, fit_ok=None):
        if fit_ok is not None:
            self.e.rec.close()
            self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = str(fit_ok)
        strokes = ink(self.rng, n=max(3, len(w.prompt.text)))      # enough ink for the prompt
        return w.submit(strokes, "device:6.2", (0, 0, 702, 300, 96, 0)), strokes


class Prompts(Case):
    def test_shipped_sets(self):
        sets = P.load("en")
        self.assertEqual(set(sets), set(P.SETS) | {P.OWN})
        for name in P.SETS:
            for p in sets[name]:
                self.assertTrue(p.licence and p.source, p)
                self.assertFalse(set(p.text) - set(CHARSET), p.text)
                self.assertTrue(p.shipped)
        letters = "".join(p.text for p in sets["letters"])
        self.assertEqual(sorted(letters), sorted("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        for p in sets["pangrams"]:
            self.assertFalse(set("abcdefghijklmnopqrstuvwxyz") - set(p.text.lower()), p.text)
        self.assertEqual(sets[P.OWN], [])

    def test_choice_by_coverage(self):
        """the next prompt teaches what has few samples"""
        sets = {"words": [P.Prompt(t, "words", "en") for t in ("tent", "test", "quiz", "nest")]}
        cov = P.Coverage()
        for _ in range(30):
            cov.add("tents nest stent", "tents nest stent")
        self.assertEqual(P.choose(sets, "words", cov, {}).text, "quiz")

    def test_choice_by_error(self):
        """with the same coverage, a character read wrongly most often wins"""
        sets = {"words": [P.Prompt(t, "words", "en") for t in ("mama", "kaka")]}
        cov = P.Coverage()
        for _ in range(20):
            cov.add("mama kaka", "mama haha")    # k always read as h
        self.assertEqual(P.choose(sets, "words", cov, {}).text, "kaka")
        self.assertEqual(P.char_errors("kaka", "haha"), [1, 0, 1, 0])
        self.assertEqual(P.char_errors("cat", "ct"), [0, 1, 0])
        row = {c: (n, err) for c, n, err in cov.table("akm")}
        self.assertEqual(row["k"], (20 * 2, 1.0))
        self.assertEqual(row["m"][1], 0.0)

    def test_no_repeat_until_worked_through(self):
        sets = {"words": [P.Prompt(t, "words", "en") for t in ("one", "two", "six")]}
        cov, used, seen = P.Coverage(), {}, []
        for _ in range(3):
            p = P.choose(sets, "words", cov, used)
            P.mark_used(used, p)
            seen.append(p.text)
        self.assertEqual(sorted(seen), ["one", "six", "two"])
        self.assertIn(P.choose(sets, "words", cov, used).text, seen)      # a new round
        self.assertEqual(used["words"], [])

    def test_own_words(self):
        P.save_own(["Grünewald", "  my   words ", ""])
        own = P.load("en")[P.OWN]
        self.assertEqual([p.text for p in own], ["Grünewald", "my words"])
        self.assertFalse(own[0].shipped)
        self.assertEqual(os.stat(P.own_words_path()).st_mode & 0o777, 0o600)


class Writing(Case):
    def test_kept_sample_exact_and_unaltered(self):
        st = flow.State()
        st.set = "words"
        w = self.e.writer(st)
        first = w.prompt
        sh, strokes = self.write(w)
        self.assertEqual((sh.status, sh.exact), ("kept", True))
        self.assertNotEqual(w.prompt, first)                    # the next prompt is up
        smp = self.e.rec.sample(sh.sample_id)
        self.assertEqual((smp["source"], smp["spans_source"], smp["text"]), ("prompted", "exact", first.text))
        self.assertEqual(len(smp["spans"]), len(first.text))
        self.assertEqual(smp["units"], {"xy": "device", "per_mm": 6.2})
        self.assertEqual(smp["area"], {"box": [0, 0, 702, 300], "line": 96, "orientation": 0})
        self.assertEqual([r["text"] for r in smp["reading"]], [r[0] for r in sh.reading])
        t0 = strokes[0][0][2]
        for q, got in zip(strokes, smp["strokes"]):
            self.assertEqual(len(q), len(got))
            for p, g in zip(q, got):
                self.assertEqual(g[0:2], list(p[0:2]))
                self.assertEqual(g[2], round((p[2] - t0) / 1000, 6))
                self.assertEqual(g[3:], list(p[3:]))
        self.assertEqual(w.counts(), (1, 1))

    def test_check_failed_kept_only_when_confirmed(self):
        w = self.e.writer()
        first = w.prompt
        sh, _ = self.write(w, fit_ok=0.99)
        self.assertEqual(sh.status, "check")
        self.assertEqual(w.prompt, first)                        # not moved on
        self.assertEqual(self.e.cli.samples(), [])
        w.keep_anyway()
        self.assertEqual(sh.status, "kept")
        self.assertFalse(sh.exact)
        self.assertEqual(self.e.rec.sample(sh.sample_id)["spans_source"], "inferred")
        self.assertNotEqual(w.prompt, first)

    def test_redo_skip_discard_delete(self):
        w = self.e.writer()
        p1 = w.prompt
        self.write(w)
        self.assertEqual(len(self.e.cli.samples()), 1)
        self.assertEqual(w.redo(), p1)                             # back to the same prompt
        self.assertEqual(self.e.cli.samples(), [])
        self.write(w)
        p2 = w.prompt
        w.discard()
        self.assertEqual(self.e.cli.samples(), [])
        self.assertEqual(w.prompt, p2)
        self.assertNotIn(p1.text, w.state.used.get(p1.set, []))   # may come again
        w.skip()                                                   # nothing written: next prompt
        self.assertEqual(self.e.cli.samples(), [])
        self.assertIn(p2.text, w.state.used[p2.set])
        sh, _ = self.write(w, fit_ok=0.99)                        # failed check, then Skip
        self.assertEqual(sh.status, "check")
        w.skip()
        self.assertEqual(self.e.cli.samples(), [])
        sh, _ = self.write(w, fit_ok=0)
        sid = sh.sample_id
        self.write(w)
        self.assertEqual(len(self.e.cli.samples()), 2)
        self.e.cli.delete(sid)                                     # later, in the sample view
        self.assertEqual([r[0] for r in self.e.cli.samples()], [w.shown.sample_id])
        self.assertEqual(self.e.cli.erase(), 1)
        self.assertEqual(self.e.cli.samples(), [])

    def test_resumes_its_place(self):
        st = flow.State()
        st.set = "sentences"
        w = self.e.writer(st)
        self.write(w)
        here = w.prompt
        w2 = self.e.writer(flow.State())
        self.assertEqual(w2.prompt, here)
        self.assertEqual(w2.state.set, "sentences")
        self.assertEqual(w2.state.used, w.state.used)

    def test_coverage_from_samples(self):
        st = flow.State()
        st.set = "letters"
        w = self.e.writer(st)
        texts = []
        for _ in range(3):
            texts.append(w.prompt.text)
            self.write(w)
        cache = {}
        cov = w.load_coverage(cache)
        self.assertEqual(sum(cov.count(c) for c in texts), 3)
        self.assertEqual(len(cache), 3)


class Donation(Case):
    def test_rules(self):
        P.save_own(["Grünewald"])
        st = flow.State()
        st.set = "words"
        w = self.e.writer(st)
        self.write(w)                                              # donating off: nothing donated
        self.assertEqual(self.e.cli.donation(), (None, 0))
        self.e.cli.donation_start("CC-BY-4.0")
        w.state.donating = True
        sh, _ = self.write(w)                                      # a shipped prompt: donated
        self.assertTrue(sh.donated_id)
        self.assertEqual(sh.donated_id, sh.sample_id)
        self.assertEqual(self.e.cli.donation(), ("CC-BY-4.0", 1))
        w.reload_sets()
        w.choose_set(P.OWN)
        self.assertEqual(w.prompt.text, "Grünewald")
        own, _ = self.write(w)                                     # the own list: not donated
        self.assertEqual((own.status, own.donated_id), ("kept", None))
        self.assertEqual(self.e.cli.donation()[1], 1)
        w.choose_set("words")
        gone, _ = self.write(w)
        self.assertEqual(self.e.cli.donation()[1], 2)
        w.discard()                                                # Discard takes back both
        self.assertEqual(self.e.cli.donation()[1], 1)
        self.assertNotIn(gone.sample_id, [r[0] for r in self.e.cli.samples()])
        self.e.cli.donation_add(own.sample_id)                     # one by one, by the person
        self.assertEqual(self.e.cli.donation()[1], 2)
        out = self.e.dir / "donation.jsonl"
        self.e.cli.donation_write(str(out))
        rows = [json.loads(x) for x in out.read_text().splitlines()]
        self.assertEqual([r["text"] for r in rows], [sh.prompt.text, "Grünewald"])
        for r in rows:
            self.assertEqual(r["licence"], "CC-BY-4.0")
            for k in ("client", "time", "confirmed", "device", "app", "id", "user", "host"):
                self.assertNotIn(k, r)
            self.assertNotIn(APP_ID, json.dumps(r))
            self.assertEqual(min(p[2] for q in r["strokes"] for p in q), 0)
        n = len(self.e.cli.samples())
        self.e.cli.donation_erase()                                # never touches the samples
        self.assertEqual(len(self.e.cli.samples()), n)
        w.state.donating = False
        self.write(w)
        self.assertEqual(self.e.cli.donation(), (None, 0))

    def test_learning_off_still_donates(self):
        self.e.cli.run("learning", "off")
        self.e.cli.donation_start("CC0-1.0")
        st = flow.State()
        st.donating = True
        st.set = "pangrams"
        w = self.e.writer(st)
        sh, _ = self.write(w)
        self.assertEqual((sh.status, sh.sample_id, sh.why), ("kept", None, "learning"))
        self.assertEqual(self.e.cli.donation()[1], 1)
        self.assertEqual(self.e.cli.samples(), [])
        w.discard()
        self.assertEqual(self.e.cli.donation()[1], 0)


class Files(Case):
    def test_export_import_round_trip(self):
        w = self.e.writer()
        for _ in range(4):
            self.write(w)
        before = {i: self.e.rec.sample(i) for i, _, _ in self.e.cli.samples()}
        out = str(self.e.dir / "export.jsonl")
        self.e.cli.export(out)
        self.assertEqual(self.e.cli.erase(), 4)
        self.assertEqual(self.e.cli.import_(out), 4)
        after = {i: self.e.rec.sample(i) for i, _, _ in self.e.cli.samples()}
        self.assertEqual(before, after)

    def test_adaptation_plan_and_request(self):
        plan = self.e.cli.plan()
        self.assertEqual((plan["train"], plan["held"], plan["new"], plan["charging"]), (0, 0, 0, True))
        w = self.e.writer()
        for _ in range(3):
            self.write(w)
        self.assertEqual(self.e.cli.plan()["new"], 3)
        lines = []
        last = self.e.cli.adapt(on_line=lambda k, f: lines.append(k))
        self.assertEqual(last[0], "skip")
        self.assertEqual(last[1][0], "too-few")
        self.assertEqual(lines[0], "plan")
        self.assertEqual(self.e.cli.adaptations(), {"current": None, "previous": None})


if __name__ == "__main__":
    unittest.main(verbosity=1)
