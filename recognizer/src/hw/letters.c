// SPDX-License-Identifier: GPL-2.0-or-later
// The letter model (see letters.h and docs/recognizer.md, "The letter model").
#include <errno.h>
#include <fcntl.h>
#include <json-c/json.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include "adapt.h"
#include "base.h"
#include "cut.h"
#include "letters.h"
#include "sample.h"
#include "neaten.h"
#include "store.h"
#include "words.h"

/* ---------------------------------------------------------------- small helpers */

static int avg_mean(void);

static uint64_t fnv(uint64_t h, const void *p, size_t n)
{
	const unsigned char *b = p;
	for (size_t i = 0; i < n; i++) h = (h ^ b[i]) * 1099511628211ULL;
	return h;
}
#define FNV0 1469598103934665603ULL

static int cmp_double(const void *a, const void *b)
{
	double x = *(const double *)a, y = *(const double *)b;
	return x < y ? -1 : x > y;
}

/* the median of v (sorted in place); def when empty */
static double median(double *v, size_t n, double def)
{
	if (!n) return def;
	qsort(v, n, sizeof *v, cmp_double);
	return n % 2 ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

struct dvec { double *v; size_t n, cap; };
static void dpush(struct dvec *d, double x)
{
	if (d->n == d->cap) {
		size_t c = d->cap ? d->cap * 2 : 64;
		double *v = realloc(d->v, c * sizeof *v);
		if (!v) return;
		d->v = v; d->cap = c;
	}
	d->v[d->n++] = x;
}

static double clampd(double x, double a, double b) { return x < a ? a : x > b ? b : x; }

/* character classes for the line metrics (Latin; other scripts get the fallbacks) */
static int is_x(unsigned c) { return c < 128 && strchr("acemnorsuvwxz", (int)c) && c; }
static int is_asc(unsigned c) { return c < 128 && strchr("bdhklt", (int)c) && c; }
static int is_desc(unsigned c) { return c < 128 && strchr("gjpqy", (int)c) && c; }
static int is_cap(unsigned c) { return c >= 'A' && c <= 'Z' && c != 'Q' && c != 'J'; }
static int on_base(unsigned c)
{
	return (c >= 'a' && c <= 'z' && !is_desc(c) && c != 'f') || is_cap(c) || (c >= '0' && c <= '9');
}

/* ---------------------------------------------------------------- free / format / parse */

static void variant_free(struct hw_variant *v)
{
	for (size_t j = 0; j < v->nstrokes; j++) {
		free(v->s[j].x); free(v->s[j].y);
		if (v->best) { free(v->best[j].x); free(v->best[j].y); }
	}
	free(v->s); free(v->best);
	free(v->ids);
	free(v->pres); free(v->speed);
	memset(v, 0, sizeof *v);
}

void hw_letters_free(struct hw_letters *L)
{
	for (size_t i = 0; i < L->n; i++)
		for (int k = 0; k < L->l[i].nvar; k++) variant_free(&L->l[i].var[k]);
	free(L->l);
	for (size_t i = 0; i < L->njoined; i++) variant_free(&L->joined[i].v);
	free(L->joined);
	free(L->jshape);
	free(L->jpair);
	hw_spacing_free(&L->sp);
	memset(L, 0, sizeof *L);
}

struct sbuf { char *s; size_t n, cap; };
static void sput(struct sbuf *b, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
#include <stdarg.h>
static void sput(struct sbuf *b, const char *fmt, ...)
{
	va_list ap;
	for (;;) {
		va_start(ap, fmt);
		int k = vsnprintf(b->s ? b->s + b->n : NULL, b->s ? b->cap - b->n : 0, fmt, ap);
		va_end(ap);
		if (k < 0) return;
		if (b->s && b->n + (size_t)k < b->cap) { b->n += (size_t)k; return; }
		size_t c = (b->cap + (size_t)k + 1) * 2;
		char *x = realloc(b->s, c);
		if (!x) return;
		b->s = x; b->cap = c;
	}
}

/* a number as the model file writes it: 4 decimals, no "-0" */
static void num(struct sbuf *b, double x)
{
	double r = round(x * 1e4) / 1e4;
	if (r == 0) r = 0;
	sput(b, "%.4f", r);
	/* trim trailing zeros */
	while (b->n && b->s[b->n - 1] == '0') b->n--;
	if (b->n && b->s[b->n - 1] == '.') b->n--;
	b->s[b->n] = 0;
}

/* the same, null for none */
static void numn(struct sbuf *b, double x)
{
	if (isfinite(x)) num(b, x); else sput(b, "null");
}

/* a number of the model file, NAN for null or none */
static double jdn(json_object *e, size_t i)
{
	json_object *x = json_object_array_length(e) > i ? json_object_array_get_idx(e, i) : NULL;
	return x && (json_object_is_type(x, json_type_double) || json_object_is_type(x, json_type_int)) ? json_object_get_double(x) : NAN;
}

/* a character as a JSON string */
static void char_out(struct sbuf *b, unsigned cp)
{
	char u[8];
	u[hw_utf8_encode(cp, u)] = 0;
	json_object *js = json_object_new_string(u);
	sput(b, "%s", json_object_to_json_string_ext(js, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE));
	json_object_put(js);
}

/* a variant's object in the model file (with_best 0: without its best version) */
static void variant_out(struct sbuf *b, const struct hw_variant *x, int with_best)
{
	sput(b, "{\"width\":");
	num(b, x->width);
	sput(b, ",\"weight\":");
	num(b, x->weight);
	sput(b, ",\"members\":%d,\"use\":%d,\"lc\":%d,\"rc\":%d", x->members, x->use, x->lc, x->rc);
	if (x->out) sput(b, ",\"left_out\":true");
	if (x->favourite) sput(b, ",\"favourite\":true");
	sput(b, ",\"ids\":[");
	for (size_t q = 0; q < x->nids; q++) sput(b, "%s\"%s\"", q ? "," : "", x->ids[q]);
	sput(b, "]");
	for (int side = 0; side < 2; side++) {
		sput(b, side ? ",\"right\":[" : ",\"left\":[");
		for (int k = 0; k < HW_PROF_BANDS; k++) {
			double y = side ? x->prof.r[k] : x->prof.l[k];
			if (k) sput(b, ",");
			if (isnan(y)) sput(b, "null"); else num(b, y);
		}
		sput(b, "]");
	}
	sput(b, ",\"middle\":");
	numn(b, x->mid);
	for (int bk = 0; bk < 2; bk++) {
		const struct hw_lstroke *ts = bk ? x->best : x->s;
		if (!ts || (bk && !with_best)) continue;
		sput(b, bk ? ",\"best\":[" : ",\"strokes\":[");
		for (size_t j = 0; j < x->nstrokes; j++) {
			sput(b, "%s[", j ? "," : "");
			for (size_t p = 0; p < ts[j].n; p++) {
				sput(b, "%s[", p ? "," : "");
				num(b, ts[j].x[p]);
				sput(b, ",");
				num(b, ts[j].y[p]);
				sput(b, "]");
			}
			sput(b, "]");
		}
		sput(b, "]");
	}
	/* how it was written (FR-006): the profiles, per stroke */
	for (int pk = 0; pk < 2; pk++) {
		const double *pv = pk ? x->speed : x->pres;
		if (!pv) continue;
		sput(b, pk ? ",\"speed\":[" : ",\"pressure\":[");
		for (size_t j = 0; j < x->nstrokes; j++) {
			sput(b, "%s[", j ? "," : "");
			for (int q = 0; q < HW_LETTERS_PROFILE; q++) { if (q) sput(b, ","); num(b, pv[j * HW_LETTERS_PROFILE + (size_t)q]); }
			sput(b, "]");
		}
		sput(b, "]");
	}
	/* its entry and exit (FR-007), with the model's other inputs to neatening, not the font's */
	for (int e = 0; with_best && e < 2; e++) {
		const struct hw_stub *t = e ? &x->exit : &x->entry;
		if (t->n <= 0) continue;
		sput(b, ",\"%s\":{\"stroke\":%d,\"n\":%d,\"points\":[", e ? "exit" : "entry", t->stroke, t->n);
		for (int q = 0; q < HW_STUB_N; q++) {
			sput(b, "%s[", q ? "," : "");
			num(b, t->x[q]);
			sput(b, ",");
			num(b, t->y[q]);
			sput(b, "]");
		}
		sput(b, "]}");
	}
	sput(b, "}");
}

/* with_best 0: without the best versions (the font is made without them) */
/* a Unicode scalar value a character of the model can be */
static int cp_ok(unsigned c) { return c && c <= 0x10ffff && (c < 0xd800 || c > 0xdfff); }

/* every character the model names is one (FR-006: the model is UTF-8 text) */
static int model_chars_ok(const struct hw_letters *L)
{
	const struct hw_spacing *sp = &L->sp;
	for (size_t i = 0; i < sp->npairs; i++) if (!cp_ok(sp->pairs[i].a) || !cp_ok(sp->pairs[i].b)) return 0;
	for (size_t i = 0; i < sp->nreach; i++) if (!cp_ok(sp->reach[i].cp)) return 0;
	for (size_t i = 0; i < L->n; i++) {
		if (!cp_ok(L->l[i].cp)) return 0;
		for (int r = 0; r < L->l[i].nrules; r++) if (!cp_ok(L->l[i].rule[r].nb)) return 0;
	}
	for (size_t i = 0; i < L->njoined; i++) if (!cp_ok(L->joined[i].a) || !cp_ok(L->joined[i].b)) return 0;
	for (size_t i = 0; i < L->njpair; i++) if (!cp_ok(L->jpair[i].a) || !cp_ok(L->jpair[i].b)) return 0;
	for (size_t i = 0; i < L->njshape; i++)
		if ((L->jshape[i].a || L->jshape[i].b) && (!cp_ok(L->jshape[i].a) || !cp_ok(L->jshape[i].b))) return 0;
	return 1;
}

/* the model file's text; NULL when a character in it isn't one (it's never written) */
static char *format_model(const struct hw_letters *L, size_t *len, int with_best)
{
	if (len) *len = 0;
	if (!model_chars_ok(L)) {
		fprintf(stderr, "glassine-handwriting: letters: a character in the model isn't one; not written\n");
		return NULL;
	}
	struct sbuf b = { 0 };
	sput(&b, "{\"format\":\"%s\",\"version\":%d,\"inputs\":\"%s\",\"samples\":%ld,\"built\":%lld}\n",
	     HW_LETTERS_FORMAT, HW_LETTERS_VERSION, L->inputs, L->samples, L->built);
	const struct hw_metrics *m = &L->m;
	const char *names[] = { "cap_height", "ascender", "descender", "slant", "letter_spacing", "word_spacing", "ink_width",
				"x_height_per_line", "base_below_guide", "x_height_mm", "pressure_median", "speed_median" };
	double vals[] = { m->cap, m->asc, m->desc, m->slant, m->letter_gap, m->word_gap, m->ink, m->xh_line, m->base_guide,
			  m->xh_mm, m->pres_mid, m->speed_mid };
	sput(&b, "{\"metrics\":{\"x_height\":1");
	for (int i = 0; i < 12; i++) { sput(&b, ",\"%s\":", names[i]); num(&b, vals[i]); }
	sput(&b, "}}\n");
	/* the spacing */
	{
		const struct hw_spacing *sp = &L->sp;
		sput(&b, "{\"spacing\":{\"letter\":");
		num(&b, sp->letter);
		sput(&b, ",\"word\":");
		num(&b, sp->word);
		sput(&b, ",\"open\":");
		num(&b, sp->open);
		sput(&b, ",\"open0\":");
		num(&b, sp->open0);
		sput(&b, ",\"run\":");
		num(&b, sp->run);
		sput(&b, ",\"narrow\":");
		num(&b, sp->narrow);
		sput(&b, ",\"pairs\":[");
		for (size_t i = 0; i < sp->npairs; i++) {
			char u[16];
			size_t k = hw_utf8_encode(sp->pairs[i].a, u);
			u[k] = 0;
			json_object *ja = json_object_new_string(u);
			k = hw_utf8_encode(sp->pairs[i].b, u);
			u[k] = 0;
			json_object *jb = json_object_new_string(u);
			sput(&b, "%s[%s,%s,", i ? "," : "", json_object_to_json_string_ext(ja, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE),
			     json_object_to_json_string_ext(jb, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE));
			json_object_put(ja); json_object_put(jb);
			num(&b, sp->pairs[i].gap);
			sput(&b, ",%d,", sp->pairs[i].n);
			numn(&b, sp->pairs[i].mid);
			sput(&b, "]");
		}
		sput(&b, "],\"sides\":[");
		for (size_t i = 0; i < sp->nsides; i++) {
			sput(&b, "%s[%d,%d,", i ? "," : "", sp->sides[i].rc, sp->sides[i].lc);
			num(&b, sp->sides[i].gap);
			sput(&b, ",%d]", sp->sides[i].n);
		}
		sput(&b, "],\"reach\":[");
		for (size_t i = 0; i < sp->nreach; i++) {
			char u[16];
			size_t k = hw_utf8_encode(sp->reach[i].cp, u);
			u[k] = 0;
			json_object *jc = json_object_new_string(u);
			sput(&b, "%s[%s,", i ? "," : "", json_object_to_json_string_ext(jc, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE));
			json_object_put(jc);
			numn(&b, sp->reach[i].right);
			sput(&b, ",");
			numn(&b, sp->reach[i].left);
			sput(&b, ",%d,%d]", sp->reach[i].nr, sp->reach[i].nl);
		}
		for (int side = 0; side < 2; side++) {
			sput(&b, side ? "],\"right_shapes\":[" : "],\"left_shapes\":[");
			for (int c = 0; c < (side ? sp->nr : sp->nl); c++) {
				sput(&b, "%s[", c ? "," : "");
				for (int k = 0; k < HW_PROF_BANDS; k++) { if (k) sput(&b, ","); num(&b, side ? sp->rshape[c][k] : sp->lshape[c][k]); }
				sput(&b, "]");
			}
		}
		sput(&b, "]}}\n");
	}
	for (size_t i = 0; i < L->n; i++) {
		const struct hw_letter *l = &L->l[i];
		char u[8];
		size_t k = hw_utf8_encode(l->cp, u);
		u[k] = 0;
		json_object *js = json_object_new_string(u);
		sput(&b, "{\"char\":%s,\"samples\":%d,\"covered\":%s,", json_object_to_json_string_ext(js, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE),
		     l->samples, l->covered ? "true" : "false");
		if (l->marks[0]) sput(&b, "\"marks\":\"%s\",", l->marks);
		sput(&b, "\"variants\":[");
		json_object_put(js);
		for (int v = 0; v < l->nvar; v++) {
			if (v) sput(&b, ",");
			variant_out(&b, &l->var[v], with_best);
		}
		sput(&b, "]");
		if (l->nvar) {
			sput(&b, ",\"slots\":[");
			for (int k = 0; k < HW_LETTERS_SLOTS; k++) sput(&b, "%s%d", k ? "," : "", l->slot[k]);
			sput(&b, "]");
		}
		/* the neighbour rules (FR-038) */
		if (l->nrules) {
			sput(&b, ",\"rules\":[");
			for (int r = 0; r < l->nrules; r++) {
				const struct hw_rule *ru = &l->rule[r];
				sput(&b, "%s{\"%s\":", r ? "," : "", ru->side ? "next" : "prev");
				char_out(&b, ru->nb);
				sput(&b, ",\"variant\":%d,\"n\":%d,\"same\":%d,\"hits\":", ru->var, ru->n, ru->same);
				num(&b, ru->hits);
				sput(&b, ",\"cycle\":");
				num(&b, ru->cycle);
				sput(&b, "}");
			}
			sput(&b, "]");
		}
		sput(&b, "}\n");
	}
	/* the joined pairs (FR-039) */
	for (size_t i = 0; i < L->njoined; i++) {
		const struct hw_joined *j = &L->joined[i];
		char u[16];
		size_t k = hw_utf8_encode(j->a, u);
		u[k + hw_utf8_encode(j->b, u + k)] = 0;
		json_object *js = json_object_new_string(u);
		sput(&b, "{\"joined\":%s,\"samples\":%d,\"written\":%d,\"of\":%d,\"variant\":",
		     json_object_to_json_string_ext(js, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE), j->samples, j->joined, j->of);
		json_object_put(js);
		variant_out(&b, &j->v, 0);
		sput(&b, "}\n");
	}
	/* the joins (FR-044), only with a shape */
	if (L->njshape) {
		sput(&b, "{\"joins\":{\"pairs\":[");
		for (size_t i = 0; i < L->njpair; i++) {
			sput(&b, "%s[", i ? "," : "");
			char_out(&b, L->jpair[i].a);
			sput(&b, ",");
			char_out(&b, L->jpair[i].b);
			sput(&b, ",%d,%d]", L->jpair[i].seen, L->jpair[i].joined);
		}
		sput(&b, "],\"kinds\":[");
		int first = 1;
		for (int e = 0; e < HW_JOIN_EXITS; e++)
			for (int c = 0; c < HW_JOIN_ENTRIES; c++) {
				if (!L->jkind[e][c].seen) continue;
				sput(&b, "%s[%d,%d,%d,%d]", first ? "" : ",", e, c, L->jkind[e][c].seen, L->jkind[e][c].joined);
				first = 0;
			}
		sput(&b, "],\"shapes\":[");
		for (size_t i = 0; i < L->njshape; i++) {
			const struct hw_jshape *t = &L->jshape[i];
			if (i) sput(&b, ",");
			if (t->a || t->b) {
				char u[16];
				size_t k = hw_utf8_encode(t->a, u);
				u[k + hw_utf8_encode(t->b, u + k)] = 0;
				json_object *js = json_object_new_string(u);
				sput(&b, "{\"pair\":%s", json_object_to_json_string_ext(js, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE));
				json_object_put(js);
			} else sput(&b, "{\"kind\":[%d,%d]", t->ec, t->nc);
			sput(&b, ",\"n\":%d,\"middle\":", t->n);
			num(&b, t->mid);
			sput(&b, ",\"points\":[");
			for (int q = 0; q < HW_JOIN_N; q++) {
				sput(&b, "%s[", q ? "," : "");
				num(&b, t->x[q]);
				sput(&b, ",");
				num(&b, t->y[q]);
				sput(&b, "]");
			}
			sput(&b, "]}");
		}
		sput(&b, "]}}\n");
	}
	if (len) *len = b.n;
	return b.s;
}

char *hw_letters_format(const struct hw_letters *L, size_t *len) { return format_model(L, len, 1); }

static double jnum(json_object *o, const char *k, double def)
{
	json_object *v;
	return json_object_object_get_ex(o, k, &v) ? json_object_get_double(v) : def;
}

/* a variant's profiles and middle, from its strokes */
static void variant_measure(struct hw_variant *v)
{
	struct hw_mid md = { 0, 0 };
	hw_profile_clear(&v->prof);
	for (size_t j = 0; v->s && j < v->nstrokes; j++) {
		const struct hw_lstroke *t = &v->s[j];
		for (size_t i = 0; t->x && t->y && i < t->n; i++) {
			size_t i0 = i ? i - 1 : 0;
			hw_profile_seg(&v->prof, t->x[i0], t->y[i0], t->x[i], t->y[i]);
			hw_mid_seg(&md, t->x[i0], t->y[i0], t->x[i], t->y[i]);
		}
	}
	v->mid = round(hw_mid_of(&md) * 1e4) / 1e4;
}

/* a variant from its object in the model file */
static void variant_in(json_object *x, struct hw_variant *w)
{
	json_object *st;
	w->width = jnum(x, "width", 0);
	w->weight = jnum(x, "weight", 0);
	w->members = (int)jnum(x, "members", 0);
	w->use = (int)jnum(x, "use", 1);
	w->lc = (int)jnum(x, "lc", -1);
	w->rc = (int)jnum(x, "rc", -1);
	json_object *f;
	w->out = json_object_object_get_ex(x, "left_out", &f) && json_object_get_boolean(f);
	w->favourite = json_object_object_get_ex(x, "favourite", &f) && json_object_get_boolean(f);
	if (json_object_object_get_ex(x, "ids", &f) && json_object_is_type(f, json_type_array)) {
		size_t ni = json_object_array_length(f);
		w->ids = calloc(ni + 1, sizeof *w->ids);
		for (size_t q = 0; w->ids && q < ni; q++) {
			const char *id = json_object_get_string(json_object_array_get_idx(f, q));
			if (id && strlen(id) == 16) snprintf(w->ids[w->nids++], 17, "%s", id);
		}
	}
	if (!json_object_object_get_ex(x, "strokes", &st)) return;
	w->nstrokes = json_object_array_length(st);
	w->s = calloc(w->nstrokes + 1, sizeof *w->s);
	for (size_t j = 0; w->s && j < w->nstrokes; j++) {
		json_object *ps = json_object_array_get_idx(st, j);
		size_t np = json_object_array_length(ps);
		w->s[j].n = np;
		w->s[j].x = malloc((np + 1) * sizeof(double));
		w->s[j].y = malloc((np + 1) * sizeof(double));
		for (size_t p = 0; p < np && w->s[j].x && w->s[j].y; p++) {
			json_object *pt = json_object_array_get_idx(ps, p);
			w->s[j].x[p] = json_object_get_double(json_object_array_get_idx(pt, 0));
			w->s[j].y[p] = json_object_get_double(json_object_array_get_idx(pt, 1));
		}
	}
	/* the best version (FR-035): as many strokes and points as the variant */
	json_object *bs;
	if (w->s && json_object_object_get_ex(x, "best", &bs) && json_object_is_type(bs, json_type_array) &&
	    json_object_array_length(bs) == w->nstrokes) {
		int good = (w->best = calloc(w->nstrokes + 1, sizeof *w->best)) != NULL;
		for (size_t j = 0; good && j < w->nstrokes; j++) {
			json_object *ps = json_object_array_get_idx(bs, j);
			size_t np = json_object_is_type(ps, json_type_array) ? json_object_array_length(ps) : 0;
			struct hw_lstroke *t = &w->best[j];
			t->x = malloc((np + 1) * sizeof(double));
			t->y = malloc((np + 1) * sizeof(double));
			if (np != w->s[j].n || !t->x || !t->y) { good = 0; break; }
			t->n = np;
			for (size_t p = 0; p < np; p++) {
				json_object *pt = json_object_array_get_idx(ps, p);
				t->x[p] = json_object_get_double(json_object_array_get_idx(pt, 0));
				t->y[p] = json_object_get_double(json_object_array_get_idx(pt, 1));
			}
		}
		if (!good && w->best) {
			for (size_t j = 0; j < w->nstrokes; j++) { free(w->best[j].x); free(w->best[j].y); }
			free(w->best);
			w->best = NULL;
		}
	}
	/* how it was written: one profile of HW_LETTERS_PROFILE points per stroke */
	for (int pk = 0; pk < 2 && w->s; pk++) {
		json_object *pa;
		if (!json_object_object_get_ex(x, pk ? "speed" : "pressure", &pa) || !json_object_is_type(pa, json_type_array) ||
		    json_object_array_length(pa) != w->nstrokes) continue;
		double *pv = malloc((w->nstrokes + 1) * HW_LETTERS_PROFILE * sizeof *pv);
		int good = pv != NULL;
		for (size_t j = 0; good && j < w->nstrokes; j++) {
			json_object *e = json_object_array_get_idx(pa, j);
			if (!json_object_is_type(e, json_type_array) || json_object_array_length(e) != HW_LETTERS_PROFILE) { good = 0; break; }
			for (int q = 0; q < HW_LETTERS_PROFILE; q++)
				pv[j * HW_LETTERS_PROFILE + (size_t)q] = json_object_get_double(json_object_array_get_idx(e, (size_t)q));
		}
		if (!good) { free(pv); continue; }
		if (pk) w->speed = pv; else w->pres = pv;
	}
	/* its entry and exit: HW_STUB_N points, at a stroke it has */
	for (int e = 0; e < 2 && w->s; e++) {
		json_object *o, *f2, *pa;
		struct hw_stub *t = e ? &w->exit : &w->entry;
		if (!json_object_object_get_ex(x, e ? "exit" : "entry", &o) || !json_object_is_type(o, json_type_object) ||
		    !json_object_object_get_ex(o, "points", &pa) || !json_object_is_type(pa, json_type_array) ||
		    json_object_array_length(pa) != HW_STUB_N) continue;
		int stroke = json_object_object_get_ex(o, "stroke", &f2) ? json_object_get_int(f2) : -1;
		int n = json_object_object_get_ex(o, "n", &f2) ? json_object_get_int(f2) : 0;
		if (stroke < 0 || (size_t)stroke >= w->nstrokes || n <= 0) continue;
		int good = 1;
		for (int q = 0; q < HW_STUB_N && good; q++) {
			json_object *pt = json_object_array_get_idx(pa, (size_t)q);
			good = json_object_is_type(pt, json_type_array) && json_object_array_length(pt) == 2;
			if (good) { t->x[q] = json_object_get_double(json_object_array_get_idx(pt, 0)); t->y[q] = json_object_get_double(json_object_array_get_idx(pt, 1)); }
		}
		if (good) { t->stroke = stroke; t->n = n; }
		else memset(t, 0, sizeof *t);
	}
	/* its profiles and middle, from its strokes as the build makes them */
	variant_measure(w);
}

/* s is strict UTF-8 holding exactly want characters (Unicode scalar values), into cp */
static int model_chars(const char *s, unsigned *cp, size_t want)
{
	const unsigned char *p = (const unsigned char *)s;
	size_t n = 0;
	while (p && *p) {
		unsigned c = *p++, min;
		int extra = c < 0x80 ? 0 : (c & 0xe0) == 0xc0 ? 1 : (c & 0xf0) == 0xe0 ? 2 : (c & 0xf8) == 0xf0 ? 3 : -1;
		if (extra < 0) return 0;
		min = extra == 1 ? 0x80 : extra == 2 ? 0x800 : extra == 3 ? 0x10000 : 0;
		if (extra) c &= 0x3fu >> extra;
		for (int i = 0; i < extra; i++) {
			if ((*p & 0xc0) != 0x80) return 0;
			c = c << 6 | (*p++ & 0x3f);
		}
		if (c < min || !cp_ok(c) || n >= want) return 0;
		cp[n++] = c;
	}
	return p && n == want;
}

/* an entry of the model left out: where, never what */
static void model_skip(int line, const char *field)
{
	fprintf(stderr, "glassine-handwriting: letters: model line %d: %s isn't valid, left out\n", line, field);
}

int hw_letters_parse(const char *text, struct hw_letters *L)
{
	memset(L, 0, sizeof *L);
	const char *s = text;
	int line = 0, ok = 0, at = 0;
	size_t cap = 0;
	while (s && *s) {
		at++;
		const char *e = strchr(s, '\n');
		size_t n = e ? (size_t)(e - s) : strlen(s);
		char *ln = strndup(s, n);
		s = e ? e + 1 : s + n;
		if (!ln) goto bad;
		json_object *o = json_tokener_parse(ln);
		free(ln);
		if (!o) {
			if (!n) continue;
			if (!line) goto bad;
			model_skip(at, "the line");
			continue;
		}
		json_object *v;
		if (line == 0) {
			if (!json_object_object_get_ex(o, "format", &v) || strcmp(json_object_get_string(v), HW_LETTERS_FORMAT) ||
			    (int)jnum(o, "version", 0) != HW_LETTERS_VERSION) { json_object_put(o); goto bad; }
			if (json_object_object_get_ex(o, "inputs", &v)) snprintf(L->inputs, sizeof L->inputs, "%s", json_object_get_string(v));
			L->samples = (long)jnum(o, "samples", 0);
			L->built = (long long)jnum(o, "built", 0);
			ok = 1;
		} else if (json_object_object_get_ex(o, "metrics", &v)) {
			L->m = (struct hw_metrics){ jnum(v, "cap_height", 1.4), jnum(v, "ascender", 1.5), jnum(v, "descender", -0.5),
						    jnum(v, "slant", 0), jnum(v, "letter_spacing", 0.15), jnum(v, "word_spacing", 0.7),
						    jnum(v, "ink_width", 0.07), jnum(v, "x_height_per_line", 0),
						    jnum(v, "base_below_guide", 0), jnum(v, "x_height_mm", 0),
						    jnum(v, "pressure_median", 0), jnum(v, "speed_median", 0) };
		} else if (json_object_object_get_ex(o, "spacing", &v)) {
			struct hw_spacing *sp = &L->sp;
			sp->letter = jnum(v, "letter", L->m.letter_gap);
			sp->word = jnum(v, "word", L->m.word_gap);
			sp->open = jnum(v, "open", 0);
			sp->open0 = jnum(v, "open0", 0);
			sp->run = jnum(v, "run", 1);
			sp->narrow = jnum(v, "narrow", 1);
			json_object *a;
			if (json_object_object_get_ex(v, "pairs", &a) && json_object_is_type(a, json_type_array)) {
				size_t na = json_object_array_length(a);
				sp->pairs = calloc(na + 1, sizeof *sp->pairs);
				for (size_t i = 0; sp->pairs && i < na; i++) {
					json_object *e = json_object_array_get_idx(a, i);
					unsigned c1[2], c2[2];
					if (json_object_array_length(e) < 4) continue;
					if (!model_chars(json_object_get_string(json_object_array_get_idx(e, 0)), c1, 1) ||
					    !model_chars(json_object_get_string(json_object_array_get_idx(e, 1)), c2, 1)) { model_skip(at, "a pair's character"); continue; }
					sp->pairs[sp->npairs++] = (struct hw_pair){ c1[0], c2[0], json_object_get_double(json_object_array_get_idx(e, 2)),
										   json_object_get_int(json_object_array_get_idx(e, 3)), jdn(e, 4) };
				}
			}
			if (json_object_object_get_ex(v, "reach", &a) && json_object_is_type(a, json_type_array)) {
				size_t na = json_object_array_length(a);
				sp->reach = calloc(na + 1, sizeof *sp->reach);
				for (size_t i = 0; sp->reach && i < na; i++) {
					json_object *e = json_object_array_get_idx(a, i);
					unsigned c1[2];
					if (json_object_array_length(e) < 5) continue;
					if (!model_chars(json_object_get_string(json_object_array_get_idx(e, 0)), c1, 1)) { model_skip(at, "a reach's character"); continue; }
					sp->reach[sp->nreach++] = (struct hw_reach){ c1[0], jdn(e, 1), jdn(e, 2),
										     json_object_get_int(json_object_array_get_idx(e, 3)),
										     json_object_get_int(json_object_array_get_idx(e, 4)) };
				}
			}
			if (json_object_object_get_ex(v, "sides", &a) && json_object_is_type(a, json_type_array)) {
				size_t na = json_object_array_length(a);
				sp->sides = calloc(na + 1, sizeof *sp->sides);
				for (size_t i = 0; sp->sides && i < na; i++) {
					json_object *e = json_object_array_get_idx(a, i);
					if (json_object_array_length(e) < 4) continue;
					sp->sides[sp->nsides++] = (struct hw_side_pair){ json_object_get_int(json_object_array_get_idx(e, 0)),
											json_object_get_int(json_object_array_get_idx(e, 1)),
											json_object_get_double(json_object_array_get_idx(e, 2)),
											json_object_get_int(json_object_array_get_idx(e, 3)) };
				}
			}
			for (int side = 0; side < 2; side++) {
				if (!json_object_object_get_ex(v, side ? "right_shapes" : "left_shapes", &a) || !json_object_is_type(a, json_type_array)) continue;
				int na = (int)json_object_array_length(a), *nn = side ? &sp->nr : &sp->nl;
				for (int c = 0; c < na && c < HW_SIDE_CLASSES; c++) {
					json_object *e = json_object_array_get_idx(a, (size_t)c);
					for (int k = 0; k < HW_PROF_BANDS; k++) {
						double y = json_object_get_double(json_object_array_get_idx(e, (size_t)k));
						if (side) sp->rshape[c][k] = y; else sp->lshape[c][k] = y;
					}
					*nn = c + 1;
				}
			}
		} else if (json_object_object_get_ex(o, "char", &v)) {
			unsigned cp[2];
			if (!model_chars(json_object_get_string(v), cp, 1)) { model_skip(at, "its character"); json_object_put(o); line++; continue; }
			if (L->n == cap) {
				cap = cap ? cap * 2 : 64;
				struct hw_letter *x = realloc(L->l, cap * sizeof *x);
				if (!x) { json_object_put(o); goto bad; }
				L->l = x;
			}
			struct hw_letter *l = &L->l[L->n++];
			memset(l, 0, sizeof *l);
			l->cp = cp[0];
			l->samples = (int)jnum(o, "samples", 0);
			json_object *c;
			l->covered = json_object_object_get_ex(o, "covered", &c) && json_object_get_boolean(c);
			if (json_object_object_get_ex(o, "marks", &c)) snprintf(l->marks, sizeof l->marks, "%s", json_object_get_string(c));
			json_object *vs;
			if (json_object_object_get_ex(o, "variants", &vs) && json_object_is_type(vs, json_type_array)) {
				size_t nv = json_object_array_length(vs);
				for (size_t k = 0; k < nv && l->nvar < HW_LETTERS_VARIANTS; k++) {
					variant_in(json_object_array_get_idx(vs, k), &l->var[l->nvar++]);
				}
			}
			json_object *sl;
			int have = json_object_object_get_ex(o, "slots", &sl) && json_object_is_type(sl, json_type_array) &&
				   json_object_array_length(sl) == HW_LETTERS_SLOTS;
			for (int k = 0; k < HW_LETTERS_SLOTS; k++) {
				int v = have ? json_object_get_int(json_object_array_get_idx(sl, k)) : (l->nvar ? k % l->nvar : 0);
				l->slot[k] = (unsigned char)(v >= 0 && v < l->nvar ? v : 0);
			}
			/* the neighbour rules (FR-038) */
			json_object *ra;
			if (json_object_object_get_ex(o, "rules", &ra) && json_object_is_type(ra, json_type_array)) {
				size_t nr = json_object_array_length(ra);
				for (size_t k = 0; k < nr && l->nrules < HW_LETTERS_RULES; k++) {
					json_object *x = json_object_array_get_idx(ra, k), *nb;
					int side = json_object_object_get_ex(x, "next", &nb) ? 1 : json_object_object_get_ex(x, "prev", &nb) ? 0 : -1;
					unsigned c1[2];
					int var = (int)jnum(x, "variant", -1);
					if (side < 0 || var < 0 || var >= l->nvar) continue;
					if (!model_chars(json_object_get_string(nb), c1, 1)) { model_skip(at, "a rule's neighbour"); continue; }
					l->rule[l->nrules++] = (struct hw_rule){ c1[0], side, var, (int)jnum(x, "n", 0), (int)jnum(x, "same", 0),
										 jnum(x, "hits", 0), jnum(x, "cycle", 0) };
				}
			}
		} else if (json_object_object_get_ex(o, "joined", &v)) {
			unsigned c2[3];
			json_object *jv;
			int good = model_chars(json_object_get_string(v), c2, 2);
			if (!good) model_skip(at, "its pair");
			if (good && json_object_object_get_ex(o, "variant", &jv) && L->njoined < HW_JOINED_MAX) {
				struct hw_joined *x = realloc(L->joined, (L->njoined + 1) * sizeof *x);
				if (!x) { json_object_put(o); goto bad; }
				L->joined = x;
				struct hw_joined *j = &L->joined[L->njoined++];
				memset(j, 0, sizeof *j);
				j->a = c2[0];
				j->b = c2[1];
				j->samples = (int)jnum(o, "samples", 0);
				j->joined = (int)jnum(o, "written", 0);
				j->of = (int)jnum(o, "of", 0);
				variant_in(jv, &j->v);
				if (!j->v.nstrokes) { variant_free(&j->v); L->njoined--; }
			}
		} else if (json_object_object_get_ex(o, "joins", &v) && json_object_is_type(v, json_type_object) && !L->njshape) {
			json_object *a;
			if (json_object_object_get_ex(v, "pairs", &a) && json_object_is_type(a, json_type_array)) {
				size_t na = json_object_array_length(a);
				L->jpair = calloc(na + 1, sizeof *L->jpair);
				for (size_t i = 0; L->jpair && i < na; i++) {
					json_object *e = json_object_array_get_idx(a, i);
					unsigned c1[2], c2[2];
					if (!json_object_is_type(e, json_type_array) || json_object_array_length(e) < 4) continue;
					if (!model_chars(json_object_get_string(json_object_array_get_idx(e, 0)), c1, 1) ||
					    !model_chars(json_object_get_string(json_object_array_get_idx(e, 1)), c2, 1)) { model_skip(at, "a join's pair"); continue; }
					L->jpair[L->njpair++] = (struct hw_jpair){ c1[0], c2[0], json_object_get_int(json_object_array_get_idx(e, 2)),
										   json_object_get_int(json_object_array_get_idx(e, 3)) };
				}
			}
			if (json_object_object_get_ex(v, "kinds", &a) && json_object_is_type(a, json_type_array)) {
				for (size_t i = 0; i < json_object_array_length(a); i++) {
					json_object *e = json_object_array_get_idx(a, i);
					if (!json_object_is_type(e, json_type_array) || json_object_array_length(e) < 4) continue;
					int ec = json_object_get_int(json_object_array_get_idx(e, 0)), nc = json_object_get_int(json_object_array_get_idx(e, 1));
					if (ec < 0 || ec >= HW_JOIN_EXITS || nc < 0 || nc >= HW_JOIN_ENTRIES) continue;
					L->jkind[ec][nc] = (struct hw_jkind){ json_object_get_int(json_object_array_get_idx(e, 2)),
									      json_object_get_int(json_object_array_get_idx(e, 3)) };
				}
			}
			if (json_object_object_get_ex(v, "shapes", &a) && json_object_is_type(a, json_type_array)) {
				size_t na = json_object_array_length(a);
				L->jshape = calloc(na + 1, sizeof *L->jshape);
				for (size_t i = 0; L->jshape && i < na && L->njshape < HW_JOIN_MAX; i++) {
					json_object *e = json_object_array_get_idx(a, i), *f, *pa;
					struct hw_jshape t;
					memset(&t, 0, sizeof t);
					t.ec = t.nc = -1;
					if (!json_object_is_type(e, json_type_object)) continue;
					unsigned c2[3];
					if (json_object_object_get_ex(e, "pair", &f)) {
						if (!model_chars(json_object_get_string(f), c2, 2)) { model_skip(at, "a join's pair"); continue; }
						t.a = c2[0]; t.b = c2[1];
					} else if (json_object_object_get_ex(e, "kind", &f) && json_object_is_type(f, json_type_array) &&
						   json_object_array_length(f) == 2) {
						t.ec = json_object_get_int(json_object_array_get_idx(f, 0));
						t.nc = json_object_get_int(json_object_array_get_idx(f, 1));
						if (t.ec < 0 || t.ec >= HW_JOIN_EXITS || t.nc < 0 || t.nc >= HW_JOIN_ENTRIES) continue;
					} else continue;
					t.n = (int)jnum(e, "n", 0);
					t.mid = jnum(e, "middle", 0);
					if (!json_object_object_get_ex(e, "points", &pa) || !json_object_is_type(pa, json_type_array) ||
					    json_object_array_length(pa) != HW_JOIN_N || t.n <= 0) continue;
					int good = 1;
					for (int q = 0; q < HW_JOIN_N && good; q++) {
						json_object *pt = json_object_array_get_idx(pa, (size_t)q);
						good = json_object_is_type(pt, json_type_array) && json_object_array_length(pt) == 2;
						if (good) { t.x[q] = json_object_get_double(json_object_array_get_idx(pt, 0)); t.y[q] = json_object_get_double(json_object_array_get_idx(pt, 1)); }
					}
					if (good) L->jshape[L->njshape++] = t;
				}
			}
		}
		json_object_put(o);
		line++;
	}
	if (ok) {
		/* a model from before the spacing: its gaps */
		if (!L->sp.letter && !L->sp.word) { L->sp.letter = L->m.letter_gap; L->sp.word = L->m.word_gap; }
		return 0;
	}
bad:
	hw_letters_free(L);
	return -1;
}

static char *slurp(const char *p, size_t *len)
{
	FILE *f = fopen(p, "re");
	if (!f) return NULL;
	struct sbuf b = { 0 };
	char buf[65536];
	size_t k;
	b.s = malloc(1); b.cap = 1; b.s[0] = 0;
	while ((k = fread(buf, 1, sizeof buf, f)) > 0) {
		if (b.n + k + 1 > b.cap) { b.cap = (b.n + k + 1) * 2; char *x = realloc(b.s, b.cap); if (!x) break; b.s = x; }
		memcpy(b.s + b.n, buf, k); b.n += k; b.s[b.n] = 0;
	}
	fclose(f);
	if (len) *len = b.n;
	return b.s;
}

int hw_letters_load(struct hw_letters *L)
{
	char p[4096];
	memset(L, 0, sizeof *L);
	if (hw_store_path(p, sizeof p, "letters/model")) return -1;
	char *t = slurp(p, NULL);
	if (!t) return errno == ENOENT ? 0 : -1;
	int r = hw_letters_parse(t, L);
	free(t);
	return r ? -1 : 1;
}

/* ---------------------------------------------------------------- inputs */

static int cmp_str(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

int hw_letters_inputs(char out[17])
{
	const struct hw_index_entry *es;
	size_t n;
	if (hw_samples_index(&es, &n)) return -1;
	char **keys = calloc(n + 1, sizeof *keys);
	size_t k = 0;
	if (!keys) return -1;
	for (size_t i = 0; i < n; i++) {
		if (strcmp(es[i].meta.source, "prompted")) continue;
		char b[64];
		snprintf(b, sizeof b, "%s %s %s", es[i].meta.id, es[i].written, es[i].pair);
		keys[k++] = strdup(b);
	}
	qsort(keys, k, sizeof *keys, cmp_str);
	uint64_t h = FNV0;
	char v[32];
	snprintf(v, sizeof v, "letters %d %d%s\n", HW_LETTERS_VERSION, HW_LETTERS_BUILD, avg_mean() ? " mean" : "");
	h = fnv(h, v, strlen(v));
	for (size_t i = 0; i < k; i++) { if (keys[i]) h = fnv(h, keys[i], strlen(keys[i]) + 1); free(keys[i]); }
	free(keys);
	struct hw_pref *pr;
	size_t np;
	if (hw_prefs(&pr, &np, NULL)) return -1;
	char **pk = calloc(np + 1, sizeof *pk);
	for (size_t i = 0; pk && i < np; i++) {
		char b[80];
		snprintf(b, sizeof b, "%s %s %d %d", pr[i].chosen, pr[i].other, pr[i].cspan, pr[i].ospan);
		pk[i] = strdup(b);
	}
	if (pk) {
		qsort(pk, np, sizeof *pk, cmp_str);
		h = fnv(h, "\npreferences\n", 13);
		for (size_t i = 0; i < np; i++) { if (pk[i]) h = fnv(h, pk[i], strlen(pk[i]) + 1); free(pk[i]); }
	}
	free(pk);
	free(pr);
	snprintf(out, 17, "%016llx", (unsigned long long)h);
	return 0;
}

/* ---------------------------------------------------------------- building */

/* one version of a character in one sample, in the sample's units until normalized */
struct inst {
	unsigned cp;
	int sample;                     /* index into the sorted samples */
	int pos;                        /* the character's place in the sample's text */
	double x0, x1, y0, y1;          /* box, sample units, y down */
	size_t nstrokes;
	struct hw_lstroke *s;           /* sample units until normalized, then x-heights, y up */
	double w;                       /* weight */
	double bw;                      /* its weight in the best version, times w (FR-035) */
	int dropped;
	int slip;                       /* far from the character's other versions in size */
	int var;                        /* the variant it is written like, -1 none */
	int joined;                     /* cut from a stroke it shares with its neighbours (FR-007) */
	double w0;                      /* its weight before a pass of build_chars */
	/* written joined: the joining stroke into it and out of it (entry, exit), HW_STUB_N points
	 * as struct hw_stub has them, at its stroke stroke; n 0 none */
	struct hw_stub stub[2];
	int occ;                        /* its occurrence record (one character's build), -1 none */
	unsigned prev, next;            /* the characters beside it in its sample's text, 0 none or a space */
	double *rs;                     /* resampled, centred: 2 * total points (per group) */
	/* how it was written: per stroke HW_LETTERS_PROFILE points along its arc length, the
	 * pressure and the speed (sample units, then x-heights per second, then relative to the
	 * person's median); NAN where the pen gave none */
	double *pp, *pv;
};

struct smp {
	char id[17];
	long long time;
	struct hw_sample s;
	int parsed;
	double base, xh;                /* baseline (y down) and x-height, sample units; xh 0: unknown */
	int ninst, first;               /* its instances */
	double guide;                   /* the area guide's baseline, NAN when none */
	double read[512];               /* readability per character (first 512) */
	int pressure;                   /* its pen gave pressure that varies (not one value throughout) */
	char *own;                      /* per character: strokes of its own (own_chars) */
	int *cnt;                       /* per stroke: the characters it is with */
};

static int cmp_smp(const void *a, const void *b) { return strcmp(((const struct smp *)a)->id, ((const struct smp *)b)->id); }

/* readability per character: matched by the first stored reading (edit-distance alignment) */
static void readability(struct smp *m, const unsigned *cp, size_t n)
{
	for (size_t i = 0; i < n && i < 512; i++) m->read[i] = 0.6;
	if (m->s.reading.n < 1 || !m->s.reading.text[0]) return;
	unsigned rd[512];
	size_t nr = hw_utf8_decode(m->s.reading.text[0], rd, 512);
	if (n > 511 || nr > 511) return;
	size_t W = nr + 1;
	unsigned *D = malloc((n + 1) * W * sizeof *D);
	if (!D) return;
	for (size_t i = 0; i <= n; i++) D[i * W] = (unsigned)i;
	for (size_t j = 0; j <= nr; j++) D[j] = (unsigned)j;
	for (size_t i = 1; i <= n; i++)
		for (size_t j = 1; j <= nr; j++) {
			unsigned a = D[(i - 1) * W + j - 1] + (cp[i - 1] != rd[j - 1]);
			unsigned b = D[(i - 1) * W + j] + 1, c = D[i * W + j - 1] + 1;
			D[i * W + j] = a < b ? (a < c ? a : c) : (b < c ? b : c);
		}
	for (size_t i = 0; i < n; i++) m->read[i] = 0.35;
	size_t i = n, j = nr;
	while (i > 0 && j > 0) {
		unsigned d = D[i * W + j];
		if (d == D[(i - 1) * W + j - 1] + (cp[i - 1] != rd[j - 1])) {
			if (cp[i - 1] == rd[j - 1]) m->read[i - 1] = 1.0;
			i--; j--;
		} else if (d == D[(i - 1) * W + j] + 1) i--;
		else j--;
	}
	free(D);
}

/* arc-length resampling of a polyline to n points */
static void resample(const struct hw_lstroke *s, size_t n, double *out)
{
	if (s->n == 0) { for (size_t i = 0; i < n; i++) out[2 * i] = out[2 * i + 1] = 0; return; }
	double total = 0;
	for (size_t i = 1; i < s->n; i++) total += hypot(s->x[i] - s->x[i - 1], s->y[i] - s->y[i - 1]);
	if (total <= 0 || n < 2 || s->n < 2) {
		for (size_t i = 0; i < n; i++) { out[2 * i] = s->x[0]; out[2 * i + 1] = s->y[0]; }
		return;
	}
	size_t seg = 1;
	double acc = 0;
	for (size_t i = 0; i < n; i++) {
		double want = total * (double)i / (double)(n - 1);
		while (seg < s->n - 1) {
			double l = hypot(s->x[seg] - s->x[seg - 1], s->y[seg] - s->y[seg - 1]);
			if (acc + l >= want) break;
			acc += l; seg++;
		}
		double l = hypot(s->x[seg] - s->x[seg - 1], s->y[seg] - s->y[seg - 1]);
		double t = l > 0 ? clampd((want - acc) / l, 0, 1) : 0;
		out[2 * i] = s->x[seg - 1] + t * (s->x[seg] - s->x[seg - 1]);
		out[2 * i + 1] = s->y[seg - 1] + t * (s->y[seg] - s->y[seg - 1]);
	}
}

/* A stroke's pressure and speed along its normalised arc length (FR-006): HW_LETTERS_PROFILE
 * points, linear between the pen's points; the speed over the PROF_WIN points to either side
 * (the pen's own steps are uneven), sample units per second. NAN where there is none: a pen
 * without pressure (pres 0), a stroke without length or without rising times. */
#define PROF_WIN 2
static void stroke_profile(const struct hw_ink *k, int pres, double *pp, double *pv)
{
	enum { K = HW_LETTERS_PROFILE };
	for (int q = 0; q < K; q++) pp[q] = pv[q] = NAN;
	size_t n = k->n;
	if (!n) return;
	double *s = malloc(n * sizeof *s), *sp = malloc(n * sizeof *sp);
	if (!s || !sp) { free(s); free(sp); return; }
	s[0] = 0;
	int timed = n > 1 && k->t[n - 1] > k->t[0];
	for (size_t i = 1; i < n; i++) {
		s[i] = s[i - 1] + hypot(k->x[i] - k->x[i - 1], k->y[i] - k->y[i - 1]);
		if (!(k->t[i] >= k->t[i - 1])) timed = 0;
	}
	double L = s[n - 1];
	for (size_t i = 0; i < n; i++) {
		size_t lo = i > PROF_WIN ? i - PROF_WIN : 0, hi = i + PROF_WIN < n ? i + PROF_WIN : n - 1;
		double dt = k->t[hi] - k->t[lo];
		sp[i] = timed && dt > 0 ? (s[hi] - s[lo]) / dt : NAN;
	}
	if (L <= 0) {
		/* a dot: its mean pressure, no speed */
		double a = 0;
		for (size_t i = 0; i < n; i++) a += k->p[i];
		if (pres) for (int q = 0; q < K; q++) pp[q] = a / (double)n;
		free(s); free(sp);
		return;
	}
	size_t i = 0;
	for (int q = 0; q < K; q++) {
		double want = L * q / (K - 1);
		while (i + 2 < n && s[i + 1] < want) i++;
		double l = s[i + 1] - s[i], f = l > 0 ? clampd((want - s[i]) / l, 0, 1) : 0;
		if (pres) pp[q] = k->p[i] + f * (k->p[i + 1] - k->p[i]);
		pv[q] = sp[i] + f * (sp[i + 1] - sp[i]);
	}
	free(s); free(sp);
}

/* a pen's pressure that tells something: there, and not one value throughout */
static int varied_pressure(const struct hw_sample *s)
{
	if (!s->pressure) return 0;
	double lo = INFINITY, hi = -INFINITY;
	for (size_t j = 0; j < s->nstrokes; j++)
		for (size_t i = 0; i < s->strokes[j].n; i++) {
			double p = s->strokes[j].p[i];
			if (isnan(p)) return 0;
			if (p < lo) lo = p;
			if (p > hi) hi = p;
		}
	return hi - lo > 1e-3;
}

/* the averaged profile of the members of a variant (make_variant): acc and wt per point;
 * NULL when no member had one, smoothed by one pass of [1 2 1] / 4 per stroke */
static double *profile_mean(const double *acc, const double *wt, size_t nstrokes)
{
	enum { K = HW_LETTERS_PROFILE };
	size_t n = nstrokes * K;
	int any = 0;
	for (size_t i = 0; i < n; i++) any |= wt[i] > 0;
	if (!any) return NULL;
	double *o = malloc((n + 1) * sizeof *o);
	if (!o) return NULL;
	for (size_t i = 0; i < n; i++) o[i] = wt[i] > 0 ? acc[i] / wt[i] : 1;
	for (size_t j = 0; j < nstrokes; j++) {
		double *r = o + j * K, prev = r[0];
		for (int q = 1; q < K - 1; q++) { double c = (prev + 2 * r[q] + r[q + 1]) / 4; prev = r[q]; r[q] = c; }
	}
	for (size_t i = 0; i < n; i++) o[i] = round(clampd(o[i], 0.05, 20) * 1e3) / 1e3;
	return o;
}

static double slen(const struct hw_lstroke *s)
{
	double t = 0;
	for (size_t i = 1; i < s->n; i++) t += hypot(s->x[i] - s->x[i - 1], s->y[i] - s->y[i - 1]);
	return t;
}

/* a group: a character's versions with one stroke count */
struct group {
	size_t nstrokes;
	int *members, n;
	int *pts;                       /* points per stroke */
	int total;
};

static double rdist(const double *a, const double *b, int total)
{
	double s = 0;
	for (int i = 0; i < 2 * total; i += 2) {
		double dx = a[i] - b[i], dy = a[i + 1] - b[i + 1];
		s += dx * dx + dy * dy;
	}
	return sqrt(s / (total ? total : 1));
}

#define TAU 0.30        /* distance (x-heights, RMS) within which versions are one shape */
#define SIGMA 0.15      /* the kernel of a variant around its seed */
#define HEIGHT_MIN 0.1  /* a variant's height (x-heights) below which it is left as averaged */
#define HEIGHT_GROW 1.5 /* the most a variant is stretched back to its members' height */
#define DIFF_MIN 0.06   /* a further variant must differ at least this much */
#define WAY_TAU 0.60    /* within a stroke count, versions this close (x-heights, RMS) are one way of writing it */

/* ---------------------------------------------------------------- registration (FR-006)
 *
 * The samples of a way of writing a character are resampled by arc length, so the same
 * point of the letter (a loop's top, a cusp) falls at a different place along each one:
 * one writes the loop a little wider, the next starts with a longer lead-in. Averaged point
 * by point they blur (loops shrink, cusps round off, the letter comes out shorter). So each
 * sample is first registered to the variant's shape: matched point to point along each
 * stroke by dynamic time warping over position, direction and turning (each step of the
 * match at most two points on either side for one on the other), the match smoothed into a
 * steady warp, and moved as a whole to fit; the variant is the weighted average of the
 * registered samples, starting from its seed, its points spaced evenly along each stroke
 * again, and the samples are registered again to that average (REG_ITER times: DTW
 * barycentre averaging). Where it still comes out rougher than the samples averaged point
 * by point (shape_rough, cusps left out), it is smoothed further, its cusps kept. Which samples count, and how much, goes by their distance from the
 * seed by arc length. GLASSINE_HANDWRITING_LETTERS_AVG=mean builds it
 * the way before (point by point, unregistered), for measuring one against the other; it
 * is part of the model's inputs. */
#define REG_MAXN 64     /* points per stroke at most (the groups' resampling caps them so) */
#define REG_DIR 1.0     /* the cost of the direction against position: 1 - cos, x-heights² */
#define REG_TURN 0.5    /* and of the turning (radians per point)² */
#define REG_BAND 0.25   /* the match stays within this share of the stroke's points of the diagonal */
#define REG_ITER 3      /* rounds of registering and averaging */
#define REG_WARP_SMOOTH 2 /* passes of [1 2 1] / 4 over each match's warp */
#define REG_CUSP 0.8      /* radians a point turns by at least to be a cusp, not a wobble */
#define REG_SMOOTH_MAX 7  /* further passes over the average at most, while it is rougher than the pointwise mean */
#define REG_SMOOTH_KEEP 4 /* of which the first this many keep its size (Taubin) */
enum { AVG_MEAN, AVG_DBA };

static int avg_method(void)
{
	const char *e = getenv("GLASSINE_HANDWRITING_LETTERS_AVG");
	if (e && !strcmp(e, "mean")) return AVG_MEAN;
	return AVG_DBA;
}

static int avg_mean(void) { return avg_method() == AVG_MEAN; }
struct group;
static double shape_rough(const double *p, const struct group *g, double cusp);
/* shape_rough of p after the one pass of [1 2 1] / 4 every version gets (make_variant):
 * the jitter from point to point that pass takes off isn't counted, the wobble it leaves is */
static double shape_rough_smoothed(const double *p, const struct group *g, double cusp, double *tmp)
{
	int T = 0;
	for (size_t j = 0; j < g->nstrokes; j++) T += g->pts[j];
	memcpy(tmp, p, (size_t)T * 2 * sizeof *tmp);
	int o = 0;
	for (size_t j = 0; j < g->nstrokes; j++) {
		int n = g->pts[j];
		double px = tmp[2 * o], py = tmp[2 * o + 1];
		for (int i = 1; i < n - 1; i++) {
			double *c = tmp + 2 * (o + i), nx = (px + 2 * c[0] + c[2]) / 4, ny = (py + 2 * c[1] + c[3]) / 4;
			px = c[0]; py = c[1]; c[0] = nx; c[1] = ny;
		}
		o += n;
	}
	return shape_rough(tmp, g, cusp);
}

/* per point the direction (radians) and the turning (radians per point) of a stroke */
static void reg_feat(const double *p, int n, double *th, double *ka)
{
	for (int i = 0; i < n; i++) {
		int a = i > 0 ? i - 1 : 0, b = i < n - 1 ? i + 1 : n - 1;
		th[i] = atan2(p[2 * b + 1] - p[2 * a + 1], p[2 * b] - p[2 * a]);
	}
	for (int i = 0; i < n; i++) {
		int a = i > 0 ? i - 1 : 0, b = i < n - 1 ? i + 1 : n - 1;
		double d = th[b] - th[a];
		while (d > M_PI) d -= 2 * M_PI;
		while (d < -M_PI) d += 2 * M_PI;
		ka[i] = b > a ? d / (b - a) : 0;
	}
}

/* one stroke X (n points, moved by tx, ty) registered to M (n points, its features mth and
 * mka): W gets, per point of M, the mean of the points of X matched with it. Symmetric
 * steps of slope 1/2 to 2 (Sakoe and Chiba's P = 1), ends matched with ends. */
static void reg_stroke(const double *M, const double *mth, const double *mka, const double *X, double tx, double ty,
		       int n, double *W)
{
	double P[2 * REG_MAXN], th[REG_MAXN], ka[REG_MAXN], su[REG_MAXN];
	static double D[REG_MAXN][REG_MAXN], G[REG_MAXN][REG_MAXN];
	static signed char mv[REG_MAXN][REG_MAXN];
	int cnt[REG_MAXN];
	for (int i = 0; i < n; i++) { P[2 * i] = X[2 * i] + tx; P[2 * i + 1] = X[2 * i + 1] + ty; }
	if (n < 3 || n > REG_MAXN) { memcpy(W, P, (size_t)n * 2 * sizeof *W); return; }
	reg_feat(P, n, th, ka);
	int band = (int)(n * REG_BAND);
	if (band < 2) band = 2;
	for (int i = 0; i < n; i++)
		for (int j = 0; j < n; j++) {
			G[i][j] = INFINITY;
			if (abs(i - j) > band) { D[i][j] = INFINITY; continue; }
			double dx = M[2 * i] - P[2 * j], dy = M[2 * i + 1] - P[2 * j + 1], dk = mka[i] - ka[j];
			D[i][j] = dx * dx + dy * dy + REG_DIR * (1 - cos(mth[i] - th[j])) + REG_TURN * dk * dk;
		}
	G[0][0] = D[0][0];
	for (int i = 0; i < n; i++)
		for (int j = 0; j < n; j++) {
			if (!i && !j) continue;
			if (!isfinite(D[i][j])) continue;
			double b = INFINITY;
			signed char m = -1;
			if (i >= 1 && j >= 1 && G[i - 1][j - 1] + 2 * D[i][j] < b) { b = G[i - 1][j - 1] + 2 * D[i][j]; m = 0; }
			if (i >= 1 && j >= 2 && G[i - 1][j - 2] + 2 * D[i][j - 1] + D[i][j] < b) { b = G[i - 1][j - 2] + 2 * D[i][j - 1] + D[i][j]; m = 1; }
			if (i >= 2 && j >= 1 && G[i - 2][j - 1] + 2 * D[i - 1][j] + D[i][j] < b) { b = G[i - 2][j - 1] + 2 * D[i - 1][j] + D[i][j]; m = 2; }
			G[i][j] = b;
			mv[i][j] = m;
		}
	if (!isfinite(G[n - 1][n - 1])) { memcpy(W, P, (size_t)n * 2 * sizeof *W); return; }
	for (int i = 0; i < n; i++) { su[i] = 0; cnt[i] = 0; }
#define REG_ADD(a, b) do { su[a] += (b); cnt[a]++; } while (0)
	int i = n - 1, j = n - 1;
	while (i > 0 || j > 0) {
		signed char m = mv[i][j];
		REG_ADD(i, j);
		if (m == 0) { i--; j--; }
		else if (m == 1) { REG_ADD(i, j - 1); i--; j -= 2; }
		else if (m == 2) { REG_ADD(i - 1, j); i -= 2; j--; }
		else break;
		if (i < 0 || j < 0) break;
	}
	if (i == 0 && j == 0) REG_ADD(0, 0);
#undef REG_ADD
	/* the warp as a smooth function (a match point by point jumps a point back and forth,
	 * which bends a smooth curve): per point of M where along X it falls, smoothed, and X
	 * taken there */
	double u[REG_MAXN];
	for (int q = 0; q < n; q++) u[q] = cnt[q] ? su[q] / cnt[q] : q;
	for (int pass = 0; pass < REG_WARP_SMOOTH; pass++) {
		double pu = u[0];
		for (int q = 1; q < n - 1; q++) { double nu = (pu + 2 * u[q] + u[q + 1]) / 4; pu = u[q]; u[q] = nu; }
	}
	for (int q = 1; q < n; q++) if (u[q] < u[q - 1]) u[q] = u[q - 1];
	for (int q = 0; q < n; q++) {
		double f = u[q] < 0 ? 0 : u[q] > n - 1 ? n - 1 : u[q];
		int a = (int)f; if (a > n - 2) a = n - 2;
		double t = f - a;
		W[2 * q] = P[2 * a] + t * (P[2 * a + 2] - P[2 * a]);
		W[2 * q + 1] = P[2 * a + 1] + t * (P[2 * a + 3] - P[2 * a + 1]);
	}
}

/* a version X (a group's resampled, centred shape) registered to M stroke by stroke, then
 * moved as a whole to fit M (t: its move, kept from round to round); W the registered
 * version (per point of M); the RMS distance (x-heights) of W from M */
static double reg_align(const double *M, const double *X, const struct group *g, double t[2], double *W)
{
	double th[REG_MAXN], ka[REG_MAXN], mx = 0, my = 0, s = 0;
	int o = 0;
	for (size_t j = 0; j < g->nstrokes; j++) {
		int n = g->pts[j];
		if (n <= REG_MAXN) reg_feat(M + 2 * o, n, th, ka);
		reg_stroke(M + 2 * o, th, ka, X + 2 * o, t[0], t[1], n, W + 2 * o);
		o += n;
	}
	for (int q = 0; q < g->total; q++) { mx += M[2 * q] - W[2 * q]; my += M[2 * q + 1] - W[2 * q + 1]; }
	mx /= g->total ? g->total : 1; my /= g->total ? g->total : 1;
	t[0] += mx; t[1] += my;
	for (int q = 0; q < g->total; q++) {
		W[2 * q] += mx; W[2 * q + 1] += my;
		double dx = W[2 * q] - M[2 * q], dy = W[2 * q + 1] - M[2 * q + 1];
		s += dx * dx + dy * dy;
	}
	return sqrt(s / (g->total ? g->total : 1));
}

/* the registered distance of X from M (two rounds of moving it), x-heights RMS */
static double reg_dist(const double *M, const double *X, const struct group *g, double *W)
{
	double t[2] = { 0, 0 };
	reg_align(M, X, g, t, W);
	return reg_align(M, X, g, t, W);
}

static void centre_shape(double *p, int total)
{
	double cx = 0, cy = 0;
	for (int q = 0; q < total; q++) { cx += p[2 * q]; cy += p[2 * q + 1]; }
	cx /= total ? total : 1; cy /= total ? total : 1;
	for (int q = 0; q < total; q++) { p[2 * q] -= cx; p[2 * q + 1] -= cy; }
}

/* The shape of a variant (var < 0) or of variant var's best version: the members of its
 * group near the seed (within cut x-heights RMS), each weighed by its weight (times its best
 * weight for a best version) and by its closeness to the seed (a Gaussian of width sigma);
 * kw per member of the group its weight (0 when out), out the centred shape (2 * total).
 * Returns the sum of the weights, 0 when none. */
static double shape_mean(const struct inst *in, const struct group *g, int seed, int var, double sigma, double cut,
			 double *kw, double *out)
{
	int T = g->total, method = avg_method();
	const double *sr = in[seed].rs;
	double wsum = 0;
	double *W = malloc((size_t)T * 2 * sizeof *W), *acc = calloc((size_t)T * 2, sizeof *acc);
	double *tr = calloc((size_t)g->n * 2 + 2, sizeof *tr);
	if (!W || !acc || !tr || !sr) { free(W); free(acc); free(tr); return 0; }
	for (int k = 0; k < g->n; k++) {
		const struct inst *x = &in[g->members[k]];
		kw[k] = 0;
		if (!x->rs || (var >= 0 && x->var != var)) continue;
		double d = rdist(x->rs, sr, T);
		if (d > cut) continue;
		double w = x->w * (var >= 0 ? x->bw : 1) * exp(-(d / sigma) * (d / sigma));
		if (!(w > 0)) continue;
		kw[k] = w;
		wsum += w;
	}
	if (!(wsum > 0)) { free(W); free(acc); free(tr); return 0; }
	if (method == AVG_MEAN) {
		for (int k = 0; k < g->n; k++)
			for (int i = 0; kw[k] > 0 && i < 2 * T; i++) acc[i] += kw[k] * in[g->members[k]].rs[i];
		for (int i = 0; i < 2 * T; i++) out[i] = acc[i] / wsum;
	} else {
		memcpy(out, sr, (size_t)T * 2 * sizeof *out);
		for (int it = 0; it < REG_ITER; it++) {
			memset(acc, 0, (size_t)T * 2 * sizeof *acc);
			for (int k = 0; k < g->n; k++) {
				if (!(kw[k] > 0)) continue;
				reg_align(out, in[g->members[k]].rs, g, tr + 2 * k, W);
				for (int i = 0; i < 2 * T; i++) acc[i] += kw[k] * W[i];
			}
			for (int i = 0; i < 2 * T; i++) out[i] = acc[i] / wsum;
			centre_shape(out, T);
			/* evenly along each stroke again: where many points of the samples meet one of it,
			 * its points bunch, and smoothing bunched points kinks the curve */
			{
				int o = 0;
				for (size_t j = 0; j < g->nstrokes; j++) {
					int n = g->pts[j];
					double xs[REG_MAXN], ys[REG_MAXN];
					if (n <= REG_MAXN) {
						for (int i = 0; i < n; i++) { xs[i] = out[2 * (o + i)]; ys[i] = out[2 * (o + i) + 1]; }
						struct hw_lstroke t = { (size_t)n, xs, ys };
						resample(&t, (size_t)n, out + 2 * o);
					}
					o += n;
				}
			}
		}
		/* not more wobbly than the samples averaged point by point, both measured after the
		 * one pass every version gets: smoothed further (up to REG_SMOOTH_MAX passes) while it
		 * is, its cusps (where it turns by more than REG_CUSP at a point) left as they are and
		 * not counted */
		double *pm = calloc((size_t)T * 4, sizeof *pm), *tmp = pm + 2 * T;
		for (int k = 0; pm && k < g->n; k++)
			for (int i = 0; kw[k] > 0 && i < 2 * T; i++) pm[i] += kw[k] * in[g->members[k]].rs[i] / wsum;
		double rough = pm ? shape_rough_smoothed(pm, g, REG_CUSP, tmp) : INFINITY;
		for (int pass = 0; pm && pass < REG_SMOOTH_MAX && shape_rough_smoothed(out, g, REG_CUSP, tmp) > rough; pass++) {
			/* first passes that don't shrink (Taubin: a step to the neighbours' middle, then
			 * back a little more), then plain ones */
			for (int half = 0; half < (pass < REG_SMOOTH_KEEP ? 2 : 1); half++) {
				double f = pass >= REG_SMOOTH_KEEP ? 0.5 : half ? -0.53 : 0.5;
				int o = 0;
				for (size_t j = 0; j < g->nstrokes; j++) {
					int n = g->pts[j];
					double px = out[2 * o], py = out[2 * o + 1];
					for (int i = 1; i < n - 1; i++) {
						double *c = out + 2 * (o + i);
						double nx = c[0] + f * ((px + c[2]) / 2 - c[0]), ny = c[1] + f * ((py + c[3]) / 2 - c[1]);
						double ax = c[0] - px, ay = c[1] - py, bx = c[2] - c[0], by = c[3] - c[1];
						int keep = fabs(atan2(ax * by - ay * bx, ax * bx + ay * by)) > REG_CUSP;
						px = c[0]; py = c[1];
						if (!keep) { c[0] = nx; c[1] = ny; }
					}
					o += n;
				}
			}
		}
		free(pm);
	}
	free(W); free(acc); free(tr);
	return wsum;
}

/* ---------------------------------------------------------------- the letter model's measures
 *
 * GLASSINE_HANDWRITING_LETTERS_STATS=FILE: a build appends one line per variant (and joined
 * pair) it makes, numbers only (no ink), for measuring the build (tools/eval-letters.py):
 * `variant CP N STROKES ARC REG EFF HM HV AM AV`, CP the character (0 a joined pair), N the
 * samples written like it, ARC and REG how far those are from it on average (x-heights RMS:
 * by arc length as neatening measures, and registered), EFF how many samples it is made of
 * in effect (the weights' (sum)² / sum of squares), HM and AM their mean height and enclosed
 * area (with those weights), HV and AV its own as averaged (smoothed, before it is stretched
 * back to their height). */
static unsigned stats_cp;               /* the character being built, 0 a joined pair */

struct vstat { double eff, hm, hv, am, av, rm, rv; };

/* how rough a shape is: per stroke, resampled evenly by arc length to its points, the
 * turning angle (radians) at each point less its local mean (the turning of the point
 * before, itself twice and the one after, over 4), squared and summed: a smooth curve,
 * however tightly it bends, turns about as much at a point as at its neighbours; jitter,
 * kinks and wobbles don't */
static double shape_rough(const double *p, const struct group *g, double cusp)
{
	double e = 0, buf[2 * REG_MAXN], xs[REG_MAXN], ys[REG_MAXN], tn[REG_MAXN];
	int o = 0;
	for (size_t j = 0; j < g->nstrokes; j++) {
		int n = g->pts[j];
		if (n >= 5 && n <= REG_MAXN) {
			for (int i = 0; i < n; i++) { xs[i] = p[2 * (o + i)]; ys[i] = p[2 * (o + i) + 1]; }
			struct hw_lstroke t = { (size_t)n, xs, ys };
			resample(&t, (size_t)n, buf);
			for (int i = 1; i < n - 1; i++) {
				double ax = buf[2 * i] - buf[2 * i - 2], ay = buf[2 * i + 1] - buf[2 * i - 1];
				double bx = buf[2 * i + 2] - buf[2 * i], by = buf[2 * i + 3] - buf[2 * i + 1];
				tn[i] = hypot(ax, ay) < 1e-9 || hypot(bx, by) < 1e-9 ? 0 : atan2(ax * by - ay * bx, ax * bx + ay * by);
			}
			for (int i = 2; i < n - 2; i++) {
				if (fabs(tn[i - 1]) > cusp || fabs(tn[i]) > cusp || fabs(tn[i + 1]) > cusp) continue;
				double d = tn[i] - (tn[i - 1] + 2 * tn[i] + tn[i + 1]) / 4;
				e += d * d;
			}
		}
		o += n;
	}
	return e;
}

/* the area a shape's strokes enclose, each closed by the line from its end to its start */
static double shape_area(const double *p, const struct group *g)
{
	double a = 0;
	int o = 0;
	for (size_t j = 0; j < g->nstrokes; j++) {
		int n = g->pts[j];
		double sh = 0;
		for (int i = 0; i < n; i++) {
			const double *u = p + 2 * (o + i), *w = p + 2 * (o + (i + 1) % n);
			sh += u[0] * w[1] - w[0] * u[1];
		}
		a += fabs(sh) / 2;
		o += n;
	}
	return a;
}

static void variant_stats(const struct inst *in, const struct group *g, const char *sel, const struct hw_variant *v,
			  const struct vstat *vs)
{
	const char *f = getenv("GLASSINE_HANDWRITING_LETTERS_STATS");
	if (!f || !*f || v->nstrokes != g->nstrokes) return;
	int T = g->total;
	double *vr = malloc((size_t)T * 2 * sizeof *vr), *W = malloc((size_t)T * 2 * sizeof *W);
	if (!vr || !W) { free(vr); free(W); return; }
	int o = 0;
	for (size_t j = 0; j < g->nstrokes; j++) { resample(&v->s[j], (size_t)g->pts[j], vr + 2 * o); o += g->pts[j]; }
	centre_shape(vr, T);
	double arc = 0, reg = 0;
	int n = 0;
	for (int k = 0; k < g->n; k++) {
		const double *r = in[g->members[k]].rs;
		if (!sel[k] || !r) continue;
		arc += rdist(r, vr, T);
		reg += reg_dist(vr, r, g, W);
		n++;
	}
	FILE *fp = n ? fopen(f, "a") : NULL;
	if (fp) {
		fprintf(fp, "variant\t%u\t%d\t%zu\t%.5f\t%.5f\t%.3f\t%.5f\t%.5f\t%.5f\t%.5f\t%.5f\t%.5f\n", stats_cp, n, g->nstrokes,
			arc / n, reg / n, vs->eff, vs->hm, vs->hv, vs->am, vs->av, vs->rm, vs->rv);
		fclose(fp);
	}
	free(vr); free(W);
}

static void letter_slots(struct hw_letter *l);

/* builds one variant around a seed from its group; *xmin the left edge its x is from */
/* a variant narrowed about its middle by k (FR-006), its best version with it, x again from
 * its left edge; its profiles and middle measured again */
static void variant_narrow(struct hw_variant *v, double k)
{
	if (!(k < 1) || !v->s) return;
	double c = isfinite(v->mid) ? v->mid : v->width / 2, lo = INFINITY, hi = -INFINITY;
	for (size_t j = 0; j < v->nstrokes; j++)
		for (size_t i = 0; i < v->s[j].n; i++) {
			double x = c + (v->s[j].x[i] - c) * k;
			if (x < lo) lo = x;
			if (x > hi) hi = x;
		}
	if (!isfinite(lo)) return;
	for (size_t j = 0; j < v->nstrokes; j++) {
		for (size_t i = 0; i < v->s[j].n; i++) v->s[j].x[i] = round((c + (v->s[j].x[i] - c) * k - lo) * 1e4) / 1e4;
		for (size_t i = 0; v->best && i < v->best[j].n; i++)
			v->best[j].x[i] = round((c + (v->best[j].x[i] - c) * k - lo) * 1e4) / 1e4;
	}
	v->width = round((hi - lo) * 1e4) / 1e4;
	/* (an entry or exit runs from a point of the variant: narrowed with it) */
	for (int e = 0; e < 2; e++) {
		struct hw_stub *t = e ? &v->exit : &v->entry;
		for (int q = 0; t->n > 0 && q < HW_STUB_N; q++) t->x[q] = round(t->x[q] * k * 1e4) / 1e4;
	}
	variant_measure(v);
}

static int make_variant(struct inst *in, const struct group *g, int seed, struct hw_variant *v, double *xmin_out, struct vstat *vs)
{
	double *acc = calloc((size_t)g->total * 2 + 1, sizeof *acc), *kw = calloc((size_t)g->n + 1, sizeof *kw), hsum = 0, w2 = 0, asum = 0, rsum = 0;
	if (!acc || !kw) { free(acc); free(kw); return -1; }
	int members = 0;
	/* the members near the seed, registered and averaged (shape_mean) */
	double wsum = shape_mean(in, g, seed, -1, SIGMA, 2 * SIGMA, kw, acc);
	if (!(wsum > 0)) { free(acc); free(kw); return -1; }
	/* how it was written: the members' profiles with the same weights, each point over the
	 * members that have one there (a pen without pressure gives speed alone) */
	size_t np = g->nstrokes * HW_LETTERS_PROFILE;
	double *pa = calloc(4 * np + 1, sizeof *pa), *pw = pa + np, *va = pa + 2 * np, *vw = pa + 3 * np;
	if (!pa) { free(acc); free(kw); return -1; }
	for (int k = 0; k < g->n; k++) {
		struct inst *x = &in[g->members[k]];
		double w = kw[k];
		if (!(w > 0)) continue;
		double lo = INFINITY, hi = -INFINITY;
		for (int i = 1; i < 2 * g->total; i += 2) { if (x->rs[i] < lo) lo = x->rs[i]; if (x->rs[i] > hi) hi = x->rs[i]; }
		if (hi > lo) hsum += w * (hi - lo);
		w2 += w * w;
		asum += w * shape_area(x->rs, g);
		rsum += w * shape_rough(x->rs, g, INFINITY);
		for (size_t i = 0; x->pp && x->pv && i < np; i++) {
			if (!isnan(x->pp[i])) { pa[i] += w * x->pp[i]; pw[i] += w; }
			if (!isnan(x->pv[i])) { va[i] += w * x->pv[i]; vw[i] += w; }
		}
		members++;
	}
	v->pres = profile_mean(pa, pw, g->nstrokes);
	v->speed = profile_mean(va, vw, g->nstrokes);
	free(pa);
	/* back on the line where its versions sit: the centred shape plus their centres'
	 * average, with the same weights (one version's baseline is off by its own error) */
	double scx = 0, scy = 0;
	{
		double *raw = malloc((size_t)g->total * 2 * sizeof *raw), cw = 0;
		if (!raw) { free(acc); free(kw); return -1; }
		for (int k = 0; k < g->n; k++) {
			struct inst *x = &in[g->members[k]];
			double w = kw[k], cx = 0, cy = 0;
			if (!(w > 0)) continue;
			int o = 0;
			for (size_t j = 0; j < x->nstrokes; j++) { resample(&x->s[j], (size_t)g->pts[j], raw + 2 * o); o += g->pts[j]; }
			for (int i = 0; i < o; i++) { cx += raw[2 * i]; cy += raw[2 * i + 1]; }
			if (!o) continue;
			scx += w * cx / o; scy += w * cy / o; cw += w;
		}
		free(raw);
		if (cw > 0) { scx /= cw; scy /= cw; }
	}
	v->nstrokes = g->nstrokes;
	v->s = calloc(g->nstrokes, sizeof *v->s);
	if (!v->s) { free(acc); free(kw); return -1; }
	int o = 0;
	double xmin = INFINITY, xmax = -INFINITY;
	for (size_t j = 0; j < g->nstrokes; j++) {
		int n = g->pts[j];
		struct hw_lstroke *t = &v->s[j];
		t->n = (size_t)n;
		t->x = malloc((size_t)n * sizeof(double));
		t->y = malloc((size_t)n * sizeof(double));
		if (!t->x || !t->y) { free(acc); free(kw); return -1; }
		for (int i = 0; i < n; i++) { t->x[i] = acc[2 * (o + i)] + scx; t->y[i] = acc[2 * (o + i) + 1] + scy; }
		/* the jitter that is left: one pass of [1 2 1] / 4, the ends kept */
		if (n > 2) {
			double px = t->x[0], py = t->y[0];
			for (int i = 1; i < n - 1; i++) {
				double nx = (px + 2 * t->x[i] + t->x[i + 1]) / 4, ny = (py + 2 * t->y[i] + t->y[i + 1]) / 4;
				px = t->x[i]; py = t->y[i];
				t->x[i] = nx; t->y[i] = ny;
			}
		}
		for (int i = 0; i < n; i++) { if (t->x[i] < xmin) xmin = t->x[i]; if (t->x[i] > xmax) xmax = t->x[i]; }
		o += n;
	}
	/* as tall as its members on average (FR-006): what averaging and smoothing still take
	 * off (registered, little) is given back from its centre (where it sits), the width as
	 * it comes */
	{
		double lo = INFINITY, hi = -INFINITY, h = hsum / wsum, cy = 0;
		size_t np2 = 0;
		for (size_t j = 0; j < v->nstrokes; j++)
			for (size_t i = 0; i < v->s[j].n; i++) {
				double y = v->s[j].y[i];
				if (y < lo) lo = y;
				if (y > hi) hi = y;
				cy += y; np2++;
			}
		cy /= np2 ? np2 : 1;
		if (vs) {
			double *sh = malloc((size_t)g->total * 2 * sizeof *sh);
			int q = 0;
			for (size_t j = 0; sh && j < v->nstrokes; j++)
				for (size_t i = 0; i < v->s[j].n; i++, q++) { sh[2 * q] = v->s[j].x[i]; sh[2 * q + 1] = v->s[j].y[i]; }
			*vs = (struct vstat){ w2 > 0 ? wsum * wsum / w2 : 0, h, hi - lo, asum / wsum, sh ? shape_area(sh, g) : 0,
					      rsum / wsum, sh ? shape_rough(sh, g, INFINITY) : 0 };
			free(sh);
		}
		if (h >= HEIGHT_MIN && hi - lo > 1e-6 && h / (hi - lo) <= HEIGHT_GROW) {
			for (size_t j = 0; j < v->nstrokes; j++)
				for (size_t i = 0; i < v->s[j].n; i++) v->s[j].y[i] = cy + (v->s[j].y[i] - cy) * h / (hi - lo);
		}
	}
	for (size_t j = 0; j < v->nstrokes; j++)
		for (size_t i = 0; i < v->s[j].n; i++) v->s[j].x[i] -= xmin;
	*xmin_out = xmin;
	v->width = xmax - xmin;
	v->weight = wsum;
	v->members = members;
	/* its strokes as the model file keeps them, then its profiles from those */
	for (size_t j = 0; j < v->nstrokes; j++)
		for (size_t i = 0; i < v->s[j].n; i++) {
			v->s[j].x[i] = round(v->s[j].x[i] * 1e4) / 1e4;
			v->s[j].y[i] = round(v->s[j].y[i] * 1e4) / 1e4;
		}
	variant_measure(v);
	v->lc = v->rc = -1;
	free(acc); free(kw);
	return 0;
}

/* The best version of variant k (FR-035), made from its group around its seed as the variant
 * is: the samples written like it, each with its weight times bw, within twice the variant's
 * kernel; on the line where they sit, smoothed, x from the variant's left edge xmin. None
 * when they all weigh the same (the variant itself is the best) or it comes out as the
 * variant. */
#define BEST_SAME 0.005 /* x-heights RMS: a best version closer than this to its variant is it */
static void make_best(struct inst *in, const struct group *g, int seed, int k, double xmin, struct hw_variant *v)
{
	double lo = INFINITY, hi = -INFINITY;
	for (int i = 0; i < g->n; i++) {
		const struct inst *x = &in[g->members[i]];
		if (x->var != k || !x->rs) continue;
		if (x->bw < lo) lo = x->bw;
		if (x->bw > hi) hi = x->bw;
	}
	if (!(hi > lo * (1 + 1e-9)) || v->nstrokes != g->nstrokes) return;
	double *acc = calloc((size_t)g->total * 4 + 1, sizeof *acc), *raw = acc + 2 * g->total, wsum = 0, scx = 0, scy = 0;
	double *kw = calloc((size_t)g->n + 1, sizeof *kw);
	if (!acc || !kw) { free(acc); free(kw); return; }
	wsum = shape_mean(in, g, seed, k, 2 * SIGMA, INFINITY, kw, acc);
	for (int i = 0; wsum > 0 && i < g->n; i++) {
		struct inst *x = &in[g->members[i]];
		if (!(kw[i] > 0)) continue;
		int o = 0;
		double cx = 0, cy = 0;
		for (size_t j = 0; j < x->nstrokes; j++) { resample(&x->s[j], (size_t)g->pts[j], raw + 2 * o); o += g->pts[j]; }
		for (int q = 0; q < o; q++) { cx += raw[2 * q]; cy += raw[2 * q + 1]; }
		scx += kw[i] * cx / (o ? o : 1); scy += kw[i] * cy / (o ? o : 1);
	}
	free(kw);
	struct hw_lstroke *b = wsum > 0 ? calloc(g->nstrokes + 1, sizeof *b) : NULL;
	double dd = 0;
	int o = 0, bad = !b;
	for (size_t j = 0; !bad && j < g->nstrokes; j++) {
		int n = g->pts[j];
		struct hw_lstroke *t = &b[j];
		t->x = malloc(((size_t)n + 1) * sizeof(double));
		t->y = malloc(((size_t)n + 1) * sizeof(double));
		if (!t->x || !t->y || v->s[j].n != (size_t)n) { bad = 1; break; }
		t->n = (size_t)n;
		for (int i = 0; i < n; i++) { t->x[i] = acc[2 * (o + i)] + scx / wsum; t->y[i] = acc[2 * (o + i) + 1] + scy / wsum; }
		if (n > 2) {
			double px = t->x[0], py = t->y[0];
			for (int i = 1; i < n - 1; i++) {
				double nx = (px + 2 * t->x[i] + t->x[i + 1]) / 4, ny = (py + 2 * t->y[i] + t->y[i + 1]) / 4;
				px = t->x[i]; py = t->y[i];
				t->x[i] = nx; t->y[i] = ny;
			}
		}
		for (int i = 0; i < n; i++) {
			t->x[i] = round((t->x[i] - xmin) * 1e4) / 1e4;
			t->y[i] = round(t->y[i] * 1e4) / 1e4;
			dd += (t->x[i] - v->s[j].x[i]) * (t->x[i] - v->s[j].x[i]) + (t->y[i] - v->s[j].y[i]) * (t->y[i] - v->s[j].y[i]);
		}
		o += n;
	}
	free(acc);
	if (!bad && sqrt(dd / (o ? o : 1)) >= BEST_SAME) { v->best = b; return; }
	for (size_t j = 0; b && j < g->nstrokes; j++) { free(b[j].x); free(b[j].y); }
	free(b);
}

static int cmp_uint(const void *a, const void *b)
{
	unsigned x = *(const unsigned *)a, y = *(const unsigned *)b;
	return x < y ? -1 : x > y;
}

/* the guide line the ink of a sample sits on (prompted writing: two thirds of a line) */
static double guide_base(const struct hw_sample *s)
{
	if (!s->area.set || s->area.line <= 0) return NAN;
	struct dvec ys = { 0 };
	for (size_t j = 0; j < s->nstrokes; j++)
		for (size_t i = 0; i < s->strokes[j].n; i++) dpush(&ys, s->strokes[j].y[i]);
	double my = median(ys.v, ys.n, NAN);
	free(ys.v);
	if (isnan(my)) return NAN;
	double line = s->area.line, first = s->area.box[1] + line * 2 / 3;
	double k = ceil((my - first) / line);
	return first + (k > 0 ? k : 0) * line;
}

/* The spans follow the order of writing and split between two characters halfway between
 * where the recognizer saw each, so a stroke can sit in the spans of characters it isn't
 * part of. Two cases are put right here, by where the ink lies, before a character counts
 * as having strokes of its own (joined writing, one stroke across two letters, stays
 * shared):
 * - a mark (a full stop, a comma) has its own small stroke, and its span also holds a
 *   neighbour's stroke more than twice as long that lies wholly beside the mark's ink and
 *   not under it (a mark above, an i's dot, stays): that stroke is the neighbour's alone;
 * - a short stroke written after the strokes it belongs with (an i's dot, the bar of a t or
 *   an f, the arms of an E, the bar of an H, the second stroke of an x or a k) goes to the
 *   character whose other strokes it lies across, and to that one alone. */
static double stroke_len(const struct hw_ink *k)
{
	double t = 0;
	for (size_t i = 1; i < k->n; i++) t += hypot(k->x[i] - k->x[i - 1], k->y[i] - k->y[i - 1]);
	return t;
}

static void ink_box(const struct hw_ink *k, double b[4])
{
	for (size_t i = 0; i < k->n; i++) {
		if (k->x[i] < b[0]) b[0] = k->x[i];
		if (k->x[i] > b[2]) b[2] = k->x[i];
		if (k->y[i] < b[1]) b[1] = k->y[i];
		if (k->y[i] > b[3]) b[3] = k->y[i];
	}
}

/* a character's strokes other than j: their box (y down), their length, whether one of them
 * is the character's alone; 0 when it has no others */
static int others(const struct hw_sample *s, const int *sp, int j, const int *cnt, double b[4], double *len, int *own)
{
	int n = 0;
	b[0] = b[1] = INFINITY; b[2] = b[3] = -INFINITY;
	*len = 0; *own = 0;
	for (const int *p = sp; p && *p >= 0; p++) {
		if (*p == j || (size_t)*p >= s->nstrokes || !s->strokes[*p].n) continue;
		ink_box(&s->strokes[*p], b);
		*len += stroke_len(&s->strokes[*p]);
		if (cnt[*p] == 1) *own = 1;
		n++;
	}
	return n;
}

/* whether stroke j crosses another stroke of span sp (two of their segments cross) */
static int crosses(const struct hw_sample *s, const int *sp, int j)
{
	const struct hw_ink *a = &s->strokes[j];
	for (const int *p = sp; p && *p >= 0; p++) {
		if (*p == j || (size_t)*p >= s->nstrokes) continue;
		const struct hw_ink *b = &s->strokes[*p];
		for (size_t u = 1; u < a->n; u++)
			for (size_t v = 1; v < b->n; v++) {
				double ax = a->x[u - 1], ay = a->y[u - 1], bx = a->x[u], by = a->y[u];
				double cx = b->x[v - 1], cy = b->y[v - 1], dx = b->x[v], dy = b->y[v];
				double d1 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax), d2 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax);
				double d3 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx), d4 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx);
				if (d1 * d2 < 0 && d3 * d4 < 0) return 1;
			}
	}
	return 0;
}

