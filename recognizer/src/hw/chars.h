// SPDX-License-Identifier: GPL-2.0-or-later
// The character model of a language (docs/recognizer.md, Characters): how likely each
// character is after the ones before it, from LANG/chars.gclm beside the base model, read
// only through mmap. Optional: without it, readings are without it.
#ifndef HW_CHARS_H
#define HW_CHARS_H

#include <stdint.h>

struct hw_chars;

/* A context: the last characters (at most 7), newest in the low byte, each its symbol + 1;
 * 0 is the empty context. */
typedef uint64_t hw_chars_ctx;

/* the language's character model (loaded on first use, again when its file changed);
 * NULL when there is none or it can't be read */
const struct hw_chars *hw_chars_get(const char *lang);
void hw_chars_forget_all(void);

/* the context after a text (UTF-8; NULL or empty: a space, the start of a word) */
hw_chars_ctx hw_chars_start(const struct hw_chars *m, const char *before);
/* natural log of P(cp | h); a character the model lacks: as uniform */
double hw_chars_logp(const struct hw_chars *m, hw_chars_ctx h, uint32_t cp);
/* the context after cp; a character the model lacks ends the context */
hw_chars_ctx hw_chars_push(const struct hw_chars *m, hw_chars_ctx h, uint32_t cp);
int hw_chars_order(const struct hw_chars *m);

#endif
