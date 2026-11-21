#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Spec 005 SC-005 with a trained model: each line of the recorded ink
(recognizer/tests/data/ink.jsonl) of more than one word written into a region (small letters
of 1.5, 3 and 6 mm), read; then one word of its value changed as a text edit (to "edited"),
each word in turn, each in a fresh region. It counts the edits after which every other word
of the value with ink keeps its own ink (passes at 95 %), and how many the edit had to set
whole (FR-020).

    HW_BIN=path/to/glassine-handwriting python3 tests/check_regions.py MODEL_DIR
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REC = ROOT.parent / "recognizer"
BIN = str(Path(os.environ.get("HW_BIN") or REC / "glassine-handwriting").resolve())
sys.path.insert(0, str(ROOT))
from glassine_hwservice import perms, regions  # noqa: E402
from glassine_hwservice.engine import Engine  # noqa: E402

PER_MM = 6.25
SIZES = (4.0, 8.0, 16.0)        # mm per em: small letters of 1.5, 3 and 6 mm for the fixture writer
INK = REC / "tests" / "data" / "ink.jsonl"


class Pen:
    def __init__(self, engine):
        self.engine, self.out = engine, []

    def spawn(self, coro):
        asyncio.ensure_future(coro)

    def send(self, m):
        self.out.append(m)


class Surface:
    def __init__(self, pen):
        self.pen, self.sid, self.app = pen, 1, "org.example.Check"


async def main(models):
    home = tempfile.mkdtemp(prefix="hw-regions-")
    env = dict(os.environ, GLASSINE_HANDWRITING_MODELS=str(models), XDG_DATA_HOME=home + "/data",
               XDG_CONFIG_HOME=home + "/conf", HOME=home)
    env.pop("GLASSINE_HANDWRITING_SIZE", None)
    perms.state = lambda app, what: "refused"           # nothing stored
    pen = Pen(Engine(BIN, env))
    s = Surface(pen)
    recs = [json.loads(ln) for ln in INK.read_text().splitlines()]
    ok = whole = tested = 0
    for mm_em in SIZES:
        u = mm_em * PER_MM
        for i, r in enumerate(recs):
            j = 0
            while True:
                g = regions.Region(s, i)
                g.set({"reading": True, "units": "device:%g" % PER_MM, "size": [4000, 400]})
                for k, st in enumerate(r["strokes"]):
                    g.stroke(k, [[x * u + 20, y * u + 3 * u, t * 1000, 0.5, 0, 0] for x, y, t, *_ in st])
                g.cancel()
                await g.update()
                ws = [w for ln in g.ordered() for w in ln]
                if len(ws) < 2 or j >= len(ws):
                    g.forget()
                    break
                tested += 1
                text = " ".join("edited" if n == j else w.text for n, w in enumerate(ws))
                await g.set_text(text)
                after = {(w.text, w.ks) for w in g.words if w.ink}
                ok += all((w.text, w.ks) in after for n, w in enumerate(ws) if w.ink and n != j)
                whole += not any(w.ink for w in g.words)
                g.forget()
                j += 1
    # samples from a region with the real recognizer: each multi-word line accepted (3 mm),
    # learning allowed; the report's result and the samples the store holds after
    perms.state = lambda app, what: "allowed"
    results = []
    u = 8.0 * PER_MM
    for i, r in enumerate(recs):
        if " " not in r["text"]:
            continue
        g = regions.Region(s, 1000 + i)
        g.set({"reading": True, "units": "device:%g" % PER_MM, "size": [4000, 400]})
        for k, st in enumerate(r["strokes"]):
            g.stroke(k, [[x * u + 20, y * u + 3 * u, t * 1000, 0.5, 0, 0] for x, y, t, *_ in st])
        g.cancel()
        await g.pause()
        serial = g.serial
        n = len(pen.out)
        await g.report({"serial": serial, "action": "accepted"})
        results += [m["result"] for m in pen.out[n:] if m["op"] == "region_report"]
        g.forget()
    stored = len((await pen.engine.cli("samples", "list")).splitlines()) if hasattr(pen.engine, "cli") else -1
    print("samples: reports %s; samples stored %d" % (results, stored))
    await pen.engine.close()
    pc = 100.0 * ok / max(1, tested)
    print("SC-005: %d of %d edits keep every unchanged word's ink (%.1f %%); set whole: %d" % (ok, tested, pc, whole))
    return pc >= 95.0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main(Path(sys.argv[1]).resolve())) else 1)
