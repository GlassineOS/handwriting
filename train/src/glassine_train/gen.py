"""The model-4 line generator (spec 001 FR-018, FR-020): text laid out as online ink by
writers, each a set of letter shapes, a spacing, a timing and habits of stroke order.

Shapes stay the fonts' (glassine-synth glyph tables, with the synth's per-letter jitter and
variation), so they are broad. What changes against `glassine_synth.ink.line`:

- **Size** (FR-018): every sample carries its reference heights: `xh`, the writer's letter
  (x-) height, and `line`, the line height of a writing area drawn for it (x-height / line
  drawn per line, XH_LINE), with the baseline `base` in the ink's units. A lone character
  or mark is drawn at its true size on the line, as in a word.
- **Spacing** (FR-020): letters are placed by their ink profiles (spacing.py, as the
  recognizer's spacing.c), each pair at a gap drawn from the writer's spacing: a typical gap,
  closer where the profiles leave open space (`open`), and a spread per pair, so touching,
  overlapping and crossing letters occur; word gaps (box to box) drawn per word around the
  writer's. A writer's typical gap and word gap come from a pool of styles: the fonts' own
  spacing (each font laid out at its advances, measured by profiles) and any letter models
  given (the person's at most one style among many).
- **Timing** (FR-020): sigma-lognormal pulses along each stroke and pen-up pauses
  (timing.py), from a fit to real ink, per writer drawn around it (timing.BROADEN).
- **Delayed strokes** (FR-020): dots of i and j and bars of t, f and barred capitals
  written after the letters that follow: each by the writer's chance (dots around 0.8,
  bars 0.05 to 0.2), at the word's end or (about a tenth) after a later letter of the
  word, several mostly right to left, after their own pause.

A writer is drawn per line (`draw_style`); fonts as in the synth (a font per line or per
word, rarely per letter). A letter model (`LetterWriter`, docs/recognizer.md "Model file")
writes in its own versions, in proportion of their use, at its own spacing.

Samples: {text, strokes [(n, 3) x, y em (y down), t s], spans, xh, line, base, writer}.
"""

from dataclasses import dataclass

import numpy as np
from glassine_synth.ink import XH, Params, _affine, _elastic, _warp, _zones

from . import spacing as S
from .timing import RATE, Fit, Timing, WriterTiming, draw_writer

# x-height over line height of the writing area: lognormal around XH_LINE[0], sd XH_LINE[1],
# clipped to XH_LINE[2:]. One writer's prompted samples measured 0.095; the recognizer's
# fallback assumes 0.2 (docs/recognizer.md, Size); real areas vary more than that.
XH_LINE = (0.14, 0.4, 0.06, 0.35)

DOT_CHARS = "ij"
GAP_SD = 0.12  # median spread of a writer's gaps pair to pair (x-heights)
OVER_MIN = -0.5  # x-heights: the most a bar or hook reaches over the next letter
GAP_MIN = -0.12  # x-heights: letters touch, cross or overlap a little, never sit on each other
BAR_CHARS = "tfAEFHT"


@dataclass
class Style:
    """One writer's spacing and habits (x-heights, seconds, chances)."""
    gap: float = 0.4  # typical letter gap by profiles
    gap_sd: float = 0.22  # spread of a pair's gap around its expected one
    open: float = 0.2  # how much closer letters stand where the profiles leave open space
    word: float = 1.2  # typical word gap (box to box)
    word_sd: float = 0.25  # lognormal sd of a word gap
    dot_delay: float = 0.8
    bar_delay: float = 0.1
    rtl: float = 0.8  # chance a word's delayed strokes go right to left
    mid: float = 0.1  # chance a delayed stroke comes after a later letter, not the word's end
    join: float = 0.4  # chance two close strokes of a word are written in one
    xh_line: float = 0.14
    timing: WriterTiming = None
    one_font: bool = True


# Where the fonts' own spacing is moved to (spec 001 FR-020: spacing as written, not as
# typeset): their typical letter gaps and word gaps, ranked, onto these distributions, so
# each font keeps its place (tight fonts stay the tighter writers) while the spread matches
# real ink broadly (one writer measured: letter gaps median 0.42 x-heights, sd 0.29, 7 %
# touching; word gaps about 1.4). Letter gaps normal (median, sd); word gaps lognormal
# (median, sigma), in x-heights.
CALIB = {"gap": (0.46, 0.09), "word": (1.35, 0.2)}


