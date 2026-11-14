// SPDX-License-Identifier: GPL-2.0-or-later
// Gestures (spec FR-008): strike-through, scratch-out and caret, recognized from the
// shape of the newest stroke before any text is read. Sizes are relative to the writing:
// the core height of the other strokes when there are some, else the pen's units (10 mm,
// or 1 em for ink in em), else the gesture's own shape only (no absolute size check).
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "gesture.h"

#define ADD(o, i) do { if ((o)->ncovers < HW_GESTURE_MAX) (o)->covers[(o)->ncovers++] = (int)(i); } while (0)

static double path_len(const struct hw_stroke *s)
{
	double L = 0;
	for (size_t i = 1; i < s->n; i++) L += hypot(s->x[i] - s->x[i - 1], s->y[i] - s->y[i - 1]);
	return L;
}

static void bbox(const struct hw_stroke *s, double b[4])
{
	b[0] = b[1] = INFINITY; b[2] = b[3] = -INFINITY;
	for (size_t i = 0; i < s->n; i++) {
		if (s->x[i] < b[0]) b[0] = s->x[i];
		if (s->y[i] < b[1]) b[1] = s->y[i];
		if (s->x[i] > b[2]) b[2] = s->x[i];
		if (s->y[i] > b[3]) b[3] = s->y[i];
	}
}

/* how far the points stray from the chord, at most, relative to its length */
static double straightness(const struct hw_stroke *s, size_t a, size_t b)
{
	double dx = s->x[b] - s->x[a], dy = s->y[b] - s->y[a], d = hypot(dx, dy), worst = 0;
	if (d <= 0) return INFINITY;
	for (size_t i = a; i <= b; i++) {
		double off = fabs((s->x[i] - s->x[a]) * dy - (s->y[i] - s->y[a]) * dx) / d;
		if (off > worst) worst = off;
	}
	return worst / d;
}

/* horizontal reversals larger than `hyst` */
static int reversals(const struct hw_stroke *s, double hyst)
{
	int n = 0, dir = 0;
	double lo = s->x[0], hi = s->x[0], ext = s->x[0];
	for (size_t i = 1; i < s->n; i++) {
		double x = s->x[i];
		if (!dir) {
			if (x < lo) lo = x;
			if (x > hi) hi = x;
			if (x - lo > hyst) { dir = 1; ext = x; }
			else if (hi - x > hyst) { dir = -1; ext = x; }
		} else if (dir > 0) {
			if (x > ext) ext = x;
			else if (ext - x > hyst) { n++; dir = -1; ext = x; }
		} else {
			if (x < ext) ext = x;
			else if (x - ext > hyst) { n++; dir = 1; ext = x; }
		}
	}
	return n;
}

static int overlaps(const double a[4], const double b[4])
{
	return a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3];
}

int hw_gesture(const struct hw_stroke *s, size_t ns, size_t g, double scale, struct hw_gesture *out)
{
	memset(out, 0, sizeof *out);
	const struct hw_stroke *q = &s[g];
	if (q->n < 3) return 0;
	double b[4];
	bbox(q, b);
	double w = b[2] - b[0], h = b[3] - b[1], L = path_len(q);
	if (L <= 0) return 0;
	double unit = scale > 0 ? scale : (w > h ? w : h) / 3;
	memcpy(out->bbox, b, sizeof b);

	/* strike-through: long, straight, flat */
	double chord = hypot(q->x[q->n - 1] - q->x[0], q->y[q->n - 1] - q->y[0]);
	if (chord / L > 0.9 && straightness(q, 0, q->n - 1) < 0.08 && h < 0.3 * w && w >= 2.5 * unit) {
		out->kind = HW_GESTURE_STRIKE;
		double mid = (b[1] + b[3]) / 2;
		for (size_t i = 0; i < ns; i++) {
			if (i == g || !s[i].n) continue;
			double c[4];
			bbox(&s[i], c);
			double cx = (c[0] + c[2]) / 2;
			/* crossed: its middle lies under the line, and the line runs through its height */
			if (cx >= b[0] && cx <= b[2] && mid >= c[1] - 0.25 * (c[3] - c[1]) && mid <= c[3] + 0.25 * (c[3] - c[1]))
				ADD(out, i);
		}
		return out->kind;
	}

	/* scratch-out: back and forth over a small width, several times */
	int rev = reversals(q, 0.5 * (w > 0 ? w : 1));
	if (rev >= 4 && L > 3.5 * (w + h) / 2 && (w + h) >= 1.0 * unit) {
		out->kind = HW_GESTURE_SCRATCH;
		for (size_t i = 0; i < ns; i++) {
			if (i == g || !s[i].n) continue;
			double c[4];
			bbox(&s[i], c);
			if (!overlaps(b, c)) continue;
			double ix = fmin(b[2], c[2]) - fmax(b[0], c[0]), iy = fmin(b[3], c[3]) - fmax(b[1], c[1]);
			double area = fmax(c[2] - c[0], 1e-9) * fmax(c[3] - c[1], 1e-9);
			if (ix * iy >= 0.5 * area || ((c[2] - c[0]) < 1e-9 || (c[3] - c[1]) < 1e-9))
				ADD(out, i);
		}
		return out->kind;
	}

	/* caret: two straight legs meeting at a top apex (y points down) */
	size_t apex = 0;
	for (size_t i = 1; i < q->n; i++) if (q->y[i] < q->y[apex]) apex = i;
	if (apex > 0 && apex < q->n - 1) {
		double l1 = hypot(q->x[apex] - q->x[0], q->y[apex] - q->y[0]);
		double l2 = hypot(q->x[q->n - 1] - q->x[apex], q->y[q->n - 1] - q->y[apex]);
		double up = q->y[0] - q->y[apex], down = q->y[q->n - 1] - q->y[apex];
		int rising = q->x[apex] > q->x[0] && q->x[q->n - 1] > q->x[apex];
		double ratio = l1 > l2 ? l1 / fmax(l2, 1e-9) : l2 / fmax(l1, 1e-9);
		double gap = q->x[q->n - 1] - q->x[0];
		if (rising && up > 0.5 * l1 && down > 0.5 * l2 && ratio < 2.0 && straightness(q, 0, apex) < 0.12 &&
		    straightness(q, apex, q->n - 1) < 0.12 && gap > 0.3 * (l1 + l2) / 2 && (l1 + l2) / 2 >= 0.5 * unit &&
		    (l1 + l2) < 1.3 * L && (l1 + l2) > 0.85 * L) {
			out->kind = HW_GESTURE_CARET;
			out->x = q->x[apex];
			out->y = q->y[apex];
			for (size_t i = 0; i < ns; i++) {
				if (i == g || !s[i].n) continue;
				double c[4];
				bbox(&s[i], c);
				if ((c[0] + c[2]) / 2 < out->x) ADD(out, i);   /* the strokes before it */
			}
			return out->kind;
		}
	}
	return 0;
}

const char *hw_gesture_name(int kind)
{
	switch (kind) {
	case HW_GESTURE_STRIKE: return "strike";
	case HW_GESTURE_SCRATCH: return "scratch";
	case HW_GESTURE_CARET: return "caret";
	}
	return "none";
}
