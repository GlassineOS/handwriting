"""Lay out text as humanised online ink: jitter, font blending, stroke order variants, timing.

Output sample: text, strokes as (n, 3) arrays of x, y (em, y down) and t (seconds),
per-character stroke indices, and the font of each character.
"""

from dataclasses import dataclass

import numpy as np
from scipy.special import erf

from .glyph import Glyph

RATE = 200.0  # pen samples per second


@dataclass
class Params:
    size: float = 0.06  # glyph scale sd
    rot: float = 3.0  # glyph rotation sd (degrees)
    shear: float = 0.06  # glyph shear sd
    elastic: float = 0.015  # glyph bend amplitude (em)
    slant: float = 0.08  # line slant sd (shear)
    tilt: float = 1.0  # line tilt sd (degrees)
    drift: float = 0.04  # baseline wander amplitude (em)
    spacing: float = 0.06  # letter advance sd
    word_space: float = 0.35  # extra word gap (em), mean
    noise: float = 0.002  # point noise (em)
    speed: float = 5.0  # mean pen speed (em/s)
    font_per_letter: float = 0.04  # chance a letter changes font inside a word
    reverse_loop: float = 0.25  # chance a closed stroke is written clockwise
    delay: float = 0.5  # chance dots and crosses wait for the word's end
    extra_lift: float = 0.03  # chance a stroke is split by a stray pen lift


def _affine(pts, rng, p: Params, centre):
    s = 1 + rng.normal(0, p.size)
    a = np.radians(rng.normal(0, p.rot))
    sh = rng.normal(0, p.shear)
    m = s * np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]) @ np.array([[1, sh], [0, 1]])
    return (pts - centre) @ m.T + centre


def _elastic(pts, rng, p: Params):
    out = pts.copy()
    for axis in (0, 1):
        for _ in range(2):
            k = rng.uniform(1.5, 4.0)
            ph = rng.uniform(0, 2 * np.pi)
            out[:, axis] += rng.normal(0, p.elastic) * np.sin(k * pts[:, 1 - axis] * np.pi + ph)
    return out


def _corners(s, deg=60.0):
    if len(s) < 3:
        return [0, len(s) - 1]
    v = np.diff(s, axis=0)
    ang = np.degrees(np.abs(np.arctan2(v[:-1, 0] * v[1:, 1] - v[:-1, 1] * v[1:, 0], (v[:-1] * v[1:]).sum(1))))
    idx = [0] + [i + 1 for i in np.nonzero(ang > deg)[0]] + [len(s) - 1]
    return sorted(set(idx))


def _time(s, rng, p: Params, t0: float):
    """Sample a stroke at the pen rate; one lognormal speed pulse per segment between corners."""
    out = []
    t = t0
    cut = _corners(s)
    for a, b in zip(cut[:-1], cut[1:]):
        seg = s[a:b + 1]
        d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(seg, axis=0), axis=1))]
        length = d[-1]
        if length <= 0:
            continue
        dur = max(0.03, length / (p.speed * rng.lognormal(0, 0.2)) + 0.02)
        mu, sigma = np.log(dur / 2), rng.uniform(0.25, 0.4)
        n = max(2, int(dur * RATE))
        tt = np.linspace(0, dur, n)
        cdf = 0.5 * (1 + erf((np.log(np.maximum(tt, 1e-6)) - mu) / (sigma * np.sqrt(2))))
        cdf = (cdf - cdf[0]) / (cdf[-1] - cdf[0] + 1e-12)
        x = np.interp(cdf * length, d, seg[:, 0])
        y = np.interp(cdf * length, d, seg[:, 1])
        pts = np.c_[x, y, t + tt]
        out.append(pts if not out else pts[1:])
        t += dur
    if not out:
        return np.c_[s[:1], [t0]], t0
    arr = np.concatenate(out)
    return arr, float(arr[-1, 2])


def line(text: str, fonts: list[tuple[str, dict[str, Glyph]]], rng: np.random.Generator,
         p: Params = Params()) -> dict:
    """fonts: list of (font id, glyph table). Returns one sample."""
    slant = rng.normal(0.05, p.slant)
    tilt = np.radians(rng.normal(0, p.tilt))
    ph, freq = rng.uniform(0, 2 * np.pi), rng.uniform(0.1, 0.4)
    pen_x = 0.0
    shapes = []  # (char index, stroke array (n,2), delayable, word index)
    char_font = []
    word = 0
    fi = int(rng.integers(len(fonts)))
    for ci, ch in enumerate(text):
        if ch == " ":
            word += 1
            fi = int(rng.integers(len(fonts)))
            pen_x += rng.normal(p.word_space, p.word_space * 0.3)
        elif rng.random() < p.font_per_letter:
            fi = int(rng.integers(len(fonts)))
        fid, table = fonts[fi]
        char_font.append(fid)
        g = table.get(ch)
        if g is None:
            continue
        centre = np.array([g.advance / 2, -0.35])
        base_y = p.drift * np.sin(freq * pen_x * 2 * np.pi + ph)
        for s, dl in zip(g.strokes, g.delayable):
            q = _elastic(_affine(s, rng, p, centre), rng, p)
            q = q + np.array([pen_x, base_y])
            q[:, 0] += -slant * q[:, 1]
            shapes.append((ci, q, dl, word))
        pen_x += g.advance * max(0.6, 1 + rng.normal(0, p.spacing))

    # stroke order variants: loops reversed, dots and crosses delayed to the word's end
    order = []
    delayed = {}
    for ci, q, dl, w in shapes:
        closed = np.linalg.norm(q[0] - q[-1]) < 0.04
        if closed and rng.random() < p.reverse_loop:
            q = q[::-1]
        if dl and rng.random() < p.delay:
            delayed.setdefault(w, []).append((ci, q))
            continue
        order.append((ci, q, w))
    seq = []
    for i, (ci, q, w) in enumerate(order):
        seq.append((ci, q))
        nxt = order[i + 1][2] if i + 1 < len(order) else None
        if nxt != w:
            seq.extend(delayed.pop(w, []))
    for rest in delayed.values():
        seq.extend(rest)

    # timing, stray pen lifts, tilt, noise
    rot = np.array([[np.cos(tilt), -np.sin(tilt)], [np.sin(tilt), np.cos(tilt)]])
    strokes, owner = [], []
    t = 0.0
    last = None
    for ci, q in seq:
        q = q @ rot.T
        parts = [q]
        if len(q) > 20 and rng.random() < p.extra_lift:
            k = int(rng.integers(5, len(q) - 5))
            parts = [q[:k + 1], q[k:]]
        for part in parts:
            if last is not None:
                gap = np.linalg.norm(part[0] - last)
                t += rng.uniform(0.05, 0.12) + gap / (p.speed * 1.5)
            arr, t = _time(part, rng, p, t)
            arr[:, :2] += rng.normal(0, p.noise, arr[:, :2].shape)
            strokes.append(arr)
            owner.append(ci)
            last = part[-1]

    spans = [[] for _ in text]
    for si, ci in enumerate(owner):
        spans[ci].append(si)
    return {"text": text, "strokes": strokes, "spans": spans, "fonts": char_font}
