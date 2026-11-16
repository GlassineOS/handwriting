// SPDX-License-Identifier: GPL-2.0-or-later
// The character model (chars.h). Format: gclm version 1, written by the trainer's
// charlm.py and described there and in docs/recognizer.md; interpolated Witten-Bell over
// the counts it holds, computed as charlm.py computes it.
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include "chars.h"
#include "recog.h"

#define GCLM_MAGIC "GHCM"
#define GCLM_VERSION 1
#define GCLM_HEADER 48
#define WITTEN_BELL 1
#define MAX_ORDER 8

struct hw_chars {
	char lang[36];
	void *map;
	size_t len;
	struct stat st;
	uint32_t order, nsym, nnodes;
	uint32_t cp[256];               /* symbol -> code point */
	const uint32_t *es, *ss, *edges, *succ;
	uint32_t *total;                /* per node: the sum of its counts */
};

static struct hw_chars cache[4];
static size_t ncache;

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

static void unload(struct hw_chars *m)
{
	if (m->map) munmap(m->map, m->len);
	free(m->total);
	m->map = NULL; m->len = 0; m->total = NULL; m->nnodes = 0;
}

static int check(struct hw_chars *m)
{
	const unsigned char *b = m->map;
	size_t L = m->len;
	if (L < GCLM_HEADER || memcmp(b, GCLM_MAGIC, 4)) return -1;
	uint32_t h[11];
	memcpy(h, b + 4, sizeof h);
	uint32_t order = h[2], nsym = h[3], csb = h[4], nn = h[5], ne = h[6], nsucc = h[7];
	if (h[0] != GCLM_VERSION || h[1] != GCLM_HEADER || h[8] != WITTEN_BELL) return -1;
	if (order < 1 || order > MAX_ORDER || nsym < 1 || nsym > 255 || nn < 1 || nn >= 1u << 24) return -1;
	size_t off = GCLM_HEADER + (size_t)csb + (-(size_t)csb & 3);
	size_t need = off + 4 * ((size_t)nn + 1) * 2 + 4 * (size_t)ne + 4 * (size_t)nsucc;
	if (csb > L || need != L) return -1;
	uint32_t k = 0;
	for (size_t i = 0; i < csb && k < 256;) {
		uint32_t c;
		i += (size_t)u8dec(b + GCLM_HEADER + i, csb - i, &c);
		if (c == 0xFFFD) return -1;
		m->cp[k++] = c;
	}
	if (k != nsym) return -1;
	m->es = (const uint32_t *)(b + off);
	m->ss = m->es + nn + 1;
	m->edges = m->ss + nn + 1;
	m->succ = m->edges + ne;
	if (m->es[0] || m->ss[0] || m->es[nn] != ne || m->ss[nn] != nsucc) return -1;
	for (uint32_t i = 0; i < nn; i++) {
		if (m->es[i] > m->es[i + 1] || m->ss[i] > m->ss[i + 1]) return -1;
		for (uint32_t e = m->es[i]; e < m->es[i + 1]; e++) {
			uint32_t s = m->edges[e] >> 24, to = m->edges[e] & 0xFFFFFF;
			if (s >= nsym || to <= i || to >= nn) return -1;
			if (e > m->es[i] && s <= m->edges[e - 1] >> 24) return -1;
		}
		for (uint32_t e = m->ss[i]; e < m->ss[i + 1]; e++) {
			uint32_t s = m->succ[e] >> 24;
			if (s >= nsym) return -1;
			if (e > m->ss[i] && s <= m->succ[e - 1] >> 24) return -1;
		}
	}
	m->total = malloc(nn * sizeof *m->total);
	if (!m->total) return -1;
	for (uint32_t i = 0; i < nn; i++) {
		uint64_t t = 0;
		for (uint32_t e = m->ss[i]; e < m->ss[i + 1]; e++) t += m->succ[e] & 0xFFFFFF;
		if (t > 0xFFFFFFFFu) return -1;
		m->total[i] = (uint32_t)t;
	}
	m->order = order; m->nsym = nsym; m->nnodes = nn;
	return 0;
}

static void load(struct hw_chars *m)
{
	char p[4096];
	struct stat st;
	snprintf(p, sizeof p, "%s/%s/chars.gclm", hw_models_dir(), m->lang);
	if (stat(p, &st)) memset(&st, 0, sizeof st);
	if (st.st_ino == m->st.st_ino && st.st_size == m->st.st_size && st.st_mtim.tv_sec == m->st.st_mtim.tv_sec &&
	    st.st_mtim.tv_nsec == m->st.st_mtim.tv_nsec && (m->map || !st.st_size))
		return;
	unload(m);
	m->st = st;
	if (!st.st_size) return;
	int fd = open(p, O_RDONLY | O_CLOEXEC);
	if (fd < 0) return;
	void *x = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
	close(fd);
	if (x == MAP_FAILED) return;
	m->map = x;
	m->len = (size_t)st.st_size;
	if (check(m)) {
		fprintf(stderr, "glassine-handwriting: %s: not a character model this version reads\n", p);
		unload(m);
	}
}