def calibrate(styles: list[tuple[float, float]], calib: dict | None = None) -> list[tuple[float, float]]:
    """The fonts' (gap, word) styles moved onto CALIB by rank."""
    from scipy.stats import norm
    c = CALIB if calib is None else calib
    n = len(styles)
    if not n:
        return []
    q = lambda v: norm.ppf((np.argsort(np.argsort(v, kind="stable")) + 0.5) / n)
    g = c["gap"][0] + c["gap"][1] * q(np.array([x for x, _ in styles]))
    w = c["word"][0] * np.exp(c["word"][1] * q(np.array([y for _, y in styles])))
    return [(float(a), float(b)) for a, b in zip(g, w)]


def draw_style(rng, pool: list[tuple[float, float]], rate_jitter: bool = True, broaden: dict | None = None) -> Style:
    """A writer: typical letter and word gaps from a style of the pool (each (gap, word) in
    x-heights), the rest drawn broadly."""
    from .timing import BROADEN
    g, w = pool[int(rng.integers(len(pool)))]
    a, sd, lo, hi = XH_LINE
    g = float(g + rng.normal(0, 0.05))
    w = max(w, g + 0.35)  # words stand further apart than letters, whatever the style
    return Style(gap=g, gap_sd=float(np.clip(rng.lognormal(np.log(GAP_SD), 0.35), 0.05, 0.5)),
                 open=float(rng.uniform(0.0, 0.5)), word=float(max(0.3, w * rng.lognormal(0, 0.15))),
                 word_sd=float(rng.uniform(0.1, 0.35)), dot_delay=float(rng.beta(8, 2)),
                 bar_delay=float(rng.uniform(0.03, 0.2)), rtl=float(rng.beta(4, 1.5)), mid=float(rng.uniform(0.03, 0.2)),
                 join=float(rng.uniform(0.15, 0.7)), xh_line=float(np.clip(rng.lognormal(np.log(a), sd), lo, hi)),
                 timing=draw_writer(rng, BROADEN if broaden is None else broaden), one_font=bool(rng.random() < 0.6))


# ---------------------------------------------------------------------------- glyph sources

class FontWriter:
    """Glyphs of the fonts (glyph tables, as data.tables) with the characters each covers."""

    def __init__(self, tables: list[tuple[str, dict]], covers: dict[str, set] | None = None):
        self.tables = tables
        self.covers = [covers.get(f) if covers else None for f, _ in tables]

    def fonts_for(self, text: str) -> list[int]:
        need = set(text) - {" "}
        ok = [i for i, c in enumerate(self.covers) if c is None or need <= c]
        return ok

    def glyph(self, fi: int, ch: str, rng):
        fid, t = self.tables[fi]
        g = t.get(ch)
        return fid, g


class LetterWriter:
    """A person's letter model (docs/recognizer.md, "Model file"): versions in proportion
    of their `use`, strokes in x-heights from the baseline, y up."""

    def __init__(self, rows: list[dict], name: str = "letters"):
        self.name = name
        self.spacing = {}
        self.metrics = {}
        self.chars = {}
        for r in rows:
            if "spacing" in r:
                self.spacing = r["spacing"]
            elif "metrics" in r:
                self.metrics = r["metrics"]
            elif "char" in r and r.get("covered") and r.get("variants"):
                vs = []
                for v in r["variants"]:
                    st = [np.asarray(q, float) for q in v["strokes"] if len(q)]
                    if not st:
                        continue
                    em = [np.c_[q[:, 0] * XH, -q[:, 1] * XH] for q in st]
                    from glassine_synth.glyph import Glyph
                    g = Glyph(r["char"], float(v.get("width", np.ptp(np.concatenate(st)[:, 0]))) * XH, em,
                              [False] * len(em))
                    vs.append((g, max(1.0, float(v.get("use", 1)))))
                if vs:
                    w = np.array([u for _, u in vs])
                    self.chars[r["char"]] = ([g for g, _ in vs], w / w.sum())

    def covers(self, text: str) -> bool:
        return all(c == " " or c in self.chars for c in text)

    def glyph(self, ch: str, rng):
        gs, p = self.chars[ch]
        return self.name, gs[int(rng.choice(len(gs), p=p))]

    habits: dict = {}  # measured delayed strokes of the writer (inkmeasure.delays), if known

    def style(self, rng) -> Style:
        """The writer's own spacing (its pairs, typical gap, open space), its measured habits
        of delaying strokes, the fitted timing without a writer drawn around it."""
        sp, h = self.spacing, self.habits
        a, sd, lo, hi = XH_LINE
        return Style(gap=float(sp.get("letter", 0.4)), gap_sd=0.15, open=float(sp.get("open", 0.0)),
                     word=float(sp.get("word", self.metrics.get("word_spacing", 1.2))), word_sd=0.2,
                     dot_delay=float(h.get("dot_delayed") or 0.8), bar_delay=float(h.get("bar_delayed") or 0.1),
                     rtl=float(h.get("right_to_left") or 0.8), mid=float(h.get("mid_word") or 0.1),
                     xh_line=float(np.clip(rng.lognormal(np.log(a), sd), lo, hi)), timing=WriterTiming())


