// SPDX-License-Identifier: GPL-2.0-or-later
// Spacing from the letters' ink profiles (spec 003 FR-006, FR-007): one model of how far
// apart the person sets their letters, for the font (ttf.c) and neatening (neaten.c).
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "letters.h"

#define BAND ((HW_PROF_Y1 - HW_PROF_Y0) / HW_PROF_BANDS)
#define SIDE_NEAR 0.3          /* x-heights (RMS): sides this close are one side shape */
#define SIDE_FAR 1.0            /* a band reaching further in than this counts as this */
#define SIDE_NONE 1.2           /* a band without ink */
#define GAP_MIN (-0.6)          /* x-heights: the gaps learned from */
#define GAP_MAX 1.6

void hw_profile_clear(struct hw_profile *p)
{
	for (int b = 0; b < HW_PROF_BANDS; b++) p->l[b] = p->r[b] = NAN;
}

int hw_profile_empty(const struct hw_profile *p)
{
	for (int b = 0; b < HW_PROF_BANDS; b++) if (!isnan(p->l[b])) return 0;
	return 1;
}

static void put(struct hw_profile *p, int b, double x)
{
	if (isnan(p->l[b]) || x < p->l[b]) p->l[b] = x;
	if (isnan(p->r[b]) || x > p->r[b]) p->r[b] = x;
}

static double clampy(double y)
{
	double lo = HW_PROF_Y0 + 1e-9, hi = HW_PROF_Y1 - 1e-9;
	return y < lo ? lo : y > hi ? hi : y;
}

static int band_of(double y)
{
	int b = (int)floor((clampy(y) - HW_PROF_Y0) / BAND);
	return b < 0 ? 0 : b >= HW_PROF_BANDS ? HW_PROF_BANDS - 1 : b;
}

void hw_profile_seg(struct hw_profile *p, double x0, double y0, double x1, double y1)
{
	if (!isfinite(x0) || !isfinite(y0) || !isfinite(x1) || !isfinite(y1)) return;
	/* the first and the last band are open-ended: ink beyond them counts in them */
	y0 = clampy(y0);
	y1 = clampy(y1);
	int b0 = band_of(y0), b1 = band_of(y1);
	put(p, b0, x0);
	put(p, b1, x1);
	if (b0 == b1) return;
	int lo = b0 < b1 ? b0 : b1, hi = b0 < b1 ? b1 : b0;
	/* where it crosses each band edge between */
	for (int b = lo + 1; b <= hi; b++) {
		double ye = HW_PROF_Y0 + b * BAND, t = (ye - y0) / (y1 - y0), x = x0 + t * (x1 - x0);
		put(p, b - 1, x);
		put(p, b, x);
	}
}

double hw_profile_gap(const struct hw_profile *a, const struct hw_profile *b, double shift)
{
	double g = INFINITY, ar = -INFINITY, bl = INFINITY;
	for (int k = 0; k < HW_PROF_BANDS; k++) {
		if (!isnan(a->r[k]) && a->r[k] > ar) ar = a->r[k];
		if (!isnan(b->l[k]) && b->l[k] < bl) bl = b->l[k];
		if (isnan(a->r[k]) || isnan(b->l[k])) continue;
		double d = b->l[k] + shift - a->r[k];
		if (d < g) g = d;
	}
	if (isfinite(g)) return g;
	return isfinite(ar) && isfinite(bl) ? bl + shift - ar : 0;
}

double hw_profile_open(const struct hw_profile *a, const struct hw_profile *b)
{
	double g = hw_profile_gap(a, b, 0), s = 0;
	int n = 0;
	for (int k = 0; k < HW_PROF_BANDS; k++) {
		if (isnan(a->r[k]) || isnan(b->l[k])) continue;
		double d = b->l[k] - a->r[k] - g;
		s += d < SIDE_FAR ? d : SIDE_FAR;
		n++;
	}
	return n >= 2 ? s / n : 0;
}

