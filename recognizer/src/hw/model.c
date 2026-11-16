// SPDX-License-Identifier: GPL-2.0-or-later
// The engine: loads a .ghm base model and runs it in plain C (float32), the same graph as
// the ONNX export of train/src/glassine_train/model.py (Single): scale, Conv1d + GELU,
// strided Conv1d + GELU, bidirectional LSTM layers (ONNX gate order i o f c), linear,
// log-softmax.
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include "ctc.h"
#include "featurize.h"
#include "model.h"

#define HDR_WORDS 16     /* magic, version, header bytes, 8 config words, charset bytes, trained until (2), 2 reserved */

uint64_t hw_hash(const void *p, size_t n, uint64_t h)
{
	const unsigned char *b = p;
	if (!h) h = 1469598103934665603ULL;
	for (size_t i = 0; i < n; i++) { h ^= b[i]; h *= 1099511628211ULL; }
	return h;
}

static int utf8_next(const unsigned char **s, const unsigned char *end, uint32_t *cp)
{
	const unsigned char *p = *s;
	if (p >= end) return -1;
	uint32_t c = *p++;
	int extra = c < 0x80 ? 0 : (c >> 5) == 6 ? 1 : (c >> 4) == 14 ? 2 : (c >> 3) == 30 ? 3 : -1;
	if (extra < 0) return -1;
	if (extra) c &= 0x3f >> extra;
	for (int i = 0; i < extra; i++) {
		if (p >= end || (*p & 0xc0) != 0x80) return -1;
		c = c << 6 | (*p++ & 0x3f);
	}
	*s = p;
	*cp = c;
	return 0;
}

