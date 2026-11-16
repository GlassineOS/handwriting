# Feature Specification: Handwriting recognizer

**Feature Branch**: `001-recognizer`
**Status**: Draft
**Input**: Turn pen strokes into text on the device with a recognizer the project trains
itself, as one self-contained program: the on-screen keyboard's writing paper
(`GlassineOS/shell` spec 006) is its first client, but any app can use it, including apps that reverse the idea, such
as one that turns the person's own handwriting into a font.

## Context

Online recognition reads the pen's movement (points with time, pen down and up) rather
than a picture of finished writing: stroke order, direction and pauses carry most of
what tells letters apart, so a small model is enough and it can work while the person is
still writing. The pen already reports this data.

The shell's spec 006 needs the recognizer for the writing paper and fixes the flow there (pending
text, alternatives, corrections). This spec is the recognizer itself: what it accepts and
returns, the samples it learns from, how it learns, and how other apps use it.

## User scenarios

### Scenario 1 – Writing into a text field (Priority: P1)

**Given** the keyboard's writing paper (shell spec 006), **When** the person writes a short
phrase and pauses, **Then** the recognizer returns the phrase and up to two alternatives
within a second, ranked with the text before the cursor in mind.

### Scenario 2 – It reads the person's hand better every week (Priority: P1)

**Given** the person has confirmed or corrected handwriting for a while, **When** the
device has been idle and charging, **Then** the recognizer has adapted to their writing,
and a recognition that failed before on their samples now succeeds more often; if an
adaptation would make it worse on their own samples, it is not used.

### Scenario 3 – Another app asks for recognition (Priority: P2)

**Given** a note-taking app with its own ink, **When** it hands a selection of strokes to
the recognizer, **Then** it gets text and alternatives back, through the same interface the
keyboard uses, with no handwriting stored unless the person confirms text in that app and
the app passes the confirmation on.

### Scenario 4 – Write your own font (Priority: P2)

**Given** an app that shows prompted text (a pangram, then single letters and digits),
**When** the person writes each prompt, **Then** the recognizer aligns the strokes to the
prompt's characters and returns one stroke group per character, which the app turns into
glyphs; the same samples, labelled by the prompt, also teach the recognizer.

### Scenario 5 – Gestures (Priority: P2)

**Given** strokes that are a strike-through, a caret or a scratch-out, **When** they reach
the recognizer, **Then** it reports the gesture and what it covers instead of reading it as
letters.

### Scenario 6 – The person stays in charge of their samples (Priority: P1)

**Given** stored samples, **When** the person opens the settings, **Then** they see how many
samples there are, from which apps, and can turn learning off, erase everything, or export
and import their samples and adaptation as one file (for a new device); nothing leaves the
device otherwise.

### Scenario 7 – Contributing samples to the base model (Priority: P3)

**Given** a person who wants to help the base models, **When** they start contributing in the
handwriting samples app (spec 002), **Then** they are shown public-domain sentences to write, see each written
sentence again before it is kept, choose a licence once, and get one file they send
themselves; nothing is contributed without that review, and their own samples are never
included unless they pick them one by one.

## Requirements

- **FR-001**: The recognizer is a separate program with a stable, versioned interface
  on the person's session; the keyboard and any app are its clients. It starts on demand
  and stops after a while without requests. It runs without a network.
- **FR-002**: Input: one or more strokes, each a list of points (x, y in a stated unit,
  time in milliseconds, pen pressure where the pen has it) between pen down and pen up;
  optional context: the text before the cursor, the language or languages to expect, the
  field's purpose, and a prompt when the text is known in advance (FR-007).
- **FR-003**: Output: up to a requested number of candidates (default three), best first,
  each with a score and its characters aligned to the strokes (which strokes, or which
  part of a stroke, made each character); or a gesture (FR-008) with the characters or
  position it covers.
- **FR-004**: Writing in progress: a client can send strokes as they are drawn and ask for
  the current reading at any time; the final reading after a pause is ready within 1 s for
  a short word on the reference device, and the recognizer uses no processor time while no
  client writes.
