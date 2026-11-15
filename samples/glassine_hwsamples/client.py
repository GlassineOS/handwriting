# SPDX-License-Identifier: GPL-2.0-or-later
"""The recognizer's session interface (docs/recognizer.md, contract 1): the line protocol
on a child process, and its command line. The app keeps no samples of its own: everything
it stores, adapts and donates goes through here."""

import os
import select
import subprocess
import threading

from . import APP_ID

BIN = os.environ.get("GLASSINE_HANDWRITING_BIN", "glassine-handwriting")
TIMEOUT = 60          # seconds for one reply (a long sentence on a slow device takes a few)


def esc(s):
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def unesc(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            out.append({"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def fmt(v):
    """a number exactly as the pen gave it (repr is the shortest exact form)"""
    if float(v).is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(float(v))


def points(stroke, t0):
    """a stroke [(x, y, t_ms, p, tx, ty), ...] as POINTS; every point the same width"""
    out = []
    for p in stroke:
        vals = [p[0], p[1], p[2] - t0] + list(p[3:])
        out.append(",".join(fmt(v) for v in vals))
    return " ".join(out)


class Error(Exception):
    pass


class Recognizer:
    """One recognizer child process; restarted when it went away (an idle exit)."""

    def __init__(self, binary=BIN, client=APP_ID, env=None):
        self.binary, self.client, self.env = binary, client, env
        self.p = None
        self.n = 0
        self.lock = threading.Lock()

    def _start(self):
        self.p = subprocess.Popen([self.binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1, env=self.env)
        self.n = 0
        self._ask("hello", "1", self.client)

    def _ask(self, verb, *fields):
        self.n += 1
        rid = f"q{self.n}"
        self.p.stdin.write("\t".join([verb, rid, *fields]) + "\n")
        self.p.stdin.flush()
        # never wait forever: a recognizer that doesn't answer is restarted
        if not select.select([self.p.stdout], [], [], TIMEOUT)[0]:
            self.p.kill()
            self.p.wait()
            raise Error("the recognizer didn't answer")
        line = self.p.stdout.readline()
        if not line.endswith("\n"):
            raise BrokenPipeError("recognizer went away")
        r = line[:-1].split("\t")
        if len(r) < 2 or r[1] != rid:
            raise Error(f"unexpected reply {r!r}")
        if r[0] == "error":
            raise Error(r[2] if len(r) > 2 else "error")
        return r[2:]

    def ask(self, verb, *fields):
        with self.lock:
            for attempt in (0, 1):
                try:
                    if self.p is None or self.p.poll() is not None:
                        self._start()
                    return self._ask(verb, *fields)
                except (BrokenPipeError, OSError):
                    if self.p:
                        self.p.kill()
                        self.p.wait()
                        for f in (self.p.stdin, self.p.stdout):
                            try:
                                f.close()
                            except OSError:
                                pass
                    self.p = None
                    if attempt:
                        raise Error("no recognizer")
        raise Error("no recognizer")

    def close(self):
        with self.lock:
            if self.p:
                for f in (self.p.stdin, self.p.stdout):
                    try:
                        f.close()
                    except OSError:
                        pass
                try:
                    self.p.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self.p.kill()
                    self.p.wait()
            self.p = None

    # ------------------------------------------------------------ the session interface

    def write(self, session, lang, units, area, strokes):
        """begin a session with the strokes (and the writing area)"""
        r = self.ask("begin", session, lang, "normal", units, "")
        if r[0] != "ok":
            raise Error("begin " + r[0])
        if area:
            self.ask("area", session, ",".join(fmt(v) for v in area))
        t0 = min((p[2] for s in strokes for p in s), default=0)
        for s in strokes:
            self.ask("stroke", session, points(s, t0))

    def read(self, session, count=3):
        """[(text, score)] best first: the ink's reading alone (no words), what the hand
        itself reads"""
        r = self.ask("read", session, str(count), "raw")
        if r[0] != "text":
            return []
        n = int(r[1])
        return [(unesc(r[2 + 3 * i]), float(r[3 + 3 * i])) for i in range(n)]

    def align(self, session, prompt):
        r = self.ask("align", session, esc(prompt))
        return None if r[0] == "short" else float(r[0])

    def confirm_prompted(self, session, text, keep=False):
        """('ok', id, exact|inferred) or ('skip', reason, fit)"""
        r = self.ask("confirm", session, "prompted", esc(text), *(["keep"] if keep else []))
        if r[0] == "ok":
            return ("ok", r[1], r[2])
        return ("skip", r[1], float(r[2]) if len(r) > 2 else None)

    def donate(self, session, text, keep=False):
        """('ok', count, id) or ('skip', reason, fit)"""
        r = self.ask("donate", session, esc(text), *(["keep"] if keep else []))
        if r[0] == "ok":
            return ("ok", int(r[1]), r[2])
        return ("skip", r[1], float(r[2]) if len(r) > 2 else None)

    def end(self, session):
        try:
            self.ask("end", session)
        except Error:
            pass

    def forget(self, sample_id):
        """takes back one of this app's samples and its donated copy; the places it was in"""
        try:
            r = self.ask("forget", sample_id)
        except Error as e:
            if str(e) == "unknown-sample":
                return []
            raise
        return r[1].split(",")

    def sample_ids(self):
        r = self.ask("samples")
        return r[1:1 + int(r[0])]

    def sample(self, sample_id):
        import json
        r = self.ask("sample", sample_id)
        return json.loads(unesc(r[0]))


class Cli:
    """The command line part of contract 1 (settings, adaptation, donation)."""

    def __init__(self, binary=BIN, env=None, client=APP_ID):
        self.binary, self.env, self.client = binary, env, client

    def run(self, *args, check=True):
        p = subprocess.run([self.binary, *args], capture_output=True, text=True, env=self.env, timeout=600)
        if check and p.returncode:
            raise Error(p.stderr.strip() or f"{args[0]} failed")
        return p.stdout

    def samples(self):
        """this app's samples: [(id, time, text)] in the order stored"""
        out = []
        for line in self.run("samples", "list", "-c", self.client).splitlines():
            f = line.split("\t")
            if len(f) >= 6:
                out.append((f[0], int(f[2]), unesc(f[5])))
        return out

    def stats(self):
        d = {}
        for line in self.run("samples", "stats").splitlines():
            f = line.split("\t")
            if f[0] == "client":
                d.setdefault("clients", {})[f[1]] = int(f[2])
            elif len(f) == 2:
                d[f[0]] = f[1]
        return d

    def delete(self, sample_id):
        self.run("samples", "delete", sample_id)

    def erase(self):
        return int(self.run("samples", "erase", "-c", self.client).strip() or 0)

    def export(self, path):
        self.run("samples", "export", "-c", self.client, path)

    def import_(self, path):
        return int(self.run("samples", "import", path).strip() or 0)

    def charging(self):
        return self.run("charging").strip() == "charging"

    def plan(self, lang="en"):
        f = self.run("adapt", "--plan", "-l", lang).strip().split("\t")
        return {"train": int(f[1]), "held": int(f[2]), "new": int(f[3]), "min_train": int(f[4]),
                "min_held": int(f[5]), "charging": f[6] == "charging"}

    def adapt(self, lang="en", on_line=None, extra=()):
        """runs a requested adaptation; on_line(kind, fields) per progress line; returns the
        last line's (kind, fields)"""
        p = subprocess.Popen([self.binary, "adapt", "-r", "--progress", "-l", lang, *extra], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, env=self.env)
        last = ("skip", ["error"])
        with p:
            for line in p.stdout:
                f = line.rstrip("\n").split("\t")
                last = (f[0], f[1:])
                if on_line:
                    on_line(*last)
        return last

    def adaptations(self, lang="en"):
        """{'current': None | {time, train, held, before, after}, 'previous': ...}"""
        d = {}
        for line in self.run("adaptation", "show", "-l", lang).splitlines():
            f = line.split("\t")
            d[f[0]] = None if f[1] == "none" else {"time": int(f[1]), "train": int(f[2]), "held": int(f[3]),
                                                   "before": float(f[4]), "after": float(f[5])}
        return d

    def revert(self, lang="en"):
        self.run("adaptation", "revert", "-l", lang)

    def learning(self):
        return self.run("learning").strip() == "on"

    def donation(self):
        """(licence or None, count)"""
        lic, n = None, 0
        for line in self.run("donation", "list").splitlines():
            f = line.split("\t")
            if f[0] == "licence" and f[1] != "-":
                lic = f[1]
            elif f[0] == "samples":
                n = int(f[1])
        return lic, n

    def donation_start(self, licence):
        self.run("donation", "start", licence)

    def donation_add(self, sample_id):
        self.run("donation", "add", sample_id)

    def donation_write(self, path):
        self.run("donation", "write", path)

    def donation_erase(self):
        self.run("donation", "erase")
