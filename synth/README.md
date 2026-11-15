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

7. **Measured presets** (`PRESETS`, `preset(name)`): parameter sets fitted to real ink with
   `stats.py`. The defaults stay the original model; `real1` adds steady speed along a stroke
   (`pulse_floor`, `corner`), short pauses nearly independent of the jump (`pause_lo`,
   `pause_hi`, `jump_speed`), strokes of a word joined pen-down when they are close (`join`,
   `join_dist`), and taller ascenders and descenders (`asc_scale`, `desc_scale`, `slant_mean`).
8. **Glyphs with their own timing**: a glyph whose strokes carry a third column (seconds from
   the stroke's start) keeps that timing instead of the speed model (the trainer's
   `hand.py` uses this to draw text in a person's own letters).

## Real ink (`stats.py`)

`stats.measure()` gives the same aggregate measures for real samples and generated ones,
lengths in x-heights (from each sample's x-height letters that own their strokes), times in
seconds; `glassine-train inkstats` renders the texts of a set of real samples with 40 training
fonts and measures both. The real ink here is one writer's prompted words (640 samples from
the training sessions, single words and characters, a 333 Hz pen); the generator rendered the
same texts. Medians unless noted.

| measure | real (one writer) | defaults | `real1` |
|---|---|---|---|
| ascender height / x-height | 2.87 | 1.71 | 2.46 |
| descender depth / x-height | 1.20 | 0.89 | 1.03 |
| capital height / x-height | 2.5 (16 measured) | 1.88 | 2.96 |
| pen speed (x-heights/s), median / 90th percentile | 11.8 / 29.0 | 7.1 / 26.0 | 10.1 / 23.4 |
| speed along a stroke (tenths of its path, over its mean) | 0.5 1.2 1.4 1.4 1.5 1.5 1.4 1.3 1.4 1.4 | 0.3 1.5 2.1 2.4 2.5 2.4 2.1 1.8 1.2 0.5 | 0.6 1.2 1.4 1.5 1.5 1.5 1.5 1.4 1.2 0.8 |
| speed pulses per x-height of path | 2.2 | 0.9 | 1.2 |
| stroke duration (s) | 0.26 | 0.14 | 0.25 |
| slant of downstrokes (degrees, quartiles) | 3.7 / 9.2 / 13.7 | −1.0 / 6.2 / 13.2 | 2.0 / 7.9 / 14.1 |
| pen-up pause between characters (s) | 0.11 | 0.14 | 0.12 |
| pause fit: a + jump / v | 0.11 s + jump / 100 x-heights/s | 0.09 s + jump / 19 | 0.10 s + jump / 62 |
| strokes per character (words) | 1.12 | 1.99 | 1.12 |
| strokes per single character | 1.36 | 2.43 | 1.72 |

The x-height is about a tenth of the writing area's line (0.095 lines), the ink of a word
about a third (0.33). What the defaults got most wrong: the pen stops at every corner (a speed
bell per segment) where real ink keeps moving and is still fast when the pen lifts; pauses grow
with the jump four times too steeply; and letters fall apart into twice the strokes a person
writes. `real1` fixes those three for any hand. The zone sizes and slant are this writer's and
are not a claim about hands in general; `zones` keeps its spread around them. The pen rate is
the generator's 200 Hz against the pen's 333 Hz (the features resample by path, so it matters
little).

## Sample format (JSON lines)

`text`; `strokes`: list of `[x, y, t]` points (em units, y down, seconds); `spans`: for each
character of `text` the indices of its strokes (empty for spaces); `fonts`: font per
character; `provenance`: licence and source of every font used.

## Known gaps

- Letters are laid out one by one; `join` connects nearby strokes of a word with straight
  pen-down bridges, not with the join shapes a writer would make.
- Joined script fonts (Dancing Script) come out dense: their letters are designed to
  overlap, and per-glyph layout ignores kerning and join shapes.
- Stroke order is a heuristic; some glyphs start at a junction rather than a free end.
- Only a fixed set of direction and delay variants; no split or merged strokes (k, B, M, E)
  yet.
- The built-in text is a handful of lines written for this tool; larger corpora must be
  redistributable, with their source recorded.
