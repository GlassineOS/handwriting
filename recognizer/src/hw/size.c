// SPDX-License-Identifier: GPL-2.0-or-later
// Size (spec 001 FR-017, docs/recognizer.md "Size"): the reference from the person's letters,
// the ink grouped into glyphs, and each glyph's weight on the model's classes.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include "recog.h"
#include "sample.h"
#include "size.h"
#include "store.h"
#include "words.h"

/* ---------------------------------------------------------------- characters */

static int in(unsigned c, const unsigned *set)
{
	for (; *set; set++) if (*set == c) return 1;
	return 0;
}

static const unsigned DOTS[] = { '.', 0xB7, 0 };
static const unsigned FLATS[] = { '-', '_', 0x2010, 0x2013, 0x2014, 0x2212, 0 };
static const unsigned THINS[] = { ',', '\'', '`', '"', 0x2018, 0x2019, 0x201A, 0x201C, 0x201D, 0x201E, 0 };
/* letters as tall as an x: their capitals differ mostly in size */
static const unsigned SMALL[] = { 'a', 'c', 'e', 'm', 'n', 'o', 'r', 's', 'u', 'v', 'w', 'x', 'z', 0 };
static const unsigned ASCENDERS[] = { 'b', 'd', 'f', 'h', 'k', 'l', 't', 0 };
/* case pairs whose forms differ mostly in size, and those that differ in their place on
 * the line (the small one hangs below it) */
static const unsigned PAIRS[] = { 'c', 'o', 's', 'u', 'v', 'w', 'x', 'z', 0 };
static const unsigned HANGING[] = { 'p', 'y', 0 };

static int is_mark(unsigned c) { return in(c, DOTS) || in(c, FLATS) || in(c, THINS); }
static int is_small(unsigned c) { return in(c, SMALL); }
static int is_capital(unsigned c) { return hw_is_letter(c) && hw_fold_cp(c) != c; }
static int is_tall(unsigned c) { return is_capital(c) || (c >= '0' && c <= '9') || in(c, ASCENDERS); }

/* ---------------------------------------------------------------- the person's letters */

/* measured per sample: a word's small-letter and capital height (each the median of its
 * characters written with strokes of their own), or a lone character's height */
struct meas {
	char id[17];
	int kind;                       /* 0 nothing, 1 word, 2 lone small, 3 lone tall */
	double v[2];                    /* word: x, c (0: none); lone: height */
	double per_mm, line;            /* the sample's units: points per mm, area line (0: unknown) */
	int em;                         /* written in em */
};

#define PERSON_WORDS 48
#define PERSON_LONE 48

static struct {
	int have;
	char lang[36];
	struct stat st;                 /* samples.jsonl when measured */
	unsigned long gen;
	struct meas *m;
	size_t n;
	double mm[4], ln[4], em[4];     /* x, c, xs, cs per mm, per area line, in em (0: not known) */
	int nwords, nlone;
} P;

static int cmpd(const void *a, const void *b)
{
	double x = *(const double *)a, y = *(const double *)b;
	return (x > y) - (x < y);
}

