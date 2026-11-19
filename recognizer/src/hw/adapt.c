// SPDX-License-Identifier: GPL-2.0-or-later
// Adaptation (spec FR-009): the encoder of the base model stays frozen; a head (a
// residual bottleneck adapter in front of the output layer, starting as the identity, as
// in train/src/glassine_train/adapt.py) is trained with CTC on the person's samples.
// One in five samples (by its id) is held back and never trained on; the new head is kept
// only when it reads those better than the head in use, which then becomes the previous.
#include <dirent.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "adapt.h"
#include "calib.h"
#include "ctc.h"
#include "recog.h"
#include "sample.h"
#include "size.h"
#include "store.h"

#define HDR_BYTES 56

char *hw_adapt_serialize(const struct hw_head *h, const struct hw_adapt_info *in, size_t *len)
{
	size_t n = HDR_BYTES + h->nparam * sizeof(float);
	unsigned char *b = calloc(1, n);
	if (!b) return NULL;
	uint32_t v = HW_GHA_VERSION;
	memcpy(b, HW_GHA_MAGIC, 4);
	memcpy(b + 4, &v, 4);
	memcpy(b + 8, &in->base, 8);
	uint32_t w[5] = { h->rank, h->dim, h->nclass, in->ntrain, in->nheld };
	memcpy(b + 16, w, sizeof w);
	memcpy(b + 36, &in->cer, 4);
	memcpy(b + 40, &in->cer_before, 4);
	memcpy(b + 44, &in->time, 8);
	memcpy(b + 52, &in->temp, 4);
	memcpy(b + HDR_BYTES, h->mem, h->nparam * sizeof(float));
	*len = n;
	return (char *)b;
}

static unsigned char *read_all(const char *path, size_t *len)
{
	FILE *f = fopen(path, "re");
	if (!f) return NULL;
	fseek(f, 0, SEEK_END);
	long n = ftell(f);
	fseek(f, 0, SEEK_SET);
	unsigned char *b = n > 0 ? malloc((size_t)n) : NULL;
	if (b && fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); b = NULL; }
	fclose(f);
	*len = (size_t)n;
	return b;
}

static int parse_info(const unsigned char *b, size_t len, struct hw_adapt_info *in, uint32_t *dim, uint32_t *nclass)
{
	uint32_t v, w[5];
	if (len < HDR_BYTES || memcmp(b, HW_GHA_MAGIC, 4)) return -1;
	memcpy(&v, b + 4, 4);
	if (v != HW_GHA_VERSION) return -1;
	memcpy(&in->base, b + 8, 8);
	memcpy(w, b + 16, sizeof w);
	in->rank = w[0]; *dim = w[1]; *nclass = w[2]; in->ntrain = w[3]; in->nheld = w[4];
	memcpy(&in->cer, b + 36, 4);
	memcpy(&in->cer_before, b + 40, 4);
	memcpy(&in->time, b + 44, 8);
	memcpy(&in->temp, b + 52, 4);       /* 0 in a head made before calibration */
	if (!(in->temp >= HW_CALIB_MIN && in->temp <= HW_CALIB_MAX)) in->temp = 0;
	return 0;
}

int hw_adapt_info(const char *path, struct hw_adapt_info *info)
{
	size_t len;
	unsigned char *b = read_all(path, &len);
	uint32_t d, c;
	int r = b ? parse_info(b, len, info, &d, &c) : -1;
	free(b);
	return r;
}

int hw_adapt_load(const char *path, const struct hw_model *m, struct hw_head *h, struct hw_adapt_info *info)
{
	size_t len;
	unsigned char *b = read_all(path, &len);
	uint32_t dim, nclass;
	if (!b || parse_info(b, len, info, &dim, &nclass) || info->base != m->hash || dim != m->base.dim ||
	    nclass != m->base.nclass || info->rank > 4096) {
		free(b);
		return -1;
	}
	if (hw_head_init(h, &m->base, info->rank)) { free(b); return -1; }
	if (len != HDR_BYTES + h->nparam * sizeof(float)) { hw_head_free(h); free(b); return -1; }
	memcpy(h->mem, b + HDR_BYTES, h->nparam * sizeof(float));
	if (!h->rank) h->bup = NULL;
	h->temp = info->temp;
	free(b);
	return 0;
}

