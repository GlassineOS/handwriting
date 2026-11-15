# GlassineOS/handwriting Constitution

This repository follows the GlassineOS organization principles (I–VIII). They come from
the `glassine` Spec Kit preset in `GlassineOS/.github` and are not edited per
repository. Repository-specific principles go in the last section.

## Organization principles

### I. Honest framing

GlassineOS is a hobby project, written largely with AI coding agents and not reviewed by
subsystem experts. Every README says so. Pull requests and commit messages state what was
verified and how (host test, emulator, which device and date), and say "untested" where
it applies.

### II. Safety before features

Anything that flashes, partitions, touches the bootloader, recovery, updates or user data
states its failure mode and its escape route before it is implemented. If the escape route
depends on what the change could break, the change needs explicit maintainer approval.
Device-affecting steps never run without the maintainer's go.

### III. Nothing vendor-owned, nothing personal

No vendor firmware or binaries, device dumps, decompiler output, keys, serial numbers, MAC
addresses or local paths in any repository. Firmware comes from the user's own device at
install time. The safety scan must pass.

### IV. Contracts are owned and versioned

Every interface between repositories (protocols, file formats, configuration, D-Bus or
socket APIs) has one owning repository that specifies and versions it. Consumers pin the
version they support and adopt new versions through their own spec. Breaking changes
bump the major version.

### V. Test at the right level

Libraries and daemons test their own behaviour in their own repository (host tests, no
device needed where possible). End-to-end behaviour of the assembled system is specified
and tested only as product acceptance in the `images` repository, as high-level
behavioural scenarios on hardware, never repeating library unit tests.

### VI. Device-neutral by default

No device constants outside device packages and device configuration. Code reads device
facts from configuration so a second device needs no changes outside its own packages.

### VII. E-ink first

Every user-facing change considers refresh cost, ghosting and latency on e-ink. Animations
and continuous redraws need a reason. Latency or power trade-offs are checked by feel on
the device, not by benchmark alone.

### VIII. Upstream and licenses

Our code is GPL-2.0-or-later unless the repository says otherwise; fork commits use the
upstream license. Nothing is submitted upstream unsolicited. AI-assisted commits carry an
`Assisted-by:` or `Co-Authored-By:` trailer; agents never add `Signed-off-by`.

## Repository principles

### Contracts

- **Owned:** the recognizer's session interface (recognition, streaming, prompted
  alignment, gestures, samples, learning control, export and import), the sample file
  format and the base-model package format. All three are versioned; a change an old
  client or reader would misread raises the version.
- **Consumed:** pen strokes as clients hand them over (positions over time, pressure
  where the pen has it). Clients include the shell's on-screen keyboard
  (`GlassineOS/shell`, spec 006) and any app.

### Privacy

Everything runs on the device; the program opens no network connection. Samples come
only from a person's confirmation in a client, never from the recognizer's own
unconfirmed output and never from a private field. They stay in the person's data
directory, readable only by them, and leave the device only through an export the person
starts. A person's samples are part of a base model only when that person gave them for
it (Models and data).

### Models and data

Base models are trained only on data the project may use: under a suitable licence,
synthetic, or samples their writer gave for training. Such samples are never committed or
published; only the trained model and aggregate measurements are. Every model ships with
its training recipe, its data sources and their licences. No vendor engine, model or resource is committed or shipped.

### Tests and hardware

Recognition, alignment, gestures and the sample rules are tested on the host against
recorded stroke files. Every release publishes the base model's error rate on the
project's fixed test set, which must not get worse from one release to the next. Runs on
real devices happen with the owner's go; nothing is called hardware-proven without one.

## Governance

The organization principles change only in `GlassineOS/.github` (preset version bump);
repositories pick changes up through their `.glassine/org` submodule. Repository
principles change by pull request in this repository. Cross-repository decisions are
recorded as decision records in `GlassineOS/docs`.

Only `spec.md` is committed. It states what a feature does and why, in words that stay
true whatever the code looks like: no languages, modules, routes or other parts of the
implementation. Plans, research, data models, contracts drafted for a plan, and task
lists are working notes for one build. They are not committed, because the code changes
under them and they go stale; pull requests and commit messages record how something was
built and why. An interface other repositories depend on is the exception: it is
specified and versioned by its owner (principle IV).

**Version**: 1.1.0 | **Ratified**: 2026-11-12 | **Last Amended**: 2026-11-12