static double median(double *v, size_t n)
{
	if (!n) return 0;
	qsort(v, n, sizeof *v, cmpd);
	return n % 2 ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

static void bbox_of(const struct hw_ink *k, const int *idx, size_t ns, double b[4])
{
	b[0] = b[1] = INFINITY; b[2] = b[3] = -INFINITY;
	for (size_t j = 0; idx ? idx[j] >= 0 : j < ns; j++) {
		const struct hw_ink *s = &k[idx ? (size_t)idx[j] : j];
		for (size_t i = 0; i < s->n; i++) {
			if (s->x[i] < b[0]) b[0] = s->x[i];
			if (s->y[i] < b[1]) b[1] = s->y[i];
			if (s->x[i] > b[2]) b[2] = s->x[i];
			if (s->y[i] > b[3]) b[3] = s->y[i];
		}
	}
}

static void measure(const char *line, struct meas *m)
{
	struct hw_sample s;
	char err[64];
	m->kind = 0;
	if (hw_sample_parse(line, &s, err, sizeof err)) return;
	unsigned cp[HW_MAX_LABELS];
	size_t n = hw_utf8_decode(s.text, cp, HW_MAX_LABELS);
	m->per_mm = s.units.per_mm > 0 && !strcmp(s.units.xy, "device") ? s.units.per_mm : 0;
	m->line = s.area.set && s.area.line > 0 ? s.area.line : 0;
	m->em = !strcmp(s.units.xy, "em");
	if (n == 1 && s.nstrokes && (is_small(cp[0]) || is_tall(cp[0]))) {
		double b[4];
		bbox_of(s.strokes, NULL, s.nstrokes, b);
		if (b[3] > b[1]) { m->kind = is_small(cp[0]) ? 2 : 3; m->v[0] = b[3] - b[1]; }
	} else if (n >= 3 && s.nspans == n) {
		/* strokes shared by two characters (joined writing) say nothing about either */
		int *uses = calloc(s.nstrokes + 1, sizeof *uses);
		double *xs = malloc((n + 1) * sizeof *xs), *cs = malloc((n + 1) * sizeof *cs);
		size_t nx = 0, nc = 0;
		for (size_t k = 0; uses && k < n; k++)
			for (int *p = s.spans[k]; p && *p >= 0; p++) if ((size_t)*p < s.nstrokes) uses[*p]++;
		for (size_t k = 0; uses && xs && cs && k < n; k++) {
			int *p = s.spans[k], own = p && *p >= 0;
			for (; own && *p >= 0; p++) own = (size_t)*p < s.nstrokes && uses[*p] == 1;
			if (!own || !(is_small(cp[k]) || (is_tall(cp[k]) && cp[k] != 't' && cp[k] != 'f' && cp[k] != 'J' && cp[k] != 'Q')))
				continue;
			double b[4];
			bbox_of(s.strokes, s.spans[k], 0, b);
			if (!(b[3] > b[1])) continue;
			if (is_small(cp[k])) xs[nx++] = b[3] - b[1];
			else cs[nc++] = b[3] - b[1];
		}
		if (nx || nc) { m->kind = 1; m->v[0] = median(xs, nx); m->v[1] = median(cs, nc); }
		free(uses); free(xs); free(cs);
	}
	hw_sample_free(&s);
}

static int same_lang(const char *a, const char *b)
{
	size_t la = strcspn(a, "-_"), lb = strcspn(b, "-_");
	return la == lb && !strncmp(a, b, la);
}

/* the store's `size` file: measurements of samples by id, so a sample is measured once
 * (derived like the index: made again from the samples when it is missing) */
#define SIZE_HEAD "glassine-handwriting-size 1"

static struct meas *size_load(size_t *n)
{
	char p[4096];
	*n = 0;
	if (hw_store_path(p, sizeof p, "size")) return NULL;
	FILE *f = fopen(p, "re");
	if (!f) return NULL;
	struct meas *m = NULL;
	size_t cap = 0;
	char line[256];
	if (!fgets(line, sizeof line, f) || strncmp(line, SIZE_HEAD "\n", sizeof SIZE_HEAD)) { fclose(f); return NULL; }
	while (fgets(line, sizeof line, f)) {
		struct meas x = { 0 };
		if (sscanf(line, "%16s %d %lf %lf %lf %lf %d", x.id, &x.kind, &x.v[0], &x.v[1], &x.per_mm, &x.line, &x.em) != 7 ||
		    strlen(x.id) != 16 || x.kind < 0 || x.kind > 3)
			continue;
		if (*n == cap) {
			cap = cap ? cap * 2 : 64;
			struct meas *t = realloc(m, cap * sizeof *m);
			if (!t) break;
			m = t;
		}
		m[(*n)++] = x;
	}
	fclose(f);
	return m;
}

/* writes the measurements of the samples in use (none of a deleted sample stays); skipped
 * while another process holds the store's lock (it is only a cache) */
static void size_save(const struct meas *m, size_t n)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "lock")) return;
	int fd = open(p, O_RDWR | O_CLOEXEC);
	if (fd < 0) return;
	if (flock(fd, LOCK_EX | LOCK_NB)) { close(fd); return; }
	size_t cap = 64 + n * 128, len = 0;
	char *b = malloc(cap);
	if (b) {
		len = (size_t)snprintf(b, cap, "%s\n", SIZE_HEAD);
		for (size_t i = 0; i < n; i++)
			len += (size_t)snprintf(b + len, cap - len, "%s %d %.9g %.9g %.9g %.9g %d\n", m[i].id, m[i].kind, m[i].v[0],
						m[i].v[1], m[i].per_mm, m[i].line, m[i].em);
		hw_store_replace("size", b, len);
	}
	free(b);
	close(fd);
}

int hw_size_drop(const char *id)
{
	size_t n;
	struct meas *m = size_load(&n);
	if (!m) return 0;
	size_t k = 0;
	for (size_t i = 0; i < n; i++) if (strcmp(m[i].id, id)) m[k++] = m[i];
	int r = 0;
	if (k != n) {
		size_t cap = 64 + k * 128, len = 0;
		char *b = malloc(cap);
		if (!b) r = -1;
		else {
			len = (size_t)snprintf(b, cap, "%s\n", SIZE_HEAD);
			for (size_t i = 0; i < k; i++)
				len += (size_t)snprintf(b + len, cap - len, "%s %d %.9g %.9g %.9g %.9g %d\n", m[i].id, m[i].kind,
							m[i].v[0], m[i].v[1], m[i].per_mm, m[i].line, m[i].em);
			r = hw_store_replace("size", b, len);
		}
		free(b);
	}
	free(m);
	/* this process measures again from what is left */
	for (size_t i = 0; i < P.n; i++) if (!strcmp(P.m[i].id, id)) { P.have = 0; break; }
	return r;
}

/* the person's letters for lang, again only when samples.jsonl changed; a sample measured
 * once is not read again */
