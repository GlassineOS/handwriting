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
#include "words.h"

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
	for (int i = 0; i < nc; i++) out->c[i].from = i ? HW_FROM_BEAM : HW_FROM_INK;
	return nc;
}

/* ---------------------------------------------------------------- readings with words */

/* the weights (natural log units); $GLASSINE_HANDWRITING_WORDS="lm=0.3,margin=2,..."
 * replaces them (tuning) */
static struct { double lm, word, chr, margin, near; int beam, nbest, minlen; } W = { 0.3, 0, -6.0, 2.0, 6.0, 16, 10, 4 };

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
		p += used;
		if (*p == ',') p++;
	}
	if (W.nbest > HW_READ_MAX - 2) W.nbest = HW_READ_MAX - 2;
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

struct scored {
	unsigned cp[HW_MAX_LABELS];
	int len, from, allwords;
	double fwd, total;
};

static double forward(const struct hw_lang *l, const struct hw_run *r, const unsigned *cp, int n)
{
	int lab[HW_MAX_LABELS], L = 0;
	for (int k = 0; k < n; k++) {
		int c = hw_class_of(&l->model, cp[k]);
		if (!c) return -INFINITY;
		lab[L++] = c;
	}
	double nll = hw_ctc_loss(r->logp, r->T, r->C, lab, L, NULL, 0);
	return isfinite(nll) ? -nll : -INFINITY;
}

static void score(const struct hw_context *x, struct scored *s)
{
	int nw, unk;
	double lp = text_words(x, s->cp, s->len, &nw, &unk);
	s->allwords = !unk && nw;
	s->total = s->fwd + W.lm * lp + W.word * nw;
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
				if (id < 0) continue;
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

static int cmp_total(const void *a, const void *b)
{
	const struct scored *x = a, *y = b;
	return x->total < y->total ? 1 : x->total > y->total ? -1 : 0;
}

int hw_read_words(const struct hw_lang *l, const struct hw_run *r, const struct hw_context *x, int n,
		  struct hw_reading *out)
{
	if (!x || !x->w) return hw_read(l, r, n, out);
	weights();
	memset(out, 0, sizeof *out);
	if (n > 8) n = 8;
	if (r->T <= 0 || n <= 0) return 0;
	struct hw_cand cand[HW_READ_MAX];
	int nc = hw_ctc_beam(r->logp, r->T, r->C, W.beam, cand, W.nbest);
	if (!nc) return 0;
	static struct scored s[HW_READ_MAX];
	int ns = 0;
	for (int i = 0; i < nc; i++) {
		struct scored *c = &s[ns];
		if (i && !cand[i].n) continue;      /* ink read as nothing: no candidate */
		c->len = cand[i].n;
		for (int k = 0; k < c->len; k++) c->cp[k] = l->model.charset[cand[i].lab[k] - 1];
		c->from = ns ? HW_FROM_BEAM : HW_FROM_INK;
		c->fwd = forward(l, r, c->cp, c->len);
		if (!isfinite(c->fwd)) c->fwd = cand[i].score;
		score(x, c);
		ns++;
	}
	/* words of the list near an unknown word of the ink's best readings */
	int props = 0, nink = ns;
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
			for (int g = 0; g < k && props < 2 && ns < HW_READ_MAX; g++) {
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
				p->fwd = forward(l, r, p->cp, p->len);
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
		if (s[i].from == HW_FROM_BEAM && s[i].total > s[first].total) first = i;
	if (first && (s[0].allwords || s[first].total - s[0].total < W.margin)) first = 0;
	struct scored head = s[first], ink = s[0];
	int rest = 0;
	static struct scored others[HW_READ_MAX];
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
	int k = 0;
	const struct scored *order[HW_READ_MAX];
	order[k++] = &head;
	for (int i = 0; i < rest && k < n; i++) order[k++] = &others[i];
	for (int i = 0; i < k; i++) {
		out->c[i].len = order[i]->len;
		memcpy(out->c[i].cp, order[i]->cp, (size_t)order[i]->len * sizeof(unsigned));
		out->c[i].score = order[i]->fwd;
		out->c[i].from = order[i]->from;
	}
	out->n = k;
	return k;
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
