# The handwriting recognizer (contract version 1)

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
only help with gestures and go into samples.

`POINTS` is one stroke (pen down to pen up): points separated by spaces, each `x,y,t`,
`x,y,t,p` or `x,y,t,p,tx,ty`: position (y pointing down), time in milliseconds (any origin,
increasing), pressure 0 to 1, tilt in degrees from the vertical. All points of a stroke have the
same number of values. A stored sample keeps every value as sent (written in the shortest form
that reads back as the same number), times counted from the sample's first point, to the
microsecond.

## Requests

| Request | Reply |
|---|---|
| `hello ID VERSION [CLIENT]` | `hello ID 1` |
| `begin ID S LANGS FIELD UNITS BEFORE` | `begin ID ok`, or `begin ID private` (nothing is kept) |
| `stroke ID S POINTS` | `stroke ID ok N`: the stroke's number in this session |
| `read ID S [COUNT [raw]]` | `read ID text K {TEXT SCORE ALIGN}×K`, `read ID gesture …` (below), `read ID empty`, or `drop ID` |
| `align ID S PROMPT` | `align ID SCORE K {FIT SEGMENTS}×K`, or `align ID short` |
| `remove ID S N[,N…]` | `remove ID ok REMOVED` |
| `clear ID S` | `clear ID ok`: all strokes gone, the session stays |
| `end ID S` | `end ID ok` |
| `area ID S X0,Y0,X1,Y1,LINE,ROT` | `area ID ok` |
| `confirm ID S SOURCE TEXT [keep]` | `confirm ID ok SAMPLE` (`prompted`: `confirm ID ok SAMPLE exact\|inferred`), or `confirm ID skip REASON` |
| `recognize ID LANGS FIELD UNITS COUNT BEFORE STROKES` | as `read`, with the verb `recognize`; or `recognize ID private` |
| `samples ID [all]` | `samples ID K SAMPLE…` |
| `sample ID SAMPLE` | `sample ID JSON` (the sample's line, escaped) |
| `donate ID S TEXT [keep]` | `donate ID ok COUNT SAMPLE`, or `donate ID skip REASON` |
| `forget ID SAMPLE` | `forget ID ok WHERE` |
| anything wrong | `error ID REASON` (`ID` is `-` when the line has no usable id) |

**hello** names the contract version (1) and, optionally, the client: an application id
(`org.glassine.keyboard`, same characters as an id, up to 63). The name is stored with each sample
the client confirms; `samples` needs it. It is what the client says it is (the recognizer runs as
the person's own process and can't check it).

**begin** opens session `S` (an existing one of that name is ended first). At most 16 sessions at
once (`busy`). `BEFORE` is the text before the cursor; in a prose field the words before it rank
the readings (below, Words).
In a private field (below) no session is kept: strokes sent to it get `unknown-session`.

**stroke** adds a stroke. Stroke numbers count up from 0 and are never reused in a session, also
after `remove`. At most 4096 strokes of 100000 points each.

**read** reads the strokes so far, up to `COUNT` candidates (1 to 8, default 3), best first; with
`raw`, the ink's reading alone, without words. A
client may read after every stroke (writing in progress, FR-004); a reading of the same strokes is
the same whether they came one by one or at once. When several `read` requests of one session are
read together (the client was faster than the recognizer), only the newest is answered; the older
ones get `drop ID`. Requests between them are carried out in order.

- `text K`: `K` candidates (0 when nothing could be read, for instance a single dot), each
  `TEXT SCORE ALIGN`. `SCORE` is the log-probability (≤ 0, higher is better). `ALIGN` has one
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

**area** names the writing area of session `S` for the samples it makes: its box `X0,Y0` to
`X1,Y1` and its line spacing `LINE`, in the session's units, and the screen's orientation `ROT`
(0, 90, 180 or 270 degrees). It stays until the session ends. A box that is empty or an
orientation that isn't one of these is `bad-area`.

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
put it there (a near word or the guided search, below) rather than the ink, it is checked like a
prompted sample,
and also every written character must fit at least 0.3; failing, the reply is
`confirm ID skip fit SCORE` and `keep` doesn't override it (the client shows nothing).

## Words (FR-015)

In a prose field (purpose `normal` or `-`, hints only those that change nothing, see Fields) the
readings use the keyboard's word predictor's words for the language
(`$GLASSINE_PREDICT_MODELS/LANG.gpm` or `/usr/share/glassine/predict/LANG.gpm`, the base language
when there is none for a regional one; shell `docs/predictor.md`) and the person's learned words
(`$XDG_DATA_HOME/glassine/predict/words`, removed words left out), read only, picked up again when
they change. Without either, readings are the ink's alone. The setting `words = off` turns them
off. Every other field, and `read … raw`, reads without them.

- A second search of the ink is guided by the words: a prefix inside a word of the list pays
  nothing until the word ends, then as below; a letter that leaves the list makes an unknown
  word that pays per character, so names, numbers and codes still come through. Its ten best
  readings join the ink's own ten best, when the ink scores them within 6 of its best.
- All these readings are scored again: the ink's log-probability, plus 0.3 × (for each
  word of the list, the natural log of its probability after the two words before, from `BEFORE`
  and the reading itself; for every other character but a space, −4.5).
