// SPDX-License-Identifier: GPL-2.0-or-later
#include <math.h>
#include <stdlib.h>

#include "aligner.h"
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

static int cut_dp(const double *x, const double *y, size_t m, double xh, size_t R, const struct hw_cut_ref *refs,
		  const size_t *off, size_t *at, int *which, double *cost);

int hw_cut_shape(const double *x, const double *y, size_t m, double xh, size_t R, const struct hw_cut_ref *refs,
		 const size_t *off, size_t *at, int *which, double *cost, const struct hw_cut_ctx *ctx)
{
	if (!R || m < 2 * R || !(xh > 0)) return 1;
	/* the cut is placed at no more than HW_CUT_PTS points (and HW_CUT_PER per letter) of the
	 * stroke: every step-th and the last (the work grows with the cube of the points) */
	size_t cap = HW_CUT_PTS > HW_CUT_PER * R ? HW_CUT_PTS : HW_CUT_PER * R;
	if (m <= cap) return cut_dp(x, y, m, xh, R, refs, off, at, which, cost);
	size_t step = (m + cap - 1) / cap, nd = 0;
	size_t *idx = malloc((m / step + 2) * sizeof *idx);
	double *dx = malloc((m / step + 2) * 2 * sizeof *dx);
	if (!idx || !dx) { free(idx); free(dx); return -1; }
	for (size_t i = 0; i < m; i += step) idx[nd++] = i;
	if (idx[nd - 1] != m - 1) idx[nd++] = m - 1;
	double *dy = dx + nd;
	for (size_t i = 0; i < nd; i++) { dx[i] = x[idx[i]]; dy[i] = y[idx[i]]; }
	int r = cut_dp(dx, dy, nd, xh, R, refs, off, at, which, cost);
	if (!r) {
		for (size_t k = 0; k < R; k++) at[k] = idx[at[k]];
		at[R] = m;
	}
	free(idx); free(dx);
	return r;
}

static int cut_dp(const double *x, const double *y, size_t m, double xh, size_t R, const struct hw_cut_ref *refs,
		  const size_t *off, size_t *at, int *which, double *cost)
{
	if (m < 2 * R) return 1;
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

int hw_cut_aligner(const double *x, const double *y, size_t m, double xh, size_t R, const struct hw_cut_ref *refs,
		   const size_t *off, size_t *at, int *which, double *cost, const struct hw_cut_ctx *ctx)
{
	int ok = ctx && ctx->a && ctx->frame && ctx->cls && R && m >= 2 * R && xh > 0;
	for (size_t r = 0; ok && r < R; r++) ok = ctx->cls[r] > 0 && off[r + 1] > off[r];
	if (!ok) return hw_cut_shape(x, y, m, xh, R, refs, off, at, which, cost, ctx);
	long f0 = ctx->frame[0], f1 = ctx->frame[m - 1];
	if (f1 < f0) return hw_cut_shape(x, y, m, xh, R, refs, off, at, which, cost, ctx);
	int *lab = malloc((size_t)(f1 - f0 + 1) * sizeof *lab);
	double *cum = malloc((m + 1) * sizeof *cum);
	if (!lab || !cum) { free(lab); free(cum); return -1; }
	int r = hw_aligner_split(ctx->a, f0, f1, ctx->cls, R, lab);
	if (r < 0) { free(lab); free(cum); return -1; }
	if (!r) {
		/* letter k's part from the first point whose frame is k's or later's */
		size_t i = 0;
		at[0] = 0;
		for (size_t k = 1; k < R; k++) {
			while (i < m && lab[ctx->frame[i] - f0] < (int)k) i++;
			at[k] = i;
		}
		at[R] = m;
		for (size_t k = 0; k < R && !r; k++) if (at[k + 1] < at[k] + 2) r = 1;
	}
	free(lab);
	if (r) { free(cum); return hw_cut_shape(x, y, m, xh, R, refs, off, at, which, cost, ctx); }
	hw_cut_arc(x, y, m, cum);
	double tot = 0;
	for (size_t k = 0; k < R; k++) {
		double best = INFINITY;
		int bc = (int)off[k];
		for (size_t c = off[k]; c < off[k + 1]; c++) {
			double e = hw_cut_cost(x, y, at[k], at[k + 1], cum, &refs[c], xh);
			if (e < best) { best = e; bc = (int)c; }
		}
		if (which) which[k] = bc;
		tot += best;
	}
	if (cost) *cost = tot;
	free(cum);
	return 0;
}
