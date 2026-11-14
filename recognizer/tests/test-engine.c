// SPDX-License-Identifier: GPL-2.0-or-later
// Engine unit tests: features against features.py's recorded output, the inference
// against onnxruntime's on a tiny random model, CTC decoding, alignment and the loss
// gradient. Run from the recognizer directory: tests/test-engine tests/data
#include <json-c/json.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "../src/hw/ctc.h"
#include "../src/hw/featurize.h"
#include "../src/hw/model.h"
#include "../src/hw/sample.h"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__); \
	fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } } while (0)

static json_object *load(const char *dir, const char *name)
{
	char p[4096];
	if (name[0] == '/') snprintf(p, sizeof p, "%s", name);
	else snprintf(p, sizeof p, "%s/%s", dir, name);
	json_object *o = json_object_from_file(p);
	if (!o) { fprintf(stderr, "can't read %s\n", p); exit(2); }
	return o;
}

/* strokes JSON ([[x, y, t, ...], ...] per stroke) -> ink */
static size_t strokes_of(json_object *a, struct hw_ink **ink, struct hw_stroke **s)
{
	size_t n = json_object_array_length(a);
	*ink = calloc(n + 1, sizeof **ink);
	*s = calloc(n + 1, sizeof **s);
	for (size_t i = 0; i < n; i++) {
		json_object *q = json_object_array_get_idx(a, i);
		for (size_t k = 0; k < json_object_array_length(q); k++) {
			json_object *p = json_object_array_get_idx(q, k);
			hw_ink_add(&(*ink)[i], json_object_get_double(json_object_array_get_idx(p, 0)),
				   json_object_get_double(json_object_array_get_idx(p, 1)),
				   json_object_get_double(json_object_array_get_idx(p, 2)), NAN);
		}
		(*s)[i] = (struct hw_stroke){ (*ink)[i].n, (*ink)[i].x, (*ink)[i].y, (*ink)[i].t };
	}
	return n;
}

static void test_features(const char *dir, const char *file)
{
	json_object *v = load(dir, file);
	for (size_t c = 0; c < json_object_array_length(v); c++) {
		json_object *o = json_object_array_get_idx(v, c), *st, *fe, *nm;
		json_object_object_get_ex(o, "strokes", &st);
		json_object_object_get_ex(o, "features", &fe);
		json_object_object_get_ex(o, "name", &nm);
		struct hw_ink *ink; struct hw_stroke *s;
		size_t n = strokes_of(st, &ink, &s);
		float *f; struct hw_origin *org;
		long rows = hw_features(s, n, &f, &org);
		long want = (long)json_object_array_length(fe);
		CHECK(rows == want, "features '%s': %ld rows, want %ld", json_object_get_string(nm), rows, want);
		double worst = 0;
		for (long r = 0; r < rows && r < want; r++) {
			json_object *row = json_object_array_get_idx(fe, (size_t)r);
			for (int k = 0; k < HW_NFEAT; k++) {
				double d = fabs(f[r * HW_NFEAT + k] - json_object_get_double(json_object_array_get_idx(row, (size_t)k)));
				if (d > worst) worst = d;
			}
			CHECK(org[r].stroke >= 0 && (size_t)org[r].stroke < n && org[r].point >= 0 &&
			      (size_t)org[r].point < s[org[r].stroke].n, "origin out of range");
		}
		CHECK(worst <= 2e-6, "features '%s': off by %g", json_object_get_string(nm), worst);
		free(f); free(org);
		for (size_t i = 0; i < n; i++) hw_ink_free(&ink[i]);
		free(ink); free(s);
	}
	json_object_put(v);
}

static const char *b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static size_t unb64(const char *in, unsigned char *out)
{
	size_t n = 0;
	unsigned v = 0; int bits = 0;
	for (; *in && *in != '='; in++) {
		const char *p = strchr(b64, *in);
		if (!p) continue;
		v = v << 6 | (unsigned)(p - b64); bits += 6;
		if (bits >= 8) { bits -= 8; out[n++] = (unsigned char)(v >> bits); }
	}
	return n;
}

