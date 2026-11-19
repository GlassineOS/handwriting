# The handwriting recognizer (contract version 1.5)

<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

`glassine-handwriting` reads handwriting as it is written (spec 001): strokes in, ranked
candidates out, each with which strokes made which character; gestures; prompted writing; the
person's samples and the adaptation learned from them. It is a separate program behind a line
contract, so another engine can stand in for it. This page is enough to talk to it and to read its
files without its code. The sample format is in [`samples.md`](samples.md).

## Running it

A client (the keyboard's writing paper, a notes app) starts `glassine-handwriting` with no
arguments as a child process, writes requests to its stdin and reads replies from its stdout, one
line each. It runs until stdin closes (or SIGTERM, SIGINT, SIGHUP) and exits 0. It never opens a
network socket, and nothing written appears on stderr.

```
glassine-handwriting [-i IDLE_SECONDS] [--no-adapt]
```

After `IDLE_SECONDS` (default 300; 0 = never) without a request it exits 0 by itself, so an
unused recognizer gives its memory back; a client starts it again when it needs it (an idle exit
is not a crash). On that exit, when the machine is charging and idle (load average below 1) and
there are samples, it first starts one adaptation (below) in a detached process at the lowest
priority; `--no-adapt` leaves that out.

A client should never wait on it: send strokes as they are drawn, keep handling the pen, and show
a reply only when its id is the newest it asked for. A missing or exited recognizer means no
candidates, nothing else.

## Lines

Requests and replies are UTF-8 lines ending in `\n`, fields separated by one tab. In text fields
(`BEFORE`, `TEXT`, `PROMPT`, a sample) a backslash, tab, newline and carriage return are written
`\\`, `\t`, `\n`, `\r`; any other backslash sequence is `bad-escape`. A request line is at most
4 MiB; a longer one is answered with one `error - too-long` and skipped up to its newline.

`ID` is the client's name for the request: 1 to 32 characters of `A-Z a-z 0-9 . _ -`. Every
request gets exactly one reply line carrying its id, in the order of the requests. `S` is a
session (one piece of writing, one field) with the same characters. `LANGS` is the list of
languages, comma-separated, in order of preference (`en`, `en-GB,de`), or `-`/empty for the
setting, then the locale, then `en`; the first one with a model is used (`en-GB` falls back to
`en`). `FIELD` is the field's text-input-v3 content type: the purpose, then its hints,
comma-separated (`normal`, `email,sensitive_data`), or `-` for ink that isn't a text field (an
app's own writing).

`UNITS` says how to read a point: `em` (the client scaled its writing so a line of text is about
1 high) or `device` (the pen's own units), optionally `device:PER_MM` (units per millimetre) or
`device:PER_MM:RATE` (points per second). The recognizer normalizes the ink itself; the units
help with gestures and with the ink's size (below, Size) and go into samples.

`POINTS` is one stroke (pen down to pen up): points separated by spaces, each `x,y,t`,
`x,y,t,p` or `x,y,t,p,tx,ty`: position (y pointing down), time in milliseconds (any origin,
increasing), pressure 0 to 1, tilt in degrees from the vertical. All points of a stroke have the
same number of values. A stored sample keeps every value as sent (written in the shortest form
that reads back as the same number), times counted from the sample's first point, to the
microsecond.

## Requests

| Request | Reply |
|---|---|
| `hello ID VERSION [CLIENT [relay]]` | `hello ID 1` |
| `client ID NAME` | `client ID ok` (after `hello … relay` only, else `not-relay`) |
| `begin ID S LANGS FIELD UNITS BEFORE` | `begin ID ok`, or `begin ID private` (nothing is kept) |
| `stroke ID S POINTS` | `stroke ID ok N`: the stroke's number in this session |
| `read ID S [COUNT [raw\|chars] [detail]]` | `read ID text K {TEXT SCORE ALIGN}×K` (with `detail` `{TEXT SCORE ALIGN FROM CHECK}×K`), `read ID gesture …` (below), `read ID empty`, or `drop ID` |
| `align ID S PROMPT` | `align ID SCORE K {FIT SEGMENTS}×K`, or `align ID short` |
| `remove ID S N[,N…]` | `remove ID ok REMOVED` |
| `clear ID S` | `clear ID ok`: all strokes gone, the session stays |
| `end ID S` | `end ID ok` |
| `area ID S X0,Y0,X1,Y1,LINE,ROT[,BASE]` | `area ID ok` |
| `confirm ID S SOURCE TEXT [keep]` | `confirm ID ok SAMPLE` (`prompted`: `confirm ID ok SAMPLE exact\|inferred`), or `confirm ID skip REASON` |
| `recognize ID LANGS FIELD UNITS COUNT BEFORE STROKES` | as `read`, with the verb `recognize`; or `recognize ID private` |
| `samples ID [all]` | `samples ID K SAMPLE…` |
| `list ID [all]` | `list ID K {SAMPLE TIME SOURCE LANG TEXT READ}×K` |
| `mark ID S WRITTEN PAIR` | `mark ID ok` |
| `marked ID [all]` | `marked ID K {SAMPLE WRITTEN PAIR}×K` |
| `prefer ID CHOSEN OTHER [CHOSEN_CHAR OTHER_CHAR]` | `prefer ID ok` |
| `preferences ID [all]` | `preferences ID K {CHOSEN OTHER CHOSEN_CHAR OTHER_CHAR}×K` |
| `sample ID SAMPLE` | `sample ID JSON` (the sample's line, escaped) |
| `neaten ID S STRENGTH [TEXT [ALIGN]]` | `neaten ID ok TEXT K {N HOW POINTS}×K WHY` |
| `donate ID S TEXT [keep]` | `donate ID ok COUNT SAMPLE`, or `donate ID skip REASON` |
| `forget ID SAMPLE` | `forget ID ok WHERE` |
| anything wrong | `error ID REASON` (`ID` is `-` when the line has no usable id) |

**hello** names the contract version (1: versions 1.1 to 1.5 only add to 1.0, so a client of any
says and gets 1) and, optionally, the client: an application id
(`org.glassine.keyboard`, same characters as an id, up to 63). The name is stored with each sample
the client confirms; `samples` needs it. It is what the client says it is (the recognizer runs as
the person's own process and can't check it).

With `relay` the process serves several clients: the handwriting service (docs/service.md)
starts one for all apps, so the model is loaded once. `client` then names the client the
requests after it are for, as `hello`'s `CLIENT` does for a whole process. A session belongs to
the client it was begun for: other clients get `unknown-session` for it (and `busy` for a
`begin` of its name), and its samples are stored under that client. Without `relay`, `client`
is `not-relay` and nothing else changes.

**begin** opens session `S` (an existing one of that name is ended first). At most 64 sessions at
once (`busy`). `BEFORE` is the text before the cursor; in a prose field the words before it rank
the readings (below, Words).
In a private field (below) no session is kept: strokes sent to it get `unknown-session`.

**stroke** adds a stroke. Stroke numbers count up from 0 and are never reused in a session, also
after `remove`. At most 4096 strokes of 100000 points each.

**read** reads the strokes so far, up to `COUNT` candidates (1 to 8, default 3), best first, with
what the field reads with (Words, Characters, below); with `raw`, the ink's reading alone, with
neither (how readable the ink itself is: prompted writing's checks); with `chars`, with the
character model but without words (free writing that should not be bent towards the word list;
in a field without the character model the same as `raw`). A
client may read after every stroke (writing in progress, FR-004); a reading of the same strokes is
the same whether they came one by one or at once. When several `read` requests of one session are
read together (the client was faster than the recognizer), only the newest is answered; the older
ones get `drop ID`. Requests between them are carried out in order.

- `text K`: `K` candidates (0 when nothing could be read), each
  `TEXT SCORE ALIGN`, with `detail` also `FROM CHECK`: where it came from (`ink` the ink's best
  reading, `beam` another reading of the ink, `words` a word of the list one edit from a reading,
  `guided` the search the words guided, `chars` the search the character model weighed; Words
  and Characters, below) and `ok` when a pick of it is stored as it
  is or `check` when a pick is checked first and would not pass (confirm, below). `SCORE` is the log-probability of the text given the ink, calibrated (≤ 0, higher is better; Calibration, below). `ALIGN` has one
  entry per character (code point) of `TEXT`, separated by `;`: the strokes that make it,
  comma-separated, each `N` (the whole stroke) or `N:A-B` (points `A` to `B` of stroke `N`); empty
  for a space. Every written stroke belongs to some character.
- `gesture KIND N X0,Y0,X1,Y1 COVERED CHARS`: the newest stroke is a gesture. `KIND` is `strike`
  (a line through writing: delete what it crosses), `scratch` (scribbled over: delete), or
  `caret` (a `^`: insert here). `N` is the gesture's stroke number,
  `X0,Y0,X1,Y1` its bounding box in the client's units, `COVERED` the numbers of the strokes it
  covers (comma-separated, `-` for none). `CHARS` is in the best reading of the other strokes:
  `A-B` the characters it covers (0-based, inclusive), `@K` for a caret the character index it
  inserts before, `-` when nothing. The gesture stroke is removed from the session (it isn't
  writing); the client deletes or inserts.
- `empty`: no strokes.

**align** (prompted writing, FR-007): `PROMPT` is the text the person was asked to write. The
reply has one `FIT SEGMENTS` pair per character of `PROMPT`: `FIT` how well the strokes match that
character (0 to 1; `-` for a space or a character not written) and `SEGMENTS` as in `ALIGN`.
`SCORE` is the mean fit of the written characters; a low one means the person wrote something
else. `short`: too little ink for the prompt.

**area** names the writing area of session `S` for the samples it makes and for the size: its
box `X0,Y0` to `X1,Y1` and its line spacing `LINE`, in the session's units, the screen's
orientation `ROT` (0, 90, 180 or 270 degrees), and optionally `BASE`, the y of its first guide
line (the line letters sit on; the next ones every `LINE` from it). It stays until the session
ends. A box that is empty or an orientation that isn't one of these is `bad-area`. `BASE` is not
stored in samples.

**remove** removes strokes by number (unknown numbers are ignored; `REMOVED` counts the ones
removed). An undo, or the strokes a gesture covered once the client acted on it.

**confirm** stores the session's strokes as a sample with `TEXT` (FR-009, FR-011). `SOURCE` is
`picked` (the person picked a candidate), `confirmed` (they accepted the reading), `corrected`
(they edited it) or `prompted` (written to a shown text); `picked` is stored as `confirmed`. A
client confirms only what the person confirmed; the recognizer never stores its own guesses.
`SAMPLE` is the sample's id (16 hex digits). Reasons for `skip`: `field` (the field doesn't
learn), `learning` (learning is off), `empty` (no strokes or no text), `no-model`, `short` (the
strokes can't hold the text), `fit SCORE` (below). The strokes stay in the session.

A `prompted` sample is checked first: the strokes are aligned to `TEXT` as in `align`, and when
the mean fit is below 0.6 the reply is `confirm ID skip fit SCORE` (the person likely wrote
something else). The client shows that, and when the person keeps the sample all the same, sends
the confirm again with `keep`. A sample that passed has its spans `exact`; one kept with `keep`
has them `inferred`; the reply says which. A prompted sample also stores the writing area
(`area`) and the reading of its strokes at that moment (up to three candidates, before any
adaptation from it), sample format `area` and `reading`.

A `picked` or `confirmed` text that was one of the candidates of the session's last reading is
stored with its place among them and where it came from (sample format `pick`). When the words
or the character model put it there (a near word, the guided search or the character model's
search, below) rather than the ink, it is checked like a prompted sample,
and also every written character must fit at least 0.3; failing, the reply is
`confirm ID skip fit SCORE` and `keep` doesn't override it (the client shows nothing).

## Words (FR-015)

In a prose field (purpose `normal` or `-`, hints only those that change nothing, see Fields) the
readings use the keyboard's word predictor's words for the language
(`$GLASSINE_PREDICT_MODELS/LANG.gpm` or `/usr/share/glassine/predict/LANG.gpm`, the base language
when there is none for a regional one; shell `docs/predictor.md`) and the person's learned words
(`$XDG_DATA_HOME/glassine/predict/words`, removed words left out), read only, picked up again when
they change. Without either, readings are without words. The setting `words = off` turns them
off. Every other field, and `read … raw` and `read … chars`, reads without them. The character
model (below) weighs these readings too.

- A second search of the ink is guided by the words: a prefix inside a word of the list pays
  nothing until the word ends, then as below; a letter that leaves the list makes an unknown
  word that pays per character, so names, numbers and codes still come through. Its ten best
  readings join the ink's own ten best, when the ink scores them within 9 of its best.
- All these readings are scored again: the ink's log-probability, plus 0.3 × (for each
  word of the list, the natural log of its probability after the two words before, from `BEFORE`
  and the reading itself; for every other character but a space, −4.5), plus the character
  model's score (below).
- The ink's best reading stays first, unless another reading of the ink (its own or the guided
  search's) scores 2.5 more and the best
  isn't all words of the list; the ink's best then comes second. The rest follow by score.
- For an unknown word of four letters or more in the three best readings, up to two of the most
  frequent words of the list one edit away (a letter changed, left out, added, or two swapped)
  are added, in the written word's case, when the ink scores them within 9 of its best; never
  first.
- `SCORE` stays the ink's (calibrated) log-probability of the text.

## Characters (FR-019)

The model reads each frame of the ink on its own, so it has no notion of which character is
likely after the ones before ("lig4t" for "light"). The character model of the language,
`chars.gclm` in its base model's directory (below; none: readings are without it), gives the
probability of each character after the ones before it. It is used in prose fields (with the
words) and in fields of purpose `alpha` or `name` (without them), with hints only those that
change nothing (Fields); every other field, `read … raw`, and the setting `chars = off` read
without it.

- Each character of a reading adds 0.35 × max(ln P(character | the characters
  before it), −8) + 1 (the English model looks at the 4 before it). The characters before a reading are the end of `BEFORE`; without
  `BEFORE`, a space (a reading starts as a word does). Nothing is added for the end of a
  reading: one still being written is not unfinished text.
- The floor bounds what one character can cost, so a code, a name or a number such as `4th`
  or `R2D2` that the ink reads clearly still comes through.
- A search of the ink weighed by the character model runs beside the ink's own (width
  8); its ten best readings join the others when the ink scores them within 9 of its
  best. In a prose field the words' guided search is weighed by it too.
- All readings are ranked by the ink's log-probability plus the character model's score (and
  the words' in a prose field). Without words the reading that scores best comes first; with
  words, as under Words, but the margin another reading needs over the ink's best is 1
  instead of 2.5. Either way the ink's best reading is then second.
- `SCORE` stays the ink's (calibrated) log-probability of the text.

**Candidates** (contract 1.4). The best reading of the search the character model weighs (by
the ink, the character model and, in a prose field, the words), and in a prose field the best
of the search the words guide, is always among the candidates when it isn't the ink's best,
also when the ink scores it more than 9 below its best (then it never comes first). Up to
`COUNT`, the candidates are the first, the ink's best when it isn't first, then the character
model's search's best, then the words' search's best, then the others by score (each once;
one that would come later takes the place of the last of the others). With `COUNT` 1 only the
first; with 2 the first and the ink's best (or the next one). A lone dot, dash or tick still
lists its marks first (Size). `detail` names where each came from (`chars`, `guided`).

**Look-alikes.** Letters and digits of one shape (`O` and `o` against `0`, `I` and `l` against
`1`, `G` against `6`) are decided by the field where the ink leaves them open. In a field of
purpose `normal`, `alpha` or `name` each frame's chance of `0`, `1` and `6` is halved, in one of
purpose `digits`, `number`, `phone`, `date`, `time` or `datetime` that of their letters; the
frame's other classes share what was taken, in proportion. So the field's side wins where the
ink gave the other side less than twice its chance, and a character written clearly reads as
written. Not a text field (`-`) and every other field don't lean, nor does `read … raw` or a
fit (a fit takes the better of the leaned and the ink's own chance, as with the size).

Each writer also has look-alikes of their own (contract 1.4). A `confirm` of source `picked`,
`confirmed` or `corrected` whose text has as many characters as the session's last first
reading and differs from it in one or two places, each a letter where the reading had a digit
or a digit where it had a letter (`5` read, `S` confirmed), counts each such pair once: in a
field that learns, with learning on (Fields, Settings), also when the pick is refused (`skip
fit`), never for a prompted sample. A pair counted 3 times, either way round, leans as `O` and
`0` do: its digit in a field for text, its letter in a field for numbers. The pairs are the
store's `lookalikes` (below): a writer's store has its own, the person's never reach it.

## Calibration (contract 1.4, FR-022)

The ink's frames are made surer or less sure by a temperature `T` before any reading is
searched or ranked: each frame's log-probabilities are divided by `T` and normalized again
(log-softmax of the log-probabilities over `T`). `T` above 1 makes a frame less sure, below 1
surer; it never changes which class a frame reads most surely. The temperature in use is the
adaptation's (`gha`, byte 52), else, for the base model's own head, the base model's
(`calibration` beside it, Base models); none (0, or none that fits the model) reads with 1.

- Fitted, as an adaptation is made (Adaptation) and by `calibrate` for a base model (Command
  line), as the temperature in [0.5, 4] whose summed CTC loss of the held-back texts is
  lowest (a golden-section search on log `T`); 1 when none is lower than at 1.
- The size's weights (Size) and the look-alikes' lean apply to the calibrated frames as to the
  ink's own; readings (`read`, `recognize`, all modes), their `SCORE`, the gesture's covered
  characters and the reading stored with a prompted sample are made on the calibrated frames.
- Fits stay the ink's own (the prompted check, the check of a pick, `align`, the letter model
  and neatening's checks): calibration doesn't move what passes.
- `GLASSINE_HANDWRITING_CALIB` (Environment) replaces the temperature, for measurements.

## Size (FR-017)

A version 1 model reads every piece of ink scaled to its own height, so on its own it can't tell a lone
`.` from an `O` or a lone `o` from an `O`. The recognizer compares the ink's real size with a
reference and weighs the model's reading by it. A version 2 model reads the ink at this
reference itself (Base models) and is not weighed: on synthetic lone characters at their size,
weighing it as well lost more than it gained (lone `o` read right 49 % of the time against 92 %
unweighed). The reference below serves both; the weighing only a version 1 model.

- **Reference.** The person's letters: from their newest samples of the language (up to 48 of
  three characters or more and 48 of one letter or digit), the height of each small letter
  (`a c e m n o r s u v w x z`) and each capital (not `J`, `Q`), digit or tall letter (`b d h k l`)
  written with strokes of its own, and the height of the lone ones; their medians, each needing 5 words (or 3
  lone samples; otherwise lone ones count as 1.4 times the word heights, capitals 2.5 times the
  small letters). They are kept in millimetres (samples with `device:PER_MM`), in line heights
  (samples with an `area` line) and in em (`em` samples without an area), and used in the same
  kind of units only: a session with `device:PER_MM` in millimetres, else one with an `area` by
  its LINE, else an `em` one in em. Without enough samples in those units, the line alone (the
  area's LINE, or 1 for `em`; small letters 0.2 lines high, capitals 0.5), which only tells
  dots and dashes apart. Without either, nothing below applies.
- **Glyphs.** Strokes that overlap horizontally (within 0.2 small-letter heights, also through
  other strokes) make one glyph. A glyph no bigger than half a small letter is a dot; one at most
  half a small letter high and at least 2.5 times as wide as high a dash; with the person's
  letters, one at most 0.6 small letters wide and at most 0.6 capitals and 1.8 small letters high
  a tick. Anything else is a letter-sized glyph.
- **Which ink.** The ink of one dot, dash or tick, or of one or two glyphs the model's best
  reading makes one or two characters: each glyph of it. In longer ink only a dot, dash or tick
  written last and standing apart at the end (0.3 small letters from the glyph before).
- **Weights.** In each frame of the model that lies on such a glyph the blank keeps its
  chance; the chance of a character is shared among the characters by weights: on a dot `.`
  first, then `,` and `·`; on a dash `-` (`—` when long, `_` on the line); on a tick `,` and `'`
  (`’`) alike, other quotes after them; letters and digits all but ruled out (−30). On a lone
  letter-sized glyph, with the person's letters, a mark counts −8, and within each case pair whose forms differ mostly in
  size (`c o s u v w x z`, with `0` beside `O`) the pair's chance moves to the form its height
  says: 6 × ln(height / √(lone small × lone capital)), nothing within 1 of the middle, at most
  6 either way (only with the person's letters, and only when each glyph reads as one
  character). `k`/`K`, `p`/`P` and other pairs are left to the shape.
- **The line.** With the area's `BASE`, the guide line nearest a glyph's middle is its line: a
  dot hanging below it reads as `,`, one high above as `·`; a tick down to the line as `,`, one
  high above it as `'`; a dash on the line as `_`; a lone `p`/`P` or `y`/`Y` hanging below the
  line as the small form, one sitting on it as the capital.
- **Readings.** All readings (`read`, `recognize`, with or without words, `raw` and `chars`), the
  prompted fit (`align`, `confirm … prompted`, `donate`) and the check of a pick are made on the
  weighed frames; for a lone dot, dash or tick, the marks its shape allows come first (also when
  the search found none of them), then the other readings. A fit is the better of the weighed
  frames' and the ink's own, so the size never lowers one. `SCORE` is the (calibrated)
  log-probability with the size taken into account.
- The measurements are kept in the store's `size` (below); reading never waits on anything but
  the samples it hasn't measured yet.

## Neatening (spec 003 FR-013 to FR-017)

**neaten** gives the session's strokes back neatened: towards the person's own letters as their
font draws them (the letter model, below) with a straight baseline, a steady slant and even sizes and
spacing. `STRENGTH` is 0 to 1 (`bad-strength` otherwise): 0 gives every stroke back exactly as
it was sent, 1 the neatest version. Four levels have names (FR-014), and a strength between two
of them goes between what they do:

| Strength | Level | What moves |
|---|---|---|
| 0.25 | Light | the line only: baseline, slant, size and spacing (each letter keeps its shape) |
| 0.5 | Medium | Light, and every letter whose glyph has its structure pulled half way to the glyph the font draws there, also one written badly |
| 0.75 | Strong | the same, pulled 0.85 of the way |
| 1 | Neatest | every such letter takes that glyph's shape, on its own points |

Below 0.25 the line-level corrections apply in part (0.1: 0.4 of them). The reply has one `N HOW POINTS` triple per stroke of the
session, in the session's order: `N` the stroke's number, `POINTS` the stroke with the same
points in the same order, each point's `x,y` moved (written to a thousandth) and the rest of it
(time, pressure, tilt) exactly as sent. `HOW` says what was done: `pulled` (a character of it
moved towards its glyph, or at Neatest given its shape, and the
line-level corrections), `line` (the
line-level corrections only) or `kept` (the stroke as sent). `TEXT` (escaped) is the reading the
neatening followed, empty when none. `WHY` (contract 1.5, FR-041) is the last field: per
character of `TEXT`, space-separated, whether it was pulled or why not: `pulled`; `level` (the
strength pulls no letter, or this one not yet at it); `not-covered` (the font has no glyph
for it there); `no-structure` (its pieces aren't its glyph's strokes, in count or place, or
its glyph can't be traced on its points);
`shape-guard` (pulled it would come nearer another character's versions, below);
`low-confidence` (its word is read with low confidence, below); `drawing` or `gesture` (its
strokes are drawing, or the newest stroke a gesture); `no-ink` (no ink of its own); `space`
for a space. Empty when `TEXT` is. A client of 1.4 or before reads the fields by place and never gets to
it; the samples app shows nothing of it yet. The same strokes, letter model and strength always give
the same reply. `neaten` changes nothing in the session (its strokes, the last reading's
candidates) and never stores anything: neatened ink is not a sample (FR-016, FR-024).

- **The reading.** Without `TEXT`, the session's best reading (as `read` gives it, with what
  the field reads with) says which strokes make which character; with `TEXT` (the text the
  person confirmed or picked), the strokes are aligned to it as in `align`; with `ALIGN` too (as
  `read` gives it for that text: per character the strokes or `N:A-B` parts), that alignment
  is used as it is, trusted (`bad-align` when it doesn't parse; `bad-text` for an empty or too
  long `TEXT`).
- **Writing only (FR-020).** The newest stroke when it is a gesture, strokes taller than 3.5
  x-heights (the person's letters for the size, else the strokes' own median height) and
  strokes no character of the reading has are drawing and stay `kept`.
- **Per word (FR-017).** Without `TEXT`, a word whose characters' mean fit (as in `align`:
  how well the ink fits the reading, not whether the dictionary has the word) is below 0.15
  is read with low confidence: its characters stay at the line level (`low-confidence`), the
  other words are neatened. A word the dictionary lacks, a name, is not low confidence by that
  alone. With `TEXT` the text is the person's: no word is held back for its reading, only a
  character whose strokes can't be split into its pieces (`no-structure`).
- **Line-level corrections (FR-014, FR-017).** For every character of the writing: the line's
  baseline is fitted through the bottoms of the letters that sit on it (fit at least 0.5,
  outliers more than 0.3 x-heights off left out) and straightened to its height at the line's
  middle; such a letter moves onto it when its bottom is within 0.35 x-heights (one further off
  is more likely a descender read wrong); the slant of each character moves to the letter
  model's (without one, the line's own), at most 0.4; letters read with a fit of 0.5 or more are
  scaled to the line's x-height (by their class: small letters, tall letters, capitals and
  digits; 0.85 to 1.18); each pair of neighbouring letters moves towards where the letter model's spacing places it
  (below: its middles' distance, guarded by its gap; profiles and middles taken from the ink as
  the line-level map and the pull leave it; without a letter model, towards the line's median
  profile gap), and the
  gaps between words (box to box) towards the letter model's word spacing (without one, the
  line's median), at most 0.3 x-heights each. Characters joined in one stroke move together.
- **The target (FR-035).** Each character's target is exactly the glyph the font draws for
  it at that place in the text (`TEXT`, else the reading followed): the variant of the font's
  cycle at that step (FR-010: one step per character or space along a run, a character the
  font lacks starting the run again), unless a neighbour rule picks another (FR-038: the first
  of its rules whose neighbour is beside it), and for two letters the font joins (FR-039) the
  pair's one glyph, both letters together. It is the variant itself (a version left out isn't
  in the font), not its best version (below), which neatening doesn't use. The font, its
  preview and neatening choose the glyphs the same way (`hw_letters_glyphs`).
- **The pull (FR-014, FR-035, FR-036).** From above Light, a character with a fit of 0.5 or
  more (or from `ALIGN`) goes towards its target. Its
  pieces are its runs of points: a stroke of its own, or its part of a stroke joined with a
  neighbour. Without `ALIGN` the alignment's edges are put right first: points of no character
  go with their neighbours, a run of one character shorter than 0.35 x-heights along the stroke
  goes to the run next to it (the longer one), and a character with a count of pieces no
  version of it has (more than some) gives a run it shares with a neighbour to that neighbour.
  Then the strokes are put right by where the ink lies as for the letter model, so a dot or
  bar written after the word belongs to the letter it lies across, not to the word's last one
  (five x-heights standing in for the area's line); a stroke joined from two characters' parts
  keeps its parts. `ALIGN` is used as given. Its structure is tested coarsely, the shapes
  don't matter: as many pieces as the glyph has strokes, each about where its stroke lies in
  the glyph (its middle within 0.45 x-heights, both about their middles), and its ink's
  middle within 0.75 x-heights of the glyph's above the baseline (so ink the alignment gave
  the wrong character, a letter for the mark above it, isn't pulled into that one's glyph).
  A letter written
  in another stroke order or cut otherwise at a joint doesn't have it, and stays at the line
  level (`no-structure`). Nor does one whose glyph its points can't trace: when the glyph,
  traced on the ink's own points (all the way, and as far as the level pulls), gives a turn
  over 90 degrees where the ink turns by less than 55 within five points, or a step back where
  the ink has none (a loop or a turn the ink doesn't have, drawn with too few points), the
  piece is traced again by the length along it (its points spread over the glyph's stroke as
  over the ink's); still so, the letter stays at the line level. A piece whose matched places
  don't reach its glyph stroke's ends (within a tenth of its length, 0.3 at a joint) is traced
  by the length along it too: the glyph is drawn whole, not a part of it on all of the
  piece's points.
- **Along the stroke (FR-036).** Each point goes to a place on its stroke of the glyph, as a
  share of that stroke's length: both are resampled every 0.04 x-heights (8 to 128 points) and
  matched in order (dynamic time warping) by where the points are, the direction of the stroke
  and how it turns, so a point is matched to the part of the glyph written the same way, not
  just the nearest one. The places never go back along the stroke, are smoothed over a few
  points and spread at most 2.5 times as fast as evenly (a part the stroke lacks is traced, not
  cut across). A hook, loop or overshoot at a start or end the glyph doesn't have (up to a
  quarter of the stroke's length, at most 0.6 x-heights) goes to the glyph's end, so it is
  folded in, not dragged across the letter. The matching is done twice: the second time against the glyph
  mapped onto the ink as the first gives it (moved, stretched and sheared, not mirrored; this
  map only finds the places). The glyph itself is only moved and scaled: placed on the straight
  baseline at the character's middle (moved by the matched points' mean offset, at most 0.5
  x-heights), at the line's x-height, with the lean the font draws it with. A point moves by
  the line-level corrections (fully from Light) and then by the pull × (its place on the glyph
  − where the corrections put it): 0.5 at Medium, 0.85 at Strong, 1 at Neatest, going between
  them linearly (from 0.25 to Medium, Medium to Strong, Strong to Neatest). At Neatest a
  letter's points lie on its glyph, as many as it had, each with its time and pressure.
- **Joined letters (FR-036).** Where a stroke passes from one character to the next, the
  joint's ends are matched with the glyph's ends left open (a lead-in or lead-out the
  glyph doesn't have follows the matched point next to it), and each point near the joint
  takes the moves of both characters, weighted by how near it is to each one's points (over
  0.15 x-heights to either side, wider where the two characters' moves differ more: the move
  changes by at most half the length along the stroke, up to 0.8 x-heights), so the stroke
  doesn't jump, kink or step back where they meet.
- **The flow (FR-036).** The neatened strokes are checked as they come out: a turn over 90
  degrees where the ink turns by less than 55 within five points, or a step back (against the
  way the stroke goes over three points to either side) where the ink has none, measured at
  0.75 of the line's x-height, is new; each pulled character with points within a joint's
  blend (0.8 x-heights along the stroke) of one goes back to the line level (`no-structure`)
  and the line is neatened again, as for the shape guard (below). An alignment that cuts a
  joined stroke elsewhere than the letters meet so gives none of the letters a kink.
- **Spacing with the pull.** A pulled character's profile and middle for the spacing above go
  towards its glyph's as far as it is pulled (a joined pair's two letters towards the pair's).
- **Without a letter model** only the line-level corrections apply (FR-022).
- **The shape guard (SC-004, FR-037).** Each pulled character's neatened ink must be nearer one
  of its own versions than any version of another character, whatever their strokes: each as
  one sequence of 48 points along its strokes in writing order (each stroke as many as its
  share of their length, a pen lift no part of it), centred, at the line's x-height, compared
  point by point (RMS). Ink written nearer another character already (a hasty letter can be)
  may stay so, but the neatening may not take it further that way than it was written. One
  that fails goes back to the line level alone (`shape-guard`), the others keep their pull,
  and the line is neatened again (with the flow check, at most three times). A joined pair, the font's own glyph for
  both, isn't checked. Nothing is read again, and no check holds back the whole line.

**recognize** is `begin`, a stroke per `;`-separated part of `STROKES`, `read`, `end` in one line,
for a client that has the whole ink at once. It stores nothing.

**samples** lists the ids of the samples this client confirmed, in the order they were stored;
`all` lists everyone's and needs the client in the `readers` setting. **list** is the same
listing with, per sample, its confirmation time (seconds since 1970), source, language, text and
the first reading kept when it was written (`READ`, empty when none), both text fields escaped:
what a client shows of its samples without fetching each one.

**mark** (spec 003 FR-001, FR-002) says how session `S`'s writing is meant: `WRITTEN` `careful`
(written to "Write it your best"), `quick` (the quick half of a pair) or `-`, and `PAIR` the link a
quick and a careful sample share (16 hex digits the client makes at random) or `-`. Samples the
session keeps from then on (`confirm`, `donate`) carry them as `written` and `pair` (sample
format); it lasts until the session ends or the next `mark`. Anything else is `bad-mark`.
**marked** lists the client's samples that carry a marking or a link (with `all`, everyone's, as
for `samples`), `-` for the part they don't have. A pair whose other sample was deleted links
nothing.

**prefer** (FR-003) records that of two of the client's samples the person chose `CHOSEN` over
`OTHER`; with `CHOSEN_CHAR OTHER_CHAR`, one character of each, by its place in the sample's text
(0-based code points). Errors: `unknown-sample`, `denied` (another client's sample, unless a
reader), `bad-char` (no such character, or the same sample and character twice). "Both fine" and
"Skip" record nothing: the client sends nothing. **preferences** lists the client's preferences
whose two samples are both stored (`-` for a whole sample). Deleting either sample takes the
preference with it; so does erasing them. **sample** returns one
sample's line (sample format v1, escaped as a text field): the client's own, or any with
`readers`. Both need a client name from `hello` (`no-client`) and are what an app that builds a
font from the person's writing uses.

"Donation" is the internal name of what the person sees as a contribution (spec 001 FR-014).

**donate** (FR-014): with a donation started (`glassine-handwriting donation start`), adds the
session's strokes with `TEXT` (the prompt) to the donation, separate from the person's samples,
checked and stored like a prompted `confirm` (`skip fit SCORE`, `keep`). `TEXT` must be one of
the prompts shipped for the language: a line of the model's `prompts.txt` or of a `.txt` file in
its `prompts/` directory (below); else `skip prompt`. `COUNT` is the number of donated samples,
`SAMPLE` the donated sample's id: the same as a `confirm` of the same strokes and prompt gives, so
a client can take both back with one `forget`. `skip off`: no donation started.

**forget** takes back one of this client's samples (a Discard right after it was kept): it is
deleted from the samples, and from the donation when it is there too. `WHERE` is `samples`,
`donation` or `samples,donation`. A sample of another client is `denied` (unless the client is in
`readers`); an id in neither is `unknown-sample`. Needs a client name (`no-client`).

Error reasons: `bad-line` (wrong number of fields, a bad number), `bad-id`, `bad-session`,
`bad-client`, `unknown-verb`, `bad-escape`, `bad-units`, `bad-lang`, `bad-field`, `bad-points`,
`bad-count`, `bad-area`, `bad-prompt` (empty or more than 512 characters), `bad-source`, `bad-strength`,
`bad-text`, `bad-align`, `unknown-session`,
`too-many` (strokes in a session), `busy` (sessions), `no-model` (no model for any of the
languages), `no-client`, `denied`, `unknown-sample`, `too-long`, `store` (the store can't be read
or written; nothing changed), `memory`.

## Fields (FR-011)

The recognizer applies the rule itself, whatever the client sends:

| Field | Reads | Reads with | Learns |
|---|---|---|---|
| purpose `password` or `pin`, or the hint `sensitive_data` or `hidden_text` | no (`private`, nothing kept) | – | no |
| purpose `normal` or `-` | yes | words and the character model | yes |
| purpose `alpha` or `name` | yes | the character model | yes |
| purpose `email` or `url` | yes | the ink alone | only when the keyboard's `addresses` setting is `learn` |
| `terminal`, `digits`, `number`, `phone`, `date`, `time`, `datetime` | yes | the ink alone | no |
| a purpose or hint this version doesn't know | yes | the ink alone | no |

The hints `completion`, `spellcheck`, `auto_capitalization`, `lowercase`, `uppercase`,
`titlecase`, `latin` and `multiline` change nothing. A FIELD with characters other than lowercase
letters, `_` and `,` is `bad-field`.

## Settings

`$XDG_CONFIG_HOME/glassine/handwriting.conf` (or `~/.config/glassine/handwriting.conf`),
`KEY = VALUE` lines, the last one of a key wins. Read at each request that needs them.

| Key | Values | Default |
|---|---|---|
| `learning` | `off` (also `false`, `0`) stores no samples and adapts nothing | on |
| `languages` | languages in order (`en,de`), used when a request's `LANGS` is `-` or empty | unset: the locale, then `en` |
| `readers` | client names, comma-separated, that may read every client's samples | none |
| `words` | `off`: readings without words (Words) | on |
| `chars` | `off`: readings without the character model (Characters) | on |
| `models` | a full path: the base models' directory instead of `/usr/share/glassine/handwriting` (another set installed beside the default one, Base models); taken at the next request | unset |

The keyboard's `predict.conf` (`learning`, `addresses`) counts too: learning is off when either
file says so, so the keyboard's one learning switch covers handwriting (shell spec 006 FR-008).
Turning learning off keeps what was stored; erasing is separate.

## The store

`$XDG_DATA_HOME/glassine/handwriting` (or `~/.local/share/glassine/handwriting`): directory
0700, files 0600, created by the first write, never by a read or a private field. `lock` is the
lock (`flock`, exclusive) every writer holds; files are replaced through `NAME.tmp` and a rename.
Nothing in it leaves the device except by export or a donation the person writes out.

- `samples.jsonl`: one sample per line, sample format v1. The recognizer stores `spans` from its
  own alignment (`spans_source` `inferred`) and times starting at 0. Samples are appended; a
  deleted one is cut off the end of the file or, when other samples follow it, overwritten with
  spaces where it is (a line of spaces is no sample), and the file is written anew without those
  once they are a quarter of it. A line that is not a whole JSON object (zero bytes or a line cut
  short by a crash) is no sample either.
- `samples.meta`: first line `glassine-handwriting-meta 1 GEN` (format, version, erase count),
  then one line per sample, tab-separated: `ID CLIENT TIME SOURCE LANG` (`TIME` seconds since
  1970; `CLIENT` `-` when none was named). `ID` is the sample's id: the first 16 hex digits of a
  hash of its line, so the same sample is stored once.
- `samples.index`: derived from the two files above, so listing, counting, looking up and
  deleting never read the whole of `samples.jsonl`. First line `glassine-handwriting-index 2 INODE
  GEN` (the inode of `samples.jsonl` and the erase count it belongs to), then one line per stretch
  of `samples.jsonl` in file order: `ID OFFSET LENGTH CLIENT TIME SOURCE LANG TEXT READ MARK` for a
  sample (its line without the newline; `TEXT` and the first reading `READ` escaped as text
  fields; `MARK` its `written` or `-`, then `:PAIR` when it has a pair link), `- OFFSET LENGTH` for
  bytes that are no sample. An index of another version is made again. It is checked against the files at every
  open (inode, erase count, the last sample's id and place); lines after the last one it covers
  (written by an earlier version, or an index line lost in a crash) are added from the file, and
  an index that doesn't fit is made again from the file (also when a sample it lists no longer
  begins its line). A deletion writes the index (for the next erase count) first, then the file,
  then `samples.meta`, so a crash on the way leaves the sample with its client and time or takes
  it out. A store without an index gets one at its first open.
- `size`: the person's letters as measured for the size (above): first line
  `glassine-handwriting-size 1`, then `ID KIND V0 V1 PER_MM LINE EM` per sample in use (`KIND` 0
  nothing to measure, 1 a word: its small-letter and capital heights, 2 a lone small letter, 3 a
  lone capital, digit or tall letter: its height in `V0`; the sample's units per millimetre and
  area line, 0 when unknown, and `EM` 1 for `em`). Derived like the index: made again from the
  samples when it is missing (written by a reading that needs it, skipped while another process
  holds the lock); a deletion takes the sample's line out with it, an erase removes the file.
- `preferences`: one line per preference, `CHOSEN OTHER CHOSEN_CHAR OTHER_CHAR` (sample ids;
  `-1` for a whole sample). A deletion takes a sample's preferences out before the sample.
- `marks`: the person's marks on the letter model (spec 003 FR-031), one line per sample and
  character, `ID CODEPOINT MARK` (tab-separated, the code point in hex, MARK `out`, `way-out` or
  `favourite`). A deletion takes a sample's marks out with it, an erase with the samples (all, or
  the client's). Never exported or donated.
- `lookalikes`: the person's look-alike picks (Characters, Look-alikes), one line per pair,
  `SAMPLE CLIENT READ CHOSEN` (tab-separated; `SAMPLE` the sample the pick made, `-` for one
  refused; `CLIENT` `-` when none was named; `READ` and `CHOSEN` code points in hex), the
  newest 4096 kept. A deletion takes the sample's lines out with it, an erase of a client's
  samples that client's lines, an erase of all samples the file. Never exported or donated.
- `adaptation/LANG/current`, `previous`: the adaptation in use and the one before (below);
  `state` (`SAMPLES GEN TIME ID`: what the last run saw, the confirm time and id of the newest
  sample in it, so it doesn't run again for nothing and can count what came since); `log` (one
  line per run: `TIME kept|rejected TRAIN HELD ERROR_BEFORE ERROR_AFTER`).
- `letters/model`: the letter model (below); `letters/usable` (its usable samples, below); `letters/font` (`NAME VERSION PATH`, tab-separated:
  the installed font, PATH its Regular style's file; then `style STYLE PATH` for Calligraphy and Brush and `nib DEG`,
  the nib angle it was made with, and `random on` when made with random alternates; a font installed before the styles has the first line alone) and `letters/font-version` (the last version made, so a removed and
  remade font doesn't reuse one) and `letters/font-model` (a hash of the letters the installed
  font was made from). An erase of all samples or of a client's takes out the letter
  model, the installed font and these files (the erased samples' preferences go with them).
- `donation/licence` (`CC0-1.0` or `CC-BY-4.0`), `donation/samples.jsonl` (kept like
  `samples.jsonl`) with its index `donation/index` (first line `glassine-handwriting-donation-index
  1 INODE 0`, then `ID OFFSET LENGTH` or `- OFFSET LENGTH`), and `donation/donor-id`: the donor ID,
  a random UUID (version 4, from the system's random source, nothing about the device or the
  person in it), made when a donation is first started and kept when a donation is erased.

A running recognizer picks up a new adaptation at its next reading.

## Writer stores (contract 1.3, FR-021)

Contract 1.3 adds stores for guest writers of the samples app (spec 002 FR-014 to FR-025). A
program of contract 1.2 has none of this: it takes `--writer` and `writer` as a usage error
(exit 2), so a client can tell. `hello` still answers 1.

**Where.** `$XDG_DATA_HOME/glassine/handwriting-writers/WRITER/` (or
`~/.local/share/glassine/handwriting-writers/WRITER/`), beside the person's store, never inside
it: directory 0700, files 0600, laid out and locked like the person's store (above), with its
own `lock`. `WRITER` is 16 lowercase hex digits from the system's random source, made by the
recognizer when it creates the store; it says nothing about the writer, and is not the
writer's donor ID. A writer's name never reaches the recognizer; the samples app keeps it.
`handwriting-writers/` itself (0700) holds only the stores and, while one is being removed,
`.removing-WRITER`.

**Naming the store.** A writer's store is used only when named, with `--writer WRITER` before
anything else: for the line contract `glassine-handwriting --writer WRITER [-i IDLE_SECONDS]`,
so every request of that process is for that store, and for every command,
`glassine-handwriting --writer WRITER samples …`. No variable chooses a store; without
`--writer`, everything is the person's store, as before. A `WRITER` that isn't a store (never
made, or removed) is refused: a process exits 1 at start with `unknown-writer` on stderr, a
command exits 1 the same way, and a request of a running process whose store was removed
meanwhile gets `error ID unknown-writer` and keeps nothing.

```
glassine-handwriting writer new               create a store; prints WRITER
glassine-handwriting writer list              WRITER SAMPLES DONATION per store
glassine-handwriting writer remove WRITER     delete that store for good; prints removed
```

`writer remove` takes the store's lock, renames it to `.removing-WRITER` and deletes that, so
the store is gone from its name when the command returns; a `.removing-*` left by a crash is
deleted at the next `writer` command or open of any writer store. A process of the removed
store finds it gone at its next request.

**What a writer's store does.** Everything the person's store does for samples, preferences,
marks, the size measurements and the donation, with the same requests and commands: `confirm`,
`donate`, `forget`, `samples`, `list`, `mark`, `prefer`, `sample`, `samples stats|list|erase|
delete|export`, `donation …` (its own `donation/donor-id`, made at its first `donation start`,
kept when its donation is erased, replaced by `donor new`), `read`. The person's `learning`
setting doesn't apply: a writer's prompted samples are kept whatever it says. Readings are the
base model's alone, with the writer's own size measurements (Size), the language's word list
and the character model; never the person's adaptation, learned words (the predictor's
`words` file is not read) or letter model. `words`, `chars` and `languages` from
`handwriting.conf` apply as for the person.

**What it refuses.** For a writer's store: `neaten` gets `error ID writer-store`; `adapt`,
`adaptation`, `letters …`, `font …`, `learning` and `samples import` exit 2 with `writer-store`
on stderr. An idle exit of a writer's process starts no adaptation.

**The person's store.** `samples stats` without `--writer` also prints `writers N`, the number
of writer stores, so the settings app can say they are kept. `samples erase`, `samples export`
and `import` without `--writer` cover the person's store only; no erase of the person's store
touches `handwriting-writers/`.

**The service.** The handwriting service (docs/service.md) serves the person's store only; the
samples app reaches a writer's store through a process or command of its own with `--writer`.

**Moving a store into a writer's store.** A store kept elsewhere (another `XDG_DATA_HOME`'s
`glassine/handwriting/`) becomes a writer's store by hand, with no recognizer running for
either: `writer new` gives `WRITER`; then everything in the old store except `lock`,
`adaptation/` and `letters/` (what a writer's store has none of) is copied into
`handwriting-writers/WRITER/`, keeping the names, directories 0700 and files 0600. The writer's
samples, preferences, marks, size measurements and donation, with its `donation/donor-id`,
are then that store's; `writer list` counts them.

## Adaptation (FR-009)

`glassine-handwriting adapt` trains a small head (a residual adapter on the encoder's output and
the output layer; the encoder stays as shipped) on the person's samples of one language. Samples
the base model was trained on (confirmed at or before the time its file records, below) are left
out: neither learned from nor held back, so a head learns only what the base model hasn't seen and
its check isn't made on samples the base model already knows. Of the rest, one sample in five (by
its id) is held back; the run keeps the new head only when its character error
on the held-back samples is lower than that of the head in use (the base model's, or the current
adaptation's). Then the current one becomes `previous`. A new head is calibrated on the same
held-back samples (Calibration) and keeps its temperature; when the run has nothing new to
learn from, or the new head isn't kept, a head in use without a temperature (made before
calibration) gets one from the held-back samples, nothing else in it changed. It needs 40 samples to learn from and 10
held back, runs only when something changed, and, unless `-f`, only while charging and idle, and
stops when the charger is unplugged. With `-r` the person asked for it in a client (spec 002):
charging is enough, and it runs at the lowest priority so the device stays usable. An adaptation belongs to one base model file (its hash is in
the file); after a model update it is ignored until the next run. An erase while it runs drops its
result.

