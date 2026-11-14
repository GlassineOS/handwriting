#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The recognizer against the trainer as it is now (../train): the C features against
features.py on fresh random ink, and the character set against text.py. A change to
either side that the other doesn't follow fails here. Needs numpy.

    python3 tests/test_train_sync.py [TEST_ENGINE]
"""

import ast
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TRAIN = HERE.parent.parent / "train" / "src" / "glassine_train"
ENGINE = Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "test-engine").resolve()


def load(name):
    spec = importlib.util.spec_from_file_location(name, TRAIN / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def chars():
    for node in ast.parse((TRAIN / "text.py").read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CHARS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("text.py: no CHARS")


def ink(rng):
    """a line of random strokes: scribbles, dots, repeated points, pauses, tilt, any scale"""
    scale = 10 ** rng.uniform(-1, 3)
    tilt = rng.uniform(-0.6, 0.6)
    strokes, x, t = [], 0.0, 0.0
    for _ in range(rng.integers(1, 12)):
        n = int(rng.integers(1, 60))
        kind = rng.integers(0, 4)
        if kind == 0:
            pts = rng.normal(0, 0.3, (n, 2)).cumsum(0)
        elif kind == 1:
            pts = np.zeros((n, 2)) + rng.normal(0, 0.2, 2)
        elif kind == 2:
            a = np.linspace(0, rng.uniform(1, 7), n)
            pts = np.c_[0.4 * np.cos(a), 0.4 * np.sin(a)]
        else:
            pts = np.repeat(rng.normal(0, 0.5, (max(1, n // 3), 2)), 3, axis=0)
        pts[:, 0] += x
        x += rng.uniform(0.2, 1.5)
        pts = pts @ np.array([[np.cos(tilt), np.sin(tilt)], [-np.sin(tilt), np.cos(tilt)]]) * scale
        tt = t + np.sort(rng.uniform(0, 0.5, len(pts)))
        t = tt[-1] + rng.exponential(0.3)
        strokes.append(np.c_[pts, tt])
    if rng.random() < 0.2:
        strokes.insert(int(rng.integers(0, len(strokes))), np.zeros((0, 3)))
    return strokes


def main():
    features = load("features").features
    text_chars = chars()
    mk = (HERE / "mkmodel.py").read_text()
    if f'CHARS = "{text_chars}"' not in mk:
        raise SystemExit("tests/mkmodel.py: CHARS differs from the trainer's text.py")
    vec = json.loads((HERE / "data" / "model-en.json").read_text())
    if vec["charset"] != text_chars:
        raise SystemExit("tests/data/model-en.json: charset differs from the trainer's text.py")
    rng = np.random.default_rng(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    cases = []
    for i in range(300):
        s = ink(rng)
        f = features(s)
        cases.append({"name": f"random {i}", "strokes": [np.round(q, 6).tolist() for q in s],
                      "features": np.round(f, 7).tolist()})
        # strokes as written to the case (rounded): the features of exactly those
        cases[-1]["features"] = np.round(features([np.asarray(q, float).reshape(-1, 3) for q in cases[-1]["strokes"]]), 7).tolist()
    with tempfile.NamedTemporaryFile("w", suffix=".json") as fh:
        json.dump(cases, fh)
        fh.flush()
        r = subprocess.run([str(ENGINE), "--features", fh.name])
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
