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


def rotate(prev, n):
    """the variant at a character, as the font's contextual alternates pick it (letters.h
    hw_letters_rotate): prev the variant before in the run, None at its start or after a
    character the font lacks"""
    if prev is None or n < 2:
        return 0
    if prev == 0:
        return 1
    if prev == 1 and n == 3:
        return 2
    if prev == 2 and n == 2:
        return 1
    return 0


def variants(model, text):
    """per character of text: the variant the font shows, None for one it lacks"""
    out, prev = [], None
    for c in text:
        if c == " ":
            out.append(0)
            prev = 0
            continue
        d = model["chars"].get(c) if model else None
        if not d or not d.get("covered") or not d.get("variants"):
            out.append(None)
            prev = None
            continue
        v = rotate(prev, len(d["variants"]))
        out.append(v)
        prev = v
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


def coverage(model, chars=TARGET):
    """[(char, usable samples, in the font)] for the characters the page shows"""
    out = []
    for c in chars:
        d = model["chars"].get(c, {}) if model else {}
        out.append((c, int(d.get("samples", 0)), bool(d.get("covered") and d.get("variants"))))
    return out


def missing(model, chars=TARGET):
    return [c for c, _, ok in coverage(model, chars) if not ok]
