// SPDX-License-Identifier: GPL-2.0-or-later
// The point aligner (docs/recognizer.md "Ownership"): a small model in the base model's
// format and shape (model.h; train/src/glassine_train/aligner.py PointAligner) that gives
// every frame of a line a log-probability per character, class 0 a blank only ever between
// two equal letters in a row. Decoded through the chain of a known text it says which
// character each point of the ink belongs to: where a stroke shared by several letters
// passes from one to the next (a hw_cut_fn, cut.h), and which letter a stroke written after
// the word (a dot, a bar) belongs to. Optional: without LANG/aligner.ghm nothing changes.
#ifndef HW_ALIGNER_H
#define HW_ALIGNER_H

#include <stddef.h>
#include "model.h"
#include "sample.h"

#define HW_ALIGNER_FILE "aligner.ghm"
/* the log-penalty of a stroke written after a later letter (decode with delayed strokes);
 * picked on synthetic data (train/README.md) */
#define HW_ALIGNER_DELAY -1.0

/* The aligner of the models' directory for lang (loaded on first use); NULL when there is
 * none or it can't be used. GLASSINE_HANDWRITING_ALIGNER=off: none. */
const struct hw_model *hw_aligner_get(const char *lang);

/* One run over a line's strokes (those with skip[j] set left out; skip may be NULL). */
struct hw_aligned {
	const struct hw_model *m;
	long T;                         /* frames */
	int C;                          /* classes */
	float *logp;                    /* T x C */
	size_t n;                       /* strokes */
	int **frame;                    /* per stroke, per point: its frame; NULL: not run */
	size_t *np;                     /* per stroke: its points (0: not run) */
};

/* 0, 1 no ink, -1 out of memory */
int hw_aligner_run(const struct hw_model *m, const struct hw_ink *ink, size_t n, const unsigned char *skip,
		   struct hw_aligned *a);
void hw_aligned_free(struct hw_aligned *a);

/* the class of a character, 0 none */
int hw_aligner_class(const struct hw_aligned *a, unsigned cp);

/* Each point's character (own[j][i], index into cp; -1 none) by the text alone: the best
 * path through the chain of cp's characters the model has, in writing order, where a whole
 * stroke after the first may also be a delayed one (one character already passed, of its
 * word or the word before) at log-penalty pen. Strokes not run are left as they are.
 * 0, 1 no path (too few frames, no character), -1 out of memory. */
int hw_aligner_label(const struct hw_aligned *a, const unsigned *cp, size_t nc, double pen, int **own);

/* The best monotone split of frames [f0, f1] over the classes cls[0..R) in order (each at
 * least one frame, a blank frame required between two equal classes): per frame the index
 * of its class, into lab (f1 - f0 + 1 of them). 0, 1 too few frames, -1 out of memory. */
int hw_aligner_split(const struct hw_aligned *a, long f0, long f1, const int *cls, size_t R, int *lab);

#endif