static void person(const char *lang)
{
	char p[4096];
	struct stat st;
	if (hw_store_path(p, sizeof p, "samples.jsonl") || stat(p, &st)) {
		P.have = 0;
		P.nwords = P.nlone = 0;
		memset(P.mm, 0, sizeof P.mm); memset(P.ln, 0, sizeof P.ln); memset(P.em, 0, sizeof P.em);
		return;
	}
	if (P.have && !strcmp(P.lang, lang) && st.st_ino == P.st.st_ino && st.st_size == P.st.st_size &&
	    st.st_mtim.tv_sec == P.st.st_mtim.tv_sec && st.st_mtim.tv_nsec == P.st.st_mtim.tv_nsec &&
	    P.gen == hw_store_generation())
		return;
	const struct hw_index_entry *ix;
	size_t nix;
	struct meas *want = calloc(PERSON_WORDS + PERSON_LONE + 1, sizeof *want);
	size_t nw = 0, nl = 0, nwant = 0;
	if (!want || hw_samples_index(&ix, &nix)) { free(want); return; }
	/* the newest first; the index is borrowed until the next store call, so the ids first */
	for (size_t i = nix; i-- > 0 && (nw < PERSON_WORDS || nl < PERSON_LONE);) {
		if (!same_lang(ix[i].meta.lang, lang)) continue;
		unsigned cp[8];
		size_t n = hw_utf8_decode(ix[i].text, cp, 8);
		int lone = n == 1 && (is_small(cp[0]) || is_tall(cp[0]));
		int word = n >= 3;
		if ((lone && nl < PERSON_LONE) || (word && nw < PERSON_WORDS)) {
			memcpy(want[nwant].id, ix[i].meta.id, 17);
			want[nwant++].kind = lone ? 2 : 1;
			nl += lone; nw += word;
		}
	}
	size_t nfile = 0;
	struct meas *file = P.m ? NULL : size_load(&nfile);
	/* what the file holds now: as loaded, or as this process last wrote or found it */
	const struct meas *had = file ? file : P.m;
	size_t nhad = file ? nfile : P.n;
	int measured = 0;
	for (size_t i = 0; i < nwant; i++) {
		const struct meas *old = NULL;
		for (size_t k = 0; k < P.n && !old; k++) if (!strcmp(P.m[k].id, want[i].id)) old = &P.m[k];
		for (size_t k = 0; k < nfile && !old; k++) if (!strcmp(file[k].id, want[i].id)) old = &file[k];
		if (old) { want[i] = *old; continue; }
		int err;
		char *line = hw_samples_line(want[i].id, NULL, &err);
		want[i].kind = 0;
		if (line) measure(line, &want[i]);
		free(line);
		measured = 1;
	}
	int differs = measured || nhad != nwant;
	for (size_t i = 0; !differs && i < nwant; i++) differs = strcmp(had[i].id, want[i].id) != 0;
	if (differs) size_save(want, nwant);
	free(file);
	free(P.m);
	P.m = want;
	P.n = nwant;
	P.st = st;
	P.gen = hw_store_generation();
	snprintf(P.lang, sizeof P.lang, "%s", lang);
	P.have = 1;
	/* per unit: in mm and in line heights; each needs enough samples of its own */
	double *v = malloc((nwant + 1) * sizeof *v);
	for (int u = 0; u < 3; u++) {
		double *out = u == 2 ? P.em : u ? P.ln : P.mm;
		for (int q = 0; q < 4; q++) {
			size_t k = 0;
			for (size_t i = 0; v && i < nwant; i++) {
				const struct meas *m = &want[i];
				double scale = u == 2 ? (m->em && !m->line ? 1 : 0) : u ? m->line : m->per_mm;
				if (scale <= 0) continue;
				if (q < 2 && m->kind == 1 && m->v[q] > 0) v[k++] = m->v[q] / scale;
				if (q == 2 && m->kind == 2) v[k++] = m->v[0] / scale;
				if (q == 3 && m->kind == 3) v[k++] = m->v[0] / scale;
			}
			out[q] = k >= (q < 2 ? 5u : 3u) ? median(v, k) : 0;
		}
	}
	P.nwords = P.nlone = 0;
	for (size_t i = 0; i < nwant; i++) { P.nwords += want[i].kind == 1; P.nlone += want[i].kind >= 2; }
	free(v);
}

static void one_guide(struct hw_size_ref *out);

/* from x (and what else is known) to a whole reference */
static void complete(struct hw_size_ref *r)
{
	if (!(r->c > 0)) r->c = 2.5 * r->x;
	if (!(r->xs > 0)) r->xs = 1.4 * r->x;
	if (!(r->cs > 0)) r->cs = 1.4 * r->c;
}

void hw_size_ref_for(const char *lang, const struct hw_units *u, const struct hw_area *a, double base,
		     struct hw_size_ref *out)
{
	memset(out, 0, sizeof *out);
	int em = !strcmp(u->xy, "em");
	double line = a && a->set && a->line > 0 ? a->line : em ? 1 : 0;
	out->line = line;
	out->area_line = a && a->set && a->line > 0;
	out->has_base = line > 0 && isfinite(base);
	out->base = out->has_base ? base : 0;
	out->given_base = isfinite(base) ? base : NAN;
	const char *e = getenv("GLASSINE_HANDWRITING_SIZE");
	if (e && *e) {
		/* "noweigh" or "weigh", alone or after the letters */
		const char *w = strrchr(e, ',');
		w = w ? w + 1 : e;
		out->weigh = !strcmp(w, "noweigh") ? -1 : !strcmp(w, "weigh") ? 1 : 0;
		double v[4] = { 0 };
		int k = sscanf(e, "%lf,%lf,%lf,%lf", &v[0], &v[1], &v[2], &v[3]);
		if (k >= 2 && v[0] > 0 && v[1] > 0) {
			out->kind = HW_SIZE_PERSON;
			out->x = v[0]; out->c = v[1];
			out->xs = k == 4 ? v[2] : 0; out->cs = k == 4 ? v[3] : 0;
			complete(out);
			one_guide(out);
			return;
		}
		if (!strcmp(e, "off")) return;
	}
	/* the person's letters are looked up only for ink that needs them (resolve) */
	out->pending = 1;
	snprintf(out->lang, sizeof out->lang, "%s", lang);
	out->per_mm = !em && u->per_mm > 0 ? u->per_mm : 0;
}

/* an area without a line but with its guide line (BASE): that one guide, the letters on it
 * measured against the reference (the next ones a line of 1 / HW_LINE_XH small letters apart) */
static void one_guide(struct hw_size_ref *out)
{
	if (out->kind != HW_SIZE_NONE && !(out->line > 0) && isfinite(out->given_base) && out->x > 0) {
		out->line = out->x / HW_LINE_XH;
		out->has_base = 1;
		out->base = out->given_base;
	}
}

