# Base model releases

<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

A base model is trained by hand (`train/README.md`) and published as an asset of a GitHub
release of this repository. No model file is committed here: what is committed is what a
release is built from, `models/LANG/prompts.txt` (texts for prompted writing and donation,
CC0-1.0, every character in the model's character set) and the release command.

## A release

One tarball per language and version, `glassine-handwriting-model-LANG-VERSION.tar.gz`,
holding the directory the recognizer reads (`docs/recognizer.md`, "Base models"):

| File | What |
|---|---|
| `recognizer.ghm` | the model (ghm version 1, or 2 for one reading the reference height; its kind, recognizer, recorded in the header) |
| `README` | the recipe (trainer commit, training command), the data sources, the results |
| `LICENSES` | the licences of these files and of the training data (font notices, OFL text), and the terms a model carries from its data (a model trained on BRUSH: non-commercial use, `train/README.md`) |
| `prompts.txt` | `models/LANG/prompts.txt` |

`VERSION` is a plain number, counted per language (`model-en-1`, `model-en-2`, …). A
published asset is never replaced: a new model is a new version and a new tag. A release
published again under a new tag (its old tag can't be reused) adds `-rN` to the tag
(`model-en-2-r2`, `chars-en-1-r2`); its assets keep their names and bytes, so the version
is still the number before `-rN`.

## Making one

In `train/`, with the trained run in `build/runs/RUN/` (its `args.json` and
`eval-best.json` from `glassine-train eval`), the datasets in `build/data/` and the fonts
from `glassine-synth fetch`:

```sh
uv run glassine-train release --run RUN --lang en --version N --commit TRAINED_AT
make -C ../recognizer check-model MODEL_DIR=$PWD/build/release/glassine-handwriting-model-en-N
gh release create model-en-N build/release/glassine-handwriting-model-en-N.tar.gz \
    --target main --title "English handwriting model N" --notes-file NOTES.md
```

`--commit` is the commit of this repository the run was trained with (the run doesn't
record it). `release` exports the checkpoint to ONNX, stops when the held-out error through
ONNX differs from the run's evaluation (checkpoint and trainer don't match), converts it with
`recognizer/tools/onnx2ghm.py`, writes `README` and `LICENSES` and packs the tarball
deterministically (same inputs, same bytes), with its sha512 in a `.sha512` file beside it.
The release notes give the character error on the training fonts and on the held-out fonts
from the `README`, and say that it is measured on synthetic ink.

## The character model

The recognizer's character model (`chars.gclm`, `docs/recognizer.md`, Characters) is not
trained by hand: it is counted from the training books' text by a reproducible step, and
published as an asset of its own release, `chars-LANG-VERSION`, with a version counted on its
own (a new base model doesn't need a new character model, nor the other way round). In
`train/`:

```sh
uv run glassine-train charlm --lang en --version N     # fetches the books, checks their sha256
gh release create chars-en-N build/release/glassine-handwriting-chars-en-N.tar.gz \
    --target main --title "English character model N" --notes-file NOTES.md
```

The tarball holds `chars.gclm`, `chars.json` (order, pruning, sizes, the bits per character on
held-out paragraphs, the sources with their sha256) and `CHARS-LICENSE`; the books are
pinned by sha256 in `charlm.py` (`SOURCES`), so the same commit gives the same
`chars.gclm` (its sha256 is in `chars.json`). The files go into the language's model
directory beside `recognizer.ghm`; the `glassine-handwriting-model` package downloads both
assets and installs them together.

## Shipping a newer model

The `glassine-handwriting-model` package (GlassineOS/packages,
`glassine/glassine-handwriting-model/APKBUILD`) downloads the assets by their release URLs
and checks their sha512; the URLs are built from the versions (`pkgver` for the model, one
variable for the character model). For a new English model, set `pkgver`
to the new version and replace the tarball's line in `sha512sums` with the new `.sha512`
(or run `abuild checksum`). A language with its own version is a new subpackage of the
same recipe.

A model under trial is a package of its own beside the default one
(`glassine-handwriting-model4-en`, in `/usr/share/glassine/handwriting-model-4/en/` with the
same character model): installing it changes nothing until the setting `models` names that
directory (`docs/recognizer.md`, Base models).

## A model the person installs themselves

A model that can't be released here (for example one trained on handwriting data whose terms
don't allow it to be shipped) is never published by this repository. A person who obtained one
themselves, from its official source and under its terms, installs it on their own device as a
private replacement for a shipped model (spec 006; `docs/recognizer.md`, Installed models). The
system never downloads one.
