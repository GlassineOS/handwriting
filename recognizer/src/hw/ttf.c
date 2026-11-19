// SPDX-License-Identifier: GPL-2.0-or-later
// The handwriting font (spec 003 FR-009 to FR-011, FR-034): the letter model as a TrueType
// font, in three styles. Every stroke is outlined as convex pieces of one direction that
// overlap, so the non-zero fill makes their union and overlapping strokes draw as one ink:
// Regular in the pen's width (a quadrilateral per segment, a disc at the ends, a wedge or a
// disc where it turns), Calligraphy as a broad nib swept along it (per segment the hull of the
// nib at both ends, rounded by the thinnest line), Brush with a width per point (per segment
// the hull of the discs at both ends). Variants are unencoded glyphs a contextual-alternates
// lookup ('calt', on by default in text shaping) picks along the text (letters.h,
// hw_letters_step). After it: joined pairs as ligatures ('liga', FR-039), neighbour rules as
// a second 'calt' lookup of chain contexts (FR-038), and, only when asked, each letter's
// versions as random alternates ('rand', FR-040); everything but the outlines is the same in
// the three styles.
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "letters.h"
#include "sample.h"

#define UPEM 1000
#define XH 500                  /* units per x-height */

struct bb { unsigned char *d; size_t n, cap; int bad; };

static void bgrow(struct bb *b, size_t k)
{
	if (b->n + k <= b->cap) return;
	size_t c = (b->n + k) * 2 + 256;
	unsigned char *x = realloc(b->d, c);
	if (!x) { b->bad = 1; return; }
	b->d = x; b->cap = c;
}
static void u8(struct bb *b, unsigned v) { bgrow(b, 1); if (!b->bad) b->d[b->n++] = (unsigned char)v; }
static void u16(struct bb *b, unsigned v) { u8(b, v >> 8 & 255); u8(b, v & 255); }
static void s16(struct bb *b, int v) { u16(b, (unsigned)v & 0xffff); }
static void u32(struct bb *b, uint32_t v) { u16(b, v >> 16); u16(b, v & 0xffff); }
static void put16(struct bb *b, size_t at, unsigned v) { if (!b->bad) { b->d[at] = (unsigned char)(v >> 8); b->d[at + 1] = (unsigned char)v; } }
static void put32(struct bb *b, size_t at, uint32_t v) { put16(b, at, v >> 16); put16(b, at + 2, v & 0xffff); }
static void pad4(struct bb *b) { while (b->n % 4) u8(b, 0); }

/* ---------------------------------------------------------------- outlines */

struct pt { int x, y, on; };
struct glyph {
	struct pt *p;
	int np, cap;
	int *ends, nc, ccap;
	int adv, xmin, ymin, xmax, ymax;
};

static void gpt(struct glyph *g, double x, double y, int on)
{
	if (g->np == g->cap) {
		g->cap = g->cap ? g->cap * 2 : 256;
		struct pt *q = realloc(g->p, (size_t)g->cap * sizeof *q);
		if (!q) return;
		g->p = q;
	}
	g->p[g->np++] = (struct pt){ (int)lround(x), (int)lround(y), on };
}

/* closes the contour started at `start`: clockwise (TrueType's outer direction), or dropped
 * when it has no area */
static void gclose(struct glyph *g, int start)
{
	int n = g->np - start;
	double a = 0;
	for (int i = 0; i < n; i++) {
		struct pt *p = &g->p[start + i], *q = &g->p[start + (i + 1) % n];
		a += (double)p->x * q->y - (double)q->x * p->y;
	}
	if (n < 3 || fabs(a) < 1) { g->np = start; return; }
	if (a > 0)      /* counter-clockwise (y up): reverse */
		for (int i = 0, j = n - 1; i < j; i++, j--) { struct pt t = g->p[start + i]; g->p[start + i] = g->p[start + j]; g->p[start + j] = t; }
	/* a contour must not start off the curve for some readers: rotate to an on-curve point */
	int k = 0;
	while (k < n && !g->p[start + k].on) k++;
	if (k && k < n) {
		struct pt *tmp = malloc((size_t)n * sizeof *tmp);
		if (tmp) {
			for (int i = 0; i < n; i++) tmp[i] = g->p[start + (i + k) % n];
			memcpy(g->p + start, tmp, (size_t)n * sizeof *tmp);
			free(tmp);
		}
	}
	if (g->nc == g->ccap) {
		g->ccap = g->ccap ? g->ccap * 2 : 64;
		int *e = realloc(g->ends, (size_t)g->ccap * sizeof *e);
		if (!e) return;
		g->ends = e;
	}
	g->ends[g->nc++] = g->np - 1;
}

static void disc(struct glyph *g, double cx, double cy, double r)
{
	int s = g->np;
	double ro = r / cos(M_PI / 8);
	for (int i = 0; i < 8; i++) {
		double a = -i * M_PI / 4;       /* clockwise */
		gpt(g, cx + r * cos(a), cy + r * sin(a), 1);
		gpt(g, cx + ro * cos(a - M_PI / 8), cy + ro * sin(a - M_PI / 8), 0);
	}
	gclose(g, s);
}

static void poly4(struct glyph *g, double ax, double ay, double bx, double by, double cx, double cy, double dx, double dy)
{
	int s = g->np;
	gpt(g, ax, ay, 1); gpt(g, bx, by, 1); gpt(g, cx, cy, 1); gpt(g, dx, dy, 1);
	gclose(g, s);
}

static void tri(struct glyph *g, double ax, double ay, double bx, double by, double cx, double cy)
{
	int s = g->np;
	gpt(g, ax, ay, 1); gpt(g, bx, by, 1); gpt(g, cx, cy, 1);
	gclose(g, s);
}

/* Douglas-Peucker on font units */
static void simplify(const double *x, const double *y, size_t a, size_t b, double tol, char *keep)
{
	if (b <= a + 1) return;
	double dx = x[b] - x[a], dy = y[b] - y[a], l = hypot(dx, dy), best = -1;
	size_t bi = a;
	for (size_t i = a + 1; i < b; i++) {
		double d = l > 0 ? fabs(dy * (x[i] - x[a]) - dx * (y[i] - y[a])) / l : hypot(x[i] - x[a], y[i] - y[a]);
		if (d > best) { best = d; bi = i; }
	}
	if (best > tol) { keep[bi] = 1; simplify(x, y, a, bi, tol, keep); simplify(x, y, bi, b, tol, keep); }
}

static void outline_stroke(struct glyph *g, const struct hw_lstroke *s, double ox, double r)
{
	if (!s->n) return;
	double *x = malloc(s->n * sizeof *x), *y = malloc(s->n * sizeof *y);
	char *keep = calloc(s->n, 1);
	if (!x || !y || !keep) { free(x); free(y); free(keep); return; }
	for (size_t i = 0; i < s->n; i++) { x[i] = ox + s->x[i] * XH; y[i] = s->y[i] * XH; }
	keep[0] = keep[s->n - 1] = 1;
	simplify(x, y, 0, s->n - 1, 2.5, keep);
	size_t m = 0;
	for (size_t i = 0; i < s->n; i++)
		if (keep[i] && (!m || hypot(x[i] - x[m - 1], y[i] - y[m - 1]) > 0.5)) { x[m] = x[i]; y[m] = y[i]; m++; }
	free(keep);
	disc(g, x[0], y[0], r);
	if (m > 1) disc(g, x[m - 1], y[m - 1], r);
	for (size_t i = 0; i + 1 < m; i++) {
		double dx = x[i + 1] - x[i], dy = y[i + 1] - y[i], l = hypot(dx, dy);
		double nx = -dy / l * r, ny = dx / l * r;
		poly4(g, x[i] + nx, y[i] + ny, x[i + 1] + nx, y[i + 1] + ny, x[i + 1] - nx, y[i + 1] - ny, x[i] - nx, y[i] - ny);
		if (i + 2 < m) {
			/* the joint: a wedge on the outer side, a disc where it turns sharply */
			double ex = x[i + 2] - x[i + 1], ey = y[i + 2] - y[i + 1], el = hypot(ex, ey);
			double cr = (dx * ey - dy * ex) / (l * el), dt = (dx * ex + dy * ey) / (l * el);
			double turn = atan2(cr, dt);
			if (fabs(turn) > 0.35) disc(g, x[i + 1], y[i + 1], r);
			else if (fabs(turn) > 1e-3) {
				double mx = -ey / el * r, my = ex / el * r, sg = turn > 0 ? -1 : 1;
				tri(g, x[i + 1], y[i + 1], x[i + 1] + sg * nx, y[i + 1] + sg * ny, x[i + 1] + sg * mx, y[i + 1] + sg * my);
			}
		}
	}
	free(x); free(y);
}

/* ---------------------------------------------------------------- the styles (FR-034) */

const char *const hw_font_style_names[HW_STYLES] = { "Regular", "Calligraphy", "Brush" };

/* Calligraphy: a broad nib at one angle, its line between CAL_MIN and CAL_MAX times the ink
 * width (within the x-height bounds), the widest across the nib, the thinnest along it */
