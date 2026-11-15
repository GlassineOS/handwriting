// SPDX-License-Identifier: GPL-2.0-or-later
// The person's store and settings (see store.h and docs/recognizer.md).
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <json-c/json.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "model.h"
#include "sample.h"
#include "store.h"

#define META_HEAD "glassine-handwriting-meta"
#define EXPORT_FORMAT "glassine-handwriting-export"

static int base_dir(char *out, size_t n, const char *xdg, const char *home_rel)
{
	const char *x = getenv(xdg), *h = getenv("HOME");
	int r;
	if (x && x[0] == '/') r = snprintf(out, n, "%s/glassine", x);
	else if (h && *h) r = snprintf(out, n, "%s/%s/glassine", h, home_rel);
	else return -1;
	return r < 0 || (size_t)r >= n ? -1 : 0;
}

int hw_store_path(char *out, size_t n, const char *rel)
{
	char b[3800];
	if (base_dir(b, sizeof b, "XDG_DATA_HOME", ".local/share")) return -1;
	int r = snprintf(out, n, "%s/handwriting%s%s", b, *rel ? "/" : "", rel);
	return r < 0 || (size_t)r >= n ? -1 : 0;
}

int hw_store_adaptation_path(char *out, size_t n, const char *lang, const char *slot)
{
	char rel[128];
	snprintf(rel, sizeof rel, "adaptation/%s/%s", lang, slot);
	return hw_store_path(out, n, rel);
}

static int mkdir_0700(const char *p)
{
	if (mkdir(p, 0700) && errno != EEXIST) return -1;
	return chmod(p, 0700) && errno != EPERM ? -1 : 0;
}

int hw_store_mkdir(const char *rel)
{
	char b[3800], p[4096];
	if (base_dir(b, sizeof b, "XDG_DATA_HOME", ".local/share")) return -1;
	/* the parents (~/.local/share/glassine) with the usual mode, ours 0700 */
	for (char *s = b + 1; *s; s++)
		if (*s == '/') { *s = 0; mkdir(b, 0755); *s = '/'; }
	mkdir(b, 0755);
	snprintf(p, sizeof p, "%s/handwriting", b);
	if (mkdir_0700(p)) return -1;
	if (!*rel) return 0;
	size_t base = strlen(p);
	snprintf(p + base, sizeof p - base, "/%s", rel);
	for (char *s = p + base + 1; *s; s++)
		if (*s == '/') { *s = 0; if (mkdir_0700(p)) return -1; *s = '/'; }
	return mkdir_0700(p);
}

int hw_store_lock(void)
{
	char p[4096];
	if (hw_store_mkdir("") || hw_store_path(p, sizeof p, "lock")) return -1;
	int fd = open(p, O_RDWR | O_CREAT | O_CLOEXEC, 0600);
	if (fd < 0) return -1;
	while (flock(fd, LOCK_EX)) if (errno != EINTR) { close(fd); return -1; }
	return fd;
}

void hw_store_unlock(int fd)
{
	if (fd >= 0) close(fd);
}

