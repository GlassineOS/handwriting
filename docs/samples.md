# Sample format, version 1

One format for every handwriting sample: the generator's synthetic lines, samples a person
confirms on the device, samples they donate, and what the trainer reads. A file holds one
sample per line (JSON lines, UTF-8). "Donation" and `donor` are the internal names of what
the person sees as a contribution and their contributor number.

## Fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `version` | integer | yes | `1` |
| `text` | string | yes | The text the strokes spell, as confirmed |
| `lang` | string | yes | BCP 47 language tag (`en`, `de`) |
| `strokes` | list of strokes | yes | One stroke = the points between pen down and pen up, in writing order |
| `spans` | list of lists | yes | One entry per character of `text` (code points): the indices of the strokes that write it; empty for spaces and characters not written |
| `spans_source` | string | yes | `exact` (known at creation: generator, prompted writing) or `inferred` (from an alignment) |
| `source` | string | yes | `synthetic`, `confirmed` (picked alternative), `corrected` (the person edited the text), `prompted` (written to a shown text) |
| `units` | object | yes | How to read a point; see below |
| `provenance` | object | synthetic only | Fonts used: name, licence, source, upstream commit |
| `fonts` | list of strings | synthetic only | Font id per character of `text` |
| `licence` | string | donated only | Licence the person chose for the sample (`CC0-1.0` or `CC-BY-4.0`) |
| `donor` | string | donated only | The donor ID: a random UUID, the same in every sample and every donation of one person (until they ask for a new one), so samples can be counted per writer; nothing about the person or the device |
| `written` | string | no | How it was written (spec 003): `careful` (written as nicely as the person can) or `quick` (the quick half of a pair) |
| `pair` | string | no | The link a quick and a careful sample of the same prompt share: 16 hex digits made at random; one whose other sample is gone links nothing |
| `area` | object | no | The writing area it was written in (prompted writing): `box` `[x0, y0, x1, y1]` and `line` (the line spacing), in the units of `x` and `y`; `orientation`, the screen's rotation in degrees (0, 90, 180, 270) |
| `reading` | list | no | What the recognizer read when it was written, before learning from it: candidates best first, each `{"text": …, "score": …}` (`score` the log-probability, ≤ 0) |
| `pick` | object | no | A picked candidate (`confirmed`): `rank`, its place among the candidates (1 = first), and `from`, where it came from: `ink` (the ink's best reading), `beam` (another reading of the ink), `words` (a word of the list one edit from a reading), `guided` (a reading of the search the words guided) or `chars` (a reading of the search the character model weighed) |

A point is a list `[x, y, t]` or `[x, y, t, pressure]` or `[x, y, t, pressure, tilt_x,
tilt_y]`. All strokes of one sample use the same length.

- `x`, `y`: position, y pointing down.
- `t`: seconds since the first point of the sample.
- `pressure`: 0 to 1.
- `tilt_x`, `tilt_y`: degrees from the vertical.

`units` says what `x` and `y` are:

- `{"xy": "em"}`: em of the writing; the x-height is 0.45 em (the generator).
- `{"xy": "device", "per_mm": 10.0, "rate": 200}`: the pen's own units, points per
  millimetre, and the pen's report rate in hertz. Raw values are kept; the trainer
  normalises them.

## Rules

- Points are the pen's values as reported: no smoothing, resampling or cleaning. Hover
  positions (the pen above the screen) are not part of a stroke.
- Nothing identifies the writer or the device: no device name, serial, user name, client
  name or wall-clock time in a sample. A donated sample's `donor` is random: it tells one
  writer's samples apart from another's, nothing more. Times start at 0 for each sample.
- On the device, a stored sample also has its client and the time it was confirmed (spec
  001 FR-009). They live beside the sample in the person's data directory, not in it,
  and are dropped on export for donation.
- A reader must reject a `version` it does not know and ignore fields it does not know.

## Preference records

A file of samples (an export, a donation) can also hold preferences (spec 003 FR-003): which of
two samples, or of one character in each, the person found nicer. A preference is a line of its
own, after the samples it names, with no `strokes`; a reader that wants only samples passes over
lines without `strokes`. (An export, docs/recognizer.md, wraps its samples in records of its own;
its preference lines are the same.)

```json
{"preference": {"chosen": "4a7d46113566c847", "other": "a31fcebd612e77a7", "chosen_char": 0, "other_char": 2}}
```

- `chosen`, `other`: the two samples by their ids in this file: the first 16 hex digits of the
  64-bit FNV-1a hash of the sample's line as written in the file (UTF-8, without the newline).
- `chosen_char`, `other_char` (optional, together): the character of each, by its place in the
  sample's `text` (0-based code points); without them the whole samples.
- A preference is in a donation only when both its samples are.

## Example

```json
{"version": 1, "text": "hi", "lang": "en", "source": "synthetic", "spans_source": "exact",
 "units": {"xy": "em"},
 "strokes": [[[0.0, -0.7, 0.0], [0.0, 0.0, 0.12]], [[0.0, -0.3, 0.2], [0.2, 0.0, 0.31]],
             [[0.4, -0.3, 0.4], [0.4, 0.0, 0.47]], [[0.4, -0.5, 0.6], [0.4, -0.48, 0.62]]],
 "spans": [[0, 1], [2, 3]], "fonts": ["kalam", "kalam"],
 "provenance": {"kalam": {"name": "Kalam", "license": "OFL-1.1", "source": "...", "upstream": "...", "commit": "..."}}}
```
