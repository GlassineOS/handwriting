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
  is learned from them.

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

**Given** handwritten ink in a client that offers it (the keyboard's writing paper, a notes
app), **When** the person chooses Neaten, **Then** the ink is replaced by a neatened version
of the same text in the same hand, a strength control moves between the original and the
neatest version, and the original can be brought back at any time.

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

- **FR-006**: For each character the letter model holds the person's best versions of it:
  up to a small number of variants, each built from their samples of that character with
  the person's preferences, careful marking, readability (how confidently the recognizer
  reads it) and consistency (closeness to the person's typical shape) as weights. Jitter is
  removed; shape, proportions, stroke count and stroke order stay the person's.
- **FR-007**: A character is covered when it has at least 3 usable samples (spans known,
  fit check passed). Line metrics (baseline, x-height, cap height, slant, letter spacing,
  word spacing) come from the person's word and sentence samples.
- **FR-008**: The letter model is rebuilt when samples, markings or preferences changed,
  under the same conditions as adaptation (spec 001 FR-009: charging and idle, or when the
  person asks), and stays on the device. Erasing samples erases the letter model, the
  preferences and the font with them.

### The font

- **FR-009**: The font is a standard outline font the system installs for this person
  only, named by the person (default: "My handwriting"). It contains the covered characters
  in the person's ink width; every other character falls back to the system's default font.
- **FR-010**: Repeated letters don't look stamped: the font switches between a character's
  variants along the text, the same way every time for the same text.
- **FR-011**: The font carries a version that changes with every rebuild, so apps and the
  font cache pick up the new outlines without a restart of the session.
- **FR-012**: The font never leaves the device except by the person's own export; it is not
  part of a donation.

### Neatening

- **FR-013**: Neatening takes ink and returns ink: the same strokes in the same order, each
  point moved, with timing and pressure kept. The text it is read as comes from the
  recognizer (spec 001), which also tells which strokes form which character.
- **FR-014**: Neatening applies line-level corrections (straight baseline, steady slant,
  even letter size and spacing) and moves each covered character towards its nearest
  variant in the letter model. A strength from 0 to 1 scales both; 0 returns the ink
  unchanged, 1 the neatest version.
- **FR-015**: The same ink, letter model and strength always give the same result.
- **FR-016**: Neatening is a request in the recognizer's interface (docs/recognizer.md),
  so every client offers the same result; clients keep the original ink and let the person
  switch back. Neatening never learns from the ink it is given.
- **FR-017**: Ink the recognizer can't read, or reads with low confidence, gets only the
  line-level corrections.

## Key entities

- **Careful / quick marking**: how a sample was written (FR-001), and the pair it belongs to.
- **Preference**: two versions and the one chosen (FR-003).
- **Letter model**: per character, the person's best variants and the line metrics; derived
  data, rebuilt from samples, markings and preferences.
- **Handwriting font**: the letter model as an installed font, named and versioned.

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Sample format (marking, pair, preference records) | owned | minor bump of v1 | docs/samples.md |
| Recognizer interface (letter model build, font build, neaten) | owned | minor bump | docs/recognizer.md |
| Per-person font installation | consumed (freedesktop font directories, the system font cache) | — | — |
| Font choice in the settings app | consumed (GlassineOS/shell settings) | — | shell specs |
| Writing paper offering Neaten | consumed by GlassineOS/shell spec 006 (the keyboard) | — | shell spec 006 |

## Device scope and risk

Device-neutral; any device with a pen that runs the recognizer. No device risk: nothing
flashes or touches boot, recovery or updates. User data: the letter model, preferences and
font live with the person's samples and follow their erase and export rules (FR-003,
FR-008, FR-012).

## Acceptance

Host level:

- a letter model built from a fixed set of samples is the same on every run; a character
  with fewer than 3 usable samples is not covered;
- the font built from it passes a standard font validator, contains exactly the covered
  characters, alternates variants deterministically, and installs and is found by the
  system font cache in a test user;
- neatening at strength 0 returns the input byte for byte; at strength 1 every covered
  character is closer to its variant than before; stroke count, order, timing and pressure
  are kept;
- the recognizer reads neatened ink at least as well as the original on a held-back set.

Product level: in the images acceptance suite, a test user with a prepared sample store
builds the font, the font appears in the settings app's font list, and a text set in it
renders.

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
