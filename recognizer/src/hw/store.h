// SPDX-License-Identifier: GPL-2.0-or-later
// The person's store (docs/recognizer.md): samples, their client and time beside them,
// adaptations, the donation; and the settings file. Everything in
// $XDG_DATA_HOME/glassine/handwriting (directory 0700, files 0600), created by the first
// write, never by a read.
#ifndef HW_STORE_H
#define HW_STORE_H

#include <stddef.h>

#define HW_CLIENT_MAX 64

struct hw_meta {
	char id[17];                    /* 16 hex digits: a hash of the sample's line */
	char client[HW_CLIENT_MAX];
	long long time;                 /* seconds since 1970, when it was confirmed */
	char source[16];
	char lang[36];
};

struct hw_entry {
	struct hw_meta meta;
	char *json;                     /* the sample's line (sample format v1) */
};

int hw_store_path(char *out, size_t n, const char *rel);      /* rel "" = the directory */
int hw_store_adaptation_path(char *out, size_t n, const char *lang, const char *slot);
int hw_store_mkdir(const char *rel);    /* the directory (and rel under it), 0700 */
int hw_store_lock(void);                /* creates the directory; fd to pass to unlock, -1 */
void hw_store_unlock(int fd);
/* writes data to rel through rel.tmp + rename, mode 0600 (NULL data: removes rel) */
int hw_store_replace(const char *rel, const char *data, size_t len);

/* settings: $XDG_CONFIG_HOME/glassine/handwriting.conf, KEY = VALUE, last one wins */
int hw_conf_get(const char *key, char *out, size_t n);         /* 1 found, 0 not */
int hw_conf_set(const char *key, const char *value);
/* learning is on unless handwriting.conf or predict.conf (the keyboard's one setting)
 * says off */
int hw_learning_on(void);

/* samples */
void hw_sample_id(const char *json, char out[17]);
/* 0 added, 1 already there, -1 error */
int hw_samples_add(const char *json, const struct hw_meta *m);
/* all samples, in the order they were added; *gen the erase count */
int hw_samples_load(struct hw_entry **out, size_t *n, unsigned long *gen);
void hw_entries_free(struct hw_entry *e, size_t n);
/* The index of the samples (the samples.index file, kept in step with samples.jsonl): what
 * a listing needs without reading any strokes. Each entry is a sample stored once (a line
 * stored twice counts once), in the order stored; text and read (the first reading at the
 * time of writing, "" when none) are the sample's own. */
struct hw_index_entry {
	struct hw_meta meta;
	const char *text, *read;
	char written[8], pair[17];      /* the sample's marking and pair link, "" when none */
};
/* borrowed: valid until the next call into the store, not freed by the caller */
int hw_samples_index(const struct hw_index_entry **out, size_t *n);
/* one sample's line by id, malloc'd; NULL when there is none (*err 0) or on an error
 * (*err -1); m (may be NULL) gets its meta */
char *hw_samples_line(const char *id, struct hw_meta *m, int *err);
/* erase all (client NULL) or one client's; the adaptations go too. Returns the number
 * of samples erased, -1 on error. */
long hw_samples_erase(const char *client);
unsigned long hw_store_generation(void);
/* deletes one sample by id (only owner's unless NULL): 1 deleted, 0 no such sample, -2
 * it belongs to another client, -1 error. Bumps the erase count like an erase, so a
 * running adaptation drops its result; the adaptations in use stay. */
int hw_samples_delete(const char *id, const char *owner);

/* one file with the samples (and their client and time) and the adaptations; with a
 * client, only that client's samples and no adaptation */
int hw_export(const char *path, const char *client);
/* returns the number of samples added, -1 on error (reason on stderr) */
long hw_import(const char *path);

/* donation: separate from the person's samples */
int hw_donation_start(const char *licence);     /* CC0-1.0 or CC-BY-4.0 */
int hw_donation_licence(char *out, size_t n);   /* 1 started, 0 not */
int hw_donation_add(const char *json);          /* a reviewed sample (no client, no time) */
int hw_donation_write(const char *path);
int hw_donation_erase(void);                   /* the donor ID stays */
/* the donor ID (donation/donor-id): a random UUID made at the first donation start; 1 there,
 * 0 none yet. hw_donor_new makes a new one (replacing it) and gives it. */
int hw_donor_id(char *out, size_t n);
int hw_donor_new(char *out, size_t n);
int hw_donation_remove(const char *id);         /* 1 removed, 0 not there, -1 error */
int hw_donation_has(const char *id);            /* 1 there, 0 not, -1 error */
char *hw_donation_line(const char *id);
/* the ids of the samples in the donation, each once, in the order added (malloc'd) */
int hw_donation_ids(char (**out)[17], size_t *n);

/* Preferences (spec 003 FR-003): of two samples (or one character of each, by its index in
 * the text; -1 the whole sample) the one the person chose. Kept in the store's
 * `preferences`, removed with either sample, exported and donated only with both. */
struct hw_pref { char chosen[17], other[17]; int cspan, ospan; };
/* the preferences whose samples are both stored (and, with owner, both the owner's), malloc'd */
int hw_prefs(struct hw_pref **out, size_t *n, const char *owner);
/* 0 recorded, -2 a sample isn't the owner's, -3 no such sample, -4 a bad span or the same
 * sample twice, -1 an error */
int hw_prefer(const struct hw_pref *p, const char *owner);         /* a donated sample's line, malloc'd; NULL none */
long hw_donation_count(void);

#endif