## The letter model and the font (spec 003)

`glassine-handwriting letters build` makes the letter model from the person's prompted samples:
for each character, a version for each way they write it often enough (up to 5). A character's sample is usable
when its spans are exact (`spans_source` `exact`, as the samples app stores them), or the sample
is one character alone kept with `keep` after a failed check (its strokes can only be that
character's: lone full stops and commas fail the check most often), and its strokes
are its own (not shared with a neighbour, so joined letters are never cut apart); a character is
covered with 3 usable samples (unless none of its versions can be measured: no baseline or
x-height to put it on). The spans follow the order of writing, split halfway between where the
recognizer saw two characters, so before a character's strokes count as its own they are put
right by where the ink lies:
a stroke the alignment put with several characters goes to one of them alone when it lies
beside the other strokes of another (across at most a quarter of the narrower of the two, and
not under ink above it, so an i's dot keeps its stem), such as a letter's stroke in a full
stop's span; and a short stroke (at most 0.4 area lines, or half the sample's median stroke
without an area) written after the strokes it belongs with goes to the character it lies
across (its middle over that character's ink, or over half of the narrower of the two, or
touching its side), from the top of that character down to its baseline (rising at most that
character's height above it, or above the rest only where the character has ink above: i, j,
`:` `;` `!` `?` `=` `%` `"` and accented letters); a dot or accent above an i, a j or an
accented letter goes to the nearest one up to two of its heights (or a fifth of the area's
line) to either side. Of several, the one whose ink its middle is over, then one it lies
across, then one it touches; among those the one it is centred over. That covers an i's or
j's dot, the bar of a t or an f, the arms of an E, the bar of an H and the second stroke of an
x or a k, in words as well as alone. A stroke that is all a character has stays with it.
Its samples are grouped by stroke count, each resampled by arc length (strokes in the order
written) and centred; within a stroke count, the ways of writing it are the samples within 0.6
x-heights (RMS) of each other, taken greedily from the one with the most such company, so a
different stroke order or shape is another way. A way gives a version when it holds at least 3
samples and at least a tenth of the character's (a slip, a lead-in or a neighbour's part stays
out; with no such way, the largest one). Each sample is weighted by how it was written (careful
above quick), the person's picks (the chosen character or sample up, the other down), how well it
was read (its stored first reading against the prompt) and how close it is to its way's typical
shape. Each way's seed is its most supported sample, the most used way first; while a character
has fewer than three versions, further strong samples of its ways that differ from those chosen
and have company seed more (so repeated letters don't look stamped). A version is the weighted
average of the samples near its seed, smoothed, so a single sample's jitter is gone; its `use`
is how many samples of its ways are nearest to it of the versions with their strokes, and the
versions are stored most used first. Sizes are in x-heights from the baseline of each sample's area (or of its
own letters); the line metrics (cap height, ascender, descender, slant, ink width as the app's
pen draws) come from all usable samples.

