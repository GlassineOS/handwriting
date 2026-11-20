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

    eval-neaten.py BIN STORE_DIR --models DIR [--strength S ...] [--limit N] [--adaptation] [--no-letters] [--words] [--tmp DIR]

--words neatens as the pen interface's service does (service/glassine_hwservice/pen.py): the
line read once, then each word of the best reading on its own (without the reading as its
text: the service has no confirmed text, spec 003 FR-043); the time is that of the read and
all the words.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "service"))


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


def session(rec, s, strokes, name="s"):
    rec.ask("begin", name, s.get("lang", "en"), "-", units_of(s), "")
    a = s.get("area")
    if a:
        b = a["box"]
        rec.ask("area", name, f"{b[0]},{b[1]},{b[2]},{b[3]},{a['line']},{a.get('orientation', 0)}")
    for st in strokes:
        rec.ask("stroke", name, st)


def neaten_words(rec, s, strokes, strength):
    """the service's way: a read with detail, then each word apart -> (how, points) per stroke"""
    from glassine_hwservice.engine import parse_text
    from glassine_hwservice.pen import words
    r = rec.ask("read", "s", "1", "detail")
    rd = parse_text([r[0]] + r[2:])
    how, out = ["kept"] * len(strokes), list(strokes)
    if not rd or isinstance(rd, tuple):
        return how, out
    for wt, ks in words(rd[0][0], rd[0][2]):
        ks = sorted(ks)
        if not ks:
            continue
        session(rec, s, [strokes[k] for k in ks], "w")
        _, h, o = parse_neaten(rec.ask("neaten", "w", strength))
        rec.ask("end", "w")
        for j, k in enumerate(ks):
            how[k], out[k] = h[j], o[j]
    return how, out


def parse_neaten(r):
    # neaten ID ok TEXT K {N HOW POINTS}xK
    text, k = unesc(r[3]), int(r[4])
    out, how = [], []
    for i in range(k):
        how.append(r[5 + 3 * i + 1])
        out.append(r[5 + 3 * i + 2])
    WHY.append(r[5 + 3 * k].split(" ") if len(r) > 5 + 3 * k and r[5 + 3 * k] else [])
    return text, how, out


WHY = []    # the WHY field of each reply, in order (parse_neaten)


def why_counts(start):
    c = {}
    for w in WHY[start:]:
        for x in w:
            c[x] = c.get(x, 0) + 1
    return c


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


BANDS, Y0, Y1 = 8, -0.6, 1.6


def profile(strokes, xh, base):
    """the ink profile as spacing.c makes it: per height band the leftmost and rightmost x"""
    lo, hi = [math.inf] * BANDS, [-math.inf] * BANDS
    bw = (Y1 - Y0) / BANDS

    def band(y):
        return max(0, min(BANDS - 1, int(math.floor((y - Y0) / bw))))

    def put(b, x):
        lo[b] = min(lo[b], x)
        hi[b] = max(hi[b], x)

    for st in strokes:
        pts = [(x / xh, (base - y) / xh) for x, y in st]
        for i, (x1, y1) in enumerate(pts):
            x0, y0 = pts[i - 1] if i else (x1, y1)
            y0, y1 = (min(max(y, Y0 + 1e-9), Y1 - 1e-9) for y in (y0, y1))
            b0, b1 = band(y0), band(y1)
            put(b0, x0)
            put(b1, x1)
            for b in range(min(b0, b1) + 1, max(b0, b1) + 1):
                ye = Y0 + b * bw
                xe = x0 + (ye - y0) / (y1 - y0) * (x1 - x0)
                put(b - 1, xe)
                put(b, xe)
    return lo, hi


def profile_gap(a, b):
    g = min((b[0][k] - a[1][k] for k in range(BANDS) if math.isfinite(a[1][k]) and math.isfinite(b[0][k])), default=None)
    if g is not None:
        return g
    return min(x for x in b[0] if math.isfinite(x)) - max(x for x in a[1] if math.isfinite(x))


def line_gaps(text, spans, strokes, xh, base):
    """the profile gaps (x-heights) of neighbouring letters with strokes of their own"""
    cnt = {}
    for sp in spans:
        for j in sp:
            cnt[j] = cnt.get(j, 0) + 1
    out = []
    for k in range(len(text) - 1):
        if text[k] == " " or text[k + 1] == " " or k + 1 >= len(spans) or not spans[k] or not spans[k + 1]:
            continue
        if any(cnt[j] != 1 for j in spans[k] + spans[k + 1]):
            continue
        a = profile([strokes[j] for j in spans[k]], xh, base)
        b = profile([strokes[j] for j in spans[k + 1]], xh, base)
        out.append((text[k], text[k + 1], profile_gap(a, b)))
    return out


