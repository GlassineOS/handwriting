/* SPDX-License-Identifier: GPL-2.0-or-later
 * libglassine-handwriting: the pen interface for apps (glassine-handwriting.h). */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client.h>
#include "glassine-handwriting.h"
#include "glassine-handwriting-v1-client-protocol.h"

struct ghw_pen {
	struct wl_display *display;
	struct wl_compositor *compositor;
	struct glassine_handwriting_manager_v1 *mgr;
	struct glassine_handwriting_v1 *hw;
	const struct ghw_pen_listener *l;
	void *data;
	uint32_t caps, pending;
	char **langs, **next;
	size_t nlangs, nnext;
	struct ghw_point *buf;          /* the stroke being received (points events) */
	size_t n, cap;
	uint32_t k, kind;
	int bad;                        /* out of memory on the way: the stroke goes out empty */
	uint32_t mgr_version;
};

static void free_langs(char **l, size_t n)
{
	for (size_t i = 0; i < n; i++) free(l[i]);
	free(l);
}

static void m_caps(void *d, struct glassine_handwriting_manager_v1 *m, uint32_t caps)
{
	struct ghw_pen *p = d;
	p->pending = caps;
	free_langs(p->next, p->nnext);
	p->next = NULL;
	p->nnext = 0;
}

static void m_lang(void *d, struct glassine_handwriting_manager_v1 *m, const char *lang)
{
	struct ghw_pen *p = d;
	char **n = realloc(p->next, (p->nnext + 1) * sizeof *n);
	if (!n) return;
	p->next = n;
	if ((n[p->nnext] = strdup(lang))) p->nnext++;
}

static void m_done(void *d, struct glassine_handwriting_manager_v1 *m)
{
	struct ghw_pen *p = d;
	free_langs(p->langs, p->nlangs);
	p->langs = p->next;
	p->nlangs = p->nnext;
	p->next = NULL;
	p->nnext = 0;
	int changed = p->caps != p->pending;
	p->caps = p->pending;
	if (changed && p->hw && p->l && p->l->capabilities) p->l->capabilities(p->data, p->caps);
}

static const struct glassine_handwriting_manager_v1_listener mgr_listener = {
	.capabilities = m_caps, .language = m_lang, .done = m_done,
};

static void h_points(void *d, struct glassine_handwriting_v1 *h, uint32_t k, uint32_t kind, struct wl_array *a)
{
	struct ghw_pen *p = d;
	if (p->n && (k != p->k || kind != p->kind)) p->n = 0;   /* a new stroke: parts never interleave */
	p->k = k;
	p->kind = kind;
	size_t m = a->size / (6 * sizeof(int32_t));
	if (p->n + m > p->cap) {
		size_t c = p->cap ? p->cap : 256;
		while (c < p->n + m) c *= 2;
		struct ghw_point *b = realloc(p->buf, c * sizeof *b);
		if (!b) { p->bad = 1; return; }
		p->buf = b;
		p->cap = c;
	}
	const int32_t *q = a->data;
	for (size_t i = 0; i < m; i++, q += 6)
		p->buf[p->n++] = (struct ghw_point){ wl_fixed_to_double(q[0]), wl_fixed_to_double(q[1]), (uint32_t)q[2],
						     (uint32_t)q[3] / 65535.0, wl_fixed_to_double(q[4]), wl_fixed_to_double(q[5]) };
}

/* the points received for stroke k of kind, then forgotten */
static size_t take(struct ghw_pen *p, uint32_t k, uint32_t kind)
{
	size_t n = p->bad || p->k != k || p->kind != kind ? 0 : p->n;
	p->n = 0;
	p->bad = 0;
	return n;
}

static void h_stroke(void *d, struct glassine_handwriting_v1 *h, uint32_t k)
{
	struct ghw_pen *p = d;
	size_t n = take(p, k, GLASSINE_HANDWRITING_V1_KIND_RAW);
	if (p->l && p->l->stroke) p->l->stroke(p->data, k, p->buf, n);
}

static void h_neat(void *d, struct glassine_handwriting_v1 *h, uint32_t k, uint32_t how)
{
	struct ghw_pen *p = d;
	size_t n = take(p, k, GLASSINE_HANDWRITING_V1_KIND_NEAT);
	if (p->l && p->l->neat) p->l->neat(p->data, k, (int)how, p->buf, n);
}

