# glassine-train (prototype)

A small English online-handwriting recognizer, trained only on synthetic ink from `../synth`.
It is a prototype to learn what works, not the product engine: every number below is
measured on synthetic ink, never on real handwriting.

```sh
(cd ../synth && uv run glassine-synth fetch)        # the fonts in ../synth/fonts.toml
uv run glassine-train data --subsets 40 --variation --copies 2 --workers 20
uv run glassine-train train --run full --data train-f158-var-x2 --augment --hidden 176 \
    --minutes 400 --steps-per-s 0.66667 --lr 3e-3 --threads 10      # 16000 steps
uv run --extra lexicon glassine-train lexicon       # word list + character model (CC BY-SA)
uv run glassine-train tune --run full               # decoder weights, on the dev fonts
uv run glassine-train eval --run full --lexicon build/lexicon/en   # every test set, greedy + lexicon
uv run glassine-train export --run full             # ONNX fp32 + int8, size, x86 timing
uv run glassine-train adapt --run full --font reeniebeanie        # head-only adaptation
uv run glassine-train release --run full --lang en --version N --commit C   # ../models/README.md
uv run pytest -q tests
```

`--minutes` is a hard deadline and `--steps-per-s` only sets the schedule length (minutes ×
60 × rate = 16000 steps here), so runs on a busy host get the same step count.

## Data

**Ink.** `glassine-synth` (`../synth`): one rendered sample per text line. The fonts are
every Latin handwriting family of Google Fonts that the stroke extraction handles, 176 in
all (163 OFL, 13 Apache 2.0; `../synth/fonts.toml` lists them and the ones left out with
the reason; licence and upstream commit are kept next to each font and in every dataset's
`meta.json`).

**Fonts by split** (`split` in `fonts.toml`; a font is in one split only):

| split | fonts | used for |
|---|---|---|
| train | 158 | training |
| dev | 4 (2 print, 2 script) | choosing the checkpoint, the pilots and the decoder weights |
| test | 2 original + 12 new | the reported held-out numbers, never used for any choice |

The two original held-out fonts, `kalam` and `reeniebeanie`, stay held out, and their test
sets keep their names and bytes, so old and new models are measured on the same ink. The 12
new test fonts and the dev fonts were drawn at random from the pool, stratified six print
and six script (two and two for dev), because the pool is mostly script and the two groups
read very differently. None has a near-duplicate in training: its nearest training font by
glyph-skeleton distance is no closer than training fonts are to each other (the closest
training pairs, a family and its swash-capital variant, were cut to one). Many families
share a designer; that was not excluded, and the per-font results below show it does not
explain the numbers.

**Letter-shape variation** (`--variation`, synth `Params.warp = 0.025`, `zones = 0.15`):
per letter a smooth random displacement of all its strokes, per line a scale of ascenders,
descenders and letter width. Training only; every test and dev set is rendered with the
default parameters, so the variation is never measured against itself. `--copies 2`
renders every training line twice with new ink (600 k lines).

**Text.** Public-domain books (`src/glassine_train/text.py`): the plain-text editions of
nine novels for training (Austen, Carroll, Conan Doyle, Shelley, Dickens, Stevenson, Twain,
Wells, Bronte) and one never used in training for the test texts (Baum, *The Wonderful
Wizard of Oz*). `fetch` checks each book's catalogue record for "Public domain in the USA."
and that every author died before 1956 (also free under life + 70 years), and cuts the
distributor's header, licence and footer away. Text is not stored in the repository.

Training lines (300 k), at most 40 characters, characters `a-z A-Z 0-9 .,;:!?'-()` and
space: book sentences cut into 1 to 8 word lines; random word sequences from the training
vocabulary (a third as many), so the model cannot lean on sentence context; numbers,
dates, times, phone-style numbers, punctuation fragments (1 in 12); random letter strings,
every letter equally likely (1 in 12).

Dev (`dev`) and the training-font check (`val`): 2000 held-back lines of the training
books, in the dev fonts and in the seven original training fonts. Test sets, generated
once with fixed seeds, the same texts in each font group:

| set | text |
|---|---|
| `test` | 2000 lines of the test book |
| `test_words` | 1000 lines of random words from the test book's vocabulary |
| `test_symbols` | 500 number and punctuation lines |
| `test_letters` | 500 random letter strings |

each in the original held-out fonts (`-heldout`, plus `test-font-kalam`,
`test-font-reeniebeanie`), the new held-out fonts (`-newprint`, `-newscript`; "new
held-out" below is their mean; `test-font-NAME`, 300 test-book lines, for each of them) and the seven original training fonts (`-trainfonts`, in
training for every model here).

**Text for the next base model** (`lines.py`, spec 001 FR-020), line kinds of their own in
`SHARES` of the training lines: book lines (38 %), words of the books in random order
(13 %), pseudo-words drawn letter by letter from the character model (`charlm.py`, the
training books only; never a word of the books or of the word list) plain (10 %) and
flattened at temperature 2 (6 %), names (7 %), ten code kinds (17 % together: ordinals and
measures, times, dates, phone numbers, postcodes, number plates, part numbers, email and web
addresses, numbers and prices; a third of them inside a few book words), uniform letter and
digit strings (3 %) and lone characters and marks (6 %). Names: the U.S. Census Bureau's
1990 given-name lists and 2010 surname list (surnames borne by 1000 people or more),
public domain as works of the U.S. federal government, pinned by sha256 (`NAME_SOURCES`),
weighted by the square root of how common they are; one name in ten (by CRC-32) is held out
for the test sets. The alphabet (`CHARS4`) is `CHARS` with `@/&+#%_=$` appended (every
training font has them), so the old characters keep their class numbers. The test book is
never a source; its words are only kept out of the pseudo-words.

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
GELU, padding masked; 2-layer bidirectional LSTM, 176 per direction (128 in the 7-font
baseline); linear to 73 characters + CTC blank. 1.41 M parameters (encoder 1.38 M, output
layer 26 k). Lines too short for CTC after 8× downsampling (16 of 600 k, punctuation-only
strings whose ink is tiny) are left out of training.

AdamW, one-cycle LR (peak 3e-3), batch 32 (buckets of similar length), gradient clip 5,
dropout 0.1, random slant and stretch per line (`--augment`: shear sd 0.15, x stretch
lognormal sd 0.12, y stretch sd 0.08, pause feature scaled), CPU (10 threads); checkpoint
every 1000 steps, `best.pt` = lowest CER on the dev fonts.