int hw_store_replace(const char *rel, const char *data, size_t len)
{
	char p[4096], t[4200];
	if (hw_store_path(p, sizeof p, rel)) return -1;
	if (!data) return unlink(p) && errno != ENOENT ? -1 : 0;
	snprintf(t, sizeof t, "%s.tmp", p);
	int fd = open(t, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	if (fd < 0) return -1;
	fchmod(fd, 0600);
	size_t done = 0;
	while (done < len) {
		ssize_t w = write(fd, data + done, len - done);
		if (w < 0 && errno == EINTR) continue;
		if (w <= 0) { close(fd); unlink(t); return -1; }
		done += (size_t)w;
	}
	if (fsync(fd) || close(fd)) { unlink(t); return -1; }
	if (rename(t, p)) { unlink(t); return -1; }
	return 0;
}

/* ---------------------------------------------------------------- settings */

static int conf_path(char *out, size_t n, const char *name)
{
	char b[3800];
	if (base_dir(b, sizeof b, "XDG_CONFIG_HOME", ".config")) return -1;
	int r = snprintf(out, n, "%s/%s", b, name);
	return r < 0 || (size_t)r >= n ? -1 : 0;
}

static int conf_read(const char *name, const char *key, char *out, size_t n)
{
	char p[4096], line[1024];
	if (conf_path(p, sizeof p, name)) return 0;
	FILE *f = fopen(p, "re");
	if (!f) return 0;
	int found = 0;
	size_t kl = strlen(key);
	while (fgets(line, sizeof line, f)) {
		char *s = line;
		while (*s == ' ' || *s == '\t') s++;
		if (strncmp(s, key, kl)) continue;
		s += kl;
		while (*s == ' ' || *s == '\t') s++;
		if (*s != '=') continue;
		s++;
		while (*s == ' ' || *s == '\t') s++;
		size_t l = strcspn(s, "\r\n");
		while (l && (s[l - 1] == ' ' || s[l - 1] == '\t')) l--;
		if (l >= n) l = n - 1;
		memcpy(out, s, l);
		out[l] = 0;
		found = 1;
	}
	fclose(f);
	return found;
}

int hw_conf_get(const char *key, char *out, size_t n)
{
	return conf_read("handwriting.conf", key, out, n);
}

static int is_off(const char *v)
{
	return !strcmp(v, "off") || !strcmp(v, "false") || !strcmp(v, "0");
}

int hw_learning_on(void)
{
	char v[64];
	if (conf_read("handwriting.conf", "learning", v, sizeof v) && is_off(v)) return 0;
	if (conf_read("predict.conf", "learning", v, sizeof v) && is_off(v)) return 0;
	return 1;
}

int hw_conf_set(const char *key, const char *value)
{
	char p[4096], t[4200], line[1024];
	if (conf_path(p, sizeof p, "handwriting.conf")) return -1;
	char dir[4096];
	snprintf(dir, sizeof dir, "%s", p);
	*strrchr(dir, '/') = 0;
	for (char *s = dir + 1; *s; s++) if (*s == '/') { *s = 0; mkdir(dir, 0755); *s = '/'; }
	mkdir(dir, 0755);
	snprintf(t, sizeof t, "%s.tmp", p);
	FILE *in = fopen(p, "re"), *out = fopen(t, "we");
	if (!out) { if (in) fclose(in); return -1; }
	int done = 0;
	size_t kl = strlen(key);
	while (in && fgets(line, sizeof line, in)) {
		char *s = line;
		while (*s == ' ' || *s == '\t') s++;
		char *e = s + kl;
		while (*e == ' ' || *e == '\t') e++;
		if (!strncmp(s, key, kl) && *e == '=') {
			if (!done) fprintf(out, "%s = %s\n", key, value);
			done = 1;
			continue;
		}
		fputs(line, out);
		if (!strchr(line, '\n')) fputc('\n', out);
	}
	if (!done) fprintf(out, "%s = %s\n", key, value);
	if (in) fclose(in);
	if (fclose(out) || rename(t, p)) { unlink(t); return -1; }
	return 0;
}

/* ---------------------------------------------------------------- samples */

void hw_sample_id(const char *json, char out[17])
{
	snprintf(out, 17, "%016llx", (unsigned long long)hw_hash(json, strlen(json), 0));
}

static char *slurp(const char *path, size_t *len)
{
	FILE *f = fopen(path, "re");
	if (!f) return NULL;
	size_t cap = 1 << 16, n = 0;
	char *b = malloc(cap);
	size_t r;
	while (b && (r = fread(b + n, 1, cap - n - 1, f)) > 0) {
		n += r;
		if (n + 1 == cap) { char *x = realloc(b, cap *= 2); if (!x) { free(b); b = NULL; } else b = x; }
	}
	fclose(f);
	if (b) b[n] = 0;
	if (len) *len = n;
	return b;
}

/* a file of lines (samples, their meta, the donation): after a crash or a power loss a run
 * of zero bytes can stand where an append never reached the disk; zero bytes end a line
 * like a newline, so every line around them stays readable (a string stopped at the first
 * one, and all lines after it were lost) */
static char *slurp_lines(const char *path, size_t *len)
{
	size_t n;
	char *b = slurp(path, &n);
	if (b) for (size_t i = 0; i < n; i++) if (!b[i]) b[i] = '\n';
	if (len) *len = n;
	return b;
}

/* ---------------------------------------------------------------- the indexes */

/* samples.jsonl and donation/samples.jsonl each have an index beside them (samples.index,
 * donation/index): one line per line of the file, with its offset and length, and for the
 * samples what a listing needs (client, time, source, language, text, first reading). An
 * index is derived data: it is checked against its file on every open (the file's inode,
 * the erase count, the bytes it covers, the last sample's id), lines added after it (by a
 * recognizer of an earlier version, or lost in a crash) are read from the end of what it
 * covers, and anything else that doesn't fit is rebuilt from the file. So a listing, a
 * count, a lookup and an addition never read the whole file, and a deletion overwrites
 * the sample's bytes where they are instead of writing the file anew. */

#define INDEX_HEAD "glassine-handwriting-index"
#define DINDEX_HEAD "glassine-handwriting-donation-index"

struct ixe {
	char id[17];                    /* "" for bytes that are no sample (deleted, cut short) */
	long long off, len;             /* a sample: its line without the newline; else the bytes */
	int dup;                        /* the same sample earlier in the file (counted once) */
	struct hw_index_entry pub;      /* samples: meta, text, read (malloc'd) */
};

struct ix {
	const char *jrel, *irel, *head;
	int samples;                    /* with meta, text and reading */
	struct ixe *e;
	size_t n, cap;
	size_t *h, hcap;                /* id -> entry + 1 (0 empty), first occurrence */
	struct hw_index_entry *live;
	size_t nlive;
	struct stat js, is, ms;         /* the files as last checked (st_ino 0: missing) */
	unsigned long gen;
	int have;
};

static struct ix SIX = { "samples.jsonl", "samples.index", INDEX_HEAD, 1, NULL, 0, 0, NULL, 0, NULL, 0, { 0 }, { 0 }, { 0 }, 0, 0 };
static struct ix DIX = { "donation/samples.jsonl", "donation/index", DINDEX_HEAD, 0, NULL, 0, 0, NULL, 0, NULL, 0, { 0 }, { 0 }, { 0 }, 0, 0 };

static int same_file(const struct stat *a, const struct stat *b)
{
	return a->st_dev == b->st_dev && a->st_ino == b->st_ino && a->st_size == b->st_size &&
	       a->st_mtim.tv_sec == b->st_mtim.tv_sec && a->st_mtim.tv_nsec == b->st_mtim.tv_nsec;
}

static void stat0(const char *rel, struct stat *st)
{
	char p[4096];
	memset(st, 0, sizeof *st);
	if (hw_store_path(p, sizeof p, rel) || stat(p, st)) memset(st, 0, sizeof *st);
}

static size_t idhash(const char *id)
{
	return (size_t)strtoull(id, NULL, 16);
}

static void ix_clear(struct ix *x)
{
	for (size_t i = 0; i < x->n; i++) { free((char *)x->e[i].pub.text); free((char *)x->e[i].pub.read); }
	x->n = 0;
	x->nlive = 0;
	if (x->h) memset(x->h, 0, x->hcap * sizeof *x->h);
	x->have = 0;
}

static long ix_find(const struct ix *x, const char *id)
{
	if (!x->hcap) return -1;
	for (size_t k = idhash(id) & (x->hcap - 1);; k = (k + 1) & (x->hcap - 1)) {
		if (!x->h[k]) return -1;
		if (!strcmp(x->e[x->h[k] - 1].id, id)) return (long)x->h[k] - 1;
	}
}

static int ix_rehash(struct ix *x, size_t cap)
{
	size_t *h = calloc(cap, sizeof *h);
	if (!h) return -1;
	free(x->h);
	x->h = h;
	x->hcap = cap;
	for (size_t i = 0; i < x->n; i++) {
		x->e[i].dup = 0;
		if (!x->e[i].id[0]) continue;
		size_t k = idhash(x->e[i].id) & (cap - 1);
		for (; h[k]; k = (k + 1) & (cap - 1))
			if (!strcmp(x->e[h[k] - 1].id, x->e[i].id)) { x->e[i].dup = 1; break; }
		if (!x->e[i].dup) h[k] = i + 1;
	}
	return 0;
}

/* adds an entry (text and read taken over) */
static int ix_push(struct ix *x, const struct ixe *v)
{
	if (x->n == x->cap) {
		size_t c = x->cap ? x->cap * 2 : 256;
		struct ixe *e = realloc(x->e, c * sizeof *e);
		if (!e) return -1;
		x->e = e;
		x->cap = c;
	}
	x->e[x->n++] = *v;
	if ((x->n + 1) * 2 > x->hcap) return ix_rehash(x, x->hcap ? x->hcap * 4 : 1024);
	struct ixe *e = &x->e[x->n - 1];
	e->dup = 0;
	if (!e->id[0]) return 0;
	size_t k = idhash(e->id) & (x->hcap - 1);
	for (; x->h[k]; k = (k + 1) & (x->hcap - 1))
		if (!strcmp(x->e[x->h[k] - 1].id, e->id)) { e->dup = 1; return 0; }
	x->h[k] = x->n;
	return 0;
}

static int ix_live(struct ix *x)
{
	free(x->live);
	x->live = malloc((x->n + 1) * sizeof *x->live);
	if (!x->live) { x->nlive = 0; return -1; }
	x->nlive = 0;
	for (size_t i = 0; i < x->n; i++) if (x->e[i].id[0] && !x->e[i].dup) x->live[x->nlive++] = x->e[i].pub;
	return 0;
}

static long long ix_end(const struct ix *x)
{
	if (!x->n) return 0;
	const struct ixe *e = &x->e[x->n - 1];
	return e->off + e->len + (e->id[0] ? 1 : 0);
}

/* the text fields of an index line: backslash, tab, newline and carriage return escaped */
static void esc_put(FILE *f, const char *s)
{
	for (; *s; s++) {
		if (*s == '\\') fputs("\\\\", f);
		else if (*s == '\t') fputs("\\t", f);
		else if (*s == '\n') fputs("\\n", f);
		else if (*s == '\r') fputs("\\r", f);
		else fputc(*s, f);
	}
}

static char *unesc_dup(const char *s)
{
	char *o = malloc(strlen(s) + 1), *p = o;
	if (!o) return NULL;
	for (; *s; s++) {
		if (*s == '\\' && s[1]) {
			s++;
			*p++ = *s == 't' ? '\t' : *s == 'n' ? '\n' : *s == 'r' ? '\r' : *s;
		} else *p++ = *s;
	}
	*p = 0;
	return o;
}

static void ix_put(FILE *f, const struct ix *x, const struct ixe *e)
{
	if (!e->id[0]) { fprintf(f, "-\t%lld\t%lld\n", e->off, e->len); return; }
	fprintf(f, "%s\t%lld\t%lld", e->id, e->off, e->len);
	if (x->samples) {
		const struct hw_meta *m = &e->pub.meta;
		fprintf(f, "\t%s\t%lld\t%s\t%s\t", m->client, m->time, m->source, m->lang);
		esc_put(f, e->pub.text ? e->pub.text : "");
		fputc('\t', f);
		esc_put(f, e->pub.read ? e->pub.read : "");
	}
	fputc('\n', f);
}

/* the whole index anew (tmp + rename); sync: on the disk before it returns */
static int ix_write(struct ix *x, unsigned long long ino, int sync)
{
	char p[4096], t[4200];
	if (hw_store_path(p, sizeof p, x->irel)) return -1;
	snprintf(t, sizeof t, "%s.tmp", p);
	int fd = open(t, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	FILE *f = fd >= 0 ? fdopen(fd, "w") : NULL;
	if (!f) { if (fd >= 0) close(fd); return -1; }
	fprintf(f, "%s 1 %llu %lu\n", x->head, ino, x->gen);
	for (size_t i = 0; i < x->n; i++) ix_put(f, x, &x->e[i]);
	int r = fflush(f) || (sync && fsync(fileno(f))) ? -1 : 0;
	if (fclose(f)) r = -1;
	if (!r && rename(t, p)) r = -1;
	if (r) unlink(t);
	return r;
}

/* entries from..n appended to the index file (not synced: it is rebuilt when lost) */
static int ix_append(struct ix *x, size_t from)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, x->irel)) return -1;
	FILE *f = fopen(p, "ae");
	if (!f) return -1;
	for (size_t i = from; i < x->n; i++) ix_put(f, x, &x->e[i]);
	return fclose(f) ? -1 : 0;
}