static void resolve(struct hw_size_ref *out)
{
	if (!out->pending) return;
	out->pending = 0;
	person(out->lang);
	double s = 0, line = out->line;
	const double *src = NULL;
	/* the same kind of units only: millimetres, the area's line, or em without an area (an
	 * em is the client's own scale, not a line of the samples' areas) */
	if (out->per_mm > 0 && P.mm[0] > 0) { src = P.mm; s = out->per_mm; }
	else if (out->area_line && P.ln[0] > 0) { src = P.ln; s = line; }
	else if (!out->area_line && line > 0 && P.em[0] > 0) { src = P.em; s = 1; }
	if (src) {
		out->kind = HW_SIZE_PERSON;
		out->x = src[0] * s; out->c = src[1] * s; out->xs = src[2] * s; out->cs = src[3] * s;
		complete(out);
	} else if (line > 0) {
		/* the line alone: only the marks are told apart (a letter's size on a line varies
		 * too much from hand to hand) */
		out->kind = HW_SIZE_LINE;
		out->x = HW_LINE_XH * line;
		out->c = 0.5 * line;
		complete(out);
	} else if (out->prior > 0) {
		/* the typical size (prior) as the line alone is: small letters HW_LINE_XH of a line */
		out->kind = HW_SIZE_LINE;
		out->x = out->prior;
		out->c = 0.5 * out->prior / HW_LINE_XH;
		complete(out);
	}
	one_guide(out);
}

void hw_size_ref_prior(const char *lang, const struct hw_units *u, const struct hw_area *a, double base, double prior,
		       struct hw_size_ref *out)
{
	hw_size_ref_for(lang, u, a, base, out);
	out->prior = prior > 0 && isfinite(prior) ? prior : 0;
}

/* ---------------------------------------------------------------- glyphs */

int hw_glyph_kind(double h, double w, const struct hw_size_ref *ref)
{
	double X = ref->x;
	if (fmax(h, w) <= 0.5 * X) return HW_GLYPH_DOT;
	if (h <= 0.5 * X && w >= 2.5 * h) return HW_GLYPH_FLAT;
	/* a tick well below a capital and not much taller than an x; on the line alone, a
	 * short l would pass, so only with the person's letters */
	if (ref->kind == HW_SIZE_PERSON && w <= 0.6 * X && h <= 0.6 * ref->c && h <= 1.8 * X) return HW_GLYPH_THIN;
	return HW_GLYPH_BIG;
}

struct glyph {
	double b[4];
	int kind, last;                 /* last: has the last stroke written */
};

/* strokes overlapping horizontally (within tol), also through others, are one glyph: gid
 * per stroke (-1 for an empty one), glyphs in order of their left edge */
static int group(const struct hw_stroke *s, size_t ns, double tol, int *gid, struct glyph *g)
{
	double (*sb)[4] = malloc((ns + 1) * sizeof *sb);
	size_t *ord = malloc((ns + 1) * sizeof *ord), no = 0, lastw = 0;
	if (!sb || !ord) { free(sb); free(ord); return -1; }
	for (size_t i = 0; i < ns; i++) {
		gid[i] = -1;
		if (!s[i].n) continue;
		sb[i][0] = sb[i][1] = INFINITY; sb[i][2] = sb[i][3] = -INFINITY;
		for (size_t k = 0; k < s[i].n; k++) {
			sb[i][0] = fmin(sb[i][0], s[i].x[k]); sb[i][1] = fmin(sb[i][1], s[i].y[k]);
			sb[i][2] = fmax(sb[i][2], s[i].x[k]); sb[i][3] = fmax(sb[i][3], s[i].y[k]);
		}
		/* by left edge, then by order written (insertion: the same order every time) */
		size_t at = no++;
		while (at > 0 && sb[ord[at - 1]][0] > sb[i][0]) { ord[at] = ord[at - 1]; at--; }
		ord[at] = i;
		lastw = i;
	}
	int ng = 0;
	for (size_t q = 0; q < no; q++) {
		size_t i = ord[q];
		if (ng && sb[i][0] - tol <= g[ng - 1].b[2] + tol) {
			struct glyph *x = &g[ng - 1];
			x->b[1] = fmin(x->b[1], sb[i][1]); x->b[2] = fmax(x->b[2], sb[i][2]); x->b[3] = fmax(x->b[3], sb[i][3]);
		} else {
			memcpy(g[ng].b, sb[i], sizeof g[ng].b);
			g[ng].last = 0;
			ng++;
		}
		gid[i] = ng - 1;
		if (i == lastw) g[ng - 1].last = 1;
	}
	free(sb); free(ord);
	return ng;
}

/* ---------------------------------------------------------------- weights */

/* the weight (natural log) of character cp for a glyph: marks by their shape and place,
 * a lone letter by its height; pos: the glyph's top and bottom below the baseline, in x
 * heights (has: known) */
static double weight(unsigned cp, const struct glyph *g, const struct hw_size_ref *ref, int lone, int has,
		     double top, double bot)
{
	double h = g->b[3] - g->b[1], w = g->b[2] - g->b[0];
	if (g->kind != HW_GLYPH_BIG) {
		if (!is_mark(cp)) return -30;   /* no letter or digit for a mark */
		if (g->kind == HW_GLYPH_DOT) {
			if (has && bot > 0.35) return cp == ',' || cp == 0x201A ? 0 : in(cp, DOTS) ? -1.5 : -6;
			if (has && bot < -0.8) return cp == 0xB7 ? 0 : cp == '.' ? -2 : cp == '\'' || cp == 0x2019 ? -1 : -6;
			return cp == '.' ? 0 : cp == 0xB7 ? -1 : cp == ',' ? -1.5 : -6;
		}
		if (g->kind == HW_GLYPH_FLAT) {
			if (has && fabs(bot) < 0.4) return cp == '_' ? 0 : in(cp, FLATS) ? -2 : -6;
			return cp == '-' ? 0 : cp == 0x2010 || cp == 0x2013 || cp == 0x2212 ? -1 :
			       cp == 0x2014 ? (w > 2.5 * ref->x ? 0 : -1) : cp == '_' ? -2 : cp == '.' ? -4 : -6;
		}
		/* a tick */
		if (has && bot > -0.3) return cp == ',' ? 0 : cp == 0x201A || cp == 0x201E ? -1 : in(cp, THINS) ? -5 : -6;
		if (has && bot < -0.8)
			return cp == '\'' || cp == 0x2019 ? 0 : cp == '`' || cp == 0x2018 || cp == '"' ? -1 :
			       in(cp, THINS) ? -5 : -6;
		return cp == ',' || cp == '\'' || cp == 0x2019 ? 0 : in(cp, THINS) ? -1 : cp == '.' ? -3 : -6;
	}
	(void)top; (void)bot; (void)has; (void)h; (void)w;
	/* a lone letter is no mark (with the person's letters: on the line alone a tick may
	 * count as letter-sized) */
	return lone && ref->kind == HW_SIZE_PERSON && is_mark(cp) ? -8 : 0;
}

