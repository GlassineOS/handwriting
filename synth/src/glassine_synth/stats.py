"""Measures of online ink, the same for real samples and generated ones, so the generator's
parameters can be checked against real writing. Only aggregate numbers come out.

Lengths are in x-heights (the writer's or the font's), times in seconds, so ink in the
pen's units and ink in em compare directly. Per sample:

- baseline and x-height from its x-height letters (a c e m n o r s u v w x z) that own
  their strokes (spans); the writer's x-height is the median over samples;
- zones: heights of ascender letters (b d f h k l t), depths of descender letters (g j p q
  y) below the baseline, capitals and digits, all in x-heights (and in lines, when the
  sample has a writing area);
- speed: pen speed between points (pen down); its profile along a stroke (ten equal parts
  of the stroke's path, speed over the stroke's mean speed) and speed pulses per x-height
  of path (maxima of the smoothed speed);
- slant: the median lean of downstrokes (degrees, positive = to the right);
- pen-up gaps: pause between strokes (s) and the jump's length, fitted as pause = a + jump / v,
  inside a character and between characters;
- strokes per character.
"""

import json
from pathlib import Path

import numpy as np

XLET = set("acemnorsuvwxz")
ASC = set("bdfhklt")
DESC = set("gjpqy")


def _owned(s):
    """(char, [stroke arrays]) for characters whose strokes are theirs alone."""
    cnt = {}
    for sp in s["spans"]:
        for j in sp:
            cnt[j] = cnt.get(j, 0) + 1
    out = []
    for c, sp in zip(s["text"], s["spans"]):
        if c != " " and sp and all(cnt[j] == 1 for j in sp):
            out.append((c, [np.asarray(s["strokes"][j], float)[:, :3] for j in sp if len(s["strokes"][j])]))
    return [(c, q) for c, q in out if q]


