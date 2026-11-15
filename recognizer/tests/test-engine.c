// SPDX-License-Identifier: GPL-2.0-or-later
// Engine unit tests: features against features.py's recorded output, the inference
// against onnxruntime's on a tiny random model, CTC decoding, alignment and the loss
// gradient. Run from the recognizer directory: tests/test-engine tests/data
#include <json-c/json.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "../src/hw/ctc.h"
#include "../src/hw/featurize.h"
#include "../src/hw/model.h"
#include "../src/hw/recog.h"
#include "../src/hw/sample.h"
#include "../src/hw/words.h"

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

/* ---------------------------------------------------------------- words */

/* a predictor model (format 1) with these words, sorted, unigram q each; bigram
 * successors for one context word */
static int write_gpm(const char *path, const char **words, const unsigned char *q, int n, int ctx, int succ,
		     unsigned char sq)
{
	uint32_t h[25] = { 0 };
	memcpy(h, "GLPM", 4);
	h[1] = 1;
	memcpy(&h[2], "en", 2);
	char str[1024];
	uint32_t len = 0, keys[32];
	for (int i = 0; i < n; i++) { keys[i] = len; strcpy(str + len, words[i]); len += (uint32_t)strlen(words[i]) + 1; }
	while (len % 4) str[len++] = 0;
	uint32_t off = sizeof h;
	h[4] = (uint32_t)n;
	h[5] = off; off += (uint32_t)n * 12;                     /* vocab */
	h[6] = off; h[7] = len; off += len;                      /* strings */
	h[8] = off; off += (uint32_t)(n + 2) * 4;                /* bi */
	h[9] = off; h[10] = 1; off += 4;                         /* bisucc */
	h[11] = off; h[12] = 0;                                  /* tri */
	h[13] = off; h[14] = 0;
	h[15] = off; h[16] = 0;
	FILE *f = fopen(path, "wb");
	if (!f) return -1;
	fwrite(h, sizeof h, 1, f);
	for (int i = 0; i < n; i++) {
		uint32_t w[3] = { keys[i], keys[i], q[i] };
		fwrite(w, sizeof w, 1, f);
	}
	fwrite(str, len, 1, f);
	for (int i = 0; i < n + 2; i++) { uint32_t b = i > ctx ? 1 : 0; fwrite(&b, 4, 1, f); }
	uint32_t e = (uint32_t)succ << 8 | sq;
	fwrite(&e, 4, 1, f);
	return fclose(f);
}

/* frames that read text: each character a peak of `peak` (the rest share what's left),
 * blanks between; alt: at character alt_at, the second choice alt_cp with alt_lp */
static void frames_of(const struct hw_lang *l, const char *text, int alt_at, unsigned alt_cp, float alt_lp, float *lp,
		      long *T)
{
	int C = (int)l->model.cfg.nclass;
	long t = 0;
	for (int i = 0; text[i]; i++) {
		for (int r = 0; r < 2; r++, t++) {
			int want = r ? 0 : hw_class_of(&l->model, (unsigned char)text[i]);
			float *row = lp + t * C;
			for (int k = 0; k < C; k++) row[k] = -12.0f;
			row[want] = -0.05f;
			if (!r && i == alt_at) { row[want] = -0.4f; row[hw_class_of(&l->model, alt_cp)] = alt_lp; }
		}
	}
	*T = t;
}

static void reading_text(const struct hw_reading *rd, int i, char *o)
{
	int k = 0;
	for (; k < rd->c[i].len; k++) o[k] = (char)rd->c[i].cp[k];
	o[k] = 0;
}