void hw_mid_seg(struct hw_mid *m, double x0, double y0, double x1, double y1)
{
	if (!isfinite(x0) || !isfinite(y0) || !isfinite(x1) || !isfinite(y1) || y0 == y1) return;
	/* the part between the baseline and the x-height */
	double t0 = (HW_MID_Y0 - y0) / (y1 - y0), t1 = (HW_MID_Y1 - y0) / (y1 - y0);
	if (t0 > t1) { double t = t0; t0 = t1; t1 = t; }
	if (t0 < 0) t0 = 0;
	if (t1 > 1) t1 = 1;
	if (t1 <= t0) return;
	double w = fabs(y1 - y0) * (t1 - t0), x = x0 + (x1 - x0) * (t0 + t1) / 2;
	m->sx += w * x;
	m->sw += w;
}

double hw_mid_of(const struct hw_mid *m)
{
	return m->sw > 1e-9 ? m->sx / m->sw : NAN;
}

/* a side's shape: per band, how far it lies in from the side's outermost ink */
static void side_shape(const struct hw_profile *p, int side, double *v)
{
	double ext = side ? -INFINITY : INFINITY;
	for (int k = 0; k < HW_PROF_BANDS; k++) {
		double x = side ? p->r[k] : p->l[k];
		if (isnan(x)) continue;
		if (side ? x > ext : x < ext) ext = x;
	}
	for (int k = 0; k < HW_PROF_BANDS; k++) {
		double x = side ? p->r[k] : p->l[k];
		if (isnan(x) || !isfinite(ext)) { v[k] = SIDE_NONE; continue; }
		double d = side ? ext - x : x - ext;
		v[k] = d < SIDE_FAR ? d : SIDE_FAR;
	}
}

static double vdist(const double *a, const double *b)
{
	double s = 0;
	for (int k = 0; k < HW_PROF_BANDS; k++) s += (a[k] - b[k]) * (a[k] - b[k]);
	return sqrt(s / HW_PROF_BANDS);
}

int hw_spacing_side(const struct hw_spacing *sp, const struct hw_profile *p, int side)
{
	int n = side ? sp->nr : sp->nl;
	if (!n || hw_profile_empty(p)) return -1;
	double v[HW_PROF_BANDS], bd = INFINITY;
	side_shape(p, side, v);
	int best = -1;
	for (int c = 0; c < n; c++) {
		double d = vdist(v, side ? sp->rshape[c] : sp->lshape[c]);
		if (d < bd - 1e-12) { bd = d; best = c; }
	}
	return best;
}

static int cmp_pair(const void *x, const void *y)
{
	const struct hw_pair *a = x, *b = y;
	return a->a != b->a ? (a->a < b->a ? -1 : 1) : a->b != b->b ? (a->b < b->b ? -1 : 1) : 0;
}

static int cmp_side(const void *x, const void *y)
{
	const struct hw_side_pair *a = x, *b = y;
	return a->rc != b->rc ? a->rc - b->rc : a->lc - b->lc;
}

double hw_spacing_gap(const struct hw_spacing *sp, unsigned a, const struct hw_profile *pa, int rc,
		      unsigned b, const struct hw_profile *pb, int lc)
{
	struct hw_pair kp = { a, b, 0, 0, 0 };
	const struct hw_pair *hp = sp->npairs ? bsearch(&kp, sp->pairs, sp->npairs, sizeof kp, cmp_pair) : NULL;
	if (hp) return hp->gap;
	if (rc >= 0 && lc >= 0 && sp->nsides) {
		struct hw_side_pair ks = { rc, lc, 0, 0 };
		const struct hw_side_pair *hs = bsearch(&ks, sp->sides, sp->nsides, sizeof ks, cmp_side);
		if (hs) return hs->gap;
	}
	/* from the profiles alone: closer where they leave more open space */
	double g = sp->letter;
	if (pa && pb && sp->open > 0) g -= sp->open * (hw_profile_open(pa, pb) - sp->open0);
	if (g < sp->letter - 0.3) g = sp->letter - 0.3;
	if (g > sp->letter + 0.3) g = sp->letter + 0.3;
	return g;
}

static int cmp_reach(const void *x, const void *y)
{
	unsigned a = ((const struct hw_reach *)x)->cp, b = ((const struct hw_reach *)y)->cp;
	return a < b ? -1 : a > b;
}