static void h_reading(void *d, struct glassine_handwriting_v1 *h, const char *text, struct wl_array *a)
{
	struct ghw_pen *p = d;
	if (p->l && p->l->reading) p->l->reading(p->data, text, a->data, a->size / sizeof(uint32_t));
}

static void h_candidate(void *d, struct glassine_handwriting_v1 *h, uint32_t serial, uint32_t rank, const char *text,
			uint32_t origin, uint32_t fit, const char *chars)
{
	struct ghw_pen *p = d;
	if (p->l && p->l->candidate) p->l->candidate(p->data, serial, rank, text, (int)origin, (int)fit, chars);
}

static void h_cands_done(void *d, struct glassine_handwriting_v1 *h, uint32_t serial, uint32_t count)
{
	struct ghw_pen *p = d;
	if (p->l && p->l->candidates_done) p->l->candidates_done(p->data, serial, count);
}

static void h_report(void *d, struct glassine_handwriting_v1 *h, uint32_t serial, uint32_t result)
{
	struct ghw_pen *p = d;
	if (p->l && p->l->report_done) p->l->report_done(p->data, serial, (int)result);
}

static const struct glassine_handwriting_v1_listener hw_listener = {
	.points = h_points, .stroke = h_stroke, .neat_stroke = h_neat, .reading = h_reading, .candidate = h_candidate,
	.candidates_done = h_cands_done, .report_done = h_report,
};

static void reg_global(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t version)
{
	struct ghw_pen *p = d;
	if (!strcmp(iface, glassine_handwriting_manager_v1_interface.name) && !p->mgr) {
		p->mgr_version = version < 2 ? version : 2;     /* regions since 2 */
		p->mgr = wl_registry_bind(r, name, &glassine_handwriting_manager_v1_interface, p->mgr_version);
	} else if (!strcmp(iface, wl_compositor_interface.name) && !p->compositor)
		p->compositor = wl_registry_bind(r, name, &wl_compositor_interface, version < 4 ? version : 4);
}

static void reg_remove(void *d, struct wl_registry *r, uint32_t name) {}

static const struct wl_registry_listener reg_listener = { .global = reg_global, .global_remove = reg_remove };

struct ghw_pen *ghw_pen_new(struct wl_display *display, struct wl_surface *surface,
			    const struct ghw_pen_listener *listener, void *data)
{
	struct ghw_pen *p = calloc(1, sizeof *p);
	if (!p) return NULL;
	p->display = display;
	p->l = listener;
	p->data = data;
	/* the globals on a queue of our own, so the toolkit's queue isn't dispatched here */
	struct wl_event_queue *q = wl_display_create_queue(display);
	struct wl_display *wrap = q ? wl_proxy_create_wrapper(display) : NULL;
	if (!wrap) goto fail;
	wl_proxy_set_queue((struct wl_proxy *)wrap, q);
	struct wl_registry *reg = wl_display_get_registry(wrap);
	wl_proxy_wrapper_destroy(wrap);
	wl_registry_add_listener(reg, &reg_listener, p);
	wl_display_roundtrip_queue(display, q);
	wl_registry_destroy(reg);
	if (!p->mgr) goto fail;
	glassine_handwriting_manager_v1_add_listener(p->mgr, &mgr_listener, p);
	wl_display_roundtrip_queue(display, q);         /* the capabilities */
	/* from now on the toolkit's dispatch delivers the events */
	wl_proxy_set_queue((struct wl_proxy *)p->mgr, NULL);
	if (p->compositor) wl_proxy_set_queue((struct wl_proxy *)p->compositor, NULL);
	p->hw = glassine_handwriting_manager_v1_get_handwriting(p->mgr, surface);
	if (!p->hw) goto fail;
	glassine_handwriting_v1_add_listener(p->hw, &hw_listener, p);
	wl_event_queue_destroy(q);
	wl_display_flush(display);
	return p;
fail:
	if (p->mgr) glassine_handwriting_manager_v1_destroy(p->mgr);
	if (p->compositor) wl_compositor_destroy(p->compositor);
	if (q) wl_event_queue_destroy(q);
	free_langs(p->next, p->nnext);
	free_langs(p->langs, p->nlangs);
	free(p);
	return NULL;
}

void ghw_pen_destroy(struct ghw_pen *p)
{
	if (!p) return;
	if (p->hw) glassine_handwriting_v1_destroy(p->hw);
	if (p->mgr) glassine_handwriting_manager_v1_destroy(p->mgr);
	if (p->compositor) wl_compositor_destroy(p->compositor);
	wl_display_flush(p->display);
	free_langs(p->next, p->nnext);
	free_langs(p->langs, p->nlangs);
	free(p->buf);
	free(p);
}

