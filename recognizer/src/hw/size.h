// SPDX-License-Identifier: GPL-2.0-or-later
// Size (spec 001 FR-017): the features scale every input by its own height, so the model
// can't tell a lone dot from a letter or a lone o from an O. Here the ink's real size is
// compared with the person's letters (or the line), and the model's reading of each
// character is weighed by it (docs/recognizer.md, Size).
#ifndef HW_SIZE_H
#define HW_SIZE_H

#include <stddef.h>
#include "featurize.h"
#include "model.h"
#include "sample.h"

struct hw_lang;
struct hw_run;
struct hw_reading;

/* The reference, in the session's units. */
enum { HW_SIZE_NONE, HW_SIZE_PERSON, HW_SIZE_LINE };
struct hw_size_ref {
	int kind;                       /* HW_SIZE_*: where it came from */
	double x, c;                    /* small-letter and capital height, written in words */
	double xs, cs;                  /* the same written alone */
	int has_base;                   /* the guide lines are at base + k * line */
	double base, line;
	int area_line;                  /* line is the area's (else 1 for em) */
	int pending;                    /* the person's letters not looked up yet */
	char lang[36];
	double per_mm;                  /* the session's points per mm (0: not known) */
	int noweigh;                    /* the reference for the model only, no weighing ("noweigh") */
};

/* The reference for a session: the person's letters (their confirmed samples of the
 * language, newest first; nothing is stored) in the session's units, else the line
 * (area LINE, or 1 for em), else none. base: the area's first guide line, NAN when the
 * client didn't say. $GLASSINE_HANDWRITING_SIZE ("x,c[,xs,cs]" in the session's units)
 * replaces the person's letters (tests, measurements); "noweigh" (alone or after them) keeps
 * the reference for a model that reads it (ghm version 2) but weighs nothing. */
void hw_size_ref_for(const char *lang, const struct hw_units *u, const struct hw_area *a, double base,
		     struct hw_size_ref *out);

/* The reference height a model that reads it (ghm version 2) scales the ink by: the
 * person's small letters, else HW_LINE_XH lines; 0 without either. *base: the guide line
 * the ink sits on (the first at or below the median of its y), NAN without guides. */
double hw_size_ref_height(const struct hw_size_ref *ref, const struct hw_stroke *s, size_t ns, double *base);

/* a sample taken back: its measurement out of the store's `size` (the caller holds the
 * store's lock); 0 or -1 */
int hw_size_drop(const char *id);

/* What hw_size_weigh found, kept in the run for the reading. */
struct hw_size_info {
	int glyphs;                     /* glyphs (strokes grouped by their horizontal overlap) */
	int lone_mark;                  /* the ink is one mark: its kind (HW_GLYPH_*), else 0 */
	double prior[HW_MAX_CLASSES];  /* the lone mark's weight per class */
};
enum { HW_GLYPH_BIG, HW_GLYPH_DOT, HW_GLYPH_FLAT, HW_GLYPH_THIN };

/* hw_run with the size: the frames of a lone glyph (one or two make the ink), or of a mark
 * set apart at the end of longer ink, have their classes weighed by the glyph's size
 * within the model's chance of a character there (a blank stays as likely). The ink's own
 * log-probabilities stay in r->logp0 for the fit. Without a reference, plain hw_run. */
/* For a model that reads the reference height, the run is made with it (hw_run_at). */
int hw_run_sized(struct hw_lang *l, const struct hw_stroke *s, size_t ns, const struct hw_size_ref *ref,
		 struct hw_run *r);

/* the same on a run made already (hw_run_sized's second half; tests) */
void hw_size_weigh(const struct hw_lang *l, struct hw_run *r, const struct hw_stroke *s, size_t ns,
		   const struct hw_size_ref *ref);

/* the glyph's kind from its height and width (exposed for tests) */
int hw_glyph_kind(double h, double w, const struct hw_size_ref *ref);

/* After a reading: for one lone mark, the marks its size allows come first (those the
 * search missed are added, scored by the sized run), the other readings after them. */
void hw_size_rerank(const struct hw_lang *l, const struct hw_run *r, int n, struct hw_reading *rd);

#endif