/* how much a lone glyph's size favours the capital of a case pair (natural log, + capital,
 * - small): between a lone small letter's height and a lone capital's, the farther from the
 * middle, the clearer; and for p/P, y/Y whether it hangs below the line */
static void case_shift(const struct glyph *g, const struct hw_size_ref *ref, int has, double bot, double d[2])
{
	double h = g->b[3] - g->b[1];
	d[0] = d[1] = 0;
	if (ref->kind != HW_SIZE_PERSON || g->kind != HW_GLYPH_BIG || !(h > 0)) return;
	/* near the middle the size says nothing: no shift within a sixth of it */
	double x = 6 * log(h / sqrt(ref->xs * ref->cs));
	x = x > 1 ? x - 1 : x < -1 ? x + 1 : 0;
	d[0] = fmax(-6, fmin(6, x));
	if (has) d[1] = bot > 0.5 ? -4 : fabs(bot) < 0.3 ? 4 : 0;
}

/* the model's case pairs: classes of the small form, the capital, and for o the digit 0
 * (which goes with the capital); kind 0 by size, 1 by the line */
struct pair { int s, S, z, kind; };

static int pairs_of(const struct hw_lang *l, struct pair *out, int max)
{
	int n = 0;
	for (int k = 0; k < 2; k++)
		for (const unsigned *p = k ? HANGING : PAIRS; *p && n < max; p++) {
			int a = hw_class_of(&l->model, *p), b = hw_class_of(&l->model, *p - 32);
			if (a && b) out[n++] = (struct pair){ a, b, *p == 'o' ? hw_class_of(&l->model, '0') : 0, k };
		}
	return n;
}

static double lse(double a, double b) { return a > b ? a + log1p(exp(b - a)) : b + log1p(exp(a - b)); }

/* the frames of glyph k: weights within the chance of a character, the blank as it was;
 * then within each case pair the size moves the pair's chance between its forms */
static void weigh_frames(struct hw_run *r, float *frames, const int *gid, int k, const double *wt, const struct pair *pr,
			 int npr, const double d[2])
{
	for (long f = 0; f < r->T; f++) {
		long row = f * r->down;
		if (row > r->rows - 1) row = r->rows - 1;
		if (gid[r->org[row].stroke] != k) continue;
		float *lp = frames + (size_t)f * r->C;
		double m = -INFINITY, q = -INFINITY;
		for (int c = 1; c < r->C; c++) {
			double a = lp[c], b = lp[c] + wt[c];
			m = a > m ? a + log1p(exp(m - a)) : m + log1p(exp(a - m));
			q = b > q ? b + log1p(exp(q - b)) : q + log1p(exp(b - q));
		}
		if (!isfinite(m) || !isfinite(q)) continue;
		for (int c = 1; c < r->C; c++) lp[c] = (float)(lp[c] + wt[c] - q + m);
		for (int i = 0; i < npr; i++) {
			double x = d[pr[i].kind];
			if (x == 0) continue;
			double a = lp[pr[i].s], b = lp[pr[i].S], z = pr[i].z ? lp[pr[i].z] : -INFINITY;
			double tot = lse(lse(a, b), z);
			double a2 = a - x / 2, b2 = b + x / 2, z2 = z + x / 2;
			double t2 = lse(lse(a2, b2), z2);
			if (!isfinite(tot) || !isfinite(t2)) continue;
			lp[pr[i].s] = (float)(a2 - t2 + tot);
			lp[pr[i].S] = (float)(b2 - t2 + tot);
			if (pr[i].z) lp[pr[i].z] = (float)(z2 - t2 + tot);
		}
	}
}

/* the ink's frames and the calibrated ones readings are made on (calib.h) alike */
static void weigh(struct hw_run *r, const int *gid, int k, const double *wt, const struct pair *pr, int npr,
		  const double d[2])
{
	weigh_frames(r, r->logp, gid, k, wt, pr, npr, d);
	if (r->dec) weigh_frames(r, r->dec, gid, k, wt, pr, npr, d);
}

/* characters of the run's best path (the most likely class per frame, repeats and blanks
 * taken out), counted up to 3 */
static int greedy_len(const struct hw_run *r)
{
	int n = 0, prev = 0;
	for (long f = 0; f < r->T && n < 3; f++) {
		const float *lp = r->logp + (size_t)f * r->C;
		int best = 0;
		for (int c = 1; c < r->C; c++) if (lp[c] > lp[best]) best = c;
		if (best && best != prev && r->C > 1 && r->C - 1 >= best) n += 1;
		prev = best;
	}
	return n;
}

/* ink that the size may change: read as one or two characters, or ending in a small stroke
 * (a mark after a word) */
static int may_need(const struct hw_run *r, const struct hw_stroke *s, size_t ns)
{
	if (ns <= 4 || greedy_len(r) <= 2) return 1;
	double y0 = INFINITY, y1 = -INFINITY, b[4] = { INFINITY, INFINITY, -INFINITY, -INFINITY };
	size_t last = ns;
	for (size_t i = 0; i < ns; i++)
		for (size_t k = 0; k < s[i].n; k++) { y0 = fmin(y0, s[i].y[k]); y1 = fmax(y1, s[i].y[k]); last = i; }
	if (last == ns || last == 0) return 0;
	for (size_t k = 0; k < s[last].n; k++) {
		b[0] = fmin(b[0], s[last].x[k]); b[1] = fmin(b[1], s[last].y[k]);
		b[2] = fmax(b[2], s[last].x[k]); b[3] = fmax(b[3], s[last].y[k]);
	}
	return fmax(b[2] - b[0], b[3] - b[1]) <= 0.35 * (y1 - y0);
}