- **FR-005**: Engines: recognition runs in an engine behind the interface. The default
  engine is the project's own online model, trained by the project, shipped per language
  as a base model, with a personal adaptation on top (FR-009). Another engine can stand in
  through the same interface; clients don't change.
- **FR-006**: Languages: the first release ships English (letters, digits, common
  punctuation); every other language comes later as a base model of its own, with no
  change to the interface or to clients. The
  language comes from the client's context, else from the session's keyboard layout and
  locale.
- **FR-007**: Prompted writing: when the client passes the expected text, the recognizer
  aligns the strokes to it (each character's strokes) and reports how well they fit, so
  the client can ask for a retry of a character that doesn't match.
- **FR-008**: Gestures: strike-through (deletes what it crosses), caret (insert here),
  scratch-out (discard), reported with their extent, before any text is read from the
  same strokes.
- **FR-009**: Samples and learning: a sample is the strokes, the confirmed text, the
  alignment, the language, the client that sent it and the time. Samples come only from a
  person's confirmation in a client (a picked alternative, a correction, a prompt written
  in prompted mode), never from the recognizer's own unconfirmed output, never from a
  private field. A picked candidate that the words put there rather than the ink alone
  (FR-015) is stored only when its strokes fit it as well as prompted writing must
  (FR-007's check), with no character fitting badly; otherwise no sample is made, and the
  client is not interrupted. A picked sample records which candidate it was (its place
  and whether it came from the ink or the word list). They are stored in the person's data directory, readable only by them.
  Adaptation runs while the device is idle and charging, or when the person asks for it
  in a client (spec 002) while the device charges; either way it is checked against a
  held-back part of the person's own samples, kept only when it does better than the one
  in use, and reported to the client that asked. Samples a base model was trained on
  (FR-012) are not used by an adaptation for that model, neither to learn from nor to
  check against: the model records when its training data ends, and only samples
  confirmed after that count, with the same minimum numbers; the previous adaptation stays available
  to go back to.
- **FR-010**: Control: learning on or off (on by default); erase everything (samples and
  adaptation) in one action; counts per client; export and import of samples and
  adaptation as one file, by the person only. These are offered by the settings app; the
  recognizer exposes them through its interface.
- **FR-011**: Clients and samples: any client may ask for recognition; storing samples
  from a client follows the learning setting, and the per-client counts let the person
  see and erase what each app taught. A client that reads samples (Scenario 4) gets only
  those it sent itself unless the person grants it all of them.
- **FR-012**: Base models are trained only on data the project may use: data under a
  suitable licence, text rendered as synthetic handwriting, and samples a person gave for
  it (contributed under FR-014, or given to the project by their writer for training). The
  training recipe, the data sources and their licences ship with the models; a model
  trained on real handwriting says so and from how many writers. The samples themselves
  are never published with a model or in the project's repositories, only aggregate
  measurements of them. No other sample of any person is part of a base model.
- **FR-013**: No device names in the program; what differs per device (the pen's units,
  pressure, rate) comes from the client's stroke data.
- **FR-014**: Contribution: off unless the person starts it. Contributed samples are prompted
  writing of public-domain or openly licensed sentences shipped with the program, or
  samples the person picks one by one from their own; each is shown before it is kept.
  The person chooses the licence (CC0 or CC BY 4.0) once per contribution. The contribution file
  uses the sample format with no client, wall-clock time, device or person named in it
  (`docs/samples.md`), and carries one contributor number: a random identifier made when the person
  first starts a contribution, kept with their data on the device and the same in every later
  contribution from it, so the project can tell writers apart and count each writer's
  samples across contributions. It is not derived from the device, the account or anything
  else about the person. The person sees it where they contribute and can replace it with a
  new one, after which their contributions count as a new writer's. The recognizer only writes the file; sending it is a step the
  person takes outside this program. Contributions are separate from the samples used for
  the person's own adaptation, and stopping or erasing a contribution never touches those.

- **FR-015**: Words: in prose fields (the purpose is plain text, or there is no text field)
  the recognizer reads with the language's words and the person's learned words, the
  keyboard's word predictor's (`GlassineOS/shell` docs/predictor.md), and the text before
  the cursor:
  - The ink is searched with the words guiding it, beside the ink's own search, so a word
    the ink reads only a little less well than a non-word is found; text that isn't a
    known word (names, numbers, codes) stays readable at a cost per character, never
    forced into a word.
  - Candidates are ranked by the ink and by how likely their words are after the text
    before.
  - The ink's best reading stays first unless another reading of the same ink is clearly
    more likely with its words, and it is then still among the first two.
  - A word of the list one edit from a reading that isn't a word may be offered beside
    that reading, never in its place and never first.
  - No words at all in a private field, or in a field for digits, numbers, phone numbers,
    dates, times, addresses (email, web), passwords, PINs or a terminal, or with any hint
    other than prose hints; nor when the person turns words off. The person's learned
    words are only read, never written.
  - Words never make reading miss FR-004's time budget, and a client can ask for the
    ink's reading alone.

- **FR-016**: The samples stay quick to work with however many there are: keeping a
  sample, listing them (with their text and the reading at the time of writing), counting
  them, looking one up, and taking one back take the same time with ten thousand samples
  as with ten, and none of them reads the strokes of other samples. A sample taken back
  (FR-010, FR-014) is gone from the stored files when the request returns, not at a later
  cleanup. A store written by an earlier version keeps working as it is, and a crash or
  power loss at any point leaves every confirmed sample readable and no taken-back one
  readable again.

- **FR-017**: Size: the reading respects the ink's size relative to the person's own
  letters, so a lone mark and a lone letter, and the two cases of a letter whose forms
  differ mainly in size, are told apart:
  - The reference is the person's typical letter heights (small letters, capitals, and
    each when written alone), measured from their confirmed samples; the measurements are
    kept beside the samples, and a sample taken back or erased takes its measurement with
    it (FR-010, FR-016). Without enough samples, the writing area's line height when the
    client gives one; else the ink's own reading alone, as before.
  - A mark far below the small-letter height reads as a mark (`.`, `,`, `'`, `-`), and a
    letter or digit is not offered first for it; the marks the model can write are among
    the candidates even when the ink's own search finds none of them.
  - A lone letter at the person's capital height favours capitals and tall letters, one
    at their small-letter height favours small forms (o and O, c and C, s and S, x and
    X, v and V, w and W, z and Z). Pairs that size alone can't separate (k and K, p and
    P without a line) are left to the letter's shape.
  - Where the client tells where its lines are, the mark's place on the line decides too:
    a comma sits on or below the line, an apostrophe high.
  - The same size-aware reading is used for candidates, for prompted writing's fit
    (FR-007), and for the check of a pick (FR-009); size only ever helps a fit, never
    lowers it below what the ink alone gives. Words and sentences read as they do without
    it; only a mark set apart at their end, or ink the model reads as one or two
    characters, may read differently. Readings stay the same for the same strokes and
    reference.