/* ---------------------------------------------------------------- conditions */

static int read_word(const char *path, char *out, size_t n)
{
	FILE *f = fopen(path, "re");
	if (!f) return -1;
	int ok = fgets(out, (int)n, f) != NULL;
	fclose(f);
	if (!ok) return -1;
	out[strcspn(out, "\r\n")] = 0;
	return 0;
}

int hw_charging(void)
{
	const char *root = getenv("GLASSINE_HANDWRITING_SYSFS");
	if (!root || !*root) root = "/sys/class/power_supply";
	DIR *d = opendir(root);
	if (!d) return 1;               /* no power supply class: a machine on mains */
	struct dirent *e;
	int batteries = 0, charging = 0;
	while ((e = readdir(d))) {
		if (e->d_name[0] == '.') continue;
		char p[4096], type[64], v[64];
		snprintf(p, sizeof p, "%s/%s/type", root, e->d_name);
		if (read_word(p, type, sizeof type)) continue;
		if (!strcmp(type, "Battery")) {
			batteries++;
			snprintf(p, sizeof p, "%s/%s/status", root, e->d_name);
			if (!read_word(p, v, sizeof v) && (!strcmp(v, "Charging") || !strcmp(v, "Full"))) charging = 1;
		} else if (!strcmp(type, "Mains") || !strncmp(type, "USB", 3)) {
			snprintf(p, sizeof p, "%s/%s/online", root, e->d_name);
			if (!read_word(p, v, sizeof v) && !strcmp(v, "1")) charging = 1;
		}
	}
	closedir(d);
	return charging || !batteries;
}

int hw_idle(void)
{
	const char *p = getenv("GLASSINE_HANDWRITING_LOADAVG");
	char v[128];
	if (read_word(p && *p ? p : "/proc/loadavg", v, sizeof v)) return 1;
	return strtod(v, NULL) < 1.0;
}

/* ---------------------------------------------------------------- training */

struct item {
	float *frames;
	long T;
	int lab[HW_MAX_LABELS];
	int L;
	unsigned cp[HW_MAX_LABELS];
};

static void items_free(struct item *it, size_t n)
{
	for (size_t i = 0; i < n; i++) free(it[i].frames);
	free(it);
}

/* character error rate of a head on items */
static double cer(const struct hw_head *h, const struct hw_model *m, const struct item *it, size_t n)
{
	size_t err = 0, tot = 0;
	for (size_t i = 0; i < n; i++) {
		float *lp = malloc((size_t)it[i].T * h->nclass * sizeof *lp);
		if (!lp) return 1e9;
		hw_head_forward(h, it[i].frames, it[i].T, lp, NULL, NULL);
		int lab[HW_MAX_LABELS];
		int L = hw_ctc_greedy(lp, it[i].T, (int)h->nclass, lab, HW_MAX_LABELS);
		unsigned cp[HW_MAX_LABELS];
		for (int k = 0; k < L; k++) cp[k] = m->charset[lab[k] - 1];
		err += hw_edit(it[i].cp, (size_t)it[i].L, cp, (size_t)L);
		tot += (size_t)it[i].L;
		free(lp);
	}
	return tot ? (double)err / (double)tot : 1.0;
}

/* the head's temperature fitted on items (calib.h); 0 when nothing could be fitted */
static double fit_temp(const struct hw_head *h, const struct item *it, size_t n)
{
	struct hw_calib_item *ci = calloc(n + 1, sizeof *ci);
	float **lp = calloc(n + 1, sizeof *lp);
	double t = 0;
	size_t k = 0;
	if (!ci || !lp) goto out;
	for (size_t i = 0; i < n; i++) {
		if (!(lp[i] = malloc((size_t)it[i].T * h->nclass * sizeof **lp))) goto out;
		hw_head_forward(h, it[i].frames, it[i].T, lp[i], NULL, NULL);
		ci[k++] = (struct hw_calib_item){ lp[i], it[i].T, (int)h->nclass, it[i].lab, it[i].L };
	}
	t = hw_calib_fit(ci, k, NULL, NULL);
out:
	for (size_t i = 0; lp && i < n; i++) free(lp[i]);
	free(lp); free(ci);
	return t;
}

