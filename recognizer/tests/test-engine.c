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
#include "../src/hw/chars.h"
#include "../src/hw/ctc.h"
#include "../src/hw/featurize.h"
#include "../src/hw/letters.h"
#include "../src/hw/lookalike.h"
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
	for (size_t i = 0; i < sizeof c / sizeof c[0]; i++) {
		run_of(&l, &r, 1, c[i].cps, c[i].v);
		hw_lookalike_lean(&l, &r, c[i].lean, HW_LEAN_KEEP);
		t = first_of(&l, &r, 3, all);
		CHECK(t == c[i].want, "%s (%s)", c[i].what, all);
	}
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

/* two letters joined in one stroke (FR-036): each part pulled to its own letter, the
 * stroke's points as many, in order, and no jump where the parts meet; strength 0 gives
 * the ink back as it was */
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
		struct hw_neat q = { .ink = &ink, .n = 1, .cp = cp, .nc = 2, .own = &ownp, .strength = st, .L = &L, .xh = xh };
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
			CHECK(js < 1.6 * rs, "neaten joined: %g: a jump of %.2f (raw steps up to %.2f)", st, js, rs);
			int fin = 1;
			for (int i = 0; i < NP; i++) fin &= isfinite(q.x[0][i]) && isfinite(q.y[0][i]);
			CHECK(fin, "neaten joined: %g: not finite", st);
		}
		hw_neat_free(&q);
	}
	hw_ink_free(&ink);
	hw_letters_free(&L);
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
	test_neaten_match();
	test_neaten_joined();
	if (fails) { fprintf(stderr, "test-engine: %d failed\n", fails); return 1; }
	printf("test-engine: ok\n");
	return 0;
}
