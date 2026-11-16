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
    slant_mean: float = 0.05  # line slant mean (shear, positive leans right)
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
    # letter-shape variation (all off by default, so default ink is unchanged): fonts draw a
    # letter the same way every time, writers do not
    warp: float = 0.0  # per letter: smooth local displacement, sd of each bump (em)
    zones: float = 0.0  # per line: ascender, descender and letter-width scale (lognormal sd)
    asc_scale: float = 1.0  # mean scale of the part above the x-height (ascenders, capitals)
    desc_scale: float = 1.0  # mean scale of the part below the baseline (descenders)
    # timing and pen lifts (the defaults are the original toy model)
    corner: float = 60.0  # turn (degrees) that starts a new speed pulse
    pulse_floor: float = 0.0  # share of steady speed in each pulse: 0 = stops at every corner
    pause_lo: float = 0.05  # pen-up pause before a stroke: uniform part (s)
    pause_hi: float = 0.12
    jump_speed: float = 0.0  # pen speed in the air (em/s); 0 = 1.5 × speed
    join: float = 0.0  # chance two strokes of a word close together are written as one
    join_dist: float = 0.6  # the longest jump a join bridges (em)


# Parameter sets measured against real ink (glassine-synth stats; README "Real ink").
PRESETS = {
    # one writer's prompted words: tall ascenders and capitals over a small x-height, few
    # pen lifts, steady speed along a stroke, short pauses nearly independent of the jump
    "real1": {"asc_scale": 2.3, "desc_scale": 1.2, "slant_mean": 0.1, "speed": 6.5, "corner": 30.0,
              "pulse_floor": 0.45, "pause_lo": 0.04, "pause_hi": 0.15, "jump_speed": 25.0,
              "join": 0.6, "join_dist": 0.7, "warp": 0.025, "zones": 0.15},
}


def preset(name: str, **kw) -> "Params":
    return Params(**{**PRESETS[name], **kw})


XH = 0.45  # x-height of every font after loading (em); the cli scales all fonts to it


def _warp(strokes, rng, amp):
    """One smooth random displacement field for all strokes of a letter: a few Gaussian
    bumps inside its box, so loops grow or shrink, openings close a little, bars move."""
    pts = np.concatenate(strokes)
    lo, hi = pts.min(0), pts.max(0)
    k = int(rng.integers(2, 5))
    c = rng.uniform(lo, hi, (k, 2))
    r = rng.uniform(0.12, 0.3, k)
    v = rng.normal(0, amp, (k, 2))

    def f(q):
        w = np.exp(-((q[:, None, :] - c[None]) ** 2).sum(-1) / (2 * r[None] ** 2))
        return q + w @ v
    return [f(q) for q in strokes]


def _zones(q, asc, desc):
    """Stretch the part above the x-height by asc and the part below the baseline by desc."""
    y = q[:, 1]
    y = np.where(y < -XH, -XH + (y + XH) * asc, y)
    y = np.where(y > 0, y * desc, y)
    return np.c_[q[:, 0], y]


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
    cut = _corners(s, p.corner)
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
        if p.pulse_floor > 0:  # part of the way at a steady speed: no stop at the corner
            cdf = (1 - p.pulse_floor) * cdf + p.pulse_floor * tt / dur
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
    slant = rng.normal(p.slant_mean, p.slant)
    tilt = np.radians(rng.normal(0, p.tilt))
    ph, freq = rng.uniform(0, 2 * np.pi), rng.uniform(0.1, 0.4)
    asc = desc = width = 1.0
    if p.zones > 0:
        asc, desc = rng.lognormal(0, p.zones, 2)
        width = rng.lognormal(0, p.zones / 2)
    asc, desc = asc * p.asc_scale, desc * p.desc_scale
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
        adv = g.advance * width
        centre = np.array([adv / 2, -0.35])
        base_y = p.drift * np.sin(freq * pen_x * 2 * np.pi + ph)
        strokes = g.strokes
        times = None  # a glyph of real ink carries its own timing (x, y, t from stroke start)
        if strokes and strokes[0].shape[1] == 3:
            times = [q[:, 2] for q in strokes]
            strokes = [q[:, :2] for q in strokes]
        if p.zones > 0 or asc != 1.0 or desc != 1.0:
            strokes = [_zones(q * [width, 1], asc, desc) for q in strokes]
        if p.warp > 0 and strokes:
            strokes = _warp(strokes, rng, p.warp)
        for k, (s, dl) in enumerate(zip(strokes, g.delayable)):
            q = _elastic(_affine(s, rng, p, centre), rng, p)
            q = q + np.array([pen_x, base_y])
            q[:, 0] += -slant * q[:, 1]
            if times is not None:
                q = np.c_[q, times[k]]
            shapes.append((ci, q, dl, word))
        pen_x += adv * max(0.6, 1 + rng.normal(0, p.spacing))

    # stroke order variants: loops reversed, dots and crosses delayed to the word's end
    order = []
    delayed = {}
    for ci, q, dl, w in shapes:
        closed = q.shape[1] == 2 and np.linalg.norm(q[0] - q[-1]) < 0.04
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
    if p.join > 0:  # neighbouring strokes of a word written in one go, the pen kept down
        word_of = {ci: w for ci, _, _, w in shapes}
        merged = []
        for ci, q in seq:
            if merged:
                pc, pq, _ = merged[-1]
                if (q.shape[1] == 2 and pq.shape[1] == 2 and word_of[pc] == word_of[ci]
                        and np.linalg.norm(q[0] - pq[-1]) < p.join_dist
                        and rng.random() < p.join):
                    n = max(2, int(np.linalg.norm(q[0] - pq[-1]) / 0.02))
                    bridge = np.linspace(pq[-1], q[0], n)[1:-1]
                    cis = merged[-1][2] + ([ci] if ci not in merged[-1][2] else [])
                    merged[-1] = (pc, np.vstack([pq, bridge, q]), cis)
                    continue
            merged.append((ci, q, [ci]))
        seq = [(cis, q) for _, q, cis in merged]
    else:
        seq = [([ci], q) for ci, q in seq]
    air = p.jump_speed if p.jump_speed > 0 else p.speed * 1.5
    strokes, owner = [], []
    t = 0.0
    last = None
    for cis, q in seq:
        q = np.c_[q[:, :2] @ rot.T, q[:, 2:]]
        parts = [q]
        if len(q) > 20 and rng.random() < p.extra_lift:
            k = int(rng.integers(5, len(q) - 5))
            parts = [q[:k + 1], q[k:]]
        for part in parts:
            if last is not None:
                gap = np.linalg.norm(part[0, :2] - last)
                t += rng.uniform(p.pause_lo, p.pause_hi) + gap / air
            if part.shape[1] == 3:  # real timing kept
                arr = np.c_[part[:, :2], t + part[:, 2] - part[0, 2]]
                t = float(arr[-1, 2])
            else:
                arr, t = _time(part, rng, p, t)
            arr[:, :2] += rng.normal(0, p.noise, arr[:, :2].shape)
            strokes.append(arr)
            owner.append(cis)
            last = part[-1, :2]

    spans = [[] for _ in text]
    for si, cis in enumerate(owner):
        for ci in cis:
            spans[ci].append(si)
    return {"text": text, "strokes": strokes, "spans": spans, "fonts": char_font}
