# Feature Specification: Your own models

**Feature Branch**: `006-local-models`
**Status**: Draft
**Input**: A model the person trained on their own computer (a recognizer, or a point aligner)
used on the device next to the shipped one: where it lives, how it is checked before use, how
the person chooses between it and the shipped one, falling back when it can't be used, and how
it gets from the computer onto the device.

## Context

The device reads handwriting with the shipped base model of each language (spec 001), learns
a small head on top of it from the person's samples (spec 001 FR-009), and neatens and builds
the letter model with an optional point aligner beside it (spec 003). Some people train a
model on their own computer, from their own samples (spec 002) or with their own letter model
mixed into the aligner's training ink. The trainer's documentation already says that an
aligner trained with a person's letters is theirs and is never released. Until now such a
model could only be used by replacing the shipped files, which a system update undoes and
which every user of the device would share.

This spec lets the person bring such a model onto the device for themselves, next to the
shipped one, and choose which one is used. The shipped model stays as it is; the person's
model is checked before it is used and the shipped one takes over whenever it can't be.

Relation to earlier specs: every requirement of specs 001 to 005 stays. The one model in
memory for all apps (spec 004 FR-005, SC-002), the guests' readings (spec 002 FR-017), the
contribution rules (spec 001 FR-014, spec 002) and the rule that a reading never changes a
confirmed or edited word (spec 005 FR-026) apply to the person's own model as to the shipped
one.

## User scenarios

### Scenario 1 – An aligner trained on the computer (Priority: P1)

**Given** a person who trained an aligner on their computer with their own letters mixed
into its training ink, **When** they put the file into the folder the samples app keeps its
files in and tap Import under Learn › Models, **Then** the app checks the file, says it is an
aligner for English and ready, and from then on neatening and the letter model use it, while
the shipped recognizer goes on reading their writing.

### Scenario 2 – A file that doesn't fit (Priority: P1)

**Given** a file made for another engine version, for another language's letters, or cut
short while copying, **When** the person imports it, **Then** the app says in plain words
why it can't be used (made for a newer version, letters missing, the file is damaged), keeps
nothing of it, and the device reads exactly as before.

### Scenario 3 – Choosing (Priority: P1)

**Given** a person with their own recognizer for English, **When** they choose the shipped
model under Learn › Models, **Then** the next reading in every app uses the shipped model,
and choosing their own again brings theirs back; no app restarts and no app is told.

### Scenario 4 – Falling back (Priority: P1)

**Given** a person who chose their own recognizer, **When** its file is deleted, replaced by
a damaged copy, or turns out not to fit, **Then** the shipped model reads from the next
reading on, the app says that the shipped model is reading and why, and handwriting never
stops working.

### Scenario 5 – Stored ink read again (Priority: P2)

**Given** a notes app holding writing whose words the person partly confirmed, **When** the
person switches to their own model and the app hands the writing back to be read, **Then**
the confirmed and edited words stay exactly as they were and only the words that came from a
reading alone take the new model's reading.

### Scenario 6 – Placing the file by hand (Priority: P2)

**Given** a person who prefers a file manager or a terminal, **When** they copy the model
file under its kind's name into the language's folder of their own models, **Then** it is
checked and used like an imported one, as soon as their own model is chosen for its kind.

### Scenario 7 – A guest writes (Priority: P2)

**Given** an owner with their own recognizer, **When** a guest writes in the samples app,
**Then** the guest's readings are the shipped model's, exactly as without the owner's model.

### Edge cases

- **A model for a language the device doesn't have**: refused; the language's shipped files
  are needed beside it (its prompted texts, its character model).
- **Two languages**: an own model for English and none for German: English is read with the
  person's model, German with the shipped one.
- **The file changes while apps are reading**: the next reading after the change uses the
  new file once it has passed the checks; a reading already running finishes with the model
  it began with.
- **A recognizer imported as an aligner or the other way round**: refused as the wrong kind;
  an import takes the kind from the file itself, so it can't be put in the wrong place by
  the app.