double hw_spacing_place(const struct hw_spacing *sp, unsigned a, const struct hw_profile *pa, double ma, int rc,
			unsigned b, const struct hw_profile *pb, double mb, int lc)
{
	double gap = hw_spacing_gap(sp, a, pa, rc, b, pb, lc);
	/* set at the gap */
	double s = gap - hw_profile_gap(pa, pb, 0);
	if (!isfinite(ma) || !isfinite(mb)) return s;
	/* the middles' distance: the pair's own, or the two characters' reaches */
	double d = NAN;
	struct hw_pair kp = { a, b, 0, 0, 0 };
	const struct hw_pair *hp = sp->npairs ? bsearch(&kp, sp->pairs, sp->npairs, sizeof kp, cmp_pair) : NULL;
	if (hp) d = hp->mid;
	if (!isfinite(d) && sp->nreach) {
		struct hw_reach ka = { a, 0, 0, 0, 0 }, kb = { b, 0, 0, 0, 0 };
		const struct hw_reach *ra = bsearch(&ka, sp->reach, sp->nreach, sizeof ka, cmp_reach);
		const struct hw_reach *rb = bsearch(&kb, sp->reach, sp->nreach, sizeof kb, cmp_reach);
		if (ra && rb) d = ra->right + rb->left;
	}
	if (!isfinite(d)) return s;
	/* guarded: never closer than the gap, or half the typical gap where that is smaller,
	 * between the baseline and the x-height (a bar or a hook may reach over the next letter) */
	double at = d + ma - mb, floor = gap < sp->letter / 2 ? gap : sp->letter / 2, g = INFINITY;
	for (int k = 0; k < HW_PROF_BANDS; k++) {
		double y0 = HW_PROF_Y0 + k * BAND, y1 = y0 + BAND;
		if (y1 <= HW_MID_Y0 || y0 >= HW_MID_Y1 || isnan(pa->r[k]) || isnan(pb->l[k])) continue;
		double x = pb->l[k] + at - pa->r[k];
		if (x < g) g = x;
	}
	return g < floor ? at + floor - g : at;
}

void hw_spacing_free(struct hw_spacing *sp)
{
	free(sp->pairs);
	free(sp->sides);
	free(sp->reach);
	memset(sp, 0, sizeof *sp);
}

static int cmp_d(const void *a, const void *b)
{
	double x = *(const double *)a, y = *(const double *)b;
	return x < y ? -1 : x > y;
}

