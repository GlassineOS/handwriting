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


def main():
    models = Path(sys.argv[1]).resolve()
    env = dict(os.environ, GLASSINE_HANDWRITING_MODELS=str(models))
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