static int span_has(const int *sp, int j)
{
	for (const int *p = sp; p && *p >= 0; p++) if (*p == j) return 1;
	return 0;
}

static void span_drop(int *sp, int j)
{
	int w = 0;
	for (int r = 0; sp && sp[r] >= 0; r++) if (sp[r] != j) sp[w++] = sp[r];
	if (sp) sp[w] = -1;
}

static void spilled_strokes(struct hw_sample *s, size_t nc, const unsigned *cps, int *cnt)
{
	for (size_t j = 0; j < s->nstrokes; j++) {
		const struct hw_ink *sj = &s->strokes[j];
		if (cnt[j] < 2 || !sj->n) continue;
		double sb[4] = { INFINITY, INFINITY, -INFINITY, -INFINITY };
		ink_box(sj, sb);
		/* of the characters it is with, the one it lies beside; with several (a letter's
		 * arm beside its stem and its neighbour) it stays shared */
		int beside = -1, nb = 0;
		for (size_t k = 0; k < s->nspans && k < nc; k++) {
			if (cps[k] == ' ' || !span_has(s->spans[k], (int)j)) continue;
			double b[4], ol;
			int own;
			if (!others(s, s->spans[k], (int)j, cnt, b, &ol, &own) || !own) continue;
			/* beside them: across at most a quarter of the narrower of the two */
			double ov = fmin(b[2], sb[2]) - fmax(b[0], sb[0]);
			if (ov > 0.25 * fmin(b[2] - b[0], sb[2] - sb[0])) continue;
			if (b[3] <= (sb[1] + sb[3]) / 2) continue;      /* a mark above it */
			beside = (int)k;
			nb++;
		}
		if (nb != 1) continue;
		span_drop(s->spans[beside], (int)j);
		cnt[j]--;
	}
}

