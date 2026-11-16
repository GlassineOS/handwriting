"""Letter ink profiles and gaps as the recognizer computes them (recognizer/src/hw/spacing.c,
docs/recognizer.md "Spacing"; spec 003 FR-007), for the generator to place letters by.

A profile is, per height band (BANDS bands from Y0 to Y1 x-heights above the baseline, the
outer two open-ended), the leftmost and rightmost x of the ink's centre lines, every band a
segment crosses. The gap between two letters is the narrowest distance between the first's
right profile and the second's left one over the bands both have ink in (their boxes when
they share none). Coordinates: x to the right, y up from the baseline, in x-heights.
"""

import math

import numpy as np

BANDS = 8
Y0, Y1 = -0.6, 1.6
BAND = (Y1 - Y0) / BANDS
SIDE_FAR = 1.0


def _clampy(y):
    return np.clip(y, Y0 + 1e-9, Y1 - 1e-9)


def _band(y):
    return np.clip(np.floor((_clampy(y) - Y0) / BAND).astype(int), 0, BANDS - 1)


class Profile:
    def __init__(self):
        self.l = np.full(BANDS, np.nan)
        self.r = np.full(BANDS, np.nan)

    def _put(self, b, x):
        np.fmin.at(self.l, b, x)
        np.fmax.at(self.r, b, x)

    def add(self, x, y):
        """A polyline's segments (x, y arrays, y up in x-heights), as hw_profile_seg."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        if len(x) == 1:
            self._put(_band(y), x)
            return
        x0, y0, x1, y1 = x[:-1], _clampy(y[:-1]), x[1:], _clampy(y[1:])
        b0, b1 = _band(y0), _band(y1)
        self._put(b0, x0)
        self._put(b1, x1)
        for i in np.nonzero(b0 != b1)[0]:
            lo, hi = sorted((b0[i], b1[i]))
            for b in range(lo + 1, hi + 1):
                ye = Y0 + b * BAND
                t = (ye - y0[i]) / (y1[i] - y0[i])
                xe = x0[i] + t * (x1[i] - x0[i])
                self._put(np.array([b - 1, b]), np.array([xe, xe]))

    def empty(self) -> bool:
        return bool(np.all(np.isnan(self.l)))

    def box(self) -> tuple[float, float]:
        return float(np.nanmin(self.l)), float(np.nanmax(self.r))

    def shifted(self, dx: float) -> "Profile":
        p = Profile()
        p.l, p.r = self.l + dx, self.r + dx
        return p


def gap(a: Profile, b: Profile, shift: float = 0.0) -> float:
    """hw_profile_gap: b moved right by shift."""
    both = ~np.isnan(a.r) & ~np.isnan(b.l)
    if both.any():
        return float(np.min(b.l[both] + shift - a.r[both]))
    if a.empty() or b.empty():
        return 0.0
    return float(np.nanmin(b.l) + shift - np.nanmax(a.r))


def open_space(a: Profile, b: Profile) -> float:
    """hw_profile_open: the mean open space beyond the narrowest gap (each band at most
    SIDE_FAR), 0 with fewer than two shared bands."""
    g = gap(a, b)
    both = ~np.isnan(a.r) & ~np.isnan(b.l)
    if both.sum() < 2:
        return 0.0
    d = b.l[both] - a.r[both] - g
    return float(np.minimum(d, SIDE_FAR).mean())


def of_strokes(strokes, xh: float, base: float = 0.0) -> Profile:
    """The profile of strokes given in the generator's units (em, y down, baseline `base`),
    in x-heights of xh em."""
    p = Profile()
    for q in strokes:
        p.add(np.asarray(q)[:, 0] / xh, (base - np.asarray(q)[:, 1]) / xh)
    return p


def pair_gap(spacing: dict, a: str, b: str, pa: Profile, pb: Profile) -> float:
    """hw_spacing_gap without side shapes: a pair's own gap, else the typical gap less
    `open` times the open space beyond open0, within 0.3 of the typical gap."""
    for p in spacing.get("pairs", []):
        if p[0] == a and p[1] == b:
            return float(p[2])
    g = spacing["letter"]
    if spacing.get("open", 0) > 0:
        g -= spacing["open"] * (open_space(pa, pb) - spacing.get("open0", 0.0))
    return float(min(max(g, spacing["letter"] - 0.3), spacing["letter"] + 0.3))


def finite(x) -> bool:
    return x is not None and math.isfinite(x)
