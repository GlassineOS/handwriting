// SPDX-License-Identifier: GPL-2.0-or-later
// Sample format version 1 (docs/samples.md), read and written with json-c.
#include <json-c/json.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sample.h"

void hw_ink_free(struct hw_ink *k)
{
	free(k->x); free(k->y); free(k->t); free(k->p); free(k->tx); free(k->ty);
	memset(k, 0, sizeof *k);
}

int hw_ink_add(struct hw_ink *k, double x, double y, double t, double p)
{
	return hw_ink_add_tilt(k, x, y, t, p, NAN, NAN);
}

int hw_ink_add_tilt(struct hw_ink *k, double x, double y, double t, double p, double tx, double ty)
{
	if (k->n == k->cap) {
		size_t c = k->cap ? k->cap * 2 : 64;
		double *a = realloc(k->x, c * sizeof *a); if (!a) return -1; k->x = a;
		a = realloc(k->y, c * sizeof *a); if (!a) return -1; k->y = a;
		a = realloc(k->t, c * sizeof *a); if (!a) return -1; k->t = a;
		a = realloc(k->p, c * sizeof *a); if (!a) return -1; k->p = a;
		a = realloc(k->tx, c * sizeof *a); if (!a) return -1; k->tx = a;
		a = realloc(k->ty, c * sizeof *a); if (!a) return -1; k->ty = a;
		k->cap = c;
	}
	k->x[k->n] = x; k->y[k->n] = y; k->t[k->n] = t; k->p[k->n] = p;
	k->tx[k->n] = tx; k->ty[k->n] = ty;
	k->n++;
	return 0;
}

void hw_sample_free(struct hw_sample *s)
{
	free(s->text);
	for (size_t i = 0; i < s->nstrokes; i++) hw_ink_free(&s->strokes[i]);
	free(s->strokes);
	for (size_t i = 0; i < s->nspans; i++) free(s->spans[i]);
	free(s->spans);
	for (int i = 0; i < s->reading.n; i++) free(s->reading.text[i]);
	memset(s, 0, sizeof *s);
}

size_t hw_utf8_decode(const char *str, unsigned *cp, size_t max)
{
	const unsigned char *p = (const unsigned char *)str;
	size_t n = 0;
	while (*p) {
		unsigned c = *p++;
		int extra = c < 0x80 ? 0 : (c >> 5) == 6 ? 1 : (c >> 4) == 14 ? 2 : (c >> 3) == 30 ? 3 : 0;
		if (extra) c &= 0x3f >> extra;
		for (int i = 0; i < extra && (*p & 0xc0) == 0x80; i++) c = c << 6 | (*p++ & 0x3f);
		if (cp && n < max) cp[n] = c;
		n++;
	}
	return n;
}

size_t hw_utf8_encode(unsigned c, char *o)
{
	if (c < 0x80) { o[0] = (char)c; return 1; }
	if (c < 0x800) { o[0] = (char)(0xc0 | c >> 6); o[1] = (char)(0x80 | (c & 0x3f)); return 2; }
	if (c < 0x10000) {
		o[0] = (char)(0xe0 | c >> 12); o[1] = (char)(0x80 | (c >> 6 & 0x3f)); o[2] = (char)(0x80 | (c & 0x3f));
		return 3;
	}
	o[0] = (char)(0xf0 | c >> 18); o[1] = (char)(0x80 | (c >> 12 & 0x3f));
	o[2] = (char)(0x80 | (c >> 6 & 0x3f)); o[3] = (char)(0x80 | (c & 0x3f));
	return 4;
}

static int num(json_object *o, double *v)
{
	if (!json_object_is_type(o, json_type_double) && !json_object_is_type(o, json_type_int)) return -1;
	*v = json_object_get_double(o);
	return isfinite(*v) ? 0 : -1;
}

static int str(json_object *obj, const char *key, char *out, size_t len, int required)
{
	json_object *o;
	if (!json_object_object_get_ex(obj, key, &o)) { if (required) return -1; out[0] = 0; return 0; }
	if (!json_object_is_type(o, json_type_string)) return -1;
	const char *s = json_object_get_string(o);
	if (strlen(s) >= len) return -1;
	strcpy(out, s);
	return 0;
}