def _smooth(v, k):
    if len(v) < k or k < 2:
        return v
    w = np.ones(k) / k
    return np.convolve(np.pad(v, (k // 2, k - 1 - k // 2), mode="edge"), w, mode="valid")


def measure(samples: list[dict]) -> dict:
    """Aggregate measures of a list of samples (sample format v1)."""
    per = []  # per-sample x-height and baseline
    rows = {"asc": [], "desc": [], "cap": [], "digit": [], "xh_line": [], "asc_line": [],
            "desc_line": [], "box_line": []}
    for s in samples:
        own = _owned(s)
        xl = [(np.concatenate(q)[:, 1].min(), np.concatenate(q)[:, 1].max()) for c, q in own if c in XLET]
        if not xl:
            per.append(None)
            continue
        base = float(np.median([b for _, b in xl]))
        xh = float(np.median([b - t for t, b in xl]))
        per.append((xh, base) if xh > 0 else None)
    xs = [p[0] for p in per if p]
    writer_xh = float(np.median(xs)) if xs else float("nan")
    speed_pts, prof, pulses, slants, gaps_in, gaps_between, spc = [], [], [], [], [], [], []
    dts = []
    stroke_dur = []
    for s, p in zip(samples, per):
        xh = p[0] if p and len(s["text"]) > 2 else writer_xh  # short samples: the writer's
        base = p[1] if p else None
        strokes = [np.asarray(q, float)[:, :3] for q in s["strokes"] if len(q)]
        if not strokes or not xh > 0:
            continue
        chars = [c for c in s["text"] if c != " "]
        spc.append((len(strokes), len(chars)))
        # zones
        if base is not None:
            for c, q in _owned(s):
                y = np.concatenate(q)[:, 1]
                if c in ASC:
                    rows["asc"].append((base - y.min()) / xh)
                if c in DESC:
                    rows["desc"].append((y.max() - base) / xh)
                if c.isupper():
                    rows["cap"].append((base - y.min()) / xh)
                if c.isdigit():
                    rows["digit"].append((base - y.min()) / xh)
        ar = s.get("area")
        if ar and ar.get("line"):
            L = ar["line"]
            pts = np.concatenate(strokes)
            rows["box_line"].append(np.ptp(pts[:, 1]) / L)
            if p and len(s["text"]) > 2:
                rows["xh_line"].append(p[0] / L)
        # speed, profile, pulses, slant
        for q in strokes:
            if len(q) < 4:
                continue
            d = np.linalg.norm(np.diff(q[:, :2], axis=0), axis=1) / xh
            dt = np.diff(q[:, 2])
            ok = dt > 0
            dts += dt[ok].tolist()
            if ok.sum() < 3 or q[-1, 2] - q[0, 2] <= 0:
                continue
            v = d[ok] / dt[ok]
            speed_pts += v.tolist()
            stroke_dur.append(q[-1, 2] - q[0, 2])
            # smooth over ~15 ms whatever the rate
            rate = 1.0 / np.median(dt[ok])
            vs = _smooth(v, max(1, int(round(0.015 * rate))))
            path = d.sum()
            if path > 0.3:
                pos = np.cumsum(d[ok]) / path
                mean = path / (q[-1, 2] - q[0, 2])
                prof.append([vs[(pos > a) & (pos <= a + 0.1)].mean() / mean if ((pos > a) & (pos <= a + 0.1)).any()
                             else np.nan for a in np.arange(0, 1, 0.1)])
                peaks = np.sum((vs[1:-1] > vs[:-2]) & (vs[1:-1] >= vs[2:]) & (vs[1:-1] > 0.3 * vs.max()))
                pulses.append((peaks, path))
            dx, dy = np.diff(q[:, 0]), np.diff(q[:, 1])
            down = (dy > 0) & (dy > 1.5 * np.abs(dx))
            if down.sum() >= 3:
                slants.append((np.degrees(np.arctan2(-dx[down], dy[down])), np.hypot(dx, dy)[down]))
        # pen-up gaps
        owner = {}
        for ci, sp in enumerate(s["spans"]):
            for j in sp:
                owner.setdefault(j, ci)
        full = [np.asarray(q, float)[:, :3] for q in s["strokes"]]
        for j in range(1, len(full)):
            if not len(full[j]) or not len(full[j - 1]):
                continue
            pause = full[j][0, 2] - full[j - 1][-1, 2]
            jump = np.linalg.norm(full[j][0, :2] - full[j - 1][-1, :2]) / xh
            (gaps_in if owner.get(j) == owner.get(j - 1) else gaps_between).append((pause, jump))
    out = {"samples": len(samples), "writer_x_height_units": writer_xh}

    def q(v, ps=(10, 25, 50, 75, 90)):
        v = np.asarray(v, float)
        v = v[np.isfinite(v)]
        return {f"p{p}": round(float(np.percentile(v, p)), 3) for p in ps} | {"n": int(len(v))} if len(v) else {"n": 0}

    out["zones_xh"] = {k: q(rows[k]) for k in ("asc", "desc", "cap", "digit")}
    if rows["xh_line"]:
        out["zones_line"] = {"x_height": q(rows["xh_line"]), "ink_height": q(rows["box_line"])}
    out["speed_xh_per_s"] = q(speed_pts)
    out["stroke_seconds"] = q(stroke_dur)
    out["pen_rate_hz"] = round(float(1 / np.median(dts)), 1) if dts else None
    if prof:
        out["speed_profile"] = [round(float(x), 2) for x in np.nanmean(np.array(prof), 0)]
    if pulses:
        pk = np.array(pulses, float)
        out["pulses_per_xh"] = round(float(pk[:, 0].sum() / pk[:, 1].sum()), 3)
    if slants:
        per_s = [float(np.average(a, weights=w)) for a, w in slants]
        out["slant_deg"] = q(per_s)
    for name, g in (("gaps_in_char", gaps_in), ("gaps_between_chars", gaps_between)):
        if len(g) >= 5:
            g = np.array(g)
            A = np.c_[np.ones(len(g)), g[:, 1]]
            a, b = np.linalg.lstsq(A, g[:, 0], rcond=None)[0]
            out[name] = {"pause_s": q(g[:, 0]), "jump_xh": q(g[:, 1]),
                         "fit_a_s": round(float(a), 3), "fit_v_xh_per_s": round(float(1 / b), 2) if b > 0 else None}
    if spc:
        sp = np.array(spc, float)
        out["strokes_per_char"] = round(float(sp[:, 0].sum() / sp[:, 1].sum()), 3)
        one = sp[sp[:, 1] == 1]
        if len(one):
            out["strokes_single_chars"] = round(float(one[:, 0].mean()), 3)
    return out


def load(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.startswith("{")]
