#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine against onnxruntime on a trained model: converts the ONNX file, runs both on
sample-format lines and has test-engine compare the log-probabilities (|diff| <= 1e-4,
same best class in every frame). Runs in the trainer's environment:

    cd train && uv run python ../recognizer/tools/compare-onnx.py recognizer.onnx LINES.jsonl \\
        ../recognizer/test-engine [N]
"""
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from glassine_train.data import ALPHABET
from glassine_train.features import features

sys.path.insert(0, str(Path(__file__).resolve().parent))
import onnx2ghm  # noqa: E402


def main():
    model, lines, engine = sys.argv[1:4]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 20
    ghm = onnx2ghm.convert(onnx.load(model), ALPHABET)
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    sess = ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])
    cases = []
    for line in open(lines).read().splitlines()[:n]:
        x = features(json.loads(line)["strokes"])
        cases.append({"feats": np.round(x, 7).tolist(), "logp": sess.run(None, {"ink": x[None]})[0][0].tolist()})
    with tempfile.NamedTemporaryFile("w", suffix=".json") as fh:
        json.dump({"ghm_b64": base64.b64encode(ghm).decode(), "cases": cases}, fh)
        fh.flush()
        sys.exit(subprocess.run([engine, "--engine", fh.name]).returncode)


if __name__ == "__main__":
    main()