int hw_written_ok(const char *w)
{
	return !*w || !strcmp(w, "careful") || !strcmp(w, "quick");
}

int hw_pair_ok(const char *p)
{
	if (!*p) return 1;
	size_t n = 0;
	for (; p[n]; n++) if (!((p[n] >= '0' && p[n] <= '9') || (p[n] >= 'a' && p[n] <= 'f'))) return 0;
	return n == 16;
}

#define FAIL(...) do { snprintf(err, errlen, __VA_ARGS__); goto bad; } while (0)

int hw_sample_parse(const char *line, struct hw_sample *s, char *err, size_t errlen)
{
	memset(s, 0, sizeof *s);
	json_object *o = json_tokener_parse(line), *v, *st, *sp;
	if (!o || !json_object_is_type(o, json_type_object)) FAIL("not a JSON object");
	if (!json_object_object_get_ex(o, "version", &v) || !json_object_is_type(v, json_type_int))
		FAIL("no version");
	if (json_object_get_int(v) != 1) FAIL("version %d", json_object_get_int(v));
	if (!json_object_object_get_ex(o, "text", &v) || !json_object_is_type(v, json_type_string)) FAIL("no text");
	s->text = strdup(json_object_get_string(v));
	if (!s->text) FAIL("out of memory");
	if (str(o, "lang", s->lang, sizeof s->lang, 1)) FAIL("bad lang");
	if (str(o, "source", s->source, sizeof s->source, 1)) FAIL("bad source");
	if (str(o, "spans_source", s->spans_source, sizeof s->spans_source, 1)) FAIL("bad spans_source");
	if (str(o, "licence", s->licence, sizeof s->licence, 0)) FAIL("bad licence");
	if (str(o, "donor", s->donor, sizeof s->donor, 0)) FAIL("bad donor");
	if (str(o, "written", s->written, sizeof s->written, 0) || !hw_written_ok(s->written)) FAIL("bad written");
	if (str(o, "pair", s->pair, sizeof s->pair, 0) || !hw_pair_ok(s->pair)) FAIL("bad pair");
	if (!json_object_object_get_ex(o, "units", &v) || !json_object_is_type(v, json_type_object)) FAIL("no units");
	if (str(v, "xy", s->units.xy, sizeof s->units.xy, 1)) FAIL("bad units");
	json_object *u;
	if (json_object_object_get_ex(v, "per_mm", &u) && num(u, &s->units.per_mm)) FAIL("bad per_mm");
	if (json_object_object_get_ex(v, "rate", &u) && num(u, &s->units.rate)) FAIL("bad rate");
	if (!json_object_object_get_ex(o, "strokes", &st) || !json_object_is_type(st, json_type_array)) FAIL("no strokes");
	size_t ns = json_object_array_length(st);
	s->strokes = calloc(ns + 1, sizeof *s->strokes);
	if (!s->strokes) FAIL("out of memory");
	s->nstrokes = ns;
	int width = -1;
	for (size_t i = 0; i < ns; i++) {
		json_object *q = json_object_array_get_idx(st, i);
		if (!json_object_is_type(q, json_type_array)) FAIL("bad stroke");
		size_t np = json_object_array_length(q);
		for (size_t k = 0; k < np; k++) {
			json_object *pt = json_object_array_get_idx(q, k);
			if (!json_object_is_type(pt, json_type_array)) FAIL("bad point");
			int w = (int)json_object_array_length(pt);
			if (w != 3 && w != 4 && w != 6) FAIL("a point has 3, 4 or 6 numbers");
			if (width < 0) width = w;
			if (w != width) FAIL("points of different lengths");
			double c[6];
			for (int j = 0; j < w; j++) if (num(json_object_array_get_idx(pt, (size_t)j), &c[j])) FAIL("bad number");
			if (hw_ink_add_tilt(&s->strokes[i], c[0], c[1], c[2], w >= 4 ? c[3] : NAN, w == 6 ? c[4] : NAN,
					    w == 6 ? c[5] : NAN))
				FAIL("out of memory");
		}
	}
	s->pressure = width >= 4;
	s->tilt = width == 6;
	if (json_object_object_get_ex(o, "area", &v)) {
		json_object *b, *x;
		if (!json_object_is_type(v, json_type_object) || !json_object_object_get_ex(v, "box", &b) ||
		    !json_object_is_type(b, json_type_array) || json_object_array_length(b) != 4)
			FAIL("bad area");
		for (size_t k = 0; k < 4; k++) if (num(json_object_array_get_idx(b, k), &s->area.box[k])) FAIL("bad area");
		if (json_object_object_get_ex(v, "line", &x) && num(x, &s->area.line)) FAIL("bad area");
		if (json_object_object_get_ex(v, "orientation", &x)) {
			if (!json_object_is_type(x, json_type_int)) FAIL("bad area");
			s->area.orientation = json_object_get_int(x);
			if (s->area.orientation % 90 || s->area.orientation < 0 || s->area.orientation >= 360) FAIL("bad area");
		}
		s->area.set = 1;
	}
	if (json_object_object_get_ex(o, "reading", &v)) {
		if (!json_object_is_type(v, json_type_array) || json_object_array_length(v) > 8) FAIL("bad reading");
		for (size_t k = 0; k < json_object_array_length(v); k++) {
			json_object *c = json_object_array_get_idx(v, k), *t, *sc;
			double d;
			if (!json_object_is_type(c, json_type_object) || !json_object_object_get_ex(c, "text", &t) ||
			    !json_object_is_type(t, json_type_string) || !json_object_object_get_ex(c, "score", &sc) || num(sc, &d))
				FAIL("bad reading");
			s->reading.text[s->reading.n] = strdup(json_object_get_string(t));
			if (!s->reading.text[s->reading.n]) FAIL("out of memory");
			s->reading.score[s->reading.n++] = d;
		}
	}
	if (json_object_object_get_ex(o, "pick", &v)) {
		json_object *x, *y;
		if (!json_object_is_type(v, json_type_object) || !json_object_object_get_ex(v, "rank", &x) ||
		    !json_object_is_type(x, json_type_int) || json_object_get_int(x) < 1 ||
		    !json_object_object_get_ex(v, "from", &y) || !json_object_is_type(y, json_type_string) ||
		    (strcmp(json_object_get_string(y), "ink") && strcmp(json_object_get_string(y), "beam") &&
		     strcmp(json_object_get_string(y), "words") && strcmp(json_object_get_string(y), "guided")))
			FAIL("bad pick");
		s->pick_rank = json_object_get_int(x);
		snprintf(s->pick_from, sizeof s->pick_from, "%s", json_object_get_string(y));
	}
	if (!json_object_object_get_ex(o, "spans", &sp) || !json_object_is_type(sp, json_type_array)) FAIL("no spans");
	size_t nc = hw_utf8_decode(s->text, NULL, 0);
	if (json_object_array_length(sp) != nc) FAIL("spans don't match the text");
	s->spans = calloc(nc + 1, sizeof *s->spans);
	if (!s->spans) FAIL("out of memory");
	s->nspans = nc;
	for (size_t i = 0; i < nc; i++) {
		json_object *a = json_object_array_get_idx(sp, i);
		if (!json_object_is_type(a, json_type_array)) FAIL("bad span");
		size_t n = json_object_array_length(a);
		s->spans[i] = malloc((n + 1) * sizeof **s->spans);
		if (!s->spans[i]) FAIL("out of memory");
		for (size_t k = 0; k < n; k++) {
			json_object *x = json_object_array_get_idx(a, k);
			if (!json_object_is_type(x, json_type_int)) FAIL("bad span");
			int64_t id = json_object_get_int64(x);
			if (id < 0 || (size_t)id >= ns) FAIL("span names a stroke that isn't there");
			s->spans[i][k] = (int)id;
		}
		s->spans[i][n] = -1;
	}
	json_object_put(o);
	return 0;
bad:
	if (o) json_object_put(o);
	hw_sample_free(s);
	return -1;
}

