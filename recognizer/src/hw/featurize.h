// SPDX-License-Identifier: GPL-2.0-or-later
// Ink to model input: the same computation as train/src/glassine_train/features.py,
// in double precision, so the engine reads exactly what the model was trained on.
#ifndef HW_FEATURIZE_H
#define HW_FEATURIZE_H

#include <stddef.h>

#define HW_NFEAT 5

/* One stroke: the points between pen down and pen up; t in seconds. */
struct hw_stroke {
	size_t n;
	const double *x, *y, *t;
};

/* Where a feature row came from: the stroke (index into the array given) and the
 * original point nearest before it along the stroke. */
struct hw_origin {
	int stroke;
	int point;
};

/* Features of the strokes (empty strokes are skipped, as features.py does): returns the
 * number of rows, *out a malloc'd (rows x HW_NFEAT) float array, and, when origin is not
 * NULL, *origin a malloc'd array of rows entries. Returns 0 (and NULLs) for no ink, -1
 * when out of memory. */
long hw_features(const struct hw_stroke *s, size_t ns, float **out, struct hw_origin **origin);

/* The tilt (radians), core height, centre and left edge features.py would use; for
 * gestures that need the writing's scale. Returns -1 when there is no ink. */
int hw_line_metrics(const struct hw_stroke *s, size_t ns, double *tilt, double *core,
		    double *mid, double *x0);

#endif
