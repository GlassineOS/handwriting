// SPDX-License-Identifier: GPL-2.0-or-later
// glassine-handwriting: the handwriting recognizer (GlassineOS/handwriting spec 001).
// The contract, the files and the command line are in docs/recognizer.md; in short:
//
//   glassine-handwriting [-i IDLE_S] [--no-adapt]   serve: requests on stdin, replies on
//                                                   stdout, one line each (contract 1)
//   glassine-handwriting samples stats|list|erase|export FILE|import FILE
//   glassine-handwriting learning [on|off]
//   glassine-handwriting adapt [-f] [-l LANG] ...    one adaptation (idle and charging)
//   glassine-handwriting adaptation [show|revert] [-l LANG]
//   glassine-handwriting donation start LICENCE|add ID|list|samples|earlier|donor [new]|write FILE|erase|prompts
//   glassine-handwriting letters build [-f] [-r]|status|show   the letter model (spec 003)
//   glassine-handwriting font build [-n NAME] FILE|install [-n NAME]|remove|status
//   glassine-handwriting read [-n N] [-l LANG] FILE  (host: read sample-format lines)
//   glassine-handwriting bench [-l LANG] FILE        (host: time per line, memory)
//
// Base models: /usr/share/glassine/handwriting/LANG/recognizer.ghm (or the directory in
// $GLASSINE_HANDWRITING_MODELS). The person's samples and adaptation:
// $XDG_DATA_HOME/glassine/handwriting (0700). No network, and nothing written is ever
// printed to stderr.
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "hw/adapt.h"
#include "hw/gesture.h"
#include "hw/letters.h"
#include "hw/recog.h"
#include "hw/size.h"
#include "hw/sample.h"
#include "hw/store.h"
#include "hw/words.h"

#define VERSION 1
#define MAX_LINE (4 << 20)      /* bytes of one request line */
#define MAX_BATCH 4096         /* lines answered together (a read drops older reads among them) */
#define MAX_SESSIONS 16
#define MAX_STROKES 4096
#define MAX_POINTS 100000
#define MAX_CANDS 8
#define IDLE_DEFAULT 300
#define FIT_OK 0.6              /* a prompted alignment's score from which it passes the check */

/* ---------------------------------------------------------------- output */

struct buf { char *s; size_t len, cap; };

static void bput(struct buf *b, const char *s, size_t n)
{
	if (b->len + n + 1 > b->cap) {
		size_t c = (b->len + n + 1) * 2;
		char *x = realloc(b->s, c);
		if (!x) return;
		b->s = x; b->cap = c;
	}
	memcpy(b->s + b->len, s, n);
	b->len += n;
	b->s[b->len] = 0;
}

static void bstr(struct buf *b, const char *s) { bput(b, s, strlen(s)); }

static void bfmt(struct buf *b, const char *fmt, ...)
{
	char t[512];
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(t, sizeof t, fmt, ap);
	va_end(ap);
	if (n > 0) bput(b, t, (size_t)n < sizeof t ? (size_t)n : sizeof t - 1);
}

/* a text field: backslash, tab, newline, carriage return escaped */
static void besc(struct buf *b, const char *s)
{
	for (; *s; s++) {
		switch (*s) {
		case '\\': bput(b, "\\\\", 2); break;
		case '\t': bput(b, "\\t", 2); break;
		case '\n': bput(b, "\\n", 2); break;
		case '\r': bput(b, "\\r", 2); break;
		default: bput(b, s, 1);
		}
	}
}

static struct buf out;

static void flush_out(void)
{
	size_t done = 0;
	while (done < out.len) {
		ssize_t w = write(1, out.s + done, out.len - done);
		if (w < 0 && errno == EINTR) continue;
		if (w <= 0) exit(0);            /* the client is gone */
		done += (size_t)w;
	}
	out.len = 0;
}

static void reply(const char *fmt, ...)
{
	char t[1024];
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(t, sizeof t, fmt, ap);
	va_end(ap);
	bput(&out, t, (size_t)n < sizeof t ? (size_t)n : sizeof t - 1);
	bput(&out, "\n", 1);
}

/* ---------------------------------------------------------------- fields */

static int unescape(char *s)
{
	char *o = s;
	for (; *s; s++) {
		if (*s != '\\') { *o++ = *s; continue; }
		s++;
		if (*s == '\\') *o++ = '\\';
		else if (*s == 't') *o++ = '\t';
		else if (*s == 'n') *o++ = '\n';
		else if (*s == 'r') *o++ = '\r';
		else return -1;
	}
	*o = 0;
	return 0;
}

static int valid_name(const char *s, size_t max)
{
	size_t n = strlen(s);
	if (!n || n > max) return 0;
	for (; *s; s++)
		if (!((*s >= 'a' && *s <= 'z') || (*s >= 'A' && *s <= 'Z') || (*s >= '0' && *s <= '9') ||
		      *s == '.' || *s == '_' || *s == '-'))
			return 0;
	return 1;
}

static int valid_lang(const char *s)
{
	size_t n = strlen(s), i = 0;
	if (n < 2 || n > 35) return 0;
	while (s[i] >= 'a' && s[i] <= 'z') i++;
	if (i < 2 || i > 3) return 0;
	if (!s[i]) return 1;
	if (s[i] != '-' && s[i] != '_') return 0;
	for (i++; s[i]; i++)
		if (!((s[i] >= 'a' && s[i] <= 'z') || (s[i] >= 'A' && s[i] <= 'Z') || (s[i] >= '0' && s[i] <= '9') ||
		      s[i] == '-' || s[i] == '_'))
			return 0;
	return 1;
}

/* FIELD: the text-input-v3 purpose and hints. What the recognizer may do there. */
enum { FIELD_PRIVATE, FIELD_NOLEARN, FIELD_LEARN };

static int field_rule(const char *f, int *bad)
{
	*bad = 0;
	if (!*f || !strcmp(f, "-")) return FIELD_LEARN;          /* not a text field: an app's own ink */
	char t[256];
	if (strlen(f) >= sizeof t) { *bad = 1; return FIELD_PRIVATE; }
	strcpy(t, f);
	for (char *c = t; *c; c++) if (!((*c >= 'a' && *c <= 'z') || *c == '_' || *c == ',')) { *bad = 1; return FIELD_PRIVATE; }
	static const char *hints_ok[] = { "completion", "spellcheck", "auto_capitalization", "lowercase", "uppercase",
					  "titlecase", "latin", "multiline", NULL };
	int rule = FIELD_LEARN, first = 1;
	for (char *p = strtok(t, ","); p; p = strtok(NULL, ","), first = 0) {
		if (first) {
			if (!strcmp(p, "password") || !strcmp(p, "pin")) return FIELD_PRIVATE;
			if (!strcmp(p, "normal") || !strcmp(p, "alpha") || !strcmp(p, "name")) continue;
			if (!strcmp(p, "email") || !strcmp(p, "url")) {
				char v[64] = "";
				/* addresses are learned only when the keyboard's setting says so */
				const char *x = getenv("XDG_CONFIG_HOME"), *h = getenv("HOME");
				char path[4096];
				if (x && *x == '/') snprintf(path, sizeof path, "%s/glassine/predict.conf", x);
				else snprintf(path, sizeof path, "%s/.config/glassine/predict.conf", h ? h : "");
				FILE *c = fopen(path, "re");
				char line[256];
				while (c && fgets(line, sizeof line, c)) {
					char *s = line;
					while (*s == ' ') s++;
					if (strncmp(s, "addresses", 9)) continue;
					s += 9;
					while (*s == ' ') s++;
					if (*s++ != '=') continue;
					while (*s == ' ') s++;
					snprintf(v, sizeof v, "%.*s", (int)strcspn(s, " \t\r\n"), s);
				}
				if (c) fclose(c);
				if (strcmp(v, "learn")) rule = FIELD_NOLEARN;
				continue;
			}
			rule = FIELD_NOLEARN;    /* terminal, digits, number, phone, date, time, datetime, unknown */
			continue;
		}
		if (!strcmp(p, "sensitive_data") || !strcmp(p, "hidden_text")) return FIELD_PRIVATE;
		int known = 0;
		for (int i = 0; hints_ok[i]; i++) if (!strcmp(p, hints_ok[i])) known = 1;
		if (!known) rule = FIELD_NOLEARN;
	}
	return rule;
}

/* whether readings in FIELD may use the words: prose only (purpose normal, or not a text
 * field), no hint that isn't about prose, and the setting `words` not off */
static int words_field(const char *f)
{
	if (*f && strcmp(f, "-")) {
		char t[256];
		if (strlen(f) >= sizeof t) return 0;
		strcpy(t, f);
		static const char *hints_ok[] = { "completion", "spellcheck", "auto_capitalization", "lowercase",
						  "uppercase", "titlecase", "latin", "multiline", NULL };
		int first = 1;
		for (char *p = strtok(t, ","); p; p = strtok(NULL, ","), first = 0) {
			if (first) { if (strcmp(p, "normal")) return 0; continue; }
			int known = 0;
			for (int i = 0; hints_ok[i]; i++) if (!strcmp(p, hints_ok[i])) known = 1;
			if (!known) return 0;
		}
	}
	char v[32];
	return !hw_conf_get("words", v, sizeof v) || strcmp(v, "off");
}

/* LANGS -> the first language with a model: the request's list, else the setting, else
 * the locale, else English */
static struct hw_lang *pick_lang(const char *langs, int *bad)
{
	char t[256], conf[256];
	*bad = 0;
	if (!*langs || !strcmp(langs, "-")) {
		if (hw_conf_get("languages", conf, sizeof conf)) langs = conf;
		else {
			const char *v = getenv("LC_ALL");
			if (!v || !*v) v = getenv("LC_MESSAGES");
			if (!v || !*v) v = getenv("LANG");
			if (v && v[0] >= 'a' && v[0] <= 'z' && v[1] >= 'a' && v[1] <= 'z') {
				snprintf(conf, sizeof conf, "%.*s", (int)strcspn(v, "_.@"), v);
				langs = conf;
			} else {
				langs = "en";
			}
		}
	}
	if (strlen(langs) >= sizeof t) { *bad = 1; return NULL; }
	strcpy(t, langs);
	for (char *p = strtok(t, ", "); p; p = strtok(NULL, ", ")) {
		if (!valid_lang(p)) { *bad = 1; return NULL; }
		char l[36];
		snprintf(l, sizeof l, "%.*s", (int)strcspn(p, "-_"), p);   /* en-GB -> en */
		struct hw_lang *x = hw_lang_get(p);
		if (!x) x = hw_lang_get(l);
		if (x) return x;
	}
	return NULL;
}