#define CAL_MIN 0.4
#define CAL_MAX 2.2
void hw_font_nib_widths(const struct hw_letters *L, double *wmin, double *wmax)
{
	double ink = L->m.ink;
	*wmin = fmin(fmax(CAL_MIN * ink, 0.015), 0.05);
	*wmax = fmin(fmax(CAL_MAX * ink, 0.12), 0.26);
}

/* Brush: w = ink * clamp(GAIN * fp * fv * fa, LO, HI) * taper (docs/recognizer.md):
 * fp = P^0.8 the pressure, fv = V^-0.3 the speed (V^-0.55 with no pressure), fa = 1 -
 * 0.12 tanh(dV/ds / 3) the acceleration (ds in x-heights), P and V relative to the person's
 * median (1 where the variant has no profile); the taper at the start over min(0.22, 0.3 L)
 * x-heights to 0.25, at the end over min(0.3, 0.4 L) times the speed there (1 to 2: a quick
 * flick tapers longer) to 0.12; a stroke shorter than BR_DOT keeps its body; never below
 * BR_FLOOR times the ink. */
#define BR_GAIN 1.3
#define BR_LO 0.35
#define BR_HI 2.4
#define BR_DOT 0.15
#define BR_FLOOR 0.12

static double prof_at(const double *p, double u)
{
	enum { K = HW_LETTERS_PROFILE };
	double f = u * (K - 1);
	if (f <= 0) return p[0];
	if (f >= K - 1) return p[K - 1];
	int i = (int)f;
	return p[i] + (f - i) * (p[i + 1] - p[i]);
}

static void brush_widths(const struct hw_letters *L, const struct hw_variant *v, size_t j, double *w)
{
	enum { K = HW_LETTERS_PROFILE };
	const struct hw_lstroke *s = &v->s[j];
	const double *P = v->pres ? v->pres + j * K : NULL, *V = v->speed ? v->speed + j * K : NULL;
	double ink = L->m.ink, len = 0, h = 1.0 / (K - 1);
	for (size_t i = 1; i < s->n; i++) len += hypot(s->x[i] - s->x[i - 1], s->y[i] - s->y[i - 1]);
	double vend = 1;
	if (V) { vend = 0; for (int q = 0; q < 4; q++) vend += V[K - 1 - q] / 4; }
	double t0 = fmin(0.22, 0.3 * len), t1 = fmin(0.3, 0.4 * len) * fmin(fmax(vend, 1), 2);
	double at = 0;
	for (size_t i = 0; i < s->n; i++) {
		if (i) at += hypot(s->x[i] - s->x[i - 1], s->y[i] - s->y[i - 1]);
		double u = len > 0 ? at / len : 0, f = BR_GAIN;
		if (P) f *= pow(fmin(fmax(prof_at(P, u), 0.25), 3), 0.8);
		if (V) {
			f *= pow(fmin(fmax(prof_at(V, u), 0.25), 4), P ? -0.3 : -0.55);
			double dv = len > 0 ? (prof_at(V, fmin(u + h, 1)) - prof_at(V, fmax(u - h, 0))) / ((fmin(u + h, 1) - fmax(u - h, 0)) * len) : 0;
			f *= 1 - 0.12 * tanh(dv / 3);
		}
		f = fmin(fmax(f, BR_LO), BR_HI);
		if (len >= BR_DOT) {
			f *= 0.25 + 0.75 * sqrt(fmin(1, at / t0));
			f *= 0.12 + 0.88 * pow(fmin(1, (len - at) / t1), 0.7);
		}
		w[i] = ink * fmax(f, BR_FLOOR);
	}
}

void hw_font_widths(const struct hw_letters *L, const struct hw_variant *v, size_t j, int style, double nib, double *w)
{
	const struct hw_lstroke *s = &v->s[j];
	if (style == HW_STYLE_BRUSH) { brush_widths(L, v, j, w); return; }
	if (style != HW_STYLE_CALLIGRAPHY) { for (size_t i = 0; i < s->n; i++) w[i] = L->m.ink; return; }
	double lo, hi, a = nib * M_PI / 180;
	hw_font_nib_widths(L, &lo, &hi);
	for (size_t i = 0; i < s->n; i++) {
		size_t i0 = i ? i - 1 : 0, i1 = i + 1 < s->n ? i + 1 : i;
		double dx = s->x[i1] - s->x[i0], dy = s->y[i1] - s->y[i0];
		w[i] = dx || dy ? lo + (hi - lo) * fabs(sin(atan2(dy, dx) - a)) : lo + (hi - lo) * fabs(sin(a));
	}
}

/* an arc of radius r around (cx, cy) from angle a0 to a1 (a1 >= a0, counter-clockwise):
 * its on-curve ends with quadratic pieces of at most 45 degrees between (one point when it
 * has no sweep) */
static void arc(struct glyph *g, double cx, double cy, double r, double a0, double a1)
{
	gpt(g, cx + r * cos(a0), cy + r * sin(a0), 1);
	int k = (int)ceil((a1 - a0) / (M_PI / 4) - 1e-9);
	if (k < 1) return;
	double st = (a1 - a0) / k, ro = r / cos(st / 2);
	for (int i = 0; i < k; i++) {
		double m = a0 + (i + 0.5) * st;
		gpt(g, cx + ro * cos(m), cy + ro * sin(m), 0);
		gpt(g, cx + r * cos(a0 + (i + 1) * st), cy + r * sin(a0 + (i + 1) * st), 1);
	}
}

/* the convex hull of up to 4 points, rounded by r (their discs' hull): one contour */
static void round_hull(struct glyph *g, double (*c)[2], int n, double r)
{
	/* the hull, counter-clockwise (monotone chain), points closer than half a unit as one */
	for (int a = 1; a < n; a++)
		for (int b = a; b > 0 && (c[b][0] < c[b - 1][0] || (c[b][0] == c[b - 1][0] && c[b][1] < c[b - 1][1])); b--) {
			double t0 = c[b][0], t1 = c[b][1]; c[b][0] = c[b - 1][0]; c[b][1] = c[b - 1][1]; c[b - 1][0] = t0; c[b - 1][1] = t1;
		}
	double h[10][2];
	int m = 0;
	for (int pass = 0; pass < 2; pass++) {
		int base = m;
		for (int q = 0; q < n; q++) {
			const double *p = pass ? c[n - 1 - q] : c[q];
			while (m - base >= 2) {
				double *o = h[m - 2], *a = h[m - 1];
				if ((a[0] - o[0]) * (p[1] - o[1]) - (a[1] - o[1]) * (p[0] - o[0]) > 1e-9) break;
				m--;
			}
			h[m][0] = p[0]; h[m][1] = p[1]; m++;
		}
		m--;    /* its last point is the other half's first */
	}
	int k = 0;
	for (int i = 0; i < m; i++) if (!k || hypot(h[i][0] - h[k - 1][0], h[i][1] - h[k - 1][1]) > 0.5) { h[k][0] = h[i][0]; h[k][1] = h[i][1]; k++; }
	if (k > 1 && hypot(h[0][0] - h[k - 1][0], h[0][1] - h[k - 1][1]) <= 0.5) k--;
	if (k < 2) { disc(g, c[0][0], c[0][1], r); return; }
	int s = g->np;
	for (int i = 0; i < k; i++) {
		const double *p = h[(i + k - 1) % k], *q = h[i], *nx = h[(i + 1) % k];
		/* outward normals of the edge into q and out of it (a counter-clockwise hull: to the right) */
		double ai = atan2(-(q[0] - p[0]), q[1] - p[1]), ao = atan2(-(nx[0] - q[0]), nx[1] - q[1]);
		while (ao < ai - 1e-9) ao += 2 * M_PI;
		arc(g, q[0], q[1], r, ai, ao);
	}
	gclose(g, s);
}

/* the hull of two discs (a, ra) and (b, rb): one contour */
static void disc_pair(struct glyph *g, double ax, double ay, double ra, double bx, double by, double rb)
{
	double d = hypot(bx - ax, by - ay);
	if (d <= fabs(ra - rb) + 0.5) { if (ra >= rb) disc(g, ax, ay, ra); else disc(g, bx, by, rb); return; }
	double f = atan2(by - ay, bx - ax), be = acos((ra - rb) / d);
	int s = g->np;
	/* a's far side from f + be round to f - be + 2 pi, then b's near side from f - be to f + be */
	arc(g, ax, ay, ra, f + be, f - be + 2 * M_PI);
	arc(g, bx, by, rb, f - be, f + be);
	gclose(g, s);
}