/* a number as JSON: the shortest form that reads back as the same double, so points stay
 * exactly as the pen reported them (%.17g would be noisy) */
static json_object *jnum(double v)
{
	char b[40];
	for (int p = 9; p <= 17; p++) {
		snprintf(b, sizeof b, "%.*g", p, v);
		if (strtod(b, NULL) == v) break;
	}
	return json_object_new_double_s(v, b);
}

char *hw_sample_format(const struct hw_sample *s)
{
	json_object *o = json_object_new_object();
	json_object_object_add(o, "version", json_object_new_int(1));
	json_object_object_add(o, "text", json_object_new_string(s->text));
	json_object_object_add(o, "lang", json_object_new_string(s->lang));
	json_object_object_add(o, "source", json_object_new_string(s->source));
	json_object_object_add(o, "spans_source", json_object_new_string(s->spans_source));
	if (s->licence[0]) json_object_object_add(o, "licence", json_object_new_string(s->licence));
	if (s->donor[0]) json_object_object_add(o, "donor", json_object_new_string(s->donor));
	if (s->written[0]) json_object_object_add(o, "written", json_object_new_string(s->written));
	if (s->pair[0]) json_object_object_add(o, "pair", json_object_new_string(s->pair));
	json_object *u = json_object_new_object();
	json_object_object_add(u, "xy", json_object_new_string(s->units.xy));
	if (s->units.per_mm > 0) json_object_object_add(u, "per_mm", jnum(s->units.per_mm));
	if (s->units.rate > 0) json_object_object_add(u, "rate", jnum(s->units.rate));
	json_object_object_add(o, "units", u);
	json_object *st = json_object_new_array();
	for (size_t i = 0; i < s->nstrokes; i++) {
		json_object *q = json_object_new_array();
		const struct hw_ink *k = &s->strokes[i];
		for (size_t j = 0; j < k->n; j++) {
			json_object *pt = json_object_new_array();
			json_object_array_add(pt, jnum(k->x[j]));
			json_object_array_add(pt, jnum(k->y[j]));
			json_object_array_add(pt, jnum(k->t[j]));
			if (s->pressure || s->tilt) json_object_array_add(pt, jnum(isnan(k->p[j]) ? 0 : k->p[j]));
			if (s->tilt) {
				json_object_array_add(pt, jnum(isnan(k->tx[j]) ? 0 : k->tx[j]));
				json_object_array_add(pt, jnum(isnan(k->ty[j]) ? 0 : k->ty[j]));
			}
			json_object_array_add(q, pt);
		}
		json_object_array_add(st, q);
	}
	json_object_object_add(o, "strokes", st);
	json_object *sp = json_object_new_array();
	for (size_t i = 0; i < s->nspans; i++) {
		json_object *a = json_object_new_array();
		for (int *p = s->spans[i]; p && *p >= 0; p++) json_object_array_add(a, json_object_new_int(*p));
		json_object_array_add(sp, a);
	}
	json_object_object_add(o, "spans", sp);
	if (s->area.set) {
		json_object *a = json_object_new_object(), *b = json_object_new_array();
		for (int k = 0; k < 4; k++) json_object_array_add(b, jnum(s->area.box[k]));
		json_object_object_add(a, "box", b);
		if (s->area.line > 0) json_object_object_add(a, "line", jnum(s->area.line));
		json_object_object_add(a, "orientation", json_object_new_int(s->area.orientation));
		json_object_object_add(o, "area", a);
	}
	if (s->reading.n) {
		json_object *a = json_object_new_array();
		for (int k = 0; k < s->reading.n; k++) {
			json_object *c = json_object_new_object();
			json_object_object_add(c, "text", json_object_new_string(s->reading.text[k]));
			json_object_object_add(c, "score", jnum(s->reading.score[k]));
			json_object_array_add(a, c);
		}
		json_object_object_add(o, "reading", a);
	}
	if (s->pick_rank > 0 && *s->pick_from) {
		json_object *k = json_object_new_object();
		json_object_object_add(k, "rank", json_object_new_int(s->pick_rank));
		json_object_object_add(k, "from", json_object_new_string(s->pick_from));
		json_object_object_add(o, "pick", k);
	}
	char *r = strdup(json_object_to_json_string_ext(o, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE));
	json_object_put(o);
	return r;
}

void hw_sample_strokes(const struct hw_sample *s, struct hw_stroke *out)
{
	for (size_t i = 0; i < s->nstrokes; i++)
		out[i] = (struct hw_stroke){ s->strokes[i].n, s->strokes[i].x, s->strokes[i].y, s->strokes[i].t };
}