### Interface

Unchanged from the 7-font baseline: the same features (`features.py`), ONNX input `ink`
(1, points, 5) float32 and output `logp` (1, frames, 74) log-probabilities, class 0 = CTC
blank and class k = `ALPHABET[k - 1]` (`data.py`), frames = points downsampled 8× (three
stride-2 convolutions). Only the LSTM width changed, which is inside the file. One
consequence outside it: the encoder frames, which are the input of a per-writer head
(`adapt.py`), are now 352 wide (2 × 176) instead of 256, so a head trained on the 7-font
baseline does not fit this encoder; heads are always trained against the encoder they run
on.

## Lexicon decoding (`lexicon.py`)

A CTC prefix beam search (beam 16) over the model's output with a word list and a
character n-gram model, in plain Python so the recognizer can port it as it is (the
module docstring is the specification). Score of a hypothesis:

    log P_ctc + lm_weight · Σ log P_char(c | previous 4 characters) + char_bonus · characters
    + word_bonus · listed words − oov_penalty · unlisted words

A word is a run of letters with inner apostrophes, looked up without case; numbers and
punctuation pass without word bonus or penalty, and words that are not in the list stay
possible (they pay the penalty), so names and rare words can still be written. The
character model is interpolated Witten-Bell over n-gram counts (a table of context, next
character, count). Weights tuned on the dev fonts only (`tune`, 1000 lines, grid in
`decoder.json`): `lm_weight 0.25, char_bonus 0.5, word_bonus 0, oov_penalty 1.5`; a word
bonus never helped. Per line about 5 ms in CPython on the x86 host (median, warm cache).

**Word list and character model** (`lexicon`): from the same pinned sources as the word
predictor's English model (GlassineOS/shell `tools/predict-model/en.sources`, the same
sha256): the Simple English Wikipedia (CC BY-SA 3.0 and GFDL) and Tatoeba's English
sentences (CC BY 2.0 FR) without its two stock names. The 50 000 most frequent words, and
character n-grams up to order 5 seen at least 50 times (432 k entries, 4.8 MB as text, 1.9
MB gzipped). The result is CC BY-SA 3.0, not GPL; `LICENSE.txt` with the attributions is
written next to it, and neither file is in the repository. The test book is not in the
sources. Out-of-list words: 1.5 % of the test book's words, 8 % of its random-word lines,
3 % of the dev text. Order 6 with every n-gram seen 3 times (4.9 M entries, 52 MB) read
the dev fonts 0.3 points better (with the 7-font baseline); order 4 0.7 points worse.

## The recognizer's character model (`charlm.py`)

The recognizer program reads with a character model (`docs/recognizer.md`, Characters; spec
001 FR-019); `glassine-train charlm --version N` builds it and packs its release
(`../models/README.md`). Interpolated Witten-Bell over character 5-grams (counts only, the
module docstring is the specification), from the nine training books' paragraphs (pinned by
sha256; the test book never) plus generated number and code lines for 3 % of the
characters (`codes()`: numbers, prices, times, dates, ordinals, measures, phone numbers,
postcodes, plates, part numbers), so digits have their real neighbours. A context seen fewer
than 20 times backs off to the shorter one as a whole. One paragraph in 50 (by CRC-32) is
held out of it. 1.1 MB, 26 k contexts.

Held-out bits per character (no code lines): order 4 2.25, order 5 2.00, order 6 1.91 (2.2
MB), order 7 (pruned at 50) 1.95; Kneser-Ney was within 0.03 unpruned and needs its own
pruning, so Witten-Bell. A small character RNN was not tried: the decoder asks for a
probability at every new prefix of its searches (thousands per reading), which an n-gram
lookup answers in a few table probes.

**Decoder weights,** tuned through the recognizer program itself (`eval-samples.py`) on
synthetic dev ink only: held-out paragraphs of the training books (never in the character
model) rendered in the 4 dev fonts as prose lines (500), single words (500), number lines
(150), codes (150: ordinals, times, measures, plates, part numbers, dates), random letter
strings (150) and lone characters (150). Grid over weight, floor, bonus per character,
margin, beam, order and pruning, the share of code lines; the choice: weight 0.35, floor −8,
bonus 1, beam 8, margin 0 (with words 1). CER, model 2, `read … raw` / `chars`:

| set | ink only | character model | words (before) | words + character model |
|---|---|---|---|---|
| prose lines | 4.3 % | 2.2 % | 3.5 % | 2.3 % |
| single words | 4.7 % | 1.9 % | 3.6 % | 2.1 % |
| codes | 25.0 % | 23.4 % | 25.0 % | 23.5 % |
| number lines | 6.3 % | 6.2 % | 6.3 % | 6.6 % |
| random letters | 22.2 % | 23.2 % | 22.2 % | 24.2 % |
| lone characters | 39.3 % | 39.3 % | 39.3 % | 40.0 % |

Without the code lines the character model cost codes 1 to 5 points; a higher weight (0.5)
reads prose a little better (2.0 %) but random letters 4 points worse.

**On one writer's real ink, same writer** (prompted samples, mostly single words, read by the recognizer
program, three candidates; frozen weights, the test session read once). Model 2 with a fresh
per-writer head trained on the other sessions (`glassine-handwriting adapt`), CER / WER:

| | ink only | character model | words (before) | words + character model |
|---|---|---|---|---|
| the fixed test session (116) | 13.9 / 66 % | 5.6 / 25 % | 8.2 / 33 % | **5.4 / 22 %** |
| each session held out once (756) | 14.4 / 51 % | 8.4 / 29 % | 9.6 / 34 % | **7.2 / 23 %** |
| later sessions (96) | 13.7 / 57 % | 7.4 / 30 % | 7.1 / 25 % | **5.9 / 22 %** |
| model 2 alone, each session held out once | 37.4 / 91 % | 32.0 / 82 % | 35.6 / 85 % | 30.7 / 76 % |

Words + character model against words alone (paired bootstrap over samples, 95 %): with
the head −3.1 to −1.7 CER points held-out-session pooled, −4.3 to −1.4 on the fixed test
session; without a head −5.9 to −3.9. Texts with digits (41, all in the first two sessions)
read no worse (head: 26.0 → 25.2 %); letters read as digits inside words stayed as rare as
they were with the head (6 → 7 of 623 texts) and fell without it (5 → 2). Lone characters (97)
read as before with words, but 6 points worse with the character model alone (`read … chars`,
margin 0): the margin was tuned on synthetic lone characters, where it cost nothing.

