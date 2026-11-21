#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A random-weight base model (.ghm) for tests: no training, no blob in git.

    mkmodel.py OUT.ghm [--seed N] [--conv C] [--hidden H] [--strided S] [--ref] [--kind K] [--aligner]
                       [--charset CHARS] [--bias C]

--ref: version 2, a model reading the reference-height features (6 per row).
--kind: the kind recorded in the header (recognizer, aligner; none: not recorded, as files
from before it was). --aligner: a point aligner's shape (one strided convolution, no space,
kind aligner). --charset: another character set. --bias C: the output layer leans to C.

The character set is the English one of the trainer (kept in step by test-train-sync).
"""
import argparse
import random
import struct

CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,;:!?'-() "


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--conv", type=int, default=32)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--strided", type=int, default=3)
    ap.add_argument("--layers", type=int, default=2)
    ap.add_argument("--ref", action="store_true")
    ap.add_argument("--kind", choices=("none", "recognizer", "aligner"), default="none")
    ap.add_argument("--aligner", action="store_true")
    ap.add_argument("--charset")
    ap.add_argument("--bias")
    a = ap.parse_args()
    chars = CHARS
    if a.aligner:
        a.strided, a.kind, chars = 1, "aligner", CHARS.replace(" ", "")
    if a.charset is not None:
        chars = a.charset
    rng = random.Random(a.seed)
    version, nfeat = (2, 6) if a.ref else (1, 5)
    k, c1, conv, H, nclass = 5, a.conv // 2, a.conv, a.hidden, len(chars) + 1

    def w(n, fan_in):
        b = (3.0 / fan_in) ** 0.5
        return [rng.uniform(-b, b) for _ in range(n)]

    data = [10.0, 10.0, 1.0, 1.0, 1.0] + [1.0] * (nfeat - 5)
    data += w(c1 * nfeat * k, nfeat * k) + w(c1, nfeat * k)
    cin = c1
    for _ in range(a.strided):
        data += w(conv * cin * k, cin * k) + w(conv, cin * k)
        cin = conv
    for layer in range(a.layers):
        i = cin if layer == 0 else 2 * H
        for _ in range(2):
            data += w(4 * H * i, i) + w(4 * H * H, H) + w(4 * H, H)
    bias = [0.0] * nclass
    if a.bias:
        bias[chars.index(a.bias) + 1] = 8.0
    data += w(nclass * 2 * H, 2 * H) + bias
    cs = chars.encode()
    kind = {"none": 0, "recognizer": 1, "aligner": 2}[a.kind]
    hdr = b"GHWM" + struct.pack("<15I", version, 64, nfeat, c1, conv, a.strided, k, H, a.layers, nclass, len(cs), 0, 0,
                                kind, 0)
    with open(a.out, "wb") as f:
        f.write(hdr + cs + b"\0" * (-len(cs) % 4) + struct.pack(f"<{len(data)}f", *data))


if __name__ == "__main__":
    main()
