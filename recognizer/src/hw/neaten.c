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

/* ---------------------------------------------------------------- the correspondence (FR-036) */

#define M_STEP 0.04     /* x-heights between the points the strokes are matched at */
#define M_MIN 8
#define M_MAX 128
#define M_SMOOTH 3      /* the matched places averaged over this many points to either side */
#define M_SPREAD 2.5    /* the matched places move along the version at most this many times as fast as evenly */
#define M_HOOK 0.25     /* share of a stroke's length an end hook may take */
#define M_HOOK_MAX 0.6  /* x-heights */
/* the cost of matching two points: place (squared, x-heights), direction (1 - cos), turn
 * (radians per M_STEP); a point of a hook costs at most C_HOOK */
#define W_POS 4.0
#define W_DIR 1.0
#define W_TURN 0.5
#define C_HOOK 0.15
/* where a stroke passes from one character to the next, their moves are blended over this
 * many x-heights to either side (at most half of either character's run) */
#define HW_NEAT_BLEND 0.15
/* ink more than this many times as wide or tall as the version (and 0.35 x-heights) isn't
 * written like it */
#define HW_NEAT_SIZE 1.8
/* nor a piece more than this many x-heights from where its stroke lies in the version */
#define HW_NEAT_PLACE 0.45
/* nor ink whose points, matched in shape, are further than this (x-heights, RMS) from theirs
 * once the version is stretched, sheared and moved onto them at best (a letter written wider
 * or leaning still has its structure; one written in another way doesn't) */
#define HW_NEAT_MATCH 0.12
/* nor one whose first or last matched point is further than this from its place so */
#define HW_NEAT_ENDS 0.3
/* how hard the stretch is held to none (per matched point) */
#define HW_NEAT_RIGID 0.02

/* unit direction (by the points to either side) and turn ([1 2 1] smoothed) along a resampled stroke */
static void shape_feats(const double *x, const double *y, int n, double *ux, double *uy, double *tu)
{
	for (int i = 0; i < n; i++) {
		int a = i ? i - 1 : 0, b = i < n - 1 ? i + 1 : n - 1;
		double dx = x[b] - x[a], dy = y[b] - y[a], l = hypot(dx, dy);
		ux[i] = l > 0 ? dx / l : 1;
		uy[i] = l > 0 ? dy / l : 0;
	}
	double *t = tu;
	for (int i = 0; i < n; i++) {
		if (i == 0 || i == n - 1) { t[i] = 0; continue; }
		double a1 = atan2(y[i] - y[i - 1], x[i] - x[i - 1]), a2 = atan2(y[i + 1] - y[i], x[i + 1] - x[i]), d = a2 - a1;
		while (d > M_PI) d -= 2 * M_PI;
		while (d < -M_PI) d += 2 * M_PI;
		t[i] = d;
	}
	double prev = t[0];
	for (int i = 1; i < n - 1; i++) { double c = (prev + 2 * t[i] + t[i + 1]) / 4; prev = t[i]; t[i] = c; }
}

static double arc(const double *x, const double *y, size_t n, double *cum)
{
	double L = 0;
	if (cum && n) cum[0] = 0;
	for (size_t i = 1; i < n; i++) { L += hypot(x[i] - x[i - 1], y[i] - y[i - 1]); if (cum) cum[i] = L; }
	return L;
}

