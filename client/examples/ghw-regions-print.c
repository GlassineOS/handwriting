/* SPDX-License-Identifier: GPL-2.0-or-later
 * ghw-regions-print: writing regions with libglassine-handwriting (spec 005). It makes one
 * surface and the regions stdin asks for, keeps each region's strokes, value and saved
 * state as an app keeps them in its document, and prints every event as a line. The host
 * tests drive it; it also shows the calls an app makes.
 *
 * stdin, ID a small number naming a region:
 *   "region ID NAME", "rect ID X Y W H", "clip ID X Y W H", "guides ID SPACING FIRST",
 *   "context ID PURPOSE HINTS", "reading ID ON", "neaten ID ON STRENGTH", "layer ID L",
 *   "mode ID MODE", "display ID DISPLAY", "allmode MODE", "edit ID TEXT", "report ID SERIAL ACTION RANK [TEXT]",
 *   "restore ID FROM" (region ID gets what the app kept of region FROM: its strokes less
 *   those gone, its value and state), "strokes-only ID FROM" (the strokes alone),
 *   "destroy ID", "sync" (answers "synced").
 * Output ("\n" in a text printed as \n):
 *   "caps C", "stroke ID K N X,Y,T,P,TX,TY …", "neat ID K HOW N X,Y …",
 *   "word ID START LENGTH ink K,K… DX DY", "word ID START LENGTH set X Y W H NPATH" or (an
 *   ink word shown set) "word ID START LENGTH shown K,K… X Y W H NPATH" for each word, "gone ID K,K…", then "value ID SERIAL STATE_BYTES TEXT";
 *   "candidate ID SERIAL RANK ORIGIN FIT TEXT", "done ID SERIAL COUNT",
 *   "gesture ID SERIAL KIND K,K…", "report ID SERIAL RESULT". */
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wayland-client.h>
#include "glassine-handwriting.h"

#define MAX 32

/* what the app keeps of a region: its strokes, its value and saved state */
struct kept {
	struct ghw_stored_stroke *st;
	size_t n;
	char *value;
	void *state;
	size_t state_len;
};

static struct ghw_region *regions[MAX];
static struct kept kept[MAX];
static struct wl_compositor *compositor;

static int id_of(struct ghw_region *r)
{
	for (int i = 0; i < MAX; i++)
		if (regions[i] == r) return i;
	return -1;
}

static void print_text(const char *t)
{
	for (; *t; t++) {
		if (*t == '\n') fputs("\\n", stdout);
		else putchar(*t);
	}
}

static void out_points(const struct ghw_point *p, size_t n, int all)
{
	for (size_t i = 0; i < n; i++) {
		if (all) printf(" %.17g,%.17g,%u,%.17g,%.17g,%.17g", p[i].x, p[i].y, p[i].time, p[i].pressure, p[i].tilt_x,
				p[i].tilt_y);
		else printf(" %.17g,%.17g", p[i].x, p[i].y);
	}
	printf("\n");
}

static void out_list(const uint32_t *k, size_t n)
{
	printf(" ");
	for (size_t i = 0; i < n; i++) printf("%s%u", i ? "," : "", k[i]);
	if (!n) printf("-");
}

static void forget_stroke(struct kept *a, uint32_t k)
{
	for (size_t i = 0; i < a->n; i++) {
		if (a->st[i].stroke != k) continue;
		free((void *)a->st[i].points);
		a->st[i] = a->st[--a->n];
		return;
	}
}

static void keep_stroke(struct kept *a, uint32_t k, const struct ghw_point *p, size_t n)
{
	forget_stroke(a, k);
	struct ghw_stored_stroke *s = realloc(a->st, (a->n + 1) * sizeof *s);
	struct ghw_point *c = malloc((n ? n : 1) * sizeof *c);
	if (!s || !c) { free(c); if (s) a->st = s; return; }
	a->st = s;
	memcpy(c, p, n * sizeof *c);
	a->st[a->n++] = (struct ghw_stored_stroke){ k, c, n };
}

static void on_stroke(void *d, struct ghw_region *r, uint32_t k, const struct ghw_point *p, size_t n)
{
	int id = id_of(r);
	keep_stroke(&kept[id], k, p, n);
	printf("stroke %d %u %zu", id, k, n);
	out_points(p, n, 1);
}