def line_base(s, xh):
    ys = [max(p[1] for j in s["spans"][k] for p in s["strokes"][j])
          for k, c in enumerate(s["text"]) if c in "acemnorsuvwxz" and k < len(s["spans"]) and s["spans"][k]]
    return statistics.median(ys) if ys else 0


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
    ap.add_argument("--words", action="store_true", help="word by word, as the pen interface's service")
    ap.add_argument("--tmp", help="where the temporary copy is made (for a person's store, a directory as private as it)")
    a = ap.parse_args()
    strengths = a.strength or [0.5, 1.0]
    store = Path(a.store)
    lines = [ln for ln in (store / "samples.jsonl").read_text(encoding="utf-8").split("\n") if ln.strip()]
    meta = {}
    for ln in (store / "samples.meta").read_text(encoding="utf-8").split("\n")[1:]:
        f = ln.split("\t")
        if len(f) >= 5:
            meta[f[0]] = ln
    with tempfile.TemporaryDirectory(dir=a.tmp) as td:
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
        setw, setr = {}, {"err": 0, "werr": 0, "chars": 0}
        dropped = {"letters": 0, "dropped": 0, "noread": 0}
        closer = {"n": 0, "closer": 0, "before": [], "after": []}
        spacing = next((m["spacing"] for m in model if "spacing" in m), None)
        learned = {(p[0], p[1]): p[2] for p in spacing["pairs"]} if spacing else {}
        spread = {"before": [], "after": [], "dev_b": [], "dev_a": []}
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
                w0 = len(WHY)
                t0 = time.perf_counter()
                if a.words:
                    how, out = neaten_words(rec, s, strokes, st)
                else:
                    _, how, out = parse_neaten(rec.ask("neaten", "s", st))
                ms = (time.perf_counter() - t0) * 1000
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
                for x, v in why_counts(w0).items():
                    q.setdefault("why", {})[x] = q.setdefault("why", {}).get(x, 0) + v
            # closeness at strength 1 with the sample's own spans
            spans = s.get("spans") or []
            align = ";".join(",".join(str(j) for j in sp) for sp in spans)
            w0 = len(WHY)
            r = rec.ask("neaten", "s", "1", esc(text), align)
            _, how, out = parse_neaten(r)
            for x, v in why_counts(w0).items():
                setw[x] = setw.get(x, 0) + v
            # a letter the reading gives ink but Neatest leaves without ink of its own: dropped
            wy = WHY[-1] if len(WHY) > w0 else []
            for k, c in enumerate(text):
                if c.isspace() or k >= len(wy):
                    continue
                if k < len(spans) and spans[k]:
                    dropped["letters"] += 1
                    dropped["dropped"] += wy[k] == "no-ink"
                else:
                    dropped["noread"] += 1
            nxy = [to_xy(p) for p in out]
            session(rec, s, out)
            n = rec.ask("read", "s", "1", "raw")
            rn = unesc(n[4]) if len(n) > 4 and n[2] == "text" and n[3] != "0" else ""
            n = rec.ask("read", "s", "1")
            wn = unesc(n[4]) if len(n) > 4 and n[2] == "text" and n[3] != "0" else ""
            setr["err"] += edit(rn, text)
            setr["werr"] += edit(wn, text)
            setr["chars"] += len(text)
            session(rec, s, strokes)
            xh = line_xh(s)
            cnt = {}
            for sp in spans:
                for j in sp:
                    cnt[j] = cnt.get(j, 0) + 1
            if xh > 0 and how.count("pulled") + how.count("line") > 0:
                base = line_base(s, xh)
                gb = line_gaps(text, spans, [[(p[0], p[1]) for p in st] for st in s["strokes"]], xh, base)
                ga = line_gaps(text, spans, nxy, xh, base)
                if len(gb) >= 3 and len(ga) == len(gb):
                    spread["before"].append(statistics.pstdev([g for _, _, g in gb]))
                    spread["after"].append(statistics.pstdev([g for _, _, g in ga]))
                    for (ca, cb, g0), (_, _, g1) in zip(gb, ga):
                        if (ca, cb) in learned:
                            spread["dev_b"].append(abs(g0 - learned[(ca, cb)]))
                            spread["dev_a"].append(abs(g1 - learned[(ca, cb)]))
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
            print("  why: " + ", ".join(f"{k} {v}" for k, v in sorted(q.get("why", {}).items())))
        print(f"Neatest with the text (words set in the font): character error {100 * setr['err'] / max(1, setr['chars']):.1f}%, "
              f"with words {100 * setr['werr'] / max(1, setr['chars']):.1f}%; why: "
              + ", ".join(f"{k} {v}" for k, v in sorted(setw.items())))
        print(f"  characters with ink in the reading {dropped['letters']}, left without ink of their own "
              f"(dropped) {dropped['dropped']}; characters the reading gives no ink {dropped['noread']}")
        if read_ms:
            rm = sorted(read_ms)
            print(f"a read of the same lines: median {rm[len(rm) // 2]:.1f} ms, max {rm[-1]:.1f} ms")
        if spread["before"]:
            print(f"spacing between letters at strength 1, per line ({len(spread['before'])} lines): spread median "
                  f"{statistics.median(spread['before']):.3f} -> {statistics.median(spread['after']):.3f} x-heights; "
                  f"learned pairs ({len(spread['dev_b'])}) off their learned gap by median "
                  f"{statistics.median(spread['dev_b'] or [0]):.3f} -> {statistics.median(spread['dev_a'] or [0]):.3f}")
        if closer["n"]:
            print(f"covered characters with strokes of their own: {closer['n']}, closer to their variant at "
                  f"strength 1: {closer['closer']}; distance median {statistics.median(closer['before']):.3f} -> "
                  f"{statistics.median(closer['after']):.3f} x-heights")


if __name__ == "__main__":
    sys.exit(main())
