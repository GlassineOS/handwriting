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

**Models.** The recognizer reads with the shipped model of each language, or with one the
person installed themselves (spec 006; docs/recognizer.md, Installed models). The service says
nothing about which: no field, capability or message of either interface names a model, and a
change of model or of the person's choice takes effect at the next reading of every app, with
the same recognizer process (it looks at the chosen file at each request). Writing handed back
after a change is read with the model there is then; confirmed and edited words stay (Writing
regions, Stored ink).

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
| `hello` | | `version` (1), `app` (who the service takes the caller for), `sandboxed`, `neaten`, `text`, `letters` (a letter model has been built: neatening has the person's letters to go by; Autograph is there either way), `languages` |
| `status` | | `engine` (`running`, `pid`, `starts`), `surfaces` (pen interface surfaces held) |
| `permission.get` | | the caller's `learn`, `samples` |
| `permission.set` | `app`, `learn`, `samples` | that app's states; unsandboxed callers only |
| `read` | `strokes`, `count` (1–8), `units`, `area`, `context` | `candidates`: `text`, `score`, `from`, `fit`, `chars`; or `private`, or `gesture` |
| `neaten` | `strokes`, `strength` (0–1), `text`, `units`, `area`, `context` | `text`, `strokes`: `how`, `points` |
| `samples.list` | | `samples` kept through the caller: `id`, `time`, `source`, `language`, `text` (needs `samples`) |
| `samples.delete` | `id` | `{}`, or `not-yours` (needs `samples`) |
| `letters.status` | | `state` (`current` or `outdated`), `covered`, `font` (`name`, `version`; needs `samples`) |

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

**Autograph (spec 003 FR-009, FR-011).** The service keeps the person's font: at its start and
then every half hour (`GLASSINE_HANDWRITING_AUTOGRAPH_S` seconds; 0 at start only) it runs the
recognizer's `font auto` (docs/recognizer.md, Command line), which installs Autograph when there
is none and, while the device charges and is idle, builds the letter model and the font again
once new samples changed them, at the lowest priority. Apps see the new version through the font
cache; `letters.status` gives it.

Not in version 1, still owed for spec 004 FR-013 and FR-015: sample export and show,
learning control, letter model and font builds on request with progress notifications, and donation
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
  app's ruled line give a reference, and never over the writing's own size: writing whose small
  letters can be measured is read at their height, and the prior only settles what the height
  of writing too short to measure means (a small letter or a capital), for ink at most twice
  (a lone character) or four times (more) as high as it; taller ink is read by its own height
  (docs/recognizer.md, Size). Ink in `em` gets none (its units say how high a line is).
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
| lines, the prior and the writing's own size | 7.4 | 8.3 | 11.6 |
| lines, the found line before | 24.0 | 24.0 | 24.0 |
| lines, no area (no reference) | 13.6 | 13.6 | 13.6 |
| lines, the keyboard's paper | 20.7 | 20.7 | 20.7 |
| lone characters, the prior and the writing's own size | 30.5 | 34.3 | 55.7 |
| lone characters, before (no area) | 55.7 | 55.7 | 55.7 |
| lone characters, the keyboard's paper | 25.7 | 25.7 | 25.7 |

With the writer's letters known both rules read the same (lines 2.1, lone characters 31.9; the
keyboard's paper 2.1 and 9.5, its ruled guide line placing a lone character). A version 1
model (model 2) reads as before (lines 5.0; lone characters 21.0, one more error at 1.5 mm).
Every size reads at least as well as before; a lone character of 6 mm small letters is more
than twice as high as the prior and is read by its own height, as before. The figures the
rule uses come from the person's writing, not from this ink (docs/recognizer.md, Size); on the
person's other half, the prior alone, at 1.5, 3 and 6 mm small letters, it reads their words
at 35.1, 35.9 and 35.5 % CER (the prior as it was: 34.9, 41.1, 38.9; no reference: 38.9) and
their lone characters at 58.9, 68.3 and 66.6 (the prior as it was: 55.7, 72.5, 90.6; no
reference: 66.6). The person's letters, once known, replace the prior.

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
| `destroy` | `s` | forget the surface and all it held, its regions too |

The writing regions' ops (`region`, `region_set`, `region_mode`, `region_stroke`,
`region_edit`, `region_report`, `region_restore`, `region_destroy`; from the service
`region_neat`, `region_value`, `region_candidates`, `region_gesture`, `region_report`) are
specified with the compositor's side in GlassineOS/sway `docs/handwriting-channel.md`; what the
service does with them is under Writing regions, below.

From the service:

| `op` | Fields | Meaning |
|---|---|---|
| `hello` | `version`, `neaten`, `text`, `regions`, `letters`, `languages` | sent once at start: the capabilities |
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

## Writing regions

A surface can hold writing regions (spec 005): fields of their own, each with its area, ruled
lines, field type, languages, reading and neatening, whose reading is the field's value. The
compositor routes each stroke to the region it starts in, in the region's own coordinates
(GlassineOS/sway `docs/handwriting-channel.md`); the service never sees a region's place on
the surface, so a move changes nothing here, and a resize (`region_set` with a new `size`) only
gives a session begun later the new box: nothing is read again (FR-003, FR-004).

