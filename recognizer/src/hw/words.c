// SPDX-License-Identifier: GPL-2.0-or-later
// Words for reading prose (words.h). The model format is the predictor's (version 1 of
// GlassineOS/shell src/predict/gpm.h, described in its docs/predictor.md); folding and the
// letter classes are the predictor's too, so a word matches the same way on both sides.
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "words.h"

/* ---------------------------------------------------------------- the model file */

#define GPM_MAGIC "GLPM"
#define GPM_VERSION 1

struct gpm_header {
	char magic[4];
	uint32_t version;
	char lang[8];
	uint32_t nvocab;
	uint32_t off_vocab;
	uint32_t off_str, len_str;
	uint32_t off_bi;
	uint32_t off_bisucc, nbisucc;
	uint32_t off_tri, ntri;
	uint32_t off_trisucc, ntrisucc;
	uint32_t off_top, ntop;
	uint32_t reserved[4];
};

struct gpm_word { uint32_t key, disp; uint8_t q, pad[3]; };
struct gpm_tri { uint32_t a, b, start; };

#define GPM_ID(e) ((e) >> 8)
#define GPM_Q(e) ((e) & 0xFF)

struct hw_words {
	char lang[36];
	char mlang[36];                 /* the model's language: lang, else its base (en-GB: en) */
	/* the model */
	void *map;
	size_t len;
	struct stat mst;
	const struct gpm_header *h;
	const char *str;
	const struct gpm_word *v;
	const uint32_t *bi, *bisucc, *trisucc;
	const struct gpm_tri *tri;
	/* the person's words: learned (folded, sorted) and removed */
	char **learned, **removed;
	size_t nlearned, nremoved;
	struct stat lst;
	int have_lst;
};

static struct hw_words cache[4];
static size_t ncache;

/* p = 2^(-q/8) */
static double qp(unsigned q) { return exp2(-(double)q / 8); }

static int in(size_t off, size_t n, size_t sz, size_t len)
{
	return off % 4 == 0 && off <= len && n <= (len - off) / (sz ? sz : 1);
}

static int model_check(struct hw_words *w)
{
	const struct gpm_header *h = w->map;
	size_t L = w->len;
	if (L < sizeof *h || memcmp(h->magic, GPM_MAGIC, 4) || h->version != GPM_VERSION) return -1;
	uint32_t V = h->nvocab;
	if (V == 0 || V >= (1u << 21) - 1) return -1;
	if (!in(h->off_vocab, V, sizeof(struct gpm_word), L) || h->off_str > L || h->len_str > L - h->off_str ||
	    !h->len_str || !in(h->off_bi, (size_t)V + 2, 4, L) || !in(h->off_bisucc, h->nbisucc, 4, L) ||
	    !in(h->off_tri, h->ntri, sizeof(struct gpm_tri), L) || !in(h->off_trisucc, h->ntrisucc, 4, L))
		return -1;
	w->h = h;
	w->str = (const char *)w->map + h->off_str;
	w->v = (const void *)((const char *)w->map + h->off_vocab);
	w->bi = (const void *)((const char *)w->map + h->off_bi);
	w->bisucc = (const void *)((const char *)w->map + h->off_bisucc);
	w->tri = (const void *)((const char *)w->map + h->off_tri);
	w->trisucc = (const void *)((const char *)w->map + h->off_trisucc);
	if (w->str[h->len_str - 1]) return -1;
	for (uint32_t i = 0; i < V; i++) {
		if (w->v[i].key >= h->len_str || w->v[i].disp >= h->len_str) return -1;
		if (i && strcmp(w->str + w->v[i - 1].key, w->str + w->v[i].key) >= 0) return -1;
	}
	for (uint32_t i = 0; i <= V; i++) if (w->bi[i] > w->bi[i + 1]) return -1;
	if (w->bi[V + 1] > h->nbisucc) return -1;
	for (uint32_t i = 0; i < h->ntri; i++) {
		if (w->tri[i].start > h->ntrisucc || w->tri[i].a > V || w->tri[i].b > V) return -1;
		if (i && w->tri[i].start < w->tri[i - 1].start) return -1;
	}
	for (uint32_t i = 0; i < h->nbisucc; i++) if (GPM_ID(w->bisucc[i]) >= V) return -1;
	for (uint32_t i = 0; i < h->ntrisucc; i++) if (GPM_ID(w->trisucc[i]) >= V) return -1;
	return 0;
}

static const char *models_dir(void)
{
	const char *d = getenv("GLASSINE_PREDICT_MODELS");
	return d && *d ? d : "/usr/share/glassine/predict";
}

