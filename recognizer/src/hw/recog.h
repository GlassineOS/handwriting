// SPDX-License-Identifier: GPL-2.0-or-later
// Recognition on top of the engine: base models per language (with the person's
// adaptation on top), readings with candidates, and the alignment of text to strokes.
#ifndef HW_RECOG_H
#define HW_RECOG_H

#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>
#include "ctc.h"
#include "featurize.h"
#include "local.h"
#include "model.h"

struct hw_lang {
	char lang[36];
	int loaded;                     /* 1: model ok, -1: none or unusable */
	struct hw_model model;
	struct hw_head adapted;         /* the person's head, when there is one (rank > 0 or not) */
	int has_adapted;
	struct stat adapt_st;           /* the adaptation file as last read */
	double base_temp;               /* the base model's calibration, 0: none */
	char dir[4300];                 /* the directory of the file read */
	struct hw_resolved res;         /* which file, as it was when read (local.h) */
};

/* The model directory: $GLASSINE_HANDWRITING_MODELS or /usr/share/glassine/handwriting. */
const char *hw_models_dir(void);
/* The language's model (loaded on first use, adaptation re-read when its file changed);
 * NULL when there is no usable model for it. */
struct hw_lang *hw_lang_get(const char *lang);
/* 1 when lang reads with the person's own recognizer (local.h) */
int hw_lang_local(const char *lang);
const struct hw_head *hw_lang_head(const struct hw_lang *l);
void hw_lang_forget_all(void);

/* One run of the engine over some strokes. */
struct hw_run {
	long rows, T;
	int C, down;                    /* classes; input rows per frame */
	int nfeat;                      /* features per row */
	float *feats, *logp, *frames;
	struct hw_origin *org;
	size_t nstrokes;
	const size_t *npoints;          /* points per stroke (for "whole stroke") */
	/* the size (size.h): logp weighed by it, the ink's own in logp0; NULL without */
	float *logp0;
	struct hw_size_info *size;
	/* the calibrated frames readings are searched and scored on (calib.h; weighed by the
	 * size and leaned like logp); NULL: logp itself (no calibration). Fits use logp. */
	float *dec;
};

/* the frames a reading is searched and scored on */
static inline const float *hw_run_dec(const struct hw_run *r) { return r->dec ? r->dec : r->logp; }
/* the temperature the language's head in use reads with (calib.h): the adaptation's own, the
 * base model's for the base head; 1 when it has none */
double hw_lang_temp(const struct hw_lang *l);

int hw_run(struct hw_lang *l, const struct hw_stroke *s, size_t ns, struct hw_run *r);
/* hw_run with the ink's reference height h and baseline (the ink's y of it, NAN: none), in
 * the ink's units, for a model that reads them (ghm version 2); without one (h <= 0) the
 * ink's own core height. A version 1 model ignores them. */
int hw_run_at(struct hw_lang *l, const struct hw_stroke *s, size_t ns, double h, double base, struct hw_run *r);
/* on: hw_run keeps its last run and gives a copy for the same strokes, language and
 * adaptation (a server answering several requests on one piece of writing) */
void hw_run_cache(int on);
void hw_run_free(struct hw_run *r);

/* Per character of a text, its place in the strokes (from the forced alignment of the
 * text's characters that the model has; a space or a character it lacks gets none). */
struct hw_charspan {
	unsigned cp;
	long r0, r1;                    /* feature rows, -1: none */
	double fit;                     /* 0..1: how well the strokes fit (-1: not aligned) */
	int top;                        /* the class read most surely over its own frames (spec 003
					 * FR-037: neatening checks it), 0 none */
};

/* Aligns text (code points) to the run. Returns the number of characters, or -1 when
 * the strokes are too short for the text, -2 when out of memory. *out malloc'd. */
long hw_align(const struct hw_lang *l, const struct hw_run *r, const unsigned *cp, size_t n,
	      struct hw_charspan **out);

/* "S" (a whole stroke) or "S:A-B" (points A to B of stroke S), comma-separated, per
 * character; ids maps stroke index -> the client's stroke number. Appends to buf. */
void hw_span_text(const struct hw_run *r, const struct hw_charspan *c, const int *ids, char **buf,
		  size_t *len, size_t *cap);
/* The strokes of a character as the sample format has them: indices, -1 ended. */
int *hw_span_strokes(const struct hw_run *r, const struct hw_charspan *c, size_t nchars, size_t k);

/* Candidates: up to n readings, best first. */
#define HW_READ_MAX 16
/* the ink's best reading, another reading of the ink, a word of the list one edit from a
 * reading, a reading found with the words guiding the search, one found with the character
 * model weighing the search */
enum { HW_FROM_INK, HW_FROM_BEAM, HW_FROM_WORDS, HW_FROM_GUIDED, HW_FROM_CHARS };
struct hw_reading {
	int n;
	struct {
		unsigned cp[HW_MAX_LABELS];
		int len;
		double score;           /* log-probability of the text given the ink */
		int from;
	} c[HW_READ_MAX];
};
/* the ink's own readings (prefix beam search), up to n (at most 8) */
int hw_read(const struct hw_lang *l, const struct hw_run *r, int n, struct hw_reading *out);

/* The words around a reading: the language's words and the two before it (from the text
 * before the cursor). */
struct hw_words;
struct hw_chars;
struct hw_context {
	const struct hw_words *w;       /* NULL: no words */
	long prev2, prev1;
	const struct hw_chars *ch;      /* the character model, NULL: none */
	uint64_t cctx;                  /* its context: the text before */
};
/* the words' context (and no character model) */
void hw_context_of(const struct hw_words *w, const char *before, struct hw_context *x);
/* the reading's context: words (w, may be NULL) and the character model (ch, may be NULL);
 * returns x, or NULL when neither (the ink's reading alone) */
struct hw_context *hw_context_make(const struct hw_words *w, const struct hw_chars *ch, const char *before,
				   struct hw_context *x);
/* readings ranked with the words and the character model: the ink's readings, those of a
 * search the character model weighs (x->ch) and of one the words guide (x->w), re-scored by
 * the words they make after the words before and by the character model, plus up to two
 * words of the list one edit from a reading that isn't one (never first); the ink's best
 * reading stays first unless another one wins by a clear margin and it isn't all words,
 * and it is among the first two. x NULL: hw_read. */
int hw_read_words(const struct hw_lang *l, const struct hw_run *r, const struct hw_context *x, int n,
		  struct hw_reading *out);

/* log P(text | ink) by the run (CTC forward); -INFINITY when the model lacks a character */
double hw_text_logp(const struct hw_lang *l, const struct hw_run *r, const unsigned *cp, int n);

/* Character error rate helpers */
size_t hw_edit(const unsigned *a, size_t na, const unsigned *b, size_t nb);

#endif
