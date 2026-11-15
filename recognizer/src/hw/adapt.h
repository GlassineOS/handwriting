// SPDX-License-Identifier: GPL-2.0-or-later
// The person's adaptation (spec FR-009): a small head trained on their samples on top of
// the frozen base encoder; checked on held-back samples and kept only when it does better.
#ifndef HW_ADAPT_H
#define HW_ADAPT_H

#include <stdint.h>
#include "model.h"

#define HW_GHA_MAGIC "GHWA"
#define HW_GHA_VERSION 1

struct hw_adapt_info {
	uint64_t base;          /* hash of the base model it was trained on */
	uint32_t rank, ntrain, nheld;
	float cer, cer_before;  /* on the held-back samples: this head, the one it replaced */
	int64_t time;
};

/* 0 ok; -1 unreadable, or made for another base model */
int hw_adapt_load(const char *path, const struct hw_model *m, struct hw_head *h, struct hw_adapt_info *info);
int hw_adapt_info(const char *path, struct hw_adapt_info *info);
/* the file's bytes, malloc'd */
char *hw_adapt_serialize(const struct hw_head *h, const struct hw_adapt_info *info, size_t *len);

struct hw_adapt_opts {
	const char *lang;
	int force;              /* skip the idle and charging checks, and run even when nothing changed */
	int min_train, min_held;
	int epochs, rank, batch;
	float lr;
	int verbose;
	int requested;          /* the person asked in a client: charging is enough, idle isn't needed */
	int progress;           /* tab-separated progress lines on stdout (docs/recognizer.md) */
};

/* What a run would use, without running: samples of the language to learn from and held
 * back, and how many came since the last run. 0, or -1 when the store can't be read. */
int hw_adapt_plan(const char *lang, long *train, long *held, long *fresh);

/* Runs one adaptation for opts->lang. Returns 1 kept (better), 0 not kept, 2 nothing to
 * do (too few samples, nothing new, not idle or not charging), -1 error. A line saying
 * what happened goes to stdout when verbose. */
int hw_adapt_run(const struct hw_adapt_opts *o);

/* Whether the device is charging (a mains or USB supply online, or a battery charging or
 * full; no battery at all counts as mains). $GLASSINE_HANDWRITING_SYSFS replaces
 * /sys/class/power_supply (tests). */
int hw_charging(void);
/* Whether the system is idle enough (1-minute load average below 1). */
int hw_idle(void);

#endif
