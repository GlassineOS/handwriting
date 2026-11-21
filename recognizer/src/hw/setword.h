// SPDX-License-Identifier: GPL-2.0-or-later
// A text set in the person's font, as pen strokes (spec 005 FR-018, FR-021, FR-024): the
// shape of an edited word for an app that draws it without the font (docs/recognizer.md,
// `set`). The glyphs are the font's (hw_letters_glyphs, hw_font_layout): a character the
// letter model doesn't cover is the font's own (none yet without one: its advance alone).
#ifndef HW_SETWORD_H
#define HW_SETWORD_H

#include <stddef.h>
#include "letters.h"

struct hw_set {
	double width;                   /* of the ink, x-heights (the advances' when it has none) */
	double xh_mm;                   /* the person's x-height in mm (the letter model's), 0 unknown */
	double ink;                     /* the pen's width, x-heights, 0 unknown */
	size_t n;                       /* strokes: x-heights, y up, the baseline at 0, the ink's left at 0 */
	struct hw_lstroke *s;
};

/* L: the letter model (NULL: none yet). 0, or -1 out of memory. */
int hw_set_text(const struct hw_letters *L, const unsigned *cp, size_t n, struct hw_set *out);
void hw_set_free(struct hw_set *s);

#endif
