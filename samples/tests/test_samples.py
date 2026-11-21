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
CHARSET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,;:!?'-() @/&+#%_=$"
MARKS = "@/&+#%_=$"  # the next base model's added marks: prompted alone and in context


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

    def test_every_set_choosable_and_its_characters_shown(self):
        """each shipped set is on the sets page (owner and guest), and every character the
        single-character prompts teach has a cell on the coverage page"""
        try:
            from glassine_hwsamples import pages
        except ImportError as e:                # the pages draw with cairo and glassine_ui
            self.skipTest("no cairo or glassine_ui: %s" % e)
        for rows in (pages.SETS, pages.GSETS):
            self.assertLessEqual(set(P.SETS), {r[0] for r in rows})
        for r in pages.SETS + pages.GSETS:
            self.assertIn(r[0], pages.SET_NAMES)
        shown = "".join(c for _, cs in pages.GROUPS for c in cs).replace("’", "'")
        sets = P.load("en")
        for name in ("letters", "digits", "symbols"):
            for p in sets[name]:
                if len(p.text) == 1:
                    self.assertIn(p.text, shown, name)

    def test_marks_prompted_alone_and_in_context(self):
        sets = P.load("en")
        sym = sets["symbols"]
        for c in MARKS:
            alone = [p for p in sym if p.text == c]
            self.assertTrue(alone and alone[0].kind == "character", c)
            self.assertGreaterEqual(sum(c in p.text and p.text != c for p in sym), 2, c)
        cov = P.Coverage()
        for p in sym:
            cov.add(p.text, p.text)
        self.assertTrue(all(cov.count(c) >= 3 for c in MARKS))
        self.assertEqual({c for c, n, _ in cov.table(CHARSET) if n} & set(MARKS), set(MARKS))

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

    def test_written_again_replaces_only_once_kept(self):
        """spec 003 FR-033 in the session: the new ink to the old sample's text, marked as the old
        one was written; a failed check keeps nothing and the old one stays; once kept the old one
        is deleted. The prompt on screen stays"""
        w = self.e.writer()
        w.best = {"step": "quick", "pair": "00112233aabbccdd", "n": 0, "chars": []}
        w.prompt = P.Prompt("garden", "words", "en", written="quick", pair="00112233aabbccdd")
        sh, _ = self.write(w, fit_ok=0)
        old_id = sh.sample_id
        old = self.e.rec.sample(old_id)
        w.best, w.prompt = None, w.next_prompt()
        shown, before = w.prompt, w.shown
        area = (0, 0, 702, 300, 96, 0)
        self.e.rec.close()
        self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = "0.99"
        new = w.rewrite(old_id, old, ink(self.rng, n=6), "device:6.2", area)
        self.assertEqual(new.status, "check")
        self.assertIsNone(new.sample_id)
        self.assertEqual([r[0] for r in self.e.cli.samples()], [old_id])
        self.assertEqual(self.e.rec.sample(old_id), old)            # as it was
        self.e.rec.close()
        self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"
        new = w.rewrite(old_id, old, ink(self.rng, n=6), "device:6.2", area)
        self.assertEqual(new.status, "kept")
        self.assertEqual([r[0] for r in self.e.cli.samples()], [new.sample_id])
        got = self.e.rec.sample(new.sample_id)
        for f in ("text", "lang", "source", "written", "pair"):
            self.assertEqual(got[f], old[f], f)
        self.assertNotEqual(got["strokes"], old["strokes"])
        self.assertEqual(self.e.rec.marked(), {new.sample_id: ("quick", "00112233aabbccdd")})
        self.assertIs(w.prompt, shown)
        self.assertIs(w.shown, before)

    def test_written_again_keeps_the_ways_marks(self):
        """FR-033: the old sample's marks on a character's ways (way-out, favourite) go to the
        new sample; its own out mark goes with it"""
        w = self.e.writer()
        w.prompt = P.Prompt("garden", "words", "en")
        sh, _ = self.write(w, fit_ok=0)
        old_id = sh.sample_id
        old = self.e.rec.sample(old_id)
        self.e.cli.letters_mark("way-out", "g", [old_id])
        self.e.cli.letters_mark("favourite", "d", [old_id])
        self.e.cli.letters_mark("out", "n", [old_id])
        self.e.rec.close()
        new = w.rewrite(old_id, old, ink(self.rng, n=6), "device:6.2", (0, 0, 702, 300, 96, 0))
        self.assertEqual(new.status, "kept")
        self.assertEqual(sorted(self.e.cli.letters_marks()), [(new.sample_id, "d", "favourite"),
                                                             (new.sample_id, "g", "way-out")])

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


class DamagedModel(Case):
    """a letter model with damaged entries (bytes that aren't UTF-8, a line that isn't JSON, a
    character that isn't one) still reads: the app gets its text, the preview leaves those
    entries out with a warning and keeps the rest"""

    def test_left_out_rest_kept(self):
        from glassine_hwsamples import fontview
        import contextlib
        import io
        good = {"format": "glassine-handwriting-letters", "version": 1}
        lines = [json.dumps(good).encode(),
                 b'{"spacing":{"letter":0.3,"word":1.2,"pairs":[["\xfc\x83\x80\xb0","a",0.3,3,null],'
                 b'["a","b",0.3,3,0.9]],"reach":[["\xed\xa0\x80",0.5,0.5,3,3],["a",0.5,0.5,3,3]]}}',
                 b'{"char": not json',
                 b'{"char":"\xff","variants":[]}',
                 b'{"char":"a","samples":3,"covered":true,"variants":[],"rules":[{"prev":"\xfe","variant":0}]}',
                 b'{"joined":"a\xc0","variant":{}}']
        f = self.e.dir / "model.bin"
        f.write_bytes(b"\n".join(lines) + b"\n")
        cli = client.Cli(binary="cat", env=dict(os.environ))
        cli.argv = lambda *a: ["cat", str(f)]
        text = cli.run("letters", "show")                     # no decoding error
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            m = fontview.parse(text)
        self.assertEqual(list(m["chars"]), ["a"])
        self.assertEqual(m["chars"]["a"]["rules"], [])
        self.assertEqual([p[:2] for p in m["spacing"]["pairs"]], [["a", "b"]])
        self.assertEqual([r[0] for r in m["spacing"]["reach"]], ["a"])
        self.assertNotIn("joined", m)
        self.assertGreaterEqual(err.getvalue().count("isn't valid, left out"), 5)
        self.assertNotIn("\ufffd", err.getvalue())