int hw_model_load(struct hw_model *m, const char *path, char *err, size_t errlen)
{
	memset(m, 0, sizeof *m);
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0) { snprintf(err, errlen, "%s", strerror(errno)); return -1; }
	struct stat st;
	if (fstat(fd, &st) || st.st_size < HDR_WORDS * 4) { close(fd); snprintf(err, errlen, "too short"); return -1; }
	size_t len = (size_t)st.st_size;
	unsigned char *buf = malloc(len);
	size_t got = 0;
	while (buf && got < len) {
		ssize_t r = read(fd, buf + got, len - got);
		if (r <= 0) break;
		got += (size_t)r;
	}
	close(fd);
	if (!buf || got != len) { free(buf); snprintf(err, errlen, "can't read"); return -1; }
	uint32_t w[HDR_WORDS];
	memcpy(w, buf, sizeof w);
	struct hw_cfg *c = &m->cfg;
	if (memcmp(buf, HW_GHM_MAGIC, 4)) { snprintf(err, errlen, "not a model"); goto bad; }
	if (w[1] != HW_GHM_VERSION && w[1] != HW_GHM_VERSION_REF) { snprintf(err, errlen, "model version %u", w[1]); goto bad; }
	*c = (struct hw_cfg){ w[3], w[4], w[5], w[6], w[7], w[8], w[9], w[10] };
	m->version = w[1];
	/* the features each version reads */
	if (c->nfeat != (m->version == HW_GHM_VERSION_REF ? HW_NFEAT_REF : HW_NFEAT)) {
		snprintf(err, errlen, "%u features in a version %u model", c->nfeat, m->version); goto bad;
	}
	uint32_t hdr = w[2], cs = w[11];
	m->trained_until = (int64_t)((uint64_t)w[12] | (uint64_t)w[13] << 32);
	if (hdr != HDR_WORDS * 4 || c->nfeat == 0 || c->nfeat > 64 || c->c1 == 0 || c->c1 > 4096 ||
	    c->conv == 0 || c->conv > 4096 || c->nstrided > 8 || c->kernel == 0 || c->kernel > 31 ||
	    c->kernel % 2 == 0 || c->hidden == 0 || c->hidden > 4096 || c->layers == 0 || c->layers > 8 ||
	    c->nclass < 2 || c->nclass > HW_MAX_CLASSES || cs > len) {
		snprintf(err, errlen, "bad header"); goto bad;
	}
	const unsigned char *p = buf + hdr, *end = p + cs;
	if ((size_t)(end - buf) > len) { snprintf(err, errlen, "bad header"); goto bad; }
	for (uint32_t k = 0; k + 1 < c->nclass; k++)
		if (utf8_next(&p, end, &m->charset[k])) { snprintf(err, errlen, "bad charset"); goto bad; }
	if (p != end) { snprintf(err, errlen, "charset is not nclass - 1 characters"); goto bad; }
	size_t off = (hdr + cs + 3) / 4 * 4;
	size_t H = c->hidden, K = c->kernel;
	size_t need = c->nfeat + (size_t)c->c1 * c->nfeat * K + c->c1;
	for (uint32_t i = 0; i < c->nstrided; i++) need += (size_t)c->conv * (i ? c->conv : c->c1) * K + c->conv;
	size_t in = c->nstrided ? c->conv : c->c1;
	for (uint32_t l = 0; l < c->layers; l++) {
		size_t I = l ? 2 * H : in;
		need += 2 * (4 * H * I + 4 * H * H + 4 * H);
	}
	need += (size_t)c->nclass * 2 * H + c->nclass;
	if (off > len || (len - off) / 4 != need || (len - off) % 4) { snprintf(err, errlen, "size doesn't match the header"); goto bad; }
	m->ndata = need;
	m->data = malloc(need * sizeof(float));
	if (!m->data) { snprintf(err, errlen, "out of memory"); goto bad; }
	memcpy(m->data, buf + off, need * sizeof(float));     /* little endian on every target we have */
	m->hash = hw_hash(buf, len, 0);
	free(buf);
	const float *f = m->data;
	m->scale = f; f += c->nfeat;
	m->c1w = f; f += (size_t)c->c1 * c->nfeat * K;
	m->c1b = f; f += c->c1;
	for (uint32_t i = 0; i < c->nstrided; i++) {
		m->sw[i] = f; f += (size_t)c->conv * (i ? c->conv : c->c1) * K;
		m->sb[i] = f; f += c->conv;
	}
	for (uint32_t l = 0; l < c->layers; l++) {
		size_t I = l ? 2 * H : in;
		for (int d = 0; d < 2; d++) {
			m->W[l][d] = f; f += 4 * H * I;
			m->R[l][d] = f; f += 4 * H * H;
			m->B[l][d] = f; f += 4 * H;
		}
	}
	/* convolution weights to [out][tap][in], for contiguous dot products */
	for (uint32_t i = 0; i <= c->nstrided; i++) {
		float *w = (float *)(i ? m->sw[i - 1] : m->c1w);
		size_t co = i ? c->conv : c->c1, ci = i == 0 ? c->nfeat : i == 1 ? c->c1 : c->conv;
		float *t = malloc(ci * K * sizeof *t);
		if (!t) { snprintf(err, errlen, "out of memory"); hw_model_free(m); return -1; }
		for (size_t o = 0; o < co; o++) {
			float *wo = w + o * ci * K;
			for (size_t a = 0; a < ci; a++)
				for (size_t k = 0; k < K; k++) t[k * ci + a] = wo[a * K + k];
			memcpy(wo, t, ci * K * sizeof *t);
		}
		free(t);
	}
	struct hw_head *b = &m->base;
	b->dim = 2 * c->hidden; b->nclass = c->nclass; b->rank = 0;
	b->out = (float *)f; f += (size_t)c->nclass * 2 * H;
	b->bout = (float *)f;
	b->nparam = (size_t)c->nclass * (2 * H + 1);
	return 0;
bad:
	free(buf);
	return -1;
}

void hw_model_free(struct hw_model *m)
{
	free(m->data);
	memset(m, 0, sizeof *m);
}

int hw_class_of(const struct hw_model *m, uint32_t cp)
{
	for (uint32_t k = 0; k + 1 < m->cfg.nclass; k++) if (m->charset[k] == cp) return (int)k + 1;
	return 0;
}

long hw_frames(const struct hw_model *m, long rows)
{
	for (uint32_t i = 0; i < m->cfg.nstrided; i++) rows = (rows - 1) / 2 + 1;
	return rows;
}

