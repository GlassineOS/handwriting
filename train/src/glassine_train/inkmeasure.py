"""Measures of ink for the model-4 generator, the same for real and generated samples:
delayed strokes and letter spacing (spec 001 FR-020). Only aggregate numbers come out.

Samples are given in x-heights (y down, as the pen reports) and seconds:
`to_xh(samples)` converts sample-format samples by their writer's x-height (stats.measure).

**Which character a stroke is.** Spans follow the order of writing, so a dot written after
the word sits in the last letter's span. As the letter model does (docs/recognizer.md, the
letter model), a short stroke (a dot at most 0.35 x-heights across, or a flat bar) that
does not lie over its span's character's other ink goes to the
character it lies over: the one whose other strokes' x-range holds its middle, an i or j
for a dot, a t, f or other barred letter for a bar, nearest first.

**Delayed strokes.** A stroke is delayed when a later character of its word (by text) has
a stroke written before it. A delayed stroke is mid-word when the word's later characters
are not all written before it; delayed strokes of one word are right to left when each is
of an earlier character than the one before it.
"""

import numpy as np

from glassine_synth.stats import measure

DOT_CHARS = set("ij")
BAR_CHARS = set("tfAEFHT")  # letters with a bar people may add after the word


def to_xh(samples: list[dict]) -> tuple[list[tuple[list[np.ndarray], list[list[int]], str]], float]:
    """[(strokes in x-heights and seconds, spans, text)] and the writer's x-height in the
    samples' units."""
    xh = measure(samples)["writer_x_height_units"]
    out = []
    for s in samples:
        st = [np.asarray(q, float)[:, :3] for q in s["strokes"]]
        st = [np.c_[q[:, :2] / xh, q[:, 2]] for q in st]
        keep = [i for i, q in enumerate(st) if len(q)]
        remap = {o: n for n, o in enumerate(keep)}
        spans = [[remap[j] for j in sp if j in remap] for sp in s["spans"]]
        out.append(([st[i] for i in keep], spans, s["text"]))
    return out, xh


def _kind(q: np.ndarray) -> str | None:
    w, h = np.ptp(q[:, 0]), np.ptp(q[:, 1])
    path = np.linalg.norm(np.diff(q[:, :2], axis=0), axis=1).sum() if len(q) > 1 else 0.0
    if max(w, h) < 0.35 and path < 0.8:
        return "dot"
    if w > 2.0 * max(h, 1e-6) and w > 0.3 and path < 3.0:
        return "bar"
    return None


def owners(strokes: list[np.ndarray], spans: list[list[int]], text: str) -> list[int]:
    """The character of each stroke: by its span, short strokes by where they lie."""
    own = [-1] * len(strokes)
    for ci, sp in enumerate(spans):
        for j in sp:
            if 0 <= j < len(own) and own[j] < 0:
                own[j] = ci
    kinds = [_kind(q) for q in strokes]
    ext = {}
    for j, ci in enumerate(own):
        if ci >= 0 and kinds[j] is None:
            lo, hi = strokes[j][:, 0].min(), strokes[j][:, 0].max()
            a, b = ext.get(ci, (lo, hi))
            ext[ci] = (min(a, lo), max(b, hi))
    for j, k in enumerate(kinds):
        if k is None:
            continue
        want = DOT_CHARS if k == "dot" else BAR_CHARS
        cx = float(strokes[j][:, 0].mean())
        o = own[j]
        if o >= 0 and o in ext and ext[o][0] - 0.1 <= cx <= ext[o][1] + 0.1:
            continue  # it lies over its span's character: that character's
        best, bd = own[j], np.inf
        for ci, (lo, hi) in ext.items():
            if text[ci] not in want:
                continue
            d = 0.0 if lo - 0.3 <= cx <= hi + 0.3 else min(abs(cx - lo), abs(cx - hi))
            if d < bd and d < 0.8:
                best, bd = ci, d
        own[j] = best
    return own


def word_of(text: str) -> list[int]:
    w, out = 0, []
    for c in text:
        if c == " ":
            w += 1
        out.append(w)
    return out