/* characters with ink wholly above the rest of theirs (a dot, an accent, a colon's upper dot) */
static int has_above(unsigned c)
{
	return (c < 128 && c && strchr("ij:;!?=%\"", (int)c)) || (c >= 0xC0 && c <= 0x24F && c != 0xD7 && c != 0xF7);
}
/* of those, letters whose dot or accent the person may set well beside the stem, as a
 * late dot often is */
static int dotted(unsigned c) { return c == 'i' || c == 'j' || (c >= 0xC0 && c <= 0x24F && c != 0xD7 && c != 0xF7); }

static void delayed_strokes(struct hw_sample *s, size_t nc, const unsigned *cps, int *cnt)
{
	double small;
	if (s->area.set && s->area.line > 0) small = 0.4 * s->area.line;
	else {
		struct dvec ls = { 0 };
		for (size_t j = 0; j < s->nstrokes; j++) dpush(&ls, stroke_len(&s->strokes[j]));
		small = 0.5 * median(ls.v, ls.n, 0);
		free(ls.v);
	}
	if (small <= 0) return;
	double line = s->area.set && s->area.line > 0 ? s->area.line : 0;
	for (size_t j = 0; j < s->nstrokes; j++) {
		const struct hw_ink *sj = &s->strokes[j];
		if (!cnt[j] || !sj->n || stroke_len(sj) > small) continue;
		double sb[4] = { INFINITY, INFINITY, -INFINITY, -INFINITY };
		ink_box(sj, sb);
		double mx = (sb[0] + sb[2]) / 2, my = (sb[1] + sb[3]) / 2, ext = fmax(sb[2] - sb[0], sb[3] - sb[1]);
		int best = -1, dot = -1, alone = 0;
		double bd = INFINITY, dd = INFINITY;
		for (size_t k = 0; k < s->nspans && k < nc; k++) {
			if (cps[k] == ' ' || !s->spans[k]) continue;
			int has = span_has(s->spans[k], (int)j);
			double b[4], ol;
			int own;
			/* the only stroke of its own a character has stays (its other strokes shared),
			 * and so does one longer than HW_LETTERS_LATE_SHARE of the rest of the character it
			 * is with (two stems alike: an H), unless that
			 * is a mark (a 5's body below its bar is no late stroke of the 6 beside it) */
			if (!others(s, s->spans[k], (int)j, cnt, b, &ol, &own) ||
			    (has && cnt[j] == 1 && (!own || (stroke_len(sj) > HW_LETTERS_LATE_SHARE * ol && ol >= 0.25 * small)))) {
				alone |= has;
				continue;
			}
			double h = b[3] - b[1], dx = mx < b[0] ? b[0] - mx : mx > b[2] ? mx - b[2] : 0;
			int above = sb[3] < b[1] - 0.1 * h;   /* wholly above its ink, beyond the pen's wobble */
			/* a dot or an accent above an i, a j or an accented letter, up to two of its
			 * heights (or the writing paper's fifth of a line) to either side and three
			 * above: the nearest such letter */
			double reach = fmax(2 * h, line / 5);
			if (dotted(cps[k]) && above && ext <= fmax(0.8 * h, line / 20) && dx <= reach && my >= b[1] - 1.5 * reach &&
			    (dx < dd - 1e-9 || (dx <= dd + 1e-9 && has))) { dd = dx; dot = (int)k; }
			/* across the character's ink (over half of the narrower of the two) or
			 * touching its side, from above it (only where the character has ink above
			 * the rest; else rising at most its height above it) down to its baseline */
			double ov = fmin(sb[2], b[2]) - fmax(sb[0], b[0]), gap = -ov;
			if (ov < 0.5 * fmin(sb[2] - sb[0], b[2] - b[0]) && !(dx <= 0 || gap <= 0.25 * h)) continue;
			if (my > b[3] + 0.25 * h) continue;
			if (has_above(cps[k]) ? my < b[1] - h : (above || sb[1] < b[1] - h)) continue;
			/* of several, one whose ink its middle is over, then one it lies across, then
			 * one it touches; of those the one it is centred over; then one that has it */
			double d = (dx <= 0 ? 0 : ov >= 0.5 * fmin(sb[2] - sb[0], b[2] - b[0]) ? 1e6 : 2e6) +
				   fabs(mx - (b[0] + b[2]) / 2);
			if (d < bd - 1e-9 || (d <= bd + 1e-9 && has)) { bd = d; best = (int)k; }
		}
		/* where it is already, when that is the best place (a colon's upper dot stays) */
		int stay = best >= 0 && cnt[j] == 1 && span_has(s->spans[best], (int)j);
		if (dot >= 0 && !(stay && has_above(cps[best]))) best = dot;
		if (alone || best < 0 || (cnt[j] == 1 && span_has(s->spans[best], (int)j))) continue;
		/* it goes to that one, and only to that one */
		for (size_t k = 0; k < s->nspans && k < nc; k++) {
			int *p = s->spans[k];
			if (!p) continue;
			span_drop(p, (int)j);
			if ((int)k != best) continue;
			int w = 0;
			while (p[w] >= 0) w++;
			int *x = realloc(p, (size_t)(w + 2) * sizeof *x);
			if (!x) continue;
			int q = w;
			while (q > 0 && x[q - 1] > (int)j) { x[q] = x[q - 1]; q--; }
			x[q] = (int)j;
			x[w + 1] = -1;
			s->spans[k] = x;
		}
		cnt[j] = 1;
	}
}


