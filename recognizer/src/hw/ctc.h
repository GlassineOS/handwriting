// SPDX-License-Identifier: GPL-2.0-or-later
// CTC over per-frame log-probabilities (class 0 = blank): greedy and prefix beam
// decoding, forced alignment of a known labelling, and the loss with its gradient.
#ifndef HW_CTC_H
#define HW_CTC_H

#include <stddef.h>

#define HW_MAX_LABELS 512

struct hw_cand {
	int n;
	int lab[HW_MAX_LABELS];
	double score;           /* log-probability of the labelling (summed over its paths) */
};

/* best path: returns the number of labels written to lab (at most max) */
int hw_ctc_greedy(const float *logp, long T, int C, int *lab, int max);

/* prefix beam search: up to n candidates, best first; returns how many */
int hw_ctc_beam(const float *logp, long T, int C, int beam, struct hw_cand *out, int n);

/* the same, the prefixes ranked by their probability plus a language score: step() gives
 * what label adds to the prefix `parent` making the new prefix `node` (node ids count up
 * from 1; 0 is the empty prefix), once per prefix; end() what a prefix adds when the
 * reading ends there; -INFINITY drops it. The candidates' scores stay the frames' alone. */
struct hw_ctc_lm {
	void *ctx;
	double (*step)(void *ctx, int parent, int node, int label);
	double (*end)(void *ctx, int node);
};
int hw_ctc_beam_lm(const float *logp, long T, int C, int beam, struct hw_cand *out, int n, const struct hw_ctc_lm *lm);

/* Viterbi alignment of lab[0..L) to the frames: for each label, the first and last frame
 * of its own (non-blank) state, written to first/last, and the frame range it owns
 * (from[k]..to[k], blanks split between neighbours at the midpoint). Returns the path's
 * log-probability, or -INFINITY when the frames are too few. */
double hw_ctc_align(const float *logp, long T, int C, const int *lab, int L,
		    long *first, long *last, long *from, long *to);

/* Negative log-likelihood of lab under logp, and when grad is not NULL its gradient with
 * respect to the logits before the log-softmax (T x C), added to grad times `weight`.
 * Returns INFINITY when lab can't be aligned (nothing added). */
double hw_ctc_loss(const float *logp, long T, int C, const int *lab, int L, float *grad, float weight);

#endif