/* ---------------------------------------------------------------- sessions */

struct session {
	char name[33];
	char langs[128];
	char field[256];
	int rule;
	struct hw_units units;
	char *before;
	struct hw_ink *ink;
	int *ids;
	size_t n, cap;
	int next_id;
	int width;                      /* numbers per point: 3, or 4 with pressure; 0 none yet */
	int pressure_all, tilt_all;
	struct hw_area area;            /* the writing area, when the client said (area) */
	int has_base;                   /* and its first guide line (area BASE) */
	double base;
	int words;                      /* readings use the words (a prose field, the setting on) */
	char written[8], pair[17];      /* the marking its kept samples get (mark), "" none */
	/* the last reply's candidates, for a pick: text, where each came from */
	char *cand[MAX_CANDS];
	int cand_from[MAX_CANDS], ncand;
};

static struct session sessions[MAX_SESSIONS];
static char client[HW_CLIENT_MAX] = "-";

static struct session *find_session(const char *name)
{
	for (int i = 0; i < MAX_SESSIONS; i++) if (sessions[i].name[0] && !strcmp(sessions[i].name, name)) return &sessions[i];
	return NULL;
}

static void cands_clear(struct session *s)
{
	for (int i = 0; i < s->ncand; i++) free(s->cand[i]);
	s->ncand = 0;
}

static void session_clear(struct session *s)
{
	cands_clear(s);
	for (size_t i = 0; i < s->n; i++) hw_ink_free(&s->ink[i]);
	s->n = 0;
	s->width = 0;
	s->pressure_all = 1;
	s->tilt_all = 1;
}

static void session_end(struct session *s)
{
	session_clear(s);
	free(s->ink); free(s->ids); free(s->before);
	memset(s, 0, sizeof *s);
}

/* UNITS: em, device, device:PER_MM or device:PER_MM:RATE */
static int parse_units(const char *u, struct hw_units *o)
{
	memset(o, 0, sizeof *o);
	if (!strcmp(u, "em")) { strcpy(o->xy, "em"); return 0; }
	if (strncmp(u, "device", 6)) return -1;
	strcpy(o->xy, "device");
	const char *p = u + 6;
	if (!*p) return 0;
	if (*p++ != ':') return -1;
	char *e;
	o->per_mm = strtod(p, &e);
	if (e == p || !isfinite(o->per_mm) || o->per_mm <= 0) return -1;
	if (!*e) return 0;
	if (*e++ != ':') return -1;
	p = e;
	o->rate = strtod(p, &e);
	if (e == p || *e || !isfinite(o->rate) || o->rate <= 0) return -1;
	return 0;
}

/* POINTS: x,y,t[,p[,tx,ty]] separated by spaces; t in milliseconds, tilt in degrees */
static int parse_points(char *s, struct hw_ink *k, int *width)
{
	memset(k, 0, sizeof *k);
	int w = 0;
	for (char *p = strtok(s, " "); p; p = strtok(NULL, " ")) {
		double v[6] = { 0, 0, 0, NAN, NAN, NAN };
		int n = 0;
		char *e = p;
		while (n < 6) {
			char *q = e;
			v[n] = strtod(q, &e);
			if (e == q || !isfinite(v[n])) { hw_ink_free(k); return -1; }
			n++;
			if (*e == ',') { e++; continue; }
			break;
		}
		if (*e || n < 3 || n == 5) { hw_ink_free(k); return -1; }
		if (!w) w = n;
		if (n != w) { hw_ink_free(k); return -1; }
		if (k->n >= MAX_POINTS || hw_ink_add_tilt(k, v[0], v[1], v[2] / 1000.0, v[3], v[4], v[5])) { hw_ink_free(k); return -1; }
	}
	if (!k->n) return -1;
	*width = w;
	return 0;
}

static int session_add(struct session *s, struct hw_ink *k, int width)
{
	if (s->n >= MAX_STROKES) return -1;
	if (s->n == s->cap) {
		size_t c = s->cap ? s->cap * 2 : 16;
		struct hw_ink *x = realloc(s->ink, c * sizeof *x);
		if (!x) return -1;
		s->ink = x;
		int *y = realloc(s->ids, c * sizeof *y);
		if (!y) return -1;
		s->ids = y;
		s->cap = c;
	}
	if (width < 4) s->pressure_all = 0;
	if (width != 6) s->tilt_all = 0;
	s->ink[s->n] = *k;
	s->ids[s->n] = s->next_id++;
	s->n++;
	return s->ids[s->n - 1];
}

static void session_remove(struct session *s, size_t i)
{
	hw_ink_free(&s->ink[i]);
	memmove(s->ink + i, s->ink + i + 1, (s->n - i - 1) * sizeof *s->ink);
	memmove(s->ids + i, s->ids + i + 1, (s->n - i - 1) * sizeof *s->ids);
	s->n--;
}

static struct hw_stroke *strokes_of(const struct hw_ink *ink, size_t n, size_t **np)
{
	struct hw_stroke *st = calloc(n + 1, sizeof *st);
	*np = calloc(n + 1, sizeof **np);
	if (!st || !*np) { free(st); free(*np); return NULL; }
	for (size_t i = 0; i < n; i++) {
		st[i] = (struct hw_stroke){ ink[i].n, ink[i].x, ink[i].y, ink[i].t };
		(*np)[i] = ink[i].n;
	}
	return st;
}

/* ---------------------------------------------------------------- reading */

static double gesture_scale(const struct hw_stroke *st, size_t n, size_t g, const struct hw_units *u)
{
	size_t pts = 0;
	for (size_t i = 0; i < n; i++) if (i != g) pts += st[i].n;
	if (pts >= 10) {
		struct hw_stroke *o = malloc(n * sizeof *o);
		size_t m = 0;
		for (size_t i = 0; o && i < n; i++) if (i != g) o[m++] = st[i];
		double tilt, core = 0, mid, x0;
		int r = o ? hw_line_metrics(o, m, &tilt, &core, &mid, &x0) : -1;
		free(o);
		if (!r && core > 0) return core;
	}
	if (!strcmp(u->xy, "em")) return 0.45;
	if (u->per_mm > 0) return 3.0 * u->per_mm;
	return 0;
}

static void text_of(const unsigned *cp, int n, char *o, size_t max)
{
	size_t k = 0;
	for (int i = 0; i < n && k + 5 < max; i++) k += hw_utf8_encode(cp[i], o + k);
	o[k] = 0;
}

/* the session's size reference (FR-017) */
static void size_ref(const struct hw_lang *l, const struct session *s, struct hw_size_ref *ref)
{
	hw_size_ref_for(l->lang, &s->units, &s->area, s->has_base ? s->base : NAN, ref);
}

/* candidates (and their alignment) for ink; appends "\tTEXT\tSCORE\tALIGN" per candidate */
static int candidates(struct hw_lang *l, struct session *s, int raw, int want, struct buf *b, int *ncand)
{
	const struct hw_ink *ink = s->ink;
	const int *ids = s->ids;
	size_t n = s->n;
	size_t *np;
	struct hw_stroke *st = strokes_of(ink, n, &np);
	if (!st) return -1;
	struct hw_run r;
	struct hw_size_ref ref;
	size_ref(l, s, &ref);
	int ret = hw_run_sized(l, st, n, &ref, &r);
	*ncand = 0;
	if (ret == 0 && r.rows > 0) {
		r.npoints = np;
		struct hw_reading rd;
		struct hw_context x;
		if (s->words && !raw) hw_context_of(hw_words_get(l->lang), s->before, &x);
		hw_read_words(l, &r, s->words && !raw && x.w ? &x : NULL, want, &rd);
		*ncand = rd.n;
		cands_clear(s);
		for (int c = 0; c < rd.n; c++) {
			char text[4 * HW_MAX_LABELS + 1];
			text_of(rd.c[c].cp, rd.c[c].len, text, sizeof text);
			if (c < MAX_CANDS && (s->cand[c] = strdup(text))) { s->cand_from[c] = rd.c[c].from; s->ncand = c + 1; }
			bput(b, "\t", 1);
			besc(b, text);
			bfmt(b, "\t%.3f\t", rd.c[c].score);
			struct hw_charspan *cs = NULL;
			long nc = hw_align(l, &r, rd.c[c].cp, (size_t)rd.c[c].len, &cs);
			for (long k = 0; k < nc; k++) {
				char *a = NULL;
				size_t al = 0, ac = 0;
				if (k) bstr(b, ";");
				hw_span_text(&r, &cs[k], ids, &a, &al, &ac);
				if (a) bstr(b, a);
				free(a);
			}
			free(cs);
		}
	}
	hw_run_free(&r);
	free(st); free(np);
	return ret < 0 ? -1 : 0;
}

/* the characters of the best reading of the other strokes that the covered strokes make
 * ("A-B"), or for a caret the position before which it inserts ("@K") */
static void gesture_chars(struct hw_lang *l, const struct session *s, size_t g, const struct hw_gesture *ge, int raw,
			  struct buf *b)
{
	size_t n = 0, *np;
	struct hw_ink *ink = malloc(s->n * sizeof *ink);
	int *map = malloc(s->n * sizeof *map);
	if (!ink || !map) { free(ink); free(map); bstr(b, "-"); return; }
	for (size_t i = 0; i < s->n; i++) if (i != g) { ink[n] = s->ink[i]; map[n] = (int)i; n++; }
	struct hw_stroke *st = n ? strokes_of(ink, n, &np) : NULL;
	struct hw_run r;
	int done = 0;
	if (st && !hw_run(l, st, n, &r) && r.rows > 0) {
		r.npoints = np;
		struct hw_reading rd;
		struct hw_charspan *cs = NULL;
		struct hw_context x;
		if (s->words && !raw) hw_context_of(hw_words_get(l->lang), s->before, &x);
		if (hw_read_words(l, &r, s->words && !raw && x.w ? &x : NULL, 1, &rd) == 1) {
			long nc = hw_align(l, &r, rd.c[0].cp, (size_t)rd.c[0].len, &cs);
			long lo = -1, hi = -1, before = 0;
			for (long k = 0; k < nc; k++) {
				int *ss = hw_span_strokes(&r, cs, (size_t)nc, (size_t)k);
				int hit = 0, all = ss && ss[0] >= 0;
				for (int *p = ss; p && *p >= 0; p++) {
					int cov = 0;
					for (int j = 0; j < ge->ncovers; j++) if (ge->covers[j] == map[*p]) cov = 1;
					hit |= cov;
					all &= cov;
				}
				free(ss);
				if (hit && rd.c[0].cp[k] != ' ') { if (lo < 0) lo = k; hi = k; }
				if (all) before = k + 1;
			}
			if (ge->kind == HW_GESTURE_CARET) { bfmt(b, "@%ld", before); done = 1; }
			else if (lo >= 0) { bfmt(b, "%ld-%ld", lo, hi); done = 1; }
		}
		free(cs);
		hw_run_free(&r);
	}
	if (st) { free(st); free(np); }
	free(ink); free(map);
	if (!done) bstr(b, ge->kind == HW_GESTURE_CARET ? "@0" : "-");
}