class FontPreview(Case):
    """spec 003 FR-010: the preview shows what shaping shows, with neighbour rules (FR-038) and
    joined pairs (FR-039): the same glyphs (each letter's variant, each pair as one) at the
    same pen positions as hb-shape on the font built from the same model"""

    def test_preview_matches_shaping(self):
        if not shutil.which("hb-shape"):
            self.skipTest("no hb-shape")
        from glassine_hwsamples import fontview
        sys.path.insert(0, str(ROOT.parent / "recognizer" / "tests"))
        import test_letters as TL
        rnd = random.Random(71)
        ss = TL.alphabet_samples(seed=71, per=3)
        ss += [TL.sample("lo", rnd, styles={"o": 1}) for _ in range(8)]
        ss += [TL.sample("zxo", rnd) for _ in range(8)]
        ss += [TL.joined(TL.sample("th", rnd), [0]) for _ in range(5)]
        ss += [TL.spaced_sample(w, rnd) for w in ("hot", "oh to", "tot") * 3]
        f = self.e.dir / "in.jsonl"
        f.write_text("\n".join([json.dumps({"format": "glassine-handwriting-export", "version": 1})] +
                               [json.dumps({"sample": s, "client": APP_ID, "confirmed": 1700000000 + i,
                                            "source": "prompted"}) for i, s in enumerate(ss)]) + "\n")
        self.e.cli.run("samples", "import", str(f))
        self.e.cli.run("letters", "build", "-r")
        text = self.e.cli.letters_model()
        model = fontview.parse(text)
        self.assertIn("th", fontview.joined_pairs(model))
        self.assertTrue(any(r.get("prev") == "l" for r in model["chars"]["o"].get("rules", [])))
        ttf = self.e.dir / "preview.ttf"
        self.e.cli.run("font", "build", "-n", "Preview", str(ttf))
        gl, view = TL.font_glyphs(text), fontview.font_view(model)
        for t in ("lo lot the lot", "hello world lo", "othello lolo th", "the 'lot' Q", "Theo hath lots"):
            sh = json.loads(subprocess.run(["hb-shape", "--no-glyph-names", "--output-format=json", str(ttf), t],
                                           capture_output=True, text=True, check=True).stdout)
            got = []
            for g in sh:
                k = gl.get(g["g"], ("missing",))
                if k[0] == "step":
                    got.append(("letter", k[1], fontview.slots(view["chars"][k[1]])[k[2]]))
                elif k[0] == "variant":
                    got.append(("letter", k[1], k[2]))
                elif k[0] == "joined":
                    got.append(("joined", k[1], None))
                else:
                    got.append((k[0],))
            want = [g if g[0] in ("letter", "joined") else (g[0],) for g in fontview.glyphs(model, t)]
            self.assertEqual(got, want, t)
            if ("missing",) in want:
                continue                # another font sets those: no positions to compare
            xs, x = [], 0
            for g in sh:
                xs.append(x + g["dx"])
                x += g["ax"]
            lay = fontview.layout(model, t)
            self.assertEqual(len(lay), len(xs))
            for (c, _, lx, _, _), hx in zip(lay, xs):
                self.assertAlmostEqual(lx * fontview.XH, hx, delta=1.01, msg=(t, c))