/* Douglas-Peucker on font units with the radius as a third coordinate */
static void simplify3(const double *x, const double *y, const double *r, size_t a, size_t b, double tol, char *keep)
{
	if (b <= a + 1) return;
	double dx = x[b] - x[a], dy = y[b] - y[a], l = hypot(dx, dy), best = -1, la = 0, lt = 0;
	size_t bi = a;
	for (size_t i = a + 1; i <= b; i++) lt += hypot(x[i] - x[i - 1], y[i] - y[i - 1]);
	for (size_t i = a + 1; i < b; i++) {
		la += hypot(x[i] - x[i - 1], y[i] - y[i - 1]);
		double d = l > 0 ? fabs(dy * (x[i] - x[a]) - dx * (y[i] - y[a])) / l : hypot(x[i] - x[a], y[i] - y[a]);
		double dr = fabs(r[i] - (r[a] + (lt > 0 ? la / lt : 0) * (r[b] - r[a])));
		if (dr > d) d = dr;
		if (d > best) { best = d; bi = i; }
	}
	if (best > tol) { keep[bi] = 1; simplify3(x, y, r, a, bi, tol, keep); simplify3(x, y, r, bi, b, tol, keep); }
}

/* a stroke in Calligraphy or Brush: its points in font units, the radius per point (Brush)
 * or the thinnest line's (Calligraphy, with the nib's half span hx, hy) */
static void outline_styled(struct glyph *g, const struct hw_letters *L, const struct hw_variant *v, size_t j, double ox,
			   int style, double nib)
{
	const struct hw_lstroke *s = &v->s[j];
	if (!s->n) return;
	double *x = malloc(s->n * sizeof *x), *y = malloc(s->n * sizeof *y), *r = malloc(s->n * sizeof *r);
	char *keep = calloc(s->n, 1);
	if (!x || !y || !r || !keep) { free(x); free(y); free(r); free(keep); return; }
	double lo = 0, hi = 0, a = nib * M_PI / 180;
	if (style == HW_STYLE_CALLIGRAPHY) {
		hw_font_nib_widths(L, &lo, &hi);
		for (size_t i = 0; i < s->n; i++) r[i] = 0;
	} else {
		hw_font_widths(L, v, j, style, nib, r);
		for (size_t i = 0; i < s->n; i++) r[i] = fmax(r[i] * XH / 2, 3);
	}
	for (size_t i = 0; i < s->n; i++) { x[i] = ox + s->x[i] * XH; y[i] = s->y[i] * XH; }
	keep[0] = keep[s->n - 1] = 1;
	simplify3(x, y, r, 0, s->n - 1, 2.5, keep);
	size_t m = 0;
	for (size_t i = 0; i < s->n; i++)
		if (keep[i] && (!m || hypot(x[i] - x[m - 1], y[i] - y[m - 1]) > 0.5)) { x[m] = x[i]; y[m] = y[i]; r[m] = r[i]; m++; }
	free(keep);
	if (style == HW_STYLE_CALLIGRAPHY) {
		/* the nib from its centre: half its span along the nib's angle, rounded by the thinnest line */
		double hx = (hi - lo) / 2 * XH * cos(a), hy = (hi - lo) / 2 * XH * sin(a), rr = fmax(lo * XH / 2, 3);
		for (size_t i = 0; i < (m > 1 ? m - 1 : 1); i++) {
			size_t k = m > 1 ? i + 1 : i;
			double c[4][2] = { { x[i] + hx, y[i] + hy }, { x[i] - hx, y[i] - hy }, { x[k] + hx, y[k] + hy }, { x[k] - hx, y[k] - hy } };
			round_hull(g, c, 4, rr);
		}
	} else {
		if (m == 1) disc(g, x[0], y[0], r[0]);
		for (size_t i = 0; i + 1 < m; i++) disc_pair(g, x[i], y[i], r[i], x[i + 1], y[i + 1], r[i + 1]);
	}
	free(x); free(y); free(r);
}

static void glyph_of(struct glyph *g, const struct hw_letters *L, const struct hw_variant *v, double gap, double ink,
		     int style, double nib)
{
	memset(g, 0, sizeof *g);
	double r = ink * XH / 2;
	if (r < 6) r = 6;
	/* the side bearings: half the person's typical gap each side, between the pen's centre
	 * lines as the spacing measures it (GPOS kerns each pair to where the spacing places it) */
	double ox = gap / 2 * XH;
	for (size_t j = 0; j < v->nstrokes; j++)
		if (style == HW_STYLE_REGULAR) outline_stroke(g, &v->s[j], ox, r);
		else outline_styled(g, L, v, j, ox, style, nib);
	g->adv = (int)lround((v->width + gap) * XH);
}

static void glyph_box(struct glyph *g)
{
	g->xmin = g->ymin = 0x7fff; g->xmax = g->ymax = -0x7fff;
	for (int i = 0; i < g->np; i++) {
		if (g->p[i].x < g->xmin) g->xmin = g->p[i].x;
		if (g->p[i].x > g->xmax) g->xmax = g->p[i].x;
		if (g->p[i].y < g->ymin) g->ymin = g->p[i].y;
		if (g->p[i].y > g->ymax) g->ymax = g->p[i].y;
	}
	if (!g->np) g->xmin = g->ymin = g->xmax = g->ymax = 0;
}

static void glyf_data(struct bb *b, const struct glyph *g)
{
	if (!g->nc) return;             /* an empty glyph has no data */
	s16(b, g->nc);
	s16(b, g->xmin); s16(b, g->ymin); s16(b, g->xmax); s16(b, g->ymax);
	for (int i = 0; i < g->nc; i++) u16(b, (unsigned)g->ends[i]);
	u16(b, 0);                      /* no instructions */
	int px = 0, py = 0;
	for (int i = 0; i < g->np; i++) {
		int dx = g->p[i].x - px, dy = g->p[i].y - py;
		unsigned f = g->p[i].on ? 1 : 0;
		if (dx == 0) f |= 0x10; else if (abs(dx) < 256) f |= 0x02 | (dx > 0 ? 0x10 : 0);
		if (dy == 0) f |= 0x20; else if (abs(dy) < 256) f |= 0x04 | (dy > 0 ? 0x20 : 0);
		u8(b, f);
		px = g->p[i].x; py = g->p[i].y;
	}
	px = 0;
	for (int i = 0; i < g->np; i++) {
		int dx = g->p[i].x - px;
		if (dx && abs(dx) < 256) u8(b, (unsigned)abs(dx)); else if (dx) s16(b, dx);
		px = g->p[i].x;
	}
	py = 0;
	for (int i = 0; i < g->np; i++) {
		int dy = g->p[i].y - py;
		if (dy && abs(dy) < 256) u8(b, (unsigned)abs(dy)); else if (dy) s16(b, dy);
		py = g->p[i].y;
	}
}

/* ---------------------------------------------------------------- tables */

static void coverage(struct bb *b, const int *gl, int n)
{
	u16(b, 1); u16(b, (unsigned)n);
	for (int i = 0; i < n; i++) u16(b, (unsigned)gl[i]);
}

static int gl_at(int i, int s, void *ctx);

/* What GSUB and GPOS are made of: the letters (ls, their variants' glyphs from outl[i]), the
 * joined pairs (letters ja[k] and jb[k], glyph lig0 + k) and whether random alternates are in. */
struct gctx {
	int nl;
	const struct hw_letter *const *ls;
	const int *outl;
	int nj, lig0;
	const int *ja, *jb;
	int random;
};

/* a coverage of a sorted glyph list, into its own buffer at its end */
struct gset { int *g; int n, cap; };
static void gadd(struct gset *s, int g)
{
	if (s->n == s->cap) {
		s->cap = s->cap ? s->cap * 2 : 64;
		int *x = realloc(s->g, (size_t)s->cap * sizeof *x);
		if (!x) return;
		s->g = x;
	}
	s->g[s->n++] = g;
}

/* letter i's glyphs: its steps; with all, its variants and the joined pairs it ends (side 0)
 * or starts (side 1), ascending */
static void letter_glyphs(struct gset *s, const struct gctx *c, int i, int all, int side)
{
	for (int st = 0; st < HW_LETTERS_SLOTS; st++) gadd(s, gl_at(i + 1, st, NULL));
	if (!all) return;
	for (int v = 0; v < c->ls[i]->nvar; v++) gadd(s, c->outl[i] + v);
	for (int k = 0; k < c->nj; k++) if ((side ? c->ja[k] : c->jb[k]) == i) gadd(s, c->lig0 + k);
}

/* the extension lookups (type 7): each a list of subtables of one type, written after the
 * lookup list with 32-bit offsets, so no 16-bit offset outgrows its table */
struct xlook { int type, nsub; struct bb *sub; };
static struct bb *xsub(struct xlook *x)
{
	struct bb *n = realloc(x->sub, (size_t)(x->nsub + 1) * sizeof *n);
	if (!n) return NULL;
	x->sub = n;
	memset(&n[x->nsub], 0, sizeof *n);
	return &n[x->nsub++];
}

static int letter_of(const struct gctx *c, unsigned cp)
{
	for (int i = 0; i < c->nl; i++) if (c->ls[i]->cp == cp) return i;
	return -1;
}