static void do_read(const char *id, struct session *s, int want, int raw)
{
	int bad;
	if (!s->n) { reply("read\t%s\tempty", id); return; }
	struct hw_lang *l = pick_lang(s->langs, &bad);
	if (!l) { reply("error\t%s\tno-model", id); return; }
	struct buf b = { 0 };
	/* a gesture first: the newest stroke */
	size_t *np;
	struct hw_stroke *st = strokes_of(s->ink, s->n, &np);
	if (!st) { reply("error\t%s\tmemory", id); return; }
	struct hw_gesture *ge = malloc(sizeof *ge);
	size_t g = s->n - 1;
	if (ge && hw_gesture(st, s->n, g, gesture_scale(st, s->n, g, &s->units), ge)) {
		bfmt(&b, "read\t%s\tgesture\t%s\t%d\t%.6g,%.6g,%.6g,%.6g\t", id, hw_gesture_name(ge->kind), s->ids[g],
		     ge->bbox[0], ge->bbox[1], ge->bbox[2], ge->bbox[3]);
		for (int j = 0; j < ge->ncovers; j++) bfmt(&b, "%s%d", j ? "," : "", s->ids[ge->covers[j]]);
		if (!ge->ncovers) bstr(&b, "-");
		bstr(&b, "\t");
		gesture_chars(l, s, g, ge, raw, &b);
		session_remove(s, g);       /* a gesture is not ink to read */
		bstr(&out, b.s);
		bput(&out, "\n", 1);
		free(b.s); free(ge); free(st); free(np);
		return;
	}
	free(ge); free(st); free(np);
	struct buf c = { 0 };
	int nc;
	if (candidates(l, s, raw, want, &c, &nc)) { reply("error\t%s\tmemory", id); free(c.s); return; }
	bfmt(&b, "read\t%s\ttext\t%d", id, nc);
	if (c.s) bstr(&b, c.s);
	bstr(&out, b.s);
	bput(&out, "\n", 1);
	free(b.s); free(c.s);
}

static void do_align(const char *id, struct session *s, const char *prompt)
{
	int bad;
	struct hw_lang *l = pick_lang(s->langs, &bad);
	if (!l) { reply("error\t%s\tno-model", id); return; }
	unsigned cp[HW_MAX_LABELS];
	size_t n = hw_utf8_decode(prompt, cp, HW_MAX_LABELS);
	if (!n || n > HW_MAX_LABELS) { reply("error\t%s\tbad-prompt", id); return; }
	if (!s->n) { reply("align\t%s\tshort", id); return; }
	size_t *np;
	struct hw_stroke *st = strokes_of(s->ink, s->n, &np);
	struct hw_run r;
	struct hw_size_ref ref;
	size_ref(l, s, &ref);
	if (!st || hw_run_sized(l, st, s->n, &ref, &r) || r.rows <= 0) { free(st); free(np); reply("align\t%s\tshort", id); return; }
	r.npoints = np;
	struct hw_charspan *cs;
	long nc = hw_align(l, &r, cp, n, &cs);
	if (nc < 0) { reply("align\t%s\tshort", id); hw_run_free(&r); free(st); free(np); return; }
	double sum = 0;
	int m = 0;
	for (long k = 0; k < nc; k++) if (cs[k].fit >= 0 && cp[k] != ' ') { sum += cs[k].fit; m++; }
	struct buf b = { 0 };
	bfmt(&b, "align\t%s\t%.2f\t%ld", id, m ? sum / m : 0.0, nc);
	for (long k = 0; k < nc; k++) {
		if (cs[k].fit < 0 || cp[k] == ' ') bstr(&b, "\t-\t");
		else bfmt(&b, "\t%.2f\t", cs[k].fit);
		char *a = NULL;
		size_t al = 0, ac = 0;
		hw_span_text(&r, &cs[k], s->ids, &a, &al, &ac);
		if (a) bstr(&b, a);
		free(a);
	}
	bstr(&out, b.s);
	bput(&out, "\n", 1);
	free(b.s); free(cs);
	hw_run_free(&r);
	free(st); free(np);
}

/* the session's ink and text as a sample (spans from the alignment). Prompted writing
 * (the text was shown before it was written): spans exact when the alignment fits, and
 * the reading at the time of writing beside it; *exact says which. */
/* the check's threshold; $GLASSINE_HANDWRITING_FIT_OK replaces it (tests, with a model whose
 * fits are noise) */
static double fit_ok(void)
{
	const char *e = getenv("GLASSINE_HANDWRITING_FIT_OK");
	char *end;
	double v = e ? strtod(e, &end) : FIT_OK;
	return e && (end == e || !isfinite(v)) ? FIT_OK : v;
}

/* keep: the person kept the sample although the alignment failed the check (its spans are
 * then inferred); without it such a sample isn't made (*reason 3, *fit its score) */
/* gate: a pick the ink alone didn't read (a word from the list, proposed one edit from a
 * reading, or found with the words guiding the search) is kept only when its alignment passes the check and no character fits below
 * PICK_MIN; failing, no sample (*reason 3). pick_rank > 0: the candidate picked (1 = the
 * first) and where it came from. */
#define PICK_MIN 0.3
static char *make_sample(struct hw_lang *l, const struct session *s, const char *text, const char *source, int prompted,
			 int keep, int gate, int pick_rank, const char *pick_from, int *exact, double *fit, int *reason)
{
	unsigned cp[HW_MAX_LABELS];
	size_t n = hw_utf8_decode(text, cp, HW_MAX_LABELS);
	*reason = 0;
	*exact = 0;
	if (!n || n > HW_MAX_LABELS) { *reason = 1; return NULL; }
	size_t *np;
	struct hw_stroke *st = strokes_of(s->ink, s->n, &np);
	struct hw_run r;
	struct hw_size_ref ref;
	size_ref(l, s, &ref);
	if (!st || hw_run_sized(l, st, s->n, &ref, &r) || r.rows <= 0) { free(st); free(np); *reason = 2; return NULL; }
	r.npoints = np;
	struct hw_charspan *cs;
	long nc = hw_align(l, &r, cp, n, &cs);
	char *line = NULL;
	if (nc < 0) { *reason = 2; goto out; }
	struct hw_sample smp = { 0 };
	smp.text = (char *)text;
	snprintf(smp.lang, sizeof smp.lang, "%s", l->lang);
	snprintf(smp.source, sizeof smp.source, "%s", source);
	if (prompted) {
		double sum = 0;
		int m = 0;
		for (long k = 0; k < nc; k++) if (cs[k].fit >= 0 && cp[k] != ' ') { sum += cs[k].fit; m++; }
		*fit = m ? sum / m : 0;
		*exact = m && *fit >= fit_ok();
		if (!*exact && !keep) { *reason = 3; free(cs); goto out; }
	} else if (gate) {
		double sum = 0, lo = 1;
		int m = 0;
		for (long k = 0; k < nc; k++)
			if (cs[k].fit >= 0 && cp[k] != ' ') { sum += cs[k].fit; m++; if (cs[k].fit < lo) lo = cs[k].fit; }
		*fit = m ? sum / m : 0;
		if (!m || *fit < fit_ok() || lo < PICK_MIN) { *reason = 3; free(cs); goto out; }
	}
	strcpy(smp.spans_source, *exact ? "exact" : "inferred");
	smp.units = s->units;
	smp.strokes = s->ink;
	smp.nstrokes = s->n;
	smp.pressure = s->pressure_all;
	smp.tilt = s->tilt_all;
	smp.area = s->area;
	strcpy(smp.written, s->written);
	strcpy(smp.pair, s->pair);
	smp.pick_rank = pick_rank;
	snprintf(smp.pick_from, sizeof smp.pick_from, "%s", pick_rank > 0 ? pick_from : "");
	struct hw_reading rd = { 0 };
	char texts[3][4 * HW_MAX_LABELS + 1];
	if (prompted) {
		hw_read(l, &r, 3, &rd);
		for (int c = 0; c < rd.n && c < 3; c++) {
			text_of(rd.c[c].cp, rd.c[c].len, texts[c], sizeof texts[c]);
			smp.reading.text[c] = texts[c];
			smp.reading.score[c] = round(rd.c[c].score * 1000) / 1000;
			smp.reading.n = c + 1;
		}
	}
	smp.spans = calloc((size_t)nc + 1, sizeof *smp.spans);
	smp.nspans = (size_t)nc;
	/* times start at 0 for each sample; to the microsecond, below any pen's clock, so a
	 * far origin leaves no rounding noise */
	double t0 = INFINITY;
	struct hw_ink *ink = calloc(s->n + 1, sizeof *ink);
	for (size_t i = 0; i < s->n; i++) for (size_t k = 0; k < s->ink[i].n; k++) if (s->ink[i].t[k] < t0) t0 = s->ink[i].t[k];
	for (size_t i = 0; ink && i < s->n; i++) {
		ink[i] = s->ink[i];
		ink[i].t = malloc((s->ink[i].n + 1) * sizeof *ink[i].t);
		for (size_t k = 0; ink[i].t && k < s->ink[i].n; k++) ink[i].t[k] = round((s->ink[i].t[k] - t0) * 1e6) / 1e6;
	}
	smp.strokes = ink;
	int ok = ink != NULL;
	for (size_t i = 0; ok && i < s->n; i++) ok = ink[i].t != NULL;
	for (long k = 0; smp.spans && k < nc; k++) smp.spans[k] = cp[k] == ' ' ? NULL : hw_span_strokes(&r, cs, (size_t)nc, (size_t)k);
	line = smp.spans && ok ? hw_sample_format(&smp) : NULL;
	for (size_t i = 0; ink && i < s->n; i++) free(ink[i].t);
	free(ink);
	for (long k = 0; smp.spans && k < nc; k++) free(smp.spans[k]);
	free(smp.spans);
	free(cs);
out:
	hw_run_free(&r);
	free(st); free(np);
	return line;
}