**Time on the device** (the e-ink tablet's Cortex-A53/A73 cores, the recognizer program
before and after this change, interleaved, three rounds of 60 synthetic lines each, median
read time): prose lines with words 241 → 243 ms (+1 %; 95th percentile +3 %), single words
85 → 88 ms (+4 %; +6 %); with the character model alone against the ink alone, 222 → 227 ms
(+2 %) and 81 → 85 ms (+4 %). Round-to-round spread is about ±5 %.

## Data for the next base model (model 4; `gen4`)

Spec 001 FR-018 and FR-020. Model 4 is trained on it (Results, Model 4); the shipped model is
still model 2.

```sh
uv run glassine-train real STORE --out build/real --keep-test OLD/split.json  # the fixed test set
uv run glassine-train dynfit --real build/real --out build/real/fit.json      # timing, delays, gaps (aggregates)
uv run glassine-train gen4 --out build/ink4 --workers 16                       # every set, manifest.json
uv run glassine-train inkcheck build/ink4/train --real build/real/fit.json --sheet build/ink4/train.png
uv run glassine-train featurize build/ink4/train --out build/data/train4-ref --ref mix
uv run glassine-train gen4-letters LETTERS/model --out PRIVATE/letters4 --fit build/real/fit.json
# some kinds again (here after a change to them), into the existing set without featurizing it all:
uv run glassine-train gen4 --only train-fix --train-name train-fix --kinds uniform lone --lines 54000 --seed 43
uv run glassine-train featurize build/ink4/train-fix --out build/data/train-fix-ref --ref mix
uv run glassine-train mergeset --base train4-ref --drop-kinds uniform lone --add train-fix-ref --out train4-fix
```

`train --init CKPT` goes on from a checkpoint (its architecture, inputs and characters) with
a new schedule. `writereval STORE --models NAME=NOCHARS,CHARS ...` reads another writer's
samples as a new person (no head, no letters), every decoder mode, each model against the
first; it is an evaluation set only and writes only aggregates.

**Ink sets, not features** (`inkstore.py`): the pen's points (x, y in 1/512 em, t in
seconds) and per line its text, kind, writer, spans and its references: `xh`, the
writer's letter height, `line`, the line height of a writing area drawn for it, and `base`,
the baseline. `featurize` turns a set into the trainer's dataset format, either with the
shipped features (`--ref none`) or with the reference-height prototype
(`features.features_ref`, `--ref xh|line|mix`): lengths in reference heights (the letter
height, or 0.2 lines when only the line is known; `mix` uses the line alone for 30 % of the
lines), y above the baseline, and a sixth input, the ink's height in reference heights. A
real sample's references come from `real.reference` (the writer's letter height measured on
the training sessions, the area's line and guide). `InkSet` reads the number of inputs from
the dataset.

**Generator** (`gen.py`): the fonts' glyphs with the synth's per-letter jitter and
variation, laid out by writers drawn per line:

- *spacing* by ink profiles as the recognizer's `spacing.c` (`spacing.py`; on the letter
  model's versions it gives the stored profiles and middles to 5e-5 x-heights). A font
  writer sets each pair at the writer's typical gap measured in the x-height band (the
  bands between the baseline and the x-height, the recognizer's guard: a t's bar or an f's
  hook may reach over the next letter, at most 0.5 x-heights), closer where the profiles
  leave open space, plus a spread per pair, never more than 0.12 x-heights into a letter
  before it; word gaps box to box. Typical gaps and word gaps come from a pool of styles:
  each training font laid out at its own advances, moved by rank onto the spread of
  written spacing (`gen.CALIB`: x-height-band gaps median 0.46, sd 0.09 between writers;
  word gaps median 1.35, lognormal 0.2), so a tight font stays a tight writer but no font
  keeps its typeset spacing; and a letter model's spacing as one style more. A letter
  model writer places pairs as the recognizer does (`spacing.place`, `hw_spacing_place`):
  by its letters' middles at the pair's distance or the characters' reaches, guarded by
  the gap. On 1500 lines the generated x-height-band gaps come out at median 0.46,
  sd 0.30, 10 % touching (all bands: 0.43, 13 %), word gaps median 1.35, against one
  writer's real ink 0.44, 0.29, 6 % (0.42, 7 %) and about 1.4;
- *timing* (`timing.py`): a sigma-lognormal speed model fitted to real ink (`dynfit`):
  pulses found by least squares on each stroke's speed, described by duration, skew and
  overlap, with the stroke's duration from its path; pen-up pauses from the jump. Each
  writer is drawn around the fit (`BROADEN`: tempo, skew, pulse rate, overlap and pauses);
  the fit is of one writer (`FIT_ONE_WRITER`), so that spread is assumed, not measured;
- *delayed strokes*: dots of i and j (chance per writer around 0.8), bars of t, f and barred
  capitals (0.03 to 0.2) after the word or, a tenth of the time, after a later letter,
  several mostly right to left, after their own pause; in every set;
- *size*: lone characters and marks at their true size on the line; the writing area's
  x-height over line height drawn per line (median 0.14, 0.06 to 0.35).

The sets (`build4.py`): `train` (600 k lines in `lines.SHARES`, training fonts), `val`,
`dev`, `dev_kinds`, model 2's test texts in each held-out group, `test_kinds` (100 lines of
every kind but prose, held-out names) and one set per held-out font. `writers.py` adds
contributors' letter models as writers (`--writers`): only under a licence that covers
derived models (CC0-1.0, CC-BY-4.0) and never a contributor in the real test set.
`gen4-letters` writes in one letter model's own versions (in proportion of use), spacing,
fitted timing and delay habits; ink made from a person's letter model stays with their
samples, never in a repository, a release or a log. It is not part of the base model's
data: it is for that person's adaptation head only.

**Names** (`lines.py`): given names from the U.S. Social Security Administration's national
baby names (1930 to 2025, at least 50 times, weighted by the square root of how often;
public domain), surnames from the 2010 census. The SSA file refuses scripted downloads:
open https://www.ssa.gov/oact/babynames/limits.html in a browser, take "National data"
(`names.zip`) and save it in `build/names/` as `ssa.names.zip`; `gen4` checks its sha256
and says how to fetch it when it is missing. `--given census` uses the 1990 census given
names instead, so changing the given-name lists is one regeneration.

