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

**On one writer's real ink** (prompted samples, mostly single words, read by the recognizer
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

### Fine-tuning on real ink from one writer (`real`, `finetune`, `realeval`)

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
