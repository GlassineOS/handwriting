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
                        GLASSINE_HANDWRITING_LOADAVG=str(self.dir / "load"), GLASSINE_HANDWRITING_FIT_OK="0",
                        GLASSINE_PREDICT_MODELS=str(self.dir / "predict"))
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

    def write_to(self, w, p, fit_ok):
        self.e.rec.close()
        self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = str(fit_ok)
        return w.submit(ink(self.rng, n=max(3, len(p.text))), "device:6.2", (0, 0, 702, 300, 96, 0), p)


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

    def test_next_shows_the_next_prompt_before_the_reading(self):
        """Next takes the prompt (the next one is up at once); the reading follows with it"""
        w = self.e.writer()
        p1 = w.prompt
        self.assertEqual(w.take(), p1)
        p2 = w.prompt
        self.assertNotEqual(p2, p1)
        self.assertIn(p1.text, w.state.used[p1.set])
        strokes = ink(self.rng, n=max(3, len(p1.text)))
        sh = w.submit(strokes, "device:6.2", (0, 0, 702, 300, 96, 0), p1)
        self.assertEqual((sh.status, sh.prompt), ("kept", p1))
        self.assertEqual(self.e.rec.sample(sh.sample_id)["text"], p1.text)
        self.assertEqual(w.prompt, p2)                             # not moved on a second time
        self.assertEqual(w.redo(), p1)                             # Redo brings the taken one back
        self.assertEqual(self.e.cli.samples(), [])
        # a failed check: the next prompt stays up, Keep it and Skip don't move on again
        p = w.take()
        nxt = w.prompt
        sh = self.write_to(w, p, fit_ok=0.99)
        self.assertEqual(sh.status, "check")
        self.assertEqual(w.prompt, nxt)
        w.keep_anyway()
        self.assertEqual((sh.status, w.prompt), ("kept", nxt))
        p = w.take()
        nxt = w.prompt
        self.assertEqual(self.write_to(w, p, fit_ok=0.99).status, "check")
        w.skip()
        self.assertEqual(w.prompt, nxt)
        p = w.take()
        nxt = w.prompt
        self.assertEqual(self.write_to(w, p, fit_ok=0.99).status, "check")
        w.discard()
        self.assertEqual(w.prompt, nxt)
        self.assertNotIn(p.text, w.state.used.get(p.set, []))     # may come again

    def test_failed_checks_wait_their_turn(self):
        """a failed check stays shown while later samples are read, until the person acts;
        two of them show one after the other"""
        w = self.e.writer()
        p1 = w.take()
        sh1 = self.write_to(w, p1, fit_ok=0.99)
        p2 = w.take()
        sh2 = self.write_to(w, p2, fit_ok=0)                       # kept, read after it
        self.assertEqual((sh1.status, sh2.status), ("check", "kept"))
        self.assertIs(w.shown, sh1)
        p3 = w.take()
        sh3 = self.write_to(w, p3, fit_ok=0.99)
        self.assertIs(w.shown, sh1)
        w.skip()
        self.assertIs(w.shown, sh3)                                # the next one waiting
        w.keep_anyway()
        self.assertEqual(sh3.status, "kept")
        self.assertIs(w.shown, sh3)
        self.assertEqual(w.checks, [])
        self.assertEqual(len(self.e.cli.samples()), 2)

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
        w2 = self.e.writer(flow.State())         # a new start: from the listing alone
        cov = w2.load_coverage()
        self.assertEqual(sum(cov.count(c) for c in texts), 3)
        self.assertEqual(len(w2.counted), 3)
        rows = self.e.rec.list()
        self.assertEqual([r[0] for r in rows], [r[0] for r in self.e.cli.samples()])
        self.assertEqual([r[4] for r in rows], texts)
        self.assertTrue(all(r[2] == "prompted" and r[3] == "en" and r[5] is not None for r in rows))

    def test_coverage_follows_the_listing(self):
        """the coverage counts each sample once: those kept here, those in a listing, and
        drops those taken back or deleted elsewhere"""
        st = flow.State()
        st.set = "letters"
        w = self.e.writer(st)
        texts = []
        for _ in range(4):
            texts.append(w.prompt.text)
            self.write(w)
        total = lambda cov: sum(n for n, _ in cov.chars.values())
        self.assertEqual(total(w.cov), 4)
        w.load_coverage()                                          # nothing counted twice
        self.assertEqual(total(w.cov), 4)
        ids = self.e.rec.sample_ids()
        self.e.cli.delete(ids[0])                                  # deleted elsewhere
        w.load_coverage()
        self.assertEqual(total(w.cov), 3)
        p = w.prompt
        self.write(w)
        w.redo()                                                   # taken back
        self.assertEqual(w.prompt, p)
        self.assertEqual(total(w.cov), 3)
        w.load_coverage()
        self.assertEqual(total(w.cov), 3)
        w.forgotten()
        self.assertEqual(total(w.cov), 0)
        self.assertEqual(w.cov.pairs, {})


