// SPDX-License-Identifier: GPL-2.0-or-later
// Recognition: models per language, runs of the engine, candidates and alignment.
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "adapt.h"
#include "recog.h"
#include "store.h"

#define NLANGS 4

static struct hw_lang langs[NLANGS];
static int nlangs;

const char *hw_models_dir(void)
{
	const char *d = getenv("GLASSINE_HANDWRITING_MODELS");
	return d && *d ? d : "/usr/share/glassine/handwriting";
}

static void refresh_adaptation(struct hw_lang *l)
{
	char p[4096];
	if (hw_store_adaptation_path(p, sizeof p, l->lang, "current")) return;
	struct stat st;
	int have = !stat(p, &st);
	if (have && l->has_adapted >= 0 && st.st_ino == l->adapt_st.st_ino && st.st_size == l->adapt_st.st_size &&
	    st.st_mtim.tv_sec == l->adapt_st.st_mtim.tv_sec && st.st_mtim.tv_nsec == l->adapt_st.st_mtim.tv_nsec)
		return;
	if (!have && !l->has_adapted) return;
	if (l->has_adapted > 0) hw_head_free(&l->adapted);
	l->has_adapted = 0;
	memset(&l->adapt_st, 0, sizeof l->adapt_st);
	if (!have) return;
	l->adapt_st = st;
	struct hw_adapt_info info;
	if (!hw_adapt_load(p, &l->model, &l->adapted, &info)) l->has_adapted = 1;
}

struct hw_lang *hw_lang_get(const char *lang)
{
	struct hw_lang *l = NULL;
	for (int i = 0; i < nlangs; i++) if (!strcmp(langs[i].lang, lang)) l = &langs[i];
	if (!l) {
		if (nlangs == NLANGS) {               /* forget the oldest */
			if (langs[0].loaded > 0) hw_model_free(&langs[0].model);
			if (langs[0].has_adapted > 0) hw_head_free(&langs[0].adapted);
			memmove(langs, langs + 1, sizeof langs - sizeof *langs);
			nlangs--;
		}
		l = &langs[nlangs++];
		memset(l, 0, sizeof *l);
		snprintf(l->lang, sizeof l->lang, "%s", lang);
		snprintf(l->dir, sizeof l->dir, "%s/%s", hw_models_dir(), lang);
		char p[4200], err[128];
		snprintf(p, sizeof p, "%s/recognizer.ghm", l->dir);
		l->loaded = -1;
		if (!hw_model_load(&l->model, p, err, sizeof err)) l->loaded = 1;
		else if (errno != ENOENT || strcmp(err, strerror(ENOENT)))
			fprintf(stderr, "glassine-handwriting: %s: %s\n", p, err);
	}
	if (l->loaded < 0) return NULL;
	refresh_adaptation(l);
	return l;
}

const struct hw_head *hw_lang_head(const struct hw_lang *l)
{
	return l->has_adapted > 0 ? &l->adapted : &l->model.base;
}

void hw_lang_forget_all(void)
{
	for (int i = 0; i < nlangs; i++) {
		if (langs[i].loaded > 0) hw_model_free(&langs[i].model);
		if (langs[i].has_adapted > 0) hw_head_free(&langs[i].adapted);
	}
	nlangs = 0;
}

void hw_run_free(struct hw_run *r)
{
	free(r->feats); free(r->logp); free(r->frames); free(r->org);
	memset(r, 0, sizeof *r);
}

int hw_run(struct hw_lang *l, const struct hw_stroke *s, size_t ns, struct hw_run *r)
{
	memset(r, 0, sizeof *r);
	r->nstrokes = ns;
	r->C = (int)l->model.cfg.nclass;
	r->down = 1 << l->model.cfg.nstrided;
	r->rows = hw_features(s, ns, &r->feats, &r->org);
	if (r->rows <= 0) return (int)r->rows;
	r->T = hw_encode(&l->model, r->feats, r->rows, &r->frames);
	if (r->T <= 0) { hw_run_free(r); return -1; }
	r->logp = malloc((size_t)r->T * r->C * sizeof *r->logp);
	if (!r->logp) { hw_run_free(r); return -1; }
	hw_head_forward(hw_lang_head(l), r->frames, r->T, r->logp, NULL, NULL);
	return 0;
}

