# SPDX-License-Identifier: GPL-2.0-or-later
"""Autograph before it is installed (spec 003 Scenario 3, FR-009, FR-010): text laid out
from the model as the font draws it (`glassine-handwriting font show`: the letter model over
the base, docs/recognizer.md; `letters show` works too, without the base) with the variants,
the neighbour rules (FR-038), the joined pairs (FR-039), the base's characters and the spacing
the font has, so the preview shows what the installed font shows.
Toolkit-free; pages.py draws the result."""

import json
import math
import sys

TARGET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MIN = 3                                  # usable samples for a character to be in the font
NAME = "Autograph"                       # the installed font's name (FR-011)
PREVIEW = "The quick brown fox jumps over the lazy dog."


def _chars(s, n):
    """s is n real characters (no replacement character from bytes that weren't UTF-8)"""
    return (isinstance(s, str) and len(s) == n
            and all(c != "\ufffd" and not 0xD800 <= ord(c) <= 0xDFFF for c in s))


def _skip(at, field):
    print("glassine-handwriting-samples: letter model line %d: %s isn't valid, left out" % (at, field),
          file=sys.stderr, flush=True)


def parse(text):
    """the model file's lines -> {"metrics": {...}, "chars": {c: {"samples", "covered",
    "variants"}}, "base": {c: variant}}; None when it isn't a letter model. An entry that isn't valid (a line that
    isn't JSON, a character that isn't one) is left out with a warning"""
    out = {"metrics": {}, "chars": {}}
    lines = [(i + 1, ln) for i, ln in enumerate(text.splitlines()) if ln.strip()]
    if not lines:
        return None
    try:
        head = json.loads(lines[0][1])
    except ValueError:
        return None
    if not isinstance(head, dict) or head.get("format") != "glassine-handwriting-letters" or head.get("version") != 1:
        return None
    out["head"] = head
    for at, ln in lines[1:]:
        try:
            d = json.loads(ln)
        except ValueError:
            _skip(at, "the line")
            continue
        if not isinstance(d, dict):
            _skip(at, "the line")
        elif "metrics" in d:
            out["metrics"] = d["metrics"]
        elif "spacing" in d:
            sp = d["spacing"] if isinstance(d["spacing"], dict) else {}
            for key, n in (("pairs", 2), ("reach", 1)):
                good = [e for e in sp.get(key) or [] if isinstance(e, list) and len(e) >= n
                        and all(_chars(c, 1) for c in e[:n])]
                if len(good) != len(sp.get(key) or []):
                    _skip(at, "a character in " + key)
                if key in sp:
                    sp[key] = good
            out["spacing"] = sp
        elif "char" in d:
            if not _chars(d["char"], 1):
                _skip(at, "its character")
                continue
            rules = d.get("rules") or []
            good = [r for r in rules if isinstance(r, dict) and ("prev" in r or "next" in r)
                    and all(_chars(r[x], 1) for x in ("prev", "next") if x in r)]
            if len(good) != len(rules):
                _skip(at, "a rule's neighbour")
                d["rules"] = good
            out["chars"][d["char"]] = d
        elif "base" in d:
            # a character the font draws from the base (font show)
            if not _chars(d["base"], 1) or not isinstance(d.get("variant"), dict):
                _skip(at, "its base character")
                continue
            out.setdefault("base", {})[d["base"]] = d["variant"]
        elif "joined" in d:
            if not _chars(d["joined"], 2):
                _skip(at, "its pair")
                continue
            out.setdefault("joined", {})[d["joined"]] = d
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


def _in_font(model, c):
    d = model["chars"].get(c) if model else None
    return bool(d and d.get("covered") and d.get("variants"))


def joined_pairs(model):
    """the joined pairs the font draws as one glyph (FR-039): both letters in it"""
    return {k: d for k, d in ((model or {}).get("joined") or {}).items()
            if len(k) == 2 and isinstance(d.get("variant"), dict) and d["variant"].get("strokes")
            and _in_font(model, k[0]) and _in_font(model, k[1])}


def _rule_variant(model, d, out, k):
    """a letter's neighbour rule that applies at glyph k (FR-038): the first of its rules
    whose neighbour's glyph is beside it (a joined pair counts by its letter on that side),
    its variant; None for none"""
    n = len(d["variants"])

    def beside(side, c):
        j = k - 1 if side == "prev" else k + 1
        if not 0 <= j < len(out):
            return False
        kind, t = out[j][0], out[j][1]
        return (kind == "letter" and t == c) or (kind == "joined" and t[-1 if side == "prev" else 0] == c)

    for r in d.get("rules") or []:
        v = r.get("variant")
        if not isinstance(v, int) or not 0 <= v < n:
            continue
        # a context rule (both neighbours) or a neighbour rule (one)
        sides = [x for x in ("prev", "next") if x in r]
        if not sides or not all(_in_font(model, r[x]) for x in sides):
            continue
        if all(beside(x, r[x]) for x in sides):
            return v
    return None