class Donation(Case):
    def test_rules(self):
        P.save_own(["Grünewald"])
        st = flow.State()
        st.set = "words"
        w = self.e.writer(st)
        self.write(w)                                              # donating off: nothing donated
        self.assertEqual(self.e.cli.donation()[:2], (None, 0))
        self.e.cli.donation_start("CC-BY-4.0")
        w.state.donating = True
        sh, _ = self.write(w)                                      # a shipped prompt: donated
        self.assertTrue(sh.donated_id)
        self.assertEqual(sh.donated_id, sh.sample_id)
        self.assertEqual(self.e.cli.donation()[:2], ("CC-BY-4.0", 1))
        donor = self.e.cli.donation()[2]
        self.assertRegex(donor, "^[0-9a-f-]{36}$")                 # a donor ID, made at the start
        self.assertNotEqual(self.e.cli.donor_new(), donor)
        self.assertNotEqual(self.e.cli.donation()[2], donor)
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
        self.assertEqual(self.e.cli.donation()[:2], (None, 0))

    def test_earlier_offer(self):
        """prompted samples kept before donating was on: offered for the donation, reviewed,
        any left out; own-list and everyday samples never in the offer"""
        P.save_own(["Grünewald"])
        st = flow.State()
        st.set = "words"
        w = self.e.writer(st)
        before = [self.write(w)[0] for _ in range(3)]               # shipped prompts, donating off
        w.reload_sets()
        w.choose_set(P.OWN)
        own, _ = self.write(w)                                     # the own list
        r = self.e.rec
        r.write(flow.SESSION, "en", "em", None, ink(self.rng, n=4))
        every = r.ask("confirm", flow.SESSION, "corrected", "tabby")   # an everyday sample
        r.end(flow.SESSION)
        self.assertEqual(every[0], "ok", every)
        self.assertEqual(self.e.cli.donation()[:2], (None, 0))
        self.e.cli.donation_start("CC0-1.0")
        offer = self.e.cli.donation_earlier()
        self.assertEqual([o[0] for o in offer], [sh.sample_id for sh in before])
        self.assertEqual([o[2] for o in offer], [sh.prompt.text for sh in before])
        self.assertNotIn(own.sample_id, [o[0] for o in offer])
        self.assertEqual(self.e.cli.donation()[1], 0)              # nothing added by itself
        keep = [o[0] for o in offer if o[0] != before[1].sample_id]   # one left out
        self.assertEqual(self.e.cli.donation_add_all(keep), 2)
        self.assertEqual(self.e.cli.donation()[1], 2)
        self.assertEqual([o[0] for o in self.e.cli.donation_earlier()], [before[1].sample_id])  # the offer stays
        self.e.cli.donation_add(keep[0])                           # once only
        self.assertEqual(self.e.cli.donation()[1], 2)
        w.state.donating = True
        w.choose_set("words")
        now, _ = self.write(w)                                     # donated while on: not offered
        self.assertTrue(now.donated_id)
        self.assertEqual([o[0] for o in self.e.cli.donation_earlier()], [before[1].sample_id])
        self.e.cli.donation_erase()                                # a new donation offers them again
        self.e.cli.donation_start("CC0-1.0")
        self.assertEqual(len(self.e.cli.donation_earlier()), 4)

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


