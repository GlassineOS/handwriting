"""Pen timing for the generator: a sigma-lognormal speed model fitted to real ink, and the
measures that compare generated ink with real ink (spec 001 FR-020, "timing and dynamics
from real ink, shapes broad").

Model. A stroke's speed along its path is a sum of lognormal pulses (the kinematic theory of
rapid movements; here along the path, so only the speed is modelled, not the direction):

    v(t) = sum_i D_i / (sigma_i sqrt(2 pi) (t - t0_i)) exp(-(ln(t - t0_i) - mu_i)^2 / (2 sigma_i^2))

D_i is the pulse's share of the path (x-heights), t0_i its onset, mu_i and sigma_i its log
time scale and log spread. `fit_stroke` finds the pulses of one real stroke (speed peaks as
the start, then least squares on the whole speed curve); `fit()` gathers every well fitted
stroke of a set of samples into a few distributions (`Fit`):

- a pulse's duration T (from its 1 % to its 99 % point, 2 exp(mu) sinh(Z sigma)):
  ln T = a + b ln D + noise, how long a pulse of a given length takes;
- ln sigma (the pulse's skew): normal, correlated with the duration's noise;
- pulse rate: pulses per x-height of path, lognormal;
- overlap: the time from one pulse's peak to the next over the mean of their durations,
  lognormal (how far neighbouring pulses overlap).

Duration, skew and peak describe a pulse by what the speed curve shows; (t0, mu, sigma)
alone are nearly interchangeable for narrow pulses (a late, narrow lognormal looks like an
early, wide one), so their spread would mean nothing to draw from;
- the speed when the pen touches down and lifts, as a share of the stroke's peak speed (the
  pen is moving at both ends, the fitted pulses start before the touch);
- pen-up pauses: ln pause = a + b ln(1 + jump) + noise, between characters and inside
  one, and the pause before a delayed stroke.

`Timing` draws strokes from a Fit: per writer a tempo, a spread and a pulse rate drawn
around the fit (`broaden`; the fit has one writer's numbers only, so the writer-to-writer
spread is an assumption, kept broad), per stroke pulses along the path (cut at its sharpest
corners first), times sampled at the pen rate.

`dynamics()` measures any ink (real or generated, in x-heights and seconds, resampled to
one rate so both are measured alike): speed, tangential acceleration, stroke duration,
pen-up pauses. Only aggregate numbers come out.
"""

import pickle
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import find_peaks
from scipy.special import erf

RATE = 200.0  # Hz, the generator's pen rate and the rate ink is measured at
SQ2PI = np.sqrt(2 * np.pi)


def _lognorm(t, D, t0, mu, sigma):
    x = np.maximum(t - t0, 1e-9)
    return D / (sigma * SQ2PI * x) * np.exp(-(np.log(x) - mu) ** 2 / (2 * sigma ** 2)) * (t > t0)


Z99 = 2.326  # the 99 % point of a standard normal


def duration(mu, sigma):
    """A pulse's duration from its 1 % to its 99 % point."""
    return 2 * np.exp(mu) * np.sinh(Z99 * np.asarray(sigma))


def _lognorm_cdf(t, t0, mu, sigma):
    x = np.maximum(t - t0, 1e-9)
    return np.where(t > t0, 0.5 * (1 + erf((np.log(x) - mu) / (sigma * np.sqrt(2)))), 0.0)


def resample(q: np.ndarray, rate: float = RATE) -> np.ndarray:
    """A stroke (x, y, t) at a fixed rate (linear in time)."""
    if len(q) < 2 or q[-1, 2] <= q[0, 2]:
        return q
    t = np.arange(q[0, 2], q[-1, 2] + 1e-9, 1.0 / rate)
    return np.c_[np.interp(t, q[:, 2], q[:, 0]), np.interp(t, q[:, 2], q[:, 1]), t]


