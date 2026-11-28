# Feature Specification: Written in your hand (generated handwriting)

**Feature Branch**: `spec-007-written-in-your-hand`
**Status**: Draft
**Input**: A handwriting generator that keeps a writer's style apart from the shape of each
character, so the person's style, worked out on the device from their own samples, writes text
they never wrote: letters they haven't written yet in their own style, Neatest as a neat
rewriting of what they wrote, typed text shown as ink in their hand, and text from elsewhere
shown in a hand that is clearly not theirs. The generator ships centrally in a package of its
own, may carry non-commercial terms, and every feature works without it.

## Context

The person's font, Autograph, and neatening (spec 003) draw on the person's own letters only:
the letter model covers a character once the person has written it at least 3 times, and every
character it doesn't cover yet comes from the plain base font. A person who has written a few
sessions sees their font mixed with school print ("Plain letters are the base's still"), and
Neatest keeps the written ink of every letter it doesn't cover.

A learned handwriting generator can close that gap. Trained on many writers, it separates what
belongs to a writer (their style: slant, proportions, how round, how fast, how they join) from
what belongs to a character (its shape in anyone's hand), so a style learned from the
characters the person did write can write the characters they didn't, and whole words and
lines they never wrote. The approach follows "Generating Handwriting via Decoupled Style
Descriptors" by Atsunobu Kotani, Stefanie Tellex and James Tompkin (ECCV 2020).

This spec adds four things that rest on it, each a better way to show text in ink:

- **letters in your style**: every character the letter model doesn't cover yet comes out in
  the person's style instead of the base's print, in Autograph and at Neatest;
- **rewriting**: at Neatest, a word whose text is known can be written anew, neatly, in the
  person's style, instead of having its strokes pulled onto their font;
- **typed words in your hand**: words without ink in a writing region (spec 005: typed in text
  mode or given by the app) can be shown as ink in the person's hand instead of set in their
  font;
- **another hand**: text that came from elsewhere (another app, a synced server) can be shown in
  a made-up hand clearly unlike the person's, so they see at a glance they didn't write it.

The generator is the same for everyone and ships with the system; what is the person's is only
their **style**, worked out on the device from their own samples, never elsewhere. The
generator may be trained on data with non-commercial terms (such as the BRUSH dataset, used
with its authors' permission), so it ships in a package of its own that carries those terms,
and nothing in the system depends on it: without it, everything is as specs 003 and 005 say.

Relation to earlier specs: every requirement of specs 001 to 006 stays. Where this spec gives
a character, a word or a field another source of ink, it says so in the requirement of the
earlier spec (spec 003 FR-009, FR-035, FR-043; spec 005 FR-018) by pointing here. These apply
to generated ink as they apply to neatened ink: neatened or generated ink is never data about
the person's hand (spec 003 FR-024, spec 005 FR-014), and a private field is never read,
neatened or written for (spec 003 FR-023, spec 004 FR-010).

Terms used below:

| Term | Meaning |
|---|---|
| generator | the shipped model that writes a text as pen strokes in a given style |
| style | what the generator needs to write in one person's hand, worked out from their samples |
| generated ink | strokes the generator wrote; never written by a pen |
| letter in your style | a generated way of writing a character the letter model doesn't cover |
| rewritten word | a word at Neatest replaced by generated ink of its text in the person's style |
| text from elsewhere | text in a writing region that neither the person's pen nor their typing on this device made |
| another hand | a shipped made-up style for text from elsewhere, clearly unlike the person's |
| likeness | how close ink is to the person's own writing of the same text, measured against their held-back samples (FR-009) |

## User scenarios

### Scenario 1 – Letters you haven't written yet (Priority: P1)

**Given** the generator is installed and the person has written their first session of prompts
(at least 20 words) in the samples app, **When** their style is ready and they open the font page
on the Autograph tab, **Then** the preview shows the whole sentence in their style: the letters
they wrote at least 3 times from their own samples, the others in their style, none in the
base's print. The list of characters says which are "written by you" and which are "in your
style, not written yet", each with a way to write it now.

**Given** that font page, **When** the person installs or rebuilds Autograph, **Then** apps that
use Autograph show the letters in their style where their own letters are still missing.

**Given** the person writes a character in its style 3 times, **When** the letter model learns it
(spec 003 FR-008, FR-032), **Then** that character comes from their own samples and is listed as
written by them.

### Scenario 2 – Your style, from the first session (Priority: P1)

**Given** a person who has just written their first session and the generator is installed,
**When** the device is charging and idle (or they ask on the font page), **Then** their style is
worked out in the background at the lowest priority, with its progress shown and the device
usable meanwhile. Until it is ready, everything is as without the generator, and the font page
says "Your style is being worked out" or, with too few samples, "Write a few more prompts so
your style can be worked out".

### Scenario 3 – Typed words in your hand (Priority: P2)

**Given** "Typed words in my hand" is on, **When** the person changes "noon" to "one" in a
field's text mode (spec 005 Scenario 4) and goes back to ink mode, **Then** "one" is shown as ink
in their hand, on the field's line at their size, varied and joined as they write, and the
unchanged words keep their ink. **When** they turn the setting off, **Then** "one" is set in
Autograph. The field's value is "Meet the team at one" either way.

### Scenario 4 – Neatest rewrites (Priority: P2)

**Given** an app that draws through the neat ink layer at Neatest, with Rewrite on, **When** the
person writes a line and its text is known, **Then** each word whose rewriting passes its checks
(FR-021) is replaced by the same text written anew, neatly, in their style: on a straight line,
evenly sized and spaced; the other words are neatened as Neatest does without rewriting.
**When** the person turns on "Show rewritten", **Then** the rewritten words are picked out.
**When** they lower the strength or turn Rewrite off, **Then** the words come back as written or
neatened.

### Scenario 5 – Another hand for text from elsewhere (Priority: P2)

**Given** an app that shows text from elsewhere in another hand (the planner's synced entries,
GlassineOS/planner spec 002), **When** an appointment made on a phone arrives, **Then** its
title is shown written in a hand that is plainly not the person's. **When** the person writes
after it with the pen, **Then** their words are their ink beside it; a word they change in text
mode is theirs from then on (their font, or their hand with Scenario 3's setting).

### Scenario 6 – Without the generator (Priority: P1)

**Given** the generator isn't installed (or was removed), **When** the person opens the font page,
**Then** Autograph shows the base's plain letters where theirs are missing, as in spec 003, and
one sentence says why and how to change it: "Letters you haven't written yet are plain: the
handwriting generator isn't installed." **When** they open the neatening control, a field's
text-mode bar or an app's setting for text from elsewhere, **Then** the features of this spec
are offered as unavailable with the same kind of sentence, and everything else works as specs
003 and 005 say.

**Given** the person opens the generator's row on the Autograph tab, **When** they read it,
**Then** it says in plain words what the generator does, that it runs only on the device, and
the terms it comes under: "Made from handwriting shared for non-commercial use only, and
offered under those terms."

### Scenario 7 – Generated ink teaches nothing (Priority: P1)

**Given** typed words in the person's hand, rewritten words or letters in their style on the
page, **When** the person confirms the field's text, contributes samples or the recognizer
adapts, **Then** no generated stroke becomes a sample, shapes the letter model or the style, or
reaches a contribution.

### Edge cases

- **A character the generator can't write** (a script or symbol it wasn't trained on) comes from
  the base, in the font and in set words; in a rewritten or generated word it is drawn
  from the person's font at its place (FR-022).
- **A word with letters written and letters not yet written**: at Neatest's setting (spec 003
  FR-043) the covered letters are set on the person's own glyphs and the others on their letters
  in your style; when rewritten the whole word is generated.
- **Very few samples**: the style isn't ready (FR-008) and nothing generated is shown.
- **All samples erased**: the style is erased with them, Autograph returns to the base, and kept
  generated ink is dropped (FR-030); everything shows as without the generator.
- **A new version of the generator**: the style is worked out again for it; until it is ready the
  features fall back, and kept generated ink is made again in the background (FR-031).
- **The generator is removed while a page shows generated ink**: the next time each view is drawn
  the affected words are shown as without it (font, plain letters, Neatest's set words, the app's
  own way for text from elsewhere); nothing is lost from what the person wrote or typed.
- **A guest's session** (spec 002) has no style and no generated features; text from elsewhere in
  a guest's session is shown as without the generator.
- **A private field**: none of its text is given to the generator; typed words there stay as any
  private text field shows them.
- **A word far wider or narrower when rewritten**: it isn't rewritten (FR-021) and gets Neatest's
  setting, so the line keeps its layout.
- **Generation slower than the targets** (a busy or slow device): the word is shown as without
  the generator first and replaced once (FR-032); the pen never waits.
- **The other hand too close to the person's**: another shipped other hand is used; with none
  clearly unlike the person's, text from elsewhere is shown as without the generator (FR-027).
- **A rewritten word the person strikes through or erases**: the gesture acts on the word where it
  is shown (spec 005 FR-012); erasing removes the word's raw strokes, and the rewritten version
  with them.

## Requirements

### The generator

- **FR-001**: The generator is a model that writes a given text as pen strokes (positions over
  time, pen down and up, pressure) in a given style, with a writer's style kept apart from each
  character's shape, so a style learned from some characters writes all the characters the
  generator knows. It is the same for everyone, trained centrally under the repository's rules on
  models and data, and runs only on the device; no part of it opens a network connection.
- **FR-002**: The generator ships in a package of its own, apart from the recognizer and the base
  models, with its training recipe, data sources and licences. When its data carry terms (such as
  non-commercial use), the package carries them, its description says so, the Autograph tab's
  generator row says so in plain words (Scenario 6), and ink it writes is treated as carrying
  them (FR-017). It is installed and removed as any system package; the handwriting programs never
  fetch it.
- **FR-003**: The generator is checked before it is used, as an installed model is (spec 006):
  a package that is damaged, of a version the system doesn't read, or gives a number that isn't
  finite on a fixed text isn't used, and the features fall back (FR-005).
- **FR-004**: Whether the generator is usable, and whether the person's style is ready, is
  announced with the system's handwriting capabilities (spec 004 FR-003) and changes when the
  package comes or goes or the style becomes ready, without restarting the session.
- **FR-005**: **Without the generator** (not installed, removed, refused by its check, failing
  on a request, or the person's style not ready) every feature of this spec behaves exactly as
  specs 003 and 005 say without it: uncovered characters from the base, Neatest as spec 003
  FR-043, words without ink set in the person's font, text from elsewhere as the app shows it
  without. Nothing waits for the generator and nothing fails without it. Where the person would
  use a feature of this spec, one plain sentence says that it isn't available and why (not
  installed, style not ready yet, too few samples), with a way to install the package where the
  system offers one.

### The person's style

- **FR-006**: The style is worked out on the device from the person's own samples in their
  store: prompted samples whose spans are exact (spec 003 FR-007's usable samples) and, as they
  allow, whole words and sentences. Never from a guest's store, from generated or neatened ink,
  or from a private field. No step of it happens anywhere but on the device: the generator ships
  ready, and only the style is learned, from the samples where they are.
- **FR-007**: The style is worked out under the conditions of adaptation (spec 001 FR-009:
  charging and idle, or when the person asks on the font page), at the lowest priority, with its
  progress shown and the device usable meanwhile. It is worked out again when samples were added
  or removed, or the generator changed. The same samples and the same generator always give the
  same style.
- **FR-008**: A style is ready only when it was worked out from at least a first session's
  samples (at least 20 words with exact spans) and passes a check on held-back samples: ink it
  writes for their texts is read by the recognizer as those texts at least as often as the
  person's own ink of them is, and is closer to the person's own ink of them (by likeness, FR-009)
  than ink in every shipped other hand is. A new style replaces the one before only when it does
  at least as well on the same held-back samples. A style that isn't ready is not used.
- **FR-009**: **Likeness** is one measure, used everywhere this spec checks faithfulness: how
  close a piece of ink is to the person's own writing of the same text, compared against their
  held-back samples, independent of where on the page it stands and how large it is. It is the
  same measure the success criteria use, documented with the recognizer, and it can be computed
  on the host from recorded stroke files.
- **FR-010**: The style is the person's data, derived from their samples: kept with the store,
  readable only by them, erased with the samples (all of them, or one client's when that changes
  it), never exported (an import works it out again), never contributed, and never leaving the
  device. A writer's store (spec 002) has no style.

### Letters in your style

- **FR-011**: Each character has three sources, in this order: the letter model (a covered
  character, spec 003 FR-007), letters in the person's style (any other character the generator
  writes), and the base font (spec 003 FR-045). A covered character always comes from the person's
  own samples, never from the generator.
- **FR-012**: Letters in your style are made with the letter model's build (spec 003 FR-008,
  FR-032), from the person's style, for every character of the base's set the letter model doesn't
  cover and the generator writes: up to 3 ways of writing each, so repeated letters don't look
  stamped, each placed on the line and sized by the person's line metrics, with ink profiles,
  pressure and speed as a variant of the letter model has (spec 003 FR-006). They are spaced as
  spec 003 FR-007 spaces a character the person hasn't written next to others, and joined only by
  the person's own learned joins (spec 003 FR-044); they never add ways of joining.
