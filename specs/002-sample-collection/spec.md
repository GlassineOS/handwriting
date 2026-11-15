# Feature Specification: Handwriting samples app

**Feature Branch**: `002-sample-collection`
**Status**: Draft
**Input**: An app in which a person writes shown words and sentences with the pen, so the
recognizer (spec 001) learns their hand on the device, and so the project gets complete,
correctly labelled raw handwriting from which future base models are trained.

## Context

The recognizer learns from samples a person confirms (spec 001 FR-009). Samples from
normal use come slowly, are biased towards the words the person happens to write, and are
labelled only as well as the person's corrections. Prompted writing gives exact labels:
the text is known before the pen touches the screen, so every character's strokes are
known too (spans `exact` in the sample format).

The same samples serve two purposes with different rules:

- **Adaptation** of the person's own recognizer: samples stay on the device, in the
  person's data, and are used as spec 001 FR-009 describes.
- **Base models** for everyone: only samples the person donates (spec 001 FR-014), reviewed
  and licensed by them, ever reach a base model.

This app is a client of the recognizer. It owns the prompts, the writing session, the
review of each sample and the controls for adapting and donating; the recognizer owns the
samples, the adaptation and the donation file.

## User scenarios

### Scenario 1 – Writing shown text (Priority: P1)

**Given** the app is open, **When** it shows a word or sentence and the person writes it
on the writing area and then pauses or taps Next, **Then** the written sample is shown
next to the prompt together with what the recognizer read before it learned from it, the
sample is kept, and the next prompt appears. The ink appears under the pen as fast as on
the keyboard's writing paper.

### Scenario 2 – A bad sample is not kept (Priority: P1)

**Given** the person slipped, wrote something else, or was interrupted, **When** they tap
Redo, Skip or Discard on the shown sample (also for the previous sample, until the next
one is kept), **Then** that sample is not stored or is removed, and nothing from it is used
for adaptation or donation.

### Scenario 3 – Adapting on request (Priority: P1)

**Given** the person has written new samples, **When** they tap Adapt, **Then** the app
shows how many new samples will be used, runs the adaptation (while the device charges;
otherwise it says so and offers to run once it charges), and afterwards shows the error
rate on the person's held-back samples before and after; the new adaptation is used only
if it does better, and the person can go back to the previous one.

### Scenario 4 – Prompts that teach what is weak (Priority: P2)

**Given** samples already stored and the recognizer's errors on them, **When** the app
picks the next prompts, **Then** it favours characters, capitals, digits, punctuation and
letter pairs that have few samples or are read wrongly most often, and the person can
see coverage per character and choose a set instead: words, sentences, single
characters, digits and punctuation, or their own list of words.

### Scenario 5 – Raw handwriting for future models (Priority: P1)

**Given** a person who wants their prompted writing to help the base models, **When** they
turn on donating in the app and choose a licence once, **Then** every prompted sample they
keep from then on, already reviewed in Scenario 1, is also added to their donation, the
app shows the donation's size, and on request writes the donation file for them to send
themselves (spec 001 FR-014). Turning donating off or erasing the donation never touches
the samples used for their own adaptation.

### Scenario 6 – The person stays in charge (Priority: P1)

**Given** stored samples, **When** the person opens the app's sample view, **Then** they see
the samples from this app, can look at any sample and delete it, erase all of them, and
export them as one file in the sample format; nothing leaves the device otherwise.

## Requirements

- **FR-001**: The app is a client of the recognizer's session interface (spec 001 FR-001);
  it reads, stores, adapts and donates only through that interface and keeps no samples of
  its own.
- **FR-002**: Prompts come from texts shipped with the app per language, each under a
  licence that allows its use in donated training data (public domain or openly licensed),
  with their sources and licences listed next to them, plus the person's own word list,
  which is never shipped and never donated as a prompt set.
- **FR-003**: Prompt kinds: single words, short sentences, single characters (each letter
  in both cases, digits, punctuation), and pangram-like sentences that cover the language's
  alphabet. The first release ships English; other languages follow with no change to the
  app beyond their prompt texts.
- **FR-004**: Prompt choice (Scenario 4) weighs coverage (samples per character and per
  letter pair) and the recognizer's error on the person's samples; the same prompt is not
  shown again until the set has been worked through.
- **FR-005**: The writing area looks and feels like the keyboard's writing paper (shell
  spec 006): the same line height, guide lines and ink; the area's size, line spacing and
  the screen's orientation are recorded with each sample.
