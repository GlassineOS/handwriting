# Feature Specification: Handwriting samples app

**Feature Branch**: `002-sample-collection`
**Status**: Draft
**Input**: An app in which a person writes shown words and sentences with the pen, so the
recognizer (spec 001) learns their hand on the device, and so the project gets complete,
correctly labelled raw handwriting from which future base models are trained, also from
other writers the person lets write on their device, kept apart from the person's own.

## Context

The recognizer learns from samples a person confirms (spec 001 FR-009). Samples from
normal use come slowly, are biased towards the words the person happens to write, and are
labelled only as well as the person's corrections. Prompted writing gives exact labels:
the text is known before the pen touches the screen, so every character's strokes are
known too (spans `exact` in the sample format).

The same samples serve two purposes with different rules:

- **Adaptation** of the person's own recognizer: samples stay on the device, in the
  person's data, and are used as spec 001 FR-009 describes.
- **Base models** for everyone: only samples the person contributes (spec 001 FR-014), reviewed
  and licensed by them, ever reach a base model.

Base models need many writers, and the people a person can ask to write (family, friends,
colleagues) rarely have a device of their own for it. So someone else can write in the app
as a guest writer: on the device owner's account (the shell has no second account), under a
name the owner gives them, with samples, a contribution and a contributor number of their
own, and with their own say over contributing. In this spec the **owner** is the person whose
account it is, and their samples are the ones the rest of this spec describes.

This app is a client of the recognizer. It owns the prompts, the writing session, the
review of each sample and the controls for adapting and contributing; the recognizer owns the
samples, the adaptation and the contribution file.

## User scenarios

### Scenario 1 – Writing shown text (Priority: P1)

**Given** the app is open, **When** it shows a word or sentence and the person writes it
on the writing area and then pauses or taps Next, **Then** the next prompt and an empty
writing area appear at once, the written sample is shown next to the prompt together with
what the recognizer read before it learned from it as soon as the reading is done, and the
sample is kept. The ink appears under the pen as fast as on the keyboard's writing paper.

### Scenario 2 – A bad sample is not kept (Priority: P1)

**Given** the person slipped, wrote something else, or was interrupted, **When** they tap
Redo, Skip or Discard on the shown sample (also for the previous sample, until the next
one is kept), **Then** that sample is not stored or is removed, and nothing from it is used
for adaptation or contribution.

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
see coverage per character (Coverage, beneath Learn) and choose a set instead: words,
sentences, single characters, digits and punctuation, or their own list of words.

### Scenario 5 – Raw handwriting for future models (Priority: P1)

**Given** a person who wants their prompted writing to help the base models, **When** they
turn on contributing in the app and choose a licence once, **Then** every prompted sample they
keep from then on, already reviewed in Scenario 1, is also added to their contribution, the
app shows the contribution's size, and on request writes the contribution, with its licence, as
one file into a folder among the person's own files, for them to send themselves (spec 001
FR-014). Turning contributing off or erasing the contribution never touches
the samples used for their own adaptation.

**Given** the person already kept prompted samples before turning contributing on, **When**
they turn it on, **Then** the app offers to add those earlier samples too, shows how many
there are, and lets the person review the list and leave any out before they are added;
declining adds none of them, and the offer can be taken up later from the Contribute page.

### Scenario 7 – Someone else writes (Priority: P1)

**Given** the owner's session is open, **When** the owner chooses "Someone else writes" and
names a new writer or picks one from the list of writers, **Then** the app shows that
writer's session, with their name in the header, their own prompts, samples and
contribution, and nothing of the owner's. A new writer first gets the short introduction:
one prompt to try, what happens to their samples, and the question whether to contribute,
which the writer answers and licenses themselves.

### Scenario 8 – Coming back another day (Priority: P1)

**Given** a writer kept from an earlier session, **When** the owner picks them again,
**Then** their session continues where it stopped: the next prompt of their set, their
samples, their contribution with its licence, and the same contributor number as before.
The introduction is not shown again.

### Scenario 9 – Finish (Priority: P1)

**Given** a guest writer's session, **When** the writer taps Finish, **Then** their
contribution, if they contribute and it holds samples not yet saved, is saved as a file, and
the app is back in the owner's session in that one step. A note there says what was saved
and that the writer is kept for next time, with Remove beside it.

### Scenario 10 – Removing a writer (Priority: P2)

**Given** the list of writers in the owner's session, **When** the owner removes a writer,
**Then** a question first says what goes for good (the writer's samples, any contribution
not saved as a file, their contributor number, their place in the prompts) and that the
contribution files already saved are not affected; after Remove, all of it is gone and the
name leaves the list.

