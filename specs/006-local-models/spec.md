# Feature Specification: A model you install yourself

**Feature Branch**: `006-local-models`
**Status**: Draft
**Input**: A private replacement for the shipped recognizer or aligner, which the person
obtains elsewhere and installs on their device themselves: where it lives, how it is checked
before use, how the person chooses between it and the shipped model, falling back when it
can't be used, and installing it from the samples app.

## Context

The device reads handwriting with the shipped base model of each language (spec 001) and
neatens and builds the letter model with an optional point aligner beside it (spec 003). The
shipped models are trained only on data the project may redistribute (the repository's
principles, Models and data).

Some handwriting data is published for research under terms that don't allow a model trained
on it to be shipped with the system. A model trained on such data can read some people better
than the shipped one. If the project never gets permission to ship such a model, a person who
has one from elsewhere, under the data's terms, can still use it on their own device.

This spec lets the person install a model file they already have as a **private
replacement** for a shipped model, for themselves only. The system never fetches such a model
and never passes it on. The shipped model stays installed. The person's model is checked before
it is used, and the shipped model takes over whenever the person's model can't be used.
Tailoring to the person's own handwriting is a separate matter: it happens on the device by
other means (spec 001 FR-009, spec 003) and is not part of this spec.

Relation to earlier specs: every requirement of specs 001 to 005 stays. These apply to the
person's model as they apply to the shipped one:

- the one model in memory for all apps (spec 004 FR-005, SC-002);
- the guests' readings (spec 002 FR-017);
- the contribution rules (spec 001 FR-014, spec 002);
- the rule that a reading never changes a confirmed or edited word (spec 005 FR-026).

## User scenarios

### Scenario 1 – Installing a replacement recognizer (Priority: P1)

**Given** a person who obtained a recognizer file for English elsewhere, **When** they put it
into the folder the samples app keeps its files in and tap Install under Learn › Models,
**Then** the app checks the file, says it is a recognizer for English and ready, and from the
next reading on every app reads their writing with it.

### Scenario 2 – A file that doesn't fit (Priority: P1)

**Given** a file made for a newer version of the engine, made for another language's letters,
or cut short while copying, **When** the person installs it, **Then** the app says in plain
words why it can't be used (made for a newer version, letters missing, the file is damaged).
Nothing of the file is kept, and the device reads exactly as before.

### Scenario 3 – Choosing (Priority: P1)

**Given** a person with an installed recognizer for English, **When** they choose the shipped
model under Learn › Models, **Then** the next reading in every app uses the shipped model.
Choosing the installed model again brings it back. No app restarts and no app is told.

### Scenario 4 – Falling back (Priority: P1)

**Given** a person who chose their installed recognizer, **When** its file is deleted, is
replaced by a damaged copy, or turns out not to fit, **Then** the shipped model reads from the
next reading on, the app says that the shipped model is reading and why, and handwriting never
stops working.

### Scenario 5 – Replacing the installed file (Priority: P1)

**Given** apps reading with an installed model, **When** the person installs a newer file of
the same kind and language, **Then** the next reading uses the new file. No reading after that
gives a result of the old file.

### Scenario 6 – Stored ink read again (Priority: P2)

**Given** a notes app holding writing whose words the person partly confirmed, **When** the
person switches models and the app hands the writing back to be read, **Then** the confirmed
and edited words stay exactly as they were. Only the words that came from a reading alone take
the new model's reading.

### Scenario 7 – Placing the file by hand (Priority: P2)

**Given** a person who prefers a file manager or a terminal, **When** they copy the model file,
under its kind's name, into the language's folder of their installed models, **Then** it is
checked and used like one installed in the app, as soon as the installed model is chosen for
its kind.

### Scenario 8 – A guest writes (Priority: P2)

**Given** an owner with an installed recognizer, **When** a guest writes in the samples app,
**Then** the guest's readings are the shipped model's, exactly as without the owner's model.

### Edge cases

- **A model for a language the device doesn't have**: refused. The language's shipped files
  (its prompted texts, its character model) are needed beside it.
- **Two languages**: with an installed model for English and none for German, English is read
  with the installed model and German with the shipped one.
- **A recognizer installed as an aligner, or the other way round**: refused as the wrong kind.
  The kind is taken from the file itself, so it can't end up in the wrong place.
- **A file from before the kind was recorded**: its kind is told from its shape. When the shape
  doesn't tell, the file is refused.
- **A model the size of a hundred shipped ones**: refused before it is read into memory.
- **Numbers that are not numbers** (a training run that diverged): refused.
- **A system update brings a newer shipped model**: the installed model stays chosen where the
  person chose it. The shipped model it falls back to is then the newer one.
