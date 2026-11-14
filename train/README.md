# glassine-train (prototype)

A first small English online-handwriting recognizer, trained only on synthetic ink from
`../synth`. It is a prototype to learn what works, not the product engine: every number
below is measured on synthetic ink, never on real handwriting.

```sh
(cd ../synth && uv run glassine-synth fetch)       # the fonts
uv run glassine-train data --ablation              # text, then all datasets (about 15 min, 4 GB in build/)
uv run glassine-train train --run main --minutes 120 --steps-per-s 1.8056 --lr 3e-3 --threads 6
uv run glassine-train train --run novar --data train-novar --minutes 120 --steps-per-s 1.8056 --lr 3e-3 --threads 6
uv run glassine-train eval --run main              # every test set, CER/WER + confusions
uv run glassine-train export --run main            # ONNX, size, single-thread x86 timing
uv run glassine-train adapt --run main --font reeniebeanie   # head-only adaptation + timings
uv run glassine-train train --run aug --augment ...  # same, with random slant and stretch
uv run pytest -q tests
```

`--minutes` is a hard deadline and `--steps-per-s` only sets the schedule length
(minutes × 60 × rate = 13000 steps here), so two runs get the same step count.

## Data

**Ink.** `glassine-synth` (`../synth`), nine OFL fonts from the Google Fonts handwriting
category (`../synth/fonts.toml`; licence and upstream commit kept next to each font and in
every dataset's `meta.json`). One rendered sample per text line, default `Params`.

**Fonts held out.** `kalam` and `reeniebeanie` never appear in training or validation.
The other seven train. Dancing Script stays in training: its joined letters are a known
generator artefact, so holding it out would measure the artefact.

**Text.** Public-domain books (`src/glassine_train/text.py`): the plain-text editions of
nine novels for training (Austen, Carroll, Conan Doyle, Shelley, Dickens, Stevenson,
Twain, Wells, Bronte) and one never used in training for the test texts (Baum, *The
Wonderful Wizard of Oz*). `fetch` checks each book's catalogue record for "Public domain
in the USA." and that every author died before 1956 (also free under life + 70 years),
and cuts the distributor's header, licence and footer away. Text is not stored in the
repository; it is fetched and rebuilt from this list.

Training lines (310 k available, 300 k used), at most 40 characters, characters
`a-z A-Z 0-9 .,;:!?'-()` and space:

- book sentences cut into 1 to 8 word lines;
- random word sequences from the training vocabulary (a third as many), so the model
  cannot lean on sentence context;
- numbers, dates, times, phone-style numbers, punctuation fragments (1 in 12);
- random letter strings, every letter equally likely (1 in 12), for rare letters and
  capitals (the nine training books contain 8 capital Z in all).

Validation (model selection only): 2000 held-back lines of the training books, training
fonts. Test sets, generated once with fixed seeds and reused for every model, the same
texts in both font groups:

| set | text |
|---|---|
| `test` | 2000 lines of the test book |
| `test_words` | 1000 lines of random words from the test book's vocabulary |
| `test_symbols` | 500 number and punctuation lines |
| `test_letters` | 500 random letter strings |

each rendered in the training fonts (`-trainfonts`) and in the held-out fonts only
(`-heldout`, the honest proxy), plus `test-font-kalam` / `test-font-reeniebeanie`, and
for the ablation `test-*-novar` (no stroke-order variants).

## Features (`features.py`)

Only what a pen reports: x, y, t per point, strokes in order. Height, tilt and baseline
come from the ink, never from the generator:

1. remove the line's tilt by a least-squares fit (only for lines wider than twice their
   height, at most 20 degrees);
2. core height = spread of y between the 20 % and 80 % quantiles of the arc-length
   resampled ink; scale everything by it, centre y on the median;
3. resample each stroke by arc length, step 0.1 core height;
4. per point: dx, dy (at a stroke start the pen-up jump), y, a pen-up flag, and log pause
   length before the stroke. Synthetic timing is a toy model, so time enters only as the
   pause and the order of points.

## Model (`model.py`)

