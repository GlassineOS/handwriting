// SPDX-License-Identifier: GPL-2.0-or-later
// Features: a line-by-line port of train/src/glassine_train/features.py (the reference).
// Every step follows NumPy's arithmetic (np.interp, np.linspace, np.quantile 'linear',
// sequential np.cumsum) so the rows agree with the trainer's to float32 rounding. Build
// with -ffp-contract=off: a fused multiply-add changes the last bits, and the resampling
// count (a ceil) could then differ on an exact boundary.
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "featurize.h"

#define STEP 0.1        /* resampling step, in core heights */
#define CLIP 6.0
#define MAX_TILT 0.35   /* radians */

struct pts {
	size_t n;
	double *x, *y, *t;
	int *j;         /* original point index (the last at or before the position) */
};

static void pts_free(struct pts *p) { free(p->x); free(p->y); free(p->t); free(p->j); memset(p, 0, sizeof *p); }

static int pts_alloc(struct pts *p, size_t n)
{
	p->n = n;
	p->x = malloc(n * sizeof *p->x);
	p->y = malloc(n * sizeof *p->y);
	p->t = malloc(n * sizeof *p->t);
	p->j = malloc(n * sizeof *p->j);
	if (!p->x || !p->y || !p->t || !p->j) { pts_free(p); return -1; }
	return 0;
}

/* largest j with xp[j] <= v (xp ascending, xp[0] <= v) */
static size_t search(const double *xp, size_t n, double v)
{
	size_t lo = 0, hi = n;     /* answer in [lo, hi) */
	while (hi - lo > 1) {
		size_t mid = lo + (hi - lo) / 2;
		if (xp[mid] <= v) lo = mid; else hi = mid;
	}
	return lo;
}

/* np.interp(v, xp, fp) for v inside [xp[0], xp[n-1]], with its handling of equal xp */
static double interp(double v, const double *xp, const double *fp, size_t n, size_t j)
{
	if (v < xp[0]) return fp[0];
	if (v > xp[n - 1]) return fp[n - 1];
	if (j == n - 1) return fp[j];
	if (xp[j] == v) return fp[j];
	double slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
	double r = slope * (v - xp[j]) + fp[j];
	if (isnan(r)) {
		r = slope * (v - xp[j + 1]) + fp[j + 1];
		if (isnan(r) && fp[j] == fp[j + 1]) r = fp[j];
	}
	return r;
}

/* _resample: by arc length, about `step` apart, both ends kept */
static int resample(const double *x, const double *y, const double *t, size_t n, double step, struct pts *out)
{
	double *d = malloc(n * sizeof *d);
	if (!d) return -1;
	d[0] = 0;
	for (size_t i = 1; i < n; i++) {
		double dx = x[i] - x[i - 1], dy = y[i] - y[i - 1];
		d[i] = d[i - 1] + sqrt(dx * dx + dy * dy);
	}
	double L = d[n - 1];
	if (n < 2 || L < step) {
		size_t m = (n > 1 && L > 0) ? 2 : 1;
		if (pts_alloc(out, m)) { free(d); return -1; }
		size_t idx[2] = { 0, n - 1 };
		for (size_t k = 0; k < m; k++) {
			out->x[k] = x[idx[k]]; out->y[k] = y[idx[k]]; out->t[k] = t[idx[k]]; out->j[k] = (int)idx[k];
		}
		free(d);
		return 0;
	}
	size_t m = (size_t)ceil(L / step) + 1;
	if (pts_alloc(out, m)) { free(d); return -1; }
	double du = L / (double)(m - 1);
	for (size_t k = 0; k < m; k++) {
		double u = k == m - 1 ? L : (double)k * du + 0.0;
		size_t j = search(d, n, u);
		out->x[k] = interp(u, d, x, n, j);
		out->y[k] = interp(u, d, y, n, j);
		out->t[k] = interp(u, d, t, n, j);
		out->j[k] = (int)j;
	}
	free(d);
	return 0;
}

static int cmpd(const void *a, const void *b)
{
	double x = *(const double *)a, y = *(const double *)b;
	return (x > y) - (x < y);
}