/* a dot product in 8 independent sums: the compiler turns it into vector code */
static float dot(const float *a, const float *b, size_t n)
{
	float acc[8] = { 0 };
	size_t i = 0;
	for (; i + 8 <= n; i += 8)
		for (int k = 0; k < 8; k++) acc[k] += a[i + k] * b[i + k];
	float s = ((acc[0] + acc[4]) + (acc[1] + acc[5])) + ((acc[2] + acc[6]) + (acc[3] + acc[7]));
	for (; i < n; i++) s += a[i] * b[i];
	return s;
}

/* four rows times one vector: the vector is read once for all four */
static void dot4(const float *a, size_t stride, const float *b, size_t n, float *out)
{
	float acc[4][8] = { { 0 } };
	const float *a0 = a, *a1 = a + stride, *a2 = a + 2 * stride, *a3 = a + 3 * stride;
	size_t i = 0;
	for (; i + 8 <= n; i += 8)
		for (int k = 0; k < 8; k++) {
			float x = b[i + k];
			acc[0][k] += a0[i + k] * x;
			acc[1][k] += a1[i + k] * x;
			acc[2][k] += a2[i + k] * x;
			acc[3][k] += a3[i + k] * x;
		}
	for (int r = 0; r < 4; r++) {
		const float *q = acc[r];
		float s = ((q[0] + q[4]) + (q[1] + q[5])) + ((q[2] + q[6]) + (q[3] + q[7]));
		for (size_t j = i; j < n; j++) s += a[r * stride + j] * b[j];
		out[r] = s;
	}
}

/* y[r] = rows of a (stride n) times b, r < m */
static void matvec(const float *a, const float *b, size_t m, size_t n, float *y)
{
	size_t r = 0;
	for (; r + 4 <= m; r += 4) dot4(a + r * n, n, b, n, y + r);
	for (; r < m; r++) y[r] = dot(a + r * n, b, n);
}

static float gelu(float x)
{
	/* ONNX export: x * (erf(x / sqrt 2) + 1) * 0.5 */
	return x * (erff(x / 1.41421354f) + 1.0f) * 0.5f;
}

/* in (L x cin), time-major -> out (Lo x cout); w as [cout][K][cin] (reordered at load),
 * stride s, padding K/2 */
static void conv(const float *in, size_t cin, long L, const float *w, const float *b, size_t cout,
		 size_t K, int s, float *out, long Lo)
{
	long pad = (long)K / 2;
	for (long t = 0; t < Lo; t++) {
		float *y = out + (size_t)t * cout;
		long k0 = 0, k1 = (long)K;
		long base = t * s - pad;
		if (base < 0) k0 = -base;
		if (base + (long)K > L) k1 = L - base;
		if (k0 == 0 && k1 == (long)K) {
			/* the taps inside the line are contiguous in time-major input */
			matvec(w, in + (size_t)base * cin, cout, K * cin, y);
			for (size_t o = 0; o < cout; o++) y[o] = gelu(y[o] + b[o]);
			continue;
		}
		for (size_t o = 0; o < cout; o++) {
			const float *wo = w + o * K * cin;
			float a = b[o];
			if (k1 > k0) a += dot(wo + (size_t)k0 * cin, in + (size_t)(base + k0) * cin, (size_t)(k1 - k0) * cin);
			y[o] = gelu(a);
		}
	}
}

static float sigm(float x) { return 1.0f / (1.0f + expf(-x)); }

/* one LSTM layer, both directions: x (T x I) -> y (T x 2H) */
static int lstm(const struct hw_model *m, int l, const float *x, long T, size_t I, float *y)
{
	size_t H = m->cfg.hidden;
	float *g = malloc((size_t)T * 4 * H * sizeof *g);
	float *h = calloc(2 * H, sizeof *h), *c = h + H;
	float *z = malloc(4 * H * sizeof *z);
	if (!g || !h || !z) { free(g); free(h); free(z); return -1; }
	for (int d = 0; d < 2; d++) {
		const float *W = m->W[l][d], *R = m->R[l][d], *B = m->B[l][d];
		for (long t = 0; t < T; t++) {
			const float *xt = x + (size_t)t * I;
			float *gt = g + (size_t)t * 4 * H;
			matvec(W, xt, 4 * H, I, gt);
			for (size_t r = 0; r < 4 * H; r++) gt[r] += B[r];
		}
		memset(h, 0, 2 * H * sizeof *h);
		for (long k = 0; k < T; k++) {
			long t = d ? T - 1 - k : k;
			const float *gt = g + (size_t)t * 4 * H;
			matvec(R, h, 4 * H, H, z);
			for (size_t r = 0; r < 4 * H; r++) z[r] += gt[r];
			float *yt = y + (size_t)t * 2 * H + (size_t)d * H;
			for (size_t j = 0; j < H; j++) {
				float i_ = sigm(z[j]), o_ = sigm(z[H + j]), f_ = sigm(z[2 * H + j]), c_ = tanhf(z[3 * H + j]);
				c[j] = f_ * c[j] + i_ * c_;
				h[j] = o_ * tanhf(c[j]);
				yt[j] = h[j];
			}
		}
	}
	free(g); free(h); free(z);
	return 0;
}

