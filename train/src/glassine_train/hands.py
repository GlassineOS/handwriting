"""Text written in real people's hands (spec 001 FR-020): letter models (gen.LetterWriter;
for BRUSH's writers brushletters.py makes them) write training lines with the generator
(gen.Generator), one hand per line or, in a share of the lines, a hand per word.

**A hand** is a letter model and what the generator needs beside it, drawn once per hand
from its seed so a hand writes alike in all its lines:
- *joins*: `Style.join` is the chance that two close strokes of a word are written in one;
  it is set so the hand's lines join about as many neighbouring letters as its writer did
  (`join_rate`, measured on the writer's ink): calibrated by writing a few lines with every
  close pair joined (`eligible`). The joining stroke is drawn (gen.connector: curved in
  `CURVE` of the joins), as the models carry none of their own.
- *delayed strokes*: i's and j's dots and bars of t, f and barred capitals written after the
  word, at chances drawn around the generator's (gen.draw_style's priors), per hand;
- *timing*: the generator's fitted timing with a writer drawn around it per hand
  (timing.draw_writer);
- *shape*: each letter written with a smooth displacement of its own (`WARP`): a version is
  the average of the writer's letters like it, smoother than any of them.

**Characters a hand lacks** (no versions: digits, a capital it wrote too seldom) come from a
donor font (gen.FontWriter), drawn per line from the fonts that have them all. A line takes
a donor only for at most `DONOR_MAX` of its characters (else another text is drawn); its
`writer` says so (`hand-N+donor`).

**Mixed lines**: each word in one of 2-3 hands (the line's own and others drawn), at the
same x-height (the models are in x-heights); `writer` `hands-N,M,...`.

Lines carry the writer as `hand-N` (N the hand's place in its list), never a person's
number. What hands write is theirs: it stays where their letter models are (private).
"""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from glassine_synth.glyph import Glyph
from glassine_synth.ink import _warp

from . import gen
from .timing import BROADEN, draw_writer

CURVE = 0.6            # share of drawn joins that bow (the rest straight)
WARP = 0.03            # per letter written: a smooth displacement (glassine_synth.ink._warp, em),
                       # as the versions are averages of the writer's letters
DONOR_MAX = 0.15       # most characters of a line (not spaces) a donor writes
MIXED_HANDS = (2, 3)   # hands in a mixed line, at most


class Hand:
    """A letter model with its drawn habits and join chance."""

    def __init__(self, lw: gen.LetterWriter, n: int, join_rate: float, seed: int):
        self.lw, self.n, self.join_rate = lw, n, join_rate
        rng = np.random.default_rng([seed, n])
        self.habits = {"dot_delayed": float(rng.beta(8, 2)), "bar_delayed": float(rng.uniform(0.03, 0.2)),
                       "right_to_left": float(rng.beta(4, 1.5)), "mid_word": float(rng.uniform(0.03, 0.2))}
        self.timing = draw_writer(rng, BROADEN)
        self.join = float(min(1.0, join_rate))  # until calibrate()

    def style(self, rng) -> gen.Style:
        self.lw.habits = self.habits
        return replace(self.lw.style(rng), join=self.join, curve=CURVE, timing=self.timing)

    def calibrate(self, g: gen.Generator, texts: list[str], seed: int = 0) -> float:
        """Style.join so lines join about join_rate of neighbouring letters: the share of them
        joined when every close pair is (`eligible`), measured on `texts`."""
        rng = np.random.default_rng([seed, self.n, 1])
        pairs = joined = 0
        for t in texts:
            if not self.lw.covers(t):
                continue
            st = replace(self.style(rng), join=1.0, bar_lead=0.0)
            s = g.line(t, rng, style=st, letters=self.lw)
            p, j = joined_pairs(s)
            pairs, joined = pairs + p, joined + j
        self.eligible = joined / pairs if pairs else 0.0
        self.join = float(min(1.0, self.join_rate / self.eligible)) if self.eligible > 0 else 0.0
        return self.join


def joined_pairs(s: dict) -> tuple[int, int]:
    """(neighbouring letters of a word, those one stroke writes both of) of a line."""
    text, spans = s["text"], s["spans"]
    pairs = joined = 0
    for k in range(len(text) - 1):
        if text[k] != " " and text[k + 1] != " ":
            pairs += 1
            joined += bool(set(spans[k]) & set(spans[k + 1]))
    return pairs, joined