static void test_engine(const char *dir, const char *file, const char *tmp)
{
	json_object *v = load(dir, file), *g, *cases;
	int tiny = !strcmp(file, "tiny.json");
	json_object_object_get_ex(v, "ghm_b64", &g);
	json_object_object_get_ex(v, "cases", &cases);
	const char *s = json_object_get_string(g);
	unsigned char *bin = malloc(strlen(s));
	size_t n = unb64(s, bin);
	FILE *f = fopen(tmp, "wb");
	fwrite(bin, 1, n, f);
	fclose(f);
	free(bin);
	struct hw_model m;
	char err[128];
	CHECK(!hw_model_load(&m, tmp, err, sizeof err), "tiny model: %s", err);
	CHECK(!tiny || (m.cfg.nclass == 74 && m.cfg.nstrided == 3 && m.cfg.hidden == 8), "tiny model config");
	CHECK(hw_class_of(&m, 'a') == 1 && hw_class_of(&m, ' ') == 73 && hw_class_of(&m, 0xe9) == 0, "charset");
	for (size_t c = 0; c < json_object_array_length(cases); c++) {
		json_object *o = json_object_array_get_idx(cases, c), *fe, *lp;
		json_object_object_get_ex(o, "feats", &fe);
		json_object_object_get_ex(o, "logp", &lp);
		long rows = (long)json_object_array_length(fe);
		float *x = malloc((size_t)rows * HW_NFEAT * sizeof *x);
		for (long r = 0; r < rows; r++)
			for (int k = 0; k < HW_NFEAT; k++)
				x[r * HW_NFEAT + k] = (float)json_object_get_double(json_object_array_get_idx(json_object_array_get_idx(fe, (size_t)r), (size_t)k));
		float *fr;
		long T = hw_encode(&m, x, rows, &fr);
		CHECK(T == (long)json_object_array_length(lp) && T == hw_frames(&m, rows), "frames %ld", T);
		float *out = malloc((size_t)T * m.cfg.nclass * sizeof *out);
		hw_head_forward(&m.base, fr, T, out, NULL, NULL);
		double worst = 0;
		int argmax_diff = 0;
		for (long t = 0; t < T; t++) {
			json_object *row = json_object_array_get_idx(lp, (size_t)t);
			int a = 0, b = 0;
			for (uint32_t k = 0; k < m.cfg.nclass; k++) {
				double want = json_object_get_double(json_object_array_get_idx(row, k));
				double d = fabs(out[t * m.cfg.nclass + k] - want);
				if (d > worst) worst = d;
				if (out[t * m.cfg.nclass + k] > out[t * m.cfg.nclass + a]) a = (int)k;
				if (want > json_object_get_double(json_object_array_get_idx(row, (size_t)b))) b = (int)k;
			}
			argmax_diff += a != b;
		}
		CHECK(worst <= 1e-4, "engine vs onnxruntime, case %zu: off by %g", c, worst);
		CHECK(!argmax_diff, "engine vs onnxruntime, case %zu: %d frames decode differently", c, argmax_diff);
		free(x); free(fr); free(out);
	}
	hw_model_free(&m);
	json_object_put(v);
	remove(tmp);
}

/* CTC: a peaked distribution reads as its labels by greedy and beam; the loss gradient
 * matches finite differences; alignment covers each label. */
static void test_ctc(void)
{
	enum { T = 12, C = 5 };
	float lp[T * C];
	int path[T] = { 0, 1, 1, 0, 2, 0, 2, 2, 0, 3, 4, 0 };      /* reads 1 2 2 3 4 */
	for (int t = 0; t < T; t++) {
		float z[C], mx = -1e9f, s = 0;
		for (int k = 0; k < C; k++) { z[k] = (k == path[t] ? 3.0f : 0.0f) + 0.1f * (float)((t * 7 + k * 3) % 5); if (z[k] > mx) mx = z[k]; }
		for (int k = 0; k < C; k++) s += expf(z[k] - mx);
		for (int k = 0; k < C; k++) lp[t * C + k] = z[k] - mx - logf(s);
	}
	int lab[16];
	int n = hw_ctc_greedy(lp, T, C, lab, 16);
	CHECK(n == 5 && lab[0] == 1 && lab[1] == 2 && lab[2] == 2 && lab[3] == 3 && lab[4] == 4, "greedy");
	struct hw_cand cand[3];
	int nc = hw_ctc_beam(lp, T, C, 8, cand, 3);
	CHECK(nc == 3 && cand[0].n == 5 && !memcmp(cand[0].lab, lab, 5 * sizeof *lab), "beam top");
	CHECK(nc == 3 && cand[0].score >= cand[1].score && cand[1].score >= cand[2].score, "beam order");
	int want[5] = { 1, 2, 2, 3, 4 };
	double nll = hw_ctc_loss(lp, T, C, want, 5, NULL, 0);
	CHECK(fabs(-nll - cand[0].score) < 2e-2, "beam score %g is the labelling's probability %g", cand[0].score, -nll);
	long first[5], last[5], from[5], to[5];
	double ap = hw_ctc_align(lp, T, C, want, 5, first, last, from, to);
	CHECK(isfinite(ap) && first[0] == 1 && last[0] == 2 && first[1] == 4 && first[2] >= 6, "align");
	CHECK(from[0] == 0 && to[4] == T - 1, "align covers the frames");
	for (int k = 1; k < 5; k++) CHECK(from[k] == to[k - 1] + 1, "align ranges are contiguous");
	int toolong[12] = { 1, 1, 1, 1, 1, 1, 1, 1 };
	CHECK(!isfinite(hw_ctc_align(lp, T, C, toolong, 8, first, last, from, to)), "align too long");
	/* gradient w.r.t. logits by finite differences */
	float z[T * C], g[T * C] = { 0 }, lq[T * C];
	for (int i = 0; i < T * C; i++) z[i] = 0.3f * sinf((float)i);
#define LOGSM(src, dst) for (int t = 0; t < T; t++) { double mx = -1e9, s = 0; \
	for (int k = 0; k < C; k++) if (src[t * C + k] > mx) mx = src[t * C + k]; \
	for (int k = 0; k < C; k++) s += exp(src[t * C + k] - mx); \
	for (int k = 0; k < C; k++) dst[t * C + k] = (float)(src[t * C + k] - mx - log(s)); }
	LOGSM(z, lq);
	hw_ctc_loss(lq, T, C, want, 5, g, 1.0f);
	double worst = 0;
	for (int i = 0; i < T * C; i += 7) {
		float keep = z[i];
		z[i] = keep + 1e-2f; LOGSM(z, lq); double up = hw_ctc_loss(lq, T, C, want, 5, NULL, 0);
		z[i] = keep - 1e-2f; LOGSM(z, lq); double dn = hw_ctc_loss(lq, T, C, want, 5, NULL, 0);
		z[i] = keep;
		double d = fabs((up - dn) / 2e-2 - g[i]);
		if (d > worst) worst = d;
	}
	CHECK(worst < 2e-3, "CTC gradient off by %g", worst);
}