long hw_encode(const struct hw_model *m, const float *feats, long rows, float **frames)
{
	const struct hw_cfg *c = &m->cfg;
	*frames = NULL;
	if (rows <= 0) return -1;
	size_t F = c->nfeat, H = c->hidden;
	long L = rows;
	float *x = malloc((size_t)L * F * sizeof *x);
	float *a = malloc((size_t)L * c->c1 * sizeof *a);
	if (!x || !a) { free(x); free(a); return -1; }
	for (long t = 0; t < L; t++)
		for (size_t i = 0; i < F; i++) x[t * F + i] = feats[t * F + i] * m->scale[i];
	conv(x, F, L, m->c1w, m->c1b, c->c1, c->kernel, 1, a, L);
	free(x);
	size_t cin = c->c1;
	for (uint32_t s = 0; s < c->nstrided; s++) {
		long Lo = (L - 1) / 2 + 1;
		float *b = malloc((size_t)Lo * c->conv * sizeof *b);
		if (!b) { free(a); return -1; }
		conv(a, cin, L, m->sw[s], m->sb[s], c->conv, c->kernel, 2, b, Lo);
		free(a);
		a = b; L = Lo; cin = c->conv;
	}
	float *in = a;
	size_t I = cin;
	for (uint32_t l = 0; l < c->layers; l++) {
		float *y = malloc((size_t)L * 2 * H * sizeof *y);
		if (!y || lstm(m, (int)l, in, L, I, y)) { free(y); free(in); return -1; }
		free(in);
		in = y; I = 2 * H;
	}
	*frames = in;
	return L;
}

int hw_head_init(struct hw_head *h, const struct hw_head *from, uint32_t rank)
{
	memset(h, 0, sizeof *h);
	h->dim = from->dim; h->nclass = from->nclass; h->rank = rank;
	size_t D = h->dim, C = h->nclass, r = rank;
	h->nparam = 2 * r * D + r + D + C * D + C;
	h->mem = calloc(h->nparam, sizeof(float));
	if (!h->mem) return -1;
	float *f = h->mem;
	h->down = f; f += r * D;
	h->bdown = f; f += r;
	h->up = f; f += D * r;
	h->bup = f; f += D;
	h->out = f; f += C * D;
	h->bout = f;
	memcpy(h->out, from->out, C * D * sizeof(float));
	memcpy(h->bout, from->bout, C * sizeof(float));
	/* the adapter's down projection as torch initialises a Linear (uniform, 1/sqrt(dim)),
	 * deterministic; up is zero, so the head starts as the one it came from */
	uint64_t seed = 0x9E3779B97F4A7C15ULL;
	float bound = 1.0f / sqrtf((float)D);
	for (size_t i = 0; i < r * D + r; i++) {
		seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
		h->down[i] = bound * (2.0f * (float)(seed >> 40) / (float)(1ULL << 24) - 1.0f);
	}
	if (rank && from->rank == rank) {
		memcpy(h->down, from->down, r * D * sizeof(float));
		memcpy(h->bdown, from->bdown, r * sizeof(float));
		memcpy(h->up, from->up, D * r * sizeof(float));
		memcpy(h->bup, from->bup, D * sizeof(float));
	}
	if (!rank) h->bup = NULL;
	return 0;
}

void hw_head_free(struct hw_head *h)
{
	free(h->mem);
	memset(h, 0, sizeof *h);
}