class Pages(Case):
    """the UI object on a real recognizer (no window): the first start, Your best, Which one
    is nicer?, deleting from the samples and the donation (needs GTK and glassine_ui)"""

    def setUp(self):
        Case.setUp(self)
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
        self.assertEqual((u.page, u.tab), ("write", "font"))     # a part of Your font
        kinds = []
        for _ in range(4):
            sh = self.submit(u)
            kinds.append(sh.prompt.written)
        self.assertEqual(kinds, ["careful", "careful", "quick", "careful"])
        self.assertEqual(u.careful_today(), 3)
        u.open_best()                   # back to the font page
        self.settle(u)
        self.assertEqual((u.page, u.tab, u.best), ("font", "font", "font"))
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
        # opening the page shows the counts and offers to learn the letters; asked, they are
        # learned in the background and the font stays as it is
        u.open_font()
        self.settle(u, lambda: u.font is not None and not u.letters_loading)
        self.assertEqual(u.font["status"]["state"], "none")
        self.assertIsNone(u.letters_job)
        u.build_letters()
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
        cov = {c: d for c, d in model["chars"].items() if d["covered"]}
        self.assertGreaterEqual(len(cov["l"]["variants"]), 2)
        text = "llll ab lxl"
        want, prev = [], None
        for c in text:
            if c == " ":
                want.append(0)
                prev = TL.step(prev)
            elif c in cov:
                prev = TL.step(prev)
                want.append(cov[c]["slots"][prev])
            else:
                want.append(None)
                prev = None
        self.assertEqual(fontview.variants(model, text), want)
        self.assertGreater(len(set(want[:4])), 1)                 # 'llll' isn't stamped
        lay = fontview.layout(model, text)
        self.assertIsNone(lay[text.index("x")][1])            # x: the system's font
        self.assertTrue(all(lay[i][2] < lay[i + 1][2] for i in range(len(lay) - 1)))
        # the preview spaces letters as the font does: each pen position where shaping puts it
        ttf = self.e.dir / "preview-check.ttf"
        self.e.cli.run("font", "build", "-n", "Check", str(ttf))
        for text in ("lllll abcde lol", "hello kid"):
            sh = json.loads(subprocess.run(["hb-shape", "--no-glyph-names", "--output-format=json", str(ttf), text], capture_output=True,
                                           text=True, check=True).stdout)
            xs, x = [], 0
            for g in sh:
                xs.append(x + g["dx"])
                x += g["ax"]
            lay = fontview.layout(model, text)
            self.assertEqual(len(lay), len(xs))
            for (c, _, lx, _, _), hx in zip(lay, xs):
                self.assertAlmostEqual(lx * fontview.XH, hx, delta=1.01, msg=(text, c))
        # Write these now: the first missing one's sheet, on Write more, written with care
        u.write_missing()
        self.settle(u)
        self.assertEqual((u.sheet["char"], u.sheet["seg"]), ("p", "write"))
        self.assertTrue(self.U.writing(u))
        self.assertEqual((u.writer.prompt.text, u.writer.prompt.written), ("p", "careful"))
        u.close_char()
        self.settle(u, lambda: not u.relearn and not u.letters_loading)
        # a new name: made again under it, the version counts on
        u.open_font()
        self.settle(u, lambda: u.font is not None and not u.letters_job and not u.letters_loading)
        u.font_name = "Notes hand"
        u.make_font()
        self.settle(u, lambda: not u.font_busy and u.font["status"]["font"] == ("Notes hand", 2))
        u.remove_font()
        self.settle(u, lambda: u.font["status"]["font"] is None)

    def test_font_styles(self):
        """FR-034: the preview draws each style with the font's widths (the same numbers as
        `font widths`); a changed nib angle turns Update on and goes into the font"""
        from glassine_hwsamples import fontview
        sys.path.insert(0, str(ROOT.parent / "recognizer" / "tests"))
        import test_letters as TL
        rnd = random.Random(5)
        # written with a pressure and speed that change along each stroke (Brush follows them)
        ss = [TL.written_sample(t, rnd) for t in ["abcde", "fghij"] * 3]
        f = self.e.dir / "in.jsonl"
        f.write_text("\n".join([json.dumps({"format": "glassine-handwriting-export", "version": 1})] +
                               [json.dumps({"sample": s, "client": APP_ID, "confirmed": 1700000000 + i,
                                            "source": "prompted"}) for i, s in enumerate(ss)]) + "\n")
        self.e.cli.run("samples", "import", str(f))
        self.e.cli.run("letters", "build")
        u = self.make()
        u.wizard_done()
        u.open_font()
        self.settle(u, lambda: u.font is not None and not u.letters_loading and u.font["model"])
        model = u.font["model"]
        # the widths the preview draws are the font's, point for point
        for style, nib in (("regular", 35), ("calligraphy", 35), ("calligraphy", 50), ("brush", 35)):
            out = self.e.cli.run("font", "widths", "--style", style, "--nib", str(nib)).splitlines()
            head = json.loads(out[0])
            self.assertEqual((head["style"].lower(), head["nib"]), (style, nib))
            if style == "calligraphy":
                lo, hi = fontview.nib_widths(model)
                self.assertAlmostEqual(lo, head["nib_min"], delta=1e-4)
                self.assertAlmostEqual(hi, head["nib_max"], delta=1e-4)
            n = 0
            for ln in out[1:]:
                r = json.loads(ln)
                var = model["chars"][r["char"]]["variants"][r["variant"]]
                got = fontview.widths(model, var, r["stroke"], style, nib)
                self.assertEqual(len(got), len(r["widths"]), (style, r))
                for a, b in zip(got, r["widths"]):
                    self.assertAlmostEqual(a, b, delta=1e-4, msg=(style, nib, r["char"]))
                n += 1
            self.assertGreater(n, 10, style)
        var = next(v for d in model["chars"].values() if d["covered"] for v in d["variants"])
        self.assertTrue(var.get("pressure") and var.get("speed"))
        self.assertGreater(len({round(w, 4) for w in fontview.widths(model, var, 0, "brush")}), 3)
        # Install puts the chosen nib angle in; the status says which
        self.assertEqual((u.font_style, u.nib_value()), ("regular", fontview.NIB))
        u.set_font_style("calligraphy")
        u.nib_step(1)
        self.assertEqual(u.nib_value(), 40)
        self.assertFalse(u.nib_changed())                    # nothing installed yet
        u.make_font()
        self.settle(u, lambda: not u.font_busy and u.font and u.font["status"]["font"])
        self.assertEqual(u.font["status"]["font_nib"], 40)
        self.assertIsNone(u.font_nib)
        self.assertEqual(u.nib_value(), 40)
        self.assertEqual(u.font_style, "calligraphy")        # the preview stays in it
        # an installed, current font: Update waits for a change of the angle
        self.assertFalse(u.nib_changed())
        u.nib_step(-1)
        u.nib_step(-1)
        self.assertEqual(u.nib_value(), 30)
        self.assertTrue(u.nib_changed())
        u.nib_step(1)
        u.nib_step(1)
        self.assertFalse(u.nib_changed())                    # back where it is
        for _ in range(20):
            u.nib_step(1)
        self.assertEqual(u.nib_value(), fontview.NIB_MAX)
        for _ in range(20):
            u.nib_step(-1)
        self.assertEqual(u.nib_value(), fontview.NIB_MIN)
        u.make_font()
        self.settle(u, lambda: not u.font_busy and u.font["status"]["font"][1] == 2)
        self.assertEqual(u.font["status"]["font_nib"], fontview.NIB_MIN)
        self.assertFalse(u.nib_changed())

    def test_character_sheet(self):
        """spec 003 FR-031/FR-032 in the app: a key opens its sheet; a favourite, a version left
        out and a sample left out (with Undo) are marks; closing learns the character again,
        the preview follows, the installed font changes only with Update"""
        from glassine_hwsamples import fontview
        sys.path.insert(0, str(ROOT.parent / "recognizer" / "tests"))
        import test_letters as TL
        rnd = random.Random(5)
        ss = [TL.sample(t, rnd) for t in ["abcde", "fghij", "klmno"] * 3]
        ss += [TL.sample("l", rnd, styles={"l": k % 3}) for k in range(9)]
        f = self.e.dir / "in.jsonl"
        f.write_text("\n".join([json.dumps({"format": "glassine-handwriting-export", "version": 1})] +
                               [json.dumps({"sample": s, "client": APP_ID, "confirmed": 1700000000 + i,
                                            "source": "prompted"}) for i, s in enumerate(ss)]) + "\n")
        self.e.cli.run("samples", "import", str(f))
        u = self.make()
        u.wizard_done()
        u.open_font()
        self.settle(u, lambda: u.font is not None and not u.letters_loading)
        u.make_font()                               # Install learns the letters first
        self.settle(u, lambda: not u.font_busy and u.font and u.font["status"]["font"] and not u.letters_loading)
        self.assertEqual(u.font["status"]["state"], "current")
        self.assertTrue(u.font["status"]["font_same"])
        u.open_char("l")
        self.assertEqual(u.sheet["seg"], "versions")
        vs = u.font["model"]["chars"]["l"]["variants"]
        self.assertGreaterEqual(len(vs), 2)
        fav_ids, out_ids = set(vs[-1]["ids"]), set(vs[0]["ids"])
        u.mark_version(len(vs) - 1, "favourite")
        u.mark_version(0, "out")
        self.settle(u)
        kinds = {ln.split("\t")[3] for ln in self.e.cli.run("letters", "marks").splitlines()}
        self.assertEqual(kinds, {"favourite", "way-out"})
        # its samples, one left out and put back with Undo
        u.sheet_seg("samples")
        self.settle(u, lambda: u.sheet["occ"] is not None)
        use = [o for o in u.sheet["occ"] if o[2] == "usable"]
        self.assertTrue(use)
        u.mark_sample(use[0][0])
        self.settle(u)
        self.assertIn("out", self.e.cli.run("letters", "marks"))
        u.undo_out()
        self.settle(u)
        self.assertNotIn("\tout\n", self.e.cli.run("letters", "marks") + "\n")
        # Write more: careful prompts for l alone and in words
        u.sheet_seg("write")
        self.assertTrue(self.U.writing(u))
        self.assertEqual([p.text for p in u.sheet["prompts"]][0], "l")
        self.assertTrue(all(p.written == "careful" for p in u.sheet["prompts"]))
        # what is written there is kept as a careful sample at once
        before = len(self.e.cli.samples())
        p = u.writer.take()
        self.assertEqual(p.text, "l")
        u.submit(ink(self.rng, n=1), "device:6.2", (0, 0, 702, 300, 96, 0), p)
        self.settle(u, lambda: not u.letters_loading)
        sh = u.writer.shown
        if sh.status == "kept":
            self.assertEqual(len(self.e.cli.samples()), before + 1)
            self.assertEqual(u.marked[sh.sample_id][0], "careful")
            self.assertEqual(u.writer.prompt.text, u.sheet["prompts"][1].text)
        # a failed check shows in the sheet, which makes room for it, with its buttons
        import cairo
        probe = lambda: cairo.Context(cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))
        u.writer.shown = None
        plain_hits = self.U.Hits()
        self.U.pages.sheet_write_top(probe(), u, plain_hits, 702)
        u.writer.shown = flow.Shown(p, ink(self.rng, n=1), "device:6.2", (0, 0, 702, 300, 96, 0), [("x", -1.0)],
                                    "check", fit=0.2)
        hits = self.U.Hits()
        self.U.pages.sheet_write_top(probe(), u, hits, 702)
        top = self.U.top_height(u, 702)
        self.assertGreater(len(hits.boxes), len(plain_hits.boxes))  # Keep it, Skip, Redo
        self.assertTrue(all(y + h <= top + 1 for _, y, _, h, _ in hits.boxes))
        u.writer.shown = None
        # closing: l learned again in the background; the preview has it, the font doesn't
        u.close_char()
        self.assertIsNotNone(u.relearn)
        self.settle(u, lambda: not u.relearn and not u.letters_loading)
        st, model = u.font["status"], u.font["model"]
        wrote = sh.status == "kept"         # a new sample: the whole model is out of date (FR-032)
        self.assertEqual(st["state"], "outdated" if wrote else "current")
        self.assertFalse(st["font_same"])
        l = model["chars"]["l"]
        # the marks follow their samples (the versions may come out in another order)
        by = lambda ids: next(v for v in l["variants"] if ids <= set(v["ids"]))   # (the new l may join one)
        self.assertTrue(by(out_ids).get("left_out"))
        self.assertTrue(by(fav_ids).get("favourite"))
        self.assertNotIn(l["variants"].index(by(out_ids)), fontview.slots(l))
        self.assertEqual(self.U.pages.font_mode(u), "outdated" if wrote else "relearned")
        # Update puts the new letters in the font
        u.make_font()
        self.settle(u, lambda: not u.font_busy and u.font["status"]["font"][1] == 2 and not u.letters_loading)
        self.assertTrue(u.font["status"]["font_same"])
        self.assertEqual(self.U.pages.font_mode(u), "installed")

    def test_character_samples_opened(self):
        """spec 003 FR-033 in the app: a sample opened from the sheet shows the tablet's reading
        only when it has one; Write it again replaces it only once the new one is kept (its
        text, source, how it was written and its pair carried; its preferences and its marks
        gone, the other samples' version marks kept), a failed check or Cancel keeps it; Delete
        there is the Samples view's deletion; a version lists exactly its ids"""
        from glassine_hwsamples import pages
        sys.path.insert(0, str(ROOT.parent / "recognizer" / "tests"))
        import test_letters as TL
        rnd = random.Random(5)
        ss = [TL.sample(t, rnd) for t in ["abcde", "fghij", "klmno"] * 3]
        ss += [TL.sample("l", rnd, styles={"l": k % 3}, written="careful") for k in range(12)]
        ss[-1]["written"], ss[-1]["pair"] = "quick", "00112233aabbccdd"
        ss[-4]["pair"] = "00112233aabbccdd"          # its careful half
        del ss[-2]["reading"]
        f = self.e.dir / "in.jsonl"
        f.write_text("\n".join([json.dumps({"format": "glassine-handwriting-export", "version": 1})] +
                               [json.dumps({"sample": s, "client": APP_ID, "confirmed": 1700000000 + i,
                                            "source": "prompted"}) for i, s in enumerate(ss)]) + "\n")
        self.e.cli.run("samples", "import", str(f))
        ids = self.e.cli.samples()
        got = {r[0]: self.e.rec.sample(r[0]) for r in ids}
        old = next(i for i, g in got.items() if g.get("written") == "quick")
        noread = next(i for i, g in got.items() if "reading" not in g)
        u = self.make()
        u.wizard_done()
        u.open_font()
        self.settle(u, lambda: u.font is not None and not u.letters_loading)
        u.build_letters()
        self.settle(u, lambda: not u.letters_job and not u.letters_loading and u.font["model"])
        u.open_char("l")
        vs = u.font["model"]["chars"]["l"]["variants"]
        self.assertGreaterEqual(len(vs), 2)
        vi = next(i for i, v in enumerate(vs) if old in v["ids"])
        others = [i for i in range(len(vs)) if i != vi]
        u.mark_version(vi, "favourite")             # a version mark on the old sample and its company
        u.mark_version(others[0], "out")
        partner = next(i for i in vs[vi]["ids"] if i != old) if len(vs[vi]["ids"]) > 1 else None
        self.settle(u)
        u.rec.prefer(old, vs[others[0]]["ids"][0], 0, 0)
        marks = lambda: {tuple(ln.split("\t")[1:4]) for ln in self.e.cli.run("letters", "marks").splitlines()}
        kept_marks = {m for m in marks() if m[0] != old}
        self.assertIn((old, "l", "favourite"), marks())
        # a version's samples: exactly its ids
        u.version_samples(others[0])
        self.settle(u, lambda: u.sheet["occ"] is not None)
        self.assertEqual([o[0] for o in pages.vlist_occ(u)], vs[others[0]]["ids"])
        self.assertLess(len(pages.vlist_occ(u)), len({o[0] for o in u.sheet["occ"]}))
        u.sheet_back()
        self.assertIsNone(u.sheet["vlist"])
        # the reading only when the sample has one
        u.sheet_seg("samples")
        u.sheet_sample(noread, 0)
        self.settle(u, lambda: u.inks.get(noread))
        self.assertNotIn("The tablet read", [r[0] for r in pages.opened_rows(u)])
        u.sheet_back()
        u.sheet_sample(old, 0)
        self.settle(u, lambda: u.inks.get(old))
        self.assertIn(("The tablet read", "“l”"), pages.opened_rows(u))
        before = self.e.rec.sample(old)

        rng = self.rng

        class Area:
            def __init__(self):
                self.strokes = ink(rng, n=2)

            def payload(self):
                return self.strokes, "device:6.2", (0, 0, 702, 300, 96, 0)

            def clear(self):
                self.strokes = []
        # a failed check, then Cancel: the old one as it was
        self.e.rec.close()
        self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = "0.99"
        u.redo_start()
        self.assertTrue(self.U.redoing(u) and self.U.writing(u))
        u.redo_done(Area())
        self.settle(u)
        self.assertEqual(u.sheet["redo"]["shown"].status, "check")
        u.redo_cancel()
        self.assertFalse(self.U.redoing(u))
        self.assertEqual(self.e.rec.sample(old), before)
        self.assertEqual(len(self.e.cli.samples()), len(ids))
        # kept: in its place
        self.e.rec.close()
        self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"
        u.redo_start()
        u.redo_done(Area())
        self.settle(u, lambda: not u.sheet["redo"] and u.sheet["occ"] is not None and not u.letters_loading)
        new = u.sheet["open"][0]
        self.assertNotEqual(new, old)
        self.assertTrue(u.sheet["redone"] and u.sheet["changed"])
        now = {r[0] for r in self.e.cli.samples()}
        self.assertEqual(now, {r[0] for r in ids} - {old} | {new})
        got = self.e.rec.sample(new)
        for k in ("text", "lang", "source", "written", "pair"):
            self.assertEqual(got[k], before[k], k)
        self.assertNotIn(old, json.dumps(self.e.rec.preferences()))
        self.assertEqual({m for m in marks() if m[0] != new}, kept_marks)    # the version marks stay
        self.assertFalse([m for m in marks() if m[0] == old])
        self.assertFalse([m for m in marks() if m[0] == new and m[2] == "out"])
        self.assertIn((new, "l", "favourite"), marks())                  # the old one's way, carried
        if partner:
            self.assertIn((partner, "l", "favourite"), marks())
        # Delete in the sheet = Delete in Samples: the sample, its marks, its preferences
        a, b = [o[0] for o in u.sheet["occ"] if o[0] not in (new, noread) and o[2] == "usable"][:2]
        for x in (a, b):
            u.rec.prefer(x, new, 0, 0)
        self.e.cli.letters_mark("out", "l", [a, b])
        u.sheet_back()
        u.sheet_sample(a, 0)
        u.ask_sheet_delete()
        self.assertTrue(u.sheet["asking"])
        u.sheet_delete()
        self.settle(u, lambda: not u.letters_loading)
        self.assertIsNone(u.sheet["open"])
        u.delete_sample(b)
        self.settle(u)
        now = {r[0] for r in self.e.cli.samples()}
        for x in (a, b):
            self.assertNotIn(x, now)
            self.assertFalse([m for m in marks() if m[0] == x])
            self.assertNotIn(x, json.dumps(self.e.rec.preferences()))
        self.assertNotIn(a, [o[0] for o in u.sheet["occ"]])        # the sheet's list read again
        # the sheet closed while the new one is checked: it still takes the old one's place, and
        # the character is learned again
        relearned = []
        learn = u.relearn_char
        u.relearn_char = lambda c: (relearned.append(c), learn(c))
        u.sheet_sample(new, 0)
        self.settle(u, lambda: u.inks.get(new))
        u.redo_start()
        u.redo_done(Area())
        u.sheet["changed"] = False                  # nothing else to learn on close
        u.close_char()
        self.settle(u, lambda: not u.relearn and not u.letters_loading and relearned)
        now = {r[0] for r in self.e.cli.samples()}
        self.assertNotIn(new, now)
        self.assertEqual(len(now), len(ids) - 2)
        self.assertEqual(relearned, ["l"])

    def test_neaten_page(self):
        """spec 003 Scenario 4 in the app: what is written comes back with the same strokes,
        the original stays, a strength change neatens again, nothing becomes a sample"""
        u = self.make()
        u.wizard_done()
        before = len(self.e.cli.samples())
        u.open_font()
        u.open_neat()
        self.assertEqual((u.page, u.tab), ("neat", "font"))
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
        # Neatest: each line's own top reading goes as the text (the playground sets it in the
        # font at once, misreads too: FR-043); the text shown is that reading
        asks, rec = [], u.neat.rec

        class Rec:
            def __getattr__(s, name):
                return getattr(rec, name)

            def neaten(s, *a):
                asks.append(a)
                return rec.neaten(*a)
        u.neat.rec = Rec()
        u.set_strength(1.0, (strokes, "device:6.2", area))
        self.settle(u, lambda: not u.neat_busy)
        u.neat.rec = rec
        firsts = [a for a in asks if len(a) == 2]
        self.assertTrue(firsts)
        withtext = [a[2] for a in asks if len(a) == 3]
        self.assertTrue(withtext and all(withtext), asks)
        self.assertEqual(u.neat_text, " · ".join(withtext))
        u.set_strength(0.0, (strokes, "device:6.2", area))
        self.settle(u, lambda: not u.neat_busy)
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

    def test_pen_ink(self):
        """Neaten my writing through the pen interface: the compositor's strokes take the
        place of the pen's own drawing, neat strokes are shown by the page's index, in the
        writing area's coordinates; a stroke without one joins as written"""
        from glassine_hwsamples import neat

        class Pen:
            capabilities = {"strokes", "neaten", "text"}

            def __init__(s):
                s.calls = []

            def __getattr__(s, name):
                return lambda *a: s.calls.append((name, a))
        page, changes = [], []
        pi = neat.PenInk(Pen(), page, lambda: changes.append(1))
        pi.place(10, 20, 300, 200, 150, 93)
        self.assertEqual(pi.pen.calls, [("set_area", (10, 20, 300, 200)), ("set_guides", (150, 113))])
        own = [(1, 2, 0, 0.5, 0, 0)]
        pi.down(own)
        self.assertEqual(pi.pending, [own])
        pi.stroke(0, [(11, 22, 5, 0.5, 1, 2), (12, 23, 6, 0.5, 1, 2)])
        self.assertEqual((pi.pending, page), ([], [[(1, 2, 5, 0.5, 1, 2), (2, 3, 6, 0.5, 1, 2)]]))
        pi.neat(0, "pulled", [(15, 25, 5, 0.5, 1, 2), (16, 26, 6, 0.5, 1, 2)])
        self.assertEqual(pi.shown, {0: [(5, 5), (6, 6)]})
        u = self.make()
        u.page, u.pen_ink = "neat", pi
        self.assertEqual(self.U.neat_view(u, page), [[(5, 5), (6, 6)]])
        mouse = [(3, 3, 0, None, None, None)]
        pi.down(mouse)
        pi.lost(mouse)
        self.assertEqual(page[1], mouse)
        pi.reading("hi", [0])
        self.assertEqual(pi.text, "hi")
        u.set_strength(1.0, ([], "device", ()))
        self.assertEqual(pi.pen.calls[-1], ("set_neaten", (True, 1.0)))
        # Neatest: the page's own neatening is shown (the service sets no word, FR-043)
        u.neat_shown = {0: [(7, 7), (8, 8)]}
        self.assertEqual(self.U.neat_view(u, page), [[(7, 7), (8, 8)], mouse])
        u.neat_shown = {}
        pi.clear()
        self.assertEqual((pi.shown, pi.index, pi.text), ({}, {}, ""))
        # the service leaves: no more neatening through it, the pen's waiting strokes join the page
        self.assertTrue(pi.neatens)
        late = [(4, 4, 0, 0.5, 0, 0)]
        pi.down(late)
        pi.capabilities({"strokes"})
        self.assertFalse(pi.neatens)
        pi.settle()
        self.assertEqual((pi.pending, pi.page[-1]), ([], late))
        # a compositor without the service: the page doesn't take the pen interface
        Pen.capabilities = {"strokes"}
        self.assertFalse(neat.PenInk(Pen(), [], lambda: None).neatens)

    def test_neat_lines(self):
        from glassine_hwsamples import neat
        s = lambda y: [(0, y - 10, 0), (5, y, 5), (9, y + 10, 9)]
        self.assertEqual(neat.lines([s(80), s(93), s(100), s(200), s(240), s(400)], 150, 93),
                         {0: [0, 1, 2], 1: [3, 4], 2: [5]})


