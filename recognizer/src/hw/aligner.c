// SPDX-License-Identifier: GPL-2.0-or-later
// The point aligner's run and its decoding (aligner.h), the same computation as
// train/src/glassine_train/aligner.py (featurize_rows, viterbi, decode_delayed).
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "aligner.h"
#include "featurize.h"
#include "recog.h"

#define NEG (-1e30)

static struct { char lang[36]; int state; struct hw_model m; } cache;   /* state 1 loaded, -1 none */

const struct hw_model *hw_aligner_get(const char *lang)
{
	const char *e = getenv("GLASSINE_HANDWRITING_ALIGNER");
	if (e && !strcmp(e, "off")) return NULL;
	if (cache.state && !strcmp(cache.lang, lang)) return cache.state > 0 ? &cache.m : NULL;
	if (cache.state > 0) hw_model_free(&cache.m);
	memset(&cache, 0, sizeof cache);
	snprintf(cache.lang, sizeof cache.lang, "%s", lang);
	char p[4300], err[128];
	snprintf(p, sizeof p, "%s/%s/%s", hw_models_dir(), lang, HW_ALIGNER_FILE);
	cache.state = -1;
	if (!hw_model_load(&cache.m, p, err, sizeof err)) {
		/* a frame every two rows, the recognizer's own features */
		if (cache.m.version == HW_GHM_VERSION && cache.m.cfg.nstrided == 1) cache.state = 1;
		else { fprintf(stderr, "glassine-handwriting: %s: not an aligner\n", p); hw_model_free(&cache.m); }
	} else if (errno != ENOENT || strcmp(err, strerror(ENOENT)))
		fprintf(stderr, "glassine-handwriting: %s: %s\n", p, err);
	return cache.state > 0 ? &cache.m : NULL;
}

void hw_aligned_free(struct hw_aligned *a)
{
	if (a->frame) for (size_t j = 0; j < a->n; j++) free(a->frame[j]);
	free(a->frame);
	free(a->np);
	free(a->logp);
	memset(a, 0, sizeof *a);
}

int hw_aligner_run(const struct hw_model *m, const struct hw_ink *ink, size_t n, const unsigned char *skip,
		   struct hw_aligned *a)
{
	memset(a, 0, sizeof *a);
	a->m = m;
	a->n = n;
	struct hw_stroke *s = malloc((n + 1) * sizeof *s);
	int *idx = malloc((n + 1) * sizeof *idx);
	long *K = calloc(n + 1, sizeof *K);
	a->frame = calloc(n + 1, sizeof *a->frame);
	a->np = calloc(n + 1, sizeof *a->np);
	float *feats = NULL, *frames = NULL;
	struct hw_origin *org = NULL;
	int ret = -1;
	if (!s || !idx || !K || !a->frame || !a->np) goto out;
	size_t ns = 0;
	for (size_t j = 0; j < n; j++)
		if (ink[j].n && !(skip && skip[j])) {
			s[ns] = (struct hw_stroke){ ink[j].n, ink[j].x, ink[j].y, ink[j].t };
			idx[ns++] = (int)j;
		}
	ret = 1;
	if (!ns) goto out;
	ret = -1;
	long rows = hw_features(s, ns, &feats, &org);
	if (rows < 0) goto out;
	ret = 1;
	if (!rows) goto out;
	ret = -1;
	long T = hw_encode(m, feats, rows, &frames);
	if (T <= 0) { ret = T < 0 ? -1 : 1; goto out; }
	a->T = T;
	a->C = (int)m->cfg.nclass;
	a->logp = malloc((size_t)T * (size_t)a->C * sizeof *a->logp);
	if (!a->logp) goto out;
	hw_head_forward(&m->base, frames, T, a->logp, NULL, NULL);
	for (long r = 0; r < rows; r++) if (org[r].stroke >= 0 && (size_t)org[r].stroke < ns) K[org[r].stroke]++;
	long at = 0;
	for (size_t k = 0; k < ns; k++) {
		const struct hw_ink *q = &ink[idx[k]];
		int *f = malloc((q->n + 1) * sizeof *f);
		if (!f) goto out;
		a->frame[idx[k]] = f;
		a->np[idx[k]] = q->n;
		/* a point's row: the one nearest it along its stroke (rows evenly spaced by arc) */
		double L = 0;
		for (size_t i = 1; i < q->n; i++) L += hypot(q->x[i] - q->x[i - 1], q->y[i] - q->y[i - 1]);
		double d = 0;
		for (size_t i = 0; i < q->n; i++) {
			if (i) d += hypot(q->x[i] - q->x[i - 1], q->y[i] - q->y[i - 1]);
			long row = K[k] < 2 || !(L > 0) ? 0 : (long)floor(d * (double)(K[k] - 1) / L + 0.5);
			if (row > K[k] - 1) row = K[k] - 1;
			if (row < 0) row = 0;
			long fr = (row + at + 1) / 2;
			f[i] = (int)(fr < T - 1 ? fr : T - 1);
		}
		at += K[k];
	}
	ret = 0;
out:
	free(s); free(idx); free(K); free(feats); free(frames); free(org);
	if (ret) hw_aligned_free(a);
	return ret;
}

