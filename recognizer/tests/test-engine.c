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
#include "../src/hw/aligner.h"
#include "../src/hw/chars.h"
#include "../src/hw/cut.h"
#include "../src/hw/ctc.h"
#include "../src/hw/featurize.h"
#include "../src/hw/letters.h"
#include "../src/hw/lookalike.h"
#include "../src/hw/calib.h"
#include "../src/hw/store.h"
#include "../src/hw/model.h"
#include "../src/hw/neaten.h"
#include "../src/hw/recog.h"
#include "../src/hw/sample.h"
#include "../src/hw/size.h"
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
		json_object *o = json_object_array_get_idx(v, c), *st, *fe, *nm, *hj, *bj;
		json_object_object_get_ex(o, "strokes", &st);
		json_object_object_get_ex(o, "features", &fe);
		json_object_object_get_ex(o, "name", &nm);
		struct hw_ink *ink; struct hw_stroke *s;
		size_t n = strokes_of(st, &ink, &s);
		float *f; struct hw_origin *org;
		/* "h" (and "base", null: none): the reference-height features */
		int ref = json_object_object_get_ex(o, "h", &hj);
		int nf = ref ? HW_NFEAT_REF : HW_NFEAT;
		double base = ref && json_object_object_get_ex(o, "base", &bj) && bj ? json_object_get_double(bj) : NAN;
		long rows = ref ? hw_features_ref(s, n, json_object_get_double(hj), base, &f, &org) : hw_features(s, n, &f, &org);
		long want = (long)json_object_array_length(fe);
		CHECK(rows == want, "features '%s': %ld rows, want %ld", json_object_get_string(nm), rows, want);
		double worst = 0;
		for (long r = 0; r < rows && r < want; r++) {
			json_object *row = json_object_array_get_idx(fe, (size_t)r);
			CHECK((int)json_object_array_length(row) == nf, "features '%s': %d columns", json_object_get_string(nm), nf);
			for (int k = 0; k < nf; k++) {
				double d = fabs(f[r * nf + k] - json_object_get_double(json_object_array_get_idx(row, (size_t)k)));
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

/* ---------------------------------------------------------------- size (FR-017) */

/* a stroke: a circle (r > 0) or a line from (x, y) to (x2, y2), in 24 points */
static void stroke_at(struct hw_ink *k, struct hw_stroke *s, double x, double y, double r, double x2, double y2)
{
	memset(k, 0, sizeof *k);
	for (int i = 0; i < 24; i++) {
		double a = 2 * M_PI * i / 23, u = i / 23.0;
		if (r > 0) hw_ink_add(k, x + r * cos(a), y + r * sin(a), i * 0.01, NAN);
		else hw_ink_add(k, x + (x2 - x) * u, y + (y2 - y) * u, i * 0.01, NAN);
	}
	*s = (struct hw_stroke){ k->n, k->x, k->y, k->t };
}

/* a run of 6 frames for ns strokes (each frame from stroke frame * ns / 6): blanks around two
 * frames whose classes have the given log-probabilities (the rest -12, the blank -3) */
static void run_of(const struct hw_lang *l, struct hw_run *r, size_t ns, const char *cps, const float *v)
{
	hw_run_free(r);
	r->C = (int)l->model.cfg.nclass;
	r->T = r->rows = 6;
	r->down = 1;
	r->logp = malloc(6 * (size_t)r->C * sizeof *r->logp);
	r->org = calloc(6, sizeof *r->org);
	for (int f = 0; f < 6; f++) {
		float *row = r->logp + (size_t)f * r->C;
		r->org[f].stroke = (int)((size_t)f * ns / 6);
		for (int c = 0; c < r->C; c++) row[c] = -12;
		if (f == 2 || f == 3) {
			row[0] = -3;
			for (int i = 0; cps[i]; i++) row[hw_class_of(&l->model, (unsigned char)cps[i])] = v[i];
		} else {
			row[0] = -0.01f;
		}
	}
}

static char first_of(const struct hw_lang *l, struct hw_run *r, int n, char *all)
{
	struct hw_reading rd;
	hw_read(l, r, n, &rd);
	all[0] = 0;
	for (int i = 0; i < rd.n; i++) {
		size_t k = strlen(all);
		for (int j = 0; j < rd.c[i].len && k < 60; j++) all[k++] = (char)rd.c[i].cp[j];
		all[k++] = '|'; all[k] = 0;
	}
	return rd.n && rd.c[0].len == 1 ? (char)rd.c[0].cp[0] : 0;
}

static void test_size(void)
{
	struct hw_lang l;
	memset(&l, 0, sizeof l);
	const char *cs = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,;:!?'-() ";
	for (int k = 0; cs[k]; k++) l.model.charset[k] = (unsigned char)cs[k];
	l.model.cfg.nclass = (uint32_t)strlen(cs) + 1;
	/* the person's letters: x-height 10, capitals 25, alone 14 and 35 */
	struct hw_size_ref ref = { 0 };
	ref.kind = HW_SIZE_PERSON; ref.x = 10; ref.c = 25; ref.xs = 14; ref.cs = 35;
	struct hw_ink k[4];
	struct hw_stroke s[4];
	struct hw_run r;
	memset(&r, 0, sizeof r);
	char all[64], t;

	CHECK(hw_glyph_kind(2, 2, &ref) == HW_GLYPH_DOT && hw_glyph_kind(1, 10, &ref) == HW_GLYPH_FLAT &&
	      hw_glyph_kind(7, 1, &ref) == HW_GLYPH_THIN && hw_glyph_kind(35, 4, &ref) == HW_GLYPH_BIG &&
	      hw_glyph_kind(14, 14, &ref) == HW_GLYPH_BIG, "glyph kinds by size");

	/* a dot at its size: the model, seeing it blown up, says L or O */
	stroke_at(&k[0], &s[0], 50, 50, 1, 0, 0);
	run_of(&l, &r, 1, "LO.,", (const float[]){ -0.1f, -1, -9, -10 });
	hw_size_weigh(&l, &r, s, 1, &ref);
	t = first_of(&l, &r, 3, all);
	CHECK(t == '.', "a dot reads as a dot (%s)", all);
	CHECK(r.logp0 && r.size && r.size->lone_mark == HW_GLYPH_DOT, "the ink's own reading kept beside");
	/* whatever the model says: no letter first for a mark */
	run_of(&l, &r, 1, "L.,'-", (const float[]){ -0.05f, -40, -40, -40, -40 });
	hw_size_weigh(&l, &r, s, 1, &ref);
	t = first_of(&l, &r, 3, all);
	CHECK(t == '.', "a dot the model hardly sees still reads as a dot (%s)", all);
	hw_ink_free(&k[0]);

	/* a hyphen */
	stroke_at(&k[0], &s[0], 40, 50, 0, 50, 50.5);
	run_of(&l, &r, 1, "I-.", (const float[]){ -0.1f, -8, -9 });
	hw_size_weigh(&l, &r, s, 1, &ref);
	t = first_of(&l, &r, 3, all);
	CHECK(t == '-', "a hyphen reads as one (%s)", all);
	hw_ink_free(&k[0]);

	/* a tick: comma or apostrophe; the line decides */
	stroke_at(&k[0], &s[0], 50, 45, 0, 49, 52);
	run_of(&l, &r, 1, "I1,'", (const float[]){ -0.1f, -0.5f, -9, -9.5f });
	hw_size_weigh(&l, &r, s, 1, &ref);
	t = first_of(&l, &r, 3, all);
	CHECK(t == ',' || t == '\'', "a tick reads as a comma or an apostrophe (%s)", all);
	struct hw_size_ref on = ref;
	on.has_base = 1; on.line = 100; on.base = 50;          /* the tick hangs from the line */
	run_of(&l, &r, 1, "I1,'", (const float[]){ -0.1f, -0.5f, -9.5f, -9 });
	hw_size_weigh(&l, &r, s, 1, &on);
	t = first_of(&l, &r, 3, all);
	CHECK(t == ',', "on the line: a comma (%s)", all);
	on.base = 70;                                          /* high above it */
	run_of(&l, &r, 1, "I1,'", (const float[]){ -0.1f, -0.5f, -9, -9.5f });
	hw_size_weigh(&l, &r, s, 1, &on);
	t = first_of(&l, &r, 3, all);
	CHECK(t == '\'', "high above the line: an apostrophe (%s)", all);
	hw_ink_free(&k[0]);

	/* case pairs: the same shape small and tall; the model can't tell them apart */
	const char *pairs = "ocsxvwz";
	for (const char *p = pairs; *p; p++) {
		char two[3] = { *p, (char)(*p - 32), 0 };
		for (int big = 0; big < 2; big++) {
			stroke_at(&k[0], &s[0], 50, 50, big ? 17.5 : 7, 0, 0);
			run_of(&l, &r, 1, two, (const float[]){ -0.7f, -0.7f });
			hw_size_weigh(&l, &r, s, 1, &ref);
			t = first_of(&l, &r, 3, all);
			CHECK(t == two[big], "%c at %s height reads as %c (%s)", *p, big ? "capital" : "small-letter", two[big], all);
			hw_ink_free(&k[0]);
		}
	}
	/* the size moves a pair's chance between its forms only: a letter of another shape
	 * the model prefers stays first */
	stroke_at(&k[0], &s[0], 50, 50, 17.5, 0, 0);
	run_of(&l, &r, 1, "awW", (const float[]){ -0.3f, -1.5f, -9 });
	hw_size_weigh(&l, &r, s, 1, &ref);
	t = first_of(&l, &r, 3, all);
	CHECK(t == 'a', "a tall a stays a (%s)", all);
	/* a lone letter is no mark */
	run_of(&l, &r, 1, "'l", (const float[]){ -0.3f, -1 });
	hw_size_weigh(&l, &r, s, 1, &ref);
	t = first_of(&l, &r, 3, all);
	CHECK(t == 'l', "a tall stroke isn't an apostrophe (%s)", all);
	/* the fit: never lower than the ink's own */
	run_of(&l, &r, 1, "oO", (const float[]){ -0.2f, -1.8f });
	float raw = r.logp[2 * r.C + hw_class_of(&l.model, 'o')];
	hw_size_weigh(&l, &r, s, 1, &ref);
	struct hw_charspan *sp = NULL;
	unsigned o1 = 'o';
	CHECK(hw_align(&l, &r, &o1, 1, &sp) == 1 && sp && sp[0].fit >= exp(raw) - 1e-6, "size never lowers a fit");
	free(sp);
	hw_ink_free(&k[0]);

	/* a word (three glyphs): read as it is */
	for (int i = 0; i < 3; i++) stroke_at(&k[i], &s[i], 10 + 30 * i, 50, 7, 0, 0);
	run_of(&l, &r, 3, "oO.", (const float[]){ -0.7f, -0.7f, -1 });
	for (int f = 0; f < 6; f++) r.logp[f * r.C + hw_class_of(&l.model, 'a')] = f == 1 || f == 4 ? -0.1f : -12;
	float *was = malloc(6 * (size_t)r.C * sizeof *was);
	memcpy(was, r.logp, 6 * (size_t)r.C * sizeof *was);
	hw_size_weigh(&l, &r, s, 3, &ref);
	CHECK(!r.logp0 && !memcmp(was, r.logp, 6 * (size_t)r.C * sizeof *was), "a word reads as without the size");
	free(was);
	for (int i = 0; i < 3; i++) hw_ink_free(&k[i]);

	/* a mark set apart at the end of a word, written last: a dot, whatever the model saw */
	for (int i = 0; i < 3; i++) stroke_at(&k[i], &s[i], 10 + 30 * i, 50, 7, 0, 0);
	stroke_at(&k[3], &s[3], 90, 56, 1, 0, 0);
	hw_run_free(&r);
	r.C = (int)l.model.cfg.nclass;
	r.T = r.rows = 8;
	r.down = 1;
	r.logp = malloc(8 * (size_t)r.C * sizeof *r.logp);
	r.org = calloc(8, sizeof *r.org);
	const char *rd8 = "a_a_a_L_";
	const int st8[] = { 0, 0, 1, 1, 2, 2, 3, 3 };
	for (int f = 0; f < 8; f++) {
		float *row = r.logp + (size_t)f * r.C;
		r.org[f].stroke = st8[f];
		for (int c = 0; c < r.C; c++) row[c] = -12;
		if (rd8[f] == '_') row[0] = -0.01f;
		else { row[0] = -4; row[hw_class_of(&l.model, (unsigned char)rd8[f])] = -0.05f; }
		if (rd8[f] == 'L') row[hw_class_of(&l.model, '.')] = -6;
	}
	hw_size_weigh(&l, &r, s, 4, &ref);
	{
		struct hw_reading rd;
		char txt[16] = "";
		hw_read(&l, &r, 1, &rd);
		for (int j = 0; rd.n && j < rd.c[0].len && j < 15; j++) txt[j] = (char)rd.c[0].cp[j], txt[j + 1] = 0;
		CHECK(!strcmp(txt, "aaa."), "a dot after a word reads as a dot (%s)", txt);
	}
	/* an i's dot written last over its stem is part of the i: the word reads as it is */
	hw_ink_free(&k[2]); hw_ink_free(&k[3]);
	stroke_at(&k[2], &s[2], 70, 45, 0, 70, 57);
	stroke_at(&k[3], &s[3], 70.5, 38, 1, 0, 0);
	memcpy(r.logp + 6 * (size_t)r.C, r.logp + 4 * (size_t)r.C, 2 * (size_t)r.C * sizeof *r.logp);
	free(r.logp0); free(r.size); r.logp0 = NULL; r.size = NULL;
	was = malloc(8 * (size_t)r.C * sizeof *was);
	memcpy(was, r.logp, 8 * (size_t)r.C * sizeof *was);
	hw_size_weigh(&l, &r, s, 4, &ref);
	CHECK(!r.logp0 && !memcmp(was, r.logp, 8 * (size_t)r.C * sizeof *was), "an i's dot leaves the word as it is");
	free(was);
	for (int i = 0; i < 4; i++) hw_ink_free(&k[i]);

	/* no reference: nothing changes */
	stroke_at(&k[0], &s[0], 50, 50, 1, 0, 0);
	run_of(&l, &r, 1, "LO.,", (const float[]){ -0.1f, -1, -9, -10 });
	struct hw_size_ref none = { 0 };
	hw_size_weigh(&l, &r, s, 1, &none);
	t = first_of(&l, &r, 3, all);
	CHECK(t == 'L' && !r.logp0, "without a reference the ink's own reading (%s)", all);
	/* the line alone tells a dot */
	struct hw_size_ref line = { 0 };
	line.kind = HW_SIZE_LINE; line.line = 50; line.x = 10; line.c = 25; line.xs = 14; line.cs = 35;
	run_of(&l, &r, 1, "LO.,", (const float[]){ -0.1f, -1, -9, -10 });
	hw_size_weigh(&l, &r, s, 1, &line);
	t = first_of(&l, &r, 3, all);
	CHECK(t == '.', "with the line only, a dot still reads as a dot (%s)", all);
	hw_ink_free(&k[0]);
	hw_run_free(&r);

	/* the reference from the line: writing far smaller than the line's letters is read at
	 * its own (twice its small letters), a lone letter and the person's letters as they are */
	enum { NP = 801 };
	double *zx = malloc(NP * sizeof *zx), *zy = malloc(NP * sizeof *zy), *zt = calloc(NP, sizeof *zt);
	double base, href;
	struct hw_stroke z = { 0, zx, zy, zt };
	for (int periods = 1; periods <= 8; periods += 7)
		for (double xh = 2; xh <= 9; xh += 7) {
			z.n = periods == 8 ? NP : NP / 8;
			for (size_t i = 0; i < z.n; i++) {
				double u = (double)i / (double)(z.n - 1);
				zx[i] = 100 + u * 3.0 * xh * periods;
				zy[i] = 200 - xh * (0.5 - 0.5 * cos(2 * M_PI * periods * u));
			}
			href = hw_size_ref_height(&line, &z, 1, &base);
			if (periods == 8 && xh == 2)
				CHECK(href > 4 && href < 6, "small writing on the line alone reads at its own letters (%g)", href);
			else
				CHECK(href == line.x, "%s reads at the line's letters (%g)", periods == 1 ? "a lone letter" : "writing at the line's size", href);
			CHECK(hw_size_ref_height(&ref, &z, 1, &base) == ref.x, "the person's letters stay the reference");
		}
	free(zx); free(zy); free(zt);
}

/* the character model against charlm.py: {"lang": L, "cases": [{"before": B, "text": T,
 * "logp": [...]}]} with $GLASSINE_HANDWRITING_MODELS/L/chars.gclm; "missing": a language
 * without a model reads none */
static void test_chars(const char *file)
{
	json_object *v = load(".", file), *lang, *cases, *missing;
	json_object_object_get_ex(v, "lang", &lang);
	json_object_object_get_ex(v, "cases", &cases);
	const struct hw_chars *m = hw_chars_get(json_object_get_string(lang));
	CHECK(m != NULL, "character model not read");
	if (json_object_object_get_ex(v, "missing", &missing))
		CHECK(hw_chars_get(json_object_get_string(missing)) == NULL, "a language without a model has one");
	double worst = 0;
	size_t n = 0;
	for (size_t c = 0; m && c < json_object_array_length(cases); c++) {
		json_object *o = json_object_array_get_idx(cases, c), *b, *t, *lp;
		json_object_object_get_ex(o, "before", &b);
		json_object_object_get_ex(o, "text", &t);
		json_object_object_get_ex(o, "logp", &lp);
		unsigned cp[HW_MAX_LABELS];
		size_t k = hw_utf8_decode(json_object_get_string(t), cp, HW_MAX_LABELS);
		CHECK(k == json_object_array_length(lp), "case %zu: %zu characters, %zu values", c, k,
		      json_object_array_length(lp));
		hw_chars_ctx h = hw_chars_start(m, json_object_get_string(b));
		for (size_t i = 0; i < k && i < json_object_array_length(lp); i++, n++) {
			double d = fabs(hw_chars_logp(m, h, cp[i]) - json_object_get_double(json_object_array_get_idx(lp, i)));
			if (d > worst) worst = d;
			h = hw_chars_push(m, h, cp[i]);
		}
	}
	CHECK(worst <= 1e-9, "character model: off by %g over %zu characters", worst, n);
	json_object_put(v);
}

/* look-alike letters and digits (FR-019): a field's lean decides only where the ink is unsure */
static void test_lookalike(void)
{
	struct hw_lang l;
	memset(&l, 0, sizeof l);
	const char *cs = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,;:!?'-() ";
	for (int k = 0; cs[k]; k++) l.model.charset[k] = (unsigned char)cs[k];
	l.model.cfg.nclass = (uint32_t)strlen(cs) + 1;
	struct hw_run r;
	memset(&r, 0, sizeof r);
	char all[64], t;

	CHECK(hw_lean_of_field("normal") == HW_LEAN_LETTERS && hw_lean_of_field("name,titlecase") == HW_LEAN_LETTERS &&
	      hw_lean_of_field("alpha") == HW_LEAN_LETTERS && hw_lean_of_field("phone") == HW_LEAN_DIGITS &&
	      hw_lean_of_field("date") == HW_LEAN_DIGITS && hw_lean_of_field("number") == HW_LEAN_DIGITS &&
	      hw_lean_of_field("-") == HW_LEAN_NONE && hw_lean_of_field("") == HW_LEAN_NONE &&
	      hw_lean_of_field("email") == HW_LEAN_NONE && hw_lean_of_field("terminal") == HW_LEAN_NONE &&
	      hw_lean_of_field("normalx") == HW_LEAN_NONE, "the lean of each field");

	struct { const char *cps; float v[2]; int lean; char want; const char *what; } c[] = {
		{ "0O", { -0.6f, -0.8f }, HW_LEAN_NONE, '0', "without a lean the ink decides" },
		{ "0O", { -0.6f, -0.8f }, HW_LEAN_LETTERS, 'O', "unsure in a text field: the letter" },
		{ "0O", { -0.05f, -3.5f }, HW_LEAN_LETTERS, '0', "a clear digit in a text field stays a digit" },
		{ "1l", { -0.6f, -0.8f }, HW_LEAN_LETTERS, 'l', "1 or l in a text field: l" },
		{ "6G", { -0.6f, -0.8f }, HW_LEAN_LETTERS, 'G', "6 or G in a text field: G" },
		{ "O0", { -0.6f, -0.8f }, HW_LEAN_DIGITS, '0', "unsure in a number field: the digit" },
		{ "I1", { -0.6f, -0.8f }, HW_LEAN_DIGITS, '1', "I or 1 in a number field: 1" },
		{ "O0", { -0.05f, -3.5f }, HW_LEAN_DIGITS, 'O', "a clear letter in a number field stays a letter" },
		{ "S5", { -0.6f, -0.8f }, HW_LEAN_LETTERS, 'S', "other pairs unchanged" },
	};
	/* a store of its own: the person's look-alikes (none yet) */
	char data[] = "/tmp/hw-look-XXXXXX", sd[200], sf[240];
	CHECK(mkdtemp(data) != NULL, "a store");
	setenv("XDG_DATA_HOME", data, 1);
	for (size_t i = 0; i < sizeof c / sizeof c[0]; i++) {
		run_of(&l, &r, 1, c[i].cps, c[i].v);
		hw_lookalike_lean(&l, &r, c[i].lean, HW_LEAN_KEEP);
		t = first_of(&l, &r, 3, all);
		CHECK(t == c[i].want, "%s (%s)", c[i].what, all);
	}
	/* the person's own pair, picked three times (either way): it leans as the shared ones */
	struct { const char *cps; int lean; char want; const char *what; } p[] = {
		{ "5S", HW_LEAN_LETTERS, 'S', "the person's 5 or S in a text field: S" },
		{ "S5", HW_LEAN_DIGITS, '5', "the person's S or 5 in a number field: 5" },
		{ "5S", HW_LEAN_NONE, '5', "no lean without a field" },
		{ "4h", HW_LEAN_LETTERS, '4', "a pair picked twice is not the person's yet" },
	};
	snprintf(sd, sizeof sd, "%s/glassine", data); mkdir(sd, 0700);
	snprintf(sd, sizeof sd, "%s/glassine/handwriting", data); mkdir(sd, 0700);
	snprintf(sf, sizeof sf, "%s/lookalikes", sd);
	FILE *f = fopen(sf, "w");
	if (f) {
		fputs("0123456789abcdef\torg.example.keyboard\t35\t53\n-\torg.example.keyboard\t35\t53\n"
		      "1123456789abcdef\t-\t53\t35\n2123456789abcdef\t-\t34\t68\n-\t-\t34\t68\n"
		      "-\t-\t61\t62\n-\t-\t61\t62\n-\t-\t61\t62\n", f);
		fclose(f);
	}
	unsigned pairs[8][2];
	int np = hw_lookalikes(pairs, 8);
	CHECK(np == 1 && pairs[0][0] == '5' && pairs[0][1] == 'S', "one pair of the person's (%d), never two letters", np);
	for (size_t i = 0; i < sizeof p / sizeof p[0]; i++) {
		run_of(&l, &r, 1, p[i].cps, (const float[]){ -0.6f, -0.8f });
		hw_lookalike_lean(&l, &r, p[i].lean, HW_LEAN_KEEP);
		t = first_of(&l, &r, 3, all);
		CHECK(t == p[i].want, "%s (%s)", p[i].what, all);
	}
	unlink(sf); rmdir(sd);
	snprintf(sd, sizeof sd, "%s/glassine", data); rmdir(sd); rmdir(data);
	CHECK(hw_lookalikes(pairs, 8) == 0, "no store: none");
	unsetenv("XDG_DATA_HOME");
	/* the frame's total stays one; the ink's own reading is kept for the fit */
	run_of(&l, &r, 1, "0O", (const float[]){ -0.6f, -0.8f });
	double before = 0, after = 0;
	for (int k = 0; k < r.C; k++) before += exp(r.logp[2 * r.C + k]);
	for (int k = 0; k < r.C; k++) r.logp[2 * r.C + k] -= (float)log(before);
	hw_lookalike_lean(&l, &r, HW_LEAN_LETTERS, HW_LEAN_KEEP);
	for (int k = 0; k < r.C; k++) after += exp(r.logp[2 * r.C + k]);
	CHECK(fabs(after - 1) < 1e-4, "the frame keeps its total (%g)", after);
	CHECK(r.logp0 && fabs(r.logp0[2 * r.C + hw_class_of(&l.model, '0')] - (-0.6 - log(before))) < 1e-5, "the ink's own reading kept");
	hw_run_free(&r);
}

/* ---------------------------------------------------------------- calibration (FR-022) */

static void test_calib(void)
{
	/* a temperature keeps each frame a distribution and its surest class */
	float in[2 * 3] = { -0.05f, -3.5f, -4.0f, -2.0f, -0.3f, -1.6f }, out[6];
	hw_calib_apply(in, out, 2, 3, 2.0);
	for (int f = 0; f < 2; f++) {
		double tot = 0;
		int a = 0, b = 0;
		for (int c = 0; c < 3; c++) {
			tot += exp(out[f * 3 + c]);
			if (in[f * 3 + c] > in[f * 3 + a]) a = c;
			if (out[f * 3 + c] > out[f * 3 + b]) b = c;
		}
		CHECK(fabs(tot - 1) < 1e-5 && a == b, "frame %d: a distribution (%g), the same surest class", f, tot);
	}
	CHECK(out[0] < in[0] && out[1] > in[1], "above 1: less sure");
	/* ink that reads 'a' at 95 % where it is right 60 % of the time (else 'b'): the fitted
	 * temperature is above 1 and makes 'a' about as likely as it is right */
	static float lp[3];
	lp[0] = (float)log(0.01); lp[1] = (float)log(0.95); lp[2] = (float)log(0.04);
	int la = 1, lb = 2;
	struct hw_calib_item it[10];
	for (int i = 0; i < 10; i++) it[i] = (struct hw_calib_item){ lp, 1, 3, i < 6 ? &la : &lb, 1 };
	double before, after, t = hw_calib_fit(it, 10, &before, &after);
	float cal[3];
	hw_calib_apply(lp, cal, 1, 3, t);
	CHECK(t > 1.5 && t <= HW_CALIB_MAX && after < before, "overconfident ink: t %g, loss %g -> %g", t, before, after);
	CHECK(fabs(exp(cal[1]) - 0.6) < 0.1, "calibrated: 'a' %g", exp(cal[1]));
	/* ink as sure as it is right: about 1 */
	lp[0] = (float)log(1e-6); lp[1] = (float)log(0.6); lp[2] = (float)log(0.4);
	t = hw_calib_fit(it, 10, NULL, NULL);
	CHECK(fabs(t - 1) < 0.1, "calibrated ink stays about 1 (%g)", t);
	CHECK(hw_calib_fit(it, 0, NULL, NULL) == 0, "nothing to fit: none");
	/* the base model's file: its line for this model's hash, in range */
	char dir[] = "/tmp/hw-calib-XXXXXX", p[200];
	CHECK(mkdtemp(dir) != NULL, "a model directory");
	snprintf(p, sizeof p, "%s/calibration", dir);
	CHECK(hw_calib_base(dir, 0x1234) == 0, "no file: none");
	FILE *f = fopen(p, "w");
	if (f) {
		fputs("glassine-handwriting-calibration 1\ntemperature 00000000000000ff 1.3\n"
		      "temperature 0000000000001234 1.25\ntemperature 0000000000005678 9\n", f);
		fclose(f);
	}
	CHECK(fabs(hw_calib_base(dir, 0x1234) - 1.25) < 1e-9, "the model's own line");
	CHECK(hw_calib_base(dir, 0x9999) == 0 && hw_calib_base(dir, 0x5678) == 0, "another model's, or out of range: none");
	f = fopen(p, "w");
	if (f) { fputs("glassine-handwriting-calibration 2\ntemperature 0000000000001234 1.25\n", f); fclose(f); }
	CHECK(hw_calib_base(dir, 0x1234) == 0, "another version: none");
	unlink(p); rmdir(dir);
}

/* neatening's correspondence (spec 003 FR-036), a property over many strokes: each point's
 * place on the target never goes back along the stroke, stays on it (0..1), and hooks,
 * loops and overshoots the target lacks go to its ends; joint ends leave NAN only at the
 * ends, in one run each */
static uint64_t rng_state = 88172645463325252ULL;
static double urand(void)
{
	rng_state ^= rng_state << 13; rng_state ^= rng_state >> 7; rng_state ^= rng_state << 17;
	return (double)(rng_state >> 11) / 9007199254740992.0;
}

/* a smooth random stroke of n points (x-heights): a few turns, sometimes a loop */
static void random_stroke(double *x, double *y, int n, double scale)
{
	double a = urand() * 6.283, px = 0, py = 0, turn = (urand() - 0.5) * 0.6, loop = urand() < 0.3 ? 6.283 / (n * 0.4) : 0;
	int l0 = (int)(urand() * n * 0.5);
	for (int i = 0; i < n; i++) {
		x[i] = px; y[i] = py;
		a += turn + (urand() - 0.5) * 0.4 + (i >= l0 && i < l0 + (int)(n * 0.4) ? loop : 0);
		if (urand() < 0.08) turn = (urand() - 0.5) * 0.6;
		double step = scale * (0.02 + 0.03 * urand());
		px += step * cos(a); py += step * sin(a);
	}
}

static void test_neaten_match(void)
{
	enum { N = 220 };
	double x[N], y[N], tx[N], ty[N], u[N];
	int bad = 0, nan_mid = 0, ends_off = 0;
	for (int it = 0; it < 4000; it++) {
		int n = 2 + (int)(urand() * 150), tn = 2 + (int)(urand() * 100);
		random_stroke(tx, ty, tn, 1);
		int kind = it % 5;
		if (kind == 0) {
			/* the target itself, jittered */
			n = tn;
			for (int i = 0; i < n; i++) { x[i] = tx[i] + (urand() - 0.5) * 0.06; y[i] = ty[i] + (urand() - 0.5) * 0.06; }
		} else if (kind == 1) {
			/* the target with a hook at the start and an overshoot at the end */
			int h = 6, o = 6, m = tn;
			n = h + m + o;
			for (int i = 0; i < h; i++) { x[i] = tx[0] + 0.12 * sin(i * 0.5); y[i] = ty[0] + 0.12 - 0.02 * i; }
			for (int i = 0; i < m; i++) { x[h + i] = tx[i]; y[h + i] = ty[i]; }
			double dx = tx[m - 1] - tx[m > 1 ? m - 2 : 0], dy = ty[m - 1] - ty[m > 1 ? m - 2 : 0];
			for (int i = 0; i < o; i++) { x[h + m + i] = tx[m - 1] + dx * (i + 1); y[h + m + i] = ty[m - 1] + dy * (i + 1); }
		} else if (kind == 2) {
			/* the target with a loop in the middle */
			int m = tn, a = m / 2, l = 12;
			n = m + l;
			for (int i = 0, o = 0; i < m; i++) {
				x[o] = tx[i]; y[o] = ty[i]; o++;
				if (i == a) for (int k = 1; k <= l; k++) { x[o] = tx[i] + 0.1 * sin(k * 6.283 / l); y[o] = ty[i] + 0.1 - 0.1 * cos(k * 6.283 / l); o++; }
			}
		} else {
			/* anything */
			random_stroke(x, y, n, 1 + urand());
			if (kind == 4) for (int i = 0; i < n; i++) { x[i] = x[n / 2]; y[i] = y[n / 2]; }   /* a dot */
		}
		int j0 = urand() < 0.3, j1 = urand() < 0.3;
		if (hw_neat_match(x, y, (size_t)n, tx, ty, (size_t)tn, j0, j1, u)) { CHECK(0, "neaten match: memory"); return; }
		double last = -1;
		int seen = 0, ended = 0;
		for (int i = 0; i < n; i++) {
			if (isnan(u[i])) {
				/* only at a joint end, before any matched point or after them all */
				if (!(j0 && !seen) && !j1) nan_mid++;
				if (seen) ended = 1;
				continue;
			}
			if (ended) nan_mid++;
			seen = 1;
			if (u[i] < last - 1e-12 || u[i] < 0 || u[i] > 1) bad++;
			last = u[i];
		}
		if (kind == 1 && !j0 && !j1 && (u[0] != 0 || u[n - 1] != 1)) ends_off++;
	}
	CHECK(bad == 0, "neaten match: %d points went back along the stroke", bad);
	CHECK(nan_mid == 0, "neaten match: %d unmatched points away from a joint end", nan_mid);
	CHECK(ends_off == 0, "neaten match: %d hooks or overshoots not at the target's ends", ends_off);
}

/* the glyphs the font draws for a text (hw_letters_glyphs, as fontview.py glyphs()): the
 * cycle along each run (a space takes a step, a character the font lacks starts it again),
 * the joined pairs left to right, a neighbour rule before the cycle */
static void test_letters_glyphs(void)
{
	const char *v1 = "{\"width\":0.5,\"weight\":3,\"members\":3,\"use\":3,\"strokes\":[[[0,0],[0.5,1]]]}";
	char model[4096];
	snprintf(model, sizeof model,
		 "{\"format\":\"%s\",\"version\":%d,\"inputs\":\"0000000000000000\",\"samples\":3,\"built\":0}\n"
		 "{\"metrics\":{\"x_height\":1}}\n"
		 "{\"char\":\"a\",\"samples\":6,\"covered\":true,\"variants\":[%s,%s,%s],\"slots\":[0,1,2,0,1,2,0,1,2,0],"
		 "\"rules\":[{\"prev\":\"b\",\"variant\":2}]}\n"
		 "{\"char\":\"b\",\"samples\":3,\"covered\":true,\"variants\":[%s]}\n"
		 "{\"char\":\"c\",\"samples\":3,\"covered\":true,\"variants\":[%s]}\n"
		 "{\"joined\":\"bc\",\"samples\":3,\"written\":3,\"of\":3,\"variant\":%s}\n",
		 HW_LETTERS_FORMAT, HW_LETTERS_VERSION, v1, v1, v1, v1, v1, v1);
	struct hw_letters L = { 0 }, V;
	if (hw_letters_parse(model, &L) || hw_letters_view(&L, &V)) { CHECK(0, "glyphs: model"); return; }
	/* a a  a b a bc a x a: steps 0 1 (2) 3 4 5 6 7 8 9, x lacking restarts at 0 */
	const char *text = "aa abaabcaxa";
	unsigned cp[16];
	size_t n = strlen(text);
	for (size_t k = 0; k < n; k++) cp[k] = (unsigned char)text[k];
	int var[16];
	long jo[16];
	hw_letters_glyphs(&V, NULL, cp, n, var, jo, NULL);
	/* a at step 0 -> 0, step 1 -> 1, (space 2), step 3 -> 0, b, a after b -> rule 2, a step 6
	 * -> 0, b c joined, a step 9 -> 0, x lacking, a at step 0 -> 0 */
	const int want[] = { 0, 1, -1, 0, 0, 2, 0, -1, -1, 0, -1, 0 };
	int ok = 1;
	for (size_t k = 0; k < n; k++) ok &= var[k] == want[k];
	CHECK(ok, "glyphs: variants");
	CHECK(jo[7] == 0 && jo[8] == 0 && jo[6] < 0 && jo[9] < 0, "glyphs: the joined pair");
	hw_letters_view_free(&V);
	hw_letters_free(&L);
	/* the model file's versions: the one written and the one before (no context rules) are
	 * read, any other not (a reader of version 1 refuses a model with context rules) */
	char *hv = strstr(model, "\"version\":");
	for (int ver = 0; hv && ver <= HW_LETTERS_VERSION + 1; ver++) {
		hv[10] = (char)('0' + ver);
		struct hw_letters M = { 0 };
		int r = hw_letters_parse(model, &M);
		CHECK(!r == (ver >= HW_LETTERS_VERSION_MIN && ver <= HW_LETTERS_VERSION), "letters: version %d %s", ver, r ? "not read" : "read");
		if (!r) hw_letters_free(&M);
	}
	CHECK(HW_LETTERS_VERSION == 2 && hv, "letters: the version written");
}

/* two letters joined in one stroke (FR-036): each part pulled to its own letter, the
 * stroke's points as many, in order, and no jump where the parts meet; at Neatest the word
 * set (FR-043): a joining line from the one glyph to the next where the pen wasn't lifted;
 * strength 0 gives the ink back as it was */
static void test_neaten_joined(void)
{
	/* a c (an arc open to the right) and a u, one variant each (x-heights, y up) */
	char model[8192], cs[2048], us[2048];
	int o = 0;
	for (int i = 0; i < 24; i++) {
		double a = 0.9 + 4.5 * i / 23;
		o += snprintf(cs + o, sizeof cs - o, "%s[%.4f,%.4f]", i ? "," : "", 0.3 + 0.3 * cos(a), 0.5 + 0.5 * sin(a));
	}
	o = 0;
	for (int i = 0; i < 24; i++) {
		double a = 3.1416 * i / 23;
		o += snprintf(us + o, sizeof us - o, "%s[%.4f,%.4f]", i ? "," : "", 0.3 - 0.3 * cos(a), 1 - sin(a));
	}
	snprintf(model, sizeof model,
		 "{\"format\":\"%s\",\"version\":%d,\"inputs\":\"0000000000000000\",\"samples\":3,\"built\":0}\n"
		 "{\"metrics\":{\"x_height\":1}}\n"
		 "{\"char\":\"c\",\"samples\":3,\"covered\":true,\"variants\":[{\"width\":0.6,\"weight\":3,\"members\":3,\"use\":3,\"strokes\":[[%s]]}]}\n"
		 "{\"char\":\"u\",\"samples\":3,\"covered\":true,\"variants\":[{\"width\":0.6,\"weight\":3,\"members\":3,\"use\":3,\"strokes\":[[%s]]}]}\n",
		 HW_LETTERS_FORMAT, HW_LETTERS_VERSION, cs, us);
	struct hw_letters L = { 0 };
	if (hw_letters_parse(model, &L)) { CHECK(0, "neaten joined: model"); return; }
	/* the ink: the c, a short bridge, the u; written a little off (wider, wobbling) */
	enum { NP = 24 + 3 + 24 };
	struct hw_ink ink = { 0 };
	double xh = 30, base = 100, rx[NP], ry[NP];
	int own[NP];
	for (int i = 0; i < NP; i++) {
		double x, y;
		if (i < 24) { double a = 0.9 + 4.5 * i / 23; x = 0.3 + 0.36 * cos(a); y = 0.5 + 0.47 * sin(a); }
		else if (i < 27) { double t = (i - 23) / 4.0, ex = 0.3 + 0.36 * cos(5.4), ey = 0.5 + 0.47 * sin(5.4); x = ex + t * (0.75 - ex); y = ey + t * (1 - ey); }
		else { double a = 3.1416 * (i - 27) / 23; x = 0.75 + 0.32 - 0.32 * cos(a); y = 1 - 0.95 * sin(a); }
		x += 0.03 * sin(i * 1.7); y += 0.03 * cos(i * 2.3);
		rx[i] = 20 + x * xh; ry[i] = base - y * xh;
		hw_ink_add(&ink, rx[i], ry[i], i * 0.01, 0.5);
		own[i] = i < 26 ? 0 : 1;
	}
	int *ownp = own;
	unsigned cp[2] = { 'c', 'u' };
	for (int lv = 0; lv < 3; lv++) {
		double st = lv == 0 ? 0 : lv == 1 ? 0.75 : 1;
		struct hw_neat q = { .ink = &ink, .n = 1, .cp = cp, .nc = 2, .own = &ownp, .strength = st, .L = &L, .xh = xh, .confirmed = 1 };
		if (hw_neaten(&q)) { CHECK(0, "neaten joined: memory"); break; }
		double rs = 0, js = 0;
		for (int i = 0; i + 1 < NP; i++) rs = fmax(rs, hypot(rx[i + 1] - rx[i], ry[i + 1] - ry[i]));
		for (int i = 0; i + 1 < NP; i++) js = fmax(js, hypot(q.x[0][i + 1] - q.x[0][i], q.y[0][i + 1] - q.y[0][i]));
		if (st == 0) {
			int same = q.how[0] == HW_NEAT_KEPT;
			for (int i = 0; i < NP; i++) same &= q.x[0][i] == rx[i] && q.y[0][i] == ry[i];
			CHECK(same, "neaten joined: strength 0 changed the ink");
		} else {
			CHECK(q.how[0] == HW_NEAT_PULLED && q.pulled && q.pulled[0] && q.pulled[1],
			      "neaten joined: %g: not both pulled (how %d)", st, q.how[0]);
			if (st < 1) CHECK(js < 1.6 * rs, "neaten joined: %g: a jump of %.2f (raw steps up to %.2f)", st, js, rs);
			else CHECK(q.why[0] == HW_NEAT_WHY_SET && q.why[1] == HW_NEAT_WHY_SET, "neaten joined: not set");
			int fin = 1;
			for (int i = 0; i < NP; i++) fin &= isfinite(q.x[0][i]) && isfinite(q.y[0][i]);
			CHECK(fin, "neaten joined: %g: not finite", st);
		}
		hw_neat_free(&q);
	}
	hw_letters_free(&L);
	/* the same with the pair joined in the font (FR-039): both letters go to its one glyph,
	 * at Strong pulled, at Neatest set on it (at the line's x-height) */
	char ps[4096];
	o = 0;
	for (int i = 0; i < NP; i++) {
		double x, y;
		if (i < 24) { double a = 0.9 + 4.5 * i / 23; x = 0.3 + 0.3 * cos(a); y = 0.5 + 0.5 * sin(a); }
		else if (i < 27) { double t = (i - 23) / 4.0, ex = 0.3 + 0.3 * cos(5.4), ey = 0.5 + 0.5 * sin(5.4); x = ex + t * (0.75 - ex); y = ey + t * (1 - ey); }
		else { double a = 3.1416 * (i - 27) / 23; x = 0.75 + 0.3 - 0.3 * cos(a); y = 1 - sin(a); }
		o += snprintf(ps + o, sizeof ps - o, "%s[%.4f,%.4f]", i ? "," : "", x, y);
	}
	char model2[16384];
	int m1 = snprintf(model2, sizeof model2, "%s", model);
	snprintf(model2 + m1, sizeof model2 - (size_t)m1,
		 "{\"joined\":\"cu\",\"samples\":3,\"written\":3,\"of\":3,\"variant\":{\"width\":1.05,\"weight\":3,\"members\":3,\"use\":3,\"strokes\":[[%s]]}}\n", ps);
	if (hw_letters_parse(model2, &L) || L.njoined != 1) { CHECK(0, "neaten joined pair: model"); hw_ink_free(&ink); return; }
	for (int lv = 0; lv < 2; lv++) {
		struct hw_neat q = { .ink = &ink, .n = 1, .cp = cp, .nc = 2, .own = &ownp, .strength = lv ? 1 : 0.75, .L = &L, .xh = xh, .confirmed = 1 };
		if (hw_neaten(&q)) { CHECK(0, "neaten joined pair: memory"); break; }
		int w = lv ? HW_NEAT_WHY_SET : HW_NEAT_WHY_PULLED;
		CHECK(q.pulled && q.pulled[0] && q.pulled[1] && q.why[0] == w && q.why[1] == w, "neaten joined pair: %d: not both", lv);
		struct hw_ink out = { 0 };
		for (int i = 0; i < NP; i++) hw_ink_add(&out, q.x[0][i], q.y[0][i], i * 0.01, 0.5);
		double dr = hw_neat_variant_dist(&ink, 1, xh, &L.joined[0].v), dn = hw_neat_variant_dist(&out, 1, lv ? q.line_xh : xh, &L.joined[0].v);
		if (!lv) CHECK(dn < 0.5 * dr && dn < 0.03, "neaten joined pair: not its glyph (%.3f, written %.3f)", dn, dr);
		if (lv) {
			/* set: every point on the glyph's centre line, its first at the glyph's start */
			const struct hw_lstroke *g = &L.joined[0].v.s[0];
			double sx = q.x[0][0] - g->x[0] * q.line_xh, sy = q.y[0][0] + g->y[0] * q.line_xh, far = 0;
			for (int i = 0; i < NP; i++) {
				double best = INFINITY;
				for (size_t t = 1; t < g->n; t++) {
					double ax = sx + g->x[t - 1] * q.line_xh, ay = sy - g->y[t - 1] * q.line_xh;
					double bx = sx + g->x[t] * q.line_xh, by = sy - g->y[t] * q.line_xh, dx = bx - ax, dy = by - ay;
					double ll = dx * dx + dy * dy, u = ll > 0 ? fmin(1, fmax(0, ((q.x[0][i] - ax) * dx + (q.y[0][i] - ay) * dy) / ll)) : 0;
					best = fmin(best, hypot(ax + u * dx - q.x[0][i], ay + u * dy - q.y[0][i]));
				}
				far = fmax(far, best);
			}
			CHECK(far < 0.001 * q.line_xh, "neaten joined pair: set off its glyph by %.4f", far);
		}
		hw_ink_free(&out);
		hw_neat_free(&q);
	}
	hw_ink_free(&ink);
	hw_letters_free(&L);
}

/* the neighbour rules and joined pairs in the model file (FR-038, FR-039): read and written
 * back the same; a rule for a version left out gone from the font's view, the others
 * renumbered; the font with 'liga' and, only when asked, 'rand' (FR-040) */
static void test_letters_context(void)
{
	char model[8192], st[1024];
	int o = 0;
	for (int i = 0; i < 12; i++) o += snprintf(st + o, sizeof st - o, "%s[%.4f,%.4f]", i ? "," : "", 0.05 * i, 0.5 + 0.4 * sin(i));
	snprintf(model, sizeof model,
		 "{\"format\":\"%s\",\"version\":%d,\"inputs\":\"0000000000000000\",\"samples\":3,\"built\":0}\n"
		 "{\"metrics\":{\"x_height\":1}}\n"
		 "{\"char\":\"l\",\"samples\":3,\"covered\":true,\"variants\":[{\"width\":0.55,\"weight\":3,\"members\":3,\"use\":3,\"strokes\":[[%s]]}],\"slots\":[0,0,0,0,0,0,0,0,0,0]}\n"
		 "{\"char\":\"o\",\"samples\":9,\"covered\":true,\"variants\":[{\"width\":0.55,\"weight\":3,\"members\":3,\"use\":6,\"left_out\":true,\"strokes\":[[%s]]},"
		 "{\"width\":0.55,\"weight\":3,\"members\":3,\"use\":3,\"strokes\":[[%s]]},{\"width\":0.55,\"weight\":3,\"members\":3,\"use\":3,\"strokes\":[[%s]]}],"
		 "\"slots\":[1,2,1,2,1,2,1,2,1,2],\"rules\":[{\"prev\":\"l\",\"variant\":2,\"n\":7,\"same\":7,\"hits\":7,\"cycle\":3.5},"
		 "{\"next\":\"l\",\"variant\":0,\"n\":6,\"same\":6,\"hits\":6,\"cycle\":0}]}\n"
		 "{\"joined\":\"lo\",\"samples\":4,\"written\":4,\"of\":5,\"variant\":{\"width\":1.1,\"weight\":4,\"members\":4,\"use\":4,\"strokes\":[[%s]]}}\n",
		 HW_LETTERS_FORMAT, HW_LETTERS_VERSION, st, st, st, st, st);
	struct hw_letters L = { 0 };
	if (hw_letters_parse(model, &L)) { CHECK(0, "letters context: model"); return; }
	CHECK(L.n == 2 && L.l[1].nrules == 2 && L.l[1].rule[0].side == 0 && L.l[1].rule[0].nb == 'l' && L.l[1].rule[0].var == 2 &&
	      L.l[1].rule[1].side == 1, "letters context: the rules as read");
	CHECK(L.njoined == 1 && L.joined[0].a == 'l' && L.joined[0].b == 'o' && L.joined[0].samples == 4 && L.joined[0].joined == 4 &&
	      L.joined[0].of == 5 && L.joined[0].v.nstrokes == 1, "letters context: the joined pair as read");
	size_t n1, n2;
	char *t1 = hw_letters_format(&L, &n1);
	struct hw_letters M = { 0 };
	int r = t1 ? hw_letters_parse(t1, &M) : -1;
	char *t2 = r ? NULL : hw_letters_format(&M, &n2);
	CHECK(t1 && t2 && n1 == n2 && !memcmp(t1, t2, n1), "letters context: written back differently");
	free(t1); free(t2);
	hw_letters_free(&M);
	struct hw_letters V;
	if (!hw_letters_view(&L, &V)) {
		CHECK(V.l[1].nvar == 2 && V.l[1].nrules == 1 && V.l[1].rule[0].var == 1, "letters context: the view's rules (%d)", V.l[1].nrules);
		hw_letters_view_free(&V);
	}
	for (int rnd = 0; rnd < 2; rnd++) {
		size_t len = 0;
		unsigned char *f = hw_font_build_style(&L, "Test", 1, HW_STYLE_REGULAR, HW_FONT_NIB_DEFAULT, rnd, &len);
		CHECK(f != NULL, "letters context: no font");
		if (!f) continue;
		CHECK(memmem(f, len, "liga", 4) != NULL, "letters context: no liga");
		CHECK((memmem(f, len, "rand", 4) != NULL) == rnd, "letters context: rand %s", rnd ? "missing" : "unasked");
		free(f);
	}
	hw_letters_free(&L);
}

/* the point aligner's decoding on made-up log-probabilities (no model run): the split of a
 * stroke's frames, the cut through it, and the whole line's labels with a delayed stroke */
static void test_aligner(void)
{
	struct hw_model m = { 0 };
	m.cfg.nclass = 4;
	m.charset[0] = 'a'; m.charset[1] = 'b'; m.charset[2] = 'c';
	/* 9 frames, classes 1 (a), 2 (b), 3 (c): a a a b b c c, then a a (a dot after the word) */
	int want[9] = { 1, 1, 1, 2, 2, 3, 3, 1, 1 };
	float lp[9 * 4];
	for (int t = 0; t < 9; t++) for (int c = 0; c < 4; c++) lp[t * 4 + c] = c == want[t] ? 0.0f : -20.0f;
	struct hw_aligned a = { &m, 9, 4, lp, 0, NULL, NULL };
	int lab[9], cls[3] = { 1, 2, 3 };
	CHECK(!hw_aligner_split(&a, 0, 6, cls, 3, lab), "split");
	CHECK(lab[2] == 0 && lab[3] == 1 && lab[4] == 1 && lab[5] == 2, "split at the class changes: %d %d %d %d", lab[2], lab[3], lab[4], lab[5]);
	int two[2] = { 1, 1 };
	CHECK(hw_aligner_split(&a, 0, 0, two, 2, lab) == 1, "two equal classes need a blank frame between them");
	/* the cut: a stroke of 14 points over frames 0..6, two points a frame */
	double x[14], y[14];
	int fr[14];
	for (int i = 0; i < 14; i++) { x[i] = i * 0.1; y[i] = 0; fr[i] = i / 2; }
	struct hw_cut_ref refs[3];
	double rx[2] = { 0, 1 }, ry[2] = { 0, 0 };
	for (int r = 0; r < 3; r++) hw_cut_ref_make(rx, ry, 2, &refs[r]);
	size_t off[4] = { 0, 1, 2, 3 }, at[4];
	int which[3];
	double cost;
	struct hw_cut_ctx ctx = { &a, fr, cls };
	CHECK(!hw_cut_aligner(x, y, 14, 1.0, 3, refs, off, at, which, &cost, &ctx), "cut");
	CHECK(at[0] == 0 && at[1] == 6 && at[2] == 10 && at[3] == 14, "cut where the classes change: %zu %zu %zu", at[1], at[2], at[3]);
	/* the whole line: three strokes (frames 0-2, 3-6, 7-8), the third a delayed a */
	int *fs[3], *own[3], f0[6] = { 0, 0, 1, 1, 2, 2 }, f1[8] = { 3, 3, 4, 4, 5, 5, 6, 6 }, f2[4] = { 7, 7, 8, 8 };
	int o0[6], o1[8], o2[4];
	size_t np[3] = { 6, 8, 4 };
	fs[0] = f0; fs[1] = f1; fs[2] = f2; own[0] = o0; own[1] = o1; own[2] = o2;
	struct hw_aligned b = { &m, 9, 4, lp, 3, fs, np };
	unsigned cp[3] = { 'a', 'b', 'c' };
	CHECK(!hw_aligner_label(&b, cp, 3, -3.0, own), "label");
	CHECK(o0[0] == 0 && o1[0] == 1 && o1[7] == 2 && o2[0] == 0 && o2[3] == 0, "the late stroke goes to the a: %d %d %d", o1[7], o2[0], o2[3]);
	CHECK(!hw_aligner_label(&b, cp, 3, -1e6, own), "label");
	CHECK(o2[0] == 2, "at a penalty no stroke pays, the chain's last letter: %d", o2[0]);
	/* the nearest of equal letters: "iti", both dots given to the first i by the decoding */
	struct hw_ink ik[5] = { { 0 } };
	double xs[5] = { 0, 1, 2, 2, 0 };
	for (int j = 0; j < 5; j++) for (int i = 0; i < 3; i++) hw_ink_add(&ik[j], xs[j] + 0.01 * i, j < 3 ? -i * 0.3 : 1.0, 0.1 * j, NAN);
	int d0[3] = { 0, 0, 0 }, d1[3] = { 1, 1, 1 }, d2[3] = { 2, 2, 2 }, d3[3] = { 0, 0, 0 }, d4[3] = { 0, 0, 0 };
	int *dn[5] = { d0, d1, d2, d3, d4 };
	unsigned iti[3] = { 'i', 't', 'i' };
	CHECK(!hw_aligner_nearest(ik, 5, iti, 3, dn), "nearest");
	CHECK(d3[0] == 2 && d3[2] == 2 && d4[0] == 0 && d0[0] == 0 && d2[0] == 2,
	      "each dot to the i under it: %d %d", d3[0], d4[0]);
	/* two words apart ("i x i", the i two words on): a late dot stays where it was */
	unsigned far[5] = { 'i', ' ', 'x', ' ', 'i' };
	int e0[3] = { 0, 0, 0 }, e1[3] = { 2, 2, 2 }, e2[3] = { 4, 4, 4 }, e3[3] = { 0, 0, 0 }, e4[3] = { 0, 0, 0 };
	int *en[5] = { e0, e1, e2, e3, e4 };
	CHECK(!hw_aligner_nearest(ik, 5, far, 5, en), "nearest");
	CHECK(e3[0] == 0, "an i two words on is not a candidate: %d", e3[0]);
	for (int j = 0; j < 5; j++) hw_ink_free(&ik[j]);
}

/* a stroke taller than HW_NEAT_DRAWING x-heights is drawing; $GLASSINE_HANDWRITING_NEATEN
 * drawing=F sets the factor (a looped f is taller than that in some hands) */
static void test_drawing(void)
{
	struct hw_ink k[2] = { { 0 } };
	for (int i = 0; i < 5; i++) { hw_ink_add(&k[0], 0, i * 1.0, i * 0.01, NAN); hw_ink_add(&k[1], 1, i * 0.2, i * 0.01, NAN); }
	unsigned char keep[2] = { 0, 0 };
	unsetenv("GLASSINE_HANDWRITING_NEATEN");
	hw_neat_drawing(k, 2, 1.0, keep);
	CHECK(keep[0] == 1 && keep[1] == 0, "4 x-heights tall is drawing: %d %d", keep[0], keep[1]);
	keep[0] = keep[1] = 0;
	setenv("GLASSINE_HANDWRITING_NEATEN", "drawing=5", 1);
	hw_neat_drawing(k, 2, 1.0, keep);
	CHECK(keep[0] == 0, "at drawing=5 it is writing: %d", keep[0]);
	unsetenv("GLASSINE_HANDWRITING_NEATEN");
	for (int j = 0; j < 2; j++) hw_ink_free(&k[j]);
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
	if (argc > 2 && !strcmp(argv[1], "--chars")) {        /* a character model against charlm.py */
		test_chars(argv[2]);
		if (fails) { fprintf(stderr, "test-engine: %d failed\n", fails); return 1; }
		printf("test-engine: character model ok\n");
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
	test_size();
	test_lookalike();
	test_calib();
	test_neaten_match();
	test_letters_glyphs();
	test_neaten_joined();
	test_letters_context();
	test_aligner();
	test_drawing();
	if (fails) { fprintf(stderr, "test-engine: %d failed\n", fails); return 1; }
	printf("test-engine: ok\n");
	return 0;
}
