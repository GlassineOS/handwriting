# glassine-synth (prototype)

Synthetic online handwriting for the recognizer's base model (spec 001, FR-012): timed pen
strokes made from handwriting fonts, humanised with jitter.

```sh
uv run glassine-synth fetch            # fonts in fonts.toml → build/fonts (OFL + HANDWRITING checked)
uv run glassine-synth glyphs           # build/glyphs.svg: strokes per glyph, numbered in pen order
uv run glassine-synth lines -n 4       # build/lines/samples.jsonl + lines.svg (colour = time)
uv run pytest -q tests
```

## Pipeline

1. **Fetch.** Each family in `fonts.toml` is read from `google/fonts`; its `METADATA.pb` must
   say category HANDWRITING and licence OFL (families under `ofl/`) or Apache 2.0 (`tree =
   "apache"`, families under `apache/`). Regular weight only. The licence text and the
   metadata (designer, upstream repository and commit) are kept next to the font.
   `fonts.toml` lists every Latin handwriting family of the catalogue that the stroke
   extraction handles (176); the ones left out are listed in it with the reason. Each family
   has a `split` for the recognizer (train, dev or test) and held-out families a `style`
   (print or script).
2. **Glyph to strokes.** Render at 256 px per em, skeletonise, split the skeleton into end
   points, junctions and edges, prune short spurs, walk the edges into strokes (start at the
   top-left free end, continue on the straightest edge). Closed strokes start at the top and
   run counter-clockwise. Dots of i and j and crosses of t and f are marked delayable.
   Every font is scaled to the same x-height.
3. **Lines.** Font chosen per word (sometimes per letter); per glyph size, rotation, shear and
   a smooth bend; per line slant, tilt, baseline wander and uneven spacing.
4. **Letter-shape variation** (off by default, `Params.warp`, `Params.zones`): per letter a
   smooth random displacement field over all its strokes (loops grow or shrink, openings
   close, bars move), and per line a scale for ascenders, descenders and letter width. A
   font draws a letter the same way every time; this gives more than one shape per font.
5. **Stroke order variants.** Closed strokes sometimes clockwise; dots and crosses sometimes
   written after the word; a rare stray pen lift splitting a stroke.
6. **Timing.** One lognormal speed pulse per segment between sharp corners (a simplified
   sigma-lognormal model), sampled at 200 Hz, pen-up gaps by distance.

## Sample format (JSON lines)

`text`; `strokes`: list of `[x, y, t]` points (em units, y down, seconds); `spans`: for each
character of `text` the indices of its strokes (empty for spaces); `fonts`: font per
character; `provenance`: licence and source of every font used.

## Known gaps

- Letters are laid out one by one: script fonts are not joined into one pen-down stroke.
- Joined script fonts (Dancing Script) come out dense: their letters are designed to
  overlap, and per-glyph layout ignores kerning and join shapes.
- Stroke order is a heuristic; some glyphs start at a junction rather than a free end.
- Only a fixed set of direction and delay variants; no split or merged strokes (k, B, M, E)
  yet.
- The built-in text is a handful of lines written for this tool; larger corpora must be
  redistributable, with their source recorded.