uint32_t ghw_pen_capabilities(const struct ghw_pen *p) { return p->caps; }
size_t ghw_pen_languages(const struct ghw_pen *p) { return p->nlangs; }
const char *ghw_pen_language(const struct ghw_pen *p, size_t i) { return i < p->nlangs ? p->langs[i] : NULL; }

void ghw_pen_set_area(struct ghw_pen *p, int x, int y, int w, int h)
{
	if (w <= 0 || h <= 0 || !p->compositor) {
		glassine_handwriting_v1_set_area(p->hw, NULL);
		return;
	}
	struct wl_region *r = wl_compositor_create_region(p->compositor);
	wl_region_add(r, x, y, w, h);
	glassine_handwriting_v1_set_area(p->hw, r);
	wl_region_destroy(r);
}

void ghw_pen_set_guides(struct ghw_pen *p, double spacing, double first)
{
	glassine_handwriting_v1_set_guides(p->hw, wl_fixed_from_double(spacing > 0 ? spacing : 0), wl_fixed_from_double(first));
}

void ghw_pen_set_neaten(struct ghw_pen *p, int on, double strength)
{
	glassine_handwriting_v1_set_neaten(p->hw, on != 0, wl_fixed_from_double(strength < 0 ? 0 : strength > 1 ? 1 : strength));
}

void ghw_pen_set_text(struct ghw_pen *p, int on) { glassine_handwriting_v1_set_text(p->hw, on != 0); }

void ghw_pen_set_context(struct ghw_pen *p, uint32_t purpose, uint32_t hints, const char *before, const char *after,
			 const char *languages)
{
	glassine_handwriting_v1_set_context(p->hw, purpose, hints, before ? before : "", after ? after : "",
					    languages ? languages : "");
}

void ghw_pen_clear(struct ghw_pen *p) { glassine_handwriting_v1_clear(p->hw); }

void ghw_pen_report(struct ghw_pen *p, uint32_t serial, int action, uint32_t rank, const char *text)
{
	glassine_handwriting_v1_report(p->hw, serial, (uint32_t)action, rank, text ? text : "");
}

int ghw_pen_flush(struct ghw_pen *p) { return wl_display_flush(p->display); }

/* ---- writing regions ---- */

struct buf {
	char *d;
	size_t n, cap;
	int bad;
};

static int buf_add(struct buf *b, const void *d, size_t n)
{
	if (b->n + n + 1 > b->cap) {
		size_t c = b->cap ? b->cap : 256;
		while (c < b->n + n + 1) c *= 2;
		char *x = realloc(b->d, c);
		if (!x) { b->bad = 1; return -1; }
		b->d = x;
		b->cap = c;
	}
	memcpy(b->d + b->n, d, n);
	b->n += n;
	b->d[b->n] = 0;
	return 0;
}

static void buf_free(struct buf *b) { free(b->d); *b = (struct buf){ 0 }; }

enum { KIND_TEXT, KIND_STATE, KIND_STROKES, KIND_SHAPE, KINDS };

struct ghw_region {
	struct ghw_pen *pen;
	struct glassine_handwriting_region_v1 *r;
	const struct ghw_region_listener *l;
	void *data;
	struct buf part[KINDS];         /* data parts gathered per kind */
	struct ghw_point *pts;          /* the stroke being received */
	size_t n, cap;
	uint32_t k, kind;
	int bad;
	struct ghw_word *words;         /* the value being received */
	size_t nwords, capwords;
	struct buf gone;
};

static void r_points(void *d, struct glassine_handwriting_region_v1 *x, uint32_t k, uint32_t kind, struct wl_array *a)
{
	struct ghw_region *g = d;
	if (g->n && (k != g->k || kind != g->kind)) g->n = 0;
	g->k = k;
	g->kind = kind;
	size_t m = a->size / (6 * sizeof(int32_t));
	if (g->n + m > g->cap) {
		size_t c = g->cap ? g->cap : 256;
		while (c < g->n + m) c *= 2;
		struct ghw_point *b = realloc(g->pts, c * sizeof *b);
		if (!b) { g->bad = 1; return; }
		g->pts = b;
		g->cap = c;
	}
	const int32_t *q = a->data;
	for (size_t i = 0; i < m; i++, q += 6)
		g->pts[g->n++] = (struct ghw_point){ wl_fixed_to_double(q[0]), wl_fixed_to_double(q[1]), (uint32_t)q[2],
						     (uint32_t)q[3] / 65535.0, wl_fixed_to_double(q[4]), wl_fixed_to_double(q[5]) };
}