int hw_letters_place(const struct hw_ink *ink, size_t n, double line, const unsigned *cps, size_t nc, int **spans)
{
	struct hw_sample s;
	memset(&s, 0, sizeof s);
	s.strokes = (struct hw_ink *)ink;
	s.nstrokes = n;
	s.spans = spans;
	s.nspans = nc;
	s.area.set = line > 0;
	s.area.line = line;
	int *cnt = calloc(n + 1, sizeof *cnt);
	if (!cnt) return -1;
	for (size_t k = 0; k < nc; k++)
		for (int *p = spans[k]; p && *p >= 0; p++) if ((size_t)*p < n) cnt[*p]++;
	spilled_strokes(&s, nc, cps, cnt);
	delayed_strokes(&s, nc, cps, cnt);
	free(cnt);
	return 0;
}

/* A sample's characters with strokes of their own (FR-007), after the spans are put right
 * by where the ink lies (above): own[k] for each of the nc characters. The spans change. */
/* per character whether its strokes are its own (own[k]); with cnt_out, how many characters
 * each stroke is with, as they are counted here (the caller frees it) */
static int own_chars(struct hw_sample *s, const unsigned *cps, size_t nc, char *own, int **cnt_out)
{
	int *cnt = calloc(s->nstrokes + 1, sizeof *cnt);
	if (!cnt) return -1;
	for (size_t k = 0; k < s->nspans && k < nc; k++)
		for (int *p = s->spans[k]; p && *p >= 0; p++) if ((size_t)*p < s->nstrokes) cnt[*p]++;
	spilled_strokes(s, nc, cps, cnt);
	delayed_strokes(s, nc, cps, cnt);
	for (size_t k = 0; k < nc; k++) {
		int *sp = k < s->nspans ? s->spans[k] : NULL;
		own[k] = 0;
		if (cps[k] == ' ' || !sp || sp[0] < 0) continue;
		int ok = 1, ink = 0;
		for (int *p = sp; *p >= 0; p++) {
			if ((size_t)*p >= s->nstrokes || cnt[*p] != 1) ok = 0;
			else if (s->strokes[*p].n) ink = 1;
		}
		own[k] = (char)(ok && ink);
	}
	/* a character whose ink a neighbour's stroke in its word crosses or starts on (a bar
	 * or a bowl written on into the next letter, a lead-in through the last) has a part
	 * of it in the other's ink and the other a part of it: neither is a whole letter */
	char *part = calloc(nc + 1, 1);
	for (size_t k = 0; part && k < nc && k < s->nspans; k++) {
		if (!s->spans[k] || cps[k] == ' ') continue;
		double b[4] = { INFINITY, INFINITY, -INFINITY, -INFINITY };
		for (const int *p = s->spans[k]; *p >= 0; p++) if ((size_t)*p < s->nstrokes) ink_box(&s->strokes[*p], b);
		double near = HW_LETTERS_TOUCH * (b[3] - b[1]);
		for (size_t o = k + 1; o <= k + 2 && o < nc && o < s->nspans && cps[o] != ' '; o++)
			for (const int *p = s->spans[o]; p && *p >= 0; p++) {
				if ((size_t)*p >= s->nstrokes || !s->strokes[*p].n || span_has(s->spans[k], *p)) continue;
				const struct hw_ink *a = &s->strokes[*p];
				int on = crosses(s, s->spans[k], *p);
				for (const int *r = s->spans[k]; !on && *r >= 0; r++)
					for (size_t v = 0; (size_t)*r < s->nstrokes && v < s->strokes[*r].n && !on; v++)
						on = hypot(a->x[0] - s->strokes[*r].x[v], a->y[0] - s->strokes[*r].y[v]) <= near ||
						     hypot(a->x[a->n - 1] - s->strokes[*r].x[v], a->y[a->n - 1] - s->strokes[*r].y[v]) <= near;
				if (on) part[k] = part[o] = 1;
			}
	}
	for (size_t k = 0; part && k < nc; k++) if (part[k]) own[k] = 0;
	free(part);
	if (cnt_out) *cnt_out = cnt;
	else free(cnt);
	return 0;
}

/* ---------------------------------------------------------------- usable samples (FR-030) */

/* letters/usable: per prompted sample, the characters it has with strokes of their own, so
 * counting them needn't read every sample again (derived like the size file: made again for
 * samples it lacks; written by a count or a build, skipped while another process holds the
 * store's lock) */
#define USABLE_HEAD "glassine-handwriting-usable 1"

struct urec { char id[17]; char *cps; };       /* cps: "-" or code points in hex, sorted, "," */

static int cmp_urec(const void *a, const void *b) { return strcmp(((const struct urec *)a)->id, ((const struct urec *)b)->id); }

static void urecs_free(struct urec *r, size_t n)
{
	for (size_t i = 0; i < n; i++) free(r[i].cps);
	free(r);
}

static struct urec *usable_load(size_t *n)
{
	char p[4096], head[64];
	*n = 0;
	if (hw_store_path(p, sizeof p, "letters/usable")) return NULL;
	FILE *f = fopen(p, "re");
	if (!f) return NULL;
	snprintf(head, sizeof head, "%s %d\n", USABLE_HEAD, HW_LETTERS_USABLE);
	char *line = NULL;
	size_t cap = 0, rc = 0;
	struct urec *r = NULL;
	if (getline(&line, &cap, f) < 0 || strcmp(line, head)) { free(line); fclose(f); return NULL; }
	ssize_t k;
	while ((k = getline(&line, &cap, f)) > 0) {
		if (line[k - 1] == '\n') line[--k] = 0;
		char *sp = strchr(line, ' ');
		if (!sp || sp - line != 16 || !sp[1]) continue;
		if (*n == rc) { rc = rc ? rc * 2 : 256; struct urec *t = realloc(r, rc * sizeof *r); if (!t) break; r = t; }
		memcpy(r[*n].id, line, 16); r[*n].id[16] = 0;
		r[*n].cps = strdup(sp + 1);
		if (r[*n].cps) (*n)++;
	}
	free(line);
	fclose(f);
	if (r) qsort(r, *n, sizeof *r, cmp_urec);
	return r;
}

static void usable_save(const struct urec *r, size_t n)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "lock")) return;
	int fd = open(p, O_RDWR | O_CLOEXEC);
	if (fd < 0) return;
	if (flock(fd, LOCK_EX | LOCK_NB)) { close(fd); return; }
	struct sbuf b = { 0 };
	sput(&b, "%s %d\n", USABLE_HEAD, HW_LETTERS_USABLE);
	for (size_t i = 0; i < n; i++) sput(&b, "%s %s\n", r[i].id, r[i].cps);
	if (b.s && !hw_store_mkdir("letters")) hw_store_replace("letters/usable", b.s, b.n);
	free(b.s);
	close(fd);
}

/* the cps text of a sample whose own characters are known */
static char *usable_cps(const unsigned *cps, size_t nc, const char *own)
{
	unsigned *u = malloc((nc + 1) * sizeof *u);
	size_t nu = 0;
	if (!u) return NULL;
	for (size_t k = 0; k < nc; k++) if (own[k]) u[nu++] = cps[k];
	qsort(u, nu, sizeof *u, cmp_uint);
	struct sbuf b = { 0 };
	for (size_t k = 0; k < nu; k++) if (!k || u[k] != u[k - 1]) sput(&b, "%s%x", b.n ? "," : "", u[k]);
	free(u);
	return b.s ? b.s : strdup("-");
}

/* a prompted sample whose characters' strokes are known: the fit check passed (spans
 * exact), or a prompt of one character alone that the person confirmed after a failed
 * check, whose strokes can only be that character's (spec 003 FR-007). 1 when it counts,
 * the lone one's spans then all its strokes */
static int known_spans(struct hw_sample *s)
{
	if (strcmp(s->source, "prompted") || !s->nstrokes) return 0;
	if (!strcmp(s->spans_source, "exact")) return 1;
	unsigned cp[2];
	if (strcmp(s->spans_source, "inferred") || hw_utf8_decode(s->text, cp, 2) != 1 || cp[0] == ' ') return 0;
	int *sp = malloc((s->nstrokes + 1) * sizeof *sp);
	int **spans = sp ? calloc(1, sizeof *spans) : NULL;
	if (!spans) { free(sp); return 0; }
	for (size_t j = 0; j < s->nstrokes; j++) sp[j] = (int)j;
	sp[s->nstrokes] = -1;
	spans[0] = sp;
	for (size_t k = 0; k < s->nspans; k++) free(s->spans[k]);
	free(s->spans);
	s->spans = spans;
	s->nspans = 1;
	return 1;
}

/* a sample's cps text from its line ("-": no known spans, or nothing usable) */
static char *usable_of_line(const char *json)
{
	struct hw_sample s;
	char err[128];
	if (hw_sample_parse(json, &s, err, sizeof err)) return strdup("-");
	char *out = NULL;
	if (known_spans(&s)) {
		unsigned *cps = malloc(4097 * sizeof *cps);
		char *own = malloc(4097);
		if (cps && own) {
			size_t nc = hw_utf8_decode(s.text, cps, 4096);
			if (nc > 4096) nc = 4096;
			if (!own_chars(&s, cps, nc, own, NULL)) out = usable_cps(cps, nc, own);
		}
		free(cps); free(own);
	}
	hw_sample_free(&s);
	return out ? out : strdup("-");
}

static int cmp_lcount(const void *a, const void *b) { return cmp_uint(&((const struct hw_lcount *)a)->cp, &((const struct hw_lcount *)b)->cp); }

int hw_letters_usable(struct hw_lcount **out, size_t *nout, long *samples, void (*progress)(size_t, size_t))
{
	*out = NULL; *nout = 0; *samples = 0;
	const struct hw_index_entry *ix;
	size_t nix;
	if (hw_samples_index(&ix, &nix)) return -1;
	struct urec *want = calloc(nix + 1, sizeof *want);
	size_t nw = 0;
	if (!want) return -1;
	/* the index is borrowed until the next store call: the ids first */
	for (size_t i = 0; i < nix; i++) if (!strcmp(ix[i].meta.source, "prompted")) memcpy(want[nw++].id, ix[i].meta.id, 17);
	qsort(want, nw, sizeof *want, cmp_urec);
	size_t k = 0;
	for (size_t i = 0; i < nw; i++) if (!k || strcmp(want[k - 1].id, want[i].id)) want[k++] = want[i];
	nw = k;
	size_t nf;
	struct urec *file = usable_load(&nf);
	size_t miss = 0, done = 0;
	for (size_t i = 0; i < nw; i++) {
		struct urec *f = file ? bsearch(&want[i], file, nf, sizeof *file, cmp_urec) : NULL;
		if (f) { want[i].cps = f->cps; f->cps = NULL; } else miss++;
	}
	int ret = 0;
	for (size_t i = 0; i < nw && ret == 0; i++) {
		if (want[i].cps) continue;
		int err;
		char *line = hw_samples_line(want[i].id, NULL, &err);
		if (!line && err) { ret = -1; break; }
		want[i].cps = line ? usable_of_line(line) : strdup("-");
		free(line);
		if (!want[i].cps) ret = -1;
		if (progress) progress(++done, miss);
	}
	if (ret == 0 && (miss || nf != nw)) usable_save(want, nw);
	/* a sample the person left out of a character doesn't count for it (FR-031) */
	struct hw_mark *MK = NULL;
	size_t nmk = 0;
	if (ret == 0 && hw_marks(&MK, &nmk)) ret = -1;
	/* the counts: each sample once per character */
	struct hw_lcount *c = NULL;
	size_t nc = 0, cc = 0;
	for (size_t i = 0; ret == 0 && i < nw; i++) {
		const char *p = want[i].cps;
		if (!strcmp(p, "-")) continue;
		(*samples)++;
		while (*p) {
			char *e;
			unsigned long v = strtoul(p, &e, 16);
			if (e == p) break;
			if (nmk && hw_mark_of(MK, nmk, want[i].id, (unsigned)v) == HW_MARK_OUT) { p = *e == ',' ? e + 1 : e; continue; }
			if (nc == cc) { cc = cc ? cc * 2 : 128; struct hw_lcount *t = realloc(c, cc * sizeof *c); if (!t) { ret = -1; break; } c = t; }
			c[nc++] = (struct hw_lcount){ (unsigned)v, 1 };
			p = *e == ',' ? e + 1 : e;
		}
	}
	urecs_free(want, nw);
	urecs_free(file, nf);
	free(MK);
	if (ret) { free(c); return -1; }
	if (c) qsort(c, nc, sizeof *c, cmp_lcount);
	k = 0;
	for (size_t i = 0; i < nc; i++) {
		if (k && c[k - 1].cp == c[i].cp) c[k - 1].samples++;
		else c[k++] = c[i];
	}
	*out = c;
	*nout = k;
	return 0;
}

static int build_progress;      /* letters build --progress */
/* one character's build (relearn, its occurrences): only that character's variants, only
 * the samples with it; its occurrences recorded */
static unsigned build_only;
static struct hw_metrics build_m;      /* the model's, for one character's build */
static double build_narrow = 1;         /* the model's narrowing, for one character's build */
struct occrec { char id[17]; int pos, state, inst; char *text; };
static struct occrec *build_occ;
static size_t build_nocc, build_cocc;

static int occ_add(const char *id, int pos, int state, int inst, const char *text)
{
	if (build_nocc == build_cocc) {
		size_t c = build_cocc ? build_cocc * 2 : 64;
		struct occrec *x = realloc(build_occ, c * sizeof *x);
		if (!x) return -1;
		build_occ = x;
		build_cocc = c;
	}
	struct occrec *o = &build_occ[build_nocc];
	snprintf(o->id, sizeof o->id, "%s", id);
	o->pos = pos; o->state = state; o->inst = inst;
	o->text = strdup(text ? text : "");
	return o->text ? (int)build_nocc++ : -1;
}

static void occ_free(void)
{
	for (size_t i = 0; i < build_nocc; i++) free(build_occ[i].text);
	free(build_occ);
	build_occ = NULL;
	build_nocc = build_cocc = 0;
}

static int cmp_id17(const void *a, const void *b) { return strcmp((const char *)a, (const char *)b); }

/* the hash of the marks of cp in a sorted list (by id, code point); "" none */
static void marks_hash_of(const struct hw_mark *m, size_t n, unsigned cp, char out[17])
{
	uint64_t h = FNV0;
	int any = 0;
	for (size_t i = 0; i < n; i++) {
		if (m[i].cp != cp) continue;
		char b[40];
		snprintf(b, sizeof b, "%s %d", m[i].id, m[i].kind);
		h = fnv(h, b, strlen(b) + 1);
		any = 1;
	}
	if (any) snprintf(out, 17, "%016llx", (unsigned long long)h);
	else out[0] = 0;
}

void hw_letters_marks_hash(unsigned cp, char out[17])
{
	struct hw_mark *m;
	size_t n;
	out[0] = 0;
	if (hw_marks(&m, &n)) return;
	marks_hash_of(m, n, cp, out);
	free(m);
}

int hw_letters_marks_changed(const struct hw_letters *L)
{
	struct hw_mark *m;
	size_t n;
	if (hw_marks(&m, &n)) return 1;
	int changed = 0;
	char h[17];
	/* a model from before the marks: its versions don't name their samples */
	for (size_t i = 0; i < L->n && !changed; i++)
		for (int v = 0; v < L->l[i].nvar && !changed; v++) changed = L->l[i].var[v].nids == 0;
	/* a character in the model whose marks are not those it was built with */
	for (size_t i = 0; i < L->n && !changed; i++) { marks_hash_of(m, n, L->l[i].cp, h); changed = strcmp(h, L->l[i].marks) != 0; }
	/* or a mark of a character the model has none of but whose samples it saw */
	for (size_t i = 0; i < n && !changed; i++) {
		int have = 0;
		for (size_t k = 0; k < L->n && !have; k++) have = L->l[k].cp == m[i].cp;
		changed = !have;
	}
	free(m);
	return changed;
}

/* ---------------------------------------------------------------- neighbour rules (FR-038) */

struct nocc { unsigned nb; int side, sample, var; };

static int cmp_nocc(const void *a, const void *b)
{
	const struct nocc *x = a, *y = b;
	if (x->side != y->side) return x->side - y->side;
	if (x->nb != y->nb) return x->nb < y->nb ? -1 : 1;
	return x->sample - y->sample;
}

static int cmp_rule(const void *a, const void *b)
{
	const struct hw_rule *x = a, *y = b;
	double gx = x->hits - x->cycle, gy = y->hits - y->cycle;
	if (gx > gy + 1e-9) return -1;
	if (gy > gx + 1e-9) return 1;
	if (x->side != y->side) return x->side - y->side;
	return x->nb < y->nb ? -1 : x->nb > y->nb;
}

/* A letter's neighbour rules from its versions v (their variants set, the cycle made): per
 * character before it and after it, the variant written there most, when written there
 * HW_RULE_MIN times, that variant in HW_RULE_SHARE of them and HW_RULE_LIFT more than in
 * the cycle, and the rule wins on samples it
 * was not made from: leaving out one sample at a time, the variant the others write most
 * there matches the left-out sample's more often than the cycle's (each variant as often as
 * its share of the cycle's steps). Variants left out count for none. */
static void letter_rules(struct hw_letter *l, const struct inst *in, const int *v, int nv)
{
	l->nrules = 0;
	if (l->nvar < 2) return;
	struct nocc *o = malloc((2 * (size_t)nv + 1) * sizeof *o);
	if (!o) return;
	double share[HW_LETTERS_VARIANTS] = { 0 };
	for (int k = 0; k < HW_LETTERS_SLOTS; k++) share[l->slot[k]] += 1.0 / HW_LETTERS_SLOTS;
	size_t n = 0;
	for (int i = 0; i < nv; i++) {
		const struct inst *x = &in[v[i]];
		if (x->var < 0 || x->var >= l->nvar || l->var[x->var].out) continue;
		if (x->prev) o[n++] = (struct nocc){ x->prev, 0, x->sample, x->var };
		if (x->next) o[n++] = (struct nocc){ x->next, 1, x->sample, x->var };
	}
	qsort(o, n, sizeof *o, cmp_nocc);
	struct hw_rule R[64];
	int nr = 0;
	for (size_t i = 0; i < n;) {
		size_t j = i;
		while (j < n && o[j].side == o[i].side && o[j].nb == o[i].nb) j++;
		int m = (int)(j - i), cnt[HW_LETTERS_VARIANTS] = { 0 }, top = 0;
		for (size_t q = i; q < j; q++) cnt[o[q].var]++;
		for (int k = 1; k < l->nvar; k++) if (cnt[k] > cnt[top]) top = k;
		if (m >= HW_RULE_MIN && cnt[top] * HW_RULE_SHARE_DEN >= m * HW_RULE_SHARE_NUM &&
		    (double)cnt[top] / m >= share[top] + HW_RULE_LIFT - 1e-9) {
			double hits = 0, cyc = 0;
			for (size_t q = i; q < j;) {
				size_t r = q;
				int cs[HW_LETTERS_VARIANTS] = { 0 }, p = -1;
				while (r < j && o[r].sample == o[q].sample) cs[o[r++].var]++;
				for (int k = 0; k < l->nvar; k++)
					if (cnt[k] - cs[k] > 0 && (p < 0 || cnt[k] - cs[k] > cnt[p] - cs[p])) p = k;
				for (int k = 0; k < l->nvar; k++) {
					cyc += cs[k] * share[k];
					hits += p >= 0 ? (k == p) * cs[k] : cs[k] * share[k];
				}
				q = r;
			}
			if (hits > cyc + 1e-9 && nr < 64)
				R[nr++] = (struct hw_rule){ o[i].nb, o[i].side, top, m, cnt[top], round(hits * 1e4) / 1e4, round(cyc * 1e4) / 1e4 };
		}
		i = j;
	}
	free(o);
	qsort(R, (size_t)nr, sizeof *R, cmp_rule);
	for (int k = 0; k < nr && k < HW_LETTERS_RULES; k++) l->rule[l->nrules++] = R[k];
}

/* ---------------------------------------------------------------- joined pairs (FR-039) */

/* a pair of neighbouring letters in a sample: kind 0 written apart (or not plainly joined),
 * 1 joined, 2 joined with the pair's strokes its own (none shared with a third character) */
struct jocc { unsigned a, b; int sample, pos, kind; };

/* how close a letter's own samples are to its versions (FR-039): per letter the
 * HW_JOINED_ORDINARY quantile of each own sample's distance to its nearest version; NAN
 * with fewer than HW_LETTERS_MIN */
struct jref { unsigned cp; double d; };

static int cmp_ord(const void *x, const void *y) { return *(const int *)x - *(const int *)y; }

/* the strokes of span sp but `skip`, in writing order, into k (at most 64); their count */
static size_t span_strokes(const struct hw_sample *s, const int *sp, int skip, struct hw_ink *k, int *ord)
{
	size_t n = 0;
	for (const int *p = sp; *p >= 0 && n < 64; p++)
		if (*p != skip && (size_t)*p < s->nstrokes && s->strokes[*p].n) ord[n++] = *p;
	qsort(ord, n, sizeof *ord, cmp_ord);
	for (size_t j = 0; j < n; j++) k[j] = s->strokes[ord[j]];
	return n;
}

/* the nearest version of letter l to the strokes k (y down, sample units) whose stroke at
 * place `at` (-1 none) is as long as k's within HW_JOINED_LEN times; INFINITY none */
static double near_version(const struct hw_letter *l, const struct hw_ink *k, size_t n, double xh, int at)
{
	double best = INFINITY;
	for (int v = 0; l && v < l->nvar; v++) {
		const struct hw_variant *x = &l->var[v];
		if (x->out || x->nstrokes != n) continue;
		if (at >= 0) {
			double a = 0, b = slen(&x->s[at]);
			for (size_t i = 1; i < k[at].n; i++) a += hypot(k[at].x[i] - k[at].x[i - 1], k[at].y[i] - k[at].y[i - 1]);
			a /= xh;
			if (!(a <= HW_JOINED_LEN * b && b <= HW_JOINED_LEN * a)) continue;
		}
		double d = hw_neat_variant_dist(k, n, xh, x);
		if (d < best) best = d;
	}
	return best;
}

static const struct hw_letter *letter_get(const struct hw_letters *L, unsigned cp)
{
	for (size_t i = 0; i < L->n; i++) if (L->l[i].cp == cp) return &L->l[i];
	return NULL;
}

static double ordinary(struct jref **R, size_t *nr, const struct hw_letters *L, struct smp *S, size_t ns, unsigned cp,
		       const struct hw_mark *MK, size_t nmk)
{
	for (size_t i = 0; i < *nr; i++) if ((*R)[i].cp == cp) return (*R)[i].d;
	const struct hw_letter *l = letter_get(L, cp);
	struct dvec d = { 0 };
	unsigned *cps = malloc(4097 * sizeof *cps);
	for (size_t si = 0; l && cps && si < ns; si++) {
		struct smp *m = &S[si];
		if (m->xh <= 0) continue;
		size_t nc = hw_utf8_decode(m->s.text, cps, 4096);
		if (nc > 4096) nc = 4096;
		for (size_t k = 0; k < nc && k < m->s.nspans; k++) {
			if (cps[k] != cp || !m->s.spans[k] || hw_mark_of(MK, nmk, m->id, cp) == HW_MARK_OUT) continue;
			/* its strokes its own: in no other span */
			int own = 1;
			for (const int *p = m->s.spans[k]; *p >= 0 && own; p++)
				for (size_t q = 0; q < nc && q < m->s.nspans && own; q++)
					if (q != k && m->s.spans[q] && span_has(m->s.spans[q], *p)) own = 0;
			if (!own) continue;
			struct hw_ink ink[64];
			int ord[64];
			size_t n = span_strokes(&m->s, m->s.spans[k], -1, ink, ord);
			double x = n ? near_version(l, ink, n, m->xh, -1) : INFINITY;
			if (isfinite(x)) dpush(&d, x);
		}
	}
	free(cps);
	double q = NAN;
	if (d.n >= HW_LETTERS_MIN) {
		qsort(d.v, d.n, sizeof *d.v, cmp_double);
		q = HW_JOINED_SLACK * d.v[(size_t)fmin((double)d.n - 1, floor(HW_JOINED_ORDINARY * (double)d.n))];
	}
	free(d.v);
	struct jref *x = realloc(*R, (*nr + 1) * sizeof *x);
	if (x) { *R = x; x[(*nr)++] = (struct jref){ cp, q }; }
	return q;
}

/* the pair at k, k + 1 of sample m (letters la, lb), sharing only stroke j, written joined
 * (FR-039): j's first points with a's other strokes written like a, its last ones with b's
 * like b, at one place each (the ends that are, one stretch of j each), and between them at
 * most the pen's way across (HW_JOINED_BRIDGE) */
static int real_joint(const struct hw_letter *la, const struct hw_letter *lb, const struct smp *m, int k, int j,
		      double ra, double rb)
{
	const struct hw_ink *J = &m->s.strokes[j];
	if (J->n < 4 || !isfinite(ra) || !isfinite(rb)) return 0;
	struct hw_ink A[65], B[65];
	int oa[64], ob[64];
	size_t na = span_strokes(&m->s, m->s.spans[k], j, A, oa), nb = span_strokes(&m->s, m->s.spans[k + 1], j, B, ob);
	if (na >= 64 || nb >= 64) return 0;
	/* the run's place among the letter's strokes: by writing order */
	size_t pa = 0, pb = 0;
	while (pa < na && oa[pa] < j) pa++;
	while (pb < nb && ob[pb] < j) pb++;
	memmove(A + pa + 1, A + pa, (na - pa) * sizeof *A);
	memmove(B + pb + 1, B + pb, (nb - pb) * sizeof *B);
	/* a's run: points [0, c); b's: [c, n) */
	size_t step = J->n / 64 + 1, a_lo = 0, a_hi = 0, b_lo = 0, b_hi = 0;
	int ra_runs = 0, rb_runs = 0, pra = 0, prb = 0;
	for (size_t c = 2; c + 1 < J->n; c += step) {
		struct hw_ink a = *J, b = *J;
		a.n = c;
		b.x += c; b.y += c; b.n = J->n - c;
		A[pa] = a; B[pb] = b;
		int oka = near_version(la, A, na + 1, m->xh, (int)pa) <= ra, okb = near_version(lb, B, nb + 1, m->xh, (int)pb) <= rb;
		if (oka) { if (!pra) { ra_runs++; a_lo = c; } a_hi = c; }
		if (okb) { if (!prb) { rb_runs++; b_lo = c; } b_hi = c; }
		pra = oka; prb = okb;
	}
	if (ra_runs != 1 || rb_runs != 1 || a_lo > b_hi) return 0;
	/* the shortest way between a's end and b's start */
	size_t e = a_hi < b_lo ? a_hi : b_lo, f = b_lo > e ? b_lo : e;
	double w = 0;
	for (size_t i = e; e < f && i <= f && i < J->n; i++) w += hypot(J->x[i] - J->x[i - 1], J->y[i] - J->y[i - 1]);
	return w <= HW_JOINED_BRIDGE * m->xh;
}