static void do_confirm(const char *id, struct session *s, const char *source, const char *text, int keep)
{
	const char *src = !strcmp(source, "picked") || !strcmp(source, "confirmed") ? "confirmed" :
			  !strcmp(source, "corrected") ? "corrected" : !strcmp(source, "prompted") ? "prompted" : NULL;
	if (!src) { reply("error\t%s\tbad-source", id); return; }
	if (s->rule != FIELD_LEARN) { reply("confirm\t%s\tskip\tfield", id); return; }
	if (!hw_learning_on()) { reply("confirm\t%s\tskip\tlearning", id); return; }
	if (!s->n || !*text) { reply("confirm\t%s\tskip\tempty", id); return; }
	int bad, why;
	struct hw_lang *l = pick_lang(s->langs, &bad);
	if (!l) { reply("confirm\t%s\tskip\tno-model", id); return; }
	int prompted = !strcmp(src, "prompted"), exact;
	/* a pick: which candidate of the last reading it was */
	int rank = 0, from = -1;
	if (!strcmp(src, "confirmed"))
		for (int i = 0; i < s->ncand && !rank; i++) if (!strcmp(s->cand[i], text)) { rank = i + 1; from = s->cand_from[i]; }
	int gate = from == HW_FROM_WORDS || from == HW_FROM_GUIDED;
	static const char *froms[] = { "ink", "beam", "words", "guided" };
	double fit = 0;
	char *line = make_sample(l, s, text, src, prompted, keep && !gate, gate, rank,
				 from >= 0 && from < 4 ? froms[from] : "", &exact, &fit, &why);
	if (!line && why == 3) { reply("confirm\t%s\tskip\tfit\t%.2f", id, fit); return; }
	if (!line) { reply("confirm\t%s\tskip\t%s", id, why == 1 ? "empty" : "short"); return; }
	struct hw_meta m = { 0 };
	snprintf(m.client, sizeof m.client, "%s", client);
	m.time = (long long)time(NULL);
	snprintf(m.source, sizeof m.source, "%s", src);
	snprintf(m.lang, sizeof m.lang, "%s", l->lang);
	char sid[17];
	hw_sample_id(line, sid);
	int r = hw_samples_add(line, &m);
	free(line);
	if (r < 0) reply("error\t%s\tstore", id);
	else if (prompted) reply("confirm\t%s\tok\t%s\t%s", id, sid, exact ? "exact" : "inferred");
	else reply("confirm\t%s\tok\t%s", id, sid);
}

static int reader_granted(void)
{
	char v[1024];
	if (!hw_conf_get("readers", v, sizeof v)) return 0;
	for (char *p = strtok(v, ", "); p; p = strtok(NULL, ", ")) if (!strcmp(p, client)) return 1;
	return 0;
}

/* samples ID [all]: ids; list ID [all]: per sample its id, time, source, language, text
 * and first reading, from the index (no strokes read) */
static void do_samples(const char *id, const char *which, int full)
{
	const char *verb = full ? "list" : "samples";
	if (!strcmp(client, "-")) { reply("error\t%s\tno-client", id); return; }
	int all = !strcmp(which, "all");
	if (*which && !all) { reply("error\t%s\tbad-line", id); return; }
	if (all && !reader_granted()) { reply("error\t%s\tdenied", id); return; }
	const struct hw_index_entry *es;
	size_t n;
	if (hw_samples_index(&es, &n)) { reply("error\t%s\tstore", id); return; }
	size_t k = 0;
	for (size_t i = 0; i < n; i++) if (all || !strcmp(es[i].meta.client, client)) k++;
	bfmt(&out, "%s\t%s\t%zu", verb, id, k);
	for (size_t i = 0; i < n; i++) {
		if (!all && strcmp(es[i].meta.client, client)) continue;
		bfmt(&out, "\t%s", es[i].meta.id);
		if (!full) continue;
		bfmt(&out, "\t%lld\t%s\t%s\t", es[i].meta.time, es[i].meta.source, es[i].meta.lang);
		besc(&out, es[i].text);
		bput(&out, "\t", 1);
		besc(&out, es[i].read);
	}
	bput(&out, "\n", 1);
}

/* the client's (or with all, everyone's) samples with a marking or a pair link */
static void do_marked(const char *id, const char *which)
{
	if (!strcmp(client, "-")) { reply("error\t%s\tno-client", id); return; }
	int all = !strcmp(which, "all");
	if (*which && !all) { reply("error\t%s\tbad-line", id); return; }
	if (all && !reader_granted()) { reply("error\t%s\tdenied", id); return; }
	const struct hw_index_entry *es;
	size_t n;
	if (hw_samples_index(&es, &n)) { reply("error\t%s\tstore", id); return; }
	size_t k = 0;
#define MARKED(e) (((e).written[0] || (e).pair[0]) && (all || !strcmp((e).meta.client, client)))
	for (size_t i = 0; i < n; i++) k += MARKED(es[i]);
	bfmt(&out, "marked\t%s\t%zu", id, k);
	for (size_t i = 0; i < n; i++)
		if (MARKED(es[i])) bfmt(&out, "\t%s\t%s\t%s", es[i].meta.id, es[i].written[0] ? es[i].written : "-",
				       es[i].pair[0] ? es[i].pair : "-");
#undef MARKED
	bput(&out, "\n", 1);
}

static int span_arg(const char *a, int *v)
{
	if (!strcmp(a, "-")) { *v = -1; return 0; }
	char *e;
	long x = strtol(a, &e, 10);
	if (e == a || *e || x < 0 || x > 100000) return -1;
	*v = (int)x;
	return 0;
}

/* prefer ID CHOSEN OTHER [CHOSEN_CHAR OTHER_CHAR] */
static void do_prefer(const char *id, char **f, int n)
{
	if (!strcmp(client, "-")) { reply("error\t%s\tno-client", id); return; }
	struct hw_pref p = { .cspan = -1, .ospan = -1 };
	if (!valid_name(f[2], 16) || !valid_name(f[3], 16) || strlen(f[2]) != 16 || strlen(f[3]) != 16) {
		reply("error\t%s\tunknown-sample", id); return;
	}
	strcpy(p.chosen, f[2]);
	strcpy(p.other, f[3]);
	if (n == 6 && (span_arg(f[4], &p.cspan) || span_arg(f[5], &p.ospan))) { reply("error\t%s\tbad-char", id); return; }
	int r = hw_prefer(&p, reader_granted() ? NULL : client);
	if (r == -2) reply("error\t%s\tdenied", id);
	else if (r == -3) reply("error\t%s\tunknown-sample", id);
	else if (r == -4) reply("error\t%s\tbad-char", id);
	else if (r) reply("error\t%s\tstore", id);
	else reply("prefer\t%s\tok", id);
}

static void do_preferences(const char *id, const char *which)
{
	if (!strcmp(client, "-")) { reply("error\t%s\tno-client", id); return; }
	int all = !strcmp(which, "all");
	if (*which && !all) { reply("error\t%s\tbad-line", id); return; }
	if (all && !reader_granted()) { reply("error\t%s\tdenied", id); return; }
	struct hw_pref *ps;
	size_t n;
	if (hw_prefs(&ps, &n, all ? NULL : client)) { reply("error\t%s\tstore", id); return; }
	bfmt(&out, "preferences\t%s\t%zu", id, n);
	for (size_t i = 0; i < n; i++) {
		bfmt(&out, "\t%s\t%s\t", ps[i].chosen, ps[i].other);
		if (ps[i].cspan >= 0) bfmt(&out, "%d", ps[i].cspan); else bput(&out, "-", 1);
		bput(&out, "\t", 1);
		if (ps[i].ospan >= 0) bfmt(&out, "%d", ps[i].ospan); else bput(&out, "-", 1);
	}
	bput(&out, "\n", 1);
	free(ps);
}

static void do_sample(const char *id, const char *sid)
{
	if (!strcmp(client, "-")) { reply("error\t%s\tno-client", id); return; }
	struct hw_meta m;
	int err;
	char *line = valid_name(sid, 16) ? hw_samples_line(sid, &m, &err) : (err = 0, NULL);
	if (!line) { reply("error\t%s\t%s", id, err ? "store" : "unknown-sample"); return; }
	if (strcmp(m.client, client) && !reader_granted()) reply("error\t%s\tdenied", id);
	else {
		bfmt(&out, "sample\t%s\t", id);
		besc(&out, line);
		bput(&out, "\n", 1);
	}
	free(line);
}

/* whether text is one of the prompts shipped for the language: a line of LANG/prompts.txt
 * (the base model's) or of a .txt file in LANG/prompts/ (the samples app's sets). The lines
 * are read once per language and again when the files change. */
struct prompt_set {
	char lang[36];
	struct stat a, b;               /* prompts.txt and prompts/ as read */
	char **line;
	size_t n, cap;
};

static int cmp_str(const void *a, const void *b)
{
	return strcmp(*(char *const *)a, *(char *const *)b);
}

static void prompts_read(struct prompt_set *ps, const char *path)
{
	FILE *f = fopen(path, "re");
	char line[2048];
	while (f && fgets(line, sizeof line, f)) {
		line[strcspn(line, "\r\n")] = 0;
		if (!*line) continue;
		if (ps->n == ps->cap) {
			size_t c = ps->cap ? ps->cap * 2 : 256;
			char **x = realloc(ps->line, c * sizeof *x);
			if (!x) break;
			ps->line = x; ps->cap = c;
		}
		if (!(ps->line[ps->n] = strdup(line))) break;
		ps->n++;
	}
	if (f) fclose(f);
}