static size_t r_take(struct ghw_region *g, uint32_t k, uint32_t kind)
{
	size_t n = g->bad || g->k != k || g->kind != kind ? 0 : g->n;
	g->n = 0;
	g->bad = 0;
	return n;
}

static void r_stroke(void *d, struct glassine_handwriting_region_v1 *x, uint32_t k)
{
	struct ghw_region *g = d;
	size_t n = r_take(g, k, GLASSINE_HANDWRITING_V1_KIND_RAW);
	if (g->l && g->l->stroke) g->l->stroke(g->data, g, k, g->pts, n);
}

static void r_neat(void *d, struct glassine_handwriting_region_v1 *x, uint32_t k, uint32_t how)
{
	struct ghw_region *g = d;
	size_t n = r_take(g, k, GLASSINE_HANDWRITING_V1_KIND_NEAT);
	if (g->l && g->l->neat) g->l->neat(g->data, g, k, (int)how, g->pts, n);
}

static void r_data(void *d, struct glassine_handwriting_region_v1 *x, uint32_t kind, struct wl_array *a)
{
	struct ghw_region *g = d;
	if (kind < KINDS) buf_add(&g->part[kind], a->data, a->size);
}

/* the gathered part of kind, taken: its bytes stay valid until the next take */
static struct buf take_part(struct ghw_region *g, int kind)
{
	struct buf b = g->part[kind];
	g->part[kind] = (struct buf){ 0 };
	return b;
}

static void free_words(struct ghw_region *g)
{
	for (size_t i = 0; i < g->nwords; i++) {
		free((void *)g->words[i].strokes);
		free((void *)g->words[i].path);
	}
	g->nwords = 0;
}

static struct ghw_word *new_word(struct ghw_region *g)
{
	if (g->nwords == g->capwords) {
		size_t c = g->capwords ? 2 * g->capwords : 16;
		struct ghw_word *w = realloc(g->words, c * sizeof *w);
		if (!w) return NULL;
		g->words = w;
		g->capwords = c;
	}
	struct ghw_word *w = &g->words[g->nwords++];
	*w = (struct ghw_word){ 0 };
	return w;
}

static void r_word(void *d, struct glassine_handwriting_region_v1 *x, uint32_t serial, uint32_t start, uint32_t length,
		   wl_fixed_t dx, wl_fixed_t dy)
{
	struct ghw_region *g = d;
	struct buf b = take_part(g, KIND_STROKES);
	struct ghw_word *w = new_word(g);
	if (!w) { buf_free(&b); return; }
	w->start = start;
	w->length = length;
	w->dx = wl_fixed_to_double(dx);
	w->dy = wl_fixed_to_double(dy);
	w->strokes = (const uint32_t *)b.d;     /* the word owns the bytes now */
	w->nstrokes = b.n / sizeof(uint32_t);
}

static void r_set_word(void *d, struct glassine_handwriting_region_v1 *x, uint32_t serial, uint32_t start,
		       uint32_t length, wl_fixed_t bx, wl_fixed_t by, wl_fixed_t bw, wl_fixed_t bh)
{
	struct ghw_region *g = d;
	struct buf b = take_part(g, KIND_SHAPE);
	struct ghw_word *w = new_word(g);
	if (!w) { buf_free(&b); return; }
	size_t n = b.n / sizeof(int32_t);
	double *path = n ? malloc(n * sizeof *path) : NULL;
	if (n && !path) { g->nwords--; buf_free(&b); return; }
	/* op codes stay codes; coordinates are wl_fixed */
	static const int ncoord[] = { 2, 2, 4, 6, 0 };
	const int32_t *q = (const int32_t *)b.d;
	size_t i = 0;
	while (i < n) {
		int32_t op = q[i];
		if (op < 0 || op > 4 || i + 1 + (size_t)ncoord[op] > n) break;
		path[i] = op;
		for (int j = 1; j <= ncoord[op]; j++) path[i + (size_t)j] = wl_fixed_to_double(q[i + (size_t)j]);
		i += 1 + (size_t)ncoord[op];
	}
	buf_free(&b);
	w->start = start;
	w->length = length;
	w->set = 1;
	w->x = wl_fixed_to_double(bx);
	w->y = wl_fixed_to_double(by);
	w->width = wl_fixed_to_double(bw);
	w->height = wl_fixed_to_double(bh);
	w->path = path;
	w->npath = i;
}