/* the index file, when it belongs to the file with this inode and erase count; -1 when it
 * doesn't (the entries are then empty), 1 when it was read only up to a bad line */
static int ix_read(struct ix *x, unsigned long long ino)
{
	char p[4096];
	ix_clear(x);
	if (hw_store_path(p, sizeof p, x->irel)) return -1;
	size_t len;
	char *s = slurp(p, &len);
	if (!s) return -1;
	int ret = -1;
	char *line = s, *e = memchr(s, '\n', len);
	if (!e) goto out;
	*e = 0;
	char head[64];
	unsigned long long fino;
	unsigned long fgen;
	if (sscanf(line, "%63s 1 %llu %lu", head, &fino, &fgen) != 3 || strcmp(head, x->head) || fino != ino ||
	    fgen != x->gen)
		goto out;
	ret = 0;
	long long prev = 0;
	for (line = e + 1; line < s + len; line = e + 1) {
		e = memchr(line, '\n', (size_t)(s + len - line));
		if (!e) { ret = 1; break; }                 /* a line cut short */
		*e = 0;
		char *f[9];
		int nf = 0;
		for (char *c = line; nf < 9;) {
			f[nf++] = c;
			char *t = strchr(c, '\t');
			if (!t) break;
			*t = 0;
			c = t + 1;
		}
		struct ixe v;
		memset(&v, 0, sizeof v);
		char *end1, *end2;
		v.off = strtoll(nf > 1 ? f[1] : "", &end1, 10);
		v.len = strtoll(nf > 2 ? f[2] : "", &end2, 10);
		int junk = !strcmp(f[0], "-");
		if (nf < 3 || *end1 || *end2 || v.off < prev || v.len < 0 || (!junk && strlen(f[0]) != 16) ||
		    (junk && nf != 3) || (!junk && nf != (x->samples ? 9 : 3))) { ret = 1; break; }
		if (!junk) {
			strcpy(v.id, f[0]);
			if (x->samples) {
				struct hw_meta *m = &v.pub.meta;
				if (strlen(f[3]) >= sizeof m->client || strlen(f[5]) >= sizeof m->source ||
				    strlen(f[6]) >= sizeof m->lang) { ret = 1; break; }
				strcpy(m->id, v.id); strcpy(m->client, f[3]); strcpy(m->source, f[5]); strcpy(m->lang, f[6]);
				m->time = atoll(f[4]);
				v.pub.text = unesc_dup(f[7]);
				v.pub.read = unesc_dup(f[8]);
				if (!v.pub.text || !v.pub.read) { free((char *)v.pub.text); free((char *)v.pub.read); ret = 1; break; }
			}
		}
		if (ix_push(x, &v)) { free((char *)v.pub.text); free((char *)v.pub.read); ret = -1; break; }
		prev = v.off + v.len + (junk ? 0 : 1);
	}
	if (ret < 0) ix_clear(x);
out:
	free(s);
	return ret;
}

/* skips one JSON value; the byte after it, NULL when it isn't one */
static const char *jskip(const char *s)
{
	s += strspn(s, " \t\r\n");
	if (*s == '"') {
		for (s++; *s && *s != '"'; s++) if (*s == '\\' && s[1]) s++;
		return *s ? s + 1 : NULL;
	}
	if (*s == '{' || *s == '[') {
		int depth = 0;
		for (; *s; s++) {
			if (*s == '"') {
				for (s++; *s && *s != '"'; s++) if (*s == '\\' && s[1]) s++;
				if (!*s) return NULL;
			} else if (*s == '{' || *s == '[') depth++;
			else if ((*s == '}' || *s == ']') && --depth == 0) return s + 1;
		}
		return NULL;
	}
	size_t n = strcspn(s, ",}] \t\r\n");
	return n ? s + n : NULL;
}

static json_object *jsub(const char *a, const char *b)
{
	char *t = strndup(a, (size_t)(b - a));
	json_object *o = t ? json_tokener_parse(t) : NULL;
	free(t);
	return o;
}

/* a sample's text and first reading ("" when none) without reading its strokes: the
 * top-level keys are walked and only those two values parsed */
static int peek_sample(const char *line, char **text, char **read)
{
	*text = *read = NULL;
	const char *s = line + strspn(line, " \t");
	if (*s++ != '{') return -1;
	for (;;) {
		s += strspn(s, " \t\r\n");
		if (*s == '}') break;
		const char *k = s, *ke = *s == '"' ? jskip(s) : NULL;
		if (!ke) goto bad;
		s = ke + strspn(ke, " \t\r\n");
		if (*s++ != ':') goto bad;
		s += strspn(s, " \t\r\n");
		const char *v = s, *ve = jskip(s);
		if (!ve) goto bad;
		size_t kl = (size_t)(ke - k);
		int is_text = kl == 6 && !memcmp(k, "\"text\"", 6), is_read = kl == 9 && !memcmp(k, "\"reading\"", 9);
		if ((is_text && !*text) || (is_read && !*read)) {
			json_object *o = jsub(v, ve), *c, *t;
			if (is_text && o && json_object_is_type(o, json_type_string)) *text = strdup(json_object_get_string(o));
			if (is_read && o && json_object_is_type(o, json_type_array)) {
				c = json_object_array_length(o) ? json_object_array_get_idx(o, 0) : NULL;
				*read = strdup(c && json_object_object_get_ex(c, "text", &t) && json_object_is_type(t, json_type_string)
					       ? json_object_get_string(t) : "");
			}
			json_object_put(o);
			if (is_text && !*text) goto bad;
		}
		s = ve + strspn(ve, " \t\r\n");
		if (*s == ',') { s++; continue; }
		if (*s == '}') break;
		goto bad;
	}
	if (!*text) goto bad;
	if (!*read) *read = strdup("");
	if (*read) return 0;
bad:
	free(*text); free(*read);
	*text = *read = NULL;
	return -1;
}

/* meta lines: ID TAB CLIENT TAB TIME TAB SOURCE TAB LANG */
static int parse_meta(char *line, struct hw_meta *m)
{
	char *f[5];
	int n = 0;
	for (char *s = line; n < 5; n++) {
		f[n] = s;
		char *t = strchr(s, '\t');
		if (!t) { n++; break; }
		*t = 0;
		s = t + 1;
	}
	if (n != 5 || strlen(f[0]) != 16 || strlen(f[1]) >= sizeof m->client || strlen(f[3]) >= sizeof m->source ||
	    strlen(f[4]) >= sizeof m->lang)
		return -1;
	memset(m, 0, sizeof *m);
	strcpy(m->id, f[0]); strcpy(m->client, f[1]); strcpy(m->source, f[3]); strcpy(m->lang, f[4]);
	m->time = atoll(f[2]);
	return 0;
}

static unsigned long read_gen(const char *meta)
{
	unsigned long g = 0;
	if (meta && !strncmp(meta, META_HEAD " 1 ", sizeof META_HEAD + 2)) g = strtoul(meta + sizeof META_HEAD + 2, NULL, 10);
	return g;
}

unsigned long hw_store_generation(void)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "samples.meta")) return 0;
	FILE *f = fopen(p, "re");
	if (!f) return 0;
	char line[256] = "";
	if (!fgets(line, sizeof line, f)) line[0] = 0;
	fclose(f);
	return read_gen(line);
}

/* samples.meta as a table by id (the first line of an id wins, as in a load) */
struct metamap {
	struct hw_meta *m;
	size_t n, *h, hcap;
};

static void metamap_free(struct metamap *mm)
{
	free(mm->m); free(mm->h);
	memset(mm, 0, sizeof *mm);
}