- The ink's best reading stays first, unless another reading of the ink (its own or the guided
  search's) scores 2.5 more and the best
  isn't all words of the list; the ink's best then comes second. The rest follow by score.
- For an unknown word of four letters or more in the three best readings, up to two of the most
  frequent words of the list one edit away (a letter changed, left out, added, or two swapped)
  are added, in the written word's case, when the ink scores them within 6 of its best; never
  first.
- `SCORE` stays the ink's log-probability of the text.

**recognize** is `begin`, a stroke per `;`-separated part of `STROKES`, `read`, `end` in one line,
for a client that has the whole ink at once. It stores nothing.

**samples** lists the ids of the samples this client confirmed, in the order they were stored;
`all` lists everyone's and needs the client in the `readers` setting. **sample** returns one
sample's line (sample format v1, escaped as a text field): the client's own, or any with
`readers`. Both need a client name from `hello` (`no-client`) and are what an app that builds a
font from the person's writing uses.

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
`bad-count`, `bad-area`, `bad-prompt` (empty or more than 512 characters), `bad-source`, `unknown-session`,
`too-many` (strokes in a session), `busy` (sessions), `no-model` (no model for any of the
languages), `no-client`, `denied`, `unknown-sample`, `too-long`, `store` (the store can't be read
or written; nothing changed), `memory`.

## Fields (FR-011)

The recognizer applies the rule itself, whatever the client sends:

| Field | Reads | Learns |
|---|---|---|
| purpose `password` or `pin`, or the hint `sensitive_data` or `hidden_text` | no (`private`, nothing kept) | no |
| purpose `normal`, `alpha`, `name`, or `-` | yes | yes |
| purpose `email` or `url` | yes | only when the keyboard's `addresses` setting is `learn` |
| `terminal`, `digits`, `number`, `phone`, `date`, `time`, `datetime` | yes | no |
| a purpose or hint this version doesn't know | yes | no |

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

The keyboard's `predict.conf` (`learning`, `addresses`) counts too: learning is off when either
file says so, so the keyboard's one learning switch covers handwriting (shell spec 006 FR-008).
Turning learning off keeps what was stored; erasing is separate.

## The store

`$XDG_DATA_HOME/glassine/handwriting` (or `~/.local/share/glassine/handwriting`): directory
0700, files 0600, created by the first write, never by a read or a private field. `lock` is the
lock (`flock`, exclusive) every writer holds; files are replaced through `NAME.tmp` and a rename.
Nothing in it leaves the device except by export or a donation the person writes out.

- `samples.jsonl`: one sample per line, sample format v1. The recognizer stores `spans` from its
  own alignment (`spans_source` `inferred`) and times starting at 0.
- `samples.meta`: first line `glassine-handwriting-meta 1 GEN` (format, version, erase count),
  then one line per sample, tab-separated: `ID CLIENT TIME SOURCE LANG` (`TIME` seconds since
  1970; `CLIENT` `-` when none was named). `ID` is the sample's id: the first 16 hex digits of a
  hash of its line, so the same sample is stored once.
- `adaptation/LANG/current`, `previous`: the adaptation in use and the one before (below);
  `state` (`SAMPLES GEN TIME ID`: what the last run saw, the confirm time and id of the newest
  sample in it, so it doesn't run again for nothing and can count what came since); `log` (one
  line per run: `TIME kept|rejected TRAIN HELD ERROR_BEFORE ERROR_AFTER`).
