// SPDX-License-Identifier: GPL-2.0-or-later
// Look-alike letters and digits (spec 001 FR-019, docs/recognizer.md "Look-alikes"): O and o
// against 0, I and l against 1, G against 6. A field for text leans to the letter, a field
// for numbers to the digit; the ink's own reading never leans.
#ifndef HW_LOOKALIKE_H
#define HW_LOOKALIKE_H

#include "recog.h"

enum { HW_LEAN_NONE, HW_LEAN_LETTERS, HW_LEAN_DIGITS };

/* the lean of a field (the text-input-v3 purpose, then hints): letters for plain text, names
 * and letters (normal, alpha, name), digits for digits, number, phone, date, time and
 * datetime, none for anything else (no text field, addresses, a terminal, a purpose not known) */
int hw_lean_of_field(const char *field);

/* The share of a look-alike's chance it keeps in a frame where the other side is favoured:
 * $GLASSINE_HANDWRITING_LOOKALIKE (a number in (0, 1], or "off" for 1), else HW_LEAN_KEEP. */
#define HW_LEAN_KEEP 0.5
double hw_lean_keep(void);

/* In every frame, the disfavoured side (in a field for text the digits, in one for numbers
 * their letters) keeps only `keep` of its chance, the frame's other classes share the rest
 * in proportion: the favoured side wins where the two were within 1/keep of each other, so a
 * clearly written character still reads as written. The ink's own
 * log-probabilities are kept in r->logp0 (a fit only ever gains, FR-017) when they aren't
 * already. */
void hw_lookalike_lean(const struct hw_lang *l, struct hw_run *r, int lean, double keep);

#endif