static int metamap_load(struct metamap *mm, unsigned long *gen)
{
	char p[4096];
	memset(mm, 0, sizeof *mm);
	if (gen) *gen = 0;
	if (hw_store_path(p, sizeof p, "samples.meta")) return -1;
	char *meta = slurp_lines(p, NULL);
	if (gen) *gen = read_gen(meta);
	size_t cap = 0;
	for (char *s = meta, *e; s && *s; s = e) {
		e = strchr(s, '\n');
		if (e) *e++ = 0; else e = s + strlen(s);
		struct hw_meta m;
		if (parse_meta(s, &m)) continue;
		if (mm->n == cap) { cap = cap ? cap * 2 : 256; struct hw_meta *x = realloc(mm->m, cap * sizeof *x); if (!x) break; mm->m = x; }
		mm->m[mm->n++] = m;
	}
	free(meta);
	for (mm->hcap = 1024; mm->hcap < mm->n * 2 + 2; mm->hcap *= 2) {}
	mm->h = calloc(mm->hcap, sizeof *mm->h);
	if (!mm->h) { metamap_free(mm); return -1; }
	for (size_t i = 0; i < mm->n; i++) {
		size_t k = idhash(mm->m[i].id) & (mm->hcap - 1);
		int seen = 0;
		for (; mm->h[k]; k = (k + 1) & (mm->hcap - 1)) if (!strcmp(mm->m[mm->h[k] - 1].id, mm->m[i].id)) { seen = 1; break; }
		if (!seen) mm->h[k] = i + 1;
	}
	return 0;
}

static const struct hw_meta *metamap_get(const struct metamap *mm, const char *id)
{
	if (!mm->hcap) return NULL;
	for (size_t k = idhash(id) & (mm->hcap - 1); mm->h[k]; k = (k + 1) & (mm->hcap - 1))
		if (!strcmp(mm->m[mm->h[k] - 1].id, id)) return &mm->m[mm->h[k] - 1];
	return NULL;
}

/* reads the file from off to its end into entries: each whole line a sample (its id the
 * hash of the line), anything else (a line cut short, zero bytes after a crash, a deleted
 * sample's blanks) bytes that are none */
static int ix_scan(struct ix *x, int fd, long long off, long long size, struct metamap *mm)
{
	long long n = size - off;
	if (n <= 0) return 0;
	char *b = malloc((size_t)n + 1);
	if (!b) return -1;
	long long got = 0;
	while (got < n) {
		ssize_t r = pread(fd, b + got, (size_t)(n - got), off + got);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) break;
		got += r;
	}
	if (got < n) { free(b); return -1; }
	b[n] = 0;
	int ret = 0;
	long long junk0 = -1;
	for (long long i = 0; i < n && !ret;) {
		char *s = b + i, *e = memchr(s, '\n', (size_t)(n - i));
		long long l = e ? e - s : n - i;
		int sample = 0;
		struct ixe v;
		memset(&v, 0, sizeof v);
		if (e && l >= 2 && s[0] == '{' && s[l - 1] == '}' && !memchr(s, 0, (size_t)l)) {
			*e = 0;
			sample = !x->samples || !peek_sample(s, (char **)&v.pub.text, (char **)&v.pub.read);
			if (!sample && x->samples) {        /* not something peek can walk: json-c decides */
				struct hw_sample smp;
				char err[64];
				if (!hw_sample_parse(s, &smp, err, sizeof err)) {
					v.pub.text = strdup(smp.text ? smp.text : "");
					v.pub.read = strdup(smp.reading.n ? smp.reading.text[0] : "");
					sample = v.pub.text && v.pub.read;
					hw_sample_free(&smp);
				}
			}
		}
		if (!sample) {
			free((char *)v.pub.text); free((char *)v.pub.read);
			if (junk0 < 0) junk0 = off + i;
			i += e ? l + 1 : l;
			continue;
		}
		if (junk0 >= 0) {
			struct ixe j = { .off = junk0, .len = off + i - junk0 };
			if (ix_push(x, &j)) ret = -1;
			junk0 = -1;
		}
		hw_sample_id(s, v.id);
		v.off = off + i;
		v.len = l;
		if (x->samples) {
			const struct hw_meta *m = mm ? metamap_get(mm, v.id) : NULL;
			if (m) v.pub.meta = *m;
			else { strcpy(v.pub.meta.id, v.id); strcpy(v.pub.meta.client, "-"); }
		}
		if (!ret && ix_push(x, &v)) { free((char *)v.pub.text); free((char *)v.pub.read); ret = -1; }
		i += l + 1;
	}
	if (!ret && junk0 >= 0) {
		struct ixe j = { .off = junk0, .len = size - junk0 };
		if (ix_push(x, &j)) ret = -1;
	}
	free(b);
	return ret;
}

/* writes over the bytes [off, off+len) with spaces, the first byte first, so a crash leaves
 * no line that still reads as the sample; on the disk before it returns */
static int blank(int fd, long long off, long long len)
{
	if (len <= 0) return 0;
	char sp[4096];
	memset(sp, ' ', sizeof sp);
	if (pwrite(fd, sp, 1, off) != 1 || fdatasync(fd)) return -1;
	for (long long d = 1; d < len;) {
		size_t k = len - d > (long long)sizeof sp ? sizeof sp : (size_t)(len - d);
		ssize_t w = pwrite(fd, sp, k, off + d);
		if (w < 0 && errno == EINTR) continue;
		if (w <= 0) return -1;
		d += w;
	}
	return fdatasync(fd);
}

static int line_is(int fd, const struct ixe *e)
{
	char *b = malloc((size_t)e->len + 2);
	if (!b) return 0;
	ssize_t r = pread(fd, b, (size_t)e->len + 1, e->off);
	int ok = r == e->len + 1 && b[e->len] == '\n';
	if (ok) {
		b[e->len] = 0;
		char id[17];
		hw_sample_id(b, id);
		ok = !strcmp(id, e->id);
	}
	free(b);
	return ok;
}

/* brings the index in step with its file; locked: the caller holds the store's lock (else
 * it is taken when anything has to be read or written) */
static int ix_sync(struct ix *x, int locked)
{
	struct stat j, i, m;
	stat0(x->jrel, &j);
	stat0(x->irel, &i);
	if (x->samples) stat0("samples.meta", &m); else memset(&m, 0, sizeof m);
	if (x->have && same_file(&j, &x->js) && same_file(&i, &x->is) && same_file(&m, &x->ms)) return 0;
	if (!j.st_ino) {                /* no file: nothing in it (and nothing to create) */
		ix_clear(x);
		x->gen = x->samples ? hw_store_generation() : 0;
		x->js = j; x->is = i; x->ms = m;
		x->have = 1;
		return ix_live(x);
	}
	int fd = locked ? -2 : hw_store_lock();
	if (fd == -1) return -1;
	char p[4096];
	int jfd = hw_store_path(p, sizeof p, x->jrel) ? -1 : open(p, O_RDWR | O_CLOEXEC);
	int ret = -1;
	struct metamap mm = { 0 };
	if (jfd < 0) goto out;
	struct stat js;
	if (fstat(jfd, &js)) goto out;
	unsigned long gen = 0;
	if (x->samples && metamap_load(&mm, &gen)) goto out;
	x->gen = gen;
	int r = ix_read(x, (unsigned long long)js.st_ino), rewrite = r != 0;
	if (r < 0 || ix_end(x) > js.st_size) { ix_clear(x); rewrite = 1; }
	/* the last sample must still be where the index says */
	for (size_t k = x->n; k-- > 0;) {
		if (!x->e[k].id[0]) continue;
		if (!line_is(jfd, &x->e[k])) { ix_clear(x); rewrite = 1; }
		break;
	}
	size_t had = x->n;
	if (ix_scan(x, jfd, ix_end(x), js.st_size, x->samples ? &mm : NULL)) goto out;
	/* bytes the index says are no sample are blanks: a crash between the index and the
	 * overwrite (the deletion finishes here), zero bytes or a line cut short by a crash
	 * are overwritten too */
	for (size_t k = 0; k < x->n; k++) {
		char c, nl = 0;
		const struct ixe *e = &x->e[k];
		if (!e->id[0] && e->len > 0 && pread(jfd, &c, 1, e->off) == 1 && c != ' ' && c != '\n' &&
		    pread(jfd, &nl, 1, e->off + e->len - 1) == 1)
			blank(jfd, e->off, e->len - (nl == '\n'));
	}
	if (rewrite) { if (ix_write(x, (unsigned long long)js.st_ino, 0)) {} }
	else if (x->n > had && ix_append(x, had)) {}
	stat0(x->jrel, &x->js);
	stat0(x->irel, &x->is);
	if (x->samples) stat0("samples.meta", &x->ms); else memset(&x->ms, 0, sizeof x->ms);
	x->have = 1;
	ret = ix_live(x);
out:
	if (ret) ix_clear(x);
	metamap_free(&mm);
	if (jfd >= 0) close(jfd);
	if (fd >= 0) hw_store_unlock(fd);
	return ret;
}

