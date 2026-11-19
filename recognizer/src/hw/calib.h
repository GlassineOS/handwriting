// SPDX-License-Identifier: GPL-2.0-or-later
// Calibration of the ink's scores (spec 001 FR-022, docs/recognizer.md "Calibration"): a
// temperature on each frame's log-probabilities, per base model and per adaptation, fitted
// on held-back ink so that the ink's log-probability of a text is as sure as the ink really
// is. Readings are searched and ranked on the calibrated frames; fits stay the ink's own.
#ifndef HW_CALIB_H
#define HW_CALIB_H

#include <stddef.h>
#include <stdint.h>

/* the range a fitted temperature is kept in */
#define HW_CALIB_MIN 0.5
#define HW_CALIB_MAX 4.0

/* out = log-softmax(in / t) per frame of C classes (in and out may be the same) */
void hw_calib_apply(const float *in, float *out, long T, int C, double t);

/* one piece of held-back ink: its frames' log-probabilities and its text's classes */
struct hw_calib_item {
	const float *logp;
	long T;
	int C;
	const int *lab;
	int L;
};

/* The temperature in [HW_CALIB_MIN, HW_CALIB_MAX] with the least summed CTC loss of the
 * items' texts; *before and *after (may be NULL) the mean loss per character at 1 and at it.
 * Items that can't be aligned don't count. Returns 0 when no item counts. */
double hw_calib_fit(const struct hw_calib_item *it, size_t n, double *before, double *after);

/* The base model's temperature from DIR/calibration for the model of this hash (docs/
 * recognizer.md); 0 when there is none, it is for another model or it is out of range. */
double hw_calib_base(const char *dir, uint64_t hash);

#endif
