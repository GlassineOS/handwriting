#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Neatening's ways measured against each other on a store of the person's samples (spec 003
FR-014, FR-043): how faithful each is to the ink as written and how neat it comes out. The
letter model is built from the samples not held back (one in five by id is held back, as
eval-neaten.py does); each held-back sample is neatened with its text and its own spans in
every way:

    Light, Medium, Strong   the pull at 0.25, 0.5 and 0.75
    pull 1                  the pull all the way, no word set ($GLASSINE_HANDWRITING_NEATEN set=0)
    set                     Neatest: each word set in the font (FR-043)
    set, conditioned        Neatest with each word set along its own baseline, lean, x-height
                            and spacing, each letter onto its nearest version (cond=1)
    set, line-conditioned   the same along the line's baseline, at the line's lean and
                            x-height (cond=2)

Faithfulness (the neatened line against the line as written; x-heights are the line's as
written): `move` the RMS distance each point moved, `shape` the RMS distance between each letter
and its ink as written with both centred (only its shape), `angle` and `lean` how far the
baseline's direction and the downstrokes' lean turned (degrees), `size` how much the x-height
changed (|log|). Neatness (the neatened line against itself and the font): `font` each letter's
centred RMS distance to the nearest version of it in the letter model, taken off its line's
baseline and lean onto the font's and measured at its line's x-height; `base` how far the letters
on the baseline are from the line through them; `sizes` the spread of the x-height letters'
heights (|log|); `gaps` the spread of the gaps between neighbouring letters; `cer` the reading's
character error. Letters whose strokes are shared with another character count for the
line-wide numbers only. Each is the median over the lines (the letters, for `shape` and `font`).
`--tilt` adds a copy of each line written on a slope and leaning further (a y-shear of TILT and
an x-shear of TILT, the same ink otherwise), measured the same way.

Prints only aggregate numbers. `--dump FILE` writes the lines with every way's ink as JSON (the
person's ink: only for a place as private as the store). The store given is only read;
everything happens in a temporary copy.

    eval-setting.py BIN STORE_DIR --models DIR [--limit N] [--tilt T] [--dump FILE] [--tmp DIR]