**Real ink in training** (`real.py`). The split's test set is fixed (`--keep-test`), and no
test sample ever trains, the adaptation head included: the split writes `test-ids.txt`,
and every reader of training ink (`load_split`, the adaptation store `realeval` and the
letter model build from, `finetune --test-ids` for more ids) checks the ids and stops when
a test sample is there. Train and dev samples whose text is a test text, or nearly (the
same after case, marks and spaces; or 0.85 alike), are left out. The real test set is one
writer, the one whose ink the timing fit and the letter model come from: results on it are
**same writer**, not other hands. A test on other writers comes from guest contributions
(spec 002).

## Results

CER = character edit distance / reference length, WER the same over words, "greedy" =
best path, "lexicon" = the decoder above. All on synthetic ink.

### Before and after

| | 7-font baseline | this model |
|---|---|---|
| training | 7 fonts, 300 k lines, 13 k steps, 885 k parameters | 158 fonts with letter-shape variation, 600 k lines, 16 k steps, 1.41 M parameters |
| **original held-out fonts**, test book, greedy / lexicon | **30.0 % / 19.4 %** | **12.0 % / 6.0 %** |
| … `kalam` | 9.2 % | 1.1 % / 0.7 % |
| … `reeniebeanie` | 50.0 % | 23.7 % / 11.7 % |
| **new held-out fonts**, test book, greedy / lexicon | **37.2 % / 28.0 %** | **4.9 % / 2.1 %** |
| … print / script, greedy | 25.3 % / 49.0 % | 3.8 % / 6.0 % |
| … print / script, lexicon | 16.7 % / 39.2 % | 1.7 % / 2.5 % |
| random words, original / new held-out, greedy | 33.4 % / 41.5 % | 14.2 % / 5.5 % |
| … lexicon | 25.7 % / 34.4 % | 7.9 % / 2.5 % |
| numbers and punctuation, original / new held-out, greedy | 29.4 % / 40.0 % | 8.3 % / 13.6 % |
| random letters, original / new held-out, greedy | 32.8 % / 52.4 % | 22.3 % / 22.5 % |
| test book, training fonts, greedy / lexicon | 0.6 % / 0.9 % | 1.7 % / 1.1 % |
| random letters, training fonts, greedy (case folded) | 5.4 % (3.5 %) | 15.9 % (10.5 %) |
| dev fonts, greedy | 34.7 % | 3.8 % |

The 7-font baseline's lexicon numbers use weights tuned for it on the dev fonts in the same
way (`lm_weight 0.7, char_bonus 3.5, oov_penalty 2`). With the slant-and-stretch
augmentation alone (a run of the 7-font recipe with `--augment`) the original held-out fonts read at
26.9 % and the new ones at 36.8 %.

Per font, test book, greedy (300 lines each; "shares a designer" = a training font is by
the same designer, which is the case for most of the pool):

| font | style | shares a designer | 7-font baseline | this model |
|---|---|---|---|---|
| annieuseyourtelescope | print | yes | 24.7 % | 3.6 % |
| delicioushandrawn | print | no | 13.4 % | 2.5 % |
| eaglelake | print | yes | 47.8 % | 5.2 % |
| nothingyoucoulddo | print | yes | 25.6 % | 3.1 % |
| waitingforthesunrise | print | yes | 21.2 % | 11.3 % |
| yuyu | print | no | 18.6 % | 4.0 % |
| betaniapatmos | script | no | 47.5 % | 8.8 % |
| caramel | script | yes | 49.9 % | 3.2 % |
| damion | script | yes | 44.9 % | 8.0 % |
| kolkerbrush | script | yes | 67.0 % | 7.6 % |
| sacramento | script | yes | 55.4 % | 8.4 % |
| yellowtail | script | yes | 34.7 % | 4.0 % |

The three fonts whose designer has nothing in training read no worse (mean 5.1 %) than the
nine that share one (6.0 %), so the gain is not a designer leak.

The new held-out fonts are now read at 5 % greedy, 2 % with the lexicon. The original pair
improved less (12 %), almost all of it `reeniebeanie` (`e→c`, `n→u`, `a→e`: an `e` without
a bar, an open `a`), which is far from every training font. The price is on the seven
original fonts: 0.6 % → 1.7 % on book text and 5 % → 16 % on random letter strings,
mostly capitals (case-folded 10.5 %): the training set now has 158 alphabets of capitals,
many of them ornamental, and capitals are rare in the text. Top errors on new held-out
print are spaces and `r→v`; on script `n→m`, spaces, `g→q`, `o→a`.

**Lexicon decoding** halves the error on text in every font group and is the second
biggest step after the fonts. It hurts where there are no words: numbers and punctuation
(held-out 8.3 → 10.3 %, new 13.6 → 15.4 %) and random letters (22 → 33 %). The recognizer
should therefore keep the greedy reading as an alternative (or skip the word terms when the
field expects numbers or codes).

### What each step bought (pilots)

Pilot runs on the same recipe (885 k parameters, `--augment`, dev selection, 6000-step
schedule, run side by side). The 7- and 40-font pilots were stopped at step 3000 (7 fonts
had stopped improving: dev 39.3, 36.1, 35.9 % at steps 1000, 2000, 3000). Test sets
greedy:

| training fonts | steps | dev | original held-out | new held-out | training fonts |
|---|---|---|---|---|---|
| 7 | 3000 | 35.9 % | 30.4 % | 38.6 % | 2.0 % |
| 40 (random subset) | 3000 | 11.3 % | 21.4 % | 15.4 % | 11.6 % |
| 158 | 6000 | 5.3 % | 13.8 % | 7.5 % | 4.0 % |
| 158 + letter-shape variation | 6000 | 5.6 % | 14.1 % | 7.3 % | 4.3 % |

Dev CER at equal steps (1000 / 2000 / 3000): 7 fonts 39.3 / 36.1 / 35.9 %, 40 fonts 31.4 /
14.2 / 11.3 %, 158 fonts 30.4 / 10.7 / 6.7 %, with variation 31.3 / 9.5 / 7.6 %.

- **More fonts** is by far the largest gain: new held-out 38.6 → 7.5 %, original held-out
  30.4 → 13.8 %, and the curve is still falling from 40 to 158 fonts.
