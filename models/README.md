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
| `recognizer.ghm` | the model (ghm version 1) |
| `README` | the recipe (trainer commit, training command), the data sources, the results |
| `LICENSES` | the licences of these files and of the training data (font notices, OFL text) |
| `prompts.txt` | `models/LANG/prompts.txt` |

`VERSION` is a plain number, counted per language (`model-en-1`, `model-en-2`, …). A
published asset is never replaced: a new model is a new version and a new tag.

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

## Shipping a newer model

The `glassine-handwriting-model` package (GlassineOS/packages,
`glassine/glassine-handwriting-model/APKBUILD`) downloads the asset by its release URL and
checks its sha512; the URL is built from the version. For a new English model, set `pkgver`
to the new version and replace the tarball's line in `sha512sums` with the new `.sha512`
(or run `abuild checksum`). A language with its own version is a new subpackage of the
same recipe.
