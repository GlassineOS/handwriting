#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A trained model through the pen interface against the keyboard's own path (spec 004
FR-016, FR-017): the recorded ink (recognizer/tests/data/ink.jsonl, em) written at a few
sizes in px with their size per mm, read

- through the service as an app surface without an ink area gets it (a stand-in
  compositor on the pen channel, units device:PER_MM as the compositor sends them),
- as the keyboard reads it (device:PER_MM:RATE, its paper: a line half the paper high,
  the guide line two thirds down, the ink an em of 0.3 papers),
- and as the service read it without an area (device:PER_MM, no area);

each with the line alone (no letters of the person) and with the fixture writer's letters
known (GLASSINE_HANDWRITING_SIZE). It prints the character error rates and fails when a
model reading the size (ghm version 2) reads more than 1.5 points worse through the service
than through the keyboard's path, or any model reads worse through the service than without
an area.

    HW_BIN=path/to/glassine-handwriting python3 tests/check_sizes.py MODEL_DIR
"""
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REC = ROOT.parent / "recognizer"
BIN = str(Path(os.environ.get("HW_BIN") or REC / "glassine-handwriting").resolve())
sys.path.insert(0, str(REC / "tests"))
sys.path.insert(0, str(ROOT))
from check_model import letters  # noqa: E402
from glassine_hwservice import area as A  # noqa: E402

PER_MM = 6.25
SIZES = (4.0, 8.0, 16.0)        # mm per em: small letters of 1.5, 3 and 6 mm for the fixture writer
INK = REC / "tests" / "data" / "ink.jsonl"


def lev(a, b):
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (ca != cb))
    return d[-1]


def cer(got, recs):
    return 100.0 * sum(lev(g, r["text"]) for g, r in zip(got, recs)) / sum(len(r["text"]) for r in recs)


def placed(r, mm_em):
    """a fixture line in px: an em of mm_em millimetres, its baseline (y 0) two thirds down a
    keyboard paper of 1/0.3 em, times in ms; and that paper's height"""
    u = mm_em * PER_MM
    h = u / 0.3
    return [[[x * u + 20, y * u + h * 2 / 3, t * 1000] for x, y, t, *_ in s] for s in r["strokes"]], h


class Direct:
    def __init__(self, env):
        self.p = subprocess.Popen([BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        self.n = 0
        self.ask("hello", "1", "check-model")

    def ask(self, *f):
        self.n += 1
        self.p.stdin.write("\t".join([f[0], "q%d" % self.n, *f[1:]]) + "\n")
        self.p.stdin.flush()
        return self.p.stdout.readline().rstrip("\n").split("\t")

    def read(self, units, st, area):
        from glassine_hwservice.engine import points
        self.ask("begin", "s", "-", "normal", units, "")  # as the service begins a surface
        for s in st:
            self.ask("stroke", "s", points(s))
        if area:
            self.ask("area", "s", area)
        r = self.ask("read", "s", "5")
        self.ask("end", "s")
        return r[4] if len(r) > 4 and r[2] == "text" else ""

    def close(self):
        self.p.stdin.close()
        self.p.wait()


def through_service(env, lines):
    """each line on a surface of its own, text on: the best candidate"""
    tmp = Path(tempfile.mkdtemp(prefix="hws-model-", dir="/tmp"))
    a, b = socket.socketpair()
    svc = subprocess.Popen([sys.executable, "-m", "glassine_hwservice", "--socket", str(tmp / "service"),
                            "--pen-fd", str(b.fileno())], env=dict(env, GLASSINE_HANDWRITING_BIN=BIN,
                                                                   PYTHONPATH=str(ROOT)),
                           pass_fds=[b.fileno()], cwd=str(ROOT))
    b.close()
    buf = b""
    got = {}

    def send(**m):
        a.sendall((json.dumps(m) + "\n").encode())
    for i, st in enumerate(lines):
        send(op="surface", s=i, sandbox="org.example.Check", pid=0)
        send(op="set", s=i, text=True, units="device:%g" % PER_MM)
        for k, s in enumerate(st):
            send(op="stroke", s=i, k=k, points=[[x, y, t, 0.5, 0, 0] for x, y, t in s])
    while len(got) < len(lines):
        if not select.select([a], [], [], 120)[0]:
            raise SystemExit("the service sent no candidates for %d lines" % (len(lines) - len(got)))
        d = a.recv(1 << 20)
        if not d:
            raise SystemExit("the service went away")
        buf += d
        while b"\n" in buf:
            ln, _, buf = buf.partition(b"\n")
            m = json.loads(ln)
            if m.get("op") == "candidates":
                got[m["s"]] = m["items"][0]["text"] if m["items"] else ""
    a.close()
    svc.wait(timeout=60)
    return [got[i] for i in range(len(lines))]


def main():
    models = Path(sys.argv[1]).resolve()
    with open(models / "en" / "recognizer.ghm", "rb") as fh:
        version = int.from_bytes(fh.read(8)[4:8], "little")
    recs = [json.loads(ln) for ln in INK.read_text().splitlines()]
    x, c = letters(INK)
    home = tempfile.mkdtemp(prefix="hw-model-")
    base = dict(os.environ, GLASSINE_HANDWRITING_MODELS=str(models), XDG_DATA_HOME=home + "/data",
                XDG_CONFIG_HOME=home + "/conf", HOME=home)
    base.pop("GLASSINE_HANDWRITING_SIZE", None)
    print("version %d model, recorded ink (%d lines), CER %%; px per mm %g" % (version, len(recs), PER_MM))
    print("%-34s" % "mm per em (small letters mm)" + "".join("%12s" % ("%g (%.1f)" % (e, x * e)) for e in SIZES))
    fails = []
    for known in (False, True):
        rows = {"service, no ink area": [], "keyboard's paper": [], "service before (no area)": []}
        for e in SIZES:
            env = dict(base)
            if known:
                env["GLASSINE_HANDWRITING_SIZE"] = "%.4f,%.4f" % (x * e * PER_MM, c * e * PER_MM)
            lines = [placed(r, e) for r in recs]
            rows["service, no ink area"].append(cer(through_service(env, [st for st, _ in lines]), recs))
            d = Direct(env)
            kb, old = [], []
            for st, h in lines:
                units = A.with_rate("device:%g" % PER_MM, A.rate(st))
                kb.append(d.read(units, st, A.text((0, 0, 40 * h, h), h / 2, h * 2 / 3)))
                old.append(d.read("device:%g" % PER_MM, st, None))
            d.close()
            rows["keyboard's paper"].append(cer(kb, recs))
            rows["service before (no area)"].append(cer(old, recs))
        print("letters %s" % ("known" if known else "not known (the line alone)"))
        for k, v in rows.items():
            print("  %-32s" % k + "".join("%12.2f" % f for f in v))
        for e, s, kbd, old in zip(SIZES, rows["service, no ink area"], rows["keyboard's paper"],
                                  rows["service before (no area)"]):
            if version >= 2 and s > kbd + 1.5:
                fails.append("%g mm per em, letters %s: service %.2f vs keyboard %.2f" % (e, known, s, kbd))
            if s > old + 0.01:
                fails.append("%g mm per em, letters %s: service %.2f vs before %.2f" % (e, known, s, old))
    for f in fails:
        print("FAIL:", f)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
