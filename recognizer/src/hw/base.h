// SPDX-License-Identifier: GPL-2.0-or-later
// Autograph's base font (spec 003 FR-009, FR-045): a plain school print model's letters as pen
// strokes (their centre lines, made from the font by recognizer/tools/base-font.py when it is
// packaged). Autograph draws every character the letter model doesn't cover from it, on the
// person's metrics, so it is drawn like their own letters. It is never a neatening target: only
// the font and its preview read it (docs/recognizer.md, The base font).
#ifndef HW_BASE_H
#define HW_BASE_H

#include <stddef.h>
#include "letters.h"

#define HW_BASE_FORMAT "glassine-handwriting-base"
#define HW_BASE_VERSION 1
#ifndef HW_BASE_PATH
#define HW_BASE_PATH "/usr/share/glassine/handwriting/autograph/base.jsonl"
#endif

struct hw_base_char {
	unsigned cp;
	size_t nstrokes;
	struct hw_lstroke *s;           /* x-heights, y up from the baseline, upright */
};

struct hw_base {
	char hash[17];                  /* of the file */
	char family[128], copyright[512], licence_url[256];
	double slant, cap, asc, desc, ink, letter, word;
	size_t n;                       /* sorted by code point */
	struct hw_base_char *c;
};

/* The base (GLASSINE_HANDWRITING_BASE: a file, `off` none; else HW_BASE_PATH), read once;
 * NULL when there is none or it can't be read */
const struct hw_base *hw_base_get(void);

/* V (a view, hw_letters_view, or its copy) with the base's metrics where it has none of its
 * own (before the person's first sample: all of them) and the base's letter and word spacing
 * where it has no spacing of its own; scalars only, V's arrays are untouched */
void hw_base_defaults(struct hw_letters *V, const struct hw_base *B);

/* The glyphs Autograph draws from the base: one per base character V doesn't cover (in the
 * font: covered with a variant), scaled to V's metrics (small letters by the ascender and
 * descender, capitals and digits uniformly by the cap height), at V's slant, with its
 * profiles, middle and side shapes as a variant has them. Sorted by code point. 0, -1. */
struct hw_base_glyph { unsigned cp; struct hw_variant v; };
int hw_base_glyphs(const struct hw_letters *V, const struct hw_base *B, struct hw_base_glyph **out, size_t *n);
void hw_base_glyphs_free(struct hw_base_glyph *g, size_t n);

#endif
