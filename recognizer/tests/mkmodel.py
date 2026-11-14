#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A random-weight base model (.ghm, version 1) for tests: no training, no blob in git.

    mkmodel.py OUT.ghm [--seed N] [--conv C] [--hidden H] [--strided S]

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
    a = ap.parse_args()
    rng = random.Random(a.seed)
    nfeat, k, c1, conv, H, nclass = 5, 5, a.conv // 2, a.conv, a.hidden, len(CHARS) + 1

    def w(n, fan_in):
        b = (3.0 / fan_in) ** 0.5
        return [rng.uniform(-b, b) for _ in range(n)]

    data = [10.0, 10.0, 1.0, 1.0, 1.0]
    data += w(c1 * nfeat * k, nfeat * k) + w(c1, nfeat * k)
    cin = c1
    for _ in range(a.strided):
        data += w(conv * cin * k, cin * k) + w(conv, cin * k)
        cin = conv
    for layer in range(a.layers):
        i = cin if layer == 0 else 2 * H
        for _ in range(2):
            data += w(4 * H * i, i) + w(4 * H * H, H) + w(4 * H, H)
    data += w(nclass * 2 * H, 2 * H) + [0.0] * nclass
    cs = CHARS.encode()
    hdr = b"GHWM" + struct.pack("<15I", 1, 64, nfeat, c1, conv, a.strided, k, H, a.layers, nclass, len(cs), 0, 0, 0, 0)
    with open(a.out, "wb") as f:
        f.write(hdr + cs + b"\0" * (-len(cs) % 4) + struct.pack(f"<{len(data)}f", *data))


if __name__ == "__main__":
    main()