def _smooth(v, sd_pts):
    if sd_pts <= 0 or len(v) < 3:
        return v
    k = np.arange(-int(3 * sd_pts) - 1, int(3 * sd_pts) + 2)
    w = np.exp(-k ** 2 / (2 * sd_pts ** 2))
    w /= w.sum()
    return np.convolve(np.pad(v, len(k) // 2, mode="edge"), w, mode="valid")


def speed(q: np.ndarray, rate: float = RATE, smooth_s: float = 0.01) -> tuple[np.ndarray, np.ndarray]:
    """(t, speed) of a stroke resampled at `rate`, positions smoothed (Gaussian, smooth_s)."""
    r = resample(q, rate)
    if len(r) < 3:
        return r[:, 2], np.zeros(len(r))
    x = _smooth(r[:, 0], smooth_s * rate)
    y = _smooth(r[:, 1], smooth_s * rate)
    v = np.hypot(np.gradient(x), np.gradient(y)) * rate
    return r[:, 2] - r[0, 2], v


# ---------------------------------------------------------------------------- fitting

def fit_stroke(q: np.ndarray, max_pulses: int = 12):
    """Pulses (D, t0, mu, sigma) of one stroke in x-heights and seconds (t from the stroke's
    start) and the fit's signal-to-noise ratio (dB); None for a stroke too short to fit."""
    t, v = speed(q)
    if len(t) < 8 or v.max() <= 0:
        return None
    pk, _ = find_peaks(v, prominence=0.08 * v.max(), distance=max(2, int(0.02 * RATE)))
    if not len(pk):
        pk = np.array([int(np.argmax(v))])
    pk = pk[np.argsort(-v[pk])][:max_pulses]
    pk.sort()
    # initial pulses: each peak gets the path between the valleys around it
    cuts = [0] + [int(a + np.argmin(v[a:b + 1])) for a, b in zip(pk[:-1], pk[1:])] + [len(v) - 1]
    dt = 1.0 / RATE
    x0, lo, hi = [], [], []
    for i, p in enumerate(pk):
        D = max(float(v[cuts[i]:cuts[i + 1] + 1].sum() * dt), 1e-3)
        sigma = 0.3
        width = max((cuts[i + 1] - cuts[i]) * dt, 0.03)
        mu = np.log(width / 2)
        t0 = t[p] - np.exp(mu - sigma ** 2)
        x0 += [D, t0, mu, sigma]
        lo += [1e-4, -1.0, np.log(0.003), 0.05]
        hi += [50.0, t[-1], np.log(3.0), 1.5]
    x0 = np.clip(x0, np.array(lo) + 1e-6, np.array(hi) - 1e-6)

    def res(x):
        m = np.zeros_like(t)
        for i in range(0, len(x), 4):
            m += _lognorm(t, *x[i:i + 4])
        return m - v
    try:
        r = least_squares(res, x0, bounds=(lo, hi), max_nfev=200 * len(x0), x_scale="jac")
    except ValueError:
        return None
    err = (r.fun ** 2).sum()
    snr = 10 * np.log10((v ** 2).sum() / max(err, 1e-12))
    pulses = r.x.reshape(-1, 4)
    return pulses[np.argsort(pulses[:, 1])], float(snr), t, v


@dataclass
class Fit:
    dur_a: float = np.log(0.2)  # ln T = dur_a + dur_b ln D + N(0, dur_sd)
    dur_b: float = 0.3
    dur_sd: float = 0.3
    lsig_mean: float = np.log(0.3)  # ln sigma ~ N(lsig_mean, lsig_sd)
    lsig_sd: float = 0.3
    dur_lsig_corr: float = 0.0  # correlation of the duration's noise with ln sigma
    rate_mean: float = np.log(1.0)  # ln(pulses per x-height of path)
    rate_sd: float = 0.4
    ovl_mean: float = np.log(0.5)  # ln(peak-to-peak time / mean duration of the two)
    ovl_sd: float = 0.4
    st_a: float = np.log(0.1)  # a stroke's duration: ln T = st_a + st_b ln(path) + N(0, st_sd)
    st_b: float = 0.5
    st_sd: float = 0.3
    start_speed: float = 0.2  # share of the peak speed at touch-down (median)
    end_speed: float = 0.3  # at lift
    pause: dict = field(default_factory=lambda: {"between": [np.log(0.11), 0.1, 0.5], "inside": [np.log(0.08), 0.1, 0.5]})
    delayed_pause: list = field(default_factory=lambda: [np.log(0.17), 0.5])  # ln s, sd
    strokes: int = 0
    pulses: int = 0
    snr_db_median: float = 0.0


def _rsd(x) -> float:
    """A robust standard deviation (1.4826 median absolute deviations): bad fits of a few
    strokes would widen a plain one."""
    x = np.asarray(x, float)
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def _fit_one(q):
    r = fit_stroke(q)
    if r is None:
        return None
    P, snr, t, v = r
    return P, snr, float(v[0] / v.max()), float(v[-1] / v.max())


def fit(samples_xh: list[tuple[list[np.ndarray], list[list[int]]]], min_snr: float = 15.0,
        workers: int = 1, cache=None) -> tuple[Fit, dict]:
    """A Fit from samples given as (strokes in x-heights and seconds, spans per character),
    and a report of the fit's quality (aggregates)."""
    Ds, durs, sigs, rates, lags, starts, ends, snrs = [], [], [], [], [], [], [], []
    todo = []
    for strokes, _ in samples_xh:
        for q in strokes:
            path = np.linalg.norm(np.diff(q[:, :2], axis=0), axis=1).sum() if len(q) > 1 else 0
            if path < 0.2 or q[-1, 2] - q[0, 2] < 0.04:
                continue
            todo.append((q, path))
    n_all = len(todo)
    lp = np.log([pth for _, pth in todo])
    lt = np.log([q[-1, 2] - q[0, 2] for q, _ in todo])
    (sa, sb), *_ = np.linalg.lstsq(np.c_[np.ones(len(lp)), lp], lt, rcond=None)
    s_res = lt - (sa + sb * lp)
    if cache is not None and Path(cache).exists():  # the pulses of the same strokes, fitted before
        fits = pickle.loads(Path(cache).read_bytes())
    elif workers > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(workers) as pool:
            fits = pool.map(_fit_one, [q for q, _ in todo], chunksize=16)
    else:
        fits = [_fit_one(q) for q, _ in todo]
    if cache is not None and not Path(cache).exists():
        Path(cache).write_bytes(pickle.dumps(fits))
    if len(fits) != len(todo):
        raise SystemExit("the pulse cache is of other strokes")
    for (q, path), r in zip(todo, fits):
        if r is None:
            continue
        P, snr, v0, v1 = r
        snrs.append(snr)
        if snr < min_snr:
            continue
        T = duration(P[:, 2], P[:, 3])
        Ds += P[:, 0].tolist()
        durs += T.tolist()
        sigs += P[:, 3].tolist()
        rates.append(len(P) / path)
        peak = P[:, 1] + np.exp(P[:, 2] - P[:, 3] ** 2)
        for i in range(1, len(P)):
            lags.append((peak[i] - peak[i - 1]) / ((T[i] + T[i - 1]) / 2))
        starts.append(v0)
        ends.append(v1)
    Ds, durs = np.array(Ds), np.array(durs)
    ok = Ds > 1e-3
    A = np.c_[np.ones(ok.sum()), np.log(Ds[ok])]
    (a, b), *_ = np.linalg.lstsq(A, np.log(durs[ok]), rcond=None)
    resid = np.log(durs[ok]) - A @ [a, b]
    lags = np.array([x for x in lags if x > 1e-3])
    ls = np.log(np.array(sigs))[ok]
    f = Fit(dur_a=float(a), dur_b=float(b), dur_sd=_rsd(resid),
            lsig_mean=float(np.median(ls)), lsig_sd=_rsd(ls),
            dur_lsig_corr=float(np.corrcoef(resid, ls)[0, 1]),
            rate_mean=float(np.median(np.log(rates))), rate_sd=_rsd(np.log(rates)),
            ovl_mean=float(np.median(np.log(lags))), ovl_sd=_rsd(np.log(lags)),
            st_a=float(sa), st_b=float(sb), st_sd=_rsd(s_res),
            start_speed=float(np.median(starts)), end_speed=float(np.median(ends)),
            strokes=len(rates), pulses=len(Ds), snr_db_median=float(np.median(snrs)))
    rep = {"strokes_tried": n_all, "strokes_fitted": len(rates), "pulses": len(Ds),
           "snr_db": {"p10": float(np.percentile(snrs, 10)), "p50": float(np.median(snrs)),
                      "p90": float(np.percentile(snrs, 90))},
           "share_above_min_snr": float(np.mean(np.array(snrs) >= min_snr))}
    return f, rep


def fit_pauses(f: Fit, samples_xh, delayed_flags) -> Fit:
    """The pause model of f from real samples: ln pause on ln(1 + jump), between characters
    and inside one; and the pause before delayed strokes (`delayed_flags`: per sample, per
    stroke, True for a stroke written after a later character's)."""
    rows = {"between": [], "inside": []}
    dl = []
    for (strokes, spans), flags in zip(samples_xh, delayed_flags):
        owner = {}
        for ci, sp in enumerate(spans):
            for j in sp:
                owner.setdefault(j, ci)
        for j in range(1, len(strokes)):
            pause = strokes[j][0, 2] - strokes[j - 1][-1, 2]
            if pause <= 0:
                continue
            jump = float(np.linalg.norm(strokes[j][0, :2] - strokes[j - 1][-1, :2]))
            if flags[j]:
                dl.append(pause)
                continue
            k = "inside" if owner.get(j) == owner.get(j - 1) else "between"
            rows[k].append((np.log(pause), np.log1p(jump)))
    pz = {}
    for k, r in rows.items():
        r = np.array(r)
        A = np.c_[np.ones(len(r)), r[:, 1]]
        (a, b), *_ = np.linalg.lstsq(A, r[:, 0], rcond=None)
        pz[k] = [float(a), float(b), float((r[:, 0] - A @ [a, b]).std())]
    f.pause = pz
    if len(dl) >= 5:
        f.delayed_pause = [float(np.log(dl).mean()), float(np.log(dl).std())]
    return f


# The fit of one writer's prompted samples (the training sessions, 3882 strokes, 10470
# pulses, median fit 23.8 dB; glassine-train dynfit): what the generator draws writers
# around. Aggregate numbers only; one writer, so BROADEN's spread is an assumption.
FIT_ONE_WRITER = {
    "dur_a": -1.912, "dur_b": 0.638, "dur_sd": 0.412, "lsig_mean": -2.283, "lsig_sd": 1.057,
    "dur_lsig_corr": 0.618, "rate_mean": -0.228, "rate_sd": 0.522, "ovl_mean": -0.426,
    "ovl_sd": 0.512, "st_a": -2.189, "st_b": 0.65, "st_sd": 0.277, "start_speed": 0.102,
    "end_speed": 0.399,
    "pause": {"between": [-2.617, 0.454, 0.406], "inside": [-2.634, 0.538, 0.427]},
    "delayed_pause": [-1.657, 0.451], "strokes": 3882, "pulses": 10470, "snr_db_median": 23.789}


# ---------------------------------------------------------------------------- drawing

@dataclass
class WriterTiming:
    """One writer's timing, drawn from a Fit."""
    mu_shift: float = 0.0  # tempo: added to every pulse's ln duration
    sig_scale: float = 1.0
    rate_scale: float = 1.0
    lag_scale: float = 1.0  # overlap of neighbouring pulses
    pause_scale: float = 1.0


# writer-to-writer spread around the fit: assumed, not measured (one writer only)
BROADEN = {"mu_shift": 0.25, "sig_scale": 0.15, "rate_scale": 0.25, "lag_scale": 0.15, "pause_scale": 0.3,
           "residual": 1.25}


def draw_writer(rng, broaden: dict | None = BROADEN) -> WriterTiming:
    if not broaden:
        return WriterTiming()
    return WriterTiming(mu_shift=float(rng.normal(0, broaden["mu_shift"])),
                        sig_scale=float(rng.lognormal(0, broaden["sig_scale"])),
                        rate_scale=float(rng.lognormal(0, broaden["rate_scale"])),
                        lag_scale=float(rng.lognormal(0, broaden["lag_scale"])),
                        pause_scale=float(rng.lognormal(0, broaden["pause_scale"])))


def _corner_cuts(s: np.ndarray, d: np.ndarray, k: int) -> list[float]:
    """k - 1 cut positions (arc length) along a path: its sharpest corners first, then the
    middle of the longest pieces."""
    if k <= 1:
        return []
    cuts = []
    if len(s) >= 3:
        v = np.diff(s, axis=0)
        ang = np.abs(np.arctan2(v[:-1, 0] * v[1:, 1] - v[:-1, 1] * v[1:, 0], (v[:-1] * v[1:]).sum(1)))
        order = np.argsort(-ang)
        for i in order:
            if ang[i] < np.radians(35) or len(cuts) >= k - 1:
                break
            pos = d[i + 1]
            if all(abs(pos - c) > 0.08 * d[-1] for c in cuts) and 0.05 * d[-1] < pos < 0.95 * d[-1]:
                cuts.append(float(pos))
    while len(cuts) < k - 1:
        edges = [0.0] + sorted(cuts) + [float(d[-1])]
        j = int(np.argmax(np.diff(edges)))
        cuts.append((edges[j] + edges[j + 1]) / 2)
    return sorted(cuts)


class Timing:
    def __init__(self, f: Fit, residual: float = BROADEN["residual"]):
        self.f, self.res = f, residual

    def stroke(self, s: np.ndarray, xh: float, w: WriterTiming, rng, t0: float, rate: float = RATE) -> np.ndarray:
        """Points (x, y, t) along path s (em) drawn with pulses: lengths in x-heights (xh em)."""
        f = self.f
        d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(s, axis=0), axis=1))]
        L = d[-1] / xh
        if len(s) < 2 or L <= 1e-4:
            return np.c_[s[:1], [t0]]
        k = max(1, int(round(L * w.rate_scale * np.exp(rng.normal(f.rate_mean, f.rate_sd * self.res)))))
        k = min(k, max(1, len(s) - 1), 16)
        cuts = np.array([0.0] + _corner_cuts(s, d, k) + [d[-1]]) / xh
        D = np.diff(cuts)
        z1, z2 = rng.normal(0, 1, (2, k))
        rho = f.dur_lsig_corr
        z2 = rho * z1 + np.sqrt(max(0.0, 1 - rho ** 2)) * z2
        T = np.exp(f.dur_a + f.dur_b * np.log(np.maximum(D, 1e-3)) + w.mu_shift + f.dur_sd * self.res * z1)
        T = np.clip(T, 0.02, 2.0)
        sig = np.clip(np.exp(f.lsig_mean + f.lsig_sd * self.res * z2) * w.sig_scale, 0.03, 1.2)
        mu = np.log(T / (2 * np.sinh(Z99 * sig)))
        peaks = np.zeros(k)
        lag = np.exp(rng.normal(f.ovl_mean, f.ovl_sd * self.res, k)) * w.lag_scale
        for i in range(1, k):
            peaks[i] = peaks[i - 1] + lag[i] * (T[i] + T[i - 1]) / 2
        on = peaks - np.exp(mu - sig ** 2)
        # the pen is already moving when it touches: the stroke starts once the first pulse
        # has reached the share of its speed the fit gives, and lifts likewise at the end
        tt = np.arange(on.min(), (on + np.exp(mu + 3 * sig)).max(), 1.0 / (rate * 4))
        v = sum(_lognorm(tt, D[i], on[i], mu[i], sig[i]) for i in range(k))
        vmax = v.max()
        a = int(np.argmax(v >= f.start_speed * vmax * rng.lognormal(0, 0.3)))
        tail = np.nonzero(v >= min(0.95, f.end_speed * rng.lognormal(0, 0.3)) * vmax)[0]
        b = int(tail[-1]) if len(tail) else len(v) - 1
        b = max(b, a + 1)
        S = sum(D[i] * _lognorm_cdf(tt, on[i], mu[i], sig[i]) for i in range(k))
        S = (S - S[a]) / max(S[b] - S[a], 1e-9)
        # the stroke's duration as fitted for its path; the pulses give the speed's shape
        dur = float(np.exp(f.st_a + f.st_b * np.log(L) + w.mu_shift + rng.normal(0, f.st_sd * self.res)))
        dur = float(np.clip(dur, 0.02, 3.0))
        scale = dur / max(tt[b] - tt[a], 1e-6)
        ts = tt[a] + np.arange(0, dur, 1.0 / rate) / scale
        if len(ts) < 2:
            ts = np.array([tt[a], tt[b]])
        pos = np.clip(np.interp(ts, tt, S), 0, 1) * d[-1]
        x = np.interp(pos, d, s[:, 0])
        y = np.interp(pos, d, s[:, 1])
        return np.c_[x, y, t0 + (ts - ts[0]) * scale]

    def pause(self, kind: str, jump_xh: float, w: WriterTiming, rng) -> float:
        a, b, sd = self.f.pause[kind]
        return float(np.exp(a + b * np.log1p(jump_xh) + rng.normal(0, sd * self.res)) * w.pause_scale)

    def delayed_pause(self, w: WriterTiming, rng) -> float:
        m, sd = self.f.delayed_pause
        return float(np.exp(m + rng.normal(0, sd * self.res)) * w.pause_scale)


