# Feature Specification: Your handwriting (font and neatening)

**Feature Branch**: `003-your-handwriting`
**Status**: Draft
**Input**: A font made from a person's own handwriting, usable anywhere a font can be
chosen, and a way to neaten hasty ink towards the best version of that same handwriting,
both learned on the device from the person's samples.

## Context

The recognizer (spec 001) and the samples app (spec 002) already collect a person's
handwriting with exact labels: every character's strokes are known. The same samples can
describe how the person writes each letter at their best. Two features follow from that:

- **A handwriting font**: the person's letters as an ordinary font, so their hand can set
  any text on the system (notes, titles, cards) the way a typeface does.
- **Neatening**: hasty ink moved towards the person's own best letters, with straight
  lines and even sizes. The result is still their handwriting, not a typeface: the target
  is learned from them. Neatening is a layer around the ink surface of the e-ink stack: it
  takes the pen's strokes, has them neatened and passes the neatened vector strokes on, so
  any app that draws pen ink through it, a notes app as much as a drawing app, gets
  neatened strokes without doing the neatening itself.

Both rest on one model of the person's best letters (Key entities: letter model), which
needs signals the existing samples don't carry: which samples were written with care, and
which of two versions the person likes better. Spec 002 gets two modes that collect them.

This spec is about the person's own hand on their own device. Learning from many writers
(donations, base models) stays as spec 001 and 002 describe.

## User scenarios

### Scenario 1 – Writing it your best (Priority: P1)

**Given** the samples app is open, **When** the person chooses "Write it your best",
**Then** prompts are shown as usual but framed as unhurried, careful writing, and each
kept sample is marked as careful.

**Given** the paired variant, **When** the person writes the same prompt once quickly and
then once carefully, **Then** both samples are kept and linked as a pair.

### Scenario 2 – Which one is nicer? (Priority: P1)

**Given** the person has at least two samples of the same character or word, **When** they
choose "Which one is nicer?", **Then** two of their own versions are shown side by side,
ink only, and a tap on one records the preference; "Both fine" and "Skip" record nothing
against either. Nothing is scored or ranked on screen.

### Scenario 3 – My handwriting as a font (Priority: P1)

**Given** enough samples, **When** the person asks for their font, **Then** a preview shows
a sentence in it, characters that are not yet covered are listed with a way to write them
now, and on confirmation the font is installed for this person and appears wherever fonts
are chosen (the settings app, text editors, notes apps).

**Given** the font is installed, **When** the person writes more samples and asks again,
**Then** the font is rebuilt under the same name and apps that use it show the new version.

### Scenario 4 – Neaten my ink (Priority: P2)

**Given** an app that draws pen ink through the neat ink layer (the handwriting app's own
writing page, a notes app, a drawing app, the keyboard's writing paper) with neatening on,
**When** the person writes, **Then** each stroke appears under the pen as written, and once
the words it belongs to are read it is replaced by its neatened version: the same text in the
same hand. A strength control moves between the original and the neatest version, and the
original can be brought back at any time.

### Scenario 6 – Drawing stays drawing (Priority: P2)

**Given** an app that draws through the neat ink layer with neatening on, **When** the person
draws shapes, arrows or a sketch beside their writing, **Then** those strokes stay exactly as
drawn; only what is read as writing is neatened.

### Scenario 5 – Letters not learned yet (Priority: P2)

**Given** ink containing characters the letter model doesn't cover, **When** it is
neatened, **Then** those characters get only the line-level corrections (baseline, slant,
size, spacing) and keep their shape.

## Requirements

### Collecting

- **FR-001**: A sample can carry how it was written: `careful` (written to "Write it your
  best"), `quick` (the quick half of a pair) or nothing (normal). The marking is part of the
  sample format (docs/samples.md) and travels with the sample in export and donation.
- **FR-002**: A pair links a quick and a careful sample of the same prompt; deleting one
  sample of a pair keeps the other, unlinked. The link is a random value both samples carry,
  so it says nothing about the person and survives export and donation unchanged.
- **FR-003**: A preference records two samples (or the same character in two samples, by
  its span) and which one the person chose. Preferences are the person's data: kept with the
  samples, exported with them, erased with them, donated only together with both samples
  they refer to and under the same donation rules (spec 002 FR-011). Deleting either sample
  removes the preference. In a file of samples (an export, a donation) a preference is a
  record of its own beside the samples, naming the two samples of that same file it refers
  to; a reader that wants only samples can pass over it.
- **FR-004**: "Which one is nicer?" picks pairs that teach the most: characters with few
  preferences, versions that differ visibly, and characters the font or neatening uses
  most. It never shows a sample from a private field.
- **FR-005**: Careful and quick samples are also ordinary training samples for the
  recognizer (spec 001 FR-009); the modes add signals, they don't take samples away.

### The letter model

- **FR-006**: For each character the letter model holds a variant for each way the person
  writes it often enough: their usable samples of the character are grouped by structure
  (stroke count, stroke order and rough shape), and a group gives a variant when it holds
  at least 3 samples and at least a tenth of the character's samples (a one-off slip gives
  none). A character has up to 5 variants, each weighted by how often the person writes it
  that way. Each variant is built from the samples of its group with the person's
  preferences, careful marking, readability (how well the recognizer read it when it was
  written) and consistency (closeness to the group's typical shape) as weights. Jitter is
  removed; shape, proportions, stroke count and stroke order stay the person's. A variant
  is never made by mixing versions written with different strokes.
- **FR-007**: A character is covered when it has at least 3 usable samples: written to a
  prompt with its fit check passed (spans exact) or, for a prompt of one character alone,
  kept on the person's confirmation after a failed check (its strokes can only be that
  character's: a lone full stop or comma counts), and with strokes of its own (a stroke
  shared with a neighbour, as in joined writing, is not cut). Which strokes are a
  character's goes by where the ink lies as well as by the order of writing: a stroke written
  after the rest (an i's dot, the bar of a t or an f, the arms of an E, the second stroke of
  an x) is part of the character it lies across or stands over, in words as well as alone,
  and a neighbour's stroke that the alignment put beside a small mark (a full stop, a comma)
  stays the neighbour's, so both count. Line metrics (baseline,
  x-height, cap height, ascender, descender, slant, letter spacing, word spacing) come from
  the person's word and sentence samples. The same samples, markings and preferences
  always give the same letter model.
