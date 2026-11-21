#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance of the handwriting service (spec 004) with a stand-in compositor on the
pen channel and a random-weight recognizer model (readings are nonsense, but every path
runs): neatened strokes and candidates for a surface, nothing for a surface with both off
or a private field, a pick stores the sample a direct client stores for the same values,
permissions per app (unasked, refused, allowed; a sandboxed app can't grant itself), an app
sees only its own samples, and two apps share one recognizer process.

    HW_BIN=path/to/glassine-handwriting python3 tests/test_service.py
"""
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REC = ROOT.parent / "recognizer"
BIN = str(Path(os.environ.get("HW_BIN") or REC / "glassine-handwriting").resolve())
sys.path.insert(0, str(REC / "tests"))
sys.path.insert(0, str(ROOT))
import test_recognizer as TR  # noqa: E402

HELPER = r'''
import json, socket, sys
print(__import__("os").getpid(), flush=True)
sys.stdin.readline()
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); f = s.makefile("rw")
for line in sys.stdin:
    f.write(line); f.flush(); print(f.readline().strip(), flush=True)
'''


def ink(n=4, seed=1, x0=10.0, y0=40.0, t0=1000):
    """strokes as the compositor gives them: x, y and tilt multiples of 1/256, pressure k/65535"""
    import random
    rng = random.Random(seed)
    out, t = [], t0
    for i in range(n):
        x, y, s = x0 + 30 * i, y0, []
        for j in range(24):
            x += rng.randrange(-256, 768) / 256
            y += rng.randrange(-512, 512) / 256
            s.append([x, y, t, rng.randrange(0, 65536) / 65535, rng.randrange(-2560, 2560) / 256,
                      rng.randrange(-2560, 2560) / 256])
            t += 7
        t += 150
        out.append(s)
    return out


class AreaRules(unittest.TestCase):
    """the area, units and size prior (docs/service.md, The writing's size)"""

    def test_rules(self):
        from glassine_hwservice import area as A
        # three strokes 10, 20 and 40 high, one dipping below
        st = [[[0, 50, 0], [2, 40, 10]], [[10, 30, 20], [90, 50, 30]], [[100, 20, 40], [101, 60, 50]]]
        self.assertEqual(A.box(st), (0, 20, 101, 60))
        self.assertFalse(hasattr(A, "line"))                      # no line is found from writing
        self.assertEqual(A.base(st), 50)
        self.assertEqual(A.text(A.box(st), 0, A.base(st)), "0,20,101,60,0,0,50")
        self.assertEqual(A.text((0, 0, 1, 1), 1.23456), "0,0,1,1,1.235,0")
        self.assertEqual(A.box([[[5, 5, 0]]]), (5, 5, 6, 6))      # a dot: never an empty box
        self.assertEqual(A.glyphs(st), 3)
        self.assertEqual(A.glyphs(st[:2]), 2)
        self.assertIsNone(A.base(st[:2]))                          # too little to show its line
        self.assertEqual(A.glyphs([[[0, 0, 0], [10, 0, 1]], [[5, 5, 2], [20, 5, 3]], [[30, 0, 4]]]), 2)
        self.assertEqual(A.rate(st), 0)                            # too few steps
        self.assertEqual(A.rate([[[i, 0, 5 * i] for i in range(9)]]), 200)
        self.assertEqual(A.with_rate("device:6.25", 200), "device:6.25:200")
        self.assertEqual(A.with_rate("device:6.25:120", 200), "device:6.25:120")
        self.assertEqual(A.with_rate("device", 200), "device")
        self.assertEqual(A.with_rate("em", 200), "em")
        self.assertEqual(A.with_rate("device:6.25", 0), "device:6.25")
        # the prior: a typical small letter in mm at the units' density, else the default one
        self.assertAlmostEqual(A.prior("device:6.25"), A.TYPICAL_XH_MM * 6.25)
        self.assertAlmostEqual(A.prior("device:6.25:143"), A.TYPICAL_XH_MM * 6.25)
        self.assertAlmostEqual(A.prior("device"), A.TYPICAL_XH_MM * A.DEFAULT_PX_PER_MM)
        self.assertIsNone(A.prior("em"))


class Service(unittest.TestCase):
    def setUp(self):
        self.e = TR.Env()
        self.dir = Path(tempfile.mkdtemp(prefix="hws-", dir="/tmp"))
        self.proc = self.dir / "proc"
        self.sock = str(self.dir / "service")
        self.fake(os.getpid(), "/usr/bin/settings-test")
        # the recognizer behind a tee: the requests the service makes, for the tests to read
        self.log = self.dir / "requests"
        rec = self.dir / "recognizer"
        rec.write_text('#!/bin/sh\ntee -a "%s" | exec "%s" "$@"\n' % (self.log, BIN))
        rec.chmod(0o755)
        env = dict(self.e.env, GLASSINE_HANDWRITING_BIN=str(rec), GLASSINE_HANDWRITING_FIT_OK="0",
                   GLASSINE_HWSERVICE_PROC=str(self.proc), PYTHONPATH=str(ROOT))
        self.env = env
        a, b = socket.socketpair()
        self.pen = a
        self.d = subprocess.Popen([sys.executable, "-m", "glassine_hwservice", "--socket", self.sock,
                                   "--pen-fd", str(b.fileno())], env=env, pass_fds=[b.fileno()], cwd=str(ROOT))
        b.close()
        self.buf = b""
        self.hello = self.pen_msg(20)
        for _ in range(100):
            if os.path.exists(self.sock):
                break
            time.sleep(0.05)
        self.c = socket.socket(socket.AF_UNIX)
        self.c.connect(self.sock)
        self.cf = self.c.makefile("rw")
        self.n = 0

    def tearDown(self):
        for f in (self.pen, self.cf, self.c):
            f.close()
        try:
            self.d.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.d.kill()
        shutil.rmtree(self.dir, ignore_errors=True)
        self.e.close()

    def fake(self, pid, exe, flatpak=None):
        d = self.proc / str(pid)
        (d / "root").mkdir(parents=True, exist_ok=True)
        if not (d / "exe").exists():
            os.symlink(exe, d / "exe")
        (d / "cmdline").write_bytes(exe.encode() + b"\0")
        if flatpak:
            (d / "root" / ".flatpak-info").write_text("[Application]\nname=%s\n" % flatpak)

    def call(self, method, f=None, **params):
        self.n += 1
        (f or self.cf).write(json.dumps({"id": self.n, "method": method, "params": params}) + "\n")
        (f or self.cf).flush()
        r = json.loads((f or self.cf).readline())
        self.assertEqual(r["id"], self.n)
        return r

    def app(self, flatpak):
        """another app on the service socket, sandboxed as flatpak"""
        h = subprocess.Popen([sys.executable, "-c", HELPER, self.sock], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True)
        self.addCleanup(h.kill)
        pid = int(h.stdout.readline())
        self.fake(pid, "/usr/bin/bwrap", flatpak)
        h.stdin.write("\n")
        h.stdin.flush()
        h.n = 0

        def call(method, **params):
            h.n += 1
            h.stdin.write(json.dumps({"id": h.n, "method": method, "params": params}) + "\n")
            h.stdin.flush()
            return json.loads(h.stdout.readline())
        return call

    def pen_send(self, **m):
        self.pen.sendall((json.dumps(m) + "\n").encode())

    def pen_msg(self, timeout):
        """the next message on the pen channel, None after timeout seconds without one"""
        end = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.pen], [], [], left)[0]:
                return None
            d = self.pen.recv(65536)
            if not d:
                return None
            self.buf += d
        line, _, self.buf = self.buf.partition(b"\n")
        return json.loads(line)

    def pen_events(self, until, timeout=20):
        """pen channel messages until until(messages) holds"""
        out = []
        while not until(out):
            m = self.pen_msg(timeout)
            if m is None:
                self.fail("no more messages after %r" % out)
            out.append(m)
        return out

    def quiet(self, secs=1.5):
        """no message on the pen channel for a while"""
        return self.pen_msg(secs) is None

    def write(self, sid, strokes, k0=0):
        for i, s in enumerate(strokes):
            self.pen_send(op="stroke", s=sid, k=k0 + i, points=s)

    def test_hello_and_status(self):
        self.assertEqual(self.hello["op"], "hello")
        self.assertEqual(self.hello["languages"], ["en"])
        r = self.call("hello")["result"]
        self.assertEqual((r["app"], r["sandboxed"]), ("host.settings-test", False))
        self.assertFalse(self.call("status")["result"]["engine"]["running"])

    def test_pen_neat_and_candidates(self):
        self.pen_send(op="surface", s=1, sandbox="org.example.Notes", pid=0)
        self.pen_send(op="set", s=1, neaten=True, strength=0.5, text=True, purpose=0, hints=0, units="device:6.25")
        st = ink(4)
        self.write(1, st)
        ev = self.pen_events(lambda o: any(m["op"] == "candidates" for m in o))
        neat = {m["k"]: m for m in ev if m["op"] == "neat"}
        self.assertEqual(sorted(neat), [0, 1, 2, 3])
        for k, m in neat.items():
            self.assertIn(m["how"], ("kept", "line", "pulled"))
            self.assertEqual(len(m["points"]), len(st[k]))
        self.assertTrue(any(m["op"] == "reading" and m["strokes"] == [0, 1, 2, 3] for m in ev))
        cand = [m for m in ev if m["op"] == "candidates"][-1]
        self.assertTrue(cand["items"])
        for c in cand["items"]:
            self.assertIn(c["from"], ("ink", "beam", "words", "guided"))
            self.assertIsInstance(c["fit"], bool)
        # a new strength neatens again without new ink; strength 0 gives the raw strokes
        self.pen_send(op="set", s=1, strength=0.0)
        ev = self.pen_events(lambda o: len([m for m in o if m["op"] == "neat"]) >= 4)
        for m in ev:
            if m["op"] == "neat":
                self.assertEqual(m["points"], [p[:2] for p in st[m["k"]]])
        # the report: not allowed until the person allows it
        self.pen_send(op="report", s=1, serial=cand["serial"], action="picked", rank=0, text=cand["items"][0]["text"])
        r = self.pen_events(lambda o: any(m["op"] == "report" for m in o))[-1]
        self.assertEqual(r["result"], "not_allowed")

    def test_neatened_without_the_reading_as_text(self):
        """the pen's words are neatened without their reading as TEXT (spec 003 FR-043: only a
        confirmed text is set in the font); the neaten method passes the app's text on"""
        self.pen_send(op="surface", s=3, sandbox="org.example.Notes", pid=0)
        self.pen_send(op="set", s=3, neaten=True, strength=1.0, text=False, units="device:6.25")
        st = ink(4, seed=8)
        self.write(3, st)
        self.pen_events(lambda o: len({m["k"] for m in o if m["op"] == "neat"}) >= 4)
        asks = [f for f in self.requests() if f[0] == "neaten"]
        self.assertTrue(asks)
        self.assertTrue(all(len(f) == 4 for f in asks), asks)
        r = self.app("org.example.A")("neaten", strokes=ink(2, seed=9), strength=1, text="ab")
        self.assertIn("strokes", r["result"])
        asks = [f for f in self.requests() if f[0] == "neaten"]
        self.assertEqual(asks[-1][4:5], ["ab"])

    def test_pick_stores_the_same_sample(self):
        self.call("permission.set", app="org.example.Notes", learn="allowed", samples="allowed")
        self.pen_send(op="surface", s=2, sandbox="org.example.Notes", pid=0)
        self.pen_send(op="set", s=2, neaten=False, text=True, units="device:6.25")
        st = ink(3, seed=5)
        self.write(2, st)
        cand = [m for m in self.pen_events(lambda o: any(m["op"] == "candidates" for m in o))
                if m["op"] == "candidates"][-1]
        text = cand["items"][0]["text"]
        self.pen_send(op="report", s=2, serial=cand["serial"], action="picked", rank=0, text=text)
        r = self.pen_events(lambda o: any(m["op"] == "report" for m in o))[-1]
        self.assertEqual(r["result"], "stored")
        # an old serial is stale
        self.pen_send(op="report", s=2, serial=cand["serial"], action="accepted", rank=0, text="")
        self.assertEqual(self.pen_events(lambda o: any(m["op"] == "report" for m in o))[-1]["result"], "stale")
        mine = self.app("org.example.Notes")("samples.list")["result"]["samples"]
        self.assertEqual(len(mine), 1)
        # a client talking to the recognizer directly, same values, its own store
        d = TR.Env()
        self.addCleanup(d.close)
        d.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"
        c = d.start()
        c.ask("hello", "1", "org.example.Notes")
        # the units with the pen's rate, the area and the prior the service documents
        # (docs/service.md, The writing's size): the box of the writing, no ruled line, the
        # guide line at the median of the strokes' lowest points, a typical small letter
        from glassine_hwservice import area as A
        from glassine_hwservice.engine import points
        from glassine_hwservice.pen import lines
        self.assertEqual(len(lines(dict(enumerate(st)))), 1)
        c.ask("begin", "s", "-", "normal", "device:6.25:143", "")
        for s in st:
            c.ask("stroke", "s", points(s))
        c.ask("prior", "s", A._num(A.prior("device:6.25")))
        c.ask("area", "s", A.text(A.box(st), 0, A.base(st)))
        c.ask("read", "s", "5")
        r = c.ask("confirm", "s", "picked", TR.esc(text))
        c.close()
        self.assertEqual(r[2], "ok")
        self.assertEqual(r[3], mine[0]["id"])

    def requests(self):
        return [ln.split("\t") for ln in self.log.read_text().splitlines()] if self.log.exists() else []

    def sessions(self):
        """per session begun: its units and the requests on it, in order"""
        out = []
        for f in self.requests():
            if f[0] == "begin":
                out.append({"name": f[2], "units": f[5], "asks": []})
            elif len(f) > 2 and out and f[0] in ("stroke", "area", "read", "neaten", "confirm", "prior"):
                cur = [x for x in out if x["name"] == f[2]][-1]
                cur["asks"].append(f[0] if f[0] != "area" else ("area", f[3]))
                if f[0] == "prior":
                    cur["prior"] = float(f[3])
        return out

    def assert_every_read_has_an_area(self, units, prior=True):
        ss = self.sessions()
        self.assertTrue(ss)
        from glassine_hwservice import area as A
        for x in ss:
            self.assertEqual(x["units"], units, x["name"])
            if prior:
                self.assertAlmostEqual(x.get("prior", 0), round(A.prior(units), 3), 3, x["name"])
            for i, a in enumerate(x["asks"]):
                if a in ("read", "neaten", "confirm"):
                    self.assertTrue(any(isinstance(b, tuple) for b in x["asks"][:i]), "%s: %s before an area" % (x["name"], a))
        return [b[1] for x in ss for b in x["asks"] if isinstance(b, tuple)]

    def test_area_and_units_without_an_ink_area(self):
        """no ink area: every session gets an area of the writing's box with no ruled line,
        the size prior, and the units with the px per mm and the pen's rate (7 ms a point:
        143 points a second)"""
        self.pen_send(op="surface", s=11, sandbox="org.example.Notes", pid=0)
        self.pen_send(op="set", s=11, neaten=True, strength=0.5, text=True, units="device:6.25")
        st = ink(4, seed=3)
        self.write(11, st)
        self.pen_events(lambda o: any(m["op"] == "candidates" for m in o))
        from glassine_hwservice import area as A
        areas = self.assert_every_read_has_an_area("device:6.25:143")
        self.assertIn(A.text(A.box(st), 0, A.base(st)), areas)
        for a in areas:
            f = [float(v) for v in a.split(",")]
            self.assertTrue(f[2] > f[0] and f[3] > f[1] and f[4] == 0 and f[5] == 0, a)

    def test_lone_character_never_borrows_a_line(self):
        """a lone character: no line found from itself nor from earlier writing on the
        surface, no guide line, only the prior; on an output of unknown size the units stay
        the pen's own, the prior at the default density"""
        self.pen_send(op="surface", s=14, sandbox="org.example.Notes", pid=0)
        self.pen_send(op="set", s=14, text=True, units="device")
        self.write(14, ink(4, seed=8))
        self.pen_events(lambda o: any(m["op"] == "candidates" for m in o))
        self.pen_send(op="clear", s=14)
        self.log.write_text("")
        one = ink(1, seed=9)
        self.write(14, one, k0=10)
        self.pen_events(lambda o: any(m["op"] == "candidates" for m in o))
        from glassine_hwservice import area as A
        areas = self.assert_every_read_has_an_area("device")
        self.assertEqual(set(areas), {A.text(A.box(one), 0)})
        # with the person's density the compositor sends units with it
        self.pen_send(op="clear", s=14)
        self.pen_send(op="set", s=14, text=True, units="device:8.94")
        self.log.write_text("")
        self.write(14, one, k0=20)
        self.pen_events(lambda o: any(m["op"] == "candidates" for m in o))
        self.assert_every_read_has_an_area("device:8.94:143")

    def test_area_of_the_ink_area(self):
        """an ink area with ruled lines: that box, its line and its first guide line"""
        self.pen_send(op="surface", s=12, sandbox="org.example.Notes", pid=0)
        self.pen_send(op="set", s=12, neaten=True, strength=0.5, text=True, units="device:6.25",
                      box=[0, 0, 600, 400], guides=[60.5, 45])
        self.write(12, ink(4, seed=4))
        self.pen_events(lambda o: any(m["op"] == "candidates" for m in o))
        self.assertEqual(set(self.assert_every_read_has_an_area("device:6.25:143")), {"0,0,600,400,60.5,0,45"})
        # an ink area without ruled lines: that box, no line
        self.pen_send(op="surface", s=13, sandbox="org.example.Notes", pid=0)
        self.pen_send(op="set", s=13, text=True, units="device:6.25", box=[10, 20, 700, 500])
        self.write(13, ink(3, seed=6))
        self.pen_events(lambda o: any(m["op"] == "candidates" and m["s"] == 13 for m in o))
        a13 = [b[1] for x in self.sessions() if x["name"] == "p13" for b in x["asks"] if isinstance(b, tuple)]
        self.assertTrue(a13 and all(a.startswith("10,20,700,500,0,0") for a in a13), a13)

    def test_service_read_has_an_area(self):
        """the service interface: the area of the writing with no ruled line, else the one given"""
        a = self.app("org.example.A")
        st = ink(3, seed=7)
        self.assertIn("candidates", a("read", strokes=st, count=1, units="device:6.25")["result"])
        from glassine_hwservice import area as A
        self.assertEqual(self.assert_every_read_has_an_area("device:6.25:143"),
                         [A.text(A.box(st), 0, A.base(st))])
        self.log.write_text("")
        a("read", strokes=st, count=1, units="device:6.25", area={"box": [0, 0, 400, 200], "line": 80, "base": 60})
        self.assertEqual(self.assert_every_read_has_an_area("device:6.25:143"), ["0,0,400,200,80,0,60"])
        self.assertEqual(a("read", strokes=st, area={"box": [0, 0, 0, 0], "line": 80})["error"]["code"], "bad-request")
        self.assertEqual(a("read", strokes=st, area={"line": 0})["error"]["code"], "bad-request")
        # em says how high a line is: no area found for it, no prior; one given is used
        self.log.write_text("")
        a("read", strokes=[[[x / 40, y / 40, t] for x, y, t, *_ in s] for s in st], count=1, units="em")
        self.assertEqual([b for x in self.sessions() for b in x["asks"] if isinstance(b, tuple)], [])
        self.assertNotIn("prior", self.sessions()[0])
        # a lone character: the box of it, no line, no guide line
        self.log.write_text("")
        a("read", strokes=st[:1], count=1, units="device:6.25")
        self.assertEqual(self.assert_every_read_has_an_area("device:6.25:143"), [A.text(A.box(st[:1]), 0)])

    def test_nothing_sent_when_off_or_private(self):
        self.pen_send(op="surface", s=3, sandbox="org.example.Draw", pid=0)
        self.write(3, ink(3))                                   # both off (the default)
        self.pen_send(op="surface", s=4, sandbox="org.example.Draw", pid=0)
        self.pen_send(op="set", s=4, neaten=True, strength=1, text=True, purpose=8)    # a password
        self.write(4, ink(3))
        self.pen_send(op="surface", s=5, sandbox="org.example.Draw", pid=0)
        self.pen_send(op="set", s=5, neaten=True, strength=1, text=True, hints=1 << 7)   # sensitive data
        self.write(5, ink(3))
        self.assertTrue(self.quiet())
        self.assertEqual(self.call("status")["result"]["engine"]["starts"], 0)
        # neatening on, text off: neat strokes, no readings, no candidates
        self.pen_send(op="set", s=3, neaten=True, strength=1)
        self.write(3, ink(2, seed=9), k0=3)
        ev = self.pen_events(lambda o: len([m for m in o if m["op"] == "neat"]) >= 2)
        self.assertTrue(self.quiet())
        self.assertEqual({m["op"] for m in ev}, {"neat"})

    def test_permissions_and_own_samples(self):
        a, b = self.app("org.example.A"), self.app("org.example.B")
        self.assertEqual(a("samples.list")["error"]["code"], "permission-needed")
        self.assertEqual(a("permission.set", app="org.example.A", samples="allowed")["error"]["code"], "not-allowed")
        self.call("permission.set", app="org.example.B", samples="refused")
        self.assertEqual(b("samples.list")["error"]["code"], "not-allowed")
        self.call("permission.set", app="org.example.A", samples="allowed", learn="allowed")
        self.call("permission.set", app="org.example.B", samples="allowed", learn="allowed")
        self.assertEqual(a("permission.get")["result"], {"learn": "allowed", "samples": "allowed"})
        # each app's pick through its own surface
        for sid, app in ((6, "org.example.A"), (7, "org.example.B")):
            self.pen_send(op="surface", s=sid, sandbox=app, pid=0)
            self.pen_send(op="set", s=sid, text=True, units="device:6.25")
            self.write(sid, ink(3, seed=sid))
            cand = [m for m in self.pen_events(lambda o: any(m["op"] == "candidates" for m in o))
                    if m["op"] == "candidates"][-1]
            self.pen_send(op="report", s=sid, serial=cand["serial"], action="corrected", rank=0, text="hi")
            self.assertEqual(self.pen_events(lambda o: any(m["op"] == "report" for m in o))[-1]["result"], "stored")
        sa, sb = a("samples.list")["result"]["samples"], b("samples.list")["result"]["samples"]
        self.assertEqual((len(sa), len(sb)), (1, 1))
        self.assertNotEqual(sa[0]["id"], sb[0]["id"])
        self.assertEqual(b("samples.delete", id=sa[0]["id"])["error"]["code"], "not-yours")
        self.assertEqual(len(a("samples.list")["result"]["samples"]), 1)

    def test_two_apps_one_model(self):
        a, b = self.app("org.example.A"), self.app("org.example.B")
        st = ink(3, seed=2)
        ra = a("read", strokes=st, count=3)["result"]
        rb = b("read", strokes=st, count=3)["result"]
        self.assertEqual(ra, rb)                     # the same ink reads the same for everyone
        n = a("neaten", strokes=st, strength=0)["result"]
        self.assertEqual([s["points"] for s in n["strokes"]], [[p[:2] for p in s] for s in st])
        s = self.call("status")["result"]["engine"]
        self.assertEqual((s["running"], s["starts"]), (True, 1))
        self.assertEqual(a("read", strokes=[], count=3)["error"]["code"], "bad-request")
        self.assertEqual(a("nope")["error"]["code"], "unknown-method")



    def test_example_against_schema(self):
        """the client example (service/examples) talks to the service; every line it sends
        and gets passes docs/service.schema.json"""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("no jsonschema")
        schema = json.loads((ROOT.parent / "docs" / "service.schema.json").read_text())
        trace = self.dir / "trace"
        run = ("import os, runpy, sys; print(os.getpid(), flush=True); sys.stdin.readline(); "
               "sys.argv = [sys.argv[1]] + sys.argv[2:]; runpy.run_path(sys.argv[0], run_name='__main__')")
        ex = ROOT / "examples" / "hw-service-example.py"
        env = dict(self.env, XDG_RUNTIME_DIR=str(self.dir), GLASSINE_HWSERVICE_TRACE=str(trace))
        os.makedirs(self.dir / "glassine-handwriting", exist_ok=True)
        link = self.dir / "glassine-handwriting" / "service"
        if not link.exists():
            os.symlink(self.sock, link)
        h = subprocess.Popen([sys.executable, "-c", run, str(ex)], env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        pid = int(h.stdout.readline())
        self.fake(pid, "/usr/bin/python3")
        (self.proc / str(pid) / "cmdline").write_bytes(b"python3\0" + str(ex).encode() + b"\0")
        out, _ = h.communicate("\n", timeout=120)
        self.assertEqual(h.returncode, 0, out)
        self.assertIn("permission-needed", out)
        lines = [json.loads(x) for x in trace.read_text().splitlines()]
        self.assertEqual(len(lines) % 2, 0)
        defs = schema["$defs"]
        for req, resp in zip(lines[::2], lines[1::2]):
            jsonschema.validate(req, {**schema, "$ref": "#/$defs/request"})
            jsonschema.validate(resp, {**schema, "$ref": "#/$defs/response"})
            if "result" in resp:
                jsonschema.validate(resp["result"], {**schema, "$ref": "#/$defs/methods/%s/result" % req["method"]})
        self.assertEqual([r["method"] for r in lines[::2]], ["hello", "permission.get", "read", "neaten", "samples.list"])
        self.assertTrue(defs)

if __name__ == "__main__":
    unittest.main()
