// SPDX-License-Identifier: GPL-2.0-or-later
// Cutting a stroke shared by several letters (a joined word written in one go) into one part
// per letter, where the parts best match those letters' reference strokes (spec 003 FR-007,
// FR-036): neatening cuts by the glyphs it pulls towards, the letter model by the variants
// built from samples written apart. The cut is a function, so another way of matching can
// take its place.
#ifndef HW_CUT_H
#define HW_CUT_H

#include <stddef.h>

#define HW_CUT_N 24             /* points a part and a reference stroke are compared at */
#define HW_CUT_LEN 0.25         /* the weight of the log of the lengths' ratio */
#define HW_CUT_PTS 96           /* hw_cut_shape cuts a stroke at most at this many of its points, */
#define HW_CUT_PER 12           /* or this many per letter */

/* a reference stroke: x-heights, y up, resampled to HW_CUT_N points by arc length; len its
 * length */
struct hw_cut_ref { double x[HW_CUT_N], y[HW_CUT_N], len; };
void hw_cut_ref_make(const double *x, const double *y, size_t n, struct hw_cut_ref *r);

/* the cumulative arc length of a polyline into cum (n of them), the whole length returned */
double hw_cut_arc(const double *x, const double *y, size_t n, double *cum);

/* how far points [a, b) of a stroke (x, y: sample units, y down; cum its hw_cut_arc) are from
 * reference g at x-height xh: centred RMS in x-heights plus HW_CUT_LEN times the log of the
 * lengths' ratio; INFINITY for fewer than 2 points */
double hw_cut_cost(const double *x, const double *y, size_t a, size_t b, const double *cum, const struct hw_cut_ref *g,
		   double xh);

struct hw_aligned;

/* What a cut may know of the stroke besides its shape: the point aligner's run over its
 * line (aligner.h), the frame of each of the stroke's m points there, and each letter's
 * class (0: the aligner doesn't know it). NULL: nothing. */
struct hw_cut_ctx {
	const struct hw_aligned *a;
	const int *frame;               /* m */
	const int *cls;                 /* R */
};

/* A cut of stroke (x, y, m points, sample units, y down, x-height xh) among R letters in
 * writing order, letter r with its candidate references refs[off[r]] .. refs[off[r + 1] - 1]:
 * at[0] = 0 < at[1] < ... < at[R] = m, letter r's part the points [at[r], at[r + 1]), each at
 * least 2 points; which[r] the candidate its part matched (an index into refs); *cost the
 * total (hw_cut_cost of each part against that candidate). ctx may be NULL. 0 done, 1 no cut
 * (too few points, a letter without candidates), -1 out of memory. Deterministic: the first
 * of equal costs is kept. */
typedef int (*hw_cut_fn)(const double *x, const double *y, size_t m, double xh, size_t R, const struct hw_cut_ref *refs,
			 const size_t *off, size_t *at, int *which, double *cost, const struct hw_cut_ctx *ctx);

/* the shape cut: each part against its letter's references (hw_cut_cost, the best
 * candidate), the least total, by dynamic programming over the cut points (a stroke of more
 * than HW_CUT_PTS points, HW_CUT_PER per letter, at every step-th of them: the parts end at
 * points of the stroke all the same) */
int hw_cut_shape(const double *x, const double *y, size_t m, double xh, size_t R, const struct hw_cut_ref *refs,
		 const size_t *off, size_t *at, int *which, double *cost, const struct hw_cut_ctx *ctx);

/* the aligner's cut: where the aligner's best path through the letters' classes over the
 * stroke's frames passes from one letter to the next (aligner.h hw_aligner_split), each
 * part's candidate the one its shape matches best; the shape cut where the context has no
 * aligner, a letter has no class, or a part would be shorter than 2 points */
int hw_cut_aligner(const double *x, const double *y, size_t m, double xh, size_t R, const struct hw_cut_ref *refs,
		   const size_t *off, size_t *at, int *which, double *cost, const struct hw_cut_ctx *ctx);

#endif