static double med(double *v, size_t n, double def)
{
	if (!n) return def;
	qsort(v, n, sizeof *v, cmp_d);
	return n % 2 ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

/* side shapes: the variants' sides, the most supported first (greedy, in the letters'
 * order, so the same model gives the same shapes) */
static int side_classes(struct hw_letters *L, int side, double (*shape)[HW_PROF_BANDS])
{
	size_t nv = 0;
	for (size_t i = 0; i < L->n; i++) nv += (size_t)L->l[i].nvar;
	double (*v)[HW_PROF_BANDS] = malloc((nv + 1) * sizeof *v);
	int *cls = malloc((nv + 1) * sizeof *cls), *wt = malloc((nv + 1) * sizeof *wt);
	if (!v || !cls || !wt) { free(v); free(cls); free(wt); return -1; }
	size_t k = 0;
	for (size_t i = 0; i < L->n; i++)
		for (int j = 0; j < L->l[i].nvar; j++) {
			side_shape(&L->l[i].var[j].prof, side, v[k]);
			wt[k] = L->l[i].var[j].use > 0 ? L->l[i].var[j].use : 1;
			cls[k++] = -1;
		}
	int nc = 0;
	while (nc < HW_SIDE_CLASSES) {
		long bs = 0;
		int bi = -1;
		for (size_t x = 0; x < nv; x++) {
			if (cls[x] >= 0) continue;
			long s = 0;
			for (size_t y = 0; y < nv; y++) if (cls[y] < 0 && vdist(v[x], v[y]) <= SIDE_NEAR) s += wt[y];
			if (s > bs) { bs = s; bi = (int)x; }
		}
		if (bi < 0) break;
		memcpy(shape[nc], v[bi], sizeof shape[nc]);
		for (size_t y = 0; y < nv; y++) if (cls[y] < 0 && vdist(v[bi], v[y]) <= SIDE_NEAR) cls[y] = nc;
		nc++;
	}
	/* the variants: their nearest */
	k = 0;
	for (size_t i = 0; i < L->n; i++)
		for (int j = 0; j < L->l[i].nvar; j++, k++) {
			double bd = INFINITY;
			int best = 0;
			for (int c = 0; c < nc; c++) { double d = vdist(v[k], shape[c]); if (d < bd - 1e-12) { bd = d; best = c; } }
			if (side) L->l[i].var[j].rc = nc ? best : -1; else L->l[i].var[j].lc = nc ? best : -1;
		}
	free(v); free(cls); free(wt);
	return nc;
}

struct obs_ix { unsigned a, b; int rc, lc; double gap, open, d; int run; };

static int cmp_obs_pair(const void *x, const void *y)
{
	const struct obs_ix *a = x, *b = y;
	if (a->a != b->a) return a->a < b->a ? -1 : 1;
	if (a->b != b->b) return a->b < b->b ? -1 : 1;
	return a->gap < b->gap ? -1 : a->gap > b->gap;
}

static int cmp_obs_side(const void *x, const void *y)
{
	const struct obs_ix *a = x, *b = y;
	if (a->rc != b->rc) return a->rc - b->rc;
	if (a->lc != b->lc) return a->lc - b->lc;
	return a->gap < b->gap ? -1 : a->gap > b->gap;
}

/* the characters' reaches: the middles' distance of every pair as the first's right reach
 * plus the second's left reach, by alternating medians (the same pairs, the same reaches);
 * x: room for n numbers */
static int reaches(struct hw_spacing *sp, const struct obs_ix *ix, size_t n, double *x)
{
	size_t nc = 0;
	struct hw_reach *r = malloc((2 * n + 1) * sizeof *r);
	if (!r) return -1;
	for (size_t i = 0; i < n; i++) {
		if (!isfinite(ix[i].d)) continue;
		r[nc++] = (struct hw_reach){ ix[i].a, NAN, NAN, 0, 0 };
		r[nc++] = (struct hw_reach){ ix[i].b, NAN, NAN, 0, 0 };
	}
	qsort(r, nc, sizeof *r, cmp_reach);
	size_t u = 0;
	for (size_t i = 0; i < nc; i++) if (!u || r[u - 1].cp != r[i].cp) r[u++] = r[i];
	nc = u;
	for (size_t i = 0; i < n; i++) {
		if (!isfinite(ix[i].d)) continue;
		struct hw_reach ka = { ix[i].a, 0, 0, 0, 0 }, kb = { ix[i].b, 0, 0, 0, 0 };
		((struct hw_reach *)bsearch(&ka, r, nc, sizeof ka, cmp_reach))->nr++;
		((struct hw_reach *)bsearch(&kb, r, nc, sizeof kb, cmp_reach))->nl++;
	}
	for (size_t c = 0; c < nc; c++) if (r[c].nl >= HW_PAIR_MIN) r[c].left = 0;
	for (int it = 0; it < 8; it++)
		for (int side = 1; side >= 0; side--)
			for (size_t c = 0; c < nc; c++) {
				if ((side ? r[c].nr : r[c].nl) < HW_PAIR_MIN) continue;
				size_t k = 0;
				for (size_t i = 0; i < n; i++) {
					if (!isfinite(ix[i].d) || (side ? ix[i].a : ix[i].b) != r[c].cp) continue;
					struct hw_reach ko = { side ? ix[i].b : ix[i].a, 0, 0, 0, 0 };
					const struct hw_reach *o = bsearch(&ko, r, nc, sizeof ko, cmp_reach);
					double other = side ? o->left : o->right;
					if (isfinite(other)) x[k++] = ix[i].d - other;
				}
				if (k) { if (side) r[c].right = med(x, k, 0); else r[c].left = med(x, k, 0); }
			}
	/* the characters with both, or one, learned */
	u = 0;
	for (size_t c = 0; c < nc; c++) {
		if (r[c].nr < HW_PAIR_MIN) r[c].right = NAN;
		if (r[c].nl < HW_PAIR_MIN) r[c].left = NAN;
		if (!isfinite(r[c].right) && !isfinite(r[c].left)) continue;
		if (isfinite(r[c].right)) r[c].right = round(r[c].right * 1e4) / 1e4;
		if (isfinite(r[c].left)) r[c].left = round(r[c].left * 1e4) / 1e4;
		r[u++] = r[c];
	}
	sp->reach = r;
	sp->nreach = u;
	return 0;
}

/* the median of the middles' distances of o[i..j) written in running text (run 1) or in
 * single words (0), in x[]; NAN with fewer than HW_PAIR_MIN */
static double run_med(const struct obs_ix *o, size_t i, size_t j, int run, double *x)
{
	size_t k = 0;
	for (size_t q = i; q < j; q++) if (o[q].run == run && isfinite(o[q].d)) x[k++] = o[q].d;
	return k >= HW_PAIR_MIN ? med(x, k, 0) : NAN;
}

/* Running text (FR-007): a pair written HW_PAIR_MIN times in running text keeps only those;
 * the others' single-word observations come closer by the person's ratio sp->run (their
 * distance times it, their gap less what that takes off). The observations left; x: room
 * for n numbers. */
static size_t running_text(struct hw_spacing *sp, struct obs_ix *ix, size_t n, double *x)
{
	qsort(ix, n, sizeof *ix, cmp_obs_pair);
	/* the ratio, from the pairs written often enough both ways */
	double *r = malloc((n + 1) * sizeof *r), dw0;
	size_t nr = 0, nd = 0;
	sp->run = 1;
	if (!r) return n;
	for (size_t i = 0; i < n;) {
		size_t j = i;
		while (j < n && ix[j].a == ix[i].a && ix[j].b == ix[i].b) j++;
		double dr = run_med(ix, i, j, 1, x), dw = run_med(ix, i, j, 0, x);
		if (isfinite(dr) && isfinite(dw) && dw > 0.05) r[nr++] = dr / dw;
		i = j;
	}
	if (nr >= HW_RUN_PAIRS) {
		double k = med(r, nr, 1);
		sp->run = round((k < HW_RUN_MIN ? HW_RUN_MIN : k > HW_RUN_MAX ? HW_RUN_MAX : k) * 1e4) / 1e4;
	}
	free(r);
	/* the single words' typical distance, for an observation without middles */
	for (size_t i = 0; i < n; i++) if (!ix[i].run && isfinite(ix[i].d)) x[nd++] = ix[i].d;
	dw0 = med(x, nd, 1);
	size_t w = 0;
	for (size_t i = 0; i < n;) {
		size_t j = i, nrun = 0;
		while (j < n && ix[j].a == ix[i].a && ix[j].b == ix[i].b) j++;
		for (size_t q = i; q < j; q++) nrun += ix[q].run;
		for (size_t q = i; q < j; q++) {
			struct obs_ix e = ix[q];
			if (nrun >= HW_PAIR_MIN && !e.run) continue;
			if (!e.run) {
				e.gap -= (1 - sp->run) * (isfinite(e.d) ? e.d : dw0);
				e.d *= sp->run;
			}
			ix[w++] = e;
		}
		i = j;
	}
	return w;
}

int hw_spacing_learn(struct hw_letters *L, const struct hw_gap_obs *o, size_t n, const double *words, size_t nw)
{
	struct hw_spacing *sp = &L->sp;
	hw_spacing_free(sp);
	int nl = side_classes(L, 0, sp->lshape), nr = side_classes(L, 1, sp->rshape);
	if (nl < 0 || nr < 0) return -1;
	sp->nl = nl;
	sp->nr = nr;
	double *g = malloc((n + nw + 1) * sizeof *g), *x = malloc((n + 1) * sizeof *x);
	struct obs_ix *ix = malloc((n + 1) * sizeof *ix);
	if (!g || !x || !ix) { free(g); free(x); free(ix); return -1; }
	/* a gap far outside handwriting (ink the alignment put with the wrong letter) is left out */
	size_t m0 = 0;
	for (size_t i = 0; i < n; i++) {
		if (!(o[i].gap >= GAP_MIN && o[i].gap <= GAP_MAX)) continue;
		ix[m0++] = (struct obs_ix){ o[i].a, o[i].b, hw_spacing_side(sp, &o[i].pa, 1), hw_spacing_side(sp, &o[i].pb, 0),
					   o[i].gap, hw_profile_open(&o[i].pa, &o[i].pb), o[i].mb - o[i].ma, o[i].run };
	}
	n = running_text(sp, ix, m0, x);
	for (size_t i = 0; i < n; i++) g[i] = ix[i].gap;
	/* the person's typical gap; how much closer open profiles sit (a least-squares slope of
	 * the gap on the open space, with enough pairs) */
	sp->letter = med(g, n, 0.15);
	if (sp->letter < 0) sp->letter = 0;
	if (sp->letter > 0.8) sp->letter = 0.8;
	for (size_t i = 0; i < n; i++) x[i] = ix[i].open;
	sp->open0 = med(x, n, 0);
	if (n >= 10) {
		double mx = 0, my = 0, sxx = 0, sxy = 0;
		for (size_t i = 0; i < n; i++) { mx += ix[i].open; my += ix[i].gap; }
		mx /= n; my /= n;
		for (size_t i = 0; i < n; i++) { sxx += (ix[i].open - mx) * (ix[i].open - mx); sxy += (ix[i].open - mx) * (ix[i].gap - my); }
		double slope = sxx > 1e-9 ? sxy / sxx : 0;
		sp->open = slope < 0 ? (-slope > 1 ? 1 : -slope) : 0;
	}
	for (size_t i = 0; i < nw; i++) g[i] = words[i];
	sp->word = med(g, nw, sp->letter + 0.55);
	if (sp->word < sp->letter + 0.2) sp->word = sp->letter + 0.2;
	if (sp->word > 2.0) sp->word = 2.0;
	/* the pairs written often enough, then the pairs of side shapes */
	qsort(ix, n, sizeof *ix, cmp_obs_pair);
	sp->pairs = malloc((n + 1) * sizeof *sp->pairs);
	sp->sides = malloc((n + 1) * sizeof *sp->sides);
	if (!sp->pairs || !sp->sides) { free(g); free(x); free(ix); return -1; }
	for (size_t i = 0; i < n;) {
		size_t j = i;
		while (j < n && ix[j].a == ix[i].a && ix[j].b == ix[i].b) j++;
		if (j - i >= HW_PAIR_MIN) {
			size_t m = j - i, k = 0;
			double v = m % 2 ? ix[i + m / 2].gap : (ix[i + m / 2 - 1].gap + ix[i + m / 2].gap) / 2;
			for (size_t q = i; q < j; q++) if (isfinite(ix[q].d)) x[k++] = ix[q].d;
			double d = k >= HW_PAIR_MIN ? round(med(x, k, 0) * 1e4) / 1e4 : NAN;
			sp->pairs[sp->npairs++] = (struct hw_pair){ ix[i].a, ix[i].b, v, (int)m, d };
		}
		i = j;
	}
	if (reaches(sp, ix, n, x)) { free(g); free(x); free(ix); return -1; }
	qsort(ix, n, sizeof *ix, cmp_obs_side);
	for (size_t i = 0; i < n;) {
		size_t j = i;
		while (j < n && ix[j].rc == ix[i].rc && ix[j].lc == ix[i].lc) j++;
		if (ix[i].rc >= 0 && ix[i].lc >= 0 && j - i >= HW_SIDE_PAIR_MIN) {
			size_t m = j - i;
			double v = m % 2 ? ix[i + m / 2].gap : (ix[i + m / 2 - 1].gap + ix[i + m / 2].gap) / 2;
			sp->sides[sp->nsides++] = (struct hw_side_pair){ ix[i].rc, ix[i].lc, v, (int)m };
		}
		i = j;
	}
	free(g); free(x); free(ix);
	return 0;
}