/* the adaptation in use, without a temperature (made before calibration), gets the one its
 * held-back samples give; the file is written again with it, nothing else changed */
static int calibrate_current(const struct hw_lang *l, const char *rel, const char *cur, const struct item *he, size_t nhe)
{
	struct hw_adapt_info in;
	if (l->has_adapted <= 0 || l->adapted.temp > 0 || hw_adapt_info(cur, &in) || in.base != l->model.hash) return 0;
	in.temp = (float)fit_temp(&l->adapted, he, nhe);
	if (!(in.temp > 0)) return 0;
	size_t len;
	char *bin = hw_adapt_serialize(&l->adapted, &in, &len), crel[192];
	snprintf(crel, sizeof crel, "%s/current", rel);
	int r = bin && !hw_store_replace(crel, bin, len) ? 1 : -1;
	free(bin);
	return r;
}

struct adam { float *m, *v; long step; };

static void train(struct hw_head *h, struct item *it, size_t n, const struct hw_adapt_opts *o, int *aborted)
{
	size_t D = h->dim, r = h->rank, P = h->nparam;
	float *grad = calloc(P, sizeof *grad);
	struct adam a = { calloc(P, sizeof(float)), calloc(P, sizeof(float)), 0 };
	size_t *order = malloc(n * sizeof *order);
	if (!grad || !a.m || !a.v || !order) goto out;
	for (size_t i = 0; i < n; i++) order[i] = i;
	unsigned long long seed = 0x2545F4914F6CDD1DULL;
	for (int ep = 0; ep < o->epochs; ep++) {
		double eploss = 0;
		if (!o->force && !hw_charging()) { *aborted = 1; break; }
		if (o->progress) { printf("epoch\t%d\t%d\n", ep + 1, o->epochs); fflush(stdout); }
		for (size_t i = n; i > 1; i--) {      /* shuffle (xorshift) */
			seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
			size_t j = (size_t)(seed % i), t = order[i - 1];
			order[i - 1] = order[j]; order[j] = t;
		}
		for (size_t b0 = 0; b0 < n; b0 += (size_t)o->batch) {
			size_t b1 = b0 + (size_t)o->batch < n ? b0 + (size_t)o->batch : n;
			memset(grad, 0, P * sizeof *grad);
			for (size_t bi = b0; bi < b1; bi++) {
				struct item *x = &it[order[bi]];
				double loss = hw_head_grad(h, x->frames, x->T, x->lab, x->L, grad,
							   1.0f / ((float)x->L * (float)(b1 - b0)));
				if (isfinite(loss)) eploss += loss / x->L;
			}
			/* Adam (beta 0.9, 0.999, eps 1e-8) */
			a.step++;
			float b1c = 1 - powf(0.9f, (float)a.step), b2c = 1 - powf(0.999f, (float)a.step);
			for (size_t p = 0; p < P; p++) {
				if (!r && p < 2 * r * D + r + D) continue;
				a.m[p] = 0.9f * a.m[p] + 0.1f * grad[p];
				a.v[p] = 0.999f * a.v[p] + 0.001f * grad[p] * grad[p];
				h->mem[p] -= o->lr * (a.m[p] / b1c) / (sqrtf(a.v[p] / b2c) + 1e-8f);
			}
		}
		if (o->verbose > 1) fprintf(stderr, "epoch %d loss %.4f\n", ep, eploss / (double)n);
	}
out:
	free(grad); free(a.m); free(a.v); free(order);
}

static uint64_t id_value(const char *id) { return strtoull(id, NULL, 16); }

static void progress(const struct hw_adapt_opts *o, const char *fmt, ...)
{
	if (!o->progress) return;
	va_list ap;
	va_start(ap, fmt);
	vprintf(fmt, ap);
	va_end(ap);
	putchar('\n');
	fflush(stdout);
}

