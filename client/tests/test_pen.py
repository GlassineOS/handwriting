#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance of the pen interface end to end (spec 004, spec 003 FR-018 to FR-029):
the stand-in compositor (GlassineOS/sway test/handwriting-standin.c, with sway's own
glassine-handwriting.c), the handwriting service, the recognizer with a random-weight
model, and ghw-pen-print on libglassine-handwriting as the app. A stand-in pen writes.

    HW_BIN=… HW_STANDIN=…/handwriting-standin GHW_PRINT=…/ghw-pen-print python3 tests/test_pen.py

Checks: raw strokes arrive at once, exactly as the pen gave them, also without the service;
strokes outside the area don't count; each neat stroke names its raw stroke, keeps every
value but x and y; strength 0 gives the raw strokes; text off gives no candidates; a
private field and both off send the service nothing; a pick stores, under the app's
sandbox id, the sample a direct client stores for the same values, and only when the
person allows that app to learn."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REC = ROOT / "recognizer"
sys.path.insert(0, str(REC / "tests"))
sys.path.insert(0, str(ROOT / "service"))
import test_recognizer as TR  # noqa: E402
from glassine_hwservice.engine import points as rec_points  # noqa: E402

BIN = os.environ.get("HW_BIN") or str(REC / "glassine-handwriting")
STANDIN = os.environ.get("HW_STANDIN")
PRINT = os.environ.get("GHW_PRINT")


class Proc:
    """a child with line input, and its output lines read by a thread of their own (so
    neither side's pipe can fill while the other waits)"""

    def __init__(self, args, env):
        import queue
        import threading
        self.p = subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.q = queue.Queue()

        def pump():
            for line in self.p.stdout:
                self.q.put(line.decode().rstrip("\n"))
            self.q.put(None)
        threading.Thread(target=pump, daemon=True).start()

    def send(self, line):
        self.p.stdin.write((line + "\n").encode())
        self.p.stdin.flush()

    def line(self, timeout=20):
        import queue
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def until(self, pred, timeout=20):
        out = []
        while True:
            line = self.line(timeout)
            if line is None:
                raise AssertionError("no more output after %r" % out[-10:])
            out.append(line)
            if pred(line):
                return out

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()


def parse_points(fields):
    return [[float(v) if "." in v or "e" in v else int(v) for v in f.split(",")] for f in fields]


