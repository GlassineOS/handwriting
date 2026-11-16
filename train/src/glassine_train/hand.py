"""A person's hand as a font: glyphs cut from their prompted samples, so any text can be
rendered in their own letters by glassine-synth (`line()`), as one more font.

Glyphs come only from whole strokes: a single-character sample gives its strokes, and in a
word every character whose strokes are its alone (spans; a stroke shared with a neighbour,
as in joined writing, keeps the character out). Each glyph keeps the pen's points and
timing. Positions are normalised by the writing area: the baseline is the area's guide
the ink sits on (the guides are at two thirds of each line), and the scale makes the
writer's x-height the generator's (0.45 em). A character gets every shape the person wrote
for it; a table holds one shape per character, so `tables()` returns several tables with
the shapes drawn at random, and rendering picks a table per letter.

Joins between letters are the generator's, not the person's, so spliced ink is weighted
below real ink in training. A later step could cut letters inside shared strokes by a
CTC alignment of the points; this module does not.
"""

import numpy as np
from glassine_synth.ink import XH
from glassine_synth.glyph import Glyph
from glassine_synth.stats import measure

from .real import usable


def _baseline(s: dict, ys: np.ndarray) -> float:
    ar = s["area"]
    line, y0 = ar["line"], ar["box"][1]
    first = y0 + line * 2 / 3
    k = int(np.ceil((np.median(ys) - first) / line))
    return first + max(k, 0) * line


def shapes(samples: list[dict]) -> tuple[dict[str, list[Glyph]], dict]:
    """{char: [Glyph]} from a person's samples, and a report (counts, scale, spacing)."""
    samples = [s for s in samples if usable(s) and s.get("area", {}).get("line")]
    xh = measure(samples)["writer_x_height_units"]
    k = XH / xh
    out: dict[str, list[Glyph]] = {}
    gaps = []
    for s in samples:
        allys = np.concatenate([np.asarray(q, float)[:, 1] for q in s["strokes"] if len(q)])
        base = _baseline(s, allys)
        cnt = {}
        for sp in s["spans"]:
            for j in sp:
                cnt[j] = cnt.get(j, 0) + 1
        prev_right = None
        for c, sp in zip(s["text"], s["spans"]):
            own = c != " " and sp and all(cnt[j] == 1 for j in sp)
            if not own:
                prev_right = None
                continue
            qs = [np.asarray(s["strokes"][j], float)[:, :3] for j in sp if len(s["strokes"][j])]
            if not qs:
                continue
            pts = np.concatenate(qs)
            x0, x1 = pts[:, 0].min(), pts[:, 0].max()
            if prev_right is not None:
                gaps.append((x0 - prev_right) * k)
            prev_right = x1
            strokes = [np.c_[(q[:, 0] - x0) * k, (q[:, 1] - base) * k, q[:, 2] - q[0, 2]] for q in qs]
            out.setdefault(c, []).append(Glyph(c, (x1 - x0) * k, strokes, [False] * len(strokes)))
    # a shape far from the character's others is more likely a slip than a style
    for c, gs in out.items():
        if len(gs) >= 3:
            h = np.array([np.ptp(np.concatenate(g.strokes)[:, 1]) for g in gs])
            med = np.median(h)
            out[c] = [g for g, x in zip(gs, h) if 0.4 * med <= x <= 2.5 * med] or gs
    bearing = float(np.clip(np.median(gaps), 0.02, 0.3)) if gaps else 0.1
    for gs in out.values():
        for g in gs:
            g.advance += bearing
    rep = {"samples": len(samples), "characters": len(out), "shapes": sum(len(v) for v in out.values()),
           "x_height_units": xh, "letter_gap_em": bearing}
    return out, rep


def tables(sh: dict[str, list[Glyph]], n: int, seed: int = 0) -> list[tuple[str, dict]]:
    """n glyph tables (font id, {char: Glyph}), one random shape per character each."""
    rng = np.random.default_rng(seed)
    return [(f"hand{i}", {c: gs[int(rng.integers(len(gs)))] for c, gs in sh.items()}) for i in range(n)]


def covered(text: str, sh: dict) -> bool:
    return all(c == " " or c in sh for c in text)
