// SPDX-License-Identifier: GPL-2.0-or-later
// Look-alike letters and digits (spec 001 FR-019): see lookalike.h.
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "lookalike.h"
#include "store.h"

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
	/* the pairs: the shapes everyone shares, then the person's own (store.h) */
	unsigned pairs[16 + 64][2];
	int np = 0;
	for (size_t g = 0; g < NGROUPS; g++)
		for (const char *p = GROUPS[g].letters; *p; p++) { pairs[np][0] = GROUPS[g].digit; pairs[np][1] = (unsigned char)*p; np++; }
	np += hw_lookalikes(pairs + np, 64);
	/* the classes the field disfavours: the digit of each pair in a field for text, its letter
	 * in one for numbers; both sides must be in the model */
	unsigned char *off = calloc((size_t)r->C, 1);
	int any = 0;
	if (!off) return;
	for (int i = 0; i < np; i++) {
		int d = hw_class_of(&l->model, pairs[i][0]), c = hw_class_of(&l->model, pairs[i][1]);
		if (!d || !c) continue;
		off[lean == HW_LEAN_LETTERS ? d : c] = 1;
		any = 1;
	}
	if (!any) { free(off); return; }
	size_t sz = (size_t)r->T * r->C;
	if (!r->logp0 && (r->logp0 = malloc(sz * sizeof *r->logp0))) memcpy(r->logp0, r->logp, sz * sizeof *r->logp);
	double lk = log(keep);
	/* the ink's frames (for fits) and the calibrated ones readings are made on alike */
	for (long f = 0; f < 2 * r->T; f++) {
		if (f >= r->T && !r->dec) break;
		float *row = f < r->T ? r->logp + (size_t)f * r->C : r->dec + (size_t)(f - r->T) * r->C;
		double lost = 0;                /* the share of the frame's chance taken */
		for (int c = 1; c < r->C; c++)
			if (off[c]) {
				lost += exp(row[c]) * (1 - keep);
				row[c] = (float)(row[c] + lk);
			}
		/* the rest of the frame keeps its proportions and the frame its total */
		double up = lost < 1 ? -log1p(-lost) : 0;
		for (int c = 0; c < r->C; c++) row[c] = (float)(row[c] + up);
	}
	free(off);
}
