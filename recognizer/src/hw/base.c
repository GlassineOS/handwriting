// SPDX-License-Identifier: GPL-2.0-or-later
// Autograph's base font as pen strokes (base.h; docs/recognizer.md, The base font).
#include <ctype.h>
#include <errno.h>
#include <json-c/json.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "sample.h"

static struct hw_base base;
static int base_state;                  /* 0 not read, 1 read, -1 none */

static double jnum(json_object *o, const char *k, double def)
{
	json_object *v;
	return json_object_object_get_ex(o, k, &v) ? json_object_get_double(v) : def;
}

static void jstr(json_object *o, const char *k, char *out, size_t n)
{
	json_object *v;
	out[0] = 0;
	if (json_object_object_get_ex(o, k, &v) && json_object_is_type(v, json_type_string))
		snprintf(out, n, "%s", json_object_get_string(v));
}

static int cmp_char(const void *a, const void *b)
{
	unsigned x = ((const struct hw_base_char *)a)->cp, y = ((const struct hw_base_char *)b)->cp;
	return (x > y) - (x < y);
}

static void base_free(struct hw_base *B)
{
	for (size_t i = 0; i < B->n; i++) {
		for (size_t j = 0; j < B->c[i].nstrokes; j++) { free(B->c[i].s[j].x); free(B->c[i].s[j].y); }
		free(B->c[i].s);
	}
	free(B->c);
	memset(B, 0, sizeof *B);
}

/* one character's line: 0, -1 not a valid one (left out) */
static int char_in(json_object *o, struct hw_base_char *c)
{
	json_object *ch, *st;
	if (!json_object_object_get_ex(o, "char", &ch) || !json_object_is_type(ch, json_type_string) ||
	    !json_object_object_get_ex(o, "strokes", &st) || !json_object_is_type(st, json_type_array))
		return -1;
	unsigned cp[2];
	if (hw_utf8_decode(json_object_get_string(ch), cp, 2) != 1 || cp[0] <= 0x20) return -1;
	size_t ns = json_object_array_length(st);
	if (!ns || ns > 64) return -1;
	c->cp = cp[0];
	c->s = calloc(ns, sizeof *c->s);
	if (!c->s) return -1;
	c->nstrokes = ns;
	for (size_t j = 0; j < ns; j++) {
		json_object *s = json_object_array_get_idx(st, j);
		size_t n = json_object_is_type(s, json_type_array) ? json_object_array_length(s) : 0;
		if (!n || n > 4096) return -1;
		c->s[j].x = malloc(n * sizeof(double));
		c->s[j].y = malloc(n * sizeof(double));
		if (!c->s[j].x || !c->s[j].y) return -1;
		c->s[j].n = n;
		for (size_t i = 0; i < n; i++) {
			json_object *p = json_object_array_get_idx(s, i);
			if (!json_object_is_type(p, json_type_array) || json_object_array_length(p) != 2) return -1;
			double x = json_object_get_double(json_object_array_get_idx(p, 0)), y = json_object_get_double(json_object_array_get_idx(p, 1));
			if (!isfinite(x) || !isfinite(y) || fabs(x) > 20 || fabs(y) > 20) return -1;
			c->s[j].x[i] = x;
			c->s[j].y[i] = y;
		}
	}
	return 0;
}

