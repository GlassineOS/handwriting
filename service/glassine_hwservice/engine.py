# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine behind the service: one recognizer process for every app (docs/recognizer.md,
`hello … relay`), so the model is loaded once. Requests go one after the other; each names
its client first when it isn't the one before. The recognizer leaves after a while idle and
is started again on the next request; `gen` counts the starts, so sessions begun in an
earlier process can be begun again."""
import asyncio
import os
import re

BIN = os.environ.get("GLASSINE_HANDWRITING_BIN", "/usr/bin/glassine-handwriting")
TIMEOUT = 60
_ESC = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
_UNESC = re.compile(r"\\(.)", re.S)
_UNESC_MAP = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}


def esc(s):
    return "".join(_ESC.get(c, c) for c in s)


def unesc(s):
    return _UNESC.sub(lambda m: _UNESC_MAP.get(m.group(1), m.group(1)), s) if "\\" in s else s


def num(v):
    """a value exactly as given, in its shortest form"""
    v = float(v)
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)


def points(stroke):
    """[[x, y, t, p, tx, ty], …] as the recognizer's POINTS (every value as sent)"""
    return " ".join(",".join(num(v) for v in p) for p in stroke)


class Error(Exception):
    pass


class Engine:
    def __init__(self, binary=None, env=None):
        self.binary = binary or BIN
        self.env = env
        self.proc = None
        self.lock = asyncio.Lock()
        self.client = None
        self.n = 0
        self.gen = 0

    @property
    def pid(self):
        return self.proc.pid if self.proc and self.proc.returncode is None else None

    async def _start(self):
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=self.env, limit=1 << 24)
        self.gen += 1
        self.client = None
        r = await self._ask("hello", "1", "org.glassine.HandwritingService", "relay")
        if r[0] != "hello":
            raise Error("no relay")

    async def _ask(self, verb, *fields):
        self.n += 1
        rid = "q%d" % self.n
        self.proc.stdin.write(("\t".join([verb, rid, *fields]) + "\n").encode())
        await self.proc.stdin.drain()
        line = await asyncio.wait_for(self.proc.stdout.readline(), TIMEOUT)
        if not line.endswith(b"\n"):
            raise BrokenPipeError("recognizer went away")
        r = line[:-1].decode().split("\t")
        if r[0] == "drop":
            return r
        if len(r) < 2 or r[1] != rid:
            raise Error("unexpected reply")
        if r[0] == "error":
            raise Error(r[2] if len(r) > 2 else "error")
        return [r[0]] + r[2:]

    async def ask(self, client, verb, *fields):
        """the reply's fields after the id, with the verb first; Error for an error reply"""
        async with self.lock:
            for attempt in (0, 1):
                try:
                    if self.proc is None or self.proc.returncode is not None:
                        await self._start()
                    if client != self.client:
                        await self._ask("client", client)
                        self.client = client
                    return await self._ask(verb, *fields)
                except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError):
                    self.kill()
                    if attempt:
                        raise Error("no recognizer")
            raise Error("no recognizer")

    def kill(self):
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        self.proc = None

    async def close(self):
        if self.proc and self.proc.returncode is None:
            self.proc.stdin.close()
            try:
                await asyncio.wait_for(self.proc.wait(), 30)
            except asyncio.TimeoutError:
                self.kill()
        self.proc = None

    async def cli(self, *args):
        """one run of the recognizer's command line (the store's status, builds)"""
        p = await asyncio.create_subprocess_exec(self.binary, *args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE, env=self.env)
        out, err = await p.communicate()
        if p.returncode:
            raise Error(err.decode().strip() or "failed")
        return out.decode()


def parse_text(r):
    """a read reply with detail -> [(text, score, align, from, ok)]; ("gesture", fields)"""
    if r[0] != "read":
        return []
    if r[1] == "gesture":
        return ("gesture", r[2:])
    if r[1] != "text":
        return []
    k = int(r[2])
    out = []
    for i in range(k):
        t, s, a, f, c = r[3 + 5 * i:8 + 5 * i]
        out.append((unesc(t), float(s), a, f, c == "ok"))
    return out


def align_strokes(align):
    """ALIGN -> per character the set of stroke numbers it uses"""
    out = []
    for e in align.split(";"):
        out.append({int(p.split(":")[0]) for p in e.split(",") if p})
    return out


def map_align(align, m):
    """ALIGN with each stroke number N replaced by m[N]"""
    def one(p):
        n, _, rest = p.partition(":")
        return str(m[int(n)]) + (":" + rest if rest else "")
    return ";".join(",".join(one(p) for p in e.split(",") if p) for e in align.split(";"))