- **A model the size of a hundred shipped ones**: refused before it is read into memory.
- **Numbers that are not numbers** (a training run that diverged): refused.
- **Learning off**: the person's own model still reads; nothing is learned on top of it.
- **A system update brings a newer shipped model**: the person's own model is still the one
  chosen where they chose it; the shipped model it falls back to is the newer one.

## Requirements

### Where a local model lives

- **FR-001**: A local model is a model file the person brings to the device for one language
  and one kind: a **recognizer** (reads the writing) or an **aligner** (decides which ink
  belongs to which letter, for neatening and the letter model). It lives in the person's own
  data on the device, readable only by them, beside their samples and never inside them.
- **FR-002**: A local model is never installed by or into a system package and never changes
  the shipped models. A system update leaves it as it is, and removing it leaves the shipped
  models as they are.
- **FR-003**: A local model is the person's alone: guest writers' stores (spec 002) never
  use it, so a guest's readings stay the shipped model's alone (spec 002 FR-017). Another
  account on the same device has its own local models or none.
- **FR-004**: There is at most one local model per language and kind; bringing another
  replaces it.

### Checked before use

- **FR-005**: Before a local model is used, and again whenever its file changes, the system
  checks it. A model that fails a check is never used, for anything.
- **FR-006**: The checks:
  - **Format**: a model file in a format and format version this system reads, whole (the
    file holds exactly what its header says), with the inputs that version stands for.
  - **Kind**: a recognizer where a recognizer is used and an aligner where an aligner is; a
    file whose kind can't be told from it is refused.
  - **Alphabet and language**: its characters are valid and each appears once; it is for a
    language whose shipped files are on the device; a recognizer holds every character of
    the language's prompted texts and the space between words, an aligner every letter of
    them.
  - **The engine's limits**: its sizes are within what the device's engine runs (a file size
    limit, checked before the file is read, and the engine's bounds on each layer); every
    number in it is finite; a reading of a fixed synthetic line with it gives a finite
    result.
- **FR-007**: A refusal names its reason, one per check above, in words the samples app
  shows as they are (made for another version, damaged, the wrong kind, letters missing,
  too big, not a number). A refused or broken model never crashes, hangs or stops reading:
  the shipped model reads (FR-011).

### Choosing

- **FR-008**: The person chooses, separately for the recognizer and for the aligner, the
  shipped model or their own, in the samples app's settings (Learn › Models). The choice
  holds for every language: their own where they have a usable one, the shipped one for the
  others. Their own aligner can go with the shipped recognizer and the other way round.
- **FR-009**: Only one recognizer per language is ever in memory for all apps (spec 004
  SC-002): choosing never runs two recognizers side by side.
- **FR-010**: The samples app shows, per kind, which model is in use, and for the person's
  own: none, ready or refused with the reason, and whether it was trained with the
  person's samples and up to when.

### Falling back

- **FR-011**: When the chosen local model is missing, unreadable or refused, the shipped
  model is used for that language and kind from the next reading, without restarting
  anything, and the samples app says that the shipped model is in use and why. When a
  usable local model is there again, it is used again from the next reading.
- **FR-012**: Without a shipped aligner, falling back from a refused local aligner is
  neatening and the letter model as they are without one.

### Apps and readings

- **FR-013**: No app is told about models: no event, capability or field of the service
  interface or the pen interface names the model in use or changes when it changes (spec
  004, spec 005 FR-026). A change of model or of choice takes effect at the next reading of
  every app.
- **FR-014**: Reading again never changes words the person confirmed or edited (spec 005
  FR-026): after a change of model only the words that came from a reading alone take the
  new reading. Samples already kept keep their text.

### Learning on top

- **FR-015**: What the device learns on top of a local recognizer (spec 001 FR-009) is kept
  apart from what it learned on top of the shipped one. Switching uses each one's own again;
  a head learned for one model is never used with another. A local recognizer that records
  the newest of the person's samples it was trained on has those samples left out of the
  device's learning, as for a shipped model.

### Never leaves the device

- **FR-016**: A local model leaves the device by no path of the system: not in a sample
  export, not in a contribution (spec 001 FR-014), not in a log. A contribution says nothing
  about which model was in use. Erasing samples doesn't remove a local model; it is removed
  on its own (FR-019).

### Copying a model onto the device

- **FR-017**: Import in the samples app: the person puts the file into the folder the app
  already uses for its own files and taps Import under Learn › Models. The app checks the
  newest model file there for the language it works in, takes its kind from the file,
  copies it into the person's local models (FR-001) when the checks pass, and chooses the
  person's own model for that kind. The original stays where it was. A refused file leaves
  everything as it was (FR-007).
- **FR-018**: Placing by hand: a model file copied under its kind's name into the
  language's folder of the person's local models is checked and used like an imported one
  (FR-005), once the person's own model is chosen for its kind.
- **FR-019**: The person removes a local model in the samples app; the shipped one is used
  from the next reading.
- **FR-020**: Checking, importing, choosing, removing and the state of the person's models
  are part of the recognizer's documented contract, so another settings app can offer the
  same.

## Key entities

- **Shipped model**: a language's recognizer and, when there is one, aligner, installed with
  the system (spec 001).
- **Local model**: the person's own file for one language and kind (FR-001).
- **Choice**: per kind, shipped or the person's own (FR-008).
- **Refusal**: the check a local model failed and why (FR-006, FR-007).

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Recognizer line contract and commands: checking, importing, choosing, removing local models, their state | owned | next minor version (additive) | this spec; docs/recognizer.md |
| Model file format | owned, unchanged | ghm 1 and 2 | docs/recognizer.md |
| Service interface and pen interface | owned by specs 004 and 005, unchanged: nothing about models | 1 | docs/service.md |
| The trainer's export | owned by this repository, consumed as the source of local models | — | train/README.md |

## Device scope and risk

Device-neutral. No device risk: nothing is flashed or installed into the system. User data:
local models are the person's data, stored and protected like their samples; a refused model
changes nothing.

## Acceptance

Host level:

- a test model that fails each check of FR-006 (another format version, the wrong inputs,
  a cut-off file, the wrong kind, a kind that can't be told, a missing letter, a repeated
  character, a language without shipped files, too big, a number that isn't one) is
  refused with its reason, at import and when placed by hand, and the shipped model reads;
- a usable local recognizer reads the same as the same file installed as the shipped model,
  and differently from the shipped one; choosing shipped reads as the shipped model again,
  without restarting the recognizer;
- the chosen local model removed or damaged: the next reading is the shipped model's;
- a local aligner is used for neatening while the shipped recognizer reads;
- a guest's reading is the shipped model's with an owner's local model chosen;
- a sample export and a contribution file are the same with a local model as without, and
  hold nothing of it;
- through the service: no message names a model, a switch takes effect at the next reading,
  and stored ink handed back after a switch keeps its confirmed and edited words;
- learning on top of a local recognizer is kept apart from the shipped model's;
- end to end with a small model written by the trainer's export path: placed in the
  folder, imported, used, removed.

Product level: none beyond the samples app's own (Learn › Models); the person's training on
a computer is outside the device.

## Out of scope

- Training on the device; the training itself (train/README.md).
- Sharing a local model with others, or publishing it; a model for everyone is a release
  (models/README.md).
- More than one local model per language and kind, or choosing per app.
- Local character models, word lists or prompted texts.
- Reading with two recognizers at once and combining them.

## Success criteria

- **SC-001**: A change of choice, a removed or a refused local model takes effect at the
  next reading of every app, without a restart of anything and without a message to any app.
- **SC-002**: A local model never makes handwriting stop: with any file in place of the
  person's model, readings go on (with the shipped model when it is refused).
- **SC-003**: Memory for the recognizer stays one model per language, whichever is chosen.
- **SC-004**: Checking a model of the shipped model's size takes under two seconds on the
  device.