static int base_read(const char *path, struct hw_base *B)
{
	FILE *f = fopen(path, "re");
	if (!f) {
		if (errno != ENOENT) fprintf(stderr, "glassine-handwriting: %s: %s\n", path, strerror(errno));
		return -1;
	}
	memset(B, 0, sizeof *B);
	char *line = NULL;
	size_t cap = 0, ccap = 0;
	ssize_t got;
	uint64_t h = 1469598103934665603ULL;
	int first = 1, ok = 1;
	while ((got = getline(&line, &cap, f)) > 0) {
		for (ssize_t i = 0; i < got; i++) { h ^= (unsigned char)line[i]; h *= 1099511628211ULL; }
		json_object *o = json_tokener_parse(line);
		if (!o) { if (first) ok = 0; continue; }
		if (first) {
			json_object *fm;
			first = 0;
			if (!json_object_object_get_ex(o, "format", &fm) || strcmp(json_object_get_string(fm), HW_BASE_FORMAT) ||
			    jnum(o, "version", 0) != HW_BASE_VERSION)
				ok = 0;
			jstr(o, "family", B->family, sizeof B->family);
			jstr(o, "copyright", B->copyright, sizeof B->copyright);
			jstr(o, "licence_url", B->licence_url, sizeof B->licence_url);
			B->slant = jnum(o, "slant", 0);
			B->cap = jnum(o, "cap_height", 0);
			B->asc = jnum(o, "ascender", 0);
			B->desc = jnum(o, "descender", 0);
			B->ink = jnum(o, "ink_width", 0);
			B->letter = jnum(o, "letter_spacing", 0);
			B->word = jnum(o, "word_spacing", 0);
			if (!(B->cap > 1 && B->asc > 1 && B->desc < 0 && B->ink > 0 && B->letter > 0 && B->word > B->letter &&
			      fabs(B->slant) < 1))
				ok = 0;
			json_object_put(o);
			if (!ok) break;
			continue;
		}
		if (B->n == ccap) {
			ccap = ccap ? ccap * 2 : 128;
			struct hw_base_char *x = realloc(B->c, ccap * sizeof *x);
			if (!x) { json_object_put(o); ok = 0; break; }
			B->c = x;
		}
		struct hw_base_char *c = &B->c[B->n];
		memset(c, 0, sizeof *c);
		if (char_in(o, c) == 0) {
			B->n++;
		} else {
			for (size_t j = 0; j < c->nstrokes; j++) { free(c->s[j].x); free(c->s[j].y); }
			free(c->s);
			fprintf(stderr, "glassine-handwriting: %s: a character isn't valid, left out\n", path);
		}
		json_object_put(o);
	}
	free(line);
	fclose(f);
	if (!ok || first || !B->n) {
		if (ok) fprintf(stderr, "glassine-handwriting: %s: no base font in it\n", path);
		else fprintf(stderr, "glassine-handwriting: %s: not a base font of this version\n", path);
		base_free(B);
		return -1;
	}
	qsort(B->c, B->n, sizeof *B->c, cmp_char);
	/* one line per character: the first of a code point kept */
	size_t k = 0;
	for (size_t i = 0; i < B->n; i++) {
		if (k && B->c[k - 1].cp == B->c[i].cp) {
			for (size_t j = 0; j < B->c[i].nstrokes; j++) { free(B->c[i].s[j].x); free(B->c[i].s[j].y); }
			free(B->c[i].s);
			continue;
		}
		B->c[k++] = B->c[i];
	}
	B->n = k;
	snprintf(B->hash, sizeof B->hash, "%016llx", (unsigned long long)h);
	return 0;
}

const struct hw_base *hw_base_get(void)
{
	if (!base_state) {
		const char *p = getenv("GLASSINE_HANDWRITING_BASE");
		if (!p || !*p) p = HW_BASE_PATH;
		base_state = strcmp(p, "off") && !base_read(p, &base) ? 1 : -1;
	}
	return base_state == 1 ? &base : NULL;
}

void hw_base_defaults(struct hw_letters *V, const struct hw_base *B)
{
	if (!B) return;
	struct hw_metrics *m = &V->m;
	/* before the person's first usable sample (the model empty, or built from none: its
	 * metrics are the build's stand-ins): the base's own metrics and spacing */
	if (V->samples <= 0) { m->ink = 0; V->sp.letter = 0; }
	if (!(m->ink > 0)) {
		m->cap = B->cap; m->asc = B->asc; m->desc = B->desc; m->slant = B->slant; m->ink = B->ink;
		m->letter_gap = B->letter; m->word_gap = B->word;
	}
	if (!(m->cap > 1)) m->cap = B->cap;
	if (!(m->asc > 1)) m->asc = B->asc;
	if (!(m->desc < 0)) m->desc = B->desc;
	/* no spacing of its own: the base's */
	if (!(V->sp.letter > 0) || !(V->sp.word > V->sp.letter)) {
		V->sp.letter = B->letter;
		V->sp.word = B->word;
	}
	if (!(V->sp.run > 0)) V->sp.run = 1;
	if (!(V->sp.narrow > 0)) V->sp.narrow = 1;
}

