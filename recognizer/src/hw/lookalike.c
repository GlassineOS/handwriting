// SPDX-License-Identifier: GPL-2.0-or-later
// Look-alike letters and digits (spec 001 FR-019): see lookalike.h.
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "lookalike.h"

/* each digit and the letters it looks like */
static const struct { unsigned digit; const char *letters; } GROUPS[] = {
	{ '0', "Oo" }, { '1', "Il" }, { '6', "G" },
};
#define NGROUPS (sizeof GROUPS / sizeof GROUPS[0])

int hw_lean_of_field(const char *field)
{
	if (!field || !*field || !strcmp(field, "-")) return HW_LEAN_NONE;
	size_t n = strcspn(field, ",");
	static const char *letters[] = { "normal", "alpha", "name", NULL };
	static const char *digits[] = { "digits", "number", "phone", "date", "time", "datetime", NULL };
	for (int i = 0; letters[i]; i++) if (strlen(letters[i]) == n && !strncmp(field, letters[i], n)) return HW_LEAN_LETTERS;
	for (int i = 0; digits[i]; i++) if (strlen(digits[i]) == n && !strncmp(field, digits[i], n)) return HW_LEAN_DIGITS;
	return HW_LEAN_NONE;
}

double hw_lean_keep(void)
{
	const char *e = getenv("GLASSINE_HANDWRITING_LOOKALIKE");
	if (e && *e) {
		if (!strcmp(e, "off")) return 1;
		char *end;
		double v = strtod(e, &end);
		if (!*end && v > 0 && v <= 1) return v;
	}
	return HW_LEAN_KEEP;
}

void hw_lookalike_lean(const struct hw_lang *l, struct hw_run *r, int lean, double keep)
{
	if (lean == HW_LEAN_NONE || !(keep > 0) || keep >= 1 || !r->logp || r->T <= 0) return;
	int dcl[NGROUPS], lcl[NGROUPS][4], nl[NGROUPS], any = 0;
	for (size_t g = 0; g < NGROUPS; g++) {
		dcl[g] = hw_class_of(&l->model, GROUPS[g].digit);
		nl[g] = 0;
		for (const char *p = GROUPS[g].letters; *p && nl[g] < 4; p++) {
			int c = hw_class_of(&l->model, (unsigned char)*p);
			if (c) lcl[g][nl[g]++] = c;
		}
		any |= dcl[g] && nl[g];
	}
	if (!any) return;
	size_t sz = (size_t)r->T * r->C;
	if (!r->logp0 && (r->logp0 = malloc(sz * sizeof *r->logp0))) memcpy(r->logp0, r->logp, sz * sizeof *r->logp);
	double lk = log(keep);
	for (long f = 0; f < r->T; f++) {
		float *row = r->logp + (size_t)f * r->C;
		double lost = 0;                /* the share of the frame's chance taken */
		for (size_t g = 0; g < NGROUPS; g++) {
			if (!dcl[g] || !nl[g]) continue;
			if (lean == HW_LEAN_LETTERS) {
				lost += exp(row[dcl[g]]) * (1 - keep);
				row[dcl[g]] = (float)(row[dcl[g]] + lk);
			} else
				for (int i = 0; i < nl[g]; i++) {
					lost += exp(row[lcl[g][i]]) * (1 - keep);
					row[lcl[g][i]] = (float)(row[lcl[g][i]] + lk);
				}
		}
		/* the rest of the frame keeps its proportions and the frame its total */
		double up = lost < 1 ? -log1p(-lost) : 0;
		for (int c = 0; c < r->C; c++) row[c] = (float)(row[c] + up);
	}
}
