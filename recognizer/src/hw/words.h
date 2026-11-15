// SPDX-License-Identifier: GPL-2.0-or-later
// Words for reading prose: the keyboard's word predictor's base model for a language
// (LANG.gpm, GlassineOS/shell docs/predictor.md, read-only through mmap) and the person's
// learned words from the predictor's store. Both are optional: without them, readings are
// the ink's alone.
#ifndef HW_WORDS_H
#define HW_WORDS_H

#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>

#define HW_WORD_MAX 48                  /* bytes; longer runs of letters are not words */
#define HW_NO_WORD (-1)                 /* not a word the model knows */
#define HW_BOS (-2)                     /* the start of a sentence (as context) */

struct hw_words;

/* The words of a language (loaded on first use, again when the files changed); NULL when
 * there is neither a model nor learned words for it. */
const struct hw_words *hw_words_get(const char *lang);
void hw_words_forget_all(void);

/* folded (lower case, apostrophes ASCII), UTF-8; out has room for 2 * n + 1 bytes */
size_t hw_fold(const char *s, size_t n, char *out);
uint32_t hw_fold_cp(uint32_t c);
int hw_is_letter(uint32_t c);
int hw_is_apostrophe(uint32_t c);
/* the predictor's tokens: a sentence end (. ! ? line break) and a break (digits, other
 * symbols) cut the context; spaces, commas, quotes, brackets and dashes don't */
int hw_is_sentence_end(uint32_t c);
int hw_is_transparent(uint32_t c);

/* a folded word: its id in the model (>= 0), HW_NO_WORD; *known: in the model or learned
 * (and not removed) */
long hw_words_find(const struct hw_words *w, const char *folded, int *known);
/* natural log of P(word | the two words before), ids or HW_BOS / HW_NO_WORD for the
 * context; word HW_NO_WORD with known set: a learned word */
double hw_words_logp(const struct hw_words *w, long prev2, long prev1, long word, int known);
/* the model's display form of a word id */
const char *hw_words_text(const struct hw_words *w, long id);
/* the word's unigram log-probability (natural log) */
double hw_words_unigram(const struct hw_words *w, long id);
long hw_words_count(const struct hw_words *w);

#endif