class YourHandwriting(Case):
    """spec 003 FR-001 to FR-005: careful and quick samples, pairs, Which one is nicer?"""

    def best_write(self, w):
        p = w.take()
        sh = self.write_to(w, p, fit_ok=0)
        self.assertEqual(sh.status, "kept")
        return p, sh

    def test_careful_quick_and_pairs(self):
        w = self.e.writer()
        w.start_best()
        written = [self.best_write(w) for _ in range(4)]      # careful, careful, quick, its careful half
        steps = [p.written for p, _ in written]
        self.assertEqual(steps, ["careful", "careful", "quick", "careful"])
        quick, half = written[2], written[3]
        self.assertEqual(quick[0].text, half[0].text)          # the same word again, slowly
        self.assertTrue(quick[0].pair and quick[0].pair == half[0].pair)
        self.assertFalse(written[0][0].pair)
        marked = self.e.rec.marked()
        self.assertEqual({sh.sample_id: (p.written, p.pair) for p, sh in written}, marked)
        smp = self.e.rec.sample(quick[1].sample_id)
        self.assertEqual((smp["written"], smp["pair"]), ("quick", quick[0].pair))
        self.assertEqual(len(self.e.cli.samples()), 4)          # also ordinary samples (FR-005)
        w.stop_best()
        p, sh = self.best_write(w)
        self.assertFalse(p.written or p.pair)
        self.assertNotIn(sh.sample_id, self.e.rec.marked())

    def test_redo_of_the_quick_one(self):
        w = self.e.writer()
        w.start_best()
        for _ in range(2):
            self.best_write(w)
        quick_p, quick = self.best_write(w)
        self.assertEqual(w.best["step"], "pair")
        again = w.redo_quick()
        self.assertEqual((again.text, again.written), (quick_p.text, "quick"))
        self.assertNotEqual(again.pair, quick_p.pair)          # a new pair
        self.assertNotIn(quick.sample_id, [r[0] for r in self.e.cli.samples()])

    def test_which_one_is_nicer(self):
        st = flow.State()
        st.set = "words"
        w = self.e.writer(st)
        for _ in range(6):
            self.write(w, fit_ok=0)
        rows = [(r[0], r[1], r[4]) for r in self.e.rec.list()]
        cands = flow.pick_candidates(rows, [], random.Random(1))
        self.assertTrue(cands)
        for ch, places in cands:
            self.assertGreaterEqual(len({sid for sid, _ in places}), 2)
            for sid, k in places:
                self.assertEqual(next(r[2] for r in rows if r[0] == sid)[k], ch)
        pk = flow.load_pick(self.e.rec, rows, [], random.Random(1))
        self.assertIsNotNone(pk)
        (a, sa, ka), (b, sb, kb) = pk["a"], pk["b"]
        self.assertNotEqual(a, b)
        smp = self.e.rec.sample(a)
        self.assertEqual(sa, [smp["strokes"][i] for i in smp["spans"][ka]])
        self.e.rec.prefer(a, b, ka, kb)
        prefs = self.e.rec.preferences()
        self.assertEqual(prefs, [(a, b, ka, kb)])
        # a character already picked comes after the ones not picked yet
        again = flow.pick_candidates(rows, prefs * 3, random.Random(1))
        self.assertEqual(again[-1][0], pk["char"])
        # deleting either sample takes the preference with it
        self.e.rec.forget(b)
        self.assertEqual(self.e.rec.preferences(), [])
        self.assertIsNone(flow.load_pick(self.e.rec, rows[:1], []))

    def test_introduction_once(self):
        st = flow.State()
        self.assertTrue(flow.needs_intro(st, 0))
        st.introduced = True
        self.assertFalse(flow.needs_intro(st, 0))
        old = flow.State()                                     # a store with samples from before
        old.introduced = False
        self.assertFalse(flow.needs_intro(old, 12))
        self.assertTrue(flow.State().introduced)               # saved: never asked again