/* state: USABLE GEN TIME ID of the last run: TIME and ID of the newest sample it saw (since
 * this version) */
struct state { unsigned long usable, gen; long long time; char id[17]; };

static void read_state(const char *lang, struct state *st)
{
	char statep[4096], buf[128];
	memset(st, 0, sizeof *st);
	st->time = -1;
	hw_store_adaptation_path(statep, sizeof statep, lang, "state");
	FILE *sf = fopen(statep, "re");
	if (sf) { if (fgets(buf, sizeof buf, sf)) sscanf(buf, "%lu %lu %lld %16s", &st->usable, &st->gen, &st->time, st->id); fclose(sf); }
}

/* samples stored after the newest one the last run saw (all, when there was none) */
static long fresh_since(const struct hw_entry *es, size_t ne, const char *lang, const struct state *st)
{
	long k = 0;
	size_t from = 0;
	int found = 0;
	for (size_t i = 0; st->id[0] && i < ne; i++) if (!strcmp(es[i].meta.id, st->id)) { from = i + 1; found = 1; }
	for (size_t i = from; i < ne; i++)
		k += !strcmp(es[i].meta.lang, lang) && (found || es[i].meta.time > st->time);
	return k;
}

/* a sample confirmed before the base model's training data ended: the model learned from it
 * already, so an adaptation neither learns from it nor checks against it */
static int in_base(const struct hw_model *m, const struct hw_entry *e)
{
	return m->trained_until > 0 && e->meta.time <= m->trained_until;
}

int hw_adapt_plan(const char *lang, long *train, long *held, long *fresh)
{
	/* from the index: the texts, without reading any strokes */
	const struct hw_index_entry *ix;
	size_t ne;
	*train = *held = *fresh = 0;
	if (hw_samples_index(&ix, &ne)) return -1;
	struct hw_entry *es = calloc(ne + 1, sizeof *es);
	if (!es) return -1;
	for (size_t i = 0; i < ne; i++) es[i].meta = ix[i].meta;
	struct hw_lang *l = hw_lang_get(lang);
	for (size_t i = 0; i < ne; i++) {
		if (strcmp(es[i].meta.lang, lang) || (l && in_base(&l->model, &es[i]))) continue;
		unsigned cp[HW_MAX_LABELS];
		size_t nc = hw_utf8_decode(ix[i].text, cp, HW_MAX_LABELS);
		int ok = nc && nc <= HW_MAX_LABELS;
		for (size_t k = 0; ok && l && k < nc; k++) ok = hw_class_of(&l->model, cp[k]) != 0;
		if (!ok) continue;
		if (strtoull(es[i].meta.id, NULL, 16) % 5 == 0) (*held)++; else (*train)++;
	}
	struct state st;
	read_state(lang, &st);
	*fresh = fresh_since(es, ne, lang, &st);
	free(es);
	return 0;
}

