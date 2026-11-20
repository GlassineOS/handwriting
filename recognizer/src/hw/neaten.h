// SPDX-License-Identifier: GPL-2.0-or-later
// Neatening (spec 003 FR-013 to FR-017, FR-035 to FR-037, FR-043, docs/recognizer.md "Neatening"):
// ink in, ink out, the same strokes in the same order with every point moved: line-level
// corrections (straight baseline, steady slant, even size and spacing) and each readable
// character that the letter model covers pulled along its strokes towards the person's best
// version of it, by levels of a strength from 0 to 1; at Neatest each word whose text is
// known set anew in the font (FR-043). Only x and y change; deterministic.
#ifndef HW_NEATEN_H
#define HW_NEATEN_H

#include <stddef.h>
#include "letters.h"
#include "sample.h"

/* what was done to a stroke */
enum { HW_NEAT_KEPT, HW_NEAT_LINE, HW_NEAT_PULLED };

/* what was done to a character (FR-041): pulled, or why not */
enum {
	HW_NEAT_WHY_PULLED,
	HW_NEAT_WHY_LEVEL,              /* the level pulls no letter (Light and below, no letter model's pull) */
	HW_NEAT_WHY_NOT_COVERED,        /* the font has no glyph for it there (not covered) */
	HW_NEAT_WHY_NO_STRUCTURE,       /* its glyph hasn't its structure (strokes, where they lie) */
	HW_NEAT_WHY_SHAPE_GUARD,        /* its new ink came closer to another character (FR-037) */
	HW_NEAT_WHY_LOW_CONFIDENCE,     /* in a word read with low confidence, or itself (FR-017) */
	HW_NEAT_WHY_DRAWING,            /* its strokes are drawing (FR-020) */
	HW_NEAT_WHY_GESTURE,            /* its stroke is a gesture (FR-020; the caller sets it) */
	HW_NEAT_WHY_NO_INK,             /* no ink of its own (a space; or none aligned to it) */
	HW_NEAT_WHY_SET,                /* its word set anew in the font (FR-043) */
};

/* the levels (FR-014): the strength of each; in between, part of the way to the next */
#define HW_NEAT_LIGHT 0.25      /* the line-level corrections, all of them from here */
#define HW_NEAT_MEDIUM 0.5      /* and every character whose glyph has its structure pulled */
#define HW_NEAT_STRONG 0.75     /* further */
/* how far a pulled character goes towards its glyph (the one the font draws there, FR-035)
 * at Medium and at Strong; at 1 (Neatest) it is the glyph */
#define HW_NEAT_PULL_MEDIUM 0.5
#define HW_NEAT_PULL_STRONG 0.85

/* a character gets the pull with at least this fit, the ink is writing (line-level
 * corrections) with at least NEAT_READ mean fit of its characters */
#define HW_NEAT_FIT_PULL 0.5
#define HW_NEAT_FIT_READ 0.15
/* a word is set where its ink (as Strong leaves it) is at most SET_WIDE times as wide as
 * the font sets it, or the font's at most that times the ink's, and each character's ink's
 * middle within SET_PLACE x-heights of its glyph's (from the word's left, the ink scaled to
 * the font's width) */
#define HW_NEAT_SET_WIDE 1.6
#define HW_NEAT_SET_PLACE 0.6
/* a pen lift in a set character's ink moves to the end of a glyph's stroke within this
 * share of the glyph's length */
#define HW_NEAT_SET_SNAP 0.2
#define HW_NEAT_SET_ON 0.1     /* (of a letter's height) a neighbour's piece starting or ending this near its ink is on it */
/* a stroke taller than this many x-heights is drawing, not writing */
#define HW_NEAT_DRAWING 3.5

struct hw_neat {
	/* in */
	const struct hw_ink *ink;
	size_t n;
	unsigned char *keep;            /* per stroke: 1 passes unchanged (in and out: drawing is added) */
	const unsigned *cp;             /* the reading, code points */
	size_t nc;
	const double *fit;              /* per character 0..1 (-1 not aligned); NULL: all trusted */
	int **own;                      /* per stroke, per point: its character, -1 none */
	const unsigned char *share;     /* per stroke, per character (n x nc): 1 the stroke holds some
					 * of its ink (ALIGN of whole strokes); NULL unknown */
	double strength;                /* 0..1 */
	const struct hw_letters *L;     /* NULL: line-level corrections only */
	double xh;                      /* the person's x-height in the ink's units, 0 unknown */
	int line_only;                  /* no pull: line-level corrections only */
	const unsigned char *nopull;    /* per character: 1 not pulled (FR-037); NULL none */
	const unsigned char *guard;     /* (tests) per character: 1 fails the shape guard; NULL none */
	int confirmed;                  /* cp is the confirmed text: its words can be set (FR-043) */
	/* out (malloc'd by hw_neaten; hw_neat_free) */
	double **x, **y;
	int *how;
	unsigned char *pulled;          /* per character: 1 pulled */
	unsigned char *why;             /* per character: HW_NEAT_WHY_* */
	unsigned char *set;             /* per character: 1 its word set (FR-043) */
	double line_xh, line_base;      /* the line's x-height and baseline (y) as neatened */
	int line_hasbase;
};

/* Strokes too tall to be writing: marked in keep (xh as in struct hw_neat). */
void hw_neat_drawing(const struct hw_ink *ink, size_t n, double xh, unsigned char *keep);

/* 0, or -1 when out of memory. Strokes kept (keep) come out as they went in. A word whose
 * characters are read with low confidence (mean fit) gets the line-level corrections only,
 * the other words keep their pull (FR-017). Each pulled character's new ink must be nearer
 * (centred RMS, at the line's x-height) to its own versions than to any version of another
 * character with as many strokes, else it isn't pulled (FR-037, the shape guard). */
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
