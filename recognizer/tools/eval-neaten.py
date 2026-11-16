#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Neatening measured on a store of the person's samples (spec 003 FR-013 to FR-017,
SC-003, SC-004): the letter model is built from the samples not held back (one in five by
id is held back, as for adaptation), then each held-back sample is neatened with the
recognizer's own reading (no prompt given) and both versions are read back. Prints only
aggregate numbers: character error of the original and of the neatened ink against the
prompt (the ink's own reading and the reading with words, as a prose field reads), how many
strokes were pulled, given the line-level corrections or kept, how far the covered characters are from their
variant before and after (strength 1, with the sample's own spans), and the time per line
(and of a read of the same line, for comparison).
The store given is only read; everything happens in a temporary copy.

    eval-neaten.py BIN STORE_DIR --models DIR [--strength S ...] [--limit N] [--adaptation] [--no-letters]
"""
import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def esc(s):
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def unesc(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append({"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}[s[i + 1]])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def edit(a, b):
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        p, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (a[i - 1] != b[j - 1]))
    return d[len(b)]


class Rec:
    def __init__(self, binary, env):
        self.p = subprocess.Popen([binary, "-i", "0", "--no-adapt"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  env=env, text=True, encoding="utf-8")
        self.n = 0

    def ask(self, *f):
        self.n += 1
        self.p.stdin.write("\t".join([f[0], f"r{self.n}"] + [str(x) for x in f[1:]]) + "\n")
        self.p.stdin.flush()
        return self.p.stdout.readline().rstrip("\n").split("\t")

    def close(self):
        self.p.stdin.close()
        self.p.wait()


def points(stroke):
    return " ".join(",".join(repr(v) if isinstance(v, float) else str(v) for v in [p[0], p[1], p[2] * 1000] + list(p[3:]))
                    for p in stroke)


def units_of(s):
    u = s.get("units", {"xy": "em"})
    if u.get("xy") != "device":
        return "em"
    if u.get("per_mm"):
        return f"device:{u['per_mm']}" + (f":{u['rate']}" if u.get("rate") else "")
    return "device"


def session(rec, s, strokes):
    rec.ask("begin", "s", s.get("lang", "en"), "-", units_of(s), "")
    a = s.get("area")
    if a:
        b = a["box"]
        rec.ask("area", "s", f"{b[0]},{b[1]},{b[2]},{b[3]},{a['line']},{a.get('orientation', 0)}")
    for st in strokes:
        rec.ask("stroke", "s", st)


def parse_neaten(r):
    # neaten ID ok TEXT K {N HOW POINTS}xK
    text, k = unesc(r[3]), int(r[4])
    out, how = [], []
    for i in range(k):
        how.append(r[5 + 3 * i + 1])
        out.append(r[5 + 3 * i + 2])
    return text, how, out


def to_xy(pts):
    return [tuple(float(v) for v in p.split(",")[:2]) for p in pts.split(" ")]


# the distance of a character to a variant, as neaten.c measures it
def resample(xs, ys, m):
    if len(xs) < 2 or m < 2:
        return [xs[0]] * m, [ys[0]] * m
    seg = [math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]) for i in range(1, len(xs))]
    total = sum(seg)
    if total <= 0:
        return [xs[0]] * m, [ys[0]] * m
    ox, oy, k, acc = [], [], 0, 0.0
    for i in range(m):
        want = total * i / (m - 1)
        while k < len(seg) - 1 and acc + seg[k] < want:
            acc += seg[k]
            k += 1
        t = min(max((want - acc) / seg[k], 0), 1) if seg[k] > 0 else 0
        ox.append(xs[k] + t * (xs[k + 1] - xs[k]))
        oy.append(ys[k] + t * (ys[k + 1] - ys[k]))
    return ox, oy


def vdist(strokes, xh, var):
    if len(strokes) != len(var["strokes"]):
        return math.inf
    ax, ay, vx, vy = [], [], [], []
    for st, vs in zip(strokes, var["strokes"]):
        m = max(2, len(vs))
        x, y = resample([p[0] for p in st], [p[1] for p in st], m)
        ax += [v / xh for v in x]
        ay += [-v / xh for v in y]
        x, y = resample([p[0] for p in vs], [p[1] for p in vs], m)
        vx += x
        vy += y
    n = len(ax)
    acx, acy, vcx, vcy = sum(ax) / n, sum(ay) / n, sum(vx) / n, sum(vy) / n
    return math.sqrt(sum(((ax[i] - acx) - (vx[i] - vcx)) ** 2 + ((ay[i] - acy) - (vy[i] - vcy)) ** 2
                         for i in range(n)) / n)


def line_xh(s):
    hs = []
    for k, c in enumerate(s["text"]):
        if c in "acemnorsuvwxz" and k < len(s["spans"]) and s["spans"][k]:
            ys = [p[1] for j in s["spans"][k] for p in s["strokes"][j]]
            hs.append(max(ys) - min(ys))
    return statistics.median(hs) if hs else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("store")
    ap.add_argument("--models", required=True)
    ap.add_argument("--strength", type=float, action="append")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--adaptation", action="store_true", help="read with the store's adaptation (it has seen the train part only when made from the same split)")
    ap.add_argument("--no-letters", action="store_true", help="no letter model: line-level corrections only")
    a = ap.parse_args()
    strengths = a.strength or [0.5, 1.0]
    store = Path(a.store)
    lines = [ln for ln in (store / "samples.jsonl").read_text(encoding="utf-8").split("\n") if ln.strip()]
    meta = {}
    for ln in (store / "samples.meta").read_text(encoding="utf-8").split("\n")[1:]:
        f = ln.split("\t")
        if len(f) >= 5:
            meta[f[0]] = ln
    with tempfile.TemporaryDirectory() as td:
        os.chmod(td, 0o700)
        data = Path(td) / "data"
        hw = data / "glassine" / "handwriting"
        hw.mkdir(parents=True)
        env = dict(os.environ, HOME=td, XDG_DATA_HOME=str(data), XDG_CONFIG_HOME=str(Path(td) / "config"),
                   GLASSINE_HANDWRITING_MODELS=a.models)
        first = (store / "samples.meta").read_text(encoding="utf-8").split("\n")[0]
        # the ids: as samples.meta lists them, in the file's order
        ids = [ln.split("\t")[0] for ln in (store / "samples.meta").read_text(encoding="utf-8").split("\n")[1:] if ln]
        pairs = list(zip(ids, lines))
        held = [(i, l) for i, l in pairs if int(i, 16) % 5 == 0]
        train = [(i, l) for i, l in pairs if int(i, 16) % 5 != 0]
        (hw / "samples.jsonl").write_text("".join(l + "\n" for _, l in train), encoding="utf-8")
        (hw / "samples.meta").write_text(first + "\n" + "".join(meta[i] + "\n" for i, _ in train), encoding="utf-8")
        built = subprocess.run([a.bin, "letters", "build", "-f"], env=env, capture_output=True, text=True)
        print("letter model (train part):", built.stdout.strip() or built.stderr.strip())
        if a.adaptation and (store / "adaptation").is_dir():
            shutil.copytree(store / "adaptation", hw / "adaptation")
        model = [json.loads(ln) for ln in (hw / "letters" / "model").read_text().splitlines()]
        letters = {m["char"]: m for m in model if "char" in m}
        if a.no_letters:
            shutil.rmtree(hw / "letters")
        rec = Rec(a.bin, env)
        if a.limit:
            held = held[:a.limit]
        res = {s: {"err_o": 0, "err_n": 0, "chars": 0, "pulled": 0, "line": 0, "kept": 0, "ms": [],
                   "better": 0, "worse": 0, "werr_o": 0, "werr_n": 0} for s in strengths}
        read_ms = []
        closer = {"n": 0, "closer": 0, "before": [], "after": []}
        for sid, ln in held:
            s = json.loads(ln)
            strokes = [points(st) for st in s["strokes"]]
            text = s["text"]
            session(rec, s, strokes)
            t0 = time.perf_counter()
            w = rec.ask("read", "s", "1")
            read_ms.append((time.perf_counter() - t0) * 1000)
            wo = unesc(w[4]) if len(w) > 4 and w[2] == "text" and w[3] != "0" else ""
            o = rec.ask("read", "s", "1", "raw")
            ro = unesc(o[4]) if len(o) > 4 and o[2] == "text" and o[3] != "0" else ""
            for st in strengths:
                t0 = time.perf_counter()
                r = rec.ask("neaten", "s", st)
                ms = (time.perf_counter() - t0) * 1000
                _, how, out = parse_neaten(r)
                session(rec, s, out)
                n = rec.ask("read", "s", "1", "raw")
                rn = unesc(n[4]) if len(n) > 4 and n[2] == "text" and n[3] != "0" else ""
                n = rec.ask("read", "s", "1")
                wn = unesc(n[4]) if len(n) > 4 and n[2] == "text" and n[3] != "0" else ""
                session(rec, s, strokes)
                q = res[st]
                eo, en = edit(ro, text), edit(rn, text)
                q["err_o"] += eo
                q["err_n"] += en
                q["better"] += en < eo
                q["worse"] += en > eo
                q["chars"] += len(text)
                q["werr_o"] += edit(wo, text)
                q["werr_n"] += edit(wn, text)
                q["ms"].append(ms)
                for h in how:
                    q[h] += 1
            # closeness at strength 1 with the sample's own spans
            spans = s.get("spans") or []
            align = ";".join(",".join(str(j) for j in sp) for sp in spans)
            r = rec.ask("neaten", "s", "1", esc(text), align)
            _, how, out = parse_neaten(r)
            nxy = [to_xy(p) for p in out]
            xh = line_xh(s)
            cnt = {}
            for sp in spans:
                for j in sp:
                    cnt[j] = cnt.get(j, 0) + 1
            for k, c in enumerate(text):
                le = letters.get(c)
                if not le or not le["covered"] or k >= len(spans) or not spans[k] or xh <= 0:
                    continue
                if any(cnt[j] != 1 for j in spans[k]):
                    continue
                own = sorted(spans[k])
                before = [[(p[0], p[1]) for p in s["strokes"][j]] for j in own]
                after = [nxy[j] for j in own]
                db = min(vdist(before, xh, v) for v in le["variants"])
                if not math.isfinite(db):
                    continue
                da = min(vdist(after, xh, v) for v in le["variants"])
                closer["n"] += 1
                closer["closer"] += da < db
                closer["before"].append(db)
                closer["after"].append(da)
        rec.close()
        print(f"held-back samples: {len(held)}")
        for st, q in res.items():
            ms = sorted(q["ms"])
            print(f"strength {st}: character error original {100 * q['err_o'] / max(1, q['chars']):.1f}%, "
                  f"neatened {100 * q['err_n'] / max(1, q['chars']):.1f}% (better {q['better']}, worse {q['worse']}); "
                  f"strokes pulled {q['pulled']}, line {q['line']}, kept {q['kept']}; "
                  f"time median {ms[len(ms) // 2]:.1f} ms, max {ms[-1]:.1f} ms, over 300 ms {sum(m > 300 for m in ms)}" if ms else "")
            print(f"  with words: character error original {100 * q['werr_o'] / max(1, q['chars']):.1f}%, "
                  f"neatened {100 * q['werr_n'] / max(1, q['chars']):.1f}%")
        if read_ms:
            rm = sorted(read_ms)
            print(f"a read of the same lines: median {rm[len(rm) // 2]:.1f} ms, max {rm[-1]:.1f} ms")
        if closer["n"]:
            print(f"covered characters with strokes of their own: {closer['n']}, closer to their variant at "
                  f"strength 1: {closer['closer']}; distance median {statistics.median(closer['before']):.3f} -> "
                  f"{statistics.median(closer['after']):.3f} x-heights")


if __name__ == "__main__":
    sys.exit(main())
