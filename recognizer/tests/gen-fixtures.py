#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regenerates the recorded test data in tests/data/ (needs the trainer's environment:
torch, onnx, onnxruntime, glassine-synth with its fonts fetched).

    cd train && uv run python ../recognizer/tests/gen-fixtures.py [--fonts ../synth/build/fonts]
        [--model build/runs/main/recognizer.onnx]

Writes:
  data/ink.jsonl        recorded stroke files (sample format v1) from glassine-synth
  data/features.json    features.py on edge-case strokes and on the ink: the C port's vector
  data/tiny.json        a tiny random-weight model (torch -> ONNX -> onnx2ghm, base64) with
                        onnxruntime's output on three lines: the engine's vector
  data/model-en.json    (with --model) the interface of the trained model the engine relies
                        on: charset, tensor names and shapes, and what it reads on lines of
                        training fonts. `make check-model` holds a new model to it.
Random weights only, never a trained model, go into git.
"""

import argparse
import base64
import io
import json
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
from glassine_synth.cli import font_tables
from glassine_synth.fetch import load as load_fonts
from glassine_synth.ink import Params, line
from glassine_train.data import ALPHABET
from glassine_train.features import features
from glassine_train.model import Recognizer, Single

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import onnx2ghm  # noqa: E402

WORDS = ["cat", "hello", "Note", "quick", "jumps", "fifty", "tea", "1984", "it's", "Fix", "zebra", "minimum"]
LINES = ["The quick brown fox jumps over the lazy dog", "Meet me at 10:30, please!"]
FONTS_WORDS = ["kalam", "caveat"]
FONTS_LINES = ["kalam", "patrickhand"]


def r(a, d=6):
    return np.round(np.asarray(a, float), d).tolist()


def sample(smp, prov):
    return {"version": 1, "text": smp["text"], "lang": "en", "source": "synthetic", "spans_source": "exact",
            "units": {"xy": "em"}, "strokes": [np.round(s, 4).tolist() for s in smp["strokes"]],
            "spans": smp["spans"], "fonts": smp["fonts"], "provenance": prov}


def edge_cases():
    """Strokes the C port must treat exactly as features.py does."""
    rng = np.random.default_rng(7)
    t = np.linspace(0, 0.4, 40)
    wave = np.c_[np.linspace(0, 3, 40), 0.2 * np.sin(np.linspace(0, 9, 40)), t]
    return {
        "one point": [[[1.0, 2.0, 0.0]]],
        "duplicate points": [[[0, 0, 0], [0, 0, 0.01], [0.5, 0.2, 0.02], [0.5, 0.2, 0.03], [1, 0, 0.04], [1, 0, 0.05]]],
        "shorter than the step": [[[0, 0, 0], [0.001, 0.0005, 0.01]], [[2, 0, 0.3], [2, 1, 0.4]]],
        "empty stroke": [[], [[0, 0, 0], [0, 1, 0.1], [0.3, 1.2, 0.2]], []],
        "narrow line": [[[0, 0, 0], [0.1, 3, 0.2], [0.2, 0, 0.4]]],
        "clipped tilt": [[[0, 0, 0], [3, -3, 0.3]], [[4, -4.2, 0.5], [6, -5.9, 0.7]]],
        "wave": [wave.tolist()],
        "noise": [np.c_[rng.normal(0, 1, (30, 2)).cumsum(0), np.linspace(0, 1, 30)].tolist(),
                  np.c_[rng.normal(5, 1, (12, 2)), np.linspace(1.5, 2, 12)].tolist()],
        "pause and big jump": [[[0, 0, 0], [0, 1, 0.1]], [[40, 0, 5.0], [40, 1, 5.1]]],
        "device units": [[[1200, 800, 0], [1250, 830, 0.01], [1300, 800, 0.02], [1310, 900, 0.05]],
                         [[1400, 800, 0.4], [1500, 805, 0.5]]],
    }


def tiny_model(lines):
    torch.manual_seed(1234)
    m = Recognizer(conv=8, hidden=8, layers=2, dropout=0.0, down=3).eval()
    with torch.no_grad():   # random but not tiny: so the LSTM gates do something
        for p in m.parameters():
            p.mul_(3.0)
    single = Single(m).eval()
    buf = io.BytesIO()
    x0 = torch.tensor(lines[0])[None]
    torch.onnx.export(single, (x0,), buf, input_names=["ink"], output_names=["logp"],
                      dynamic_axes={"ink": {1: "points"}, "logp": {1: "frames"}}, opset_version=17, dynamo=False)
    proto = onnx.load_from_string(buf.getvalue())
    ghm = onnx2ghm.convert(proto, ALPHABET)
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    sess = ort.InferenceSession(buf.getvalue(), so, providers=["CPUExecutionProvider"])
    cases = []
    for x in lines:
        x = np.asarray(x, np.float32)
        lp = sess.run(None, {"ink": x[None]})[0][0]
        cases.append({"feats": r(x, 7), "logp": r(lp, 6)})
    return {"ghm_b64": base64.b64encode(ghm).decode(), "cases": cases}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonts", type=Path, default=Path("../synth/build/fonts"))
    ap.add_argument("--model", type=Path)
    a = ap.parse_args()
    out = HERE / "data"
    out.mkdir(exist_ok=True)
    prov = {f["dir"]: {"name": f["name"], "license": f["license"], "source": f["source"],
                       "upstream": f["upstream"], "commit": f["upstream_commit"]} for f in load_fonts(a.fonts)}
    chars = ALPHABET.replace(" ", "")
    rng = np.random.default_rng(42)
    ink = []
    for fonts, texts in ((FONTS_WORDS, WORDS), (FONTS_LINES, LINES)):
        for f in fonts:
            tables = font_tables(a.fonts, chars, [f])
            for t in texts:
                smp = line(t, tables, rng, Params())
                ink.append(sample(smp, {k: prov[k] for k in sorted(set(smp["fonts"]))}))
    with open(out / "ink.jsonl", "w") as fh:
        for s in ink:
            fh.write(json.dumps(s) + "\n")

    vec = []
    for name, strokes in edge_cases().items():
        vec.append({"name": name, "strokes": strokes, "features": r(features([np.asarray(q, float).reshape(-1, 3)
                                                                               for q in strokes]), 7)})
    for s in ink[:6] + ink[-2:]:
        vec.append({"name": s["text"], "strokes": s["strokes"], "features": r(features(s["strokes"]), 7)})
    (out / "features.json").write_text(json.dumps(vec) + "\n")

    lines = [features(s["strokes"]) for s in (ink[0], ink[13], ink[-1])]
    (out / "tiny.json").write_text(json.dumps(tiny_model(lines)) + "\n")

    if a.model:
        m = onnx.load(str(a.model))
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        sess = ort.InferenceSession(str(a.model), so, providers=["CPUExecutionProvider"])
        g = m.graph
        dims = lambda vi: [d.dim_param or d.dim_value for d in vi.type.tensor_type.shape.dim]
        reads = []
        tables = font_tables(a.fonts, chars, ["caveat", "patrickhand"])
        for t in ["hello world", "The quick brown fox", "it's 10:30"]:
            smp = line(t, tables, np.random.default_rng(5), Params())
            x = features(smp["strokes"])
            lp = sess.run(None, {"ink": x[None]})[0][0]
            best = lp.argmax(-1)
            txt = "".join(ALPHABET[k - 1] for i, k in enumerate(best) if k and (i == 0 or k != best[i - 1]))
            reads.append({"text": t, "strokes": [np.round(q, 4).tolist() for q in smp["strokes"]],
                          "read": txt, "frames": int(lp.shape[0]), "rows": int(x.shape[0])})
        (out / "model-en.json").write_text(json.dumps({
            "charset": ALPHABET, "input": g.input[0].name, "input_shape": dims(g.input[0]),
            "output": g.output[0].name, "output_shape": dims(g.output[0]), "reads": reads}) + "\n")
    print("written to", out)


if __name__ == "__main__":
    main()