- **FR-013**: Autograph draws letters in your style in all three styles (spec 003 FR-034); in
  Brush their width follows the pressure and speed the generator wrote. They take part in the
  cycle and the preview with their ways in equal shares (FR-012), and have no context or
  neighbour rules of their own. Making them never changes the person's
  own letters.
- **FR-014**: The font page and a character's page (spec 003 Scenario 3, FR-030, FR-033) tell the
  three sources apart: the characters list says "written by you", "in your style, not written
  yet" or "plain"; the note under the preview counts the letters in their style, and names the
  plain ones only when there are any; a character in their style opens on writing it, with its
  generated ways shown and marked as made, not written. Marks (spec 003 FR-031) apply to the
  person's own letters only.
- **FR-015**: A letter in your style is never a neatening target at Light, Medium or Strong:
  neatening pulls ink only towards the person's own letters (spec 003 FR-035), so an uncovered
  character keeps its shape there. At Neatest, a set word (spec 003 FR-043) sets an uncovered
  character on its letter in your style as it sets a covered one on its glyph, in place of
  keeping its written ink.
- **FR-016**: Letters in your style follow the style: when it is worked out again they are made
  again with the next build of the letter model, and the installed font changes with its next
  rebuild (spec 003 FR-011).
- **FR-017**: An exported Autograph (spec 003 FR-012) holds the person's own letters and the base
  font's for every other character: letters in their style are left out, so the exported fonts
  carry only the base font's licence. The export page says so in one sentence. The installed
  Autograph keeps them.