int hw_samples_index(const struct hw_index_entry **out, size_t *n)
{
	*out = NULL; *n = 0;
	if (ix_sync(&SIX, 0)) return -1;
	*out = SIX.live; *n = SIX.nlive;
	return 0;
}

static char *ix_line(struct ix *x, const char *id, struct hw_meta *m, int *err)
{
	*err = 0;
	for (int attempt = 0; attempt < 2; attempt++) {
		if (ix_sync(x, 0)) { *err = -1; return NULL; }
		long k = ix_find(x, id);
		if (k < 0) return NULL;
		const struct ixe *e = &x->e[k];
		char p[4096];
		int fd = hw_store_path(p, sizeof p, x->jrel) ? -1 : open(p, O_RDONLY | O_CLOEXEC);
		char *b = fd >= 0 ? malloc((size_t)e->len + 2) : NULL;
		ssize_t r = b ? pread(fd, b, (size_t)e->len + 1, e->off) : -1;
		if (fd >= 0) close(fd);
		if (r == e->len + 1 && b[e->len] == '\n') {
			b[e->len] = 0;
			char got[17];
			hw_sample_id(b, got);
			if (!strcmp(got, id)) {
				if (m) *m = e->pub.meta;
				return b;
			}
		}
		free(b);
		x->have = 0;                    /* the file changed under the index: read it again */
		struct stat z = { 0 };
		x->is = z;
		char ip[4096];
		if (!hw_store_path(ip, sizeof ip, x->irel)) unlink(ip);
	}
	*err = -1;
	return NULL;
}

char *hw_samples_line(const char *id, struct hw_meta *m, int *err)
{
	return ix_line(&SIX, id, m, err);
}

/* ---------------------------------------------------------------- samples */

void hw_entries_free(struct hw_entry *e, size_t n)
{
	for (size_t i = 0; i < n; i++) free(e[i].json);
	free(e);
}

int hw_samples_load(struct hw_entry **out, size_t *n, unsigned long *gen)
{
	char p[4096];
	*out = NULL; *n = 0;
	struct metamap mm;
	if (metamap_load(&mm, gen)) return -1;
	if (hw_store_path(p, sizeof p, "samples.jsonl")) { metamap_free(&mm); return -1; }
	char *data = slurp_lines(p, NULL);
	struct hw_entry *es = NULL;
	size_t ne = 0, ce = 0, hcap = 1024, *h = NULL;
	for (char *s = data, *e; s && *s; s = e) {
		e = strchr(s, '\n');
		if (e) *e++ = 0; else e = s + strlen(s);
		if (!*s) continue;
		size_t l = strlen(s);
		if (*s != '{' || s[l - 1] != '}') continue;    /* a line cut short by a crash, or deleted */
		if (ne == ce) { ce = ce ? ce * 2 : 64; struct hw_entry *x = realloc(es, ce * sizeof *x); if (!x) break; es = x; }
		if (!h || (ne + 1) * 2 > hcap) {
			size_t c = h ? hcap * 4 : hcap, *nh = calloc(c, sizeof *nh);
			if (!nh) break;
			for (size_t i = 0; i < ne; i++) {
				size_t k = idhash(es[i].meta.id) & (c - 1);
				while (nh[k]) k = (k + 1) & (c - 1);
				nh[k] = i + 1;
			}
			free(h); h = nh; hcap = c;
		}
		struct hw_entry *x = &es[ne];
		memset(x, 0, sizeof *x);
		hw_sample_id(s, x->meta.id);
		/* a line twice (an import of the same sample) counts once */
		size_t k = idhash(x->meta.id) & (hcap - 1);
		int dup = 0;
		for (; h[k]; k = (k + 1) & (hcap - 1)) if (!strcmp(es[h[k] - 1].meta.id, x->meta.id)) { dup = 1; break; }
		if (dup) continue;
		const struct hw_meta *m = metamap_get(&mm, x->meta.id);
		if (m) x->meta = *m; else strcpy(x->meta.client, "-");
		x->json = strdup(s);
		if (!x->json) break;
		h[k] = ++ne;
	}
	free(h);
	free(data);
	metamap_free(&mm);
	*out = es; *n = ne;
	return 0;
}

/* appends a line to a file of lines; *off: where it starts */
static int append_at(const char *rel, const char *line, long long *off)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, rel)) return -1;
	int fd = open(p, O_RDWR | O_APPEND | O_CREAT | O_CLOEXEC, 0600);
	if (fd < 0) return -1;
	fchmod(fd, 0600);
	/* a line cut short by a crash doesn't take this one with it */
	struct stat st;
	char last = '\n';
	if (fstat(fd, &st)) { close(fd); return -1; }
	if (st.st_size > 0 && pread(fd, &last, 1, st.st_size - 1) != 1) last = '\n';
	const char *parts[2] = { last == '\n' ? "" : "\n", line };
	if (off) *off = st.st_size + (last == '\n' ? 0 : 1);
	for (int k = 0; k < 2; k++) {
		size_t len = strlen(parts[k]), done = 0;
		while (done < len) {
			ssize_t w = write(fd, parts[k] + done, len - done);
			if (w < 0 && errno == EINTR) continue;
			if (w <= 0) { close(fd); return -1; }
			done += (size_t)w;
		}
	}
	/* on the disk before the call answers: a crash right after a sample was kept must not
	 * leave zero bytes in its place */
	if (fdatasync(fd)) { close(fd); return -1; }
	return close(fd);
}

static int append(const char *rel, const char *line)
{
	return append_at(rel, line, NULL);
}

static int ensure_meta_head(void)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "samples.meta")) return -1;
	struct stat st;
	if (!stat(p, &st) && st.st_size > 0) return 0;
	return append("samples.meta", META_HEAD " 1 0\n");
}

static void sanitize(char *s)
{
	for (; *s; s++) if (*s == '\t' || *s == '\n' || *s == '\r') *s = ' ';
}

/* a line added to an indexed file (the store's lock held, the index in step): the entry
 * goes into the index too */
static int ix_added(struct ix *x, const char *json, long long off, const struct hw_meta *m)
{
	struct ixe v;
	memset(&v, 0, sizeof v);
	hw_sample_id(json, v.id);
	v.off = off;
	v.len = (long long)strlen(json);
	if (x->samples) {
		v.pub.meta = *m;
		if (peek_sample(json, (char **)&v.pub.text, (char **)&v.pub.read)) {
			v.pub.text = strdup("");
			v.pub.read = strdup("");
		}
	}
	size_t had = x->n;
	/* an index that doesn't end where the line starts is rebuilt at the next look */
	if (ix_end(x) > off || ix_push(x, &v)) {
		free((char *)v.pub.text); free((char *)v.pub.read);
		x->have = 0;
		return 0;
	}
	struct stat js;
	stat0(x->jrel, &js);
	if ((x->is.st_ino ? ix_append(x, had) : ix_write(x, (unsigned long long)js.st_ino, 0)) || ix_live(x)) x->have = 0;
	stat0(x->jrel, &x->js);
	stat0(x->irel, &x->is);
	if (x->samples) stat0("samples.meta", &x->ms);
	return 0;
}

