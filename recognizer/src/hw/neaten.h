// SPDX-License-Identifier: GPL-2.0-or-later
// Neatening (spec 003 FR-013 to FR-017, docs/recognizer.md "Neatening"): ink in, ink out,
// the same strokes in the same order with every point moved: line-level corrections
// (straight baseline, steady slant, even size and spacing) and each readable character
// that the letter model covers pulled towards its nearest variant, both scaled by a
// strength from 0 to 1. Only x and y change; deterministic.
#ifndef HW_NEATEN_H
#define HW_NEATEN_H

#include <stddef.h>
#include "letters.h"
#include "sample.h"

/* what was done to a stroke */
enum { HW_NEAT_KEPT, HW_NEAT_LINE, HW_NEAT_PULLED };

/* a character gets the pull with at least this fit, the ink is writing (line-level
 * corrections) with at least NEAT_READ mean fit of its characters */
#define HW_NEAT_FIT_PULL 0.5
#define HW_NEAT_FIT_READ 0.15
/* a stroke taller than this many x-heights is drawing, not writing */
#define HW_NEAT_DRAWING 3.5
/* a character is pulled only when its ink is within this distance (x-heights, RMS, both
 * centred) of the variant it goes to */
#define HW_NEAT_NEAR 0.3

struct hw_neat {
	/* in */
	const struct hw_ink *ink;
	size_t n;
	unsigned char *keep;            /* per stroke: 1 passes unchanged (in and out: drawing is added) */
	const unsigned *cp;             /* the reading, code points */
	size_t nc;
	const double *fit;              /* per character 0..1 (-1 not aligned); NULL: all trusted */
	int **own;                      /* per stroke, per point: its character, -1 none */
	double strength;                /* 0..1 */
	const struct hw_letters *L;     /* NULL: line-level corrections only */
	double xh;                      /* the person's x-height in the ink's units, 0 unknown */
	int line_only;                  /* no pull: line-level corrections only */
	/* out (malloc'd by hw_neaten; hw_neat_free) */
	double **x, **y;
	int *how;
};

/* Strokes too tall to be writing: marked in keep (xh as in struct hw_neat). */
void hw_neat_drawing(const struct hw_ink *ink, size_t n, double xh, unsigned char *keep);

/* 0, or -1 when out of memory. Strokes kept (keep, or not written by any character of a
 * reading the ink is read with low confidence) come out as they went in. */
int hw_neaten(struct hw_neat *q);
void hw_neat_free(struct hw_neat *q);

/* For tests and measurements: the centred RMS distance (x-heights) between the ink of one
 * character (its strokes, in writing order) and a variant with as many strokes; xh and
 * base give the ink's x-height and baseline (y down). INFINITY when the counts differ. */
double hw_neat_variant_dist(const struct hw_ink *ink, size_t n, double xh, const struct hw_variant *v);

#endif