/* characters of the run's best reading, counted up to 3 */
static int best_len(const struct hw_run *r)
{
	struct hw_cand c;
	return hw_ctc_beam(r->logp, r->T, r->C, 8, &c, 1) == 1 ? (c.n < 3 ? c.n : 3) : 0;
}

/* With the line alone (docs/recognizer.md, Size): the line's letter height is a guess, and
 * a writer who writes far smaller than it would be read with too few points per letter. A
 * line of writing (at least INK_TURNS turns at each side, INK_WIDE of its own letters wide)
 * whose letters come out below INK_SMALL of the line's is read at INK_SCALE times their
 * height instead, at most INK_MAXF times below the line's; a lone character or mark keeps
 * the line's. Its letters: every stroke resampled evenly by arc length (span / 400), the
 * median height of its lowest turns (a letter's foot) less the median of its highest (its
 * top), over INK_TURN_X (what that is of the small letters as the store measures them). */
#define INK_TURNS 5
#define INK_TURN_X 0.84
#define INK_WIDE 4.0
#define INK_SMALL 0.35
#define INK_SCALE 2.0
#define INK_MIN 0.08
#define INK_MAXF 5.0

/* the ink's small-letter height by its turns, 0 when it has too few; *width its width */
static double ink_letter_height(const struct hw_stroke *s, size_t ns, double *width)
{
	double xmin = INFINITY, xmax = -INFINITY, ymin = INFINITY, ymax = -INFINITY;
	for (size_t i = 0; i < ns; i++)
		for (size_t k = 0; k < s[i].n; k++) {
			xmin = fmin(xmin, s[i].x[k]); xmax = fmax(xmax, s[i].x[k]);
			ymin = fmin(ymin, s[i].y[k]); ymax = fmax(ymax, s[i].y[k]);
		}
	*width = xmax - xmin;
	double span = fmax(fmax(xmax - xmin, ymax - ymin), 1e-6), step = span / 400;
	size_t cap = 64, nt = 0, nb = 0, ycap = 0;
	double *top = malloc(cap * sizeof *top), *bot = malloc(cap * sizeof *bot), *y = NULL;
	double r = 0;
	if (!top || !bot) goto out;
	for (size_t i = 0; i < ns; i++) {
		const struct hw_stroke *q = &s[i];
		if (q->n < 2) continue;
		double L = 0;
		for (size_t k = 1; k < q->n; k++) L += hypot(q->x[k] - q->x[k - 1], q->y[k] - q->y[k - 1]);
		if (L < step) continue;
		size_t m = (size_t)ceil(L / step) + 1;
		if (m > ycap) {
			double *t = realloc(y, m * sizeof *y);
			if (!t) goto out;
			y = t; ycap = m;
		}
		/* y at even arc lengths, linear between the pen's points */
		double du = L / (double)(m - 1), d0 = 0, d1 = hypot(q->x[1] - q->x[0], q->y[1] - q->y[0]);
		size_t k = 1;
		for (size_t j = 0; j < m; j++) {
			double u = j == m - 1 ? L : (double)j * du;
			while (d1 < u && k < q->n - 1) { d0 = d1; k++; d1 = d0 + hypot(q->x[k] - q->x[k - 1], q->y[k] - q->y[k - 1]); }
			double f = d1 > d0 ? (u - d0) / (d1 - d0) : 0;
			y[j] = q->y[k - 1] + (f < 1 ? f : 1) * (q->y[k] - q->y[k - 1]);
		}
		for (size_t j = 1; j + 1 < m; j++) {
			int hi = y[j] < y[j - 1] && y[j] <= y[j + 1], lo = y[j] > y[j - 1] && y[j] >= y[j + 1];
			if (!hi && !lo) continue;
			if (nt == cap || nb == cap) {
				double *t1 = realloc(top, 2 * cap * sizeof *top);
				if (!t1) goto out;
				top = t1;
				double *t2 = realloc(bot, 2 * cap * sizeof *bot);
				if (!t2) goto out;
				bot = t2;
				cap *= 2;
			}
			if (hi) top[nt++] = y[j];
			else bot[nb++] = y[j];
		}
	}
	if (nt >= INK_TURNS && nb >= INK_TURNS) r = (median(bot, nb) - median(top, nt)) / INK_TURN_X;
out:
	free(top); free(bot); free(y);
	return r > 0 ? r : 0;
}

/* the reference for the line alone (above): h, or one from the ink's own letters */
static double line_reference(double h, const struct hw_stroke *s, size_t ns)
{
	double w, x = ink_letter_height(s, ns, &w);
	if (!(x > 0) || w < INK_WIDE * x || x < INK_MIN * h || x >= INK_SMALL * h) return h;
	return fmax(INK_SCALE * x, h / INK_MAXF);
}

/* The ink's small-letter height for the size prior (docs/recognizer.md, Size), robust to a
 * pen's jitter: a turn counts only once the pen went back MEASURE_BACK of the ink's height
 * (raw points; the turns of the height measure above count every wobble, and a jittery pen
 * shrinks it to a fraction of the letters), at least INK_TURNS turns at each side, the
 * median foot less the median top over MEASURE_X. MEASURE_BACK and MEASURE_X from the
 * person's words (a train half): 0.05 of the height measures 7 in 10 of them, its median
 * 0.85 of their small letters as the store measures them. 0 when it can't be measured;
 * *height the ink's height. */
