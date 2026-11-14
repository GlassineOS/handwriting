// SPDX-License-Identifier: GPL-2.0-or-later
// Gestures (spec FR-008): strike-through, scratch-out, caret.
#ifndef HW_GESTURE_H
#define HW_GESTURE_H

#include <stddef.h>
#include "featurize.h"

#define HW_GESTURE_STRIKE 1
#define HW_GESTURE_SCRATCH 2
#define HW_GESTURE_CARET 3
#define HW_GESTURE_MAX 1024

struct hw_gesture {
	int kind;
	double bbox[4];                 /* x0, y0, x1, y1 of the gesture stroke */
	double x, y;                    /* caret: the apex */
	int covers[HW_GESTURE_MAX];     /* indices of the other strokes it covers (caret: those before it) */
	int ncovers;
};

/* Whether stroke g of s is a gesture; scale is the writing's core height in the strokes'
 * units (0: unknown). Returns the kind, 0 for none. */
int hw_gesture(const struct hw_stroke *s, size_t ns, size_t g, double scale, struct hw_gesture *out);
const char *hw_gesture_name(int kind);

#endif