/* np.quantile(sorted, q), method 'linear' */
static double quantile(const double *v, size_t n, double q)
{
	double vi = (double)(n - 1) * q;
	double fl = floor(vi);
	size_t lo = (size_t)fl, hi = lo + 1;
	if (hi > n - 1) hi = n - 1;
	if (lo > n - 1) lo = n - 1;
	double g = vi - fl, a = v[lo], b = v[hi], diff = b - a;
	return g >= 0.5 ? b - diff * (1 - g) : a + diff * g;
}

/* normalise(): tilt removed, scaled by the core height, centred on the line. Fills
 * rot (one pts per stroke, rotated) and the metrics. */
static int normalise(const struct hw_stroke *s, size_t ns, struct pts *rot, double *tilt,
		     double *core, double *mid, double *x0)
{
	size_t N = 0;
	double xmin = INFINITY, xmax = -INFINITY, ymin = INFINITY, ymax = -INFINITY;
	double sx = 0, sy = 0;
	for (size_t i = 0; i < ns; i++)
		for (size_t k = 0; k < s[i].n; k++) {
			double x = s[i].x[k], y = s[i].y[k];
			if (x < xmin) xmin = x;
			if (x > xmax) xmax = x;
			if (y < ymin) ymin = y;
			if (y > ymax) ymax = y;
			sx += x; sy += y; N++;
		}
	double th = 0;
	if (xmax - xmin > 2 * (ymax - ymin)) {
		/* least-squares slope (np.polyfit(x, y, 1)[0]), centred for accuracy */
		double mx = sx / (double)N, my = sy / (double)N, sxy = 0, sxx = 0;
		for (size_t i = 0; i < ns; i++)
			for (size_t k = 0; k < s[i].n; k++) {
				double dx = s[i].x[k] - mx;
				sxy += dx * (s[i].y[k] - my);
				sxx += dx * dx;
			}
		th = -atan(sxy / sxx);
		if (th < -MAX_TILT) th = -MAX_TILT;
		if (th > MAX_TILT) th = MAX_TILT;
	}
	double c = cos(th), sn = sin(th);
	double rxmin = INFINITY, rxmax = -INFINITY, rymin = INFINITY, rymax = -INFINITY;
	for (size_t i = 0; i < ns; i++) {
		if (pts_alloc(&rot[i], s[i].n)) return -1;
		for (size_t k = 0; k < s[i].n; k++) {
			double x = s[i].x[k], y = s[i].y[k];
			double a = x * c - y * sn, b = x * sn + y * c;
			rot[i].x[k] = a; rot[i].y[k] = b; rot[i].t[k] = s[i].t[k]; rot[i].j[k] = (int)k;
			if (a < rxmin) rxmin = a;
			if (a > rxmax) rxmax = a;
			if (b < rymin) rymin = b;
			if (b > rymax) rymax = b;
		}
	}
	double span = rxmax - rxmin > rymax - rymin ? rxmax - rxmin : rymax - rymin;
	if (!(span > 1e-6)) span = 1e-6;
	/* core height from evenly spaced points */
	size_t ne = 0, cap = 0;
	double *ey = NULL, ex0 = INFINITY;
	for (size_t i = 0; i < ns; i++) {
		struct pts e = { 0 };
		if (resample(rot[i].x, rot[i].y, rot[i].t, rot[i].n, span / 400, &e)) { free(ey); return -1; }
		if (ne + e.n > cap) {
			cap = (ne + e.n) * 2;
			double *t = realloc(ey, cap * sizeof *ey);
			if (!t) { pts_free(&e); free(ey); return -1; }
			ey = t;
		}
		for (size_t k = 0; k < e.n; k++) {
			ey[ne++] = e.y[k];
			if (e.x[k] < ex0) ex0 = e.x[k];
		}
		pts_free(&e);
	}
	qsort(ey, ne, sizeof *ey, cmpd);
	double lo = quantile(ey, ne, 0.2), md = quantile(ey, ne, 0.5), hi = quantile(ey, ne, 0.8);
	free(ey);
	double h = hi - lo;
	if (!(h >= span / 50)) h = span / 50;
	*tilt = th; *core = h; *mid = md; *x0 = ex0;
	return 0;
}