class Pages(Case):
    """the UI object on a real recognizer (no window): the first start, Your best, Which one
    is nicer?, deleting from the samples and the donation (needs GTK and glassine_ui)"""

    def setUp(self):
        super().setUp()
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import GLib
            from glassine_hwsamples import ui
        except (ImportError, ValueError) as e:
            self.skipTest("no GTK or glassine_ui: %s" % e)
        self.GLib, self.U = GLib, ui
        os.environ["GLASSINE_HANDWRITING_BIN"] = BIN
        client.BIN = BIN

    def settle(self, u, cond=lambda: True, limit=60):
        """runs the main loop until the worker is idle and cond() holds"""
        loop = self.GLib.MainLoop()
        t0 = [0]

        def check():
            t0[0] += 1
            if (not u.busy and not u.refreshing and cond()) or t0[0] > limit * 100:
                loop.quit()
                return False
            return True
        self.GLib.timeout_add(10, check)
        loop.run()
        self.assertTrue(cond())

    def make(self):
        u = self.U.UI(lambda: None)
        u.rec.close()
        u.rec, u.cli = self.e.rec, self.e.cli
        u.writer = flow.Writer(u.rec, u.cli, u.state)
        u.refresh()
        self.settle(u, lambda: u.loaded)
        return u

    def submit(self, u):
        p = u.writer.take()
        u.submit(ink(self.rng, n=max(3, len(p.text))), "device:6.2", (0, 0, 702, 300, 96, 0), p)
        self.settle(u)
        return u.writer.shown

    def test_first_start_and_decline(self):
        u = self.make()
        self.assertEqual((u.page, u.wstep), ("wizard", "w-welcome"))
        u.wizard_next()
        self.assertEqual(u.wstep, "w-try")
        sh = self.submit(u)                                   # Try one: an ordinary prompted sample
        self.assertTrue(u.tried and sh.status == "kept")
        self.assertEqual(self.e.rec.sample(sh.sample_id)["source"], "prompted")
        for step in ("w-private", "w-learning", "w-share"):
            u.wizard_next()
            self.assertEqual(u.wstep, step)
        u.wizard_decline()                                    # Not now: no licence, no donor ID
        self.settle(u)
        self.assertEqual(u.wstep, "w-ready")
        self.assertEqual(self.e.cli.donation()[0], None)
        self.assertEqual(self.e.cli.run("donation", "donor").strip(), "-")
        u.wizard_next()
        self.assertEqual(u.page, "write")
        self.assertTrue(flow.State().introduced)
        self.assertFalse(flow.needs_intro(flow.State(), 1))

    def test_accept_then_delete_takes_both(self):
        u = self.make()
        u.wizard_to("w-licence")
        u.pick_licence("CC0-1.0")
        u.wizard_next()
        self.settle(u, lambda: u.donation[0] == "CC0-1.0")
        self.assertEqual(u.wstep, "w-ready")
        u.wizard_done()
        sh = self.submit(u)
        self.assertTrue(sh.sample_id and sh.donated_id)
        u.refresh()
        self.settle(u, lambda: sh.sample_id in u.donated)    # the grid marks it as donated
        u.delete_sample(sh.sample_id)
        self.settle(u, lambda: not u.samples)
        self.assertEqual(u.donation[1], 0)                    # gone from the donation too
        self.assertEqual(self.e.cli.donation_ids(), set())

    def test_best_and_pick(self):
        u = self.make()
        u.wizard_done()
        u.start_best()
        self.settle(u)
        kinds = []
        for _ in range(4):
            sh = self.submit(u)
            kinds.append(sh.prompt.written)
        self.assertEqual(kinds, ["careful", "careful", "quick", "careful"])
        self.assertEqual(u.careful_today(), 3)
        u.open_best("intro")
        self.settle(u)
        self.assertIsNone(u.writer.best)
        u.refresh()
        self.settle(u)
        u.start_pick()
        self.settle(u, lambda: u.pick is not None)
        u.choose_pick(0)
        self.settle(u, lambda: u.prefs)
        self.assertEqual(len(self.e.rec.preferences()), 1)
        self.assertEqual(u.picks_today(), 1)

    def test_your_font(self):
        """spec 003 Scenario 3: the letters it has, the preview's variants as the font's, the
        missing ones written now, install, a rebuild under a new name, remove"""
        from glassine_hwsamples import fontview
        sys.path.insert(0, str(ROOT.parent / "recognizer" / "tests"))
        import test_letters as TL
        rnd = random.Random(2)
        ss = [TL.sample(t, rnd) for t in ["abcde", "fghij", "klmno"] * 3]
        ss += [TL.sample("l", rnd, styles={"l": k % 3}) for k in range(9)]
        f = self.e.dir / "in.jsonl"
        f.write_text("\n".join([json.dumps({"format": "glassine-handwriting-export", "version": 1})] +
                               [json.dumps({"sample": s, "client": APP_ID, "confirmed": 1700000000 + i,
                                            "source": "prompted"}) for i, s in enumerate(ss)]) + "\n")
        self.e.cli.run("samples", "import", str(f))
        # before any letter model: the counts are there all the same (FR-030)
        st = self.e.cli.letters()
        self.assertEqual(st["state"], "none")
        self.assertTrue(all(st["chars"][c] == (3, 0) for c in "abcdefghijk"))
        self.assertEqual(st["chars"]["l"], (12, 0))
        u = self.make()
        u.wizard_done()
        # opening the page learns the letters in the background, the font stays as it is
        u.open_font()
        self.settle(u, lambda: u.font is not None and not u.letters_job and not u.letters_loading
                    and u.font["status"]["state"] == "current")
        self.assertIsNone(u.font["status"]["font"])
        self.assertIsNotNone(u.font["model"])
        self.assertEqual(self.e.cli.letters()["chars"]["a"][0], 3)
        u.make_font()
        self.settle(u, lambda: not u.font_busy and u.font and u.font["status"]["font"])
        st, model = u.font["status"], u.font["model"]
        self.assertEqual(st["font"], ("My handwriting", 1))
        self.assertEqual(st["state"], "current")
        self.assertEqual({c for c, _, ok in fontview.coverage(model) if ok}, set("abcdefghijklmno"))
        # the preview picks the variants the font's alternates pick
        n = {c: len(d["variants"]) for c, d in model["chars"].items() if d["covered"]}
        self.assertGreaterEqual(n["l"], 2)
        text = "llll ab lxl"
        want, prev = [], None
        for c in text:
            v = TL.rotate(prev, n.get(c, 1)) if c == " " or c in n else None
            want.append(v)
            prev = v
        self.assertEqual(fontview.variants(model, text), want)
        lay = fontview.layout(model, text)
        self.assertIsNone(lay[text.index("x")][1])            # x: the system's font
        self.assertTrue(all(lay[i][2] < lay[i + 1][2] for i in range(len(lay) - 1)))
        # the missing ones, one at a time, written with care
        u.write_missing()
        self.settle(u)
        self.assertEqual(u.page, "write")
        self.assertEqual((u.writer.prompt.text, u.writer.prompt.written), ("p", "careful"))
        # a new name: made again under it, the version counts on
        u.open_best("intro")
        u.open_font()
        self.settle(u, lambda: u.font is not None and not u.letters_job and not u.letters_loading)
        u.font_name = "Notes hand"
        u.make_font()
        self.settle(u, lambda: not u.font_busy and u.font["status"]["font"] == ("Notes hand", 2))
        u.remove_font()
        self.settle(u, lambda: u.font["status"]["font"] is None)

    def test_neaten_page(self):
        """spec 003 Scenario 4 in the app: what is written comes back with the same strokes,
        the original stays, a strength change neatens again, nothing becomes a sample"""
        u = self.make()
        u.wizard_done()
        before = len(self.e.cli.samples())
        u.open_best("intro")
        u.open_neat()
        self.assertEqual((u.page, u.tab), ("neat", "learn"))
        self.assertTrue(self.U.writing(u))
        line, first = 150, 93
        strokes = ink(self.rng, n=4) + [[(x, y + line, *r) for x, y, *r in s] for s in ink(self.rng, n=3)]
        area = (0, 0, 702, 600, line, 0, first)
        u.neaten((strokes, "device:6.2", area))
        self.settle(u, lambda: not u.neat_busy and u.neat_shown)
        self.assertEqual(sorted(u.neat_shown), list(range(len(strokes))))
        for i, s in enumerate(strokes):
            self.assertEqual(len(u.neat_shown[i]), len(s))
        self.assertEqual(self.U.neat_view(u, strokes), [u.neat_shown[i] for i in range(len(strokes))])
        u.show_original(True)
        self.assertEqual(self.U.neat_view(u, strokes), strokes)
        # strength 0 gives the ink back as written
        u.set_strength(0.0, (strokes, "device:6.2", area))
        self.settle(u, lambda: not u.neat_busy)
        for i, s in enumerate(strokes):
            for a, b in zip(u.neat_shown[i], s):
                self.assertAlmostEqual(a[0], b[0], places=3)
                self.assertAlmostEqual(a[1], b[1], places=3)
        # the same ink again: no new call for lines that didn't change
        n = self.e.rec.n
        u.neaten((strokes, "device:6.2", area))
        self.settle(u, lambda: not u.neat_busy)
        self.assertEqual(self.e.rec.n, n)
        # cleared while a call runs: its result is dropped
        u.neaten((strokes[:2], "device:6.2", area))
        u.neat_cleared()
        self.settle(u, lambda: not u.neat_busy)
        self.assertEqual(u.neat_shown, {})
        u.go("write")
        self.assertIsNone(u.neat)
        self.settle(u)
        self.assertEqual(len(self.e.cli.samples()), before)

    def test_neat_lines(self):
        from glassine_hwsamples import neat
        s = lambda y: [(0, y - 10, 0), (5, y, 5), (9, y + 10, 9)]
        self.assertEqual(neat.lines([s(80), s(93), s(100), s(200), s(240), s(400)], 150, 93),
                         {0: [0, 1, 2], 1: [3, 4], 2: [5]})


