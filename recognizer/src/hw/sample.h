// SPDX-License-Identifier: GPL-2.0-or-later
// Sample format version 1 (docs/samples.md): one JSON object per line.
#ifndef HW_SAMPLE_H
#define HW_SAMPLE_H

#include <stddef.h>
#include "featurize.h"

struct hw_ink {
	size_t n, cap;
	double *x, *y, *t, *p;          /* t in seconds; p: pressure, NAN when the pen has none */
};

struct hw_units {
	char xy[16];                    /* "em" or "device" */
	double per_mm, rate;            /* device units: points per mm, report rate (0: unknown) */
};

struct hw_sample {
	char *text;                     /* UTF-8 */
	char lang[36];
	char source[16];                /* synthetic, confirmed, corrected, prompted */
	char spans_source[16];          /* exact, inferred */
	char licence[16];               /* donated samples only ("" otherwise) */
	struct hw_units units;
	struct hw_ink *strokes;
	size_t nstrokes;
	int **spans;                    /* per code point of text: stroke indices, -1 ended */
	size_t nspans;
	int pressure;                   /* points carry pressure */
};

void hw_ink_free(struct hw_ink *k);
int hw_ink_add(struct hw_ink *k, double x, double y, double t, double p);
void hw_sample_free(struct hw_sample *s);

/* Parses one line; returns 0, or -1 with the reason in err (a version it doesn't know, a
 * missing field, ...). Fields it doesn't know are ignored. */
int hw_sample_parse(const char *line, struct hw_sample *s, char *err, size_t errlen);

/* The line (no newline), malloc'd. With `donation`, the licence is written; never a client
 * or time (they aren't part of a sample). */
char *hw_sample_format(const struct hw_sample *s);

/* The strokes as the engine takes them (pointers into s). */
void hw_sample_strokes(const struct hw_sample *s, struct hw_stroke *out);

/* Number of code points in a UTF-8 string, and the code points (up to max). */
size_t hw_utf8_decode(const char *s, unsigned *cp, size_t max);
size_t hw_utf8_encode(unsigned cp, char *out);

#endif