### Typed words in your hand

- **FR-018**: A word without ink in a writing region (typed in text mode or given by the app;
  spec 005 FR-018) can be shown as generated ink of its text in the person's style instead of set
  in their font: written word by word on the same line, at the same size and in the same place
  that spec 005 FR-021 gives a set word, with the generator's joins and variation inside the word,
  carrying the generator's timing and pressure. The app draws it as ink, as it draws neatened
  strokes. The value, the saved state, readings, reports and samples are the same as with the
  word set in the font (spec 005 FR-031).
- **FR-019**: "Typed words in my hand" is off by default. The person turns it on for every app in
  the handwriting settings (on the Autograph tab); an app may offer it per field, and a field's
  own choice wins over the person's. Off, or without the generator, the words are set in the
  person's font.
- **FR-020**: A region's display set (spec 005 FR-030) stays the font: words that have ink are
  shown set in the font, and words without ink follow FR-018 and FR-019.

### Rewriting at Neatest

- **FR-021**: Neatest (spec 003 FR-014) offers **Rewrite**, a choice beside the strength, off by
  default. With it on, each word whose text is known (the confirmed text, or else its reading,
  unless the word is read with low confidence, spec 003 FR-017) is replaced by generated ink of that text in the person's style,
  written neatly: on the line's baseline, at the line's x-height and the style's slant, its
  letters spaced by the letter model's spacing, placed where the written word starts. A word is
  rewritten only when its rewritten ink passes all of these, checked per word; otherwise it gets
  Neatest as spec 003 says:
  - the recognizer reads the rewritten ink as the word's text, as its first candidate;
  - for a word known only by its reading, the rewritten ink is close to the written word's own ink
    by likeness (FR-009), within a margin documented with the recognizer, so a misread word is not
    rewritten as another word;
  - its likeness (FR-009) to the person's writing is no lower than the likeness of the word as
    Neatest would set it, less a fixed margin documented with the recognizer;
  - it is about as wide as the written word (neither more than 1.6 times the other), so the line
    keeps its layout;
  - the word holds no drawing or gesture (spec 003 FR-020) and is not in a private field.
