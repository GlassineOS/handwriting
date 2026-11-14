// SPDX-License-Identifier: GPL-2.0-or-later
// CTC decoding, alignment and loss (Graves et al. 2006), in the log domain.
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "ctc.h"

#define NEG (-INFINITY)
#define PRUNE (-12.0f)       /* classes this unlikely in a frame don't open a prefix */

static double lse(double a, double b)
{
	if (a == NEG) return b;
	if (b == NEG) return a;
	return a > b ? a + log1p(exp(b - a)) : b + log1p(exp(a - b));
}

int hw_ctc_greedy(const float *logp, long T, int C, int *lab, int max)
{
	int n = 0, prev = 0;
	for (long t = 0; t < T; t++) {
		const float *p = logp + (size_t)t * C;
		int best = 0;
		for (int k = 1; k < C; k++) if (p[k] > p[best]) best = k;
		if (best && best != prev && n < max) lab[n++] = best;
		prev = best;
	}
	return n;
}

/* ---------------------------------------------------------------- prefix beam search */

struct node { int parent, label, len; };

struct trie {
	struct node *n;
	size_t len, cap;
	int *hash;          /* open addressing: node ids keyed by (parent, label) */
	size_t hcap;
};

static size_t hkey(int parent, int label, size_t cap)
{
	uint64_t k = (uint64_t)(uint32_t)parent * 0x9E3779B97F4A7C15ULL ^ (uint64_t)(uint32_t)label * 0xC2B2AE3D27D4EB4FULL;
	return (size_t)(k ^ (k >> 29)) & (cap - 1);
}

static int trie_rehash(struct trie *tr, size_t cap)
{
	int *h = malloc(cap * sizeof *h);
	if (!h) return -1;
	for (size_t i = 0; i < cap; i++) h[i] = -1;
	for (size_t id = 1; id < tr->len; id++) {
		size_t k = hkey(tr->n[id].parent, tr->n[id].label, cap);
		while (h[k] >= 0) k = (k + 1) & (cap - 1);
		h[k] = (int)id;
	}
	free(tr->hash);
	tr->hash = h;
	tr->hcap = cap;
	return 0;
}

static int child(struct trie *tr, int parent, int label)
{
	size_t k = hkey(parent, label, tr->hcap);
	for (; tr->hash[k] >= 0; k = (k + 1) & (tr->hcap - 1)) {
		struct node *x = &tr->n[tr->hash[k]];
		if (x->parent == parent && x->label == label) return tr->hash[k];
	}
	if (tr->n[parent].len >= HW_MAX_LABELS) return -1;
	if (tr->len == tr->cap) {
		size_t cap = tr->cap * 2;
		struct node *n = realloc(tr->n, cap * sizeof *n);
		if (!n) return -1;
		tr->n = n; tr->cap = cap;
	}
	int id = (int)tr->len++;
	tr->n[id] = (struct node){ parent, label, tr->n[parent].len + 1 };
	if (tr->len * 2 > tr->hcap) {
		if (trie_rehash(tr, tr->hcap * 2)) { tr->len--; return -1; }
	} else {
		tr->hash[k] = id;
	}
	return id;
}

struct beam { int node; double pb, pnb; };

static int grow_slots(int **slot, size_t *cap, size_t need)
{
	if (need <= *cap) return 0;
	size_t n = need * 2;
	int *s = realloc(*slot, n * sizeof *s);
	if (!s) return -1;
	for (size_t i = *cap; i < n; i++) s[i] = -1;
	*slot = s;
	*cap = n;
	return 0;
}

static int cmp_beam(const void *a, const void *b)
{
	const struct beam *x = a, *y = b;
	double sx = lse(x->pb, x->pnb), sy = lse(y->pb, y->pnb);
	if (sx != sy) return sx < sy ? 1 : -1;
	return x->node - y->node;      /* stable among equals */
}