# ---------------------------------------------------------------------------- delayable strokes

def delayable(ch: str, strokes: list[np.ndarray]) -> list[str | None]:
    """Per stroke of a glyph (em, y down, baseline 0): 'dot', 'bar' or None. A dot: a small
    stroke of an i or j above the x-height; a bar: a flat stroke of a barred letter that is
    not its only stroke."""
    out = []
    for s in strokes:
        w, h = np.ptp(s[:, 0]), np.ptp(s[:, 1])
        if ch in DOT_CHARS and max(w, h) < 0.15 and s[:, 1].max() < -0.4:
            out.append("dot")
        elif ch in BAR_CHARS and len(strokes) > 1 and w > 2.5 * h and w > 0.12:
            out.append("bar")
        else:
            out.append(None)
    return out


# ---------------------------------------------------------------------------- layout

def font_spacing(table: dict, bigrams: list[str]) -> tuple[float, float, float]:
    """A font's own spacing in x-heights: the median x-height-band gap of `bigrams` laid out at the
    font's advances, the median open space, and its word gap (box to box: the space's
    advance plus the letters' side bearings)."""
    prof, box = {}, {}
    for c, g in table.items():
        if g.strokes:
            prof[c] = S.of_strokes(g.strokes, XH)
            lo, hi = prof[c].box()
            box[c] = (lo, hi, g.advance / XH)
    gaps, opens = [], []
    for bg in bigrams:
        a, b = bg
        if a in prof and b in prof:
            h = S.guard_gap(prof[a], prof[b], table[a].advance / XH)
            gaps.append(h if np.isfinite(h) else S.gap(prof[a], prof[b], table[a].advance / XH))
            opens.append(S.open_space(prof[a], prof[b]))
    sp = table.get(" ")
    space = (sp.advance / XH) if sp is not None else 0.6
    rb = np.median([adv - hi for lo, hi, adv in box.values()])
    lb = np.median([lo for lo, hi, adv in box.values()])
    return float(np.median(gaps)), float(np.median(opens)), float(space + rb + lb)