/* GSUB: the font steps through a cycle of HW_LETTERS_SLOTS along the text (letters.h,
 * hw_letters_step), every glyph carrying its step: one chain-context lookup in 'calt' with a
 * rule per step (after a glyph at step s, the next character's glyph at step s + 1), and a
 * single substitution per step it calls. gl(i, s): the glyph of character i (0 the space) at
 * step s, ascending in i for each s. Then, in this order of lookups, so each sees the steps
 * already set: the joined pairs as ligatures ('liga', FR-039: any step of the first and the
 * second), the neighbour rules (a second 'calt' lookup, FR-038: a letter at any step after or
 * before its neighbour's glyphs, the joined pairs that end or start with it among them,
 * turned into the rule's variant; the first rule that applies wins) and, when asked for, the
 * random alternates ('rand', FR-040: each letter's step glyphs to the variants of its cycle's
 * steps, so each is picked as often as the cycle shows it). */
static void gsub(struct bb *b, int nch, int (*gl)(int, int, void *), void *ctx, const struct gctx *c)
{
	enum { S = HW_LETTERS_SLOTS };
	int *cls = malloc((size_t)(nch + 1) * sizeof(int)), *sub = malloc((size_t)(nch + 1) * sizeof(int));
	if (!cls || !sub) { b->bad = 1; free(cls); free(sub); return; }
	/* the rules that can apply (their neighbour in the font), and a substitution per letter
	 * and variant they turn to */
	int (*rsub)[2] = malloc(((size_t)c->nl * HW_LETTERS_VARIANTS + 1) * sizeof *rsub), nrs = 0, nrules = 0;
	struct xlook *X = calloc((size_t)c->nl * HW_LETTERS_VARIANTS + 4, sizeof *X);
	size_t *at_l = malloc(((size_t)c->nl * HW_LETTERS_VARIANTS + 4 + S) * sizeof *at_l);
	if (!rsub || !X || !at_l) { b->bad = 1; free(rsub); free(X); free(at_l); free(cls); free(sub); return; }
	for (int i = 0; i < c->nl; i++)
		for (int r = 0; r < c->ls[i]->nrules; r++) {
			const struct hw_rule *ru = &c->ls[i]->rule[r];
			if (letter_of(c, ru->nb) < 0 || ru->var >= c->ls[i]->nvar) continue;
			nrules++;
			int k = 0;
			while (k < nrs && !(rsub[k][0] == i && rsub[k][1] == ru->var)) k++;
			if (k == nrs) { rsub[nrs][0] = i; rsub[nrs][1] = ru->var; nrs++; }
		}
	/* the extension lookups: their indices after the cycle's S */
	int nx = 0, ilig = -1, irule = -1, irand = -1;
	if (c->nj) {
		ilig = S + nx;
		X[nx++].type = 4;
	}
	if (nrules) {
		irule = S + nx;
		X[nx++].type = 6;
		for (int k = 0; k < nrs; k++) X[nx++].type = 1;
	}
	if (c->random) {
		irand = S + nx;
		X[nx++].type = 3;
	}
	/* the joined pairs: LigatureSubst format 1, its first glyphs (the steps of the letters
	 * that start one) in subtables of at most about 48 KiB */
	if (ilig >= 0) {
		struct xlook *x = &X[ilig - S];
		for (int i0 = 0; i0 < c->nl;) {
			int i1 = i0;
			long size = 6;
			while (i1 < c->nl) {
				int nj = 0;
				for (int k = 0; k < c->nj; k++) nj += c->ja[k] == i1;
				long add = (long)S * (6 + (long)nj * S * 8);
				if (i1 > i0 && size + add > 48000) break;
				size += add;
				i1++;
			}
			int any = 0;
			for (int k = 0; k < c->nj; k++) any |= c->ja[k] >= i0 && c->ja[k] < i1;
			if (any) {
				struct bb *t = xsub(x);
				if (!t) { b->bad = 1; break; }
				struct gset first = { 0 };
				for (int i = i0; i < i1; i++) {
					int nj = 0;
					for (int k = 0; k < c->nj; k++) nj += c->ja[k] == i;
					if (nj) letter_glyphs(&first, c, i, 0, 0);
				}
				u16(t, 1);
				size_t at_c = t->n; u16(t, 0);
				u16(t, (unsigned)first.n);
				size_t at_s = t->n;
				for (int q = 0; q < first.n; q++) u16(t, 0);
				for (int q = 0; q < first.n; q++) {
					int i = (first.g[q] - 1) / S - 1;
					put16(t, at_s + 2 * (size_t)q, (unsigned)t->n);
					size_t set = t->n;
					int nl2 = 0;
					for (int k = 0; k < c->nj; k++) nl2 += c->ja[k] == i ? S : 0;
					u16(t, (unsigned)nl2);
					size_t at_l = t->n;
					for (int k = 0; k < nl2; k++) u16(t, 0);
					int w = 0;
					for (int k = 0; k < c->nj; k++) {
						if (c->ja[k] != i) continue;
						for (int st = 0; st < S; st++) {
							put16(t, at_l + 2 * (size_t)w++, (unsigned)(t->n - set));
							u16(t, (unsigned)(c->lig0 + k)); u16(t, 2); u16(t, (unsigned)gl_at(c->jb[k] + 1, st, NULL));
						}
					}
				}
				put16(t, at_c, (unsigned)t->n);
				coverage(t, first.g, first.n);
				free(first.g);
				if (t->n > 0xffff) b->bad = 1;
			}
			i0 = i1;
		}
	}
	/* the rules: ChainContextSubst format 3, a subtable each */
	if (irule >= 0) {
		struct xlook *x = &X[irule - S];
		for (int i = 0; i < c->nl; i++)
			for (int r = 0; r < c->ls[i]->nrules; r++) {
				const struct hw_rule *ru = &c->ls[i]->rule[r];
				int nb = letter_of(c, ru->nb), k = 0;
				if (nb < 0 || ru->var >= c->ls[i]->nvar) continue;
				while (k < nrs && !(rsub[k][0] == i && rsub[k][1] == ru->var)) k++;
				if (k == nrs) continue;
				struct bb *t = xsub(x);
				if (!t) { b->bad = 1; break; }
				struct gset in = { 0 }, ng = { 0 };
				letter_glyphs(&in, c, i, 0, 0);
				letter_glyphs(&ng, c, nb, 1, ru->side);
				u16(t, 3);
				u16(t, ru->side ? 0 : 1);
				size_t at_bc = t->n;
				if (!ru->side) u16(t, 0);
				u16(t, 1); size_t at_ic = t->n; u16(t, 0);
				u16(t, ru->side ? 1 : 0);
				size_t at_lc = t->n;
				if (ru->side) u16(t, 0);
				u16(t, 1); u16(t, 0); u16(t, (unsigned)(irule + 1 + k));
				put16(t, ru->side ? at_lc : at_bc, (unsigned)t->n);
				coverage(t, ng.g, ng.n);
				put16(t, at_ic, (unsigned)t->n);
				coverage(t, in.g, in.n);
				free(in.g); free(ng.g);
			}
		/* the substitutions: a letter's steps to one of its variants */
		for (int k = 0; k < nrs; k++) {
			struct bb *t = xsub(&X[irule - S + 1 + k]);
			if (!t) { b->bad = 1; break; }
			struct gset in = { 0 };
			letter_glyphs(&in, c, rsub[k][0], 0, 0);
			u16(t, 2); size_t at_c = t->n; u16(t, 0); u16(t, (unsigned)in.n);
			for (int q = 0; q < in.n; q++) u16(t, (unsigned)(c->outl[rsub[k][0]] + rsub[k][1]));
			put16(t, at_c, (unsigned)t->n);
			coverage(t, in.g, in.n);
			free(in.g);
		}
	}
	/* random alternates: AlternateSubst format 1, one set per letter (its cycle's variants),
	 * shared by its steps */
	if (irand >= 0) {
		struct bb *t = xsub(&X[irand - S]);
		if (!t) b->bad = 1;
		struct gset cov = { 0 };
		for (int i = 0; t && i < c->nl; i++) if (c->ls[i]->nvar > 1) letter_glyphs(&cov, c, i, 0, 0);
		if (t) {
			u16(t, 1); size_t at_c = t->n; u16(t, 0); u16(t, (unsigned)cov.n);
			size_t at_s = t->n;
			for (int q = 0; q < cov.n; q++) u16(t, 0);
			for (int q = 0; q < cov.n; q += S) {
				int i = (cov.g[q] - 1) / S - 1;
				for (int st = 0; st < S; st++) put16(t, at_s + 2 * (size_t)(q + st), (unsigned)t->n);
				u16(t, S);
				for (int st = 0; st < S; st++) u16(t, (unsigned)(c->outl[i] + c->ls[i]->slot[st]));
			}
			put16(t, at_c, (unsigned)t->n);
			coverage(t, cov.g, cov.n);
		}
		free(cov.g);
	}

	size_t g0 = b->n;
	u16(b, 1); u16(b, 0);           /* version 1.0 */
	size_t at_sl = b->n; u16(b, 0);
	size_t at_fl = b->n; u16(b, 0);
	size_t at_ll = b->n; u16(b, 0);
	/* the features, by tag: calt (the cycle, then the rules), liga, rand */
	int nf = 1 + (ilig >= 0) + (irand >= 0);
	/* ScriptList: DFLT and latn, each a default LangSys with every feature */
	put16(b, at_sl, (unsigned)(b->n - g0));
	size_t sl = b->n;
	u16(b, 2);
	const char *tags[2] = { "DFLT", "latn" };
	size_t at_s[2];
	for (int i = 0; i < 2; i++) { for (int k = 0; k < 4; k++) u8(b, (unsigned char)tags[i][k]); at_s[i] = b->n; u16(b, 0); }
	for (int i = 0; i < 2; i++) {
		put16(b, at_s[i], (unsigned)(b->n - sl));
		u16(b, 4); u16(b, 0);   /* the default LangSys right after, no others */
		u16(b, 0); u16(b, 0xffff); u16(b, (unsigned)nf);
		for (int k = 0; k < nf; k++) u16(b, (unsigned)k);
	}
	put16(b, at_fl, (unsigned)(b->n - g0));
	size_t fl = b->n;
	u16(b, (unsigned)nf);
	size_t at_f[3];
	const char *ftag[3] = { "calt", "liga", "rand" };
	int fon[3] = { 1, ilig >= 0, irand >= 0 };
	for (int k = 0; k < 3; k++) {
		if (!fon[k]) continue;
		for (int q = 0; q < 4; q++) u8(b, (unsigned char)ftag[k][q]);
		at_f[k] = b->n; u16(b, 0);
	}
	for (int k = 0; k < 3; k++) {
		if (!fon[k]) continue;
		put16(b, at_f[k], (unsigned)(b->n - fl));
		u16(b, 0);
		if (k == 0) { u16(b, irule >= 0 ? 2 : 1); u16(b, 0); if (irule >= 0) u16(b, (unsigned)irule); }
		else { u16(b, 1); u16(b, (unsigned)(k == 1 ? ilig : irand)); }
	}
	/* LookupList: 0 the chain, k = 1 .. S-1 the substitution to step k (step 0 is the
	 * glyph the character maps to), then the extension lookups */
	put16(b, at_ll, (unsigned)(b->n - g0));
	size_t ll = b->n;
	u16(b, (unsigned)(S + nx));
	for (int i = 0; i < S + nx; i++) { at_l[i] = b->n; u16(b, 0); }
	/* lookup 0: chain context, format 3, a subtable per step s = 0 .. S-2 */
	put16(b, at_l[0], (unsigned)(b->n - ll));
	size_t lk = b->n;
	u16(b, 6); u16(b, 0); u16(b, S - 1);
	size_t at_st[S];
	for (int i = 0; i < S - 1; i++) { at_st[i] = b->n; u16(b, 0); }
	for (int i = 0; i < nch; i++) sub[i] = gl(i, 0, ctx);
	for (int st0 = 0; st0 < S - 1; st0++) {
		put16(b, at_st[st0], (unsigned)(b->n - lk));
		size_t st = b->n;
		u16(b, 3);
		u16(b, 1); size_t at_bc = b->n; u16(b, 0);
		u16(b, 1); size_t at_ic = b->n; u16(b, 0);
		u16(b, 0);
		u16(b, 1); u16(b, 0); u16(b, (unsigned)(st0 + 1));
		for (int i = 0; i < nch; i++) cls[i] = gl(i, st0, ctx);
		put16(b, at_bc, (unsigned)(b->n - st));
		coverage(b, cls, nch);
		put16(b, at_ic, (unsigned)(b->n - st));
		coverage(b, sub, nch);
	}
	/* lookups 1 .. S-1: single substitution, format 2 (step 0 -> step k) */
	for (int k = 1; k < S; k++) {
		put16(b, at_l[k], (unsigned)(b->n - ll));
		u16(b, 1); u16(b, 0); u16(b, 1); u16(b, 8);
		size_t st = b->n;
		u16(b, 2); size_t at_c = b->n; u16(b, 0); u16(b, (unsigned)nch);
		for (int i = 0; i < nch; i++) u16(b, (unsigned)gl(i, k, ctx));
		put16(b, at_c, (unsigned)(b->n - st));
		coverage(b, sub, nch);
	}
	/* the extension lookups' tables and records, then their subtables */
	size_t nrec = 0;
	for (int k = 0; k < nx; k++) nrec += (size_t)X[k].nsub;
	size_t *rec = calloc(nrec + 1, sizeof *rec);
	nrec = 0;
	if (!rec) b->bad = 1;
	for (int k = 0; rec && k < nx; k++) {
		put16(b, at_l[S + k], (unsigned)(b->n - ll));
		size_t lt = b->n;
		u16(b, 7); u16(b, 0); u16(b, (unsigned)X[k].nsub);
		size_t at_x = b->n;
		for (int q = 0; q < X[k].nsub; q++) u16(b, 0);
		for (int q = 0; q < X[k].nsub; q++) {
			put16(b, at_x + 2 * (size_t)q, (unsigned)(b->n - lt));
			rec[nrec++] = b->n;
			u16(b, 1); u16(b, (unsigned)X[k].type); u32(b, 0);
		}
	}
	if (b->n - g0 > 0xffff) b->bad = 1;
	nrec = 0;
	for (int k = 0; rec && k < nx; k++)
		for (int q = 0; q < X[k].nsub; q++) {
			pad4(b);
			put32(b, rec[nrec] + 4, (uint32_t)(b->n - rec[nrec]));
			nrec++;
			bgrow(b, X[k].sub[q].n);
			if (!b->bad && X[k].sub[q].n) { memcpy(b->d + b->n, X[k].sub[q].d, X[k].sub[q].n); b->n += X[k].sub[q].n; }
			if (X[k].sub[q].bad) b->bad = 1;
		}
	free(rec);
	for (int k = 0; k < nx; k++) { for (int q = 0; q < X[k].nsub; q++) free(X[k].sub[q].d); free(X[k].sub); }
	free(X); free(rsub); free(at_l);
	free(cls); free(sub);
}