/* frame range -> feature rows (frame f is centred on row f * down) */
static void rows_of(const struct hw_run *r, long f0, long f1, long *a, long *b)
{
	long h = r->down / 2;
	*a = f0 * r->down - h;
	*b = f1 == r->T - 1 ? r->rows - 1 : (f1 + 1) * r->down - h - 1;
	if (*a < 0) *a = 0;
	if (*b > r->rows - 1) *b = r->rows - 1;
}

long hw_align(const struct hw_lang *l, const struct hw_run *r, const unsigned *cp, size_t n, struct hw_charspan **out)
{
	struct hw_charspan *c = calloc(n + 1, sizeof *c);
	int *lab = malloc((n + 1) * sizeof *lab), *who = malloc((n + 1) * sizeof *who);
	long *first = malloc((n + 1) * sizeof(long) * 4);
	if (!c || !lab || !who || !first) { free(c); free(lab); free(who); free(first); return -2; }
	long *last = first + n + 1, *from = last + n + 1, *to = from + n + 1;
	int L = 0;
	for (size_t k = 0; k < n; k++) {
		c[k] = (struct hw_charspan){ cp[k], -1, -1, -1 };
		int cl = hw_class_of(&l->model, cp[k]);
		if (cl && L < HW_MAX_LABELS) { lab[L] = cl; who[L] = (int)k; L++; }
	}
	long ret = (long)n;
	if (L && r->T > 0) {
		double lp = hw_ctc_align(r->logp, r->T, r->C, lab, L, first, last, from, to);
		if (!isfinite(lp)) ret = -1;
		else
			for (int j = 0; j < L; j++) {
				struct hw_charspan *x = &c[who[j]];
				double best = -INFINITY;
				for (long f = first[j]; f <= last[j]; f++) {
					double v = r->logp[(size_t)f * r->C + lab[j]];
					if (v > best) best = v;
				}
				x->fit = exp(best);
				if (cp[who[j]] != ' ') rows_of(r, from[j], to[j], &x->r0, &x->r1);
			}
		/* what the model read as a space is still ink: it goes to the characters beside it,
		 * so every stroke belongs to a character */
		if (ret >= 0)
			for (int j = 0; j < L; j++) {
				if (cp[who[j]] != ' ') continue;
				long a, b;
				rows_of(r, from[j], to[j], &a, &b);
				struct hw_charspan *p = NULL, *q = NULL;
				for (int i = j - 1; i >= 0 && !p; i--) if (cp[who[i]] != ' ') p = &c[who[i]];
				for (int i = j + 1; i < L && !q; i++) if (cp[who[i]] != ' ') q = &c[who[i]];
				long mid = p && q ? (a + b) / 2 : q ? a - 1 : b;
				if (p && p->r0 >= 0 && mid >= a) p->r1 = mid;
				if (q && q->r0 >= 0 && mid < b) q->r0 = mid + 1;
			}
	} else if (L) {
		ret = -1;
	}
	free(lab); free(who); free(first);
	if (ret < 0) { free(c); return ret; }
	*out = c;
	return ret;
}

static void put(char **buf, size_t *len, size_t *cap, const char *s)
{
	size_t n = strlen(s);
	if (*len + n + 1 > *cap) {
		size_t c = (*len + n + 1) * 2;
		char *b = realloc(*buf, c);
		if (!b) return;
		*buf = b; *cap = c;
	}
	memcpy(*buf + *len, s, n + 1);
	*len += n;
}

/* rows of each stroke in the run */
static void stroke_rows(const struct hw_run *r, int s, long *a, long *b)
{
	*a = -1; *b = -1;
	for (long i = 0; i < r->rows; i++)
		if (r->org[i].stroke == s) { if (*a < 0) *a = i; *b = i; }
}