static int cmp_jocc(const void *x, const void *y)
{
	const struct jocc *a = x, *b = y;
	if (a->a != b->a) return a->a < b->a ? -1 : 1;
	if (a->b != b->b) return a->b < b->b ? -1 : 1;
	if (a->sample != b->sample) return a->sample - b->sample;
	return a->pos - b->pos;
}

static int span_ink(const struct hw_sample *s, const int *sp)
{
	for (const int *p = sp; p && *p >= 0; p++) if ((size_t)*p < s->nstrokes && s->strokes[*p].n) return 1;
	return 0;
}

/* distinct samples among o[i..j) of at least this kind */
static int jsamples(const struct jocc *o, size_t i, size_t j, int kind)
{
	int n = 0, last = -1;
	for (size_t q = i; q < j; q++) if (o[q].kind >= kind && o[q].sample != last) { n++; last = o[q].sample; }
	return n;
}

static void inst_free(struct inst *x)
{
	for (size_t j = 0; j < x->nstrokes; j++) { free(x->s[j].x); free(x->s[j].y); }
	free(x->s); free(x->rs); free(x->pp); free(x->pv);
}

/* The pair's glyph from its usable joined occurrences o[i..j), made as a variant is from
 * every stroke of both letters (their late strokes too): the versions with the most common
 * stroke count, averaged around the best supported one, the jitter smoothed, on the line;
 * none when fewer than HW_JOINED_MIN are of its way. 0 made, 1 none, -1 out of memory. */
static int joined_glyph(struct hw_joined *J, const struct hw_letters *L, struct smp *S, const struct jocc *o, size_t i, size_t j)
{
	size_t n = 0;
	struct inst *in = calloc(j - i + 1, sizeof *in);
	if (!in) return -1;
	for (size_t q = i; q < j; q++) {
		if (o[q].kind != 2) continue;
		struct smp *m = &S[o[q].sample];
		const int *sa = m->s.spans[o[q].pos], *sb = m->s.spans[o[q].pos + 1];
		int ord[512], no = 0;
		for (int side = 0; side < 2; side++)
			for (const int *p = side ? sb : sa; *p >= 0; p++) {
				int dup = 0;
				for (int t = 0; t < no; t++) dup |= ord[t] == *p;
				if (!dup && no < 512 && (size_t)*p < m->s.nstrokes && m->s.strokes[*p].n) ord[no++] = *p;
			}
		for (int a = 1; a < no; a++) for (int b = a; b > 0 && ord[b] < ord[b - 1]; b--) { int t = ord[b]; ord[b] = ord[b - 1]; ord[b - 1] = t; }
		struct inst *x = &in[n];
		x->sample = o[q].sample; x->pos = o[q].pos; x->var = -1; x->occ = -1;
		x->s = calloc((size_t)no + 1, sizeof *x->s);
		x->pp = malloc(((size_t)no + 1) * HW_LETTERS_PROFILE * sizeof *x->pp);
		x->pv = malloc(((size_t)no + 1) * HW_LETTERS_PROFILE * sizeof *x->pv);
		if (!x->s || !x->pp || !x->pv) { inst_free(x); continue; }
		x->x0 = x->y0 = INFINITY; x->x1 = x->y1 = -INFINITY;
		for (int a = 0; a < no; a++) {
			const struct hw_ink *k2 = &m->s.strokes[ord[a]];
			for (size_t t = 0; t < k2->n; t++) {
				if (k2->x[t] < x->x0) x->x0 = k2->x[t];
				if (k2->x[t] > x->x1) x->x1 = k2->x[t];
				if (k2->y[t] < x->y0) x->y0 = k2->y[t];
				if (k2->y[t] > x->y1) x->y1 = k2->y[t];
			}
		}
		for (int a = 0; a < no; a++) {
			const struct hw_ink *k2 = &m->s.strokes[ord[a]];
			struct hw_lstroke *t = &x->s[x->nstrokes];
			t->x = malloc(k2->n * sizeof(double)); t->y = malloc(k2->n * sizeof(double));
			if (!t->x || !t->y) { free(t->x); free(t->y); t->x = t->y = NULL; continue; }
			t->n = k2->n;
			for (size_t r = 0; r < k2->n; r++) { t->x[r] = (k2->x[r] - x->x0) / m->xh; t->y[r] = (m->base - k2->y[r]) / m->xh; }
			double *pp = x->pp + x->nstrokes * HW_LETTERS_PROFILE, *pv = x->pv + x->nstrokes * HW_LETTERS_PROFILE;
			stroke_profile(k2, m->pressure, pp, pv);
			for (int r = 0; r < HW_LETTERS_PROFILE; r++) {
				pp[r] = L->m.pres_mid > 0 ? pp[r] / L->m.pres_mid : NAN;
				pv[r] = L->m.speed_mid > 0 ? pv[r] / m->xh / L->m.speed_mid : NAN;
			}
			x->nstrokes++;
		}
		/* far from a pair of letters in size: a slip */
		if (!x->nstrokes || (x->x1 - x->x0) / m->xh > 8 || (x->y1 - x->y0) / m->xh > 6) { inst_free(x); memset(x, 0, sizeof *x); continue; }
		double w = !strcmp(m->s.written, "careful") ? 1.5 : !strcmp(m->s.written, "quick") ? 0.6 : 1.0;
		int k = o[q].pos;
		x->w = w * (0.25 + (k + 1 < 512 ? (m->read[k] + m->read[k + 1]) / 2 : 0.6));
		n++;
	}
	/* the most common stroke count (the fewer strokes of two as common) */
	size_t best = 0;
	int bn = 0;
	for (size_t a = 0; a < n; a++) {
		int c = 0;
		for (size_t b = 0; b < n; b++) c += in[b].nstrokes == in[a].nstrokes;
		if (c > bn || (c == bn && in[a].nstrokes < best)) { bn = c; best = in[a].nstrokes; }
	}
	int ret = 1;
	struct group g = { best, calloc(n + 1, sizeof(int)), 0, calloc(best + 1, sizeof(int)), 0 };
	if (!g.members || !g.pts) { ret = -1; goto out; }
	for (size_t a = 0; a < n; a++) if (in[a].nstrokes == best) g.members[g.n++] = (int)a;
	if (g.n < 1) goto out;
	for (size_t s2 = 0; s2 < best; s2++) {
		double *ls = malloc((size_t)g.n * sizeof *ls);
		if (!ls) { ret = -1; goto out; }
		for (int a = 0; a < g.n; a++) ls[a] = slen(&in[g.members[a]].s[s2]);
		int np = (int)lround(median(ls, (size_t)g.n, 0) / 0.04);
		free(ls);
		g.pts[s2] = np < 4 ? 4 : np > 64 ? 64 : np;
		g.total += g.pts[s2];
	}
	for (int a = 0; a < g.n; a++) {
		struct inst *x = &in[g.members[a]];
		x->rs = malloc((size_t)g.total * 2 * sizeof(double));
		if (!x->rs) { ret = -1; goto out; }
		int off = 0;
		for (size_t s2 = 0; s2 < best; s2++) { resample(&x->s[s2], (size_t)g.pts[s2], x->rs + 2 * off); off += g.pts[s2]; }
		double cx = 0, cy = 0;
		for (int q = 0; q < g.total; q++) { cx += x->rs[2 * q]; cy += x->rs[2 * q + 1]; }
		cx /= g.total; cy /= g.total;
		for (int q = 0; q < g.total; q++) { x->rs[2 * q] -= cx; x->rs[2 * q + 1] -= cy; }
	}
	/* the seed: the version with the most others of one way near it */
	int seed = -1, bs = -1;
	for (int a = 0; a < g.n; a++) {
		int sup = 0, x = g.members[a];
		for (int b = 0; b < g.n; b++) sup += rdist(in[x].rs, in[g.members[b]].rs, g.total) <= WAY_TAU;
		if (sup > bs || (sup == bs && in[x].w > in[seed].w + 1e-12)) { bs = sup; seed = x; }
	}
	/* of one way, as a variant's group is: HW_JOINED_MIN of them close to each other, each
	 * weighed by its closeness to the typical one (the rest stay out of it) */
	if (seed < 0 || bs < HW_JOINED_MIN) goto out;
	for (int a = 0; a < g.n; a++) {
		struct inst *x = &in[g.members[a]];
		double d = rdist(x->rs, in[seed].rs, g.total), c = exp(-(d / (2 * TAU / 3)) * (d / (2 * TAU / 3)));
		x->w *= c < 0.15 ? 0.15 : c;
	}
	double xmin;
	struct vstat vs = { 0 };
	stats_cp = 0;
	if (make_variant(in, &g, seed, &J->v, &xmin, &vs)) { variant_free(&J->v); ret = -1; goto out; }
	if (getenv("GLASSINE_HANDWRITING_LETTERS_STATS")) {
		char *sel = calloc((size_t)g.n + 1, 1);
		for (int a = 0; sel && a < g.n; a++) sel[a] = rdist(in[g.members[a]].rs, in[seed].rs, g.total) <= WAY_TAU;
		if (sel) variant_stats(in, &g, sel, &J->v, &vs);
		free(sel);
	}
	variant_narrow(&J->v, L->sp.narrow);
	J->v.use = bs;
	J->v.ids = calloc((size_t)g.n + 1, sizeof *J->v.ids);
	for (int a = 0; J->v.ids && a < g.n; a++) {
		const struct inst *x = &in[g.members[a]];
		if (rdist(x->rs, in[seed].rs, g.total) > WAY_TAU) continue;
		const char *id = S[x->sample].id;
		int dup = 0;
		for (size_t q = 0; q < J->v.nids && !dup; q++) dup = !strcmp(J->v.ids[q], id);
		if (!dup) memcpy(J->v.ids[J->v.nids++], id, 17);
	}
	if (J->v.ids) qsort(J->v.ids, J->v.nids, sizeof *J->v.ids, cmp_id17);
	J->v.lc = hw_spacing_side(&L->sp, &J->v.prof, 0);
	J->v.rc = hw_spacing_side(&L->sp, &J->v.prof, 1);
	ret = 0;
out:
	free(g.members); free(g.pts);
	for (size_t a = 0; a < n; a++) inst_free(&in[a]);
	free(in);
	return ret;
}

/* The joined pairs (FR-039): every pair of neighbouring letters in the samples with known
 * strokes, written joined (real_joint) or not; a pair joined in HW_JOINED_MIN usable samples
 * (the pair's strokes its own) and in HW_JOINED_SHARE of the samples with it gets a glyph
 * when those are of one way. A sample left out of either letter counts for none. */
static int joined_pairs(struct hw_letters *L, struct smp *S, size_t ns, const struct hw_mark *MK, size_t nmk)
{
	struct jocc *o = NULL;
	struct jref *R = NULL;
	size_t n = 0, cap = 0, nr = 0;
	unsigned *cps = malloc(4097 * sizeof *cps);
	if (!cps) return -1;
	for (size_t si = 0; si < ns; si++) {
		struct smp *m = &S[si];
		if (m->xh <= 0 || isnan(m->base)) continue;
		size_t nc = hw_utf8_decode(m->s.text, cps, 4096);
		if (nc > 4096) nc = 4096;
		if (nc < 2) continue;
		int *cnt = calloc(m->s.nstrokes + 1, sizeof *cnt);
		if (!cnt) { free(o); free(cps); free(R); return -1; }
		for (size_t k = 0; k < m->s.nspans && k < nc; k++)
			for (int *p = m->s.spans[k]; p && *p >= 0; p++) if ((size_t)*p < m->s.nstrokes) cnt[*p]++;
		for (size_t k = 0; k + 1 < nc && k + 1 < m->s.nspans; k++) {
			unsigned a = cps[k], b = cps[k + 1];
			const int *sa = m->s.spans[k], *sb = m->s.spans[k + 1];
			/* a pair of letters: no digits, marks or symbols */
			if (!hw_is_letter(a) || !hw_is_letter(b) || !span_ink(&m->s, sa) || !span_ink(&m->s, sb)) continue;
			if (hw_mark_of(MK, nmk, m->id, a) == HW_MARK_OUT || hw_mark_of(MK, nmk, m->id, b) == HW_MARK_OUT) continue;
			int shared = 0, sj = -1, own = 1;
			for (int side = 0; side < 2; side++)
				for (const int *p = side ? sb : sa; *p >= 0; p++) {
					if ((size_t)*p >= m->s.nstrokes) { own = 0; continue; }
					int here = span_has(sa, *p) + span_has(sb, *p);
					if (here == 2 && !side) { shared++; sj = *p; }
					if (cnt[*p] != here) own = 0;
				}
			/* one stroke in both, cut plainly in two: a joint (an aligner's share of a
			 * stroke between two letters written apart is none) */
			int joint = shared == 1 &&
				    real_joint(letter_get(L, a), letter_get(L, b), m, (int)k, sj,
					       ordinary(&R, &nr, L, S, ns, a, MK, nmk), ordinary(&R, &nr, L, S, ns, b, MK, nmk));
			if (n == cap) {
				cap = cap ? cap * 2 : 1024;
				struct jocc *x = realloc(o, cap * sizeof *x);
				if (!x) { free(cnt); free(o); free(cps); free(R); return -1; }
				o = x;
			}
			o[n++] = (struct jocc){ a, b, (int)si, (int)k, joint ? (own ? 2 : 1) : 0 };
		}
		free(cnt);
	}
	free(cps);
	free(R);
	qsort(o, n, sizeof *o, cmp_jocc);
	int ret = 0;
	for (size_t i = 0; i < n && ret == 0;) {
		size_t j = i;
		while (j < n && o[j].a == o[i].a && o[j].b == o[i].b) j++;
		int of = jsamples(o, i, j, 0), joined = jsamples(o, i, j, 1), usable = jsamples(o, i, j, 2);
		if (usable >= HW_JOINED_MIN && joined * HW_JOINED_SHARE_DEN >= of * HW_JOINED_SHARE_NUM && L->njoined < HW_JOINED_MAX) {
			struct hw_joined *x = realloc(L->joined, (L->njoined + 1) * sizeof *x);
			if (!x) { ret = -1; break; }
			L->joined = x;
			struct hw_joined *J = &L->joined[L->njoined];
			memset(J, 0, sizeof *J);
			J->a = o[i].a; J->b = o[i].b;
			J->samples = usable; J->joined = joined; J->of = of;
			int r = joined_glyph(J, L, S, o, i, j);
			if (r < 0) ret = -1;
			else if (r == 0) L->njoined++;
		}
		i = j;
	}
	free(o);
	return ret;
}

/* ---------------------------------------------------------------- joins (FR-044) */

static double lstroke_len(const struct hw_lstroke *t)
{
	double l = 0;
	for (size_t i = 1; i < t->n; i++) l += hypot(t->x[i] - t->x[i - 1], t->y[i] - t->y[i - 1]);
	return l;
}

int hw_join_body(const struct hw_variant *v, size_t j)
{
	if (j >= v->nstrokes) return 0;
	double m = 0;
	for (size_t q = 0; q < v->nstrokes; q++) m = fmax(m, lstroke_len(&v->s[q]));
	return lstroke_len(&v->s[j]) >= HW_JOIN_BODY * m;
}

/* the first (side 0) or last (1) stroke of v's main body, its lengths measured once */
static size_t body_end(const struct hw_variant *v, int last)
{
	enum { MAXS = 64 };
	size_t ns = v->nstrokes;
	if (ns < 2) return 0;
	if (ns > MAXS) {
		size_t j = last ? ns - 1 : 0;
		while (last ? j > 0 && !hw_join_body(v, j) : j + 1 < ns && !hw_join_body(v, j)) j += last ? (size_t)-1 : 1;
		return j;
	}
	double len[MAXS], m = 0;
	for (size_t q = 0; q < ns; q++) { len[q] = lstroke_len(&v->s[q]); m = fmax(m, len[q]); }
	size_t j = last ? ns - 1 : 0;
	while (last ? j > 0 && len[j] < HW_JOIN_BODY * m : j + 1 < ns && len[j] < HW_JOIN_BODY * m) j += last ? (size_t)-1 : 1;
	return j;
}

size_t hw_join_exit_stroke(const struct hw_variant *v)
{
	if (v->exit.n > 0 && v->exit.stroke >= 0 && hw_join_body(v, (size_t)v->exit.stroke)) return (size_t)v->exit.stroke;
	return body_end(v, 1);
}

size_t hw_join_entry_stroke(const struct hw_variant *v)
{
	if (v->entry.n > 0 && v->entry.stroke >= 0 && hw_join_body(v, (size_t)v->entry.stroke)) return (size_t)v->entry.stroke;
	return body_end(v, 0);
}

/* a height's band: below the baseline, near it, at the x-height, above */
static int join_band(double y) { return y < -0.25 ? 0 : y < 0.5 ? 1 : y < 1.25 ? 2 : 3; }

int hw_join_exit_class(const struct hw_variant *v)
{
	if (!v->nstrokes) return -1;
	const struct hw_lstroke *t = &v->s[hw_join_exit_stroke(v)];
	return t->n ? join_band(t->y[t->n - 1]) : -1;
}

int hw_join_entry_class(const struct hw_variant *v)
{
	if (!v->nstrokes) return -1;
	const struct hw_lstroke *t = &v->s[hw_join_entry_stroke(v)];
	return t->n ? 2 * join_band(t->y[0]) + (t->x[0] > v->width / 2) : -1;
}

int hw_join_letter(unsigned cp) { return hw_is_letter(cp); }

static const struct hw_jshape *jshape_pair(const struct hw_letters *L, unsigned a, unsigned b)
{
	for (size_t i = 0; i < L->njshape; i++) if (L->jshape[i].a == a && L->jshape[i].b == b && (a || b)) return &L->jshape[i];
	return NULL;
}

static const struct hw_jshape *jshape_kind(const struct hw_letters *L, int ec, int nc)
{
	for (size_t i = 0; i < L->njshape; i++)
		if (!L->jshape[i].a && !L->jshape[i].b && L->jshape[i].ec == ec && L->jshape[i].nc == nc) return &L->jshape[i];
	return NULL;
}

const struct hw_jshape *hw_join_shape(const struct hw_letters *L, unsigned a, const struct hw_variant *va, unsigned b,
				      const struct hw_variant *vb)
{
	if (!L->njshape || !hw_join_letter(a) || !hw_join_letter(b)) return NULL;
	const struct hw_jshape *t = jshape_pair(L, a, b);
	return t ? t : jshape_kind(L, hw_join_exit_class(va), hw_join_entry_class(vb));
}

void hw_join_map(const struct hw_jshape *s, int i0, int i1, double px, double py, double qx, double qy, double *x, double *y)
{
	/* along the shape's own chord u, beside it v (both over its length), then onto the new one;
	 * a shape too short to have a direction: the straight line */
	double cx = s->x[i1] - s->x[i0], cy = s->y[i1] - s->y[i0], c2 = cx * cx + cy * cy, cl = sqrt(c2);
	double dx = qx - px, dy = qy - py, dl = hypot(dx, dy), bv = dl > 0 && cl > 0 ? fmin(dl, HW_JOIN_BOW * cl) / dl : 0;
	for (int i = i0; i <= i1; i++) {
		double f = (double)(i - i0) / (i1 - i0), u = f, v = 0;
		if (cl >= 1e-3) {
			double ax = s->x[i] - s->x[i0], ay = s->y[i] - s->y[i0];
			u = (ax * cx + ay * cy) / c2; v = (-ax * cy + ay * cx) / c2;
		}
		x[i - i0] = px + u * dx - v * bv * dy;
		y[i - i0] = py + u * dy + v * bv * dx;
	}
}

void hw_join_curve(const struct hw_jshape *s, double px, double py, double qx, double qy, double *x, double *y)
{
	hw_join_map(s, 0, HW_JOIN_N - 1, px, py, qx, qy, x, y);
}

int hw_join_pair_decides(const struct hw_letters *L, unsigned a, unsigned b)
{
	size_t lo = 0, hi = L->njpair;
	while (lo < hi) {
		size_t mid = (lo + hi) / 2;
		const struct hw_jpair *p = &L->jpair[mid];
		if (p->a < a || (p->a == a && p->b < b)) lo = mid + 1; else hi = mid;
	}
	if (lo >= L->njpair || L->jpair[lo].a != a || L->jpair[lo].b != b || L->jpair[lo].seen < HW_JOIN_SEEN) return -1;
	const struct hw_jpair *p = &L->jpair[lo];
	return p->joined >= HW_JOIN_TWICE && 2 * p->joined >= p->seen;
}

/* a joint seen while building: sample si, the first letter at k (a, b the pair), the join
 * HW_JOIN_N points from where a's own ink ends (x-heights, y up), y0 that point's height */
struct jobs { unsigned a, b; int si, k, ec, nc; double y0, x[HW_JOIN_N], y[HW_JOIN_N]; };
#ifndef HW_JOBS_KEEP
#define HW_JOBS_KEEP 0          /* a measuring harness keeps them after the build */
#endif
#ifndef HW_JOBS_SEEN
#define HW_JOBS_SEEN(o, m)      /* and sees each one's sample */
#endif
static struct jobs *build_jobs;
static size_t build_njobs, build_cjobs;

/* points [a, b] of ink k (sample units, y down) as a joint */
static void jobs_add(const struct hw_ink *k, size_t a, size_t b, const struct smp *m, unsigned ca, unsigned cb, int si, int pos)
{
	if (b <= a || !(m->xh > 0) || isnan(m->base)) return;
	if (build_njobs == build_cjobs) {
		size_t c = build_cjobs ? 2 * build_cjobs : 256;
		struct jobs *x = realloc(build_jobs, c * sizeof *x);
		if (!x) return;
		build_jobs = x;
		build_cjobs = c;
	}
	struct jobs *o = &build_jobs[build_njobs];
	double out[2 * HW_JOIN_N];
	struct hw_lstroke ls = { b - a + 1, k->x + a, k->y + a };
	resample(&ls, HW_JOIN_N, out);
	for (int q = 0; q < HW_JOIN_N; q++) {
		o->x[q] = (out[2 * q] - k->x[a]) / m->xh;
		o->y[q] = (k->y[a] - out[2 * q + 1]) / m->xh;
	}
	if (hypot(o->x[HW_JOIN_N - 1], o->y[HW_JOIN_N - 1]) > HW_JOIN_FAR) return;
	o->y0 = (m->base - k->y[a]) / m->xh;
	o->a = ca; o->b = cb; o->si = si; o->k = pos; o->ec = o->nc = -1;
	HW_JOBS_SEEN(o, m);
	build_njobs++;
}

static int cmp_jobs(const void *x, const void *y)
{
	const struct jobs *a = x, *b = y;
	if (a->a != b->a) return a->a < b->a ? -1 : 1;
	if (a->b != b->b) return a->b < b->b ? -1 : 1;
	if (a->si != b->si) return a->si - b->si;
	return a->k - b->k;
}

static int cmp_jpair(const void *x, const void *y)
{
	const struct hw_jpair *a = x, *b = y;
	if (a->a != b->a) return a->a < b->a ? -1 : 1;
	if (a->b != b->b) return a->b < b->b ? -1 : 1;
	return 0;
}

/* (sample, place) -> instance */
struct ipos { int si, pos; size_t i; };
static int cmp_ipos(const void *x, const void *y)
{
	const struct ipos *a = x, *b = y;
	if (a->si != b->si) return a->si - b->si;
	if (a->pos != b->pos) return a->pos - b->pos;
	return a->i < b->i ? -1 : a->i > b->i;
}

/* the variant an instance is written like (the letter's in L), NULL none */
static const struct hw_variant *ipos_var(const struct hw_letters *L, const struct inst *in, const struct ipos *ix, size_t nix,
					 int si, int pos)
{
	size_t lo = 0, hi = nix;
	while (lo < hi) {
		size_t mid = (lo + hi) / 2;
		if (ix[mid].si < si || (ix[mid].si == si && ix[mid].pos < pos)) lo = mid + 1; else hi = mid;
	}
	for (; lo < nix && ix[lo].si == si && ix[lo].pos == pos; lo++) {
		const struct inst *x = &in[ix[lo].i];
		if (x->dropped || x->var < 0) continue;
		const struct hw_letter *l = letter_get(L, x->cp);
		if (l && l->covered && x->var < l->nvar) return &l->var[x->var];
	}
	return NULL;
}

/* a joint in its chord's frame: u along the chord, v to its left, over its length; -1 too short */
static int join_frame(const struct jobs *j, double *u, double *v)
{
	double ex = j->x[HW_JOIN_N - 1], ey = j->y[HW_JOIN_N - 1], l2 = ex * ex + ey * ey;
	if (l2 < HW_JOIN_SHORT * HW_JOIN_SHORT) return -1;
	for (int q = 0; q < HW_JOIN_N; q++) { u[q] = (j->x[q] * ex + j->y[q] * ey) / l2; v[q] = (-j->x[q] * ey + j->y[q] * ex) / l2; }
	return 0;
}

/* how far two lines of HW_JOIN_N points are apart along their whole length: the mean of each
 * one's points' nearest distance to the other (its points 4 times as dense), both ways */
static double join_apart(const double *x, const double *y, const double *u, const double *v)
{
	enum { D = 4 * (HW_JOIN_N - 1) + 1 };
	double a[2 * D], b[2 * D];
	struct hw_lstroke sa = { HW_JOIN_N, (double *)x, (double *)y }, sb = { HW_JOIN_N, (double *)u, (double *)v };
	resample(&sa, D, a); resample(&sb, D, b);
	double s = 0;
	for (int w = 0; w < 2; w++) {
		const double *p = w ? b : a, *q = w ? a : b;
		for (int i = 0; i < D; i++) {
			double m = INFINITY;
			for (int k = 0; k < D; k++) m = fmin(m, hypot(p[2 * i] - q[2 * k], p[2 * i + 1] - q[2 * k + 1]));
			s += m;
		}
	}
	return s / (2 * D);
}

/* how rough a join's way is: shape_rough's measure (the turning at each point less its local
 * mean, squared and summed, resampled evenly by its length) over its HW_JOIN_N points */
static double join_rough(const double *x, const double *y)
{
	enum { N = HW_JOIN_N };
	double xs[N], ys[N], buf[2 * N], tn[N] = { 0 }, e = 0;
	memcpy(xs, x, sizeof xs); memcpy(ys, y, sizeof ys);
	struct hw_lstroke t = { N, xs, ys };
	resample(&t, N, buf);
	for (int i = 1; i < N - 1; i++) {
		double ax = buf[2 * i] - buf[2 * i - 2], ay = buf[2 * i + 1] - buf[2 * i - 1];
		double bx = buf[2 * i + 2] - buf[2 * i], by = buf[2 * i + 3] - buf[2 * i + 1];
		tn[i] = hypot(ax, ay) < 1e-9 || hypot(bx, by) < 1e-9 ? 0 : atan2(ax * by - ay * bx, ax * bx + ay * by);
	}
	for (int i = 2; i < N - 2; i++) {
		double d = tn[i] - (tn[i - 1] + 2 * tn[i] + tn[i + 1]) / 4;
		e += d * d;
	}
	return e;
}

/* a shape from joints o[idx[0 .. n)]: their ends' mean, their way in their chords' frames
 * averaged onto it; curved only where it beats a straight line left out one at a time and
 * is smooth (no rougher than HW_JOIN_ROUGH: a straight line has none) */
static void jshape_make(struct hw_jshape *t, const struct jobs *o, const size_t *idx, size_t n)
{
	enum { N = HW_JOIN_N };
	double su[N] = { 0 }, sv[N] = { 0 }, ex = 0, ey = 0, sm = 0, u[N], v[N];
	int nf = 0;
	for (size_t i = 0; i < n; i++) {
		const struct jobs *j = &o[idx[i]];
		ex += j->x[N - 1]; ey += j->y[N - 1];
		sm += j->y0 + j->y[HW_JOIN_MID];
		if (join_frame(j, u, v)) continue;
		for (int q = 0; q < N; q++) { su[q] += u[q]; sv[q] += v[q]; }
		nf++;
	}
	ex /= (double)n; ey /= (double)n;
	t->n = (int)n;
	t->mid = round(sm / (double)n * 1e4) / 1e4;
	/* left out one at a time: the others' mean against the straight line, at each joint */
	double curved = 0, straight = 0;
	for (size_t i = 0; nf >= 2 && i < n; i++) {
		const struct jobs *j = &o[idx[i]];
		if (join_frame(j, u, v)) continue;
		struct hw_jshape m = { 0 };
		double lx[N], ly[N], cx[N], cy[N];
		for (int q = 0; q < N; q++) {
			double f = (double)q / (N - 1);
			m.x[q] = (su[q] - u[q]) / (nf - 1); m.y[q] = (sv[q] - v[q]) / (nf - 1);
			lx[q] = f * j->x[N - 1]; ly[q] = f * j->y[N - 1];
		}
		hw_join_curve(&m, 0, 0, j->x[N - 1], j->y[N - 1], cx, cy);
		curved += join_apart(j->x, j->y, cx, cy);
		straight += join_apart(j->x, j->y, lx, ly);
	}
	int bent = nf >= 2 && curved < straight;
	if (bent) {
		double mx[N], my[N];
		for (int q = 0; q < N; q++) { mx[q] = su[q] / nf; my[q] = sv[q] / nf; }
		bent = join_rough(mx, my) <= HW_JOIN_ROUGH;
	}
	for (int q = 0; q < N; q++) {
		double f = (double)q / (N - 1), a = bent ? su[q] / nf : f, b = bent ? sv[q] / nf : 0;
		t->x[q] = round((a * ex - b * ey) * 1e4) / 1e4;
		t->y[q] = round((a * ey + b * ex) * 1e4) / 1e4;
	}
}

/* The joins (FR-044) from the joints seen (build_jobs) and the letter pairs of the samples
 * (with known strokes, a letter left out of a sample counting for neither), their kinds by
 * the variants the letters are written like. 0, -1 out of memory. */
