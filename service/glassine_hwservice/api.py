# SPDX-License-Identifier: GPL-2.0-or-later
"""The service interface (spec 004 FR-012 to FR-015; docs/service.md, schema
docs/service.schema.json): JSON lines over a unix socket in the person's runtime
directory. Each request {"id", "method", "params"} gets {"id", "result"} or {"id",
"error": {"code", "message"}}; the caller is who the operating system says (ident)."""
import json
import os

from . import area, ident, outputs, perms
from .engine import Error, esc, parse_text, points, unesc
from .pen import field, lines

VERSION = 1
ERRORS = {"bad-request", "unknown-method", "permission-needed", "not-allowed", "not-yours", "unavailable",
          "failed"}


class Fail(Exception):
    def __init__(self, code, message=""):
        super().__init__(message)
        self.code, self.message = code, message


def need(app, kind):
    s = perms.state(app, kind)
    if s == "unasked":
        raise Fail("permission-needed", "the person hasn't said whether %s may %s" % (app, kind))
    if s != "allowed":
        raise Fail("not-allowed", "the person doesn't let %s %s" % (app, kind))


def strokes_of(p):
    st = p.get("strokes")
    if not isinstance(st, list) or not st or not all(isinstance(s, list) and s for s in st):
        raise Fail("bad-request", "strokes: a list of strokes, each a list of points")
    for s in st:
        for q in s:
            if not isinstance(q, list) or len(q) not in (3, 4, 6) or not all(isinstance(v, (int, float)) for v in q):
                raise Fail("bad-request", "a point is [x, y, t], [x, y, t, p] or [x, y, t, p, tx, ty]")
    return st


def area_of(p, strokes, units):
    """the request's writing area as the recognizer's AREA (FR-016): the one given, its
    missing parts as the pen interface has them (area.py: the writing's box, no ruled line,
    the guide line of one line of writing). None for em units without one given (em says
    how high a line is)"""
    a = p.get("area")
    if a is None:
        if units == "em":
            return None
        a = {}
    if not isinstance(a, dict):
        raise Fail("bad-request", "area: an object with box, line, base, rot")
    try:
        bx = [float(v) for v in a["box"]] if "box" in a else list(area.box(strokes))
        ln = float(a["line"]) if "line" in a else 1.0 if units == "em" else 0.0
        rot = int(a.get("rot", 0))
        if "base" in a:
            bs = float(a["base"])
        else:
            bs = area.base(strokes) if len(lines(dict(enumerate(strokes)))) == 1 else None
    except (TypeError, ValueError, KeyError):
        raise Fail("bad-request", "area: box [x0, y0, x1, y1], line, base and rot are numbers")
    if len(bx) != 4 or bx[2] <= bx[0] or bx[3] <= bx[1] or ("line" in a and not ln > 0) or rot not in (0, 90, 180, 270):
        raise Fail("bad-request", "area: a box that isn't empty, a line above 0, rot 0, 90, 180 or 270")
    return area.text(bx, ln, bs, rot)