void hw_span_text(const struct hw_run *r, const struct hw_charspan *c, const int *ids, char **buf, size_t *len, size_t *cap)
{
	if (c->r0 < 0) return;
	int first = 1;
	for (long i = c->r0; i <= c->r1;) {
		int s = r->org[i].stroke;
		long j = i;
		int p0 = r->org[i].point, p1 = p0;
		while (j <= c->r1 && r->org[j].stroke == s) { if (r->org[j].point > p1) p1 = r->org[j].point; j++; }
		long a, b;
		stroke_rows(r, s, &a, &b);
		char t[64];
		int np = (int)r->npoints[s];
		if (i == a) p0 = 0;
		if (j - 1 == b) p1 = np - 1;
		if (i == a && j - 1 == b) snprintf(t, sizeof t, "%s%d", first ? "" : ",", ids ? ids[s] : s);
		else snprintf(t, sizeof t, "%s%d:%d-%d", first ? "" : ",", ids ? ids[s] : s, p0, p1);
		put(buf, len, cap, t);
		first = 0;
		i = j;
	}
}

int *hw_span_strokes(const struct hw_run *r, const struct hw_charspan *c, size_t nchars, size_t k)
{
	int *out = malloc((r->nstrokes + 1) * sizeof *out);
	if (!out) return NULL;
	int n = 0;
	if (c[k].r0 >= 0) {
		long *cov = calloc(r->nstrokes + 1, sizeof *cov), *tot = calloc(r->nstrokes + 1, sizeof *tot);
		if (!cov || !tot) { free(cov); free(tot); free(out); return NULL; }
		for (long i = 0; i < r->rows; i++) tot[r->org[i].stroke]++;
		for (long i = c[k].r0; i <= c[k].r1; i++) cov[r->org[i].stroke]++;
		/* a stroke belongs to the character when the character covers a good part of it,
		 * or it is the character's main stroke */
		long best = 0; int bs = -1;
		for (size_t s = 0; s < r->nstrokes; s++) {
			if (cov[s] > best) { best = cov[s]; bs = (int)s; }
			if (tot[s] && cov[s] * 10 >= tot[s] * 3) out[n++] = (int)s;
		}
		if (!n && bs >= 0) out[n++] = bs;
		free(cov); free(tot);
	}
	out[n] = -1;
	(void)nchars;
	return out;
}

int hw_read(const struct hw_lang *l, const struct hw_run *r, int n, struct hw_reading *out)
{
	memset(out, 0, sizeof *out);
	if (n > 8) n = 8;
	if (r->T <= 0) return 0;
	struct hw_cand cand[8];
	int nc = hw_ctc_beam(r->logp, r->T, r->C, 16, cand, n);
	for (int i = 0; i < nc; i++) {
		out->c[i].len = cand[i].n;
		out->c[i].score = cand[i].score;
		for (int k = 0; k < cand[i].n; k++) out->c[i].cp[k] = l->model.charset[cand[i].lab[k] - 1];
	}
	out->n = nc;
	return nc;
}

size_t hw_edit(const unsigned *a, size_t na, const unsigned *b, size_t nb)
{
	size_t *d = malloc((nb + 1) * sizeof *d);
	if (!d) return na > nb ? na : nb;
	for (size_t j = 0; j <= nb; j++) d[j] = j;
	for (size_t i = 1; i <= na; i++) {
		size_t prev = d[0];
		d[0] = i;
		for (size_t j = 1; j <= nb; j++) {
			size_t t = d[j];
			size_t v = prev + (a[i - 1] != b[j - 1]);
			if (d[j] + 1 < v) v = d[j] + 1;
			if (d[j - 1] + 1 < v) v = d[j - 1] + 1;
			d[j] = v;
			prev = t;
		}
	}
	size_t r = d[nb];
	free(d);
	return r;
}