/* the head's gradient (adapter and output layer) against finite differences */
static void test_head_grad(void)
{
	struct hw_head base = { 0 }, h;
	enum { D = 6, C = 5, T = 9 };
	float out[C * D], bout[C], fr[T * D];
	for (int i = 0; i < C * D; i++) out[i] = 0.4f * sinf((float)i * 1.3f);
	for (int i = 0; i < C; i++) bout[i] = 0.1f * (float)i;
	for (int i = 0; i < T * D; i++) fr[i] = cosf((float)i * 0.7f);
	base.dim = D; base.nclass = C; base.out = out; base.bout = bout;
	CHECK(!hw_head_init(&h, &base, 3), "head init");
	for (size_t i = 0; i < 3 * D; i++) h.up[i] = 0.3f * sinf((float)i);    /* not the identity, so every part has a gradient */
	int lab[3] = { 1, 3, 3 };
	float *g = calloc(h.nparam, sizeof *g), *scratch = calloc(h.nparam, sizeof *g);
	double l0 = hw_head_grad(&h, fr, T, lab, 3, g, 1.0f);
	CHECK(isfinite(l0), "head loss");
	double worst = 0;
	for (size_t p = 0; p < h.nparam; p++) {
		float keep = h.mem[p];
		h.mem[p] = keep + 1e-2f;
		double up = hw_head_grad(&h, fr, T, lab, 3, scratch, 1.0f);
		h.mem[p] = keep - 1e-2f;
		double dn = hw_head_grad(&h, fr, T, lab, 3, scratch, 1.0f);
		h.mem[p] = keep;
		double d = fabs((up - dn) / 2e-2 - g[p]);
		if (d > worst) worst = d;
	}
	CHECK(worst < 5e-3, "head gradient off by %g", worst);
	free(g); free(scratch);
	hw_head_free(&h);
}

int main(int argc, char **argv)
{
	const char *dir = argc > 1 ? argv[1] : "tests/data";
	char tmp[] = "/tmp/hw-test-XXXXXX";
	int fd = mkstemp(tmp);
	if (fd < 0) return 2;
	close(fd);
	if (argc > 2 && !strcmp(argv[1], "--engine")) {       /* a trained model against onnxruntime */
		test_engine(".", argv[2], tmp);
		if (fails) { fprintf(stderr, "test-engine: %d failed\n", fails); return 1; }
		printf("test-engine: engine ok\n");
		return 0;
	}
	if (argc > 2 && !strcmp(argv[1], "--features")) {     /* a vector from the trainer's features.py */
		test_features(".", argv[2]);
		if (fails) { fprintf(stderr, "test-engine: %d failed\n", fails); return 1; }
		printf("test-engine: features ok\n");
		return 0;
	}
	test_features(dir, "features.json");
	test_engine(dir, "tiny.json", tmp);
	test_ctc();
	test_head_grad();
	if (fails) { fprintf(stderr, "test-engine: %d failed\n", fails); return 1; }
	printf("test-engine: ok\n");
	return 0;
}