- **FR-006**: A sample keeps every point the pen reports between pen down and pen up, with
  its time, pressure and tilt where the pen has them, in the pen's own units with their
  scale and report rate (sample format `units`); no smoothing, resampling or cleaning in
  what is stored. Hover positions are not stored.
- **FR-007**: A kept sample is `prompted`, its text the prompt, its spans `exact` (taken
  from the order of writing against the prompt, checked by the recognizer's prompted
  alignment, spec 001 FR-007); a sample whose alignment fails the check is shown as such
  and kept only if the person confirms it.
- **FR-008**: Each sample also keeps the recognizer's reading at the time of writing
  (best candidate and alternatives, before any adaptation from it), so errors can be
  studied and future models tested against what this one got wrong.
- **FR-009**: Redo, Skip and Discard (Scenario 2) act before a sample is kept or on the
  last kept sample; deleting any sample later is in the sample view (Scenario 6).
- **FR-010**: Adapt (Scenario 3) asks the recognizer for an adaptation run; the run
  follows spec 001 FR-009's check (held-back samples, kept only if better, previous one
  kept to go back to). The app shows progress and the before and after error rates, and
  the device stays usable while it runs.
- **FR-011**: Donating (Scenario 5) is off until the person turns it on; the licence
  (CC0 or CC BY 4.0) is chosen once per donation; only samples written to shipped prompts
  while donating is on are added, never samples written to the person's own word list or
  from normal use, unless the person adds such a sample one by one (spec 001 FR-014). The
  donation file follows spec 001 FR-014 (no client, wall-clock time, device or person in
  it).
- **FR-012**: Sessions are short and resumable: the app keeps its place in the prompt set,
  shows counts (today, total, since the last adaptation), and asks nothing before the
  first prompt.
- **FR-013**: No device names in the app; the pen's properties come with its stroke data.

## Key entities

- **Prompt**: text to write, its kind, language, and source and licence.
- **Prompted sample**: a sample (spec 001) with exact spans, the writing area's geometry and
  orientation, and the recognizer's reading at the time of writing.
- **Coverage**: samples and error rate per character and letter pair, from the person's
  samples.
- **Donation**: as spec 001.

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Recognizer session interface: prompted alignment, samples, adaptation run on request with its result, donation | consumer (spec 001) | 1 | spec 001 FR-001, FR-007, FR-009–FR-011, FR-014 |
| Sample format: the writing area's geometry and orientation, the reading at the time of writing | consumer, adds optional fields (`docs/samples.md`) | 1 | FR-005, FR-008 |
| Writing paper look (line height, guides, ink) | consumer (`GlassineOS/shell` spec 006) | — | FR-005 |
| Pen ink areas (fast ink under the pen) | consumer (the e-ink session's ink protocol) | — | Scenario 1 |

## Success criteria

- A person writes 100 prompted words in under ten minutes, including review.
- Every kept prompted sample has exact spans and its raw points as the pen reported them;
  a round trip through export and import gives the same samples.
- After an adaptation from a few hundred prompted samples, the error rate on the person's
  held-back samples is lower than before, and is shown in the app.
- A donation file holds only samples written to shipped prompts while donating was on,
  plus samples added one by one, each with the chosen licence, and nothing that names the
  person, device, client or time.

## Device scope and risk

Device-neutral; it needs a pen that reports positions over time.

- **Failure modes:** a mislabelled sample (the person wrote something else) slips through
  (review in Scenario 1, the alignment check in FR-007, deletion later); an adaptation run
  makes recognition worse (spec 001 FR-009's check); a run slows the device (FR-010).
- **What it never touches:** the network, partitions, the boot chain, system files.
- **Escape route:** Discard or delete samples, go back to the previous adaptation, turn
  learning or donating off.

## Acceptance

Host level: prompt choice by coverage and error; a kept sample has exact spans and
unaltered points; Redo, Skip, Discard and delete store nothing or remove the sample; the
donation rules (only shipped prompts while on, one-by-one additions, no identifying
fields); export and import round trip.

Hardware: supervised: a writing session of words and sentences in English, an adaptation
on request with the before and after rates, a donation file inspected on the host.

## Out of scope

- Training base models (the project's training pipeline reads donation files).
- Sending a donation anywhere (the person does that outside the app).
- Fonts from the person's handwriting (spec 001 Scenario 4's app).