class Generator:
    def __init__(self, fonts: FontWriter | None, fit: Fit, pool: list[tuple[float, float]], open0: float = 0.3,
                 params: Params | None = None, residual: float | None = None):
        self.fonts, self.pool, self.open0 = fonts, pool, open0
        self.p = params or Params(warp=0.025, zones=0.2, asc_scale=1.1)
        self.timing = Timing(fit) if residual is None else Timing(fit, residual)

    def line(self, text: str, rng, style: Style | None = None, letters: LetterWriter | None = None,
             points: bool = False) -> dict:
        """One line of ink. points: also each point's character (`point_owner`: per stroke, the
        character index of each point, -1 on a pen-down join between two characters) and the
        joins (`joins`: per stroke, [from arc, to arc, character before, character after] in
        em along the stroke as written, before noise); the same ink either way."""
        p = self.p
        st = style or (letters.style(rng) if letters else draw_style(rng, self.pool))
        slant = rng.normal(p.slant_mean, p.slant) if letters is None else rng.normal(0, p.slant / 2)
        tilt = np.radians(rng.normal(0, p.tilt))
        ph, freq = rng.uniform(0, 2 * np.pi), rng.uniform(0.1, 0.4)
        asc = desc = width = 1.0
        if p.zones > 0 and letters is None:
            asc, desc = rng.lognormal(0, p.zones, 2)
            width = rng.lognormal(0, p.zones / 2)
            asc, desc = asc * p.asc_scale, desc * p.desc_scale
        fonts_ok = self.fonts.fonts_for(text) if letters is None else []
        if letters is None and not fonts_ok:
            raise ValueError("no font covers the line")
        pick = lambda: fonts_ok[int(rng.integers(len(fonts_ok)))]
        fi = pick() if letters is None else None

        # glyphs, transformed, at x = 0
        glyphs = []  # (char index, word, [strokes], [kind], profile, font)
        word = 0
        for ci, ch in enumerate(text):
            if ch == " ":
                word += 1
                if letters is None and not st.one_font:
                    fi = pick()
                continue
            if letters is None:
                if rng.random() < p.font_per_letter / 2:
                    fi = pick()
                fid, g = self.fonts.glyph(fi, ch, rng)
            else:
                fid, g = letters.glyph(ch, rng)
            if g is None or not g.strokes:
                glyphs.append((ci, word, [], [], None, fid, float("nan")))
                continue
            strokes = [q[:, :2] for q in g.strokes]
            kinds = delayable(ch, strokes)
            if letters is None:
                strokes = [_zones(q * [width, 1], asc, desc) for q in strokes]
                if p.warp > 0:
                    strokes = _warp(strokes, rng, p.warp)
            centre = np.array([g.advance * width / 2, -0.35])
            out = []
            for q in strokes:
                q = _elastic(_affine(q, rng, p, centre), rng, p)
                q = q.copy()
                q[:, 0] += -slant * q[:, 1]
                out.append(q)
            glyphs.append((ci, word, out, kinds, S.of_strokes(out, XH), fid,
                           S.middle([np.c_[q[:, 0] / XH, -q[:, 1] / XH] for q in out])))

        # placement by profiles: in a word at the drawn pair gap, words at the drawn word gap
        placed = []  # (char index, word, strokes moved, kinds, font)
        prev, prev_word, word_right = None, None, None
        word_profs = []
        for ci, w, strokes, kinds, prof, fid, mid in glyphs:
            if prof is None:
                continue
            if prev is not None and w == prev_word:
                pch, pprof, _, pmid = prev
                if letters is not None:
                    # where the letter model's spacing puts the pair (by middles, guarded)
                    dx = S.place(letters.spacing, text[pch], text[ci], pprof, prof, pmid, mid)
                    dx += rng.normal(0, st.gap_sd)
                else:
                    # the writer's gap in the x-height band (bars and hooks above or below
                    # may reach over the next letter), as the letter model's guard measures
                    g = st.gap - st.open * (S.open_space(pprof, prof) - self.open0)
                    g += rng.normal(0, st.gap_sd)
                    g = float(np.clip(g, GAP_MIN, 1.2))
                    have = S.guard_gap(pprof, prof)
                    dx = g - (have if np.isfinite(have) else S.gap(pprof, prof))
                dx = max(dx, OVER_MIN - S.gap(pprof, prof))
                for ep in word_profs[:-1]:  # never far into a letter before the last one
                    dx = max(dx, GAP_MIN - S.gap(ep, prof))
            elif prev is not None:
                lo, _ = prof.box()
                dx = word_right + st.word * rng.lognormal(0, st.word_sd) - lo
            else:
                dx = -prof.box()[0]
            moved = [q + [dx * XH, 0.0] for q in strokes]
            mp = prof.shifted(dx)
            word_profs = word_profs + [mp] if (prev is not None and w == prev_word) else [mp]
            placed.append((ci, w, moved, kinds, fid))
            right = mp.box()[1]
            word_right = right if (w != prev_word or word_right is None) else max(word_right, right)
            prev, prev_word = (ci, mp, fid, mid + dx), w
        # baseline wander
        for ci, w, strokes, kinds, fid in placed:
            for q in strokes:
                q[:, 1] += p.drift * np.sin(freq * q[:, 0].mean() * 2 * np.pi + ph)

        # stroke order: loops sometimes reversed; dots and bars delayed
        seq = []  # (char index, stroke, delayed)
        by_word = {}
        for ci, w, strokes, kinds, fid in placed:
            by_word.setdefault(w, []).append((ci, strokes, kinds))
        for w in sorted(by_word):
            letters_w = by_word[w]
            held = []  # (after letter position, char index, stroke)
            for pos, (ci, strokes, kinds) in enumerate(letters_w):
                for q, k in zip(strokes, kinds):
                    closed = np.linalg.norm(q[0] - q[-1]) < 0.04
                    if closed and rng.random() < p.reverse_loop:
                        q = q[::-1]
                    chance = st.dot_delay if k == "dot" else st.bar_delay if k == "bar" else 0.0
                    if pos < len(letters_w) - 1 and chance and rng.random() < chance:
                        after = len(letters_w) - 1
                        if rng.random() < st.mid and pos + 1 < len(letters_w) - 1:
                            after = int(rng.integers(pos + 1, len(letters_w) - 1))
                        held.append((after, ci, q))
                        continue
                    seq.append((ci, q, False))
                mine = [h for h in held if h[0] == pos]
                if mine:
                    if len(mine) > 1 and rng.random() < st.rtl:
                        mine = mine[::-1]
                    seq += [(ci2, q2, True) for _, ci2, q2 in mine]
                    held = [h for h in held if h[0] != pos]
            # a dot on the word's last letter is written with it (nothing follows to delay it)

        # pen-down joins inside a word, never of a delayed stroke
        word_of = {ci: w for ci, w, *_ in placed}
        merged = []
        pieces = []  # per merged stroke: (points, character or (before, after) for a join)
        for ci, q, dl in seq:
            if merged and not dl and not merged[-1][2]:
                pq, cis = merged[-1][1], merged[-1][3]
                if (word_of[cis[-1]] == word_of[ci] and np.linalg.norm(q[0] - pq[-1]) < 0.7 * XH / 0.45
                        and rng.random() < st.join):
                    n = max(2, int(np.linalg.norm(q[0] - pq[-1]) / 0.02))
                    bridge = np.linspace(pq[-1], q[0], n)[1:-1]
                    merged[-1] = (merged[-1][0], np.vstack([pq, bridge, q]), False,
                                  cis + ([ci] if ci not in cis else []))
                    # (a join inside one character is that character's ink)
                    pieces[-1] += [(len(bridge), (cis[-1], ci) if cis[-1] != ci else ci), (len(q), ci)]
                    continue
            merged.append((ci, q, dl, [ci]))
            pieces.append([(len(q), ci)])

        # timing, stray lifts, tilt, noise
        rot = np.array([[np.cos(tilt), -np.sin(tilt)], [np.sin(tilt), np.cos(tilt)]])
        out, owner = [], []
        powner, joins = [], []
        t, last, last_ci = 0.0, None, None
        for (ci, q, dl, cis), pcs in zip(merged, pieces):
            q = q @ rot.T
            parts = [q]
            cuts = [(0, len(q))]
            if len(q) > 20 and rng.random() < p.extra_lift:
                k = int(rng.integers(5, len(q) - 5))
                parts = [q[:k + 1], q[k:]]
                cuts = [(0, k + 1), (k, len(q))]
            if points:  # each path point's character, and the arc where each piece starts
                lab = np.concatenate([np.full(n, -1 if isinstance(c, tuple) else c) for n, c in pcs])
                ends = np.cumsum([n for n, _ in pcs])
            for part, (i0, i1) in zip(parts, cuts):
                if last is not None:
                    jump = float(np.linalg.norm(part[0] - last)) / XH
                    if dl:
                        t += self.timing.delayed_pause(st.timing, rng)
                    else:
                        t += self.timing.pause("inside" if ci == last_ci else "between", jump, st.timing, rng)
                arr = self.timing.stroke(part, XH, st.timing, rng, t)
                if points:
                    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(part, axis=0), axis=1))]
                    pos = self.timing.last_pos
                    # a point between path points i and i+1 is the character of both, or a join
                    k = np.clip(np.searchsorted(d, pos, side="right") - 1, 0, len(part) - 1)
                    k2 = np.minimum(k + 1, len(part) - 1)
                    near = np.where(pos - d[k] <= d[k2] - pos, k, k2)
                    powner.append(lab[i0:i1][near].astype(int))
                    jn = []
                    start = 0
                    for (n, c), e in zip(pcs, ends):
                        if isinstance(c, tuple):
                            a, b = start - 1 - i0, e - i0  # the bridge runs from the path point before it to the one after
                            if 0 <= a and b < len(part):
                                jn.append([float(d[a]), float(d[b]), int(c[0]), int(c[1])])
                        start = e
                    joins.append(jn)
                arr[:, :2] += rng.normal(0, p.noise, arr[:, :2].shape)
                t = float(arr[-1, 2])
                out.append(arr)
                owner.append(cis)
                last, last_ci = part[-1], cis[-1]
        spans = [[] for _ in text]
        for si, cis in enumerate(owner):
            for c in cis:
                spans[c].append(si)
        fonts = {fid for *_, fid in placed}
        res = {"text": text, "strokes": out, "spans": spans, "xh": XH, "line": XH / st.xh_line, "base": 0.0,
               "writer": letters.name if letters else (sorted(fonts)[0] if len(fonts) == 1 else "mixed"),
               "rate": RATE}
        if points:
            res["point_owner"], res["joins"] = powner, joins
        return res
