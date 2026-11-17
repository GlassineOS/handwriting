#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A trained model against what the recognizer relies on (tests/data/model-en.json, made
by gen-fixtures.py from the model the recognizer was built against): the lines it read
right must still read right, and the recorded ink is read with a character error rate
printed for the release notes.

    HW_BIN=./glassine-handwriting python3 tests/check_model.py MODEL_DIR
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = os.environ.get("HW_BIN") or str(HERE.parent / "glassine-handwriting")


SMALL = set("acemnorsuvwxz")


def tall(c):
    return c.isupper() or c.isdigit() or c in "bdhkl"   # as size.c, less t, f, J and Q


def letters(path):
    """the fixture writer's letters as the recognizer measures a person's (size.c measure):
    per word of three or more characters the median height of its small letters and of its
    tall ones drawn with strokes of their own, then the median over the words"""
    xs, cs = [], []
    for line in path.read_text().splitlines():
        r = json.loads(line)
        t, st, sp = r["text"], r["strokes"], r["spans"]
        if len(t) < 3 or len(sp) != len(t):
            continue
        uses = [0] * len(st)
        for s in sp:
            for i in s:
                uses[i] += 1
        x, c = [], []
        for ch, s in zip(t, sp):
            if not s or any(uses[i] != 1 for i in s) or not (ch in SMALL or tall(ch)):
                continue
            ys = [p[1] for i in s for p in st[i]]
            (x if ch in SMALL else c).append(max(ys) - min(ys))
        if x:
            xs.append(sorted(x)[len(x) // 2] if len(x) % 2 else sum(sorted(x)[len(x) // 2 - 1:len(x) // 2 + 1]) / 2)
        if c:
            cs.append(sorted(c)[len(c) // 2] if len(c) % 2 else sum(sorted(c)[len(c) // 2 - 1:len(c) // 2 + 1]) / 2)
    med = lambda v: sorted(v)[len(v) // 2] if len(v) % 2 else sum(sorted(v)[len(v) // 2 - 1:len(v) // 2 + 1]) / 2
    return med(xs), med(cs)


def main():
    models = Path(sys.argv[1]).resolve()
    env = dict(os.environ, GLASSINE_HANDWRITING_MODELS=str(models))
    with open(models / "en" / "recognizer.ghm", "rb") as fh:
        version = int.from_bytes(fh.read(8)[4:8], "little")
    if version >= 2 and "GLASSINE_HANDWRITING_SIZE" not in env:
        # a model reading the reference height: the fixtures are em ink without a writing area,
        # so it gets the fixture writer's letters, measured as a person's would be
        x, c = letters(HERE / "data" / "ink.jsonl")
        env["GLASSINE_HANDWRITING_SIZE"] = f"{x:.4f},{c:.4f}"
        print(f"version {version} model: the fixture writer's letters {x:.3f} and {c:.3f} em")
    vec = json.loads((HERE / "data" / "model-en.json").read_text())
    fails = 0
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl") as fh:
        for r in vec["reads"]:
            fh.write(json.dumps({"version": 1, "text": r["text"], "lang": "en", "source": "synthetic",
                                 "spans_source": "exact", "units": {"xy": "em"}, "strokes": r["strokes"],
                                 "spans": [[] for _ in r["text"]]}) + "\n")
        fh.flush()
        out = subprocess.run([BIN, "read", "-l", "en", fh.name], env=env, capture_output=True, text=True, check=True)
    for r, line in zip(vec["reads"], out.stdout.splitlines()):
        got = line.split("\t")[1] if "\t" in line else ""
        ok = got == r["text"]
        fails += not ok
        print(f"{'ok' if ok else 'FAIL'}: {r['text']!r} read as {got!r}")
    res = subprocess.run([BIN, "read", "-l", "en", str(HERE / "data" / "ink.jsonl")], env=env, capture_output=True,
                         text=True, check=True)
    print("recorded ink:", res.stderr.strip())
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