#define MEASURE_BACK 0.05
#define MEASURE_X 0.85

static int push(double **v, size_t *n, size_t *cap, double y)
{
	if (*n == *cap) {
		size_t c = *cap ? 2 * *cap : 64;
		double *t = realloc(*v, c * sizeof *t);
		if (!t) return -1;
		*v = t; *cap = c;
	}
	(*v)[(*n)++] = y;
	return 0;
}

static double measured_letters(const struct hw_stroke *s, size_t ns, double *width, double *height)
{
	double xmin = INFINITY, xmax = -INFINITY, ymin = INFINITY, ymax = -INFINITY;
	for (size_t i = 0; i < ns; i++)
		for (size_t k = 0; k < s[i].n; k++) {
			xmin = fmin(xmin, s[i].x[k]); xmax = fmax(xmax, s[i].x[k]);
			ymin = fmin(ymin, s[i].y[k]); ymax = fmax(ymax, s[i].y[k]);
		}
	*width = xmax > xmin ? xmax - xmin : 0;
	*height = ymax > ymin ? ymax - ymin : 0;
	double th = MEASURE_BACK * *height, *top = NULL, *bot = NULL, r = 0;
	size_t nt = 0, nb = 0, ct = 0, cb = 0;
	if (!(th > 0)) return 0;
	for (size_t i = 0; i < ns; i++) {
		const struct hw_stroke *q = &s[i];
		if (q->n < 3) continue;
		/* dir: 1 going down (y grows), -1 up, 0 not yet; ext the extreme since the last turn */
		double ext = q->y[0];
		int dir = 0;
		for (size_t k = 1; k < q->n; k++) {
			double y = q->y[k];
			if (dir >= 0 && y > ext) { ext = y; if (!dir && y - q->y[0] > th) dir = 1; continue; }
			if (dir <= 0 && y < ext) { ext = y; if (!dir && q->y[0] - y > th) dir = -1; continue; }
			if (dir == 1 && ext - y > th) { if (push(&bot, &nb, &cb, ext)) goto out; ext = y; dir = -1; }
			else if (dir == -1 && y - ext > th) { if (push(&top, &nt, &ct, ext)) goto out; ext = y; dir = 1; }
		}
	}
	if (nt >= INK_TURNS && nb >= INK_TURNS) r = (median(bot, nb) - median(top, nt)) / MEASURE_X;
out:
	free(top); free(bot);
	return r > 0 ? r : 0;
}

/* the reference for the size prior alone: the prior is a typical size, never the writing's
 * own. A line of writing whose small letters can be measured (measured_letters, at least
 * INK_WIDE of them wide) is read at MEASURED_SCALE times their height, whatever the prior.
 * Ink that can't be measured: the prior settles what its height means (a small letter or a
 * capital) only while the ink is at most LONE_MAX (one or two glyphs) or WORDS_MAX times as
 * high as the prior; taller, it is read without a reference (by its own height, as without
 * any size). All from the person's writing (a train half): a reference helps a lone
 * character up to 1.5 times the reference high, is level with none up to 2 and costs more
 * beyond; for words it helps up to 3, is level up to 4 and costs beyond; a measured line read
 * best at 1.5 times its letters (of 1, 1.25, 1.5 and 2) at every size. */
#define LONE_MAX 2.0
#define WORDS_MAX 4.0
#define MEASURED_SCALE 1.5

static double prior_reference(double h, const struct hw_stroke *s, size_t ns)
{
	double w, H, x = measured_letters(s, ns, &w, &H);
	if (x > 0 && w >= INK_WIDE * x) return MEASURED_SCALE * x;
	int *gid = malloc((ns + 1) * sizeof *gid);
	struct glyph *g = malloc((ns + 1) * sizeof *g);
	int ng = gid && g ? group(s, ns, 0.2 * h, gid, g) : -1;
	free(gid); free(g);
	if (ng >= 1 && H > (ng <= 2 ? LONE_MAX : WORDS_MAX) * h) return 0;
	return h;
}

double hw_size_ref_height(const struct hw_size_ref *ref0, const struct hw_stroke *s, size_t ns, double *base)
{
	*base = NAN;
	if (!ref0) return 0;
	struct hw_size_ref rf = *ref0;
	resolve(&rf);
	if (rf.kind == HW_SIZE_NONE || !(rf.x > 0)) return 0;
	if (rf.has_base && rf.line > 0) {
		size_t n = 0;
		for (size_t i = 0; i < ns; i++) n += s[i].n;
		double *y = n ? malloc(n * sizeof *y) : NULL;
		if (y) {
			size_t k = 0;
			for (size_t i = 0; i < ns; i++) for (size_t j = 0; j < s[i].n; j++) y[k++] = s[i].y[j];
			double med = median(y, n);
			/* each guide owns its line from two thirds above it to a third below (descenders,
			 * a comma): ink just under a guide is still that guide's */
			double g = ceil((med - rf.base) / rf.line - 1.0 / 3);
			*base = rf.base + (g > 0 ? g : 0) * rf.line;
			free(y);
		}
	}
	if (rf.kind == HW_SIZE_LINE && s && ns && rf.prior > 0 && !rf.area_line) return prior_reference(rf.x, s, ns);
	if (rf.kind == HW_SIZE_LINE && s && ns) return line_reference(rf.x, s, ns);
	return rf.x;
}

int hw_run_sized(struct hw_lang *l, const struct hw_stroke *s, size_t ns, const struct hw_size_ref *ref,
		 struct hw_run *r)
{
	double base = NAN, h = l->model.version == HW_GHM_VERSION_REF ? hw_size_ref_height(ref, s, ns, &base) : 0;
	int ret = hw_run_at(l, s, ns, h, base, r);
	/* a model that reads the size (version 2) is not weighed again: measured on synthetic
	 * lone characters, weighing it loses more than it gains */
	int weigh = ref && ref->weigh ? ref->weigh > 0 : l->model.version != HW_GHM_VERSION_REF;
	if (!ret && r->rows > 0 && weigh) hw_size_weigh(l, r, s, ns, ref);
	return ret;
}