static int store_path(char *p, size_t n)
{
	const char *x = getenv("XDG_DATA_HOME"), *home = getenv("HOME");
	if (x && x[0] == '/') return snprintf(p, n, "%s/glassine/predict/words", x) >= (int)n ? -1 : 0;
	if (!home || home[0] != '/') return -1;
	return snprintf(p, n, "%s/.local/share/glassine/predict/words", home) >= (int)n ? -1 : 0;
}

static void model_unload(struct hw_words *w)
{
	if (w->map) munmap(w->map, w->len);
	w->map = NULL; w->len = 0; w->h = NULL;
}

static void model_load(struct hw_words *w)
{
	char p[4096];
	struct stat st;
	snprintf(p, sizeof p, "%s/%s.gpm", models_dir(), w->mlang);
	if (stat(p, &st)) memset(&st, 0, sizeof st);
	if (w->map && st.st_ino == w->mst.st_ino && st.st_size == w->mst.st_size &&
	    st.st_mtim.tv_sec == w->mst.st_mtim.tv_sec && st.st_mtim.tv_nsec == w->mst.st_mtim.tv_nsec)
		return;
	model_unload(w);
	w->mst = st;
	if (!st.st_size) return;
	int fd = open(p, O_RDONLY | O_CLOEXEC);
	if (fd < 0) return;
	void *m = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
	close(fd);
	if (m == MAP_FAILED) return;
	w->map = m;
	w->len = (size_t)st.st_size;
	if (model_check(w)) {
		fprintf(stderr, "glassine-handwriting: %s: not a word model this version reads\n", p);
		model_unload(w);
	}
}

static void free_list(char **l, size_t n)
{
	for (size_t i = 0; i < n; i++) free(l[i]);
	free(l);
}