- **FR-022**: A rewritten word's strokes are new: spec 003 FR-013's "the same strokes, each point
  moved" doesn't hold for them. The neat ink layer passes them on marked as rewritten and as
  replacing the raw strokes of the word, so a strength below Neatest, Rewrite off or strength 0
  gives back the neatened or raw strokes exactly as without rewriting. A character the generator
  can't write is drawn there from the person's font.
- **FR-023**: The neatening reply (spec 003 FR-041) says per word whether it was rewritten, and
  when it wasn't, why: text not known, no generator, style not ready, not read back as its text,
  likeness too low, width, drawing or gesture. Each character of a rewritten word is reported as
  `rewritten`.
- **FR-024**: The person can tell which words were rewritten: an app that offers Rewrite also
  offers a way to pick out the rewritten words on request ("Show rewritten" on the handwriting
  app's writing page), and the neatening control says how many words of the page were rewritten.
  Rewritten words carry no lasting mark in the ink itself; an app that shows no markers in ink
  mode (GlassineOS/planner spec 001 FR-006) shows them only while the person asks.

### Another hand

- **FR-025**: The generator's package ships at least one other hand: a made-up style that is no
  single real writer's (none of the generator's training writers, nor any person's or guest's
  store), legible (the recognizer reads text written in it as often as it reads the base font's
  letters drawn as ink), and plainly not anyone's own: neat, upright and even.