static void r_gone(void *d, struct glassine_handwriting_region_v1 *x, uint32_t serial)
{
	struct ghw_region *g = d;
	struct buf b = take_part(g, KIND_STROKES);
	buf_add(&g->gone, b.d ? b.d : "", b.n);
	buf_free(&b);
}

static void r_value(void *d, struct glassine_handwriting_region_v1 *x, uint32_t serial)
{
	struct ghw_region *g = d;
	struct buf t = take_part(g, KIND_TEXT), st = take_part(g, KIND_STATE);
	if (g->l && g->l->value)
		g->l->value(g->data, g, serial, t.d ? t.d : "", g->words, g->nwords, (const uint32_t *)g->gone.d,
			    g->gone.n / sizeof(uint32_t), st.d, st.n);
	buf_free(&t);
	buf_free(&st);
	free_words(g);
	g->gone.n = 0;
}

static void r_candidate(void *d, struct glassine_handwriting_region_v1 *x, uint32_t serial, uint32_t rank,
			uint32_t origin, uint32_t fit)
{
	struct ghw_region *g = d;
	struct buf t = take_part(g, KIND_TEXT);
	if (g->l && g->l->candidate) g->l->candidate(g->data, g, serial, rank, t.d ? t.d : "", (int)origin, (int)fit);
	buf_free(&t);
}

static void r_cands_done(void *d, struct glassine_handwriting_region_v1 *x, uint32_t serial, uint32_t count)
{
	struct ghw_region *g = d;
	if (g->l && g->l->candidates_done) g->l->candidates_done(g->data, g, serial, count);
}

static void r_gesture(void *d, struct glassine_handwriting_region_v1 *x, uint32_t serial, uint32_t kind)
{
	struct ghw_region *g = d;
	struct buf b = take_part(g, KIND_STROKES);
	if (g->l && g->l->gesture)
		g->l->gesture(g->data, g, serial, (int)kind, (const uint32_t *)b.d, b.n / sizeof(uint32_t));
	buf_free(&b);
}

static void r_report(void *d, struct glassine_handwriting_region_v1 *x, uint32_t serial, uint32_t result)
{
	struct ghw_region *g = d;
	if (g->l && g->l->report_done) g->l->report_done(g->data, g, serial, (int)result);
}

static const struct glassine_handwriting_region_v1_listener region_listener = {
	.points = r_points, .stroke = r_stroke, .neat_stroke = r_neat, .data_part = r_data, .word = r_word,
	.set_word = r_set_word, .gone = r_gone, .value = r_value, .candidate = r_candidate,
	.candidates_done = r_cands_done, .gesture = r_gesture, .report_done = r_report,
};

struct ghw_region *ghw_region_new(struct ghw_pen *p, const char *name, const struct ghw_region_listener *listener,
				  void *data)
{
	if (!p || !p->hw || p->mgr_version < 2 || wl_proxy_get_version((struct wl_proxy *)p->hw) < 2) return NULL;
	struct ghw_region *g = calloc(1, sizeof *g);
	if (!g) return NULL;
	g->pen = p;
	g->l = listener;
	g->data = data;
	g->r = glassine_handwriting_v1_get_region(p->hw, name ? name : "");
	if (!g->r) { free(g); return NULL; }
	glassine_handwriting_region_v1_add_listener(g->r, &region_listener, g);
	return g;
}

void ghw_region_destroy(struct ghw_region *g)
{
	if (!g) return;
	glassine_handwriting_region_v1_destroy(g->r);
	for (int i = 0; i < KINDS; i++) buf_free(&g->part[i]);
	free_words(g);
	free(g->words);
	buf_free(&g->gone);
	free(g->pts);
	free(g);
}

void ghw_region_set_rect(struct ghw_region *g, double x, double y, double w, double h)
{
	glassine_handwriting_region_v1_set_rect(g->r, wl_fixed_from_double(x), wl_fixed_from_double(y),
						wl_fixed_from_double(w > 0 ? w : 0), wl_fixed_from_double(h > 0 ? h : 0));
}

void ghw_region_set_clip(struct ghw_region *g, int x, int y, int w, int h)
{
	if (w <= 0 || h <= 0 || !g->pen->compositor) {
		glassine_handwriting_region_v1_set_clip(g->r, NULL);
		return;
	}
	struct wl_region *r = wl_compositor_create_region(g->pen->compositor);
	wl_region_add(r, x, y, w, h);
	glassine_handwriting_region_v1_set_clip(g->r, r);
	wl_region_destroy(r);
}