int hw_aligner_class(const struct hw_aligned *a, unsigned cp)
{
	return a && a->m ? hw_class_of(a->m, cp) : 0;
}

/* the chain of classes: per state its class (0 a blank) and whether it may repeat; a blank
 * between equal classes. S states, at most 2R. */
static size_t chain_of(const int *cls, size_t R, int *sc, unsigned char *rep, int *lab)
{
	size_t S = 0;
	for (size_t r = 0; r < R; r++) {
		if (r && cls[r] == cls[r - 1]) { sc[S] = 0; rep[S] = 0; lab[S] = (int)r - 1; S++; }
		sc[S] = cls[r]; rep[S] = 1; lab[S] = (int)r; S++;
	}
	return S;
}

int hw_aligner_split(const struct hw_aligned *a, long f0, long f1, const int *cls, size_t R, int *lab)
{
	long T = f1 - f0 + 1;
	if (!R || T < 1) return 1;
	int *sc = malloc(2 * R * sizeof *sc), *sl = malloc(2 * R * sizeof *sl);
	unsigned char *rep = malloc(2 * R), *back = NULL;
	double *al = malloc(4 * R * sizeof *al);
	int ret = -1;
	if (!sc || !sl || !rep || !al) goto out;
	size_t S = chain_of(cls, R, sc, rep, sl);
	ret = 1;
	if ((size_t)T < S) goto out;
	ret = -1;
	back = malloc((size_t)T * S);
	if (!back) goto out;
	double *cur = al, *nx = al + 2 * R;
	const float *lp = a->logp + (size_t)f0 * (size_t)a->C;
	for (size_t s = 0; s < S; s++) cur[s] = NEG;
	cur[0] = lp[sc[0]];
	for (long t = 1; t < T; t++) {
		const float *e = lp + (size_t)t * (size_t)a->C;
		for (size_t s = 0; s < S; s++) {
			double stay = rep[s] ? cur[s] : NEG, move = s ? cur[s - 1] : NEG;
			back[(size_t)t * S + s] = move > stay;
			nx[s] = (move > stay ? move : stay) + e[sc[s]];
		}
		double *w = cur; cur = nx; nx = w;
	}
	size_t s = S - 1;
	for (long t = T - 1; t >= 0; t--) {
		lab[t] = sc[s] ? sl[s] : -1;
		if (t && back[(size_t)t * S + s]) s--;
	}
	/* a blank frame goes with the letter after it */
	for (long t = T - 1; t >= 0; t--) if (lab[t] < 0) lab[t] = t + 1 < T ? lab[t + 1] : sl[S - 1];
	ret = 0;
out:
	free(sc); free(sl); free(rep); free(al); free(back);
	return ret;
}

