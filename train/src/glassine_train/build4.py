"""The next base model's data set (spec 001 FR-018, FR-020): ink sets (inkstore.py) of the
model-4 generator (gen.py) with the line kinds of lines.py, and a manifest.

Sets (`glassine-train gen4`):

| set | fonts | text |
|---|---|---|
| `train` | training fonts | `--lines` lines of every kind, in lines.SHARES |
| `val` | the seven original training fonts | the 2000 held-back book lines model 2 was checked on |
| `dev` | dev fonts | the same 2000 lines (model selection) |
| `dev_kinds` | dev fonts | 100 lines of each kind but prose, names from the held-out names |
| `test`, `test_words`, `test_symbols`, `test_letters` | each held-out group | model 2's test texts (text.corpus) |
| `test_kinds` | each held-out group | 100 lines of each kind but prose, held-out names |
| `test-font-NAME` | one held-out font | 300 test-book lines |

Held-out groups: `heldout` (kalam, reeniebeanie), `newprint`, `newscript` (fonts.toml). Every
set renders real-looking ink: the same generator, writers drawn per line, delayed strokes.
"""

import hashlib
import json
import multiprocessing as mp
import pickle
import subprocess
import time
from pathlib import Path

import numpy as np

from . import gen, lines, text
from .inkstore import Writer
from .timing import Fit

FONTS = Path("../synth/build/fonts")
_G = None  # (gen.Generator, letters writer or None), set before the workers fork


def _render(job):
    t, kind, seed = job[:3]
    rng = np.random.default_rng(seed)
    g, lw = _G
    if isinstance(lw, list):  # several writers: the job says which
        lw = lw[job[3]]
    for _ in range(3):
        try:
            return g.line(t, rng, letters=lw), kind, seed
        except ValueError:
            continue
    return None, kind, seed


def font_tables(fonts: list[str], cache: Path, workers: int):
    """Glyph tables of CHARS4 and space for the fonts (cached), and the characters each font
    has in its character map (a font without one draws a missing-glyph box)."""
    from fontTools.ttLib import TTFont
    from glassine_synth import fetch
    from glassine_synth.cli import font_tables as ft
    chars = lines.CHARS4.replace(" ", "") + " "
    key = hashlib.sha256((chars + ",".join(sorted(fonts))).encode()).hexdigest()[:16]
    f = cache / f"glyphs-{key}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    tabs = ft(FONTS, chars, fonts, workers)
    covers = {}
    for fam in fetch.load(FONTS):
        if fam["dir"] in fonts:
            cm = TTFont(FONTS / fam["dir"] / fam["files"][0]).getBestCmap()
            covers[fam["dir"]] = {c for c in chars if ord(c) in cm}
    cache.mkdir(parents=True, exist_ok=True)
    f.write_bytes(pickle.dumps((tabs, covers)))
    return tabs, covers


def style_pool(tabs, bigrams: list[str], extra: list[tuple[float, float]] = (),
               calib: dict | None = None) -> tuple[list, float, dict]:
    """(gap, word) styles: one per font (its own spacing, calibrated: gen.calibrate) plus
    `extra` (letter models, as they are), and the fonts' median open space."""
    raw, opens, per = [], [], {}
    for fid, t in tabs:
        g, o, w = gen.font_spacing(t, bigrams)
        raw.append((g, w))
        opens.append(o)
        per[fid] = {"gap": round(g, 3), "open": round(o, 3), "word": round(w, 3)}
    pool = gen.calibrate(raw, calib)
    for (fid, _), (g, w) in zip(tabs, pool):
        per[fid] |= {"gap_calibrated": round(g, 3), "word_calibrated": round(w, 3)}
    return pool + list(extra), float(np.median(opens)), per


def bigrams(sents: list[str], n: int = 4000, seed: int = 0) -> list[str]:
    rng = np.random.default_rng(seed)
    out = []
    while len(out) < n:
        s = sents[int(rng.integers(len(sents)))]
        for w in s.split():
            w = "".join(c for c in w if c.isalpha())
            out += [w[i:i + 2] for i in range(len(w) - 1)]
    return out[:n]


def render_set(out: Path, jobs: list[tuple[str, str, int]], g, lw, workers: int, meta: dict) -> dict:
    global _G
    _G = (g, lw)
    if (out / "meta.json").exists():
        print(f"{out.name}: exists", flush=True)
        return json.loads((out / "meta.json").read_text())
    w = Writer(out)
    t0 = time.time()
    failed = 0
    with mp.get_context("fork").Pool(workers) as pool:
        for i, (smp, kind, seed) in enumerate(pool.imap(_render, jobs, chunksize=64)):
            if smp is None:
                failed += 1
                continue
            w.add(smp, kind, seed)
            if (i + 1) % 20000 == 0:
                print(f"  {out.name}: {i + 1}/{len(jobs)} {time.time() - t0:.0f} s", flush=True)
    m = w.close({**meta, "failed": failed, "seconds": round(time.time() - t0)})
    print(f"{out.name}: {m['lines']} lines, {m['points']} points, {m['bytes'] / 1e6:.0f} MB", flush=True)
    return m


def jobs_of(texts: list[tuple[str, str]], seed: int) -> list[tuple[str, str, int]]:
    seeds = np.random.default_rng(seed).integers(0, 2**62, len(texts))
    return [(t, k, int(s)) for (t, k), s in zip(texts, seeds.tolist())]


def source_pins(fit_path: Path | None, given: str = lines.GIVEN) -> dict:
    """What the set was made from: the repository commit, the books, the name lists, the
    character model, the fit (its sha256, not its numbers' origin)."""
    def git(*a):
        try:
            return subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    raw = Path("build/text")
    pins = {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--", "."))}
    pins["books"] = json.loads((raw / "manifest.json").read_text())
    pins["book_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(raw.glob("pg*.txt"))}
    pins["names"] = lines.name_sources(given)
    if fit_path:
        pins["timing_fit_sha256"] = hashlib.sha256(fit_path.read_bytes()).hexdigest()
    return pins


def sheet(store, idx: list[int], out: Path, width: int = 1400, row_h: int = 110):
    """A PNG of lines of an ink set to look at: every line at the same x-height, its
    baseline, x-height and writing-area line in grey, strokes from yellow to blue as time
    goes on, the text and kind below."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, row_h * len(idx)), "white")
    d = ImageDraw.Draw(img)
    for r, i in enumerate(idx):
        strokes, row = store[i]
        y0 = r * row_h
        k = (row_h * 0.16) / row["xh"]
        base = y0 + row_h * 0.62
        for gy in (base, base - row["xh"] * k, base - row["line"] * k * 2 / 3):
            d.line([(10, gy), (width - 10, gy)], fill=(215, 215, 215))
        t1 = max(float(q[-1, 2]) for q in strokes) or 1.0
        for q in strokes:
            pts = [(20 + x * k, base + (y - row["base"]) * k) for x, y in q[:, :2]]
            shade = int(150 * (1 - q[0, 2] / t1))
            if len(pts) == 1:
                pts = pts * 2
            d.line(pts, fill=(shade, shade, 255 - shade), width=2)
        d.text((12, y0 + row_h - 16), f"{row['kind']}: {row['text']}", fill=(90, 90, 90))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
