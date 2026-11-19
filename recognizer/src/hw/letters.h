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
#define HW_LETTERS_BUILD 11     /* how it is built: part of the inputs, so a change rebuilds it */
#define HW_LETTERS_FAVOURITE 2  /* a favourite variant is shown this many times as often (FR-031) */
#define HW_LETTERS_USABLE 5     /* how the usable samples are counted (letters/usable): a change recounts */
#define HW_LETTERS_MIN 3        /* usable samples for a character to be covered */
#define HW_LETTERS_VARIANTS 5   /* variants per character at most (FR-006) */
#define HW_LETTERS_SLOTS 10     /* the font's cycle of variants along the text (FR-010): a tenth each */
#define HW_LETTERS_WAY_SHARE 10 /* a way of writing a character gives a variant from 1/this of its samples */
#define HW_LETTERS_PROFILE 16   /* points per stroke of a variant's pressure and speed profiles (FR-006) */
/* neighbour rules (FR-038): a character written next to a neighbour at least HW_RULE_MIN
 * times, the same variant in at least HW_RULE_SHARE_NUM / HW_RULE_SHARE_DEN of them (and in
 * HW_RULE_LIFT more of them than the cycle shows it: a variant written so everywhere is no
 * way of writing it there), and the rule picking the variant written more often than the
 * cycle on samples it was not made from */
#define HW_RULE_MIN 6
#define HW_RULE_SHARE_NUM 3
#define HW_RULE_SHARE_DEN 4
#define HW_RULE_LIFT 0.25       /* and in that much more of them than its share of the cycle */
#define HW_LETTERS_RULES 16     /* rules per character at most */
/* joined pairs (FR-039): written joined in at least HW_JOINED_MIN usable samples and in at
 * least HW_JOINED_SHARE_NUM / HW_JOINED_SHARE_DEN of the samples with the pair */
#define HW_JOINED_MIN 4
#define HW_JOINED_SHARE_NUM 3
#define HW_JOINED_SHARE_DEN 4
#define HW_JOINED_MAX 256       /* joined pairs in a model at most */
/* the best version's weights (FR-035), times a sample's own weight: */
#define HW_BEST_FAVOURITE 4.0   /* marked favourite */
#define HW_BEST_CAREFUL 2.0     /* written to "Write it your best" (quick: 1 / this) */
#define HW_BEST_CHOSEN 2.0      /* each time chosen in "Which one is nicer?" */
#define HW_BEST_OTHER 0.3       /* each time not chosen there */
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

/* The ink's middle (FR-007): the mean x of its ink between the baseline and the x-height,
 * each piece weighted by how far it runs up or down, so stems and bowls count and bars,
 * hooks and connecting strokes hardly do. NAN without ink running up or down there. */
#define HW_MID_Y0 0.0
#define HW_MID_Y1 1.0
struct hw_mid { double sx, sw; };
void hw_mid_seg(struct hw_mid *m, double x0, double y0, double x1, double y1);
double hw_mid_of(const struct hw_mid *m);

struct hw_variant {
	double width;                   /* of the ink, x-heights */
	double weight;                  /* the sum of its samples' weights */
	int members;                    /* versions it was made from */
	int use;                        /* versions written this way (nearest to it of their strokes) */
	struct hw_profile prof;         /* x from the variant's left edge */
	double mid;                     /* its ink's middle (hw_mid_of), x from its left edge */
	int lc, rc;                     /* the side shapes of its left and right side */
	int out, favourite;             /* the person's marks (FR-031): left out, their favourite */
	size_t nids;                    /* the samples written this way (its use), sorted */
	char (*ids)[17];
	size_t nstrokes;
	struct hw_lstroke *s;
	/* the person's best version of it (FR-035): its samples averaged with the favourite,
	 * careful and chosen ones weighted up (HW_BEST_*), nstrokes strokes with the points of
	 * s, x from the variant's left edge; NULL when nothing weighs its samples differently
	 * (the variant itself is the best) */
	struct hw_lstroke *best;
	/* how its samples were written (FR-006, the Brush style FR-034): per stroke
	 * HW_LETTERS_PROFILE values along its normalised arc length, the averaged pressure and
	 * speed, 1 the person's median; NULL when none of its samples gave one */
	double *pres, *speed;
};

/* A neighbour rule (FR-038): after (side 0: the neighbour is the character before) or
 * before (side 1: the one after) nb, the character shows variant var instead of its cycle's;
 * n the times written next to it, same those written as var, hits how often the rule picked
 * the variant written on samples it was not made from (leave one sample out), cycle how
 * often the cycle does (the expected count) */