Conv1d 5→64 (k5), three strided Conv1d 64→128→128→128 (k5, stride 2: 8× fewer frames),
GELU, padding masked; 2-layer bidirectional LSTM, 128 per direction; linear to 73
characters + CTC blank. Greedy CTC decoding. 885 k parameters. Lines too short for CTC
after 8× downsampling (5 of 300 k, all punctuation-only strings such as `..!` whose
ink is tiny) are left out of training.

AdamW, one-cycle LR (peak 3e-3), batch 32 (buckets of similar length), gradient clip 5,
dropout 0.1, CPU (6 threads), checkpoint and validation every 1000 steps; the best
validation CER is kept.

## Results

Two runs of 13000 steps (batch 32, about 1.4 passes over 300 k lines), 75 minutes each
on 6 CPU threads, run side by side. Greedy decoding, no lexicon or language model. CER =
character edit distance / reference length; WER the same over words; "lines" = share of
lines read exactly.

### Main model (stroke-order variants on)

| test set | CER | CER, case folded | WER | lines exact |
|---|---|---|---|---|
| test book, training fonts | 0.6 % | 0.5 % | 4.2 % | 90 % |
| test book, **held-out fonts** | **30.0 %** | 28.6 % | 76.7 % | 7 % |
| … held-out `kalam` only | 9.2 % | 8.4 % | 39.0 % | 23 % |
| … held-out `reeniebeanie` only | 50.0 % | 48.6 % | 112 % | 2 % |
| random words, training fonts | 0.6 % | 0.5 % | 5.1 % | 89 % |
| random words, held-out fonts | 33.4 % | 31.3 % | 99.0 % | 4 % |
| numbers and punctuation, training / held-out | 2.2 % / 29.4 % | | | |
| random letters, training / held-out | 5.4 % / 32.8 % | 3.5 % / 28.0 % | | |

Validation (held-back training-book lines, training fonts) ended at 0.52 % CER.
Random word order reads as well as sentences, so the model does not rely on sentence
context; random letter strings are clearly worse (5.4 % vs 0.6 % on training fonts), so it
does carry a word-spelling prior. The held-out fonts are the honest number, and it is
poor, with a wide spread: `kalam` (a print hand close to the training fonts) reads at
9 %, `reeniebeanie` (small x-height, an `e` without a bar, an open `a`) at 50 %. The
generated strokes for `reeniebeanie` were checked by eye against training fonts: no
skeleton artefacts, the letters are just drawn differently. Top held-out errors:
`e→c` (1302), `a→o` (623), `h→l` (508), `r→c`, `n→u`, `y→l`, `g→o`: letter shapes that
differ in small loops and joins between fonts. On training fonts the errors are spaces
(missing or extra), `I/l/i`, `O/o/0` and apostrophes.

### Ablation: stroke-order variants (synth `reverse_loop`, `delay`, `extra_lift`)

Same texts, seeds and steps; the second model trained with the three set to 0.

| test set | variants on in training | variants off in training |
|---|---|---|
| training fonts, test ink with variants | 0.6 % | 10.4 % |
| training fonts, test ink without variants | 0.7 % | 0.6 % |
| held-out fonts, test ink with variants | 30.0 % | 39.2 % |
| held-out fonts, test ink without variants | 29.8 % | 30.9 % |

The variants cost nothing on ink written in canonical order, and a model trained without
them cannot read dots and crosses written after the word (10 % CER on fonts it knows).
Keep them. This compares the generator with itself; whether real writers' orders match
the variants is open until there is real ink.

### Size and speed (x86 host, one thread)

| | |
|---|---|
| parameters | 885 k (encoder 866 k, output layer 19 k) |
| ONNX fp32 | 3.5 MB; matches PyTorch to 4e-5 |
| ONNX int8 weights (dynamic) | 0.9 MB; held-out CER 30.3 % (fp32 30.0 %) |
| one line (median 600 points), onnxruntime | 3.9 ms fp32, 5.1 ms int8 (median) |

Preprocessing (`features()`, Python + NumPy, not part of the ONNX file) adds 7.6 ms per
40-character line on the same host. ARM speed is not measured; as an estimate only, even
20× slower per thread would keep a line near 250 ms, inside the 1 s budget.

### Per-writer adaptation (FR-009): frozen encoder, small head