**How it was written** (FR-006, for the font's Brush style): each version keeps, per stroke, its
samples' pressure and speed at 16 points along the stroke's arc length (from its start to its
end, each sample's stroke stretched to the same length), averaged with the weights that make its
shape and smoothed once by [1 2 1] / 4, each relative to the person's median over all their usable
samples (1 = their median; stored in the metrics as `pressure_median`, the pen's own units, and
`speed_median`, x-heights per second). The speed at a point is the distance over the time of the
pen's points 2 to either side of it. A sample whose pen gave no pressure (points of 3 values) or
one pressure throughout counts for speed only; a version none of whose samples gave pressure has
no pressure profile (and one without times, none of speed). One character's build takes the
medians from the model.

**Spacing** (spec 003 FR-007; `spacing.c`), one for the font and neatening. A version's ink
profile is, per height band (8 bands from −0.6 to 1.6 x-heights, the outer two open-ended), the
leftmost and rightmost x of its strokes' centre lines, every band a segment crosses. The gap
between two letters is the narrowest distance between the first's right profile and the
second's left one over the bands both have ink in (their boxes when they share none). From
neighbouring letters of the word samples (each with strokes of its own, placed by where they
lie; gaps beyond −0.6 or 1.6 x-heights are the alignment's errors and left out) come: the
typical gap (the median); a pair's own gap (the median, written at least 3 times); the side
shapes, the versions' left and right sides grouped by their shape (per band, how far in from
the side's outermost ink, at most 1; within 0.3 x-heights RMS, at most 16 per side, greedily
by use), and a pair of side shapes' gap (the median, seen at least 5 times); how much closer
letters stand where their profiles leave more open space (`open`, the least-squares slope of
the gap on the mean open space beyond the narrowest, at most 1). A pair's gap is its own, else
its side shapes', else the typical gap less `open` times its open space beyond the median one
(`open0`), within 0.3 x-heights of the typical gap. Word spacing is the median box gap between
words. Where a pair goes is by its letters' middles: a version's middle is the mean x of its
ink between the baseline and the x-height, each piece weighted by how far it runs up or down
(stems and bowls count, bars, hooks and connecting strokes hardly do). From the same
neighbouring letters come a pair's distance (the median from the first's middle to the
second's, written at least 3 times) and each character's reaches (the distance as the first's
right reach plus the second's left reach, by alternating medians, for a character on that side
of at least 3 pairs). A pair is set at its distance, else at its characters' reaches, applied to
the middles of the versions used; then, where between the baseline and the x-height (the
bands from −0.05 to 1.05) their profiles come closer than its gap, or than half the typical gap
when that is smaller, moved apart to it. A pair without a distance (or a version without ink
running up or down in the x-height band) is set at its gap. A version sits on the line where the
samples it is made from sit on average (with the weights that make its shape). The same samples and picks make
the same model, byte for byte except the build time, in any order they were stored.

It is made again when its inputs (the prompted samples and the picks, and the way it is built: a
revision of the build, `HW_LETTERS_BUILD` in `letters.h`, counted on when the model changes, as
for the profiles) changed: after a background adaptation (the same conditions: charging and idle), or when asked
(`-r`, any power; at the lowest priority, so the device stays usable; a client asks while it
shows a page about the letters, spec 003 FR-008). `-f` makes it even when nothing changed.

**Marks (FR-031).** The person has a say per character, kept in `marks`:
- `out`: the sample is left out of that character. It isn't a usable sample of it (not counted,
  shapes no version); it stays a sample for everything else (the reader's adaptation, base
  training, the donation).
- `way-out`: the sample belongs to a version the person left out. A version at least a third of
  whose samples (by `use`, below) are marked so is kept in the model marked `left_out` (samples
  written that way later join it without bringing it back): it is not in the font's
  cycle, not in the font and no target for neatening. A character with no other version isn't in
  the font (it stays covered in the counts).
- `favourite`: the sample belongs to the person's favourite version. The version (not left out)
  with the most samples marked so is the favourite (one per character) and counts 2 times its
  use in the cycle.

**The best version (FR-035).** Each version (not left out) has a best version: the same strokes
with the same points, made from the version's samples as the version is but with the person's
judgement counted in: a sample marked `favourite` counts 4 times, one written to "Write it your
best" (`careful`) 2 times and a `quick` one half, and per preference a sample chosen counts 2
times, one not chosen 0.3 times (for one character, the preferences that name it or the whole
sample). Neatening doesn't use it (its target is the glyph the font draws, above). When the samples all count alike, or it differs
from the version by less than 0.005 x-heights (RMS), it is the version itself and isn't stored.

Marks name samples rather than versions, so they keep their meaning when the versions are made
again: the client marks a version's `ids` (below); a model whose versions have no `ids` (built
before the marks) is outdated. A change of marks makes the model outdated;
it changes the font only at the next `font install`.