const struct hw_chars *hw_chars_get(const char *lang)
{
	struct hw_chars *m = NULL;
	for (size_t i = 0; i < ncache; i++) if (!strcmp(cache[i].lang, lang)) m = &cache[i];
	if (!m) {
		if (ncache == sizeof cache / sizeof *cache) {
			unload(&cache[0]);
			memmove(cache, cache + 1, sizeof cache - sizeof *cache);
			ncache--;
		}
		m = &cache[ncache++];
		memset(m, 0, sizeof *m);
		snprintf(m->lang, sizeof m->lang, "%s", lang);
	}
	load(m);
	return m->map ? m : NULL;
}

void hw_chars_forget_all(void)
{
	for (size_t i = 0; i < ncache; i++) unload(&cache[i]);
	ncache = 0;
}

int hw_chars_order(const struct hw_chars *m) { return m ? (int)m->order : 0; }

/* the model's symbol for a code point (as the trainer's clean() writes text), -1 none */
static int sym_of(const struct hw_chars *m, uint32_t cp)
{
	switch (cp) {
	case '\t': case '\n': case '\r': case 0xA0: cp = ' '; break;
	case 0x2018: case 0x2019: case 0x201C: case 0x201D: case '"': case 0x2BC: cp = '\''; break;
	case 0x2013: case 0x2014: cp = '-'; break;
	}
	for (uint32_t k = 0; k < m->nsym; k++) if (m->cp[k] == cp) return (int)k;
	return -1;
}

hw_chars_ctx hw_chars_push(const struct hw_chars *m, hw_chars_ctx h, uint32_t cp)
{
	int s = sym_of(m, cp);
	if (s < 0 || m->order < 2) return 0;
	h = h << 8 | (uint64_t)(s + 1);
	if (m->order - 1 < 8) h &= ((uint64_t)1 << (8 * (m->order - 1))) - 1;
	return h;
}

hw_chars_ctx hw_chars_start(const struct hw_chars *m, const char *before)
{
	hw_chars_ctx h = 0;
	size_t n = before ? strlen(before) : 0;
	if (!n) return hw_chars_push(m, 0, ' ');
	size_t from = n > 64 ? n - 64 : 0;
	while (from > 0 && ((unsigned char)before[from] & 0xC0) == 0x80) from--;
	for (size_t i = from; i < n;) {
		uint32_t c;
		i += (size_t)u8dec((const unsigned char *)before + i, n - i, &c);
		h = hw_chars_push(m, h, c);
	}
	return h;
}

static uint32_t child(const struct hw_chars *m, uint32_t node, uint32_t s)
{
	uint32_t lo = m->es[node], hi = m->es[node + 1];
	while (lo < hi) {
		uint32_t mid = lo + (hi - lo) / 2, x = m->edges[mid] >> 24;
		if (x == s) return m->edges[mid] & 0xFFFFFF;
		if (x < s) lo = mid + 1; else hi = mid;
	}
	return 0;
}

static uint32_t count_of(const struct hw_chars *m, uint32_t node, uint32_t s)
{
	uint32_t lo = m->ss[node], hi = m->ss[node + 1];
	while (lo < hi) {
		uint32_t mid = lo + (hi - lo) / 2, x = m->succ[mid] >> 24;
		if (x == s) return m->succ[mid] & 0xFFFFFF;
		if (x < s) lo = mid + 1; else hi = mid;
	}
	return 0;
}

double hw_chars_logp(const struct hw_chars *m, hw_chars_ctx h, uint32_t cp)
{
	int s = sym_of(m, cp);
	if (s < 0) return log(1.0 / m->nsym);
	uint32_t path[MAX_ORDER], np = 0, node = 0;
	path[np++] = 0;
	for (; h && np < m->order; h >>= 8) {
		node = child(m, node, (uint32_t)(h & 0xFF) - 1);
		if (!node) break;
		path[np++] = node;
	}
	double p = 1.0 / m->nsym;
	for (uint32_t k = 0; k < np; k++) {
		uint32_t i = path[k], T = m->ss[i + 1] - m->ss[i];
		if (!T) continue;
		p = (count_of(m, i, (uint32_t)s) + T * p) / ((double)m->total[i] + T);
	}
	return log(p);
}