- `donation/licence` (`CC0-1.0` or `CC-BY-4.0`) and `donation/samples.jsonl`.

A running recognizer picks up a new adaptation at its next reading.

## Adaptation (FR-009)

`glassine-handwriting adapt` trains a small head (a residual adapter on the encoder's output and
the output layer; the encoder stays as shipped) on the person's samples of one language. Samples
the base model was trained on (confirmed at or before the time its file records, below) are left
out: neither learned from nor held back, so a head learns only what the base model hasn't seen and
its check isn't made on samples the base model already knows. Of the rest, one sample in five (by
its id) is held back; the run keeps the new head only when its character error
on the held-back samples is lower than that of the head in use (the base model's, or the current
adaptation's). Then the current one becomes `previous`. It needs 40 samples to learn from and 10
held back, runs only when something changed, and, unless `-f`, only while charging and idle, and
stops when the charger is unplugged. With `-r` the person asked for it in a client (spec 002):
charging is enough, and it runs at the lowest priority so the device stays usable. An adaptation belongs to one base model file (its hash is in
the file); after a model update it is ignored until the next run. An erase while it runs drops its
result.

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
glassine-handwriting donation start CC0-1.0|CC-BY-4.0 | add SAMPLE | remove SAMPLE | list | write FILE | erase | prompts [LANG]
glassine-handwriting read [-n N] [-l LANG] FILE   read sample-format lines (testing)
glassine-handwriting bench [-l LANG] FILE         error, time per line, memory
```

`export -c CLIENT` writes only that client's samples and no adaptation. `delete` deletes one
sample by id; it counts as a change for the next adaptation (a running one drops its result), but
the adaptation in use stays.

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
add` copies one of the person's samples into the donation; `remove` takes one out by id; `write` writes the donation as sample
format v1 lines, each with `licence`, times from 0 and no client or time of day.

**Export file:** JSON lines. The first `{"format":"glassine-handwriting-export","version":1}`;
then one `{"sample":{…},"client":"…","confirmed":TIME,"source":"…"}` per sample and one
`{"adaptation":{"lang":"en","slot":"current","data":"BASE64"}}` per adaptation file. Import adds
samples not yet there (same id), keeps their client and time, and adds adaptations only where
none exists.

## Base models

One directory per language: `/usr/share/glassine/handwriting/LANG/` (or
`$GLASSINE_HANDWRITING_MODELS/LANG/`) with `recognizer.ghm` (the model), `README` (the recipe and
data sources), `LICENSES` and `prompts.txt` (texts for prompted writing and donation, one per
line), and `prompts/` with the handwriting samples app's prompt sets (`*.txt`, one per line, and
`SOURCES` with their sources and licences), shipped with the app. `recognizer/tools/onnx2ghm.py` converts the trainer's ONNX export;
`glassine-train release` makes the whole directory as a release tarball
([`models/README.md`](../models/README.md)).

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

**gha, version 1** (an adaptation, little-endian): `GHWA`, version, the base model's hash (64-bit),
rank, dimension, classes, samples learned from, held back (32-bit), error after and before
(float), time (64-bit), then from byte 56 the head's floats: adapter down weights and bias, up
weights and bias, output weights and bias.

## Environment

| Variable | Meaning |
|---|---|
| `GLASSINE_HANDWRITING_MODELS` | the base models' directory instead of `/usr/share/glassine/handwriting` |
| `XDG_DATA_HOME`, `XDG_CONFIG_HOME` | the store and the settings, as above |
| `GLASSINE_HANDWRITING_SYSFS` | for tests: a directory laid out like `/sys/class/power_supply` |
| `GLASSINE_HANDWRITING_LOADAVG` | for tests: a file laid out like `/proc/loadavg` |
| `GLASSINE_HANDWRITING_DEBUG` | `adapt` prints the loss of each epoch |
| `GLASSINE_HANDWRITING_FIT_OK` | for tests: the prompted check's threshold instead of 0.6 |
| `GLASSINE_PREDICT_MODELS` | the word predictor's models' directory (Words) |
| `GLASSINE_HANDWRITING_WORDS` | for tuning: `lm=…,chr=…,margin=…,near=…,beam=…,nbest=…,minlen=…,guided=0\|1` replace the Words weights |