- **FR-008**: The letter model is rebuilt when samples, markings or preferences changed,
  under the same conditions as adaptation (spec 001 FR-009: charging and idle, or when the
  person asks), and stays on the device. Having a page open that shows or uses it (the font
  page, neatening) counts as asking: when the model is missing or out of date (its samples,
  markings or preferences changed, or the way it is built changed) such a page has it built in
  the background at the lowest priority, shows its progress and stays usable meanwhile, and
  offers to build it at once. Building the letter model never changes the installed font
  (FR-011). Erasing samples (all, or one client's) erases the
  letter model, the preferences and the font with them; deleting one sample leaves the
  letter model out of date until its next rebuild.
- **FR-030**: How many usable samples each character has is known from the samples
  themselves, whether a letter model is built or not: a page about the person's letters shows
  these counts as soon as it opens, says whether the letter model is built, out of date or
  being built, and never shows an empty page because the model isn't built yet.

### The font

- **FR-009**: The font is a standard outline font the system installs for this person
  only, named by the person (default: "My handwriting"). It maps exactly the covered
  characters and the space (the person's word spacing), drawn in the person's ink width
  (the pen's width on the writing paper they wrote their samples on, relative to their
  x-height); a character's other variants are in the font without a character of their own.
  Every other character falls back to the system's default font.
- **FR-010**: Repeated letters don't look stamped: the font switches between a character's
  variants along the text, each about as often as the person writes it that way, the same
  way every time for the same text, by contextual
  alternates that text shaping applies by default (no setting in the app). The preview
  (Scenario 3) shows the same variants as the installed font for the same text.
- **FR-011**: The font carries a version that changes with every rebuild, in the font and in
  its file name, so apps and the font cache pick up the new outlines without a restart of
  the session. A rebuild keeps the name; the font is rebuilt only when the person asks.
- **FR-012**: The font never leaves the device except by the person's own export; it is not
  part of a donation.

### Neatening

- **FR-013**: Neatening takes ink and returns ink: the same strokes in the same order, each
  point moved, with timing, pressure and tilt kept. The text it is read as comes from the
  recognizer (spec 001), which also tells which strokes form which character.
- **FR-014**: Neatening applies line-level corrections (straight baseline, steady slant,
  even letter size and spacing) and moves each covered character towards its nearest
  variant in the letter model with the same strokes (a t written in one stroke towards a
  one-stroke variant); a character none of whose variants has its strokes is not moved. A strength from 0 to 1 scales both; 0 returns the ink
  unchanged, 1 the neatest version.
- **FR-015**: The same ink, letter model and strength always give the same result.
- **FR-016**: Neatening is a request in the recognizer's interface (docs/recognizer.md), so
  every app gets the same result; apps reach it through the neat ink layer (FR-018), keep the
  original ink and let the person switch back. Neatening never learns from the ink it is
  given: nothing it neatens becomes a sample, and it never changes the letter model.
- **FR-024**: Neatened ink is never data about the person's hand. The neat ink layer
  always passes the raw strokes on beside the neatened ones, and itself feeds recognition,
  samples, adaptation (spec 001 FR-009), the letter model and donations from the raw pen
  input, whatever the app keeps. An app may keep raw strokes, neatened ones or both in its
  own documents; neatened ink it keeps is marked as neatened, and neatened ink that reaches
  a sample (a confirm, an import, a donation) is refused, or kept marked and left out of
  adaptation, the letter model, base training and donations.
- **FR-017**: Ink the recognizer can't read, or reads with low confidence, gets only the
  line-level corrections.

### The neat ink layer

- **FR-018**: The neat ink layer sits around the ink surface of the e-ink stack (the ink
  areas and pen-preview brushes of the ink protocol, and the panel engine's pen preview).
  An app that draws pen ink uses it in place of the bare ink surface: it sets its ink areas
  and brushes through the layer as it would on the ink surface, and the layer takes the
  pen's strokes in those areas and passes on vector strokes: each raw stroke as it is drawn,
  and, once the recognizer has read the writing it belongs to, its neatened version at the
  app's current strength, marked as replacing that raw stroke. A replacement can come again
  when later strokes change how earlier ones are read.
- **FR-019**: The panel engine's preview at the pen's tip stays the raw stroke, so the layer
  adds no latency under the pen; the neatened strokes reach the screen with the app's next
  frame, which replaces the preview as with any ink.
- **FR-020**: Only writing is neatened. Strokes the recognizer reads as a gesture, as
  drawing (shapes, arrows, sketches) or not at all pass on unchanged, so a drawing app can
  leave the layer on.
- **FR-021**: The app keeps the raw strokes: the layer gives both versions, a change of
  strength re-neatens the kept writing without new pen input, and strength 0 gives the raw
  strokes back. What the app saves (raw, neatened or both) is its choice; the layer itself
  keeps nothing after the app is done with a piece of writing.
- **FR-022**: Neatening is off until the app or the person turns it on for the app. With it
  off, without a letter model or without a recognizer, the layer passes the raw strokes on
  unchanged and the app works as on the bare ink surface. Without a letter model, ink gets
  only the line-level corrections.
- **FR-023**: Ink in a private field (a password, a PIN; spec 001's field rules) is neither
  read nor neatened, and the layer sends the recognizer nothing from it.

### Text from the neat ink layer

- **FR-025**: The neat ink layer is the whole of pen to strokes and text: with text output
  on, it passes on with the strokes what they are read as, so a client such as the
  keyboard's writing paper uses the layer instead of talking to the recognizer itself.
  Text output is chosen per surface and off by default: a drawing surface doesn't pay for
  recognition it doesn't use (it still gets the readings neatening needs, without being
  sent them).
- **FR-026**: The text comes as ranked candidates, best first, each with its text, where it
  came from (the ink's own reading, another reading of the ink, a word of the list, the
  search the words guided; spec 001) and its fit verdict (whether it passed the check a
  picked candidate gets), and with which strokes make which character. Partial readings
  come while the person writes; each reading names the strokes it covers, and a newer one
  replaces the older.
- **FR-027**: A surface gives the layer its field context: the input purpose and hints
  (prose, an email address, a web address, digits, a private field), the text around the
  cursor, and the languages. The layer applies the recognizer's field rules (what is read,
  what may be learned) unchanged.
- **FR-028**: The client tells the layer what the person did with a reading: picked a
  candidate, accepted the reading, or corrected it, with the chosen text. That is the
  confirmation spec 001 learns from: the layer stores the raw strokes with that text as a
  sample under the field rules, and nothing is stored without it.
- **FR-029**: Strength 0 gives the raw strokes and the text, with no neatening; text output
  works the same at every strength and with neatening off.

## Key entities

- **Careful / quick marking**: how a sample was written (FR-001), and the pair it belongs to.
- **Preference**: two versions and the one chosen (FR-003).
- **Letter model**: per character, the person's best variants and the line metrics; derived
  data, rebuilt from samples, markings and preferences.
- **Handwriting font**: the letter model as an installed font, named and versioned.
- **Neat ink layer**: what an app draws pen ink through: pen strokes and field context in;
  raw and neatened vector strokes, and on request the text they are read as, out; the
  person's choice of a reading back in.
- **Neatened ink**: the raw ink moved towards the letter model, marked as neatened wherever
  it is kept; never a sample.

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Sample format (marking, pair, preference records) | owned | minor bump of v1 | docs/samples.md |
| Recognizer interface (letter model build and status, font build and installation, neaten) | owned | minor bump | docs/recognizer.md |
| Neat ink layer (pen strokes and field context in; raw and neatened vector strokes, strength, candidates out; picks back) | owned | new, version 1 | this spec (FR-018 to FR-029); its protocol definition beside the ink protocol in GlassineOS/protocols |
| Ink protocol (ink areas, pen-preview brushes) | consumed and extended: the neat ink layer wraps it | glassine_ink_v1 | GlassineOS/protocols `glassine-ink-v1.xml` |
| E-ink stack pen preview (the panel engine draws the stroke at the pen's tip) | consumed | — | GlassineOS/epd |
| GTK ink areas module | consumed: unchanged, it announces areas only; neatening needs an app that draws through the layer | — | GlassineOS/shell spec 003 |
| Per-person font installation | consumed (freedesktop font directories, the system font cache) | — | — |
| Font choice in the settings app | consumed (GlassineOS/shell settings) | — | shell specs |
| Writing paper: strokes, text candidates and Neaten | consumed by GlassineOS/shell spec 006 (the keyboard's writing paper, GlassineOS/wvkbd), through the neat ink layer instead of the recognizer directly | — | shell spec 006 |

## Device scope and risk

Device-neutral; any device with a pen that runs the recognizer. No device risk: nothing
flashes or touches boot, recovery or updates. User data: the letter model, preferences and
font live with the person's samples and follow their erase and export rules (FR-003,
FR-008, FR-012).

## Acceptance

Host level:

- a letter model built from a fixed set of samples is the same on every run; a character
  with fewer than 3 usable samples is not covered;
- in words and alone, the versions of t, f, i, j, k, x, E and H written with a late dot, bar
  or arm keep that stroke, and a full stop or comma beside a letter counts for both;
- a character written one way in about two thirds of its samples and another way in a
  third gets a variant for each, weighted alike, and the font shows them in about that
  proportion along a long text; a way written once among many gets none;
- without a letter model, and with one that is out of date, the usable samples per character
  are reported as a built model would count them; the font page and neatening show them, say
  the model isn't built or is out of date, and build it without installing the font;
- the font built from it passes a standard font validator, maps exactly the covered
  characters and the space, alternates variants deterministically when shaped with no
  feature requested, and installs and is found by the system font cache in a test user;
  erasing the samples removes it;
- neatening at strength 0 returns the input byte for byte; at strength 1 every covered
  character is closer to its variant than before, unless the reading check gives its line
  back; a character is pulled towards a variant with its own strokes; stroke count, order,
  timing and pressure are kept;
- the recognizer reads neatened ink at least as well as the original on a held-back set;
- through the neat ink layer, with a stand-in pen and recognizer: raw strokes pass on at
  once and unchanged, each neatened stroke names the raw stroke it replaces, strokes read
  as drawing pass unchanged, strength 0 gives the raw strokes, and a private field sends
  the recognizer nothing;
- whatever a client keeps (raw strokes, neatened ones or both), a confirmation through the
  neat ink layer with neatening on stores a sample whose strokes are byte for byte those a
  client talking to the recognizer directly stores for the same pen input; neatened ink
  that reaches a sample path is refused or kept marked and left out of adaptation, the
  letter model and a donation;
- through the neat ink layer with text output on, the same pen input and field context give
  the same candidates (text, origin, fit verdict, character strokes) as the recognizer's own
  reading, partial readings arrive while writing, a pick stores one sample with the picked
  text, a surface with text output off gets no candidates, and strength 0 gives raw strokes
  and text.

Product level: in the images acceptance suite, a test user with a prepared sample store
builds the font, the font appears in the settings app's font list, and a text set in it
renders; in the handwriting app's writing page with neatening on, written ink is replaced
by its neatened version and the original comes back at strength 0.

## Out of scope

- Generated handwriting with joined letters for text never written (a learned writing
  model; a later spec).
- Neatening with another person's letter model, or sharing a handwriting font.
- Fonts for scripts the recognizer has no model for.

## Success criteria

- **SC-001**: With 40 careful samples of the alphabet in both cases and the digits, the
  font covers all of them.
- **SC-002**: The font builds in under 2 minutes on the device while charging, without
  making the device less responsive.
- **SC-003**: Neatening one line of ink answers within 300 ms on the device.
- **SC-004**: The recognizer's error on neatened ink is not higher than on the original.
- **SC-005**: In a blind check of original against neatened ink at the default strength,
  the person prefers the neatened version in at least 7 of 10 cases.