static void name_rec(struct bb *recs, struct bb *str, unsigned id, const char *utf8)
{
	unsigned cp[256];
	size_t n = hw_utf8_decode(utf8, cp, 256);
	if (n > 256) n = 256;
	size_t off = str->n;
	for (size_t i = 0; i < n; i++) {
		unsigned c = cp[i];
		if (c >= 0x10000) { c -= 0x10000; u16(str, 0xd800 + (c >> 10)); u16(str, 0xdc00 + (c & 0x3ff)); }
		else u16(str, c);
	}
	u16(recs, 3); u16(recs, 1); u16(recs, 0x409); u16(recs, id);
	u16(recs, (unsigned)(str->n - off)); u16(recs, (unsigned)off);
}

static uint32_t tsum(const unsigned char *d, size_t n)
{
	uint32_t s = 0;
	for (size_t i = 0; i < n; i += 4) {
		uint32_t w = 0;
		for (int k = 0; k < 4; k++) w = w << 8 | (i + (size_t)k < n ? d[i + (size_t)k] : 0);
		s += w;
	}
	return s;
}

/* GPOS: pair kerning (FR-009), one class per variant and joined pair, so every pair comes out
 * where the spacing places it (spacing.c): a letter by its profiles and its middle, a joined
 * pair (FR-039) by its profiles at the gap its letters set (its first letter's on the left,
 * its last one's on the right; its parts have no middles of their own). PairPos format 2
 * subtables of as many first classes as fit 16-bit offsets, behind Extension lookups (32-bit).
 * The classes: cls[g - g1] for the glyphs from g1, the letters' step glyphs their variant's,
 * the variants' and the joined pairs' glyphs their own; -1 none. */