- **Learning on top**: what the device learns from the person's samples on top of an installed
  recognizer (spec 001 FR-009) is kept apart from what it learned on top of the shipped one.

## Requirements

### What it is, and where it lives

- **FR-001**: An installed model is a model file the person obtained elsewhere and installs on
  their device themselves, for one language and one kind:
  - a **recognizer**, which reads the writing;
  - an **aligner**, which decides which ink belongs to which letter, for neatening and the
    letter model.

  It replaces the shipped model of that language and kind for this person while it is chosen.
- **FR-002**: An installed model lives in the person's own data on the device, beside their
  samples and never inside them, and only that person can read it. It is never installed by or
  into a system package and never changes the shipped models. A system update leaves it as it
  is, and removing it leaves the shipped models as they are.
- **FR-003**: An installed model is the person's alone. Guest writers' stores (spec 002) never
  use it, so a guest's readings stay the shipped model's alone (spec 002 FR-017). Another
  account on the same device has its own installed models or none.
- **FR-004**: There is at most one installed model per language and kind. Installing another
  replaces it.

### Obtained elsewhere, never passed on

- **FR-005**: The system never downloads, fetches or offers a model to install. It installs
  only a file the person already has on the device. The documentation on installing a model
  tells the person to obtain the model and its training data only from their official source
  and under their terms.
- **FR-006**: An installed model leaves the device by no path of the system: not in a sample
  export, not in a contribution (spec 001 FR-014), not in a backup the system makes of the
  samples, not in a log. A contribution says nothing about which model was in use. Erasing
  samples doesn't remove an installed model; it is removed on its own (FR-023).

### Checked before use

- **FR-007**: Before an installed model is used, and again whenever its file changes, the
  system checks it. A model that fails a check is never used, for anything.
- **FR-008**: The checks:
  - **Format**: a model file in a format and format version this system reads, and whole (the
    file holds exactly what its header says), with the inputs that version stands for.
  - **Kind**: a recognizer where a recognizer is used and an aligner where an aligner is. The
    kind recorded in the file decides. A file without a recorded kind is told by its shape, and
    a file whose kind can't be told is refused.
  - **Alphabet and language**: its characters are valid and each appears once. It is for a
    language whose shipped files are on the device. A recognizer holds every character of the
    language's prompted texts and the space between words; an aligner holds every letter of
    them.
  - **The engine's limits**: its sizes are within what the device's engine runs. That means a
    file size limit, checked before the file is read, and the engine's bounds on each layer.
    Every number in it is finite, and a reading of a fixed synthetic line with it gives a
    finite result.
- **FR-009**: A refusal names its reason, one per check above, in words the samples app shows
  as they are (made for another version, damaged, the wrong kind, letters missing, too big, not
  a number). A refused or broken model never crashes, hangs or stops reading: the shipped model
  reads (FR-013).

### Choosing

- **FR-010**: The person chooses, separately for the recognizer and for the aligner, between
  the shipped model and their installed one, in the samples app's settings (Learn › Models).
  - The choice holds for every language: the installed model where a usable one is installed,
    and the shipped model for the others.
  - The choices for the two kinds are independent: an installed aligner can go with the
    shipped recognizer, and the other way round.
- **FR-011**: Only one recognizer per language is ever in memory for all apps (spec 004
  SC-002). No choice runs two recognizers side by side.
- **FR-012**: The samples app shows, per kind, which model is in use. For the installed model
  it shows its state: none, ready, or refused with the reason.

### Falling back and replacing

- **FR-013**: When the chosen installed model is missing, unreadable or refused, the shipped
  model is used for that language and kind from the next reading, without restarting anything.
  The samples app says that the shipped model is in use and why. When a usable installed model
  is there again, it is used again from the next reading.
- **FR-014**: When no aligner is shipped, falling back from a refused installed aligner means
  neatening and building the letter model without an aligner, as they work without one today.
- **FR-015**: A file replaced in place, under the same name, is picked up at the next reading.
  No reading after the replacement is answered from the old file, even for strokes read before
  it.

### Apps and readings

- **FR-016**: No app is told about models. No event, capability or field of the service
  interface or the pen interface names the model in use, or changes when it changes (spec 004,
  spec 005 FR-026). A change of model or of choice takes effect at the next reading of every
  app.
- **FR-017**: Reading again never changes words the person confirmed or edited (spec 005
  FR-026). After a change of model, only the words that came from a reading alone take the new
  reading. Samples already kept keep their text.

### Learning on top