- **FR-018**: Base models keep the ink's absolute size: the model reads ink scaled by a
  reference height (the person's letter height when known, else the line height) rather
  than by the ink's own height, takes the size as an input of its own, and is trained with
  single characters and punctuation at their true size relative to the line. A model that
  reads size this way says so in its file's format version, and the recognizer reads both
  kinds.
- **FR-019**: Characters: the model reads each moment of the ink on its own, so it has no
  sense of which character is likely after the ones before it ("lig4t" for "light"). Every
  reading therefore also weighs how likely each character is after the characters before
  it, by a character model of the language shipped with the base model:
  - In prose fields (as FR-015) the readings use the character model and the words; in a
    field for names or plain letters, the character model alone; in every other field
    (addresses, numbers, phone numbers, dates, times, a terminal, a purpose the recognizer
    doesn't know) neither, and nothing at all in a private field.
  - The text before the cursor is the character model's context; without it, a reading
    starts as a new word does. A reading still being written pays nothing for not being
    finished.
  - Its weight is bounded per character, so a code, a name, a number or a mix such as
    "4th" or "R2D2" written clearly still reads as written; the ink, not the character
    model, decides between readings the ink tells apart.
  - A client can ask for the ink's reading alone (prompted writing's checks: how readable
    the ink itself is) or for the reading without words but with the character model
    (free writing that should not be bent towards the word list); the person can turn
    the character model off as they can the words.
  - A candidate the character model put before the ink's own best reading counts, for a
    pick (FR-009), as one the words put there: it is stored only when its strokes fit it as
    prompted writing must. Prompted writing's fit and the reading stored with a prompted
    sample stay the ink's alone.
  - The character model is built only from the public-domain texts the base model's
    training text comes from (never from the test texts), by a reproducible step whose
    sources, checksums and licences ship with it; the same sources give the same file.
    Without it, readings are as without it, and it costs little: reading with it takes at
    most a tenth longer than without.
- **FR-020**: The next base model (requirements on its training, beside FR-018):
  - Training text with the language's real character statistics: no uniformly random
    letter strings, except as a small remainder; instead pseudo-words sampled from a
    character model of the language (not words of the word list, so no word can be
    learned by heart, but with real letter sequences); names from public name lists; and
    codes and numbers as line kinds of their own, as they are really written (ordinals and
    units such as 4th, 10:30 or 5 km, dates, phone numbers, postcodes, number plates and
    part numbers such as B-XY 123 or R2D2, email and web addresses), so a digit stands
    next to letters only where it really does.
  - Optionally, a decoder whose output depends on its earlier output (attention, or CTC
    with attention) measured against the CTC model with the character model (FR-019); not
    required.
  - Nice to have: synthetic ink with delayed strokes as people write them (dots of i and
    j, bars of t and f and others added after the word, often right to left, sometimes
    mid-word, after a longer pause than between letters), in every set that renders
    real-looking ink.
  - The generator's timing and dynamics drawn from real ink broadly, with a timing model
    fitted to the real ink of many writers, while the shapes stay broad (no one writer's
    geometry).
  - A test set of other real writers' ink, from contributions with their contributor numbers, as soon as
    there is one; the held-out synthetic fonts only stand in for other hands. The base
    model's real-ink error is then reported per writer.
  - Letters placed as people space them: the generator spaces letters by their ink
    profiles and pair gaps (spec 003 FR-007), drawn from the spread real writers show,
    not the mean, with touching and overlapping letters and varying word gaps; one
    writer's spacing is at most one style among many.
  - Part of the text drawn from the character model flattened (sampled at a higher
    temperature) beside the plain samples, so the ink model does not learn the language's
    letter statistics as a second character model; codes, names and unusual words read
    no worse than with model 2 and the character model.
  - Contributors' letter models (versions in proportion, spacing, timing) as further
    synthetic writers, when their contribution's licence covers derived models; a
    contributor in the real test set is never one of the generator's writers.
- **FR-021**: Writer stores: besides the person's store, the recognizer keeps one store per
  guest writer of the samples app (spec 002 FR-014–FR-025), laid out as the person's,
  readable only by them, beside it and never inside it:
  - A writer's store is named explicitly, by a random identifier the recognizer makes when
    it creates the store, for every recognizer process and every command that is to use
    it; nothing in the environment chooses a store, and without a name a request is for the
    person's store. Creating a store gives its identifier; removing one deletes the whole
    store for good when the request returns, a crash on the way leaving it whole or gone;
    a request for a removed or unknown store is refused and keeps nothing. The identifier
    says nothing about the writer, and no name of a writer reaches the recognizer.
  - For a writer's store the recognizer keeps samples, preferences, marks and a
    contribution with its own contributor number (FR-014), counts, lists, deletes, erases
    and exports them, and measures that writer's size reference (FR-017) from them; it
    reads with the base model, the language's words and the character model, never with
    the person's adaptation, learned words or letters.
  - It refuses, for a writer's store, an adaptation (also in the background), the letter
    model, a font, neatening, a learning switch of its own and an import; the person's
    learning setting does not apply to it.
  - The person's erase, counts and export cover the person's store only and count the
    writer stores kept beside it; only the samples app reaches a writer's store, never
    another client or the handwriting service's other apps (spec 004).

## Key entities

- **Stroke**: points between pen down and pen up (x, y, time, pressure).
- **Reading**: candidates with scores and character-to-stroke alignment, or a gesture.
- **Sample**: strokes, confirmed text, alignment, language, client, time.
- **Base model**: per language, shipped with the program, the same for everyone.
- **Adaptation**: per person, built on the device from their samples.
- **Contribution**: samples the person reviewed and licensed for the base models.
- **Writer's store**: a guest writer's samples and contribution (spec 002), beside the
  person's store, named by a random identifier.

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Recognizer session interface (recognize, stream, prompted alignment, gestures, samples, learning control, export/import, writer stores) | owned (this program) | 1.3 | FR-001–FR-011, FR-021; `docs/recognizer.md` |
| Sample format (export/import, contribution, training data) | owned (this program) | 1 | FR-010, FR-014; `docs/samples.md` |
| Base model package (model, language, recipe, data sources and licences) | owned (this program) | 1 | FR-005, FR-012 |
| Writing paper and corrections | consumer (`GlassineOS/shell` spec 006) | — | shell spec 006 FR-011–FR-013a |
| Settings: learning, erase, counts, export/import | consumer (the shell's settings app) | — | shell spec 006 FR-008 |
| Word predictor model and learned words | consumer (`GlassineOS/shell` docs/predictor.md) | 1 | FR-015 |
| Character model (per language, beside the base model) | owned (this program) | 1 | FR-019 |

## Success criteria

- A short word reads within 1 s of the pause on the reference device; no processor use
  while idle.
- On a fixed test set of the project's own recorded handwriting, the base model's
  character error rate is published per release, and does not get worse from one release
  to the next.
- After a few hundred confirmed samples from one person, the error rate on their held-back
  samples is lower than the base model's alone.
- With words (FR-015), the character and word error rates on a person's held-back
  samples are lower than the ink's alone, within the same time budget; readings the words
  make wrong are far fewer than those they make right, also for text the list lacks.
- With the character model (FR-019), the character and word error rates on a person's
  held-back samples are lower than without it, in the ink-only and the words reading;
  letters read as digits inside words (and the reverse) are fewer; numbers and codes read
  no worse; and a reading takes at most a tenth longer on the reference device.
- Scenario 4's prompted mode returns one stroke group per prompted character for a
  pangram written in one go.
- With 10,000 samples stored, on the reference device: a prompted sample is kept within
  100 ms of its confirmation beyond the reading itself, the listing of a client's samples
  answers within 100 ms, and taking a sample back within 100 ms (FR-016).

## Device scope and risk

Device-neutral; it needs a pen that reports positions over time.

- **Failure modes:** a bad adaptation (caught by FR-009's check, with the previous one
  kept); samples leaking from a private field (clients never send them; a host test per
  purpose); a slow engine (FR-004 budget; clients never wait on it, shell spec 006 FR-011).
- **What it never touches:** the network, partitions, the boot chain, system files.
- **Escape route:** learning off, erase, or going back to the previous adaptation; the
  keyboard works without handwriting.

## Acceptance

Host level: the interface against recorded stroke files (recognize, streaming, prompted
alignment, gestures); the sample rules (no sample without confirmation, none from a
private field, erase and export/import round trip); an adaptation that does worse is
rejected; a contribution file holds only reviewed samples and no client, time or device;
words (FR-015) off in every non-prose field, a list word never first, a proposed pick
that doesn't fit makes no sample; the character model (FR-019) on and off per field and
per request, a pick it put first that doesn't fit makes no sample, and the error rates
with and without it on held-back samples; error rates with and without words on held-back
samples; size (FR-017): a dot, comma, apostrophe and hyphen and the case
pairs at their true size next to a known reference read as written, and words and
sentences of a person's samples read no worse than without it; writer stores (FR-021):
samples kept for a writer's store are in it alone and the person's store is unchanged byte
for byte, a removed store is gone and refuses requests, the refusals, and readings for a
writer's store equal the base model's whatever the person's adaptation and learned words.

Hardware: supervised: writing on the keyboard's paper in English; an adaptation
after a day of use, compared on held-back samples.

## Out of scope

- Turning stroke groups into a font file (the font app's work; this program delivers the
  aligned strokes).
- Recognition of whole pages, layout or drawings (shapes, maths).
- Any recognition off the device.