static int add_locked(const char *json, const struct hw_meta *m0)
{
	struct hw_meta m = *m0;
	hw_sample_id(json, m.id);
	if (ix_sync(&SIX, 1)) return -1;
	if (ix_find(&SIX, m.id) >= 0) return 1;
	sanitize(m.client); sanitize(m.source); sanitize(m.lang);
	if (!m.client[0]) strcpy(m.client, "-");
	char *line = malloc(strlen(json) + 2), meta[256];
	if (!line) return -1;
	sprintf(line, "%s\n", json);
	snprintf(meta, sizeof meta, "%s\t%s\t%lld\t%s\t%s\n", m.id, m.client, m.time, m.source, m.lang);
	long long off = 0;
	int r = ensure_meta_head() || append_at("samples.jsonl", line, &off) || append("samples.meta", meta) ? -1 : 0;
	free(line);
	if (!r) ix_added(&SIX, json, off, &m);
	else SIX.have = 0;
	return r;
}

int hw_samples_add(const char *json, const struct hw_meta *m)
{
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	int r = add_locked(json, m);
	hw_store_unlock(fd);
	return r;
}

static void rm_tree(const char *p)
{
	DIR *d = opendir(p);
	if (d) {
		struct dirent *e;
		while ((e = readdir(d))) {
			if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
			char q[4096];
			snprintf(q, sizeof q, "%s/%s", p, e->d_name);
			struct stat st;
			if (!lstat(q, &st) && S_ISDIR(st.st_mode)) rm_tree(q);
			else unlink(q);
		}
		closedir(d);
	}
	rmdir(p);
}

/* takes every line of the id out of an indexed file (the store's lock held, the index in
 * step): the lines at the end are cut off, the others overwritten with blanks where they
 * are; when the blanks grow to a quarter of the file, it is written anew without them.
 * The index goes to the disk first (with gen, the erase count it will belong to), so a
 * crash on the way either leaves the sample or finishes the deletion at the next look.
 * before_file: called between the index and the file (the meta's new erase count).
 * Returns the number of lines taken out, -1 on an error. */
static long ix_remove(struct ix *x, const char *id, unsigned long gen, int (*before_file)(void *), void *arg)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, x->jrel)) return -1;
	int fd = open(p, O_RDWR | O_CLOEXEC);
	if (fd < 0) return errno == ENOENT ? 0 : -1;
	struct stat st;
	long hits = 0;
	if (fstat(fd, &st)) { close(fd); return -1; }
	for (size_t k = 0; k < x->n; k++) if (x->e[k].id[0] && !strcmp(x->e[k].id, id)) hits++;
	if (!hits) { close(fd); return 0; }
	/* the lines to blank become bytes that are no sample; at the end they are cut */
	struct ixe *blanks = calloc((size_t)hits, sizeof *blanks);
	size_t nb = 0;
	if (!blanks) { close(fd); return -1; }
	for (size_t k = 0; k < x->n; k++) {
		struct ixe *e = &x->e[k];
		if (!e->id[0] || strcmp(e->id, id)) continue;
		free((char *)e->pub.text); free((char *)e->pub.read);
		e->pub.text = e->pub.read = NULL;
		e->id[0] = 0;
		e->len += 1;                    /* with its newline */
		blanks[nb++] = *e;
	}
	long long cut = st.st_size;
	while (x->n && !x->e[x->n - 1].id[0]) cut = x->e[--x->n].off;
	long long junk = 0, size = cut;
	for (size_t k = 0; k < x->n; k++) if (!x->e[k].id[0]) junk += x->e[k].len;
	int compact = junk * 4 > size;
	int r = -1;
	x->gen = gen;
	if (compact) {
		/* the live lines into a new file, then the index for it, then the rename */
		char t[4200];
		snprintf(t, sizeof t, "%s.tmp", p);
		int tfd = open(t, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
		struct stat ts;
		long long at = 0;
		int ok = tfd >= 0;
		size_t w = 0;
		for (size_t k = 0; ok && k < x->n; k++) {
			struct ixe *e = &x->e[k];
			if (!e->id[0]) { free((char *)e->pub.text); free((char *)e->pub.read); continue; }
			char *b = malloc((size_t)e->len + 1);
			ok = b && pread(fd, b, (size_t)e->len + 1, e->off) == e->len + 1;
			for (long long d = 0; ok && d < e->len + 1;) {
				ssize_t q = write(tfd, b + d, (size_t)(e->len + 1 - d));
				if (q < 0 && errno == EINTR) continue;
				if (q <= 0) ok = 0; else d += q;
			}
			free(b);
			e->off = at;
			at += e->len + 1;
			x->e[w++] = *e;
		}
		x->n = w;
		ok = ok && !fsync(tfd) && !fstat(tfd, &ts);
		if (tfd >= 0) close(tfd);
		if (ok && !ix_rehash(x, x->hcap) && !ix_write(x, (unsigned long long)ts.st_ino, 1) &&
		    (!before_file || !before_file(arg)) && !rename(t, p))
			r = 0;
		else unlink(t);
	} else if (!ix_write(x, (unsigned long long)st.st_ino, 1) && (!before_file || !before_file(arg))) {
		r = 0;
		if (cut < st.st_size && (ftruncate(fd, cut) || fsync(fd))) r = -1;
		for (size_t k = 0; !r && k < nb; k++)
			if (blanks[k].off < cut && blank(fd, blanks[k].off, blanks[k].len - 1)) r = -1;
	}
	free(blanks);
	close(fd);
	if (!compact) ix_rehash(x, x->hcap);
	x->have = 0;                    /* looked at again from the files */
	struct stat z = { 0 };
	x->is = z;
	return r ? -1 : hits;
}

/* samples.meta without the id's lines and with the erase count counted up */
struct meta_out { const char *id; unsigned long gen; };

static int rewrite_meta(void *arg)
{
	const struct meta_out *mo = arg;
	char p[4096];
	if (hw_store_path(p, sizeof p, "samples.meta")) return -1;
	size_t len;
	char *s = slurp_lines(p, &len);
	char *o = malloc(len + 128);
	if (!o) { free(s); return -1; }
	size_t ol = (size_t)snprintf(o, 128, "%s 1 %lu\n", META_HEAD, mo->gen);
	int first = 1;
	for (char *c = s, *e; c && *c; c = e) {
		e = strchr(c, '\n');
		if (e) *e++ = 0; else e = c + strlen(c);
		if (first) { first = 0; if (!strncmp(c, META_HEAD, sizeof META_HEAD - 1)) continue; }
		if (!*c || (mo->id && !strncmp(c, mo->id, 16) && c[16] == '\t')) continue;
		size_t l = strlen(c);
		memcpy(o + ol, c, l); o[ol + l] = '\n'; ol += l + 1;
	}
	free(s);
	int r = hw_store_replace("samples.meta", o, ol);
	free(o);
	return r;
}

long hw_samples_erase(const char *client)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "")) return -1;
	struct stat st;
	if (stat(p, &st)) return 0;      /* nothing stored: nothing to create either */
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	struct hw_entry *es;
	size_t n;
	unsigned long gen;
	long erased = -1;
	if (hw_samples_load(&es, &n, &gen)) goto out;
	size_t cap = 64, len = 0, mcap = 256, mlen = 0;
	char *data = malloc(cap), *meta = malloc(mcap);
	if (!data || !meta) { free(data); free(meta); hw_entries_free(es, n); goto out; }
	data[0] = 0;
	mlen = (size_t)snprintf(meta, mcap, "%s 1 %lu\n", META_HEAD, gen + 1);
	erased = 0;
	for (size_t i = 0; i < n; i++) {
		if (!client || !strcmp(es[i].meta.client, client)) { erased++; continue; }
		size_t l = strlen(es[i].json) + 1;
		if (len + l + 1 > cap) { cap = (len + l + 1) * 2; char *x = realloc(data, cap); if (!x) { erased = -1; break; } data = x; }
		memcpy(data + len, es[i].json, l - 1); data[len + l - 1] = '\n'; len += l; data[len] = 0;
		char ml[256];
		const struct hw_meta *m = &es[i].meta;
		int k = snprintf(ml, sizeof ml, "%s\t%s\t%lld\t%s\t%s\n", m->id, m->client, m->time, m->source, m->lang);
		if (mlen + (size_t)k + 1 > mcap) { mcap = (mlen + (size_t)k + 1) * 2; char *x = realloc(meta, mcap); if (!x) { erased = -1; break; } meta = x; }
		memcpy(meta + mlen, ml, (size_t)k + 1); mlen += (size_t)k;
	}
	hw_entries_free(es, n);
	if (erased >= 0) {
		/* the index first (derived, rebuilt from what is left), then the generation: a
		 * running adaptation sees it and drops its result */
		if (hw_store_replace("samples.index", NULL, 0) || hw_store_replace("samples.meta", meta, mlen) ||
		    hw_store_replace("samples.jsonl", len ? data : NULL, len))
			erased = -1;
		char a[4096];
		if (!hw_store_path(a, sizeof a, "adaptation")) rm_tree(a);
	}
	free(data); free(meta);
	SIX.have = 0;