`adapt.py`: the encoder (convolutions + LSTM, 866 k parameters) runs once per confirmed
sample and its frames are cached; only the head trains, on those frames. Two heads: the
output layer alone (19 k parameters), or a residual bottleneck adapter 256→32→256 in front
of it, starting as the identity (36 k parameters). Adam, lr 1e-3, batch 8, 5 epochs.
A held-out font stands in for a new writer: adaptation lines are training-book text in
that font, the check is 1000 test-book lines in the same font, the other held-out font
and the training fonts.

Host, one thread (another training run on the machine at the same time):

| | |
|---|---|
| encoder forward, one line (PyTorch eager, host under load) | 7–8 ms (median) |
| head training, 200 cached lines × 5 epochs | 0.6 s (output layer), 1.0 s (adapter) |
| full-model fine-tune, one line (forward + backward + step) | 32–37 ms |

So adapting to 200 samples costs about 200 × 8 ms of encoding (once, cacheable as samples
arrive) plus about a second of head training on this host; a full fine-tune of the same
5 epochs would be about 35 s and touch every weight.

CER before → after, adapter (rank 32) / output layer only:

| writer stand-in | lines | same font | other held-out font | training fonts |
|---|---|---|---|---|
| `reeniebeanie` | before | 50.1 % | 9.4 % | 0.7 % |
| | 50 | 19.3 % / 36.1 % | 10.3 % / 9.0 % | 2.7 % / 1.3 % |
| | 200 | **8.2 %** / 18.9 % | 14.8 % / 11.2 % | 3.1 % / 1.8 % |
| `kalam` | before | 9.4 % | 50.1 % | 0.7 % |
| | 50 | 3.0 % / 5.4 % | 47.3 % / 47.9 % | 1.5 % / 0.9 % |
| | 200 | **1.8 %** / 2.5 % | 46.5 % / 47.0 % | 1.4 % / 1.1 % |

A small head learns a new hand quickly from a few hundred lines, which is the case for
FR-009 with a frozen shared encoder. These are upper bounds: a font draws every letter
the same way each time, which is far easier to learn than a real writer's variation. The
fact that a 36 k-parameter head on frozen features recovers `reeniebeanie` from 50 % to
8 % also says the encoder's features do separate its letters; it is the output mapping
that the seven training fonts never taught. The price is drift on other hands (training fonts
0.7 % → 3 % after the strongest adaptation): fine for a personal model, and exactly what
FR-009's held-back check on the person's own samples should watch; keep the base head to
fall back to.

### Slant and stretch augmentation

Pending: a third run (`--augment`: random shear, x and y stretch per line, applied to the
features) is training; its numbers follow in a later commit.

## What limits accuracy

1. **Too few hands.** Seven fonts are seven writers; the model fits their shapes
   (0.6 % CER) and generalises unevenly to others (9 % and 50 % on the two held out).
   This dominates everything else.
2. **Generator realism.** Fonts give tidy, repeatable glyphs; real hands vary letter to
   letter, join untidily and use stroke orders the generator does not have. Synthetic
   held-out fonts are only a proxy; there is no real-ink number yet.
3. **Small things that will matter later:** greedy decoding with no lexicon (spaces and
   `I/l`, `O/0` are context questions), and case (2–5 points of CER on letters).

## Next steps

- More hands: all OFL/Apache handwriting fonts the generator handles (the category has
  many more than nine), per-glyph stroke perturbation beyond affine jitter, and the
  augmentation above; measure held-out fonts after each.
- Real ink to test on: a small donated set under clear terms, as the research notes say;
  until then, report held-out fonts per release.
- Beam search with a word list and a small character n-gram, and context from the text
  before the cursor (FR-003, FR-006).
- Run the int8 ONNX model on the reference device to get real latency and head-training
  times (not measured here).
- Prompted alignment (FR-007): CTC forced alignment gives stroke spans per character; the
  generator's `spans` can test it.

## Licence

Code GPL-2.0-or-later. The fonts are OFL (their licence travels with them in
`../synth/build/fonts`); the texts are public domain. A model trained by this recipe
contains neither, but its recipe, sources and licences are this file.
