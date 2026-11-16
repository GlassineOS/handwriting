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
#define HW_LETTERS_BUILD 6      /* how it is built: part of the inputs, so a change rebuilds it */
#define HW_LETTERS_USABLE 5     /* how the usable samples are counted (letters/usable): a change recounts */
#define HW_LETTERS_MIN 3        /* usable samples for a character to be covered */
#define HW_LETTERS_VARIANTS 5   /* variants per character at most (FR-006) */
#define HW_LETTERS_SLOTS 10     /* the font's cycle of variants along the text (FR-010): a tenth each */
#define HW_LETTERS_WAY_SHARE 10 /* a way of writing a character gives a variant from 1/this of its samples */
#define HW_FONT_NAME_DEFAULT "My handwriting"
#define HW_FONT_NAME_MAX 63     /* bytes of UTF-8 */

/* Coordinates of a variant: x-heights, y up, the baseline at 0, x from the left edge of
 * the ink (the first point's x is not necessarily 0). */
struct hw_lstroke {
	size_t n;
	double *x, *y;
};

/* An ink profile (spec 003 FR-006, FR-007): how far the ink reaches to the left (l, its
 * smallest x) and the right (r, its largest x) in each height band, in x-heights, y up from
 * the baseline; the bands run from HW_PROF_Y0 to HW_PROF_Y1 (descender to cap height), the
 * first and the last open-ended. NAN: no ink in that band. */
#define HW_PROF_BANDS 8
#define HW_PROF_Y0 (-0.6)
#define HW_PROF_Y1 1.6
struct hw_profile {
	double l[HW_PROF_BANDS], r[HW_PROF_BANDS];
};
void hw_profile_clear(struct hw_profile *p);
/* the segment (x0, y0)-(x1, y1) (x-heights, y up) into p, every band it crosses */
void hw_profile_seg(struct hw_profile *p, double x0, double y0, double x1, double y1);
int hw_profile_empty(const struct hw_profile *p);
/* the narrowest distance between a's right profile and b's left one, b moved right by
 * shift (between the pen's centre lines); their boxes when they share no band */
double hw_profile_gap(const struct hw_profile *a, const struct hw_profile *b, double shift);
/* how much open space they leave: the mean, over the bands they share, of the distance
 * beyond the narrowest (0 when they share fewer than two) */
double hw_profile_open(const struct hw_profile *a, const struct hw_profile *b);

struct hw_variant {
	double width;                   /* of the ink, x-heights */
	double weight;                  /* the sum of its samples' weights */
	int members;                    /* versions it was made from */
	int use;                        /* versions written this way (nearest to it of their strokes) */
	struct hw_profile prof;         /* x from the variant's left edge */
	int lc, rc;                     /* the side shapes of its left and right side */
	size_t nstrokes;
	struct hw_lstroke *s;
};

struct hw_letter {
	unsigned cp;
	int samples;                    /* usable samples with this character */
	int covered;
	int nvar;
	struct hw_variant var[HW_LETTERS_VARIANTS];    /* the most used first */
	/* the variant at each step of the font's cycle, as often as each is used */
	unsigned char slot[HW_LETTERS_SLOTS];
};

struct hw_metrics {
	double cap, asc, desc;          /* cap height, ascender (top), descender (bottom, < 0) */
	double slant;                   /* tan of the lean, > 0 leaning right */
	double letter_gap, word_gap;    /* between the ink of two letters, of two words */
	double ink;                     /* the pen's width */
};

/* The spacing (FR-007): a pair's median gap, a pair of side shapes' gap, and for the rest
 * the person's typical gap less open * (the profiles' open space - open0); words at word.
 * Gaps are hw_profile_gap's, in x-heights. */
#define HW_PAIR_MIN 3                   /* times written for a pair's own gap */
#define HW_SIDE_PAIR_MIN 5              /* for a pair of side shapes' gap */
#define HW_SIDE_CLASSES 16              /* side shapes per side at most */
struct hw_pair { unsigned a, b; double gap; int n; };
struct hw_side_pair { int rc, lc; double gap; int n; };
struct hw_spacing {
	double letter, word, open, open0;
	struct hw_pair *pairs;          /* sorted by a, b */
	size_t npairs;
	struct hw_side_pair *sides;     /* sorted by rc, lc */
	size_t nsides;
	int nl, nr;                     /* side shapes: their typical side, as hw_spacing_side compares */
	double lshape[HW_SIDE_CLASSES][HW_PROF_BANDS], rshape[HW_SIDE_CLASSES][HW_PROF_BANDS];
};

struct hw_letters {
	char inputs[17];                /* hash of what it was built from */
	long samples;                   /* usable samples */
	long long built;                /* the newest of those samples' confirm times */
	struct hw_metrics m;
	size_t n;                       /* characters seen, sorted by code point */
	struct hw_letter *l;
	struct hw_spacing sp;
};

/* the side shape of a profile's left (side 0) or right (1) side: the nearest typical one */
int hw_spacing_side(const struct hw_spacing *sp, const struct hw_profile *p, int side);
/* the gap between a (its right side) and b (its left side) the person sets them at */
double hw_spacing_gap(const struct hw_spacing *sp, unsigned a, const struct hw_profile *pa, int rc,
		      unsigned b, const struct hw_profile *pb, int lc);
/* one observed pair of neighbouring letters, in x-heights, for hw_spacing_learn */
struct hw_gap_obs { unsigned a, b; struct hw_profile pa, pb; double gap; };
/* the spacing from observed letter pairs and word gaps; the variants' side shapes set
 * (lc, rc) on L's letters. 0, -1 out of memory. */
int hw_spacing_learn(struct hw_letters *L, const struct hw_gap_obs *o, size_t n, const double *words, size_t nw);
void hw_spacing_free(struct hw_spacing *sp);

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

struct hw_ink;
/* The rules the build puts a sample's spans right with, by where the ink lies (a late dot,
 * bar or arm to the character it lies across; a neighbour's stroke out of a mark's span):
 * spans[k] (malloc'd, -1 ended, may be reallocated) the strokes of each of the nc
 * characters; line the writing area's line spacing in the ink's units, 0 unknown. 0, -1. */
int hw_letters_place(const struct hw_ink *ink, size_t n, double line, const unsigned *cps, size_t nc, int **spans);

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

/* The step of the font's cycle a text is at, as its contextual alternates give it (FR-010):
 * prev the step of the character (or space) before in the same run, -1 at its start or after a
 * character the font lacks. The character shows its variant slot[step]. */
int hw_letters_step(int prev);

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