class Models(Case):
    """Learn › Models (spec 006) end to end on the real recognizer: a model written by the
    export's writer put in Documents › Handwriting, installed from the page, in use, chosen back
    to shipped, removed; a refused file named in words and nothing installed; not in a guest's
    session"""

    settle, make = Pages.settle, Pages.make

    def setUp(self):
        Pages.setUp(self)
        self.docs = self.e.dir / "docs"
        (self.docs / "Handwriting").mkdir(parents=True)
        self.U.documents_dir = lambda: str(self.docs)

    def export(self, name, **kw):
        sys.path.insert(0, str(ROOT.parent / "recognizer" / "tests"))
        sys.path.insert(0, str(ROOT.parent / "recognizer" / "tools"))
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("no numpy")
        import test_local
        return test_local.export_model(self.docs / "Handwriting" / name, **kw)

    def open(self):
        u = self.make()
        u.go("models")
        self.settle(u, lambda: u.models is not None)
        return u

    def test_install_use_remove(self):
        u = self.open()
        self.assertEqual(u.tab, "learn")
        self.assertEqual(u.models["local"], {"recognizer": None, "aligner": None})
        self.assertEqual(u.models["inuse"]["recognizer"], "shipped")
        f = self.export("replacement.ghm", seed=31)
        M = sys.modules["glassine_hwsamples.models"]
        M.install(u)
        self.settle(u, lambda: u.models["local"]["recognizer"] is not None)
        self.assertEqual(u.models["local"]["recognizer"][0], "ready")
        self.assertEqual((u.models["use"]["recognizer"], u.models["inuse"]["recognizer"]), ("local", "local"))
        self.assertIn("replacement.ghm", u.msg)
        self.assertTrue(f.exists())
        installed = self.e.dir / "data" / "glassine" / "handwriting-models" / "en" / "recognizer.ghm"
        self.assertEqual(installed.read_bytes(), f.read_bytes())
        M.use(u, "recognizer", "shipped")
        self.settle(u, lambda: u.models["use"]["recognizer"] == "shipped")
        self.assertEqual(u.models["inuse"]["recognizer"], "shipped")
        M.use(u, "recognizer", "local")
        self.settle(u, lambda: u.models["use"]["recognizer"] == "local")
        M.ask_remove(u, "recognizer")
        self.assertEqual(u.confirm[2], "Remove")
        u.confirm[3]()
        self.settle(u, lambda: u.models["local"]["recognizer"] is None)
        self.assertEqual(u.models["inuse"]["recognizer"], "shipped")
        self.assertFalse(installed.exists())
        self.assertTrue(f.exists())

    def test_refused_in_words(self):
        u = self.open()
        f = self.docs / "Handwriting" / "newer.ghm"
        b = bytearray((self.e.dir / "models" / "en" / "recognizer.ghm").read_bytes())
        b[4] = 9                                                # a ghm version this engine doesn't read
        f.write_bytes(bytes(b))
        M = sys.modules["glassine_hwsamples.models"]
        u.msg = ""
        M.install(u)
        self.settle(u, lambda: "isn’t installed" in u.msg)
        self.assertIn("another version", u.msg)
        self.assertFalse((self.e.dir / "data" / "glassine" / "handwriting-models").exists())
        self.assertEqual(u.models["use"]["recognizer"], "shipped")
        f.unlink()
        u.msg = ""
        M.install(u)
        self.assertIn("No model file", u.msg)

    def test_words_for_every_reason(self):
        M = sys.modules["glassine_hwsamples.models"]
        for r in ("missing", "unreadable", "too-big", "not-a-model", "format-version", "features", "damaged",
                  "kind-unknown", "not-finite"):
            self.assertNotIn("(", M.why(r, "", "en"), r)
        self.assertIn("neatening", M.why("kind", "aligner", "en"))
        self.assertIn("the space", M.why("alphabet", "q z space", "en"))
        self.assertIn("broken", M.why("alphabet", "twice a", "en"))
        self.assertIn("German", M.why("language", "de", "en"))


