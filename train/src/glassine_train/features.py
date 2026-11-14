"""Ink to model input. Uses only what a pen reports (x, y, t per point, strokes in order);
scale, tilt and baseline are estimated from the ink itself, never taken from the generator.

Steps: remove the line's tilt (least squares), estimate the writing's core height from the
spread of arc-length-resampled y values, resample every stroke by arc length with a step
of STEP core heights, then per point:

  dx, dy   move from the previous point (at a stroke start: the pen-up jump), clipped
  y        height above or below the line's centre, in core heights
  up       1 at the first point of every stroke after the first (pen was lifted)
  dt       log(1 + gap in seconds * 10) for a stroke's first point (pause length), else 0
"""

import numpy as np

STEP = 0.1  # resampling step, in core heights
NFEAT = 5
CLIP = 6.0
MAX_TILT = 0.35  # radians


def _resample(s: np.ndarray, step: float) -> np.ndarray:
    xy = s[:, :2]
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
    if len(s) < 2 or d[-1] < step:
        return s[[0, -1]] if len(s) > 1 and d[-1] > 0 else s[:1]
    u = np.linspace(0, d[-1], int(np.ceil(d[-1] / step)) + 1)
    return np.c_[np.interp(u, d, s[:, 0]), np.interp(u, d, s[:, 1]), np.interp(u, d, s[:, 2])]


def normalise(strokes: list[np.ndarray]) -> list[np.ndarray]:
    """Tilt-corrected, scaled to the core height, centred on the line: same shapes as input."""
    pts = np.concatenate(strokes)
    x, y = pts[:, 0], pts[:, 1]
    th = 0.0
    if np.ptp(x) > 2 * np.ptp(y):  # only lines wide enough for a meaningful tilt
        th = float(np.clip(-np.arctan(np.polyfit(x, y, 1)[0]), -MAX_TILT, MAX_TILT))
    c, s = np.cos(th), np.sin(th)
    rot = [np.c_[q[:, 0] * c - q[:, 1] * s, q[:, 0] * s + q[:, 1] * c, q[:, 2]] for q in strokes]
    # core height from evenly spaced points, so slow parts of the line do not weigh more
    span = max(np.ptp(np.concatenate(rot)[:, :2], axis=0).max(), 1e-6)
    even = np.concatenate([_resample(q, span / 400) for q in rot])
    lo, mid, hi = np.quantile(even[:, 1], [0.2, 0.5, 0.8])
    h = max(hi - lo, span / 50)
    x0 = even[:, 0].min()
    return [np.c_[(q[:, 0] - x0) / h, (q[:, 1] - mid) / h, q[:, 2]] for q in rot]


def features(strokes: list[np.ndarray]) -> np.ndarray:
    """(n, NFEAT) float32 for strokes given as (k, 3) arrays of x, y, t."""
    strokes = [np.asarray(q, float) for q in strokes if len(q)]
    if not strokes:
        return np.zeros((0, NFEAT), np.float32)
    norm = normalise(strokes)
    rows = []
    prev = None
    prev_t = None
    for i, q in enumerate(norm):
        r = _resample(q, STEP)
        f = np.zeros((len(r), NFEAT))
        start = prev if prev is not None else r[0, :2]
        xy = np.vstack([start, r[:, :2]])
        f[:, 0:2] = np.diff(xy, axis=0)
        f[:, 2] = r[:, 1]
        if i:
            f[0, 3] = 1.0
            f[0, 4] = np.log1p(max(0.0, r[0, 2] - prev_t) * 10)
        rows.append(f)
        prev, prev_t = r[-1, :2], r[-1, 2]
    out = np.concatenate(rows)
    np.clip(out, -CLIP, CLIP, out=out)
    return out.astype(np.float32)
