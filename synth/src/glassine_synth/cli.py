"""glassine-synth: fetch fonts, review glyph strokes, generate humanised lines."""

import argparse
import json
import multiprocessing as mp
from pathlib import Path

import numpy as np

from . import fetch, sheet
from .glyph import EM, glyph, load_font
from .ink import XH, Params, line

XHEIGHT = XH  # em
CHARS = ("abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" ".,;:!?'-()")
TEXT = [  # written for this tool; redistributable with it
    "the quick brown fox jumps over the lazy dog",
    "pack my box with five dozen liquor jugs",
    "a fine day for a quiet walk",
    "Write it once, read it twice.",
    "jinx: tiny dots, tall crosses, fifty times",
]


def _table(args):
    path, chars = args
    font = load_font(path)
    k = XHEIGHT / (-font.getbbox("x", anchor="ls")[1] / EM)  # same x-height for every font
    table = {}
    for c in chars:
        g = glyph(font, c)
        g.advance *= k
        g.strokes = [s * k for s in g.strokes]
        table[c] = g
    return table


def font_tables(cache: Path, chars: str, only: list[str] | None = None, workers: int = 1):
    """Glyph tables (font id, {char: Glyph}) for the fetched fonts, all at the same x-height."""
    jobs = [(fam["dir"], (cache / fam["dir"] / f, chars)) for fam in fetch.load(cache)
            if not only or fam["dir"] in only for f in fam["files"]]
    if workers > 1 and len(jobs) > 1:
        with mp.get_context("fork").Pool(min(workers, len(jobs))) as pool:
            tables = pool.map(_table, [j for _, j in jobs])
    else:
        tables = [_table(j) for _, j in jobs]
    return [(d, t) for (d, _), t in zip(jobs, tables)]


def _json(smp, prov, lang):
    # sample format v1, docs/samples.md
    return {
        "version": 1,
        "text": smp["text"],
        "lang": lang,
        "source": "synthetic",
        "spans_source": "exact",
        "units": {"xy": "em"},
        "strokes": [np.round(s, 4).tolist() for s in smp["strokes"]],
        "spans": smp["spans"],
        "fonts": smp["fonts"],
        "provenance": prov,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="glassine-synth")
    ap.add_argument("--cache", type=Path, default=Path("build/fonts"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download the fonts in the manifest, check licences")
    f.add_argument("--manifest", type=Path, default=Path("fonts.toml"))
    g = sub.add_parser("glyphs", help="SVG sheet of glyph strokes per font")
    g.add_argument("--chars", default=CHARS)
    g.add_argument("--font", action="append")
    g.add_argument("-o", type=Path, default=Path("build/glyphs.svg"))
    s = sub.add_parser("lines", help="generate humanised lines (JSON lines + SVG sheet)")
    s.add_argument("--text", type=Path, help="one line of text per line (default: built-in)")
    s.add_argument("-n", type=int, default=1, help="variants per text line")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--lang", default="en", help="language tag of the text")
    s.add_argument("--font", action="append")
    s.add_argument("-o", type=Path, default=Path("build/lines"))
    a = ap.parse_args(argv)

    if a.cmd == "fetch":
        fetch.fetch(a.manifest, a.cache)
    elif a.cmd == "glyphs":
        tables = font_tables(a.cache, a.chars, a.font)
        a.o.parent.mkdir(parents=True, exist_ok=True)
        a.o.write_text(sheet.glyph_sheet(tables, a.chars))
        print(a.o)
    elif a.cmd == "lines":
        texts = a.text.read_text().splitlines() if a.text else TEXT
        texts = [t for t in texts if t.strip()]
        chars = "".join(sorted(set("".join(texts))))
        tables = font_tables(a.cache, chars, a.font)
        prov = {f["dir"]: {"name": f["name"], "license": f["license"], "source": f["source"],
                           "upstream": f["upstream"], "commit": f["upstream_commit"]}
                for f in fetch.load(a.cache)}
        rng = np.random.default_rng(a.seed)
        samples = [line(t, tables, rng, Params()) for t in texts for _ in range(a.n)]
        a.o.mkdir(parents=True, exist_ok=True)
        with open(a.o / "samples.jsonl", "w") as fh:
            for smp in samples:
                used = sorted(set(smp["fonts"]))
                fh.write(json.dumps(_json(smp, {k: prov[k] for k in used}, a.lang)) + "\n")
        (a.o / "lines.svg").write_text(sheet.line_sheet(samples))
        print(a.o / "samples.jsonl", a.o / "lines.svg", f"{len(samples)} samples")


if __name__ == "__main__":
    main()