static int shipped_prompt_of(const char *lang, const char *text)
{
	static struct prompt_set sets[4];
	static int next;
	char d[4200], pa[4400], pb[4400];
	snprintf(d, sizeof d, "%s/%s", hw_models_dir(), lang);
	snprintf(pa, sizeof pa, "%s/prompts.txt", d);
	snprintf(pb, sizeof pb, "%s/prompts", d);
	struct stat a, b;
	if (stat(pa, &a)) memset(&a, 0, sizeof a);
	if (stat(pb, &b)) memset(&b, 0, sizeof b);
	struct prompt_set *ps = NULL;
	for (int i = 0; i < 4 && !ps; i++) if (!strcmp(sets[i].lang, lang)) ps = &sets[i];
	int fresh = ps && a.st_mtim.tv_sec == ps->a.st_mtim.tv_sec && a.st_mtim.tv_nsec == ps->a.st_mtim.tv_nsec &&
		    a.st_ino == ps->a.st_ino && b.st_mtim.tv_sec == ps->b.st_mtim.tv_sec &&
		    b.st_mtim.tv_nsec == ps->b.st_mtim.tv_nsec && b.st_ino == ps->b.st_ino;
	if (!fresh) {
		if (!ps) { ps = &sets[next]; next = (next + 1) % 4; }
		for (size_t i = 0; i < ps->n; i++) free(ps->line[i]);
		free(ps->line);
		memset(ps, 0, sizeof *ps);
		snprintf(ps->lang, sizeof ps->lang, "%s", lang);
		ps->a = a; ps->b = b;
		prompts_read(ps, pa);
		DIR *dir = opendir(pb);
		if (dir) {
			struct dirent *e;
			while ((e = readdir(dir))) {
				size_t n = strlen(e->d_name);
				if (n < 5 || strcmp(e->d_name + n - 4, ".txt")) continue;
				char q[4700];
				snprintf(q, sizeof q, "%s/%s", pb, e->d_name);
				prompts_read(ps, q);
			}
			closedir(dir);
		}
		if (ps->n) qsort(ps->line, ps->n, sizeof *ps->line, cmp_str);
	}
	return *text && ps->n && bsearch(&text, ps->line, ps->n, sizeof *ps->line, cmp_str) != NULL;
}

static int shipped_prompt(const struct hw_lang *l, const char *text)
{
	return shipped_prompt_of(l->lang, text);
}

static void do_donate(const char *id, struct session *s, const char *text, int keep)
{
	char lic[32];
	if (!hw_donation_licence(lic, sizeof lic)) { reply("donate\t%s\tskip\toff", id); return; }
	if (!s->n || !*text) { reply("donate\t%s\tskip\tempty", id); return; }
	int bad, why, exact;
	struct hw_lang *l = pick_lang(s->langs, &bad);
	if (!l) { reply("donate\t%s\tskip\tno-model", id); return; }
	if (!shipped_prompt(l, text)) { reply("donate\t%s\tskip\tprompt", id); return; }
	double fit = 0;
	char *line = make_sample(l, s, text, "prompted", 1, keep, 0, 0, "", &exact, &fit, &why);
	if (!line && why == 3) { reply("donate\t%s\tskip\tfit\t%.2f", id, fit); return; }
	if (!line) { reply("donate\t%s\tskip\t%s", id, why == 1 ? "empty" : "short"); return; }
	char sid[17];
	hw_sample_id(line, sid);
	int r = hw_donation_add(line);
	free(line);
	if (r) reply("error\t%s\tstore", id);
	else reply("donate\t%s\tok\t%ld\t%s", id, hw_donation_count(), sid);
}

/* removes one of the client's samples, and the same sample from the donation */
static void do_forget(const char *id, const char *sid)
{
	if (!strcmp(client, "-")) { reply("error\t%s\tno-client", id); return; }
	if (!valid_name(sid, 16)) { reply("error\t%s\tunknown-sample", id); return; }
	int r = hw_samples_delete(sid, reader_granted() ? NULL : client);
	if (r == -2) { reply("error\t%s\tdenied", id); return; }
	if (r < 0) { reply("error\t%s\tstore", id); return; }
	int d = hw_donation_remove(sid);
	if (d < 0) { reply("error\t%s\tstore", id); return; }
	if (!r && !d) { reply("error\t%s\tunknown-sample", id); return; }
	reply("forget\t%s\tok\t%s%s%s", id, r ? "samples" : "", r && d ? "," : "", d ? "donation" : "");
}

/* the writing area: X0,Y0,X1,Y1,LINE,ORIENTATION[,BASE] in the session's units */
static int parse_area(const char *a, struct hw_area *o, int *has_base, double *base)
{
	double v[7];
	const char *p = a;
	int n = 0;
	for (int i = 0; i < 7; i++) {
		char *e;
		v[i] = strtod(p, &e);
		if (e == p || !isfinite(v[i])) return -1;
		n = i + 1;
		if (i >= 5 && !*e) break;
		if (*e != ',' || i == 6) return -1;
		p = e + 1;
	}
	if (n < 6) return -1;
	int rot = (int)v[5];
	if (v[5] != rot || rot < 0 || rot >= 360 || rot % 90 || v[2] <= v[0] || v[3] <= v[1] || v[4] < 0) return -1;
	*o = (struct hw_area){ 1, { v[0], v[1], v[2], v[3] }, v[4], rot };
	*has_base = n == 7;
	*base = n == 7 ? v[6] : 0;
	return 0;
}

/* ---------------------------------------------------------------- requests */

static int nfields(char **f, int max, char *line)
{
	int n = 0;
	for (char *s = line;; ) {
		if (n == max) return max + 1;
		f[n++] = s;
		char *t = strchr(s, '\t');
		if (!t) break;
		*t = 0;
		s = t + 1;
	}
	return n;
}

static int version_set;

static void request(char *line, int skip_read)
{
	char *f[12];
	int n = nfields(f, 10, line);
	const char *v = f[0];
	const char *id = n >= 2 && valid_name(f[1], 32) ? f[1] : "-";
	if (n < 2 || !*v) { reply("error\t-\tbad-line"); return; }
	if (!valid_name(f[1], 32)) { reply("error\t-\tbad-id"); return; }
#define NEED(k) do { if (n != (k)) { reply("error\t%s\tbad-line", id); return; } } while (0)
#define UNESC(i) do { if (unescape(f[i])) { reply("error\t%s\tbad-escape", id); return; } } while (0)
	if (!strcmp(v, "hello")) {
		if (n != 3 && n != 4) { reply("error\t%s\tbad-line", id); return; }
		char *e;
		long ver = strtol(f[2], &e, 10);
		if (e == f[2] || *e || ver < 1) { reply("error\t%s\tbad-line", id); return; }
		if (n == 4) {
			if (!valid_name(f[3], HW_CLIENT_MAX - 1)) { reply("error\t%s\tbad-client", id); return; }
			snprintf(client, sizeof client, "%s", f[3]);
		}
		version_set = 1;
		reply("hello\t%s\t%d", id, VERSION);
		return;
	}
	if (!strcmp(v, "begin")) {
		NEED(7);
		/* begin ID S LANGS FIELD UNITS BEFORE */
		if (!valid_name(f[2], 32)) { reply("error\t%s\tbad-session", id); return; }
		int bad;
		int rule = field_rule(f[4], &bad);
		if (bad) { reply("error\t%s\tbad-field", id); return; }
		struct hw_units u;
		if (parse_units(f[5], &u)) { reply("error\t%s\tbad-units", id); return; }
		UNESC(6);
		if (strlen(f[3]) >= sizeof sessions[0].langs) { reply("error\t%s\tbad-lang", id); return; }
		struct session *s = find_session(f[2]);
		if (s) session_end(s);
		if (rule == FIELD_PRIVATE) { reply("begin\t%s\tprivate", id); return; }   /* nothing kept */
		if (*f[3] && strcmp(f[3], "-")) {
			char t[128];
			strcpy(t, f[3]);
			for (char *p = strtok(t, ","); p; p = strtok(NULL, ",")) if (!valid_lang(p)) { reply("error\t%s\tbad-lang", id); return; }
		}
		for (int i = 0; i < MAX_SESSIONS && !s; i++) if (!sessions[i].name[0]) s = &sessions[i];
		if (!s) { reply("error\t%s\tbusy", id); return; }
		memset(s, 0, sizeof *s);
		snprintf(s->name, sizeof s->name, "%s", f[2]);
		snprintf(s->langs, sizeof s->langs, "%s", f[3]);
		snprintf(s->field, sizeof s->field, "%s", f[4]);
		s->rule = rule;
		s->units = u;
		s->before = strdup(f[6]);
		s->words = words_field(f[4]);
		s->pressure_all = 1;
		s->tilt_all = 1;
		reply("begin\t%s\tok", id);
		return;
	}
	if (!strcmp(v, "recognize")) {
		/* recognize ID LANGS FIELD UNITS N BEFORE STROKES (strokes separated by ';') */
		NEED(8);
		int bad;
		int rule = field_rule(f[3], &bad);
		if (bad) { reply("error\t%s\tbad-field", id); return; }
		if (rule == FIELD_PRIVATE) { reply("recognize\t%s\tprivate", id); return; }
		struct session tmp = { 0 };
		if (parse_units(f[4], &tmp.units)) { reply("error\t%s\tbad-units", id); return; }
		char *e;
		long want = strtol(f[5], &e, 10);
		if (e == f[5] || *e || want < 1 || want > MAX_CANDS) { reply("error\t%s\tbad-count", id); return; }
		UNESC(6);
		snprintf(tmp.langs, sizeof tmp.langs, "%s", f[2]);
		tmp.before = strdup(f[6]);
		tmp.words = words_field(f[3]);
		tmp.pressure_all = 1;
		tmp.tilt_all = 1;
		char *save = NULL;
		for (char *p = strtok_r(f[7], ";", &save); p; p = strtok_r(NULL, ";", &save)) {
			struct hw_ink k;
			int w;
			if (parse_points(p, &k, &w) || session_add(&tmp, &k, w) < 0) {
				reply("error\t%s\tbad-points", id); session_end(&tmp); return;
			}
		}
		struct buf b0 = out;      /* the reply line, renamed from read to recognize */
		size_t at = b0.len;
		do_read(id, &tmp, (int)want, 0);
		if (out.len > at && !strncmp(out.s + at, "read\t", 5)) {
			struct buf t = { 0 };
			bstr(&t, "recognize");
			bstr(&t, out.s + at + 4);
			out.len = at;
			bstr(&out, t.s);
			free(t.s);
		}
		session_end(&tmp);
		return;
	}
	struct session *s = n >= 3 ? find_session(f[2]) : NULL;
	if (!strcmp(v, "stroke") || !strcmp(v, "read") || !strcmp(v, "align") || !strcmp(v, "remove") ||
	    !strcmp(v, "clear") || !strcmp(v, "end") || !strcmp(v, "confirm") || !strcmp(v, "donate") ||
	    !strcmp(v, "area") || !strcmp(v, "mark")) {
		if (n < 3) { reply("error\t%s\tbad-line", id); return; }
		if (!s) { reply("error\t%s\tunknown-session", id); return; }
	}
	if (!strcmp(v, "stroke")) {
		NEED(4);
		struct hw_ink k;
		int w, sid;
		if (parse_points(f[3], &k, &w)) { reply("error\t%s\tbad-points", id); return; }
		if ((sid = session_add(s, &k, w)) < 0) { hw_ink_free(&k); reply("error\t%s\ttoo-many", id); return; }
		reply("stroke\t%s\tok\t%d", id, sid);
	} else if (!strcmp(v, "read")) {
		/* read ID S [COUNT [raw]] */
		if (n < 3 || n > 5 || (n == 5 && strcmp(f[4], "raw"))) { reply("error\t%s\tbad-line", id); return; }
		long want = 3;
		if (n >= 4) {
			char *e;
			want = strtol(f[3], &e, 10);
			if (e == f[3] || *e || want < 1 || want > MAX_CANDS) { reply("error\t%s\tbad-count", id); return; }
		}
		if (skip_read) { reply("drop\t%s", id); return; }
		do_read(id, s, (int)want, n == 5);
	} else if (!strcmp(v, "align")) {
		NEED(4);
		UNESC(3);
		do_align(id, s, f[3]);
	} else if (!strcmp(v, "remove")) {
		NEED(4);
		int removed = 0;
		for (char *p = strtok(f[3], ","); p; p = strtok(NULL, ",")) {
			char *e;
			long k = strtol(p, &e, 10);
			if (e == p || *e) { reply("error\t%s\tbad-line", id); return; }
			for (size_t i = 0; i < s->n; i++) if (s->ids[i] == k) { session_remove(s, i); removed++; break; }
		}
		reply("remove\t%s\tok\t%d", id, removed);
	} else if (!strcmp(v, "clear")) {
		NEED(3);
		session_clear(s);
		reply("clear\t%s\tok", id);
	} else if (!strcmp(v, "end")) {
		NEED(3);
		session_end(s);
		reply("end\t%s\tok", id);
	} else if (!strcmp(v, "confirm")) {
		if (n != 5 && n != 6) { reply("error\t%s\tbad-line", id); return; }
		if (n == 6 && strcmp(f[5], "keep")) { reply("error\t%s\tbad-line", id); return; }
		UNESC(4);
		do_confirm(id, s, f[3], f[4], n == 6);
	} else if (!strcmp(v, "donate")) {
		if (n != 4 && n != 5) { reply("error\t%s\tbad-line", id); return; }
		if (n == 5 && strcmp(f[4], "keep")) { reply("error\t%s\tbad-line", id); return; }
		UNESC(3);
		do_donate(id, s, f[3], n == 5);
	} else if (!strcmp(v, "area")) {
		NEED(4);
		if (parse_area(f[3], &s->area, &s->has_base, &s->base)) { reply("error\t%s\tbad-area", id); return; }
		reply("area\t%s\tok", id);
	} else if (!strcmp(v, "forget")) {
		NEED(3);
		do_forget(id, f[2]);
	} else if (!strcmp(v, "samples") || !strcmp(v, "list")) {
		if (n != 2 && n != 3) { reply("error\t%s\tbad-line", id); return; }
		do_samples(id, n == 3 ? f[2] : "", !strcmp(v, "list"));
	} else if (!strcmp(v, "sample")) {
		NEED(3);
		do_sample(id, f[2]);
	} else if (!strcmp(v, "mark")) {
		/* mark ID S WRITTEN PAIR: what the session's kept samples carry (spec 003) */
		NEED(5);
		const char *w = strcmp(f[3], "-") ? f[3] : "", *pr = strcmp(f[4], "-") ? f[4] : "";
		if (strlen(w) >= sizeof s->written || !hw_written_ok(w) || strlen(pr) >= sizeof s->pair || !hw_pair_ok(pr)) {
			reply("error\t%s\tbad-mark", id); return;
		}
		strcpy(s->written, w);
		strcpy(s->pair, pr);
		reply("mark\t%s\tok", id);
	} else if (!strcmp(v, "marked") || !strcmp(v, "preferences")) {
		if (n != 2 && n != 3) { reply("error\t%s\tbad-line", id); return; }
		if (!strcmp(v, "marked")) do_marked(id, n == 3 ? f[2] : "");
		else do_preferences(id, n == 3 ? f[2] : "");
	} else if (!strcmp(v, "prefer")) {
		if (n != 4 && n != 6) { reply("error\t%s\tbad-line", id); return; }
		do_prefer(id, f, n);
	} else {
		reply("error\t%s\tunknown-verb", id);
	}
#undef NEED
#undef UNESC
}

