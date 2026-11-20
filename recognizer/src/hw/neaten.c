// SPDX-License-Identifier: GPL-2.0-or-later
// Neatening (see neaten.h and docs/recognizer.md, "Neatening").
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "neaten.h"

static double clampd(double x, double a, double b) { return x < a ? a : x > b ? b : x; }

/* the shape guard (FR-037): a pulled character's ink nearer one of its own versions than this
 * times the nearest version of any other character */
#define HW_NEAT_GUARD_MARGIN 1.0
/* a character whose ink the letter model moved (a run given to or taken from a neighbour) is
 * pulled only when its ink is at most this many times as far from its own versions as from
 * any other character's */
#define HW_NEAT_MOVED_RATIO 1.0
/* the flow check measures corners and steps back at this share of the line's x-height (a
 * little finer than it, so ink of letters measured smaller has none either) */
#define HW_NEAT_FLOW_FINE 0.75

/* $GLASSINE_HANDWRITING_NEATEN (measurements): "base=0,slant=0,size=0,space=0,pull=0"
 * leaves a correction out, "trace=0" the check of the glyph traced on the ink's points,
 * "set=0" the words set at Neatest (above Strong the pull goes on, all the way at 1),
 * "margin=M" sets the shape guard's margin */
static struct { int base, slant, size, space, pull, trace, set; double margin; } on;
static void knobs(void)
{
	on.base = on.slant = on.size = on.space = on.pull = on.trace = on.set = 1;
	on.margin = HW_NEAT_GUARD_MARGIN;
	const char *e = getenv("GLASSINE_HANDWRITING_NEATEN");
	if (!e) return;
	const char *m = strstr(e, "margin=");
	if (m && atof(m + 7) > 0) on.margin = atof(m + 7);
	struct { const char *k; int *v; } kv[] = { { "base=", &on.base }, { "slant=", &on.slant }, { "size=", &on.size },
						 { "space=", &on.space }, { "pull=", &on.pull }, { "trace=", &on.trace },
						 { "set=", &on.set } };
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
/* ... wider where the two characters' moves differ more: the move changes by at most this
 * much per unit of the stroke's length (half of a tent's slope; below 1 no step goes back) */
#define HW_NEAT_BLEND_SLOPE 0.5
#define HW_NEAT_BLEND_MAX 0.8
/* above this strength a joint between two pulled letters isn't blended: its joining stroke
 * is drawn anew (a cubic of HW_NEAT_JOIN_STEPS segments), from the first glyph's end to the
 * second's start along their directions (over HW_NEAT_JOIN_DIR x-heights of their points) */
#define HW_NEAT_JOIN HW_NEAT_PULL_MEDIUM
#define HW_NEAT_JOIN_STEPS 64
#define HW_NEAT_JOIN_DIR 0.08
/* the ends' directions as long as this share of the chord */
#define HW_NEAT_JOIN_TAN 0.5
/* a run of a stroke's points of one character shorter than this (x-heights along the
 * stroke) is the alignment's spill, not a joint */
#define HW_NEAT_SPILL 0.35
/* a piece more than this many x-heights from where its stroke lies in the glyph (both about
 * their character's middle) hasn't the glyph's structure */
#define HW_NEAT_PLACE 0.45
/* nor ink whose middle lies more than this many x-heights above or below the glyph's on the
 * line */
#define HW_NEAT_PLACE_LINE 0.75
/* nor ink more than this many times as wide as the glyph (at least half an x-height), nor
 * ink whose larger side is less than this share of the glyph's: a letter's ink holds about
 * one letter */
#define HW_NEAT_WIDE 2.0
#define HW_NEAT_SMALL 0.5
/* a piece's matched places reach its glyph stroke's ends within this share of its length
 * (at a joint within the other), else it is traced by the length along it */
#define HW_NEAT_COVER 0.1
/* ... above Medium within this at a joint drawn anew */
#define HW_NEAT_COVER_JOINED 0.02
#define HW_NEAT_COVER_JOINT 0.3
/* how hard the stretch is held to none (per matched point; the matching's map only, the
 * glyph itself is only moved and scaled) */
#define HW_NEAT_RIGID 0.02
/* a turn this sharp (degrees) the version gives a letter's points where its ink has none
 * near (this many points to either side) is a part the ink doesn't have */
#define HW_NEAT_CORNER 90
#define HW_NEAT_CORNER_NEAR 5
#define HW_NEAT_CORNER_RAW 55

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

static int is_space(unsigned c) { return c == ' ' || c == '\t' || c == 0xa0 || c == 0x3000; }

static const struct hw_letter *letter_of(const struct hw_letters *L, unsigned cp)
{
	size_t lo = 0, hi = L ? L->n : 0;
	while (lo < hi) {
		size_t mid = (lo + hi) / 2;
		if (L->l[mid].cp < cp) lo = mid + 1; else hi = mid;
	}
	return L && lo < L->n && L->l[lo].cp == cp ? &L->l[lo] : NULL;
}

void hw_neat_free(struct hw_neat *q)
{
	if (q->x) for (size_t j = 0; j < q->n; j++) free(q->x[j]);
	if (q->y) for (size_t j = 0; j < q->n; j++) free(q->y[j]);
	free(q->x); free(q->y); free(q->how); free(q->pulled); free(q->why); free(q->set);
	q->x = q->y = NULL;
	q->how = NULL;
	q->pulled = q->why = q->set = NULL;
}

/* how far a character goes towards its glyph at strength s (FR-014): half way at Medium,
 * HW_NEAT_PULL_STRONG at Strong, all the way at Neatest; part of the way between */
static double pull_of(double s)
{
	if (s <= HW_NEAT_MEDIUM) return clampd((s - HW_NEAT_LIGHT) / (HW_NEAT_MEDIUM - HW_NEAT_LIGHT), 0, 1) * HW_NEAT_PULL_MEDIUM;
	if (s <= HW_NEAT_STRONG) {
		double t = clampd((s - HW_NEAT_MEDIUM) / (HW_NEAT_STRONG - HW_NEAT_MEDIUM), 0, 1);
		return HW_NEAT_PULL_MEDIUM + t * (HW_NEAT_PULL_STRONG - HW_NEAT_PULL_MEDIUM);
	}
	return HW_NEAT_PULL_STRONG + clampd((s - HW_NEAT_STRONG) / (1 - HW_NEAT_STRONG), 0, 1) * (1 - HW_NEAT_PULL_STRONG);
}

/* a character's piece of ink: points a..b-1 of stroke j, its start or end a joint with
 * another character in the stroke */
/* a piece of a character's ink: points [a, b) of stroke j; at a joint, [a, f) and [l, b) are
 * the joining stroke beyond its glyph stroke's start and end (the connecting part) */
struct piece { int j; size_t a, b, f, l; int joint0, joint1; };

struct ch {
	int npts, cls, ok;
	int why;                        /* HW_NEAT_WHY_*: pulled, or why not */
	double fit;
	double x0, y0, x1, y1, cx;      /* the ink's box (y down) and middle */
	double ax, ay;                  /* the anchor: its middle on the fitted baseline */
	double f, shear, dx, dy;        /* the line-level map */
	double tx0, tx1, ty1;           /* the box after scale and shear */
	double snum, sden;              /* its downstrokes' lean */
	double pull;                    /* how far it goes towards its glyph, 0 not pulled */
	int np;                         /* its pieces, in writing order */
	struct piece *pc;
	const struct hw_variant *v;     /* the variant it goes to */
	const struct hw_lstroke *t;     /* the glyph's strokes (FR-035) */
	double off;                     /* the target placed: its x (x-heights) less the variant's */
	int pair;                       /* of a joined pair the font draws as one glyph: 1 its first letter, 2 its second */
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

/* the turn at point i of a polyline in degrees, 0 where a step is shorter than min */
static double turn_at(const double *x, const double *y, size_t i, double min)
{
	double ax = x[i] - x[i - 1], ay = y[i] - y[i - 1], bx = x[i + 1] - x[i], by = y[i + 1] - y[i];
	double na = hypot(ax, ay), nb = hypot(bx, by);
	if (!(na >= min) || !(nb >= min)) return 0;
	return acos(clampd((ax * bx + ay * by) / (na * nb), -1, 1)) * 180 / M_PI;
}

/* a step from point i against the way the stroke goes from i - 3 to i + 3 */
static int step_back(const double *x, const double *y, size_t i, double xh)
{
	double dx = x[i + 3] - x[i - 3], dy = y[i + 3] - y[i - 3], sx = x[i + 1] - x[i], sy = y[i + 1] - y[i];
	double nd = hypot(dx, dy), ns = hypot(sx, sy);
	return nd > 0.15 * xh && ns > 0.01 * xh && dx * sx + dy * sy < -0.5 * nd * ns;
}

/* the ink (x, y) moved by p (dx, dy) gets a corner (a turn over HW_NEAT_CORNER) where the ink
 * turns by less than HW_NEAT_CORNER_RAW within HW_NEAT_CORNER_NEAR points, or a step back where it
 * has none; at, when given, marks every such point (else the first ends the search) */
static int new_corners(const double *x, const double *y, const double *dx, const double *dy, size_t m, double xh, double p,
		       unsigned char *at)
{
	if (m < 3) return 0;
	double *gx = malloc(m * 2 * sizeof *gx);
	if (!gx) return 0;
	double *gy = gx + m;
	for (size_t i = 0; i < m; i++) { gx[i] = x[i] + p * dx[i]; gy[i] = y[i] + p * dy[i]; }
	int found = 0;
	for (size_t i = 1; i + 1 < m && (at || !found); i++) {
		if (!(turn_at(gx, gy, i, 0.01 * xh) > HW_NEAT_CORNER)) continue;
		int raw = 0;
		for (size_t z = i > HW_NEAT_CORNER_NEAR ? i - HW_NEAT_CORNER_NEAR : 1; z + 1 < m && z <= i + HW_NEAT_CORNER_NEAR && !raw; z++)
			raw = turn_at(x, y, z, 0.01 * xh) > HW_NEAT_CORNER_RAW;
		if (!raw) { found = 1; if (at) at[i] = 1; }
	}
	/* or a step back along the way the stroke goes over 3 points to either side */
	for (size_t i = 3; i + 4 <= m && (at || !found); i++) {
		if (!step_back(gx, gy, i, xh)) continue;
		int raw = 0;
		for (size_t z = i > HW_NEAT_CORNER_NEAR + 3 ? i - HW_NEAT_CORNER_NEAR : 3; z + 4 <= m && z <= i + HW_NEAT_CORNER_NEAR && !raw; z++)
			raw = step_back(x, y, z, xh);
		if (!raw) { found = 1; if (at) at[i] = at[i + 1] = 1; }
	}
	free(gx);
	return found;
}

static int new_corner(const double *x, const double *y, const double *dx, const double *dy, size_t m, double xh, double p)
{
	return new_corners(x, y, dx, dy, m, xh, p, NULL);
}


/* The pull of character k (FR-014, FR-035, FR-036) towards its glyph (c->v, the one the font
 * draws there): its pieces, whether they have the glyph's structure (as many as its strokes,
 * each about in its place), how far it goes (by the level), and for each of its points where
 * the pull takes it: the place on the glyph that matches it in shape (hw_neat_match), the glyph
 * only moved and scaled, on the straight baseline at the line's x-height with its middle on
 * the ink's (as the matched points have it), without a baseline its middle on the ink's.
 * 0, -1 out of memory. */
static double guard_ratio(const struct hw_neat *q, size_t k, double xh, int neat, unsigned char *const *skip,
			  const struct hw_variant *glyph);

static int pull_prepare(struct hw_neat *q, struct ch *C, size_t k, const int *nown, double xh, int hasbase,
			double base, double s, double **ex, double **ey, int doubt)
{
	struct ch *c = &C[k];
	/* its target: the glyph the font draws for it there (set by the caller) */
	if (!c->v) { c->why = HW_NEAT_WHY_NOT_COVERED; return 0; }
	c->why = HW_NEAT_WHY_NO_STRUCTURE;
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
	double *cover = NULL;
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
	ret = 0;
	/* the structure (FR-014), coarsely: as many pieces as the glyph has strokes, each about
	 * where its stroke lies in the glyph (so a stroke order or a cut of a joined stroke
	 * other than the glyph's isn't pulled into it), and on the line about where the glyph
	 * lies above the baseline (so ink the alignment gave the wrong character, a letter for
	 * a mark above it, isn't pulled into that one's glyph); how closely the shapes agree
	 * doesn't matter. The count here, the places and the size once matched (below), on the
	 * letter's own ink */
	if ((int)c->v->nstrokes != np) goto out;
	c->t = c->v->s;
	c->pull = pull_of(s);
	if (!(c->pull > 0)) { c->why = HW_NEAT_WHY_LEVEL; goto out; }
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
	size_t all = 0, nfp;
	for (int a = 0; a < np; a++) all += sn[a];
	double *fp = malloc((all + 1) * 4 * sizeof *fp);
	cover = malloc(((size_t)np + 1) * 2 * sizeof *cover);
	if (!fp || !cover) { free(fp); free(cover); goto out; }
	for (int a = 0; a < np; a++) { cover[2 * a] = 1; cover[2 * a + 1] = 0; }
	for (int pass = 0; pass < 2; pass++) {
		nfp = 0;
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
			for (size_t i = 0; i < m; i++) {
				size_t pi = pc->a + i;
				if (isnan(u[i])) { ex[pc->j][pi] = ey[pc->j][pi] = NAN; continue; }
				if (pass) { cover[2 * a] = fmin(cover[2 * a], u[i]); cover[2 * a + 1] = fmax(cover[2 * a + 1], u[i]); }
				double f2[2], p2[2] = { X[i], Y[i] };
				/* (the version unmapped: the map only finds the places) */
				at_fraction(T, TY, tn, tc, u[i], &f2[0], &f2[1]);
				fp[4 * nfp] = f2[0]; fp[4 * nfp + 1] = f2[1]; fp[4 * nfp + 2] = p2[0]; fp[4 * nfp + 3] = p2[1];
				nfp++;
				if (!pass) continue;
				double gx = acx + (f2[0] + sh[0]) * xh, fy = f2[1] + sh[1] - shy;
				double gy = hasbase ? base - fy * xh : acy - (fy - vcy) * xh;
				ex[pc->j][pi] = gx - sx[a][i];
				ey[pc->j][pi] = gy - sx[np + a][i];
			}
			free(X); free(T);
		}
		/* (a map that would mirror: the glyph as placed) */
		if (affine_fit(fp, nfp, M, mf, mp, &r2)) { M[0] = M[3] = 1; M[1] = M[2] = 0; }
		if (!pass) for (int r = 0; r < 2; r++) sh[r] = clampd(mp[r] - mf[r], -0.5, 0.5);
	}
	free(fp);
	/* a joint's lead-in or lead-out the version doesn't have goes with the point next to it */
	for (int a = 0; a < np; a++) {
		const struct piece *pc = &c->pc[a];
		double *px = ex[pc->j], *py = ey[pc->j];
		size_t f = pc->a, l = pc->b;
		while (f < pc->b && isnan(px[f])) f++;
		while (l > f && isnan(px[l - 1])) l--;
		for (size_t i = pc->a; i < f; i++) { px[i] = f < pc->b ? px[f] : 0; py[i] = f < pc->b ? py[f] : 0; }
		for (size_t i = l; i < pc->b; i++) { px[i] = px[l - 1]; py[i] = py[l - 1]; }
		c->pc[a].f = f;
		c->pc[a].l = f < pc->b ? l : f;
	}
	/* the places and the size (above), on the letter's own ink: a joint's connecting part
	 * left out */
	{
		double icx = 0, icy = 0, tcx = 0, tcy = 0, pb[64][8];
		long ni = 0, nt = 0;
		int bad = np > 64;
		for (int a = 0; !bad && a < np; a++) {
			double *b = pb[a];
			b[0] = b[2] = b[4] = b[6] = INFINITY;
			b[1] = b[3] = b[5] = b[7] = -INFINITY;
			/* (the letter's own ink: a joint's connecting part left out) */
			size_t f0 = c->pc[a].f - c->pc[a].a, l0 = c->pc[a].l - c->pc[a].a;
			if (l0 <= f0) { f0 = 0; l0 = sn[a]; }
			for (size_t i = f0; i < l0; i++) {
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
		if (hasbase && !bad) {
			double i0 = INFINITY, i1 = -INFINITY, t0 = INFINITY, t1 = -INFINITY;
			for (int a = 0; a < np; a++) { i0 = fmin(i0, pb[a][2]); i1 = fmax(i1, pb[a][3]); t0 = fmin(t0, pb[a][6]); t1 = fmax(t1, pb[a][7]); }
			bad = fabs((i0 + i1) / 2 + base / xh - (t0 + t1) / 2) > HW_NEAT_PLACE_LINE;
		}
		for (int a = 0; !bad && a < np; a++) {
			const double *b = pb[a];
			double dx = ((b[0] + b[1]) / 2 - icx) - ((b[4] + b[5]) / 2 - tcx), dy = ((b[2] + b[3]) / 2 - icy) - ((b[6] + b[7]) / 2 - tcy);
			bad = hypot(dx, dy) > HW_NEAT_PLACE;
		}
		/* and about its glyph's size: ink far wider than the glyph holds more than this
		 * letter (a joined word's stroke given whole to one of its letters), ink far smaller
		 * is a cut of a joined stroke that left it a little of its own; pulled, the one
		 * would shrink its neighbours' ink into this glyph, the other draw the glyph where
		 * the neighbours' ink still holds the letter */
		if (!bad) {
			double i0 = INFINITY, i1 = -INFINITY, j0 = INFINITY, j1 = -INFINITY, t0 = INFINITY, t1 = -INFINITY, u0 = INFINITY, u1 = -INFINITY;
			for (int a = 0; a < np; a++) {
				i0 = fmin(i0, pb[a][0]); i1 = fmax(i1, pb[a][1]); j0 = fmin(j0, pb[a][2]); j1 = fmax(j1, pb[a][3]);
				t0 = fmin(t0, pb[a][4]); t1 = fmax(t1, pb[a][5]); u0 = fmin(u0, pb[a][6]); u1 = fmax(u1, pb[a][7]);
			}
			double gw = t1 - t0, gs = fmax(gw, u1 - u0);
			bad = i1 - i0 > HW_NEAT_WIDE * fmax(gw, 0.5) || fmax(i1 - i0, j1 - j0) < HW_NEAT_SMALL * gs;
		}
		if (bad) { c->pull = 0; ret = 0; goto out; }
	}
	/* a character the letter model gave ink of its neighbour's or took ink from (the
	 * alignment off there): only pulled when its own ink as written (a joint's connecting
	 * part left out) is nearer its own versions than any other character's, else the run
	 * moved can be its neighbour's whole letter, which the pull would draw as this one */
	if (doubt) {
		unsigned char **skip = calloc(q->n + 1, sizeof *skip);
		int oom = !skip;
		for (int a = 0; !oom && a < np; a++) {
			const struct piece *pc = &c->pc[a];
			if (!skip[pc->j] && !(skip[pc->j] = calloc(q->ink[pc->j].n + 1, 1))) { oom = 1; break; }
			for (size_t i = pc->a; i < pc->b; i++) if (pc->l > pc->f && (i < pc->f || i >= pc->l)) skip[pc->j][i] = 2;
		}
		double rr = oom ? -1 : guard_ratio(q, k, xh, 0, skip, NULL);
		for (size_t j = 0; skip && j < q->n; j++) free(skip[j]);
		free(skip);
		if (rr < 0) goto out;
		if (isfinite(rr) && rr > HW_NEAT_MOVED_RATIO) { c->why = HW_NEAT_WHY_SHAPE_GUARD; c->pull = 0; ret = 0; goto out; }
	}
	/* the glyph traced on the ink's points (all the way, and as far as the level pulls): a
	 * corner or a step back it gets where the ink has none is a part of the glyph the shape
	 * matching gave too few points (a loop the ink lacks, a turn written otherwise). The
	 * piece is traced again by the length along it instead (its points spread over the glyph's
	 * stroke as over the ink's); still kinked, the glyph hasn't the ink's structure there and
	 * it isn't pulled. So is a piece whose matched places don't reach the glyph stroke's ends
	 * (within HW_NEAT_COVER of its length, HW_NEAT_COVER_JOINT at a joint): the glyph is
	 * drawn whole, not a part of it on all of the piece's points */
	for (int a = 0; a < np; a++) {
		const struct piece *pc = &c->pc[a];
		size_t m = sn[a];
		double *px = ex[pc->j] + pc->a, *py = ey[pc->j] + pc->a;
		/* (above Medium the joining stroke is drawn anew from the glyph's very end: its
		 * own ink takes all of the glyph's stroke there too) */
		double cj = s > HW_NEAT_JOIN ? HW_NEAT_COVER_JOINED : HW_NEAT_COVER_JOINT;
		int whole = cover[2 * a] <= (pc->joint0 ? cj : HW_NEAT_COVER) &&
			    cover[2 * a + 1] >= 1 - (pc->joint1 ? cj : HW_NEAT_COVER);
		/* (above Medium a joint's connecting part is drawn anew, or, where it isn't, the
		 * flow check of the whole stroke holds the letters back: the letter's own points
		 * here, from the first to the last) */
		size_t t0 = 0, t1 = m;
		if (s > HW_NEAT_JOIN && pc->l > pc->f) {
			if (pc->joint0) t0 = pc->f - pc->a;
			if (pc->joint1) t1 = pc->l - pc->a;
		}
#define TRACE_KINKS() (new_corner(sx[a] + t0, sx[np + a] + t0, px + t0, py + t0, t1 - t0, xh, 1) || \
		       new_corner(sx[a] + t0, sx[np + a] + t0, px + t0, py + t0, t1 - t0, xh, c->pull))
		if (whole && !TRACE_KINKS()) continue;
		const struct hw_lstroke *t = &c->t[a];
		double *ic = malloc((m + 1 + t->n + 1) * sizeof *ic), *tc = ic + m + 1;
		if (!ic) goto out;
		/* (the connecting part at a joint stays as it is: the glyph on the letter's own) */
		size_t f0 = pc->f - pc->a, l0 = pc->l - pc->a;
		if (l0 <= f0) { f0 = 0; l0 = m; }
		arc(sx[a], sx[np + a], m, ic);
		double il = ic[l0 - 1] - ic[f0];
		arc(t->x, t->y, t->n, tc);
		for (size_t i = f0; i < l0; i++) {
			double fx, fy;
			at_fraction(t->x, t->y, t->n, tc, il > 0 ? (ic[i] - ic[f0]) / il : 0, &fx, &fy);
			fx -= vcx;
			fy += shy;
			double gx = acx + (fx + sh[0]) * xh, gy2 = fy + sh[1] - shy;
			double gy = hasbase ? base - gy2 * xh : acy - (gy2 - vcy) * xh;
			px[i] = gx - sx[a][i];
			py[i] = gy - sx[np + a][i];
		}
		for (size_t i = 0; i < f0; i++) { px[i] = px[f0]; py[i] = py[f0]; }
		for (size_t i = l0; i < m; i++) { px[i] = px[l0 - 1]; py[i] = py[l0 - 1]; }
		free(ic);
		if (on.trace && TRACE_KINKS()) {
			c->pull = 0;
			ret = 0;
			goto out;
		}
	}
#undef TRACE_KINKS
	c->off = acx / xh - vcx + sh[0];
	c->why = HW_NEAT_WHY_PULLED;
	ret = 0;
out:
	for (int a = 0; sx && a < np; a++) { free(sx[a]); free(sx[np + a]); }
	free(sx); free(sn); free(cover);
	if (ret || !(c->pull > 0)) c->pull = 0;
	return ret;
}

/* the piece of character o (a joined pair's second letter: of the pair) with point i of
 * stroke j */
static const struct piece *piece_at(const struct ch *C, int o, size_t j, size_t i)
{
	const struct ch *c = &C[C[o].pair == 2 ? o - 1 : o];
	for (int a = 0; a < c->np; a++)
		if (c->pc[a].j == (int)j && c->pc[a].a <= i && i < c->pc[a].b) return &c->pc[a];
	return NULL;
}

/* a joint at point i of stroke j (where it passes from one character to the next) whose
 * joining stroke is drawn anew (above Medium, both letters pulled): its connecting part,
 * points [*A, *B), from where the first letter's glyph stroke ends to where the second's
 * starts, with a point of each letter's own on either side */
static int joint_redrawn(const struct hw_neat *q, const struct ch *C, size_t j, size_t i, double s, double xh, size_t *A, size_t *B)
{
	if (!(s > HW_NEAT_JOIN) || !i || i >= q->ink[j].n) return 0;
	int o1 = q->own[j][i - 1], o2 = q->own[j][i];
	if (o1 < 0 || o2 < 0 || o1 == o2 || !(C[o1].pull > 0) || !(C[o2].pull > 0)) return 0;
	const struct piece *p1 = piece_at(C, o1, j, i - 1), *p2 = piece_at(C, o2, j, i);
	if (!p1 || !p2 || p1 == p2 || p1->b != i || p2->a != i) return 0;
	*A = p1->l; *B = p2->f;
	/* (where the glyphs' ends leave none: the points within HW_NEAT_BLEND x-heights of
	 * the cut along the stroke) */
	const double *x = q->ink[j].x, *y = q->ink[j].y;
	int none = *B <= *A;
	if (none) {
		double d = 0;
		size_t z = i;
		while (z > p1->a + 1 && d < HW_NEAT_BLEND * xh) { d += hypot(x[z - 1] - x[z - 2], y[z - 1] - y[z - 2]); z--; }
		*A = z;
	}
	if (none) {
		double d = 0;
		size_t z = i;
		while (z + 1 < p2->b && d < HW_NEAT_BLEND * xh) { d += hypot(x[z + 1] - x[z], y[z + 1] - y[z]); z++; }
		*B = z;
	}
	if (*A <= p1->a) *A = p1->a + 1;
	if (*B >= p2->b) *B = p2->b - 1;
	return *A > p1->a && *B < p2->b && *B > *A;
}

/* the ink of stroke j as moved turns back (more than a right angle) at a point from the end of
 * a redrawn join's first glyph stroke to the start of its second's (A - 1 to B) where the ink
 * as written turns by no more than 45 degrees within 5 points */
static int join_folds(const struct hw_neat *q, size_t j, size_t A, size_t B, double xh)
{
	const struct hw_ink *k = &q->ink[j];
	size_t m = k->n;
	for (size_t z = A > 1 ? A - 1 : 1; z <= B && z + 1 < m; z++) {
		double ax = q->x[j][z] - q->x[j][z - 1], ay = q->y[j][z] - q->y[j][z - 1];
		double bx = q->x[j][z + 1] - q->x[j][z], by = q->y[j][z + 1] - q->y[j][z];
		double na = hypot(ax, ay), nb = hypot(bx, by);
		if (na < 0.01 * xh || nb < 0.01 * xh || (ax * bx + ay * by) / (na * nb) > 0) continue;
		int raw = 0;
		for (size_t w = z > 5 ? z - 5 : 1; w <= z + 5 && w + 1 < m && !raw; w++) raw = turn_at(k->x, k->y, w, 0.01 * xh) > 45;
		if (!raw) return 1;
	}
	return 0;
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

/* the runs of character k's points along the strokes */
static int pieces_of(const struct hw_neat *q, size_t k)
{
	int np = 0;
	for (size_t j = 0; j < q->n; j++)
		for (size_t i = 0; i < q->ink[j].n; i++) np += q->own[j][i] == (int)k && (!i || q->own[j][i - 1] != (int)k);
	return np;
}

/* a version of cp (not left out) with ns strokes */
static int has_version(const struct hw_letters *L, unsigned cp, int ns)
{
	const struct hw_letter *le = letter_of(L, cp);
	for (int t = 0; le && t < le->nvar; t++) if (!le->var[t].out && le->var[t].nstrokes == (size_t)ns) return 1;
	return 0;
}

/* a version of cp (not left out) with fewer than ns strokes */
static int fewer_version(const struct hw_letters *L, unsigned cp, int ns)
{
	const struct hw_letter *le = letter_of(L, cp);
	for (int t = 0; le && t < le->nvar; t++) if (!le->var[t].out && le->var[t].nstrokes < (size_t)ns) return 1;
	return 0;
}

/* a character's ink as one sequence for the shape guard: HW_NEAT_GUARD_POINTS points along
 * its strokes in writing order, each stroke as many as its share of their length (a pen lift
 * isn't part of it), centred; x, y in x-heights, y up. A dot or a stroke of no length gets one. */
#define HW_NEAT_GUARD_POINTS 48
static int guard_seq(double *const *sx, double *const *sy, const size_t *sn, size_t ns, double scale, int flip,
		     double *gx, double *gy)
{
	double tot = 0, *len = malloc((ns + 1) * sizeof *len);
	if (!len) return -1;
	for (size_t j = 0; j < ns; j++) { len[j] = arc(sx[j], sy[j], sn[j], NULL); tot += len[j]; }
	size_t o = 0, left = HW_NEAT_GUARD_POINTS;
	for (size_t j = 0; j < ns; j++) {
		size_t m = j + 1 == ns ? left : (size_t)fmax(1, round(HW_NEAT_GUARD_POINTS * (tot > 0 ? len[j] / tot : 1.0 / ns)));
		if (m > left - (ns - 1 - j)) m = left - (ns - 1 - j);
		if (!m) continue;
		if (m == 1 || sn[j] < 2) {
			for (size_t i = 0; i < m; i++) { gx[o + i] = sx[j][0]; gy[o + i] = sy[j][0]; }
		} else resample(sx[j], sy[j], sn[j], m, gx + o, gy + o);
		o += m; left -= m;
	}
	free(len);
	double cx = 0, cy = 0;
	for (size_t i = 0; i < o; i++) { gx[i] *= scale; gy[i] *= flip ? -scale : scale; cx += gx[i]; cy += gy[i]; }
	for (size_t i = 0; i < o; i++) { gx[i] -= cx / o; gy[i] -= cy / o; }
	return (int)o;
}

static double guard_dist(const double *ax, const double *ay, int na, const struct hw_variant *v)
{
	size_t ns = v->nstrokes;
	if (!ns) return INFINITY;
	double **vs = malloc(2 * ns * sizeof *vs);
	size_t *vn = malloc(ns * sizeof *vn);
	if (!vs || !vn) { free(vs); free(vn); return INFINITY; }
	for (size_t j = 0; j < ns; j++) { vs[j] = v->s[j].x; vs[ns + j] = v->s[j].y; vn[j] = v->s[j].n; }
	double bx[HW_NEAT_GUARD_POINTS], by[HW_NEAT_GUARD_POINTS];
	int nb = guard_seq(vs, vs + ns, vn, ns, 1, 0, bx, by);
	free(vs); free(vn);
	if (nb != na) return INFINITY;
	double e = 0;
	for (int i = 0; i < na; i++) e += (ax[i] - bx[i]) * (ax[i] - bx[i]) + (ay[i] - by[i]) * (ay[i] - by[i]);
	return sqrt(e / na);
}

/* how near character k's ink is to its own versions against the nearest version of any other
 * character, whatever their strokes (as one sequence each, guard_seq): the distances' ratio,
 * below 1 nearer its own; neat the neatened ink (q->x, q->y), else the ink as written. -1 out
 * of memory, INFINITY when it can't be told */
static double guard_ratio(const struct hw_neat *q, size_t k, double xh, int neat, unsigned char *const *skip,
			  const struct hw_variant *glyph)
{
	/* (its ink without the joining strokes drawn anew: skip 2) */
#define G_OWN(j, i) (q->own[j][i] == (int)k && !(skip && skip[j] && skip[j][i] == 2))
	const struct hw_letter *own = letter_of(q->L, q->cp[k]);
	if (!own || !(xh > 0)) return INFINITY;
	size_t np = 0;
	for (size_t j = 0; j < q->n; j++)
		for (size_t i = 0; i < q->ink[j].n; i++) np += G_OWN(j, i) && (!i || !G_OWN(j, i - 1));
	if (!np) return INFINITY;
	double **sx = calloc(np * 2 + 1, sizeof *sx);
	size_t *sn = calloc(np + 1, sizeof *sn);
	if (!sx || !sn) { free(sx); free(sn); return -1; }
	size_t a = 0;
	for (size_t j = 0; j < q->n; j++)
		for (size_t i = 0; i < q->ink[j].n; i++) {
			if (!G_OWN(j, i) || (i && G_OWN(j, i - 1))) continue;
			size_t b = i;
			while (b < q->ink[j].n && G_OWN(j, b)) b++;
			sx[a] = (neat ? q->x[j] : q->ink[j].x) + i; sx[np + a] = (neat ? q->y[j] : q->ink[j].y) + i; sn[a] = b - i;
			a++;
		}
	double ax[HW_NEAT_GUARD_POINTS], ay[HW_NEAT_GUARD_POINTS];
	int na = guard_seq(sx, sx + np, sn, np, 1 / xh, 1, ax, ay);
	free(sx); free(sn);
	if (na < 0) return -1;
	double mine = INFINITY, other = INFINITY;
	for (int t = 0; t < own->nvar; t++)
		if (!own->var[t].out) mine = fmin(mine, guard_dist(ax, ay, na, &own->var[t]));
	/* (the glyph the font draws for it, its target, is one of its own) */
	if (glyph) mine = fmin(mine, guard_dist(ax, ay, na, glyph));
	for (size_t l = 0; isfinite(mine) && l < q->L->n; l++) {
		const struct hw_letter *le = &q->L->l[l];
		if (le == own || !le->covered) continue;
		for (int t = 0; t < le->nvar; t++)
			if (!le->var[t].out) other = fmin(other, guard_dist(ax, ay, na, &le->var[t]));
	}
#undef G_OWN
	if (!isfinite(mine)) return INFINITY;
	return other > 0 ? mine / other : mine > 0 ? INFINITY : 0;
}

/* the shape guard of character k (FR-037): 1 when its neatened ink isn't within the margin
 * nearer its own versions than another character's (guard_ratio over HW_NEAT_GUARD_MARGIN)
 * and the neatening took it further that way than it was written (ink already nearer another
 * character, as a hasty letter can be, isn't held back by the guard unless the pull makes it
 * more so), 0 when it passes, -1 out of memory */
static int guard_fails(const struct hw_neat *q, size_t k, double xh, unsigned char *const *skip, const struct hw_variant *glyph)
{
	double rn = guard_ratio(q, k, xh, 1, skip, glyph);
	if (rn < 0) return -1;
	if (!isfinite(rn) || rn <= on.margin) return 0;
	double rr = guard_ratio(q, k, xh, 0, skip, glyph);
	if (rr < 0) return -1;
	return rn > rr;
}

/* the shape cut: a stroke the alignment shares among several letters of a word (a joined
 * word written in one go) is cut where its parts best match the letters' glyphs, each
 * letter's part its glyph's stroke as its pieces come in writing order; the alignment's
 * own edges come from its frames, which can fall a letter off or give a letter none */
#define CUT_N 24                /* points a part and a glyph stroke are compared at */
#define CUT_LEN 0.25

static void cut_resample(const double *x, const double *y, size_t n, double *ox, double *oy)
{
	double tot = 0;
	for (size_t i = 1; i < n; i++) tot += hypot(x[i] - x[i - 1], y[i] - y[i - 1]);
	size_t i = 0;
	double at = 0;
	for (int z = 0; z < CUT_N; z++) {
		double want = tot * z / (CUT_N - 1);
		while (i + 1 < n && at + hypot(x[i + 1] - x[i], y[i + 1] - y[i]) < want) { at += hypot(x[i + 1] - x[i], y[i + 1] - y[i]); i++; }
		if (i + 1 >= n) { ox[z] = x[n - 1]; oy[z] = y[n - 1]; continue; }
		double l = hypot(x[i + 1] - x[i], y[i + 1] - y[i]), u = l > 0 ? (want - at) / l : 0;
		ox[z] = x[i] + u * (x[i + 1] - x[i]);
		oy[z] = y[i] + u * (y[i + 1] - y[i]);
	}
}

/* how far part [a, b) of ink k (x-heights xh, y down) is from glyph stroke g (y up):
 * centred RMS at the line's x-height, plus its length against the glyph's */
static double cut_cost(const struct hw_ink *k, size_t a, size_t b, const double *cum, const double *gx, const double *gy,
		       double glen, double xh)
{
	if (b - a < 2) return INFINITY;
	double px[CUT_N], py[CUT_N], mx = 0, my = 0, gmx = 0, gmy = 0;
	cut_resample(k->x + a, k->y + a, b - a, px, py);
	for (int z = 0; z < CUT_N; z++) { px[z] /= xh; py[z] = -py[z] / xh; mx += px[z]; my += py[z]; gmx += gx[z]; gmy += gy[z]; }
	mx /= CUT_N; my /= CUT_N; gmx /= CUT_N; gmy /= CUT_N;
	double e = 0;
	for (int z = 0; z < CUT_N; z++) {
		double dx = px[z] - mx - (gx[z] - gmx), dy = py[z] - my - (gy[z] - gmy);
		e += dx * dx + dy * dy;
	}
	double len = (cum[b - 1] - cum[a]) / xh;
	return sqrt(e / CUT_N) + CUT_LEN * fabs(log(fmax(len, 0.02) / fmax(glen, 0.02)));
}

static int shape_cut(struct hw_neat *q, const struct hw_letters *V, const int *gvar, const long *gjo, double xh)
{
	size_t n = q->n, nc = q->nc;
	if (!V || !gvar || !(xh > 0)) return 0;
	for (size_t j = 0; j < n; j++) {
		const struct hw_ink *k = &q->ink[j];
		int *ow = q->own[j];
		size_t m = k->n;
		if ((q->keep && q->keep[j]) || m < 4) continue;
		/* its letters: as ALIGN lists them, else those the alignment gave its points and
		 * the ones between */
		int c0 = -1, c1 = -1;
		if (q->share)
			for (size_t c = 0; c < nc; c++) { if (q->share[j * nc + c]) { if (c0 < 0) c0 = (int)c; c1 = (int)c; } }
		else
			for (size_t i = 0; i < m; i++)
				if (ow[i] >= 0 && (size_t)ow[i] < nc) { if (c0 < 0 || ow[i] < c0) c0 = ow[i]; if (ow[i] > c1) c1 = ow[i]; }
		/* (without ALIGN, a neighbour the alignment gave fewer pieces than its glyph has
		 * strokes, its others after this stroke (or before, for one before): the piece it
		 * lacks is here) */
		for (int side = 0; !q->share && c0 >= 0 && side < 2; side++) {
			int c = side ? c1 + 1 : c0 - 1;
			if (c < 0 || (size_t)c >= nc || is_space(q->cp[c]) || gvar[c] < 0) continue;
			const struct hw_letter *le = letter_of(V, q->cp[c]);
			if (!le || gvar[c] >= le->nvar) continue;
			int np = 0, wrong = 0;
			for (size_t jj = 0; jj < n; jj++)
				for (size_t i = 0; i < q->ink[jj].n; i++)
					if (q->own[jj][i] == c && (!i || q->own[jj][i - 1] != c)) { np++; wrong |= side ? jj < j : jj > j; }
			if (!wrong && (size_t)np < le->var[gvar[c]].nstrokes) { if (side) c1 = c; else c0 = c; }
		}
		if (c0 < 0 || c1 <= c0) continue;
		/* one word, every letter with its glyph, no pair the font joins */
		size_t R = (size_t)(c1 - c0 + 1);
		int ok = 1;
		for (int c = c0; c <= c1 && ok; c++)
			ok = !is_space(q->cp[c]) && gvar[c] >= 0 && !(gjo && gjo[c] >= 0) && letter_of(V, q->cp[c]);
		if (!ok) continue;
		double *gx = malloc(R * CUT_N * 2 * sizeof *gx), *glen = malloc(R * sizeof *glen), *cum = malloc((m + 1) * sizeof *cum);
		double *D = malloc((R + 1) * (m + 1) * sizeof *D);
		size_t *P = malloc((R + 1) * (m + 1) * sizeof *P);
		if (!gx || !glen || !cum || !D || !P) { free(gx); free(glen); free(cum); free(D); free(P); return -1; }
		for (size_t r = 0; r < R && ok; r++) {
			size_t c = (size_t)c0 + r;
			const struct hw_letter *le = letter_of(V, q->cp[c]);
			if (gvar[c] >= le->nvar) { ok = 0; break; }
			const struct hw_variant *v = &le->var[gvar[c]];
			/* its glyph stroke: as many as its pieces before this stroke */
			size_t before = 0;
			for (size_t jj = 0; jj < j; jj++)
				for (size_t i = 0; i < q->ink[jj].n; i++)
					if (q->own[jj][i] == (int)c && (!i || q->own[jj][i - 1] != (int)c)) before++;
			if (!v->nstrokes) { ok = 0; break; }
			const struct hw_lstroke *g = &v->s[before < v->nstrokes ? before : v->nstrokes - 1];
			if (g->n < 2) { ok = 0; break; }
			cut_resample(g->x, g->y, g->n, gx + 2 * r * CUT_N, gx + (2 * r + 1) * CUT_N);
			glen[r] = 0;
			for (size_t i = 1; i < g->n; i++) glen[r] += hypot(g->x[i] - g->x[i - 1], g->y[i] - g->y[i - 1]);
		}
		if (ok) {
			arc(k->x, k->y, m, cum);
			/* D[r][b]: the least cost of the first r letters over points [0, b) */
			for (size_t i = 0; i < (R + 1) * (m + 1); i++) D[i] = INFINITY;
			D[0] = 0;
			for (size_t r = 1; r <= R; r++)
				for (size_t b = 2 * r; b <= m; b++) {
					if (r == R && b != m) continue;
					for (size_t a = 2 * (r - 1); a + 2 <= b; a++) {
						double d0 = D[(r - 1) * (m + 1) + a];
						if (!isfinite(d0) || (r == 1 && a)) continue;
						double d = d0 + cut_cost(k, a, b, cum, gx + 2 * (r - 1) * CUT_N, gx + (2 * r - 1) * CUT_N, glen[r - 1], xh);
						if (d < D[r * (m + 1) + b]) { D[r * (m + 1) + b] = d; P[r * (m + 1) + b] = a; }
					}
				}
			/* (without ALIGN, not where one letter's glyph alone takes the whole stroke as well as the
			 * parts take theirs on the mean: that's one letter's stroke the alignment spread) */
			double one = INFINITY;
			for (size_t r = 0; r < R; r++)
				one = fmin(one, cut_cost(k, 0, m, cum, gx + 2 * r * CUT_N, gx + (2 * r + 1) * CUT_N, glen[r], xh));
			if (isfinite(D[R * (m + 1) + m]) && (q->share || !(one <= D[R * (m + 1) + m] / (double)R))) {
				size_t b = m;
				for (size_t r = R; r >= 1; r--) {
					size_t a = P[r * (m + 1) + b];
					for (size_t i = a; i < b; i++) ow[i] = c0 + (int)r - 1;
					b = a;
				}
			}
		}
		free(gx); free(glen); free(cum); free(D); free(P);
	}
	return 0;
}

static int neaten_once(struct hw_neat *q, const unsigned char *mask, unsigned char *fail, const struct hw_letters *V,
		       const int *gvar, const long *gjo, unsigned char *doubt)
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
	/* the alignment's edges spill a few points of a stroke over to a neighbour: a run of a
	 * stroke's points of one character shorter than HW_NEAT_SPILL x-heights (along the
	 * stroke) goes to the run next to it (the longer one), the shortest first. A longer run
	 * is a joint, where the stroke passes from one letter to the next: each keeps its part.
	 * ALIGN is as given. */
	double xh0 = q->xh;
	{
		if (!(xh0 > 0)) {
			double *hs = malloc((n + 1) * sizeof *hs);
			size_t nh = 0;
			for (size_t j = 0; hs && j < n; j++) {
				double lo = INFINITY, hi = -INFINITY;
				for (size_t i = 0; i < q->ink[j].n; i++) { lo = fmin(lo, q->ink[j].y[i]); hi = fmax(hi, q->ink[j].y[i]); }
				if (q->ink[j].n >= 5 && hi > lo) hs[nh++] = hi - lo;
			}
			xh0 = hs ? 0.7 * median(hs, nh, 0) : 0;
			free(hs);
		}
		if (q->fit && s > HW_NEAT_LIGHT && shape_cut(q, V, gvar, gjo, xh0)) { free(C); free(nown); hw_neat_free(q); return -1; }
		for (size_t j = 0; q->fit && xh0 > 0 && j < n; j++) {
			const struct hw_ink *k = &q->ink[j];
			int *ow = q->own[j];
			/* points of no character go with their neighbours' first */
			int last = -1;
			for (size_t i = 0; i < k->n; i++) { if (ow[i] >= 0 && (size_t)ow[i] < nc) last = ow[i]; else if (last >= 0) ow[i] = last; }
			last = -1;
			for (size_t i = k->n; i-- > 0;) { if (ow[i] >= 0) last = ow[i]; else ow[i] = last; }
			for (size_t step = 0; step < k->n; step++) {
				/* the runs: [a, b) of one character, and their lengths */
				size_t best = SIZE_MAX, ba = 0, bb = 0, runs = 0;
				double bl = HW_NEAT_SPILL * xh0;
				for (size_t a = 0; a < k->n;) {
					size_t b = a;
					while (b < k->n && ow[b] == ow[a]) b++;
					if (ow[a] >= 0) {
						runs++;
						double l = 0;
						for (size_t i = a + 1; i < b; i++) l += hypot(k->x[i] - k->x[i - 1], k->y[i] - k->y[i - 1]);
						if (l < bl) { bl = l; best = a; ba = a; bb = b; }
					}
					a = b;
				}
				if (best == SIZE_MAX || runs < 2) break;
				/* the neighbouring runs' owners (past points of no character) and lengths */
				int lo = -1, hi = -1;
				double ll = 0, lh = 0;
				size_t i = ba;
				while (i > 0 && ow[i - 1] < 0) i--;
				if (i > 0) { lo = ow[i - 1]; for (size_t z = i - 1; z > 0 && ow[z - 1] == lo; z--) ll += hypot(k->x[z] - k->x[z - 1], k->y[z] - k->y[z - 1]); }
				i = bb;
				while (i < k->n && ow[i] < 0) i++;
				if (i < k->n) { hi = ow[i]; for (size_t z = i + 1; z < k->n && ow[z] == hi; z++) lh += hypot(k->x[z] - k->x[z - 1], k->y[z] - k->y[z - 1]); }
				int to = lo < 0 ? hi : hi < 0 ? lo : ll >= lh ? lo : hi;
				if (to < 0) break;
				for (size_t z = ba; z < bb; z++) ow[z] = to;
			}
		}
	}
	/* and by the letter model: a character with a count of pieces of ink no version of it has
	 * (and more than some), one of them a run of a stroke it shares with a neighbour, gives
	 * that run to the neighbour (a joint the alignment put too far along); the neighbour's
	 * count stays, the run joins its own */
	for (int round = 0; q->fit && q->L && round < 8; round++) {
		int changed = 0;
		for (size_t j = 0; j < n && !changed; j++) {
			const struct hw_ink *k = &q->ink[j];
			int *ow = q->own[j];
			for (size_t a = 0; a < k->n && !changed;) {
				size_t b = a;
				while (b < k->n && ow[b] == ow[a]) b++;
				int o = ow[a], lo = a > 0 ? ow[a - 1] : -1, hi = b < k->n ? ow[b] : -1;
				if (o >= 0 && (size_t)o < nc && (lo >= 0 || hi >= 0)) {
					int np = pieces_of(q, (size_t)o);
					if (np >= 2 && !has_version(q->L, q->cp[o], np) && fewer_version(q->L, q->cp[o], np)) {
						int to = lo < 0 ? hi : hi < 0 ? lo : (a == 0 ? hi : lo);
						if (to >= 0 && (size_t)to < nc) {
							doubt[o] = doubt[to] = 1;
							for (size_t z = a; z < b; z++) ow[z] = to;
							changed = 1;
						}
					}
				}
				a = b;
			}
		}
		if (!changed) break;
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
			/* a joined stroke keeps its parts: the rules move whole strokes */
			int first = -1, joined = 0;
			for (size_t i = 0; i < q->ink[j].n; i++) {
				int o = q->own[j][i];
				if (o < 0 || (size_t)o >= nc) continue;
				if (first < 0) first = o; else if (o != first) joined = 1;
			}
			if (joined) continue;
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
	double wx0 = INFINITY, wx1 = -INFINITY;
	for (size_t k = 0; k < nc; k++) {
		struct ch *c = &C[k];
		c->why = c->npts ? HW_NEAT_WHY_LEVEL : HW_NEAT_WHY_NO_INK;
		if (!c->npts) continue;
		c->cx = (c->x0 + c->x1) / 2;
		c->ok = c->fit >= HW_NEAT_FIT_READ;
		if (c->x0 < wx0) wx0 = c->x0;
		if (c->x1 > wx1) wx1 = c->x1;
	}
	/* a word (between spaces) read with low confidence, its characters' mean fit below
	 * HW_NEAT_FIT_READ, gets the line-level corrections only; the others keep their pull
	 * (FR-017). The fit is the alignment's (the model's frames for the characters), not the
	 * dictionary's: a word it lacks reads as well as any other. */
	for (size_t k0 = 0; k0 < nc;) {
		size_t k1 = k0;
		while (k1 < nc && !is_space(q->cp[k1])) k1++;
		double sf = 0;
		int nw = 0;
		for (size_t k = k0; k < k1; k++) if (C[k].npts) { sf += C[k].fit > 0 ? C[k].fit : 0; nw++; }
		if (nw && sf / nw < HW_NEAT_FIT_READ)
			for (size_t k = k0; k < k1; k++) { C[k].ok = 0; if (C[k].npts) C[k].why = HW_NEAT_WHY_LOW_CONFIDENCE; }
		k0 = k1 + 1;
	}
	int ret = 0;
	unsigned char *hold = NULL, **rdr = NULL;
	double *v = malloc((nc + 1) * 3 * sizeof *v);
	/* per point of a stroke: where the pull takes it from where the line-level map puts it */
	double **ex = calloc(n + 1, sizeof *ex), **ey = calloc(n + 1, sizeof *ey);
	q->pulled = calloc(nc + 1, 1);
	q->why = calloc(nc + 1, 1);
	if (!v || !ex || !ey || !q->pulled || !q->why) { ret = -1; goto done; }
	for (size_t k = 0; k < nc; k++) {
		if (!q->L && C[k].why == HW_NEAT_WHY_LEVEL) C[k].why = HW_NEAT_WHY_NOT_COVERED;
		q->why[k] = (unsigned char)C[k].why;
	}
	for (size_t j = 0; j < n; j++) {
		ex[j] = calloc(q->ink[j].n + 1, sizeof **ex);
		ey[j] = calloc(q->ink[j].n + 1, sizeof **ey);
		if (!ex[j] || !ey[j]) { ret = -1; goto done; }
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
	q->line_xh = xh; q->line_base = target_base; q->line_hasbase = hasbase;

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
	/* a letter with no ink of its own in a word, or far less than its glyph's size, that
	 * shares its strokes with its neighbours: its ink is in theirs (a joined stroke ALIGN
	 * gave whole to one letter, or a joint the alignment put far along), so neither it nor
	 * they are pulled; every letter keeps its ink at about its own size */
	hold = calloc(nc + 1, 1);
	if (!hold) { ret = -1; goto done; }
	for (size_t k = 0; V && k < nc; k++) {
		if (is_space(q->cp[k]) || gvar[k] < 0) continue;
		const struct hw_letter *le = letter_of(V, q->cp[k]);
		if (!le || gvar[k] >= le->nvar) continue;
		const struct hw_variant *gv = &le->var[gvar[k]];
		double g0 = INFINITY, g1 = -INFINITY, h0 = INFINITY, h1 = -INFINITY;
		for (size_t a = 0; a < gv->nstrokes; a++)
			for (size_t i = 0; i < gv->s[a].n; i++) {
				g0 = fmin(g0, gv->s[a].x[i]); g1 = fmax(g1, gv->s[a].x[i]);
				h0 = fmin(h0, gv->s[a].y[i]); h1 = fmax(h1, gv->s[a].y[i]);
			}
		double gs = fmax(g1 - g0, h1 - h0);
		struct ch *c = &C[k];
		double e = c->npts ? fmax(c->x1 - c->x0, c->y1 - c->y0) / xh : 0;
		if (!(gs > 0) || e >= HW_NEAT_SMALL * gs) continue;
		if (!c->npts) {
			for (int d = -1; d <= 1; d += 2)
				for (long z = (long)k + d; z >= 0 && z < (long)nc && !is_space(q->cp[z]); z += d)
					if (C[z].npts) { hold[z] = 1; break; }
			continue;
		}
		for (size_t j = 0; j < n; j++) {
			if (nown[j] < 2) continue;
			int mine = 0;
			for (size_t i = 0; i < q->ink[j].n && !mine; i++) mine = q->own[j][i] == (int)k;
			if (!mine) continue;
			hold[k] = 1;
			for (size_t i = 0; i < q->ink[j].n; i++) if (q->own[j][i] >= 0 && (size_t)q->own[j][i] < nc) hold[q->own[j][i]] = 1;
		}
	}
	/* the pull (FR-014, FR-035, FR-036): a character read well whose glyph (the one the font
	 * draws for it there: the cycle, the neighbour rules, a joined pair) has its structure,
	 * towards that glyph; a joined pair's two letters together towards its one glyph */
	if (q->L && V && on.pull && !q->line_only && s > HW_NEAT_LIGHT)
		for (size_t k = 0; k < nc; k++) {
			struct ch *c = &C[k];
			int pair = gjo[k] >= 0 && k + 1 < nc && gjo[k + 1] == gjo[k];
			if (!c->npts || c->why == HW_NEAT_WHY_LOW_CONFIDENCE) { k += pair; continue; }
			int bad = c->fit < HW_NEAT_FIT_PULL || (pair && C[k + 1].npts && C[k + 1].fit < HW_NEAT_FIT_PULL);
			int guard = (q->nopull && (q->nopull[k] || (pair && q->nopull[k + 1]))) || (mask && (mask[k] || (pair && mask[k + 1])));
			int flow = (mask && (mask[k] == 2 || (pair && mask[k + 1] == 2))) || hold[k] || (pair && hold[k + 1]);
			if (bad || guard || flow) {
				c->why = bad ? HW_NEAT_WHY_LOW_CONFIDENCE : flow ? HW_NEAT_WHY_NO_STRUCTURE : HW_NEAT_WHY_SHAPE_GUARD;
				if (pair) { C[k + 1].why = c->why; k++; }
				continue;
			}
			if (!pair) {
				if (gvar[k] >= 0) {
					const struct hw_letter *le = letter_of(V, q->cp[k]);
					if (le && gvar[k] < le->nvar) c->v = &le->var[gvar[k]];
				}
				if (pull_prepare(q, C, k, nown, xh, hasbase, target_base, s, ex, ey, doubt[k])) { ret = -1; goto done; }
				continue;
			}
			/* the pair: one glyph, so one map for both letters (the first's) and the second
			 * letter's points as the first's while it is pulled */
			c->v = &V->joined[gjo[k]].v;
			C[k + 1].f = c->f; C[k + 1].shear = c->shear; C[k + 1].ax = c->ax; C[k + 1].ay = c->ay; C[k + 1].dy = c->dy;
			c->pair = 1;
			C[k + 1].pair = 2;
			size_t nm = 0;
			for (size_t j = 0; j < n; j++)
				for (size_t i = 0; i < q->ink[j].n; i++) nm += q->own[j][i] == (int)k + 1;
			size_t (*moved)[2] = malloc((nm + 1) * sizeof *moved);
			if (!moved) { ret = -1; goto done; }
			nm = 0;
			for (size_t j = 0; j < n; j++)
				for (size_t i = 0; i < q->ink[j].n; i++)
					if (q->own[j][i] == (int)k + 1) { q->own[j][i] = (int)k; moved[nm][0] = j; moved[nm++][1] = i; }
			int r = pull_prepare(q, C, k, nown, xh, hasbase, target_base, s, ex, ey, 0);
			for (size_t z = 0; z < nm; z++) q->own[moved[z][0]][moved[z][1]] = (int)k + 1;
			free(moved);
			if (r) { ret = -1; goto done; }
			struct ch *d = &C[k + 1];
			d->why = c->why;
			d->pull = c->pull;
			d->v = c->v; d->t = c->t; d->off = c->off;
			k++;
		}

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
		/* a pulled character: its profile and middle part of the way to its glyph's,
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
	rdr = calloc(n + 1, sizeof *rdr);
	if (!rdr) { ret = -1; goto done; }
	for (size_t j = 0; j < n; j++) {
		if (!nown[j]) continue;
		const struct hw_ink *k = &q->ink[j];
		size_t m = k->n;
		double *cum = malloc((m + 1) * sizeof *cum);
		if (!cum) { ret = -1; goto done; }
		arc(k->x, k->y, m, cum);
		/* the joints' blends: wide enough that the move changes by at most HW_NEAT_BLEND_SLOPE
		 * of the way along the stroke (no step back, no kink, however far the two characters
		 * are pulled apart), at most HW_NEAT_BLEND_MAX x-heights */
		double *hw = nown[j] > 1 ? malloc((m + 1) * sizeof *hw) : NULL;
		if (nown[j] > 1 && !hw) { free(cum); ret = -1; goto done; }
		/* (a joint whose joining stroke is drawn anew isn't blended: each letter's points
		 * as its own pull takes them; seg counts those joints along the stroke) */
		int *seg = nown[j] > 1 ? malloc((m + 1) * sizeof *seg) : NULL;
		if (nown[j] > 1 && !seg) { free(cum); free(hw); ret = -1; goto done; }
		for (size_t i = 0; seg && i < m; i++) {
			size_t A, B;
			seg[i] = (i ? seg[i - 1] : 0) + joint_redrawn(q, C, j, i, s, xh, &A, &B);
		}
		for (size_t i = 0; hw && i < m; i++) hw[i] = HW_NEAT_BLEND * xh;
		for (size_t i = 1; hw && i < m; i++) {
			int o1 = q->own[j][i - 1], o2 = q->own[j][i];
			if (o1 < 0 || o2 < 0 || o1 == o2 || seg[i] != seg[i - 1]) continue;
			double ax1, ay1, ax2, ay2;
			move_of(q, C, ex, ey, j, i - 1, o1, i - 1, la, &ax1, &ay1);
			move_of(q, C, ex, ey, j, i, o2, i, la, &ax2, &ay2);
			double h = clampd(hypot(ax2 - ax1, ay2 - ay1) / HW_NEAT_BLEND_SLOPE, HW_NEAT_BLEND * xh, HW_NEAT_BLEND_MAX * xh);
			for (size_t z = 0; z < m; z++)
				if (fabs(cum[z] - cum[i]) < h && h > hw[z]) hw[z] = h;
		}
		for (size_t i = 0; i < m; i++) {
			int o = q->own[j][i];
			if (o < 0) continue;
			double mx, my;
			move_of(q, C, ex, ey, j, i, o, i, la, &mx, &my);
			if (nown[j] > 1) {
				/* the characters' moves weighted by how much of the stroke around the point is
				 * theirs (a tent of HW_NEAT_BLEND x-heights to either side, by arc length), each
				 * as at its point nearest to it: smooth across the joints, a short run too */
				double h = hw[i], wsum = 0, sx2 = 0, sy2 = 0;
				size_t lo2 = i, hi2 = i;
				while (lo2 > 0 && cum[i] - cum[lo2 - 1] < h) lo2--;
				while (hi2 + 1 < m && cum[hi2 + 1] - cum[i] < h) hi2++;
				int who[8], nw = 0;
				double ww[8];
				size_t at[8];
				for (size_t i2 = lo2; i2 <= hi2; i2++) {
					int o2 = q->own[j][i2];
					if (o2 < 0 || seg[i2] != seg[i]) continue;
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
		/* the joining strokes drawn anew (FR-036): from the end of the first letter's glyph
		 * stroke to the start of the second's, a cubic along both ends' directions; the
		 * person's points of the connecting part spread along it by their length along the
		 * ink (so each keeps its time and pressure); the joint's cut in its middle */
		for (size_t i = 1; seg && i < m; i++) {
			size_t A, B;
			if (seg[i] == seg[i - 1] || !joint_redrawn(q, C, j, i, s, xh, &A, &B)) continue;
			int o1 = q->own[j][i - 1], o2 = q->own[j][i];
			const struct piece *p1 = piece_at(C, o1, j, i - 1), *p2 = piece_at(C, o2, j, i);
			double x0 = q->x[j][A - 1], y0 = q->y[j][A - 1], x1 = q->x[j][B], y1 = q->y[j][B];
			double L = hypot(x1 - x0, y1 - y0), d[4] = { 0, 0, 0, 0 };
			/* the ends' directions: over a short way of the letter's own points */
			for (size_t z = A - 1; z > p1->a; z--) {
				d[0] = x0 - q->x[j][z - 1]; d[1] = y0 - q->y[j][z - 1];
				if (hypot(d[0], d[1]) >= HW_NEAT_JOIN_DIR * xh) break;
			}
			for (size_t z = B + 1; z < p2->b; z++) {
				d[2] = q->x[j][z] - x1; d[3] = q->y[j][z] - y1;
				if (hypot(d[2], d[3]) >= HW_NEAT_JOIN_DIR * xh) break;
			}
			for (int e = 0; e < 4; e += 2) {
				double l = hypot(d[e], d[e + 1]);
				if (l > 0) { d[e] *= HW_NEAT_JOIN_TAN * L / l; d[e + 1] *= HW_NEAT_JOIN_TAN * L / l; }
				else { d[e] = x1 - x0; d[e + 1] = y1 - y0; }
			}
			double cx[HW_NEAT_JOIN_STEPS + 1], cy[HW_NEAT_JOIN_STEPS + 1], cl[HW_NEAT_JOIN_STEPS + 1];
			for (int z = 0; z <= HW_NEAT_JOIN_STEPS; z++) {
				double t = (double)z / HW_NEAT_JOIN_STEPS, t2 = t * t, t3 = t2 * t;
				double h00 = 2 * t3 - 3 * t2 + 1, h10 = t3 - 2 * t2 + t, h01 = -2 * t3 + 3 * t2, h11 = t3 - t2;
				cx[z] = h00 * x0 + h10 * d[0] + h01 * x1 + h11 * d[2];
				cy[z] = h00 * y0 + h10 * d[1] + h01 * y1 + h11 * d[3];
			}
			arc(cx, cy, HW_NEAT_JOIN_STEPS + 1, cl);
			double il = cum[B] - cum[A - 1];
			size_t mid = A;
			for (size_t z = A; z < B; z++) {
				double u = il > 0 ? (cum[z] - cum[A - 1]) / il : (double)(z - A + 1) / (double)(B - A + 1);
				at_fraction(cx, cy, HW_NEAT_JOIN_STEPS + 1, cl, u, &q->x[j][z], &q->y[j][z]);
				if (u < 0.5) mid = z + 1;
			}
			/* (a cubic that turns sharply where the ink doesn't, two glyph ends facing apart:
			 * the moves go from the one end's to the other's along the connecting part) */
			if (join_folds(q, j, A, B, xh)) {
				double m0x = x0 - k->x[A - 1], m0y = y0 - k->y[A - 1], m1x = x1 - k->x[B], m1y = y1 - k->y[B];
				for (size_t z = A; z < B; z++) {
					double u = il > 0 ? (cum[z] - cum[A - 1]) / il : (double)(z - A + 1) / (double)(B - A + 1);
					q->x[j][z] = k->x[z] + (1 - u) * m0x + u * m1x;
					q->y[j][z] = k->y[z] + (1 - u) * m0y + u * m1y;
				}
			}
			for (size_t z = A; z < B; z++) q->own[j][z] = z < mid ? o1 : o2;
			/* (a join that folds even so, the glyph ends themselves turned back on each other:
			 * not a join drawn smooth, so the flow check holds its letters back) */
			if (join_folds(q, j, A, B, xh)) continue;
			if (!rdr[j]) rdr[j] = calloc(m + 1, 1);
			if (!rdr[j]) { free(cum); free(hw); free(seg); ret = -1; goto done; }
			for (size_t z = A > 2 ? A - 2 : 0; z < B + 2 && z < m; z++) rdr[j][z] = z >= A && z < B ? 2 : rdr[j][z] ? rdr[j][z] : 1;
		}
		free(cum); free(hw); free(seg);
	}
	/* a pulled letter's separate marks (a dot, a bar: a stroke of its own, not its tallest)
	 * kept where its glyph has them from that stroke, the stem: a mark goes where the full
	 * pull puts it, moved as far as the stem's own points (the joining strokes left out)
	 * ended up from where the full pull puts them, so a dot sits on its stem however far the
	 * stem was pulled (at Neatest, where the full pull puts it) */
	for (size_t k = 0; k < nc; k++) {
		struct ch *c = &C[k];
		if (!(c->pull > 0) || c->pair == 2 || c->np < 2 || !c->t) continue;
		int an = 0;
		double best = -1;
		for (int a = 0; a < c->np; a++) {
			double lo = INFINITY, hi = -INFINITY;
			for (size_t i = 0; i < c->t[a].n; i++) { lo = fmin(lo, c->t[a].y[i]); hi = fmax(hi, c->t[a].y[i]); }
			if (hi - lo > best) { best = hi - lo; an = a; }
		}
		/* the mean of (where piece p's points are) less (where the full pull puts them),
		 * over [f, l) */
		double r[2], t[2], w[2];
		const struct piece *ap = &c->pc[an];
		size_t f = ap->f < ap->l ? ap->f : ap->a, l = ap->f < ap->l ? ap->l : ap->b;
		size_t nr = 0;
		r[0] = r[1] = 0;
		for (size_t i = f; i < l; i++) {
			double x, y;
			if (!isfinite(ex[ap->j][i]) || !isfinite(ey[ap->j][i])) continue;
			line_map(c, q->ink[ap->j].x[i], q->ink[ap->j].y[i], &x, &y);
			r[0] += q->x[ap->j][i] - x - ex[ap->j][i];
			r[1] += q->y[ap->j][i] - y - ey[ap->j][i];
			nr++;
		}
		if (!nr) continue;
		r[0] /= (double)nr; r[1] /= (double)nr;
		for (int b = 0; b < c->np; b++) {
			const struct piece *pc = &c->pc[b];
			if (b == an || pc->joint0 || pc->joint1 || nown[pc->j] != 1 || pc->b <= pc->a) continue;
			size_t nt = 0;
			t[0] = t[1] = w[0] = w[1] = 0;
			for (size_t i = pc->a; i < pc->b; i++) {
				double x, y;
				if (!isfinite(ex[pc->j][i]) || !isfinite(ey[pc->j][i])) continue;
				line_map(c, q->ink[pc->j].x[i], q->ink[pc->j].y[i], &x, &y);
				t[0] += x + ex[pc->j][i]; t[1] += y + ey[pc->j][i];
				w[0] += q->x[pc->j][i]; w[1] += q->y[pc->j][i];
				nt++;
			}
			if (!nt) continue;
			double dx = (t[0] - w[0]) / (double)nt + r[0], dy = (t[1] - w[1]) / (double)nt + r[1];
			for (size_t i = pc->a; i < pc->b; i++) { q->x[pc->j][i] += dx; q->y[pc->j][i] += dy; }
		}
	}
	for (size_t k = 0; k < nc; k++) {
		q->pulled[k] = C[k].pull > 0;
		q->why[k] = (unsigned char)(C[k].pull > 0 ? HW_NEAT_WHY_PULLED : C[k].why == HW_NEAT_WHY_PULLED ? HW_NEAT_WHY_LEVEL : C[k].why);
	}
	/* the shape guard (FR-037): each pulled character's new ink nearer its own versions than
	 * any other character's with as many strokes */
	for (size_t k = 0; fail && k < nc; k++) {
		/* (a joined pair is the font's one glyph for both: no other character's) */
		if (!q->pulled[k] || C[k].pair) continue;
		int g = guard_fails(q, k, xh, rdr, C[k].v);
		if (g < 0) { ret = -1; goto done; }
		if (g || (q->guard && q->guard[k])) fail[k] = 1;
	}
	/* the flow (FR-036): no corner or step back along a stroke where the ink has none, also
	 * where a pulled letter meets its neighbour; up to Medium a pulled character with one in
	 * its points or within a joint's blend of them (measured a little finer than the line's
	 * x-height) is neatened again without the pull; above it the joining strokes are drawn
	 * anew, so one there holds no letter back, one in a letter's own points that letter */
	for (size_t j = 0; fail && on.trace && j < n; j++) {
		size_t m = q->ink[j].n;
		if (q->how[j] != HW_NEAT_PULLED || m < 3) continue;
		unsigned char *at = calloc(m, 1);
		double *mv = malloc((2 * m + 1) * sizeof *mv), *cum = malloc((m + 1) * sizeof *cum);
		if (!at || !mv || !cum) { free(at); free(mv); free(cum); ret = -1; goto done; }
		for (size_t i = 0; i < m; i++) { mv[i] = q->x[j][i] - q->ink[j].x[i]; mv[m + i] = q->y[j][i] - q->ink[j].y[i]; }
		arc(q->ink[j].x, q->ink[j].y, m, cum);
		if (new_corners(q->ink[j].x, q->ink[j].y, mv, mv + m, m, HW_NEAT_FLOW_FINE * xh, 1, at))
			for (size_t i = 0; i < m; i++) {
				if (!at[i]) continue;
				if (s > HW_NEAT_JOIN) {
					int o = q->own[j][i];
					if (!(rdr[j] && rdr[j][i]) && o >= 0 && (size_t)o < nc && q->pulled[o] && !fail[o]) fail[o] = 2;
					continue;
				}
				for (size_t z = 0; z < m; z++) {
					int o = q->own[j][z];
					if (o >= 0 && (size_t)o < nc && q->pulled[o] && fabs(cum[z] - cum[i]) <= HW_NEAT_BLEND_MAX * xh && !fail[o]) fail[o] = 2;
				}
			}
		free(at); free(mv); free(cum);
	}
done:
	free(hold);
	for (size_t j = 0; rdr && j < n; j++) free(rdr[j]);
	free(rdr);
	free(v);
	for (size_t j = 0; j < n; j++) { if (ex) free(ex[j]); if (ey) free(ey[j]); }
	free(ex); free(ey);
	for (size_t k = 0; k < nc; k++) free(C[k].pc);
	free(C); free(nown);
	if (ret) hw_neat_free(q);
	return ret;
}

/* Setting (FR-043): a point on a unit's glyph (its strokes one after another, u its share
 * of their length), in x-heights, y up; at a stroke's end late picks the next stroke's start */
static void glyph_at(const struct hw_variant *v, const double *len, double tot, double u, int late, double *x, double *y)
{
	double at = clampd(u, 0, 1) * tot, eps = 1e-9 * tot;
	size_t j = 0;
	while (j + 1 < v->nstrokes && (late ? at >= len[j] - eps : at > len[j] + eps)) { at -= len[j]; j++; }
	const struct hw_lstroke *t = &v->s[j];
	if (t->n < 2) { *x = t->n ? t->x[0] : 0; *y = t->n ? t->y[0] : 0; return; }
	for (size_t i = 1; i < t->n; i++) {
		double d = hypot(t->x[i] - t->x[i - 1], t->y[i] - t->y[i - 1]);
		if (at <= d || i + 1 == t->n) {
			double w = d > 0 ? clampd(at / d, 0, 1) : 0;
			*x = t->x[i - 1] + w * (t->x[i] - t->x[i - 1]);
			*y = t->y[i - 1] + w * (t->y[i] - t->y[i - 1]);
			return;
		}
		at -= d;
	}
}

/* the share of the glyph's length each piece of a unit's ink runs along: as the pieces'
 * lengths share the ink, a pen lift moved to the end of a glyph's stroke near it (each end
 * once, in order); as many pieces as the glyph has strokes: one each */
static void set_spans(const double *plen, int np, const double *len, size_t ns, double *T)
{
	double ink = 0, tot = 0;
	for (int a = 0; a < np; a++) ink += plen[a];
	for (size_t j = 0; j < ns; j++) tot += len[j];
	T[0] = 0; T[np] = 1;
	double acc = 0, gacc = 0;
	size_t g = 0;
	for (int a = 1; a < np; a++) {
		acc += plen[a - 1];
		double t = ink > 0 ? acc / ink : (double)a / np;
		if ((size_t)np == ns) { gacc += len[a - 1]; T[a] = tot > 0 ? gacc / tot : t; continue; }
		T[a] = t;
		/* the nearest end of a glyph's stroke not taken yet, leaving enough for the lifts after */
		double best = INFINITY, cum = 0;
		size_t bj = 0;
		for (size_t j = 0; j + 1 < ns; j++) {
			cum += len[j];
			if (j < g || ns - 1 - j < (size_t)(np - a)) continue;
			double e = tot > 0 ? cum / tot : 0;
			if (fabs(e - t) < best) { best = fabs(e - t); bj = j; }
		}
		if (best < HW_NEAT_SET_SNAP) {
			double cum2 = 0;
			for (size_t j = 0; j <= bj; j++) cum2 += len[j];
			T[a] = cum2 / tot;
			g = bj + 1;
		}
		if (T[a] < T[a - 1]) T[a] = T[a - 1];
	}
}

/* a unit's glyph: a joined pair's, a letter's variant there, NULL none */
static const struct hw_variant *set_glyph(const struct hw_letters *V, const unsigned *cp, const int *gvar, const long *gjo, size_t k)
{
	if (gjo[k] >= 0) return &V->joined[gjo[k]].v;
	const struct hw_letter *l = gvar[k] >= 0 ? letter_of(V, cp[k]) : NULL;
	return l && gvar[k] < l->nvar ? &l->var[gvar[k]] : NULL;
}

/* whether the word's ink is the word set there: about as wide as the font sets it, and each
 * character's ink about where the font puts its glyph (both from the word's left, the ink's
 * places scaled to the font's width), so a stroke of a neighbouring word the alignment gave
 * it, or a reading that isn't what was written, isn't set over the ink */
static int set_fits(const struct hw_neat *q, const struct hw_letters *V, const int *gvar, const long *gjo, const double *pen,
		    double bear, const int *sw, int w, size_t k0, size_t k1, double xl, double gl, double xh)
{
	double xr = -INFINITY, gr = -INFINITY;
	for (size_t j = 0; j < q->n; j++)
		if (sw[j] == w) for (size_t i = 0; i < q->ink[j].n; i++) xr = fmax(xr, q->x[j][i]);
	for (size_t k = k0; k < k1; k++) {
		const struct hw_variant *v = set_glyph(V, q->cp, gvar, gjo, k);
		for (size_t t = 0; v && t < v->nstrokes; t++)
			for (size_t i = 0; i < v->s[t].n; i++) gr = fmax(gr, pen[k] + bear + v->s[t].x[i]);
		if (!v) gr = fmax(gr, pen[k] + 1);
	}
	double wi = (xr - xl) / xh, wg = gr - gl;
	if (!(wi > 0 && wg > 0) || wi > HW_NEAT_SET_WIDE * wg || wg > HW_NEAT_SET_WIDE * wi) return 0;
	for (size_t k = k0; k < k1; k++) {
		if (gjo[k] >= 0 && k > k0 && gjo[k - 1] == gjo[k]) continue;
		int pair = gjo[k] >= 0 && k + 1 < k1 && gjo[k + 1] == gjo[k];
		double a = INFINITY, b = -INFINITY, c = INFINITY, d = -INFINITY;
		for (size_t j = 0; j < q->n; j++)
			for (size_t i = 0; sw[j] == w && i < q->ink[j].n; i++) {
				int o = q->own[j][i];
				if (o == (int)k || (pair && o == (int)k + 1)) { a = fmin(a, q->x[j][i]); b = fmax(b, q->x[j][i]); }
			}
		const struct hw_variant *v = set_glyph(V, q->cp, gvar, gjo, k);
		for (size_t t = 0; v && t < v->nstrokes; t++)
			for (size_t i = 0; i < v->s[t].n; i++) { c = fmin(c, pen[k] + bear + v->s[t].x[i]); d = fmax(d, pen[k] + bear + v->s[t].x[i]); }
		if (!v) { c = pen[k]; d = pen[k] + 1; }
		if (!(a <= b) || !(c <= d)) continue;
		double ci = ((a + b) / 2 - xl) / xh * wg / wi, cg = (c + d) / 2 - gl;
		if (fabs(ci - cg) > HW_NEAT_SET_PLACE) return 0;
	}
	return 1;
}

/* the words set: per character 1 when its word is set; how far (f: 0 Strong's ink, 1 set) */
static int set_words(struct hw_neat *q, const struct hw_letters *V, const int *gvar, const long *gjo, double f)
{
	size_t nc = q->nc, n = q->n;
	double xh = q->line_xh, base = q->line_base;
	/* (only the confirmed text: the reading's fit doesn't tell a word read wrongly) */
	if (!q->confirmed || !q->line_hasbase || !(xh > 0) || !nc || !q->x) return 0;
	int ret = -1;
	double *pen = malloc((nc + 1) * sizeof *pen), **X = NULL;
	int *word = malloc((nc + 1) * sizeof *word), *sw = malloc((n + 1) * sizeof *sw);
	long *npts = calloc(nc + 1, sizeof *npts);
	unsigned char *ok = NULL;
	if (!pen || !word || !sw || !npts) goto out;
	double bear;
	hw_font_layout(V, q->cp, nc, gvar, gjo, pen, &bear);
	int nw = 0;
	for (size_t k = 0; k < nc; k++) {
		if (is_space(q->cp[k])) { word[k] = -1; if (k && !is_space(q->cp[k - 1])) nw++; continue; }
		word[k] = nw;
	}
	nw++;
	ok = malloc((size_t)nw + 1);
	if (!ok) goto out;
	memset(ok, 1, (size_t)nw);
	/* a stroke of one word only; a kept stroke (drawing, a gesture) with a word's ink keeps it as written */
	for (size_t j = 0; j < n; j++) {
		sw[j] = -1;
		for (size_t i = 0; i < q->ink[j].n; i++) {
			int o = q->own[j][i];
			if (o < 0 || (size_t)o >= nc || word[o] < 0) continue;
			if (q->keep && q->keep[j]) { ok[word[o]] = 0; continue; }
			npts[o]++;
			if (sw[j] == -1) sw[j] = word[o];
			else if (sw[j] != word[o]) { if (sw[j] >= 0) ok[sw[j]] = 0; ok[word[o]] = 0; sw[j] = -2; }
		}
	}
	/* each character with ink of its own; some character the font has */
	for (size_t k0 = 0; k0 < nc;) {
		if (word[k0] < 0) { k0++; continue; }
		size_t k1 = k0;
		while (k1 < nc && word[k1] == word[k0]) k1++;
		int w = word[k0], any = 0;
		for (size_t k = k0; k < k1; k++) {
			int lig2 = gjo[k] >= 0 && k > k0 && gjo[k - 1] == gjo[k];
			if (!npts[k] && !lig2 && !(gjo[k] >= 0 && k + 1 < k1 && gjo[k + 1] == gjo[k] && npts[k + 1])) ok[w] = 0;
			if (gjo[k] >= 0 || gvar[k] >= 0) any = 1;
			if (q->why && (q->why[k] == HW_NEAT_WHY_LOW_CONFIDENCE || q->why[k] == HW_NEAT_WHY_DRAWING || q->why[k] == HW_NEAT_WHY_GESTURE)) ok[w] = 0;
		}
		if (!any) ok[w] = 0;
		k0 = k1;
	}
	/* the set points, per stroke */
	X = calloc(2 * (n + 1), sizeof *X);
	if (!X) goto out;
	for (size_t j = 0; j < n; j++) {
		if (sw[j] < 0 || !ok[sw[j]]) continue;
		X[j] = malloc((q->ink[j].n + 1) * sizeof(double));
		X[n + 1 + j] = malloc((q->ink[j].n + 1) * sizeof(double));
		if (!X[j] || !X[n + 1 + j]) goto outx;
	}
	for (size_t k0 = 0; k0 < nc;) {
		if (word[k0] < 0) { k0++; continue; }
		size_t k1 = k0;
		while (k1 < nc && word[k1] == word[k0]) k1++;
		if (!ok[word[k0]]) { k0 = k1; continue; }
		/* the word's left: Strong's leftmost ink; the set word's leftmost glyph point there */
		double xl = INFINITY, gl = INFINITY;
		for (size_t j = 0; j < n; j++)
			if (sw[j] == word[k0]) for (size_t i = 0; i < q->ink[j].n; i++) xl = fmin(xl, q->x[j][i]);
		for (size_t k = k0; k < k1; k++) {
			const struct hw_variant *v = gjo[k] >= 0 ? &V->joined[gjo[k]].v : gvar[k] >= 0 ? &letter_of(V, q->cp[k])->var[gvar[k]] : NULL;
			if (gjo[k] >= 0 && k > k0 && gjo[k - 1] == gjo[k]) continue;
			if (!v) {
				/* not covered: its ink in the middle of the font's box for it */
				double a = INFINITY, b = -INFINITY;
				for (size_t j = 0; j < n; j++)
					for (size_t i = 0; sw[j] == word[k0] && i < q->ink[j].n; i++)
						if (q->own[j][i] == (int)k) { a = fmin(a, q->x[j][i]); b = fmax(b, q->x[j][i]); }
				if (a <= b) gl = fmin(gl, pen[k] + (1 - (b - a) / xh) / 2);
				continue;
			}
			for (size_t t = 0; t < v->nstrokes; t++)
				for (size_t i = 0; i < v->s[t].n; i++) gl = fmin(gl, pen[k] + bear + v->s[t].x[i]);
		}
		double ox = xl - gl * xh;
		if (!set_fits(q, V, gvar, gjo, pen, bear, sw, word[k0], k0, k1, xl, gl, xh)) { ok[word[k0]] = 0; k0 = k1; continue; }
		for (size_t k = k0; k < k1; k++) {
			if (gjo[k] >= 0 && k > k0 && gjo[k - 1] == gjo[k]) continue;
			int pair = gjo[k] >= 0 && k + 1 < k1 && gjo[k + 1] == gjo[k];
			const struct hw_variant *v = gjo[k] >= 0 ? &V->joined[gjo[k]].v : gvar[k] >= 0 ? &letter_of(V, q->cp[k])->var[gvar[k]] : NULL;
#define OWNS(o) ((o) == (int)k || (pair && (o) == (int)k + 1))
			if (!v || !v->nstrokes) {
				double a = INFINITY, b = -INFINITY;
				for (size_t j = 0; j < n; j++)
					for (size_t i = 0; sw[j] == word[k0] && i < q->ink[j].n; i++)
						if (OWNS(q->own[j][i])) { a = fmin(a, q->x[j][i]); b = fmax(b, q->x[j][i]); }
				double dx = ox + (pen[k] + (1 - (b - a) / xh) / 2) * xh - a;
				for (size_t j = 0; j < n; j++)
					for (size_t i = 0; sw[j] == word[k0] && i < q->ink[j].n; i++)
						if (OWNS(q->own[j][i])) { X[j][i] = q->x[j][i] + dx; X[n + 1 + j][i] = q->y[j][i]; }
				continue;
			}
			/* its pieces in writing order, their lengths along the ink as written */
			int np = 0;
			for (size_t j = 0; j < n; j++)
				for (size_t i = 0; sw[j] == word[k0] && i < q->ink[j].n; i++)
					np += OWNS(q->own[j][i]) && (!i || !OWNS(q->own[j][i - 1]));
			double *plen = calloc((size_t)np + 1, sizeof *plen), *T = malloc(((size_t)np + 2) * sizeof *T),
			       *len = malloc((v->nstrokes + 1) * sizeof *len);
			if (!plen || !T || !len) { free(plen); free(T); free(len); goto outx; }
			double tot = 0;
			for (size_t t = 0; t < v->nstrokes; t++) { len[t] = arc(v->s[t].x, v->s[t].y, v->s[t].n, NULL); tot += len[t]; }
			int a = -1;
			for (size_t j = 0; j < n; j++)
				for (size_t i = 0; sw[j] == word[k0] && i < q->ink[j].n; i++) {
					if (!OWNS(q->own[j][i])) continue;
					if (!i || !OWNS(q->own[j][i - 1])) a++;
					else plen[a] += hypot(q->ink[j].x[i] - q->ink[j].x[i - 1], q->ink[j].y[i] - q->ink[j].y[i - 1]);
				}
			set_spans(plen, np, len, v->nstrokes, T);
			/* each point its share along its piece, onto the glyph's centre line */
			a = -1;
			double run = 0;
			for (size_t j = 0; j < n; j++)
				for (size_t i = 0; sw[j] == word[k0] && i < q->ink[j].n; i++) {
					if (!OWNS(q->own[j][i])) continue;
					if (!i || !OWNS(q->own[j][i - 1])) { a++; run = 0; }
					else run += hypot(q->ink[j].x[i] - q->ink[j].x[i - 1], q->ink[j].y[i] - q->ink[j].y[i - 1]);
					double w = plen[a] > 0 ? run / plen[a] : 0, u = T[a] + w * (T[a + 1] - T[a]), gx, gy;
					glyph_at(v, len, tot, u, w < 0.5, &gx, &gy);
					X[j][i] = ox + (pen[k] + bear + gx) * xh;
					X[n + 1 + j][i] = base - gy * xh;
				}
			free(plen); free(T); free(len);
#undef OWNS
		}
		for (size_t k = k0; k < k1; k++) {
			q->set[k] = 1;
			if (gjo[k] >= 0 || gvar[k] >= 0) { q->why[k] = HW_NEAT_WHY_SET; q->pulled[k] = 1; }
		}
		k0 = k1;
	}
	for (size_t j = 0; j < n; j++) {
		if (!X[j] || !ok[sw[j]]) continue;
		for (size_t i = 0; i < q->ink[j].n; i++) {
			q->x[j][i] += f * (X[j][i] - q->x[j][i]);
			q->y[j][i] += f * (X[n + 1 + j][i] - q->y[j][i]);
		}
		q->how[j] = HW_NEAT_PULLED;
	}
	ret = 0;
outx:
	for (size_t j = 0; j < 2 * (n + 1); j++) free(X[j]);
out:
	free(pen); free(word); free(sw); free(npts); free(ok); free(X);
	return ret;
}

/* rounds of the shape guard: a character it fails is neatened again without the pull, which
 * can move its neighbours' ink at a joint */
#define NEAT_GUARD_ROUNDS 3

int hw_neaten(struct hw_neat *q)
{
	/* doubt: the characters whose ink the letter model moved (kept over the rounds: a round
	 * starts from the ink as the one before left it) */
	unsigned char *mask = calloc(q->nc + 1, 1), *fail = calloc(q->nc + 1, 1), *doubt = calloc(q->nc + 1, 1);
	int *gvar = calloc(q->nc + 1, sizeof *gvar);
	long *gjo = calloc(q->nc + 1, sizeof *gjo);
	struct hw_letters V, *vp = NULL;
	int ret = mask && fail && doubt && gvar && gjo ? 0 : -1;
	/* the glyphs the font draws for the text (FR-035): the targets */
	if (!ret && q->L) {
		if (hw_letters_view(q->L, &V)) ret = -1;
		else { vp = &V; hw_letters_glyphs(vp, q->cp, q->nc, gvar, gjo); }
	}
	/* above Strong the words that can be set go that far towards being set (FR-043), the
	 * others keep Strong's pull */
	double s = q->strength;
	knobs();
	if (vp && s > HW_NEAT_STRONG && on.set) q->strength = HW_NEAT_STRONG;
	for (int r = 0; !ret; r++) {
		memset(fail, 0, q->nc + 1);
		ret = neaten_once(q, mask, r < NEAT_GUARD_ROUNDS ? fail : NULL, vp, gvar, gjo, doubt);
		int again = 0;
		for (size_t k = 0; !ret && k < q->nc; k++) if (fail[k] && !mask[k]) { mask[k] = fail[k]; again = 1; }
		if (ret || !again || r >= NEAT_GUARD_ROUNDS) break;
		hw_neat_free(q);
	}
	q->strength = s;
	if (!ret && q->nc && !(q->set = calloc(q->nc + 1, 1))) ret = -1;
	if (!ret && vp && s > HW_NEAT_STRONG && on.set && !q->line_only && q->why && q->pulled)
		ret = set_words(q, vp, gvar, gjo, (s - HW_NEAT_STRONG) / (1 - HW_NEAT_STRONG));
	if (ret) hw_neat_free(q);
	if (vp) hw_letters_view_free(vp);
	free(mask); free(fail); free(doubt); free(gvar); free(gjo);
	return ret;
}
