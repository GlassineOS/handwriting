// SPDX-License-Identifier: GPL-2.0-or-later
// The person's own models (spec 006, docs/recognizer.md "Local models"): a recognizer or a
// point aligner trained on a computer, kept in $XDG_DATA_HOME/glassine/handwriting-models/LANG/
// beside the store, checked before use, chosen per kind in handwriting.conf (`recognizer`,
// `aligner`: shipped or local), the shipped file used whenever the local one can't be.
#ifndef HW_LOCAL_H
#define HW_LOCAL_H

#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>

enum { HW_KIND_RECOGNIZER = 1, HW_KIND_ALIGNER = 2 };

/* the largest local model file read (the shipped models are a few MiB) */
#define HW_LOCAL_MAX_BYTES (64L << 20)

const char *hw_kind_name(int kind);     /* "recognizer", "aligner" */
const char *hw_kind_file(int kind);     /* "recognizer.ghm", "aligner.ghm" */
int hw_kind_of(const char *name);       /* 0: neither */
struct hw_model;
/* a loaded file's kind: the one it records, else told by its shape; 0 can't be told */
int hw_model_kind(const struct hw_model *m);

/* $XDG_DATA_HOME/glassine/handwriting-models[/LANG[/FILE]]: 0, -1 too long or no home */
int hw_local_dir(char *out, size_t n, const char *lang);
int hw_local_path(char *out, size_t n, const char *lang, int kind);

/* the person's choice for a kind: 1 their own; always 0 in a writer's store (spec 002
 * FR-017) and with GLASSINE_HANDWRITING_LOCAL=off */
int hw_local_chosen(int kind);

/* The checks of spec 006 FR-006 on a file for lang. 0: usable, *kind its kind (want 0: any,
 * else that one only). -1: refused, reason one of missing, unreadable, too-big, not-a-model,
 * format-version, features, damaged, kind, kind-unknown, alphabet, language, not-finite, and a
 * detail (a version, the missing characters; "" none). */
int hw_model_check(const char *path, const char *lang, int want, int *kind, char *reason, size_t rn, char *detail,
		   size_t dn);

/* The file lang reads for a kind: the local one when chosen and usable, else the shipped one
 * (which may not exist). *local 1 when it is the local one. A local file is checked once per
 * change of its file (inode, size, time). why (may be NULL): the local refusal ("" none, or
 * not chosen). */
struct hw_resolved {
	char path[4300];
	int local;
	struct stat st;                 /* of path; st_ino 0: no such file */
	char why[32], detail[200];
};
void hw_model_resolve(const char *lang, int kind, struct hw_resolved *r);

/* 1 when two resolutions name the same file as it was */
int hw_resolved_same(const struct hw_resolved *a, const struct hw_resolved *b);

#endif
