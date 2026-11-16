/* SPDX-License-Identifier: GPL-2.0-or-later
 * libglassine-handwriting: the pen interface for apps (glassine-handwriting.h). */
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
	if (!strcmp(iface, glassine_handwriting_manager_v1_interface.name) && !p->mgr)
		p->mgr = wl_registry_bind(r, name, &glassine_handwriting_manager_v1_interface, 1);
	else if (!strcmp(iface, wl_compositor_interface.name) && !p->compositor)
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
