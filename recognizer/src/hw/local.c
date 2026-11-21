// SPDX-License-Identifier: GPL-2.0-or-later
// The person's own models (local.h, spec 006): where they are, the checks, the choice.
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "featurize.h"
#include "local.h"
#include "model.h"
#include "recog.h"
#include "store.h"

const char *hw_kind_name(int kind)
{
	return kind == HW_KIND_ALIGNER ? "aligner" : kind == HW_KIND_RECOGNIZER ? "recognizer" : "-";
}

const char *hw_kind_file(int kind)
{
	return kind == HW_KIND_ALIGNER ? "aligner.ghm" : "recognizer.ghm";
}

int hw_kind_of(const char *name)
{
	return !strcmp(name, "recognizer") ? HW_KIND_RECOGNIZER : !strcmp(name, "aligner") ? HW_KIND_ALIGNER : 0;
}

int hw_local_dir(char *out, size_t n, const char *lang)
{
	char d[3900];
	if (hw_local_models_dir(d, sizeof d)) return -1;
	int r = lang ? snprintf(out, n, "%s/%s", d, lang) : snprintf(out, n, "%s", d);
	return r < 0 || (size_t)r >= n ? -1 : 0;
}

int hw_local_path(char *out, size_t n, const char *lang, int kind)
{
	char d[4000];
	if (hw_local_dir(d, sizeof d, lang)) return -1;
	int r = snprintf(out, n, "%s/%s", d, hw_kind_file(kind));
	return r < 0 || (size_t)r >= n ? -1 : 0;
}

int hw_local_chosen(int kind)
{
	if (hw_store_writer()) return 0;        /* a guest reads with the shipped model alone */
	const char *e = getenv("GLASSINE_HANDWRITING_LOCAL");
	if (e && !strcmp(e, "off")) return 0;
	char v[64];
	return hw_conf_get(hw_kind_name(kind), v, sizeof v) && !strcmp(v, "local");
}

/* ---------------------------------------------------------------- the checks */

int hw_model_kind(const struct hw_model *m)
{
	if (m->kind == HW_GHM_KIND_RECOGNIZER) return HW_KIND_RECOGNIZER;
	if (m->kind == HW_GHM_KIND_ALIGNER) return HW_KIND_ALIGNER;
	if (m->kind) return 0;                  /* a kind this engine doesn't know */
	/* a file from before the kind was recorded, by its shape: a recognizer writes the space
	 * between words; an aligner has letters only, the recognizer's own five features and a
	 * frame every two rows */
	return hw_class_of(m, ' ') ? HW_KIND_RECOGNIZER :
	       m->version == HW_GHM_VERSION && m->cfg.nstrided == 1 ? HW_KIND_ALIGNER : 0;
}

static int utf8_dec(const unsigned char **s, const unsigned char *end, uint32_t *cp)
{
	const unsigned char *p = *s;
	if (p >= end) return -1;
	uint32_t c = *p++;
	int extra = c < 0x80 ? 0 : (c >> 5) == 6 ? 1 : (c >> 4) == 14 ? 2 : (c >> 3) == 30 ? 3 : -1;
	if (extra < 0) { *s = p; *cp = 0xfffd; return 0; }
	if (extra) c &= 0x3f >> extra;
	for (int i = 0; i < extra; i++) {
		if (p >= end || (*p & 0xc0) != 0x80) { *s = p; *cp = 0xfffd; return 0; }
		c = c << 6 | (*p++ & 0x3f);
	}
	*s = p;
	*cp = c;
	return 0;
}

static void put_cp(char *out, size_t n, uint32_t c)
{
	size_t l = strlen(out);
	char b[8];
	if (c == ' ') { snprintf(out + l, n - l, "%sspace", l ? " " : ""); return; }
	if (c < 0x80) { b[0] = (char)c; b[1] = 0; }
	else if (c < 0x800) { b[0] = (char)(0xc0 | c >> 6); b[1] = (char)(0x80 | (c & 0x3f)); b[2] = 0; }
	else if (c < 0x10000) { b[0] = (char)(0xe0 | c >> 12); b[1] = (char)(0x80 | (c >> 6 & 0x3f)); b[2] = (char)(0x80 | (c & 0x3f)); b[3] = 0; }
	else { b[0] = (char)(0xf0 | c >> 18); b[1] = (char)(0x80 | (c >> 12 & 0x3f)); b[2] = (char)(0x80 | (c >> 6 & 0x3f)); b[3] = (char)(0x80 | (c & 0x3f)); b[4] = 0; }
	snprintf(out + l, n - l, "%s%s", l ? " " : "", b);
}