int hw_neat_match(const double *x, const double *y, size_t n, const double *tx, const double *ty, size_t tn,
		  int joint0, int joint1, double *u)
{
	if (!n) return 0;
	double *cum = malloc((n + 1) * sizeof *cum);
	if (!cum) return -1;
	double L = arc(x, y, n, cum), TL = arc(tx, ty, tn, NULL);
	if (n == 1 || !(L > 1e-9) || tn < 2 || !(TL > 1e-9)) {
		/* a dot, or onto one: in order along the target */
		for (size_t i = 0; i < n; i++) u[i] = n > 1 ? (double)i / (double)(n - 1) : 0.5;
		free(cum);
		return 0;
	}
	int na = (int)clampd(round(L / M_STEP) + 1, M_MIN, M_MAX), nb = (int)clampd(round(TL / M_STEP) + 1, M_MIN, M_MAX);
	size_t cells = (size_t)na * (size_t)nb;
	double *buf = malloc(((size_t)(na + nb) * 5 + (size_t)na * 4 + cells + 1) * sizeof *buf);
	unsigned char *from = malloc(cells + 1);
	if (!buf || !from) { free(buf); free(from); free(cum); return -1; }
	double *ax = buf, *ay = ax + na, *adx = ay + na, *ady = adx + na, *at = ady + na;
	double *bx = at + na, *by = bx + nb, *bdx = by + nb, *bdy = bdx + nb, *bt = bdy + nb;
	double *uj = bt + nb, *sm = uj + na, *kmin = sm + na, *kmax = kmin + na, *D = kmax + na;
	resample(x, y, n, (size_t)na, ax, ay);
	resample(tx, ty, tn, (size_t)nb, bx, by);
	shape_feats(ax, ay, na, adx, ady, at);
	shape_feats(bx, by, nb, bdx, bdy, bt);
	/* the turns per M_STEP of either stroke */
	double sa = M_STEP / (L / (na - 1)), sb = M_STEP / (TL / (nb - 1));
	int hook = (int)floor(fmin(M_HOOK * L, M_HOOK_MAX) / (L / (na - 1)));
	/* dynamic time warping: a path from both starts to both ends, each step on along either
	 * stroke or both, so the order is kept */
	for (int j = 0; j < na; j++)
		for (int k = 0; k < nb; k++) {
			double px = ax[j] - bx[k], py = ay[j] - by[k];
			double c = W_POS * (px * px + py * py) + W_DIR * (1 - (adx[j] * bdx[k] + ady[j] * bdy[k])) +
				   W_TURN * fabs(at[j] * sa - bt[k] * sb);
			if ((k == 0 && j <= hook) || (k == nb - 1 && j >= na - 1 - hook)) c = fmin(c, C_HOOK);
			size_t o = (size_t)j * (size_t)nb + (size_t)k;
			if (!j && !k) { D[o] = c; from[o] = 0; continue; }
			double best = INFINITY;
			unsigned char f = 0;
			if (j && k && D[o - (size_t)nb - 1] < best) { best = D[o - (size_t)nb - 1]; f = 3; }
			if (j && D[o - (size_t)nb] < best) { best = D[o - (size_t)nb]; f = 1; }
			if (k && D[o - 1] < best) { best = D[o - 1]; f = 2; }
			D[o] = best + c;
			from[o] = f;
		}
	/* the path back: per point of the stroke the target's points it met */
	for (int j = 0; j < na; j++) { kmin[j] = INFINITY; kmax[j] = -INFINITY; uj[j] = 0; sm[j] = 0; }
	for (int j = na - 1, k = nb - 1;;) {
		if (k < kmin[j]) kmin[j] = k;
		if (k > kmax[j]) kmax[j] = k;
		uj[j] += k;
		sm[j] += 1;
		unsigned char f = from[(size_t)j * (size_t)nb + (size_t)k];
		if (!f) break;
		if (f & 1) j--;
		if (f & 2) k--;
	}
	for (int j = 0; j < na; j++) uj[j] = (j == 0 ? kmin[j] : j == na - 1 ? kmax[j] : uj[j] / sm[j]) / (nb - 1);
	/* the hooks: the points before the path leaves the target's start, after it reaches its end */
	int j0 = 0, j1 = na - 1;
	while (j0 + 1 < na && kmax[j0 + 1] == 0) j0++;
	while (j1 - 1 > j0 && kmin[j1 - 1] == nb - 1) j1--;
	/* the places smoothed between them (the average of places in order is in order), then
	 * stretched back to where they began and ended */
	for (int j = j0; j <= j1; j++) {
		double a = 0;
		for (int d = -M_SMOOTH; d <= M_SMOOTH; d++) {
			int i = j + d;
			a += uj[i < j0 ? j0 : i > j1 ? j1 : i];
		}
		sm[j] = a / (2 * M_SMOOTH + 1);
	}
	double r0 = uj[j0], r1 = uj[j1], s0 = sm[j0], s1 = sm[j1];
	for (int j = j0; j <= j1; j++) uj[j] = s1 > s0 ? r0 + (sm[j] - s0) * (r1 - r0) / (s1 - s0) : uj[j];
	/* no point a long way along the version from the one before: where the stroke lacks a
	 * part of the version the points spread over it (at most M_SPREAD times as fast as
	 * evenly), so the version is traced, not cut across; still in order both ways */
	if (j1 > j0) {
		double d = M_SPREAD * (r1 - r0) / (j1 - j0);
		for (int j = j0 + 1; j <= j1; j++) if (uj[j] > uj[j - 1] + d) uj[j] = uj[j - 1] + d;
		uj[j1] = r1;
		for (int j = j1 - 1; j >= j0; j--) if (uj[j] < uj[j + 1] - d) uj[j] = uj[j + 1] - d;
		uj[j0] = r0;
		for (int j = j0 + 1; j <= j1; j++) if (uj[j] < uj[j - 1]) uj[j] = uj[j - 1];
	}
	for (int j = 0; j < j0; j++) uj[j] = 0;
	for (int j = j1 + 1; j < na; j++) uj[j] = 1;
	/* each of the stroke's own points by its place along it */
	for (size_t i = 0; i < n; i++) {
		double f = cum[i] / L * (na - 1);
		if ((joint0 && f < j0) || (joint1 && f > j1)) { u[i] = NAN; continue; }
		int a = (int)floor(f);
		if (a >= na - 1) { u[i] = uj[na - 1]; continue; }
		double t = f - a;
		u[i] = clampd(uj[a] + t * (uj[a + 1] - uj[a]), 0, 1);
	}
	free(buf); free(from); free(cum);
	return 0;
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
	free(q->x); free(q->y); free(q->how); free(q->pulled);
	q->x = q->y = NULL;
	q->how = NULL;
	q->pulled = NULL;
}