static int joins_learn(struct hw_letters *L, const struct inst *in, size_t nin, struct smp *S, size_t ns, const struct hw_mark *MK,
		       size_t nmk)
{
	free(L->jshape); free(L->jpair);
	L->jshape = NULL; L->jpair = NULL; L->njshape = L->njpair = 0;
	memset(L->jkind, 0, sizeof L->jkind);
	if (!build_njobs) return 0;
	int ret = -1;
	unsigned *cps = malloc(4097 * sizeof *cps);
	struct ipos *ix = malloc((nin + 1) * sizeof *ix);
	struct hw_jpair *pr = NULL;
	size_t npr = 0, cpr = 0, *idx = malloc((build_njobs + 1) * sizeof *idx);
	if (!cps || !ix || !idx) goto out;
	for (size_t i = 0; i < nin; i++) ix[i] = (struct ipos){ in[i].sample, in[i].pos, i };
	qsort(ix, nin, sizeof *ix, cmp_ipos);
	/* the letter pairs and the kinds: written with known strokes, and joined (a stroke in both) */
	for (size_t si = 0; si < ns; si++) {
		struct smp *m = &S[si];
		if (m->xh <= 0 || isnan(m->base)) continue;
		size_t nc = hw_utf8_decode(m->s.text, cps, 4096);
		if (nc > 4096) nc = 4096;
		for (size_t k = 0; k + 1 < nc && k + 1 < m->s.nspans; k++) {
			unsigned a = cps[k], b = cps[k + 1];
			const int *sa = m->s.spans[k], *sb = m->s.spans[k + 1];
			if (!hw_join_letter(a) || !hw_join_letter(b) || !span_ink(&m->s, sa) || !span_ink(&m->s, sb)) continue;
			if (hw_mark_of(MK, nmk, m->id, a) == HW_MARK_OUT || hw_mark_of(MK, nmk, m->id, b) == HW_MARK_OUT) continue;
			int joined = 0;
			for (const int *q = sa; *q >= 0 && !joined; q++) joined = span_has(sb, *q);
			if (npr == cpr) {
				cpr = cpr ? 2 * cpr : 256;
				struct hw_jpair *x = realloc(pr, cpr * sizeof *x);
				if (!x) goto out;
				pr = x;
			}
			pr[npr++] = (struct hw_jpair){ a, b, 1, joined };
			const struct hw_variant *va = ipos_var(L, in, ix, nin, (int)si, (int)k), *vb = ipos_var(L, in, ix, nin, (int)si, (int)k + 1);
			int ec = va ? hw_join_exit_class(va) : -1, nc2 = vb ? hw_join_entry_class(vb) : -1;
			if (ec >= 0 && nc2 >= 0) { L->jkind[ec][nc2].seen++; L->jkind[ec][nc2].joined += joined; }
		}
	}
	qsort(pr, npr, sizeof *pr, cmp_jpair);
	size_t w = 0;
	for (size_t i = 0; i < npr;) {
		size_t j = i;
		struct hw_jpair t = { pr[i].a, pr[i].b, 0, 0 };
		while (j < npr && pr[j].a == t.a && pr[j].b == t.b) { t.seen++; t.joined += pr[j].joined; j++; }
		if (t.seen >= HW_JOIN_SEEN) pr[w++] = t;
		i = j;
	}
	npr = w;
	/* the shapes: per letter pair, then per kind */
	qsort(build_jobs, build_njobs, sizeof *build_jobs, cmp_jobs);
	struct hw_jshape *sh = calloc(build_njobs + HW_JOIN_EXITS * HW_JOIN_ENTRIES + 1, sizeof *sh);
	size_t nsh = 0;
	if (!sh) goto out;
	for (size_t i = 0; i < build_njobs;) {
		size_t j = i;
		while (j < build_njobs && build_jobs[j].a == build_jobs[i].a && build_jobs[j].b == build_jobs[i].b) j++;
		if (j - i >= HW_JOIN_PAIR_MIN && hw_join_letter(build_jobs[i].a) && hw_join_letter(build_jobs[i].b) && nsh < HW_JOIN_MAX) {
			for (size_t q = i; q < j; q++) idx[q - i] = q;
			sh[nsh] = (struct hw_jshape){ build_jobs[i].a, build_jobs[i].b, -1, -1, 0, 0, { 0 }, { 0 } };
			jshape_make(&sh[nsh++], build_jobs, idx, j - i);
		}
		i = j;
	}
	for (size_t q = 0; q < build_njobs; q++) {
		struct jobs *o = &build_jobs[q];
		const struct hw_variant *va = ipos_var(L, in, ix, nin, o->si, o->k), *vb = ipos_var(L, in, ix, nin, o->si, o->k + 1);
		if (va && vb && hw_join_letter(o->a) && hw_join_letter(o->b)) { o->ec = hw_join_exit_class(va); o->nc = hw_join_entry_class(vb); }
	}
	for (int ec = 0; ec < HW_JOIN_EXITS; ec++)
		for (int nc2 = 0; nc2 < HW_JOIN_ENTRIES; nc2++) {
			size_t n = 0;
			for (size_t q = 0; q < build_njobs; q++)
				if (build_jobs[q].ec == ec && build_jobs[q].nc == nc2) idx[n++] = q;
			if (n >= HW_JOIN_KIND_MIN && nsh < HW_JOIN_MAX) {
				sh[nsh] = (struct hw_jshape){ 0, 0, ec, nc2, 0, 0, { 0 }, { 0 } };
				jshape_make(&sh[nsh++], build_jobs, idx, n);
			}
		}
	if (nsh) { L->jshape = sh; L->njshape = nsh; L->jpair = pr; L->njpair = npr; pr = NULL; }
	else { free(sh); memset(L->jkind, 0, sizeof L->jkind); }
	ret = 0;
out:
	free(cps); free(ix); free(pr); free(idx);
	return ret;
}

static void jobs_reset(void)
{
	free(build_jobs);
	build_jobs = NULL;
	build_njobs = build_cjobs = 0;
}

static void jobs_free(void)
{
	if (HW_JOBS_KEEP) return;
	free(build_jobs);
	build_jobs = NULL;
	build_njobs = build_cjobs = 0;
}

/* ---------------------------------------------------------------- letters written joined (FR-007) */

/* A stroke shared by several letters of a word is cut where its parts best match those
 * letters' variants, made from their samples written apart (the first pass): each part
 * against the stroke of a variant with the letter's stroke count that it takes the place of,
 * the best of them. The cut is join_cut (hw_cut_shape; another matching can take its place).
 * Within its part each letter's own ink is where its part matches that stroke best with the
 * ends at a joint let go (up to half the part at each joint end): the rest of the part is the
 * joining stroke, its entry before it and its exit after it. */
#define JOIN_LET_GO 0.5         /* of its part a letter's entry or exit takes at most this much */
static hw_cut_fn join_cut = hw_cut_shape;
/* the model the cut looks up a letter in that the build doesn't make (one character's build:
 * the model as it was) */
static const struct hw_letters *build_ref;

/* points [a, b] of ink k (sample units, y down) as a stub: HW_STUB_N points along them by
 * length, x-heights, y up, from point at */
static void stub_make(const struct hw_ink *k, size_t a, size_t b, size_t at, double xh, int stroke, struct hw_stub *t)
{
	double out[2 * HW_STUB_N];
	struct hw_lstroke ls = { b - a + 1, k->x + a, k->y + a };
	resample(&ls, HW_STUB_N, out);
	for (int q = 0; q < HW_STUB_N; q++) {
		t->x[q] = (out[2 * q] - k->x[at]) / xh;
		t->y[q] = (k->y[at] - out[2 * q + 1]) / xh;
	}
	t->stroke = stroke;
	t->n = 1;
}

/* a letter's part of a joined stroke: points [a, b), its own ink [f, l) */
struct jpart { int k, j; size_t a, f, l, b; int entry, exit; };

/* the letters written joined of the samples, as instances (normalized as the others are, the
 * pressure and speed relative to pm, vm) after in's nin, cut by the letters of L (else
 * build_ref); *added how many. 0, -1 out of memory. */
static int joined_insts(struct inst **pin, size_t *pnin, size_t *pcin, struct smp *S, size_t ns, const struct hw_letters *L,
			const struct hw_mark *MK, size_t nmk, double pm, double vm, size_t *added)
{
	unsigned *cps = malloc(4097 * sizeof *cps);
	struct jpart *P = NULL;
	size_t np = 0, cp_ = 0;
	int ret = -1;
	*added = 0;
	if (!cps) return -1;
	for (size_t si = 0; si < ns; si++) {
		struct smp *m = &S[si];
		struct hw_sample *s = &m->s;
		if (!m->own || !m->cnt || !(m->xh > 0) || isnan(m->base)) continue;
		size_t nc = hw_utf8_decode(s->text, cps, 4096);
		if (nc > 4096) nc = 4096;
		if (nc < 2) continue;
		np = 0;
		/* the strokes shared by letters next to each other in a word, cut */
		for (size_t j = 0; j < s->nstrokes; j++) {
			const struct hw_ink *ink = &s->strokes[j];
			if (m->cnt[j] < 2 || ink->n < 4) continue;
			int k0 = -1, k1 = -1, nk = 0;
			for (size_t k = 0; k < s->nspans && k < nc; k++)
				if (span_has(s->spans[k], (int)j)) { if (k0 < 0) k0 = (int)k; k1 = (int)k; nk++; }
			if (nk < 2 || nk != k1 - k0 + 1 || nk != m->cnt[j]) continue;
			size_t R = (size_t)nk, nref = 0;
			struct hw_cut_ref *refs = malloc(R * HW_LETTERS_VARIANTS * sizeof *refs);
			size_t *off = malloc((R + 1) * sizeof *off), *at = malloc((R + 1) * sizeof *at), *idx = malloc((ink->n + 1) * sizeof *idx);
			int *which = malloc((R + 1) * sizeof *which);
			double *dx = malloc((2 * ink->n + 2) * sizeof *dx), *cum = malloc((ink->n + 1) * sizeof *cum);
			if (!refs || !off || !at || !idx || !which || !dx || !cum) {
				free(refs); free(off); free(at); free(idx); free(which); free(dx); free(cum);
				goto out;
			}
			int ok = 1;
			for (size_t r = 0; r < R && ok; r++) {
				size_t k = (size_t)k0 + r;
				off[r] = nref;
				const struct hw_letter *le = letter_get(L, cps[k]);
				if ((!le || !le->covered) && build_ref) le = letter_get(build_ref, cps[k]);
				if (cps[k] == ' ' || !le || !le->covered || !le->nvar) { ok = 0; break; }
				/* its inked strokes in writing order, this one's place among them */
				int n2 = 0, pos = -1;
				for (int *q = s->spans[k]; *q >= 0; q++) {
					if ((size_t)*q >= s->nstrokes || !s->strokes[*q].n) continue;
					if (*q < (int)j) pos++;
					n2++;
				}
				pos++;
				for (int v = 0; v < le->nvar; v++) {
					const struct hw_variant *va = &le->var[v];
					if (va->out || va->nstrokes != (size_t)n2 || pos >= n2 || va->s[pos].n < 2) continue;
					hw_cut_ref_make(va->s[pos].x, va->s[pos].y, va->s[pos].n, &refs[nref++]);
				}
				if (nref == off[r]) ok = 0;
			}
			off[R] = nref;
			/* (at most as many of its points as hw_cut_shape cuts at: every step-th and the
			 * last; the parts are trimmed at them too) */
			size_t cap = HW_CUT_PTS > HW_CUT_PER * R ? HW_CUT_PTS : HW_CUT_PER * R;
			size_t m2 = ink->n, step = (m2 + cap - 1) / cap, nd = 0;
			for (size_t i = 0; i < m2; i += step) idx[nd++] = i;
			if (idx[nd - 1] != m2 - 1) idx[nd++] = m2 - 1;
			double *dy = dx + nd;
			for (size_t i = 0; i < nd; i++) { dx[i] = ink->x[idx[i]]; dy[i] = ink->y[idx[i]]; }
			double cost;
			int rc = ok ? join_cut(dx, dy, nd, m->xh, R, refs, off, at, which, &cost) : 1;
			if (rc < 0) { free(refs); free(off); free(at); free(idx); free(which); free(dx); free(cum); goto out; }
			hw_cut_arc(dx, dy, nd, cum);
			size_t np0 = np;
			for (size_t r = 0; !rc && r < R; r++) {
				size_t a = at[r], b = at[r + 1], f = a, l = b;
				double best = INFINITY;
				size_t fmax = r > 0 ? a + (size_t)((double)(b - a) * JOIN_LET_GO) : a;
				size_t lmin = r + 1 < R ? b - (size_t)((double)(b - a) * JOIN_LET_GO) : b;
				for (size_t f2 = a; f2 <= fmax; f2++)
					for (size_t l2 = b; l2 >= lmin && l2 >= f2 + 2; l2--) {
						double e = hw_cut_cost(dx, dy, f2, l2, cum, &refs[which[r]], m->xh);
						if (e < best) { best = e; f = f2; l = l2; }
						if (l2 == 0) break;
					}
				if (!isfinite(best)) continue;
				if (np == cp_) {
					cp_ = cp_ ? 2 * cp_ : 64;
					struct jpart *x = realloc(P, cp_ * sizeof *x);
					if (!x) { free(refs); free(off); free(at); free(idx); free(which); free(dx); free(cum); goto out; }
					P = x;
				}
				/* (as points of the stroke: the last of the cut's ends is its end) */
				size_t A = idx[a], B = b == nd ? m2 : idx[b], F = idx[f], Lo = l == nd ? m2 : idx[l];
				P[np++] = (struct jpart){ k0 + (int)r, (int)j, A, F, Lo, B, r > 0, r + 1 < R };
			}
			/* the joints (FR-044): from where a letter's own ink ends to where the next one's begins */
			for (size_t q = np0; q + 1 < np; q++)
				if (P[q + 1].k == P[q].k + 1 && P[q].l >= 1)
					jobs_add(ink, P[q].l - 1, P[q + 1].f, m, cps[P[q].k], cps[P[q + 1].k], (int)si, P[q].k);
			free(refs); free(off); free(at); free(idx); free(which); free(dx); free(cum);
		}
		if (!np) continue;
		/* each letter whose strokes are its own or cut here: an instance */
		for (size_t k = 0; k < nc && k < s->nspans; k++) {
			int *sp = s->spans[k];
			if (m->own[k] || cps[k] == ' ' || !sp || sp[0] < 0) continue;
			if (hw_mark_of(MK, nmk, m->id, cps[k]) == HW_MARK_OUT) continue;
			int ord[256], no = 0, good = 1, shared = 0;
			for (int *q = sp; *q >= 0 && no < 256; q++) ord[no++] = *q;
			for (int a = 1; a < no; a++) for (int b = a; b > 0 && ord[b] < ord[b - 1]; b--) { int t = ord[b]; ord[b] = ord[b - 1]; ord[b - 1] = t; }
			const struct jpart *pt[256];
			for (int a = 0; a < no && good; a++) {
				pt[a] = NULL;
				if ((size_t)ord[a] >= s->nstrokes) { good = 0; break; }
				if (m->cnt[ord[a]] == 1) continue;
				for (size_t q = 0; q < np && !pt[a]; q++) if (P[q].k == (int)k && P[q].j == ord[a]) pt[a] = &P[q];
				if (!pt[a] || pt[a]->l < pt[a]->f + 2) good = 0;
				shared++;
			}
			if (!good || !shared) continue;
			if (*pnin == *pcin) {
				*pcin = *pcin ? *pcin * 2 : 1024;
				struct inst *x = realloc(*pin, *pcin * sizeof *x);
				if (!x) goto out;
				*pin = x;
			}
			struct inst *x = &(*pin)[*pnin];
			memset(x, 0, sizeof *x);
			x->cp = cps[k]; x->sample = (int)si; x->pos = (int)k;
			x->var = -1; x->occ = -1; x->joined = 1;
			x->prev = k > 0 && cps[k - 1] != ' ' ? cps[k - 1] : 0;
			x->next = k + 1 < nc && cps[k + 1] != ' ' ? cps[k + 1] : 0;
			x->x0 = x->y0 = INFINITY; x->x1 = x->y1 = -INFINITY;
			x->s = calloc((size_t)no + 1, sizeof *x->s);
			x->pp = malloc(((size_t)no + 1) * HW_LETTERS_PROFILE * sizeof *x->pp);
			x->pv = malloc(((size_t)no + 1) * HW_LETTERS_PROFILE * sizeof *x->pv);
			if (!x->s || !x->pp || !x->pv) { free(x->s); free(x->pp); free(x->pv); goto out; }
			for (int a = 0; a < no; a++) {
				const struct hw_ink *k2 = &s->strokes[ord[a]];
				size_t f = pt[a] ? pt[a]->f : 0, l = pt[a] ? pt[a]->l : k2->n;
				if (l <= f) continue;
				struct hw_ink sub = *k2;
				sub.n = l - f;
				sub.x = k2->x + f; sub.y = k2->y + f; sub.t = k2->t + f; sub.p = k2->p + f;
				int at = (int)x->nstrokes;
				struct hw_lstroke *t = &x->s[x->nstrokes++];
				t->n = sub.n;
				t->x = malloc(sub.n * sizeof(double)); t->y = malloc(sub.n * sizeof(double));
				if (!t->x || !t->y) { free(t->x); free(t->y); x->nstrokes--; continue; }
				memcpy(t->x, sub.x, sub.n * sizeof(double)); memcpy(t->y, sub.y, sub.n * sizeof(double));
				stroke_profile(&sub, m->pressure, x->pp + at * HW_LETTERS_PROFILE, x->pv + at * HW_LETTERS_PROFILE);
				for (size_t q = 0; q < sub.n; q++) {
					if (sub.x[q] < x->x0) x->x0 = sub.x[q];
					if (sub.x[q] > x->x1) x->x1 = sub.x[q];
					if (sub.y[q] < x->y0) x->y0 = sub.y[q];
					if (sub.y[q] > x->y1) x->y1 = sub.y[q];
				}
				/* its entry: the first joint it is entered at; its exit: the last it leaves at */
				if (pt[a] && pt[a]->entry && !x->stub[0].n && pt[a]->f > pt[a]->a)
					stub_make(k2, pt[a]->a, pt[a]->f, pt[a]->f, m->xh, at, &x->stub[0]);
				if (pt[a] && pt[a]->exit && pt[a]->b > pt[a]->l)
					stub_make(k2, pt[a]->l - 1, pt[a]->b - 1, pt[a]->l - 1, m->xh, at, &x->stub[1]);
			}
			if (x->nstrokes != (size_t)no) {
				for (size_t j = 0; j < x->nstrokes; j++) { free(x->s[j].x); free(x->s[j].y); }
				free(x->s); free(x->pp); free(x->pv);
				continue;
			}
			/* weight: careful marking, readability (as for the letters written apart) */
			double w = !strcmp(s->written, "careful") ? 1.5 : !strcmp(s->written, "quick") ? 0.6 : 1.0;
			x->w = x->w0 = w * (0.25 + (k < 512 ? m->read[k] : 0.6));
			int mk = hw_mark_of(MK, nmk, m->id, cps[k]);
			double bw = !strcmp(s->written, "careful") ? HW_BEST_CAREFUL : !strcmp(s->written, "quick") ? 1 / HW_BEST_CAREFUL : 1;
			if (mk == HW_MARK_FAVOURITE) bw *= HW_BEST_FAVOURITE;
			x->bw = mk == HW_MARK_WAY_OUT ? 0 : clampd(bw, 0.01, 100);
			/* normalized as the letters written apart are */
			for (size_t j = 0; j < x->nstrokes; j++)
				for (size_t q = 0; q < x->s[j].n; q++) {
					x->s[j].x[q] = (x->s[j].x[q] - x->x0) / m->xh;
					x->s[j].y[q] = (m->base - x->s[j].y[q]) / m->xh;
				}
			for (size_t q = 0; q < x->nstrokes * HW_LETTERS_PROFILE; q++) {
				x->pv[q] /= m->xh;
				x->pp[q] = pm > 0 ? x->pp[q] / pm : NAN;
				x->pv[q] = vm > 0 ? x->pv[q] / vm : NAN;
			}
			double X0 = x->x0, Y0 = x->y0, Y1 = x->y1;
			x->x1 = (x->x1 - X0) / m->xh; x->x0 = 0;
			x->y0 = (m->base - Y1) / m->xh;
			x->y1 = (m->base - Y0) / m->xh;
			if (x->x1 > 6 || x->y1 - x->y0 > 6) x->dropped = 1;
			(*pnin)++;
			(*added)++;
		}
	}
	ret = 0;
out:
	free(cps); free(P);
	return ret;
}

/* a variant's entry and exit (FR-007): the mean of those of its letters written joined, at
 * the stroke most of them have one at (the first of equal counts), weighted as they are */
static void variant_stubs(struct hw_variant *va, const struct inst *in, const int *v, int nv, int k)
{
	for (int e = 0; e < 2; e++) {
		struct hw_stub *t = e ? &va->exit : &va->entry;
		memset(t, 0, sizeof *t);
		int cnt[64] = { 0 }, bs = -1;
		for (int i = 0; i < nv; i++) {
			const struct inst *x = &in[v[i]];
			if (!x->joined || x->var != k || x->stub[e].n <= 0 || x->stub[e].stroke < 0 || x->stub[e].stroke >= 64 ||
			    (size_t)x->stub[e].stroke >= va->nstrokes) continue;
			cnt[x->stub[e].stroke]++;
		}
		for (int q = 0; q < 64; q++) if (cnt[q] && (bs < 0 || cnt[q] > cnt[bs])) bs = q;
		if (bs < 0) continue;
		double sx[HW_STUB_N] = { 0 }, sy[HW_STUB_N] = { 0 }, ws = 0;
		for (int i = 0; i < nv; i++) {
			const struct inst *x = &in[v[i]];
			if (!x->joined || x->var != k || x->stub[e].n <= 0 || x->stub[e].stroke != bs || !(x->w > 0)) continue;
			for (int q = 0; q < HW_STUB_N; q++) { sx[q] += x->w * x->stub[e].x[q]; sy[q] += x->w * x->stub[e].y[q]; }
			ws += x->w;
			t->n++;
		}
		if (!(ws > 0)) { memset(t, 0, sizeof *t); continue; }
		t->stroke = bs;
		for (int q = 0; q < HW_STUB_N; q++) { t->x[q] = round(sx[q] / ws * 1e4) / 1e4; t->y[q] = round(sy[q] / ws * 1e4) / 1e4; }
	}
}

/* The characters of the model (FR-006, FR-007) from the instances: per character its
 * variants. Instances written joined (in->joined) only join the variants their character's
 * samples written apart give: they count for neither coverage, the ways nor the variants'
 * use, the marks or the neighbour rules. final: the build's last pass (its measures go out).
 * 0, -1 out of memory. */
static int build_chars(struct hw_letters *L, struct inst *in, size_t nin, struct smp *S, const struct hw_mark *MK, size_t nmk,
		       int final)
{
	/* the characters */
	size_t nu = 0;
	unsigned *uc = malloc((nin + 1) * sizeof *uc);
	for (size_t k = 0; uc && k < nin; k++) if (!in[k].joined) uc[nu++] = in[k].cp;
	if (uc) qsort(uc, nu, sizeof *uc, cmp_uint);
	size_t nd = 0;
	for (size_t k = 0; k < nu; k++) if (!nd || uc[nd - 1] != uc[k]) uc[nd++] = uc[k];
	L->l = calloc(nd + 1, sizeof *L->l);
	if (!L->l || !uc) { free(uc); return -1; }
	for (size_t c = 0; c < nd; c++) {
		if (build_only && uc[c] != build_only) continue;
		struct hw_letter *l = &L->l[L->n++];
		l->cp = uc[c];
		marks_hash_of(MK, nmk, l->cp, l->marks);
		/* its versions, in sample order */
		int *v = NULL, nv = 0;
		v = malloc((nin + 1) * sizeof *v);
		if (!v) continue;
		/* usable samples: those with it written with strokes of its own (FR-007), as
		 * letters status counts them; the versions are those that can be measured */
		int last = -1;
		for (size_t k = 0; k < nin; k++)
			if (in[k].cp == uc[c] && !in[k].joined && in[k].sample != last) { l->samples++; last = in[k].sample; }
		for (size_t k = 0; k < nin; k++) if (!in[k].dropped && in[k].cp == uc[c]) v[nv++] = (int)k;
		/* a size far from the character's others is more likely a slip than a style */
		int napart = 0;
		for (int i = 0; i < nv; i++) napart += !in[v[i]].joined;
		if (napart >= 3) {
			double *hs = malloc((size_t)nv * sizeof *hs);
			size_t nh = 0;
			for (int i = 0; hs && i < nv; i++) if (!in[v[i]].joined) hs[nh++] = in[v[i]].y1 - in[v[i]].y0 + (in[v[i]].x1 - in[v[i]].x0);
			double mh = hs ? median(hs, nh, 0) : 0;
			free(hs);
			int w = 0;
			for (int i = 0; i < nv; i++) {
				double hh = in[v[i]].y1 - in[v[i]].y0 + (in[v[i]].x1 - in[v[i]].x0);
				if (mh <= 0 || (hh >= 0.4 * mh && hh <= 2.5 * mh)) v[w++] = v[i];
				else in[v[i]].slip = 1;
			}
			nv = w;
		}
		napart = 0;
		for (int i = 0; i < nv; i++) napart += !in[v[i]].joined;
		/* (written joined, a letter adds to its variants, never covers it: FR-007) */
		l->covered = l->samples >= HW_LETTERS_MIN && napart > 0;
		if (!l->covered) { free(v); continue; }
		/* groups by stroke count */
		struct group G[16];
		int ng = 0;
		for (int i = 0; i < 2 * nv; i++) {
			const struct inst *xi = &in[v[i % nv]];
			if (xi->joined != (i >= nv)) continue;
			size_t sc = xi->nstrokes;
			int g = 0;
			while (g < ng && G[g].nstrokes != sc) g++;
			if (g == ng && xi->joined) continue;
			if (g == ng) {
				if (ng == 16) continue;
				G[ng] = (struct group){ sc, calloc((size_t)nv, sizeof(int)), 0, calloc(sc, sizeof(int)), 0 };
				ng++;
			}
			if (G[g].members) G[g].members[G[g].n++] = v[i % nv];
		}
		for (int g = 0; g < ng; g++) {
			struct group *gr = &G[g];
			if (!gr->members || !gr->pts) continue;
			/* points per stroke: from the median length of each stroke */
			for (size_t j = 0; j < gr->nstrokes; j++) {
				double *ls = malloc((size_t)gr->n * sizeof *ls);
				if (!ls) continue;
				size_t nl = 0;
				for (int i = 0; i < gr->n; i++) if (!in[gr->members[i]].joined) ls[nl++] = slen(&in[gr->members[i]].s[j]);
				double ml = median(ls, nl, 0);
				free(ls);
				int n = (int)lround(ml / 0.04);
				gr->pts[j] = n < 4 ? 4 : n > 64 ? 64 : n;
				gr->total += gr->pts[j];
			}
			for (int i = 0; i < gr->n; i++) {
				struct inst *x = &in[gr->members[i]];
				free(x->rs);
				x->rs = malloc((size_t)gr->total * 2 * sizeof(double));
				if (!x->rs) continue;
				int o = 0;
				for (size_t j = 0; j < gr->nstrokes; j++) { resample(&x->s[j], (size_t)gr->pts[j], x->rs + 2 * o); o += gr->pts[j]; }
				double cx = 0, cy = 0;
				for (int q = 0; q < gr->total; q++) { cx += x->rs[2 * q]; cy += x->rs[2 * q + 1]; }
				cx /= gr->total; cy /= gr->total;
				for (int q = 0; q < gr->total; q++) { x->rs[2 * q] -= cx; x->rs[2 * q + 1] -= cy; }
			}
		}
		/* the ways of writing it (FR-006): within a stroke count, versions of one rough shape
		 * and stroke order (within WAY_TAU of each other), the most supported first */
		struct way { int g, seed, n; double w; } W[64];
		int nw = 0;
		int *way = malloc((size_t)nin * sizeof *way);
		if (!way) { for (int g = 0; g < ng; g++) { free(G[g].members); free(G[g].pts); } free(v); continue; }
		for (int i = 0; i < nv; i++) way[v[i]] = -1;
		for (int g = 0; g < ng; g++) {
			struct group *gr = &G[g];
			if (!gr->members || !gr->pts || !gr->total) continue;
			for (;;) {
				int bi = -1, bs = 0;
				for (int i = 0; i < gr->n; i++) {
					int x = gr->members[i];
					if (way[x] >= 0 || !in[x].rs || in[x].joined) continue;
					int sup = 0;
					for (int k = 0; k < gr->n; k++) {
						int y = gr->members[k];
						if (way[y] < 0 && in[y].rs && !in[y].joined && rdist(in[x].rs, in[y].rs, gr->total) <= WAY_TAU) sup++;
					}
					if (sup > bs || (sup == bs && bi >= 0 && in[x].w > in[bi].w + 1e-12)) { bs = sup; bi = x; }
				}
				if (bi < 0 || nw == 64) break;
				W[nw] = (struct way){ g, bi, 0, 0 };
				for (int k = 0; k < gr->n; k++) {
					int y = gr->members[k];
					if (way[y] < 0 && in[y].rs && !in[y].joined && rdist(in[bi].rs, in[y].rs, gr->total) <= WAY_TAU) { way[y] = nw; W[nw].n++; }
				}
				nw++;
			}
		}
		/* consistency: closeness to the typical shape of its way */
		for (int i = 0; i < nv; i++) {
			struct inst *x = &in[v[i]];
			if (way[v[i]] < 0) continue;
			const struct way *wy = &W[way[v[i]]];
			double d = rdist(x->rs, in[wy->seed].rs, G[wy->g].total);
			double c = exp(-(d / (2 * TAU / 3)) * (d / (2 * TAU / 3)));
			x->w *= c < 0.15 ? 0.15 : c;
		}
		for (int i = 0; i < nv; i++) if (way[v[i]] >= 0) W[way[v[i]]].w += in[v[i]].w;
		/* a way gives a variant when it is used often enough: 3 versions and a tenth of those
		 * written apart (a slip, a lead-in or a neighbour's part stays out); with none, the most used */
		int ord[64], nq = 0;
		for (int k = 0; k < nw; k++) if (W[k].n >= HW_LETTERS_MIN && W[k].n * HW_LETTERS_WAY_SHARE >= napart) ord[nq++] = k;
		if (!nq) for (int k = 0; k < nw; k++) if (!nq || W[k].n > W[ord[0]].n) { ord[0] = k; nq = 1; }
		for (int a2 = 1; a2 < nq; a2++)
			for (int b2 = a2; b2 > 0; b2--) {
				const struct way *x = &W[ord[b2]], *y = &W[ord[b2 - 1]];
				if (x->n > y->n || (x->n == y->n && x->w > y->w + 1e-12)) { int t = ord[b2]; ord[b2] = ord[b2 - 1]; ord[b2 - 1] = t; }
				else break;
			}
		/* the letters written joined (FR-007): each into the way of its strokes that gives a
		 * variant and that it is written like (within WAY_TAU of its seed), the nearest; one
		 * written like none is no version of the character */
		for (int g = 0; g < ng; g++) {
			struct group *gr = &G[g];
			int w2 = 0;
			for (int i = 0; gr->members && i < gr->n; i++) {
				int x = gr->members[i];
				if (in[x].joined) {
					int bk = -1;
					double bd = INFINITY;
					for (int k = 0; in[x].rs && k < nq; k++) {
						if (W[ord[k]].g != g) continue;
						double d = rdist(in[x].rs, in[W[ord[k]].seed].rs, gr->total);
						if (d <= WAY_TAU && d < bd - 1e-12) { bd = d; bk = ord[k]; }
					}
					way[x] = bk;
					if (bk < 0) continue;
					double c = exp(-(bd / (2 * TAU / 3)) * (bd / (2 * TAU / 3)));
					in[x].w *= c < 0.15 ? 0.15 : c;
				}
				gr->members[w2++] = x;
			}
			gr->n = w2;
		}
		/* seeds: one per way, the most used first; then, up to three, further shapes of the
		 * ways (repeated letters don't look stamped) */
		int seeds[HW_LETTERS_VARIANTS], nsd = 0, sgr[HW_LETTERS_VARIANTS];
		for (int k = 0; k < nq && nsd < HW_LETTERS_VARIANTS; k++) { seeds[nsd] = W[ord[k]].seed; sgr[nsd] = W[ord[k]].g; nsd++; }
		while (nsd < 3) {
			double bs = -1;
			int bi = -1, bg = -1;
			for (int k = 0; k < nq; k++) {
				const struct way *wy = &W[ord[k]];
				const struct group *gr = &G[wy->g];
				for (int i = 0; i < gr->n; i++) {
					int x = gr->members[i];
					if (way[x] != ord[k] || in[x].joined) continue;
					double dmin = 1;
					for (int q2 = 0; q2 < nsd; q2++) {
						if (seeds[q2] == x) dmin = 0;
						else if (sgr[q2] == wy->g) { double d = rdist(in[x].rs, in[seeds[q2]].rs, gr->total) / TAU; if (d < dmin) dmin = d; }
					}
					if (dmin * TAU < DIFF_MIN) continue;
					/* support: other versions of the group near it */
					int sup = 0;
					for (int q2 = 0; q2 < gr->n; q2++)
						if (!in[gr->members[q2]].joined && rdist(in[x].rs, in[gr->members[q2]].rs, gr->total) <= 2 * SIGMA) sup++;
					if (sup < 2) continue;
					double score = in[x].w * dmin * wy->n / napart;
					if (score > bs + 1e-12) { bs = score; bi = x; bg = wy->g; }
				}
			}
			if (bi < 0) break;
			seeds[nsd] = bi; sgr[nsd] = bg; nsd++;
		}
		/* use: each version of a way that gives variants counts for the nearest seed with its
		 * strokes */
		int use[HW_LETTERS_VARIANTS] = { 0 };
		for (int i = 0; i < nv; i++) {
			int x = v[i], q = 0;
			if (way[x] < 0 || in[x].joined) continue;
			for (int k = 0; k < nq; k++) q |= ord[k] == way[x];
			if (!q) continue;
			int bk = -1;
			double bd = INFINITY;
			for (int k = 0; k < nsd; k++) {
				if (sgr[k] != W[way[x]].g) continue;
				double d = rdist(in[x].rs, in[seeds[k]].rs, G[sgr[k]].total);
				if (d < bd - 1e-12) { bd = d; bk = k; }
			}
			if (bk >= 0) use[bk]++;
		}
		/* the most used first (the font's own glyph for the character) */
		for (int a2 = 1; a2 < nsd; a2++)
			for (int b2 = a2; b2 > 0 && use[b2] > use[b2 - 1]; b2--) {
				int t = seeds[b2]; seeds[b2] = seeds[b2 - 1]; seeds[b2 - 1] = t;
				t = sgr[b2]; sgr[b2] = sgr[b2 - 1]; sgr[b2 - 1] = t;
				t = use[b2]; use[b2] = use[b2 - 1]; use[b2 - 1] = t;
			}
		int vix[HW_LETTERS_VARIANTS];
		double vxm[HW_LETTERS_VARIANTS];
		struct vstat vst[HW_LETTERS_VARIANTS];
		stats_cp = l->cp;
		for (int k = 0; k < nsd; k++) {
			vix[k] = -1;
			if (!make_variant(in, &G[sgr[k]], seeds[k], &l->var[l->nvar], &vxm[k], &vst[k])) { vix[k] = l->nvar; l->var[l->nvar++].use = use[k]; }
			else variant_free(&l->var[l->nvar]);
		}
		/* the samples written like each variant (as use counts them), and the person's marks
		 * on them: a variant is left out when a third of its samples say so; the favourite is
		 * the one with the most samples marked so */
		for (int i = 0; i < nv; i++) {
			int x = v[i], q = 0;
			if (way[x] < 0) continue;
			for (int k = 0; k < nq; k++) q |= ord[k] == way[x];
			if (!q) continue;
			int bk = -1;
			double bd = INFINITY;
			for (int k = 0; k < nsd; k++) {
				if (sgr[k] != W[way[x]].g) continue;
				double d = rdist(in[x].rs, in[seeds[k]].rs, G[sgr[k]].total);
				if (d < bd - 1e-12) { bd = d; bk = k; }
			}
			if (bk >= 0) in[x].var = vix[bk];
		}
		/* the build's measures (variant_stats): each variant against the samples written like it */
		for (int k = 0; final && getenv("GLASSINE_HANDWRITING_LETTERS_STATS") && k < nsd; k++) {
			if (vix[k] < 0) continue;
			const struct group *gr = &G[sgr[k]];
			char *sel = calloc((size_t)gr->n + 1, 1);
			for (int a = 0; sel && a < gr->n; a++) sel[a] = in[gr->members[a]].var == vix[k];
			if (sel) variant_stats(in, gr, sel, &l->var[vix[k]], &vst[k]);
			free(sel);
		}
		int fav = -1, favn = 0;
		for (int k = 0; k < l->nvar; k++) {
			struct hw_variant *va = &l->var[k];
			int cnt = 0, nout = 0, nfav = 0;
			for (int i = 0; i < nv; i++) cnt += in[v[i]].var == k && !in[v[i]].joined;
			va->ids = calloc((size_t)cnt + 1, sizeof *va->ids);
			for (int i = 0; va->ids && i < nv; i++) {
				if (in[v[i]].var != k || in[v[i]].joined) continue;
				const char *id = S[in[v[i]].sample].id;
				int dup = 0;
				for (size_t q = 0; q < va->nids && !dup; q++) dup = !strcmp(va->ids[q], id);
				if (!dup) memcpy(va->ids[va->nids++], id, 17);
				int mk = hw_mark_of(MK, nmk, id, l->cp);
				nout += mk == HW_MARK_WAY_OUT;
				nfav += mk == HW_MARK_FAVOURITE;
			}
			if (va->ids) qsort(va->ids, va->nids, sizeof *va->ids, cmp_id17);
			/* left out while a third of its samples are marked so: samples written that way
			 * later join it without bringing it back */
			va->out = nout && 3 * nout >= cnt;
			if (!va->out && nfav > favn) { fav = k; favn = nfav; }
		}
		if (fav >= 0) l->var[fav].favourite = 1;
		/* the best versions, the target of neatening (FR-035): none for one left out */
		for (int k = 0; k < nsd; k++)
			if (vix[k] >= 0 && !l->var[vix[k]].out) make_best(in, &G[sgr[k]], seeds[k], vix[k], vxm[k], &l->var[vix[k]]);
		/* each variant's entry and exit, from the letters written joined like it */
		for (int k = 0; k < l->nvar; k++) variant_stubs(&l->var[k], in, v, nv, k);
		letter_slots(l);
		/* (the neighbour rules from the letters written apart) */
		int na2 = 0;
		for (int i = 0; i < nv; i++) if (!in[v[i]].joined) v[na2++] = v[i];
		letter_rules(l, in, v, na2);
		free(way);
		if (!l->nvar) l->covered = 0;
		for (int g = 0; g < ng; g++) { free(G[g].members); free(G[g].pts); }
		free(v);
	}
	free(uc);
	return 0;
}