- **Letter-shape variation** bought nothing measurable (within ±0.3 points, one seed each).
  With 158 fonts the model already sees many shapes per letter; the warps add little on
  top. It is kept in the final recipe (it costs nothing), not because it was shown to help.
- **Larger model, twice the ink, more steps** (the final run against the 158-font pilot):
  new held-out 7.3 → 4.9 %, original 14.1 → 12.0 %, training fonts 4.3 → 1.7 %. The
  pilots underfit (training-font CER as high as dev), so capacity and steps were the next
  limit.

### Size and speed (x86 host, one thread)

| | 7-font baseline | this model |
|---|---|---|
| parameters | 885 k | 1.41 M |
| ONNX fp32 | 3.5 MB | 5.6 MB; matches PyTorch to 8e-5 |
| ONNX int8 weights (dynamic) | 0.9 MB | 1.4 MB; original held-out CER 12.1 % (fp32 12.0 %) |
| one line (median 600 points), onnxruntime fp32 / int8 | 3.3 / 4.2 ms | 5.3 / 5.0 ms |

Both models timed in the same process, interleaved, pinned to one core (an earlier quiet
measurement of the 7-font baseline gave 3.9 ms). Preprocessing (`features()`) and the
lexicon decoder add about 8 ms and 5 ms per line in Python.

### Per-writer adaptation (FR-009): frozen encoder, small head

`adapt.py`: the encoder (convolutions + LSTM, 1.38 M parameters) runs once per confirmed
sample and its frames are cached; only the head trains: the output layer alone (26 k
parameters) or a residual bottleneck adapter 352→32→352 in front of it, starting as the
identity (49 k). Adam, lr 1e-3, batch 8, 5 epochs. An original held-out font stands in for
a new writer: adaptation lines are training-book text in that font, the check is 1000
test-book lines in the same font, the other held-out font and the training fonts. Greedy.

| writer stand-in | lines | same font | other held-out font | training fonts |
|---|---|---|---|---|
| `reeniebeanie` | before | 23.6 % | 1.2 % | 1.8 % |
| | 50 | 5.2 % / 14.2 % | 2.4 % / 1.6 % | 3.1 % / 2.5 % |
| | 200 | **3.2 %** / 6.3 % | 2.2 % / 1.9 % | 3.6 % / 3.0 % |
| `kalam` | before | 1.2 % | 23.6 % | 1.8 % |
| | 50 | 1.2 % / 1.2 % | 24.5 % / 24.0 % | 2.8 % / 2.2 % |
| | 200 | **0.9 %** / 1.0 % | 25.7 % / 25.1 % | 2.6 % / 2.3 % |

(adapter rank 32 / output layer only). Encoder forward 8–10 ms per line (PyTorch eager,
host busy), head training 1.1 s for 200 lines × 5 epochs with the adapter, a full
fine-tune 35–42 ms per line. A small head still learns a far hand quickly (`reeniebeanie`
23.6 → 3.2 %, against 50 → 8 % with the 7-font baseline), at the cost of some drift on
other hands; keep the base head to fall back to, as FR-009 says.

### Fine-tuning on real ink from one writer, same writer (`real`, `finetune`, `realeval`)

Not released: no run kept the model as good on other hands. The data is 756 prompted
samples from one writer in three sessions. It is split by session (`glassine-train real`):
train 549 (97 of them single characters), dev 91 (the end blocks of the second
session), test 116 words (the last session, no text shared with train or dev). Every run
starts from model 2 and mixes a fixed real share into each batch (point-level
augmentation on the real ink). It keeps the best dev step or the average of the five
best, whichever is better on dev. The test session is read by the recognizer program
itself, ink only and with words; "+ fresh head" is model 2 with an adaptation head
trained only on the train and dev sessions. Synthetic dev = 300 lines in the training
fonts; held-out fonts = 1000 lines each, greedy.

| run | real share | words CER / WER | top 3 | synthetic dev | new held-out fonts | original held-out fonts |
|---|---|---|---|---|---|---|
| model 2 | – | 37.0 / 112 % | 19 % | 3.8 % | 5.0 % | 12.0 % |
| model 2 + fresh head | – | 8.2 / 33 % | 78 % | – | – | – |
| `s20` | 80 % | 5.3 / 28 % | 82 % | 5.1 % | 6.8 % | 13.1 % |
| `s20h` (+ own-letter font) | 80 % | 5.1 / 28 % | 82 % | 5.9 % | 8.0 % | 15.9 % |
| `s20adv` (domain head) | 80 % | 6.1 / 32 % | 79 % | 5.0 % | – | – |
| `s20l2` (L2-SP 0.05) | 80 % | 7.6 / 36 % | 77 % | 5.2 % | 7.6 % | 15.1 % |
| `s50` | 50 % | 7.7 / 38 % | 78 % | 4.6 % | 5.7 % | 12.2 % |
| `s50t` (half `real1` synth) | 50 % | 5.7 / 33 % | 85 % | 4.3 % | 5.7 % | 13.8 % |
| `s50l2` (L2-SP 0.05) | 50 % | 10.1 / 49 % | 72 % | 4.7 % | 6.1 % | 12.6 % |
| `s100h` (own-letter font, no real ink) | 0 % | 18.1 / 64 % | 53 % | 5.6 % | – | – |
| `s100t` (`real1` synth, no real ink) | 0 % | 29.6 / 99 % | 33 % | 4.3 % | – | – |

(one seed each; s50h and s50adv are close to s50. New held-out fonts = mean over the
12 new print and script fonts, test text.)

- Real ink is what helps this writer: `s20` beats model 2 + fresh head on the test
  session (paired bootstrap over samples: 99.7 %, CER lower by 0.8–5.0 points). A
  tuned generator or the writer's letters spliced as a font help a little, never alone.
- Every run is worse than model 2 on the held-out fonts (other hands), more so with more
  real ink: one writer's habits are learned too, not only what handwriting has in common.
  The release gate (beat model 2 + fresh head on real ink AND no held-out group more than
  about 0.5 points worse) is not met, so model 2 stays the base; the per-writer head
  already closes most of the gap for this writer.
- The test session stays the test set across releases: `glassine-train real STORE --keep-test
  OLD/split.json` keeps exactly the earlier split's test ids (all must be in the new store),
  leaves out any other sample written in their sessions, and splits the rest by whole
  sessions (dev: the newest sessions until 80 samples), so a new pull only adds sessions.