@unittest.skipUnless(os.environ.get("GLASSINE_HWSAMPLES_WINDOW_TEST") == "1", "opens a window (CI runs it on Xvfb)")
class Window(Case):
    """the real window: the introduction opens on an empty store, Try one places the
    writing area, and the window turns without the parts holding its size"""

    def test_window(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib
        import functools
        from glassine_hwsamples import ui
        rec, cli = client.Recognizer, client.Cli
        client.Recognizer = functools.partial(rec, BIN, env=self.e.env)
        client.Cli = functools.partial(cli, BIN, env=self.e.env)
        self.addCleanup(setattr, client, "Recognizer", rec)
        self.addCleanup(setattr, client, "Cli", cli)
        app = ui.App()
        seen = []

        def step():
            w = app.win
            if not w or not app.ui.loaded:
                return True
            u = app.ui
            if u.page == "wizard" and u.wstep == "w-welcome":
                seen.append(w.stack.get_visible_child_name())
                u.wizard_next()
                return True
            a = w.canvas.get_allocation()
            if u.wstep == "w-try" and not seen[1:] and a.height > 100:
                seen.append(("try", w.stack.get_visible_child_name(), a.width > 600))
                w.resize(936, 702)
                return True
            if seen[1:] and not seen[2:] and w.geom and w.geom[0]:
                seen.append(("turned", a.width < 936 and a.height < 702))
                app.quit()
                return False
            return True
        GLib.timeout_add(200, step)
        GLib.timeout_add(30000, app.quit)
        app.run(["glassine-handwriting-samples"])
        self.assertEqual(seen, ["full", ("try", "write", True), ("turned", True)])


class Drawn(unittest.TestCase):
    """every screen of the design draws, in each voice and turned (needs GTK and glassine_ui)"""

    def test_screens(self):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from glassine_hwsamples import ui
        except (ImportError, ValueError) as e:
            self.skipTest("no GTK or glassine_ui: %s" % e)
        import cairo
        for voice in ("white", "editorial", "stationery"):
            for size in ((702, 936), (1248, 702)):
                for s in ui.SCREENS:
                    u = ui.demo(s, voice)
                    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, *size)
                    hits = ui.paint(cairo.Context(surf), u, *size)
                    self.assertTrue(hits.boxes, (voice, size, s))
                    for x, y, w, h, _ in hits.boxes:
                        if (w, h) != size:                    # a dialog's backdrop
                            self.assertTrue(0 <= x and x + w <= size[0] + 1 and 0 <= y and y + h <= size[1] + 1,
                                            (voice, size, s, x, y, w, h))

    def test_font_preview_fits_its_box(self):
        """the font page's preview, in a hand with wide letters, stays inside its box"""
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from glassine_hwsamples import pages, ui
        except (ImportError, ValueError) as e:
            self.skipTest("no GTK or glassine_ui: %s" % e)
        from glassine_hwsamples import fontview
        f = ui.demo_font(False)
        for d in f["model"]["chars"].values():
            for v in d["variants"]:
                v["width"] = 1.4
        for maxw in (626, 1172):
            xh = pages.hand_fit(f["model"], fontview.PREVIEW, 14, maxw, 56)
            lines, line_h = pages.hand_lines(f["model"], fontview.PREVIEW, xh, maxw)
            m = f["model"]["metrics"]
            self.assertLessEqual(xh * m["ascender"] + (len(lines) - 1) * line_h - xh * m["descender"], 56)
            self.assertGreaterEqual(xh, 6)


if __name__ == "__main__":
    unittest.main(verbosity=1)
