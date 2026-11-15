# Sample format, version 1

One format for every handwriting sample: the generator's synthetic lines, samples a person
confirms on the device, samples they donate, and what the trainer reads. A file holds one
sample per line (JSON lines, UTF-8).

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
| `area` | object | no | The writing area it was written in (prompted writing): `box` `[x0, y0, x1, y1]` and `line` (the line spacing), in the units of `x` and `y`; `orientation`, the screen's rotation in degrees (0, 90, 180, 270) |
| `reading` | list | no | What the recognizer read when it was written, before learning from it: candidates best first, each `{"text": …, "score": …}` (`score` the log-probability, ≤ 0) |

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
  name or wall-clock time in a sample. Times start at 0 for each sample.
- On the device, a stored sample also has its client and the time it was confirmed (spec
  001 FR-009). They live beside the sample in the person's data directory, not in it,
  and are dropped on export for donation.
- A reader must reject a `version` it does not know and ignore fields it does not know.

## Example

```json
{"version": 1, "text": "hi", "lang": "en", "source": "synthetic", "spans_source": "exact",
 "units": {"xy": "em"},
 "strokes": [[[0.0, -0.7, 0.0], [0.0, 0.0, 0.12]], [[0.0, -0.3, 0.2], [0.2, 0.0, 0.31]],
             [[0.4, -0.3, 0.4], [0.4, 0.0, 0.47]], [[0.4, -0.5, 0.6], [0.4, -0.48, 0.62]]],
 "spans": [[0, 1], [2, 3]], "fonts": ["kalam", "kalam"],
 "provenance": {"kalam": {"name": "Kalam", "license": "OFL-1.1", "source": "...", "upstream": "...", "commit": "..."}}}
```
