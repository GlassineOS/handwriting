#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The service interface from an app (docs/service.md): what the service offers, the app's
permissions, a reading of some ink, the ink neatened, and the app's samples (which need the
person's permission: without it the answer says so, never an empty list).

    hw-service-example.py [INK.json]     INK: [[[x, y, t], ...], ...]; without it a made-up line

With GLASSINE_HWSERVICE_TRACE=FILE every request and response line also goes to FILE."""
import json
import math
import os
import socket
import sys


class Service:
    def __init__(self, path=None):
        run = os.environ.get("XDG_RUNTIME_DIR", "")
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path or os.path.join(run, "glassine-handwriting", "service"))
        self.f = self.s.makefile("rw", encoding="utf-8")
        self.n = 0
        t = os.environ.get("GLASSINE_HWSERVICE_TRACE")
        self.trace = open(t, "a", encoding="utf-8") if t else None

    def call(self, method, **params):
        """the result, or raises ServiceError(code, message)"""
        self.n += 1
        line = json.dumps({"id": self.n, "method": method, "params": params})
        self.f.write(line + "\n")
        self.f.flush()
        back = self.f.readline()
        if self.trace:
            self.trace.write(line + "\n" + back)
            self.trace.flush()
        r = json.loads(back)
        if "error" in r:
            raise ServiceError(r["error"]["code"], r["error"]["message"])
        return r["result"]

    def close(self):
        self.f.close()
        self.s.close()


class ServiceError(Exception):
    def __init__(self, code, message):
        super().__init__("%s: %s" % (code, message))
        self.code = code


def made_up_ink():
    """a wavy line, three strokes, 10 ms a point"""
    out, t = [], 0
    for k in range(3):
        s = []
        for i in range(30):
            s.append([20 + 40 * k + i, 50 + 8 * math.sin(i / 3), t])
            t += 10
        t += 200
        out.append(s)
    return out


def main():
    ink = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else made_up_ink()
    svc = Service()
    hello = svc.call("hello")
    print("service version %d, this app: %s; neaten %s, text %s, languages %s"
          % (hello["version"], hello["app"], hello["neaten"], hello["text"], ",".join(hello["languages"])))
    print("permissions:", svc.call("permission.get"))
    if hello["text"]:
        r = svc.call("read", strokes=ink, count=3, context={"purpose": 0})
        for c in r["candidates"]:
            print("  %-20s from %-6s %s" % (c["text"], c["from"], "fits" if c["fit"] else "would be checked"))
    if hello["neaten"]:
        r = svc.call("neaten", strokes=ink, strength=0.5)
        print("neatened %d strokes:" % len(r["strokes"]), " ".join(s["how"] for s in r["strokes"]))
    try:
        print("samples kept through this app:", len(svc.call("samples.list")["samples"]))
    except ServiceError as e:
        if e.code not in ("permission-needed", "not-allowed"):
            raise
        print("samples: the person hasn't allowed it (%s)" % e.code)
    svc.close()


if __name__ == "__main__":
    main()