def glyphs(model, text):
    """the glyphs the font shapes text into, as its lookups do (ttf.c gsub), in their order:
    every character's step of the cycle; then the joined pairs, left to right; then each
    letter's variant, by the first neighbour rule that applies, else its cycle's step.
    [(kind, text, variant index or None)]: kind "letter", "joined" (text its two letters),
    "space", "base" (a character drawn from the base: outside the cycle, the run starts
    again after it) or "missing" (a character the font lacks)"""
    model = font_view(model)
    items, prev = [], None
    for c in text:
        if c == " ":
            prev = step(prev)
            items.append(("space", c, prev))
        elif not _in_font(model, c):
            prev = None
            items.append(("base" if c in (model.get("base") or {}) else "missing", c, None))
        else:
            prev = step(prev)
            items.append(("letter", c, prev))
    pairs, out, i = joined_pairs(model), [], 0
    while i < len(items):
        if items[i][0] == "letter" and i + 1 < len(items) and items[i + 1][0] == "letter" and \
                items[i][1] + items[i + 1][1] in pairs:
            out.append(("joined", items[i][1] + items[i + 1][1], None))
            i += 2
        else:
            out.append(items[i])
            i += 1
    res = []
    for k, (kind, t, st) in enumerate(out):
        if kind == "letter":
            d = model["chars"][t]
            v = _rule_variant(model, d, out, k)
            res.append((kind, t, slots(d)[st] if v is None else v))
        elif kind == "space":
            res.append((kind, t, 0))
        else:
            res.append((kind, t, None))
    return res


def variants(model, text):
    """per character of text: the variant the font shows, None for one it lacks, -1 for a
    letter of a joined pair"""
    out = []
    for kind, t, v in glyphs(model, text):
        out += [-1, -1] if kind == "joined" else [v]
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


def _clear_near(pa, pb, at, floor):
    """spacing.c clear_near: b at at moved right until ink within a band of the other's
    height keeps floor away"""
    g = float("inf")
    for k in range(BANDS):
        for j in (k - 1, k, k + 1):
            if 0 <= j < BANDS and _ok(pa[1][k]) and _ok(pb[0][j]):
                g = min(g, pb[0][j] + at - pa[1][k])
    return at + floor - g if g < floor else at


def variant_middle(var):
    """spacing.c hw_variant_middle: its middle, or without one the mean x of all its ink by
    length"""
    m = _num(var.get("middle"))
    if _ok(m):
        return m
    sx = sw = 0.0
    for st in var.get("strokes") or []:
        for i in range(len(st)):
            i0 = i - 1 if i else 0
            w = math.hypot(st[i][0] - st[i0][0], st[i][1] - st[i0][1])
            if len(st) == 1:
                w = 1e-3
            sx += w * (st[i][0] + st[i0][0]) / 2
            sw += w
    return sx / sw if sw > 0 else float("nan")


