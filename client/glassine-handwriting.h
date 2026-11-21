/* SPDX-License-Identifier: GPL-2.0-or-later
 * libglassine-handwriting: the pen interface of GlassineOS handwriting for apps
 * (glassine_handwriting_v1, docs/pen-interface.md, spec 004), with writing regions
 * (spec 005: ghw_region).
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
struct ghw_region;

#define GHW_VERSION 2

/* capabilities (ghw_pen_capabilities); GHW_CAP_REGIONS: regions are read (values,
 * candidates, gestures, word layout); without it regions still pass on raw strokes */
enum { GHW_CAP_STROKES = 1, GHW_CAP_NEATEN = 2, GHW_CAP_TEXT = 4, GHW_CAP_LETTERS = 8, GHW_CAP_REGIONS = 16 };
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

/* ---- writing regions (spec 005; docs/pen-interface.md, Writing regions) ---- */

/* region modes, gestures */
enum { GHW_MODE_INK = 0, GHW_MODE_TEXT = 1 };
enum { GHW_STRIKE = 0, GHW_SCRATCH = 1, GHW_CARET = 2 };
/* path ops of a set word's outline (ghw_word.path) */
enum { GHW_PATH_MOVE = 0, GHW_PATH_LINE = 1, GHW_PATH_QUAD = 2, GHW_PATH_CUBIC = 3, GHW_PATH_CLOSE = 4 };

/* a word of a region's value: the bytes start to start + length of the value's text.
 * An ink word (set 0) is its strokes, drawn moved by dx, dy. A set word (set 1) has no ink:
 * draw its outline, path (npath numbers: each op code followed by its coordinates, 2 for
 * move and line, 4 for quad, 6 for cubic, none for close), filled with the non-zero rule;
 * x, y, width, height is its box. Region coordinates throughout. */
struct ghw_word {
	uint32_t start, length;
	int set;
	const uint32_t *strokes;
	size_t nstrokes;
	double dx, dy;
	double x, y, width, height;
	const double *path;
	size_t npath;
};

/* a stroke the app kept, handed back with ghw_region_restore */
struct ghw_stored_stroke {
	uint32_t stroke;
	const struct ghw_point *points;
	size_t n;
};

/* every member may be NULL; region is the region the event is for */
struct ghw_region_listener {
	/* a raw stroke when the pen lifts, region coordinates; stroke numbers are the region's */
	void (*stroke)(void *data, struct ghw_region *region, uint32_t stroke, const struct ghw_point *points, size_t n);
	void (*neat)(void *data, struct ghw_region *region, uint32_t stroke, int how, const struct ghw_point *points,
		     size_t n);
	/* the region's value, whole, with its layout: words in the text's order, the strokes
	 * that left the region for good (gone), and the saved state (opaque bytes: keep them with
	 * the strokes and the value, hand them back to ghw_region_restore) */
	void (*value)(void *data, struct ghw_region *region, uint32_t serial, const char *text,
		      const struct ghw_word *words, size_t nwords, const uint32_t *gone, size_t ngone,
		      const void *state, size_t state_len);
	/* ranked alternatives for the value serial, at a pause; a pick becomes the value */
	void (*candidate)(void *data, struct ghw_region *region, uint32_t serial, uint32_t rank, const char *text,
			  int origin, int fit);
	void (*candidates_done)(void *data, struct ghw_region *region, uint32_t serial, uint32_t count);
	/* a gesture written in the region (its strokes); its effect comes with the value serial */
	void (*gesture)(void *data, struct ghw_region *region, uint32_t serial, int kind, const uint32_t *strokes,
			size_t n);
	void (*report_done)(void *data, struct ghw_region *region, uint32_t serial, int result);
};

/* A new writing region on the pen's surface, named by the app (unique among its live
 * regions), or NULL when the compositor has no regions. It starts with an empty rectangle
 * (no strokes until ghw_region_set_rect), reading on, neatening off. */
struct ghw_region *ghw_region_new(struct ghw_pen *pen, const char *name, const struct ghw_region_listener *listener,
				  void *data);
void ghw_region_destroy(struct ghw_region *region);
/* where it lies on the surface (surface coordinates); a move reads nothing again */
void ghw_region_set_rect(struct ghw_region *region, double x, double y, double width, double height);
/* the visible part, in surface coordinates; w or h <= 0: the whole rectangle */
void ghw_region_set_clip(struct ghw_region *region, int x, int y, int w, int h);
/* ruled lines in region coordinates (spacing 0: none) */
void ghw_region_set_guides(struct ghw_region *region, double spacing, double first);
void ghw_region_set_context(struct ghw_region *region, uint32_t purpose, uint32_t hints, const char *languages);
void ghw_region_set_reading(struct ghw_region *region, int on);
void ghw_region_set_neaten(struct ghw_region *region, int on, double strength);
void ghw_region_set_layer(struct ghw_region *region, int layer);
void ghw_region_set_mode(struct ghw_region *region, int mode);
/* every region of the surface, and those made later */
void ghw_pen_set_mode(struct ghw_pen *pen, int mode);
/* the person's keyboard text for the region (send it before going back to ink mode) */
void ghw_region_edit(struct ghw_region *region, const char *text);
/* GHW_ACCEPTED or GHW_CORRECTED (text) on the value serial, GHW_PICKED rank on its
 * candidates; GHW_DISCARDED is answered not_kept */
void ghw_region_report(struct ghw_region *region, uint32_t serial, int action, uint32_t rank, const char *text);
/* stored ink: replaces what the region holds; value and state as the app kept them (NULL
 * and 0: strokes alone, read anew) */
void ghw_region_restore(struct ghw_region *region, const struct ghw_stored_stroke *strokes, size_t n,
			const char *value, const void *state, size_t state_len);

#ifdef __cplusplus
}
#endif
#endif
