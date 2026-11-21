# The handwriting service

`glassine-handwriting-service` (spec 004 FR-005, FR-012 to FR-015) holds the recognizer for
every app in the person's session: one recognizer process, one model in memory (SC-002). It
owns the person's store, so apps never touch its files. It has two sides:

- the **service interface** for apps, JSON lines over a unix socket (below; its schema is
  `docs/service.schema.json`, an example client `service/examples/hw-service-example.py`);
- the **compositor's channel** behind the pen interface (docs/pen-interface.md). The
  compositor starts the service and is its only peer on that side.

The service exits when the compositor's channel closes, and the compositor starts it again.
The recognizer is started on the first request and leaves after a while idle. Sessions
begun before such a restart are begun again on the next request.

## Who is calling (FR-001)

**Service socket.** The service asks the operating system who is calling (`SO_PEERCRED`).
Only the person's own user is answered. A process whose root holds `/.flatpak-info` is its
sandbox's application id. Any other process is `host.` followed by the program it runs (for
an interpreter, the script). A sandboxed app without a name is `unknown` and is never
allowed to learn or to see samples.

**Pen interface.** The compositor names the app: the security-context application id, or the
process id, which the service resolves as above.

**Recognizer.** The service runs one recognizer in relay mode (docs/recognizer.md, Relay).
Before each request for an app it sends `client NAME`, so every session and every sample
belongs to the app it was made for, as if that app had spoken to the recognizer itself. A
pick through the pen interface therefore stores the same sample as a direct client confirming
the same pen input in the same units and area (The writing's size, below).

## Permissions (FR-014)

Per app, the person decides two things:

- `learn`: its reports keep samples;
- `samples`: it may list and delete the samples kept through it.

Each is `allowed`, `refused` or unasked. Reading and neatening need neither. The choices are
kept in `$XDG_CONFIG_HOME/glassine/handwriting-apps.json`. Only an unsandboxed caller, such
as Settings or the samples app, can change them (`permission.set`). The samples app itself
(`host.glassine-handwriting-samples`) is allowed both. A request that needs a permission the
app lacks gets the error `permission-needed` (never asked) or `not-allowed` (refused), never
an empty result.

## The service interface

Socket: `$XDG_RUNTIME_DIR/glassine-handwriting/service`. A Flatpak app reaches it with
`--filesystem=xdg-run/glassine-handwriting` (FR-002), the one documented permission.
Each line is a request `{"id": …, "method": "…", "params": {…}}`. It is answered by a line
with the same `id` and either `"result": {…}` or `"error": {"code", "message"}`.

| Method | Params | Result |
|---|---|---|
| `hello` | | `version` (1), `app` (who the service takes the caller for), `sandboxed`, `neaten`, `text`, `letters`, `languages` |
| `status` | | `engine` (`running`, `pid`, `starts`), `surfaces` (pen interface surfaces held) |
| `permission.get` | | the caller's `learn`, `samples` |
| `permission.set` | `app`, `learn`, `samples` | that app's states; unsandboxed callers only |
| `read` | `strokes`, `count` (1–8), `units`, `area`, `context` | `candidates`: `text`, `score`, `from`, `fit`, `chars`; or `private`, or `gesture` |
| `neaten` | `strokes`, `strength` (0–1), `text`, `units`, `area`, `context` | `text`, `strokes`: `how`, `points` |
| `samples.list` | | `samples` kept through the caller: `id`, `time`, `source`, `language`, `text` (needs `samples`) |
| `samples.delete` | `id` | `{}`, or `not-yours` (needs `samples`) |
| `letters.status` | | `state`, `covered`, `font` (needs `samples`) |

Strokes are lists of points `[x, y, t]`, `[x, y, t, pressure]` or
`[x, y, t, pressure, tilt_x, tilt_y]`, with t in ms, pressure 0 to 1 and tilt in degrees.
`context`: `purpose` and `hints` as in `zwp_text_input_v3`, plus `before` and `languages`.
`units`: as the recognizer's (`device`, `device:PX_PER_MM`, `device:PX_PER_MM:RATE`, `em`);
with `device:PX_PER_MM` the service adds the rate it measures from the strokes. `area`: `box`
([x0, y0, x1, y1]), `line` (the ruled lines' spacing), `base` (the y of the first guide line)
and `rot` (0, 90, 180 or 270), in the strokes' units; a part left out is as the pen interface
has it (The writing's size, below): the box of the writing, no ruled line (in `em` a line of
1), the guide line of one line of writing. Ink in `em` without an `area` gets none (its units
say how high a line is). An area that is empty, a line of 0 or less or another turn is
`bad-request`. Every `device` request has the size prior (below).

Error codes: `bad-request`, `unknown-method`, `permission-needed`, `not-allowed`,
`not-yours`, `unavailable`, `failed`.

Not in version 1, still owed for spec 004 FR-013 and FR-015: sample export and show,
learning control, letter model and font builds with progress notifications, and donation
(through the samples app or Settings only). "Donation" is the internal name of what the
person sees as a contribution.

## The writing's size

Every session the service begins in the recognizer, for either interface, has a writing area,
units and a size prior (spec 004 FR-016, FR-017), so a model that reads the ink's size
(docs/recognizer.md, Size and Base models) judges it in physical size: the person's letters in
millimetres once there are enough of them (units with `PER_MM`), before that a typical
handwriting size in millimetres.

- **Box**: the app's ink area (`set_area`, a region's area), else the bounding box of the
  writing held.
- **Line**: the spacing of the app's ruled lines (`set_guides`), else none (`LINE` 0). A ruled
  line is a size reference only when the app sets one: no line is found from the writing, so a
  lone character is never measured against a line found from itself, nor against one found
  from other writing on the surface, in another region or in another app.
- **Guide line**: the app's first ruled line, else for a session whose strokes are one line of
  writing (the lines found as for neatening) of at least 3 glyphs (strokes overlapping
  horizontally, also through others, are one) the median of the strokes' lowest points. It
  says where letters sit (a comma from an apostrophe), not how big they are. Less writing, or
  more than one line, has none.
- **Prior**: a small letter 2.5 mm high (`TYPICAL_XH_MM`), sent as the recognizer's `prior` in
  the session's units: at the units' points per millimetre, and for `device` units without
  them at the default density of 96 points per inch (3.78 per millimetre, `DEFAULT_PX_PER_MM`).
  The recognizer uses it only when neither the person's letters in the session's units nor the
  app's ruled line give a reference; for a line of writing whose own small letters measure
  more than 1.6 times it, their height (Size). Ink in `em` gets none (its units say how high a
  line is).
- **Units**: the compositor's, `device:PX_PER_MM` when it knows the size of the output the pen
  writes on (its px over its millimetres, in logical px), with the pen's rate added once the
  surface's writing has 8 time steps: 1000 / the median step in ms, as the keyboard measures it.
  The rate is kept for the surface. For an output of unknown size the compositor sends the
  density the person set for it in Settings (GlassineOS/sway `docs/handwriting-channel.md`,
  Units); without one the units stay `device`, the pen's own, and no size in millimetres is
  assumed beyond the prior at the **default density of 96 points per inch (3.78 per
  millimetre)**.

The words of a line and the lines of the writing are read and neatened in sessions of their
own, with the surface's box, no line unless the app ruled one, the guide line of their line
and the prior. A word is neatened without its reading as the text: the reading only tells
which strokes are a word, and only a text the person confirmed is set in their font at Neatest
(spec 003 FR-043; a word read wrongly would be set as another). The `neaten` method passes the
app's `text` on as given. The main session's area follows the writing as it grows.

Measured with `make -C service check-model` (tests/check_sizes.py: the recorded ink,
recognizer/tests/data/ink.jsonl, written at 1.5, 3 and 6 mm small letters, its 28 lines and
each of its 210 letters and digits written with strokes of their own read alone; CER %, the
size prior alone, against the rule before it, which found a line of six median stroke sizes
from writing of 3 glyphs or more and for less writing took the one found earlier on the
surface, or none):

| model 4 (ghm 2) | 1.5 mm | 3 mm | 6 mm |
|---|---|---|---|
| lines, the prior | 17.8 | 24.4 | 33.1 |
| lines, the found line before | 24.0 | 24.0 | 24.0 |
| lines, the keyboard's paper | 20.7 | 20.7 | 20.7 |
| lone characters, the prior | 36.2 | 42.9 | 95.2 |
| lone characters, before (no area) | 55.7 | 55.7 | 55.7 |
| lone characters, the keyboard's paper | 25.7 | 25.7 | 25.7 |

With the writer's letters known both rules read the same (lines 2.1, lone characters 31.9; the
keyboard's paper 2.1 and 9.5, its ruled guide line placing a lone character). A version 1
model (model 2) reads as before (lines 5.0; lone characters 21.0, one more error at 1.5 mm).
The prior reads small and middling writing better than a found line did and large writing
worse: model 4 reads ink far larger than its reference with too many points per letter
(train/README.md, Size against the reference), and a lone character of 6 mm small letters is
twice as large as the typical one. The person's letters, once known, replace the prior.

A sample stored from such a session keeps that area, as every sample keeps its session's; the
prior is not stored.

## The compositor's channel

The compositor starts the service with `--pen-fd N`: its end of a socket pair. Both sides
send JSON objects, one per line, and neither waits for an answer. `s` names a surface, as
the compositor numbers them. `k` names a stroke, by the surface's stroke numbers.

From the compositor:

| `op` | Fields | Meaning |
|---|---|---|
| `surface` | `s`, `sandbox` or `pid` | a surface that wants the service, and whose it is |
| `set` | `s`, `neaten`, `strength`, `text`, `purpose`, `hints`, `before`, `after`, `languages`, `guides` ([spacing, first]), `box` ([x0, y0, x1, y1], when the area is set), `units` | its choices; a private field (`password`, `pin`, the hints `hidden_text` or `sensitive_data`) is followed by `destroy` instead |
| `stroke` | `s`, `k`, `points` ([x, y, t, pressure, tilt_x, tilt_y]) | a raw stroke |
| `clear` | `s` | the app is done with the writing held |
| `report` | `s`, `serial`, `action` (`picked`, `accepted`, `corrected`, `discarded`), `rank`, `text` | what the person did with the candidates `serial` |
| `destroy` | `s` | forget the surface and all it held |

From the service:

| `op` | Fields | Meaning |
|---|---|---|
| `hello` | `version`, `neaten`, `text`, `letters`, `languages` | sent once at start: the capabilities |
| `neat` | `s`, `k`, `how`, `points` ([x, y] per raw point) | the neatened stroke `k` |
| `reading` | `s`, `text`, `strokes` | a partial reading |
| `candidates` | `s`, `serial`, `items` (`text`, `from`, `fit`, `chars`) | the candidates at a pause |
| `report` | `s`, `serial`, `result` | the answer to a report |

The compositor turns these into the pen interface's events. It sends a stroke's points in
parts small enough for the Wayland wire, and it keeps the time, pressure and tilt of each
raw point for the neat stroke.

**Timing.** The writing is read 250 ms after the last stroke. Neatening goes line by line
(the guides, else the lines found from the writing) and, within a line, word by word along
the best reading. A word is neatened again only when its strokes, its text or the strength
change. The candidates follow a pause of 900 ms.