class LineWriter:
    """One line's writer for gen.Generator.line (its glyph_at / spacing_at hooks): each
    character in its word's hand, or the donor font where that hand has no versions."""

    def __init__(self, text: str, hands: list[Hand], word_hand: list[int], donor: gen.FontWriter | None,
                 donor_font: int | None, warp: float = WARP):
        self.text, self.hands, self.donor, self.donor_font, self.warp = text, hands, donor, donor_font, warp
        self.of = []
        w = 0
        for ch in text:
            if ch == " ":
                w += 1
            self.of.append(word_hand[min(w, len(word_hand) - 1)])
        first = hands[word_hand[0]]
        self.name = f"hand-{first.n}"
        used = sorted({hands[i].n for i in word_hand})
        if len(used) > 1:
            self.name = "hands-" + ",".join(str(x) for x in [first.n] + [u for u in used if u != first.n])
        self.donated = sum(1 for ci, ch in enumerate(text) if ch != " " and ch not in hands[self.of[ci]].lw.chars)
        if self.donated:
            self.name += "+donor"

    def glyph_at(self, ci: int, ch: str, rng):
        lw = self.hands[self.of[ci]].lw
        if ch in lw.chars:
            fid, g = lw.glyph(ch, rng)
            if self.warp > 0 and g is not None and g.strokes:
                g = Glyph(g.char, g.advance, _warp([q[:, :2] for q in g.strokes], rng, self.warp), list(g.delayable))
            return fid, g
        if self.donor is None or self.donor_font is None:
            return lw.name, None
        return self.donor.glyph(self.donor_font, ch, rng)

    def spacing_at(self, ci: int) -> dict:
        return self.hands[self.of[ci]].lw.spacing

    @property
    def spacing(self) -> dict:
        return self.hands[self.of[0]].lw.spacing


def lacking(text: str, lw: gen.LetterWriter) -> set[str]:
    return {c for c in text if c != " " and c not in lw.chars}


def plan_line(text: str, hands: list[Hand], h: int, mixed: bool, donor: gen.FontWriter | None, rng):
    """A LineWriter for text in hand h (mixed: its words in 2-3 hands), or None when the
    text needs a donor for more than DONOR_MAX of its characters or no donor font has them."""
    words = text.split(" ")
    if mixed and len([w for w in words if w]) >= 2:
        k = int(rng.integers(MIXED_HANDS[0], MIXED_HANDS[1] + 1))
        others = [int(x) for x in rng.choice([i for i in range(len(hands)) if i != h], size=min(k - 1, len(hands) - 1),
                                             replace=False)]
        pool = [h] + others   # the words take the hands in turn, the line's own first
        word_hand = [pool[i % len(pool)] for i in range(len(words))]
    else:
        word_hand = [h] * len(words)
    need = set()
    nchars = 0
    for w, hi in zip(words, word_hand):
        need |= lacking(w, hands[hi].lw)
        nchars += len(w)
    donated = sum(1 for w, hi in zip(words, word_hand) for c in w if c not in hands[hi].lw.chars)
    font = None
    if need:
        if donor is None or donated > DONOR_MAX * max(1, nchars):
            return None
        ok = [i for i, c in enumerate(donor.covers) if c is None or need <= c]
        if not ok:
            return None
        font = ok[int(rng.integers(len(ok)))]
    return LineWriter(text, hands, word_hand, donor, font)


def load_hands(path: Path, seed: int) -> list[Hand]:
    """The hands of a hands list (brushletters.build_all's hands.json)."""
    man = json.loads(Path(path).read_text())
    out = []
    for e in man["hands"]:
        rows = [json.loads(x) for x in Path(e["letters"]).read_text().splitlines() if x.strip()]
        out.append(Hand(gen.LetterWriter(rows, f"hand-{e['hand']}"), int(e["hand"]), float(e.get("join_rate", 0.0)), seed))
    return out


_W = None  # (generator, hands, donor) for the workers, set before they fork


def _calibrate(a):
    n, texts, seed = a
    g, hands, _ = _W
    return n, hands[n].calibrate(g, texts, seed), hands[n].eligible


def _render(job):
    text, kind, seed, h, mixed = job
    g, hands, donor = _W
    rng = np.random.default_rng(seed)
    lw = plan_line(text, hands, h, mixed, donor, rng)
    if lw is None and mixed:
        lw = plan_line(text, hands, h, False, donor, rng)
    if lw is None:
        return None, kind, seed
    for _ in range(3):
        try:
            return g.line(text, rng, style=hands[h].style(rng), letters=lw), kind, seed
        except ValueError:
            continue
    return None, kind, seed


def calibrate_all(g: gen.Generator, hands: list[Hand], texts: list[str], workers: int, seed: int = 0):
    """Each hand's join chance (Hand.calibrate) on the texts it covers."""
    import multiprocessing as mp
    global _W
    _W = (g, hands, None)
    with mp.get_context("fork").Pool(workers) as pool:
        for n, j, e in pool.imap_unordered(_calibrate, [(i, texts, seed) for i in range(len(hands))]):
            hands[n].join, hands[n].eligible = j, e


def render(out: Path, jobs: list[tuple], g: gen.Generator, hands: list[Hand], donor: gen.FontWriter | None,
           workers: int, meta: dict) -> dict:
    """An ink set (inkstore.py) of jobs (text, kind, seed, hand, mixed)."""
    import multiprocessing as mp
    import time
    from .inkstore import Writer
    global _W
    if (out / "meta.json").exists():
        return json.loads((out / "meta.json").read_text())
    _W = (g, hands, donor)
    w = Writer(out)
    t0, failed, donated, mixed = time.time(), 0, 0, 0
    with mp.get_context("fork").Pool(workers) as pool:
        for smp, kind, seed in pool.imap(_render, jobs, chunksize=32):
            if smp is None:
                failed += 1
                continue
            donated += "+donor" in smp["writer"]
            mixed += smp["writer"].startswith("hands-")
            w.add(smp, kind, seed)
    return w.close({**meta, "failed": failed, "donor_lines": donated, "mixed_lines": mixed,
                    "seconds": round(time.time() - t0)})
