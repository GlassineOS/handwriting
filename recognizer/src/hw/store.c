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

void hw_entries_free(struct hw_entry *e, size_t n)
{
	for (size_t i = 0; i < n; i++) free(e[i].json);
	free(e);
}

int hw_samples_load(struct hw_entry **out, size_t *n, unsigned long *gen)
{
	char p[4096];
	*out = NULL; *n = 0;
	if (gen) *gen = 0;
	if (hw_store_path(p, sizeof p, "samples.meta")) return -1;
	char *meta = slurp(p, NULL);
	if (gen) *gen = read_gen(meta);
	struct hw_meta *mm = NULL;
	size_t nm = 0, cm = 0;
	for (char *s = meta, *e; s && *s; s = e) {
		e = strchr(s, '\n');
		if (e) *e++ = 0; else e = s + strlen(s);
		struct hw_meta m;
		if (parse_meta(s, &m)) continue;
		if (nm == cm) { cm = cm ? cm * 2 : 64; struct hw_meta *x = realloc(mm, cm * sizeof *x); if (!x) break; mm = x; }
		mm[nm++] = m;
	}
	free(meta);
	if (hw_store_path(p, sizeof p, "samples.jsonl")) { free(mm); return -1; }
	char *data = slurp(p, NULL);
	struct hw_entry *es = NULL;
	size_t ne = 0, ce = 0;
	for (char *s = data, *e; s && *s; s = e) {
		e = strchr(s, '\n');
		if (e) *e++ = 0; else e = s + strlen(s);
		if (!*s) continue;
		if (ne == ce) { ce = ce ? ce * 2 : 64; struct hw_entry *x = realloc(es, ce * sizeof *x); if (!x) break; es = x; }
		struct hw_entry *x = &es[ne];
		memset(x, 0, sizeof *x);
		hw_sample_id(s, x->meta.id);
		strcpy(x->meta.client, "-");
		for (size_t i = 0; i < nm; i++) if (!strcmp(mm[i].id, x->meta.id)) { x->meta = mm[i]; break; }
		x->json = strdup(s);
		if (!x->json) break;
		/* a line twice (an import of the same sample) counts once */
		int dup = 0;
		for (size_t i = 0; i < ne; i++) if (!strcmp(es[i].meta.id, x->meta.id)) dup = 1;
		if (dup) { free(x->json); continue; }
		ne++;
	}
	free(data);
	free(mm);
	*out = es; *n = ne;
	return 0;
}

static int append(const char *rel, const char *line)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, rel)) return -1;
	int fd = open(p, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0600);
	if (fd < 0) return -1;
	fchmod(fd, 0600);
	size_t len = strlen(line), done = 0;
	while (done < len) {
		ssize_t w = write(fd, line + done, len - done);
		if (w < 0 && errno == EINTR) continue;
		if (w <= 0) { close(fd); return -1; }
		done += (size_t)w;
	}
	return close(fd);
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

static int add_locked(const char *json, const struct hw_meta *m0)
{
	struct hw_meta m = *m0;
	hw_sample_id(json, m.id);
	struct hw_entry *es;
	size_t n;
	if (hw_samples_load(&es, &n, NULL)) return -1;
	int have = 0;
	for (size_t i = 0; i < n; i++) if (!strcmp(es[i].meta.id, m.id)) have = 1;
	hw_entries_free(es, n);
	if (have) return 1;
	sanitize(m.client); sanitize(m.source); sanitize(m.lang);
	char *line = malloc(strlen(json) + 2), meta[256];
	if (!line) return -1;
	sprintf(line, "%s\n", json);
	snprintf(meta, sizeof meta, "%s\t%s\t%lld\t%s\t%s\n", m.id, m.client[0] ? m.client : "-", m.time, m.source, m.lang);
	int r = ensure_meta_head() || append("samples.jsonl", line) || append("samples.meta", meta) ? -1 : 0;
	free(line);
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

static long erase_where(const char *client, const char *id, const char *owner, int *denied);

long hw_samples_erase(const char *client)
{
	return erase_where(client, NULL, NULL, NULL);
}

int hw_samples_delete(const char *id, const char *owner)
{
	int denied = 0;
	long r = erase_where(NULL, id, owner, &denied);
	return denied ? -2 : r < 0 ? -1 : r > 0;
}

/* erases the samples of a client (NULL: all) or the one with an id (owned by owner unless
 * NULL); the adaptations go only with an erase, a deletion leaves them */
static long erase_where(const char *client, const char *id, const char *owner, int *denied)
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
		if (id ? !strcmp(es[i].meta.id, id) && (!owner || !strcmp(es[i].meta.client, owner))
		       : !client || !strcmp(es[i].meta.client, client)) { erased++; continue; }
		if (id && denied && !strcmp(es[i].meta.id, id)) *denied = 1;
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
	if (id && erased == 0) {        /* nothing to delete: nothing changes */
		free(data); free(meta);
		goto out;
	}
	if (erased >= 0) {
		/* the generation first: a running adaptation sees it and drops its result */
		if (hw_store_replace("samples.meta", meta, mlen) ||
		    hw_store_replace("samples.jsonl", len ? data : NULL, len))
			erased = -1;
		char a[4096];
		if (!id && !hw_store_path(a, sizeof a, "adaptation")) rm_tree(a);
	}
	free(data); free(meta);
out:
	hw_store_unlock(fd);
	return erased;
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

int hw_donation_start(const char *licence)
{
	if (strcmp(licence, "CC0-1.0") && strcmp(licence, "CC-BY-4.0")) return -1;
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	char l[64];
	int n = snprintf(l, sizeof l, "%s\n", licence);
	int r = hw_store_mkdir("donation") || hw_store_replace("donation/licence", l, (size_t)n) ? -1 : 0;
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
	if (line) {
		sprintf(line, "%s\n", json);
		r = append("donation/samples.jsonl", line);
		free(line);
	}
	hw_store_unlock(fd);
	return r;
}

long hw_donation_count(void)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "donation/samples.jsonl")) return 0;
	char *s = slurp(p, NULL);
	long n = 0;
	for (char *c = s; c && *c; c++) n += *c == '\n';
	free(s);
	return n;
}

int hw_donation_write(const char *path)
{
	char p[4096], lic[16];
	if (!hw_donation_licence(lic, sizeof lic) || hw_store_path(p, sizeof p, "donation/samples.jsonl")) return -1;
	char *s = slurp(p, NULL);
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
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	size_t len;
	char *s = slurp(p, &len);
	int r = 0;
	if (s) {
		char *o = malloc(len + 1);
		size_t ol = 0;
		if (!o) r = -1;
		for (char *c = s, *e; o && *c; c = e) {
			e = strchr(c, '\n');
			if (e) *e++ = 0; else e = c + strlen(c);
			char sid[17];
			hw_sample_id(c, sid);
			if (*c && !strcmp(sid, id)) { r = 1; continue; }
			size_t l = strlen(c);
			memcpy(o + ol, c, l); o[ol + l] = '\n'; ol += l + 1;
		}
		if (r == 1 && hw_store_replace("donation/samples.jsonl", o, ol)) r = -1;
		free(o);
		free(s);
	}
	hw_store_unlock(fd);
	return r;
}

int hw_donation_erase(void)
{
	char p[4096];
	if (hw_store_path(p, sizeof p, "donation")) return -1;
	struct stat st;
	if (stat(p, &st)) return 0;
	int fd = hw_store_lock();
	if (fd < 0) return -1;
	rm_tree(p);
	hw_store_unlock(fd);
	return 0;
}
