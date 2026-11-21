#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Writing regions (spec 005) against a scripted recognizer: each test stroke is one word
(its text known to the stand-in by the stroke's points), a stroke named "!strike" or
"!caret" is a gesture. It checks the value (whole, lines in reading order), gestures acting
in their region only, reports and samples (only raw ink that fits its text, only with the
permission, never set words or handed-back ink), text edits back in ink mode (unchanged words
keep their ink, edited words set in the font at their place, the ink after them moving by the
difference, the whole value set when no word matches), the saved state and stored ink
(confirmed and edited words never read again), no reading when a region moves, private
regions and FR-019 (no `neaten` request carries a text).

    python3 tests/test_regions.py
"""
import asyncio
import base64
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from glassine_hwservice import perms, regions  # noqa: E402
from glassine_hwservice.engine import unesc  # noqa: E402

XH = 10.0          # the test writing's small letters, in the region's points


def word_stroke(x, base, text, n=8):
    """a stroke one word wide (6 per letter) on a line, its letters' height"""
    top, bot = regions.tops(text)
    w = 6.0 * max(1, len(text))
    pts = []
    for i in range(n):
        f = i / (n - 1)
        y = base - top * XH if i % 2 else base - bot * XH
        pts.append([round(x + f * w, 3), round(y, 3), 1000 + 7 * i, 0.5, 0, 0])
    return pts


class FakeEngine:
    """the recognizer's line contract, scripted: a session's reading is its strokes' words
    by line (by y), left to right"""

    def __init__(self):
        self.gen = 1
        self.label = {}         # stroke key (its points) -> word
        self.sessions = {}      # name -> {"strokes": {n: pts}, "next": n}
        self.log = []
        self.samples = []
        self.fail_fit = set()   # words whose ink doesn't fit

    def name(self, pts, text):
        self.label[json.dumps(pts)] = text
        return pts

    async def ask(self, client, verb, *f):
        self.log.append((verb,) + f)
        if verb == "begin":
            self.sessions[f[0]] = {"strokes": {}, "next": 0}
            return [verb, "ok"]
        if verb == "set":
            t = unesc(f[0])
            w = 0.6 * len(t)
            return [verb, "ok", "%g" % w, "2.0", "0.1", "1", "0,0 %g,1" % w]
        s = self.sessions.get(f[0])
        if verb == "end":
            self.sessions.pop(f[0], None)
            return [verb, "ok"]
        if s is None:
            return [verb, "unknown-session"]
        if verb == "stroke":
            pts = [[float(v) for v in p.split(",")] for p in f[1].split(" ")]
            n = s["next"]
            s["next"] += 1
            s["strokes"][n] = pts
            return [verb, "ok", str(n)]
        if verb == "remove":
            for n in f[1].split(","):
                s["strokes"].pop(int(n), None)
            return [verb, "ok", "1"]
        if verb == "read":
            return self.read(s, int(f[1]))
        if verb == "align":
            t = unesc(f[1])
            return [verb, "0.3" if t in self.fail_fit else "0.9", "0"]
        if verb == "confirm":
            text = unesc(f[2])
            self.samples.append((f[1], text, [self.word(p) for p in s["strokes"].values()]))
            return [verb, "ok", "1"]
        if verb == "neaten":
            out = [verb, "ok", "-", str(len(s["strokes"]))]
            for i, (n, pts) in enumerate(sorted(s["strokes"].items())):
                out += [str(i), "neat", " ".join("%g,%g" % (p[0], p[1]) for p in pts)]
            return out
        return [verb, "ok"]

    def word(self, pts):
        for k, v in self.label.items():
            ref = json.loads(k)
            if len(ref) == len(pts) and all(abs(a[0] - b[0]) < 1e-3 and abs(a[1] - b[1]) < 1e-3
                                            for a, b in zip(ref, pts)):
                return v
        return "?"

    def read(self, s, count):
        st = s["strokes"]
        if not st:
            return ["read", "empty"]
        newest = max(st)
        g = self.word(st[newest])
        if g.startswith("!"):
            del st[newest]
            gp = self._last_gesture
            x0, x1 = min(p[0] for p in gp), max(p[0] for p in gp)
            y0, y1 = min(p[1] for p in gp), max(p[1] for p in gp)
            cov = [str(n) for n, p in st.items() if min(q[0] for q in p) < x1 and max(q[0] for q in p) > x0
                   and min(q[1] for q in p) <= y1 + XH and max(q[1] for q in p) >= y0 - XH] if g == "!strike" else []
            return ["read", "gesture", g[1:], str(newest), "%g,%g,%g,%g" % (x0, y0, x1, y1),
                    ",".join(cov) or "-", "-"]
        items = sorted(st.items(), key=lambda kv: (round(max(p[1] for p in kv[1]) / (3 * XH)), kv[1][0][0]))
        text, align = [], []
        for n, pts in items:
            w = self.word(pts)
            if text:
                text.append(" ")
                align.append("")
            for _ in w:
                align.append(str(n))
            text.append(w)
        t = "".join(text)
        out = ["read", "text", "1", t, "-1.0", ";".join(align), "model", "ok"]
        for i in range(1, count):
            out[2] = str(i + 1)
            out += [t + "x" * i, "-%d.0" % (i + 1), ";".join(align + [str(n)] * i), "model", "ok"]
        return out


class FakePen:
    def __init__(self):
        self.engine = FakeEngine()
        self.out = []
        self.tasks = []

    def spawn(self, coro):
        self.tasks.append(asyncio.ensure_future(coro))

    def send(self, m):
        self.out.append(m)

    def ops(self, op):
        return [m for m in self.out if m["op"] == "region_" + op]


class FakeSurface:
    def __init__(self, pen):
        self.pen, self.sid, self.app = pen, 1, "org.example.Notes"


class Regions(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.pen = FakePen()
        self.surface = FakeSurface(self.pen)
        self.perm = "allowed"
        self._state = perms.state
        perms.state = lambda app, what: self.perm
        self.k = 0
        regions.Region.person_mm = 0.0

    def tearDown(self):
        perms.state = self._state
        self.loop.close()

    def run_(self, coro):
        return self.loop.run_until_complete(coro)

    def region(self, rid=1, **m):
        g = regions.Region(self.surface, rid)
        self.loop.call_soon(lambda: None)
        m = dict({"reading": True, "size": [400, 200], "units": "device:5"}, **m)
        self.run_(self._set(g, m))
        return g

    async def _set(self, g, m):
        g.set(m)

    def write(self, g, x, base, text):
        self.k += 1
        pts = self.pen.engine.name(word_stroke(x, base, text), text)
        self.run_(self._stroke(g, self.k, pts))
        return self.k

    async def _stroke(self, g, k, pts):
        g.stroke(k, pts)
        g.cancel()              # the tests run the reading themselves

    def gesture(self, g, kind, x0, x1, y):
        self.k += 1
        pts = [[x0, y, 0, 0.5, 0, 0], [x1, y + 0.5, 7, 0.5, 0, 0]]
        self.pen.engine.label[json.dumps(pts)] = "!" + kind
        self.pen.engine._last_gesture = pts
        self.run_(self._stroke(g, self.k, pts))
        return self.k

    def restore(self, g, strokes, state):
        if g.restore(strokes, state):
            self.run_(g.restored())

    def value(self):
        """the newest value, each word with its text and line (from start, length in UTF-8
        bytes of the text)"""
        v = json.loads(json.dumps(self.pen.ops("value")[-1]))
        b = v["text"].encode()
        for w in v["words"]:
            w["text"] = b[w["start"]:w["start"] + w["length"]].decode()
            w["line"] = b[:w["start"]].count(b"\n")
            self.assertEqual(("strokes" in w) + ("path" in w), 1)
        return v

    # ------------------------------------------------------------------ the value

    def test_value_whole_lines_in_reading_order(self):
        g = self.region()
        b = self.write(g, 100, 40, "world")
        a = self.write(g, 20, 40, "hello")
        c = self.write(g, 20, 80, "again")
        self.run_(g.update())
        v = self.value()
        self.assertEqual(v["text"], "hello world\nagain")
        self.assertEqual([(w["text"], w["strokes"], w["line"]) for w in v["words"]],
                         [("hello", [a], 0), ("world", [b], 0), ("again", [c], 1)])
        self.assertTrue(v["state"])
        n = len(self.pen.ops("value"))
        self.run_(g.update())                       # nothing changed: nothing sent
        self.assertEqual(len(self.pen.ops("value")), n)

    def test_candidates_at_a_pause_and_a_pick(self):
        g = self.region()
        a = self.write(g, 20, 40, "one")
        self.run_(g.pause())
        c = self.pen.ops("candidates")[-1]
        self.assertEqual([i["text"] for i in c["items"]][:2], ["one", "onex"])
        self.assertEqual(c["r"], 1)
        self.assertFalse(any(s for s in self.pen.engine.sessions))        # an idle region holds none
        self.run_(g.report({"serial": c["serial"], "action": "picked", "rank": 1}))
        self.assertEqual(self.pen.ops("report")[-1]["result"], "stored")
        self.assertEqual(self.value()["text"], "onex")
        self.assertEqual(self.pen.engine.samples, [("picked", "onex", ["one"])])
        self.assertEqual([w.kind for w in g.words], ["kept"])
        self.assertEqual(self.value()["words"][0]["strokes"], [a])
        # a report never ends the writing, and a confirmed word is never read again
        self.write(g, 80, 40, "two")
        self.run_(g.update())
        self.assertEqual(self.value()["text"], "onex two")

    def test_regions_never_share(self):
        g1, g2 = self.region(1), self.region(2)
        self.write(g1, 20, 40, "alpha")
        self.write(g2, 20, 40, "beta")
        self.run_(g1.update())
        self.run_(g2.update())
        by = {m["r"]: m["text"] for m in self.pen.ops("value")}
        self.assertEqual(by, {1: "alpha", 2: "beta"})
        self.assertNotEqual(g1.name, g2.name)
        # a strike in region 2 over where region 1 has its word acts in region 2 only
        self.gesture(g2, "strike", 15, 60, 35)
        self.run_(g2.update())
        self.assertEqual(self.value()["r"], 2)
        self.assertEqual(self.value()["text"], "")
        self.assertEqual(g1.value(), "alpha")

    # ------------------------------------------------------------------ gestures

    def test_strike_deletes_words_and_their_ink_leaves(self):
        g = self.region()
        a = self.write(g, 20, 40, "keep")
        b = self.write(g, 80, 40, "drop")
        self.run_(g.update())
        s = self.gesture(g, "strike", 78, 110, 35)
        self.run_(g.update())
        v = self.value()
        self.assertEqual(v["text"], "keep")
        self.assertEqual(sorted(v["gone"]), sorted([b, s]))
        gs = self.pen.ops("gesture")[-1]
        self.assertEqual((gs["kind"], gs["strokes"], gs["serial"]), ("strike", [s], v["serial"]))
        self.assertEqual(v["words"][0]["strokes"], [a])

    def test_strike_over_a_confirmed_word(self):
        g = self.region()
        self.write(g, 20, 40, "one")
        self.write(g, 80, 40, "two")
        self.run_(g.pause())
        self.run_(g.report({"serial": self.pen.ops("candidates")[-1]["serial"], "action": "accepted"}))
        self.gesture(g, "strike", 18, 40, 35)
        self.run_(g.update())
        self.assertEqual(self.value()["text"], "two")

    def test_caret_inserts_where_it_points(self):
        g = self.region()
        self.write(g, 20, 40, "one")
        self.write(g, 120, 40, "three")
        self.run_(g.update())
        self.gesture(g, "caret", 60, 66, 42)
        self.run_(g.update())
        self.write(g, 200, 80, "two")
        self.run_(g.update())
        self.assertEqual(self.value()["text"], "one two three")

    # ------------------------------------------------------------------ samples

    def test_samples_only_with_permission_and_fitting_raw_ink(self):
        g = self.region()
        self.write(g, 20, 40, "fits")
        self.write(g, 80, 40, "nofit")
        self.pen.engine.fail_fit.add("nofit")
        self.run_(g.pause())
        self.run_(g.report({"serial": self.pen.ops("candidates")[-1]["serial"], "action": "accepted"}))
        self.assertEqual([s[1] for s in self.pen.engine.samples], ["fits"])

    def test_not_allowed(self):
        self.perm = "refused"
        g = self.region()
        self.write(g, 20, 40, "one")
        self.run_(g.pause())
        self.run_(g.report({"serial": self.pen.ops("candidates")[-1]["serial"], "action": "accepted"}))
        self.assertEqual(self.pen.ops("report")[-1]["result"], "not_allowed")
        self.assertEqual(self.pen.engine.samples, [])
        self.assertEqual(self.value()["text"], "one")          # the writing stays

    def test_stale_report(self):
        g = self.region()
        self.write(g, 20, 40, "one")
        self.run_(g.pause())
        self.run_(g.report({"serial": 99, "action": "accepted"}))
        self.assertEqual(self.pen.ops("report")[-1]["result"], "stale")

    def test_report_covers_only_the_writing_it_was_read_from(self):
        g = self.region()
        self.write(g, 20, 40, "one")
        self.run_(g.pause())
        serial = self.pen.ops("candidates")[-1]["serial"]
        self.write(g, 80, 40, "later")
        self.run_(g.update())
        self.run_(g.report({"serial": serial, "action": "accepted"}))
        self.assertEqual([s[1] for s in self.pen.engine.samples], ["one"])
        self.assertEqual([w.text for w in g.words], ["one"])
        self.assertEqual([w.text for w in g.read], ["later"])

    # ------------------------------------------------------------------ text edits

    def test_correction_sets_the_edited_word_and_moves_the_ink_after_it(self):
        g = self.region()
        a = self.write(g, 20, 40, "the")
        b = self.write(g, 60, 40, "cat")
        c = self.write(g, 100, 40, "sat")
        self.run_(g.pause())
        serial = self.pen.ops("candidates")[-1]["serial"]
        self.run_(g.report({"serial": serial, "action": "corrected", "text": "the black dog sat"}))
        v = self.value()
        self.assertEqual(v["text"], "the black dog sat")
        ws = v["words"]
        self.assertEqual([w["text"] for w in ws], ["the", "black", "dog", "sat"])
        self.assertEqual(ws[0]["strokes"], [a])
        self.assertEqual(ws[0]["dx"], 0)
        self.assertIn("path", ws[1])
        self.assertIn("path", ws[2])
        self.assertEqual(ws[3]["strokes"], [c])
        self.assertIn(b, v["gone"])                     # the replaced ink leaves
        # set words on the line of the word before them, at the writing's size
        self.assertAlmostEqual(ws[1]["box"][3], ws[2]["box"][3])
        self.assertGreater(ws[1]["box"][3] - ws[1]["box"][1], 0)
        self.assertGreater(ws[2]["box"][0], ws[1]["box"][2])
        self.assertEqual(ws[1]["path"][0], "M")
        # the kept ink after them moved by what the line grew
        self.assertGreater(ws[3]["dx"], 0)
        # samples: "the" and "sat" unchanged, and "cat"'s ink as the word that took its
        # place ("black"), here because the stand-in's check passes it; the recognizer's
        # align check keeps only ink that fits its new text
        self.assertEqual(sorted(s[1] for s in self.pen.engine.samples), ["black", "sat", "the"])
        # set words are never samples, and never neatened (FR-019): no neaten carries a text
        self.assertFalse(any(v[0] == "neaten" and len(v) > 3 for v in self.pen.engine.log))

    def test_text_edit_back_in_ink_mode(self):
        g = self.region()
        self.write(g, 20, 40, "dear")
        self.write(g, 80, 40, "sir")
        self.run_(g.update())
        self.run_(self._set(g, {"mode": "text"}))
        n = self.k
        self.run_(self._stroke(g, n + 1, word_stroke(10, 10, "x")))   # text mode takes no strokes
        self.assertNotIn(n + 1, g.strokes)
        self.run_(self._set(g, {"mode": "ink"}))
        self.run_(g.set_text("dear madam"))
        v = self.value()
        self.assertEqual(v["text"], "dear madam")
        self.assertIn("strokes", v["words"][0])
        self.assertIn("path", v["words"][1])
        self.assertEqual(sorted(s[1] for s in self.pen.engine.samples), ["dear", "madam"])

    def test_whole_value_set_when_no_word_matches(self):
        g = self.region()
        self.write(g, 20, 40, "abc")
        self.write(g, 80, 40, "def")
        self.run_(g.update())
        self.pen.engine.fail_fit |= {"xyz"}
        self.run_(g.set_text("xyz\nnew line here"))
        v = self.value()
        self.assertEqual(v["text"], "xyz\nnew line here")
        self.assertTrue(all("path" in w for w in v["words"]))
        self.assertEqual(g.strokes, {})                 # none of the ink is shown
        lines = sorted({round(w["box"][3], 3) for w in v["words"]})
        self.assertEqual(len(lines), 2)

    def test_new_writing_after_an_edit_joins_where_written(self):
        g = self.region()
        self.write(g, 20, 40, "one")
        self.run_(g.update())
        self.run_(g.set_text("one two"))
        self.write(g, 20, 80, "three")
        self.run_(g.update())
        self.assertEqual(self.value()["text"], "one two\nthree")
        self.assertIn("path", self.value()["words"][1])  # the set word stays as it is

    def test_neatest_keeps_ink(self):
        g = self.region(neaten=True, strength=1.0)
        a = self.write(g, 20, 40, "word")
        self.run_(g.pause())
        self.run_(g.report({"serial": self.pen.ops("candidates")[-1]["serial"], "action": "accepted"}))
        self.assertTrue(any(m["k"] == a for m in self.pen.ops("neat")))
        self.assertFalse(any(v[0] == "neaten" and len(v) > 3 for v in self.pen.engine.log))

    def test_correction_beside_a_kept_word(self):
        """a confirmed word and a new one, the new one corrected: the kept word keeps its ink,
        the corrected one is set, nothing doubles"""
        g = self.region()
        a = self.write(g, 20, 40, "one")
        self.run_(g.pause())
        self.run_(g.report({"serial": self.pen.ops("candidates")[-1]["serial"], "action": "accepted"}))
        b = self.write(g, 80, 40, "twa")
        self.run_(g.pause())
        self.pen.engine.samples = []
        self.pen.engine.fail_fit.add("two")
        self.run_(g.report({"serial": self.pen.ops("candidates")[-1]["serial"], "action": "corrected",
                            "text": "one two"}))
        v = self.value()
        self.assertEqual(v["text"], "one two")
        self.assertEqual([(w["text"], w.get("strokes")) for w in v["words"]], [("one", [a]), ("two", None)])
        self.assertIn(b, v["gone"])
        self.assertEqual(self.pen.engine.samples, [])       # "twa"'s ink doesn't fit "two"

    def test_gesture_without_reading_stays_ink(self):
        g = self.region(reading=False, neaten=True, strength=0.5)
        self.write(g, 20, 40, "one")
        s = self.gesture(g, "strike", 18, 40, 35)
        self.run_(g.update())
        self.assertIn(s, g.strokes)
        self.assertEqual(self.pen.ops("gesture"), [])
        self.assertEqual(self.pen.ops("value"), [])

    def test_stroke_right_after_a_restore(self):
        g = self.region()
        g.restore([(1, self.pen.engine.name(word_stroke(20, 40, "old"), "old"))], None)
        self.run_(self._stroke(g, 2, self.pen.engine.name(word_stroke(80, 40, "new"), "new")))
        self.run_(g.restored())
        self.assertEqual(self.value()["text"], "old new")
        self.assertEqual(self.pen.engine.sessions, {})      # given up again: no session held

    def test_destroyed_region_sends_nothing(self):
        g = self.region()
        self.write(g, 20, 40, "one")
        g.destroy()
        self.run_(g.update())
        self.run_(g.pause())
        self.run_(g.report({"serial": 1, "action": "accepted"}))
        self.assertEqual(self.pen.out, [])

    # ------------------------------------------------------------------ saved state, stored ink

    def test_state_and_stored_ink(self):
        g = self.region()
        self.write(g, 20, 40, "old")
        self.write(g, 80, 40, "read")
        self.run_(g.pause())
        serial = self.pen.ops("candidates")[-1]["serial"]
        self.run_(g.report({"serial": serial, "action": "corrected", "text": "gold read"}))
        v = self.value()
        state = v["state"]
        d = json.loads(base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)))
        self.assertEqual([w["t"] for w in d["w"]], ["gold", "read"])
        strokes = [{"k": k, "points": g.strokes[k]} for k in g.order]
        # handed back to a new region, with a newer model reading "read" as "reed": the
        # edited and confirmed words stay as the state says
        self.pen.engine.label = {k: ("reed" if v == "read" else v) for k, v in self.pen.engine.label.items()}
        self.pen.engine.samples = []
        g2 = self.region(2)
        self.restore(g2, [(d["k"], d["points"]) for d in strokes], state)
        self.assertEqual(self.value()["r"], 2)
        self.assertEqual(self.value()["text"], "gold read")
        # without the state the strokes alone are read with the model there is now
        g3 = self.region(3)
        self.restore(g3, [(d["k"], d["points"]) for d in strokes], None)
        self.assertEqual(self.value()["text"], "reed")
        # handed-back ink is never a sample
        self.run_(g3.pause())
        self.run_(g3.report({"serial": self.pen.ops("candidates")[-1]["serial"], "action": "accepted"}))
        self.assertEqual(self.pen.engine.samples, [])
        self.assertEqual(regions.decode("garbage"), [])
        self.assertEqual(regions.decode(None), [])

    # ------------------------------------------------------------------ cost, privacy, size

    def test_moving_a_region_reads_nothing(self):
        g = self.region()
        self.write(g, 20, 40, "one")
        self.run_(g.update())
        n = len(self.pen.engine.log)
        self.run_(self._set(g, {"size": [500, 300]}))
        self.run_(self._set(g, {"size": [500, 300]}))
        self.assertEqual(len(self.pen.engine.log), n)
        self.assertEqual(g.timers, [])

    def test_empty_regions_cost_nothing(self):
        for r in range(100):
            self.region(r)
        self.assertEqual(self.pen.engine.log, [])

    def test_private_region_sends_nothing(self):
        g = self.region(purpose=8)          # password
        self.write(g, 20, 40, "secret")
        self.run_(g.update())
        self.run_(g.set_text("x"))
        self.assertFalse(g.restore([(1, word_stroke(0, 40, "a"))], None))
        self.assertEqual(self.pen.engine.log, [])
        self.assertEqual(self.pen.out, [])

    def test_size_reference(self):
        g = self.region(units="device:5")
        self.write(g, 20, 40, "a")
        self.run_(g.update())
        log = self.pen.engine.log
        self.assertIn(("prior", g.name, "12.5"), log)              # 2.5 mm at 5 px per mm
        areas = [v for v in log if v[0] == "area"]
        self.assertTrue(areas)
        self.assertTrue(all(v[2].split(",")[4] == "0" for v in areas))   # no line found from writing
        g2 = self.region(2, guides=[30, 40])
        self.write(g2, 20, 40, "b")
        self.run_(g2.update())
        self.assertTrue(any(v[0] == "area" and v[1] == g2.name and v[2].split(",")[4] == "30"
                            for v in self.pen.engine.log))


if __name__ == "__main__":
    unittest.main()
