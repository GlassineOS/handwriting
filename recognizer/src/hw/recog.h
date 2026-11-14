// SPDX-License-Identifier: GPL-2.0-or-later
// Recognition on top of the engine: base models per language (with the person's
// adaptation on top), readings with candidates, and the alignment of text to strokes.
#ifndef HW_RECOG_H
#define HW_RECOG_H

#include <stddef.h>
#include <sys/stat.h>
#include "ctc.h"
#include "featurize.h"
#include "model.h"

struct hw_lang {
	char lang[36];
	int loaded;                     /* 1: model ok, -1: none or unusable */
	struct hw_model model;
	struct hw_head adapted;         /* the person's head, when there is one (rank > 0 or not) */
	int has_adapted;
	struct stat adapt_st;           /* the adaptation file as last read */
	char dir[4096];
};

/* The model directory: $GLASSINE_HANDWRITING_MODELS or /usr/share/glassine/handwriting. */
const char *hw_models_dir(void);
/* The language's model (loaded on first use, adaptation re-read when its file changed);
 * NULL when there is no usable model for it. */
struct hw_lang *hw_lang_get(const char *lang);
const struct hw_head *hw_lang_head(const struct hw_lang *l);
void hw_lang_forget_all(void);

/* One run of the engine over some strokes. */
struct hw_run {
	long rows, T;
	int C, down;                    /* classes; input rows per frame */
	float *feats, *logp, *frames;
	struct hw_origin *org;
	size_t nstrokes;
	const size_t *npoints;          /* points per stroke (for "whole stroke") */
};

int hw_run(struct hw_lang *l, const struct hw_stroke *s, size_t ns, struct hw_run *r);
void hw_run_free(struct hw_run *r);

/* Per character of a text, its place in the strokes (from the forced alignment of the
 * text's characters that the model has; a space or a character it lacks gets none). */
struct hw_charspan {
	unsigned cp;
	long r0, r1;                    /* feature rows, -1: none */
	double fit;                     /* 0..1: how well the strokes fit (-1: not aligned) */
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
struct hw_reading {
	int n;
	struct {
		unsigned cp[HW_MAX_LABELS];
		int len;
		double score;
	} c[8];
};
int hw_read(const struct hw_lang *l, const struct hw_run *r, int n, struct hw_reading *out);

/* Character error rate helpers */
size_t hw_edit(const unsigned *a, size_t na, const unsigned *b, size_t nb);

#endif