### Scenario 6 – The person stays in charge (Priority: P1)

**Given** stored samples, **When** the person opens the app's sample view (Samples, beneath
Learn), **Then** they see
the samples from this app, can look at any sample and delete it, erase all of them, and
export them as one file in the sample format; nothing leaves the device otherwise.

## Requirements

- **FR-001**: The app is a client of the recognizer's session interface (spec 001 FR-001);
  it reads, stores, adapts and contributes only through that interface and keeps no samples of
  its own.
- **FR-002**: Prompts come from texts shipped with the app per language, each under a
  licence that allows its use in contributed training data (public domain or openly licensed),
  with their sources and licences listed next to them, plus the person's own word list,
  which is never shipped and never contributed as a prompt set.
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
  and kept only if the person confirms it, with its spans `inferred`.
- **FR-008**: Each sample also keeps the recognizer's reading at the time of writing
  (best candidate and alternatives, before any adaptation from it), so errors can be
  studied and future models tested against what this one got wrong.
- **FR-009**: Redo, Skip and Discard (Scenario 2) act before a sample is kept or on the
  last kept sample; deleting any sample later is in the sample view (Scenario 6), and in a
  character of the font page, which also writes a kept sample again (spec 003 FR-033). A
  deletion is the same wherever it is asked: the whole sample goes, with every character it
  writes, from the letter model and the font at their next build and from adaptation.
- **FR-010**: Adapt (Scenario 3) asks the recognizer for an adaptation run; the run
  follows spec 001 FR-009's check (held-back samples, kept only if better, previous one
  kept to go back to). The app shows progress and the before and after error rates, and
  the device stays usable while it runs.
- **FR-011**: Contributing (Scenario 5) is off until the person turns it on; the licence
  (CC0 or CC BY 4.0) is chosen once per contribution; samples written to shipped prompts are
  added while contributing is on, and earlier ones only when the person accepts the offer to
  add them after reviewing the list (Scenario 5), under the licence of this contribution;
  samples written to the person's own word list or from normal use are never added in
  bulk, only when the person adds such a sample one by one (spec 001 FR-014). The
  contribution file follows spec 001 FR-014 (no client, wall-clock time, device or person in
  it), states its licence, and is written into a folder among the person's own files; how
  it reaches the project is up to the person.
- **FR-012**: Sessions are short and resumable: the app keeps its place in the prompt set,
  shows counts (today, total, since the last adaptation), and asks nothing before the
  first prompt except once, on the first start: a short introduction that lets the person
  try one prompt (kept as an ordinary prompted sample) and asks whether to contribute
  (Scenario 5), where declining is as easy as accepting. A guest writer gets their own
  introduction once, on their first session (FR-019).
- **FR-013**: No device names in the app; the pen's properties come with its stroke data.
- **FR-026**: The app's pages. The owner's session has four tabs, in this order:
  - **Write**: the prompt, the writing area and the choice of prompt set (Scenarios 1, 2
    and 4). It teaches the recognizer only.
  - **Your font**: the font, the careful samples and picks that teach it, and Neaten my
    writing (spec 003 FR-042).
  - **Learn**: the adaptation, its progress and the version before (Scenario 3), with two
    pages beneath it: **Samples**, the sample view (Scenario 6), and **Coverage**, the
    samples per character the recognizer has seen (Scenario 4). Each opens from Learn and
    leads back to it, and the Learn tab stays marked as the current one on both. The
    font's own counts per character are on the font page (spec 003 FR-030), not in Coverage.
  - **Contribute** (Scenario 5).

  A guest's session has Write and Contribute only (FR-018). A way onward leads to the tab
  whose work it is: Coverage's "Practise these" goes to Write with the set that favours
  what is weak, and Learn's "Write more" to Write; the last step of the introduction
  (FR-012) and its Skip, the way back from the list of writers, and the note after Finish
  go to Write; the ways back from the parts of Your font go to Your font.

### Other writers

- **FR-014**: Writers: the app opens in the owner's session. Beside it, the app keeps a list
  of guest writers that only the owner creates, renames and removes, from the owner's
  session; a guest writer exists only in this app, as a name, with no account. "Someone
  else writes" shows the list (each name with its number of samples) and a way to add a
  new writer. A name is a short text (1 to 40 characters), unique in the list, entered in a
  field nothing learns from: the keyboard keeps no sample, learned word or prediction from it
  (a private field, spec 001 FR-009, shell spec 006).
