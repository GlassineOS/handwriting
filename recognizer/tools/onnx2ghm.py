#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""ONNX export of the trainer (glassine-train export) -> base model file (.ghm, version 1).

    onnx2ghm.py recognizer.onnx --charset-from train/src/glassine_train/text.py -o recognizer.ghm
    onnx2ghm.py recognizer.onnx --charset 'abc...' -o recognizer.ghm

The ONNX file has no character set, so it is given here and must have exactly one
character per output class after the CTC blank. The graph is checked against what the
engine runs (input `ink` (1, points, NFEAT), output `logp` (1, frames, classes), Mul by a
scale, Conv + exact GELU, strided Conv + GELU, bidirectional LSTM layers, MatMul + Add,
LogSoftmax), so a changed export stops here instead of giving wrong readings.

The format is described in docs/recognizer.md ("Base model package").
"""

import argparse
import ast
import struct
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import numpy_helper

MAGIC = b"GHWM"
VERSION = 1


def charset_from(path: Path) -> str:
    """CHARS = "..." from the trainer's text.py, without importing it."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CHARS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"{path}: no CHARS")


def fail(msg):
    raise SystemExit(f"onnx2ghm: {msg}")


def convert(model: onnx.ModelProto, charset: str, trained_until: int = 0) -> bytes:
    g = model.graph
    init = {i.name: numpy_helper.to_array(i) for i in g.initializer}
    consts = {}
    for n in g.node:
        if n.op_type == "Constant":
            for a in n.attribute:
                if a.name == "value":
                    consts[n.output[0]] = numpy_helper.to_array(a.t)

    def dims(vi):
        return [d.dim_param or d.dim_value for d in vi.type.tensor_type.shape.dim]

    if [i.name for i in g.input] != ["ink"] or [o.name for o in g.output] != ["logp"]:
        fail("expected input 'ink' and output 'logp'")
    din, dout = dims(g.input[0]), dims(g.output[0])
    if len(din) != 3 or din[0] != 1 or len(dout) != 3 or dout[0] != 1:
        fail(f"unexpected shapes {din} -> {dout}")
    nfeat, nclass = din[2], dout[2]
    if not isinstance(nfeat, int) or not isinstance(nclass, int):
        fail("feature and class dimensions must be fixed")
    if len(charset) != nclass - 1:
        fail(f"charset has {len(charset)} characters, the model {nclass - 1} classes after the blank")

    ops = [n for n in g.node if n.op_type != "Constant"]
    kinds = [n.op_type for n in ops]
    if "Erf" not in kinds:
        fail("activation is not the exact GELU (no Erf)")
    muls = [n for n in ops if n.op_type == "Mul" and any(i in init for i in n.input)]
    if not muls or "ink" not in muls[0].input:
        fail("no input scale")
    scale = init[[i for i in muls[0].input if i in init][0]].astype(np.float32).reshape(-1)
    convs = [n for n in ops if n.op_type == "Conv"]
    lstms = [n for n in ops if n.op_type == "LSTM"]
    mm = [n for n in ops if n.op_type == "MatMul"]
    if len(convs) < 1 or len(lstms) < 1 or len(mm) != 1 or kinds[-1] != "LogSoftmax":
        fail(f"unexpected graph: {sorted(set(kinds))}")
    if kinds.count("Erf") != len(convs):
        fail("every convolution must be followed by a GELU")

    def attr(n, name, default=None):
        for a in n.attribute:
            if a.name == name:
                return onnx.helper.get_attribute_value(a)
        return default

    out = []
    k0 = None
    c1 = conv = None
    for i, n in enumerate(convs):
        w, b = init[n.input[1]], init[n.input[2]]
        k = attr(n, "kernel_shape")[0]
        stride = attr(n, "strides", [1])[0]
        pads = attr(n, "pads", [0, 0])
        if attr(n, "group", 1) != 1 or attr(n, "dilations", [1]) != [1] or pads != [k // 2, k // 2]:
            fail("convolution must be ungrouped, undilated, padded by kernel // 2")
        if k0 is None:
            k0 = k
        if k != k0 or stride != (1 if i == 0 else 2):
            fail("first convolution stride 1, the others stride 2, one kernel size")
        if i == 0:
            c1 = w.shape[0]
            if w.shape[1] != nfeat:
                fail("first convolution input is not the features")
        else:
            conv = w.shape[0]
        out += [w.astype(np.float32).ravel(), b.astype(np.float32).ravel()]
    nstrided = len(convs) - 1
    hidden = None
    for n in lstms:
        if attr(n, "direction") != b"bidirectional":
            fail("LSTM must be bidirectional")
        h = attr(n, "hidden_size")
        hidden = hidden or h
        if h != hidden:
            fail("all LSTM layers need the same size")
        if attr(n, "activations") not in (None, [b"Sigmoid", b"Tanh", b"Tanh"] * 2) or attr(n, "clip") is not None \
                or attr(n, "input_forget", 0):
            fail("LSTM with non-default activations")
        W, R, B = init[n.input[1]], init[n.input[2]], init[n.input[3]]
        if len(n.input) > 4 and n.input[4]:
            fail("LSTM with sequence lengths")
        for d in range(2):
            out += [W[d].astype(np.float32).ravel(), R[d].astype(np.float32).ravel(),
                    (B[d][:4 * h] + B[d][4 * h:]).astype(np.float32).ravel()]
    wout = init[mm[0].input[1]]
    adds = [n for n in ops if n.op_type == "Add" and mm[0].output[0] in n.input]
    if len(adds) != 1:
        fail("no output bias")
    bout = init[[i for i in adds[0].input if i in init][0]]
    if wout.shape != (2 * hidden, nclass) or bout.shape != (nclass,):
        fail("output layer shape")
    out += [wout.T.astype(np.float32).ravel(), bout.astype(np.float32).ravel()]
    if scale.shape != (nfeat,):
        fail("scale shape")

    cs = charset.encode()
    hdr = MAGIC + struct.pack("<15I", VERSION, 64, nfeat, c1, conv or c1, nstrided, k0, hidden, len(lstms), nclass,
                              len(cs), trained_until & 0xFFFFFFFF, trained_until >> 32, 0, 0)
    body = cs + b"\0" * ((-len(cs)) % 4)
    data = np.concatenate([scale] + out).astype("<f4").tobytes()
    return hdr + body + data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("onnx", type=Path)
    cs = ap.add_mutually_exclusive_group(required=True)
    cs.add_argument("--charset")
    cs.add_argument("--charset-from", type=Path, help="the trainer's text.py (its CHARS)")
    ap.add_argument("-o", type=Path, required=True)
    ap.add_argument("--trained-until", type=int, default=0,
                    help="confirmation time (s since 1970) of the newest person's sample in the training data")
    a = ap.parse_args(argv)
    charset = a.charset if a.charset is not None else charset_from(a.charset_from)
    data = convert(onnx.load(str(a.onnx)), charset, a.trained_until)
    a.o.write_bytes(data)
    print(f"{a.o}: {len(data)} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