def place(sp, a, va, b, vb):
    """spacing.c hw_spacing_place: where b's left edge goes, right of a's: the letters'
    middles at the pair's distance (or the two characters' reaches), guarded by the gap
    between the baseline and the x-height; at the gap without a distance, clear of ink near
    the other's height"""
    pa, pb = _prof(va), _prof(vb)
    gap = pair_gap(sp, a, va, b, vb)
    floor = gap if gap < sp.get("letter", 0.15) / 2 else sp.get("letter", 0.15) / 2
    s = _clear_near(pa, pb, gap - profile_gap(pa, pb, 0), floor)
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
        # a rule for a variant left out is gone
        rules = [dict(r, variant=new[r["variant"]]) for r in d.get("rules") or [] if r.get("variant") in new]
        e = dict(d, variants=[vs[i] for i in keep], slots=sl, rules=rules)
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
    """[(text, strokes or None, x, advance, variant or None)] per glyph (glyphs()), in x-heights,
    x from the left of the text, y up from the baseline; text one character, or a joined pair's
    two; strokes None: the system font draws the character (variant: the model's, for its
    widths in a style). Spaced as the font spaces it (ttf.c): half the typical gap each side,
    each pair of neighbouring glyphs kerned to where the spacing places it (a joined pair at
    its letters' gaps: its first letter's on the left, its last one's on the right), the space
    the word gap less the two sides beside it."""
    model = font_view(model)
    m = model["metrics"]
    sp = model.get("spacing") or {"letter": m.get("letter_spacing", 0.15), "word": m.get("word_spacing", 0.7)}
    gap, word = sp.get("letter", 0.15), sp.get("word", 0.7)
    pairs = joined_pairs(model)
    out, x, prev = [], 0, None
    for kind, c, v in glyphs(model, text):
        if kind == "space":
            adv = max(_round((word - gap) * XH), XH // 10)
            out.append((c, [], x / XH, adv / XH, None))
            prev = None
        elif kind == "missing":
            adv = round(0.7 * XH)
            out.append((c, None, x / XH, adv / XH, None))
            prev = None
        else:
            if kind == "joined":
                # its parts have no middles of their own: set at the gap on both sides
                var = dict(pairs[c]["variant"], middle=None)
            else:
                var = model["base"][c] if kind == "base" else model["chars"][c]["variants"][v]
                # (placed by all its ink's middle when it has none between the baseline and
                # the x-height, as ttf.c kent_var)
                var = dict(var, middle=variant_middle(var))
            if prev is not None:
                k = _kern(sp, prev[0], prev[1], c[0], var, gap)
                x += k
                pc, ps, px, pa, pv = out[-1]
                out[-1] = (pc, ps, px, pa + k / XH, pv)
            ox = gap / 2
            strokes = [[(ox + p[0], p[1]) for p in s] for s in var["strokes"]]
            adv = _round((var["width"] + gap) * XH)
            out.append((c, strokes, x / XH, adv / XH, var))
            prev = (c[-1], var)
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


# ---------------------------------------------------------------- the styles (FR-034)
# The rules of ttf.c (hw_font_widths and the outlines; docs/recognizer.md), so the preview
# draws a style as the font has it.

STYLES = (("regular", "Regular"), ("calligraphy", "Calligraphy"), ("brush", "Brush"))
NIB, NIB_MIN, NIB_MAX, NIB_STEP = 35, 10, 60, 5         # the nib angle's stepper, degrees
PROFILE = 16                    # letters.h HW_LETTERS_PROFILE


def nib_widths(model):
    """Calligraphy's thinnest and widest line (x-heights; ttf.c hw_font_nib_widths)"""
    ink = model["metrics"].get("ink_width", 0.07)
    return min(max(0.4 * ink, 0.015), 0.05), min(max(2.2 * ink, 0.12), 0.26)


def _prof_at(p, u):
    f = u * (PROFILE - 1)
    if f <= 0:
        return p[0]
    if f >= PROFILE - 1:
        return p[PROFILE - 1]
    i = int(f)
    return p[i] + (f - i) * (p[i + 1] - p[i])


def _profile(var, key, j):
    p = var.get(key)
    if isinstance(p, list) and len(p) == len(var.get("strokes") or []) and isinstance(p[j], list) and len(p[j]) == PROFILE:
        return [float(v) for v in p[j]]
    return None


def _brush(model, var, j):
    """ttf.c brush_widths"""
    st = var["strokes"][j]
    P, V = _profile(var, "pressure", j), _profile(var, "speed", j)
    ink, h = model["metrics"].get("ink_width", 0.07), 1.0 / (PROFILE - 1)
    ln = 0.0
    for a, b in zip(st, st[1:]):        # summed as ttf.c does (sum() would round otherwise)
        ln += math.hypot(b[0] - a[0], b[1] - a[1])
    vend = sum(V[PROFILE - 1 - q] for q in range(4)) / 4 if V else 1
    t0, t1 = min(0.22, 0.3 * ln), min(0.3, 0.4 * ln) * min(max(vend, 1), 2)
    out, at = [], 0.0
    for i, p in enumerate(st):
        if i:
            at += math.hypot(p[0] - st[i - 1][0], p[1] - st[i - 1][1])
        u, f = (at / ln if ln > 0 else 0), 1.3
        if P:
            f *= min(max(_prof_at(P, u), 0.25), 3) ** 0.8
        if V:
            f *= min(max(_prof_at(V, u), 0.25), 4) ** (-0.3 if P else -0.55)
            a, b = max(u - h, 0), min(u + h, 1)
            dv = (_prof_at(V, b) - _prof_at(V, a)) / ((b - a) * ln) if ln > 0 else 0
            f *= 1 - 0.12 * math.tanh(dv / 3)
        f = min(max(f, 0.35), 2.4)
        if ln >= 0.15:
            f *= 0.25 + 0.75 * math.sqrt(min(1, at / t0))
            f *= 0.12 + 0.88 * max(0.0, min(1, (ln - at) / t1)) ** 0.7
        out.append(ink * max(f, 0.12))
    return out


def widths(model, var, j, style, nib=NIB):
    """the ink's width (x-heights) at each point of the variant's stroke j in a style (ttf.c
    hw_font_widths; Calligraphy from the stroke's direction at each point)"""
    st = var["strokes"][j]
    if style == "brush":
        return _brush(model, var, j)
    ink = model["metrics"].get("ink_width", 0.07)
    if style != "calligraphy":
        return [ink] * len(st)
    lo, hi = nib_widths(model)
    a, out = math.radians(nib), []
    for i in range(len(st)):
        p, q = st[max(i - 1, 0)], st[min(i + 1, len(st) - 1)]
        dx, dy = q[0] - p[0], q[1] - p[1]
        out.append(lo + (hi - lo) * abs(math.sin(math.atan2(dy, dx) - a)) if dx or dy else lo + (hi - lo) * abs(math.sin(a)))
    return out