- **FR-015**: One writer at a time: the app has one window and shows one writer's session at
  a time; while a guest session is open, its header names the writer whose writing is being
  collected, on every page. Opening the app again while it runs shows the window as it is;
  after the app was closed, it opens in the owner's session. The pen's ink, the prompts and
  every request in a session belong to the writer shown.
- **FR-016**: Separate stores: each guest writer has a store of their own, separate from the
  owner's store and from every other writer's, not inside any of them, and kept by the
  recognizer like the owner's (spec 001 FR-009, FR-021, spec 004 FR-005). The app names the
  store explicitly for every request and every recognizer process it starts, so no request
  can fall to another store by default. Nothing a guest writes is kept in, learned by or read from anything of the
  owner's: not their samples, adaptation, size reference (spec 001 FR-017), letter model,
  font, neatening, learned words or keyboard. A sample is kept in the store of the writer
  in whose session it was written, also when it is kept after the session was left, or not
  at all; a sample of a removed writer is not kept.
- **FR-017**: Readings in a guest session: the base model alone, with that writer's own size
  reference from their samples, the language's words and character model, never the owner's
  adaptation, learned words or letters; no owner's data shows in a guest session (samples,
  counts, coverage, readings, contribution, contributor number, saved files, own word list).
  The owner's learning switch (spec 001 FR-010) is about the owner's store; a guest session
  keeps the prompted samples the writer keeps, since they exist only for that writer's review
  and contribution.
- **FR-018**: What a guest session offers: two tabs, Write and Contribute (FR-026). Write
  has the shipped prompt sets, including "Write it your best" and "Which one is nicer?"
  (spec 003 Scenarios 1 and 2), whose marks and preferences travel with a contribution, and
  prompt choice (FR-004) by the writer's own coverage and the base model's errors on their
  samples; Contribute (Scenario 5) has their own licence, contributor number and
  earlier-samples offer, of their own samples only. Counts are today's and the total.
  [NEEDS CLARIFICATION: with no Samples page in a guest's session, where does a writer look
  at, delete, erase all and export their own samples? FR-024's row for Erase all in a guest's
  session, the acceptance check of a guest's export and the last item of Out of scope rely on
  it.] Not offered: the Your font and Learn tabs, so neither Samples, Coverage, Adapt and the
  learning progress, nor the font and Neaten my writing; and no own word list. A font is
  installed for the whole account and neatening works on the account's pen ink, so either
  would put the guest's hand into the owner's apps; an adaptation or a letter model of a
  guest's samples would serve no one on this device and cost power; the own word list is a
  file among the owner's files. The recognizer refuses
  an adaptation, a letter model, a font and neatening for a guest store, and never adapts
  one in the background.
- **FR-019**: A guest's introduction: the first time a writer's session opens, the app shows
  the introduction of FR-012, addressed to that writer by name: one prompt to try (kept as
  their sample), what happens to their samples (kept on this device in a store of their own,
  apart from the owner's; not used to read anyone's writing here), said plainly: the
  device's owner can open this writer's session and their samples without them, and remove
  them. Then the question whether to contribute, which the writer is asked to answer
  themselves, declining as easy as accepting. Before the licence (Scenario 5) the writer
  confirms being 18 or older; the confirmation is asked once, at that step, and stored
  nowhere, not in the store, a sample or a contribution. A writer who doesn't confirm still
  writes, with contributing off; their Contribute page offers the same question and
  confirmation again. The introduction promises nothing a guest store doesn't do: no
  learning step, no "it reads you better".
  Contributing for a writer is turned on and off only in that writer's session, never from
  the owner's session or the list of writers.
- **FR-020**: Contributor number per writer: each writer has one, under the same rules as the
  owner's (spec 001 FR-014): random, made when the writer first starts a contribution, kept
  in their store, the same in every contribution and every resumed session, not derived
  from the name, the device, the account, the owner's number or anything else, shown on
  their Contribute page and replaced only at their request. No two writers share one; a
  writer removed and added again is a new writer with a new number.
- **FR-021**: The name stays on the device: it is a label in the list of writers and in the
  header, nothing more. It is not in the writer's store, in a sample, a contribution file,
  an export, the names of those files, or a log, and it is never passed to the recognizer;
  the store is found by a random identifier that is neither the name nor the contributor
  number. A contribution carries the contributor number alone, as the owner's does. Every
  writer's contribution files go into the same folder as the owner's (FR-011), named as
  the owner's are; right after saving, the app says which file was saved for whom.