static int cmpl(const void *x, const void *y)
{
	long a = *(const long *)x, b = *(const long *)y;
	return (a > b) - (a < b);
}

static int label_frames(const struct hw_aligned *a, const unsigned *cp, size_t nc, double pen, int **own);

/* (decoded over the frames that hold points only, so every letter the path gives a frame
 * gets points: a frame between two sparse points holds none) */
int hw_aligner_label(const struct hw_aligned *a, const unsigned *cp, size_t nc, double pen, int **own)
{
	if (!a || !a->logp || !nc) return 1;
	int ret = -1;
	long *map = malloc(((size_t)a->T + 1) * sizeof *map);
	struct hw_aligned b = { .m = a->m, .C = a->C, .n = a->n, .np = a->np };
	b.frame = calloc(a->n + 1, sizeof *b.frame);
	if (!map || !b.frame) goto out;
	for (long t = 0; t < a->T; t++) map[t] = -1;
	for (size_t j = 0; j < a->n; j++)
		for (size_t i = 0; a->frame[j] && i < a->np[j]; i++) map[a->frame[j][i]] = 0;
	for (long t = 0; t < a->T; t++) if (!map[t]) map[t] = b.T++;
	b.logp = malloc(((size_t)b.T * (size_t)a->C + 1) * sizeof *b.logp);
	if (!b.logp) goto out;
	for (long t = 0; t < a->T; t++)
		if (map[t] >= 0) memcpy(b.logp + (size_t)map[t] * (size_t)a->C, a->logp + (size_t)t * (size_t)a->C, (size_t)a->C * sizeof *b.logp);
	for (size_t j = 0; j < a->n; j++) {
		if (!a->frame[j]) continue;
		b.frame[j] = malloc((a->np[j] + 1) * sizeof **b.frame);
		if (!b.frame[j]) goto out;
		for (size_t i = 0; i < a->np[j]; i++) b.frame[j][i] = (int)map[a->frame[j][i]];
	}
	ret = label_frames(&b, cp, nc, pen, own);
out:
	for (size_t j = 0; b.frame && j < a->n; j++) free(b.frame[j]);
	free(b.frame); free(b.logp); free(map);
	return ret;
}