/* ---------------------------------------------------------------- serving */

static volatile sig_atomic_t stop;
static void on_signal(int sig) { (void)sig; stop = 1; }

static void background_adapt(void)
{
	/* the person's languages with samples */
	const struct hw_index_entry *es;
	size_t n;
	if (hw_samples_index(&es, &n) || !n) return;
	if (!hw_learning_on() || !hw_charging() || !hw_idle()) return;
	char langs[8][36];
	int nd = 0;
	for (size_t i = 0; i < n && nd < 8; i++) {
		int seen = 0;
		for (int k = 0; k < nd; k++) if (!strcmp(langs[k], es[i].meta.lang)) seen = 1;
		if (!seen && valid_lang(es[i].meta.lang)) snprintf(langs[nd++], 36, "%s", es[i].meta.lang);
	}
	pid_t p = fork();
	if (p != 0) return;
	/* the child: detached, nothing open that the client waits on, lowest priority */
	setsid();
	int null = open("/dev/null", O_RDWR);
	if (null >= 0) { dup2(null, 0); dup2(null, 1); dup2(null, 2); if (null > 2) close(null); }
	if (fork() != 0) _exit(0);
	if (nice(19) == -1) {}
#ifdef SCHED_IDLE
	struct sched_param sp = { 0 };
	sched_setscheduler(0, SCHED_IDLE, &sp);
#endif
	for (int k = 0; k < nd; k++) {
		struct hw_adapt_opts o = { langs[k], 0, 40, 10, 5, 32, 8, 1e-3f, 0, 0, 0 };
		hw_adapt_run(&o);
	}
	/* the letter model, when its samples, markings or preferences changed */
	struct hw_letters_opts lo = { 0, 0, 0 };
	hw_letters_run(&lo);
	_exit(0);
}

static int serve(int idle_s, int adapt)
{
	hw_run_cache(1);
	struct sigaction sa = { 0 };
	sa.sa_handler = on_signal;
	sigaction(SIGTERM, &sa, NULL);
	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGHUP, &sa, NULL);
	signal(SIGPIPE, SIG_IGN);
	signal(SIGCHLD, SIG_IGN);
	size_t cap = 1 << 16, len = 0;
	char *in = malloc(cap);
	int skipping = 0;
	if (!in) return 1;
	while (!stop) {
		struct pollfd p = { 0, POLLIN, 0 };
		int r = poll(&p, 1, idle_s > 0 ? idle_s * 1000 : -1);
		if (r < 0) { if (errno == EINTR) continue; break; }
		if (r == 0) {        /* idle: stop; learn first if the device is idle and charging */
			if (adapt) background_adapt();
			break;
		}
		if (len + 65536 + 1 > cap) {
			size_t c = cap * 2 > MAX_LINE + 65536 + 1 ? MAX_LINE + 65536 + 1 : cap * 2;
			if (c > cap) { char *x = realloc(in, c); if (!x) break; in = x; cap = c; }
		}
		ssize_t got = read(0, in + len, cap - len - 1);
		if (got < 0) { if (errno == EINTR || errno == EAGAIN) continue; break; }
		if (got == 0) break;
		len += (size_t)got;
		in[len] = 0;
		/* whole lines: collect, then answer; of several reads of one session only the
		 * newest. Batches of MAX_BATCH until no whole line is left. */
		char *s = in;
		for (;;) {
			char *lines[MAX_BATCH];
			int nl = 0;
			for (char *e; nl < MAX_BATCH && (e = memchr(s, '\n', len - (size_t)(s - in))); s = e + 1) {
				*e = 0;
				if (e > s && e[-1] == '\r') e[-1] = 0;
				if (skipping) { skipping = 0; continue; }
				lines[nl++] = s;
			}
			if (!nl) break;
			for (int i = 0; i < nl; i++) {
				int newer = 0;
				if (!strncmp(lines[i], "read\t", 5)) {
					const char *a = strchr(lines[i] + 5, '\t');
					if (a) {
						a++;
						size_t sl = strcspn(a, "\t");
						for (int j = i + 1; j < nl && !newer; j++) {
							if (strncmp(lines[j], "read\t", 5)) continue;
							const char *b2 = strchr(lines[j] + 5, '\t');
							if (b2 && !strncmp(b2 + 1, a, sl) && (b2[1 + sl] == '\t' || !b2[1 + sl])) newer = 1;
						}
					}
				}
				request(lines[i], newer);
				flush_out();
			}
		}
		size_t rest = len - (size_t)(s - in);
		memmove(in, s, rest);
		len = rest;
		if (len > MAX_LINE) {   /* a line too long: answered once, skipped to its end */
			reply("error\t-\ttoo-long");
			flush_out();
			len = 0;
			skipping = 1;
		}
	}
	free(in);
	return 0;
}

/* ---------------------------------------------------------------- commands */

static void usage(void)
{
	fputs("usage: glassine-handwriting [-i IDLE_SECONDS] [--no-adapt]\n"
	      "       glassine-handwriting samples stats|list [-c CLIENT]|erase [-c CLIENT]|delete ID|export [-c CLIENT] FILE|import FILE\n"
	      "       glassine-handwriting learning [on|off]\n"
	      "       glassine-handwriting adapt [-f] [-r] [--plan] [--progress] [-l LANG] [--min-train N] [--min-held N] [--epochs N] [--rank N] [--lr X]\n"
	      "       glassine-handwriting adaptation [show|revert] [-l LANG]\n"
	      "       glassine-handwriting charging\n"
	      "       glassine-handwriting donation start CC0-1.0|CC-BY-4.0 | add SAMPLE | remove SAMPLE | list | samples | earlier [-c CLIENT] | donor [new] | write FILE | erase | prompts [LANG]\n"
	      "       glassine-handwriting letters build [-f] [-r] | status | show\n"
	      "       glassine-handwriting font build [-n NAME] FILE | install [-n NAME] | remove | status\n"
	      "       glassine-handwriting read [-n N] [-l LANG] FILE\n"
	      "       glassine-handwriting bench [-l LANG] FILE\n", stderr);
}

