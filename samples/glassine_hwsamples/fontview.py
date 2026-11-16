# SPDX-License-Identifier: GPL-2.0-or-later
"""The handwriting font before it is installed (spec 003 Scenario 3, FR-010): text laid out
from the letter model (`glassine-handwriting letters show`, docs/recognizer.md) with the
variants and the spacing the font has, so the preview shows what the installed font shows.
Toolkit-free; pages.py draws the result."""

import json

TARGET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MIN = 3                                  # usable samples for a character to be in the font
DEFAULT_NAME = "My handwriting"
PREVIEW = "The quick brown fox jumps over the lazy dog."


def parse(text):
    """the model file's lines -> {"metrics": {...}, "chars": {c: {"samples", "covered",
    "variants"}}}; None when it isn't a letter model"""
    out = {"metrics": {}, "chars": {}}
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    head = json.loads(lines[0])
    if head.get("format") != "glassine-handwriting-letters" or head.get("version") != 1:
        return None
    out["head"] = head
    for ln in lines[1:]:
        d = json.loads(ln)
        if "metrics" in d:
            out["metrics"] = d["metrics"]
        elif "spacing" in d:
            out["spacing"] = d["spacing"]
        elif "char" in d:
            out["chars"][d["char"]] = d
    return out


SLOTS = 10      # letters.h HW_LETTERS_SLOTS


def step(prev):
    """the step of the font's cycle at a character or space, as its contextual alternates
    give it (letters.h hw_letters_step): prev the step before in the run, None at its start
    or after a character the font lacks"""
    return 0 if prev is None else (prev + 1) % SLOTS


def slots(d):
    """the variant a character shows at each step (the model's cycle, each variant about as
    often as it is used; a model without one: in turn)"""
    n = len(d["variants"])
    s = d.get("slots")
    if isinstance(s, list) and len(s) == SLOTS and all(isinstance(v, int) and 0 <= v < n for v in s):
        return s
    return [k % n for k in range(SLOTS)]


def variants(model, text):
    """per character of text: the variant the font shows, None for one it lacks"""
    out, prev = [], None
    for c in text:
        if c == " ":
            out.append(0)
            prev = step(prev)
            continue
        d = model["chars"].get(c) if model else None
        if not d or not d.get("covered") or not d.get("variants"):
            out.append(None)
            prev = None
            continue
        prev = step(prev)
        out.append(slots(d)[prev])
    return out


XH = 500        # ttf.c: font units per x-height; the preview rounds as the font does
BANDS = 8       # letters.h HW_PROF_BANDS


def _nan(v):
    return float("nan") if v is None else float(v)


def _prof(var):
    return [_nan(v) for v in var.get("left") or [None] * BANDS], [_nan(v) for v in var.get("right") or [None] * BANDS]


def _ok(v):
    return v == v


def profile_gap(a, b, shift):
    """spacing.c hw_profile_gap: the narrowest distance between a's right side and b's left
    side, b moved right by shift; their boxes when they share no height band"""
    ds = [b[0][k] + shift - a[1][k] for k in range(BANDS) if _ok(a[1][k]) and _ok(b[0][k])]
    if ds:
        return min(ds)
    ar = [x for x in a[1] if _ok(x)]
    bl = [x for x in b[0] if _ok(x)]
    return min(bl) + shift - max(ar) if ar and bl else 0.0


def profile_open(a, b):
    """spacing.c hw_profile_open"""
    g = profile_gap(a, b, 0)
    ds = [min(b[0][k] - a[1][k] - g, 1.0) for k in range(BANDS) if _ok(a[1][k]) and _ok(b[0][k])]
    return sum(ds) / len(ds) if len(ds) >= 2 else 0.0


def pair_gap(sp, a, va, b, vb):
    """spacing.c hw_spacing_gap: the pair's own gap, its side shapes', or from the profiles"""
    for p in sp.get("pairs", []):
        if p[0] == a and p[1] == b:
            return p[2]
    rc, lc = va.get("rc", -1), vb.get("lc", -1)
    if rc >= 0 and lc >= 0:
        for q in sp.get("sides", []):
            if q[0] == rc and q[1] == lc:
                return q[2]
    letter = sp.get("letter", 0.15)
    g = letter
    if sp.get("open", 0) > 0:
        g -= sp["open"] * (profile_open(_prof(va), _prof(vb)) - sp.get("open0", 0))
    return min(max(g, letter - 0.3), letter + 0.3)


MID_Y0, MID_Y1 = 0.0, 1.0       # letters.h HW_MID_Y0, HW_MID_Y1
PROF_Y0, PROF_Y1 = -0.6, 1.6    # letters.h HW_PROF_Y0, HW_PROF_Y1


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float("nan")