# ---------------------------------------------------------------------------- measuring

def _q(v, ps=(10, 25, 50, 75, 90)):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if not len(v):
        return {"n": 0}
    return {f"p{p}": round(float(np.percentile(v, p)), 4) for p in ps} | {"n": int(len(v))}


def dynamics(samples_xh) -> dict:
    """Speed (x-heights/s), tangential acceleration (x-heights/s^2), stroke duration (s),
    pen-up pauses (s), the speed profile along a stroke (tenths of its path, over its mean)
    and the share of the time below a tenth of the stroke's peak speed, for samples given
    as (strokes in x-heights and seconds, spans). All ink is resampled to RATE first."""
    sp, ac, dur, pz, prof, slow = [], [], [], [], [], []
    for strokes, _ in samples_xh:
        for j, q in enumerate(strokes):
            if j:
                p = q[0, 2] - strokes[j - 1][-1, 2]
                if p > 0:
                    pz.append(p)
            if len(q) < 2 or q[-1, 2] - q[0, 2] < 0.03:
                continue
            t, v = speed(q)
            if len(v) < 5:
                continue
            sp += v.tolist()
            a = np.abs(np.gradient(_smooth(v, 0.01 * RATE))) * RATE
            ac += a.tolist()
            dur.append(q[-1, 2] - q[0, 2])
            slow.append(float(np.mean(v < 0.1 * v.max())))
            path = v.sum() / RATE
            if path > 0.3:
                pos = np.cumsum(v) / RATE / path
                mean = path / (t[-1] - t[0] + 1e-9)
                prof.append([v[(pos > x) & (pos <= x + 0.1)].mean() / mean if ((pos > x) & (pos <= x + 0.1)).any()
                             else np.nan for x in np.arange(0, 1, 0.1)])
    return {"speed_xh_s": _q(sp), "accel_xh_s2": _q(ac), "stroke_s": _q(dur), "pause_s": _q(pz),
            "slow_share": _q(slow), "speed_profile": [round(float(x), 2) for x in np.nanmean(np.array(prof), 0)]
            if prof else []}


def ks(a, b) -> float:
    """Kolmogorov-Smirnov distance of two samples."""
    a, b = np.sort(np.asarray(a, float)), np.sort(np.asarray(b, float))
    x = np.r_[a, b]
    return float(np.max(np.abs(np.searchsorted(a, x, "right") / len(a) - np.searchsorted(b, x, "right") / len(b))))


def asdict_fit(f: Fit) -> dict:
    return asdict(f)