void ghw_region_set_guides(struct ghw_region *g, double spacing, double first)
{
	glassine_handwriting_region_v1_set_guides(g->r, wl_fixed_from_double(spacing > 0 ? spacing : 0),
						  wl_fixed_from_double(first));
}

void ghw_region_set_context(struct ghw_region *g, uint32_t purpose, uint32_t hints, const char *languages)
{
	glassine_handwriting_region_v1_set_context(g->r, purpose, hints, languages ? languages : "");
}

void ghw_region_set_reading(struct ghw_region *g, int on) { glassine_handwriting_region_v1_set_reading(g->r, on != 0); }

void ghw_region_set_neaten(struct ghw_region *g, int on, double strength)
{
	glassine_handwriting_region_v1_set_neaten(g->r, on != 0,
						  wl_fixed_from_double(strength < 0 ? 0 : strength > 1 ? 1 : strength));
}

void ghw_region_set_layer(struct ghw_region *g, int layer) { glassine_handwriting_region_v1_set_layer(g->r, layer); }

void ghw_region_set_mode(struct ghw_region *g, int mode) { glassine_handwriting_region_v1_set_mode(g->r, (uint32_t)mode); }

void ghw_pen_set_mode(struct ghw_pen *p, int mode)
{
	if (p->hw && wl_proxy_get_version((struct wl_proxy *)p->hw) >= 2)
		glassine_handwriting_v1_set_mode(p->hw, (uint32_t)mode);
}

/* bytes as data parts of kind, at most 2048 a part */
static void add_data(struct ghw_region *g, uint32_t kind, const void *d, size_t n)
{
	const char *c = d;
	for (size_t i = 0; i < n; i += 2048) {
		struct wl_array a = { .size = n - i < 2048 ? n - i : 2048, .alloc = 0, .data = (void *)(c + i) };
		glassine_handwriting_region_v1_add_data(g->r, kind, &a);
	}
}

void ghw_region_edit(struct ghw_region *g, const char *text)
{
	if (text) add_data(g, GLASSINE_HANDWRITING_REGION_V1_DATA_KIND_TEXT, text, strlen(text));
	glassine_handwriting_region_v1_edit(g->r);
}

void ghw_region_report(struct ghw_region *g, uint32_t serial, int action, uint32_t rank, const char *text)
{
	if (text && action == GHW_CORRECTED)
		add_data(g, GLASSINE_HANDWRITING_REGION_V1_DATA_KIND_TEXT, text, strlen(text));
	glassine_handwriting_region_v1_report(g->r, serial, (uint32_t)action, rank);
}

static int32_t pressure_of(double p) { return (int32_t)lround((p < 0 ? 0 : p > 1 ? 1 : p) * 65535); }

void ghw_region_restore(struct ghw_region *g, const struct ghw_stored_stroke *st, size_t n, const char *value,
			const void *state, size_t state_len)
{
	int32_t q[128 * 6];
	for (size_t i = 0; i < n; i++) {
		for (size_t j = 0; j < st[i].n; j += 128) {
			size_t m = st[i].n - j < 128 ? st[i].n - j : 128;
			for (size_t k = 0; k < m; k++) {
				const struct ghw_point *pt = &st[i].points[j + k];
				int32_t *o = q + 6 * k;
				o[0] = wl_fixed_from_double(pt->x);
				o[1] = wl_fixed_from_double(pt->y);
				o[2] = (int32_t)pt->time;
				o[3] = pressure_of(pt->pressure);
				o[4] = wl_fixed_from_double(pt->tilt_x);
				o[5] = wl_fixed_from_double(pt->tilt_y);
			}
			struct wl_array a = { .size = m * 6 * sizeof(int32_t), .alloc = 0, .data = q };
			glassine_handwriting_region_v1_restore_points(g->r, &a);
		}
		glassine_handwriting_region_v1_restore_stroke(g->r, st[i].stroke);
	}
	if (value) add_data(g, GLASSINE_HANDWRITING_REGION_V1_DATA_KIND_TEXT, value, strlen(value));
	if (state && state_len) add_data(g, GLASSINE_HANDWRITING_REGION_V1_DATA_KIND_STATE, state, state_len);
	glassine_handwriting_region_v1_restore(g->r);
}