struct kent { unsigned cl, cr; const struct hw_profile *prof; double ml, mr, width; int lc, rc; };
static void gpos(struct bb *b, const struct hw_letters *L, const struct kent *E, int nv, const int *cls, int g1, int ng)
{
	short *kern = malloc((size_t)(nv + 1) * (size_t)(nv + 1) * sizeof *kern);
	int *cov = malloc((size_t)(ng + 1) * sizeof(int));
	if (!kern || !cov) { b->bad = 1; free(kern); free(cov); return; }
	double letter = L->sp.letter;
	for (int a = 0; a < nv; a++)
		for (int c = 0; c < nv; c++) {
			const struct kent *ea = &E[a], *ec = &E[c];
			double at = hw_spacing_place(&L->sp, ea->cr, ea->prof, ea->mr, ea->rc, ec->cl, ec->prof, ec->ml, ec->lc);
			long k = lround((at - (ea->width + letter)) * XH);
			kern[a * nv + c] = (short)(k < -3 * XH ? -3 * XH : k > 3 * XH ? 3 * XH : k);
		}
	/* the first classes per subtable: what keeps its classes and coverage in 16-bit reach */
	int cdsz = 6 + 2 * ng, per = (60000 - 16 - 3 * cdsz) / (2 * (nv + 1)) - 1;
	if (per < 1) per = 1;
	int nsub = (nv + per - 1) / per;
	if (nsub < 1) nsub = 1;
	size_t g0 = b->n;
	u16(b, 1); u16(b, 0);
	size_t at_sl = b->n; u16(b, 0);
	size_t at_fl = b->n; u16(b, 0);
	size_t at_ll = b->n; u16(b, 0);
	put16(b, at_sl, (unsigned)(b->n - g0));
	size_t sl = b->n;
	u16(b, 2);
	const char *tags[2] = { "DFLT", "latn" };
	size_t at_s[2];
	for (int i = 0; i < 2; i++) { for (int c = 0; c < 4; c++) u8(b, (unsigned char)tags[i][c]); at_s[i] = b->n; u16(b, 0); }
	for (int i = 0; i < 2; i++) {
		put16(b, at_s[i], (unsigned)(b->n - sl));
		u16(b, 4); u16(b, 0);
		u16(b, 0); u16(b, 0xffff); u16(b, 1); u16(b, 0);
	}
	put16(b, at_fl, (unsigned)(b->n - g0));
	u16(b, 1);
	u8(b, 'k'); u8(b, 'e'); u8(b, 'r'); u8(b, 'n'); u16(b, 8);
	u16(b, 0); u16(b, 1); u16(b, 0);
	put16(b, at_ll, (unsigned)(b->n - g0));
	u16(b, 1); u16(b, 4);
	/* the lookup: Extension (type 9), its subtables the extension records right after */
	size_t lk = b->n;
	u16(b, 9); u16(b, 0); u16(b, (unsigned)nsub);
	size_t at_x = b->n;
	for (int i = 0; i < nsub; i++) u16(b, 0);
	size_t ext[64];
	for (int i = 0; i < nsub && i < 64; i++) {
		put16(b, at_x + 2 * (size_t)i, (unsigned)(b->n - lk));
		ext[i] = b->n;
		u16(b, 1); u16(b, 2); u32(b, 0);
	}
	for (int si = 0; si < nsub && si < 64; si++) {
		int c0 = si * per, c1 = c0 + per < nv ? c0 + per : nv;
		put32(b, ext[si] + 4, (uint32_t)(b->n - ext[si]));
		size_t st = b->n;
		u16(b, 2);
		size_t at_cov = b->n; u16(b, 0);
		u16(b, 0x0004); u16(b, 0);
		size_t at_cd1 = b->n; u16(b, 0);
		size_t at_cd2 = b->n; u16(b, 0);
		u16(b, (unsigned)(c1 - c0 + 1)); u16(b, (unsigned)(nv + 1));
		for (int x = 0; x < (nv + 1); x++) u16(b, 0);           /* class 0: none */
		for (int a = c0; a < c1; a++) {
			u16(b, 0);
			for (int c = 0; c < nv; c++) s16(b, kern[a * nv + c]);
		}
		/* coverage: the glyphs of this subtable's classes, ascending */
		int nc = 0;
		for (int g = 0; g < ng; g++) if (cls[g] >= c0 && cls[g] < c1) cov[nc++] = g1 + g;
		put16(b, at_cov, (unsigned)(b->n - st));
		coverage(b, cov, nc);
		/* class defs, format 1 over the glyphs from g1 */
		for (int k = 0; k < 2; k++) {
			put16(b, k ? at_cd2 : at_cd1, (unsigned)(b->n - st));
			u16(b, 1); u16(b, (unsigned)g1); u16(b, (unsigned)ng);
			for (int g = 0; g < ng; g++) {
				int vid = cls[g];
				if (k) u16(b, vid >= 0 ? (unsigned)(vid + 1) : 0);
				else u16(b, vid >= c0 && vid < c1 ? (unsigned)(vid - c0 + 1) : 0);
			}
		}
		if (b->n - st > 0xffff) b->bad = 1;
	}
	free(kern); free(cov);
}

/* the glyph ids: .notdef, the space at each step, each covered character (code point
 * order) at each step, then each character's variants (its outlines; the glyph at a step
 * is a reference to the variant the letter shows there), then the joined pairs (FR-039) */
static int gl_at(int i, int s, void *ctx)
{
	(void)ctx;
	return 1 + i * HW_LETTERS_SLOTS + s;
}

static unsigned char *font_build(const struct hw_letters *L, const char *name, unsigned version, int style, double nib,
				 int random, size_t *len);

/* the font of the letters as the person marked them: variants left out are not in it */
unsigned char *hw_font_build_style(const struct hw_letters *L, const char *name, unsigned version, int style,
				   double nib, int random, size_t *len)
{
	struct hw_letters V;
	if (style < 0 || style >= HW_STYLES || hw_letters_view(L, &V)) return NULL;
	unsigned char *r = font_build(&V, name, version, style, nib, random, len);
	hw_letters_view_free(&V);
	return r;
}

unsigned char *hw_font_build(const struct hw_letters *L, const char *name, unsigned version, size_t *len)
{
	return hw_font_build_style(L, name, version, HW_STYLE_REGULAR, HW_FONT_NIB_DEFAULT, 0, len);
}

