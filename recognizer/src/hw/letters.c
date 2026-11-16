// SPDX-License-Identifier: GPL-2.0-or-later
// The letter model (see letters.h and docs/recognizer.md, "The letter model").
#include <errno.h>
#include <fcntl.h>
#include <json-c/json.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include "adapt.h"
#include "letters.h"
#include "sample.h"
#include "store.h"

/* ---------------------------------------------------------------- small helpers */

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
	for (size_t j = 0; j < v->nstrokes; j++) { free(v->s[j].x); free(v->s[j].y); }
	free(v->s);
	memset(v, 0, sizeof *v);
}

void hw_letters_free(struct hw_letters *L)
{
	for (size_t i = 0; i < L->n; i++)
		for (int k = 0; k < L->l[i].nvar; k++) variant_free(&L->l[i].var[k]);
	free(L->l);
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

char *hw_letters_format(const struct hw_letters *L, size_t *len)
{
	struct sbuf b = { 0 };
	sput(&b, "{\"format\":\"%s\",\"version\":%d,\"inputs\":\"%s\",\"samples\":%ld,\"built\":%lld}\n",
	     HW_LETTERS_FORMAT, HW_LETTERS_VERSION, L->inputs, L->samples, L->built);
	const struct hw_metrics *m = &L->m;
	const char *names[] = { "cap_height", "ascender", "descender", "slant", "letter_spacing", "word_spacing", "ink_width" };
	double vals[] = { m->cap, m->asc, m->desc, m->slant, m->letter_gap, m->word_gap, m->ink };
	sput(&b, "{\"metrics\":{\"x_height\":1");
	for (int i = 0; i < 7; i++) { sput(&b, ",\"%s\":", names[i]); num(&b, vals[i]); }
	sput(&b, "}}\n");
	for (size_t i = 0; i < L->n; i++) {
		const struct hw_letter *l = &L->l[i];
		char u[8];
		size_t k = hw_utf8_encode(l->cp, u);
		u[k] = 0;
		json_object *js = json_object_new_string(u);
		sput(&b, "{\"char\":%s,\"samples\":%d,\"covered\":%s,\"variants\":[", json_object_to_json_string_ext(js, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE),
		     l->samples, l->covered ? "true" : "false");
		json_object_put(js);
		for (int v = 0; v < l->nvar; v++) {
			const struct hw_variant *x = &l->var[v];
			sput(&b, "%s{\"width\":", v ? "," : "");
			num(&b, x->width);
			sput(&b, ",\"weight\":");
			num(&b, x->weight);
			sput(&b, ",\"members\":%d,\"strokes\":[", x->members);
			for (size_t j = 0; j < x->nstrokes; j++) {
				sput(&b, "%s[", j ? "," : "");
				for (size_t p = 0; p < x->s[j].n; p++) {
					sput(&b, "%s[", p ? "," : "");
					num(&b, x->s[j].x[p]);
					sput(&b, ",");
					num(&b, x->s[j].y[p]);
					sput(&b, "]");
				}
				sput(&b, "]");
			}
			sput(&b, "]}");
		}
		sput(&b, "]}\n");
	}
	if (len) *len = b.n;
	return b.s;
}

static double jnum(json_object *o, const char *k, double def)
{
	json_object *v;
	return json_object_object_get_ex(o, k, &v) ? json_object_get_double(v) : def;
}

int hw_letters_parse(const char *text, struct hw_letters *L)
{
	memset(L, 0, sizeof *L);
	const char *s = text;
	int line = 0, ok = 0;
	size_t cap = 0;
	while (s && *s) {
		const char *e = strchr(s, '\n');
		size_t n = e ? (size_t)(e - s) : strlen(s);
		char *ln = strndup(s, n);
		s = e ? e + 1 : s + n;
		if (!ln) goto bad;
		json_object *o = json_tokener_parse(ln);
		free(ln);
		if (!o) { if (n) goto bad; continue; }
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
						    jnum(v, "ink_width", 0.07) };
		} else if (json_object_object_get_ex(o, "char", &v)) {
			unsigned cp[2];
			if (hw_utf8_decode(json_object_get_string(v), cp, 2) != 1) { json_object_put(o); goto bad; }
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
			json_object *vs;
			if (json_object_object_get_ex(o, "variants", &vs) && json_object_is_type(vs, json_type_array)) {
				size_t nv = json_object_array_length(vs);
				for (size_t k = 0; k < nv && l->nvar < HW_LETTERS_VARIANTS; k++) {
					json_object *x = json_object_array_get_idx(vs, k), *st;
					struct hw_variant *w = &l->var[l->nvar++];
					w->width = jnum(x, "width", 0);
					w->weight = jnum(x, "weight", 0);
					w->members = (int)jnum(x, "members", 0);
					if (!json_object_object_get_ex(x, "strokes", &st)) continue;
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
				}
			}
		}
		json_object_put(o);
		line++;
	}
	if (ok) return 0;
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
	snprintf(v, sizeof v, "letters %d\n", HW_LETTERS_VERSION);
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
	int dropped;
	double *rs;                     /* resampled, centred: 2 * total points (per group) */
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
#define DIFF_MIN 0.06   /* a further variant must differ at least this much */