def delays(samples_xh) -> tuple[dict, list[list[bool]]]:
    """Delayed-stroke measures and, per sample, per stroke, whether it is delayed."""
    cnt = {"dot": [0, 0], "bar": [0, 0]}  # [delayed, all]
    words = [0, 0]
    mid = [0, 0]
    rtl = [0, 0]
    flags = []
    for strokes, spans, text in samples_xh:
        own = owners(strokes, spans, text)
        wd = word_of(text)
        fl = [False] * len(strokes)
        per_word = {}
        for j, ci in enumerate(own):
            if ci < 0:
                continue
            later = [k for k in range(j) if own[k] > ci and wd[own[k]] == wd[ci]]
            fl[j] = bool(later)
            k = _kind(strokes[j])
            c = text[ci]
            if k == "dot" and c in DOT_CHARS or k == "bar" and c in BAR_CHARS:
                cnt[k][0] += fl[j]
                cnt[k][1] += 1
            if fl[j]:
                per_word.setdefault(wd[ci], []).append((j, ci))
                rest = [k2 for k2 in range(j + 1, len(strokes)) if own[k2] > ci and wd[own[k2]] == wd[ci]
                        and not any(own[k3] > own[k2] for k3 in range(k2))]
                mid[0] += bool(rest)
                mid[1] += 1
        nwords = len({w for w, c in zip(wd, text) if c != " "})
        words[0] += len(per_word)
        words[1] += nwords
        for ds in per_word.values():
            if len(ds) >= 2:
                rtl[1] += len(ds) - 1
                rtl[0] += sum(b[1] < a[1] for a, b in zip(ds, ds[1:]))
        flags.append(fl)
    sh = lambda a: round(a[0] / a[1], 3) if a[1] else None
    rep = {"dot_delayed": sh(cnt["dot"]), "dots": cnt["dot"][1], "bar_delayed": sh(cnt["bar"]),
           "bars": cnt["bar"][1], "words_with_delayed": sh(words), "words": words[1],
           "mid_word": sh(mid), "delayed_strokes": mid[1], "right_to_left": sh(rtl), "delayed_pairs": rtl[1]}
    return rep, flags


def pauses(samples_xh, flags) -> dict:
    """Pen-up pause before delayed strokes and between characters (seconds, medians)."""
    dl, btw = [], []
    for (strokes, spans, text), fl in zip(samples_xh, flags):
        own = owners(strokes, spans, text)
        for j in range(1, len(strokes)):
            p = strokes[j][0, 2] - strokes[j - 1][-1, 2]
            if p <= 0:
                continue
            if fl[j]:
                dl.append(p)
            elif own[j] != own[j - 1]:
                btw.append(p)
    q = lambda v: round(float(np.median(v)), 3) if v else None
    return {"before_delayed_s": q(dl), "between_chars_s": q(btw), "n_delayed": len(dl), "n_between": len(btw)}


# ---------------------------------------------------------------------------- spacing

def letter_gaps(samples_xh, baseline_of=None) -> dict:
    """Gaps between neighbouring letters of a word by their ink profiles (spacing.py), in
    x-heights, from characters with strokes of their own; and word gaps (box gaps)."""
    from .spacing import Profile, gap, guard_gap
    gaps, wgaps, xb = [], [], []
    for strokes, spans, text in samples_xh:
        own = owners(strokes, spans, text)
        cnt = {}
        for j, ci in enumerate(own):
            cnt.setdefault(ci, []).append(j)
        base = baseline_of(strokes, own, text) if baseline_of else _baseline(strokes, own, text)
        if base is None:
            continue
        prof = {}
        for ci, js in cnt.items():
            if ci < 0:
                continue
            p = Profile()
            for j in js:
                q = strokes[j]
                p.add(q[:, 0], base - q[:, 1])
            prof[ci] = p
        wd = word_of(text)
        for ci in range(len(text) - 1):
            if text[ci] != " " and text[ci + 1] != " " and ci in prof and ci + 1 in prof:
                g = gap(prof[ci], prof[ci + 1])
                if -0.6 <= g <= 1.6:
                    gaps.append(g)
                    h = guard_gap(prof[ci], prof[ci + 1])
                    if np.isfinite(h) and -0.6 <= h <= 1.6:
                        xb.append(h)
        boxes = {}
        for ci, p in prof.items():
            lo, hi = p.box()
            w = wd[ci]
            a, b = boxes.get(w, (lo, hi))
            boxes[w] = (min(a, lo), max(b, hi))
        ws = sorted(boxes)
        for a, b in zip(ws, ws[1:]):
            wgaps.append(boxes[b][0] - boxes[a][1])
    g = np.array(gaps)
    return {"letter_gap": _qs(g), "letter_gap_sd": round(float(g.std()), 3) if len(g) else None,
            "touching_or_overlapping": round(float(np.mean(g <= 0)), 3) if len(g) else None,
            "xband_gap": _qs(np.array(xb)), "xband_gap_sd": round(float(np.std(xb)), 3) if xb else None,
            "xband_touching": round(float(np.mean(np.array(xb) <= 0)), 3) if xb else None,
            "word_gap": _qs(np.array(wgaps))}


XLET = set("acemnorsuvwxz")


def _baseline(strokes, own, text):
    bs = []
    for ci, c in enumerate(text):
        if c in XLET:
            js = [j for j, o in enumerate(own) if o == ci]
            if js:
                bs.append(max(strokes[j][:, 1].max() for j in js))
    return float(np.median(bs)) if bs else None


def _qs(v):
    v = np.asarray(v, float)
    if not len(v):
        return {"n": 0}
    return {f"p{p}": round(float(np.percentile(v, p)), 3) for p in (10, 25, 50, 75, 90)} | {"n": int(len(v))}


