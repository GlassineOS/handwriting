/* SPDX-License-Identifier: GPL-2.0-or-later
 * libglassine-handwriting: the pen interface of GlassineOS handwriting for apps
 * (glassine_handwriting_v1, docs/pen-interface.md, spec 004).
 *
 * Use it on the app's own wl_display: its objects live on the display's default queue, so
 * their events arrive while the toolkit dispatches (GTK, Qt, a plain wl_display_dispatch
 * loop), on that thread. Call ghw_pen_flush() after requests if the toolkit doesn't flush
 * before it sleeps. */
#ifndef GLASSINE_HANDWRITING_H
#define GLASSINE_HANDWRITING_H
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct wl_display;
struct wl_surface;
struct ghw_pen;

#define GHW_VERSION 1

/* capabilities (ghw_pen_capabilities) */
enum { GHW_CAP_STROKES = 1, GHW_CAP_NEATEN = 2, GHW_CAP_TEXT = 4, GHW_CAP_LETTERS = 8 };
/* how a neat stroke was made */
enum { GHW_KEPT = 0, GHW_LINE = 1, GHW_PULLED = 2 };
/* where a candidate came from */
enum { GHW_FROM_INK = 0, GHW_FROM_BEAM = 1, GHW_FROM_WORDS = 2, GHW_FROM_GUIDED = 3 };
/* report actions and results */
enum { GHW_PICKED = 0, GHW_ACCEPTED = 1, GHW_CORRECTED = 2, GHW_DISCARDED = 3 };
enum { GHW_STORED = 0, GHW_NOT_KEPT = 1, GHW_NOT_ALLOWED = 2, GHW_STALE = 3, GHW_UNAVAILABLE = 4 };
/* field purposes (as zwp_text_input_v3) */
enum { GHW_NORMAL = 0, GHW_DIGITS = 2, GHW_URL = 5, GHW_EMAIL = 6, GHW_PASSWORD = 8, GHW_PIN = 9 };

/* one point as the compositor saw it: surface coordinates (y down), the time in ms,
 * pressure 0..1, tilt in degrees */
struct ghw_point {
	double x, y;
	uint32_t time;
	double pressure, tilt_x, tilt_y;
};

/* every member may be NULL */
struct ghw_pen_listener {
	/* a raw stroke, when the pen lifts */
	void (*stroke)(void *data, uint32_t stroke, const struct ghw_point *points, size_t n);
	/* the neatened version of raw stroke stroke (GHW_KEPT, GHW_LINE, GHW_PULLED) */
	void (*neat)(void *data, uint32_t stroke, int how, const struct ghw_point *points, size_t n);
	/* a partial reading of the strokes listed (text on) */
	void (*reading)(void *data, const char *text, const uint32_t *strokes, size_t n);
	/* candidates at a pause (text on): rank 0 best; fit 1 when a pick of it passes the
	 * check; chars per character the strokes that make it (docs/pen-interface.md) */
	void (*candidate)(void *data, uint32_t serial, uint32_t rank, const char *text, int origin, int fit,
			  const char *chars);
	void (*candidates_done)(void *data, uint32_t serial, uint32_t count);
	void (*report_done)(void *data, uint32_t serial, int result);
	/* the capabilities changed (the service started or left) */
	void (*capabilities)(void *data, uint32_t capabilities);
};

/* The pen interface for surface, or NULL when the compositor has none (an older system:
 * the app draws from the tablet events alone). Neatening and text start off. */
struct ghw_pen *ghw_pen_new(struct wl_display *display, struct wl_surface *surface,
			    const struct ghw_pen_listener *listener, void *data);
void ghw_pen_destroy(struct ghw_pen *pen);

uint32_t ghw_pen_capabilities(const struct ghw_pen *pen);
/* the languages the service reads, in its order: n of them, i-th */
size_t ghw_pen_languages(const struct ghw_pen *pen);
const char *ghw_pen_language(const struct ghw_pen *pen, size_t i);

/* where strokes count (surface coordinates); w or h <= 0: the whole surface */
void ghw_pen_set_area(struct ghw_pen *pen, int x, int y, int w, int h);
/* the area's ruled lines: the first at y first, then every spacing (0: none, found from
 * the writing); neatening goes line by line */
void ghw_pen_set_guides(struct ghw_pen *pen, double spacing, double first);
void ghw_pen_set_neaten(struct ghw_pen *pen, int on, double strength);
void ghw_pen_set_text(struct ghw_pen *pen, int on);
void ghw_pen_set_context(struct ghw_pen *pen, uint32_t purpose, uint32_t hints, const char *before,
			 const char *after, const char *languages);
/* the app is done with the writing held */
void ghw_pen_clear(struct ghw_pen *pen);
void ghw_pen_report(struct ghw_pen *pen, uint32_t serial, int action, uint32_t rank, const char *text);
/* wl_display_flush */
int ghw_pen_flush(struct ghw_pen *pen);

#ifdef __cplusplus
}
#endif
#endif