struct hw_rule {
	unsigned nb;
	int side, var, n, same;
	double hits, cycle;
};

struct hw_letter {
	unsigned cp;
	int samples;                    /* usable samples with this character */
	char marks[17];                 /* the hash of its marks when built ("" none) */
	int covered;
	int nvar;
	struct hw_variant var[HW_LETTERS_VARIANTS];    /* the most used first */
	/* the variant at each step of the font's cycle, as often as each is used */
	unsigned char slot[HW_LETTERS_SLOTS];
	/* the neighbour rules, the strongest (hits over cycle) first: the first that applies wins */
	int nrules;
	struct hw_rule rule[HW_LETTERS_RULES];
};

/* A joined pair (FR-039): a, b written in one stroke, drawn as one glyph made from those
 * samples; samples the usable ones written joined (the pair's strokes its own), joined all
 * written joined, of all with the pair written with known strokes. v as a variant (x from its
 * left edge; the font sets it at the gap on both sides, its middle unused). */
struct hw_joined {
	unsigned a, b;
	int samples, joined, of;
	struct hw_variant v;
};

struct hw_metrics {
	double cap, asc, desc;          /* cap height, ascender (top), descender (bottom, < 0) */
	double slant;                   /* tan of the lean, > 0 leaning right */
	double letter_gap, word_gap;    /* between the ink of two letters, of two words */
	double ink;                     /* the pen's width */
	/* what a sample without letters of its own to measure takes (one character's build
	 * takes them from the model): x-height per area line, baseline below the area's guide
	 * (x-heights), x-height in mm; 0 none */
	double xh_line, base_guide, xh_mm;
	/* the person's median pressure (as the pen gives it) and speed (x-heights per second)
	 * the profiles are relative to (one character's build takes them from the model); 0 none */
	double pres_mid, speed_mid;
};

/* The spacing (FR-007): where a pair goes is the distance between the letters' middles
 * (hw_mid_of): a pair's median distance, or the first's right reach plus the second's left
 * reach; guarded by, and without a distance set at, the gap: a pair's median gap, a pair
 * of side shapes' gap, and for the rest the person's typical gap less open * (the
 * profiles' open space - open0); words at word. Gaps are hw_profile_gap's, in x-heights. */
#define HW_PAIR_MIN 3                   /* times written for a pair's own gap */
#define HW_SIDE_PAIR_MIN 5              /* for a pair of side shapes' gap */
#define HW_SIDE_CLASSES 16              /* side shapes per side at most */
struct hw_pair { unsigned a, b; double gap; int n; double mid; };     /* mid: NAN none */
/* how far the person sets the next letter's middle after a character's (right) and its
 * middle after the previous letter's (left), from the pairs it was written in; NAN none */
struct hw_reach { unsigned cp; double right, left; int nr, nl; };
struct hw_side_pair { int rc, lc; double gap; int n; };
struct hw_spacing {
	double letter, word, open, open0;
	struct hw_pair *pairs;          /* sorted by a, b */
	size_t npairs;
	struct hw_side_pair *sides;     /* sorted by rc, lc */
	size_t nsides;
	struct hw_reach *reach;         /* sorted by cp */
	size_t nreach;
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
	size_t njoined;                 /* joined pairs, sorted by a, b */
	struct hw_joined *joined;
};

/* the side shape of a profile's left (side 0) or right (1) side: the nearest typical one */
int hw_spacing_side(const struct hw_spacing *sp, const struct hw_profile *p, int side);
/* the gap between a (its right side) and b (its left side) the person sets them at */
double hw_spacing_gap(const struct hw_spacing *sp, unsigned a, const struct hw_profile *pa, int rc,
		      unsigned b, const struct hw_profile *pb, int lc);
/* where b goes: its origin's distance right of a's, both profiles (and middles, NAN none)
 * relative to their own origins */
double hw_spacing_place(const struct hw_spacing *sp, unsigned a, const struct hw_profile *pa, double ma, int rc,
			unsigned b, const struct hw_profile *pb, double mb, int lc);
/* one observed pair of neighbouring letters, in x-heights, for hw_spacing_learn */
struct hw_gap_obs { unsigned a, b; struct hw_profile pa, pb; double gap, ma, mb; };
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

/* Learn one character again (FR-032): its variants from its samples as they are now, the rest of
 * the model as built. 1 done, 0 no model, -1 error. */
int hw_letters_relearn(unsigned cp, int verbose);

