// SPDX-License-Identifier: GPL-2.0-or-later
// The letter model (spec 003 FR-006 to FR-008): per character the person's best versions,
// built from their prompted samples, markings and preferences; the line metrics; and the
// handwriting font made from it (ttf.c). Files in the store's `letters/` directory
// (docs/recognizer.md, "The letter model").
#ifndef HW_LETTERS_H
#define HW_LETTERS_H

#include <stddef.h>

#define HW_LETTERS_FORMAT "glassine-handwriting-letters"
#define HW_LETTERS_VERSION 1
#define HW_LETTERS_BUILD 3      /* how it is built: part of the inputs, so a change rebuilds it */
#define HW_LETTERS_MIN 3        /* usable samples for a character to be covered */
#define HW_LETTERS_VARIANTS 3   /* variants per character at most */
#define HW_FONT_NAME_DEFAULT "My handwriting"
#define HW_FONT_NAME_MAX 63     /* bytes of UTF-8 */

/* Coordinates of a variant: x-heights, y up, the baseline at 0, x from the left edge of
 * the ink (the first point's x is not necessarily 0). */
struct hw_lstroke {
	size_t n;
	double *x, *y;
};

struct hw_variant {
	double width;                   /* of the ink, x-heights */
	double weight;                  /* the sum of its samples' weights */
	int members;                    /* versions it was made from */
	size_t nstrokes;
	struct hw_lstroke *s;
};

struct hw_letter {
	unsigned cp;
	int samples;                    /* usable samples with this character */
	int covered;
	int nvar;
	struct hw_variant var[HW_LETTERS_VARIANTS];
};

struct hw_metrics {
	double cap, asc, desc;          /* cap height, ascender (top), descender (bottom, < 0) */
	double slant;                   /* tan of the lean, > 0 leaning right */
	double letter_gap, word_gap;    /* between the ink of two letters, of two words */
	double ink;                     /* the pen's width */
};

struct hw_letters {
	char inputs[17];                /* hash of what it was built from */
	long samples;                   /* usable samples */
	long long built;                /* the newest of those samples' confirm times */
	struct hw_metrics m;
	size_t n;                       /* characters seen, sorted by code point */
	struct hw_letter *l;
};

void hw_letters_free(struct hw_letters *L);

/* The hash of the inputs as they are now (prompted samples with their marking and pair,
 * and the preferences): 0, or -1 when the store can't be read. */
int hw_letters_inputs(char out[17]);

/* Builds the model from the store (deterministic: the same inputs, the same model). */
int hw_letters_build(struct hw_letters *L);
/* The model's file text (JSON lines), malloc'd. */
char *hw_letters_format(const struct hw_letters *L, size_t *len);
/* reads the model file text; 0, or -1 (not a letter model of this version) */
int hw_letters_parse(const char *text, struct hw_letters *L);
/* the stored model: 1 read, 0 none, -1 unreadable */
int hw_letters_load(struct hw_letters *L);

/* Usable samples per character (FR-007, FR-030) as the store holds them now, whether a
 * model is built or not: counted from the derived letters/usable file, made again for the
 * samples it lacks (progress, may be NULL, is called for each of those). Counts sorted by
 * code point, malloc'd; *samples the samples with any. 0, or -1. */
struct hw_lcount { unsigned cp; int samples; };
int hw_letters_usable(struct hw_lcount **out, size_t *n, long *samples, void (*progress)(size_t done, size_t total));

struct hw_letters_opts {
	int force;              /* skip the checks and rebuild even when nothing changed */
	int requested;          /* the person asked: no charging or idle condition */
	int verbose;            /* one result line on stdout */
	int progress;           /* "progress DONE TOTAL" lines on stdout while it reads the samples */
};
/* 1 built, 2 nothing to do (reason printed when verbose), -1 error */
int hw_letters_run(const struct hw_letters_opts *o);

/* The variant a text shows at each character, as the font's contextual alternates give it
 * (FR-010): prev is the variant of the character before in the same run (-1 at the start or
 * after a character the font lacks), n the variants of this one. */
int hw_letters_rotate(int prev, int n);

/* The installed font (letters/font: NAME VERSION PATH): 1 there, 0 none, -1 error. */
int hw_font_record(char *name, size_t nn, unsigned *version, char *path, size_t np);
/* The font file's bytes for a model, malloc'd (ttf.c). */
unsigned char *hw_font_build(const struct hw_letters *L, const char *name, unsigned version, size_t *len);
/* Builds and installs (replacing the one there, name kept when NULL); prints the path.
 * 0, 1 no letter model or nothing covered, -1 error. */
int hw_font_install(const char *name, int verbose);
/* removes the installed font: 1 removed, 0 none, -1 error */
int hw_font_remove(void);
/* The store's lock held: removes the letter model and the installed font (an erase). */
void hw_letters_erase_locked(void);

#endif