class Writers(Case):
    """the owner's list of other writers (spec 002 FR-014, FR-025): private, unique, 1 to 40
    characters, and the name in no store, no file name and no argument"""

    def test_list(self):
        from glassine_hwsamples import writers as WR
        wl = WR.Writers()
        self.assertEqual(wl.items, [])
        self.assertEqual(wl.problem(""), "empty")
        self.assertEqual(wl.problem("   "), "empty")
        self.assertEqual(wl.problem("x" * 41), "long")
        self.assertIsNone(wl.problem("x" * 40))
        wid = self.e.cli.writer_new()
        wl.add(wid, "  Garden   Neighbour ")
        self.assertEqual(wl.name(wid), "Garden Neighbour")
        self.assertEqual(wl.problem("garden neighbour"), "taken")
        self.assertIsNone(wl.problem("garden neighbour", wid))     # renaming to itself
        with self.assertRaises(ValueError):
            wl.add("0123456789abcdef", "GARDEN NEIGHBOUR")
        with self.assertRaises(ValueError):
            wl.add("Neighbour", "Someone")                            # only a store's id goes in the list
        wl.rename(wid, "Neighbour")
        self.assertEqual(WR.Writers().items, [{"id": wid, "name": "Neighbour"}])
        st = os.stat(wl.path)
        self.assertEqual(st.st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(wl.path)).st_mode & 0o777, 0o700)
        store = self.e.dir / "data" / "glassine" / "handwriting-writers"
        self.assertTrue((store / wid).is_dir())
        self.assertFalse(Path(wl.path).resolve().is_relative_to(store.resolve()))
        self.assertFalse(Path(wl.path).resolve().is_relative_to((self.e.dir / "data" / "glassine" / "handwriting")
                                                                 .resolve()))
        self.assertEqual(set(self.e.cli.writers()), {wid})
        wl.remove(wid)
        self.assertEqual(WR.Writers().items, [])

    def test_guest_requests_name_their_store(self):
        """every process and command of a guest's session carries --writer ID first"""
        from glassine_hwsamples import writers as WR
        wid = self.e.cli.writer_new()
        seen = []
        popen, run = subprocess.Popen, subprocess.run

        def rec_popen(argv, *a, **kw):
            seen.append(list(argv))
            return popen(argv, *a, **kw)

        def rec_run(argv, *a, **kw):
            seen.append(list(argv))
            return run(argv, *a, **kw)
        subprocess.Popen, subprocess.run = rec_popen, rec_run
        self.addCleanup(setattr, subprocess, "Popen", popen)
        self.addCleanup(setattr, subprocess, "run", run)
        s = WR.Session(wid, "Neighbour", binary=BIN, env=self.e.env)
        self.addCleanup(s.close)
        w = s.writer
        p = w.take()
        sh = w.submit(ink(self.rng, n=max(3, len(p.text))), "device:6.2", (0, 0, 702, 300, 96, 0), p)
        self.assertEqual(sh.status, "kept")
        s.cli.samples()
        s.cli.donation()
        s.cli.donation_start("CC0-1.0")
        s.cli.export(str(self.e.dir / "out.jsonl"))
        with self.assertRaises(client.Error):
            s.cli.writers()                                    # the list is the owner's alone
        self.assertTrue(seen)
        for argv in seen:
            self.assertEqual(argv[:3], [BIN, "--writer", wid], argv)
            self.assertNotIn("Neighbour", " ".join(argv))
        self.assertEqual(len(self.e.rec.list()), 0)            # nothing reached the owner's store
        self.assertEqual(len(s.rec.list()), 1)