static int cmd_samples(int argc, char **argv)
{
	const char *sub = argc > 0 ? argv[0] : "stats", *who = NULL;
	if (argc >= 3 && !strcmp(argv[1], "-c")) who = argv[2];
	if (!strcmp(sub, "stats") || !strcmp(sub, "list")) {
		const struct hw_index_entry *es;
		size_t n;
		if (hw_samples_index(&es, &n)) return 1;
		if (!strcmp(sub, "list")) {
			for (size_t i = 0; i < n; i++) {
				if (who && strcmp(es[i].meta.client, who)) continue;
				struct buf b = { 0 };
				bfmt(&b, "%s\t%s\t%lld\t%s\t%s\t", es[i].meta.id, es[i].meta.client, es[i].meta.time,
				     es[i].meta.source, es[i].meta.lang);
				besc(&b, es[i].text);
				puts(b.s);
				free(b.s);
			}
		} else {
			printf("learning\t%s\nsamples\t%zu\n", hw_learning_on() ? "on" : "off", n);
			char seen[64][HW_CLIENT_MAX];
			size_t count[64];
			int ns = 0;
			for (size_t i = 0; i < n; i++) {
				int k;
				for (k = 0; k < ns; k++) if (!strcmp(seen[k], es[i].meta.client)) break;
				if (k == ns) {
					if (ns == 64) continue;
					snprintf(seen[ns], HW_CLIENT_MAX, "%s", es[i].meta.client);
					count[ns++] = 0;
				}
				count[k]++;
			}
			for (int k = 0; k < ns; k++) printf("client\t%s\t%zu\n", seen[k], count[k]);
			printf("donation\t%ld\n", hw_donation_count());
		}
		return 0;
	}
	if (!strcmp(sub, "erase")) {
		long r = hw_samples_erase(who);
		if (r < 0) { fprintf(stderr, "glassine-handwriting: can't erase\n"); return 1; }
		printf("%ld\n", r);
		return 0;
	}
	if (!strcmp(sub, "export") && (argc == 2 || (argc == 4 && who))) {
		const char *file = argv[argc - 1];
		if (hw_export(file, who)) { fprintf(stderr, "glassine-handwriting: %s: %s\n", file, strerror(errno)); return 1; }
		return 0;
	}
	if (!strcmp(sub, "delete") && argc == 2) {
		int r = hw_samples_delete(argv[1], NULL);
		if (r < 0) { fprintf(stderr, "glassine-handwriting: can't delete\n"); return 1; }
		if (!r) { fprintf(stderr, "glassine-handwriting: no sample %s\n", argv[1]); return 1; }
		return 0;
	}
	if (!strcmp(sub, "import") && argc == 2) {
		long r = hw_import(argv[1]);
		if (r < 0) return 1;
		printf("%ld\n", r);
		return 0;
	}
	usage();
	return 2;
}

static int cmd_adaptation(int argc, char **argv)
{
	const char *lang = "en", *sub = "show";
	for (int i = 0; i < argc; i++) {
		if (!strcmp(argv[i], "-l") && i + 1 < argc) lang = argv[++i];
		else sub = argv[i];
	}
	if (!valid_lang(lang)) { usage(); return 2; }
	char cur[4096], prev[4096];
	hw_store_adaptation_path(cur, sizeof cur, lang, "current");
	hw_store_adaptation_path(prev, sizeof prev, lang, "previous");
	if (!strcmp(sub, "show")) {
		const char *slot[2] = { "current", "previous" }, *path[2] = { cur, prev };
		for (int k = 0; k < 2; k++) {
			struct hw_adapt_info in;
			if (hw_adapt_info(path[k], &in)) { printf("%s\tnone\n", slot[k]); continue; }
			printf("%s\t%lld\t%u\t%u\t%.4f\t%.4f\n", slot[k], (long long)in.time, in.ntrain, in.nheld, in.cer_before, in.cer);
		}
		return 0;
	}
	if (!strcmp(sub, "revert")) {
		int fd = hw_store_lock();
		if (fd < 0) return 1;
		struct stat st;
		int r = 1;
		if (stat(prev, &st)) fprintf(stderr, "glassine-handwriting: no previous adaptation\n");
		else {
			char tmp[4200];
			snprintf(tmp, sizeof tmp, "%s.swap", cur);
			int havecur = !stat(cur, &st);
			r = (havecur && rename(cur, tmp)) || rename(prev, cur) || (havecur && rename(tmp, prev)) ? 1 : 0;
		}
		hw_store_unlock(fd);
		return r;
	}
	usage();
	return 2;
}

static int cmd_donation(int argc, char **argv)
{
	if (argc < 1) { usage(); return 2; }
	if (!strcmp(argv[0], "start") && argc == 2) {
		if (hw_donation_start(argv[1])) { fprintf(stderr, "glassine-handwriting: licence is CC0-1.0 or CC-BY-4.0\n"); return 1; }
		return 0;
	}
	if (!strcmp(argv[0], "list")) {
		char lic[32];
		char donor[40];
		printf("licence\t%s\nsamples\t%ld\ndonor\t%s\n", hw_donation_licence(lic, sizeof lic) ? lic : "-",
		       hw_donation_count(), hw_donor_id(donor, sizeof donor) ? donor : "-");
		return 0;
	}
	if (!strcmp(argv[0], "donor") && (argc == 1 || (argc == 2 && !strcmp(argv[1], "new")))) {
		char donor[40];
		if (argc == 2) {
			if (hw_donor_new(donor, sizeof donor)) { fprintf(stderr, "glassine-handwriting: can't write the donor ID\n"); return 1; }
		} else if (!hw_donor_id(donor, sizeof donor)) snprintf(donor, sizeof donor, "-");
		printf("%s\n", donor);
		return 0;
	}
	if (!strcmp(argv[0], "write") && argc == 2) return hw_donation_write(argv[1]) ? 1 : 0;
	if (!strcmp(argv[0], "erase")) return hw_donation_erase() ? 1 : 0;
	if (!strcmp(argv[0], "remove") && argc == 2) {
		int r = hw_donation_remove(argv[1]);
		if (r == 0) fprintf(stderr, "glassine-handwriting: no donated sample %s\n", argv[1]);
		return r == 1 ? 0 : 1;
	}
	if (!strcmp(argv[0], "add") && argc == 2) {
		/* one of the person's own samples, picked by them */
		char lic[32];
		if (!hw_donation_licence(lic, sizeof lic)) { fprintf(stderr, "glassine-handwriting: no donation started\n"); return 1; }
		int err;
		char *line = hw_samples_line(argv[1], NULL, &err);
		/* once: a sample already there stays one */
		int r = !line ? 1 : hw_donation_has(argv[1]) == 1 ? 0 : hw_donation_add(line) ? 1 : 0;
		if (!line) fprintf(stderr, err ? "glassine-handwriting: can't read the samples\n" : "glassine-handwriting: no sample %s\n", argv[1]);
		free(line);
		return r;
	}
	if (!strcmp(argv[0], "samples") && argc == 1) {
		char (*ids)[17];
		size_t n;
		if (hw_donation_ids(&ids, &n)) return 1;
		for (size_t i = 0; i < n; i++) puts(ids[i]);
		free(ids);
		return 0;
	}
	if (!strcmp(argv[0], "earlier") && (argc == 1 || (argc == 3 && !strcmp(argv[1], "-c")))) {
		/* the prompted samples written to shipped prompts that the donation hasn't got:
		 * the ones kept before donating was on, to offer (spec 002 FR-011) */
		const struct hw_index_entry *es;
		size_t n;
		if (hw_samples_index(&es, &n)) return 1;
		for (size_t i = 0; i < n; i++) {
			if (argc == 3 && strcmp(es[i].meta.client, argv[2])) continue;
			if (strcmp(es[i].meta.source, "prompted") || hw_donation_has(es[i].meta.id) != 0) continue;
			char base[36];
			snprintf(base, sizeof base, "%.*s", (int)strcspn(es[i].meta.lang, "-_"), es[i].meta.lang);
			if (shipped_prompt_of(es[i].meta.lang, es[i].text) || (strcmp(base, es[i].meta.lang) && shipped_prompt_of(base, es[i].text))) {
				struct buf b = { 0 };
				bfmt(&b, "%s\t%lld\t%s\t", es[i].meta.id, es[i].meta.time, es[i].meta.lang);
				besc(&b, es[i].text);
				puts(b.s);
				free(b.s);
			}
		}
		return 0;
	}
	if (!strcmp(argv[0], "prompts")) {
		char p[4200];
		snprintf(p, sizeof p, "%s/%s/prompts.txt", hw_models_dir(), argc > 1 ? argv[1] : "en");
		FILE *f = fopen(p, "re");
		if (!f) { fprintf(stderr, "glassine-handwriting: %s: %s\n", p, strerror(errno)); return 1; }
		char line[1024];
		while (fgets(line, sizeof line, f)) fputs(line, stdout);
		fclose(f);
		return 0;
	}
	usage();
	return 2;
}

static double now_s(void)
{
	struct timespec t;
	clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);   /* CPU time: a busy host skews wall time */
	return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

static int cmp_double(const void *a, const void *b)
{
	double x = *(const double *)a, y = *(const double *)b;
	return (x > y) - (x < y);
}

/* read / bench: sample-format lines from FILE */
static int cmd_letters(int argc, char **argv)
{
	const char *sub = argc > 0 ? argv[0] : "status";
	if (!strcmp(sub, "build")) {
		struct hw_letters_opts o = { 0, 0, 1 };
		for (int i = 1; i < argc; i++) {
			if (!strcmp(argv[i], "-f")) o.force = 1;
			else if (!strcmp(argv[i], "-r")) o.requested = 1;
			else { usage(); return 2; }
		}
		/* asked for or not, it runs at the lowest priority: the device stays usable */
		if (nice(19) == -1) {}
		return hw_letters_run(&o) < 0 ? 1 : 0;
	}
	if (argc != 1) { usage(); return 2; }
	if (!strcmp(sub, "show")) {
		struct hw_letters L;
		int r = hw_letters_load(&L);
		if (r < 0) return 1;
		if (!r) return 0;
		size_t len;
		char *t = hw_letters_format(&L, &len);
		hw_letters_free(&L);
		if (!t) return 1;
		fwrite(t, 1, len, stdout);
		free(t);
		return 0;
	}
	if (!strcmp(sub, "status")) {
		struct hw_letters L;
		int r = hw_letters_load(&L);
		if (r < 0) return 1;
		char now[17];
		if (hw_letters_inputs(now)) { if (r == 1) hw_letters_free(&L); return 1; }
		printf("state\t%s\n", r == 0 ? "none" : strcmp(L.inputs, now) ? "outdated" : "current");
		int covered = 0;
		for (size_t i = 0; r == 1 && i < L.n; i++) covered += L.l[i].covered;
		printf("samples\t%ld\ncovered\t%d\n", r == 1 ? L.samples : 0L, covered);
		for (size_t i = 0; r == 1 && i < L.n; i++) {
			char u[8];
			u[hw_utf8_encode(L.l[i].cp, u)] = 0;
			struct buf b = { 0 };
			bstr(&b, "char\t");
			besc(&b, u);
			bfmt(&b, "\t%d\t%d", L.l[i].samples, L.l[i].covered ? L.l[i].nvar : 0);
			puts(b.s);
			free(b.s);
		}
		if (r == 1) hw_letters_free(&L);
		char name[256], path[4096];
		unsigned v;
		int f = hw_font_record(name, sizeof name, &v, path, sizeof path);
		if (f < 0) return 1;
		if (f) {
			struct buf b = { 0 };
			bstr(&b, "font\t");
			besc(&b, name);
			bfmt(&b, "\t%u\t", v);
			besc(&b, path);
			puts(b.s);
			free(b.s);
		} else {
			puts("font\t-");
		}
		return 0;
	}
	usage();
	return 2;
}

