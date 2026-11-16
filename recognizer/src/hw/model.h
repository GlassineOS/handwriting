// SPDX-License-Identifier: GPL-2.0-or-later
// The base model (package format v1, docs/recognizer.md) and the engine that runs it:
// convolutions + bidirectional LSTM (the encoder), then the head (an optional residual
// adapter and the output layer) giving per-frame log-probabilities over the characters.
#ifndef HW_MODEL_H
#define HW_MODEL_H

#include <stddef.h>
#include <stdint.h>

#define HW_GHM_MAGIC "GHWM"
#define HW_GHM_VERSION 1
/* version 2: the same layout; the model reads the reference-height features
 * (hw_features_ref, HW_NFEAT_REF per row) */
#define HW_GHM_VERSION_REF 2
#define HW_MAX_CLASSES 512

struct hw_cfg {
	uint32_t nfeat, c1, conv, nstrided, kernel, hidden, layers, nclass;
};

/* The head: optional adapter 2H -> rank -> 2H (residual, ReLU) and the output layer. */
struct hw_head {
	uint32_t dim, rank, nclass;
	float *down, *bdown;    /* rank x dim, rank */
	float *up, *bup;        /* dim x rank, dim */
	float *out, *bout;      /* nclass x dim, nclass */
	float *mem;             /* one allocation holding all of the above */
	size_t nparam;
};

struct hw_model {
	struct hw_cfg cfg;
	uint32_t version;                       /* HW_GHM_VERSION or HW_GHM_VERSION_REF */
	uint32_t charset[HW_MAX_CLASSES];       /* class k+1 = charset[k]; 0 = CTC blank */
	char lang[32];
	uint64_t hash;                          /* of the whole file: ties an adaptation to it */
	int64_t trained_until;                  /* newest person's sample it was trained on (s), 0 = none */
	float *data;
	size_t ndata;
	const float *scale, *c1w, *c1b;
	const float *sw[8], *sb[8];             /* strided convolutions */
	const float *W[8][2], *R[8][2], *B[8][2];   /* per layer and direction, gates i o f c */
	struct hw_head base;                    /* the base output layer (rank 0) */
};

/* Loads a .ghm file; returns 0, or -1 with a reason in err. */
int hw_model_load(struct hw_model *m, const char *path, char *err, size_t errlen);
void hw_model_free(struct hw_model *m);
uint64_t hw_hash(const void *p, size_t n, uint64_t h);

/* Frames the encoder gives for `rows` input rows. */
long hw_frames(const struct hw_model *m, long rows);
/* Encoder: feats (rows x nfeat) -> *frames (nframes x 2H), malloc'd. Returns nframes or -1. */
long hw_encode(const struct hw_model *m, const float *feats, long rows, float **frames);

/* Heads. hw_head_init: rank 0 or more, the output layer copied from `from`, the adapter
 * at the identity. */
int hw_head_init(struct hw_head *h, const struct hw_head *from, uint32_t rank);
void hw_head_free(struct hw_head *h);
/* frames (T x dim) -> logp (T x nclass), log-softmax; adapter activations to `act`
 * (T x rank) and adapted frames to `adapted` (T x dim) when not NULL (training). */
void hw_head_forward(const struct hw_head *h, const float *frames, long T, float *logp,
		     float *act, float *adapted);

/* Characters: class of a code point (0 if none), code point of a class. */
int hw_class_of(const struct hw_model *m, uint32_t cp);

/* CTC loss of a head on encoder frames, its gradient (in the layout of h->mem) added to
 * grad times weight. INFINITY when the labels don't fit the frames. */
double hw_head_grad(const struct hw_head *h, const float *frames, long T, const int *lab, int L, float *grad,
		    float weight);

#endif