def alignment(samples_xh) -> dict:
    """How steadily letters sit on the line: per word, the bottoms and tops of the x-height
    letters (XLET) off a straight line through them (residual sd, x-heights), and the
    gaps after a capital (capital, small letter) by ink profiles."""
    from .spacing import Profile, gap
    bot, top, cap = [], [], []
    for strokes, spans, text in samples_xh:
        own = owners(strokes, spans, text)
        wd = word_of(text)
        per = {}
        for ci, c in enumerate(text):
            js = [j for j, o in enumerate(own) if o == ci]
            if not js:
                continue
            xs = np.concatenate([strokes[j][:, 0] for j in js])
            ys = np.concatenate([strokes[j][:, 1] for j in js])
            per[ci] = (xs, ys)
            if c in XLET:
                pass
        words = {}
        for ci, (xs, ys) in per.items():
            if text[ci] in XLET:
                words.setdefault(wd[ci], []).append((xs.mean(), ys.max(), ys.min()))
        for v in words.values():
            if len(v) >= 3:
                a = np.array(v)
                for k, out in ((1, bot), (2, top)):
                    c = np.polyfit(a[:, 0], a[:, k], 1)
                    out += (a[:, k] - np.polyval(c, a[:, 0])).tolist()
        base = _baseline(strokes, own, text)
        if base is None:
            continue
        for ci in range(len(text) - 1):
            a, b = text[ci], text[ci + 1]
            if a.isupper() and b.islower() and ci in per and ci + 1 in per:
                pa, pb = Profile(), Profile()
                for j in [j for j, o in enumerate(own) if o == ci]:
                    pa.add(strokes[j][:, 0], base - strokes[j][:, 1])
                for j in [j for j, o in enumerate(own) if o == ci + 1]:
                    pb.add(strokes[j][:, 0], base - strokes[j][:, 1])
                g = gap(pa, pb)
                if -0.6 <= g <= 1.6:
                    cap.append(g)
    sd = lambda v: round(float(np.std(v)), 3) if len(v) else None
    return {"bottom_sd": sd(bot), "top_sd": sd(top), "bottom": _qs(np.abs(bot)), "n": len(bot),
            "capital_gap": _qs(np.array(cap))}


# ---------------------------------------------------------------- the recognizer's letter height

SMALL = set("acemnorsuvwxz")  # the recognizer's small letters (recognizer/src/hw/size.c)


def small_height(strokes: list[np.ndarray], spans: list[list[int]], text: str) -> float:
    """A line's small-letter height measured as the recognizer measures a person's samples
    (size.c, measure): for three or more characters with a span each, the median height of
    the small letters drawn with strokes of their own (a stroke two characters share says
    nothing about either); a lone small letter's own height. 0 when there is none."""
    n = len(text)
    if n == 1 and text in SMALL and strokes:
        pts = np.concatenate([q for q in strokes if len(q)])
        return float(np.ptp(pts[:, 1])) if len(pts) else 0.0
    if n < 3 or len(spans) != n:
        return 0.0
    uses = np.zeros(len(strokes) + 1, int)
    for sp in spans:
        for j in sp:
            if 0 <= j < len(strokes):
                uses[j] += 1
    hs = []
    for c, sp in zip(text, spans):
        if c not in SMALL or not sp or any(not (0 <= j < len(strokes)) or uses[j] != 1 for j in sp):
            continue
        pts = np.concatenate([strokes[j] for j in sp if len(strokes[j])]) if any(len(strokes[j]) for j in sp) else None
        if pts is None:
            continue
        h = float(np.ptp(pts[:, 1]))
        if h > 0:
            hs.append(h)
    return float(np.median(hs)) if hs else 0.0


def writer_ratios(stores, per_writer: int = 400, seed: int = 0) -> dict:
    """{writer: the median of small_height / xh over up to per_writer of its lines of three or
    more characters in the ink sets `stores` (one or a list)}, and "*": the median over all
    writers (for a writer without enough measurable lines). As with a person, the reference
    is the median over many lines, not each line's own. ValueError when no writer has five."""
    if not isinstance(stores, (list, tuple)):
        stores = [stores]
    rng = np.random.default_rng(seed)
    by = {}
    for si, store in enumerate(stores):
        for i in rng.permutation(len(store)):
            row = store.rows[i]
            if len(row["text"]) >= 3 and len(by.setdefault(row.get("writer"), [])) < per_writer:
                by[row.get("writer")].append((si, int(i)))
    out = {}
    for w, idx in by.items():
        v = []
        for si, i in idx:
            strokes, row = stores[si][i]
            h = small_height(strokes, row["spans"], row["text"])
            if h > 0:
                v.append(h / row["xh"])
        if len(v) >= 5:
            out[w] = float(np.median(v))
    if not out:
        raise ValueError("no writer with five lines whose small letters can be measured")
    out["*"] = float(np.median(list(out.values())))
    return out