/* how far a character goes towards its best version at strength s (FR-014): near it (near)
 * from Medium on, else from Strong on, all the way at Neatest; part of the way between */
static double pull_of(double s, int near)
{
	if (near && s <= HW_NEAT_MEDIUM) return clampd((s - HW_NEAT_LIGHT) / (HW_NEAT_MEDIUM - HW_NEAT_LIGHT), 0, 1) * HW_NEAT_PULL_MEDIUM;
	if (s <= HW_NEAT_STRONG) {
		double t = clampd((s - HW_NEAT_MEDIUM) / (HW_NEAT_STRONG - HW_NEAT_MEDIUM), 0, 1);
		return (near ? HW_NEAT_PULL_MEDIUM : 0) + t * (HW_NEAT_PULL_STRONG - (near ? HW_NEAT_PULL_MEDIUM : 0));
	}
	return HW_NEAT_PULL_STRONG + clampd((s - HW_NEAT_STRONG) / (1 - HW_NEAT_STRONG), 0, 1) * (1 - HW_NEAT_PULL_STRONG);
}

/* a character's piece of ink: points a..b-1 of stroke j, its start or end a joint with
 * another character in the stroke */
struct piece { int j; size_t a, b; int joint0, joint1; };

struct ch {
	int npts, cls, ok;
	double fit;
	double x0, y0, x1, y1, cx;      /* the ink's box (y down) and middle */
	double ax, ay;                  /* the anchor: its middle on the fitted baseline */
	double f, shear, dx, dy;        /* the line-level map */
	double tx0, tx1, ty1;           /* the box after scale and shear */
	double snum, sden;              /* its downstrokes' lean */
	double pull;                    /* how far it goes towards its best version, 0 not pulled */
	int np;                         /* its pieces, in writing order */
	struct piece *pc;
	const struct hw_variant *v;     /* the variant it goes to */
	const struct hw_lstroke *t;     /* its best version (FR-035): v->best, else v->s */
	double off;                     /* the target placed: its x (x-heights) less the variant's */
};

/* the line-level map of a point of character c */
static void line_map(const struct ch *c, double x, double y, double *ox, double *oy)
{
	double up = c->ay - y;
	double xs = x + c->shear * up;
	*ox = c->ax + c->f * (xs - c->ax) + c->dx;
	*oy = c->ay + c->f * (y - c->ay) + c->dy;
}

/* where the pull takes a point from where the line-level map puts it is kept per point of a
 * stroke (ex, ey) */


/* the affine map of n matched version points f onto ink points p at best (least squares,
 * the stretch held towards none by HW_NEAT_RIGID): p = mp + M^T (f - mf), with fp the pairs
 * (fx, fy, px, py); r the RMS distance left. Non-zero when it mirrors or has no fit. */
static int affine_fit(const double *fp, size_t n, double *M, double *mf, double *mp, double *r)
{
	double F[4] = { 0 }, P[4] = { 0 };
	mf[0] = mf[1] = mp[0] = mp[1] = 0;
	for (size_t i = 0; i < n; i++)
		for (int a = 0; a < 2; a++) { mf[a] += fp[4 * i + a] / (double)n; mp[a] += fp[4 * i + 2 + a] / (double)n; }
	for (size_t i = 0; i < n; i++)
		for (int a = 0; a < 2; a++)
			for (int z = 0; z < 2; z++) {
				F[2 * a + z] += (fp[4 * i + a] - mf[a]) * (fp[4 * i + z] - mf[z]);
				P[2 * a + z] += (fp[4 * i + a] - mf[a]) * (fp[4 * i + 2 + z] - mp[z]);
			}
	F[0] += HW_NEAT_RIGID * (double)n; F[3] += HW_NEAT_RIGID * (double)n;
	P[0] += HW_NEAT_RIGID * (double)n; P[3] += HW_NEAT_RIGID * (double)n;
	double det = F[0] * F[3] - F[1] * F[2];
	if (!n || !(fabs(det) > 1e-12)) return -1;
	M[0] = (F[3] * P[0] - F[1] * P[2]) / det; M[1] = (F[3] * P[1] - F[1] * P[3]) / det;
	M[2] = (F[0] * P[2] - F[2] * P[0]) / det; M[3] = (F[0] * P[3] - F[2] * P[1]) / det;
	double e = 0;
	for (size_t i = 0; i < n; i++) {
		double dx = fp[4 * i] - mf[0], dy = fp[4 * i + 1] - mf[1];
		double gx = mp[0] + M[0] * dx + M[2] * dy - fp[4 * i + 2], gy = mp[1] + M[1] * dx + M[3] * dy - fp[4 * i + 3];
		e += gx * gx + gy * gy;
	}
	*r = sqrt(e / (double)n);
	return M[0] * M[3] - M[1] * M[2] > 0 ? 0 : -1;
}

