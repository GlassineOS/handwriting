#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The recognizer behind the handwriting service (docs/recognizer.md, Relay): one process
for several clients. `hello ID 1 NAME relay`, then `client ID NAME` before each client's
requests; a session belongs to the client it was begun for, a sample goes to its session's
client, and a read with `detail` says where each candidate came from and whether a pick of
it passes the check. Without relay, nothing changes.

    HW_BIN=path/to/glassine-handwriting python3 tests/test_relay.py
"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_recognizer as TR  # noqa: E402


def pts(rng, n=30):
    x, y, out = rng.uniform(10, 200), rng.uniform(20, 60), []
    for i in range(n):
        x += rng.uniform(-2, 3)
        y += rng.uniform(-2, 2)
        out.append(f"{x:.2f},{y:.2f},{i * 5},0.5")
    return " ".join(out)


class Relay(unittest.TestCase):
    def setUp(self):
        self.e = TR.Env()
        self.e.env["GLASSINE_HANDWRITING_FIT_OK"] = "0"
        self.rng = random.Random(3)

    def tearDown(self):
        TR.shutil.rmtree(self.e.dir, ignore_errors=True)

    def test_clients_and_sessions(self):
        c = self.e.start()
        self.assertEqual(c.ask("client", "a")[2], "not-relay")
        self.assertEqual(c.ask("hello", "1", "svc", "relay")[0], "hello")
        self.assertEqual(c.ask("client", "bad name")[2], "bad-client")
        ids = {}
        for app in ("org.example.A", "org.example.B"):
            self.assertEqual(c.ask("client", app)[2], "ok")
            self.assertEqual(c.ask("begin", app[-1], "en", "-", "device", "")[2], "ok")
            for _ in range(3):
                c.ask("stroke", app[-1], pts(self.rng))
        # a session is only its own client's
        c.ask("client", "org.example.B")
        self.assertEqual(c.ask("read", "A", "1")[2], "unknown-session")
        self.assertEqual(c.ask("begin", "A", "en", "-", "device", "")[2], "busy")
        for app in ("org.example.A", "org.example.B"):
            c.ask("client", app)
            r = c.ask("confirm", app[-1], "confirmed", "hi")
            self.assertEqual(r[2], "ok", r)
            ids[app] = r[3]
        for app in ("org.example.A", "org.example.B"):
            c.ask("client", app)
            r = c.ask("samples")
            self.assertEqual(r[2:], ["1", ids[app]])
        lines = self.e.run("samples", "list").stdout.splitlines()
        self.assertEqual(sorted(line.split("\t")[1] for line in lines), ["org.example.A", "org.example.B"])
        c.close()

    def test_read_detail(self):
        c = self.e.start()
        c.ask("hello", "1", "org.example.d")
        c.ask("begin", "s", "en", "-", "device", "")
        for _ in range(3):
            c.ask("stroke", "s", pts(self.rng))
        plain = c.ask("read", "s", "3")
        r = c.ask("read", "s", "3", "detail")
        self.assertEqual(r[2], "text")
        k = int(r[3])
        self.assertEqual(len(r), 4 + 5 * k)
        self.assertEqual(int(plain[3]), k)
        for i in range(k):
            self.assertEqual(r[4 + 5 * i:7 + 5 * i], plain[4 + 3 * i:7 + 3 * i])
            self.assertIn(r[7 + 5 * i], ("ink", "beam", "words", "guided"))
            self.assertIn(r[8 + 5 * i], ("ok", "check"))
        self.assertEqual(c.ask("read", "s", "3", "raw", "detail")[2], "text")
        for bad in (("3", "detail", "raw"), ("3", "x"), ("3", "raw", "raw")):
            self.assertEqual(c.ask("read", "s", *bad)[2], "bad-line")
        c.close()


if __name__ == "__main__":
    unittest.main()