- **FR-026**: An app can hand a writing region text as **from elsewhere** (spec 005 FR-025: with
  its value and saved state, or the text alone), and ask for text from elsewhere to be shown in
  another hand. Each word keeps that mark until the person changes it in text mode: a changed or
  added word is the person's (their font, or FR-018), and ink written into the region is the
  person's ink as always. The mark is part of the saved state, so it survives the app being closed
  and opened (spec 005 Scenario 6).
- **FR-027**: The device uses one other hand for all text from elsewhere, in every app: the
  shipped other hand least like the person's style by likeness, and none whose likeness to the
  person's writing comes near their own style's (a margin documented with the recognizer). With
  no other hand clearly unlike the person's, or without the generator, text from elsewhere is shown
  as the app shows it without (spec 005: the font, or the app's own typed face where it draws such
  text itself), and the capability (FR-004) says so.

### Generated ink is never the person's

- **FR-028**: Generated ink is marked as generated wherever it is kept or passed on, and is never
  data about the person's hand: never a sample, never part of adaptation, the letter model, the
  style, base training or a contribution. Generated ink that reaches a sample path (a confirm, an
  import, a contribution) is refused, or kept marked and left out of all of these, as neatened ink
  is (spec 003 FR-024). A word shown in generated ink has no ink of its own for spec 005 FR-014.
- **FR-029**: Generated ink is never read to make a value: the text it was written from is the
  value, and a confirmation of it stores nothing.

### Made once, kept

- **FR-030**: Generated ink is made once and kept on the device with the person's data, so
  drawing, scrolling, a new page or reopening a document never makes it again: letters in your
  style with the letter model's build, a word without ink when its text, its size or the style
  changes, text from elsewhere when it arrives or changes. What is kept is keyed by the text, the
  size, the style and the generator, so the app keeps nothing new; an app on an earlier version of
  the pen interface gets the font. Kept words are kept apart per app, as spec 004 keeps each app's
  writing apart: only the app they were made for and the person reach them. Kept generated ink is
  erased with the samples, and dropped when the generator is removed. Rewritten words are kept by
  the app instead: the neat ink layer passes them on with the neatened strokes and keeps nothing
  itself (spec 003 FR-021), and they are made again only when the word's ink, its text or the
  strength changes.