void hw_size_weigh(const struct hw_lang *l, struct hw_run *r, const struct hw_stroke *s, size_t ns,
		   const struct hw_size_ref *ref0)
{
	if (r->rows <= 0 || r->T <= 0 || !ref0 || ref0->weigh < 0 || (!ref0->pending && ref0->kind == HW_SIZE_NONE) ||
	    !may_need(r, s, ns))
		return;
	struct hw_size_ref rf = *ref0, *ref = &rf;
	resolve(ref);
	if (ref->kind == HW_SIZE_NONE || !(ref->x > 0)) return;
	int *gid = malloc((ns + 1) * sizeof *gid);
	struct glyph *g = malloc((ns + 1) * sizeof *g);
	double *wt = malloc((size_t)r->C * sizeof *wt);
	int ng = gid && g && wt ? group(s, ns, 0.2 * ref->x, gid, g) : -1;
	/* which glyphs: the ink when it is one or two of them; else a mark set apart at the end,
	 * written last */
	struct pair pr[16];
	int npr = pairs_of(l, pr, 16);
	int from = 0, to = -1, lone = 0, gl = 0;
	if (ng > 0) {
		for (int k = 0; k < ng; k++) g[k].kind = hw_glyph_kind(g[k].b[3] - g[k].b[1], g[k].b[2] - g[k].b[0], ref);
		/* lone: one mark, or one or two glyphs the model reads as one or two characters (a
		 * word written in one stroke is one glyph too) */
		gl = ng <= 2 ? best_len(r) : 3;
		lone = (ng == 1 && g[0].kind != HW_GLYPH_BIG) || (ng <= 2 && gl <= 2);
		if (lone) to = ng - 1;
		else if (g[ng - 1].kind != HW_GLYPH_BIG && g[ng - 1].last && g[ng - 1].b[0] - g[ng - 2].b[2] >= 0.3 * ref->x)
			from = to = ng - 1;
	}
	if (to >= from) {
		r->logp0 = malloc((size_t)r->T * r->C * sizeof *r->logp0);
		r->size = calloc(1, sizeof *r->size);
		if (!r->logp0 || !r->size) {
			free(r->logp0); free(r->size); r->logp0 = NULL; r->size = NULL; to = -1;
		} else {
			memcpy(r->logp0, r->logp, (size_t)r->T * r->C * sizeof *r->logp);
			r->size->glyphs = ng;
		}
	}
	for (int k = from; k <= to; k++) {
		double top = 0, bot = 0;
		int has = ref->has_base && ref->line > 0;
		if (has) {
			/* the guide line nearest the glyph's middle is its line */
			double mid = (g[k].b[1] + g[k].b[3]) / 2;
			double yb = ref->base + round((mid - ref->base) / ref->line) * ref->line;
			top = (g[k].b[1] - yb) / ref->x;
			bot = (g[k].b[3] - yb) / ref->x;
		}
		wt[0] = 0;
		for (int c = 1; c < r->C; c++) wt[c] = weight(l->model.charset[c - 1], &g[k], ref, lone, has, top, bot);
		double d[2] = { 0, 0 };
		/* the case by size only where each glyph reads as one character (a short word in one
		 * stroke, read as two, keeps its case) */
		if (lone && gl == ng) case_shift(&g[k], ref, has, bot, d);
		weigh(r, gid, k, wt, pr, npr, d);
		if (ng == 1 && g[0].kind != HW_GLYPH_BIG) {
			r->size->lone_mark = g[0].kind;
			memcpy(r->size->prior, wt, (size_t)r->C * sizeof *wt);
		}
	}
	free(gid); free(g); free(wt);
}

/* ---------------------------------------------------------------- readings */

void hw_size_rerank(const struct hw_lang *l, const struct hw_run *r, int n, struct hw_reading *rd)
{
	if (!r->size || !r->size->lone_mark || n <= 0) return;
	if (n > HW_READ_MAX) n = HW_READ_MAX;
	struct hw_reading out = { 0 };
	/* the marks its shape allows, best first */
	struct { unsigned cp; double s; } m[64];
	int nm = 0;
	for (int c = 1; c < r->C && nm < 64; c++) {
		unsigned cp = l->model.charset[c - 1];
		if (!is_mark(cp) || r->size->prior[c] < -2) continue;
		double s = hw_text_logp(l, r, &cp, 1);
		if (!isfinite(s)) continue;
		int at = nm++;
		while (at > 0 && m[at - 1].s < s) { m[at] = m[at - 1]; at--; }
		m[at].cp = cp; m[at].s = s;
	}
	for (int i = 0; i < nm && out.n < n; i++) {
		out.c[out.n].cp[0] = m[i].cp;
		out.c[out.n].len = 1;
		out.c[out.n].score = m[i].s;
		out.c[out.n].from = out.n ? HW_FROM_BEAM : HW_FROM_INK;
		out.n++;
	}
	/* then the rest as they were */
	for (int i = 0; i < rd->n && out.n < n; i++) {
		int dup = 0;
		for (int k = 0; k < out.n; k++)
			dup |= out.c[k].len == rd->c[i].len && !memcmp(out.c[k].cp, rd->c[i].cp, (size_t)rd->c[i].len * sizeof(unsigned));
		if (dup || !rd->c[i].len) continue;
		out.c[out.n] = rd->c[i];
		if (out.c[out.n].from == HW_FROM_INK) out.c[out.n].from = HW_FROM_BEAM;
		out.n++;
	}
	*rd = out;
}