**Cost.** The compositor announces a region (`region`) only once it holds writing and wants the
service (FR-007). Even then the region holds no recognizer session until it is read, and gives
it up at the pause after its writing. Each region's reading is a session of its own, with the
region's units, size prior, box (`[0, 0, size]`) and ruled lines, never a line from another
region (FR-028). At most 16 regions are read at once (a document opened with many fields is
read a few at a time, never past the recognizer's 64 sessions), and a region gives its session
up after a report, an edit or a restore too. Private regions never reach the service; a
region that becomes private is destroyed.

**The value.** A region's words are of three kinds: read (the current reading of its writing),
kept (a pick, an acceptance, or a word an edit left as it was: its ink and its text, never read
again, FR-026) and set (a word the person typed, with no ink). The reading is made again at
every change of the writing (250 ms after a stroke), line by line, and when the value changed
it is sent whole (`region_value`, `serial` one more than the last): the words line by line in
reading order, joined by spaces and the lines by a newline (FR-008, FR-009). Each entry of
`words` has `start` and `length` (UTF-8 bytes of `text`) and either

- `strokes`, `dx`, `dy`: an ink word, its strokes moved by `dx` along its line when an edited
  word before it changed width (`dy` is 0), or
- `box` ([x0, y0, x1, y1]) and `path`: a word set in the person's font (the recognizer's `set`)
  as an outline: each stroke of the font's glyphs drawn with its pen as filled polygons (a quad
  per segment, an octagon per point, all turning the same way, non-zero fill), so the app needs
  no font (FR-024).

With the region's display `set` (`region_set` `display`, FR-030) an ink word has both: its
`strokes`, `dx`, `dy` as above and the `box` and `path` it is shown in, its text in the person's
font on its line (the line its ink sits on) at the size of the person's writing in the region
(as for an edit, FR-021), from where its ink starts, moved right only as far as needed to stand
a word space after the word before it on the line. A change of the display sends the value again
with that layout (a new `serial`) and nothing else: no reading, no session, no stroke leaving,
the same text and saved state. A word whose shape can't be had (the recognizer's `set` fails)
stays ink.

`gone` lists the strokes that left the region since the last value: gestures, struck-out words,
ink an edit replaced. `state` is the saved state (FR-010), base64 of the kept and set words
(each kept word by its text and its strokes' points, each set word by its text and place).

**Gestures.** The recognizer's gestures act in the region they are written in (FR-012): a
strike or scratch deletes the words it covers, their ink leaving; a caret places the writing
after it where it points. `region_gesture` names the gesture's own stroke and the serial of the
value that carries its effect; the stroke leaves (`gone`). With reading off there are no
gestures: such a stroke stays ink. A word is covered where it is shown: by its ink (the strokes
the recognizer finds the gesture covers, or for a kept or set word its box), with the display
`set` by the box its shape is shown in. A strike-through that covers no word is no gesture: no
`region_gesture`, its stroke stays the region's, and it is read with the rest of the writing
(the recognizer's `read … writing` while it is the session's newest stroke), so it becomes part
of a word, or ink that is no word, as the reading has it.

**Candidates and reports.** At a pause (900 ms) the region sends its candidates with the serial
of its current value: for the line written last, each candidate with the rest of the value. A
report names a value (`accepted`, `corrected`) or its candidates (`picked`) among the region's
last 16 values (older: `stale`), and applies only to the writing that value was read from;
writing added after it stays read (FR-013). A report never ends the region's writing. `picked`
and `accepted` keep the reported words; `corrected` is an edit (below). A word becomes a sample
only with ink of its own that came from the pen in this run (never ink handed back by
`region_restore`), only when that ink fits its text (the recognizer's `align`, a score of 0.6 or
more), and only with the person's permission for the app (`not_allowed` otherwise); set words
never do (FR-014). The result is `stored` when a sample was stored, else `not_kept`.

**Edits.** A correction, or the person's keyboard text (`region_edit`, FR-017), is matched to the
value word by word: a word left as it was keeps its ink and is kept; a changed or added word is
set in the person's font where it stands: on the line of the word before it on its line of the
text, else of the kept ink it comes before, else the region's first free line, at the size of
the person's writing in the region, else their letters in millimetres, else the typical size
(FR-021). The kept ink after a changed word moves along its line by the difference in width,
unchanged in shape. The ink of a changed word leaves; where it fits the word's new text it
becomes a sample first (a correction). When no word keeps its ink, the whole value is set and
none of the ink stays (FR-020). Writing after an edit is read as usual and joins the value
where it is written; set words stay as they are (FR-023). Whether set words wrap is the app's
choice.

**Neatening.** Each word with ink is neatened in a session of its own with the area of its
line, and never with its text: at every strength kept ink stays ink (FR-019). Set words are
not neatened.

**Stored ink.** `region_restore` replaces what the region holds with the strokes handed back
at once (a stroke that follows it on the channel is added to them),
with their saved state or without (FR-025). With it, the words the state names are kept again
by their strokes and its set words come back where they were; the rest is read with the model
there is now (FR-026). Without it, the strokes are read and the reading is the value.
