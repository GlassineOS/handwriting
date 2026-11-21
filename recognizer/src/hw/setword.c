// SPDX-License-Identifier: GPL-2.0-or-later
// A text set in the person's font, as pen strokes (setword.h).
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "setword.h"
#include "base.h"

void hw_set_free(struct hw_set *s)
{
	for (size_t i = 0; i < s->n; i++) { free(s->s[i].x); free(s->s[i].y); }
	free(s->s);
	memset(s, 0, sizeof *s);
}

/* one stroke of m points at (dx, 0) into out */
static int add(struct hw_set *o, size_t *cap, const double *x, const double *y, size_t m, double dx)
{
	if (!m) return 0;
	if (o->n == *cap) {
		size_t c = *cap ? 2 * *cap : 16;
		struct hw_lstroke *t = realloc(o->s, c * sizeof *t);
		if (!t) return -1;
		o->s = t;
		*cap = c;
	}
	struct hw_lstroke *s = &o->s[o->n];
	s->x = malloc(m * sizeof *s->x);
	s->y = malloc(m * sizeof *s->y);
	if (!s->x || !s->y) { free(s->x); free(s->y); return -1; }
	for (size_t i = 0; i < m; i++) { s->x[i] = x[i] + dx; s->y[i] = y[i]; }
	s->n = m;
	o->n++;
	return 0;
}

static const struct hw_letter *letter(const struct hw_letters *V, unsigned cp)
{
	size_t lo = 0, hi = V->n;
	while (lo < hi) {
		size_t m = (lo + hi) / 2;
		if (V->l[m].cp < cp) lo = m + 1;
		else hi = m;
	}
	return lo < V->n && V->l[lo].cp == cp ? &V->l[lo] : NULL;
}

int hw_set_text(const struct hw_letters *L, const unsigned *cp, size_t n, struct hw_set *out)
{
	memset(out, 0, sizeof *out);
	if (!L || !n) {
		/* no letter model: each character its advance (an x-height), the spaces too */
		out->width = (double)n;
		return 0;
	}
	out->xh_mm = L->m.xh_mm > 0 ? L->m.xh_mm : 0;
	out->ink = L->m.ink > 0 ? L->m.ink : 0;
	struct hw_letters V0, V;
	struct hw_font_joins J = { 0 };
	struct hw_base_glyph *bg = NULL;
	size_t nbg = 0;
	if (hw_letters_view(L, &V0)) return -1;
	/* as the font has the letters over its base (its defaults; the characters they don't
	 * cover at their base glyphs' advances) */
	if (hw_base_layout(&V0, &V, &bg, &nbg)) { hw_letters_view_free(&V0); return -1; }
	int ret = -1, *var = malloc((n + 1) * sizeof *var), *jk = malloc((n + 1) * sizeof *jk);
	long *jo = malloc((n + 1) * sizeof *jo);
	double *pen = malloc((n + 1) * sizeof *pen), bear = 0;
	size_t cap = 0;
	if (!var || !jk || !jo || !pen || hw_font_joins_prepare(&V, &J)) goto done;
	hw_letters_glyphs(&V, &J, cp, n, var, jo, jk);
	hw_font_layout(&V, &J, bg, nbg, cp, n, var, jo, jk, pen, &bear);
	double hx[HW_JOIN_MID + 1], hy[HW_JOIN_MID + 1];
	for (size_t k = 0; k < n; k++) {
		const struct hw_variant *v = NULL;
		const struct hw_letter *l = cp[k] == ' ' ? NULL : letter(&V, cp[k]);
		if (jo[k] >= 0 && k + 1 < n && jo[k + 1] == jo[k]) v = &V.joined[jo[k]].v;
		else if (jo[k] >= 0) continue;                  /* the second of a joined pair: drawn with the first */
		else if (l && var[k] >= 0 && var[k] < l->nvar) v = &l->var[var[k]];
		if (!v) continue;
		for (size_t t = 0; t < v->nstrokes; t++)
			if (add(out, &cap, v->s[t].x, v->s[t].y, v->s[t].n, pen[k] + bear)) goto done;
		/* the join to the next letter (FR-044): this letter's half, then the next one's */
		if (l && jk[k] >= 0 && k + 1 < n && jo[k] < 0) {
			size_t li = (size_t)(l - V.l);
			const struct hw_letter *nl = letter(&V, cp[k + 1]);
			if (!hw_font_join_half(&V, &J, jk[k], li, var[k], 0, hx, hy) &&
			    add(out, &cap, hx, hy, HW_JOIN_MID + 1, pen[k] + bear)) goto done;
			if (nl && !hw_font_join_half(&V, &J, jk[k], (size_t)(nl - V.l), var[k + 1], 1, hx, hy) &&
			    add(out, &cap, hx, hy, HW_JOIN_MID + 1, pen[k + 1] + bear)) goto done;
		}
	}
	/* the ink's left edge at 0 */
	double x0 = INFINITY, x1 = -INFINITY;
	for (size_t i = 0; i < out->n; i++)
		for (size_t j = 0; j < out->s[i].n; j++) { x0 = fmin(x0, out->s[i].x[j]); x1 = fmax(x1, out->s[i].x[j]); }
	if (x0 <= x1) {
		for (size_t i = 0; i < out->n; i++)
			for (size_t j = 0; j < out->s[i].n; j++) out->s[i].x[j] -= x0;
		out->width = x1 - x0;
	} else {
		out->width = n ? pen[n - 1] + 1 : 0;
	}
	ret = 0;
done:
	if (ret) hw_set_free(out);
	hw_font_joins_free(&J);
	hw_base_glyphs_free(bg, nbg);
	hw_letters_view_free(&V0);
	free(var); free(jk); free(jo); free(pen);
	return ret;
}
