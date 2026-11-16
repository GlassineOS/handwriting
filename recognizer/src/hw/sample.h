// SPDX-License-Identifier: GPL-2.0-or-later
// Sample format version 1 (docs/samples.md): one JSON object per line.
#ifndef HW_SAMPLE_H
#define HW_SAMPLE_H

#include <stddef.h>
#include "featurize.h"

struct hw_ink {
	size_t n, cap;
	double *x, *y, *t, *p;          /* t in seconds; p: pressure, NAN when the pen has none */
	double *tx, *ty;                /* tilt in degrees from the vertical, NAN when none */
};

struct hw_units {
	char xy[16];                    /* "em" or "device" */
	double per_mm, rate;            /* device units: points per mm, report rate (0: unknown) */
};

/* The writing area a client wrote in (optional): its box in the sample's units, the line
 * spacing, the screen's orientation in degrees (0, 90, 180, 270). */
struct hw_area {
	int set;
	double box[4];                  /* x0, y0, x1, y1 */
	double line;
	int orientation;
};

/* What the recognizer read when the sample was written (optional). */
struct hw_reading_text {
	int n;
	char *text[8];
	double score[8];
};

struct hw_sample {
	char *text;                     /* UTF-8 */
	char lang[36];
	char source[16];                /* synthetic, confirmed, corrected, prompted */
	char spans_source[16];          /* exact, inferred */
	char licence[16];               /* donated samples only ("" otherwise) */
	char donor[40];                 /* donated samples only: the donor ID ("" otherwise) */
	char written[8];                /* how it was written: careful, quick, or "" */
	char pair[17];                  /* the pair's link (16 hex digits) both samples carry, or "" */
	struct hw_units units;
	struct hw_ink *strokes;
	size_t nstrokes;
	int **spans;                    /* per code point of text: stroke indices, -1 ended */
	size_t nspans;
	int pressure;                   /* points carry pressure */
	int tilt;                       /* points carry pressure and tilt */
	struct hw_area area;
	struct hw_reading_text reading;
	int pick_rank;                  /* a picked candidate: its place (1 = first); 0 none */
	char pick_from[8];              /* where it came from: ink, beam, words, guided */
};

void hw_ink_free(struct hw_ink *k);
int hw_ink_add(struct hw_ink *k, double x, double y, double t, double p);
int hw_ink_add_tilt(struct hw_ink *k, double x, double y, double t, double p, double tx, double ty);
void hw_sample_free(struct hw_sample *s);

/* Parses one line; returns 0, or -1 with the reason in err (a version it doesn't know, a
 * missing field, ...). Fields it doesn't know are ignored. */
int hw_sample_parse(const char *line, struct hw_sample *s, char *err, size_t errlen);

/* The line (no newline), malloc'd. With `donation`, the licence is written; never a client
 * or time (they aren't part of a sample). */
char *hw_sample_format(const struct hw_sample *s);

/* a valid marking: "careful", "quick" or ""; a valid pair link: 16 hex digits or "" */
int hw_written_ok(const char *w);
int hw_pair_ok(const char *p);

/* The strokes as the engine takes them (pointers into s). */
void hw_sample_strokes(const struct hw_sample *s, struct hw_stroke *out);

/* Number of code points in a UTF-8 string, and the code points (up to max). */
size_t hw_utf8_decode(const char *s, unsigned *cp, size_t max);
size_t hw_utf8_encode(unsigned cp, char *out);

#endif
