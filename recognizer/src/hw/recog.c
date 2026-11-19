// SPDX-License-Identifier: GPL-2.0-or-later
// Recognition: models per language, runs of the engine, candidates and alignment.
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "adapt.h"
#include "calib.h"
#include "chars.h"
#include "recog.h"
#include "size.h"
#include "store.h"
#include "words.h"

#define NLANGS 4

static struct hw_lang langs[NLANGS];
static int nlangs;

const char *hw_models_dir(void)
{
	const char *d = getenv("GLASSINE_HANDWRITING_MODELS");
	if (d && *d) return d;
	/* the setting: another set of base models installed beside the default one */
	static char conf[4000];
	if (hw_conf_get("models", conf, sizeof conf) && conf[0] == '/') return conf;
	return "/usr/share/glassine/handwriting";
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

/* l (cleared) holds lang's model from dir */
static void lang_load(struct hw_lang *l, const char *lang, const char *dir)
{
	memset(l, 0, sizeof *l);
	snprintf(l->lang, sizeof l->lang, "%s", lang);
	snprintf(l->dir, sizeof l->dir, "%s", dir);
	char p[4200], err[128];
	snprintf(p, sizeof p, "%s/recognizer.ghm", l->dir);
	l->loaded = -1;
	if (!hw_model_load(&l->model, p, err, sizeof err)) {
		l->loaded = 1;
		l->base_temp = hw_calib_base(l->dir, l->model.hash);
		l->model.base.temp = (float)l->base_temp;
	} else if (errno != ENOENT || strcmp(err, strerror(ENOENT)))
		fprintf(stderr, "glassine-handwriting: %s: %s\n", p, err);
}

static void lang_free(struct hw_lang *l)
{
	if (l->loaded > 0) hw_model_free(&l->model);
	if (l->has_adapted > 0) hw_head_free(&l->adapted);
}

struct hw_lang *hw_lang_get(const char *lang)
{
	struct hw_lang *l = NULL;
	char dir[sizeof l->dir];
	snprintf(dir, sizeof dir, "%s/%s", hw_models_dir(), lang);
	for (int i = 0; i < nlangs; i++) if (!strcmp(langs[i].lang, lang)) l = &langs[i];
	if (l && strcmp(l->dir, dir)) {               /* the setting moved to other models */
		lang_free(l);
		lang_load(l, lang, dir);
	}
	if (!l) {
		if (nlangs == NLANGS) {               /* forget the oldest */
			lang_free(&langs[0]);
			memmove(langs, langs + 1, sizeof langs - sizeof *langs);
			nlangs--;
		}
		l = &langs[nlangs++];
		lang_load(l, lang, dir);
	}
	if (l->loaded < 0) return NULL;
	refresh_adaptation(l);
	return l;
}

const struct hw_head *hw_lang_head(const struct hw_lang *l)
{
	return l->has_adapted > 0 ? &l->adapted : &l->model.base;
}

double hw_lang_temp(const struct hw_lang *l)
{
	/* for measurements: "off" none, a number that temperature instead */
	const char *e = getenv("GLASSINE_HANDWRITING_CALIB");
	if (e && *e) {
		char *end;
		double v = strtod(e, &end);
		if (!strcmp(e, "off")) return 1;
		if (!*end && v >= HW_CALIB_MIN && v <= HW_CALIB_MAX) return v;
	}
	double t = hw_lang_head(l)->temp;
	return t >= HW_CALIB_MIN && t <= HW_CALIB_MAX ? t : 1;
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
	free(r->feats); free(r->logp); free(r->frames); free(r->org); free(r->logp0); free(r->size); free(r->dec);
	memset(r, 0, sizeof *r);
}

static int run_engine(struct hw_lang *l, const struct hw_stroke *s, size_t ns, double h, double base,
		      struct hw_run *r)
{
	memset(r, 0, sizeof *r);
	r->nstrokes = ns;
	r->C = (int)l->model.cfg.nclass;
	r->down = 1 << l->model.cfg.nstrided;
	r->nfeat = (int)l->model.cfg.nfeat;
	if (l->model.version == HW_GHM_VERSION_REF) {
		double tilt, mid, x0;
		/* no reference: the ink's own core height, as version 1 scales it */
		if (!(h > 0) && hw_line_metrics(s, ns, &tilt, &h, &mid, &x0)) return 0;
		r->rows = hw_features_ref(s, ns, h, base, &r->feats, &r->org);
	} else
		r->rows = hw_features(s, ns, &r->feats, &r->org);
	if (r->rows <= 0) return (int)r->rows;
	r->T = hw_encode(&l->model, r->feats, r->rows, &r->frames);
	if (r->T <= 0) { hw_run_free(r); return -1; }
	r->logp = malloc((size_t)r->T * r->C * sizeof *r->logp);
	if (!r->logp) { hw_run_free(r); return -1; }
	hw_head_forward(hw_lang_head(l), r->frames, r->T, r->logp, NULL, NULL);
	double t = hw_lang_temp(l);
	if (t != 1) {
		r->dec = malloc((size_t)r->T * r->C * sizeof *r->dec);
		if (!r->dec) { hw_run_free(r); return -1; }
		hw_calib_apply(r->logp, r->dec, r->T, r->C, t);
	}
	return 0;
}

/* the last run, for the next request on the same strokes: a client reads, checks and keeps
 * one piece of writing in several requests (a pause's check, read, confirm, donate), each
 * of which would run the engine again */
static int cache_on;
static struct { uint64_t key; struct hw_run r; size_t dim; int have; } last;

void hw_run_cache(int on)
{
	cache_on = on;
	if (!on && last.have) { hw_run_free(&last.r); last.have = 0; }
}

static uint64_t run_key(const struct hw_lang *l, const struct hw_stroke *s, size_t ns, double ref, double base)
{
	uint64_t h = hw_hash(l->lang, strlen(l->lang), 0);
	double rb[2] = { ref, isfinite(base) ? base : 0 };
	h = hw_hash(rb, sizeof rb, h);
	const void *who[2] = { l, hw_lang_head(l) };
	h = hw_hash(who, sizeof who, h);
	double t = hw_lang_temp(l);
	h = hw_hash(&t, sizeof t, h);
	long long st[4] = { (long long)l->adapt_st.st_ino, (long long)l->adapt_st.st_size,
			    (long long)l->adapt_st.st_mtim.tv_sec, (long long)l->adapt_st.st_mtim.tv_nsec };
	h = hw_hash(st, sizeof st, h);
	h = hw_hash(&ns, sizeof ns, h);
	for (size_t i = 0; i < ns; i++) {
		h = hw_hash(&s[i].n, sizeof s[i].n, h);
		h = hw_hash(s[i].x, s[i].n * sizeof *s[i].x, h);
		h = hw_hash(s[i].y, s[i].n * sizeof *s[i].y, h);
		h = hw_hash(s[i].t, s[i].n * sizeof *s[i].t, h);
	}
	return h;
}

static void *dup_of(const void *p, size_t n)
{
	void *q = p && n ? malloc(n) : NULL;
	if (q) memcpy(q, p, n);
	return q;
}

static int run_copy(const struct hw_run *a, struct hw_run *b, size_t dim)
{
	*b = *a;
	b->feats = dup_of(a->feats, (size_t)a->rows * a->nfeat * sizeof *a->feats);
	b->org = dup_of(a->org, (size_t)a->rows * sizeof *a->org);
	b->frames = dup_of(a->frames, (size_t)a->T * dim * sizeof *a->frames);
	b->logp = dup_of(a->logp, (size_t)a->T * a->C * sizeof *a->logp);
	b->dec = dup_of(a->dec, (size_t)a->T * a->C * sizeof *a->dec);
	if ((a->feats && !b->feats) || (a->org && !b->org) || (a->frames && !b->frames) || (a->logp && !b->logp) ||
	    (a->dec && !b->dec)) {
		hw_run_free(b);
		return -1;
	}
	return 0;
}

int hw_run(struct hw_lang *l, const struct hw_stroke *s, size_t ns, struct hw_run *r)
{
	return hw_run_at(l, s, ns, 0, NAN, r);
}

int hw_run_at(struct hw_lang *l, const struct hw_stroke *s, size_t ns, double h, double base, struct hw_run *r)
{
	if (l->model.version != HW_GHM_VERSION_REF) h = 0, base = NAN;     /* the same run either way */
	if (!cache_on) return run_engine(l, s, ns, h, base, r);
	uint64_t key = run_key(l, s, ns, h, base);
	size_t dim = 2 * (size_t)l->model.cfg.hidden;
	if (last.have && last.key == key && last.dim == dim)
		return run_copy(&last.r, r, dim) ? run_engine(l, s, ns, h, base, r) : 0;
	int ret = run_engine(l, s, ns, h, base, r);
	if (last.have) { hw_run_free(&last.r); last.have = 0; }
	if (!ret && r->rows > 0 && !run_copy(r, &last.r, dim)) { last.key = key; last.dim = dim; last.have = 1; }
	return ret;
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
		c[k] = (struct hw_charspan){ cp[k], -1, -1, -1, 0 };
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
				double best = -INFINITY, tb = -INFINITY;
				for (long f = first[j]; f <= last[j]; f++) {
					for (int k = 1; k < r->C; k++)
						if (r->logp[(size_t)f * r->C + k] > tb) { tb = r->logp[(size_t)f * r->C + k]; x->top = k; }
					double v = r->logp[(size_t)f * r->C + lab[j]];
					if (v > best) best = v;
					/* the size only ever helps a fit (FR-017) */
					if (r->logp0 && (v = r->logp0[(size_t)f * r->C + lab[j]]) > best) best = v;
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
	int nc = hw_ctc_beam(hw_run_dec(r), r->T, r->C, 16, cand, n);
	for (int i = 0; i < nc; i++) {
		out->c[i].len = cand[i].n;
		out->c[i].score = cand[i].score;
		for (int k = 0; k < cand[i].n; k++) out->c[i].cp[k] = l->model.charset[cand[i].lab[k] - 1];
	}
	out->n = nc;
	for (int i = 0; i < nc; i++) out->c[i].from = i ? HW_FROM_BEAM : HW_FROM_INK;
	hw_size_rerank(l, r, n, out);
	return out->n;
}

/* ---------------------------------------------------------------- readings with words */

/* the weights (natural log units); $GLASSINE_HANDWRITING_WORDS="lm=0.3,margin=2,..."
 * replaces them (tuning) */
static struct { double lm, word, chr, margin, near; int beam, nbest, minlen, guided; } W = {
	0.3, 0, -4.5, 2.5, 9.0, 16, 10, 4, 1 };
#define POOL 40                 /* readings scored at most: the ink's, the character model's and the
				 * guided search's, near words */

/* the character model's weights (tuned on synthetic dev ink, train/README.md): per character
 * weight × max(log P, floor) + bonus; its own search's width; the margin by which a reading
 * must beat the ink's best to come first, without words and with them (W.margin then is for
 * words alone). $GLASSINE_HANDWRITING_CHARS="weight=0.35,..." replaces them (tuning) */
static struct { double weight, floor, bonus, margin, wmargin; int beam; } CW = { 0.35, -8.0, 1.0, 0.0, 1.0, 8 };

static void weights(void)
{
	static int done;
	if (done) return;
	done = 1;
	const char *e = getenv("GLASSINE_HANDWRITING_WORDS");
	for (const char *p = e; p && *p;) {
		char k[16];
		double v;
		int used;
		if (sscanf(p, "%15[a-z]=%lf%n", k, &v, &used) != 2) break;
		if (!strcmp(k, "lm")) W.lm = v;
		else if (!strcmp(k, "word")) W.word = v;
		else if (!strcmp(k, "chr")) W.chr = v;
		else if (!strcmp(k, "margin")) W.margin = v;
		else if (!strcmp(k, "near")) W.near = v;
		else if (!strcmp(k, "beam")) W.beam = (int)v;
		else if (!strcmp(k, "nbest")) W.nbest = (int)v;
		else if (!strcmp(k, "minlen")) W.minlen = (int)v;
		else if (!strcmp(k, "guided")) W.guided = (int)v;
		p += used;
		if (*p == ',') p++;
	}
	e = getenv("GLASSINE_HANDWRITING_CHARS");
	for (const char *p = e; p && *p;) {
		char k[16];
		double v;
		int used;
		if (sscanf(p, "%15[a-z]=%lf%n", k, &v, &used) != 2) break;
		if (!strcmp(k, "weight")) CW.weight = v;
		else if (!strcmp(k, "floor")) CW.floor = v;
		else if (!strcmp(k, "bonus")) CW.bonus = v;
		else if (!strcmp(k, "margin")) CW.margin = v;
		else if (!strcmp(k, "wmargin")) CW.wmargin = v;
		else if (!strcmp(k, "beam")) CW.beam = (int)v;
		p += used;
		if (*p == ',') p++;
	}
	if (CW.beam < 1) CW.beam = 1;
	if (W.nbest > (POOL - 2) / 3) W.nbest = (POOL - 2) / 3;
	if (W.nbest < 1) W.nbest = 1;
	if (W.beam < W.nbest) W.beam = W.nbest;
}

static size_t utf8_of(const unsigned *cp, int n, char *out, size_t cap)
{
	size_t o = 0;
	for (int i = 0; i < n && o + 5 < cap; i++) {
		unsigned c = cp[i];
		if (c < 0x80) out[o++] = (char)c;
		else if (c < 0x800) { out[o++] = (char)(0xC0 | c >> 6); out[o++] = (char)(0x80 | (c & 0x3F)); }
		else if (c < 0x10000) {
			out[o++] = (char)(0xE0 | c >> 12); out[o++] = (char)(0x80 | ((c >> 6) & 0x3F));
			out[o++] = (char)(0x80 | (c & 0x3F));
		} else {
			out[o++] = (char)(0xF0 | c >> 18); out[o++] = (char)(0x80 | ((c >> 12) & 0x3F));
			out[o++] = (char)(0x80 | ((c >> 6) & 0x3F)); out[o++] = (char)(0x80 | (c & 0x3F));
		}
	}
	out[o] = 0;
	return o;
}

/* a word of code points: its id and whether it is known */
static long word_id(const struct hw_words *w, const unsigned *cp, int n, int *known)
{
	char u[4 * HW_WORD_MAX + 8], f[8 * HW_WORD_MAX + 16];
	*known = 0;
	if (n <= 0 || n > HW_WORD_MAX) return HW_NO_WORD;
	size_t len = utf8_of(cp, n, u, sizeof u);
	if (len > HW_WORD_MAX) return HW_NO_WORD;
	hw_fold(u, len, f);
	return hw_words_find(w, f, known);
}

/* the words of a text: calls fn for each (start, length), and keeps the context */
struct walk { long prev2, prev1; };

static int is_wordch(const unsigned *cp, int n, int i)
{
	if (hw_is_letter(cp[i])) return 1;
	return hw_is_apostrophe(cp[i]) && i > 0 && i + 1 < n && hw_is_letter(cp[i - 1]) && hw_is_letter(cp[i + 1]);
}

/* the word score of a text: log P(word | two before) for each word of the list, and for the
 * rest (unknown words, digits, symbols; not spaces) W.chr per character, as if spelled out;
 * the number of words and of unknown words */
static double text_words(const struct hw_context *x, const unsigned *cp, int n, int *nwords, int *unknown)
{
	long p2 = x->prev2, p1 = x->prev1;
	double lp = 0;
	*nwords = *unknown = 0;
	for (int i = 0; i < n;) {
		if (is_wordch(cp, n, i)) {
			int j = i;
			while (j < n && is_wordch(cp, n, j)) j++;
			int known;
			long id = word_id(x->w, cp + i, j - i, &known);
			lp += known ? fmax(hw_words_logp(x->w, p2, p1, id, known), W.chr * (j - i)) : W.chr * (j - i);
			(*nwords)++;
			*unknown += !known;
			p2 = p1;
			p1 = known ? (id >= 0 ? id : HW_NO_WORD) : HW_NO_WORD;
			i = j;
			continue;
		}
		if (cp[i] != ' ') lp += W.chr;
		if (hw_is_sentence_end(cp[i])) { p2 = HW_NO_WORD; p1 = HW_BOS; }
		else if (!hw_is_transparent(cp[i])) { p2 = p1 = HW_NO_WORD; }
		i++;
	}
	return lp;
}

void hw_context_of(const struct hw_words *w, const char *before, struct hw_context *x)
{
	x->w = w;
	x->prev2 = HW_NO_WORD;
	x->prev1 = HW_BOS;              /* nothing before: a sentence starts */
	x->ch = NULL;
	x->cctx = 0;
	if (!w || !before) return;
	size_t n = strlen(before), from = n > 512 ? n - 512 : 0;
	/* the predictor's tokens over the last 512 bytes */
	const unsigned char *s = (const unsigned char *)before + from;
	size_t len = n - from;
	unsigned cp[512];
	int m = 0;
	for (size_t i = 0; i < len && m < 512;) {
		unsigned c = s[i];
		int l = 1;
		if (c >= 0xC0 && c < 0xE0 && i + 1 < len) { c = (c & 0x1F) << 6 | (s[i + 1] & 0x3F); l = 2; }
		else if (c >= 0xE0 && c < 0xF0 && i + 2 < len) { c = (c & 0x0F) << 12 | (s[i + 1] & 0x3F) << 6 | (s[i + 2] & 0x3F); l = 3; }
		else if (c >= 0xF0 && i + 3 < len) { c = (c & 0x07) << 18 | (s[i + 1] & 0x3F) << 12 | (s[i + 2] & 0x3F) << 6 | (s[i + 3] & 0x3F); l = 4; }
		else if (c >= 0x80) c = 0xFFFD;
		cp[m++] = c;
		i += (size_t)l;
	}
	long p2 = HW_NO_WORD, p1 = m ? HW_NO_WORD : HW_BOS;
	/* the same walk as text_words, keeping only the context at the end */
	for (int i = 0; i < m;) {
		if (is_wordch(cp, m, i)) {
			int j = i;
			while (j < m && is_wordch(cp, m, j)) j++;
			int known;
			long id = word_id(w, cp + i, j - i, &known);
			p2 = p1;
			p1 = known && id >= 0 ? id : HW_NO_WORD;
			i = j;
			continue;
		}
		if (hw_is_sentence_end(cp[i])) { p2 = HW_NO_WORD; p1 = HW_BOS; }
		else if (!hw_is_transparent(cp[i])) { p2 = p1 = HW_NO_WORD; }
		i++;
	}
	x->prev2 = p2;
	x->prev1 = p1;
}

struct hw_context *hw_context_make(const struct hw_words *w, const struct hw_chars *ch, const char *before,
				   struct hw_context *x)
{
	hw_context_of(w, before, x);
	x->ch = ch;
	x->cctx = ch ? hw_chars_start(ch, before) : 0;
	return w || ch ? x : NULL;
}

/* the character model's score of one character after h */
static double chars_step(const struct hw_chars *ch, hw_chars_ctx h, unsigned c)
{
	return CW.weight * fmax(hw_chars_logp(ch, h, c), CW.floor) + CW.bonus;
}

/* ... of a whole text after the text before */
static double chars_text(const struct hw_context *x, const unsigned *cp, int n)
{
	if (!x->ch) return 0;
	hw_chars_ctx h = x->cctx;
	double lp = 0;
	for (int i = 0; i < n; i++) {
		lp += chars_step(x->ch, h, cp[i]);
		h = hw_chars_push(x->ch, h, cp[i]);
	}
	return lp;
}

struct scored {
	unsigned cp[HW_MAX_LABELS];
	int len, from, allwords;
	double fwd, total;
};

double hw_text_logp(const struct hw_lang *l, const struct hw_run *r, const unsigned *cp, int n)
{
	int lab[HW_MAX_LABELS], L = 0;
	for (int k = 0; k < n; k++) {
		int c = hw_class_of(&l->model, cp[k]);
		if (!c) return -INFINITY;
		lab[L++] = c;
	}
	double nll = hw_ctc_loss(hw_run_dec(r), r->T, r->C, lab, L, NULL, 0);
	return isfinite(nll) ? -nll : -INFINITY;
}

static void score(const struct hw_context *x, struct scored *s)
{
	s->total = s->fwd + chars_text(x, s->cp, s->len);
	s->allwords = 0;
	if (!x->w) return;
	int nw, unk;
	double lp = text_words(x, s->cp, s->len, &nw, &unk);
	s->allwords = !unk && nw;
	s->total += W.lm * lp + W.word * nw;
}

static int same(const struct scored *a, const unsigned *cp, int n)
{
	return a->len == n && !memcmp(a->cp, cp, (size_t)n * sizeof *cp);
}

/* the case of the written word on a word of the list: Capital, ALL or as listed */
static void recase(const unsigned *was, int nw, unsigned *cp, int n)
{
	int up = 0, first = nw && hw_fold_cp(was[0]) != was[0];
	for (int k = 0; k < nw; k++) up += hw_fold_cp(was[k]) != was[k];
	for (int k = 0; k < n; k++) {
		unsigned c = cp[k];
		if ((up == nw && nw > 1) || (k == 0 && first)) {
			if (c >= 'a' && c <= 'z') c -= 32;
			else if (c >= 0xE0 && c <= 0xFE && c != 0xF7) c -= 32;
		}
		cp[k] = c;
	}
}

/* words of the list one edit (a letter changed, left out, added, or two swapped) from the
 * unknown word cp[a..b) of a reading; the two most frequent go into out */
static int near_words(const struct hw_lang *l, const struct hw_context *x, const unsigned *cp, int a, int b,
		      unsigned out[2][HW_WORD_MAX + 1], int outlen[2])
{
	unsigned w[HW_WORD_MAX + 2], t[HW_WORD_MAX + 2], abc[128];
	int n = b - a, na = 0, got = 0;
	double best[2] = { -INFINITY, -INFINITY };
	if (n < W.minlen || n >= HW_WORD_MAX) return 0;
	for (int k = 0; k < n; k++) w[k] = hw_fold_cp(cp[a + k]);
	for (int k = 0; k + 1 < (int)l->model.cfg.nclass && na < (int)(sizeof abc / sizeof *abc); k++) {
		unsigned c = l->model.charset[k];
		if (hw_is_letter(c) && hw_fold_cp(c) == c) abc[na++] = c;
	}
	for (int op = 0; op < 4; op++) {
		int pos = op == 2 ? n + 1 : n;
		for (int i = 0; i < pos; i++) {
			int nalt = op == 1 || op == 3 ? 1 : na;
			for (int j = 0; j < nalt; j++) {
				int m = 0;
				if (op == 0) {                  /* changed */
					if (abc[j] == w[i]) continue;
					memcpy(t, w, (size_t)n * sizeof *w); t[i] = abc[j]; m = n;
				} else if (op == 1) {           /* left out */
					for (int k = 0; k < n; k++) if (k != i) t[m++] = w[k];
				} else if (op == 2) {           /* added */
					for (int k = 0; k < i; k++) t[m++] = w[k];
					t[m++] = abc[j];
					for (int k = i; k < n; k++) t[m++] = w[k];
				} else {                        /* swapped */
					if (i + 1 >= n || w[i] == w[i + 1]) continue;
					memcpy(t, w, (size_t)n * sizeof *w); t[i] = w[i + 1]; t[i + 1] = w[i]; m = n;
				}
				int known;
				long id = word_id(x->w, t, m, &known);
				if (id < 0 || !known) continue;      /* not removed by the person */
				double u = hw_words_unigram(x->w, id);
				int dup = 0;
				for (int g = 0; g < got; g++) dup |= outlen[g] == m && !memcmp(out[g], t, (size_t)m * sizeof *t);
				if (dup) continue;
				int slot = got < 2 ? got++ : (best[1] < u ? 1 : -1);
				if (slot < 0) continue;
				memcpy(out[slot], t, (size_t)m * sizeof *t);
				outlen[slot] = m;
				best[slot] = u;
				if (got == 2 && best[1] > best[0]) {
					unsigned tmp[HW_WORD_MAX + 1]; int tl = outlen[0]; double tb = best[0];
					memcpy(tmp, out[0], sizeof tmp); memcpy(out[0], out[1], sizeof tmp); memcpy(out[1], tmp, sizeof tmp);
					outlen[0] = outlen[1]; outlen[1] = tl; best[0] = best[1]; best[1] = tb;
				}
			}
		}
	}
	return got;
}

/* The search guided by the words: each prefix of the beam knows the word it is in, as the
 * run of the list's words that start with it; a letter that leaves the list makes it an
 * unknown word, at W.chr per letter, so names and codes still get through. A finished
 * word scores as in text_words. */
struct gstate {
	long p2, p1;                    /* the two words before the current one */
	long lo, hi;                    /* the list's words starting with key; lo < 0: unknown */
	int klen, wlen;                 /* key bytes, letters in the word */
	char key[2 * HW_WORD_MAX + 8];
};

struct guide {
	const struct hw_lang *l;
	const struct hw_context *x;
	struct gstate *st;
	size_t cap;
	int bad;
};

static struct gstate *gnode(struct guide *g, int id)
{
	if ((size_t)id >= g->cap) {
		size_t c = g->cap ? g->cap : 1024;
		while (c <= (size_t)id) c *= 2;
		struct gstate *n = realloc(g->st, c * sizeof *n);
		if (!n) { g->bad = 1; return NULL; }
		g->st = n;
		g->cap = c;
	}
	return &g->st[id];
}

/* what a finished word adds, and the context after it */
static double word_end(const struct guide *g, struct gstate *n)
{
	double d = 0;
	if (!n->wlen) return 0;
	int known = 0;
	long id = HW_NO_WORD;
	if (n->klen <= HW_WORD_MAX) {
		n->key[n->klen] = 0;
		id = hw_words_find(g->x->w, n->key, &known);
	}
	double unk = W.chr * n->wlen;
	if (known) {
		double lp = fmax(hw_words_logp(g->x->w, n->p2, n->p1, id, known), unk);
		/* an unknown word paid as it went */
		d = n->lo < 0 ? lp - unk : lp;
	} else if (n->lo >= 0) {
		d = unk;
	}
	n->p2 = n->p1;
	n->p1 = known && id >= 0 ? id : HW_NO_WORD;
	n->wlen = n->klen = 0;
	n->lo = 0;
	n->hi = hw_words_count(g->x->w);
	return W.lm * d;
}

static double guide_step(void *ctx, int parent, int node, int label)
{
	struct guide *g = ctx;
	struct gstate *p = gnode(g, parent), *n = p ? gnode(g, node) : NULL;
	if (!n) return -INFINITY;
	p = &g->st[parent];             /* gnode may have moved it */
	*n = *p;
	unsigned c = g->l->model.charset[label - 1];
	int wordch = hw_is_letter(c) || (hw_is_apostrophe(c) && n->wlen);
	if (wordch) {
		char u[8];
		size_t ul = utf8_of(&c, 1, u, sizeof u);
		char f[16];
		size_t fl = hw_fold(u, ul, f);
		if (n->klen + fl > HW_WORD_MAX || n->wlen >= HW_WORD_MAX) {
			double d = n->lo >= 0 ? W.chr * (n->wlen + 1) : W.chr;
			n->lo = -1;
			n->wlen++;
			return W.lm * d;
		}
		memcpy(n->key + n->klen, f, fl);
		n->klen += (int)fl;
		n->wlen++;
		if (n->lo >= 0 && hw_words_narrow(g->x->w, n->key, (size_t)n->klen, &n->lo, &n->hi)) return 0;
		double d = n->lo >= 0 ? W.chr * n->wlen : W.chr;
		n->lo = -1;
		return W.lm * d;
	}
	double d = word_end(g, n);
	if (c != ' ') d += W.lm * W.chr;
	if (hw_is_sentence_end(c)) { n->p2 = HW_NO_WORD; n->p1 = HW_BOS; }
	else if (!hw_is_transparent(c)) { n->p2 = n->p1 = HW_NO_WORD; }
	return d;
}

static double guide_end(void *ctx, int node)
{
	struct guide *g = ctx;
	struct gstate *n = gnode(g, node);
	if (!n) return -INFINITY;
	struct gstate t = *n;
	return word_end(g, &t);
}

/* A search weighed by the character model, and by the words too when words is set: the
 * character model's context per prefix beside the guide's state. */
struct fused {
	struct guide g;
	int words;
	hw_chars_ctx *h;
	size_t cap;
};

static hw_chars_ctx *fnode(struct fused *f, int id)
{
	if ((size_t)id >= f->cap) {
		size_t c = f->cap ? f->cap : 1024;
		while (c <= (size_t)id) c *= 2;
		hw_chars_ctx *n = realloc(f->h, c * sizeof *n);
		if (!n) { f->g.bad = 1; return NULL; }
		f->h = n;
		f->cap = c;
	}
	return &f->h[id];
}

static double fused_step(void *ctx, int parent, int node, int label)
{
	struct fused *f = ctx;
	double d = f->words ? guide_step(&f->g, parent, node, label) : 0;
	if (!f->g.x->ch || d == -INFINITY) return d;
	hw_chars_ctx *p = fnode(f, parent), *n = p ? fnode(f, node) : NULL;
	if (!n) return -INFINITY;
	p = &f->h[parent];
	unsigned c = f->g.l->model.charset[label - 1];
	d += chars_step(f->g.x->ch, *p, c);
	*n = hw_chars_push(f->g.x->ch, *p, c);
	return d;
}

static double fused_end(void *ctx, int node)
{
	struct fused *f = ctx;
	return f->words ? guide_end(&f->g, node) : 0;
}

static int fused_read(const struct hw_lang *l, const struct hw_run *r, const struct hw_context *x, int words,
		      struct hw_cand *out, int n)
{
	struct fused f = { { l, x, NULL, 0, 0 }, words, NULL, 0 };
	if (words) {
		struct gstate *root = gnode(&f.g, 0);
		if (!root) return 0;
		*root = (struct gstate){ x->prev2, x->prev1, 0, hw_words_count(x->w), 0, 0, "" };
	}
	if (x->ch) {
		hw_chars_ctx *h = fnode(&f, 0);
		if (!h) return 0;
		*h = x->cctx;
	}
	struct hw_ctc_lm lm = { &f, fused_step, fused_end };
	int k = hw_ctc_beam_lm(hw_run_dec(r), r->T, r->C, words ? W.beam : CW.beam, out, n, &lm);
	free(f.g.st);
	free(f.h);
	return f.g.bad ? 0 : k;
}

static int cmp_total(const void *a, const void *b)
{
	const struct scored *x = a, *y = b;
	return x->total < y->total ? 1 : x->total > y->total ? -1 : 0;
}

int hw_read_words(const struct hw_lang *l, const struct hw_run *r, const struct hw_context *x, int n,
		  struct hw_reading *out)
{
	if (!x || (!x->w && !x->ch)) return hw_read(l, r, n, out);
	weights();
	memset(out, 0, sizeof *out);
	if (n > 8) n = 8;
	if (r->T <= 0 || n <= 0) return 0;
	static struct hw_cand cand[POOL];
	int nc = hw_ctc_beam(hw_run_dec(r), r->T, r->C, W.beam, cand, W.nbest);
	if (!nc) return 0;
	static struct scored s[POOL];
	int ns = 0;
	for (int i = 0; i < nc; i++) {
		struct scored *c = &s[ns];
		if (i && !cand[i].n) continue;      /* ink read as nothing: no candidate */
		c->len = cand[i].n;
		for (int k = 0; k < c->len; k++) c->cp[k] = l->model.charset[cand[i].lab[k] - 1];
		c->from = ns ? HW_FROM_BEAM : HW_FROM_INK;
		c->fwd = hw_text_logp(l, r, c->cp, c->len);
		if (!isfinite(c->fwd)) c->fwd = cand[i].score;
		score(x, c);
		ns++;
	}
	/* readings the character model finds in the ink that its own search missed, then those
	 * the words find (weighed by the character model too); each search's best reading (by
	 * the ink with the character model and the words) is shown whatever the ink says of it */
	static struct scored best[2];
	int nbest = 0;
	for (int pass = 0; pass < 2; pass++) {
		if (pass == 0 && !x->ch) continue;
		if (pass == 1 && !(x->w && W.guided && hw_words_count(x->w))) continue;
		static struct hw_cand gc[POOL];
		int ng = fused_read(l, r, x, pass, gc, W.nbest), have = 0;
		struct scored *b = &best[nbest];
		for (int i = 0; i < ng; i++) {
			static struct scored t;
			struct scored *c = &t;
			c->len = gc[i].n;
			if (!c->len) continue;
			for (int k = 0; k < c->len; k++) c->cp[k] = l->model.charset[gc[i].lab[k] - 1];
			c->from = pass ? HW_FROM_GUIDED : HW_FROM_CHARS;
			int dup = -1;
			for (int q = 0; q < ns && dup < 0; q++) if (same(&s[q], c->cp, c->len)) dup = q;
			if (dup >= 0) *c = s[dup];
			else {
				c->fwd = hw_text_logp(l, r, c->cp, c->len);
				if (!isfinite(c->fwd)) continue;
				score(x, c);
			}
			if (!have || c->total > b->total) { *b = *c; have = 1; }
			/* not what the ink says: only as the search's best */
			if (dup >= 0 || c->fwd < s[0].fwd - W.near || ns >= POOL - 2) continue;
			s[ns++] = *c;
		}
		nbest += have;
	}
	/* words of the list near an unknown word of the ink's best readings */
	int props = 0, nink = x->w ? ns : 0;
	for (int i = 0; i < nink && i < 3 && props < 2; i++) {
		const struct scored *c = &s[i];
		for (int a = 0; a < c->len && props < 2;) {
			if (!is_wordch(c->cp, c->len, a)) { a++; continue; }
			int b = a;
			while (b < c->len && is_wordch(c->cp, c->len, b)) b++;
			int known;
			word_id(x->w, c->cp + a, b - a, &known);
			unsigned near[2][HW_WORD_MAX + 1];
			int nl[2], k = known ? 0 : near_words(l, x, c->cp, a, b, near, nl);
			for (int g = 0; g < k && props < 2 && ns < POOL; g++) {
				struct scored *p = &s[ns];
				int m = 0;
				for (int q = 0; q < a; q++) p->cp[m++] = c->cp[q];
				recase(c->cp + a, b - a, near[g], nl[g]);
				for (int q = 0; q < nl[g] && m < HW_MAX_LABELS; q++) p->cp[m++] = near[g][q];
				for (int q = b; q < c->len && m < HW_MAX_LABELS; q++) p->cp[m++] = c->cp[q];
				p->len = m;
				int dup = 0;
				for (int q = 0; q < ns; q++) dup |= same(&s[q], p->cp, m);
				if (dup) continue;
				p->fwd = hw_text_logp(l, r, p->cp, p->len);
				if (!isfinite(p->fwd) || p->fwd < s[0].fwd - W.near) continue;
				p->from = HW_FROM_WORDS;
				score(x, p);
				ns++;
				props++;
			}
			a = b;
		}
	}
	/* the first: the ink's best, unless another reading of the ink wins clearly and the
	 * best isn't all words; a word from the list is never first */
	int first = 0;
	for (int i = 1; i < ns; i++)
		if (s[i].from != HW_FROM_WORDS && s[i].total > s[first].total) first = i;
	if (first && (s[0].allwords || s[first].total - s[0].total < (x->w ? (x->ch ? CW.wmargin : W.margin) : CW.margin))) first = 0;
	struct scored head = s[first], ink = s[0];
	int rest = 0;
	static struct scored others[POOL];
	for (int i = 0; i < ns; i++) if (i != first) others[rest++] = s[i];
	qsort(others, (size_t)rest, sizeof *others, cmp_total);
	/* the ink's best among the first two */
	if (first && n >= 2)
		for (int i = 0; i < rest; i++)
			if (same(&others[i], ink.cp, ink.len)) {
				memmove(others + 1, others, (size_t)i * sizeof *others);
				others[0] = ink;
				break;
			}
	int k = 0, pinned;
	const struct scored *order[POOL + 2];
	order[k++] = &head;
	for (int i = 0; i < rest && k < n; i++) order[k++] = &others[i];
	/* the first, and the ink's best when it is second, stay; each search's best reading that
	 * isn't listed yet takes the place of the last of the others (FR-019, Candidates) */
	pinned = first && n >= 2 ? 2 : 1;
	for (int b = 0; b < nbest; b++) {
		int in = 0;
		for (int i = 0; i < k; i++) in |= same(order[i], best[b].cp, best[b].len);
		if (in) continue;
		if (k < n) { order[k++] = &best[b]; continue; }
		int at = k - 1;
		while (at >= pinned && order[at] >= best && order[at] < best + 2) at--;
		if (at >= pinned) {
			memmove(order + at, order + at + 1, (size_t)(k - 1 - at) * sizeof *order);
			order[k - 1] = &best[b];
		}
	}
	for (int i = 0; i < k; i++) {
		out->c[i].len = order[i]->len;
		memcpy(out->c[i].cp, order[i]->cp, (size_t)order[i]->len * sizeof(unsigned));
		out->c[i].score = order[i]->fwd;
		out->c[i].from = order[i]->from;
	}
	out->n = k;
	hw_size_rerank(l, r, n, out);
	return out->n;
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