- **FR-031**: Kept words don't change while they are shown: when the style or the generator
  changes, kept generated words are made again in the background at low priority and shown the
  next time their view is drawn anew, never while the pen writes and never by a refresh of their
  own.
- **FR-032**: A word whose generated ink isn't ready when it is first shown (SC-006, SC-007) is shown as
  without the generator, and replaced by its generated ink once, in one refresh of only its area,
  never while the pen writes.
- **FR-033**: The same text, size, style and generator always give the same generated ink, and
  the same written line, letter model, style and strength give the same rewriting.

### Performance

- **FR-034**: Making generated ink never delays the ink under the pen (spec 003 FR-019) or a
  reading, and never makes the device less responsive: it runs below the recognizer's priority.

## Key entities

- **Generator**: the shipped model that writes text in a style; one package, its version, its
  terms (FR-001 to FR-003).
- **Style**: the person's hand as the generator needs it; derived from their samples, ready or not
  (FR-006 to FR-010).
- **Letter in your style**: a generated way of writing an uncovered character, made with the
  letter model's build (FR-011 to FR-016).
- **Generated ink**: strokes the generator wrote, marked as generated; kept by text, size, style
  and generator (FR-028 to FR-033).
- **Rewritten word**: a word at Neatest replaced by generated ink, with its checks and the reason
  when it wasn't (FR-021 to FR-024).
- **Other hand**: a shipped made-up style (FR-025, FR-027).
- **Text from elsewhere**: words of a region marked as not the person's until they change them
  (FR-026).