int hw_ctc_beam(const float *logp, long T, int C, int width, struct hw_cand *out, int nout)
{
	struct trie tr = { 0 };
	tr.cap = 1024;
	tr.n = malloc(tr.cap * sizeof *tr.n);
	size_t ncap = (size_t)width * (size_t)(C + 1) + 1;
	struct beam *cur = malloc((size_t)width * sizeof *cur), *nxt = malloc(ncap * sizeof *nxt);
	int *slot = NULL;
	size_t slotcap = 0;
	int ncur = 0, ret = 0;
	if (!tr.n || !cur || !nxt || trie_rehash(&tr, 2048)) goto out;
	tr.n[0] = (struct node){ -1, 0, 0 };
	tr.len = 1;
	cur[0] = (struct beam){ 0, 0.0, NEG };
	ncur = 1;
	for (long t = 0; t < T; t++) {
		const float *p = logp + (size_t)t * C;
		int nn = 0;
		for (int b = 0; b < ncur; b++) {
			int id = cur[b].node;
			double pb = cur[b].pb, pnb = cur[b].pnb, tot = lse(pb, pnb);
			int last = tr.n[id].label;
			for (int c = 0; c < C; c++) {
				int to = id;
				if (c) {
					if (p[c] < PRUNE) continue;
					size_t before = tr.len;
					to = child(&tr, id, c);
					if (to < 0) continue;
					if (tr.len > before) {
						if (grow_slots(&slot, &slotcap, tr.len)) goto out;
					}
				} else if (grow_slots(&slot, &slotcap, tr.len)) {
					goto out;
				}
				if (slot[to] < 0) { slot[to] = nn; nxt[nn++] = (struct beam){ to, NEG, NEG }; }
				struct beam *x = &nxt[slot[to]];
				if (!c) x->pb = lse(x->pb, tot + p[0]);
				else x->pnb = lse(x->pnb, (c == last ? pb : tot) + p[c]);
			}
			if (last) {     /* the last label again, collapsed into the same prefix */
				struct beam *x = &nxt[slot[id]];
				x->pnb = lse(x->pnb, pnb + p[last]);
			}
		}
		qsort(nxt, (size_t)nn, sizeof *nxt, cmp_beam);
		ncur = nn < width ? nn : width;
		memcpy(cur, nxt, (size_t)ncur * sizeof *cur);
		for (int b = 0; b < nn; b++) slot[nxt[b].node] = -1;
	}
	for (int b = 0; b < ncur && ret < nout; b++) {
		struct hw_cand *c = &out[ret++];
		int id = cur[b].node, n = tr.n[id].len;
		c->n = n;
		c->score = lse(cur[b].pb, cur[b].pnb);
		for (int k = n - 1; k >= 0; k--, id = tr.n[id].parent) c->lab[k] = tr.n[id].label;
	}
out:
	free(tr.n); free(tr.hash); free(cur); free(nxt); free(slot);
	return ret;
}

/* ---------------------------------------------------------------- alignment and loss */

static int ext(const int *lab, int s) { return s & 1 ? lab[s / 2] : 0; }

static int can_skip(const int *lab, int s) { return s >= 2 && (s & 1) && lab[s / 2] != lab[s / 2 - 1]; }

