# GlassineOS/handwriting

On-device handwriting recognition for GlassineOS.

It reads the pen's movement as you write (online recognition: positions over time, pen
down and up), not a picture of finished writing. Stroke order, direction and pauses tell
letters apart, so a small model is enough and it can read while you are still writing.

## One program, many clients

The recognizer is a separate program on the person's session with a versioned
interface. The shell's on-screen keyboard is its first client: its writing paper turns
handwriting into text in any field (`GlassineOS/shell`, spec 006). Any app can use the
same interface:

- **Recognize** strokes, with context (the text before the cursor, the language), into
  ranked candidates, each with which strokes made which character.
- **Gestures:** strike-through, caret and scratch-out, with what they cover.
- **Prompted writing:** given the text to expect, align the strokes to its characters.
  An app that turns a person's handwriting into a font builds on this; the same samples
  teach the recognizer.

## Learning, and the person's control

The recognizer ships a base model per language (English first) and adapts to the person
on the device, from samples they confirmed (a picked alternative, a correction, a prompt
they wrote), never from its own guesses and never from private fields. Adaptation runs
while the device is idle and charging and is kept only when it does better on held-back
samples. Learning can be turned off, everything erased at once, and samples moved to a
new device by export and import. Nothing leaves the device otherwise.

Base models are trained only on data the project may use: synthetic handwriting, data under
a suitable licence, data used with its authors' permission (the BRUSH dataset, for
non-commercial use; [`train/README.md`](train/README.md)), and samples their writer gave for
training. The recipe, sources and licences ship with them, and a model whose data carry terms
of their own, such as non-commercial use, carries those terms.

The first spec is [`specs/001-recognizer`](specs/001-recognizer/spec.md).

## What is here

- [`recognizer/`](recognizer): `glassine-handwriting`, the recognizer (C, no runtime
  beyond libc and json-c). How to talk to it and read its files:
  [`docs/recognizer.md`](docs/recognizer.md). `make -C recognizer check` runs its tests.
- [`samples/`](samples): `glassine-handwriting-samples`, the handwriting samples app
  ([`specs/002-sample-collection`](specs/002-sample-collection/spec.md)): write shown words
  and sentences so the recognizer learns your hand, ask it to learn, and contribute reviewed
  samples to the base models as a file you send yourself. Other writers on the same tablet
  (family, friends) write in sessions of their own, each with a store kept apart from yours
  and a contribution only they turn on. Python with GTK 3; its prompt sets
  and their sources are in `samples/prompts/`. `make -C samples check` runs its tests.
- [`service/`](service): `glassine-handwriting-service`, handwriting for apps
  ([`specs/004-handwriting-for-apps`](specs/004-handwriting-for-apps/spec.md)): one recognizer
  for every app, the service socket ([`docs/service.md`](docs/service.md), schema
  `docs/service.schema.json`) and the side behind the pen interface. `make -C service check`.
- [`client/`](client): `libglassine-handwriting` and its Python binding, the pen interface
  for apps ([`docs/pen-interface.md`](docs/pen-interface.md)). `make -C client check` (the
  end-to-end tests need GlassineOS/sway's stand-in compositor, `HW_STANDIN`).
- [`train/`](train): the trainer and the base model recipe.
- [`models/`](models): how a trained model is released (GitHub release assets, not
  files here) and each language's prompts.
- [`synth/`](synth): synthetic handwriting from fonts, the trainer's data.
- [`docs/samples.md`](docs/samples.md): the sample format.

## Status and framing

GlassineOS is a hobby project, written largely with AI coding agents and not reviewed by
subsystem experts. The recognizer is tested on a desktop machine only; no device has run it
yet.

## License

GPL-2.0-or-later (`LICENSE`).
