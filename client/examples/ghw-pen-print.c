/* SPDX-License-Identifier: GPL-2.0-or-later
 * ghw-pen-print: the smallest app using libglassine-handwriting. It makes one surface,
 * turns the pen interface on for it and prints every event as a line; commands on stdin
 * change its choices. The host tests drive it; it also shows the calls an app makes.
 *
 *   ghw-pen-print [-n STRENGTH] [-t] [-a X,Y,W,H] [-g SPACING,FIRST] [-p PURPOSE]
 *
 * stdin: "neaten ON STRENGTH", "text ON", "area X Y W H", "guides SPACING FIRST", "context PURPOSE HINTS",
 * "report SERIAL ACTION RANK [TEXT]", "clear", "sync" (answers "synced"). Output:
 * "caps C", "stroke K N X,Y,T,P,TX,TY …", "neat K HOW N X,Y …", "reading TEXT|K,K…",
 * "candidate SERIAL RANK ORIGIN FIT TEXT|CHARS", "done SERIAL COUNT", "report SERIAL RESULT". */
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wayland-client.h>
#include "glassine-handwriting.h"

static struct wl_compositor *compositor;

static void out_points(const struct ghw_point *p, size_t n, int all)
{
	for (size_t i = 0; i < n; i++) {
		if (all) printf(" %.17g,%.17g,%u,%.17g,%.17g,%.17g", p[i].x, p[i].y, p[i].time, p[i].pressure, p[i].tilt_x,
				p[i].tilt_y);
		else printf(" %.17g,%.17g", p[i].x, p[i].y);
	}
	printf("\n");
}

static void on_stroke(void *d, uint32_t k, const struct ghw_point *p, size_t n)
{
	printf("stroke %u %zu", k, n);
	out_points(p, n, 1);
}

static void on_neat(void *d, uint32_t k, int how, const struct ghw_point *p, size_t n)
{
	printf("neat %u %d %zu", k, how, n);
	out_points(p, n, 1);
}

static void on_reading(void *d, const char *text, const uint32_t *s, size_t n)
{
	printf("reading %s|", text);
	for (size_t i = 0; i < n; i++) printf("%s%u", i ? "," : "", s[i]);
	printf("\n");
}

static void on_candidate(void *d, uint32_t serial, uint32_t rank, const char *text, int origin, int fit, const char *chars)
{
	printf("candidate %u %u %d %d %s|%s\n", serial, rank, origin, fit, text, chars);
}

static void on_done(void *d, uint32_t serial, uint32_t count) { printf("done %u %u\n", serial, count); }
static void on_report(void *d, uint32_t serial, int result) { printf("report %u %d\n", serial, result); }
static void on_caps(void *d, uint32_t caps) { printf("caps %u\n", caps); }

static const struct ghw_pen_listener listener = {
	.stroke = on_stroke, .neat = on_neat, .reading = on_reading, .candidate = on_candidate,
	.candidates_done = on_done, .report_done = on_report, .capabilities = on_caps,
};

static void global(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t v)
{
	if (!strcmp(iface, "wl_compositor")) compositor = wl_registry_bind(r, name, &wl_compositor_interface, 4);
}

static void global_remove(void *d, struct wl_registry *r, uint32_t name) {}
static const struct wl_registry_listener reg = { global, global_remove };

int main(int argc, char **argv)
{
	struct wl_display *dpy = wl_display_connect(NULL);
	if (!dpy) { fprintf(stderr, "no display\n"); return 1; }
	struct wl_registry *r = wl_display_get_registry(dpy);
	wl_registry_add_listener(r, &reg, NULL);
	wl_display_roundtrip(dpy);
	if (!compositor) return 1;
	struct wl_surface *s = wl_compositor_create_surface(compositor);
	struct ghw_pen *pen = ghw_pen_new(dpy, s, &listener, NULL);
	if (!pen) { printf("unavailable\n"); fflush(stdout); return 2; }
	printf("caps %u", ghw_pen_capabilities(pen));
	for (size_t i = 0; i < ghw_pen_languages(pen); i++) printf(" %s", ghw_pen_language(pen, i));
	printf("\n");
	int opt;
	while ((opt = getopt(argc, argv, "n:ta:g:p:")) != -1) {
		int x, y, w, h;
		double gs, gf;
		if (opt == 'n') ghw_pen_set_neaten(pen, 1, atof(optarg));
		else if (opt == 't') ghw_pen_set_text(pen, 1);
		else if (opt == 'a' && sscanf(optarg, "%d,%d,%d,%d", &x, &y, &w, &h) == 4) ghw_pen_set_area(pen, x, y, w, h);
		else if (opt == 'g' && sscanf(optarg, "%lf,%lf", &gs, &gf) == 2) ghw_pen_set_guides(pen, gs, gf);
		else if (opt == 'p') ghw_pen_set_context(pen, (uint32_t)atoi(optarg), 0, "", "", "");
	}
	wl_display_roundtrip(dpy);
	printf("ready\n");
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
				int on, x, y, w, h;
				unsigned serial, action, rank, purpose, hints;
				double st, gs, gf;
				char text[4096] = "";
				if (sscanf(c, "neaten %d %lf", &on, &st) == 2) ghw_pen_set_neaten(pen, on, st);
				else if (sscanf(c, "text %d", &on) == 1) ghw_pen_set_text(pen, on);
				else if (sscanf(c, "area %d %d %d %d", &x, &y, &w, &h) == 4) ghw_pen_set_area(pen, x, y, w, h);
				else if (sscanf(c, "guides %lf %lf", &gs, &gf) == 2) ghw_pen_set_guides(pen, gs, gf);
				else if (sscanf(c, "context %u %u", &purpose, &hints) == 2) ghw_pen_set_context(pen, purpose, hints, "", "", "");
				else if (sscanf(c, "report %u %u %u %4095[^\n]", &serial, &action, &rank, text) >= 3)
					ghw_pen_report(pen, serial, (int)action, rank, text);
				else if (!strcmp(c, "clear")) ghw_pen_clear(pen);
				else if (!strcmp(c, "sync")) { wl_display_roundtrip(dpy); printf("synced\n"); fflush(stdout); }
				c = e + 1;
			}
			n -= (size_t)(c - line);
			memmove(line, c, n);
		}
	}
	ghw_pen_destroy(pen);
	wl_display_disconnect(dpy);
	return 0;
}