int hw_adapt_run(const struct hw_adapt_opts *o)
{
	if (!hw_learning_on()) {
		if (o->verbose) printf("adapt %s: learning is off\n", o->lang);
		progress(o, "skip\tlearning");
		return 2;
	}
	if (!o->force && (!hw_charging() || (!o->requested && !hw_idle()))) {
		if (o->verbose) printf("adapt %s: waiting (not %s)\n", o->lang, o->requested ? "charging" : "idle and charging");
		progress(o, "skip\tcharging");
		return 2;
	}
	struct hw_lang *l = hw_lang_get(o->lang);
	if (!l) { if (o->verbose) printf("adapt %s: no base model\n", o->lang); return -1; }
	const struct hw_model *m = &l->model;
	struct hw_entry *es;
	size_t ne;
	unsigned long gen;
	int at = m->version == HW_GHM_VERSION_REF;
	if (at) {
		/* the person's letters, measured once before the samples are loaded (the reference
		 * a sample is read at, as when it was written) */
		struct hw_units u = { "em", 0, 0 };
		struct hw_size_ref r0;
		double b0;
		hw_size_ref_for(o->lang, &u, NULL, NAN, &r0);
		hw_size_ref_height(&r0, NULL, 0, &b0);
	}
	if (hw_samples_load(&es, &ne, &gen)) return -1;
	struct item *tr = calloc(ne + 1, sizeof *tr), *he = calloc(ne + 1, sizeof *he);
	size_t ntr = 0, nhe = 0, usable = 0;
	int ret = -1;
	if (!tr || !he) goto out;
	for (size_t i = 0; i < ne; i++) {
		if (strcmp(es[i].meta.lang, o->lang) || in_base(m, &es[i])) continue;
		struct hw_sample s;
		char err[128];
		if (hw_sample_parse(es[i].json, &s, err, sizeof err)) continue;
		struct item x = { 0 };
		int ok = 1;
		size_t nc = hw_utf8_decode(s.text, x.cp, HW_MAX_LABELS);
		if (!nc || nc > HW_MAX_LABELS) ok = 0;
		for (size_t k = 0; ok && k < nc; k++) {
			x.lab[k] = hw_class_of(m, x.cp[k]);
			if (!x.lab[k]) ok = 0;
		}
		x.L = (int)nc;
		if (ok) {
			struct hw_stroke *st = calloc(s.nstrokes + 1, sizeof *st);
			float *feats = NULL;
			long rows = -1;
			if (st && at) {
				/* the sample's reference: the person's letters in its units, else its
				 * area's line; its guides at two thirds of each line (the samples app's) */
				hw_sample_strokes(&s, st);
				struct hw_size_ref ref;
				double base = s.area.set && s.area.line > 0 ? s.area.box[1] + s.area.line * 2 / 3 : NAN, b;
				hw_size_ref_for(o->lang, &s.units, &s.area, base, &ref);
				double h = hw_size_ref_height(&ref, st, s.nstrokes, &b), tilt, mid, x0;
				if (h > 0 || !hw_line_metrics(st, s.nstrokes, &tilt, &h, &mid, &x0))
					rows = hw_features_ref(st, s.nstrokes, h, b, &feats, NULL);
			} else if (st)
				rows = (hw_sample_strokes(&s, st), hw_features(st, s.nstrokes, &feats, NULL));
			free(st);
			x.T = rows > 0 ? hw_encode(m, feats, rows, &x.frames) : -1;
			free(feats);
			int need = x.L;
			for (int k = 1; k < x.L; k++) need += x.lab[k] == x.lab[k - 1];
			if (x.T < need) ok = 0;
		}
		hw_sample_free(&s);
		if (!ok) { free(x.frames); continue; }
		usable++;
		if (id_value(es[i].meta.id) % 5 == 0) he[nhe++] = x; else tr[ntr++] = x;
	}
	/* nothing new since the last run: don't spend the battery's charge again */
	char cur[4096], prev[4096], rel[160], buf[64];
	hw_store_adaptation_path(cur, sizeof cur, o->lang, "current");
	hw_store_adaptation_path(prev, sizeof prev, o->lang, "previous");
	struct state sta;
	read_state(o->lang, &sta);
	long fresh = fresh_since(es, ne, o->lang, &sta);
	progress(o, "plan\t%zu\t%zu\t%ld", ntr, nhe, fresh);
	if (!o->force && sta.usable == usable && sta.gen == gen && !fresh) {
		/* a head from before calibration is calibrated on its held-back samples once */
		int fd = nhe >= (size_t)o->min_held ? hw_store_lock() : -1;
		if (fd >= 0) {
			hw_store_adaptation_dir(rel, sizeof rel, o->lang);
			if (hw_store_generation() == gen && calibrate_current(l, rel, cur, he, nhe) > 0 && o->verbose)
				printf("adapt %s: calibrated the adaptation in use\n", o->lang);
			hw_store_unlock(fd);
		}
		if (o->verbose) printf("adapt %s: nothing new (%zu samples)\n", o->lang, usable);
		progress(o, "skip\tnothing-new");
		ret = 2; goto out;
	}
	if ((int)ntr < o->min_train || (int)nhe < o->min_held) {
		if (o->verbose) printf("adapt %s: too few samples (%zu to learn from, %zu held back; need %d and %d)\n",
				       o->lang, ntr, nhe, o->min_train, o->min_held);
		progress(o, "skip\ttoo-few\t%d\t%d", o->min_train, o->min_held);
		ret = 2; goto out;
	}
	const struct hw_head *inuse = hw_lang_head(l);
	double before = cer(inuse, m, he, nhe);
	struct hw_head cand;
	if (hw_head_init(&cand, &m->base, (uint32_t)o->rank)) goto out;
	int aborted = 0;
	train(&cand, tr, ntr, o, &aborted);
	if (aborted) {
		if (o->verbose) printf("adapt %s: stopped (unplugged)\n", o->lang);
		progress(o, "skip\tunplugged");
		hw_head_free(&cand);
		ret = 2; goto out;
	}
	double after = cer(&cand, m, he, nhe);
	struct hw_adapt_info info = { m->hash, (uint32_t)o->rank, (uint32_t)ntr, (uint32_t)nhe, (float)after,
				      (float)before, (int64_t)time(NULL), 0 };
	/* its calibration, on the same held-back samples it was checked on */
	info.temp = (float)fit_temp(&cand, he, nhe);
	cand.temp = info.temp;
	int fd = hw_store_lock();
	if (fd < 0) { hw_head_free(&cand); goto out; }
	hw_store_adaptation_dir(rel, sizeof rel, o->lang);
	if (hw_store_generation() != gen) {
		/* erased while this ran: what it learned came from samples that are gone */
		if (o->verbose) printf("adapt %s: samples erased meanwhile, dropped\n", o->lang);
		progress(o, "skip\terased");
		ret = 0;
	} else if (hw_store_mkdir(rel)) {
		ret = -1;
	} else {
		long long newest = -1;
		const char *nid = "-";
		for (size_t i = 0; i < ne; i++)
			if (!strcmp(es[i].meta.lang, o->lang)) { if (es[i].meta.time > newest) newest = es[i].meta.time; nid = es[i].meta.id; }
		int n = snprintf(buf, sizeof buf, "%zu %lu %lld %s\n", usable, gen, newest, nid);
		char srel[192];
		snprintf(srel, sizeof srel, "%s/state", rel);
		hw_store_replace(srel, buf, (size_t)n);
		char line[256];
		n = snprintf(line, sizeof line, "%lld %s %zu %zu %.4f %.4f\n", (long long)info.time,
			     after < before ? "kept" : "rejected", ntr, nhe, before, after);
		char lrel[192], lp[4096];
		snprintf(lrel, sizeof lrel, "%s/log", rel);
		if (!hw_store_path(lp, sizeof lp, lrel)) {
			FILE *lf = fopen(lp, "ae");
			if (lf) { fputs(line, lf); fclose(lf); chmod(lp, 0600); }
		}
		if (after < before) {
			size_t len;
			char *bin = hw_adapt_serialize(&cand, &info, &len);
			char crel[192], prel[192];
			snprintf(crel, sizeof crel, "%s/current", rel);
			snprintf(prel, sizeof prel, "%s/previous", rel);
			struct stat st;
			if (!stat(cur, &st)) rename(cur, prev);
			ret = bin && !hw_store_replace(crel, bin, len) ? 1 : -1;
			free(bin);
		} else {
			ret = 0;
			calibrate_current(l, rel, cur, he, nhe);
		}
		progress(o, "result\t%s\t%.4f\t%.4f\t%zu\t%zu", ret == 1 ? "kept" : ret == 0 ? "rejected" : "error",
			 before, after, ntr, nhe);
		if (o->verbose)
			printf("adapt %s: %s (held-back error %.1f%% -> %.1f%%, %zu learned from, %zu held back, temperature %.2f)\n",
			       o->lang, ret == 1 ? "kept" : "not kept", 100 * before, 100 * after, ntr, nhe, info.temp);
	}
	hw_store_unlock(fd);
	hw_head_free(&cand);
out:
	items_free(tr, ntr); items_free(he, nhe);
	hw_entries_free(es, ne);
	return ret;
}