/* A character's occurrences in the prompted samples (FR-031; the character sheet): per sample
 * and place, how it counts. Lines through emit(). */
enum hw_occ_state {
	HW_OCC_USABLE,          /* counts; variant: the one it is written like (-1 none) */
	HW_OCC_OUT,             /* left out by the person */
	HW_OCC_WAY_OUT,         /* counts for a variant the person left out */
	HW_OCC_UNREAD,          /* the writing wasn't read clearly (no exact spans) */
	HW_OCC_JOINED_BEFORE,   /* a stroke shared with the character before */
	HW_OCC_JOINED_AFTER,    /* with the one after */
	HW_OCC_ODD,             /* far from its other versions in size, or no baseline */
};
struct hw_occ { char id[17]; int pos; int state; int variant; const char *text; };
int hw_letters_occurrences(unsigned cp, void (*emit)(const struct hw_occ *, void *), void *arg);

/* what a font made now would be made from, hashed; 1 when the installed font was made from it */
int hw_letters_fingerprint(const struct hw_letters *L, char out[17]);
int hw_font_current(const struct hw_letters *L);

/* the marks' hash for one character as the store has them now */
void hw_letters_marks_hash(unsigned cp, char out[17]);
/* 1 when some character's marks changed since the model was built */
int hw_letters_marks_changed(const struct hw_letters *L);
/* the letters as the font and neatening use them: variants left out removed (slots
 * renumbered), letters with none left not covered; a shallow copy, free with
 * hw_letters_view_free */
int hw_letters_view(const struct hw_letters *L, struct hw_letters *out);
void hw_letters_view_free(struct hw_letters *v);

/* The step of the font's cycle a text is at, as its contextual alternates give it (FR-010):
 * prev the step of the character (or space) before in the same run, -1 at its start or after a
 * character the font lacks. The character shows its variant slot[step]. */
int hw_letters_step(int prev);

/* The font's styles (FR-034): one family, the same letters, spacing and kerning; only the
 * ink's width along each stroke differs. */
enum hw_font_style { HW_STYLE_REGULAR, HW_STYLE_CALLIGRAPHY, HW_STYLE_BRUSH, HW_STYLES };
extern const char *const hw_font_style_names[HW_STYLES];        /* "Regular", "Calligraphy", "Brush" */
#define HW_FONT_NIB_DEFAULT 35  /* the broad nib's angle, degrees from the horizontal */
#define HW_FONT_NIB_MIN 0
#define HW_FONT_NIB_MAX 90

/* The installed font (letters/font: NAME VERSION PATH, PATH the Regular style's file):
 * 1 there, 0 none, -1 error. */
int hw_font_record(char *name, size_t nn, unsigned *version, char *path, size_t np);
/* Its styles' files (paths[s], "" when the record has none: a font installed before the
 * styles has Regular alone) and the nib angle it was made with (-1 none). 1, 0 none, -1. */
int hw_font_styles(char paths[HW_STYLES][4096], int *nib);
/* whether it was made with random alternates (FR-040): 1, 0 not, -1 error */
int hw_font_random(void);
/* The font file's bytes for a model, malloc'd (ttf.c): the Regular style, or one style at
 * a nib angle (degrees, Calligraphy only), with random alternates ('rand', FR-040) when
 * random. */
unsigned char *hw_font_build(const struct hw_letters *L, const char *name, unsigned version, size_t *len);
unsigned char *hw_font_build_style(const struct hw_letters *L, const char *name, unsigned version, int style,
				   double nib, int random, size_t *len);
/* The ink's width (x-heights) at each point of a variant's stroke j in a style (ttf.c, the
 * rules in docs/recognizer.md); Calligraphy: from the stroke's direction at each point. */
void hw_font_widths(const struct hw_letters *L, const struct hw_variant *v, size_t j, int style, double nib, double *w);
/* Calligraphy's thinnest and widest line (x-heights) for the model's ink width */
void hw_font_nib_widths(const struct hw_letters *L, double *wmin, double *wmax);
/* Builds and installs all three styles (replacing the font there, name kept when NULL, the
 * nib angle kept when < 0, random alternates kept when random < 0, else on when 1);
 * prints the Regular file's path. 0, 1 no letter model or nothing covered, -1 error, -2 a
 * bad name. */
int hw_font_install(const char *name, int nib, int random, int verbose);
/* removes the installed font: 1 removed, 0 none, -1 error */
int hw_font_remove(void);
/* The store's lock held: removes the letter model and the installed font (an erase). */
void hw_letters_erase_locked(void);

#endif