/* the non-empty strokes */
static size_t nonempty(const struct hw_stroke *s, size_t ns, struct hw_stroke *ok, int *idx)
{
	size_t m = 0;
	for (size_t i = 0; i < ns; i++)
		if (s[i].n) { ok[m] = s[i]; idx[m] = (int)i; m++; }
	return m;
}

int hw_line_metrics(const struct hw_stroke *s0, size_t ns0, double *tilt, double *core, double *mid, double *x0)
{
	struct hw_stroke *s = malloc((ns0 + 1) * sizeof *s);
	int *idx = malloc((ns0 + 1) * sizeof *idx);
	struct pts *rot = NULL;
	int r = -1;
	if (!s || !idx) goto out;
	size_t ns = nonempty(s0, ns0, s, idx);
	if (!ns) goto out;
	rot = calloc(ns, sizeof *rot);
	if (!rot) goto out;
	r = normalise(s, ns, rot, tilt, core, mid, x0);
	for (size_t i = 0; i < ns; i++) pts_free(&rot[i]);
out:
	free(rot); free(s); free(idx);
	return r;
}

long hw_features(const struct hw_stroke *s0, size_t ns0, float **out, struct hw_origin **origin)
{
	*out = NULL;
	if (origin) *origin = NULL;
	struct hw_stroke *s = malloc((ns0 + 1) * sizeof *s);
	int *idx = malloc((ns0 + 1) * sizeof *idx);
	struct pts *rot = NULL, *rs = NULL;
	double *f = NULL;
	long rows = -1;
	size_t ns = 0;
	if (!s || !idx) goto out;
	ns = nonempty(s0, ns0, s, idx);
	if (!ns) { rows = 0; goto out; }
	rot = calloc(ns, sizeof *rot);
	rs = calloc(ns, sizeof *rs);
	if (!rot || !rs) goto out;
	double th, h, md, x0;
	if (normalise(s, ns, rot, &th, &h, &md, &x0)) goto out;
	size_t total = 0;
	for (size_t i = 0; i < ns; i++) {
		struct pts *q = &rot[i];
		for (size_t k = 0; k < q->n; k++) { q->x[k] = (q->x[k] - x0) / h; q->y[k] = (q->y[k] - md) / h; }
		if (resample(q->x, q->y, q->t, q->n, STEP, &rs[i])) goto out;
		total += rs[i].n;
	}
	f = calloc(total * HW_NFEAT, sizeof *f);
	float *o = malloc(total * HW_NFEAT * sizeof *o);
	struct hw_origin *org = origin ? malloc(total * sizeof *org) : NULL;
	if (!f || !o || (origin && !org)) { free(o); free(org); goto out; }
	size_t row = 0;
	double px = 0, py = 0, pt = 0;
	for (size_t i = 0; i < ns; i++) {
		struct pts *r = &rs[i];
		double sx = i ? px : r->x[0], sy = i ? py : r->y[0];
		for (size_t k = 0; k < r->n; k++, row++) {
			double *g = f + row * HW_NFEAT;
			g[0] = r->x[k] - (k ? r->x[k - 1] : sx);
			g[1] = r->y[k] - (k ? r->y[k - 1] : sy);
			g[2] = r->y[k];
			if (i && !k) {
				double gap = r->t[0] - pt;
				g[3] = 1.0;
				g[4] = log1p((gap > 0.0 ? gap : 0.0) * 10);
			}
			if (org) { org[row].stroke = idx[i]; org[row].point = r->j[k]; }
		}
		px = r->x[r->n - 1]; py = r->y[r->n - 1]; pt = r->t[r->n - 1];
	}
	for (size_t k = 0; k < total * HW_NFEAT; k++) {
		double v = f[k];
		if (v < -CLIP) v = -CLIP;
		if (v > CLIP) v = CLIP;
		o[k] = (float)v;
	}
	*out = o;
	if (origin) *origin = org;
	rows = (long)total;
out:
	if (rot) for (size_t i = 0; i < ns; i++) pts_free(&rot[i]);
	if (rs) for (size_t i = 0; i < ns; i++) pts_free(&rs[i]);
	free(rot); free(rs); free(f); free(s); free(idx);
	return rows;
}