def place(sp, a, va, b, vb):
    """spacing.c hw_spacing_place: where b's left edge goes, right of a's: the letters'
    middles at the pair's distance (or the two characters' reaches), guarded by the gap
    between the baseline and the x-height; at the gap without a distance"""
    pa, pb = _prof(va), _prof(vb)
    gap = pair_gap(sp, a, va, b, vb)
    s = gap - profile_gap(pa, pb, 0)
    ma, mb = _num(va.get("middle")), _num(vb.get("middle"))
    if not (_ok(ma) and _ok(mb)):
        return s
    d = float("nan")
    for p in sp.get("pairs", []):
        if p[0] == a and p[1] == b:
            d = _num(p[4]) if len(p) > 4 else d
            break
    if not _ok(d):
        reach = {r[0]: r for r in sp.get("reach", [])}
        if a in reach and b in reach:
            d = _num(reach[a][1]) + _num(reach[b][2])
    if not _ok(d):
        return s
    at = d + ma - mb
    floor = gap if gap < sp.get("letter", 0.15) / 2 else sp.get("letter", 0.15) / 2
    band = (PROF_Y1 - PROF_Y0) / BANDS
    g = float("inf")
    for k in range(BANDS):
        y0 = PROF_Y0 + k * band
        y1 = y0 + band
        if y1 <= MID_Y0 or y0 >= MID_Y1 or not _ok(pa[1][k]) or not _ok(pb[0][k]):
            continue
        g = min(g, pb[0][k] + at - pa[1][k])
    return at + floor - g if g < floor else at


def _kern(sp, a, va, b, vb, gap):
    """ttf.c gpos: the kerning between two variants, in font units"""
    k = _round((place(sp, a, va, b, vb) - (va["width"] + gap)) * XH)
    return max(-3 * XH, min(3 * XH, k))


def _round(x):
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def font_view(model):
    """the model as the font has it (ttf.c, hw_letters_view): variants the person left out
    taken out (slots renumbered), a character with none left not covered"""
    if model is None or model.get("_font"):
        return model
    v = model.get("_view")
    if v is not None:
        return v
    chars = {}
    for c, d in model["chars"].items():
        vs = d.get("variants") or []
        if not any(x.get("left_out") for x in vs):
            chars[c] = d
            continue
        keep = [i for i, x in enumerate(vs) if not x.get("left_out")]
        new = {i: k for k, i in enumerate(keep)}
        sl = [new.get(i, 0) for i in slots(d)] if vs else None
        e = dict(d, variants=[vs[i] for i in keep], slots=sl)
        if not keep:
            e["covered"] = False
        chars[c] = e
    v = dict(model, chars=chars, _font=True)
    model["_view"] = v
    return v


def in_ten(d, v):
    """how many of the font's 10 steps show variant v of a character"""
    return slots(d).count(v) if d.get("variants") else 0


def strokes_desc(var):
    """how a variant is written, in plain words"""
    n = len(var.get("strokes") or [])
    return "In one go" if n == 1 else "In %d strokes" % n


def layout(model, text):
    """[(char, strokes or None, x, advance)] in x-heights, x from the left of the text, y up
    from the baseline; strokes None: the system font draws the character. Spaced as the font
    spaces it (ttf.c): half the typical gap each side, each pair of neighbouring letters
    kerned to where the spacing places it, the space the word gap less the two sides beside it."""
    model = font_view(model)
    m = model["metrics"]
    sp = model.get("spacing") or {"letter": m.get("letter_spacing", 0.15), "word": m.get("word_spacing", 0.7)}
    gap, word = sp.get("letter", 0.15), sp.get("word", 0.7)
    out, x, prev = [], 0, None
    for c, v in zip(text, variants(model, text)):
        if c == " ":
            adv = max(_round((word - gap) * XH), XH // 10)
            out.append((c, [], x / XH, adv / XH))
            prev = None
        elif v is None:
            adv = round(0.7 * XH)
            out.append((c, None, x / XH, adv / XH))
            prev = None
        else:
            var = model["chars"][c]["variants"][v]
            if prev is not None:
                k = _kern(sp, prev[0], prev[1], c, var, gap)
                x += k
                pc, ps, px, pa = out[-1]
                out[-1] = (pc, ps, px, pa + k / XH)
            ox = gap / 2
            strokes = [[(ox + p[0], p[1]) for p in s] for s in var["strokes"]]
            adv = _round((var["width"] + gap) * XH)
            out.append((c, strokes, x / XH, adv / XH))
            prev = (c, var)
        x += adv
    return out


def coverage(model, chars=TARGET, status=None):
    """[(char, usable samples, in the font)] for the characters the page shows: the usable
    samples as the samples hold them now (letters status, spec 003 FR-030; the model's own
    count without a status), in the font as the model was built"""
    counts = (status or {}).get("chars")
    out = []
    for c in chars:
        d = font_view(model)["chars"].get(c, {}) if model else {}
        n = counts.get(c, (0, 0))[0] if counts is not None else int(d.get("samples", 0))
        out.append((c, n, bool(d.get("covered") and d.get("variants"))))
    return out


def missing(model, chars=TARGET, status=None):
    """the characters that still need writing: fewer than MIN usable samples"""
    return [c for c, n, _ in coverage(model, chars, status) if n < MIN]


def pending(model, chars=TARGET, status=None):
    """the characters written often enough that the next build puts them in"""
    return [c for c, n, ok in coverage(model, chars, status) if n >= MIN and not ok]