void hw_head_forward(const struct hw_head *h, const float *frames, long T, float *logp, float *act, float *adapted)
{
	size_t D = h->dim, C = h->nclass, r = h->rank;
	float *tmp = NULL;
	if (r && !adapted) adapted = tmp = malloc(D * sizeof *tmp);
	for (long t = 0; t < T; t++) {
		const float *x = frames + (size_t)t * D;
		if (r) {
			float *ad = tmp ? tmp : adapted + (size_t)t * D;
			float a[256], *ac = act ? act + (size_t)t * r : a;
			for (size_t k = 0; k < r && k < (act ? r : 256); k++) {
				float s = h->bdown[k];
				for (size_t i = 0; i < D; i++) s += h->down[k * D + i] * x[i];
				ac[k] = s > 0 ? s : 0;
			}
			for (size_t i = 0; i < D; i++) {
				float s = h->bup[i];
				for (size_t k = 0; k < r; k++) s += h->up[i * r + k] * ac[k];
				ad[i] = x[i] + s;
			}
			x = ad;
		}
		float *y = logp + (size_t)t * C, mx = -INFINITY;
		for (size_t k = 0; k < C; k++) {
			float s = 0;
			const float *w = h->out + k * D;
			for (size_t i = 0; i < D; i++) s += w[i] * x[i];
			y[k] = s + h->bout[k];
			if (y[k] > mx) mx = y[k];
		}
		float sum = 0;
		for (size_t k = 0; k < C; k++) sum += expf(y[k] - mx);
		float lse = mx + logf(sum);
		for (size_t k = 0; k < C; k++) y[k] -= lse;
	}
	free(tmp);
}

double hw_head_grad(const struct hw_head *h, const float *frames, long T, const int *lab, int L, float *grad, float weight)
{
	size_t D = h->dim, C = h->nclass, r = h->rank;
	float *gd = grad, *gbd = gd + r * D, *gu = gbd + r, *gbu = gu + D * r, *go = gbu + D, *gbo = go + C * D;
	float *lp = malloc((size_t)T * C * sizeof *lp), *g = calloc((size_t)T * C, sizeof *g);
	float *act = r ? malloc((size_t)T * r * sizeof *act) : NULL;
	float *ad = r ? malloc((size_t)T * D * sizeof *ad) : NULL;
	float *dad = malloc(D * sizeof *dad), *dact = malloc((r + 1) * sizeof *dact);
	double loss = INFINITY;
	if (!lp || !g || (r && (!act || !ad)) || !dad || !dact) goto out;
	hw_head_forward(h, frames, T, lp, act, ad);
	loss = hw_ctc_loss(lp, T, (int)C, lab, L, g, weight);
	if (!isfinite(loss)) goto out;
	for (long t = 0; t < T; t++) {
		const float *gt = g + (size_t)t * C;
		const float *in = r ? ad + (size_t)t * D : frames + (size_t)t * D;
		for (size_t k = 0; k < C; k++) {
			float gk = gt[k];
			gbo[k] += gk;
			float *gw = go + k * D;
			for (size_t i = 0; i < D; i++) gw[i] += gk * in[i];
		}
		if (!r) continue;
		for (size_t i = 0; i < D; i++) dad[i] = 0;
		for (size_t k = 0; k < C; k++) {
			float gk = gt[k];
			const float *w = h->out + k * D;
			for (size_t i = 0; i < D; i++) dad[i] += w[i] * gk;
		}
		const float *at = act + (size_t)t * r, *xt = frames + (size_t)t * D;
		for (size_t j = 0; j < r; j++) dact[j] = 0;
		for (size_t i = 0; i < D; i++) {
			gbu[i] += dad[i];
			for (size_t j = 0; j < r; j++) {
				gu[i * r + j] += dad[i] * at[j];
				dact[j] += h->up[i * r + j] * dad[i];
			}
		}
		for (size_t j = 0; j < r; j++) {
			if (at[j] <= 0) continue;
			gbd[j] += dact[j];
			for (size_t i = 0; i < D; i++) gd[j * D + i] += dact[j] * xt[i];
		}
	}
out:
	free(lp); free(g); free(act); free(ad); free(dad); free(dact);
	return loss;
}