@unittest.skipUnless(STANDIN and PRINT, "needs HW_STANDIN and GHW_PRINT")
class Pen(unittest.TestCase):
    def start(self, service=True, sandbox="org.example.Notes"):
        self.e = TR.Env()
        self.addCleanup(self.e.close)
        self.run_dir = Path(tempfile.mkdtemp(prefix="ghw-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.run_dir, True)
        os.chmod(self.run_dir, 0o700)
        env = dict(self.e.env, XDG_RUNTIME_DIR=str(self.run_dir), GLASSINE_HANDWRITING_BIN=str(Path(BIN).resolve()),
                   GLASSINE_HANDWRITING_FIT_OK="0", GLASSINE_HWSERVICE_LIB=str(ROOT / "service"),
                   GLASSINE_HANDWRITING_SERVICE=str(ROOT / "service" / "glassine-handwriting-service") if service else "",
                   STANDIN_SANDBOX=sandbox or "-")
        env.pop("WAYLAND_DISPLAY", None)
        self.env = env
        self.comp = Proc([STANDIN], env)
        self.addCleanup(self.comp.close)
        sock = self.comp.line()
        self.assertTrue(sock and sock.startswith("socket "), sock)
        env["WAYLAND_DISPLAY"] = sock.split()[1]
        if service:                              # the service said hello
            for _ in range(200):
                if (self.run_dir / "glassine-handwriting" / "service").exists():
                    break
                time.sleep(0.05)
            time.sleep(0.3)

    def app(self, *args):
        a = Proc([PRINT, *args], self.env)
        self.addCleanup(a.close)
        first = a.line()
        self.assertTrue(first.startswith("caps "), first)
        a.until(lambda line: line == "ready")
        return a, first

    def pen(self, stroke, surface=0):
        """one stroke: [(x, y, t, p, tx, ty)]"""
        x, y, t, p, tx, ty = stroke[0]
        self.comp.send(f"down {surface} {x} {y} {t} {p} {tx} {ty}")
        for x, y, t, p, tx, ty in stroke[1:]:
            self.comp.send(f"move {x} {y} {t} {p} {tx} {ty}")
        self.comp.send(f"up {stroke[-1][2]}")
        for _ in range(len(stroke) + 1):
            self.assertEqual(self.comp.line(), "ok")

    def ink(self, n=3, seed=1, x0=20.0, y0=40.0, t0=5000):
        import random
        rng = random.Random(seed)
        out, t = [], t0
        for i in range(n):
            x, y, s = x0 + 30 * i, y0, []
            for _ in range(20):
                x += rng.randrange(-256, 768) / 256
                y += rng.randrange(-512, 512) / 256
                s.append((x, y, t, rng.randrange(0, 65536) / 65535, rng.randrange(-2560, 2560) / 256,
                          rng.randrange(-2560, 2560) / 256))
                t += 7
            t += 150
            out.append(s)
        return out

    def service(self, method, **params):
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(self.run_dir / "glassine-handwriting" / "service"))
        f = s.makefile("rw")
        f.write(json.dumps({"id": 1, "method": method, "params": params}) + "\n")
        f.flush()
        r = json.loads(f.readline())
        f.close()
        s.close()
        return r

    def test_raw_strokes_without_service(self):
        self.start(service=False)
        a, first = self.app("-a", "0,0,400,300")
        self.assertEqual(first, "caps 1")                         # strokes only
        st = self.ink(2)
        for s in st:
            self.pen(s)
        self.pen([(500, 10, 9000, 0.5, 0, 0), (510, 12, 9007, 0.5, 0, 0)])    # outside the area
        a.send("sync")
        out = a.until(lambda line: line == "synced")
        strokes = [line.split() for line in out if line.startswith("stroke ")]
        self.assertEqual([s[1] for s in strokes], ["0", "1"])
        for s, want in zip(strokes, st):
            got = parse_points(s[3:])
            self.assertEqual(len(got), len(want))
            for g, w in zip(got, want):
                self.assertEqual(g[:3], [w[0], w[1], w[2]])
                self.assertEqual(round(g[3] * 65535), round(w[3] * 65535))
                self.assertEqual(g[4:], [w[4], w[5]])
        self.assertFalse([line for line in out if line.startswith(("neat", "reading", "candidate"))])

    def test_neat_text_and_pick(self):
        self.start()
        a, first = self.app("-n", "0.5", "-t")
        caps = int(first.split()[1])
        self.assertEqual(caps & 7, 7)
        self.assertIn("en", first.split()[2:])
        st = self.ink(3, seed=4)
        for s in st:
            self.pen(s)
        out = a.until(lambda line: line.startswith("done "))
        raw = {int(line.split()[1]): parse_points(line.split()[3:]) for line in out if line.startswith("stroke ")}
        neat = {}
        for line in out:
            if line.startswith("neat "):
                f = line.split()
                neat[int(f[1])] = parse_points(f[4:])
        self.assertEqual(sorted(raw), [0, 1, 2])
        self.assertEqual(sorted(neat), [0, 1, 2])
        for k in raw:
            self.assertEqual([p[2:] for p in neat[k]], [p[2:] for p in raw[k]])
        self.assertTrue(any(line.startswith("reading ") and line.endswith("|0,1,2") for line in out))
        cands = [line for line in out if line.startswith("candidate ")]
        self.assertTrue(cands)
        serial = int(out[-1].split()[1])
        text = cands[0].split(" ", 5)[5].split("|")[0]
        # strength 0: the raw strokes back
        a.send("neaten 1 0")
        out = a.until(lambda line: line.startswith("neat 2 "))
        for line in out:
            if line.startswith("neat "):
                f = line.split()
                self.assertEqual(parse_points(f[4:]), raw[int(f[1])])
        # not allowed yet
        a.send(f"report {serial} 0 0 {text}")
        self.assertEqual(a.until(lambda line: line.startswith("report "))[-1], f"report {serial} 2")
        # the person allows it; the same ink once more, then the pick stores it
        self.assertIn("result", self.service("permission.set", app="org.example.Notes", learn="allowed"))
        for s in st:
            self.pen([(x, y, t + 100000, p, tx, ty) for x, y, t, p, tx, ty in s])
        out = a.until(lambda line: line.startswith("done "))
        serial = int(out[-1].split()[1])
        text = [line for line in out if line.startswith("candidate ")][0].split(" ", 5)[5].split("|")[0]
        raw2 = [parse_points(line.split()[3:]) for line in out if line.startswith("stroke ")]
        a.send(f"report {serial} 0 0 {text}")
        self.assertEqual(a.until(lambda line: line.startswith("report "))[-1], f"report {serial} 0")
        lines = self.e.run("samples", "list").stdout.splitlines()
        self.assertEqual([line.split("\t")[1] for line in lines], ["org.example.Notes"])
        sid = lines[0].split("\t")[0]
        # a client talking to the recognizer directly, the same values, its own store
        d = TR.Env()
        self.addCleanup(d.close)
        d.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"
        c = d.start()
        c.ask("hello", "1", "org.example.Notes")
        units = "device"
        c.ask("begin", "s", "-", "normal", units, "")
        for s in raw2:
            c.ask("stroke", "s", rec_points(s))
        c.ask("read", "s", "5")
        r = c.ask("confirm", "s", "picked", TR.esc(text))
        c.close()
        self.assertEqual((r[2], r[3]), ("ok", sid))

    def test_long_strokes(self):
        """strokes far past one message's size come whole, raw and neat"""
        self.start()
        a, _ = self.app("-n", "1")
        st = [[(10 + i * 0.05, 40 + (i % 9) * 0.5, 1000 + i, (i % 100) / 99, 1.5, -2.25) for i in range(n)]
              for n in (300, 3000)]
        for s in st:
            self.pen(s)
        out = a.until(lambda line: line.startswith("neat 1 "), timeout=60)
        raw = {int(line.split()[1]): parse_points(line.split()[3:]) for line in out if line.startswith("stroke ")}
        neat = {int(line.split()[1]): parse_points(line.split()[4:]) for line in out if line.startswith("neat ")}
        for k, s in enumerate(st):
            self.assertEqual(len(raw[k]), len(s))
            self.assertEqual([p[2] for p in raw[k]], [p[2] for p in s])
            self.assertEqual([p[2:] for p in neat[k]], [p[2:] for p in raw[k]])

    def test_two_lines(self):
        """two lines of writing, with guides and without: every stroke neatened, each one
        staying on its line; the line finder agrees with the guides"""
        from glassine_hwservice.pen import lines
        for guides in (None, (60.0, 50.0)):
            with self.subTest(guides=guides):
                self.start()
                args = ["-n", "1", "-a", "0,0,600,400"] + (["-g", "%s,%s" % guides] if guides else [])
                a, _ = self.app(*args)
                st = self.ink(3, seed=11, y0=40) + self.ink(3, seed=12, y0=100, t0=9000)
                for s in st:
                    self.pen(s)
                out = a.until(lambda line: line.startswith("neat 5 "), timeout=60)
                neat = {int(line.split()[1]): parse_points(line.split()[4:]) for line in out if line.startswith("neat ")}
                self.assertEqual(sorted(neat), list(range(6)))
                for k, pts in neat.items():
                    mid = sorted(p[1] for p in pts)[len(pts) // 2]
                    self.assertLess(abs(mid - (40 if k < 3 else 100)), 25, k)
                raw = {k: [p[:2] for p in s] for k, s in enumerate(st)}
                self.assertEqual(lines(raw, guides), [[0, 1, 2], [3, 4, 5]])
                self.doCleanups()

    def test_text_off_and_private(self):
        self.start()
        a, _ = self.app("-n", "1")                               # neatening, no text
        for s in self.ink(2, seed=6):
            self.pen(s)
        out = a.until(lambda line: line.startswith("neat 1 "))
        self.assertFalse([line for line in out if line.startswith(("reading", "candidate", "done"))])
        a.send("sync")
        time.sleep(1.5)
        out = a.until(lambda line: line == "synced")
        self.assertFalse([line for line in out if line.startswith(("reading", "candidate", "done"))])
        b, _ = self.app("-n", "1", "-t", "-p", "8")              # a password field
        c, _ = self.app()                                        # both off
        before = self.service("status")["result"]["engine"]["starts"]
        for s in self.ink(2, seed=7):
            self.pen(s, surface=1)
        for s in self.ink(2, seed=8):
            self.pen(s, surface=2)
        time.sleep(1.5)
        for x in (b, c):
            x.send("sync")
            out = x.until(lambda line: line == "synced")
            self.assertEqual(len([line for line in out if line.startswith("stroke ")]), 2)
            self.assertFalse([line for line in out if line.startswith(("neat", "reading", "candidate"))])
        self.assertEqual(self.service("status")["result"]["engine"]["starts"], before)
        self.assertEqual(self.service("status")["result"]["surfaces"], 1)     # only the first one
        a.send("context 8 0")                                    # the first one turns private
        a.send("sync")
        a.until(lambda line: line == "synced")
        time.sleep(0.5)
        self.assertEqual(self.service("status")["result"]["surfaces"], 0)


if __name__ == "__main__":
    unittest.main()