class Guests(Case):
    """a guest's session in the UI object: the owner adds a writer, opens their session, the
    introduction, writing, contributing, Finish, Remove"""

    setUp, settle, submit = Pages.setUp, Pages.settle, Pages.submit

    def make(self):
        u = self.U.UI(lambda: None, kw={"binary": BIN, "env": self.e.env})
        u.rec.close()
        u.rec, u.cli = self.e.rec, self.e.cli
        u.writer = flow.Writer(u.rec, u.cli, u.state)
        u.refresh()
        self.settle(u, lambda: u.loaded)
        u.wizard_done()
        return u

    def add(self, u, name):
        u.open_others()
        u.ask_name()
        u.name_text = lambda: name
        u.use_writer_name()
        self.settle(u, lambda: u.writers.items and name in [w["name"] for w in u.writers.items])
        return next(w["id"] for w in u.writers.items if w["name"] == name)

    def record(self):
        """every argument list the app starts a program with, and what it logs"""
        import contextlib
        import io
        seen = []
        popen, run = subprocess.Popen, subprocess.run

        def rec_popen(argv, *a, **kw):
            seen.append(list(argv))
            return popen(argv, *a, **kw)

        def rec_run(argv, *a, **kw):
            seen.append(list(argv))
            return run(argv, *a, **kw)
        subprocess.Popen, subprocess.run = rec_popen, rec_run
        self.addCleanup(setattr, subprocess, "Popen", popen)
        self.addCleanup(setattr, subprocess, "run", run)
        err = io.StringIO()
        cm = contextlib.redirect_stderr(err)
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        return seen, err

    def test_guest_session(self):
        from glassine_hwsamples import look, pages
        u = self.make()
        seen, err = self.record()
        name = "Grandmother Rose"
        wid = self.add(u, name)
        self.settle(u, lambda: wid in u.wcounts)
        self.assertEqual(u.wcounts[wid][:2], (0, 0))
        u.open_writer(wid)
        self.assertTrue(u.guest)
        self.settle(u, lambda: u.loaded)
        self.assertEqual((u.page, u.wstep, u.gflow), ("wizard", "g-hello", "intro"))
        self.assertEqual([t for t, *_ in look.GUEST_TABS], ["write", "donate"])
        # the header's Finish on every page, the intro's too; two tabs, nothing of the owner's;
        # their own samples beneath Contribute
        import cairo
        for page in ("wizard", "write", "samples", "coverage", "donate", "font", "learn", "others"):
            if page != "wizard":
                u.go(page)
            self.assertNotIn(u.page, ("font", "learn", "coverage", "others"))
            if page == "samples":
                self.assertEqual((u.page, u.tab), ("samples", "donate"))
            hits = self.U.Hits()
            pages.paint_full(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 702, 936)), u, hits, 702, 936)
            self.assertTrue(any(getattr(fn, "__func__", None) is self.U.UI.finish for *_, fn in hits.boxes), page)
        for a in ("adapt", "open_font", "open_neat", "start_pick", "import_latest"):
            getattr(u, a)()
            self.assertNotIn(u.page, ("font", "learn", "neat"))
        # Which one is nicer? isn't offered to a guest, not even as a set
        self.assertNotIn("pick", [n for n, *_ in pages.GSETS])
        u.choose_set("pick")
        self.assertIsNone(u.best)
        # the introduction: hello, try, share, 18 or older, licence; the age is kept nowhere
        u.page, u.gflow = "wizard", "intro"
        u.wizard_to("g-hello")
        u.wizard_next()
        self.assertEqual(u.wstep, "w-try")
        sh = self.submit(u)
        self.assertEqual(sh.status, "kept")
        u.wizard_next()
        self.assertEqual(u.wstep, "w-share")
        u.wizard_next()
        self.assertEqual(u.wstep, "g-age")
        u.wizard_next()                                       # not ticked: stays
        self.assertEqual(u.wstep, "g-age")
        files = lambda: {p: p.read_bytes() for p in self.e.dir.rglob("*") if p.is_file() and "models" not in p.parts}
        before = files()
        u.toggle_adult()
        self.assertTrue(u.adult)
        self.assertEqual(files(), before)                     # ticking writes nothing anywhere
        u.wizard_next()
        self.assertEqual(u.wstep, "w-licence")
        self.assertFalse(u.adult)
        u.pick_licence("CC0-1.0")
        u.wizard_next()
        self.settle(u, lambda: u.donation[0] == "CC0-1.0")
        self.assertEqual(u.page, "write")
        for p, b in files().items():
            self.assertNotIn(b"adult", b, p)
            if p.name != "writers.json":                       # the list, the name's one place
                self.assertNotIn(name.encode(), b, p)
            self.assertNotIn(name, str(p))
        sh = self.submit(u)
        self.assertTrue(sh.sample_id and sh.donated_id)
        self.assertEqual(self.e.cli.samples(), [])            # the owner's store untouched
        # Finish: the contribution saved, back in the owner's session, the done note
        u.finish()
        self.assertFalse(u.guest)
        self.settle(u, lambda: u.done and not u.done.get("busy"))
        saved = u.done["saved"]
        self.assertTrue(saved and os.path.isfile(saved), u.done)
        self.assertEqual(os.path.basename(os.path.dirname(saved)), self.U.CONTRIB_DIR)
        self.assertRegex(os.path.basename(saved), r"^handwriting-contribution-\d+\.jsonl$")
        self.assertNotIn(name, Path(saved).read_text(encoding="utf-8"))
        self.assertEqual(u.done["samples"], 2)
        gc = client.Cli(BIN, env=self.e.env, writer=wid)
        self.assertEqual(gc.donation()[:2], ("CC0-1.0", 0))  # the next one starts empty, same licence
        # opened again: no introduction, the place kept
        u.close_done()
        u.open_writer(wid)
        self.settle(u, lambda: u.loaded)
        self.assertEqual(u.page, "write")
        self.assertEqual(u.counts[1], 2)
        u.finish()
        self.settle(u, lambda: u.done and not u.done.get("busy"))
        self.assertIsNone(u.done["saved"])                    # nothing new to save
        u.ask_name(wid)
        u.name_text = lambda: name + " B"
        u.use_writer_name()
        u.ask_remove(wid)
        self.settle(u, lambda: u.removing)
        u.remove_writer()
        self.settle(u, lambda: not u.writers.items)
        # the name in no argument and no log line, through add, the session, Finish, rename, Remove
        self.assertTrue(seen)
        for argv in seen:
            self.assertNotIn("Rose", " ".join(map(str, argv)), argv)
            if "--writer" in argv:
                self.assertEqual(argv[1:3], ["--writer", wid])
        self.assertTrue(any("--writer" in a for a in seen))
        self.assertNotIn("Rose", err.getvalue())

    def test_owner_learning_across_a_guest(self):
        """the owner's learning run, started before a guest's session opens, ends as the
        owner's: Learn is not left running after Finish"""
        u = self.make()
        for _ in range(3):
            self.submit(u)
        wid = self.add(u, "Pat")
        u.start_adapt()
        self.assertTrue(u.adapting)
        u.open_writer(wid)
        self.settle(u, lambda: u.loaded and not u.adapting)
        self.assertTrue(u.guest)
        u.finish()
        self.settle(u, lambda: u.done and not u.done.get("busy"))
        self.assertIsNone(u.adapting)
        self.assertIsNotNone(u.result)
        self.assertEqual(client.Cli(BIN, env=self.e.env, writer=wid).samples(), [])

    def test_finish_during_intro_and_decline(self):
        u = self.make()
        wid = self.add(u, "Sam")
        u.open_writer(wid)
        self.settle(u, lambda: u.loaded)
        self.assertEqual(u.wstep, "g-hello")
        u.finish()                                            # Finish works during the introduction
        self.assertFalse(u.guest)
        self.settle(u, lambda: u.done and not u.done.get("busy"))
        u.open_writer(wid)
        self.settle(u, lambda: u.loaded)
        self.assertEqual(u.page, "write")                     # shown once
        u.go("donate")
        u.turn_on()
        self.assertEqual((u.wstep, u.gflow), ("g-age", "tab"))
        u.wizard_decline()                                    # Not now is one tap, as Continue is
        self.settle(u)
        self.assertEqual(u.page, "donate")
        self.assertFalse(u.state.donating)
        self.assertEqual(client.Cli(BIN, env=self.e.env, writer=wid).run("donation", "donor").strip(), "-")

    def test_remove(self):
        u = self.make()
        wid = self.add(u, "Kim")
        u.open_writer(wid)
        self.settle(u, lambda: u.loaded)
        u.guest_done()
        u.state.donating = True
        u.cli.donation_start("CC0-1.0")
        self.submit(u)
        self.submit(u)
        st_path = flow.state_path(wid)
        self.assertTrue(os.path.exists(st_path))
        u.enter(u.owner)                                     # left without Finish: unsaved stays
        u.ask_remove(wid)
        self.settle(u, lambda: u.removing)
        rm = u.removing
        self.assertEqual((rm["name"], rm["samples"], rm["unsaved"]), ("Kim", 2, 2))
        u.save_for_removal()
        self.settle(u, lambda: rm["saved"])
        self.assertTrue(os.path.isfile(rm["saved"]))
        self.assertEqual((rm["unsaved"], rm["files"]), (0, 1))
        self.assertIs(u.removing, rm)                         # the question stays open
        u.remove_writer()
        self.settle(u, lambda: not u.writers.items)
        self.assertEqual(self.e.cli.writers(), {})
        self.assertFalse(os.path.exists(st_path))
        self.assertTrue(os.path.isfile(rm["saved"]))          # the saved file stays
        self.assertFalse(self.e.cli.writer_remove(wid))       # gone: unknown-writer

    def test_owner_erase_all_keeps_writers(self):
        u = self.make()
        self.submit(u)
        wid = self.add(u, "Lee")
        u.open_writer(wid)
        self.settle(u, lambda: u.loaded)
        u.guest_done()
        self.submit(u)
        u.finish()
        self.settle(u, lambda: u.done and not u.done.get("busy"))
        u.refresh()
        self.settle(u, lambda: u.samples)
        u.ask_erase_all()
        self.assertIn("Other writers’ samples are kept.", u.confirm[1])
        u.confirm[3]()
        self.settle(u, lambda: not u.samples)
        self.assertEqual(self.e.cli.samples(), [])
        self.assertEqual(len(client.Cli(BIN, env=self.e.env, writer=wid).samples()), 1)
        self.assertEqual(self.e.cli.writers()[wid][0], 1)