/* builds one variant around a seed from its group */
static int make_variant(struct inst *in, const struct group *g, int seed, struct hw_variant *v)
{
	const double *sr = in[seed].rs;
	double *acc = calloc((size_t)g->total * 2, sizeof *acc), wsum = 0;
	if (!acc) return -1;
	int members = 0;
	for (int k = 0; k < g->n; k++) {
		struct inst *x = &in[g->members[k]];
		double d = rdist(x->rs, sr, g->total);
		if (d > 2 * SIGMA) continue;
		double kw = x->w * exp(-(d / SIGMA) * (d / SIGMA));
		for (int i = 0; i < 2 * g->total; i++) acc[i] += kw * x->rs[i];
		wsum += kw;
		members++;
	}
	if (wsum <= 0) { free(acc); return -1; }
	for (int i = 0; i < 2 * g->total; i++) acc[i] /= wsum;
	/* back where the seed's ink sits: the centred average plus the seed's centre */
	struct inst *s = &in[seed];
	double scx = 0, scy = 0;
	{
		int tot = 0;
		for (size_t j = 0; j < s->nstrokes; j++) tot += g->pts[j];
		double *raw = malloc((size_t)tot * 2 * sizeof *raw);
		if (!raw) { free(acc); return -1; }
		int o = 0;
		for (size_t j = 0; j < s->nstrokes; j++) { resample(&s->s[j], (size_t)g->pts[j], raw + 2 * o); o += g->pts[j]; }
		for (int i = 0; i < tot; i++) { scx += raw[2 * i]; scy += raw[2 * i + 1]; }
		scx /= tot; scy /= tot;
		free(raw);
	}
	v->nstrokes = g->nstrokes;
	v->s = calloc(g->nstrokes, sizeof *v->s);
	if (!v->s) { free(acc); return -1; }
	int o = 0;
	double xmin = INFINITY, xmax = -INFINITY;
	for (size_t j = 0; j < g->nstrokes; j++) {
		int n = g->pts[j];
		struct hw_lstroke *t = &v->s[j];
		t->n = (size_t)n;
		t->x = malloc((size_t)n * sizeof(double));
		t->y = malloc((size_t)n * sizeof(double));
		if (!t->x || !t->y) { free(acc); return -1; }
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
	for (size_t j = 0; j < v->nstrokes; j++)
		for (size_t i = 0; i < v->s[j].n; i++) v->s[j].x[i] -= xmin;
	v->width = xmax - xmin;
	v->weight = wsum;
	v->members = members;
	free(acc);
	return 0;
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

int hw_letters_build(struct hw_letters *L)
{
	memset(L, 0, sizeof *L);
	if (hw_letters_inputs(L->inputs)) return -1;
	struct hw_entry *es;
	size_t ne;
	unsigned long gen;
	if (hw_samples_load(&es, &ne, &gen)) return -1;
	struct smp *S = calloc(ne + 1, sizeof *S);
	if (!S) { hw_entries_free(es, ne); return -1; }
	size_t ns = 0;
	for (size_t i = 0; i < ne; i++) {
		if (strcmp(es[i].meta.source, "prompted")) continue;
		struct smp *m = &S[ns];
		char err[128];
		if (hw_sample_parse(es[i].json, &m->s, err, sizeof err)) continue;
		if (strcmp(m->s.spans_source, "exact") || !m->s.nstrokes) { hw_sample_free(&m->s); continue; }
		/* a sample stored twice counts once */
		int dup = 0;
		for (size_t k = 0; k < ns && !dup; k++) dup = !strcmp(S[k].id, es[i].meta.id);
		if (dup) { hw_sample_free(&m->s); continue; }
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
		m->first = (int)nin;
		int *cnt = calloc(m->s.nstrokes + 1, sizeof *cnt);
		if (!cnt) continue;
		for (size_t k = 0; k < m->s.nspans && k < nc; k++)
			for (int *p = m->s.spans[k]; p && *p >= 0; p++) if ((size_t)*p < m->s.nstrokes) cnt[*p]++;
		for (size_t k = 0; k < m->s.nspans && k < nc; k++) {
			int *sp = m->s.spans[k];
			if (cps[k] == ' ' || !sp || sp[0] < 0) continue;
			int own = 1, np = 0;
			for (int *p = sp; *p >= 0; p++) { if ((size_t)*p >= m->s.nstrokes || cnt[*p] != 1) own = 0; np++; }
			if (!own) continue;
			if (nin == cin) { cin = cin ? cin * 2 : 1024; struct inst *x = realloc(in, cin * sizeof *x); if (!x) break; in = x; }
			struct inst *x = &in[nin];
			memset(x, 0, sizeof *x);
			x->cp = cps[k]; x->sample = (int)si; x->pos = (int)k;
			x->x0 = x->y0 = INFINITY; x->x1 = x->y1 = -INFINITY;
			x->s = calloc((size_t)np, sizeof *x->s);
			if (!x->s) break;
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
				for (size_t q = 0; q < k2->n; q++) {
					if (k2->x[q] < x->x0) x->x0 = k2->x[q];
					if (k2->x[q] > x->x1) x->x1 = k2->x[q];
					if (k2->y[q] < x->y0) x->y0 = k2->y[q];
					if (k2->y[q] > x->y1) x->y1 = k2->y[q];
				}
			}
			if (!x->nstrokes) { free(x->s); continue; }
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
			nin++;
		}
		m->ninst = (int)nin - m->first;
		free(cnt);
	}
	free(pr);

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
		double X0 = x->x0, Y0 = x->y0, Y1 = x->y1;
		x->x1 = (x->x1 - X0) / m->xh; x->x0 = 0;
		x->y0 = (m->base - Y1) / m->xh;       /* bottom */
		x->y1 = (m->base - Y0) / m->xh;       /* top */
		if (x->x1 > 6 || x->y1 - x->y0 > 6) x->dropped = 1;
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

	/* the characters */
	size_t nu = 0;
	unsigned *uc = malloc((nin + 1) * sizeof *uc);
	for (size_t k = 0; uc && k < nin; k++) if (!in[k].dropped) uc[nu++] = in[k].cp;
	if (uc) qsort(uc, nu, sizeof *uc, cmp_uint);
	size_t nd = 0;
	for (size_t k = 0; k < nu; k++) if (!nd || uc[nd - 1] != uc[k]) uc[nd++] = uc[k];
	L->l = calloc(nd + 1, sizeof *L->l);
	if (!L->l || !uc) { free(uc); goto fail; }
	for (size_t c = 0; c < nd; c++) {
		struct hw_letter *l = &L->l[L->n++];
		l->cp = uc[c];
		/* its versions, in sample order */
		int *v = NULL, nv = 0;
		v = malloc((nin + 1) * sizeof *v);
		if (!v) continue;
		for (size_t k = 0; k < nin; k++) if (!in[k].dropped && in[k].cp == uc[c]) v[nv++] = (int)k;
		/* a size far from the character's others is more likely a slip than a style */
		if (nv >= 3) {
			double *hs = malloc((size_t)nv * sizeof *hs);
			for (int i = 0; hs && i < nv; i++) hs[i] = in[v[i]].y1 - in[v[i]].y0 + (in[v[i]].x1 - in[v[i]].x0);
			double mh = hs ? median(hs, (size_t)nv, 0) : 0;
			free(hs);
			int w = 0;
			for (int i = 0; i < nv; i++) {
				double hh = in[v[i]].y1 - in[v[i]].y0 + (in[v[i]].x1 - in[v[i]].x0);
				if (mh <= 0 || (hh >= 0.4 * mh && hh <= 2.5 * mh)) v[w++] = v[i];
			}
			nv = w;
		}
		int last = -1;
		for (int i = 0; i < nv; i++) if (in[v[i]].sample != last) { l->samples++; last = in[v[i]].sample; }
		l->covered = l->samples >= HW_LETTERS_MIN;
		if (!l->covered) { free(v); continue; }
		/* groups by stroke count */
		struct group G[16];
		int ng = 0;
		for (int i = 0; i < nv; i++) {
			size_t sc = in[v[i]].nstrokes;
			int g = 0;
			while (g < ng && G[g].nstrokes != sc) g++;
			if (g == ng) {
				if (ng == 16) continue;
				G[ng] = (struct group){ sc, calloc((size_t)nv, sizeof(int)), 0, calloc(sc, sizeof(int)), 0 };
				ng++;
			}
			if (G[g].members) G[g].members[G[g].n++] = v[i];
		}
		for (int g = 0; g < ng; g++) {
			struct group *gr = &G[g];
			if (!gr->members || !gr->pts) continue;
			/* points per stroke: from the median length of each stroke */
			for (size_t j = 0; j < gr->nstrokes; j++) {
				double *ls = malloc((size_t)gr->n * sizeof *ls);
				if (!ls) continue;
				for (int i = 0; i < gr->n; i++) ls[i] = slen(&in[gr->members[i]].s[j]);
				double ml = median(ls, (size_t)gr->n, 0);
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
			/* consistency: closeness to the group's medoid, the group's share of all */
			int med = -1;
			double best = INFINITY;
			for (int i = 0; i < gr->n; i++) {
				double s = 0;
				for (int k = 0; k < gr->n; k++) s += rdist(in[gr->members[i]].rs, in[gr->members[k]].rs, gr->total);
				if (s < best - 1e-12) { best = s; med = gr->members[i]; }
			}
			double share = (double)gr->n / nv;
			for (int i = 0; i < gr->n && med >= 0; i++) {
				struct inst *x = &in[gr->members[i]];
				double d = rdist(x->rs, in[med].rs, gr->total);
				double c = exp(-(d / (2 * TAU / 3)) * (d / (2 * TAU / 3)));
				x->w *= (c < 0.15 ? 0.15 : c) * (0.5 + 0.5 * share);
			}
		}
		/* the main way of writing it: the stroke count with the most weight; another one
		 * gives variants only when it is about as strong (an extra stroke that is a slip, a
		 * lead-in or a neighbour's part stays out) */
		double gw[16] = { 0 };
		int main = 0;
		for (int g = 0; g < ng; g++) {
			for (int i = 0; i < G[g].n; i++) gw[g] += in[G[g].members[i]].w;
			if (gw[g] > gw[main] + 1e-12) main = g;
		}
		/* seeds: the strongest version, then strong versions that differ from the chosen */
		int seeds[HW_LETTERS_VARIANTS], nsd = 0, sgr[HW_LETTERS_VARIANTS];
		while (nsd < HW_LETTERS_VARIANTS) {
			double bs = -1;
			int bi = -1, bg = -1;
			for (int g = 0; g < ng; g++) {
				if (g != main && (!nsd || G[g].n < 3 || gw[g] < 0.5 * gw[main])) continue;
				for (int i = 0; i < G[g].n; i++) {
					int x = G[g].members[i];
					double dmin = 1;
					for (int k = 0; k < nsd; k++) {
						if (seeds[k] == x) dmin = 0;
						else if (sgr[k] == g) { double d = rdist(in[x].rs, in[seeds[k]].rs, G[g].total) / TAU; if (d < dmin) dmin = d; }
					}
					if (nsd && dmin * TAU < DIFF_MIN) continue;
					/* support: other versions of the group near it */
					int sup = 0;
					for (int k = 0; k < G[g].n; k++) if (rdist(in[x].rs, in[G[g].members[k]].rs, G[g].total) <= 2 * SIGMA) sup++;
					if (nsd && sup < 2) continue;
					double score = in[x].w * (nsd ? dmin : 1) * gw[g] / gw[main];
					if (score > bs + 1e-12) { bs = score; bi = x; bg = g; }
				}
			}
			if (bi < 0) break;
			seeds[nsd] = bi; sgr[nsd] = bg; nsd++;
		}
		for (int k = 0; k < nsd; k++)
			if (!make_variant(in, &G[sgr[k]], seeds[k], &l->var[l->nvar])) l->nvar++;
			else variant_free(&l->var[l->nvar]);
		if (!l->nvar) l->covered = 0;
		for (int g = 0; g < ng; g++) { free(G[g].members); free(G[g].pts); }
		free(v);
	}
	free(uc);

	/* gaps between letters and words: neighbouring own letters of one sample */
	struct dvec lgs = { 0 }, wgs = { 0 };
	for (size_t si = 0; si < ns; si++) {
		struct smp *m = &S[si];
		size_t nc = hw_utf8_decode(m->s.text, cps, 4096);
		if (nc < 2 || m->xh <= 0) continue;
		for (int k = m->first; k + 1 < m->first + m->ninst; k++) {
			struct inst *a = &in[k], *b = &in[k + 1];
			if (a->dropped || b->dropped) continue;
			/* the ink's right and left edges in the sample */
			const int *spa = m->s.spans[a->pos], *spb = m->s.spans[b->pos];
			double axr = -INFINITY, bxl = INFINITY;
			for (const int *p = spa; *p >= 0; p++) for (size_t q = 0; q < m->s.strokes[*p].n; q++) if (m->s.strokes[*p].x[q] > axr) axr = m->s.strokes[*p].x[q];
			for (const int *p = spb; *p >= 0; p++) for (size_t q = 0; q < m->s.strokes[*p].n; q++) if (m->s.strokes[*p].x[q] < bxl) bxl = m->s.strokes[*p].x[q];
			double gap = (bxl - axr) / m->xh;
			if (b->pos == a->pos + 1) dpush(&lgs, gap);
			else if (b->pos == a->pos + 2 && b->pos - 1 < (int)nc && cps[b->pos - 1] == ' ') dpush(&wgs, gap);
		}
	}
	L->m.letter_gap = clampd(median(lgs.v, lgs.n, 0.15), 0.04, 0.6);
	L->m.word_gap = clampd(median(wgs.v, wgs.n, L->m.letter_gap + 0.55), L->m.letter_gap + 0.2, 2.0);
	free(lgs.v); free(wgs.v);

	for (size_t si = 0; si < ns; si++) {
		if (S[si].time > L->built) L->built = S[si].time;
	}
	L->samples = 0;
	for (size_t si = 0; si < ns; si++) if (S[si].ninst > 0) L->samples++;
	for (size_t k = 0; k < nin; k++) {
		for (size_t j = 0; j < in[k].nstrokes; j++) { free(in[k].s[j].x); free(in[k].s[j].y); }
		free(in[k].s); free(in[k].rs);
	}
	free(in);
	for (size_t si = 0; si < ns; si++) hw_sample_free(&S[si].s);
	free(S);
	return 0;
fail:
	for (size_t k = 0; k < nin; k++) {
		for (size_t j = 0; j < in[k].nstrokes; j++) { free(in[k].s[j].x); free(in[k].s[j].y); }
		free(in[k].s); free(in[k].rs);
	}
	free(in);
	for (size_t si = 0; si < ns; si++) hw_sample_free(&S[si].s);
	free(S);
	hw_letters_free(L);
	return -1;
}

/* ---------------------------------------------------------------- runs */

int hw_letters_rotate(int prev, int n)
{
	if (prev < 0 || n < 2) return 0;
	if (prev == 0) return 1;
	if (prev == 1 && n == 3) return 2;
	if (prev == 2 && n == 2) return 1;
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
		int same = r == 1 && !strcmp(old.inputs, now);
		if (r == 1) hw_letters_free(&old);
		if (same) { if (o->verbose) puts("skip\tnothing-new"); return 2; }
	}
	unsigned long gen = hw_store_generation();
	struct hw_letters L;
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

int hw_font_install(const char *name, int verbose)
{
	struct hw_letters L;
	int r = hw_letters_load(&L);
	if (r < 0) return -1;
	if (r == 0) { if (verbose) puts("skip\tno-letters"); return 1; }
	int covered = 0;
	for (size_t i = 0; i < L.n; i++) covered += L.l[i].covered;
	if (!covered) { hw_letters_free(&L); if (verbose) puts("skip\tnothing-covered"); return 1; }
	char oname[256] = HW_FONT_NAME_DEFAULT, opath[4096] = "";
	unsigned ov;
	int had = hw_font_record(oname, sizeof oname, &ov, opath, sizeof opath) == 1;
	if (!had) snprintf(oname, sizeof oname, "%s", HW_FONT_NAME_DEFAULT);
	if (name && !valid_name(name)) { hw_letters_free(&L); return -2; }
	const char *nm = name ? name : oname;
	char dir[4096], path[4200], tmp[4300];
	if (fonts_dir(dir, sizeof dir)) { hw_letters_free(&L); return -1; }
	int fd = hw_store_lock();
	if (fd < 0) { hw_letters_free(&L); return -1; }
	unsigned v = last_version() + 1;
	size_t len;
	unsigned char *ttf = hw_font_build(&L, nm, v, &len);
	hw_letters_free(&L);
	int ret = -1;
	if (!ttf) goto out;
	mkdirs(dir);
	snprintf(path, sizeof path, "%s/handwriting-%u.ttf", dir, v);
	snprintf(tmp, sizeof tmp, "%s.tmp", path);
	FILE *f = fopen(tmp, "we");
	if (!f) goto out;
	int bad = fwrite(ttf, 1, len, f) != len;
	if (fclose(f) || bad || rename(tmp, path)) { unlink(tmp); goto out; }
	char rec[4600], vs[32];
	int k = snprintf(rec, sizeof rec, "%s\t%u\t%s\n", nm, v, path);
	snprintf(vs, sizeof vs, "%u\n", v);
	if (hw_store_mkdir("letters") || hw_store_replace("letters/font-version", vs, strlen(vs)) ||
	    hw_store_replace("letters/font", rec, (size_t)k)) { unlink(path); goto out; }
	if (had && strcmp(opath, path)) unlink(opath);
	ret = 0;
out:
	hw_store_unlock(fd);
	free(ttf);
	if (!ret) {
		fc_cache(dir);
		if (verbose) printf("installed\t%s\t%u\t%s\n", nm, v, path);
	}
	return ret;
}

static void remove_font_locked(void)
{
	char path[4096], dir[4096];
	if (hw_font_record(NULL, 0, NULL, path, sizeof path) == 1) unlink(path);
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
	remove_font_locked();
	char p[4096];
	if (hw_store_path(p, sizeof p, "letters")) return;
	const char *files[] = { "model", "model.tmp", "font", "font.tmp", "font-version", "font-version.tmp" };
	for (size_t i = 0; i < sizeof files / sizeof *files; i++) {
		char q[4200];
		snprintf(q, sizeof q, "%s/%s", p, files[i]);
		unlink(q);
	}
	rmdir(p);
}
