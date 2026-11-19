// SPDX-License-Identifier: GPL-2.0-or-later
// Calibration of the ink's scores (spec 001 FR-022): see calib.h.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "calib.h"
#include "ctc.h"

void hw_calib_apply(const float *in, float *out, long T, int C, double t)
{
	if (!(t > 0)) t = 1;
	double k = 1 / t;
	for (long f = 0; f < T; f++) {
		const float *a = in + (size_t)f * C;
		float *b = out + (size_t)f * C;
		double m = -INFINITY;
		for (int c = 0; c < C; c++) if (a[c] * k > m) m = a[c] * k;
		double s = 0;
		for (int c = 0; c < C; c++) s += exp(a[c] * k - m);
		double z = m + log(s);
		for (int c = 0; c < C; c++) b[c] = (float)(a[c] * k - z);
	}
}

/* the items' summed loss at temperature t; *chars the characters of the items that count */
static double loss_at(const struct hw_calib_item *it, size_t n, double t, float *buf, long *chars)
{
	double sum = 0;
	long nc = 0;
	for (size_t i = 0; i < n; i++) {
		if (it[i].T <= 0 || it[i].L <= 0) continue;
		const float *lp = it[i].logp;
		if (t != 1) {
			hw_calib_apply(it[i].logp, buf, it[i].T, it[i].C, t);
			lp = buf;
		}
		/* an item that can't be aligned at 1 doesn't count at any temperature */
		double x = hw_ctc_loss(lp, it[i].T, it[i].C, it[i].lab, it[i].L, NULL, 0);
		if (!isfinite(x)) continue;
		sum += x;
		nc += it[i].L;
	}
	if (chars) *chars = nc;
	return sum;
}

double hw_calib_fit(const struct hw_calib_item *it, size_t n, double *before, double *after)
{
	size_t cap = 0;
	for (size_t i = 0; i < n; i++) if ((size_t)it[i].T * (size_t)it[i].C > cap) cap = (size_t)it[i].T * (size_t)it[i].C;
	float *buf = cap ? malloc(cap * sizeof *buf) : NULL;
	if (!buf) return 0;
	long nc = 0;
	double l1 = loss_at(it, n, 1, buf, &nc);
	if (!nc) { free(buf); return 0; }
	/* golden section on log t: the loss is smooth and has one minimum in the range */
	const double g = (sqrt(5) - 1) / 2;
	double a = log(HW_CALIB_MIN), b = log(HW_CALIB_MAX);
	double x1 = b - g * (b - a), x2 = a + g * (b - a);
	double f1 = loss_at(it, n, exp(x1), buf, NULL), f2 = loss_at(it, n, exp(x2), buf, NULL);
	for (int k = 0; k < 40 && b - a > 1e-4; k++) {
		if (f1 <= f2) { b = x2; x2 = x1; f2 = f1; x1 = b - g * (b - a); f1 = loss_at(it, n, exp(x1), buf, NULL); }
		else { a = x1; x1 = x2; f1 = f2; x2 = a + g * (b - a); f2 = loss_at(it, n, exp(x2), buf, NULL); }
	}
	double t = exp((a + b) / 2), lt = loss_at(it, n, t, buf, NULL);
	/* not better than none (a flat loss): none */
	if (!(lt < l1)) { t = 1; lt = l1; }
	free(buf);
	if (before) *before = l1 / (double)nc;
	if (after) *after = lt / (double)nc;
	return t;
}

double hw_calib_base(const char *dir, uint64_t hash)
{
	char p[4200], line[256];
	snprintf(p, sizeof p, "%s/calibration", dir);
	FILE *f = fopen(p, "re");
	if (!f) return 0;
	double t = 0;
	int ok = fgets(line, sizeof line, f) && !strcmp(line, "glassine-handwriting-calibration 1\n");
	while (ok && fgets(line, sizeof line, f)) {
		unsigned long long h;
		double v;
		char kind[32];
		if (sscanf(line, "%31s %llx %lf", kind, &h, &v) == 3 && !strcmp(kind, "temperature") && h == hash &&
		    v >= HW_CALIB_MIN && v <= HW_CALIB_MAX)
			t = v;
	}
	fclose(f);
	return t;
}