/* The pull of character k (FR-014, FR-035, FR-036): its pieces, the nearest of its versions
 * with as many strokes, how far it goes (by the level, and whether it is near its best
 * version), and for each of its points where the pull takes it: the place on the best
 * version that matches it in shape (hw_neat_match), the version on the straight baseline at
 * the line's x-height with its middle on the ink's (as the matched points have it), without a
 * baseline its middle on the ink's. 0, -1 out of memory. */
static int pull_prepare(struct hw_neat *q, struct ch *C, size_t k, const int *nown, double xh, int hasbase,
			double base, double s, double **ex, double **ey)
{
	struct ch *c = &C[k];
	const struct hw_letter *le = NULL;
	size_t lo = 0, hi = q->L->n;
	while (lo < hi) {
		size_t mid = (lo + hi) / 2;
		if (q->L->l[mid].cp < q->cp[k]) lo = mid + 1; else hi = mid;
	}
	if (lo < q->L->n && q->L->l[lo].cp == q->cp[k]) le = &q->L->l[lo];
	int kept = 0;
	for (int t = 0; le && t < le->nvar; t++) kept |= !le->var[t].out;
	if (!le || !le->covered || !kept) return 0;
	/* its pieces: the runs of its points along the strokes, in writing order */
	int np = 0;
	for (size_t j = 0; j < q->n; j++) {
		if (!nown[j]) continue;
		for (size_t i = 0; i < q->ink[j].n; i++) np += q->own[j][i] == (int)k && (!i || q->own[j][i - 1] != (int)k);
	}
	if (!np) return 0;
	c->pc = malloc((size_t)np * sizeof *c->pc);
	double **sx = calloc((size_t)np * 2 + 1, sizeof *sx);
	size_t *sn = calloc((size_t)np + 1, sizeof *sn);
	int ret = -1;
	if (!c->pc || !sx || !sn) goto out;
	c->np = 0;
	for (size_t j = 0; j < q->n; j++) {
		if (!nown[j]) continue;
		const int *ow = q->own[j];
		size_t m = q->ink[j].n;
		for (size_t i = 0; i < m; i++) {
			if (ow[i] != (int)k || (i && ow[i - 1] == (int)k)) continue;
			struct piece *pc = &c->pc[c->np++];
			pc->j = (int)j;
			pc->a = i;
			pc->b = i;
			while (pc->b < m && ow[pc->b] == (int)k) pc->b++;
			pc->joint0 = i > 0 && ow[i - 1] >= 0;
			pc->joint1 = pc->b < m && ow[pc->b] >= 0;
		}
	}
	/* the pieces as the line-level map leaves them */
	for (int a = 0; a < np; a++) {
		const struct piece *pc = &c->pc[a];
		size_t m = pc->b - pc->a;
		sx[a] = malloc((m + 1) * sizeof(double));
		sx[np + a] = malloc((m + 1) * sizeof(double));
		if (!sx[a] || !sx[np + a]) goto out;
		sn[a] = m;
		for (size_t i = 0; i < m; i++)
			line_map(c, q->ink[pc->j].x[pc->a + i], q->ink[pc->j].y[pc->a + i], &sx[a][i], &sx[np + a][i]);
	}
	double best = INFINITY;
	for (int t = 0; t < le->nvar; t++) {
		/* a version made of one sample is that sample, jitter and all */
		if (le->var[t].members < 2 || le->var[t].out) continue;
		double d = dist_to(sx, sx + np, sn, (size_t)np, xh, &le->var[t], NULL, NULL);
		if (d < best - 1e-12) { best = d; c->v = &le->var[t]; }
	}
	ret = 0;
	if (!c->v || !isfinite(best)) { c->v = NULL; goto out; }
	c->t = c->v->best ? c->v->best : c->v->s;
	/* the structure (FR-014): each piece like its stroke of the version in size and in where
	 * it lies in the character, so a stroke order or a cut of a joined stroke other than the
	 * version's isn't pulled into it */
	{
		double icx = 0, icy = 0, tcx = 0, tcy = 0, pb[64][8];
		long ni = 0, nt = 0;
		int bad = np > 64;
		for (int a = 0; !bad && a < np; a++) {
			double *b = pb[a];
			b[0] = b[2] = b[4] = b[6] = INFINITY;
			b[1] = b[3] = b[5] = b[7] = -INFINITY;
			for (size_t i = 0; i < sn[a]; i++) {
				double x = sx[a][i] / xh, y = -sx[np + a][i] / xh;
				b[0] = fmin(b[0], x); b[1] = fmax(b[1], x); b[2] = fmin(b[2], y); b[3] = fmax(b[3], y);
				icx += x; icy += y; ni++;
			}
			for (size_t i = 0; i < c->t[a].n; i++) {
				double x = c->t[a].x[i], y = c->t[a].y[i];
				b[4] = fmin(b[4], x); b[5] = fmax(b[5], x); b[6] = fmin(b[6], y); b[7] = fmax(b[7], y);
				tcx += x; tcy += y; nt++;
			}
		}
		if (ni && nt) { icx /= ni; icy /= ni; tcx /= nt; tcy /= nt; }
		for (int a = 0; !bad && a < np; a++) {
			const double *b = pb[a];
			double iw = b[1] - b[0], ih = b[3] - b[2], tw = b[5] - b[4], th = b[7] - b[6];
			double dx = ((b[0] + b[1]) / 2 - icx) - ((b[4] + b[5]) / 2 - tcx), dy = ((b[2] + b[3]) / 2 - icy) - ((b[6] + b[7]) / 2 - tcy);
			bad = iw > HW_NEAT_SIZE * tw + 0.35 || tw > HW_NEAT_SIZE * iw + 0.35 || ih > HW_NEAT_SIZE * th + 0.35 ||
			      th > HW_NEAT_SIZE * ih + 0.35 || hypot(dx, dy) > HW_NEAT_PLACE;
		}
		if (bad) { c->v = NULL; goto out; }
	}
	struct hw_variant tv = *c->v;
	tv.s = (struct hw_lstroke *)c->t;
	c->pull = pull_of(s, dist_to(sx, sx + np, sn, (size_t)np, xh, &tv, NULL, NULL) <= HW_NEAT_NEAR);
	if (!(c->pull > 0)) goto out;
	/* the middles of the ink and of the version, both by arc length */
	ret = -1;
	double acx = 0, acy = 0, vcx = 0, vcy = 0;
	size_t tot = 0;
	for (int a = 0; a < np; a++) {
		size_t r = c->t[a].n < 2 ? 2 : c->t[a].n;
		double *rx = malloc((r + 1) * 2 * sizeof *rx);
		if (!rx) goto out;
		resample(sx[a], sx[np + a], sn[a], r, rx, rx + r + 1);
		for (size_t i = 0; i < r; i++) { acx += rx[i]; acy += rx[r + 1 + i]; }
		resample(c->t[a].x, c->t[a].y, c->t[a].n, r, rx, rx + r + 1);
		for (size_t i = 0; i < r; i++) { vcx += rx[i]; vcy += rx[r + 1 + i]; }
		tot += r;
		free(rx);
	}
	acx /= (double)tot; acy /= (double)tot; vcx /= (double)tot; vcy /= (double)tot;
	/* matched twice: the second time against the version mapped onto the ink as the first
	 * matching gives it at best (moved, stretched, sheared: a hook or a joint's lead-in
	 * moves the ink's middle, a letter written wider or leaning is still the letter; ink the
	 * line left off the baseline is matched where it is, the version still goes on the line) */
	double shy = hasbase ? (base - acy) / xh - vcy : 0, sh[2] = { 0, 0 }, M[4] = { 1, 0, 0, 1 }, mf[2] = { 0, 0 }, mp[2] = { 0, 0 }, r2 = 0;
	double E[128][4];
	int ne = 0;
	size_t all = 0, nfp;
	for (int a = 0; a < np; a++) all += sn[a];
	double *fp = malloc((all + 1) * 4 * sizeof *fp);
	if (!fp) goto out;
	for (int pass = 0; pass < 2; pass++) {
		nfp = 0;
		ne = 0;
		for (int a = 0; a < np; a++) {
			const struct piece *pc = &c->pc[a];
			const struct hw_lstroke *t = &c->t[a];
			size_t m = sn[a], tn = t->n;
			double *X = malloc((m + 1) * 3 * sizeof *X), *T = malloc((tn + 1) * 5 * sizeof *T);
			if (!X || !T) { free(X); free(T); free(fp); goto out; }
			double *Y = X + m + 1, *u = Y + m + 1, *TY = T + tn + 1, *tc = TY + tn + 1, *MX = tc + tn + 1, *MY = MX + tn + 1;
			for (size_t i = 0; i < m; i++) {
				X[i] = (sx[a][i] - acx) / xh;
				Y[i] = hasbase ? (base - sx[np + a][i]) / xh : (acy - sx[np + a][i]) / xh + vcy;
			}
			for (size_t i = 0; i < tn; i++) {
				T[i] = t->x[i] - vcx; TY[i] = t->y[i] + shy;
				MX[i] = mp[0] + M[0] * (T[i] - mf[0]) + M[2] * (TY[i] - mf[1]);
				MY[i] = mp[1] + M[1] * (T[i] - mf[0]) + M[3] * (TY[i] - mf[1]);
			}
			arc(T, TY, tn, tc);
			if (hw_neat_match(X, Y, m, MX, MY, tn, pc->joint0, pc->joint1, u)) { free(X); free(T); free(fp); goto out; }
			size_t first = 0, last = m ? m - 1 : 0;
			while (first < last && isnan(u[first])) first++;
			while (last > first && isnan(u[last])) last--;
			for (size_t i = 0; i < m; i++) {
				size_t pi = pc->a + i;
				if (isnan(u[i])) { ex[pc->j][pi] = ey[pc->j][pi] = NAN; continue; }
				double f2[2], p2[2] = { X[i], Y[i] };
				/* (the version unmapped: the map only finds the places) */
				at_fraction(T, TY, tn, tc, u[i], &f2[0], &f2[1]);
				fp[4 * nfp] = f2[0]; fp[4 * nfp + 1] = f2[1]; fp[4 * nfp + 2] = p2[0]; fp[4 * nfp + 3] = p2[1];
				nfp++;
				/* its first and last matched points (a stroke cut short at a joint matches its
				 * end to the middle of the version) */
				if ((i == first || i == last) && ne < 128) { E[ne][0] = f2[0]; E[ne][1] = f2[1]; E[ne][2] = X[i]; E[ne][3] = Y[i]; ne++; }
				if (!pass) continue;
				double gx = acx + (f2[0] + sh[0]) * xh, fy = f2[1] + sh[1] - shy;
				double gy = hasbase ? base - fy * xh : acy - (fy - vcy) * xh;
				ex[pc->j][pi] = gx - sx[a][i];
				ey[pc->j][pi] = gy - sx[np + a][i];
			}
			free(X); free(T);
		}
		if (affine_fit(fp, nfp, M, mf, mp, &r2)) { free(fp); c->pull = 0; ret = 0; goto out; }
		if (!pass) for (int r = 0; r < 2; r++) sh[r] = clampd(mp[r] - mf[r], -0.5, 0.5);
	}
	free(fp);
	/* matched in shape, its points stay near the version's once the version is mapped onto
	 * them at best, its ends too: else it isn't written like it */
	{
		double ends = 0;
		for (int e = 0; e < ne; e++) {
			double gx = mp[0] + M[0] * (E[e][0] - mf[0]) + M[2] * (E[e][1] - mf[1]);
			double gy = mp[1] + M[1] * (E[e][0] - mf[0]) + M[3] * (E[e][1] - mf[1]);
			ends = fmax(ends, hypot(gx - E[e][2], gy - E[e][3]));
		}
		if (r2 > HW_NEAT_MATCH || ends > HW_NEAT_ENDS) { c->pull = 0; ret = 0; goto out; }
	}
	/* a joint's lead-in or lead-out the version doesn't have goes with the point next to it */
	for (int a = 0; a < np; a++) {
		const struct piece *pc = &c->pc[a];
		double *px = ex[pc->j], *py = ey[pc->j];
		size_t f = pc->a, l = pc->b;
		while (f < pc->b && isnan(px[f])) f++;
		while (l > f && isnan(px[l - 1])) l--;
		for (size_t i = pc->a; i < f; i++) { px[i] = f < pc->b ? px[f] : 0; py[i] = f < pc->b ? py[f] : 0; }
		for (size_t i = l; i < pc->b; i++) { px[i] = px[l - 1]; py[i] = py[l - 1]; }
	}
	c->off = acx / xh - vcx + sh[0];
	ret = 0;
out:
	for (int a = 0; sx && a < np; a++) { free(sx[a]); free(sx[np + a]); }
	free(sx); free(sn);
	if (ret || !(c->pull > 0)) c->pull = 0;
	return ret;
}