/* the characters of the language's prompted texts (the shipped LANG/prompts.txt): their
 * number, -1 when the language has no shipped files */
static int language_chars(const char *lang, uint32_t *cs, int max)
{
	char p[4200];
	snprintf(p, sizeof p, "%s/%s/prompts.txt", hw_models_dir(), lang);
	FILE *f = fopen(p, "re");
	if (!f) return -1;
	size_t cap = 1 << 20, len = 0;            /* the texts are a few KiB */
	unsigned char *buf = malloc(cap);
	if (buf) len = fread(buf, 1, cap, f);
	fclose(f);
	if (!buf) return -1;
	int n = 0;
	const unsigned char *s = buf, *end = buf + len;
	uint32_t c;
	while (!utf8_dec(&s, end, &c)) {
		if (c == '\n' || c == '\r') continue;
		int dup = 0;
		for (int i = 0; i < n && !dup; i++) dup = cs[i] == c;
		if (!dup && n < max) cs[n++] = c;
	}
	free(buf);
	return n;
}

/* a fixed synthetic line: three strokes of loops and a bar, in em */
static int trial_run(const struct hw_model *m)
{
	enum { NS = 3, NP = 60 };
	static double x[NS][NP], y[NS][NP], t[NS][NP];
	struct hw_stroke s[NS];
	for (int j = 0; j < NS; j++) {
		for (int i = 0; i < NP; i++) {
			double a = i * 0.21;
			x[j][i] = j * 1.4 + i * 0.02 + 0.25 * sin(a);
			y[j][i] = 0.5 - 0.3 * cos(a) - (j == 2 ? 0.4 : 0);
			t[j][i] = j * 700.0 + i * 8.0;
		}
		s[j] = (struct hw_stroke){ NP, x[j], y[j], t[j] };
	}
	float *feats = NULL, *frames = NULL, *lp = NULL;
	struct hw_origin *org = NULL;
	long rows;
	if (m->version == HW_GHM_VERSION_REF) {
		double tilt, h, mid, x0;
		if (hw_line_metrics(s, NS, &tilt, &h, &mid, &x0)) return -1;
		rows = hw_features_ref(s, NS, h, NAN, &feats, &org);
	} else
		rows = hw_features(s, NS, &feats, &org);
	int ok = 0;
	if (rows > 0) {
		long T = hw_encode(m, feats, rows, &frames);
		if (T > 0 && (lp = malloc((size_t)T * m->cfg.nclass * sizeof *lp))) {
			hw_head_forward(&m->base, frames, T, lp, NULL, NULL);
			ok = 1;
			for (size_t i = 0; i < (size_t)T * m->cfg.nclass && ok; i++) ok = isfinite(lp[i]);
		}
	}
	free(feats); free(frames); free(lp); free(org);
	return ok ? 0 : -1;
}

#define REFUSE(r, ...) do { snprintf(reason, rn, "%s", r); snprintf(detail, dn, __VA_ARGS__); goto out; } while (0)

