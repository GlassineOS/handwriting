// SPDX-License-Identifier: GPL-2.0-or-later
// Neatening (spec 003 FR-013 to FR-017, FR-035 to FR-037, docs/recognizer.md "Neatening"):
// ink in, ink out, the same strokes in the same order with every point moved: line-level
// corrections (straight baseline, steady slant, even size and spacing) and each readable
// character that the letter model covers pulled along its strokes towards the person's best
// version of it, by levels of a strength from 0 to 1. Only x and y change; deterministic.
#ifndef HW_NEATEN_H
#define HW_NEATEN_H

#include <stddef.h>
#include "letters.h"
#include "sample.h"

/* what was done to a stroke */
enum { HW_NEAT_KEPT, HW_NEAT_LINE, HW_NEAT_PULLED };

/* the levels (FR-014): the strength of each; in between, part of the way to the next */
#define HW_NEAT_LIGHT 0.25      /* the line-level corrections, all of them from here */
#define HW_NEAT_MEDIUM 0.5      /* and the characters near their best version pulled */
#define HW_NEAT_STRONG 0.75     /* and every character with a version of its structure */
/* how far a pulled character goes towards its best version at Medium and at Strong; at 1
 * (Neatest) it is the best version */
#define HW_NEAT_PULL_MEDIUM 0.6
#define HW_NEAT_PULL_STRONG 0.85

/* a character gets the pull with at least this fit, the ink is writing (line-level
 * corrections) with at least NEAT_READ mean fit of its characters */
#define HW_NEAT_FIT_PULL 0.5
#define HW_NEAT_FIT_READ 0.15
/* a stroke taller than this many x-heights is drawing, not writing */
#define HW_NEAT_DRAWING 3.5
/* a character is near its best version (Medium pulls it) within this distance (x-heights,
 * RMS, both centred) */
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
	const unsigned char *nopull;    /* per character: 1 not pulled (FR-037); NULL none */
	/* out (malloc'd by hw_neaten; hw_neat_free) */
	double **x, **y;
	int *how;
	unsigned char *pulled;          /* per character: 1 pulled */
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

/* The pull's correspondence (FR-036), for tests: for each of the n points of a stroke (x, y)
 * the place on a target stroke (tx, ty; tn points; both in x-heights, in one frame) it goes
 * to, as a share of the target's length, matched by shape (direction and curvature along
 * the strokes, and place) in order: never decreasing along the stroke. A hook, loop or
 * overshoot at an end of the stroke the target doesn't have goes to the target's end (0 or
 * 1); at an end that is a joint with the next letter (joint0, joint1) such points are NAN
 * (they follow the matched point next to them). 0, -1 out of memory. */
int hw_neat_match(const double *x, const double *y, size_t n, const double *tx, const double *ty, size_t tn,
		  int joint0, int joint1, double *u);

#endif
