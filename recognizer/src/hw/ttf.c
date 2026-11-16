// SPDX-License-Identifier: GPL-2.0-or-later
// The handwriting font (spec 003 FR-009 to FR-011): the letter model as a TrueType font.
// Every stroke is outlined in the pen's width as pieces of one direction (a quadrilateral
// per segment, a disc at the ends, a wedge or a disc where it turns) that overlap; the
// non-zero fill makes their union. Variants are unencoded glyphs a contextual-alternates
// lookup ('calt', on by default in text shaping) picks along the text (letters.h,
// hw_letters_step).
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

static void glyph_of(struct glyph *g, const struct hw_variant *v, double gap, double ink)
{
	memset(g, 0, sizeof *g);
	double r = ink * XH / 2;
	if (r < 6) r = 6;
	/* the side bearings: half the person's typical gap each side, between the pen's centre
	 * lines as the spacing measures it (GPOS kerns each pair to its own gap) */
	double ox = gap / 2 * XH;
	for (size_t j = 0; j < v->nstrokes; j++) outline_stroke(g, &v->s[j], ox, r);
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

/* GSUB: the font steps through a cycle of HW_LETTERS_SLOTS along the text (letters.h,
 * hw_letters_step), every glyph carrying its step: one chain-context lookup in 'calt' with a
 * rule per step (after a glyph at step s, the next character's glyph at step s + 1), and a
 * single substitution per step it calls. gl(i, s): the glyph of character i (0 the space) at
 * step s, ascending in i for each s. */
static void gsub(struct bb *b, int nch, int (*gl)(int, int, void *), void *ctx)
{
	enum { S = HW_LETTERS_SLOTS };
	int *cls = malloc((size_t)(nch + 1) * sizeof(int)), *sub = malloc((size_t)(nch + 1) * sizeof(int));
	if (!cls || !sub) { b->bad = 1; free(cls); free(sub); return; }
	size_t g0 = b->n;
	u16(b, 1); u16(b, 0);           /* version 1.0 */
	size_t at_sl = b->n; u16(b, 0);
	size_t at_fl = b->n; u16(b, 0);
	size_t at_ll = b->n; u16(b, 0);
	/* ScriptList: DFLT and latn, each a default LangSys with feature 0 */
	put16(b, at_sl, (unsigned)(b->n - g0));
	size_t sl = b->n;
	u16(b, 2);
	const char *tags[2] = { "DFLT", "latn" };
	size_t at_s[2];
	for (int i = 0; i < 2; i++) { for (int c = 0; c < 4; c++) u8(b, (unsigned char)tags[i][c]); at_s[i] = b->n; u16(b, 0); }
	for (int i = 0; i < 2; i++) {
		put16(b, at_s[i], (unsigned)(b->n - sl));
		u16(b, 4); u16(b, 0);   /* the default LangSys right after, no others */
		u16(b, 0); u16(b, 0xffff); u16(b, 1); u16(b, 0);
	}
	/* FeatureList: calt -> lookup 0 */
	put16(b, at_fl, (unsigned)(b->n - g0));
	u16(b, 1);
	u8(b, 'c'); u8(b, 'a'); u8(b, 'l'); u8(b, 't'); u16(b, 8);
	u16(b, 0); u16(b, 1); u16(b, 0);
	/* LookupList: 0 the chain, k = 1 .. S-1 the substitution to step k (step 0 is the
	 * glyph the character maps to) */
	put16(b, at_ll, (unsigned)(b->n - g0));
	size_t ll = b->n;
	u16(b, S);
	size_t at_l[S];
	for (int i = 0; i < S; i++) { at_l[i] = b->n; u16(b, 0); }
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

/* GPOS: pair kerning (FR-009), one class per variant, so every pair of variants comes out
 * at the spacing's gap for its two characters (spacing.c). PairPos format 2 subtables of
 * as many first classes as fit 16-bit offsets, behind Extension lookups (32-bit). The
 * classes: the glyph of character i at step s has its variant's, the space none. */
static int gl_at(int i, int s, void *ctx);
static void gpos(struct bb *b, const struct hw_letters *L, const struct hw_letter *const *ls, int nl)
{
	enum { S = HW_LETTERS_SLOTS };
	int nv = 0;
	int *first = malloc((size_t)(nl + 1) * sizeof(int));
	if (!first) { b->bad = 1; return; }
	for (int i = 0; i < nl; i++) { first[i] = nv; nv += ls[i]->nvar; }
	const struct hw_letter **vl = malloc((size_t)(nv + 1) * sizeof *vl);
	const struct hw_variant **vv = malloc((size_t)(nv + 1) * sizeof *vv);
	short *kern = malloc((size_t)(nv + 1) * (size_t)(nv + 1) * sizeof *kern);
	int *cov = malloc((size_t)(nl * S + 1) * sizeof(int));
	if (!vl || !vv || !kern || !cov) { b->bad = 1; free(first); free(vl); free(vv); free(kern); free(cov); return; }
	for (int i = 0; i < nl; i++) for (int v = 0; v < ls[i]->nvar; v++) { vl[first[i] + v] = ls[i]; vv[first[i] + v] = &ls[i]->var[v]; }
	double letter = L->sp.letter;
	for (int a = 0; a < nv; a++)
		for (int c = 0; c < nv; c++) {
			const struct hw_variant *va = vv[a], *vc = vv[c];
			double at = hw_profile_gap(&va->prof, &vc->prof, va->width + letter);
			double want = hw_spacing_gap(&L->sp, vl[a]->cp, &va->prof, va->rc, vl[c]->cp, &vc->prof, vc->lc);
			long k = lround((want - at) * XH);
			kern[a * nv + c] = (short)(k < -3 * XH ? -3 * XH : k > 3 * XH ? 3 * XH : k);
		}
	/* the first classes per subtable: what keeps its classes and coverage in 16-bit reach */
	int cdsz = 6 + 2 * nl * S, per = (60000 - 16 - 3 * cdsz) / (2 * (nv + 1)) - 1;
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
	size_t ll = b->n;
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
	(void)ll;
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
		/* coverage: the step glyphs of this subtable's variants, ascending */
		int nc = 0;
		for (int i = 0; i < nl; i++)
			for (int s2 = 0; s2 < S; s2++) {
				int vid = first[i] + ls[i]->slot[s2];
				if (vid >= c0 && vid < c1) cov[nc++] = gl_at(i + 1, s2, NULL);
			}
		put16(b, at_cov, (unsigned)(b->n - st));
		coverage(b, cov, nc);
		/* class defs, format 1 over the letters' step glyphs */
		for (int k = 0; k < 2; k++) {
			put16(b, k ? at_cd2 : at_cd1, (unsigned)(b->n - st));
			u16(b, 1); u16(b, (unsigned)gl_at(1, 0, NULL)); u16(b, (unsigned)(nl * S));
			for (int i = 0; i < nl; i++)
				for (int s2 = 0; s2 < S; s2++) {
					int vid = first[i] + ls[i]->slot[s2];
					if (k) u16(b, (unsigned)(vid + 1));
					else u16(b, vid >= c0 && vid < c1 ? (unsigned)(vid - c0 + 1) : 0);
				}
		}
		if (b->n - st > 0xffff) b->bad = 1;
	}
	free(first); free(vl); free(vv); free(kern); free(cov);
}

/* the glyph ids: .notdef, the space at each step, each covered character (code point
 * order) at each step, then each character's variants (its outlines; the glyph at a step
 * is a reference to the variant the letter shows there) */
static int gl_at(int i, int s, void *ctx)
{
	(void)ctx;
	return 1 + i * HW_LETTERS_SLOTS + s;
}

static unsigned char *font_build(const struct hw_letters *L, const char *name, unsigned version, size_t *len);

/* the font of the letters as the person marked them: variants left out are not in it */
unsigned char *hw_font_build(const struct hw_letters *L, const char *name, unsigned version, size_t *len)
{
	struct hw_letters V;
	if (hw_letters_view(L, &V)) return NULL;
	unsigned char *r = font_build(&V, name, version, len);
	hw_letters_view_free(&V);
	return r;
}

static unsigned char *font_build(const struct hw_letters *L, const char *name, unsigned version, size_t *len)
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
	struct glyph *G = calloc((size_t)ngl, sizeof *G);
	int *ref = malloc((size_t)ngl * sizeof *ref);       /* the glyph a step glyph refers to, -1 none */
	if (!G || !ref) { free(G); free(ref); free(ls); free(base); free(outl); return NULL; }
	for (int i = 0; i < ngl; i++) ref[i] = -1;
	double gap = L->sp.letter, ink = L->m.ink;
	G[0].adv = XH;
	/* the space: the word gap less the two side bearings beside it */
	int sp = (int)lround((L->sp.word - gap) * XH);
	if (sp < XH / 10) sp = XH / 10;
	for (int s2 = 0; s2 < S; s2++) G[gl_at(0, s2, NULL)].adv = sp;
	for (int i = 0; i < nl; i++) {
		for (int v = 0; v < ls[i]->nvar; v++) glyph_of(&G[outl[i] + v], &ls[i]->var[v], gap, ink);
		for (int s2 = 0; s2 < S; s2++) ref[gl_at(i + 1, s2, NULL)] = outl[i] + (ls[i]->slot[s2] < ls[i]->nvar ? ls[i]->slot[s2] : 0);
	}
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
	int asc = (int)lround(fmax(L->m.asc, L->m.cap) * XH + r), desc = (int)lround(L->m.desc * XH - r);
	if (asc < fymax) asc = fymax;
	if (desc > fymin) desc = fymin;
	int gapline = (int)lround(0.15 * (asc - desc));

	struct bb t[12] = { 0 }, f = { 0 };
	enum { GPOS, GSUB, OS2, CMAP, GLYF, HEAD, HHEA, HMTX, LOCA, MAXP, NAME, POST };
	const char *tag[12] = { "GPOS", "GSUB", "OS/2", "cmap", "glyf", "head", "hhea", "hmtx", "loca", "maxp", "name", "post" };
	int have_gsub = 1;
	gpos(&t[GPOS], L, ls, nl);
	gsub(&t[GSUB], nl + 1, gl_at, NULL);

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
		u32(h, 0x00010000); u32(h, (uint32_t)version << 16); u32(h, 0); u32(h, 0x5F0F3CF5);
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
		char ps[64], uniq[160], ver[32], full[128];
		int pn = 0;
		for (const char *p = name; *p && pn < 40; p++)
			if ((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9')) ps[pn++] = *p;
		ps[pn] = 0;
		if (!pn) snprintf(ps, sizeof ps, "Handwriting");
		snprintf(ps + strlen(ps), sizeof ps - strlen(ps), "-Regular");
		snprintf(ver, sizeof ver, "Version %u.000", version);
		snprintf(uniq, sizeof uniq, "%s;%u;%s", ps, version, L->inputs);
		snprintf(full, sizeof full, "%s", name);
		struct bb recs = { 0 }, str = { 0 };
		name_rec(&recs, &str, 1, name);
		name_rec(&recs, &str, 2, "Regular");
		name_rec(&recs, &str, 3, uniq);
		name_rec(&recs, &str, 4, full);
		name_rec(&recs, &str, 5, ver);
		name_rec(&recs, &str, 6, ps);
		struct bb *h = &t[NAME];
		u16(h, 0); u16(h, 6); u16(h, (unsigned)(6 + recs.n));
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
	free(G); free(ref); free(ls); free(base); free(outl);
	*len = f.n;
	return f.d;
fail:
	for (int i = 0; i < 12; i++) free(t[i].d);
	for (int i = 0; i < ngl; i++) { free(G[i].p); free(G[i].ends); }
	free(f.d);
	free(G); free(ref); free(ls); free(base); free(outl);
	return NULL;
}