int hw_model_check(const char *path, const char *lang, int want, int *kind, char *reason, size_t rn, char *detail,
		   size_t dn)
{
	struct hw_model m;
	int loaded = 0, ret = -1;
	snprintf(reason, rn, "%s", "");
	snprintf(detail, dn, "%s", "");
	struct stat st;
	if (stat(path, &st)) REFUSE(errno == ENOENT ? "missing" : "unreadable", "%s", "");
	if (!S_ISREG(st.st_mode)) REFUSE("unreadable", "%s", "");
	if (st.st_size > HW_LOCAL_MAX_BYTES) REFUSE("too-big", "%lld", (long long)st.st_size);
	char err[128];
	if (hw_model_load(&m, path, err, sizeof err)) {
		if (!strcmp(err, "not a model")) REFUSE("not-a-model", "%s", "");
		if (!strncmp(err, "model version ", 14)) REFUSE("format-version", "%s", err + 14);
		if (strstr(err, " features in a version ")) REFUSE("features", "%s", "");
		if (!strcmp(err, "too short") || !strcmp(err, "bad header") || !strcmp(err, "bad charset") ||
		    !strncmp(err, "charset is not", 14) || !strncmp(err, "size doesn't match", 18))
			REFUSE("damaged", "%s", "");
		REFUSE(errno == ENOENT ? "missing" : "unreadable", "%s", "");
	}
	loaded = 1;
	uint32_t nc = m.cfg.nclass - 1;
	for (uint32_t k = 0; k < nc; k++) {
		uint32_t c = m.charset[k];
		if (c < 0x20 || (c >= 0x7f && c < 0xa0) || c == 0xfffd || (c >= 0xd800 && c < 0xe000) || c > 0x10ffff) {
			snprintf(detail, dn, "U+%04X", c);
			snprintf(reason, rn, "alphabet");
			goto out;
		}
		for (uint32_t i = 0; i < k; i++) if (m.charset[i] == c) {
			snprintf(detail, dn, "twice");
			put_cp(detail, dn, c);
			snprintf(reason, rn, "alphabet");
			goto out;
		}
	}
	int k = hw_model_kind(&m);
	if (!k) REFUSE("kind-unknown", "%s", "");
	if (want && k != want) REFUSE("kind", "%s", hw_kind_name(k));
	/* the engine runs an aligner of one shape only (aligner.h) */
	if (k == HW_KIND_ALIGNER && !(m.version == HW_GHM_VERSION && m.cfg.nstrided == 1)) REFUSE("features", "%s", "");
	uint32_t want_cs[512];
	int nl = language_chars(lang, want_cs, 512);
	if (nl < 0) REFUSE("language", "%s", lang);
	int missing = 0;
	for (int i = 0; i < nl; i++) {
		if (k == HW_KIND_ALIGNER && want_cs[i] == ' ') continue;
		if (hw_class_of(&m, want_cs[i])) continue;
		if (missing++ < 8) put_cp(detail, dn, want_cs[i]);
	}
	if (k == HW_KIND_RECOGNIZER && !hw_class_of(&m, ' ') && missing++ < 8) put_cp(detail, dn, ' ');
	if (missing) {
		if (missing > 8) { size_t l = strlen(detail); snprintf(detail + l, dn - l, " +%d", missing - 8); }
		snprintf(reason, rn, "alphabet");
		goto out;
	}
	for (size_t i = 0; i < m.ndata; i++) if (!isfinite(m.data[i])) REFUSE("not-finite", "%s", "");
	if (trial_run(&m)) REFUSE("not-finite", "%s", "");
	if (kind) *kind = k;
	ret = 0;
out:
	if (loaded) hw_model_free(&m);
	return ret;
}

/* ---------------------------------------------------------------- resolving */

/* the verdict on each local file, by its identity: checked once per change */
static struct { char lang[36]; int kind; char path[4300]; struct stat st; int ok; char why[32], detail[200]; } seen[8];
static int nseen;

static int same_file(const struct stat *a, const struct stat *b)
{
	return a->st_ino == b->st_ino && a->st_dev == b->st_dev && a->st_size == b->st_size &&
	       a->st_mtim.tv_sec == b->st_mtim.tv_sec && a->st_mtim.tv_nsec == b->st_mtim.tv_nsec;
}

void hw_model_resolve(const char *lang, int kind, struct hw_resolved *r)
{
	memset(r, 0, sizeof *r);
	if (hw_local_chosen(kind)) {
		char lp[4300];
		struct stat st;
		if (hw_local_path(lp, sizeof lp, lang, kind) || stat(lp, &st)) snprintf(r->why, sizeof r->why, "missing");
		else {
			int i = 0;
			for (; i < nseen; i++)
				if (seen[i].kind == kind && !strcmp(seen[i].lang, lang) && !strcmp(seen[i].path, lp) &&
				    same_file(&seen[i].st, &st)) break;
			if (i == nseen) {
				if (nseen == 8) { memmove(seen, seen + 1, sizeof seen - sizeof *seen); nseen--; }
				i = nseen++;
				memset(&seen[i], 0, sizeof seen[i]);
				snprintf(seen[i].lang, sizeof seen[i].lang, "%s", lang);
				snprintf(seen[i].path, sizeof seen[i].path, "%s", lp);
				seen[i].kind = kind;
				seen[i].st = st;
				seen[i].ok = !hw_model_check(lp, lang, kind, NULL, seen[i].why, sizeof seen[i].why,
							     seen[i].detail, sizeof seen[i].detail);
			}
			if (seen[i].ok) {
				snprintf(r->path, sizeof r->path, "%s", lp);
				r->st = st;
				r->local = 1;
				return;
			}
			snprintf(r->why, sizeof r->why, "%s", seen[i].why);
			snprintf(r->detail, sizeof r->detail, "%s", seen[i].detail);
		}
	}
	snprintf(r->path, sizeof r->path, "%s/%s/%s", hw_models_dir(), lang, hw_kind_file(kind));
	if (stat(r->path, &r->st)) memset(&r->st, 0, sizeof r->st);
}

int hw_resolved_same(const struct hw_resolved *a, const struct hw_resolved *b)
{
	return !strcmp(a->path, b->path) && a->local == b->local && same_file(&a->st, &b->st);
}