static void test_words(const char *tmp)
{
	char dir[256], path[300], data[300];
	snprintf(dir, sizeof dir, "%s.d", tmp);
	mkdir(dir, 0700);
	snprintf(path, sizeof path, "%s/en.gpm", dir);
	/* sorted by folded key; "the" -> "jacket" a likely pair */
	const char *words[] = { "a", "black", "jacket", "the", "with" };
	const unsigned char q[] = { 40, 90, 110, 30, 50 };
	CHECK(!write_gpm(path, words, q, 5, 3, 2, 8), "test model written");
	snprintf(data, sizeof data, "%s/data", dir);
	setenv("GLASSINE_PREDICT_MODELS", dir, 1);
	setenv("XDG_DATA_HOME", data, 1);
	hw_words_forget_all();
	const struct hw_words *w = hw_words_get("en");
	CHECK(w != NULL, "words loaded");
	if (!w) return;
	int known;
	CHECK(hw_words_find(w, "jacket", &known) == 2 && known, "a word of the model");
	CHECK(hw_words_find(w, "jaciet", &known) < 0 && !known, "not a word");
	CHECK(hw_words_get("en-GB") != NULL, "a regional language uses the base one's words");
	CHECK(hw_words_get("de") == NULL, "no words for a language without a model");

	struct hw_lang l;
	memset(&l, 0, sizeof l);
	const char *cs = "abcdefghijklmnopqrstuvwxyz '";
	for (int k = 0; cs[k]; k++) l.model.charset[k] = (unsigned char)cs[k];
	l.model.cfg.nclass = (uint32_t)strlen(cs) + 1;
	static float lp[64 * 32];
	struct hw_run r;
	memset(&r, 0, sizeof r);
	r.C = (int)l.model.cfg.nclass;
	r.logp = lp;
	struct hw_context x;
	struct hw_reading rd;
	char t0[64], t1[64];

	/* the ink reads "jaciet", "jacket" close behind: the word wins, the ink's best second */
	frames_of(&l, "jaciet", 3, 'k', -1.2f, lp, &r.T);
	hw_context_of(w, "", &x);
	hw_read_words(&l, &r, &x, 3, &rd);
	reading_text(&rd, 0, t0); reading_text(&rd, 1, t1);
	CHECK(rd.n == 3 && !strcmp(t0, "jacket") && rd.c[0].from == HW_FROM_BEAM, "a likely word first (%s)", t0);
	CHECK(!strcmp(t1, "jaciet") && rd.c[1].from == HW_FROM_INK, "the ink's best second (%s)", t1);
	CHECK(rd.c[0].score < rd.c[1].score, "scores stay the ink's");
	/* without words, or raw: the ink's order */
	hw_read_words(&l, &r, NULL, 3, &rd);
	reading_text(&rd, 0, t0);
	CHECK(!strcmp(t0, "jaciet") && rd.c[0].from == HW_FROM_INK, "no words: the ink's best first");

	/* a word one edit away that the ink reads only far behind (ten other letters there
	 * first): found by the guided search (or offered as a near word), not first */
	frames_of(&l, "jaciet", 3, 'k', -5.5f, lp, &r.T);
	for (const char *o = "lbdfhmnrtv"; *o; o++) lp[6 * r.C + hw_class_of(&l.model, (unsigned char)*o)] = -1.5f;
	hw_read_words(&l, &r, &x, 3, &rd);
	reading_text(&rd, 0, t0);
	int offered = 0;
	for (int i = 0; i < rd.n; i++) {
		reading_text(&rd, i, t1);
		if (!strcmp(t1, "jacket")) offered = (rd.c[i].from == HW_FROM_WORDS || rd.c[i].from == HW_FROM_GUIDED) && i > 0;
	}
	CHECK(!strcmp(t0, "jaciet") && rd.c[0].from == HW_FROM_INK, "the ink's best stays first (%s)", t0);
	CHECK(offered, "the near word offered beside it");

	/* the ink's best all words: never replaced */
	frames_of(&l, "with", 0, 'y', -0.6f, lp, &r.T);
	hw_read_words(&l, &r, &x, 3, &rd);
	reading_text(&rd, 0, t0);
	CHECK(!strcmp(t0, "with"), "a reading of words stays first (%s)", t0);

	/* learned and removed words from the predictor's store */
	char sd[400], sf[420];
	snprintf(sd, sizeof sd, "%s/glassine", data); mkdir(data, 0700); mkdir(sd, 0700);
	snprintf(sd, sizeof sd, "%s/glassine/predict", data); mkdir(sd, 0700);
	snprintf(sf, sizeof sf, "%s/words", sd);
	FILE *f = fopen(sf, "w");
	if (f) { fputs("glassine-words 1 2\nw en zork 3 1\nx * jacket\n", f); fclose(f); }
	w = hw_words_get("en");
	CHECK(w && hw_words_find(w, "zork", &known) < 0 && known, "a learned word is known");
	CHECK(w && (hw_words_find(w, "jacket", &known), !known), "a removed word isn't known");
	unlink(sf); rmdir(sd);
	snprintf(sd, sizeof sd, "%s/glassine", data); rmdir(sd); rmdir(data);
	unlink(path); rmdir(dir);
	hw_words_forget_all();
	unsetenv("GLASSINE_PREDICT_MODELS");
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
	test_words(tmp);
	if (fails) { fprintf(stderr, "test-engine: %d failed\n", fails); return 1; }
	printf("test-engine: ok\n");
	return 0;
}