- Model files now record when their training data ends (`--trained-until`, written from
  the split), so on-device adaptation never re-learns the base model's own samples.
- To ship real ink in the base: samples from several writers, so one hand's quirks
  average out; the same split and gate apply.

### Model 4: reading the size (FR-018, FR-020; not released)

**Recipe.** `ink4` train (600 k lines) featurized with `--ref mix` (six inputs: lengths in
reference heights, y above the baseline, the ink's height); the characters of `CHARS4` (82,
with `@ & + # % _ = $`); model 2's architecture (1.41 M parameters) and optimiser (AdamW,
one-cycle to 3e-3, batch 32, slant-and-stretch augmentation, the size column moved with the
stretch); best of `dev.xh` and `dev_kinds.xh` (mean CER). Synthetic ink only: no real ink and
no contributor's letter model in the base. 32 k steps took 209 minutes on 9 host threads; a
run of model 2's 16 k steps (139 minutes) ends at 9.7 % on the selection sets against 7.5 %.

```sh
uv run glassine-train train --run m4long --data train4-ref --val val.xh --select dev.xh,dev_kinds.xh \
    --augment --hidden 176 --minutes 560 --steps-per-s 0.95238 --threads 9   # 32 000 steps
```

**How it is read.** Every number below is the recognizer program's own reading (`inkread`,
`realeval`): the model file version 2 (`onnx2ghm`), the size weighing off (a version 2 model is
not weighed; see below), each guide owning its line from two thirds above it to a third below.
Synthetic lines are read as a person's samples: each line at a fixed letter height in a
writing area of its own line height, the guide on its baseline, with the letter height known
or the line alone. Modes: ink only (`read … raw`), the character model
alone (`read … chars`), words, words + character model (the default of a field).