static int cmd_font(int argc, char **argv)
{
	const char *sub = argc > 0 ? argv[0] : "status", *name = NULL, *file = NULL;
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "-n") && i + 1 < argc) name = argv[++i];
		else if (!file && !strcmp(sub, "build")) file = argv[i];
		else { usage(); return 2; }
	}
	if (!strcmp(sub, "install")) {
		int r = hw_font_install(name, 1);
		if (r == -2) { fputs("glassine-handwriting: a font name is 1 to 63 bytes of UTF-8 without control characters\n", stderr); return 2; }
		return r < 0 ? 1 : 0;
	}
	if (name && strcmp(sub, "build")) { usage(); return 2; }
	if (!strcmp(sub, "remove")) return hw_font_remove() < 0 ? 1 : 0;
	if (!strcmp(sub, "status")) {
		char nm[256], path[4096];
		unsigned v;
		int f = hw_font_record(nm, sizeof nm, &v, path, sizeof path);
		if (f < 0) return 1;
		if (!f) { puts("none"); return 0; }
		struct buf b = { 0 };
		besc(&b, nm);
		bfmt(&b, "\t%u\t", v);
		besc(&b, path);
		puts(b.s);
		free(b.s);
		return 0;
	}
	if (!strcmp(sub, "build") && file) {
		struct hw_letters L;
		int r = hw_letters_load(&L);
		if (r < 0) return 1;
		if (!r) { fputs("glassine-handwriting: no letter model (letters build)\n", stderr); return 1; }
		unsigned v = 0;
		if (hw_font_record(NULL, 0, &v, NULL, 0) != 1) v = 0;
		size_t len;
		unsigned char *t = hw_font_build(&L, name ? name : HW_FONT_NAME_DEFAULT, v + 1, &len);
		hw_letters_free(&L);
		if (!t) return 1;
		FILE *f = fopen(file, "we");
		if (!f) { free(t); fprintf(stderr, "glassine-handwriting: %s: %s\n", file, strerror(errno)); return 1; }
		int bad = fwrite(t, 1, len, f) != len;
		free(t);
		if (fclose(f) || bad) return 1;
		return 0;
	}
	usage();
	return 2;
}

static int cmd_read(int argc, char **argv, int bench)
{
	const char *lang = "en", *file = NULL;
	int want = 1;
	for (int i = 0; i < argc; i++) {
		if (!strcmp(argv[i], "-l") && i + 1 < argc) lang = argv[++i];
		else if (!strcmp(argv[i], "-n") && i + 1 < argc) want = atoi(argv[++i]);
		else file = argv[i];
	}
	if (!file || want < 1 || want > MAX_CANDS) { usage(); return 2; }
	struct hw_lang *l = hw_lang_get(lang);
	if (!l) { fprintf(stderr, "glassine-handwriting: no model for %s in %s\n", lang, hw_models_dir()); return 1; }
	FILE *f = strcmp(file, "-") ? fopen(file, "re") : stdin;
	if (!f) { fprintf(stderr, "glassine-handwriting: %s: %s\n", file, strerror(errno)); return 1; }
	char *line = NULL;
	size_t cap = 0, errs = 0, tot = 0, exact = 0, nl = 0, ntimes = 0, tcap = 0;
	double *times = NULL;
	long pts = 0;
	while (getline(&line, &cap, f) > 0) {
		struct hw_sample s;
		char err[128];
		if (hw_sample_parse(line, &s, err, sizeof err)) { fprintf(stderr, "skipped: %s\n", err); continue; }
		struct hw_stroke *st = calloc(s.nstrokes + 1, sizeof *st);
		size_t *np = calloc(s.nstrokes + 1, sizeof *np);
		hw_sample_strokes(&s, st);
		for (size_t i = 0; i < s.nstrokes; i++) { np[i] = s.strokes[i].n; pts += (long)s.strokes[i].n; }
		double t0 = now_s();
		struct hw_run r;
		struct hw_reading rd = { 0 };
		struct hw_size_ref sz;
		hw_size_ref_for(l->lang, &s.units, &s.area, NAN, &sz);
		if (!hw_run_sized(l, st, s.nstrokes, &sz, &r) && r.rows > 0) {
			r.npoints = np;
			hw_read(l, &r, want, &rd);
		}
		double t1 = now_s();
		hw_run_free(&r);
		if (ntimes == tcap) { tcap = tcap ? tcap * 2 : 256; times = realloc(times, tcap * sizeof *times); }
		times[ntimes++] = t1 - t0;
		unsigned ref[HW_MAX_LABELS];
		size_t nr = hw_utf8_decode(s.text, ref, HW_MAX_LABELS);
		if (rd.n) {
			errs += hw_edit(ref, nr, rd.c[0].cp, (size_t)rd.c[0].len);
			exact += hw_edit(ref, nr, rd.c[0].cp, (size_t)rd.c[0].len) == 0;
		} else {
			errs += nr;
		}
		tot += nr;
		nl++;
		if (!bench) {
			struct buf b = { 0 };
			besc(&b, s.text);
			for (int c = 0; c < rd.n; c++) {
				char t[4 * HW_MAX_LABELS + 1];
				text_of(rd.c[c].cp, rd.c[c].len, t, sizeof t);
				bstr(&b, "\t");
				besc(&b, t);
			}
			puts(b.s ? b.s : "");
			free(b.s);
		}
		free(st); free(np);
		hw_sample_free(&s);
	}
	free(line);
	if (f != stdin) fclose(f);
	if (nl) {
		qsort(times, ntimes, sizeof *times, cmp_double);
		struct rusage ru;
		getrusage(RUSAGE_SELF, &ru);
		fprintf(bench ? stdout : stderr,
			"lines %zu  CER %.2f%%  exact %.1f%%  CPU ms/line median %.2f p95 %.2f  points/line %.0f  max RSS %ld KiB\n",
			nl, 100.0 * (double)errs / (double)(tot ? tot : 1), 100.0 * (double)exact / (double)nl,
			1000 * times[ntimes / 2], 1000 * times[(size_t)(0.95 * (double)(ntimes - 1))], (double)pts / (double)nl,
			ru.ru_maxrss);
	}
	free(times);
	return 0;
}

int main(int argc, char **argv)
{
	if (argc > 1 && argv[1][0] != '-') {
		const char *c = argv[1];
		if (!strcmp(c, "samples")) return cmd_samples(argc - 2, argv + 2);
		if (!strcmp(c, "learning")) {
			if (argc == 2) { puts(hw_learning_on() ? "on" : "off"); return 0; }
			if (argc == 3 && (!strcmp(argv[2], "on") || !strcmp(argv[2], "off")))
				return hw_conf_set("learning", argv[2]) ? 1 : 0;
			usage();
			return 2;
		}
		if (!strcmp(c, "adapt")) {
			struct hw_adapt_opts o = { "en", 0, 40, 10, 5, 32, 8, 1e-3f, getenv("GLASSINE_HANDWRITING_DEBUG") ? 2 : 1, 0, 0 };
			int plan = 0;
			for (int i = 2; i < argc; i++) {
				const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
				if (!strcmp(a, "-f")) o.force = 1;
				else if (!strcmp(a, "-r")) o.requested = 1;
				else if (!strcmp(a, "--progress")) o.progress = 1;
				else if (!strcmp(a, "--plan")) plan = 1;
				else if (!strcmp(a, "-l") && v) { o.lang = v; i++; }
				else if (!strcmp(a, "--min-train") && v) { o.min_train = atoi(v); i++; }
				else if (!strcmp(a, "--min-held") && v) { o.min_held = atoi(v); i++; }
				else if (!strcmp(a, "--epochs") && v) { o.epochs = atoi(v); i++; }
				else if (!strcmp(a, "--rank") && v) { o.rank = atoi(v); i++; }
				else if (!strcmp(a, "--lr") && v) { o.lr = strtof(v, NULL); i++; }
				else { usage(); return 2; }
			}
			if (!valid_lang(o.lang) || o.epochs < 1 || o.rank < 0 || o.rank > 256 || o.min_held < 1) { usage(); return 2; }
			if (plan) {
				long tr, he, fr;
				if (hw_adapt_plan(o.lang, &tr, &he, &fr)) return 1;
				printf("plan\t%ld\t%ld\t%ld\t%d\t%d\t%s\n", tr, he, fr, o.min_train, o.min_held,
				       hw_charging() ? "charging" : "battery");
				return 0;
			}
			if (o.progress) o.verbose = 0;
			/* asked for by the person: the device stays usable while it runs */
			if (o.requested && nice(19) == -1) {}
			return hw_adapt_run(&o) < 0 ? 1 : 0;
		}
		if (!strcmp(c, "adaptation")) return cmd_adaptation(argc - 2, argv + 2);
		if (!strcmp(c, "charging") && argc == 2) { puts(hw_charging() ? "charging" : "battery"); return 0; }
		if (!strcmp(c, "donation")) return cmd_donation(argc - 2, argv + 2);
		if (!strcmp(c, "letters")) return cmd_letters(argc - 2, argv + 2);
		if (!strcmp(c, "font")) return cmd_font(argc - 2, argv + 2);
		if (!strcmp(c, "read")) return cmd_read(argc - 2, argv + 2, 0);
		if (!strcmp(c, "bench")) return cmd_read(argc - 2, argv + 2, 1);
		usage();
		return 2;
	}
	int idle = IDLE_DEFAULT, adapt = 1;
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "-i") && i + 1 < argc) idle = atoi(argv[++i]);
		else if (!strcmp(argv[i], "--no-adapt")) adapt = 0;
		else { usage(); return 2; }
	}
	return serve(idle, adapt);
}