/* the move of point i of stroke j as character o's: the line-level map as far as la, and o's
 * pull as at its point ie */
static void move_of(const struct hw_neat *q, const struct ch *C, double **ex, double **ey, size_t j, size_t i, int o,
		    size_t ie, double la, double *mx, double *my)
{
	double x, y;
	line_map(&C[o], q->ink[j].x[i], q->ink[j].y[i], &x, &y);
	*mx = la * (x - q->ink[j].x[i]);
	*my = la * (y - q->ink[j].y[i]);
	if (C[o].pull > 0 && q->own[j][ie] == o) { *mx += C[o].pull * ex[j][ie]; *my += C[o].pull * ey[j][ie]; }
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
	if (!C || !nown) { free(C); free(nown); return -1; }
	for (size_t k = 0; k < nc; k++) {
		C[k].x0 = C[k].y0 = INFINITY;
		C[k].x1 = C[k].y1 = -INFINITY;
		C[k].fit = q->fit ? q->fit[k] : 1;
		C[k].cls = cls_of(q->cp[k]);
		C[k].f = 1;
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
		q->how[j] = HW_NEAT_LINE;
		/* a stroke's points that no character has go with their neighbour's */
		int last = -1;
		for (size_t i = 0; i < k->n; i++) { if (q->own[j][i] >= 0 && (size_t)q->own[j][i] < nc) last = q->own[j][i]; else if (last >= 0) q->own[j][i] = last; }
		last = -1;
		for (size_t i = k->n; i-- > 0;) { if (q->own[j][i] >= 0) last = q->own[j][i]; else q->own[j][i] = last; }
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
	/* per point of a stroke: where the pull takes it from where the line-level map puts it */
	double **ex = calloc(n + 1, sizeof *ex), **ey = calloc(n + 1, sizeof *ey);
	q->pulled = calloc(nc + 1, 1);
	if (!v || !ex || !ey || !q->pulled) { ret = -1; goto done; }
	for (size_t j = 0; j < n; j++) {
		ex[j] = calloc(q->ink[j].n + 1, sizeof **ex);
		ey[j] = calloc(q->ink[j].n + 1, sizeof **ey);
		if (!ex[j] || !ey[j]) { ret = -1; goto done; }
	}
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
	/* the pull (FR-014, FR-035, FR-036): a character read well, covered, whose pieces are as
	 * many as one of its versions has strokes; towards that version's best */
	if (q->L && on.pull && !q->line_only && s > HW_NEAT_LIGHT)
		for (size_t k = 0; k < nc; k++)
			if (C[k].npts && C[k].fit >= HW_NEAT_FIT_PULL && !(q->nopull && q->nopull[k]))
				if (pull_prepare(q, C, k, nown, xh, hasbase, target_base, s, ex, ey)) { ret = -1; goto done; }

	/* spacing (FR-014): each pair of neighbouring letters towards the letter model's gap for
	 * it (spacing.c: the narrowest distance between their ink profiles, taken from the ink
	 * as the line-level map leaves it), without a letter model towards the line's own median;
	 * the gaps between words (their boxes) towards the word spacing */
	{
		struct hw_profile *P = malloc((nc + 1) * sizeof *P);
		struct hw_mid *M = calloc(nc + 1, sizeof *M);
		double *mid = malloc((nc + 1) * sizeof *mid);
		if (!P || !M || !mid) { free(P); free(M); free(mid); ret = -1; goto done; }
		for (size_t k = 0; k < nc; k++) hw_profile_clear(&P[k]);
		for (size_t j = 0; j < n; j++) {
			if (!nown[j]) continue;
			double px = 0, py = 0;
			int po = -1;
			for (size_t i = 0; i < q->ink[j].n; i++) {
				int o = q->own[j][i];
				if (o < 0) { po = -1; continue; }
				struct ch *c = &C[o];
				double x, y;
				line_map(c, q->ink[j].x[i], q->ink[j].y[i], &x, &y);
				double base = hasbase ? target_base : c->ay;
				x /= xh;
				y = (base - (y + c->dy)) / xh;
				hw_profile_seg(&P[o], po == o ? px : x, po == o ? py : y, x, y);
				if (po == o) hw_mid_seg(&M[o], px, py, x, y);
				px = x; py = y; po = o;
			}
		}
		for (size_t k = 0; k < nc; k++) mid[k] = hw_mid_of(&M[k]);
		/* a pulled character: its profile and middle part of the way to its best version's,
		 * as far as it is pulled, about its middle as the pull places it */
		for (size_t k = 0; k < nc; k++) {
			struct ch *c = &C[k];
			if (!(c->pull > 0)) continue;
			struct hw_profile tp;
			struct hw_mid tm = { 0, 0 };
			hw_profile_clear(&tp);
			for (size_t a = 0; a < c->v->nstrokes; a++)
				for (size_t i = 0; i < c->t[a].n; i++) {
					size_t i0 = i ? i - 1 : 0;
					hw_profile_seg(&tp, c->t[a].x[i0], c->t[a].y[i0], c->t[a].x[i], c->t[a].y[i]);
					hw_mid_seg(&tm, c->t[a].x[i0], c->t[a].y[i0], c->t[a].x[i], c->t[a].y[i]);
				}
			double p = c->pull;
			for (int bnd = 0; bnd < HW_PROF_BANDS; bnd++) {
				double *ink[2] = { &P[k].l[bnd], &P[k].r[bnd] }, tg[2] = { tp.l[bnd] + c->off, tp.r[bnd] + c->off };
				for (int e = 0; e < 2; e++) {
					if (isnan(tg[e])) { if (p >= 0.5) *ink[e] = NAN; }
					else *ink[e] = isnan(*ink[e]) ? (p >= 0.5 ? tg[e] : NAN) : (1 - p) * *ink[e] + p * tg[e];
				}
			}
			double tmid = hw_mid_of(&tm) + c->off;
			if (isfinite(tmid)) mid[k] = isfinite(mid[k]) ? (1 - p) * mid[k] + p * tmid : tmid;
		}
		double *lg = v, *wg = v + nc + 1;
		size_t nl = 0, nw = 0;
		int prev = -1, space = 0;
		for (int pass = 0; pass < 2; pass++) {
			double tl = nl >= 2 ? median(lg, nl, 0) : q->L ? q->L->sp.letter * xh : NAN;
			double tw = q->L ? q->L->sp.word * xh : nw >= 2 ? median(wg, nw, 0) : NAN;
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
					int use = !joined && C[k].ok && C[prev].ok;
					double g = space ? C[k].tx0 - C[prev].tx1 : hw_profile_gap(&P[prev], &P[k], 0) * xh;
					if (!pass) {
						if (use) { if (space) wg[nw++] = g; else lg[nl++] = g; }
					} else {
						double t = tw;
						/* with a letter model: where it places the pair, as a move */
						double mv = NAN;
						if (!space && q->L)
							mv = hw_spacing_place(&q->L->sp, q->cp[prev], &P[prev], mid[prev], hw_spacing_side(&q->L->sp, &P[prev], 1),
									      q->cp[k], &P[k], mid[k], hw_spacing_side(&q->L->sp, &P[k], 0)) * xh;
						else if (!space) t = tl;
						if (isfinite(mv)) t = g + mv;
						if (on.space && use && isfinite(t)) shift += clampd(t - g, -0.3 * xh, 0.3 * xh);
					}
				}
				if (pass) C[k].dx = shift;
				prev = (int)k;
				space = 0;
			}
		}
		free(P); free(M); free(mid);
	}

	/* the points (FR-014): the line-level map as far as the strength goes (all of it from
	 * Light on), and a pulled character's points that far along the pull; where a stroke
	 * passes from one character to the next, the two characters' moves blended (FR-036) */
	double la = clampd(s / HW_NEAT_LIGHT, 0, 1);
	for (size_t j = 0; j < n; j++) {
		if (!nown[j]) continue;
		const struct hw_ink *k = &q->ink[j];
		size_t m = k->n;
		double *cum = malloc((m + 1) * sizeof *cum);
		if (!cum) { ret = -1; goto done; }
		arc(k->x, k->y, m, cum);
		for (size_t i = 0; i < m; i++) {
			int o = q->own[j][i];
			if (o < 0) continue;
			double mx, my;
			move_of(q, C, ex, ey, j, i, o, i, la, &mx, &my);
			if (nown[j] > 1) {
				/* the characters' moves weighted by how much of the stroke around the point is
				 * theirs (a tent of HW_NEAT_BLEND x-heights to either side, by arc length), each
				 * as at its point nearest to it: smooth across the joints, a short run too */
				double h = HW_NEAT_BLEND * xh, wsum = 0, sx2 = 0, sy2 = 0;
				size_t lo2 = i, hi2 = i;
				while (lo2 > 0 && cum[i] - cum[lo2 - 1] < h) lo2--;
				while (hi2 + 1 < m && cum[hi2 + 1] - cum[i] < h) hi2++;
				int who[8], nw = 0;
				double ww[8];
				size_t at[8];
				for (size_t i2 = lo2; i2 <= hi2; i2++) {
					int o2 = q->own[j][i2];
					if (o2 < 0) continue;
					double w = 1 - fabs(cum[i2] - cum[i]) / h;
					if (!(w > 0)) w = i2 == i ? 1 : 0;
					int z = 0;
					while (z < nw && who[z] != o2) z++;
					if (z == nw) { if (nw == 8) continue; who[nw] = o2; ww[nw] = 0; at[nw] = i2; nw++; }
					if ((i2 > i ? i2 - i : i - i2) < (at[z] > i ? at[z] - i : i - at[z])) at[z] = i2;
					ww[z] += w;
				}
				for (int z = 0; z < nw && nw > 1; z++) {
					double ax2, ay2;
					move_of(q, C, ex, ey, j, i, who[z], at[z], la, &ax2, &ay2);
					sx2 += ww[z] * ax2; sy2 += ww[z] * ay2; wsum += ww[z];
				}
				if (nw > 1 && wsum > 0) { mx = sx2 / wsum; my = sy2 / wsum; }
			}
			q->x[j][i] = k->x[i] + mx;
			q->y[j][i] = k->y[i] + my;
			if (C[o].pull > 0) q->how[j] = HW_NEAT_PULLED;
		}
		free(cum);
	}
	for (size_t k = 0; k < nc; k++) q->pulled[k] = C[k].pull > 0;
done:
	free(v);
	for (size_t j = 0; j < n; j++) { if (ex) free(ex[j]); if (ey) free(ey[j]); }
	free(ex); free(ey);
	for (size_t k = 0; k < nc; k++) free(C[k].pc);
	free(C); free(nown);
	if (ret) hw_neat_free(q);
	return ret;
}
