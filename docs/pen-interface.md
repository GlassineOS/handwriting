# The pen interface

Handwriting for an app's writing surface (spec 004 FR-006 to FR-011, the neat ink layer of
spec 003): the app turns it on for a `wl_surface` and gets every pen stroke written there as
a numbered vector stroke when the pen lifts. With neatening on it also gets each stroke's
neatened version. With text on it gets readings while the person writes and ranked
candidates at a pause. The app reports what the person did with a reading, and that report
is what the person's samples learn from.

The definition is the Wayland protocol `glassine_handwriting_v1`
(`client/glassine-handwriting-v1.xml`, a copy of GlassineOS/protocols'). The compositor
(GlassineOS/sway) implements it and starts the handwriting service behind it (docs/service.md).

## What an app gets

| On | Events | Notes |
|---|---|---|
| always | `stroke` | the raw stroke as the tablet reported it, sent by the compositor at pen-up, before the app's own tablet events are handled; no round trip, so no delay to the ink (SC-003) |
| neatening | `neat_stroke` | the same points with only x and y moved, naming the raw stroke it replaces; sent again when later strokes or a new strength change it. Strength 0 gives the raw points back |
| text | `reading`, `candidate`, `candidates_done` | a reading about 250 ms after a stroke; the candidates after a pause of 900 ms (up to 5: text, origin, whether a pick passes the check, which strokes make which character) |
| text | `report_done` | the answer to a `report`: `stored`, `not_kept`, `not_allowed` (the person hasn't let this app learn), `stale`, `unavailable` |

Points are `x, y` in surface coordinates, the time in ms (the tablet events' clock), the
pressure 0 to 1 and the tilt in degrees. A stroke counts when it starts inside the area
(`set_area`, the whole surface by default). With ruled lines (`set_guides`), neatening goes
line by line on them. Without them the lines are found from the writing. Each line is read,
then neatened word by word, so a long line costs a few short requests rather than one long
one.

**Privacy (FR-010).** Nothing reaches the service for a surface with neatening and text both
off, nor for a private field: a purpose of `password` or `pin`, or the hints
`sensitive_data` or `hidden_text`. A surface that becomes private is forgotten by the
service at once. Raw strokes still come, because they never leave the compositor.

**Text off, neatening on.** The service still reads the writing (neatening needs the
reading) but sends no readings or candidates, and nothing is stored.

**Identity (FR-001).** The compositor names the app: a sandboxed app by its sandbox's
application id (Wayland security-context, as Flatpak sets it), any other app by the program
its process runs. A name the app gives itself is never asked for.

## Capabilities (FR-003)

On bind the manager sends `capabilities` (`strokes`, plus `neaten`, `text`, `letters` while
the service is there), the languages it reads, then `done`. It sends them again whenever they
change. Without the service an app still gets raw strokes. On a system without the global
(`glassine_handwriting_manager_v1`), the app draws from its own tablet events.

## C: libglassine-handwriting

`#include <glassine-handwriting.h>`, link `-lglassine-handwriting` (with `wayland-client`).
The objects live on the display's default queue, so the events arrive while the toolkit
dispatches, on its thread.

```c
static void on_stroke(void *d, uint32_t k, const struct ghw_point *p, size_t n) { /* keep it */ }
static void on_neat(void *d, uint32_t k, int how, const struct ghw_point *p, size_t n) { /* show it instead */ }
static const struct ghw_pen_listener listener = { .stroke = on_stroke, .neat = on_neat };

struct ghw_pen *pen = ghw_pen_new(display, surface, &listener, app);
if (pen) {                                    /* NULL: no pen interface on this system */
	ghw_pen_set_area(pen, x, y, w, h);
	ghw_pen_set_guides(pen, line_spacing, first_line_y);
	ghw_pen_set_neaten(pen, 1, 0.5);
	ghw_pen_flush(pen);
}
```

`client/examples/ghw-pen-print.c` is a complete app that turns every option on and prints
each event. The host tests drive it.

## Python: glassine_handwriting

The `glassine_handwriting` module (installed in `/usr/lib/glassine/python`, on
libglassine-handwriting through ctypes). For GTK 3 on Wayland:

```python
import glassine_handwriting

class Ink:
    def stroke(self, k, points): ...          # [(x, y, time, pressure, tilt_x, tilt_y)]
    def neat(self, k, how, points): ...
    def candidate(self, serial, rank, text, origin, fit, chars): ...

pen = glassine_handwriting.Pen.for_gdk_window(widget.get_window(), Ink())
if pen:
    pen.set_area(x, y, w, h)        # in the toplevel surface's coordinates
    pen.set_neaten(True, 0.5)
    pen.set_text(True)
```

The samples app's **Neaten my writing** page uses it (`samples/glassine_hwsamples/neat.py`,
`PenInk`). It keeps the direct recognizer path as the fallback on a system without the
interface.

## Reports

After `candidates_done(serial, count)`, the app reports once on that serial:
`report(serial, picked, rank, "")`, `accepted`, `corrected` with the person's text, or
`discarded`. The service then forgets the writing, whatever the result. Picked, accepted and
corrected writing is stored as a sample only when the person has let this app learn
(`learn` permission, docs/service.md). The sample is built from the raw strokes the
compositor sent, never from what the app keeps (FR-009). It is the same sample a client of
the recognizer contract stores for the same pen input.