- **FR-018**: What the device learns on top of an installed recognizer (spec 001 FR-009) is
  kept apart from what it learned on top of the shipped one. Switching uses each one's own
  again; a head learned for one model is never used with another.

### The file format

- **FR-019**: A model file records its kind. Files written before the kind was recorded still
  read as they did (FR-008, Kind). A shipped model file with a recorded kind reads on an engine
  from before this spec exactly as before.

### Installing and removing

- **FR-020**: Installing in the samples app works in three steps:
  1. The person puts the file into the folder the app already uses for its own files.
  2. The person taps Install under Learn › Models.
  3. The app checks the newest model file in that folder for the language it works in, takes
     its kind from the file, and, when the checks pass, copies it into the person's installed
     models (FR-002) and chooses it for that kind.

  The original file stays where it was. A refused file leaves everything as it was (FR-009).
- **FR-021**: The samples app says, where it offers Install, that the person installs a file
  they obtained themselves and must keep to its terms, and that the file stays on the device.
- **FR-022**: Placing by hand: a model file copied under its kind's name into the language's
  folder of the person's installed models is checked and used like one installed in the app
  (FR-007), once the installed model is chosen for its kind.
- **FR-023**: The person removes an installed model in the samples app. The shipped model is
  used from the next reading.
- **FR-024**: Checking, installing, choosing and removing installed models, and reporting their
  state, are part of the recognizer's documented contract, so another settings app can offer
  the same.

## Key entities

- **Shipped model**: a language's recognizer and, when there is one, its aligner, installed
  with the system (spec 001).
- **Installed model**: the person's own replacement file for one language and kind (FR-001).
- **Choice**: per kind, shipped or installed (FR-010).
- **Refusal**: the check an installed model failed, and why (FR-008, FR-009).

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Recognizer line contract and commands: checking, installing, choosing and removing installed models, and their state | owned | next minor version (additive) | this spec; docs/recognizer.md |
| Model file format: the kind recorded | owned | ghm 1 and 2, additive (a reserved header word) | docs/recognizer.md |
| Service interface and pen interface | owned by specs 004 and 005, unchanged: nothing about models | 1 | docs/service.md |

## Device scope and risk

Device-neutral. No device risk: nothing is flashed or installed into the system. Installed
models count as the person's data, stored and protected like their samples. A refused model
changes nothing.

## Acceptance

Host level:

- Each check of FR-008 has a test model that fails it, and each is refused with its reason,
  both when installed in the app and when placed by hand; the shipped model reads. The test
  models cover:
  - another format version;
  - the wrong inputs;
  - a cut-off file;
  - the wrong kind recorded;
  - a kind that can't be told;
  - a missing letter;
  - a repeated character;
  - a language without shipped files;
  - a file that is too big;
  - a number that isn't one.
- A usable installed recognizer reads the same as the same file installed as the shipped model,
  and differently from the shipped one. Choosing shipped again reads as the shipped model,
  without restarting the recognizer.
- When the chosen installed model is removed or damaged, the next reading is the shipped
  model's. When it is replaced in place, the next reading of the same strokes is the new file's.
- An installed aligner is used for neatening while the shipped recognizer reads.
- With an owner's installed model chosen, a guest's reading is the shipped model's.
- A sample export and a contribution file are the same with an installed model as without, and
  hold nothing of it.
- Through the service: no message names a model, a switch takes effect at the next reading, and
  stored ink handed back after a switch keeps its confirmed and edited words.
- What is learned on top of an installed recognizer is kept apart from what is learned on top
  of the shipped model.
- A file that records its kind reads on the engine as it was before this spec. A file without
  a recorded kind reads by its shape.
- End to end, with a small model written by the trainer's export path: put in the folder,
  installed, used, and removed.

Product level: none beyond the samples app's own Learn › Models page.

## Out of scope

- Downloading, listing or recommending models, and any catalogue of them.
- Sharing or publishing an installed model. A model for everyone is a release
  (models/README.md).
- Tailoring to the person's handwriting (spec 001 FR-009, spec 003).
- More than one installed model per language and kind, and choosing per app.
- Installed character models, word lists or prompted texts.
- Reading with two recognizers at once and combining them.

## Success criteria

- **SC-001**: A change of choice, or a removed, replaced or refused installed model, takes
  effect at the next reading of every app, without restarting anything and without a message
  to any app.
- **SC-002**: An installed model never makes handwriting stop. Whatever file is in the place of
  the person's model, readings go on, with the shipped model when that file is refused.
- **SC-003**: Memory for the recognizer stays one model per language, whichever is chosen.
- **SC-004**: Checking a model of the shipped model's size takes under two seconds on the
  device.