static unsigned char *font_build(const struct hw_letters *L, const char *name, unsigned version, int style, double nib,
				 int random, size_t *len)
{
	enum { S = HW_LETTERS_SLOTS };
	int nl = 0;
	for (size_t i = 0; i < L->n; i++) if (L->l[i].covered && L->l[i].nvar) nl++;
	const struct hw_letter **ls = calloc((size_t)nl + 1, sizeof *ls);
	int *base = calloc((size_t)nl + 1, sizeof(int)), *outl = calloc((size_t)nl + 1, sizeof(int));
	if (!ls || !base || !outl) { free(ls); free(base); free(outl); return NULL; }
	int k = 0;
	for (size_t i = 0; i < L->n; i++) if (L->l[i].covered && L->l[i].nvar && L->l[i].cp != ' ') ls[k++] = &L->l[i];
	nl = k;
	int ngl = 1 + (nl + 1) * S;
	for (int i = 0; i < nl; i++) { base[i] = gl_at(i + 1, 0, NULL); outl[i] = ngl; ngl += ls[i]->nvar; }
	/* the joined pairs of two letters in the font (FR-039) */
	int nj = 0, lig0 = ngl;
	int *ja = calloc(L->njoined + 1, sizeof(int)), *jb = calloc(L->njoined + 1, sizeof(int));
	const struct hw_joined **js = calloc(L->njoined + 1, sizeof *js);
	if (!ja || !jb || !js) { free(ja); free(jb); free(js); free(ls); free(base); free(outl); return NULL; }
	for (size_t q = 0; q < L->njoined; q++) {
		int a = -1, b2 = -1;
		for (int i = 0; i < nl; i++) { if (ls[i]->cp == L->joined[q].a) a = i; if (ls[i]->cp == L->joined[q].b) b2 = i; }
		if (a < 0 || b2 < 0 || !L->joined[q].v.nstrokes) continue;
		ja[nj] = a; jb[nj] = b2; js[nj] = &L->joined[q]; nj++;
	}
	ngl += nj;
	struct glyph *G = calloc((size_t)ngl, sizeof *G);
	int *ref = malloc((size_t)ngl * sizeof *ref);       /* the glyph a step glyph refers to, -1 none */
	if (!G || !ref) { free(G); free(ref); free(ls); free(base); free(outl); free(ja); free(jb); free(js); return NULL; }
	for (int i = 0; i < ngl; i++) ref[i] = -1;
	double gap = L->sp.letter, ink = L->m.ink;
	G[0].adv = XH;
	/* the space: the word gap less the two side bearings beside it */
	int sp = (int)lround((L->sp.word - gap) * XH);
	if (sp < XH / 10) sp = XH / 10;
	for (int s2 = 0; s2 < S; s2++) G[gl_at(0, s2, NULL)].adv = sp;
	for (int i = 0; i < nl; i++) {
		for (int v = 0; v < ls[i]->nvar; v++) glyph_of(&G[outl[i] + v], L, &ls[i]->var[v], gap, ink, style, nib);
		for (int s2 = 0; s2 < S; s2++) ref[gl_at(i + 1, s2, NULL)] = outl[i] + (ls[i]->slot[s2] < ls[i]->nvar ? ls[i]->slot[s2] : 0);
	}
	for (int k = 0; k < nj; k++) glyph_of(&G[lig0 + k], L, &js[k]->v, gap, ink, style, nib);
	for (int i = 0; i < ngl; i++) if (ref[i] < 0) glyph_box(&G[i]);
	for (int i = 0; i < ngl; i++)
		if (ref[i] >= 0) { const struct glyph *o = &G[ref[i]]; G[i].adv = o->adv; G[i].xmin = o->xmin; G[i].ymin = o->ymin; G[i].xmax = o->xmax; G[i].ymax = o->ymax; }
	int maxp = 0, maxc = 0, advmax = 0, minlsb = 0x7fff, minrsb = 0x7fff, xmaxext = -0x7fff;
	int fxmin = 0x7fff, fymin = 0x7fff, fxmax = -0x7fff, fymax = -0x7fff;
	long advsum = 0;
	for (int i = 0; i < ngl; i++) {
		if (ref[i] >= 0) { advsum += G[i].adv; continue; }
		if (G[i].np > maxp) maxp = G[i].np;
		if (G[i].nc > maxc) maxc = G[i].nc;
		if (G[i].adv > advmax) advmax = G[i].adv;
		advsum += G[i].adv;
		if (G[i].nc) {
			if (G[i].xmin < minlsb) minlsb = G[i].xmin;
			if (G[i].adv - G[i].xmax < minrsb) minrsb = G[i].adv - G[i].xmax;
			if (G[i].xmax > xmaxext) xmaxext = G[i].xmax;
			if (G[i].xmin < fxmin) fxmin = G[i].xmin;
			if (G[i].ymin < fymin) fymin = G[i].ymin;
			if (G[i].xmax > fxmax) fxmax = G[i].xmax;
			if (G[i].ymax > fymax) fymax = G[i].ymax;
		}
	}
	if (fxmin > fxmax) fxmin = fymin = fxmax = fymax = 0;
	if (minlsb == 0x7fff) minlsb = minrsb = xmaxext = 0;
	double r = ink * XH / 2;
	/* the line's metrics are the Regular style's in all three, so lines stand the same in each
	 * (FR-034); the clipping box (OS/2 win) is each style's own */
	int rymin = fymin, rymax = fymax;
	if (style != HW_STYLE_REGULAR) {
		rymin = 0x7fff; rymax = -0x7fff;
		for (int i = 0; i < nl; i++)
			for (int v = 0; v < ls[i]->nvar + (i == nl - 1 ? nj : 0); v++) {
				struct glyph q;
				glyph_of(&q, L, v < ls[i]->nvar ? &ls[i]->var[v] : &js[v - ls[i]->nvar]->v, gap, ink, HW_STYLE_REGULAR, nib);
				glyph_box(&q);
				if (q.nc && q.ymin < rymin) rymin = q.ymin;
				if (q.nc && q.ymax > rymax) rymax = q.ymax;
				free(q.p); free(q.ends);
			}
		if (rymin > rymax) rymin = rymax = 0;
	}
	int asc = (int)lround(fmax(L->m.asc, L->m.cap) * XH + r), desc = (int)lround(L->m.desc * XH - r);
	if (asc < rymax) asc = rymax;
	if (desc > rymin) desc = rymin;
	int gapline = (int)lround(0.15 * (asc - desc));

	struct bb t[12] = { 0 }, f = { 0 };
	enum { GPOS, GSUB, OS2, CMAP, GLYF, HEAD, HHEA, HMTX, LOCA, MAXP, NAME, POST };
	const char *tag[12] = { "GPOS", "GSUB", "OS/2", "cmap", "glyf", "head", "hhea", "hmtx", "loca", "maxp", "name", "post" };
	int have_gsub = 1;
	{
		/* the kerning classes: the letters' variants, then the joined pairs */
		int nv = nj;
		for (int i = 0; i < nl; i++) nv += ls[i]->nvar;
		int g1 = gl_at(1, 0, NULL), ng = ngl - g1, *cls = malloc((size_t)(ng + 1) * sizeof(int)), *first = malloc((size_t)(nl + 1) * sizeof(int));
		struct kent *E = malloc((size_t)(nv + 1) * sizeof *E);
		if (!cls || !first || !E) { free(cls); free(first); free(E); goto fail; }
		int e = 0;
		for (int i = 0; i < nl; i++) {
			first[i] = e;
			for (int v = 0; v < ls[i]->nvar; v++) {
				const struct hw_variant *x = &ls[i]->var[v];
				E[e++] = (struct kent){ ls[i]->cp, ls[i]->cp, &x->prof, x->mid, x->mid, x->width, x->lc, x->rc };
			}
		}
		for (int k = 0; k < nj; k++)
			E[e++] = (struct kent){ js[k]->a, js[k]->b, &js[k]->v.prof, NAN, NAN, js[k]->v.width, js[k]->v.lc, js[k]->v.rc };
		for (int g = 0; g < ng; g++) cls[g] = -1;
		for (int i = 0; i < nl; i++) {
			for (int s2 = 0; s2 < S; s2++) cls[gl_at(i + 1, s2, NULL) - g1] = first[i] + (ls[i]->slot[s2] < ls[i]->nvar ? ls[i]->slot[s2] : 0);
			for (int v = 0; v < ls[i]->nvar; v++) cls[outl[i] + v - g1] = first[i] + v;
		}
		for (int k = 0; k < nj; k++) cls[lig0 + k - g1] = e - nj + k;
		gpos(&t[GPOS], L, E, nv, cls, g1, ng);
		free(cls); free(first); free(E);
		struct gctx gc = { nl, ls, outl, nj, lig0, ja, jb, random };
		gsub(&t[GSUB], nl + 1, gl_at, NULL, &gc);
	}

	/* glyf + loca (long) */
	for (int i = 0; i < ngl; i++) {
		u32(&t[LOCA], (uint32_t)t[GLYF].n);
		if (ref[i] >= 0 && G[ref[i]].nc) {
			/* a reference: ARG_1_AND_2_ARE_WORDS | ARGS_ARE_XY_VALUES | USE_MY_METRICS, at 0, 0 */
			s16(&t[GLYF], -1);
			s16(&t[GLYF], G[i].xmin); s16(&t[GLYF], G[i].ymin); s16(&t[GLYF], G[i].xmax); s16(&t[GLYF], G[i].ymax);
			u16(&t[GLYF], 0x0001 | 0x0002 | 0x0200); u16(&t[GLYF], (unsigned)ref[i]);
			s16(&t[GLYF], 0); s16(&t[GLYF], 0);
		} else if (ref[i] < 0) glyf_data(&t[GLYF], &G[i]);
		pad4(&t[GLYF]);
	}
	u32(&t[LOCA], (uint32_t)t[GLYF].n);
	if (!t[GLYF].n) u32(&t[GLYF], 0);

	/* hmtx */
	for (int i = 0; i < ngl; i++) {
		int has = ref[i] >= 0 ? G[ref[i]].nc : G[i].nc;
		u16(&t[HMTX], (unsigned)G[i].adv); s16(&t[HMTX], has ? G[i].xmin : 0);
	}

	/* cmap: format 4 (BMP) and format 12 (all), Windows */
	unsigned *cps = malloc((size_t)(nl + 1) * sizeof *cps);
	int *gid = malloc((size_t)(nl + 1) * sizeof *gid), nm = 0;
	if (!cps || !gid) { free(cps); free(gid); goto fail; }
	cps[nm] = ' '; gid[nm] = gl_at(0, 0, NULL); nm++;
	for (int i = 0; i < nl; i++) { cps[nm] = ls[i]->cp; gid[nm] = base[i]; nm++; }
	/* sorted by code point already except the space, which is lowest of all but controls */
	for (int a = 1; a < nm; a++) for (int b2 = a; b2 > 0 && cps[b2] < cps[b2 - 1]; b2--) {
		unsigned tc = cps[b2]; cps[b2] = cps[b2 - 1]; cps[b2 - 1] = tc;
		int tg = gid[b2]; gid[b2] = gid[b2 - 1]; gid[b2 - 1] = tg;
	}
	{
		struct bb *c = &t[CMAP];
		u16(c, 0); u16(c, 2);
		u16(c, 3); u16(c, 1); size_t at4 = c->n; u32(c, 0);
		u16(c, 3); u16(c, 10); size_t at12 = c->n; u32(c, 0);
		/* format 4 segments: runs of consecutive code points and glyph ids */
		int segs = 0;
		unsigned ss[1024], se[1024];
		int sd[1024];
		for (int i = 0; i < nm && segs < 1023; i++) {
			if (cps[i] > 0xfffe) break;
			if (segs && cps[i] == se[segs - 1] + 1 && (int)((unsigned)gid[i] - cps[i]) == sd[segs - 1]) { se[segs - 1] = cps[i]; continue; }
			ss[segs] = cps[i]; se[segs] = cps[i]; sd[segs] = (int)((unsigned)gid[i] - cps[i]); segs++;
		}
		ss[segs] = se[segs] = 0xffff; sd[segs] = 1; segs++;
		put32(c, at4, (uint32_t)c->n);
		size_t f4 = c->n;
		u16(c, 4); size_t at4l = c->n; u16(c, 0); u16(c, 0);
		int sr = 1, es = 0;
		while (sr * 2 <= segs) { sr *= 2; es++; }
		u16(c, (unsigned)segs * 2); u16(c, (unsigned)sr * 2); u16(c, (unsigned)es); u16(c, (unsigned)(segs - sr) * 2);
		for (int i = 0; i < segs; i++) u16(c, se[i]);
		u16(c, 0);
		for (int i = 0; i < segs; i++) u16(c, ss[i]);
		for (int i = 0; i < segs; i++) u16(c, (unsigned)sd[i] & 0xffff);
		for (int i = 0; i < segs; i++) u16(c, 0);
		put16(c, at4l, (unsigned)(c->n - f4));
		put32(c, at12, (uint32_t)c->n);
		u16(c, 12); u16(c, 0); size_t at12l = c->n; u32(c, 0); u32(c, 0);
		size_t f12 = c->n - 12;
		size_t atg = c->n; u32(c, 0);
		uint32_t ng = 0;
		for (int i = 0; i < nm;) {
			int j = i;
			while (j + 1 < nm && cps[j + 1] == cps[j] + 1 && gid[j + 1] == gid[j] + 1) j++;
			u32(c, cps[i]); u32(c, cps[j]); u32(c, (uint32_t)gid[i]);
			ng++;
			i = j + 1;
		}
		put32(c, atg, ng);
		put32(c, at12l, (uint32_t)(c->n - f12));
	}

	/* head */
	{
		struct bb *h = &t[HEAD];
		/* the revision: the version, a step lower in Calligraphy and Brush, so Regular is the
		 * family's face where nothing asks for a style (fontconfig prefers the newest) */
		u32(h, 0x00010000); u32(h, ((uint32_t)version << 16) - (style != HW_STYLE_REGULAR)); u32(h, 0); u32(h, 0x5F0F3CF5);
		u16(h, 0x000B); u16(h, UPEM);
		/* created, modified: the newest sample's time (seconds since 1904) */
		uint64_t t1904 = (uint64_t)(L->built > 0 ? L->built : 0) + 2082844800ULL;
		u32(h, (uint32_t)(t1904 >> 32)); u32(h, (uint32_t)t1904);
		u32(h, (uint32_t)(t1904 >> 32)); u32(h, (uint32_t)t1904);
		s16(h, fxmin); s16(h, fymin); s16(h, fxmax); s16(h, fymax);
		u16(h, 0); u16(h, 8); s16(h, 2); s16(h, 1); s16(h, 0);
	}
	/* hhea */
	{
		struct bb *h = &t[HHEA];
		u32(h, 0x00010000); s16(h, asc); s16(h, desc); s16(h, gapline);
		u16(h, (unsigned)advmax); s16(h, minlsb); s16(h, minrsb); s16(h, xmaxext);
		s16(h, 1); s16(h, 0); s16(h, 0);
		for (int i = 0; i < 4; i++) s16(h, 0);
		s16(h, 0); u16(h, (unsigned)ngl);
	}
	/* maxp 1.0 */
	{
		struct bb *h = &t[MAXP];
		u32(h, 0x00010000); u16(h, (unsigned)ngl); u16(h, (unsigned)maxp); u16(h, (unsigned)maxc);
		u16(h, (unsigned)maxp); u16(h, (unsigned)maxc); u16(h, 2);
		for (int i = 0; i < 6; i++) u16(h, 0);
		u16(h, 1); u16(h, 1);  /* the step glyphs: one reference each */
	}
	/* OS/2 version 4 */
	{
		struct bb *h = &t[OS2];
		unsigned first = nm ? cps[0] : 0x20, last = nm ? cps[nm - 1] : 0x20;
		u16(h, 4); s16(h, (int)(advsum / (ngl ? ngl : 1))); u16(h, 400); u16(h, 5); u16(h, 0);
		s16(h, 650); s16(h, 600); s16(h, 0); s16(h, 75); s16(h, 650); s16(h, 600); s16(h, 0); s16(h, 350);
		s16(h, (int)lround(r * 2)); s16(h, XH / 2); s16(h, 0);
		for (int i = 0; i < 10; i++) u8(h, 0);
		uint32_t ur1 = 1;               /* Basic Latin */
		for (int i = 0; i < nm; i++) if (cps[i] >= 0x80 && cps[i] < 0x100) ur1 |= 2;
		u32(h, ur1); u32(h, 0); u32(h, 0); u32(h, 0);
		u8(h, 'N'); u8(h, 'O'); u8(h, 'N'); u8(h, 'E');
		u16(h, 0x40 | 0x80);           /* regular, typo metrics */
		u16(h, first > 0xffff ? 0xffff : first); u16(h, last > 0xffff ? 0xffff : last);
		s16(h, asc); s16(h, desc); s16(h, gapline);
		u16(h, (unsigned)(fymax > asc ? fymax : asc)); u16(h, (unsigned)(-(fymin < desc ? fymin : desc)));
		u32(h, 1); u32(h, 0);
		s16(h, XH); s16(h, (int)lround(L->m.cap * XH));
		u16(h, 0); u16(h, ' '); u16(h, 2);
	}
	/* post 3.0 */
	{
		struct bb *h = &t[POST];
		u32(h, 0x00030000); u32(h, 0); s16(h, -(int)lround(XH * 0.25)); s16(h, (int)lround(r * 2));
		u32(h, 0); u32(h, 0); u32(h, 0); u32(h, 0); u32(h, 0);
	}
	/* name */
	{
		/* Regular: the family alone; Calligraphy and Brush, styles beyond the four a family
		 * of name ID 1 and 2 holds, as their own family "NAME STYLE" (Regular) and in the
		 * typographic family NAME (IDs 16, 17), with a PostScript name of their own */
		const char *sty = hw_font_style_names[style];
		char ps[64], uniq[160], ver[32], full[160], fam[160];
		int pn = 0;
		for (const char *p = name; *p && pn < 40; p++)
			if ((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9')) ps[pn++] = *p;
		ps[pn] = 0;
		if (!pn) snprintf(ps, sizeof ps, "Handwriting");
		snprintf(ps + strlen(ps), sizeof ps - strlen(ps), "-%s", sty);
		snprintf(ver, sizeof ver, "Version %u.000", version);
		snprintf(uniq, sizeof uniq, "%s;%u;%s", ps, version, L->inputs);
		if (style == HW_STYLE_REGULAR) snprintf(fam, sizeof fam, "%s", name);
		else snprintf(fam, sizeof fam, "%s %s", name, sty);
		snprintf(full, sizeof full, "%s", fam);
		struct bb recs = { 0 }, str = { 0 };
		name_rec(&recs, &str, 1, fam);
		name_rec(&recs, &str, 2, "Regular");
		name_rec(&recs, &str, 3, uniq);
		name_rec(&recs, &str, 4, full);
		name_rec(&recs, &str, 5, ver);
		name_rec(&recs, &str, 6, ps);
		int nrec = 6;
		if (style != HW_STYLE_REGULAR) { name_rec(&recs, &str, 16, name); name_rec(&recs, &str, 17, sty); nrec = 8; }
		struct bb *h = &t[NAME];
		u16(h, 0); u16(h, (unsigned)nrec); u16(h, (unsigned)(6 + recs.n));
		bgrow(h, recs.n + str.n);
		if (!h->bad && recs.d && str.d) { memcpy(h->d + h->n, recs.d, recs.n); h->n += recs.n; memcpy(h->d + h->n, str.d, str.n); h->n += str.n; }
		free(recs.d); free(str.d);
	}
	free(cps); free(gid);

	/* the file */
	int nt = 0, order[12];
	for (int i = 0; i < 12; i++) if (i != GSUB || have_gsub) order[nt++] = i;
	int sr = 1, es = 0;
	while (sr * 2 <= nt) { sr *= 2; es++; }
	u32(&f, 0x00010000); u16(&f, (unsigned)nt); u16(&f, (unsigned)sr * 16); u16(&f, (unsigned)es); u16(&f, (unsigned)(nt - sr) * 16);
	size_t dir = f.n, headat = 0;
	for (int i = 0; i < nt; i++) for (int k2 = 0; k2 < 16; k2++) u8(&f, 0);
	for (int i = 0; i < nt; i++) {
		int ti = order[i];
		if (t[ti].bad) goto fail;
		size_t off = f.n;
		if (ti == HEAD) headat = off;
		bgrow(&f, t[ti].n);
		if (f.bad) goto fail;
		if (t[ti].n) memcpy(f.d + f.n, t[ti].d, t[ti].n);
		f.n += t[ti].n;
		size_t e = dir + (size_t)i * 16;
		for (int c = 0; c < 4; c++) f.d[e + (size_t)c] = (unsigned char)tag[ti][c];
		put32(&f, e + 4, tsum(t[ti].d, t[ti].n));
		put32(&f, e + 8, (uint32_t)off);
		put32(&f, e + 12, (uint32_t)t[ti].n);
		pad4(&f);
	}
	if (f.bad) goto fail;
	put32(&f, headat + 8, 0xB1B0AFBAu - tsum(f.d, f.n));
	for (int i = 0; i < 12; i++) free(t[i].d);
	for (int i = 0; i < ngl; i++) { free(G[i].p); free(G[i].ends); }
	free(G); free(ref); free(ls); free(base); free(outl); free(ja); free(jb); free(js);
	*len = f.n;
	return f.d;
fail:
	for (int i = 0; i < 12; i++) free(t[i].d);
	for (int i = 0; i < ngl; i++) { free(G[i].p); free(G[i].ends); }
	free(f.d);
	free(G); free(ref); free(ls); free(base); free(outl); free(ja); free(jb); free(js);
	return NULL;
}