class Api:
    def __init__(self, engine, pen, features):
        self.engine, self.pen, self.features = engine, pen, features
        self.n = 0

    async def session(self, app, p, strokes):
        """a session of its own for one request, with the strokes"""
        self.n += 1
        s = "a%d" % self.n
        ctx = p.get("context") or {}
        fld = field(int(ctx.get("purpose", 0)), int(ctx.get("hints", 0)))
        units = str(p.get("units") or "device")
        where = area_of(p, strokes, units)
        r = await self.engine.ask(app, "begin", s, str(ctx.get("languages") or "-"), fld,
                                  area.with_rate(units, area.rate(strokes)), esc(str(ctx.get("before", ""))[-4000:]))
        if r[1] == "private":
            return None
        pr = area.prior(units)
        if pr:
            await self.engine.ask(app, "prior", s, area._num(pr))
        for st in strokes:
            await self.engine.ask(app, "stroke", s, points(st))
        if where is not None:
            try:
                await self.engine.ask(app, "area", s, where)
            except Error:
                await self.engine.ask(app, "end", s)
                raise
        return s

    async def call(self, app, sandboxed, method, p):
        e = self.engine
        if method == "hello":
            return {"version": VERSION, "app": app, "sandboxed": sandboxed, **self.features()}
        if method == "status":
            return {"engine": {"running": e.pid is not None, "pid": e.pid, "starts": e.gen},
                    "surfaces": len(self.pen.surfaces) if self.pen else 0}
        if method == "permission.get":
            return perms.all_states(app)
        if method == "permission.set":
            if sandboxed:
                raise Fail("not-allowed", "only the person's own tools change permissions")
            for k in perms.KINDS:
                if k in p:
                    perms.set_state(str(p["app"]), k, p[k])
            return perms.all_states(str(p["app"]))
        if method == "output.get":
            return {"outputs": outputs.load(), "default": area.DEFAULT_PX_PER_MM}
        if method == "output.set":
            if sandboxed:
                raise Fail("not-allowed", "only the person's own tools set an output's density")
            v = p.get("px_per_mm")
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise Fail("bad-request", "px_per_mm: a number above 0, or null")
            try:
                outputs.set_density(p.get("output"), v)
            except ValueError as x:
                raise Fail("bad-request", "output: a name; px_per_mm above 0 and below 1000 (%s)" % x)
            return {"outputs": outputs.load(), "default": area.DEFAULT_PX_PER_MM}
        if method == "read":
            st = strokes_of(p)
            count = int(p.get("count", 3))
            if not 1 <= count <= 8:
                raise Fail("bad-request", "count: 1 to 8")
            s = await self.session(app, p, st)
            if s is None:
                return {"private": True, "candidates": []}
            try:
                rd = parse_text(await e.ask(app, "read", s, str(count), "detail"))
            finally:
                await e.ask(app, "end", s)
            if isinstance(rd, tuple):
                return {"gesture": rd[1][0], "candidates": []}
            return {"candidates": [{"text": t, "score": sc, "from": f, "fit": ok, "chars": a}
                                   for t, sc, a, f, ok in rd]}
        if method == "neaten":
            st = strokes_of(p)
            strength = float(p.get("strength", 0.5))
            if not 0 <= strength <= 1:
                raise Fail("bad-request", "strength: 0 to 1")
            s = await self.session(app, p, st)
            if s is None:
                return {"private": True, "strokes": [{"how": "kept", "points": [q[:2] for q in x]} for x in st]}
            try:
                r = await e.ask(app, "neaten", s, repr(strength), *([esc(p["text"])] if p.get("text") else []))
            finally:
                await e.ask(app, "end", s)
            out = []
            for i in range(int(r[3])):
                how, q = r[5 + 3 * i], r[6 + 3 * i]
                out.append({"how": how, "points": [[float(v) for v in x.split(",")[:2]] for x in q.split(" ")]})
            return {"text": unesc(r[2]), "strokes": out}
        if method == "samples.list":
            need(app, "samples")
            r = await e.ask(app, "list")
            k = int(r[1])
            return {"samples": [{"id": r[2 + 6 * i], "time": int(r[3 + 6 * i]), "source": r[4 + 6 * i],
                                 "language": r[5 + 6 * i], "text": unesc(r[6 + 6 * i])} for i in range(k)]}
        if method == "samples.delete":
            need(app, "samples")
            try:
                await e.ask(app, "forget", str(p.get("id", "")))
            except Error as x:
                if str(x) in ("unknown-sample", "denied"):
                    raise Fail("not-yours", "no sample of this app with that id")
                raise
            return {}
        if method == "letters.status":
            need(app, "samples")
            d = {"state": "none", "covered": 0, "font": None}
            for line in (await e.cli("letters", "status")).splitlines():
                f = line.split("\t")
                if f[0] == "state":
                    d["state"] = f[1]
                elif f[0] == "covered":
                    d["covered"] = int(f[1])
                elif f[0] == "font" and len(f) >= 3:
                    d["font"] = {"name": unesc(f[1]), "version": int(f[2])}
            return d
        raise Fail("unknown-method", method)

    async def serve(self, reader, writer):
        sock = writer.get_extra_info("socket")
        try:
            pid, uid = ident.peer(sock)
        except OSError:
            writer.close()
            return
        if uid != os.getuid():
            writer.close()
            return
        app, sandboxed = ident.identity(pid)
        while True:
            line = await reader.readline()
            if not line:
                break
            rid = None
            try:
                m = json.loads(line)
                if not isinstance(m, dict) or not isinstance(m.get("method"), str):
                    raise Fail("bad-request", "a request is {\"id\", \"method\", \"params\"}")
                rid = m.get("id")
                p = m.get("params") or {}
                if not isinstance(p, dict):
                    raise Fail("bad-request", "params: an object")
                out = {"id": rid, "result": await self.call(app, sandboxed, m["method"], p)}
            except Fail as x:
                out = {"id": rid, "error": {"code": x.code, "message": x.message}}
            except (ValueError, KeyError, TypeError) as x:
                out = {"id": rid, "error": {"code": "bad-request", "message": str(x)}}
            except Error as x:
                out = {"id": rid, "error": {"code": "failed", "message": str(x)}}
            writer.write((json.dumps(out, separators=(",", ":")) + "\n").encode())
            await writer.drain()
        writer.close()