static int cmp_str(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

static void push(char ***l, size_t *n, size_t *cap, const char *s)
{
	if (*n == *cap) {
		size_t c = *cap ? *cap * 2 : 64;
		char **x = realloc(*l, c * sizeof *x);
		if (!x) return;
		*l = x; *cap = c;
	}
	char *d = strdup(s);
	if (d) (*l)[(*n)++] = d;
}

/* the predictor's store, version 1: "w LANG WORD COUNT DAY" and "x LANG WORD" lines (tabs
 * or spaces); a line it doesn't understand counts for nothing */
static void store_load(struct hw_words *w)
{
	char p[4096];
	struct stat st;
	if (store_path(p, sizeof p) || stat(p, &st)) memset(&st, 0, sizeof st);
	if (w->have_lst && st.st_ino == w->lst.st_ino && st.st_size == w->lst.st_size &&
	    st.st_mtim.tv_sec == w->lst.st_mtim.tv_sec && st.st_mtim.tv_nsec == w->lst.st_mtim.tv_nsec)
		return;
	free_list(w->learned, w->nlearned);
	free_list(w->removed, w->nremoved);
	w->learned = w->removed = NULL;
	w->nlearned = w->nremoved = 0;
	w->lst = st;
	w->have_lst = 1;
	if (!st.st_size) return;
	FILE *f = fopen(p, "re");
	if (!f) return;
	char line[512];
	size_t cl = 0, cr = 0;
	size_t base = strcspn(w->lang, "-_");
	if (!fgets(line, sizeof line, f) || strncmp(line, "glassine-words 1", 16)) { fclose(f); return; }
	while (fgets(line, sizeof line, f)) {
		char *k = strtok(line, " \t\r\n"), *lang = strtok(NULL, " \t\r\n"), *word = strtok(NULL, " \t\r\n");
		if (!k || !lang || !word || strlen(word) > HW_WORD_MAX) continue;
		char fw[2 * HW_WORD_MAX + 1];
		hw_fold(word, strlen(word), fw);
		if (!strcmp(k, "x")) push(&w->removed, &w->nremoved, &cr, fw);
		else if (!strcmp(k, "w") && (!strcmp(lang, w->lang) || (strlen(lang) == base && !strncmp(lang, w->lang, base))))
			push(&w->learned, &w->nlearned, &cl, fw);
	}
	fclose(f);
	qsort(w->learned, w->nlearned, sizeof *w->learned, cmp_str);
	qsort(w->removed, w->nremoved, sizeof *w->removed, cmp_str);
}

static int listed(char *const *l, size_t n, const char *s)
{
	return l && bsearch(&s, l, n, sizeof *l, cmp_str) != NULL;
}

const struct hw_words *hw_words_get(const char *lang)
{
	struct hw_words *w = NULL;
	for (size_t i = 0; i < ncache; i++) if (!strcmp(cache[i].lang, lang)) w = &cache[i];
	if (!w) {
		if (ncache == sizeof cache / sizeof *cache) {
			model_unload(&cache[0]);
			free_list(cache[0].learned, cache[0].nlearned);
			free_list(cache[0].removed, cache[0].nremoved);
			memmove(cache, cache + 1, sizeof cache - sizeof *cache);
			ncache--;
		}
		w = &cache[ncache++];
		memset(w, 0, sizeof *w);
		snprintf(w->lang, sizeof w->lang, "%s", lang);
		snprintf(w->mlang, sizeof w->mlang, "%s", lang);
		char p[4096];
		snprintf(p, sizeof p, "%s/%s.gpm", models_dir(), lang);
		if (access(p, R_OK) && strcspn(lang, "-_") < strlen(lang))   /* en-GB: en's model */
			snprintf(w->mlang, sizeof w->mlang, "%.*s", (int)strcspn(lang, "-_"), lang);
	}
	model_load(w);
	store_load(w);
	return w->map || w->nlearned ? w : NULL;
}

void hw_words_forget_all(void)
{
	for (size_t i = 0; i < ncache; i++) {
		model_unload(&cache[i]);
		free_list(cache[i].learned, cache[i].nlearned);
		free_list(cache[i].removed, cache[i].nremoved);
	}
	ncache = 0;
}

/* ---------------------------------------------------------------- text */

static int u8dec(const unsigned char *s, size_t n, uint32_t *cp)
{
	unsigned c = s[0];
	int len; uint32_t v;
	if (c < 0x80) { *cp = c; return 1; }
	if (c >= 0xC2 && c <= 0xDF) { len = 2; v = c & 0x1F; }
	else if (c >= 0xE0 && c <= 0xEF) { len = 3; v = c & 0x0F; }
	else if (c >= 0xF0 && c <= 0xF4) { len = 4; v = c & 0x07; }
	else { *cp = 0xFFFD; return 1; }
	if ((size_t)len > n) { *cp = 0xFFFD; return 1; }
	for (int i = 1; i < len; i++) {
		if ((s[i] & 0xC0) != 0x80) { *cp = 0xFFFD; return 1; }
		v = (v << 6) | (s[i] & 0x3F);
	}
	*cp = v;
	return len;
}

static int u8enc(uint32_t c, char *o)
{
	if (c < 0x80) { o[0] = (char)c; return 1; }
	if (c < 0x800) { o[0] = (char)(0xC0 | c >> 6); o[1] = (char)(0x80 | (c & 0x3F)); return 2; }
	if (c < 0x10000) {
		o[0] = (char)(0xE0 | c >> 12); o[1] = (char)(0x80 | ((c >> 6) & 0x3F));
		o[2] = (char)(0x80 | (c & 0x3F)); return 3;
	}
	o[0] = (char)(0xF0 | c >> 18); o[1] = (char)(0x80 | ((c >> 12) & 0x3F));
	o[2] = (char)(0x80 | ((c >> 6) & 0x3F)); o[3] = (char)(0x80 | (c & 0x3F)); return 4;
}

uint32_t hw_fold_cp(uint32_t c)
{
	if (c < 0x80) return (c >= 'A' && c <= 'Z') ? c + 32 : c;
	if (c >= 0xC0 && c <= 0xDE && c != 0xD7) return c + 32;
	if (c >= 0x100 && c <= 0x17F) {
		if (c == 0x130) return 'i';
		if (c == 0x131 || c == 0x138 || c == 0x149 || c == 0x17F) return c;
		if (c == 0x178) return 0xFF;
		if ((c >= 0x139 && c <= 0x148) || (c >= 0x179 && c <= 0x17E)) return (c & 1) ? c + 1 : c;
		return (c & 1) ? c : c + 1;
	}
	if (c >= 0x391 && c <= 0x3AB && c != 0x3A2) return c + 32;
	if (c >= 0x410 && c <= 0x42F) return c + 32;
	if (c >= 0x400 && c <= 0x40F) return c + 80;
	if (c == 0x1E9E) return 0xDF;
	if (c == 0x2019 || c == 0x2BC) return '\'';
	return c;
}

size_t hw_fold(const char *s, size_t n, char *out)
{
	size_t i = 0, o = 0;
	while (i < n) {
		uint32_t c;
		i += (size_t)u8dec((const unsigned char *)s + i, n - i, &c);
		o += (size_t)u8enc(hw_fold_cp(c), out + o);
	}
	out[o] = 0;
	return o;
}

int hw_is_letter(uint32_t c)
{
	if (c < 0x80) return (c | 32) >= 'a' && (c | 32) <= 'z';
	if (c < 0xC0 || c == 0xD7 || c == 0xF7 || c == 0xFFFD) return 0;
	if (c < 0x2000) return 1;
	if (c >= 0x3040 && c < 0xD800) return 1;
	return 0;
}

int hw_is_apostrophe(uint32_t c) { return c == '\'' || c == 0x2019 || c == 0x2BC; }

int hw_is_sentence_end(uint32_t c) { return c == '.' || c == '!' || c == '?' || c == '\n' || c == '\r'; }

int hw_is_transparent(uint32_t c)
{
	switch (c) {
	case ' ': case '\t': case ',': case '"': case '(': case ')': case '[': case ']':
	case '-': case '\'': case 0xA0: case 0xAB: case 0xBB: case 0x2013: case 0x2014:
	case 0x2018: case 0x2019: case 0x201C: case 0x201D: case 0x201E: case 0x2026: case 0x2BC:
		return 1;
	}
	return 0;
}

/* ---------------------------------------------------------------- lookups */

static long model_find(const struct hw_words *w, const char *f)
{
	if (!w->h) return HW_NO_WORD;
	size_t lo = 0, hi = w->h->nvocab;
	while (lo < hi) {
		size_t mid = (lo + hi) / 2;
		int c = strcmp(w->str + w->v[mid].key, f);
		if (!c) return (long)mid;
		if (c < 0) lo = mid + 1; else hi = mid;
	}
	return HW_NO_WORD;
}

long hw_words_find(const struct hw_words *w, const char *folded, int *known)
{
	*known = 0;
	if (!w || listed(w->removed, w->nremoved, folded)) return HW_NO_WORD;
	long id = model_find(w, folded);
	*known = id >= 0 || listed(w->learned, w->nlearned, folded);
	return id;
}

static double bigram(const struct hw_words *w, long h, long id)
{
	uint32_t ctx = h == HW_BOS ? w->h->nvocab : (uint32_t)h;
	for (uint32_t e = w->bi[ctx]; e < w->bi[ctx + 1]; e++)
		if (GPM_ID(w->bisucc[e]) == (uint32_t)id) return qp(GPM_Q(w->bisucc[e]));
	return 0;
}

static double trigram(const struct hw_words *w, long a, long b, long id)
{
	uint32_t ua = a == HW_BOS ? w->h->nvocab : (uint32_t)a, ub = b == HW_BOS ? w->h->nvocab : (uint32_t)b;
	size_t lo = 0, hi = w->h->ntri;
	uint64_t key = (uint64_t)ua << 32 | ub;
	while (lo < hi) {
		size_t mid = (lo + hi) / 2;
		uint64_t k = (uint64_t)w->tri[mid].a << 32 | w->tri[mid].b;
		if (k == key) {
			uint32_t end = mid + 1 < w->h->ntri ? w->tri[mid + 1].start : w->h->ntrisucc;
			for (uint32_t e = w->tri[mid].start; e < end; e++)
				if (GPM_ID(w->trisucc[e]) == (uint32_t)id) return qp(GPM_Q(w->trisucc[e]));
			return 0;
		}
		if (k < key) lo = mid + 1; else hi = mid;
	}
	return 0;
}

double hw_words_logp(const struct hw_words *w, long prev2, long prev1, long word, int known)
{
	/* as the predictor weighs its context: a learned word 2^-16, an unknown one 2^-24 */
	if (word < 0 || !w || !w->h) return log(known ? exp2(-16) : exp2(-24));
	double pu = qp(w->v[word].q), p = pu;
	if (prev1 != HW_NO_WORD) {
		double pb = bigram(w, prev1, word);
		p = 0.8 * pb + 0.2 * pu;
		if (prev2 != HW_NO_WORD) {
			double pt = trigram(w, prev2, prev1, word);
			if (pt > 0) p = 0.6 * pt + 0.3 * pb + 0.1 * pu;
		}
	}
	return log(p > exp2(-32) ? p : exp2(-32));
}

const char *hw_words_text(const struct hw_words *w, long id)
{
	return w && w->h && id >= 0 && id < (long)w->h->nvocab ? w->str + w->v[id].disp : NULL;
}

double hw_words_unigram(const struct hw_words *w, long id)
{
	return w && w->h && id >= 0 && id < (long)w->h->nvocab ? log(qp(w->v[id].q)) : log(exp2(-24));
}

long hw_words_count(const struct hw_words *w) { return w && w->h ? (long)w->h->nvocab : 0; }

int hw_words_narrow(const struct hw_words *w, const char *prefix, size_t n, long *lo, long *hi)
{
	if (!w || !w->h) { *lo = *hi = 0; return 0; }
	long a = *lo, b = *hi;
	/* the keys are sorted by their bytes, so those with the prefix are one run */
	while (a < b) {
		long m = a + (b - a) / 2;
		if (strncmp(w->str + w->v[m].key, prefix, n) < 0) a = m + 1; else b = m;
	}
	long c = a, d = *hi;
	while (c < d) {
		long m = c + (d - c) / 2;
		if (strncmp(w->str + w->v[m].key, prefix, n) <= 0) c = m + 1; else d = m;
	}
	*lo = a;
	*hi = c;
	return c > a;
}