- **FR-022**: Finish (Scenario 9) is the one step from a guest session back to the owner's.
  When the writer contributes and their contribution holds samples not yet saved, Finish
  first saves it as a file (the writer's next contribution then starts empty, with the same
  licence and contributor number); nothing else is asked, no password either. The owner's session then shows
  what was saved and that the writer is kept, with Remove beside it. A guest session has no
  other way out of it and no way into another writer's session.
- **FR-023**: Removing a writer (Scenario 10) is offered only in the owner's session, after a
  question that names what goes (their samples, a contribution not yet saved with its number
  of samples, their contributor number, their place in the prompts), offers to save that
  contribution first, and says that contribution files already saved stay as they are. Once
  removed, the writer's store is gone from the device when the request returns, a crash on
  the way leaving it whole or gone (spec 001 FR-016), and the name leaves the list.
- **FR-024**: What each erase touches; nothing else is touched:

  | Action | Touches | Leaves |
  |---|---|---|
  | Erase all, Samples page, owner's session | the owner's samples, adaptation, letter model and font (spec 001, spec 003) | every guest store; saved contribution files; the owner's contribution and number |
  | Erase all, Samples page, a guest's session | that writer's samples, their preferences and marks | the writer, their contribution and number; every other store |
  | Erase contribution, in a session | that session's writer's contribution not yet saved | their samples and contributor number; every other store; saved files |
  | Get a new contributor number, in a session | that writer's number, from now on | everything else; saved files keep the old one |
  | Remove a writer, owner's session | that writer's whole store and their name in the list | the owner's store; every other guest store; saved files |
  | Erase everything, counts, export and import in the settings app (spec 001 FR-010) | the owner's store only | every guest store, of which the settings app says they are kept in this app |

  The owner's Erase all says that other writers' samples are kept.
- **FR-025**: Privacy: a guest store is the owner's store's equal on the device (readable only
  by the account: directories 0700, files 0600), reachable only through this app: the
  keyboard, the settings app and other apps (spec 004) never reach it. It holds only what the
  owner's store holds for the same features, and the list of writers only names and random
  identifiers: no age, contact, photo or other detail of a writer. Nothing of a writer is
  ever contributed without that writer's own yes in their session (FR-019). The separation is
  between writers within this app, not a lock against the owner: whoever holds the device
  can open the owner's session, and the owner can open any writer's session; the guest's
  introduction says so.

## Key entities

- **Prompt**: text to write, its kind, language, and source and licence.
- **Prompted sample**: a sample (spec 001) with exact spans, the writing area's geometry and
  orientation, and the recognizer's reading at the time of writing.
- **Coverage**: samples and error rate per character and letter pair, from the person's
  samples.
- **Contribution**: as spec 001.
- **Owner**: the person whose account it is; the app's default session.
- **Guest writer**: someone else who writes in the app, known to it by a name the owner
  gives and a random identifier; with a store, a place in the prompts and, once they
  contribute, a contributor number of their own.