double hw_ctc_align(const float *logp, long T, int C, const int *lab, int L,
		    long *first, long *last, long *from, long *to)
{
	int S = 2 * L + 1;
	if (T <= 0) return NEG;
	double *v = malloc((size_t)S * sizeof *v), *w = malloc((size_t)S * sizeof *w);
	unsigned char *bp = malloc((size_t)T * S);
	int *state = malloc((size_t)T * sizeof *state);
	double res = NEG;
	if (!v || !w || !bp || !state) goto out;
	for (int s = 0; s < S; s++) v[s] = NEG;
	v[0] = logp[0];
	if (S > 1) v[1] = logp[ext(lab, 1)];
	for (long t = 1; t < T; t++) {
		const float *p = logp + (size_t)t * C;
		for (int s = 0; s < S; s++) {
			double best = v[s];
			unsigned char from_ = 0;
			if (s >= 1 && v[s - 1] > best) { best = v[s - 1]; from_ = 1; }
			if (can_skip(lab, s) && v[s - 2] > best) { best = v[s - 2]; from_ = 2; }
			w[s] = best == NEG ? NEG : best + p[ext(lab, s)];
			bp[(size_t)t * S + s] = from_;
		}
		double *x = v; v = w; w = x;
	}
	int s = S - 1;
	if (S > 1 && v[S - 2] > v[S - 1]) s = S - 2;
	res = v[s];
	if (res == NEG) goto out;
	for (long t = T - 1; t >= 0; t--) {
		state[t] = s;
		if (t) s -= bp[(size_t)t * S + s];
	}
	for (int k = 0; k < L; k++) first[k] = last[k] = -1;
	for (long t = 0; t < T; t++)
		if (state[t] & 1) {
			int k = state[t] / 2;
			if (first[k] < 0) first[k] = t;
			last[k] = t;
		}
	for (int k = 0; k < L; k++) {
		from[k] = k ? (last[k - 1] + first[k]) / 2 + 1 : 0;
		to[k] = k + 1 < L ? (last[k] + first[k + 1]) / 2 : T - 1;
	}
out:
	free(v); free(w); free(bp); free(state);
	return res;
}

double hw_ctc_loss(const float *logp, long T, int C, const int *lab, int L, float *grad, float weight)
{
	int S = 2 * L + 1;
	if (T <= 0) return INFINITY;
	double *a = malloc((size_t)T * S * sizeof *a), *b = malloc((size_t)T * S * sizeof *b);
	double res = INFINITY;
	if (!a || !b) goto out;
	for (size_t i = 0; i < (size_t)T * S; i++) a[i] = b[i] = NEG;
	a[0] = logp[0];
	if (S > 1) a[1] = logp[ext(lab, 1)];
	for (long t = 1; t < T; t++)
		for (int s = 0; s < S; s++) {
			double x = a[(t - 1) * S + s];
			if (s >= 1) x = lse(x, a[(t - 1) * S + s - 1]);
			if (can_skip(lab, s)) x = lse(x, a[(t - 1) * S + s - 2]);
			a[t * S + s] = x == NEG ? NEG : x + logp[t * C + ext(lab, s)];
		}
	b[(T - 1) * S + S - 1] = logp[(T - 1) * C + ext(lab, S - 1)];
	if (S > 1) b[(T - 1) * S + S - 2] = logp[(T - 1) * C + ext(lab, S - 2)];
	for (long t = T - 2; t >= 0; t--)
		for (int s = 0; s < S; s++) {
			double x = b[(t + 1) * S + s];
			if (s + 1 < S) x = lse(x, b[(t + 1) * S + s + 1]);
			if (s + 2 < S && can_skip(lab, s + 2)) x = lse(x, b[(t + 1) * S + s + 2]);
			b[t * S + s] = x == NEG ? NEG : x + logp[t * C + ext(lab, s)];
		}
	double lp = a[(T - 1) * S + S - 1];
	if (S > 1) lp = lse(lp, a[(T - 1) * S + S - 2]);
	if (lp == NEG) goto out;
	res = -lp;
	if (!grad) goto out;
	double *occ = malloc((size_t)C * sizeof *occ);
	if (!occ) goto out;
	for (long t = 0; t < T; t++) {
		for (int k = 0; k < C; k++) occ[k] = NEG;
		for (int s = 0; s < S; s++) {
			int k = ext(lab, s);
			occ[k] = lse(occ[k], a[t * S + s] + b[t * S + s]);
		}
		for (int k = 0; k < C; k++) {
			double y = exp(logp[t * C + k]);
			double g = y - (occ[k] == NEG ? 0.0 : exp(occ[k] - lp - logp[t * C + k]));
			grad[t * C + k] += (float)(g * weight);
		}
	}
	free(occ);
out:
	free(a); free(b);
	return res;
}
