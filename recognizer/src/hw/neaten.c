// SPDX-License-Identifier: GPL-2.0-or-later
// Neatening (see neaten.h and docs/recognizer.md, "Neatening").
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "neaten.h"

static double clampd(double x, double a, double b) { return x < a ? a : x > b ? b : x; }

/* $GLASSINE_HANDWRITING_NEATEN (measurements): "base=0,slant=0,size=0,space=0,pull=0"
 * leaves a correction out */
static struct { int base, slant, size, space, pull; } on;
static void knobs(void)
{
	on.base = on.slant = on.size = on.space = on.pull = 1;
	const char *e = getenv("GLASSINE_HANDWRITING_NEATEN");
	if (!e) return;
	struct { const char *k; int *v; } kv[] = { { "base=", &on.base }, { "slant=", &on.slant }, { "size=", &on.size },
						 { "space=", &on.space }, { "pull=", &on.pull } };
	for (size_t i = 0; i < sizeof kv / sizeof *kv; i++) {
		const char *p = strstr(e, kv[i].k);
		if (p) *kv[i].v = atoi(p + strlen(kv[i].k)) != 0;
	}
}

static int cmpd(const void *a, const void *b)
{
	double x = *(const double *)a, y = *(const double *)b;
	return (x > y) - (x < y);
}