int hw_letters_build(struct hw_letters *L)
{
	memset(L, 0, sizeof *L);
	jobs_reset();
	if (hw_letters_inputs(L->inputs)) return -1;
	struct hw_entry *es;
	size_t ne;
	unsigned long gen;
	if (hw_samples_load(&es, &ne, &gen)) return -1;
	struct smp *S = calloc(ne + 1, sizeof *S);
	if (!S) { hw_entries_free(es, ne); return -1; }
	size_t ns = 0;
	/* what letters status counts, for each prompted sample (FR-030) */
	struct urec *U = calloc(ne + 1, sizeof *U);
	size_t nu0 = 0;
	if (!U) { free(S); hw_entries_free(es, ne); return -1; }
	int shown = -1;
	struct hw_mark *MK = NULL;
	size_t nmk = 0;
	if (hw_marks(&MK, &nmk)) { free(S); free(U); hw_entries_free(es, ne); return -1; }
	/* one character's build: the samples whose text has it */
	char (*with)[17] = NULL;
	size_t nwith = 0;
	if (build_only) {
		const struct hw_index_entry *ix;
		size_t nix;
		char u8[8];
		u8[hw_utf8_encode(build_only, u8)] = 0;
		if (hw_samples_index(&ix, &nix) || !(with = calloc(nix + 1, sizeof *with))) {
			free(MK); free(S); free(U); hw_entries_free(es, ne); return -1;
		}
		for (size_t i = 0; i < nix; i++) if (ix[i].text && strstr(ix[i].text, u8)) memcpy(with[nwith++], ix[i].meta.id, 17);
		qsort(with, nwith, sizeof *with, cmp_id17);
	}
	for (size_t i = 0; i < ne; i++) {
		if (build_progress && (int)(i * 50 / ne) != shown) { shown = (int)(i * 50 / ne); printf("progress\t%zu\t%zu\n", i, ne); fflush(stdout); }
		if (strcmp(es[i].meta.source, "prompted")) continue;
		if (build_only && !bsearch(es[i].meta.id, with, nwith, sizeof *with, cmp_id17)) continue;
		/* a sample stored twice counts once */
		int dup = 0;
		for (size_t k = 0; k < nu0 && !dup; k++) dup = !strcmp(U[k].id, es[i].meta.id);
		if (dup) continue;
		memcpy(U[nu0].id, es[i].meta.id, 17);
		U[nu0++].cps = NULL;
		struct smp *m = &S[ns];
		char err[128];
		if (hw_sample_parse(es[i].json, &m->s, err, sizeof err)) continue;
		if (!known_spans(&m->s)) {
			if (build_only) {
				unsigned t[1024];
				size_t nt = hw_utf8_decode(m->s.text, t, 1024);
				for (size_t k = 0; k < nt && k < 1024; k++) if (t[k] == build_only) occ_add(es[i].meta.id, (int)k, HW_OCC_UNREAD, -1, m->s.text);
			}
			hw_sample_free(&m->s);
			continue;
		}
		snprintf(m->id, sizeof m->id, "%s", es[i].meta.id);
		m->time = es[i].meta.time;
		m->parsed = 1;
		ns++;
	}
	hw_entries_free(es, ne);
	qsort(S, ns, sizeof *S, cmp_smp);

	struct hw_pref *pr = NULL;
	size_t npr = 0;
	if (hw_prefs(&pr, &npr, NULL)) npr = 0;

	/* the versions: characters whose strokes are their own */
	struct inst *in = NULL;
	size_t nin = 0, cin = 0;
	unsigned cps[4096];
	for (size_t si = 0; si < ns; si++) {
		struct smp *m = &S[si];
		size_t nc = hw_utf8_decode(m->s.text, cps, 4096);
		if (nc > 4096) nc = 4096;
		readability(m, cps, nc);
		m->guide = guide_base(&m->s);
		m->pressure = varied_pressure(&m->s);
		m->first = (int)nin;
		char *own = malloc(nc + 1);
		if (!own || own_chars(&m->s, cps, nc, own, &m->cnt)) { free(own); continue; }
		for (size_t k = 0; k < nu0; k++)
			if (!strcmp(U[k].id, m->id)) { free(U[k].cps); U[k].cps = usable_cps(cps, nc, own); break; }
		for (size_t k = 0; k < nc; k++) {
			int occ = -1;
			if (build_only && cps[k] == build_only) {
				int st = -1;
				if (k >= m->s.nspans || !m->s.spans[k] || m->s.spans[k][0] < 0) st = HW_OCC_UNREAD;
				else if (!own[k]) {
					/* shared with which neighbour */
					st = HW_OCC_JOINED_AFTER;
					for (int *p = m->s.spans[k]; *p >= 0; p++)
						if (k > 0 && k - 1 < m->s.nspans && m->s.spans[k - 1])
							for (int *q = m->s.spans[k - 1]; *q >= 0; q++) if (*q == *p) st = HW_OCC_JOINED_BEFORE;
				} else if (hw_mark_of(MK, nmk, m->id, cps[k]) == HW_MARK_OUT) st = HW_OCC_OUT;
				occ = occ_add(m->id, (int)k, st >= 0 ? st : HW_OCC_USABLE, st >= 0 ? -1 : (int)nin, m->s.text);
				if (st >= 0) continue;
			}
			if (k >= m->s.nspans) break;
			int *sp = m->s.spans[k];
			if (!own[k]) continue;
			/* left out by the person (FR-031): no instance; it still counts as a sample */
			if (hw_mark_of(MK, nmk, m->id, cps[k]) == HW_MARK_OUT) continue;
			int np = 0;
			for (int *p = sp; *p >= 0; p++) np++;
			if (nin == cin) { cin = cin ? cin * 2 : 1024; struct inst *x = realloc(in, cin * sizeof *x); if (!x) break; in = x; }
			struct inst *x = &in[nin];
			memset(x, 0, sizeof *x);
			x->cp = cps[k]; x->sample = (int)si; x->pos = (int)k;
			x->var = -1; x->occ = occ;
			x->prev = k > 0 && cps[k - 1] != ' ' ? cps[k - 1] : 0;
			x->next = k + 1 < nc && cps[k + 1] != ' ' ? cps[k + 1] : 0;
			x->x0 = x->y0 = INFINITY; x->x1 = x->y1 = -INFINITY;
			x->s = calloc((size_t)np, sizeof *x->s);
			if (!x->s) break;
			x->pp = malloc(((size_t)np + 1) * HW_LETTERS_PROFILE * sizeof *x->pp);
			x->pv = malloc(((size_t)np + 1) * HW_LETTERS_PROFILE * sizeof *x->pv);
			/* strokes in writing order (spans list them as the sample does) */
			int ord[256], no = 0;
			for (int *p = sp; *p >= 0 && no < 256; p++) ord[no++] = *p;
			for (int a = 1; a < no; a++) for (int b = a; b > 0 && ord[b] < ord[b - 1]; b--) { int t = ord[b]; ord[b] = ord[b - 1]; ord[b - 1] = t; }
			for (int a = 0; a < no; a++) {
				const struct hw_ink *k2 = &m->s.strokes[ord[a]];
				if (!k2->n) continue;
				struct hw_lstroke *t = &x->s[x->nstrokes++];
				t->n = k2->n;
				t->x = malloc(k2->n * sizeof(double)); t->y = malloc(k2->n * sizeof(double));
				if (!t->x || !t->y) { x->nstrokes--; continue; }
				memcpy(t->x, k2->x, k2->n * sizeof(double)); memcpy(t->y, k2->y, k2->n * sizeof(double));
				if (x->pp && x->pv)
					stroke_profile(k2, m->pressure, x->pp + (x->nstrokes - 1) * HW_LETTERS_PROFILE,
						       x->pv + (x->nstrokes - 1) * HW_LETTERS_PROFILE);
				for (size_t q = 0; q < k2->n; q++) {
					if (k2->x[q] < x->x0) x->x0 = k2->x[q];
					if (k2->x[q] > x->x1) x->x1 = k2->x[q];
					if (k2->y[q] < x->y0) x->y0 = k2->y[q];
					if (k2->y[q] > x->y1) x->y1 = k2->y[q];
				}
			}
			if (!x->nstrokes) { free(x->s); free(x->pp); free(x->pv); if (occ >= 0) { build_occ[occ].state = HW_OCC_UNREAD; build_occ[occ].inst = -1; } continue; }
			/* weight: careful marking, readability, preferences (consistency comes later) */
			double w = !strcmp(m->s.written, "careful") ? 1.5 : !strcmp(m->s.written, "quick") ? 0.6 : 1.0;
			w *= 0.25 + (k < 512 ? m->read[k] : 0.6);
			double pf = 1;
			for (size_t q = 0; q < npr; q++) {
				int ch = !strcmp(pr[q].chosen, m->id), ot = !strcmp(pr[q].other, m->id);
				if (ch) pf *= pr[q].cspan == (int)k ? 1.6 : pr[q].cspan < 0 ? 1.25 : 1;
				if (ot) pf *= pr[q].ospan == (int)k ? 0.6 : pr[q].ospan < 0 ? 0.8 : 1;
			}
			x->w = w * clampd(pf, 0.2, 5);
			/* its weight in the best version (FR-035): the favourite, careful and chosen
			 * samples up, quick ones and those not chosen down, those left out with their way
			 * out */
			int mk = hw_mark_of(MK, nmk, m->id, cps[k]);
			double bw = !strcmp(m->s.written, "careful") ? HW_BEST_CAREFUL : !strcmp(m->s.written, "quick") ? 1 / HW_BEST_CAREFUL : 1;
			if (mk == HW_MARK_FAVOURITE) bw *= HW_BEST_FAVOURITE;
			for (size_t q = 0; q < npr; q++) {
				if (!strcmp(pr[q].chosen, m->id) && (pr[q].cspan == (int)k || pr[q].cspan < 0)) bw *= HW_BEST_CHOSEN;
				if (!strcmp(pr[q].other, m->id) && (pr[q].ospan == (int)k || pr[q].ospan < 0)) bw *= HW_BEST_OTHER;
			}
			x->bw = mk == HW_MARK_WAY_OUT ? 0 : clampd(bw, 0.01, 100);
			nin++;
		}
		m->ninst = (int)nin - m->first;
		m->own = own;
	}
	free(pr);
	for (size_t k = 0; k < nu0; k++) if (!U[k].cps) U[k].cps = strdup("-");
	qsort(U, nu0, sizeof *U, cmp_urec);
	if (!build_only) usable_save(U, nu0);
	urecs_free(U, nu0);

	/* per sample: baseline and x-height from its own letters */
	struct dvec r_xl = { 0 }, r_go = { 0 }, r_mm = { 0 };   /* x-height/line, base-guide (in x-heights), x-height mm */
	for (size_t si = 0; si < ns; si++) {
		struct smp *m = &S[si];
		struct dvec b = { 0 }, h = { 0 };
		for (int k = m->first; k < m->first + m->ninst; k++) {
			if (on_base(in[k].cp)) dpush(&b, in[k].y1);
			if (is_x(in[k].cp)) dpush(&h, in[k].y1 - in[k].y0);
		}
		m->base = median(b.v, b.n, NAN);
		m->xh = median(h.v, h.n, 0);
		free(b.v); free(h.v);
		if (m->xh > 0 && m->s.area.set && m->s.area.line > 0) dpush(&r_xl, m->xh / m->s.area.line);
		if (m->xh > 0 && !isnan(m->base) && !isnan(m->guide)) dpush(&r_go, (m->base - m->guide) / m->xh);
		if (m->xh > 0 && !strcmp(m->s.units.xy, "device") && m->s.units.per_mm > 0) dpush(&r_mm, m->xh / m->s.units.per_mm);
	}
	double xl = median(r_xl.v, r_xl.n, 0), go = median(r_go.v, r_go.n, 0), xmm = median(r_mm.v, r_mm.n, 0);
	free(r_xl.v); free(r_go.v); free(r_mm.v);
	/* one character's build sees only some samples: the whole model's, so its versions come
	 * out as a whole build makes them */
	if (build_only) { xl = build_m.xh_line; go = build_m.base_guide; xmm = build_m.xh_mm; }
	/* as the model file keeps them, so either build uses the same */
	xl = round(xl * 1e4) / 1e4; go = round(go * 1e4) / 1e4; xmm = round(xmm * 1e4) / 1e4;
	L->m.xh_line = xl; L->m.base_guide = go; L->m.xh_mm = xmm;
	for (size_t si = 0; si < ns; si++) {
		struct smp *m = &S[si];
		if (m->xh <= 0) {
			if (xl > 0 && m->s.area.set && m->s.area.line > 0) m->xh = xl * m->s.area.line;
			else if (xmm > 0 && !strcmp(m->s.units.xy, "device") && m->s.units.per_mm > 0) m->xh = xmm * m->s.units.per_mm;
		}
		if (isnan(m->base) && !isnan(m->guide) && m->xh > 0) m->base = m->guide + go * m->xh;
	}

	/* normalize: x-heights, y up from the baseline, x from the ink's left edge */
	for (size_t k = 0; k < nin; k++) {
		struct inst *x = &in[k];
		struct smp *m = &S[x->sample];
		if (m->xh <= 0 || isnan(m->base)) { x->dropped = 1; continue; }
		for (size_t j = 0; j < x->nstrokes; j++)
			for (size_t q = 0; q < x->s[j].n; q++) {
				x->s[j].x[q] = (x->s[j].x[q] - x->x0) / m->xh;
				x->s[j].y[q] = (m->base - x->s[j].y[q]) / m->xh;
			}
		for (size_t q = 0; x->pv && q < x->nstrokes * HW_LETTERS_PROFILE; q++) x->pv[q] /= m->xh;
		double X0 = x->x0, Y0 = x->y0, Y1 = x->y1;
		x->x1 = (x->x1 - X0) / m->xh; x->x0 = 0;
		x->y0 = (m->base - Y1) / m->xh;       /* bottom */
		x->y1 = (m->base - Y0) / m->xh;       /* top */
		if (x->x1 > 6 || x->y1 - x->y0 > 6) x->dropped = 1;
	}

	/* how the person writes (FR-006): their median pressure and speed, which the profiles are
	 * relative to (one character's build takes the model's) */
	{
		struct dvec mp = { 0 }, mv = { 0 };
		for (size_t k = 0; k < nin; k++) {
			const struct inst *x = &in[k];
			if (x->dropped || !x->pp || !x->pv) continue;
			for (size_t q = 0; q < x->nstrokes * HW_LETTERS_PROFILE; q++) {
				if (!isnan(x->pp[q])) dpush(&mp, x->pp[q]);
				if (!isnan(x->pv[q]) && x->pv[q] > 0) dpush(&mv, x->pv[q]);
			}
		}
		double pm = round(median(mp.v, mp.n, 0) * 1e4) / 1e4, vm = round(median(mv.v, mv.n, 0) * 1e4) / 1e4;
		free(mp.v); free(mv.v);
		if (build_only && (build_m.pres_mid > 0 || build_m.speed_mid > 0)) { pm = build_m.pres_mid; vm = build_m.speed_mid; }
		L->m.pres_mid = pm; L->m.speed_mid = vm;
		for (size_t k = 0; k < nin; k++) {
			struct inst *x = &in[k];
			if (!x->pp || !x->pv) continue;
			for (size_t q = 0; q < x->nstrokes * HW_LETTERS_PROFILE; q++) {
				x->pp[q] = pm > 0 ? x->pp[q] / pm : NAN;
				x->pv[q] = vm > 0 ? x->pv[q] / vm : NAN;
			}
		}
	}

	/* the line metrics, from words and sentences (the pen's width from every sample) */
	struct dvec cap = { 0 }, asc = { 0 }, desc = { 0 }, ink = { 0 };
	double sl_num = 0, sl_den = 0;
	for (size_t si = 0; si < ns; si++) {
		struct smp *m = &S[si];
		size_t nc = hw_utf8_decode(m->s.text, cps, 4096);
		if (m->xh > 0 && m->s.area.set && m->s.area.line > 0) {
			/* the writing paper's pen: max(2, line / 110 * 1.5) in the sample's units */
			double pen = m->s.area.line / 110 * 1.5;
			dpush(&ink, (pen > 2.0 ? pen : 2.0) / m->xh);
		}
		if (nc < 2) continue;
		for (int k = m->first; k < m->first + m->ninst; k++) {
			struct inst *x = &in[k];
			if (x->dropped) continue;
			if (is_cap(x->cp)) dpush(&cap, x->y1);
			if (is_asc(x->cp)) dpush(&asc, x->y1);
			if (is_desc(x->cp)) dpush(&desc, x->y0);
			/* the lean of the downstrokes */
			for (size_t j = 0; j < x->nstrokes; j++)
				for (size_t q = 1; q < x->s[j].n; q++) {
					double dx = x->s[j].x[q] - x->s[j].x[q - 1], dy = x->s[j].y[q] - x->s[j].y[q - 1];
					if (dy < 0 && -dy > 2 * fabs(dx)) { sl_num += dx * dy; sl_den += dy * dy; }
				}
		}
	}
	L->m.cap = median(cap.v, cap.n, 1.4);
	L->m.asc = median(asc.v, asc.n, L->m.cap * 1.05);
	L->m.desc = median(desc.v, desc.n, -0.5);
	L->m.slant = sl_den > 0 ? sl_num / sl_den : 0;
	L->m.ink = clampd(median(ink.v, ink.n, 0.07), 0.02, 0.25);
	free(cap.v); free(asc.v); free(desc.v); free(ink.v);

	/* first from the letters written apart; then, with the letters written joined cut by
	 * those variants and added to them (FR-007), again (the measures from the last pass) */
	for (size_t k = 0; k < nin; k++) in[k].w0 = in[k].w;
	int stats = getenv("GLASSINE_HANDWRITING_LETTERS_STATS") != NULL;
	if (build_chars(L, in, nin, S, MK, nmk, 0)) goto fail;
	size_t added = 0;
	if (joined_insts(&in, &nin, &cin, S, ns, L, MK, nmk, L->m.pres_mid, L->m.speed_mid, &added)) goto fail;
	if (added || stats) {
		for (size_t i = 0; i < L->n; i++)
			for (int k = 0; k < L->l[i].nvar; k++) variant_free(&L->l[i].var[k]);
		free(L->l);
		L->l = NULL;
		L->n = 0;
		for (size_t k = 0; k < nin; k++) { in[k].w = in[k].w0; in[k].var = -1; in[k].slip = 0; }
		if (build_chars(L, in, nin, S, MK, nmk, 1)) goto fail;
	}

	/* letter widths (FR-006): per character its median width in running text (a sample of
	 * several words) over that in single words; one character's build takes the model's */
	if (build_only) L->sp.narrow = build_narrow;
	else {
		double *r = malloc((L->n + 1) * sizeof *r), *wr = malloc((nin + 1) * sizeof *wr), *ww = malloc((nin + 1) * sizeof *ww);
		size_t nr = 0;
		L->sp.narrow = 1;
		for (size_t c = 0; r && wr && ww && c < L->n; c++) {
			size_t a = 0, b = 0;
			for (size_t k = 0; k < nin; k++) {
				const struct inst *x = &in[k];
				if (x->cp != L->l[c].cp || x->dropped || x->slip || x->joined) continue;
				if (strchr(S[x->sample].s.text, ' ')) wr[a++] = x->x1;
				else ww[b++] = x->x1;
			}
			if (a < HW_PAIR_MIN || b < HW_PAIR_MIN) continue;
			double mr = median(wr, a, NAN), mw = median(ww, b, NAN);
			if (mw > 0.05) r[nr++] = mr / mw;
		}
		if (nr >= HW_RUN_CHARS) {
			double k = median(r, nr, 1);
			L->sp.narrow = round((k < HW_RUN_MIN ? HW_RUN_MIN : k > HW_RUN_MAX ? HW_RUN_MAX : k) * 1e4) / 1e4;
		}
		free(r); free(wr); free(ww);
	}
	for (size_t c = 0; c < L->n; c++)
		for (int v = 0; v < L->l[c].nvar; v++) variant_narrow(&L->l[c].var[v], L->sp.narrow);
	/* one character's build: no spacing (the model's stays) */
	if (build_only) goto measured;
	/* spacing (FR-007): the profile gaps of neighbouring own letters of one sample (their
	 * strokes placed by where they lie), running text's (a sample of several words) told
	 * apart, the box gaps between words */
	struct hw_gap_obs *obs = NULL;
	size_t nobs = 0, cobs = 0;
	struct dvec wgs = { 0 };
	for (size_t si = 0; si < ns; si++) {
		struct smp *m = &S[si];
		size_t nc = hw_utf8_decode(m->s.text, cps, 4096);
		if (nc < 2 || m->xh <= 0) continue;
		for (int k = m->first; k + 1 < m->first + m->ninst; k++) {
			struct inst *a = &in[k], *b = &in[k + 1];
			if (a->dropped || b->dropped) continue;
			const int *spa = m->s.spans[a->pos], *spb = m->s.spans[b->pos];
			if (b->pos == a->pos + 1) {
				if (nobs == cobs) {
					cobs = cobs ? 2 * cobs : 256;
					struct hw_gap_obs *x = realloc(obs, cobs * sizeof *x);
					if (!x) continue;
					obs = x;
				}
				struct hw_gap_obs *g = &obs[nobs];
				g->a = a->cp;
				g->b = b->cp;
				hw_profile_clear(&g->pa);
				hw_profile_clear(&g->pb);
				struct hw_mid md[2] = { { 0, 0 }, { 0, 0 } };
				for (int side = 0; side < 2; side++)
					for (const int *p = side ? spb : spa; *p >= 0; p++) {
						const struct hw_ink *st = &m->s.strokes[*p];
						for (size_t q = 0; q < st->n; q++) {
							size_t q0 = q ? q - 1 : 0;
							double x0 = st->x[q0] / m->xh, y0 = (m->base - st->y[q0]) / m->xh;
							double x1 = st->x[q] / m->xh, y1 = (m->base - st->y[q]) / m->xh;
							hw_profile_seg(side ? &g->pb : &g->pa, x0, y0, x1, y1);
							hw_mid_seg(&md[side], x0, y0, x1, y1);
						}
					}
				g->ma = hw_mid_of(&md[0]);
				g->mb = hw_mid_of(&md[1]);
				if (hw_profile_empty(&g->pa) || hw_profile_empty(&g->pb)) continue;
				g->gap = hw_profile_gap(&g->pa, &g->pb, 0);
				g->run = strchr(m->s.text, ' ') != NULL;
				nobs++;
			} else if (b->pos == a->pos + 2 && b->pos - 1 < (int)nc && cps[b->pos - 1] == ' ') {
				double axr = -INFINITY, bxl = INFINITY;
				for (const int *p = spa; *p >= 0; p++) for (size_t q = 0; q < m->s.strokes[*p].n; q++) if (m->s.strokes[*p].x[q] > axr) axr = m->s.strokes[*p].x[q];
				for (const int *p = spb; *p >= 0; p++) for (size_t q = 0; q < m->s.strokes[*p].n; q++) if (m->s.strokes[*p].x[q] < bxl) bxl = m->s.strokes[*p].x[q];
				dpush(&wgs, (bxl - axr) / m->xh);
			}
		}
	}
	double narrow = L->sp.narrow;
	if (hw_spacing_learn(L, obs, nobs, wgs.v, wgs.n)) { free(obs); free(wgs.v); goto fail; }
	L->sp.narrow = narrow;
	L->m.letter_gap = L->sp.letter;
	L->m.word_gap = L->sp.word;
	free(obs); free(wgs.v);
	if (joined_pairs(L, S, ns, MK, nmk)) goto fail;
	if (joins_learn(L, in, nin, S, ns, MK, nmk)) goto fail;
measured:
	/* the occurrences' states from their instances */
	for (size_t i = 0; i < build_nocc; i++) {
		struct occrec *o = &build_occ[i];
		if (o->inst < 0 || o->state != HW_OCC_USABLE) continue;
		const struct inst *x = &in[o->inst];
		if (x->dropped || x->slip) { o->state = HW_OCC_ODD; o->inst = -1; continue; }
		o->inst = x->var;      /* now the variant */
		for (size_t c = 0; c < L->n && x->var >= 0; c++)
			if (L->l[c].cp == x->cp && L->l[c].var[x->var].out) o->state = HW_OCC_WAY_OUT;
	}
	free(MK); MK = NULL;
	free(with); with = NULL;

	for (size_t si = 0; si < ns; si++) {
		if (S[si].time > L->built) L->built = S[si].time;
	}
	L->samples = 0;
	for (size_t si = 0; si < ns; si++) if (S[si].ninst > 0) L->samples++;
	for (size_t k = 0; k < nin; k++) {
		for (size_t j = 0; j < in[k].nstrokes; j++) { free(in[k].s[j].x); free(in[k].s[j].y); }
		free(in[k].s); free(in[k].rs); free(in[k].pp); free(in[k].pv);
	}
	free(in);
	for (size_t si = 0; si < ns; si++) { hw_sample_free(&S[si].s); free(S[si].own); free(S[si].cnt); }
	free(S);
	jobs_free();
	return 0;
fail:
	for (size_t k = 0; k < nin; k++) {
		for (size_t j = 0; j < in[k].nstrokes; j++) { free(in[k].s[j].x); free(in[k].s[j].y); }
		free(in[k].s); free(in[k].rs); free(in[k].pp); free(in[k].pv);
	}
	free(in);
	for (size_t si = 0; si < ns; si++) { hw_sample_free(&S[si].s); free(S[si].own); free(S[si].cnt); }
	free(S);
	free(MK);
	free(with);
	hw_letters_free(L);
	jobs_free();
	return -1;
}