**One character (FR-032).** `letters build -c CHAR` learns one character again from its samples
and marks as they are now (any power, lowest priority; only the samples with it are read, so it
takes a second or two), puts its line in the model in place of the old one and keeps the rest
(the spacing, the other characters and the model's inputs). It prints `relearned COVERED
VERSIONS` or `skip no-model` (it needs a model); `--progress` first prints `progress DONE TOTAL`
lines as `letters build` does. Its versions get their side shapes from the
model's spacing.

The usable samples per character are known without a model (FR-030): `letters/usable` holds,
per prompted sample, the characters it has with strokes of their own, by the same rules as the
build. It is derived like `size`: a count or a build writes it (skipped while another process
holds the lock), a sample it lacks is read once, and it is made again when the way the usable
samples are counted changes. `letters status` counts from it, so its figures are the model's as a build would
make them now, at once.

**Model file** (`letters/model`, `letters show`): JSON lines. The first
`{"format":"glassine-handwriting-letters","version":1,"inputs":"HASH","samples":N,"built":TIME}`,
then `{"metrics":{"x_height":1,"cap_height":…,"ascender":…,"descender":…,"slant":…,
"letter_spacing":…,"word_spacing":…,"ink_width":…,"x_height_per_line":…,"base_below_guide":…,
"x_height_mm":…,"pressure_median":…,"speed_median":…}}` (the spacing's typical and word gaps;
`x_height_per_line`, `base_below_guide` and `x_height_mm` what a sample without letters to measure
its x-height or baseline from takes, which a one-character build reuses, as it does the two
medians, 0 when none), then
`{"spacing":{"letter":…,"word":…,"open":…,"open0":…,"pairs":[["a","b",GAP,N,DISTANCE],…],
"sides":[[RIGHT,LEFT,GAP,N],…],"reach":[["a",RIGHT,LEFT,NR,NL],…],"left_shapes":[[…8],…],
"right_shapes":[[…8],…]}}` (`DISTANCE`, `RIGHT` and `LEFT` null when not learned), then per
character seen `{"char":"a","samples":N,"covered":true,"variants":[{"width":…,"weight":…,
"members":N,"use":N,"lc":L,"rc":R,"left_out":true,"favourite":true,"ids":["ID",…],"left":[…8],
"right":[…8],"middle":…,"strokes":[[[x,y],…],…],"best":[[[x,y],…],…],"pressure":[[…16],…],
"speed":[[…16],…]}],
"slots":[V,…],"rules":[{"prev":"o","variant":V,"n":N,"same":N,"hits":…,"cycle":…},…]}` (x from the version's left
edge, y up from the baseline, in x-heights; `left` and `right` its profile, null for a band
without ink, `middle` its middle (null without one), `pressure` and `speed` how it was written,
16 values per stroke (above; each left out when the version has none),
`lc` and `rc` its side shapes; `best` its best version, as `strokes` (only when it differs; the
font draws `strokes`, so it doesn't change the font); `left_out` and `favourite` only when so;
`ids` the samples written
this way, those its `use` counts, sorted; no versions when not covered). A character with marks
has `"marks":"HASH"` before `variants`, the hash of its marks when it was built. `slots` is the
font's cycle (10 steps): the version the character shows at each step, each about as often as it
is used (a favourite twice as often, a left-out one never), spread out (smooth weighted round
robin); a model without it shows its versions in turn. `rules` (only when there are any) are its
neighbour rules, below, the strongest first: `prev` or `next` the neighbour, `variant` the
version, `n` the times written beside it, `same` those written as that version, `hits` and
`cycle` the held-back check. Then per joined pair, after the characters, `{"joined":"th",
"samples":N,"written":N,"of":N,"variant":{…}}` (`variant` as a character's, without `best`).

**Neighbour rules (FR-038).** For each character and each character written right before it
(`prev`) or right after it (`next`, a space is no neighbour), in the usable samples, the build
counts which version each occurrence is written like. A rule needs all of: written beside that
neighbour at least 6 times; the same version in at least three quarters of them, and in at least
a quarter more of them than that version's share of the cycle (a version written that way
everywhere is no way of writing it there); and a check on samples the rule is not made from:
leaving out one sample at a time, the version the other samples write most beside that neighbour
matches the left-out sample's occurrences (`hits`, summed) more often than the cycle does
(`cycle`: each occurrence matched with the share of the cycle's steps that show its version).
Versions left out count for none, and a rule for a version left out is gone. A character has at
most 16 rules; where two apply, the first wins. The character's page shows the cycle's share
("6 in 10") as before: a rule changes only the places it names.

**Joined pairs (FR-039).** For each pair of neighbouring letters (no digits, marks or
symbols) in the samples with known strokes (both with ink, neither left out of that sample),
the pair is written joined only where the ink plainly runs on from one letter into the next:
- exactly one stroke is in both spans;
- its first points, with the first letter's other strokes, are written like one of that
  letter's versions, and its last points, with the second letter's other strokes, like one of
  the second's. Written like means as close (centred RMS at the x-height, as neatening
  measures it) as 95 % of the letter's own samples are to their nearest version, times 1.25
  (those samples shaped the versions they are measured against), and that version's stroke
  there as long as the run within 2 times (not a fragment). The late strokes (an i's dot, a t's
  bar) count with their letter, in writing order;
- each run fits at one stretch of the stroke only (where it could end or start in two places,
  the cut is unclear: no joint), and between the two runs at most 1.5 x-heights of the stroke
  (the pen's way across) is left.

An alignment that gives one stroke to two letters written apart (a stem given to the letter
before as well) is no joint: the letter given the extra piece doesn't match its versions. The
pair is usable when its strokes are also none of a third character's. A pair written joined in
at least 4 usable samples and in at least three quarters of the samples with that pair
(`samples`, `written`, `of`) gets a glyph, made as a version is: of its usable samples' strokes
(every stroke of both letters, their late strokes too, in writing order), those with the most
common stroke count; at least 4 of them must be of one way (close to each other, as a
variant's versions are), each weighed by how close it is to the typical one, averaged around
it, the jitter smoothed, on the line where they sit, with its profiles and its pressure and
speed. Otherwise, and for a pair written joined less often, it is two letters; it leaves the
font at the next build. A model holds at most 256 joined pairs. They are made by a whole build
(`-c` keeps them).

**The font:** `font install` makes a TrueType font of the covered characters (everything else
falls back to the system's fonts) in three styles (FR-034) and installs them together as
`$XDG_DATA_HOME/fonts/glassine-handwriting/handwriting-VERSION.ttf` (Regular),
`handwriting-VERSION-calligraphy.ttf` and `handwriting-VERSION-brush.ttf`, replacing the ones before
(all three, or none when one can't be made), then runs `fc-cache` on that directory. Its family
name is the person's (`-n`; first `My handwriting`, later the name it has), the version counts on
with every install, so apps see a new font. Along the
text the font steps through a cycle of 10, the same way every time: a run starts at step 0, and
after a character or space at step s the next one is at step s + 1 (after 9, 0; after a character
the font lacks, 0 again); a character shows the version its `slots` give for that step. Each
character and the space has a glyph per step (the step-0 one is its mapped glyph; the others are
references to one of its versions), and the `calt` feature has a rule per step that turns the
next glyph into its glyph at the step after. Letters are spaced by the spacing: each version has
half the typical gap as side bearing on each side, and the `kern` feature (GPOS pair kerning
with a class per version, every step glyph in its version's class) moves every pair of versions
to where the spacing places it; the space is the word spacing less the typical gap.

After the cycle's lookups, in this order (each sees the steps already set, so the steps count on
through a rule or a joined pair and the letters around them show what they show without them):
- **Joined pairs** (FR-039, `liga`, applied by default): a pair whose two letters are in the font
  is one unencoded glyph of its own, a ligature of the first letter's step glyphs and the
  second's; text shaping keeps both characters in its cluster, so it reads, copies and searches
  as the two. It has its own profiles and side bearings and is kerned as a version is, at the
  gap its first letter sets with the letter before and its last letter with the letter after
  (its parts have no middles of their own).
- **Neighbour rules** (FR-038, a second `calt` lookup, Extension-wrapped chain contexts): a
  letter's step glyph after (or before) any glyph of the rule's neighbour (its steps, its
  versions, a joined pair it ends or starts) turns into the rule's version's glyph.
- **Random alternates** (FR-040, `rand`), only in a font built with them: each letter's step
  glyphs to its versions, the cycle's 10 steps' versions as the alternates, so a random pick
  shows each as often as the cycle does. Without them the font has no `rand` feature.

A preview that lays text out from the model (the samples app) applies the cycle, the joined
pairs and the rules the same way and spaces it the same way. The font is made only on the device
and is never donated or exported.

**The styles** (FR-034) differ only in their outlines: the glyphs and their order, `cmap`, `GSUB`,
`GPOS`, the advances and the line metrics (`hhea`, the OS/2 typographic ones: the Regular style's)
are the same in all three, so text shapes to the same places in each; each style's bounding boxes,
side bearings (its glyphs' left edges) and OS/2 clipping box (`usWinAscent`, `usWinDescent`) are its
own. Each stroke is outlined as convex pieces that all turn the same way, so the non-zero fill
makes one ink of them where strokes overlap. Names: Regular has the family as name ID 1 and
`Regular` as ID 2; Calligraphy and Brush have `NAME STYLE` as ID 1 and 4, `Regular` as ID 2, and
the family and the style as the typographic family and subfamily (IDs 16, 17); PostScript names
`NAME-Regular`, `NAME-Calligraphy`, `NAME-Brush` (NAME's letters and digits). Calligraphy's and
Brush's `head` revision is a step below the version (Regular's is the version), so where nothing
asks for a style the family's face is Regular. Widths (x-heights; `ink` the model's ink width):
- **Regular**: `ink` throughout (a quadrilateral per segment, a disc at the ends, a wedge or a
  disc where it turns), at least 0.024.
- **Calligraphy**: a broad nib at the nib angle (degrees from the horizontal, rising to the
  right; 35 unless set) swept along the stroke, rounded by its thinnest line: across a stroke
  running in direction θ it is `w_min + (w_max - w_min) · |sin(θ - nib)|`, with `w_min` = 0.4 ·
  `ink` within 0.015 to 0.05 and `w_max` = 2.2 · `ink` within 0.12 to 0.26; a piece per segment,
  the hull of the nib at both its ends.
- **Brush**: per point `ink · max(0.12, clamp(1.3 · fp · fv · fa, 0.35, 2.4) · ts · te)`: `fp =
  P^0.8` (P the pressure profile there, within 0.25 to 3), `fv = V^-0.3` (V the speed profile,
  within 0.25 to 4; `V^-0.55` without a pressure profile), `fa = 1 - 0.12 · tanh(dV/ds / 3)` (the
  acceleration, s in x-heights), each 1 without its profile; the start tapers, `ts = 0.25 + 0.75 ·
  sqrt(min(1, s / t0))` with `t0 = min(0.22, 0.3 · L)` (L the stroke's length, s from its start),
  and the end more, `te = 0.12 + 0.88 · min(1, (L - s) / t1)^0.7` with `t1 = min(0.3, 0.4 · L)`
  times the mean speed of its last 4 profile points (within 1 to 2: a quick flick thins out
  longer); a stroke shorter than 0.15 has no taper. A piece per segment, the hull of the discs at
  both ends.

The nib angle is a setting of the installed font: `font install --nib DEG` (0 to 90) makes the font
with it and keeps it in `letters/font`; an install without `--nib` keeps the font's angle (35 the
first time).

Random alternates (FR-040) are a setting of the installed font too, off unless asked for in this
interface (the samples app doesn't offer them): `font install --random` makes the font with them
and keeps `random on` in `letters/font`, `--no-random` makes it without, and an install with
neither keeps what the font has. Which apps honour them: text shaping by HarfBuzz applies `rand`
by default, picking at random among a glyph's alternates but the same way each time for the same
text (its random numbers start alike for every run of text; asking for `rand=1` picks the first
alternate instead), so apps that shape with HarfBuzz directly or through Pango, among them GTK
apps, show the random versions; tested with `hb-shape`, `pango-view` and GTK through Pango
(HarfBuzz 13.2.1, Pango 1.57.1). Apps that don't apply `rand` keep the cycle; Firefox, Chromium
and LibreOffice are not tested. The neighbour rules and joined pairs apply either way.

## Command line

Part of contract 1, for the settings app and anything else that shows or changes the samples.
They work on the same store under the same lock. Exit status 0, 1 when the store can't be read
or written (a message on stderr), 2 for a usage error.

```
glassine-handwriting samples stats              learning, samples, per client, donation
glassine-handwriting samples list [-c CLIENT]   ID CLIENT TIME SOURCE LANG TEXT per sample
glassine-handwriting samples erase [-c CLIENT]  erase all samples (or one client's) and the adaptations
glassine-handwriting samples delete ID          delete one sample (the adaptations stay)
glassine-handwriting samples export [-c CLIENT] FILE  samples and adaptations into one file
glassine-handwriting samples import FILE        add them on another device; prints the number added
glassine-handwriting learning [on|off]          show or set handwriting.conf's learning
glassine-handwriting adapt [-f] [-r] [--plan] [--progress] [-l LANG] [--min-train N] [--min-held N] [--epochs N] [--rank N] [--lr X]
glassine-handwriting adaptation [show|revert] [-l LANG]
glassine-handwriting charging                   charging or battery
glassine-handwriting letters build [-f] [-r] [--progress] [-c CHAR] | status | show
glassine-handwriting letters mark clear|out|way-out|favourite CHAR ID... | marks | samples CHAR
glassine-handwriting font install [-n NAME] [--nib DEG] [--random|--no-random] | remove | status | build [-n NAME] [--style S] [--nib DEG] [--random] FILE | widths [--style S] [--nib DEG]
glassine-handwriting donation start CC0-1.0|CC-BY-4.0 | add SAMPLE | remove SAMPLE | list | samples | earlier [-c CLIENT] | donor [new] | write FILE | erase | prompts [LANG]
glassine-handwriting read [-n N] [-l LANG] FILE   read sample-format lines (testing)
glassine-handwriting bench [-l LANG] FILE         error, time per line, memory
glassine-handwriting calibrate [-l LANG] FILE     fit the temperature of the head in use on held-back samples
```

`export -c CLIENT` writes only that client's samples and no adaptation. An export holds the
preferences between the samples it holds, as `{"preference": …}` lines (docs/samples.md, the
samples named by their ids); `import` adds those whose two samples are stored once the file is
read. `delete` deletes one
sample by id; it counts as a change for the next adaptation (a running one drops its result), but
the adaptation in use stays.

`calibrate FILE` reads sample-format lines (their `spans` are not used; a line that isn't a
sample, or whose text the model can't write, is skipped) as `read` does, the size as for reading
and no look-alike lean, and fits the temperature of the head in use (the adaptation's when there
is one, else the base model's) on them (Calibration). It prints `temperature T N LOSS_BEFORE
LOSS_AFTER` (samples used, mean CTC loss per character at 1 and at `T`) and `line temperature
HASH T`, the line for a base model's `calibration` file; or `temperature none N` (exit 1).
The samples must be held back from the head's training.

`adapt --plan` runs nothing and prints one line, `plan TRAIN HELD NEW MIN_TRAIN MIN_HELD
charging|battery`: the samples of the language a run would learn from and hold back, those stored
since the last run, the minimums, and the power. `--progress` prints tab-separated lines while it
runs instead of the sentence: `plan TRAIN HELD NEW`, then `epoch K N` per epoch, then one of
`result kept|rejected ERROR_BEFORE ERROR_AFTER TRAIN HELD` (the held-back character error of the
head in use and of the new one, 0 to 1) or `skip REASON` (`learning`, `charging`, `nothing-new`,
`too-few MIN_TRAIN MIN_HELD`, `unplugged`, `erased`).

`stats` prints tab-separated lines `learning on|off`, `samples N`, `client NAME N` per client,
`donation N`. `erase` prints the number erased; every erase (also of nothing) bumps the erase
count, so a running adaptation drops its result. `adaptation show` prints `current` and
`previous`: `none`, or `TIME TRAIN HELD ERROR_BEFORE ERROR_AFTER`; `revert` swaps them. `donation
add` copies one of the person's samples into the donation (once: a sample already in it stays
one); `earlier` lists the person's prompted samples written to a shipped prompt (`prompts.txt` or
`prompts/`, the sample's language or its base) that the donation hasn't got, `ID TIME LANG TEXT`
per sample in the order stored (`-c`: one client's), for the offer to add the samples kept before
donating was on (spec 002 FR-011); `remove` takes one out by id; `write` writes the donation as sample
format v1 lines, each with `licence` and `donor`, times from 0 and no client or time of day, then
a preference record for each preference whose two samples are both in the donation, naming them
by their ids in that file.
`list` prints `licence LICENCE|-`, `samples N` and `donor ID|-`. `samples` prints the id of each sample in the donation, once, in the order added. `donor` prints the donor ID (`-`
before the first donation), `donor new` replaces it with a new one and prints that: later
donations then count as another writer's. `erase` keeps the donor ID.

`letters build` prints `built SAMPLES COVERED` or `skip REASON` (`charging`, `nothing-new`,
`erased`); with `--progress` first `progress DONE TOTAL` lines (samples read of those stored) while
it reads the samples. `letters status` prints `state none|current|outdated` (outdated: samples,
picks, marks or the way it is built changed since it was made), then from the samples as they are now,
model or not: `samples N` (usable), `covered K` (characters with 3 usable samples), one `char C
SAMPLES VERSIONS` per character seen (C escaped like a text field; VERSIONS the versions it has
in the model as built, 0 when none), and `font NAME VERSION PATH` or `font -`; with a font,
`font-letters same|changed`: whether it was made from the letters as the model has them now
(changed after a build or a one-character build since, or a font installed before the styles;
Update makes it again), then `font-nib DEG`, the nib angle it was made with (none for a font from
before the styles).

`letters mark KIND CHAR ID...` sets the samples' mark for that character (`clear` takes it
away; a `favourite` first clears the character's other favourites) and prints `marked`, or
`error no-such-sample` (exit 1) with nothing changed. `letters marks` prints `mark ID CHAR KIND`
per mark of a stored sample. `letters samples CHAR` lists where CHAR is written in the prompted
samples, one `sample ID PLACE STATE VERSION TEXT` per occurrence (PLACE its index in the text;
VERSION the version it is written like as a build would make it now, -1 none; TEXT escaped),
sorted by id and place, STATE one of `usable`, `out` (left out), `way-out` (in a left-out
version), `unread` (the writing wasn't read clearly: no exact strokes per character),
`joined-before` or `joined-after` (a stroke shared with the neighbour), `odd` (too far from its
other versions in size to count).

`font install` makes the font in its three styles from the letter model as it is and prints
`installed NAME VERSION PATH` (PATH the Regular style's file), or `skip
no-letters|nothing-covered`; a name that isn't 1 to 63 bytes of UTF-8 without control characters,
or a nib angle that isn't a whole number from 0 to 90, is a usage error. `font status` prints
`NAME VERSION PATH` or `none`, then for a font `style STYLE PATH` per style it has (Regular,
Calligraphy, Brush; a font from before the styles: Regular alone), `nib DEG` (as `letters
status`) and `random on` for a font with random alternates; `font remove` takes out all of its
files; `font build FILE` writes one style (`--style regular|calligraphy|brush`, Regular unless
given; the installed font's nib angle unless `--nib`; random alternates only with `--random`) to
FILE without installing it, and `font widths` prints the width rules' output as JSON lines: first
`{"style":…,"nib":…,"ink_width":…,"nib_min":…,"nib_max":…}`, then per stroke of every version
`{"char":…,"variant":V,"stroke":J,"widths":[…]}`, the width at each of its points (for
Calligraphy from the stroke's direction there; testing, and the samples app's preview checks
against it).

**Export file:** JSON lines. The first `{"format":"glassine-handwriting-export","version":1}`;
then one `{"sample":{…},"client":"…","confirmed":TIME,"source":"…"}` per sample and one
`{"adaptation":{"lang":"en","slot":"current","data":"BASE64"}}` per adaptation file. Import adds
samples not yet there (same id), keeps their client and time, and adds adaptations only where
none exists.

## Base models

One directory per language: `/usr/share/glassine/handwriting/LANG/` (or
`$GLASSINE_HANDWRITING_MODELS/LANG/`, else the setting `models`'s `LANG/`) with `recognizer.ghm` (the model), `chars.gclm` (the
character model, optional; Characters) with `chars.json` (its recipe) and `CHARS-LICENSE`,
`calibration` (the base model's temperature, optional; Calibration), `README` (the recipe and data sources), `LICENSES` and `prompts.txt` (texts for prompted writing and donation, one per
line), and `prompts/` with the handwriting samples app's prompt sets (`*.txt`, one per line, and
`SOURCES` with their sources and licences), shipped with the app. `recognizer/tools/onnx2ghm.py` converts the trainer's ONNX export;
`glassine-train release` makes the whole directory as a release tarball
([`models/README.md`](../models/README.md)).

A model under trial is installed beside the default one in a directory of its own (for
example `/usr/share/glassine/handwriting-model-4/LANG/`); the setting `models` switches to it
and back. An adaptation belongs to one model file (Adaptation), so the adaptations made
while the setting names another set are kept apart, in `adaptation/LANG@NAME/` (`NAME` the
set's directory name); the default set's stay in `adaptation/LANG/`, untouched, and are used
again when the setting goes back. Until the person's samples have trained the other set's
own, it reads without one. An export carries those apart too; an import leaves them out.

**ghm, version 1** (little-endian). A 64-byte header: `GHWM`, then fifteen 32-bit unsigned
integers: version (1), header bytes (64), features per row `F`, first convolution's channels
`C1`, the other convolutions' channels `C`, strided convolutions `S`, kernel width `K`, LSTM
hidden size `H`, LSTM layers `L`, classes `N`, charset bytes, the training data's end (64-bit,
two words, low first: the confirmation time, seconds since 1970, of the newest person's sample the
model was trained on; 0 when it was trained on none) and two zeros. Then the charset
(UTF-8; class `k+1` is its `k`-th character, class 0 the CTC blank), padded with zeros to a
multiple of 4. Then 32-bit floats, each tensor row-major in this order:

| Tensor | Floats | Use |
|---|---|---|
| scale | `F` | each feature is multiplied by it |
| first convolution weights, bias | `C1·F·K` [out][in][tap], `C1` | stride 1, padding `K/2`, then exact GELU |
| per strided convolution: weights, bias | `C·Cin·K` [out][in][tap], `C` (`Cin` = `C1` for the first, else `C`) | stride 2, padding `K/2`, GELU |
| per LSTM layer, forward then backward: input weights, recurrent weights, bias | `4H·I` (`I` = `C`, or `C1` without strided convolutions, then `2H`), `4H·H`, `4H` | gates i, o, f, c (ONNX order); the bias is ONNX's two biases added |
| output weights, bias | `N·2H` [class][input], `N` | then log-softmax |

A file whose size doesn't match the header is refused. The architecture and the features are the
trainer's (`train/`, `features.py`); the recognizer computes the same features to within float
rounding, checked by `make check`. `R` feature rows give `ceil(R / 2^S)` frames; frame `f` is
centred on row `f·2^S`.

**ghm, version 2** (FR-018): the same layout as version 1, `F` = 6, for a model that reads the
ink scaled by a reference height instead of its own (the trainer's `features_ref`): every length
in reference heights, y above the baseline of the guide the ink sits on, and a sixth input, the
ink's size, log(max(height, width / 4) / reference), clipped to ±4, the same in every row. The
reference is the person's small letters in the session's units (Size, below), else 0.2 of the
area's line (or of 1 em); the baseline the area's guide line whose line holds the median of the
ink's y, each guide's line running from two thirds of a line above it to a third below, so a
comma or a descender just under its guide stays on it (`BASE`; a stored sample's guides are at
two thirds of each of its area's lines), else the median of its y. Without any reference the ink's own core height stands in. A version 1 model
reads as before; the recognizer reads both.

**gclm, version 1** (the character model, little-endian), made by the trainer's
`glassine-train charlm` (`train/src/glassine_train/charlm.py`, from the training books' text,
never the test texts). A 48-byte header: `GHCM`, then eleven 32-bit unsigned integers: version
(1), header bytes (48), order `N` (1 to 8: `N − 1` characters of context), symbols `V`,
charset bytes, nodes, edges, successors, smoothing (1, Witten-Bell) and two zeros. Then the
charset (UTF-8, symbol `k` its `k`-th character), padded with zeros to a multiple of 4; then
32-bit arrays: `edge_start[nodes + 1]`, `succ_start[nodes + 1]`, the edges (`symbol << 24 |
node`) and the successors (`symbol << 24 | count`). Each node is a context; node 0 the empty
one. A node's edges lead to the contexts one character older (that character the edge's
symbol), sorted by symbol, to nodes after it; its successors are the characters seen after the
context with their counts, sorted by symbol. P(c | h) = (C(h c) + T(h) · P(c | h′)) / (C(h) +
T(h)) from the longest context in the file down to the empty one, below it 1/V; C(h) is the sum
of h's counts, T(h) the number of its successors, h′ h without its oldest character. Text
before is read as the trainer cleans it (white space a space, curly quotes `'`, dashes `-`); a
character not in the charset ends the context, and as the next character it counts 1/V. A file
whose size doesn't match the header, or whose arrays are out of order, is refused.

**gha, version 1** (an adaptation, little-endian): `GHWA`, version, the base model's hash (64-bit),
rank, dimension, classes, samples learned from, held back (32-bit), error after and before
(float), time (64-bit), the temperature (float, byte 52; 0 in a head made before calibration,
or one outside [0.5, 4]: none), then from byte 56 the head's floats: adapter down weights and
bias, up weights and bias, output weights and bias.

**calibration, version 1** (text): first line `glassine-handwriting-calibration 1`, then
`temperature HASH T` lines: `HASH` the model file's hash (16 hex digits, as `calibrate` prints
it), `T` its temperature. A line for another model file, or a `T` outside [0.5, 4], is not
used; without a line that fits, the base model reads with 1.

## Environment

| Variable | Meaning |
|---|---|
| `GLASSINE_HANDWRITING_MODELS` | the base models' directory instead of `/usr/share/glassine/handwriting` |
| `XDG_DATA_HOME`, `XDG_CONFIG_HOME` | the store, the writer stores' directory and the settings, as above; never which writer's store |
| `GLASSINE_HANDWRITING_SYSFS` | for tests: a directory laid out like `/sys/class/power_supply` |
| `GLASSINE_HANDWRITING_LOADAVG` | for tests: a file laid out like `/proc/loadavg` |
| `GLASSINE_HANDWRITING_DEBUG` | `adapt` prints the loss of each epoch |
| `GLASSINE_HANDWRITING_FIT_OK` | for tests: the prompted check's threshold instead of 0.6 |
| `GLASSINE_HANDWRITING_SIZE` | for tests and measurements: `X,C[,XS,CS]` the person's letters in the session's units instead of the measured ones; `off` no size; `noweigh` or `weigh` (alone or after the letters) the weighing off or on whatever the model (by default a version 1 model is weighed, a version 2 model not) |
| `GLASSINE_HANDWRITING_CALIB` | for measurements: `off` reads with no calibration, a number in [0.5, 4] with that temperature (Calibration) |
| `GLASSINE_HANDWRITING_LOOKALIKE` | for measurements: the share of a look-alike's chance it keeps in a field that leans the other way (Characters, Look-alikes) instead of 0.5; `off` no lean |
| `GLASSINE_PREDICT_MODELS` | the word predictor's models' directory (Words) |
| `GLASSINE_HANDWRITING_NEATEN` | for measurements and tests: `base=0`, `slant=0`, `size=0`, `space=0`, `pull=0` leave a correction out, `guard=CHARS` has those characters fail the shape guard once pulled (up to the next comma), `trace=0` leaves out the check of the glyph traced on the ink's points, `margin=M` sets the shape guard's margin (own versions within M times another character's; 1 by default) |
| `GLASSINE_HANDWRITING_WORDS` | for tuning: `lm=…,chr=…,margin=…,near=…,beam=…,nbest=…,minlen=…,guided=0\|1` replace the Words weights |
| `GLASSINE_HANDWRITING_CHARS` | for tuning: `weight=…,floor=…,bonus=…,margin=…,beam=…` replace the Characters weights |