static void on_neat(void *d, struct ghw_region *r, uint32_t k, int how, const struct ghw_point *p, size_t n)
{
	printf("neat %d %u %d %zu", id_of(r), k, how, n);
	out_points(p, n, 0);
}

static void on_value(void *d, struct ghw_region *r, uint32_t serial, const char *text, const struct ghw_word *w,
		     size_t nw, const uint32_t *gone, size_t ngone, const void *state, size_t state_len)
{
	int id = id_of(r);
	for (size_t i = 0; i < nw; i++) {
		printf("word %d %u %u", id, w[i].start, w[i].length);
		if (w[i].set && w[i].nstrokes) {
			printf(" shown");
			out_list(w[i].strokes, w[i].nstrokes);
			printf(" %.6g %.6g %.6g %.6g %zu\n", w[i].x, w[i].y, w[i].width, w[i].height, w[i].npath);
		} else if (w[i].set) {
			printf(" set %.6g %.6g %.6g %.6g %zu\n", w[i].x, w[i].y, w[i].width, w[i].height, w[i].npath);
		} else {
			printf(" ink");
			out_list(w[i].strokes, w[i].nstrokes);
			printf(" %.6g %.6g\n", w[i].dx, w[i].dy);
		}
	}
	if (ngone) {
		printf("gone %d", id);
		out_list(gone, ngone);
		printf("\n");
	}
	/* the app keeps the value and state with the strokes; gone strokes are no longer its */
	struct kept *a = &kept[id];
	for (size_t i = 0; i < ngone; i++) forget_stroke(a, gone[i]);
	free(a->value);
	free(a->state);
	a->value = strdup(text);
	a->state = malloc(state_len ? state_len : 1);
	if (a->state) memcpy(a->state, state, state_len);
	a->state_len = a->state ? state_len : 0;
	printf("value %d %u %zu ", id, serial, state_len);
	print_text(text);
	printf("\n");
}

static void on_candidate(void *d, struct ghw_region *r, uint32_t serial, uint32_t rank, const char *text, int origin,
			 int fit)
{
	printf("candidate %d %u %u %d %d ", id_of(r), serial, rank, origin, fit);
	print_text(text);
	printf("\n");
}

static void on_done(void *d, struct ghw_region *r, uint32_t serial, uint32_t count)
{
	printf("done %d %u %u\n", id_of(r), serial, count);
}

static void on_gesture(void *d, struct ghw_region *r, uint32_t serial, int kind, const uint32_t *k, size_t n)
{
	printf("gesture %d %u %d", id_of(r), serial, kind);
	out_list(k, n);
	printf("\n");
}

static void on_report(void *d, struct ghw_region *r, uint32_t serial, int result)
{
	printf("report %d %u %d\n", id_of(r), serial, result);
}

static const struct ghw_region_listener listener = {
	.stroke = on_stroke, .neat = on_neat, .value = on_value, .candidate = on_candidate,
	.candidates_done = on_done, .gesture = on_gesture, .report_done = on_report,
};

static void global(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t v)
{
	if (!strcmp(iface, "wl_compositor")) compositor = wl_registry_bind(r, name, &wl_compositor_interface, 4);
}

static void global_remove(void *d, struct wl_registry *r, uint32_t name) {}
static const struct wl_registry_listener reg = { global, global_remove };

static void unescape(char *t)
{
	char *o = t;
	for (; *t; t++) {
		if (t[0] == '\\' && t[1] == 'n') { *o++ = '\n'; t++; }
		else *o++ = *t;
	}
	*o = 0;
}

