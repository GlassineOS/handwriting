# SPDX-License-Identifier: GPL-2.0-or-later
"""The writing area, units and size prior of every request the service makes (spec 004
FR-016, FR-017; docs/service.md, The writing's size). A model that reads the ink's size
(spec 001 FR-018) judges it in physical size: the ink's points per millimetre and the
person's letters in millimetres, before there are enough of them a typical handwriting size
in millimetres (TYPICAL_XH_MM, the prior). A ruled line is a reference only when the app
sets one: no line is found from the writing, so a lone character is never measured against
a line found from itself, nor against one borrowed from other writing.

The area's box is the app's, else the bounding box of the writing; its line the app's ruled
lines' spacing, else 0 (none); its guide line the app's first ruled line, else for one line
of writing with at least MIN_GLYPHS glyphs the median of its strokes' lowest points (where the
letters sit, which tells the marks on the line apart: a comma from an apostrophe; it is no
size reference)."""
import math

TYPICAL_XH_MM = 2.5     # the prior: a typical small letter (x-height) in handwriting, mm
DEFAULT_PX_PER_MM = 96 / 25.4   # an output of unknown size, until the person sets its density
MIN_GLYPHS = 3          # writing with fewer glyphs tells nothing of its guide line
MIN_STEPS = 8           # the pen's rate needs this many time steps


def _median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _num(v):
    v = round(float(v), 3)
    return str(int(v)) if v.is_integer() else repr(v)


def box(strokes):
    """the writing's bounding box (x0, y0, x1, y1), never empty"""
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    return (x0, y0, max(x1, x0 + 1), max(y1, y0 + 1))


def glyphs(strokes):
    """how many glyphs: strokes overlapping horizontally, also through others, are one"""
    n, right = 0, None
    for x0, x1 in sorted((min(p[0] for p in s), max(p[0] for p in s)) for s in strokes if s):
        if right is None or x0 > right:
            n += 1
            right = x1
        else:
            right = max(right, x1)
    return n


def base(strokes):
    """the guide line of one line of writing: the median of its strokes' lowest points; None
    for writing of fewer than MIN_GLYPHS glyphs (a lone character's own bottom would hide
    where it sits: a comma from an apostrophe)"""
    if glyphs(strokes) < MIN_GLYPHS:
        return None
    low = [max(p[1] for p in s) for s in strokes if s]
    return _median(low) if low else None


def text(bx, ln, bs=None, rot=0):
    """the recognizer's area: X0,Y0,X1,Y1,LINE,ROT[,BASE] (LINE 0: no ruled line)"""
    f = [_num(v) for v in bx] + [_num(ln), str(rot)]
    if bs is not None and math.isfinite(bs):
        f.append(_num(bs))
    return ",".join(f)


def per_mm(units):
    """the units' points per millimetre, 0 when they don't say"""
    f = units.split(":")
    try:
        return float(f[1]) if f[0] == "device" and len(f) >= 2 and float(f[1]) > 0 else 0.0
    except ValueError:
        return 0.0


def prior(units):
    """the typical small letter in the units (the recognizer's `prior`): TYPICAL_XH_MM at the
    units' points per millimetre, for `device` units without one at DEFAULT_PX_PER_MM (the
    units stay the pen's own: no size in millimetres is claimed); None for em (the units say
    how high a line is)"""
    f = units.split(":")
    if f[0] != "device":
        return None
    return TYPICAL_XH_MM * (per_mm(units) or DEFAULT_PX_PER_MM)


def rate(strokes):
    """the pen's points per second, 1000 / the median time step in ms (as the keyboard
    measures it); 0 when there are too few steps"""
    d = [b[2] - a[2] for s in strokes for a, b in zip(s, s[1:]) if b[2] > a[2]]
    if len(d) < MIN_STEPS:
        return 0
    return int(math.floor(1000 / _median(d) + 0.5))


def with_rate(units, r):
    """device:PER_MM with the rate added (device:PER_MM:RATE) when it is known and the units
    don't have one; other units as they are"""
    f = units.split(":")
    if f[0] == "device" and len(f) == 2 and r > 0:
        return "%s:%d" % (units, r)
    return units
