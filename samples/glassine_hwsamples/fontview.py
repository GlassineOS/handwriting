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


def layout(model, text):
    """[(char, strokes or None, x, advance)] in x-heights, x from the left of the text, y up
    from the baseline; strokes None: the system font draws the character"""
    m = model["metrics"]
    gap, ink = m.get("letter_spacing", 0.15), m.get("ink_width", 0.07)
    word = m.get("word_spacing", 0.7)
    out, x = [], 0.0
    for c, v in zip(text, variants(model, text)):
        if c == " ":
            adv = max(word - gap, 0.2)
            out.append((c, [], x, adv))
        elif v is None:
            adv = 0.7
            out.append((c, None, x, adv))
        else:
            var = model["chars"][c]["variants"][v]
            ox = gap / 2 + ink / 2
            strokes = [[(ox + p[0], p[1]) for p in s] for s in var["strokes"]]
            adv = var["width"] + gap + ink
            out.append((c, strokes, x, adv))
        x += adv
    return out


def coverage(model, chars=TARGET, status=None):
    """[(char, usable samples, in the font)] for the characters the page shows: the usable
    samples as the samples hold them now (letters status, spec 003 FR-030; the model's own
    count without a status), in the font as the model was built"""
    counts = (status or {}).get("chars")
    out = []
    for c in chars:
        d = model["chars"].get(c, {}) if model else {}
        n = counts.get(c, (0, 0))[0] if counts is not None else int(d.get("samples", 0))
        out.append((c, n, bool(d.get("covered") and d.get("variants"))))
    return out


def missing(model, chars=TARGET, status=None):
    """the characters that still need writing: fewer than MIN usable samples"""
    return [c for c, n, _ in coverage(model, chars, status) if n < MIN]


def pending(model, chars=TARGET, status=None):
    """the characters written often enough that the next build puts them in"""
    return [c for c, n, ok in coverage(model, chars, status) if n >= MIN and not ok]