"""
import argparse
import importlib.util
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("eval_neaten", Path(__file__).resolve().parent / "eval-neaten.py")
en = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(en)

WAYS = [("Light", "", 0.25), ("Medium", "", 0.5), ("Strong", "", 0.75), ("pull 1", "set=0", 1.0),
        ("set", "", 1.0), ("set, conditioned", "cond=1", 1.0),
        ("set, line-conditioned", "cond=2", 1.0)]
X_LETTERS = "acemnorsuvwxz"


def on_base(c):
    return (c.isascii() and c.islower() and c not in "fgjpqy") or (c.isascii() and c.isupper() and c != "Q") or c.isdigit()


def own_letters(text, spans):
    """per character its strokes when they are its own (no other character has them), else None"""
    cnt = {}
    for sp in spans:
        for j in sp:
            cnt[j] = cnt.get(j, 0) + 1
    return [sorted(spans[k]) if k < len(spans) and spans[k] and all(cnt[j] == 1 for j in spans[k]) and not text[k].isspace()
            else None for k in range(len(text))]


def fit_line(pts):
    """least squares y = a + b x; b 0 with fewer than two points or no spread"""
    if not pts:
        return None
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    b = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx if len(pts) >= 2 and sxx > 0 else 0
    return my - b * mx, b


def lean_of(strokes):
    n = d = 0.0
    for st in strokes:
        for i in range(1, len(st)):
            dx, dy = st[i][0] - st[i - 1][0], -(st[i][1] - st[i - 1][1])
            if dy < 0 and -dy > 2 * abs(dx):
                n += dx * dy
                d += dy * dy
    return n / d if d > 0 else 0.0


class Line:
    """a line's geometry: its baseline (bottoms of its own letters on it), lean, x-height"""

    def __init__(self, text, own, strokes):
        self.text, self.own, self.strokes = text, own, strokes
        bot, hs = [], []
        for k, c in enumerate(text):
            if not own[k]:
                continue
            pts = [p for j in own[k] for p in strokes[j]]
            ys = [p[1] for p in pts]
            if on_base(c):
                bot.append(((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2, max(ys)))
            if c in X_LETTERS:
                hs.append(max(ys) - min(ys))
        self.bottoms = bot
        self.base = fit_line(bot)
        self.xh = statistics.median(hs) if hs else 0
        self.heights = hs
        self.lean = lean_of(strokes)

    def angle(self):
        return math.degrees(math.atan(self.base[1])) if self.base else 0.0

    def flat(self, x, y):
        """a point taken off the baseline's slope (y-shear), in x-heights, y up from the line"""
        a, b = self.base or (0, 0)
        return x / self.xh, (a + b * x - y) / self.xh


def font_dist(line, k, letters, slant):
    """letter k's distance to its nearest version, off its line's slope and lean"""
    le = letters.get(line.text[k])
    if not le or not le.get("covered") or not line.own[k] or line.xh <= 0:
        return None
    sh = line.lean - slant
    ink = []
    for j in line.own[k]:
        st = []
        for x, y in line.strokes[j]:
            u, v = line.flat(x, y)
            st.append((u - sh * v, -v))
        ink.append(st)
    d = min((en.vdist(ink, 1.0, v) for v in le["variants"]), default=math.inf)
    return d if math.isfinite(d) else None


def gaps_spread(line):
    """the spread of the gaps between neighbouring own letters of a word, off the slope"""
    if line.xh <= 0:
        return None
    gs = []
    for k in range(len(line.text) - 1):
        if line.text[k].isspace() or line.text[k + 1].isspace() or not line.own[k] or not line.own[k + 1]:
            continue
        pr = []
        for kk in (k, k + 1):
            st = [[line.flat(x, y) for x, y in line.strokes[j]] for j in line.own[kk]]
            pr.append(en.profile([[(u, -v) for u, v in s] for s in st], 1.0, 0.0))
        gs.append(en.profile_gap(pr[0], pr[1]))
    return statistics.pstdev(gs) if len(gs) >= 3 else None


def tilt(strokes, t):
    """the ink on a slope (y-shear t) and leaning t further (x-shear), about its first point"""
    x0, y0 = strokes[0][0][0], strokes[0][0][1]
    out = []
    for st in strokes:
        o = []
        for p in st:
            x, y = p[0], p[1]
            nx = x - t * (y - y0)
            o.append([nx, y + t * (nx - x0)] + list(p[2:]))
        out.append(o)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("store")
    ap.add_argument("--models", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tilt", type=float, default=0)
    ap.add_argument("--dump")
    ap.add_argument("--tmp")
    a = ap.parse_args()
    store = Path(a.store)
    lines = [ln for ln in (store / "samples.jsonl").read_text(encoding="utf-8").split("\n") if ln.strip()]
    ml = (store / "samples.meta").read_text(encoding="utf-8").split("\n")
    first, meta = ml[0], {ln.split("\t")[0]: ln for ln in ml[1:] if len(ln.split("\t")) >= 5}
    ids = [ln.split("\t")[0] for ln in ml[1:] if ln]
    pairs = list(zip(ids, lines))
    held = []
    for i, l in pairs:
        if int(i, 16) % 5 == 0:
            try:
                held.append((i, json.loads(l)))
            except ValueError:
                pass            # (bytes that are no sample: the store's index skips them too)
    train = [(i, l) for i, l in pairs if int(i, 16) % 5 != 0]
    held = [(i, s) for i, s in held if s.get("text", "").strip() and s.get("spans") and len(s["spans"]) == len(s["text"])]
    if a.limit:
        held = held[:a.limit]
    cases = [("as written", s) for _, s in held]
    if a.tilt:
        cases += [("tilted", dict(s, strokes=tilt(s["strokes"], a.tilt))) for _, s in held]
    dump = []
    with tempfile.TemporaryDirectory(dir=a.tmp) as td:
        os.chmod(td, 0o700)
        hw = Path(td) / "data" / "glassine" / "handwriting"
        hw.mkdir(parents=True)
        (hw / "samples.jsonl").write_text("".join(l + "\n" for _, l in train), encoding="utf-8")
        (hw / "samples.meta").write_text(first + "\n" + "".join(meta[i] + "\n" for i, _ in train), encoding="utf-8")
        base_env = dict(os.environ, HOME=td, XDG_DATA_HOME=str(Path(td) / "data"), XDG_CONFIG_HOME=str(Path(td) / "config"),
                        GLASSINE_HANDWRITING_MODELS=a.models)
        built = subprocess.run([a.bin, "letters", "build", "-f"], env=base_env, capture_output=True, text=True)
        print("letter model (train part):", built.stdout.strip() or built.stderr.strip())
        model = [json.loads(ln) for ln in (hw / "letters" / "model").read_text().splitlines()]
        letters = {m["char"]: m for m in model if "char" in m}
        slant = next((m["metrics"]["slant"] for m in model if "metrics" in m), 0.0)
        recs = {}
        for _, knob, _ in WAYS:
            if knob not in recs:
                recs[knob] = en.Rec(a.bin, dict(base_env, GLASSINE_HANDWRITING_NEATEN=knob))
        res = {(c, w[0]): {k: [] for k in ("move", "shape", "angle", "lean", "size", "font", "base", "sizes", "gaps")}
               for c in ("as written", "tilted") for w in WAYS}
        cer = {(c, w[0]): [0, 0] for c in ("as written", "tilted") for w in WAYS}
        setn = {(c, w[0]): [0, 0] for c in ("as written", "tilted") for w in WAYS}
        orig_font = {"as written": [], "tilted": []}
        for case, s in cases:
            text, spans = s["text"], s["spans"]
            own = own_letters(text, spans)
            strokes = [en.points(st) for st in s["strokes"]]
            o_xy = [[(p[0], p[1]) for p in st] for st in s["strokes"]]
            L0 = Line(text, own, o_xy)
            if L0.xh <= 0 or not L0.base:
                continue
            align = ";".join(",".join(str(j) for j in sp) for sp in spans)
            for k in range(len(text)):
                d = font_dist(L0, k, letters, slant)
                if d is not None:
                    orig_font[case].append(d)
            entry = {"case": case, "text": text, "ink": o_xy, "ways": {}}
            for name, knob, st in WAYS:
                rec = recs[knob]
                en.session(rec, s, strokes)
                r = rec.ask("neaten", "s", st, en.esc(text), align)
                _, how, out = en.parse_neaten(r)
                why = en.WHY[-1]
                n_xy = [en.to_xy(p) for p in out]
                en.session(rec, s, out)
                rd = rec.ask("read", "s", "1", "raw")
                rn = en.unesc(rd[4]) if len(rd) > 4 and rd[2] == "text" and rd[3] != "0" else ""
                rec.ask("end", "s")
                key = (case, name)
                cer[key][0] += en.edit(rn, text)
                cer[key][1] += len(text)
                setn[key][0] += sum(1 for w in why if w == "set")
                setn[key][1] += sum(1 for c in text if not c.isspace())
                L1 = Line(text, own, n_xy)
                q = res[key]
                d2 = [(x1 - x0) ** 2 + (y1 - y0) ** 2 for s0, s1 in zip(o_xy, n_xy) for (x0, y0), (x1, y1) in zip(s0, s1)]
                q["move"].append(math.sqrt(sum(d2) / len(d2)) / L0.xh)
                for k in range(len(text)):
                    if not own[k]:
                        continue
                    p0 = [p for j in own[k] for p in o_xy[j]]
                    p1 = [p for j in own[k] for p in n_xy[j]]
                    c0 = (sum(p[0] for p in p0) / len(p0), sum(p[1] for p in p0) / len(p0))
                    c1 = (sum(p[0] for p in p1) / len(p1), sum(p[1] for p in p1) / len(p1))
                    q["shape"].append(math.sqrt(sum(((u[0] - c1[0]) - (v[0] - c0[0])) ** 2 + ((u[1] - c1[1]) - (v[1] - c0[1])) ** 2
                                                    for u, v in zip(p1, p0)) / len(p0)) / L0.xh)
                    if L1.xh > 0 and L1.base:
                        d = font_dist(L1, k, letters, slant)
                        if d is not None:
                            q["font"].append(d)
                q["angle"].append(abs(L1.angle() - L0.angle()))
                q["lean"].append(abs(math.degrees(math.atan(L1.lean) - math.atan(L0.lean))))
                if L1.xh > 0:
                    q["size"].append(abs(math.log(L1.xh / L0.xh)))
                    if L1.base and len(L1.bottoms) >= 3:
                        a0, b0 = L1.base
                        q["base"].append(math.sqrt(sum((y - a0 - b0 * x) ** 2 for x, y in L1.bottoms) / len(L1.bottoms)) / L1.xh)
                    if len(L1.heights) >= 3:
                        q["sizes"].append(statistics.pstdev([math.log(h / L1.xh) for h in L1.heights if h > 0]))
                    g = gaps_spread(L1)
                    if g is not None:
                        q["gaps"].append(g)
                entry["ways"][name] = {"ink": n_xy, "set": sum(1 for w in why if w == "set")}
            dump.append(entry)
        for r in recs.values():
            r.close()
    med = lambda v: statistics.median(v) if v else float("nan")
    print(f"held-back lines with their text and spans: {len(held)}" + (f", and as many tilted ({a.tilt})" if a.tilt else ""))
    for case in ("as written", "tilted") if a.tilt else ("as written",):
        print(f"\n{case}: (as written the letters are {med(orig_font[case]):.3f} x-heights from the font)")
        print(f"{'':18s} {'faithful:':>9s} {'move':>6s} {'shape':>6s} {'angle':>6s} {'lean':>6s} {'size':>6s}   "
              f"{'neat:':>5s} {'font':>6s} {'base':>6s} {'sizes':>6s} {'gaps':>6s} {'cer':>6s} {'set':>6s}")
        for name, _, _ in WAYS:
            q, key = res[(case, name)], (case, name)
            print(f"{name:18s} {'':9s} {med(q['move']):6.3f} {med(q['shape']):6.3f} {med(q['angle']):6.2f} {med(q['lean']):6.2f} "
                  f"{med(q['size']):6.3f}   {'':5s} {med(q['font']):6.3f} {med(q['base']):6.3f} {med(q['sizes']):6.3f} "
                  f"{med(q['gaps']):6.3f} {100 * cer[key][0] / max(1, cer[key][1]):5.1f}% {100 * setn[key][0] / max(1, setn[key][1]):5.1f}%")
    if a.dump:
        fd = os.open(a.dump, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"slant": slant, "lines": dump}, f)


if __name__ == "__main__":
    sys.exit(main())