- **Writer's store**: a guest writer's samples and contribution, laid out as the owner's
  store, kept apart from it and from every other writer's.

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Recognizer session interface: prompted alignment, samples, adaptation run on request with its result, contribution | consumer (spec 001) | 1 | spec 001 FR-001, FR-007, FR-009–FR-011, FR-014 |
| Sample format: the writing area's geometry and orientation, the reading at the time of writing | consumer, adds optional fields (`docs/samples.md`) | 1 | FR-005, FR-008 |
| Writing paper look (line height, guides, ink) | consumer (`GlassineOS/shell` spec 006) | — | FR-005 |
| Pen ink areas (fast ink under the pen) | consumer (the e-ink session's ink protocol) | — | Scenario 1 |
| Writer stores in the recognizer: a store named explicitly with every request; creating and removing a writer's store; for a guest store no adaptation, letter model, font or neatening and no reading with the owner's learned words or adaptation | consumer (spec 001 FR-021, `docs/recognizer.md`) | 1.3 | FR-016–FR-018, FR-023 |
| Store layout per writer: the same as the owner's store (`docs/recognizer.md`), found by a random identifier | consumer (spec 001) | unchanged | FR-016, FR-021 |
| Sample format: unchanged; a guest's contribution carries their contributor number as the owner's does | consumer (`docs/samples.md`) | 1 | FR-020, FR-021 |
| List of writers (names and random identifiers, on the device only, never exported or contributed) | owned (this app) | 1 | FR-014, FR-021 |
| Service interface: guest stores reachable only by this app | consumer (spec 004) | 1 | FR-025 |
| Settings app: counts and erase cover the owner's store and say other writers' samples are kept here | consumer (the shell's settings app) | — | FR-024 |
| A private name field (no sample, learned word or prediction from it) | consumer (`GlassineOS/shell` spec 006) | — | FR-014 |

## Success criteria

- A person writes 100 prompted words in under ten minutes, including review.
- With 10,000 samples kept, on the reference device: the next prompt shows within 150 ms
  of the last one being taken, what became of a written sample shows within 100 ms of
  the recognizer's reading, and every page shows within 300 ms of being opened, with its
  figures up to date; a page is drawn again only when something on it changed.
- Every kept prompted sample that passed the alignment check has exact spans, every kept
  sample its raw points as the pen reported them;
  a round trip through export and import gives the same samples.
- After an adaptation from a few hundred prompted samples, the error rate on the person's
  held-back samples is lower than before, and is shown in the app.
- A contribution file holds only samples written to shipped prompts while contributing was on,
  plus samples added one by one, each with the chosen licence, and nothing that names the
  person, device, client or time.
- Picking a writer shows their Write page with their next prompt, and Finish shows the
  owner's session, each within 300 ms on the reference device (beyond writing a
  contribution file), with 10,000 samples in each store.
- After any guest session, the owner's store is as it was, byte for byte; the writer's name
  is in no file and no file name but the list of writers; every contribution file of one
  writer, across all their sessions, carries the same contributor number, different from
  every other writer's.

## Device scope and risk

Device-neutral; it needs a pen that reports positions over time.

- **Failure modes:** a guest's sample reaching the owner's store, or the reverse (FR-016: the
  store is named with every request, and a sample belongs to the session it was written in);
  a writer's name leaking into a contribution (FR-021); a mislabelled sample (the person wrote something else) slips through
  (review in Scenario 1, the alignment check in FR-007, deletion later); an adaptation run
  makes recognition worse (spec 001 FR-009's check); a run slows the device (FR-010).
- **What it never touches:** the network, partitions, the boot chain, system files.
- **Escape route:** Discard or delete samples, go back to the previous adaptation, turn
  learning or contributing off, remove a guest writer.

## Acceptance

Host level: prompt choice by coverage and error; a kept sample has exact spans and
unaltered points; Redo, Skip, Discard and delete store nothing or remove the sample; the
contribution rules (only shipped prompts while on, one-by-one additions, no identifying
fields); export and import round trip.

Host level, other writers:

- the owner and two guest writers each write: every sample is in its writer's store only,
  and the owner's store (samples, index, size, adaptation, letters, contribution, learned
  words) is unchanged byte for byte after the guest sessions;
- a sample still being kept when its session is left lands in its writer's store, and one
  of a writer removed meanwhile in none;
- a writer picked again continues at their next prompt with their samples, licence and
  contributor number; contribution files saved in two of their sessions carry the same
  number; a new number only at their request; removed and added again, a new number;
- a marker name appears in no file of any store, contribution file, export or recognizer
  request, and in no file name, only in the list of writers;
- one session at a time: opening the app again shows the same window; a guest session
  offers no list of writers and no way to another writer; after a restart the app opens in
  the owner's session;
- a guest's readings equal the base model's for the same strokes, whatever the owner's
  adaptation, learned words and size reference; the recognizer refuses adaptation, a letter
  model, a font and neatening for a guest store, and adapts none in the background;
- each row of FR-024 touches what it says and nothing else; Remove deletes the store and
  leaves saved contribution files;
- the name field gives the keyboard no sample, learned word or prediction;
- a guest's contributing is off until the writer turns it on in their session; the owner's
  session and the list offer no way to; without the 18-or-older confirmation no licence can
  be chosen, and the confirmation is in no file;
- the owner's export holds no guest store; a guest's export holds that writer's samples only,
  without their name.

Hardware: supervised: a writing session of words and sentences in English, an adaptation
on request with the before and after rates, a contribution file inspected on the host; a
guest writer added, introduced and contributing, Finish, picked again for a second session,
both contribution files inspected on the host for one contributor number and no name, then
removed.

## Out of scope

- Training base models (the project's training pipeline reads contribution files).
- Sending a contribution anywhere (the person does that outside the app).
- Fonts from the person's handwriting (spec 001 Scenario 4's app).
- A second account, or a guest's writing anywhere but in this app (on the keyboard or in other
  apps the device writes as the owner's).
- Checking a writer's age beyond their own confirmation (FR-019).
- Moving guest writers to another device: the owner's export holds the owner's store only; a
  writer exports their own samples from their session (FR-018).
