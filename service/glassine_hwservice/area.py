# SPDX-License-Identifier: GPL-2.0-or-later
"""The writing area and units of every request the service makes (spec 004 FR-016, FR-017;
docs/service.md, The writing's size). A model that reads the ink's size (spec 001 FR-018)
needs a reference: the person's letters in millimetres (units with PER_MM), else the area's
line. So each session gets an area: the app's when it gave one, else the box of the writing
with a line and a guide line found from the writing itself.

Found from the writing: a stroke's size is the larger of its height and a quarter of its
width (as the recognizer measures ink); the line is LINE_SIZES median stroke sizes, the
guide line of one line of writing the median of its strokes' lowest points (a descender or
a dot is one stroke among many). On the recorded ink and the synthetic held-out fonts this
reads within a point of the keyboard's paper (a known line and guide line) at every writing
size; a fixed line in millimetres is either as good or, when the person writes smaller
than it guesses, far worse."""
import math

LINE_SIZES = 6.0        # a line found from the writing: six median stroke sizes
MIN_GLYPHS = 3          # writing with fewer glyphs tells nothing of its line
MIN_STEPS = 8           # the pen's rate needs this many time steps


def _median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _num(v):
    v = round(float(v), 3)
    return str(int(v)) if v.is_integer() else repr(v)


def stroke_size(st):
    xs, ys = [p[0] for p in st], [p[1] for p in st]
    return max(max(ys) - min(ys), (max(xs) - min(xs)) / 4)


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


def line(strokes):
    """the line height found from the writing; 0 when it has fewer than MIN_GLYPHS glyphs
    (a lone character's line, found from itself, would hide its size: o or O)"""
    if glyphs(strokes) < MIN_GLYPHS:
        return 0.0
    return LINE_SIZES * _median([stroke_size(s) for s in strokes if s])


def base(strokes):
    """the guide line of one line of writing: the median of its strokes' lowest points"""
    low = [max(p[1] for p in s) for s in strokes if s]
    return _median(low) if low else None


def text(bx, ln, bs=None, rot=0):
    """the recognizer's area: X0,Y0,X1,Y1,LINE,ROT[,BASE]"""
    f = [_num(v) for v in bx] + [_num(ln), str(rot)]
    if bs is not None and math.isfinite(bs):
        f.append(_num(bs))
    return ",".join(f)


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
