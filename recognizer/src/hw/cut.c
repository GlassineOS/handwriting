// SPDX-License-Identifier: GPL-2.0-or-later
#include <math.h>
#include <stdlib.h>

#include "cut.h"

double hw_cut_arc(const double *x, const double *y, size_t n, double *cum)
{
	double L = 0;
	if (cum && n) cum[0] = 0;
	for (size_t i = 1; i < n; i++) { L += hypot(x[i] - x[i - 1], y[i] - y[i - 1]); if (cum) cum[i] = L; }
	return L;
}

static void cut_resample(const double *x, const double *y, size_t n, double *ox, double *oy)
{
	double tot = 0;
	for (size_t i = 1; i < n; i++) tot += hypot(x[i] - x[i - 1], y[i] - y[i - 1]);
	size_t i = 0;
	double at = 0;
	for (int z = 0; z < HW_CUT_N; z++) {
		double want = tot * z / (HW_CUT_N - 1);
		while (i + 1 < n && at + hypot(x[i + 1] - x[i], y[i + 1] - y[i]) < want) { at += hypot(x[i + 1] - x[i], y[i + 1] - y[i]); i++; }
		if (i + 1 >= n) { ox[z] = x[n - 1]; oy[z] = y[n - 1]; continue; }
		double l = hypot(x[i + 1] - x[i], y[i + 1] - y[i]), u = l > 0 ? (want - at) / l : 0;
		ox[z] = x[i] + u * (x[i + 1] - x[i]);
		oy[z] = y[i] + u * (y[i + 1] - y[i]);
	}
}

void hw_cut_ref_make(const double *x, const double *y, size_t n, struct hw_cut_ref *r)
{
	cut_resample(x, y, n, r->x, r->y);
	r->len = 0;
	for (size_t i = 1; i < n; i++) r->len += hypot(x[i] - x[i - 1], y[i] - y[i - 1]);
}

double hw_cut_cost(const double *x, const double *y, size_t a, size_t b, const double *cum, const struct hw_cut_ref *g,
		   double xh)
{
	if (b - a < 2) return INFINITY;
	double px[HW_CUT_N], py[HW_CUT_N], mx = 0, my = 0, gmx = 0, gmy = 0;
	cut_resample(x + a, y + a, b - a, px, py);
	for (int z = 0; z < HW_CUT_N; z++) { px[z] /= xh; py[z] = -py[z] / xh; mx += px[z]; my += py[z]; gmx += g->x[z]; gmy += g->y[z]; }
	mx /= HW_CUT_N; my /= HW_CUT_N; gmx /= HW_CUT_N; gmy /= HW_CUT_N;
	double e = 0;
	for (int z = 0; z < HW_CUT_N; z++) {
		double dx = px[z] - mx - (g->x[z] - gmx), dy = py[z] - my - (g->y[z] - gmy);
		e += dx * dx + dy * dy;
	}
	double len = (cum[b - 1] - cum[a]) / xh;
	return sqrt(e / HW_CUT_N) + HW_CUT_LEN * fabs(log(fmax(len, 0.02) / fmax(g->len, 0.02)));
}

int hw_cut_shape(const double *x, const double *y, size_t m, double xh, size_t R, const struct hw_cut_ref *refs,
		 const size_t *off, size_t *at, int *which, double *cost)
{
	if (!R || m < 2 * R || !(xh > 0)) return 1;
	for (size_t r = 0; r < R; r++) if (off[r + 1] <= off[r]) return 1;
	double *cum = malloc((m + 1) * sizeof *cum), *D = malloc((R + 1) * (m + 1) * sizeof *D);
	size_t *P = malloc((R + 1) * (m + 1) * sizeof *P);
	int *Wc = malloc((R + 1) * (m + 1) * sizeof *Wc);
	if (!cum || !D || !P || !Wc) { free(cum); free(D); free(P); free(Wc); return -1; }
	hw_cut_arc(x, y, m, cum);
	/* D[r][b]: the least cost of the first r letters over points [0, b) */
	for (size_t i = 0; i < (R + 1) * (m + 1); i++) D[i] = INFINITY;
	D[0] = 0;
	for (size_t r = 1; r <= R; r++)
		for (size_t b = 2 * r; b <= m; b++) {
			if (r == R && b != m) continue;
			for (size_t a = 2 * (r - 1); a + 2 <= b; a++) {
				double d0 = D[(r - 1) * (m + 1) + a];
				if (!isfinite(d0) || (r == 1 && a)) continue;
				double best = INFINITY;
				int bc = -1;
				for (size_t c = off[r - 1]; c < off[r]; c++) {
					double e = hw_cut_cost(x, y, a, b, cum, &refs[c], xh);
					if (e < best) { best = e; bc = (int)c; }
				}
				double d = d0 + best;
				if (d < D[r * (m + 1) + b]) { D[r * (m + 1) + b] = d; P[r * (m + 1) + b] = a; Wc[r * (m + 1) + b] = bc; }
			}
		}
	int ret = 1;
	if (isfinite(D[R * (m + 1) + m])) {
		size_t b = m;
		at[R] = m;
		for (size_t r = R; r >= 1; r--) {
			at[r - 1] = P[r * (m + 1) + b];
			if (which) which[r - 1] = Wc[r * (m + 1) + b];
			b = at[r - 1];
		}
		if (cost) *cost = D[R * (m + 1) + m];
		ret = 0;
	}
	free(cum); free(D); free(P); free(Wc);
	return ret;
}