static int label_frames(const struct hw_aligned *a, const unsigned *cp, size_t nc, double pen, int **own)
{
	int ret = -1;
	int *cls = malloc((nc + 1) * sizeof *cls), *pos = malloc((nc + 1) * sizeof *pos), *grp = malloc((nc + 1) * sizeof *grp);
	int *sc = malloc((2 * nc + 1) * sizeof *sc), *sl = malloc((2 * nc + 1) * sizeof *sl);
	unsigned char *rep = malloc(2 * nc + 1);
	long *firsts = malloc((a->n + 2) * sizeof *firsts);
	double *cur = NULL, *prev = NULL, *nx = NULL, *sums = NULL;
	unsigned char *back = NULL, *dl = NULL;
	int *dlab = NULL, *lab = NULL;
	if (!cls || !pos || !grp || !sc || !sl || !rep || !firsts) goto out;
	size_t R = 0;
	int word = 0;
	for (size_t k = 0; k < nc; k++) {
		if (cp[k] == ' ') { word++; continue; }
		int c = hw_class_of(a->m, cp[k]);
		if (c <= 0) continue;
		cls[R] = c; pos[R] = (int)k; grp[R] = word; R++;
	}
	size_t nf = 0;
	for (size_t j = 0; j < a->n; j++) if (a->frame[j]) {
		firsts[nf++] = a->frame[j][0];   /* (a stroke's frames never go back: its first is its least) */
	}
	ret = 1;
	if (!R || !nf) goto out;
	ret = -1;
	qsort(firsts, nf, sizeof *firsts, cmpl);
	size_t u = 0;
	for (size_t k = 0; k < nf; k++) if (!u || firsts[k] != firsts[u - 1]) firsts[u++] = firsts[k];
	nf = u;
	firsts[nf] = a->T;
	size_t S = chain_of(cls, R, sc, rep, sl);
	long T = a->T;
	ret = 1;
	if ((size_t)T < S) goto out;
	ret = -1;
	cur = malloc(S * sizeof *cur); prev = malloc(S * sizeof *prev); nx = malloc(S * sizeof *nx);
	sums = malloc((size_t)a->C * sizeof *sums);
	back = malloc((size_t)T * S); dl = calloc(nf * S + 1, 1);
	dlab = calloc(nf * S + 1, sizeof *dlab); lab = malloc((size_t)T * sizeof *lab);
	if (!cur || !prev || !nx || !sums || !back || !dl || !dlab || !lab) goto out;
	int started = 0;
	for (size_t j = 0; j < nf; j++) {
		long f0 = firsts[j], f1 = firsts[j + 1];
		if (f1 <= f0) continue;
		long t0 = f0;
		if (!started) {
			for (size_t s = 0; s < S; s++) cur[s] = NEG;
			cur[0] = a->logp[(size_t)f0 * (size_t)a->C + (size_t)sc[0]];
			t0 = f0 + 1;
		}
		memcpy(prev, cur, S * sizeof *cur);
		for (long t = t0; t < f1; t++) {
			const float *e = a->logp + (size_t)t * (size_t)a->C;
			for (size_t s = 0; s < S; s++) {
				double stay = rep[s] ? cur[s] : NEG, move = s ? cur[s - 1] : NEG;
				back[(size_t)t * S + s] = move > stay;
				nx[s] = (move > stay ? move : stay) + e[sc[s]];
			}
			double *w = cur; cur = nx; nx = w;
		}
		if (started) {
			/* or the whole stroke one letter already passed, the chain staying where it was */
			for (int c = 0; c < a->C; c++) sums[c] = 0;
			for (long t = f0; t < f1; t++)
				for (int c = 0; c < a->C; c++) sums[c] += a->logp[(size_t)t * (size_t)a->C + (size_t)c];
			for (size_t s = 0; s < S; s++) {
				if (!sc[s] || prev[s] <= NEG / 2) continue;
				int here = sl[s], bl = -1;
				double best = NEG;
				for (int r = 0; r < here; r++) {
					if (grp[r] < grp[here] - 1) continue;
					if (sums[cls[r]] > best) { best = sums[cls[r]]; bl = r; }
				}
				if (bl >= 0 && prev[s] + best + pen > cur[s]) {
					cur[s] = prev[s] + best + pen;
					dl[j * S + s] = 1;
					dlab[j * S + s] = bl;
				}
			}
		}
		started = 1;
	}
	size_t s = S - 1;
	for (size_t j = nf; j-- > 0;) {
		long f0 = firsts[j], f1 = firsts[j + 1];
		if (f1 <= f0) continue;
		if (dl[j * S + s]) { for (long t = f0; t < f1; t++) lab[t] = dlab[j * S + s]; continue; }
		for (long t = f1 - 1; t >= f0; t--) {
			lab[t] = sc[s] ? sl[s] : -1;
			if (t > firsts[0] && back[(size_t)t * S + s]) s--;
		}
	}
	for (long t = T - 1; t >= 0; t--) if (lab[t] < 0) lab[t] = t + 1 < T ? lab[t + 1] : (int)R - 1;
	for (size_t j = 0; j < a->n; j++) {
		if (!a->frame[j] || !own[j]) continue;
		for (size_t i = 0; i < a->np[j]; i++) own[j][i] = pos[lab[a->frame[j][i]]];
	}
	ret = 0;
out:
	free(cls); free(pos); free(grp); free(sc); free(sl); free(rep); free(firsts);
	free(cur); free(prev); free(nx); free(sums); free(back); free(dl); free(dlab); free(lab);
	return ret;
}