out:
	hw_store_unlock(fd);
	return erased;
}

int hw_samples_delete(const char *id, const char *owner)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "")) return -1;
	struct stat st;
	if (stat(p, &st)) return 0;
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	int r = -1;
	if (ix_sync(&SIX, 1)) goto out;
	long k = ix_find(&SIX, id);
	if (k < 0) { r = 0; goto out; }
	if (owner && strcmp(SIX.e[k].pub.meta.client, owner)) { r = -2; goto out; }
	/* counts as a change: a running adaptation drops its result */
	struct meta_out mo = { id, SIX.gen + 1 };
	long n = ix_remove(&SIX, id, mo.gen, rewrite_meta, &mo);
	r = n < 0 ? -1 : n > 0;
out:
	hw_store_unlock(fd);
	return r;
}

/* ---------------------------------------------------------------- export and import */

static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static char *b64enc(const unsigned char *d, size_t n)
{
	char *o = malloc(4 * ((n + 2) / 3) + 1), *p = o;
	if (!o) return NULL;
	for (size_t i = 0; i < n; i += 3) {
		unsigned v = (unsigned)d[i] << 16 | (i + 1 < n ? (unsigned)d[i + 1] << 8 : 0) | (i + 2 < n ? d[i + 2] : 0);
		*p++ = B64[v >> 18 & 63]; *p++ = B64[v >> 12 & 63];
		*p++ = i + 1 < n ? B64[v >> 6 & 63] : '=';
		*p++ = i + 2 < n ? B64[v & 63] : '=';
	}
	*p = 0;
	return o;
}

static unsigned char *b64dec(const char *s, size_t *n)
{
	unsigned char *o = malloc(strlen(s) / 4 * 3 + 3);
	if (!o) return NULL;
	unsigned v = 0;
	int bits = 0;
	size_t k = 0;
	for (; *s && *s != '='; s++) {
		const char *p = strchr(B64, *s);
		if (!p || !*s) { free(o); return NULL; }
		v = v << 6 | (unsigned)(p - B64); bits += 6;
		if (bits >= 8) { bits -= 8; o[k++] = (unsigned char)(v >> bits); }
	}
	*n = k;
	return o;
}

int hw_export(const char *path, const char *client)
{
	struct hw_entry *es;
	size_t n;
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	if (hw_samples_load(&es, &n, NULL)) { hw_store_unlock(fd); return -1; }
	char tmp[4200];
	snprintf(tmp, sizeof tmp, "%s.tmp", path);
	int ofd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	FILE *f = ofd >= 0 ? fdopen(ofd, "w") : NULL;
	int r = -1;
	if (!f) goto out;
	fprintf(f, "{\"format\":\"%s\",\"version\":1}\n", EXPORT_FORMAT);
	for (size_t i = 0; i < n; i++) {
		if (client && strcmp(es[i].meta.client, client)) continue;
		json_object *o = json_object_new_object();
		json_object_object_add(o, "client", json_object_new_string(es[i].meta.client));
		json_object_object_add(o, "confirmed", json_object_new_int64(es[i].meta.time));
		json_object_object_add(o, "source", json_object_new_string(es[i].meta.source));
		fprintf(f, "{\"sample\":%s,%s\n", es[i].json, json_object_to_json_string_ext(o, JSON_C_TO_STRING_PLAIN) + 1);
		json_object_put(o);
	}
	char ad[4096];
	if (!client && !hw_store_path(ad, sizeof ad, "adaptation")) {
		DIR *d = opendir(ad);
		struct dirent *e;
		while (d && (e = readdir(d))) {
			if (e->d_name[0] == '.') continue;
			for (int s = 0; s < 2; s++) {
				const char *slot = s ? "previous" : "current";
				char p[4400];
				snprintf(p, sizeof p, "%s/%s/%s", ad, e->d_name, slot);
				size_t len;
				char *bin = slurp(p, &len);
				if (!bin) continue;
				char *b = b64enc((unsigned char *)bin, len);
				free(bin);
				if (!b) continue;
				json_object *o = json_object_new_object();
				json_object_object_add(o, "lang", json_object_new_string(e->d_name));
				json_object_object_add(o, "slot", json_object_new_string(slot));
				json_object_object_add(o, "data", json_object_new_string(b));
				free(b);
				fprintf(f, "{\"adaptation\":%s}\n", json_object_to_json_string_ext(o, JSON_C_TO_STRING_PLAIN));
				json_object_put(o);
			}
		}
		if (d) closedir(d);
	}
	r = 0;
	if (fflush(f) || fsync(fileno(f))) r = -1;
	if (fclose(f)) r = -1;
	f = NULL;
	if (!r && rename(tmp, path)) r = -1;
	if (r) unlink(tmp);
out:
	if (f) fclose(f);
	hw_entries_free(es, n);
	hw_store_unlock(fd);
	return r;
}

static int valid_lang(const char *s)
{
	size_t n = strlen(s);
	if (n < 2 || n > 35) return 0;
	for (; *s; s++) if (!((*s >= 'a' && *s <= 'z') || (*s >= 'A' && *s <= 'Z') || (*s >= '0' && *s <= '9') || *s == '-' || *s == '_')) return 0;
	return 1;
}

long hw_import(const char *path)
{
	FILE *f = fopen(path, "re");
	if (!f) { fprintf(stderr, "glassine-handwriting: %s: %s\n", path, strerror(errno)); return -1; }
	char *line = NULL;
	size_t cap = 0;
	ssize_t len;
	long added = 0, lineno = 0;
	int fd = -1, ok = 1;
	while ((len = getline(&line, &cap, f)) > 0) {
		lineno++;
		json_object *o = json_tokener_parse(line), *v, *w;
		if (!o || !json_object_is_type(o, json_type_object)) {
			fprintf(stderr, "glassine-handwriting: %s:%ld: not JSON\n", path, lineno);
			ok = 0; json_object_put(o); break;
		}
		if (lineno == 1) {
			if (!json_object_object_get_ex(o, "format", &v) || strcmp(json_object_get_string(v), EXPORT_FORMAT) ||
			    !json_object_object_get_ex(o, "version", &w) || json_object_get_int(w) != 1) {
				fprintf(stderr, "glassine-handwriting: %s: not an export of version 1\n", path);
				ok = 0; json_object_put(o); break;
			}
			json_object_put(o);
			if ((fd = hw_store_lock()) < 0) { ok = 0; break; }
			continue;
		}
		if (json_object_object_get_ex(o, "sample", &v)) {
			const char *js = json_object_to_json_string_ext(v, JSON_C_TO_STRING_PLAIN | JSON_C_TO_STRING_NOSLASHESCAPE);
			struct hw_sample s;
			char err[128];
			if (hw_sample_parse(js, &s, err, sizeof err)) {
				fprintf(stderr, "glassine-handwriting: %s:%ld: %s (skipped)\n", path, lineno, err);
			} else {
				struct hw_meta m = { 0 };
				if (json_object_object_get_ex(o, "client", &w)) snprintf(m.client, sizeof m.client, "%s", json_object_get_string(w));
				if (json_object_object_get_ex(o, "confirmed", &w)) m.time = json_object_get_int64(w);
				snprintf(m.source, sizeof m.source, "%s", s.source);
				snprintf(m.lang, sizeof m.lang, "%s", s.lang);
				/* stored as written by this program, so the id is the same on every device */
				char *line2 = hw_sample_format(&s);
				int r = line2 ? add_locked(line2, &m) : -1;
				free(line2);
				if (r < 0) { ok = 0; hw_sample_free(&s); json_object_put(o); break; }
				added += r == 0;
				hw_sample_free(&s);
			}
		} else if (json_object_object_get_ex(o, "adaptation", &v)) {
			json_object *l, *sl, *d;
			if (json_object_object_get_ex(v, "lang", &l) && json_object_object_get_ex(v, "slot", &sl) &&
			    json_object_object_get_ex(v, "data", &d) && valid_lang(json_object_get_string(l)) &&
			    (!strcmp(json_object_get_string(sl), "current") || !strcmp(json_object_get_string(sl), "previous"))) {
				char rel[128];
				snprintf(rel, sizeof rel, "adaptation/%s/%s", json_object_get_string(l), json_object_get_string(sl));
				char p[4096];
				struct stat st;
				size_t bn;
				unsigned char *bin = b64dec(json_object_get_string(d), &bn);
				/* never over the adaptation of this device: that one came from more samples here */
				if (bin && !hw_store_path(p, sizeof p, rel) && stat(p, &st)) {
					char dir[128];
					snprintf(dir, sizeof dir, "adaptation/%s", json_object_get_string(l));
					if (hw_store_mkdir(dir) || hw_store_replace(rel, (char *)bin, bn)) ok = 0;
				}
				free(bin);
			}
		}
		json_object_put(o);
	}
	free(line);
	fclose(f);
	if (lineno == 0) { fprintf(stderr, "glassine-handwriting: %s: empty\n", path); ok = 0; }
	hw_store_unlock(fd);
	return ok ? added : -1;
}