/* ---------------------------------------------------------------- runs */

int hw_letters_step(int prev)
{
	return prev < 0 ? 0 : (prev + 1) % HW_LETTERS_SLOTS;
}

static const struct hw_letter *in_font(const struct hw_letters *V, unsigned cp)
{
	size_t lo = 0, hi = V->n;
	while (lo < hi) {
		size_t mid = (lo + hi) / 2;
		if (V->l[mid].cp < cp) lo = mid + 1; else hi = mid;
	}
	const struct hw_letter *l = lo < V->n && V->l[lo].cp == cp ? &V->l[lo] : NULL;
	return l && l->covered && l->nvar > 0 ? l : NULL;
}

static long joined_of(const struct hw_letters *V, unsigned a, unsigned b)
{
	for (size_t q = 0; q < V->njoined; q++)
		if (V->joined[q].a == a && V->joined[q].b == b && V->joined[q].v.nstrokes) return (long)q;
	return -1;
}

void hw_letters_glyphs(const struct hw_letters *V, const struct hw_font_joins *J, const unsigned *cp, size_t n, int *var,
		       long *jo, int *jk)
{
	/* every character's step of the cycle (a space takes one, a character the font lacks
	 * starts the run again) */
	int prev = -1;
	for (size_t k = 0; k < n; k++) {
		jo[k] = -1;
		var[k] = -1;
		if (cp[k] == ' ') { prev = hw_letters_step(prev); continue; }
		if (!in_font(V, cp[k])) { prev = -1; continue; }
		prev = hw_letters_step(prev);
		var[k] = prev;  /* the step, for now */
	}
	/* the joined pairs, left to right */
	for (size_t k = 0; k + 1 < n; k++)
		if (var[k] >= 0 && var[k + 1] >= 0 && jo[k] < 0) {
			long q = joined_of(V, cp[k], cp[k + 1]);
			if (q >= 0 && in_font(V, cp[k]) && in_font(V, cp[k + 1])) { jo[k] = jo[k + 1] = q; k++; }
		}
	/* each letter's variant: the first neighbour rule whose neighbour is beside it (a letter,
	 * or a joined pair by its letter on that side), else its cycle's */
	for (size_t k = 0; k < n; k++) {
		if (var[k] < 0) continue;
		if (jo[k] >= 0) { var[k] = -1; continue; }
		const struct hw_letter *l = in_font(V, cp[k]);
		int v = -1;
		for (int r = 0; r < l->nrules && v < 0; r++) {
			const struct hw_rule *ru = &l->rule[r];
			if (ru->var < 0 || ru->var >= l->nvar || !in_font(V, ru->nb)) continue;
			size_t j = ru->side == 0 ? k - 1 : k + 1;
			if ((ru->side == 0 && k == 0) || j >= n || cp[j] == ' ' || !in_font(V, cp[j])) continue;
			if (cp[j] == ru->nb) v = ru->var;
		}
		var[k] = v >= 0 ? v : l->slot[var[k]];
	}
	/* the joins (FR-044) between the letters' variants */
	for (size_t k = 0; jk && k < n; k++) {
		jk[k] = -1;
		if (!J || k + 1 >= n || var[k] < 0 || var[k + 1] < 0) continue;
		const struct hw_letter *a = in_font(V, cp[k]), *b = in_font(V, cp[k + 1]);
		jk[k] = hw_font_join_key(V, J, (size_t)(a - V->l), var[k], (size_t)(b - V->l), var[k + 1]);
	}
}

/* the cycle of a letter's variants, each as often as it is used, spread out (smooth
 * weighted round robin: the same uses give the same cycle) */
/* how much a variant counts in the cycle: its use, a favourite's HW_LETTERS_FAVOURITE times,
 * none when left out (FR-031) */
static long slot_weight(const struct hw_variant *v)
{
	if (v->out) return 0;
	long u = v->use > 0 ? v->use : 1;
	return v->favourite ? u * HW_LETTERS_FAVOURITE : u;
}

static void letter_slots(struct hw_letter *l)
{
	long cur[HW_LETTERS_VARIANTS] = { 0 }, tot = 0;
	int first = -1;
	for (int v = 0; v < l->nvar; v++) { tot += slot_weight(&l->var[v]); if (first < 0 && !l->var[v].out) first = v; }
	for (int k = 0; k < HW_LETTERS_SLOTS; k++) {
		int best = first;
		for (int v = 0; v < l->nvar; v++) {
			long w = slot_weight(&l->var[v]);
			if (!w) continue;
			cur[v] += w;
			if (best < 0 || cur[v] > cur[best]) best = v;
		}
		if (best >= 0) cur[best] -= tot;
		l->slot[k] = (unsigned char)(best >= 0 ? best : 0);
	}
}

int hw_letters_view(const struct hw_letters *L, struct hw_letters *out)
{
	*out = *L;
	out->l = calloc(L->n + 1, sizeof *out->l);
	if (!out->l) return -1;
	for (size_t i = 0; i < L->n; i++) {
		const struct hw_letter *a = &L->l[i];
		struct hw_letter *b = &out->l[i];
		*b = *a;
		b->nvar = 0;
		int map[HW_LETTERS_VARIANTS];
		for (int v = 0; v < a->nvar; v++) {
			map[v] = -1;
			if (a->var[v].out) continue;
			map[v] = b->nvar;
			b->var[b->nvar++] = a->var[v];
		}
		for (int k = 0; k < HW_LETTERS_SLOTS; k++) b->slot[k] = (unsigned char)(map[a->slot[k]] >= 0 ? map[a->slot[k]] : 0);
		/* a rule for a variant left out is gone */
		b->nrules = 0;
		for (int r = 0; r < a->nrules; r++)
			if (a->rule[r].var < a->nvar && map[a->rule[r].var] >= 0) { b->rule[b->nrules] = a->rule[r]; b->rule[b->nrules++].var = map[a->rule[r].var]; }
		if (!b->nvar) b->covered = 0;
	}
	return 0;
}

void hw_letters_view_free(struct hw_letters *v)
{
	free(v->l);
	memset(v, 0, sizeof *v);
}

static void letter_move(struct hw_letter *dst, struct hw_letter *src)
{
	*dst = *src;
	memset(src, 0, sizeof *src);
}

int hw_letters_relearn(unsigned cp, int verbose)
{
	struct hw_letters old;
	int r = hw_letters_load(&old);
	if (r <= 0) return r;
	build_m = old.m;
	build_narrow = old.sp.narrow;
	unsigned long gen = hw_store_generation();
	struct hw_letters one;
	build_only = cp;
	build_progress = verbose > 1;
	/* (its neighbours written joined with it cut by their variants in the model) */
	build_ref = &old;
	int b = hw_letters_build(&one);
	build_ref = NULL;
	hw_letters_free(&old);
	build_only = 0;
	build_progress = 0;
	occ_free();
	if (b) return -1;
	struct hw_letter fresh;
	memset(&fresh, 0, sizeof fresh);
	for (size_t i = 0; i < one.n; i++) if (one.l[i].cp == cp) letter_move(&fresh, &one.l[i]);
	if (!fresh.cp) { fresh.cp = cp; hw_letters_marks_hash(cp, fresh.marks); }
	hw_letters_free(&one);
	/* into the model as it is now (a whole build may have written one meanwhile), under the
	 * lock: its line in place of the old one (or a new one, in code point order), its sides
	 * against the model's side shapes */
	int fd = hw_store_lock();
	if (fd < 0) { for (int v = 0; v < fresh.nvar; v++) variant_free(&fresh.var[v]); return -1; }
	int ret = -1, covered = fresh.covered, nvar = fresh.nvar;
	char *text = NULL;
	size_t len = 0;
	r = hw_store_generation() != gen ? 0 : hw_letters_load(&old);
	if (r <= 0) { ret = r; goto out; }
	for (int v = 0; v < fresh.nvar; v++) {
		fresh.var[v].lc = hw_spacing_side(&old.sp, &fresh.var[v].prof, 0);
		fresh.var[v].rc = hw_spacing_side(&old.sp, &fresh.var[v].prof, 1);
	}
	size_t at = 0;
	while (at < old.n && old.l[at].cp < cp) at++;
	if (at < old.n && old.l[at].cp == cp) {
		for (int v = 0; v < old.l[at].nvar; v++) variant_free(&old.l[at].var[v]);
		letter_move(&old.l[at], &fresh);
	} else {
		struct hw_letter *x = realloc(old.l, (old.n + 1) * sizeof *x);
		if (!x) { hw_letters_free(&old); goto out; }
		old.l = x;
		memmove(&old.l[at + 1], &old.l[at], (old.n - at) * sizeof *old.l);
		letter_move(&old.l[at], &fresh);
		old.n++;
	}
	text = hw_letters_format(&old, &len);
	hw_letters_free(&old);
	if (text && !hw_store_replace("letters/model", text, len)) ret = 1;
out:
	hw_store_unlock(fd);
	for (int v = 0; v < fresh.nvar; v++) variant_free(&fresh.var[v]);
	free(text);
	if (ret == 1 && verbose) printf("relearned\t%d\t%d\n", covered, nvar);
	return ret;
}

static int cmp_occ(const void *a, const void *b)
{
	const struct occrec *x = a, *y = b;
	int c = strcmp(x->id, y->id);
	return c ? c : x->pos - y->pos;
}

int hw_letters_occurrences(unsigned cp, void (*emit)(const struct hw_occ *, void *), void *arg)
{
	struct hw_letters one;
	memset(&build_m, 0, sizeof build_m);
	build_narrow = 1;
	if (hw_letters_load(&one) == 1) { build_m = one.m; build_narrow = one.sp.narrow; hw_letters_free(&one); }
	build_only = cp;
	int b = hw_letters_build(&one);
	build_only = 0;
	if (b) { occ_free(); return -1; }
	hw_letters_free(&one);
	/* in the samples' order, then by place */
	qsort(build_occ, build_nocc, sizeof *build_occ, cmp_occ);
	for (size_t i = 0; i < build_nocc; i++) {
		const struct occrec *o = &build_occ[i];
		struct hw_occ e = { "", o->pos, o->state, o->state == HW_OCC_USABLE || o->state == HW_OCC_WAY_OUT ? o->inst : -1, o->text };
		memcpy(e.id, o->id, 17);
		emit(&e, arg);
	}
	occ_free();
	return 0;
}

int hw_letters_run(const struct hw_letters_opts *o)
{
	if (!o->force && !o->requested && (!hw_charging() || !hw_idle())) {
		if (o->verbose) puts("skip\tcharging");
		return 2;
	}
	char now[17];
	if (hw_letters_inputs(now)) return -1;
	if (!o->force) {
		struct hw_letters old;
		int r = hw_letters_load(&old);
		int same = r == 1 && !strcmp(old.inputs, now) && !hw_letters_marks_changed(&old);
		if (r == 1) hw_letters_free(&old);
		if (same) { if (o->verbose) puts("skip\tnothing-new"); return 2; }
	}
	unsigned long gen = hw_store_generation();
	struct hw_letters L;
	build_progress = o->progress;
	if (hw_letters_build(&L)) return -1;
	size_t len;
	char *text = hw_letters_format(&L, &len);
	int covered = 0;
	for (size_t i = 0; i < L.n; i++) covered += L.l[i].covered;
	long samples = L.samples;
	hw_letters_free(&L);
	if (!text) return -1;
	int fd = hw_store_lock();
	if (fd < 0) { free(text); return -1; }
	int ret = 1;
	if (hw_store_generation() != gen) {
		if (o->verbose) puts("skip\terased");
		ret = 2;
	} else if (hw_store_mkdir("letters") || hw_store_replace("letters/model", text, len)) {
		ret = -1;
	}
	hw_store_unlock(fd);
	free(text);
	if (ret == 1 && o->verbose) printf("built\t%ld\t%d\n", samples, covered);
	return ret;
}

/* ---------------------------------------------------------------- the font */

static int fonts_dir(char *out, size_t n)
{
	const char *x = getenv("XDG_DATA_HOME"), *h = getenv("HOME");
	int r;
	if (x && x[0] == '/') r = snprintf(out, n, "%s/fonts/glassine-handwriting", x);
	else if (h && *h) r = snprintf(out, n, "%s/.local/share/fonts/glassine-handwriting", h);
	else return -1;
	return r < 0 || (size_t)r >= n ? -1 : 0;
}

static void mkdirs(const char *p)
{
	char b[4096];
	snprintf(b, sizeof b, "%s", p);
	for (char *s = b + 1; *s; s++) if (*s == '/') { *s = 0; mkdir(b, 0755); *s = '/'; }
	mkdir(b, 0755);
}

/* refreshes the font cache for the person's font directory (fc-cache, when there is one) */
static void fc_cache(const char *dir)
{
	pid_t p = fork();
	if (p < 0) return;
	if (p == 0) {
		int null = open("/dev/null", O_RDWR);
		if (null >= 0) { dup2(null, 1); dup2(null, 2); }
		execlp("fc-cache", "fc-cache", "-f", dir, (char *)NULL);
		_exit(127);
	}
	int st;
	while (waitpid(p, &st, 0) < 0 && errno == EINTR) {}
}

int hw_font_record(char *name, size_t nn, unsigned *version, char *path, size_t np)
{
	char p[4096], line[4600];
	if (hw_store_path(p, sizeof p, "letters/font")) return -1;
	FILE *f = fopen(p, "re");
	if (!f) return errno == ENOENT ? 0 : -1;
	int ok = fgets(line, sizeof line, f) != NULL;
	fclose(f);
	if (!ok) return 0;
	line[strcspn(line, "\n")] = 0;
	char *a = strtok(line, "\t"), *b = strtok(NULL, "\t"), *c = strtok(NULL, "\t");
	if (!a || !b || !c) return 0;
	if (name) snprintf(name, nn, "%s", a);
	if (version) *version = (unsigned)strtoul(b, NULL, 10);
	if (path) snprintf(path, np, "%s", c);
	return 1;
}

int hw_font_styles(char paths[HW_STYLES][4096], int *nib)
{
	char p[4096], line[4600];
	for (int k = 0; k < HW_STYLES; k++) paths[k][0] = 0;
	if (nib) *nib = -1;
	if (hw_store_path(p, sizeof p, "letters/font")) return -1;
	FILE *f = fopen(p, "re");
	if (!f) return errno == ENOENT ? 0 : -1;
	int first = 1, got = 0;
	while (fgets(line, sizeof line, f)) {
		line[strcspn(line, "\n")] = 0;
		char *a = strtok(line, "\t"), *b = strtok(NULL, "\t"), *c = strtok(NULL, "\t");
		if (first) {
			first = 0;
			if (!a || !b || !c) break;
			snprintf(paths[HW_STYLE_REGULAR], 4096, "%s", c);
			got = 1;
		} else if (a && b && c && !strcmp(a, "style")) {
			for (int k = 1; k < HW_STYLES; k++) if (!strcmp(b, hw_font_style_names[k])) snprintf(paths[k], 4096, "%s", c);
		} else if (a && b && !strcmp(a, "nib") && nib) {
			*nib = atoi(b);
		}
	}
	fclose(f);
	return got;
}

static unsigned last_version(void)
{
	char p[4096], b[64] = "";
	unsigned v = 0;
	if (!hw_store_path(p, sizeof p, "letters/font-version")) {
		FILE *f = fopen(p, "re");
		if (f) { if (fgets(b, sizeof b, f)) v = (unsigned)strtoul(b, NULL, 10); fclose(f); }
	}
	unsigned rv;
	if (hw_font_record(NULL, 0, &rv, NULL, 0) == 1 && rv > v) v = rv;
	return v;
}

static int valid_name(const char *s)
{
	size_t n = strlen(s);
	if (!n || n > HW_FONT_NAME_MAX) return 0;
	for (const unsigned char *p = (const unsigned char *)s; *p; p++) if (*p < 0x20 || *p == 0x7f) return 0;
	unsigned cp[HW_FONT_NAME_MAX + 1];
	return hw_utf8_decode(s, cp, HW_FONT_NAME_MAX + 1) > 0;
}

/* what the font is made from: the model less its head (the build time, its inputs) */
int hw_letters_fingerprint(const struct hw_letters *L, char out[17])
{
	size_t len;
	/* what the font is made of: the best versions are neatening's alone; a model built from
	 * no sample is the empty one (FR-008) */
	struct hw_letters E;
	if (L->samples <= 0) { memset(&E, 0, sizeof E); L = &E; }
	char *t = format_model(L, &len, 0);
	if (!t) return -1;
	char *body = memchr(t, '\n', len);
	body = body ? body + 1 : t + len;
	uint64_t h = fnv(FNV0, body, (size_t)(t + len - body));
	free(t);
	/* and the base it draws the rest from (FR-009): a new base makes the font again */
	const struct hw_base *B = hw_base_get();
	h = fnv(h, B ? B->hash : "no base", B ? 16 : 7);
	snprintf(out, 17, "%016llx", (unsigned long long)h);
	return 0;
}

int hw_font_current(const struct hw_letters *L)
{
	char p[4096], want[17], have[24] = "";
	/* a font installed before the styles (FR-034) is made again with all three */
	char sp[HW_STYLES][4096];
	if (hw_font_styles(sp, NULL) == 1)
		for (int k = 0; k < HW_STYLES; k++) if (!sp[k][0]) return 0;
	if (hw_letters_fingerprint(L, want) || hw_store_path(p, sizeof p, "letters/font-model")) return 0;
	FILE *f = fopen(p, "re");
	if (!f) return 0;
	int ok = fgets(have, sizeof have, f) != NULL;
	fclose(f);
	return ok && !strncmp(have, want, 16);
}

/* the file of a style: handwriting-VERSION.ttf (Regular), handwriting-VERSION-STYLE.ttf */
static void style_path(char *out, size_t n, const char *dir, unsigned v, int style)
{
	char low[16];
	snprintf(low, sizeof low, "%s", hw_font_style_names[style]);
	for (char *c = low; *c; c++) if (*c >= 'A' && *c <= 'Z') *c = (char)(*c - 'A' + 'a');
	if (style == HW_STYLE_REGULAR) snprintf(out, n, "%s/handwriting-%u.ttf", dir, v);
	else snprintf(out, n, "%s/handwriting-%u-%s.ttf", dir, v, low);
}

int hw_font_random(void)
{
	char p[4096], line[4600];
	if (hw_store_path(p, sizeof p, "letters/font")) return -1;
	FILE *f = fopen(p, "re");
	if (!f) return errno == ENOENT ? 0 : -1;
	int on = 0;
	while (fgets(line, sizeof line, f)) if (!strcmp(line, "random\ton\n")) on = 1;
	fclose(f);
	return on;
}

char *hw_font_model_format(const struct hw_letters *L, size_t *len)
{
	struct hw_letters V;
	if (hw_letters_view(L, &V)) return NULL;
	const struct hw_base *B = hw_base_get();
	hw_base_defaults(&V, B);
	struct hw_base_glyph *bg;
	size_t nbg;
	char *t = NULL;
	if (!hw_base_glyphs(&V, B, &bg, &nbg)) {
		size_t n;
		t = format_model(&V, &n, 0);
		struct sbuf b = { t, n, n };
		for (size_t k = 0; t && k < nbg; k++) {
			sput(&b, "{\"base\":");
			char_out(&b, bg[k].cp);
			sput(&b, ",\"variant\":");
			variant_out(&b, &bg[k].v, 0);
			sput(&b, "}\n");
		}
		t = b.s;
		if (len) *len = b.n;
		hw_base_glyphs_free(bg, nbg);
	}
	hw_letters_view_free(&V);
	return t;
}

/* the letter model as the font is made from it: the stored one, or the empty one before the
 * first build (it covers nothing and has no spacing of its own). 0, -1 unreadable. */
static int font_letters(struct hw_letters *L)
{
	int r = hw_letters_load(L);
	if (r < 0) return -1;
	if (r == 0) memset(L, 0, sizeof *L);
	return 0;
}

/* the font's three styles under name into the files path[k] (all or none); 0, -1 */
static int write_styles(const struct hw_letters *L, const char *name, unsigned v, int nib, int random,
			char path[HW_STYLES][4200])
{
	char tmp[4200 + 8];
	int made = 0;
	for (int k = 0; k < HW_STYLES; k++, made++) {
		size_t len;
		unsigned char *ttf = hw_font_build_style(L, name, v, k, nib, random, &len);
		if (!ttf) break;
		snprintf(tmp, sizeof tmp, "%s.tmp", path[k]);
		FILE *f = fopen(tmp, "we");
		int bad = !f || fwrite(ttf, 1, len, f) != len;
		free(ttf);
		if ((f && fclose(f)) || bad || rename(tmp, path[k])) { unlink(tmp); break; }
	}
	if (made == HW_STYLES) return 0;
	for (int k = 0; k < made; k++) unlink(path[k]);
	return -1;
}

/* whether the font would draw anything: a covered letter, or the base */
static int font_has_letters(const struct hw_letters *L)
{
	if (hw_base_get()) return 1;
	for (size_t i = 0; i < L->n; i++) if (L->l[i].covered && L->l[i].nvar) return 1;
	return 0;
}

/* the store's lock held: Autograph made from L and installed. 0, 1 nothing to draw, -1 */
static int install_locked(const struct hw_letters *L, int nib, int random, int verbose, char *dir, size_t ndir)
{
	if (!font_has_letters(L)) { if (verbose) puts("skip\tnothing-covered"); return 1; }
	char opath[HW_STYLES][4096];
	unsigned ov;
	int had = hw_font_record(NULL, 0, &ov, NULL, 0) == 1, onib = -1;
	if (hw_font_styles(opath, &onib) != 1) for (int k = 0; k < HW_STYLES; k++) opath[k][0] = 0;
	/* the nib angle: asked for, else the one the font has, else the default (FR-034) */
	if (nib < 0) nib = onib >= HW_FONT_NIB_MIN && onib <= HW_FONT_NIB_MAX ? onib : HW_FONT_NIB_DEFAULT;
	/* random alternates: asked for or not, else as the font has them (FR-040) */
	if (random < 0) random = had && hw_font_random() == 1;
	char path[HW_STYLES][4200];
	if (fonts_dir(dir, ndir)) return -1;
	unsigned v = last_version() + 1;
	char fp[18] = "";
	if (!hw_letters_fingerprint(L, fp)) strcat(fp, "\n");
	mkdirs(dir);
	for (int k = 0; k < HW_STYLES; k++) style_path(path[k], sizeof path[k], dir, v, k);
	/* all three styles, or none (FR-034); the name is always Autograph (FR-011) */
	if (write_styles(L, HW_FONT_NAME, v, nib, random, path)) return -1;
	char rec[13000], vs[32];
	int n = snprintf(rec, sizeof rec, "%s\t%u\t%s\n", HW_FONT_NAME, v, path[HW_STYLE_REGULAR]);
	for (int k = 1; k < HW_STYLES && n > 0 && (size_t)n < sizeof rec; k++)
		n += snprintf(rec + n, sizeof rec - (size_t)n, "style\t%s\t%s\n", hw_font_style_names[k], path[k]);
	if (n > 0 && (size_t)n < sizeof rec) n += snprintf(rec + n, sizeof rec - (size_t)n, "nib\t%d\n", nib);
	if (random && n > 0 && (size_t)n < sizeof rec) n += snprintf(rec + n, sizeof rec - (size_t)n, "random\ton\n");
	snprintf(vs, sizeof vs, "%u\n", v);
	if (n <= 0 || (size_t)n >= sizeof rec || hw_store_mkdir("letters") || hw_store_replace("letters/font-version", vs, strlen(vs)) ||
	    hw_store_replace("letters/font", rec, (size_t)n) || hw_store_replace("letters/font-model", fp, strlen(fp))) {
		for (int k = 0; k < HW_STYLES; k++) unlink(path[k]);
		return -1;
	}
	for (int k = 0; had && k < HW_STYLES; k++)
		if (opath[k][0] && strcmp(opath[k], path[0]) && strcmp(opath[k], path[1]) && strcmp(opath[k], path[2])) unlink(opath[k]);
	if (verbose) printf("installed\t%s\t%u\t%s\n", HW_FONT_NAME, v, path[HW_STYLE_REGULAR]);
	return 0;
}

int hw_font_install(int nib, int random, int verbose)
{
	struct hw_letters L;
	if (font_letters(&L)) return -1;
	char dir[4096];
	int fd = hw_store_lock();
	if (fd < 0) { hw_letters_free(&L); return -1; }
	int ret = install_locked(&L, nib, random, verbose, dir, sizeof dir);
	hw_store_unlock(fd);
	hw_letters_free(&L);
	if (!ret) fc_cache(dir);
	return ret;
}

int hw_font_export(const char *name, const char *dir, int verbose)
{
	if (!valid_name(name)) return -2;
	struct hw_letters L;
	if (font_letters(&L)) return -1;
	if (!font_has_letters(&L)) { hw_letters_free(&L); if (verbose) puts("skip\tnothing-covered"); return 1; }
	/* the installed font's settings and version; the files named by the name's letters */
	char sp[HW_STYLES][4096];
	int nib = -1;
	if (hw_font_styles(sp, &nib) != 1 || nib < HW_FONT_NIB_MIN || nib > HW_FONT_NIB_MAX) nib = HW_FONT_NIB_DEFAULT;
	unsigned v = 0;
	if (hw_font_record(NULL, 0, &v, NULL, 0) != 1 || !v) v = 1;
	char base[64];
	int bn = 0;
	for (const char *c = name; *c && bn < 40; c++)
		if ((*c >= 'A' && *c <= 'Z') || (*c >= 'a' && *c <= 'z') || (*c >= '0' && *c <= '9')) base[bn++] = *c;
	base[bn] = 0;
	if (!bn) snprintf(base, sizeof base, "Handwriting");
	char path[HW_STYLES][4200];
	for (int k = 0; k < HW_STYLES; k++) snprintf(path[k], sizeof path[k], "%s/%s-%s.ttf", dir, base, hw_font_style_names[k]);
	int r = write_styles(&L, name, v, nib, hw_font_random() == 1, path);
	hw_letters_free(&L);
	if (r) { fprintf(stderr, "glassine-handwriting: %s: %s\n", dir, strerror(errno ? errno : EIO)); return -1; }
	for (int k = 0; verbose && k < HW_STYLES; k++) printf("exported\t%s\t%s\n", hw_font_style_names[k], path[k]);
	return 0;
}

int hw_font_auto(int verbose)
{
	char name[256];
	unsigned v;
	int f = hw_font_record(name, sizeof name, &v, NULL, 0);
	if (f < 0) return -1;
	/* installed from the start (FR-009) */
	if (!f) return hw_font_install(-1, -1, verbose);
	/* a font from before Autograph (another name): made again at once, the letters as they are */
	if (strcmp(name, HW_FONT_NAME)) return hw_font_install(-1, -1, verbose);
	/* then rebuilt in the background while charging, once the letter model changed (FR-011),
	 * under the conditions adaptation has (FR-008: learning on, charging, idle) */
	if (!hw_charging() || !hw_idle()) { if (verbose) puts("skip\tcharging"); return 2; }
	if (!hw_learning_on()) { if (verbose) puts("skip\tlearning"); return 2; }
	struct hw_letters_opts lo = { 0, 0, 0, 0 };
	if (hw_letters_run(&lo) < 0) return -1;
	struct hw_letters L;
	if (font_letters(&L)) return -1;
	int cur = hw_font_current(&L);
	hw_letters_free(&L);
	if (cur) { if (verbose) puts("skip\tcurrent"); return 2; }
	return hw_font_install(-1, -1, verbose);
}

static void remove_font_locked(void)
{
	char path[HW_STYLES][4096], dir[4096];
	if (hw_font_styles(path, NULL) == 1)
		for (int k = 0; k < HW_STYLES; k++) if (path[k][0]) unlink(path[k]);
	hw_store_replace("letters/font", NULL, 0);
	if (!fonts_dir(dir, sizeof dir)) { rmdir(dir); fc_cache(dir); }
}

int hw_font_remove(void)
{
	char p[4096];
	struct stat st;
	if (hw_store_path(p, sizeof p, "") || stat(p, &st)) return 0;
	int had = hw_font_record(NULL, 0, NULL, NULL, 0);
	if (had <= 0) return had;
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	remove_font_locked();
	hw_store_unlock(fd);
	return 1;
}

void hw_letters_erase_locked(void)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "letters")) return;
	const char *files[] = { "model", "model.tmp", "usable", "usable.tmp", "font-model", "font-model.tmp" };
	for (size_t i = 0; i < sizeof files / sizeof *files; i++) {
		char q[4200];
		snprintf(q, sizeof q, "%s/%s", p, files[i]);
		unlink(q);
	}
	/* Autograph back to the base (FR-008), its version counting on; without a base, gone */
	if (hw_font_record(NULL, 0, NULL, NULL, 0) == 1 && !hw_store_writer()) {
		struct hw_letters E;
		memset(&E, 0, sizeof E);
		char dir[4096];
		if (!install_locked(&E, -1, -1, 0, dir, sizeof dir)) { fc_cache(dir); return; }
	}
	remove_font_locked();
	const char *rest[] = { "font", "font.tmp" };     /* font-version stays: versions count on */
	for (size_t i = 0; i < sizeof rest / sizeof *rest; i++) {
		char q[4200];
		snprintf(q, sizeof q, "%s/%s", p, rest[i]);
		unlink(q);
	}
	rmdir(p);
}
