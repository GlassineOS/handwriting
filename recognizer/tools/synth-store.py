#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A synthetic store of prompted samples for measuring the letter model and neatening
(spec 003) without anyone's real ink: one made-up writer is one handwriting font from
glassine-synth, humanised with its measured preset (`real1`: letter-shape variation, zone
sizes, steady speed), so the same letter comes out a little different each time (loops
grow or shrink, strokes run longer or shorter) as a person's does. The texts are the
model's prompts (models/LANG/prompts.txt, CC0), their words, and the letters and digits
alone. Samples go in through `samples import`, as the samples app's export would bring them.

    uv run --project ../synth python3 tools/synth-store.py BIN OUT_DIR --fonts DIR --font FAMILY
        [--seed N] [--join P] [--repeat N]

OUT_DIR becomes an XDG data home: the store is OUT_DIR/glassine/handwriting. --fonts is a
glassine-synth font cache (`glassine-synth fetch`); --join the chance two close strokes of a
word are written as one (0: every letter its own strokes).
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "synth" / "src"))

import numpy as np  # noqa: E402

from glassine_synth.cli import font_tables  # noqa: E402
from glassine_synth.ink import XH, line, preset  # noqa: E402

XH_DEV = 30.0       # device units per x-height
LINE = 300.0        # the area's line spacing, device units (an x-height is a tenth of it)
ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def texts(prompts, repeat, rnd):
    out = []
    sentences = [s.strip() for s in prompts if s.strip()]
    words = sorted({w.strip(".,;:!?()'\"-") for s in sentences for w in s.split()} - {""})
    for _ in range(repeat):
        out += sentences
        out += words
        out += list(ALNUM)
    rnd.shuffle(out)
    return out


def to_sample(smp, rnd):
    k = XH_DEV / XH
    base = LINE * 2 / 3
    strokes = []
    for s in smp["strokes"]:
        strokes.append([[round(20 + float(p[0]) * k, 3), round(base + float(p[1]) * k, 3), round(float(p[2]), 4), 0.5]
                        for p in s])
    xmax = max((p[0] for s in strokes for p in s), default=200)
    s = {"version": 1, "text": smp["text"], "lang": "en", "strokes": strokes, "spans": smp["spans"],
         "spans_source": "exact", "source": "prompted", "units": {"xy": "device", "per_mm": 4.0, "rate": 200},
         "area": {"box": [0, 0, round(xmax + 40), LINE * 3], "line": LINE, "orientation": 0},
         "reading": [{"text": smp["text"], "score": -1.0}]}
    r = rnd.random()
    if r < 0.3:
        s["written"] = "careful"
    elif r < 0.45:
        s["written"] = "quick"
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("out")
    ap.add_argument("--fonts", type=Path, required=True)
    ap.add_argument("--font", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--join", type=float, default=0.0)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--lang", default="en")
    a = ap.parse_args()
    prompts = (HERE.parents[1] / "models" / a.lang / "prompts.txt").read_text(encoding="utf-8").splitlines()
    chars = "".join(sorted(({c for t in prompts for c in t} | set(ALNUM)) - {" "}))
    tables = font_tables(a.fonts, chars, only=[a.font])
    if not tables:
        sys.exit(f"no font {a.font} in {a.fonts}")
    rnd = random.Random(a.seed)
    rng = np.random.default_rng(a.seed)
    p = preset("real1", join=a.join)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"format": "glassine-handwriting-export", "version": 1})]
    for i, t in enumerate(texts(prompts, a.repeat, rnd)):
        smp = line(t, tables, rng, p)
        if not any(smp["spans"]):
            continue
        s = to_sample(smp, rnd)
        lines.append(json.dumps({"sample": s, "client": "org.example.synthetic", "confirmed": 1700000000 + i,
                                 "source": "prompted"}, ensure_ascii=False))
    env = dict(os.environ, XDG_DATA_HOME=str(out.resolve()), HOME=str(out.resolve()))
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write("\n".join(lines) + "\n")
    try:
        subprocess.run([a.bin, "samples", "import", f.name], env=env, check=True)
    finally:
        os.unlink(f.name)
    print(f"{len(lines) - 1} samples in {out / 'glassine' / 'handwriting'}")


if __name__ == "__main__":
    main()