/* ---------------------------------------------------------------- donation */

static int donor_make(char *out, size_t n);

int hw_donation_start(const char *licence)
{
	if (strcmp(licence, "CC0-1.0") && strcmp(licence, "CC-BY-4.0")) return -1;
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	char l[64];
	int n = snprintf(l, sizeof l, "%s\n", licence);
	char id[40];
	int r = hw_store_mkdir("donation") || hw_store_replace("donation/licence", l, (size_t)n) ? -1 : 0;
	if (!r && !hw_donor_id(id, sizeof id) && donor_make(id, sizeof id)) r = -1;
	hw_store_unlock(fd);
	return r;
}

int hw_donation_licence(char *out, size_t n)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "donation/licence")) return 0;
	char *s = slurp(p, NULL);
	if (!s) return 0;
	s[strcspn(s, "\r\n")] = 0;
	int ok = !strcmp(s, "CC0-1.0") || !strcmp(s, "CC-BY-4.0");
	if (ok) snprintf(out, n, "%s", s);
	free(s);
	return ok;
}

int hw_donation_add(const char *json)
{
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	char *line = malloc(strlen(json) + 2);
	int r = -1;
	if (line && !ix_sync(&DIX, 1)) {
		sprintf(line, "%s\n", json);
		long long off = 0;
		r = append_at("donation/samples.jsonl", line, &off);
		if (!r) ix_added(&DIX, json, off, NULL);
		else DIX.have = 0;
	}
	free(line);
	hw_store_unlock(fd);
	return r;
}

long hw_donation_count(void)
{
	if (ix_sync(&DIX, 0)) return 0;
	long n = 0;
	for (size_t i = 0; i < DIX.n; i++) n += DIX.e[i].id[0] != 0;     /* every line, as written */
	return n;
}

int hw_donation_write(const char *path)
{
	char p[4096], lic[16], donor[40];
	if (!hw_donation_licence(lic, sizeof lic) || hw_store_path(p, sizeof p, "donation/samples.jsonl")) return -1;
	/* a donation started before donor IDs gets one now */
	if (!hw_donor_id(donor, sizeof donor) && hw_donor_new(donor, sizeof donor)) return -1;
	char *s = slurp_lines(p, NULL);
	if (!s) return -1;
	char tmp[4200];
	snprintf(tmp, sizeof tmp, "%s.tmp", path);
	FILE *f = fopen(tmp, "we");
	int r = f ? 0 : -1;
	for (char *c = s, *e; f && *c; c = e) {
		e = strchr(c, '\n');
		if (e) *e++ = 0; else e = c + strlen(c);
		struct hw_sample smp;
		char err[128];
		if (!*c || hw_sample_parse(c, &smp, err, sizeof err)) continue;
		/* only what the format allows in a donation: no client, time or device; times
		 * start at 0 */
		snprintf(smp.licence, sizeof smp.licence, "%s", lic);
		snprintf(smp.donor, sizeof smp.donor, "%s", donor);
		double t0 = smp.nstrokes && smp.strokes[0].n ? smp.strokes[0].t[0] : 0;
		for (size_t i = 0; i < smp.nstrokes; i++)
			for (size_t k = 0; k < smp.strokes[i].n; k++) smp.strokes[i].t[k] -= t0;
		char *out = hw_sample_format(&smp);
		if (!out || fprintf(f, "%s\n", out) < 0) r = -1;
		free(out);
		hw_sample_free(&smp);
	}
	free(s);
	if (f && fclose(f)) r = -1;
	if (!r && rename(tmp, path)) r = -1;
	if (r) unlink(tmp);
	return r;
}

int hw_donation_remove(const char *id)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "donation/samples.jsonl")) return -1;
	struct stat st;
	if (stat(p, &st)) return 0;
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	long n = ix_sync(&DIX, 1) ? -1 : ix_remove(&DIX, id, 0, NULL, NULL);
	hw_store_unlock(fd);
	return n < 0 ? -1 : n > 0;
}

int hw_donation_has(const char *id)
{
	if (ix_sync(&DIX, 0)) return -1;
	return ix_find(&DIX, id) >= 0;
}

/* a donated sample's line by id (malloc'd, NULL when there is none) */
char *hw_donation_line(const char *id)
{
	int err;
	return ix_line(&DIX, id, NULL, &err);
}

int hw_donation_erase(void)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "donation")) return -1;
	struct stat st;
	if (stat(p, &st)) return 0;
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	/* everything but the donor ID: later donations count as the same writer's */
	DIR *d = opendir(p);
	struct dirent *e;
	while (d && (e = readdir(d))) {
		if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..") || !strcmp(e->d_name, "donor-id")) continue;
		char q[4400];
		snprintf(q, sizeof q, "%s/%s", p, e->d_name);
		struct stat qs;
		if (!lstat(q, &qs) && S_ISDIR(qs.st_mode)) rm_tree(q);
		else unlink(q);
	}
	if (d) closedir(d);
	rmdir(p);                       /* only when nothing was left in it */
	DIX.have = 0;
	hw_store_unlock(fd);
	return 0;
}

int hw_donor_id(char *out, size_t n)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "donation/donor-id")) return 0;
	char *s = slurp(p, NULL);
	if (!s) return 0;
	s[strcspn(s, "\r\n")] = 0;
	int ok = strlen(s) == 36 && strspn(s, "0123456789abcdef-") == 36;
	if (ok) snprintf(out, n, "%s", s);
	free(s);
	return ok;
}

/* a random UUID (version 4) from the system's random source: nothing about the device or
 * the person goes into it (the store's lock held) */
static int donor_make(char *out, size_t n)
{
	unsigned char b[16];
	int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
	if (fd < 0) return -1;
	ssize_t r = read(fd, b, sizeof b);
	close(fd);
	if (r != (ssize_t)sizeof b) return -1;
	b[6] = (unsigned char)((b[6] & 0x0f) | 0x40);
	b[8] = (unsigned char)((b[8] & 0x3f) | 0x80);
	char id[40], line[48];
	snprintf(id, sizeof id, "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", b[0], b[1], b[2],
		 b[3], b[4], b[5], b[6], b[7], b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]);
	int k = snprintf(line, sizeof line, "%s\n", id);
	if (hw_store_mkdir("donation") || hw_store_replace("donation/donor-id", line, (size_t)k)) return -1;
	snprintf(out, n, "%s", id);
	return 0;
}

int hw_donor_new(char *out, size_t n)
{
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	int r = donor_make(out, n);
	hw_store_unlock(fd);
	return r;
}
