# Feature Specification: Handwriting for apps

**Feature Branch**: `004-app-api`
**Status**: Draft
**Input**: One public, documented way for any app, including sandboxed third-party apps,
to get the person's pen input as raw and neatened ink with recognized text, and to use
the handwriting service (reading saved ink, samples, the letter model, the font,
contribution), with each app identified by the system rather than by its own claim.

## Context

The recognizer (spec 001) is a program behind a line contract that each client starts
as its own child process; the neat ink layer (spec 003) sits around the ink surface.
Apps that want handwriting today must start a host program, which a sandboxed app
cannot do, speak a custom line format with no schema or client library, and name
themselves; every app loads its own copy of the model. This spec defines the interfaces
third-party apps use instead. The recognizer's line contract stays as the engine's own
interface behind them, so another engine can still stand in.

There are two interfaces, split by what an app is doing:

- **Pen interface**: while the person writes in the app's surface. It travels with the
  pen input itself, through the display server that already delivers it.
- **Service interface**: everything that is not live pen input (reading a saved drawing,
  the person's samples, learning, the letter model and font, contribution, settings).

## User scenarios

### Scenario 1 – A notes app gets text and neat ink (Priority: P1)

**Given** a third-party notes app, sandboxed, that asks for text and neatening on its
writing surface, **When** the person writes, **Then** the app receives the raw strokes at
once, a neatened version of each stroke, partial readings while writing and ranked
candidates at a pause, and the app never starts or talks to the recognizer itself.

**Given** the person taps a candidate in the app, **When** the app reports the pick,
**Then** the pick counts as a confirmation for that app's samples and learning exactly as
a pick on the system keyboard does.

### Scenario 2 – A drawing app keeps only neat ink (Priority: P1)

**Given** a drawing app with neatening on and text off, **When** the person writes a
label, **Then** the app receives neatened strokes and stores only those, no reading is
done for it, and the person's samples and learning are unaffected by what the app keeps.

### Scenario 3 – An app reads a saved drawing (Priority: P2)

**Given** an app with stored ink, **When** it asks the service to read it, **Then** it
gets the same ranked candidates the pen interface would have given for that ink.

### Scenario 4 – The person decides which apps may learn (Priority: P1)

**Given** an app asks for the first time to keep samples or to read the person's
samples, **When** the request arrives, **Then** the person is asked once, in plain words,
and can allow or refuse; the answer is remembered per app, shown in Settings and can be
changed there. Reading ink and neatening need no permission.

### Scenario 5 – One app cannot see another's writing (Priority: P1)

**Given** two apps, **When** one asks for samples, **Then** it gets only the samples kept
through itself, whatever name it gives itself.

## Requirements

### Both interfaces

- **FR-001**: An app is identified by the system: by the display server for the pen
  interface and by the operating system for the service interface (the process's user
  and, for a sandboxed app, its sandbox application id). A name an app gives itself is a
  label only and never decides access.
- **FR-002**: Sandboxed apps can use both interfaces without extra sandbox holes beyond a
  documented permission for the service interface.
- **FR-003**: Both interfaces are versioned; an app learns which features the running
  system offers (neatening, text, languages, letter model present) before using them, and
  an older app keeps working on a newer system.
- **FR-004**: Both are documented for app developers outside the project, with a
  machine-readable definition (the pen interface as a display-server protocol definition,
  the service interface as a schema), examples, and at least one client library.
- **FR-005**: One service holds the model in memory for all apps and owns the person's
  store; apps never touch the store's files.

### Pen interface

- **FR-006**: An app turns the interface on per writing surface and chooses: neatening
  (with a strength), text (on or off), the field context (input purpose and hints, the
  text around the insertion point, languages).
- **FR-007**: The app receives, per stroke: the raw stroke as soon as the pen lifts, and
  with neatening on the neatened stroke naming the raw stroke it replaces (spec 003
  FR-018–FR-029). With text on it receives partial readings while writing and ranked
  candidates (text, origin, fit verdict, which strokes form which character) at a pause.
- **FR-008**: The app reports what the person did with the reading: picked candidate,
  accepted, corrected text, or discarded. These reports are the confirmation signal for
  the person's samples and learning (spec 001), under the person's permission for that app
  (Scenario 4).
- **FR-009**: Samples and learning always receive the raw pen input from the interface
  itself, whatever the app keeps (spec 003 FR-024).
- **FR-010**: Text is never computed for a surface that did not turn it on, and nothing is
  sent to the recognizer or kept for a field marked private.
- **FR-011**: The pen interface extends the ink protocol (ink areas and pen-preview
  brushes); an app may use the ink areas without it and gets today's behaviour.

### Service interface

- **FR-012**: The service interface is a local, request and reply interface with
  notifications, in a widely supported text data format with a published schema, reachable
  only from the person's own session.
- **FR-013**: It offers: read ink (same results as the pen interface), the person's samples
  kept through the calling app (list, show, delete, export), learning status and control
  for that app, the letter model and font status and build, contribution (only through the
  samples app or Settings), and the permission state of the calling app.
- **FR-014**: Requests that touch the person's data need the person's permission for the
  calling app (Scenario 4); a refused or missing permission gives a distinct answer, never
  an empty result that looks like no data.
- **FR-015**: Long work (learning, letter model and font builds) runs in the service at low
  priority and reports progress by notification; no request blocks the pen interface.

### The writing's size

- **FR-016**: Every request the service makes of the recognizer for an app, through either
  interface, carries the units of the ink and a writing area, so a model that reads the
  ink's size (spec 001 FR-018) has a reference: the app's ink area and ruled lines when it
  set them; otherwise the bounding box of the writing held, with a line height and guide
  line found from the writing itself (documented). Writing too little to find a line in
  (a lone character) gets the line found earlier on the same surface, and none before
  there is one; ink in em, whose units say how high a line is, gets an area only when the
  app gives one. Through the service interface an app may give the area itself.
- **FR-017**: The pen interface's units are the surface's points with the points per
  millimetre of the output the pen writes on, whenever the display server knows the
  output's size, and the pen's rate measured from the time between its points. Without a
  known output size the units stay the pen's own, and no size in millimetres is assumed.

## Key entities

- **App identity**: the system's id of the calling app (FR-001).
- **Writing surface**: an app surface with the pen interface on, its choices and context.
- **Reading**: partial readings and ranked candidates for a piece of writing.
- **Writing area**: where a piece of writing lies, its line height and guide line, in the
  units of its ink (FR-016).
- **Report**: what the person did with a reading (FR-008).
- **Permission**: per app, whether it may keep samples and read them.

## Contracts

| Contract | Owned / consumed | Version | Spec location |
|---|---|---|---|
| Pen interface (surface choices and context in; raw and neatened strokes, readings, candidates out; reports back) | owned | new, version 1 | this spec; its protocol definition beside the ink protocol in GlassineOS/protocols |
| Service interface (read ink, samples, learning, letter model and font, contribution, permissions) | owned | new, version 1 | this spec; its schema in docs/ |
| Neat ink layer | owned by spec 003, exposed through the pen interface | 1 | spec 003 FR-018–FR-029 |
| Recognizer line contract | owned by spec 001; the engine interface behind both, no longer used by apps directly | 1 | docs/recognizer.md |
| Ink protocol (ink areas, brushes) | consumed and extended | glassine_ink_v1 | GlassineOS/protocols `glassine-ink-v1.xml` |
| Display server pen input and app identity | consumed (the compositor) | — | GlassineOS/sway |
| Sandbox app identity and permissions | consumed (the sandbox's app id; the system's permission store) | — | — |
| Permission settings page | consumed by the shell's settings app | — | shell specs |
| Writing paper | consumer: the keyboard uses the pen interface | — | shell spec 006 |

## Device scope and risk

Device-neutral. No device risk. User data: the interfaces give an app only what it kept
itself, under the person's permission; the store stays with the service.

## Acceptance

Host level:

- a sandboxed test app with only the display server socket receives raw strokes, neatened
  strokes and candidates for a stand-in pen;
- an app claiming another app's name gets none of that app's samples;
- a pick reported through the pen interface stores a sample identical to one confirmed
  through the recognizer contract for the same pen input, units and area;
- a surface with text off causes no recognizer request; a private field sends nothing;
- a service request without permission returns the distinct refusal;
- without an ink area every recognizer request for a surface's writing of a few
  characters carries an area and units with points per millimetre; with one, that area;
- a model that reads the ink's size reads the recorded ink through the pen interface about
  as well as through the keyboard's own path, and a model that doesn't reads it no worse
  than before;
- two apps reading at once share one loaded model;
- a client library example passes against the schema.

Product level: in the images acceptance suite, a sandboxed demo app writes, gets a
reading and a neatened stroke, and the person's permission prompt appears once.

## Out of scope

- Network access to handwriting; the interfaces are local only.
- Apps reading samples kept through other apps, or the whole store.

## Success criteria

- **SC-001**: A third-party developer can add handwriting with text and neatening to a GTK
  or Qt app using the client library in under a day, from the documentation alone.
- **SC-002**: With three apps using handwriting at once, memory for the model is held once.
- **SC-003**: The pen interface adds no visible delay to the raw stroke compared to an app
  reading the pen directly.