static int in_font(const struct hw_letters *V, unsigned cp)
{
	for (size_t i = 0; i < V->n; i++) if (V->l[i].cp == cp) return V->l[i].covered && V->l[i].nvar;
	return 0;
}

/* capitals and digits keep their proportions (uniformly to the cap height); the rest keeps
 * its x-height and goes to the person's ascender and descender above and below it */
static int uniform(unsigned cp)
{
	return (cp >= 'A' && cp <= 'Z') || (cp >= '0' && cp <= '9') || (cp >= 0xC0 && cp <= 0xDE && cp != 0xD7);
}

int hw_base_glyphs(const struct hw_letters *V, const struct hw_base *B, struct hw_base_glyph **out, size_t *n)
{
	*out = NULL;
	*n = 0;
	if (!B) return 0;
	struct hw_base_glyph *g = calloc(B->n + 1, sizeof *g);
	if (!g) return -1;
	const struct hw_metrics *m = &V->m;
	double k = m->cap > 1 ? m->cap / B->cap : 1;
	double up = m->asc > 1 && B->asc > 1 ? (m->asc - 1) / (B->asc - 1) : 1;
	double down = m->desc < 0 && B->desc < 0 ? m->desc / B->desc : 1;
	size_t ng = 0;
	for (size_t i = 0; i < B->n; i++) {
		const struct hw_base_char *c = &B->c[i];
		if (in_font(V, c->cp)) continue;
		struct hw_base_glyph *o = &g[ng];
		o->cp = c->cp;
		struct hw_variant *v = &o->v;
		v->s = calloc(c->nstrokes, sizeof *v->s);
		if (!v->s) { hw_base_glyphs_free(g, ng); return -1; }
		v->nstrokes = c->nstrokes;
		ng++;
		double lo = INFINITY, hi = -INFINITY;
		for (size_t j = 0; j < c->nstrokes; j++) {
			size_t np = c->s[j].n;
			v->s[j].x = malloc(np * sizeof(double));
			v->s[j].y = malloc(np * sizeof(double));
			if (!v->s[j].x || !v->s[j].y) { hw_base_glyphs_free(g, ng); return -1; }
			v->s[j].n = np;
			for (size_t p = 0; p < np; p++) {
				double x = c->s[j].x[p], y = c->s[j].y[p];
				if (uniform(c->cp)) { x *= k; y *= k; }
				else if (y > 1) y = 1 + (y - 1) * up;
				else if (y < 0) y *= down;
				x += m->slant * y;
				v->s[j].x[p] = x;
				v->s[j].y[p] = y;
				if (x < lo) lo = x;
				if (x > hi) hi = x;
			}
		}
		struct hw_mid md = { 0, 0 };
		hw_profile_clear(&v->prof);
		for (size_t j = 0; j < v->nstrokes; j++)
			for (size_t p = 0; p < v->s[j].n; p++) {
				v->s[j].x[p] -= lo;
				size_t p0 = p ? p - 1 : 0;
				hw_profile_seg(&v->prof, v->s[j].x[p0], v->s[j].y[p0], v->s[j].x[p], v->s[j].y[p]);
				hw_mid_seg(&md, v->s[j].x[p0], v->s[j].y[p0], v->s[j].x[p], v->s[j].y[p]);
			}
		v->width = hi - lo;
		v->mid = hw_mid_of(&md);
		v->weight = 0;
		v->members = 0;
		v->use = 1;
		v->lc = hw_spacing_side(&V->sp, &v->prof, 0);
		v->rc = hw_spacing_side(&V->sp, &v->prof, 1);
	}
	*out = g;
	*n = ng;
	return 0;
}

void hw_base_glyphs_free(struct hw_base_glyph *g, size_t n)
{
	for (size_t i = 0; g && i < n; i++) {
		for (size_t j = 0; j < g[i].v.nstrokes; j++) { free(g[i].v.s[j].x); free(g[i].v.s[j].y); }
		free(g[i].v.s);
	}
	free(g);
}