**Real ink, same writer** (the fixed test session, 116 words; a fresh head trained on the
training sessions, the person's letters from them; CER / WER, top 3):

| | model 2 | model 4 | model 4 − model 2, CER (95 %) |
|---|---|---|---|
| ink only | 12.5 % / 63 %, 59 % | 10.1 % / 57 %, 62 % | −4.8 to +0.0 |
| words | 7.9 % / 34 %, 78 % | 4.4 % / 20 %, 85 % | −6.0 to −0.9 |
| character model | 5.0 % / 23 %, 79 % | 3.2 % / 17 %, 85 % | −3.9 to +0.1 |
| words + character model | 5.2 % / 22 %, 80 % | 2.4 % / 13 %, 87 % | −5.1 to −0.8 |
| without a head, words + character model | 32.9 % | 29.7 % | −7.6 to +1.3 |
| a new person (no head, no letters), words + character model | 32.9 % | 21.1 % | −16.4 to −7.1 |
| … words | 37.0 % | 28.2 % | −13.2 to −4.3 |

(paired bootstrap over the samples.) All choices (32 k steps, no weighing, no letter-model
lines in the head) were made on the synthetic dev sets and the real dev session (65); the
numbers above are the final configuration's reading of the test session. One later variant
(`m4fix`, below) was also chosen on dev and read the test session once.

Without a head, the person's letters as the reference make model 4 read worse, not better: 29.7 %
with them against 21.1 % without on the test session, 30.1 % against 22.0 % on dev (model 2 does
not use them for its features). On synthetic ink the letter height helps (tables below), so the
measured height of a real person's small letters and the generator's x-height probably do not
mean the same length; with a head the difference is learned away. A sweep on the real dev
session (no head, words + character model) with the reference scaled to a multiple of the
measured height: 0.6× 48.8 %, 0.8× 35.4 %, 1.0× 29.4 %, 1.25× 23.1 %, 1.5× 18.1 %, 1.75× 19.7 %,
2× 18.5 %, 2.5× 21.1 %, 3× 23.6 %. The model wants 1.5 to 2 times the measured height; the dev
and test words are about as large as the training sessions' (0.86 and 0.91 of them), so it is
not the size drifting. On synthetic ink the recognizer's way of measuring the small letters
gives 0.74 to 0.94 of the generator's x-height, so the two definitions differ. The fix belongs
in training (references measured the way the recognizer measures them, then featurize and
train again), not in a factor tuned on one writer. Not done.

The `s20` fine-tune (Fine-tuning, above; trained on this writer's ink) reads the same session
at 4.0 % without a head and 2.1 % with a fresh one (model 4: 2.4 %, difference −1.1 to +1.8):
on this writer a head on model 4 reaches what fine-tuning on the writer did, without the loss
on other hands. The writer is also the one the generator's timing and spacing were fitted to,
so every real number here is one hand.

**Synthetic held-out ink** (the three held-out groups pooled: model 2's two held-out fonts and
its twelve new ones, model 2's test texts and `test_kinds`; CER, words + character model):

| | model 2, letter height known / line alone | model 4, known / line alone |
|---|---|---|
| test book (1500 lines) | 31.2 / 31.3 % | 5.2 / 9.7 % |
| random words | 37.1 / 37.0 % | 6.0 / 11.0 % |
| numbers and punctuation | 51.8 / 51.5 % | 23.3 / 31.3 % |
| random letters (ink only) | 47.7 / 47.5 % | 29.4 / 38.3 % |
| names | 45.9 / 46.1 % | 13.6 / 23.4 % |
| codes (dates, emails, numbers, parts, phones, plates, postcodes, times, web) | 52.9 / 52.9 % | 14.5 / 22.5 % |
| lone characters | 57.6 / 57.6 % | 23.1 / 31.5 % |

Every set and every code kind is lower in every mode (paired bootstrap, 95 %: 15 to 37 points
with the letter height known, 5 to 30 with the line alone). Per held-out font, greedy, letter
height known: 4.5 to 10.6 %, `reeniebeanie` 22.1 % (model 2: 28 to 39 %, 52.9 %). **This
ink is model 4's own generator**, which model 2 never saw (on its own generator's ink model 2
reads the held-out fonts at 5 to 12 %), so the size of the gap overstates the gain; the real
session above is the fair comparison.

**Lone characters at their size** (`test_lone`: 40 of each per group, top reading right /
read as the other case, words + character model, letter height known):

| | `.` | `,` | `o` | `O` | `c` | `C` | `s` | `S` |
|---|---|---|---|---|---|---|---|---|
| model 2 (weighed) | 79 / – | 94 / – | 43 / 0 | 2 / 56 | 57 / 0 | 24 / 47 | 48 / 0 | 11 / 70 |
| model 4 | 82 / – | 91 / – | 92 / 0 | 12 / 12 | 78 / 0 | 62 / 11 | 86 / 3 | 78 / 3 |

The case of a lone letter is now mostly right. A lone `O` is read as the digit `0` (87 of 120;
model 2 read it as `o`): alone the two are one shape, so it comes down to the field. Before the
guide rule above, a lone comma read right 40 % of the time (as `'`, a line too high) and a dot
67 %.

**Size weighing.** Weighing model 4's reading by the size as well (FR-017) helps only the lone
dot (100 % against 82 % right): lone characters 13 to 19 points worse weighed (95 %), the line
kinds 0.1 to 0.2 worse; on the real dev session 2.3 % weighed, 2.1 % unweighed (the 16 k-step
run). The recognizer weighs only version 1 models.

**Regressions.**

- Letters read as digits inside words: 53 of 3897 synthetic texts without digits (1.4 %;
  model 2: 13) with the letter height known, 73 with the line alone (model 2: 14), mostly
  capitals (`O→0`, `I→1`, `G→6`, `I→4`, `z→2`); on the real test session 2 of 116 (model 2: 0).
- A lone `O` (above).
- With the line alone (no letters of the person yet) model 4 reads 4 to 10 points worse than
  with the letter height, but still far below model 2.

**One other writer** (19 prompted single words of a second person, read as a new person: no
head, no letters; an evaluation set only, never trained on; CER, 95 % interval of the
difference). 19 words is a small set:

| | model 2 | model 4 | model 4 − model 2 |
|---|---|---|---|
| ink only | 54.9 % | 36.4 % | −25.5 to −11.8 |
| character model | 54.9 % | 27.7 % | −37.1 to −17.4 |
| words | 54.4 % | 32.5 % | −30.4 to −13.7 |
| words + character model | 56.3 % | 27.7 % | −37.9 to −20.0 |

Top reading right: model 4 16 %, model 2 5 %. No letter was read as a digit in any word.

**Trying to fix the digit regressions** (`m4fix`, not adopted). Three things in the training
text pushed letters towards digits: the `uniform` kind (31.6 % of the tokens that mix letters
and digits) drew each character from letters and digits alike, so most of its tokens mixed
them; lone digits came more often than lone capitals (`0` 545 against `O` 318); and over the
whole set `0` came 2.4 times as often as `O` and `6` 2.3 times as often as `G`. The fonts'
`O` is a little wider than their `0` (width / height 0.81 against 0.75), but the held-out
fonts draw `O` narrow (0.57 to 0.67), so there a lone `O` and `0` are close to one shape. The
change: a `uniform` token is all letters or all digits (a fifth digits), lone capitals come as
often as lone digits (`lines.py`); 36 k new lone and 18 k new uniform lines replaced those
kinds in the set (`gen4 --kinds`, `mergeset`), and m4long went on for 8 k steps (one-cycle
to 1e-3). A control ran the same 8 k steps on the old set (`m4ctl`) to tell the text from
the extra training.

| | m4long | control | m4fix | model 2 |
|---|---|---|---|---|
| letters read as digits in words, letter height known | 53 / 3897 | 49 | 41 | 13 |
| … line alone | 73 | 68 | 43 | 14 |
| … ink only, letter height known / line alone | 106 / 152 | 97 / 134 | 78 / 91 | 23 / 25 |
| lone `O` read as `0`, letter height known / line alone (of 120) | 87 / 49 | 82 / 51 | 80 / 43 | 34 / 83 |
| real test session, letters read as digits in words (of 116) | 2 | | 1 | 0 |

(words + character model unless noted.) Reduced, not fixed: about a fifth fewer with the
letter height, two fifths fewer with the line alone, still three times model 2, and a lone `O`
is still read as `0`. Against m4long, m4fix is lower or level on every synthetic set (95 %),
but against the control it costs on numbers and punctuation (+0.4 to +1.6 points) and reads
a lone `S` as `s` more often (17 % against 5 %). On the real dev session the two were level
(m4fix 2.1 %, m4long 1.9 %, words + character model, fresh head), so m4fix read the test
session once:

| real test session (116 words) | m4long | m4fix | m4fix − m4long (95 %) |
|---|---|---|---|
| fresh head, words + character model | 2.4 % | 3.4 % | +0.1 to +1.9 |
| fresh head, character model | 3.2 % | 4.1 % | +0.1 to +1.8 |
| fresh head, words | 4.4 % | 5.3 % | −0.8 to +2.7 |
| new person, words + character model | 21.1 % | 21.5 % | −1.8 to +2.6 |
| new person, words | 28.2 % | 27.0 % | −3.5 to +1.1 |
| the other writer, words + character model | 27.7 % | 24.3 % | −9.6 to +1.9 |

With a fresh head m4fix is worse on this writer and does not pass the release gate against
model 2 (−4.1 to +0.3 points), so m4long stays the candidate. The head is trained with one
seed; the bootstrap covers the words, not the head's training. What is left of `O` against `0`
is probably for the field's context or the decoder rather than more rebalancing of the text.

**A head with letter-model lines.** Adding 400 lines written in the person's letter model
(`gen4-letters`) to the training sessions of the fresh head makes it worse (real dev, words +
character model 2.3 → 3.0 %), so the head learns from the person's real samples only.

**Size and speed** (x86 host, one core, interleaved with model 2, the machine busy with
training, so compare the two rather than the absolute times):

| | model 2 | model 4 |
|---|---|---|
| parameters | 1.41 M | 1.41 M |
| ONNX int8 | 1.45 MB | 1.45 MB; held-out CER 13.7 % (fp32 13.2 %) |
| network, one line (ink4 held-out text), int8 | 5.3 ms, 686 points | 3.3 ms, 427 points |
| recognizer program, prose line, words + character model | 32 ms | 25 ms |
| … line of random words | 45 ms | 34 ms |

Lengths in reference heights resample a line to 38 % fewer points, so the whole read is
21 to 24 % faster (27 to 29 % ink only). By the same ratio the tablet's 243 ms per prose line
and 88 ms per word (above) would be about 190 ms and 67 ms; not measured on the device.

**Against the release gate** (Fine-tuning, above): model 4 beats model 2 + fresh head on the
real session (words + character model 2.4 % against 5.2 %, 95 % interval of the difference
−5.1 to −0.8 points), and no held-out group is worse (all three far lower; read in ink4's
versions of model 2's held-out fonts and texts, since model 2's own test ink is kept only as
model 2's features). Not released: the device run (time, the samples app, a person writing)
is still to do.

## What limits accuracy

1. **Generator realism.** Fonts give tidy, repeatable glyphs laid out letter by letter;
   real hands vary letter to letter, join untidily (script fonts here are not joined into
   one pen-down stroke) and use stroke orders the generator does not have. Synthetic
   held-out fonts are only a proxy. On one writer's real ink model 2 reads 37 % CER
   with words (8 % with a fresh per-writer head).
2. **Far hands.** A font unlike all 158 (`reeniebeanie`) still reads at 24 % greedy; the
   per-writer head fixes it in a few hundred lines.
3. **Capitals and context-free strings.** Random letters (16 % on training fonts) and
   numbers are the weak spot, and the lexicon cannot help there.
4. **Spaces.** Missing and extra spaces are the most common error on the new held-out
   fonts; the generator's word gaps are one distribution for all fonts.

## Next steps

- **Open requirement for the next base model (spec 001 FR-018): absolute size.** The
  features scale every input by its own core height (step 2 above), so a lone `.` or `,`
  is blown up to letter size and `o`/`O`, `c`/`C`, `s`/`S` written alone look alike; the
  model never sees how big the ink is. The recognizer makes up for part of it after the
  model (`docs/recognizer.md`, Size), but the next base model must keep the size itself:
  scale by a reference height (the writer's letter height when known, else the writing
  area's line height) instead of the ink's own, give the model the size (the ink's height
  in reference heights) as a feature of its own, and train with single characters and
  punctuation at their true size relative to the line (synthetic lines with lone marks and
  letters, and real samples' area line). This changes the features and so the model file:
  a new ghm version that says how its input is scaled, read beside version 1.
- **Open requirement for the next base model (spec 001 FR-020): training text with real
  character statistics.** The text above mixes random word sequences, numbers and codes (1
  in 12) and random letter strings with every letter equally likely (1 in 12). With CTC,
  each frame's output is independent of the earlier ones, so whatever the model knows about
  which character follows which it has to learn from the text, and uniform strings teach it
  that any letter, or a digit, is as likely as any other inside a word (one writer's "light"
  read as "lig4t"). The recognizer's character model (`charlm.py`, below) makes up for it
  after the model; the next base model's text should:
  - replace the uniform letter strings with pseudo-words sampled from a character model of
    the language (the one `charlm.py` builds can serve): not words of the word list, so no
    word is learned by heart, but with real letter sequences; uniform strings at most a
    small remainder;
  - generate numbers and codes as line kinds of their own, as they are written: ordinals
    and units (4th, 10:30, 5 km), dates, phone numbers, postcodes, number plates and part
    numbers (B-XY 123, R2D2), email and web addresses, so a digit stands next to letters
    only where it really does;
  - take names from public name lists.
  No short continuation fine-tune of model 2 for this: the fix is the next base model,
  with the character model in the decoder until then.
- **Option for the next base model: an output-dependent decoder** (attention, or CTC with
  attention), measured against CTC with the character model in the decoder. Not required.
- **Nice to have: delayed strokes as people write them.** Measured on one writer's
  prompted words: about a third of the words have a delayed stroke; i and j dots are
  delayed about 80 % of the time, bars (t, f and others) 5 to 20 %; several are added
  mostly right to left, about 10 % in the middle of the word, after a pause of about 0.17 s
  against 0.11 s between letters. The generator delays only dots and crosses of i, j, t and
  f, with chance 0.5 (`delay`), left to right, at the word's end, and the `hand` sets
  render with `delay=0.0`. No accuracy problem has been measured from this, so it comes
  last.
- **The generator's dynamics from real ink, broadly.** The `real1` preset copies one
  writer's geometry; instead fit a timing model (for example sigma-lognormal) to real ink of
  many writers and sample its timing and dynamics, keeping the shapes broad (the fonts),
  not one writer's.
- **A test set of other real writers' ink** as soon as contributions with contributor numbers give one:
  the synthetic held-out fonts only stand in for other hands; report the base model's
  real-ink error per writer.
- **Spacing as people write it.** The generator spaces letters evenly by their boxes. Use
  the letter model's ink profiles and pair gaps (`../docs/recognizer.md`, letter spacing)
  instead, drawn from the spread of real gaps (one writer's varies by about 0.22
  x-heights), not the mean, so touching and overlapping letters, crossings into the next
  letter and varying word gaps appear in training; one writer's spacing is at most one
  style among many.
- **Flatten part of the text.** Text drawn only with the language's letter statistics
  teaches the CTC model a character model of its own, which the decoder's character
  model then counts twice, and real codes and names read worse. Draw part of the
  pseudo-words at a higher temperature beside the plain ones and keep the small share of
  uniform strings; check codes, names and unusual words separately against model 2 with
  the character model. Pseudo-words come only from the training books, never the test
  book.
- **Contributors as synthetic writers.** A contributor's letter model (versions in
  proportion of use, spacing, fitted timing) describes one writer completely; the
  generator can write any text in that style. With contributions from several people,
  each becomes one more writer for the base model, where the contribution's licence
  covers derived models; a contributor in the real test set is never one of the
  generator's writers.
- Real ink from more writers (`../docs/samples.md`): one writer improves that writer
  but costs other hands (see above).
- Joined script in the generator: connect letters within a word into pen-down strokes
  where the font's letters meet, so script reads like it is written; measure on a joined
  held-out set and on real ink, not on the generator alone.
- Capitals: more capital-heavy training text, and a check whether ornamental capitals
  from formal scripts help or hurt.
- Decoder: context from the text before the cursor, n-best alternatives for the keyboard
  (the decoder already returns them), and a way to turn the word terms off for numeric
  fields; port `lexicon.py` and check its speed on the device.
- Run the int8 ONNX model on the reference device for real latency and head-training
  times (not measured here).
- Prompted alignment (FR-007): CTC forced alignment gives stroke spans per character; the
  generator's `spans` can test it.

## Licence

Code GPL-2.0-or-later. The fonts are OFL or Apache 2.0 (their licence travels with them
in `../synth/build/fonts`); the texts are public domain. The lexicon files built by
`lexicon` are CC BY-SA 3.0 (see above). A model trained by this recipe
contains neither, but its recipe, sources and licences are this file.