- **Likeness**: the faithfulness measure (FR-009).

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Generator package (its files, version, terms, recipe and sources; the other hands) | owned | new, version 1 | this spec (FR-001 to FR-003, FR-025); its format documented beside the base-model package format |
| Recognizer interface (style status and build, letters in your style with the letter model, generated words, rewriting in neaten and its reply, likeness) | owned | minor bump | docs/recognizer.md |
| Pen interface with writing regions (typed words in the person's hand, text from elsewhere and another hand, rewritten strokes marked) | owned (protocol definition in GlassineOS/protocols) | next version, additive: an app on an earlier version keeps spec 005's behaviour | spec 005; this spec |
| Handwriting capabilities (generator usable, style ready, another hand available) | owned | additive | spec 004 FR-003 |
| Sample format (generated ink refused or marked) | owned | minor bump of v1 | docs/samples.md |
| Autograph and neatening | owned by spec 003, extended where it points here | — | spec 003 |
| Text from elsewhere in another hand | consumed by GlassineOS/planner spec 002 (synced entries) | — | planner spec 002 |
| System packages (installing and removing the generator) | consumed | — | GlassineOS/shell settings |

## Device scope and risk

Device-neutral; any device with a pen that runs the recognizer. No device risk: nothing flashes or
touches boot, recovery or updates. User data: the style and kept generated ink live with the
person's samples and follow their erase and export rules (FR-010, FR-030). Licence: the generator
package may carry non-commercial terms; it is never required, and what leaves the device by the
person's export (Autograph) never holds its letters (FR-017).

## Acceptance

Host level, with a stand-in generator where the real one isn't needed:

- without the generator package, and with a damaged one, every host acceptance test of specs 003
  and 005 passes unchanged, the capability says the generator isn't usable, and each feature's
  sentence of FR-005 is given;
- a style worked out twice from the same samples is the same; one from too few samples, or one
  that fails the held-back check, is not ready and nothing generated is shown; a guest's store has
  none; erasing the samples erases it and the kept generated ink;
- with a ready style, the letter model's build gives letters in your style for exactly the base
  characters it doesn't cover and the generator writes, up to 3 ways each; a covered character
  comes only from the person's samples; Autograph built from it passes the font validator, maps
  every base character with no system fallback, and an exported Autograph holds no generated
  glyph;
- at Light, Medium and Strong no character is pulled towards a letter in your style; at Neatest a
  set word sets an uncovered character on it;
- with Rewrite on, a word with known text whose rewritten ink passes the checks is rewritten, its
  strokes marked as rewritten and as replacing the word's raw strokes; a word that fails any check
  gets Neatest as spec 003 says, with the reason in the reply; strength below Neatest, Rewrite off and
  strength 0 give the same strokes as without rewriting, byte for byte;
- a word typed in text mode with "Typed words in my hand" on comes as generated ink at the place
  and size spec 005 gives its set word, with the same value, saved state and reports as with the
  setting off; a field's own choice wins over the person's;
- text handed to a region as from elsewhere is shown in the other hand, keeps the mark through a
  saved state handed back, and loses it word by word when the person edits those words; with the
  other hand too close to a recorded person's style another one is used, and with none the
  app's fallback is announced;
- no generated stroke becomes a sample, shapes the letter model or the style, or reaches a
  contribution, through a confirmation, an import or a contribution;
- the same text, size, style and generator give the same ink byte for byte; drawing a field
  again, scrolling it and handing back its saved state make no generated ink again; a style
  change makes kept words again in the background and shows them only on the next drawing anew.

Product level: in the images acceptance suite, with the generator package installed and a prepared
sample store, a test user's Autograph shows no base letter in the preview sentence; with the
package removed, it shows the base's letters and the sentence of FR-005.

## Out of scope

- Generated ink as training data for base models (the recognizer, the aligner): a later decision,
  under the repository's rules on models and data.
- Writing in another real person's hand, or sharing the person's style; styles from a guest's
  store.
- Training or tailoring the generator itself on the device beyond working out the style.
- Scripts and languages the generator wasn't trained for.

## Assumptions

- A first session of the samples app (at least 20 prompted words) is enough for a usable style; the
  readiness check (FR-008) decides it per person.
- The generator can be trained on data the project may use; when its data carry non-commercial
  terms, shipping it as a package of its own under those terms is allowed by them.
- Writing regions (spec 005) and the neat ink layer (spec 003) are what apps draw generated ink
  through; an app that draws ink otherwise gets none of it.

## Success criteria

- **SC-001**: After a first session (at least 20 words with exact spans) and with the generator
  installed, every character of the preview sentence on the font page is in the person's own hand
  or their style, none in the base's print, once the style is ready.
- **SC-002**: Likeness: on a fixed held-back set of at least 20 writers, a writer-identification
  measure picks the right writer among them for generated ink of a held-back text in at least 8 of
  10 cases, and picks the person for the other hand in fewer than 1 in 20.
- **SC-003**: Legibility: the recognizer reads generated words, in a person's style and in the
  other hand, as their text at least as often as it reads the same writers' own ink of those
  texts.
- **SC-004**: In a blind check against Neatest without rewriting, the person judges rewritten lines
  to be their own handwriting in at least 8 of 10 cases and prefers them in at least 6 of 10; and
  they tell text in the other hand from their own in at least 19 of 20.
- **SC-005**: The style is worked out within 10 minutes while the device charges, without making
  it less responsive; Autograph with letters in your style builds within 3 minutes (spec 003
  SC-002 allows 2 without them).
- **SC-006**: A field's words without ink (up to 10 words) come as generated ink within 1 second of
  leaving text mode; a line at Neatest with Rewrite on answers within 1 second (spec 003 SC-003:
  300 ms without rewriting).
- **SC-007**: Text from elsewhere arriving with a sync is ready in the other hand before the person
  opens its view in at least 9 of 10 cases; otherwise it is replaced at most once (FR-032).
- **SC-008**: Drawing a view whose generated ink is kept takes no longer than drawing it with the
  words set in the font, within 10 %.
- **SC-009**: No generated stroke is ever stored as a sample or contributed (host tests).

## Design boards

Needed in GlassineOS/ui `designs/handwriting` before implementation, each in portrait and
landscape: the font page with letters in your style (preview, the three sources in the characters
list, the note's count); a character in your style, not written yet; the style being worked out
and "write a few more prompts"; the generator row on the Autograph tab with its terms, installed
and not installed (the sentence of FR-005 and the way to install); the export page's sentence on
letters left out; the handwriting settings with "Typed words in my hand"; the writing page at
Neatest with Rewrite and "Show rewritten", rewritten words picked out and the count of rewritten
words; a field with a typed word in the person's hand beside their ink.