@unittest.skipUnless(os.environ.get("GLASSINE_HWSAMPLES_WINDOW_TEST") == "1", "opens a window (CI runs it on Xvfb)")
class Window(Case):
    """the real window: the introduction opens on an empty store, Try one places the
    writing area, and the window turns without the parts holding its size; a second start
    shows this window (one instance, on the session bus); a guest writer added in the private
    field, their session entered, written in, and finished"""

    def test_window(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk
        import functools
        from glassine_hwsamples import ui
        rec, cli = client.Recognizer, client.Cli
        client.Recognizer = functools.partial(rec, BIN, env=self.e.env)
        client.Cli = functools.partial(cli, BIN, env=self.e.env)
        self.addCleanup(setattr, client, "Recognizer", rec)
        self.addCleanup(setattr, client, "Cli", cli)
        app = ui.App()
        seen = []
        acts = []
        app.connect("activate", lambda a: acts.append(a.win))
        second = []
        guest = {}

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
                w.resize(702, 936)
                u.wizard_done()
                u.open_font()
                return True
            sa = w.stack.get_allocation()
            if seen[2:] and not seen[3:] and u.font is not None and not u.sheet and sa.width < sa.height:
                # a character's sheet on Write more: the writing area inside the sheet
                u.open_char("Q")
                return True
            if seen[2:] and not seen[3:] and u.sheet and w.stack.get_visible_child_name() == "write" and \
                    a.height > 100:
                seen.append(("sheet", u.writer.prompt.text, a.width > 600))
                u.close_char()
                u.rename_font()
                return True
            if seen[3:] and not seen[4:] and w.entry.get_visible():
                ea = w.entry.get_allocation()
                seen.append(("rename", w.stack.get_visible_child_name(), ea.y < sa.height / 2, w.entry.get_text()))
                w.entry.set_text("Garden letters")
                u.use_name()
                seen.append(("named", u.font_name, w.entry.get_visible()))
                # opened again while it runs: the same window, as it is
                second.append(subprocess.Popen([sys.executable, "-c", "import sys; from glassine_hwsamples import ui; "
                                                "sys.exit(ui.App().run(['glassine-handwriting-samples']))"],
                                               env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))))
                return True
            if second and not seen[6:]:
                if second[0].poll() is None or len(acts) < 2:
                    return True
                seen.append(("again", second[0].returncode, acts[-1] is w, u.page))
                u.open_others()
                u.ask_name()
                return True
            if seen[6:] and not seen[7:] and w.name_entry.get_visible():
                ne = w.name_entry
                seen.append(("private", ne.get_input_purpose() == Gtk.InputPurpose.PASSWORD, ne.get_visibility(),
                             bool(ne.get_input_hints() & Gtk.InputHints.NO_SPELLCHECK)))
                ne.set_text("Rose")
                ne.activate()
                return True
            if seen[7:] and not seen[8:] and u.writers.items and not u.busy:
                guest["id"] = u.writers.items[0]["id"]
                seen.append(("added", u.writers.name(guest["id"]), w.name_entry.get_visible(),
                             w.name_entry.get_text()))
                u.open_writer(guest["id"])
                return True
            if seen[8:] and not seen[9:] and u.loaded and u.guest:
                seen.append(("guest", u.page, u.wstep))
                u.guest_done()
                p = u.writer.take()
                u.submit(ink(self.rng, n=max(3, len(p.text))), "device:6.2", (0, 0, 702, 300, 96, 0), p)
                return True
            if seen[9:] and not seen[10:] and not u.busy and u.writer.shown:
                seen.append(("wrote", u.writer.shown.status))
                u.finish()
                return True
            if seen[10:] and u.done and not u.done.get("busy"):
                seen.append(("finished", u.guest, u.done["name"], u.done["samples"], app.win is w))
                app.quit()
                return False
            return True
        GLib.timeout_add(200, step)
        GLib.timeout_add(60000, app.quit)
        app.run(["glassine-handwriting-samples"])
        self.assertEqual(seen, ["full", ("try", "write", True), ("turned", True), ("sheet", "Q", True),
                                ("rename", "full", True, "My handwriting"), ("named", "Garden letters", False),
                                ("again", 0, True, "font"), ("private", True, True, True), ("added", "Rose", False, ""),
                                ("guest", "wizard", "g-hello"), ("wrote", "kept"), ("finished", False, "Rose", 1, True)])
        self.assertEqual(self.e.cli.samples(), [])          # the guest's sample is in their store alone


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

    def test_opened_sample_states(self):
        """a sample opened in the sheet, drawn in each state, upright and turned: a failed check
        while it is written again (the writing area keeps its room, Cancel waits while it is
        checked), written again, one that can't be used (no Leave out, its reason), one left
        out (Put back)"""
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from glassine_hwsamples import pages, ui
        except (ImportError, ValueError) as e:
            self.skipTest("no GTK or glassine_ui: %s" % e)
        import cairo

        def drawn(u, size):
            hits = ui.paint(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, *size)), u, *size)
            for x, y, w, h, _ in hits.boxes:
                if (w, h) != size:
                    self.assertTrue(0 <= x and x + w <= size[0] + 1 and 0 <= y and y + h <= size[1] + 1, (size, x, y))
            return len(hits.boxes)
        for size in ((702, 936), (1248, 702)):
            u = ui.demo("char.redo")
            p = P.Prompt("water", "own", "en")
            u.sheet["redo"]["shown"] = flow.Shown(p, ui.demo_ink("water", 2)[0], "em", (0, 0, 1, 1, 96, 0),
                                                  [("wader", -1.0)], "check", fit=0.2)
            drawn(u, size)
            lay = ui.layout(u, *size)
            self.assertGreater(lay["area"][3], 200, size)
            n = drawn(u, size)
            u.sheet["redo"]["busy"] = True
            u.sheet["redo"]["shown"] = None
            self.assertEqual(drawn(u, size), n - 1 - 1)  # no Write it again, Cancel waits
            u = ui.demo("char.sample")
            usable = drawn(u, size)
            u.sheet["redone"] = True
            self.assertEqual(drawn(u, size), usable)
            occ = u.sheet["occ"]
            u.sheet["open"] = (occ[3][0], occ[3][1])            # "little": joined to the next t
            self.assertEqual(drawn(u, size), usable - 1)        # no Leave out
            self.assertIn(("In your font", "Can’t be used: joined to the next t"), pages.opened_rows(u))
            u.sheet["occ"] = [o if i != 2 else (o[0], o[1], "out", -1, o[4]) for i, o in enumerate(occ)]
            u.sheet["open"] = (occ[2][0], occ[2][1])
            self.assertEqual(drawn(u, size), usable)            # Put back
            self.assertIn(("In your font", "Left out"), pages.opened_rows(u))
            self.assertTrue(pages.opened_note(u.opened()[2]).startswith("Left out"))

    def test_font_style_controls(self):
        """FR-034: the style control where the design puts it (upright in the preview's head
        row, turned above the keys), the nib stepper only for Calligraphy, Update on once the
        angle of an installed font changes"""
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from glassine_hwsamples import pages, ui
        except (ImportError, ValueError) as e:
            self.skipTest("no GTK or glassine_ui: %s" % e)
        import cairo

        def taps(u, size):
            return ui.paint(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, *size)), u, *size)
        for size in ((702, 936), (1248, 702)):
            u = ui.demo("font.installed")
            self.assertEqual(u.font_style, "regular")
            self.assertIn("in all three styles", pages.font_status_line(u, pages.font_mode(u)))
            hits = taps(u, size)
            n = len(hits.boxes)
            # the two styles not chosen are targets of one width, one segment apart, upper half
            row = {}
            for b in hits.boxes:
                if b[3] == pages.BTN_H and b[1] < size[1] / 2 and b[2] < pages.STYLE_W / 2:
                    row.setdefault(round(b[1]), []).append(b)
            two = next(r for r in row.values() if len(r) == 2 and abs(r[0][2] - r[1][2]) < 1)
            max(two, key=lambda b: b[0])[4]()
            self.assertEqual(u.font_style, "brush")
            # Brush: no stepper; Calligraphy: − and + (two more targets)
            u.set_font_style("brush")
            self.assertEqual(len(taps(u, size).boxes), n)
            u.set_font_style("calligraphy")
            self.assertEqual(len(taps(u, size).boxes), n + 2)
            self.assertFalse(u.nib_changed())
            upd = len(taps(u, size).boxes)
            u.nib_step(1)
            self.assertTrue(u.nib_changed())
            self.assertEqual(len(taps(u, size).boxes), upd + 1)      # Update is on
            self.assertEqual(pages.font_status_line(u, pages.font_mode(u)),
                             "Installed, version 3. Update puts the new nib angle in it.")
        # the demo screens of the styles
        for s, st in (("font.style-calligraphy", "calligraphy"), ("font.style-brush", "brush")):
            self.assertEqual(ui.demo(s).font_style, st)

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