static void command(struct ghw_pen *pen, struct wl_display *dpy, char *c)
{
	int id, from, on, layer, mode;
	unsigned serial, action, rank, purpose, hints;
	double x, y, w, h, st;
	char text[4096] = "";
	if (sscanf(c, "region %d %255s", &id, text) == 2 && id >= 0 && id < MAX && !regions[id]) {
		regions[id] = ghw_region_new(pen, text, &listener, NULL);
		if (!regions[id]) printf("no-regions\n");
		return;
	}
	if (sscanf(c, "allmode %d", &mode) == 1) { ghw_pen_set_mode(pen, mode); return; }
	if (!strcmp(c, "sync")) { wl_display_roundtrip(dpy); printf("synced\n"); return; }
	if (sscanf(c, "%*s %d", &id) != 1 || id < 0 || id >= MAX || !regions[id]) return;
	struct ghw_region *r = regions[id];
	if (sscanf(c, "rect %*d %lf %lf %lf %lf", &x, &y, &w, &h) == 4) ghw_region_set_rect(r, x, y, w, h);
	else if (sscanf(c, "clip %*d %lf %lf %lf %lf", &x, &y, &w, &h) == 4)
		ghw_region_set_clip(r, (int)x, (int)y, (int)w, (int)h);
	else if (sscanf(c, "guides %*d %lf %lf", &x, &y) == 2) ghw_region_set_guides(r, x, y);
	else if (sscanf(c, "context %*d %u %u", &purpose, &hints) == 2) ghw_region_set_context(r, purpose, hints, "");
	else if (sscanf(c, "reading %*d %d", &on) == 1) ghw_region_set_reading(r, on);
	else if (sscanf(c, "neaten %*d %d %lf", &on, &st) == 2) ghw_region_set_neaten(r, on, st);
	else if (sscanf(c, "layer %*d %d", &layer) == 1) ghw_region_set_layer(r, layer);
	else if (sscanf(c, "mode %*d %d", &mode) == 1) ghw_region_set_mode(r, mode);
	else if (sscanf(c, "display %*d %d", &mode) == 1) ghw_region_set_display(r, mode);
	else if (sscanf(c, "edit %*d %4095[^\n]", text) == 1) { unescape(text); ghw_region_edit(r, text); }
	else if (sscanf(c, "report %*d %u %u %u %4095[^\n]", &serial, &action, &rank, text) >= 3) {
		unescape(text);
		ghw_region_report(r, serial, (int)action, rank, text);
	} else if (sscanf(c, "restore %*d %d", &from) == 1 && from >= 0 && from < MAX) {
		struct kept *a = &kept[from];
		ghw_region_restore(r, a->st, a->n, a->value, a->state, a->state_len);
	} else if (sscanf(c, "strokes-only %*d %d", &from) == 1 && from >= 0 && from < MAX) {
		ghw_region_restore(r, kept[from].st, kept[from].n, NULL, NULL, 0);
	} else if (!strncmp(c, "destroy ", 8)) {
		ghw_region_destroy(r);
		regions[id] = NULL;
	}
}

int main(int argc, char **argv)
{
	struct wl_display *dpy = wl_display_connect(NULL);
	if (!dpy) { fprintf(stderr, "no display\n"); return 1; }
	struct wl_registry *r = wl_display_get_registry(dpy);
	wl_registry_add_listener(r, &reg, NULL);
	wl_display_roundtrip(dpy);
	if (!compositor) return 1;
	struct wl_surface *s = wl_compositor_create_surface(compositor);
	struct ghw_pen *pen = ghw_pen_new(dpy, s, NULL, NULL);
	if (!pen) { printf("unavailable\n"); fflush(stdout); return 2; }
	printf("caps %u\nready\n", ghw_pen_capabilities(pen));
	fflush(stdout);
	char line[8192];
	size_t n = 0;
	for (;;) {
		wl_display_flush(dpy);
		struct pollfd p[2] = { { wl_display_get_fd(dpy), POLLIN, 0 }, { 0, POLLIN, 0 } };
		if (poll(p, 2, -1) < 0) break;
		if (p[0].revents & POLLIN) {
			if (wl_display_dispatch(dpy) < 0) break;
			fflush(stdout);
		}
		if (p[0].revents & (POLLHUP | POLLERR)) break;
		if (p[1].revents & (POLLIN | POLLHUP)) {
			ssize_t k = read(0, line + n, sizeof line - 1 - n);
			if (k <= 0) break;
			n += (size_t)k;
			line[n] = 0;
			char *c = line, *e;
			while ((e = strchr(c, '\n'))) {
				*e = 0;
				command(pen, dpy, c);
				fflush(stdout);
				c = e + 1;
			}
			n -= (size_t)(c - line);
			memmove(line, c, n);
		}
	}
	for (int i = 0; i < MAX; i++) ghw_region_destroy(regions[i]);
	ghw_pen_destroy(pen);
	wl_display_disconnect(dpy);
	return 0;
}