static double median(double *v, size_t n, double def)
{
	if (!n) return def;
	qsort(v, n, sizeof *v, cmpd);
	return n % 2 ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

/* character classes (Latin; anything else gets no size or baseline of its own) */
enum { C_OTHER, C_X, C_ASC, C_CAP, C_DIGIT };
static int cls_of(unsigned c)
{
	if (c < 128 && c && strchr("acemnorsuvwxz", (int)c)) return C_X;
	if (c < 128 && c && strchr("bdhkl", (int)c)) return C_ASC;
	if (c >= 'A' && c <= 'Z' && c != 'J' && c != 'Q') return C_CAP;
	if (c >= '0' && c <= '9') return C_DIGIT;
	return C_OTHER;
}
/* sits on the baseline (its ink's bottom is the line) */
static int on_base(unsigned c)
{
	return (c >= 'a' && c <= 'z' && !strchr("fgjpqy", (int)c)) || (c >= 'A' && c <= 'Z' && c != 'Q') ||
	       (c >= '0' && c <= '9');
}

/* the height of a class in x-heights */
static double ratio_of(int cls, const struct hw_letters *L)
{
	switch (cls) {
	case C_X: return 1;
	case C_ASC: return L && L->m.asc > 1.1 ? L->m.asc : 1.5;
	case C_CAP: case C_DIGIT: return L && L->m.cap > 1.1 ? L->m.cap : 1.4;
	}
	return 0;
}

/* arc-length resampling of (x, y) to m points */
static void resample(const double *x, const double *y, size_t n, size_t m, double *ox, double *oy)
{
	double total = 0;
	for (size_t i = 1; i < n; i++) total += hypot(x[i] - x[i - 1], y[i] - y[i - 1]);
	if (n < 2 || m < 2 || total <= 0) {
		for (size_t i = 0; i < m; i++) { ox[i] = n ? x[0] : 0; oy[i] = n ? y[0] : 0; }
		return;
	}
	size_t seg = 1;
	double acc = 0;
	for (size_t i = 0; i < m; i++) {
		double want = total * (double)i / (double)(m - 1);
		while (seg < n - 1) {
			double l = hypot(x[seg] - x[seg - 1], y[seg] - y[seg - 1]);
			if (acc + l >= want) break;
			acc += l;
			seg++;
		}
		double l = hypot(x[seg] - x[seg - 1], y[seg] - y[seg - 1]);
		double t = l > 0 ? clampd((want - acc) / l, 0, 1) : 0;
		ox[i] = x[seg - 1] + t * (x[seg] - x[seg - 1]);
		oy[i] = y[seg - 1] + t * (y[seg] - y[seg - 1]);
	}
}

/* the point of a polyline at arc-length fraction u */
static void at_fraction(const double *x, const double *y, size_t n, const double *cum, double u, double *px, double *py)
{
	if (n == 1 || cum[n - 1] <= 0) { *px = x[0]; *py = y[0]; return; }
	double want = clampd(u, 0, 1) * cum[n - 1];
	size_t lo = 1, hi = n - 1;
	while (lo < hi) {
		size_t mid = (lo + hi) / 2;
		if (cum[mid] < want) lo = mid + 1; else hi = mid;
	}
	double l = cum[lo] - cum[lo - 1];
	double t = l > 0 ? (want - cum[lo - 1]) / l : 0;
	*px = x[lo - 1] + t * (x[lo] - x[lo - 1]);
	*py = y[lo - 1] + t * (y[lo] - y[lo - 1]);
}

/* centred RMS distance between strokes (a: y down, ink units) and a variant (y up,
 * x-heights); both resampled by arc length to the variant's points per stroke */
static double dist_to(double *const *sx, double *const *sy, const size_t *sn, size_t ns, double xh,
		      const struct hw_variant *v, double *bcx, double *bcy)
{
	if (ns != v->nstrokes || xh <= 0) return INFINITY;
	size_t tot = 0;
	for (size_t j = 0; j < ns; j++) tot += v->s[j].n < 2 ? 2 : v->s[j].n;
	double *ax = malloc(tot * 4 * sizeof *ax);
	if (!ax) return INFINITY;
	double *ay = ax + tot, *vx = ay + tot, *vy = vx + tot;
	size_t o = 0;
	for (size_t j = 0; j < ns; j++) {
		size_t m = v->s[j].n < 2 ? 2 : v->s[j].n;
		resample(sx[j], sy[j], sn[j], m, ax + o, ay + o);
		resample(v->s[j].x, v->s[j].y, v->s[j].n, m, vx + o, vy + o);
		o += m;
	}
	double acx = 0, acy = 0, vcx = 0, vcy = 0;
	for (size_t i = 0; i < tot; i++) {
		ax[i] /= xh; ay[i] = -ay[i] / xh;
		acx += ax[i]; acy += ay[i]; vcx += vx[i]; vcy += vy[i];
	}
	acx /= tot; acy /= tot; vcx /= tot; vcy /= tot;
	double s = 0;
	for (size_t i = 0; i < tot; i++) {
		double dx = (ax[i] - acx) - (vx[i] - vcx), dy = (ay[i] - acy) - (vy[i] - vcy);
		s += dx * dx + dy * dy;
	}
	free(ax);
	if (bcx) *bcx = vcx;
	if (bcy) *bcy = vcy;
	return sqrt(s / tot);
}

double hw_neat_variant_dist(const struct hw_ink *ink, size_t n, double xh, const struct hw_variant *v)
{
	double **sx = malloc((n + 1) * 2 * sizeof *sx);
	size_t *sn = malloc((n + 1) * sizeof *sn);
	if (!sx || !sn) { free(sx); free(sn); return INFINITY; }
	for (size_t j = 0; j < n; j++) { sx[j] = ink[j].x; sx[n + 1 + j] = ink[j].y; sn[j] = ink[j].n; }
	double d = dist_to(sx, sx + n + 1, sn, n, xh, v, NULL, NULL);
	free(sx); free(sn);
	return d;
}

void hw_neat_drawing(const struct hw_ink *ink, size_t n, double xh, unsigned char *keep)
{
	if (xh <= 0 && n >= 3) {
		/* no reference: the strokes' own heights (most of writing is letters) */
		double *h = malloc(n * sizeof *h);
		if (!h) return;
		for (size_t j = 0; j < n; j++) {
			double y0 = INFINITY, y1 = -INFINITY;
			for (size_t i = 0; i < ink[j].n; i++) { if (ink[j].y[i] < y0) y0 = ink[j].y[i]; if (ink[j].y[i] > y1) y1 = ink[j].y[i]; }
			h[j] = ink[j].n ? y1 - y0 : 0;
		}
		xh = median(h, n, 0);
		free(h);
	}
	if (xh <= 0) return;
	for (size_t j = 0; j < n; j++) {
		double y0 = INFINITY, y1 = -INFINITY;
		for (size_t i = 0; i < ink[j].n; i++) { if (ink[j].y[i] < y0) y0 = ink[j].y[i]; if (ink[j].y[i] > y1) y1 = ink[j].y[i]; }
		if (y1 - y0 > HW_NEAT_DRAWING * xh) keep[j] = 1;
	}
}

void hw_neat_free(struct hw_neat *q)
{
	if (q->x) for (size_t j = 0; j < q->n; j++) free(q->x[j]);
	if (q->y) for (size_t j = 0; j < q->n; j++) free(q->y[j]);
	free(q->x); free(q->y); free(q->how);
	q->x = q->y = NULL;
	q->how = NULL;
}

struct ch {
	int npts, cls, ok, pull;
	double fit;
	double x0, y0, x1, y1, cx;      /* the ink's box (y down) and middle */
	double ax, ay;                  /* the anchor: its middle on the fitted baseline */
	double f, shear, dx, dy;        /* the line-level map */
	double tx0, tx1, ty1;           /* the box after scale and shear */
	double snum, sden;              /* its downstrokes' lean */
	int own;                        /* all its points are whole strokes of its own */
	int ns, *st;                    /* those strokes, in writing order */
	const struct hw_variant *v;     /* the variant it goes to */
};

/* the line-level map of a point of character c */
static void line_map(const struct ch *c, double x, double y, double *ox, double *oy)
{
	double up = c->ay - y;
	double xs = x + c->shear * up;
	*ox = c->ax + c->f * (xs - c->ax) + c->dx;
	*oy = c->ay + c->f * (y - c->ay) + c->dy;
}

int hw_neaten(struct hw_neat *q)
{
	size_t n = q->n;
	q->x = calloc(n + 1, sizeof *q->x);
	q->y = calloc(n + 1, sizeof *q->y);
	q->how = calloc(n + 1, sizeof *q->how);
	if (!q->x || !q->y || !q->how) { hw_neat_free(q); return -1; }
	for (size_t j = 0; j < n; j++) {
		size_t m = q->ink[j].n;
		q->x[j] = malloc((m + 1) * sizeof(double));
		q->y[j] = malloc((m + 1) * sizeof(double));
		if (!q->x[j] || !q->y[j]) { hw_neat_free(q); return -1; }
		memcpy(q->x[j], q->ink[j].x, m * sizeof(double));
		memcpy(q->y[j], q->ink[j].y, m * sizeof(double));
		q->how[j] = HW_NEAT_KEPT;
	}
	knobs();
	double s = clampd(q->strength, 0, 1);
	if (!(s > 0) || !q->nc || !q->own) return 0;

	size_t nc = q->nc;
	struct ch *C = calloc(nc + 1, sizeof *C);
	int *nown = calloc(n + 1, sizeof *nown);         /* characters per stroke */
	int *sole = malloc((n + 1) * sizeof *sole);      /* its one character, -1 several or none */
	if (!C || !nown || !sole) { free(C); free(nown); free(sole); return -1; }
	for (size_t k = 0; k < nc; k++) {
		C[k].x0 = C[k].y0 = INFINITY;
		C[k].x1 = C[k].y1 = -INFINITY;
		C[k].fit = q->fit ? q->fit[k] : 1;
		C[k].cls = cls_of(q->cp[k]);
		C[k].f = 1;
		C[k].own = 1;
	}
	/* a stroke mostly one character's is all its: the alignment's edges spill a few points
	 * over to a neighbour */
	for (size_t j = 0; j < n; j++) {
		const struct hw_ink *k = &q->ink[j];
		int who[16], cnt[16], nw = 0, tot = 0, best = -1, bn = 0;
		for (size_t i = 0; i < k->n; i++) {
			int o = q->own[j][i], w = 0;
			if (o < 0 || (size_t)o >= nc) continue;
			tot++;
			while (w < nw && who[w] != o) w++;
			if (w == nw) { if (nw == 16) continue; who[nw] = o; cnt[nw++] = 0; }
			cnt[w]++;
		}
		for (int w = 0; w < nw; w++) if (cnt[w] > bn) { bn = cnt[w]; best = who[w]; }
		if (best >= 0 && bn < tot && bn * 4 >= tot * 3)
			for (size_t i = 0; i < k->n; i++) if (q->own[j][i] >= 0) q->own[j][i] = best;
	}
	/* the alignment follows the order of writing, so a dot, bar or arm written after the
	 * word sits with its last letter: it goes to the character it lies across, as the letter
	 * model has it (letters.c, spec 003 FR-007); joined strokes stay as they are */
	int **sp = calloc(nc + 1, sizeof *sp);
	for (size_t c = 0; sp && c < nc; c++) {
		sp[c] = malloc((n + 1) * sizeof **sp);
		if (!sp[c]) { for (size_t d = 0; d < c; d++) free(sp[d]); free(sp); sp = NULL; break; }
		int w = 0;
		for (size_t j = 0; j < n; j++) {
			if (q->keep && q->keep[j]) continue;
			for (size_t i = 0; i < q->ink[j].n; i++) if (q->own[j][i] == (int)c) { sp[c][w++] = (int)j; break; }
		}
		sp[c][w] = -1;
	}
	if (sp && !hw_letters_place(q->ink, n, q->xh > 0 ? 5 * q->xh : 0, q->cp, nc, sp))
		for (size_t j = 0; j < n; j++) {
			int to = -1, many = 0;
			for (size_t c = 0; c < nc; c++)
				for (int *p = sp[c]; *p >= 0; p++) if (*p == (int)j) { if (to >= 0) many = 1; to = (int)c; }
			if (to < 0 || many) continue;
			for (size_t i = 0; i < q->ink[j].n; i++) if (q->own[j][i] >= 0) q->own[j][i] = to;
		}
	for (size_t c = 0; sp && c < nc; c++) free(sp[c]);
	free(sp);
	/* the characters' ink */
	for (size_t j = 0; j < n; j++) {
		sole[j] = -1;
		if (q->keep && q->keep[j]) continue;
		const struct hw_ink *k = &q->ink[j];
		int first = -1, many = 0, any = 0;
		for (size_t i = 0; i < k->n; i++) {
			int o = q->own[j][i];
			if (o < 0 || (size_t)o >= nc) continue;
			any = 1;
			if (first < 0) first = o; else if (o != first) many = 1;
			struct ch *c = &C[o];
			c->npts++;
			if (k->x[i] < c->x0) c->x0 = k->x[i];
			if (k->x[i] > c->x1) c->x1 = k->x[i];
			if (k->y[i] < c->y0) c->y0 = k->y[i];
			if (k->y[i] > c->y1) c->y1 = k->y[i];
			if (i && q->own[j][i - 1] == o) {
				double dx = k->x[i] - k->x[i - 1], dy = -(k->y[i] - k->y[i - 1]);   /* y up */
				if (dy < 0 && -dy > 2 * fabs(dx)) { c->snum += dx * dy; c->sden += dy * dy; }
			}
		}
		if (!any) { if (q->keep) q->keep[j] = 1; continue; }
		nown[j] = many ? 2 : 1;
		sole[j] = many ? -1 : first;
		q->how[j] = HW_NEAT_LINE;
		/* a stroke's points that no character has go with their neighbour's */
		int last = -1;
		for (size_t i = 0; i < k->n; i++) { if (q->own[j][i] >= 0 && (size_t)q->own[j][i] < nc) last = q->own[j][i]; else if (last >= 0) q->own[j][i] = last; }
		last = -1;
		for (size_t i = k->n; i-- > 0;) { if (q->own[j][i] >= 0) last = q->own[j][i]; else q->own[j][i] = last; }
	}
	/* which strokes each character has: whole and alone, or shared */
	for (size_t j = 0; j < n; j++) {
		if (!nown[j]) continue;
		for (size_t i = 0; i < q->ink[j].n; i++) {
			int o = q->own[j][i];
			if (o >= 0 && sole[j] != o) C[o].own = 0;
		}
	}
	double sumfit = 0;
	int nwr = 0;
	double wx0 = INFINITY, wx1 = -INFINITY;
	for (size_t k = 0; k < nc; k++) {
		struct ch *c = &C[k];
		if (!c->npts) continue;
		c->cx = (c->x0 + c->x1) / 2;
		sumfit += c->fit > 0 ? c->fit : 0;
		nwr++;
		c->ok = c->fit >= HW_NEAT_FIT_READ;
		if (c->x0 < wx0) wx0 = c->x0;
		if (c->x1 > wx1) wx1 = c->x1;
	}
	int ret = 0;
	double *v = malloc((nc + 1) * 3 * sizeof *v);
	if (!v) { ret = -1; goto done; }
	/* read with low confidence, or not at all: not writing */
	if (!nwr || sumfit / nwr < HW_NEAT_FIT_READ) {
		for (size_t j = 0; j < n; j++) if (q->how[j] == HW_NEAT_LINE) { q->how[j] = HW_NEAT_KEPT; if (q->keep) q->keep[j] = 1; }
		goto done;
	}

	/* the x-height: the characters' heights by their class, else the person's, else the ink */
	size_t nv = 0;
	for (size_t k = 0; k < nc; k++) {
		struct ch *c = &C[k];
		double r = ratio_of(c->cls, q->L);
		if (c->npts && c->ok && r > 0 && c->y1 > c->y0) v[nv++] = (c->y1 - c->y0) / r;
	}
	double xh = median(v, nv, 0);
	if (nv < 2 && q->xh > 0) xh = nv ? (xh + q->xh) / 2 : q->xh;
	if (xh <= 0) {
		nv = 0;
		for (size_t k = 0; k < nc; k++) if (C[k].npts && C[k].y1 > C[k].y0) v[nv++] = C[k].y1 - C[k].y0;
		xh = median(v, nv, 0) / 1.2;
	}
	if (!(xh > 0)) goto done;

	/* the baseline: a line through the bottoms of the letters that sit on it, outliers out */
	int hasbase = 0;
	double ba = 0, bb = 0;
	{
		double *px = v, *py = v + nc + 1;
		int np = 0;
		for (size_t k = 0; k < nc; k++)
			if (C[k].npts && C[k].fit >= HW_NEAT_FIT_PULL && on_base(q->cp[k])) { px[np] = C[k].cx; py[np] = C[k].y1; np++; }
		unsigned char *use = calloc((size_t)np + 1, 1);
		if (np && use) {
			memset(use, 1, (size_t)np);
			for (int pass = 0; pass < 2; pass++) {
				double sx = 0, sy = 0, sxx = 0, sxy = 0;
				int m = 0;
				for (int i = 0; i < np; i++) if (use[i]) { sx += px[i]; sy += py[i]; sxx += px[i] * px[i]; sxy += px[i] * py[i]; m++; }
				if (!m) break;
				double den = m * sxx - sx * sx;
				bb = m >= 2 && den > 1e-9 * (sxx > 1 ? sxx : 1) ? (m * sxy - sx * sy) / den : 0;
				bb = clampd(bb, -0.35, 0.35);
				ba = (sy - bb * sx) / m;
				hasbase = 1;
				if (pass) break;
				int kept = 0;
				for (int i = 0; i < np; i++) { use[i] = fabs(py[i] - (ba + bb * px[i])) <= 0.3 * xh; kept += use[i]; }
				if (!kept) { memset(use, 1, (size_t)np); break; }
			}
		}
		free(use);
	}
	double target_base = ba + bb * (wx0 + wx1) / 2;

	/* the slant: steady, towards the person's (the line's own without a letter model) */
	double snum = 0, sden = 0;
	for (size_t k = 0; k < nc; k++) if (C[k].npts && C[k].ok) { snum += C[k].snum; sden += C[k].sden; }
	double sline = sden > 0 ? snum / sden : 0;
	double starget = q->L ? q->L->m.slant : sline;
	double prior = (0.5 * xh) * (0.5 * xh);

	/* per character: shear and scale about its anchor, its box after them */
	for (size_t k = 0; k < nc; k++) {
		struct ch *c = &C[k];
		if (!c->npts) continue;
		c->ax = c->cx;
		c->ay = hasbase ? ba + bb * c->cx : c->y1;
		if (!c->ok) continue;
		double sc = (c->snum + sline * prior) / (c->sden + prior);
		if (on.slant) c->shear = clampd(starget - sc, -0.4, 0.4);
		double r = ratio_of(c->cls, q->L);
		if (on.size && r > 0 && c->y1 > c->y0 && c->fit >= HW_NEAT_FIT_PULL) c->f = clampd(xh * r / (c->y1 - c->y0), 0.85, 1.18);
	}
	for (size_t k = 0; k < nc; k++) { C[k].tx0 = C[k].ty1 = INFINITY; C[k].tx1 = -INFINITY; C[k].ty1 = -INFINITY; }
	for (size_t j = 0; j < n; j++) {
		if (!nown[j]) continue;
		for (size_t i = 0; i < q->ink[j].n; i++) {
			int o = q->own[j][i];
			if (o < 0) continue;
			struct ch *c = &C[o];
			double x, y;
			line_map(c, q->ink[j].x[i], q->ink[j].y[i], &x, &y);
			if (x < c->tx0) c->tx0 = x;
			if (x > c->tx1) c->tx1 = x;
			if (y > c->ty1) c->ty1 = y;
		}
	}
	/* onto the straight baseline: a letter that sits on it by its bottom, others with the line */
	for (size_t k = 0; k < nc; k++) {
		struct ch *c = &C[k];
		if (!c->npts || !hasbase || !on.base) continue;
		c->dy = target_base - c->ay;
		/* a bottom far off the line is more likely a misread descender than a slip */
		double off = target_base - c->ty1 - c->dy;
		if (c->fit >= HW_NEAT_FIT_PULL && on_base(q->cp[k]) && fabs(off) <= 0.35 * xh) c->dy += off;
	}
	/* even spacing: the gaps between letters and between words towards the line's own */
	{
		double *lg = v, *wg = v + nc + 1;
		size_t nl = 0, nw = 0;
		int prev = -1, space = 0;
		for (size_t k = 0; k < nc; k++) {
			if (!C[k].npts) { if (q->cp[k] == ' ') space = 1; continue; }
			if (prev >= 0) {
				int joined = 0;
				for (size_t j = 0; j < n && !joined; j++) {
					if (nown[j] < 2) continue;
					int a = 0, b = 0;
					for (size_t i = 0; i < q->ink[j].n; i++) { a |= q->own[j][i] == prev; b |= q->own[j][i] == (int)k; }
					joined = a && b;
				}
				double g = C[k].tx0 - C[prev].tx1;
				if (!joined && C[k].ok && C[prev].ok) { if (space) wg[nw++] = g; else lg[nl++] = g; }
			}
			prev = (int)k;
			space = 0;
		}
		double tl = nl >= 2 ? median(lg, nl, 0) : q->L ? q->L->m.letter_gap * xh : NAN;
		double tw = nw >= 2 ? median(wg, nw, 0) : q->L ? q->L->m.word_gap * xh : NAN;
		double shift = 0;
		prev = -1;
		space = 0;
		for (size_t k = 0; k < nc; k++) {
			if (!C[k].npts) { if (q->cp[k] == ' ') space = 1; continue; }
			if (prev >= 0) {
				int joined = 0;
				for (size_t j = 0; j < n && !joined; j++) {
					if (nown[j] < 2) continue;
					int a = 0, b = 0;
					for (size_t i = 0; i < q->ink[j].n; i++) { a |= q->own[j][i] == prev; b |= q->own[j][i] == (int)k; }
					joined = a && b;
				}
				double g = C[k].tx0 - C[prev].tx1, t = space ? tw : tl;
				if (on.space && !joined && C[k].ok && C[prev].ok && isfinite(t)) shift += clampd(t - g, -0.3 * xh, 0.3 * xh);
			}
			C[k].dx = shift;
			prev = (int)k;
			space = 0;
		}
	}

	/* the pull: a character read well, covered, with strokes of its own, near a variant */
	if (q->L && on.pull && !q->line_only) {
		for (size_t k = 0; k < nc; k++) {
			struct ch *c = &C[k];
			if (!c->npts || !c->own || c->fit < HW_NEAT_FIT_PULL) continue;
			const struct hw_letter *le = NULL;
			size_t lo = 0, hi = q->L->n;
			while (lo < hi) {
				size_t mid = (lo + hi) / 2;
				if (q->L->l[mid].cp < q->cp[k]) lo = mid + 1; else hi = mid;
			}
			if (lo < q->L->n && q->L->l[lo].cp == q->cp[k]) le = &q->L->l[lo];
			if (!le || !le->covered || !le->nvar) continue;
			int ns = 0;
			for (size_t j = 0; j < n; j++) ns += sole[j] == (int)k;
			c->st = malloc((size_t)(ns + 1) * sizeof *c->st);
			double **sx = malloc((size_t)(ns + 1) * 2 * sizeof *sx);
			size_t *sn = malloc((size_t)(ns + 1) * sizeof *sn);
			if (!c->st || !sx || !sn) { free(sx); free(sn); continue; }
			c->ns = 0;
			for (size_t j = 0; j < n; j++) if (sole[j] == (int)k) c->st[c->ns++] = (int)j;
			int bad = 0;
			for (int a = 0; a < c->ns; a++) {
				size_t j = (size_t)c->st[a], m = q->ink[j].n;
				sx[a] = malloc((m + 1) * sizeof(double));
				sx[ns + 1 + a] = malloc((m + 1) * sizeof(double));
				sn[a] = m;
				if (!sx[a] || !sx[ns + 1 + a]) { bad = 1; continue; }
				for (size_t i = 0; i < m; i++) line_map(c, q->ink[j].x[i], q->ink[j].y[i], &sx[a][i], &sx[ns + 1 + a][i]);
			}
			double best = INFINITY;
			for (int t = 0; !bad && t < le->nvar; t++) {
				/* a version made of one sample is that sample, jitter and all */
				if (le->var[t].members < 2) continue;
				double d = dist_to(sx, sx + ns + 1, sn, (size_t)ns, xh, &le->var[t], NULL, NULL);
				if (d < best - 1e-12) { best = d; c->v = &le->var[t]; }
			}
			for (int a = 0; a < c->ns; a++) { free(sx[a]); free(sx[ns + 1 + a]); }
			free(sx); free(sn);
			c->pull = !bad && c->v && best <= HW_NEAT_NEAR;
		}
	}

	/* the points */
	for (size_t j = 0; j < n; j++) {
		if (!nown[j]) continue;
		const struct hw_ink *k = &q->ink[j];
		for (size_t i = 0; i < k->n; i++) {
			int o = k->n ? q->own[j][i] : -1;
			if (o < 0) continue;
			double x, y;
			line_map(&C[o], k->x[i], k->y[i], &x, &y);
			q->x[j][i] = k->x[i] + s * (x - k->x[i]);
			q->y[j][i] = k->y[i] + s * (y - k->y[i]);
		}
	}
	for (size_t ci = 0; ci < nc; ci++) {
		struct ch *c = &C[ci];
		if (!c->pull) continue;
		const struct hw_variant *va = c->v;
		/* the character's middle after the line-level map, both resampled by arc length */
		double acx = 0;
		size_t tot = 0;
		for (int a = 0; a < c->ns; a++) {
			size_t j = (size_t)c->st[a], m = q->ink[j].n, r = va->s[a].n < 2 ? 2 : va->s[a].n;
			double *tx = malloc((m + 1) * 2 * sizeof *tx), *rx = malloc((r + 1) * 2 * sizeof *rx);
			if (tx && rx) {
				for (size_t i = 0; i < m; i++) line_map(c, q->ink[j].x[i], q->ink[j].y[i], &tx[i], &tx[m + 1 + i]);
				resample(tx, tx + m + 1, m, r, rx, rx + r + 1);
				for (size_t i = 0; i < r; i++) acx += rx[i];
				tot += r;
			}
			free(tx); free(rx);
		}
		double vcx = 0, vcy = 0;
		size_t vt = 0;
		for (int a = 0; a < c->ns; a++) {
			size_t r = va->s[a].n < 2 ? 2 : va->s[a].n;
			double *rx = malloc((r + 1) * 2 * sizeof *rx);
			if (rx) {
				resample(va->s[a].x, va->s[a].y, va->s[a].n, r, rx, rx + r + 1);
				for (size_t i = 0; i < r; i++) { vcx += rx[i]; vcy += rx[r + 1 + i]; }
				vt += r;
			}
			free(rx);
		}
		if (!tot || !vt) continue;
		acx /= tot; vcx /= vt; vcy /= vt;
		for (int a = 0; a < c->ns; a++) {
			size_t j = (size_t)c->st[a], m = q->ink[j].n, vn = va->s[a].n;
			const struct hw_ink *k = &q->ink[j];
			double *tx = malloc((m + 1) * 3 * sizeof *tx), *cum = malloc((vn + 1) * sizeof *cum);
			if (!tx || !cum || !vn) { free(tx); free(cum); continue; }
			double *ty = tx + m + 1, *u = ty + m + 1;
			for (size_t i = 0; i < m; i++) line_map(c, k->x[i], k->y[i], &tx[i], &ty[i]);
			u[0] = 0;
			for (size_t i = 1; i < m; i++) u[i] = u[i - 1] + hypot(tx[i] - tx[i - 1], ty[i] - ty[i - 1]);
			double L = m ? u[m - 1] : 0;
			cum[0] = 0;
			for (size_t i = 1; i < vn; i++) cum[i] = cum[i - 1] + hypot(va->s[a].x[i] - va->s[a].x[i - 1], va->s[a].y[i] - va->s[a].y[i - 1]);
			double tcy = 0;
			for (size_t z = 0; z < m; z++) tcy += ty[z];
			tcy /= m ? (double)m : 1;
			for (size_t i = 0; i < m; i++) {
				double fx, fy;
				at_fraction(va->s[a].x, va->s[a].y, vn, cum, L > 0 ? u[i] / L : (double)i / (m > 1 ? m - 1 : 1), &fx, &fy);
				double gx = acx + (fx - vcx) * xh;
				double gy;
				if (hasbase) gy = target_base - fy * xh;
				else gy = tcy - (fy - vcy) * xh;   /* no baseline: the variant's middle on the character's */
				/* the line-level map and the pull together: at full pull the variant itself */
				q->x[j][i] = k->x[i] + s * (gx - k->x[i]);
				q->y[j][i] = k->y[i] + s * (gy - k->y[i]);
			}
			q->how[j] = HW_NEAT_PULLED;
			free(tx); free(cum);
		}
	}
	/* strokes shared by characters: the moves blended where one gives way to the next */
	for (size_t j = 0; j < n; j++) {
		if (nown[j] < 2) continue;
		size_t m = q->ink[j].n;
		int R = (int)clampd((double)m / 30, 2, 12);
		double *dx = malloc((m + 1) * 2 * sizeof *dx);
		if (!dx) continue;
		double *dy = dx + m + 1;
		for (size_t i = 0; i < m; i++) { dx[i] = q->x[j][i] - q->ink[j].x[i]; dy[i] = q->y[j][i] - q->ink[j].y[i]; }
		for (size_t i = 0; i < m; i++) {
			/* only near a change of character */
			int near = 0;
			for (int d = -R; d <= R && !near; d++) {
				long a = (long)i + d;
				if (a > 0 && (size_t)a < m && q->own[j][a] != q->own[j][a - 1]) near = 1;
			}
			if (!near) continue;
			double sx = 0, sy = 0;
			int cnt = 0;
			for (int d = -R; d <= R; d++) {
				long a = (long)i + d;
				if (a < 0 || (size_t)a >= m) continue;
				sx += dx[a]; sy += dy[a]; cnt++;
			}
			q->x[j][i] = q->ink[j].x[i] + sx / cnt;
			q->y[j][i] = q->ink[j].y[i] + sy / cnt;
		}
		free(dx);
	}
done:
	free(v);
	for (size_t k = 0; k < nc; k++) free(C[k].st);
	free(C); free(nown); free(sole);
	return ret;
}
