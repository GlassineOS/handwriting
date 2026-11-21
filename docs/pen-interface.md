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
one. The service has no confirmed text for the writing, so no word is set in the person's
font (spec 003 FR-043): at the neatest strength the words are pulled towards their letters as
at the strong one.

Every reading is judged in physical size (spec 004 FR-016, FR-017; docs/service.md, The
writing's size): the units carry the output's px per millimetre, the person's letters are known
in millimetres once there are enough of them, and before that a typical small letter of 2.5 mm
is the prior. The app's area and ruled lines count when it sets them; no line is found from the
writing, so a lone character is never read against a line found from itself or from writing
elsewhere. On an output whose physical size the compositor doesn't know, the person sets its
px per millimetre once in Settings (the compositor keeps it per output); until then the units
stay the pen's own and the prior is applied at the default density of 96 px per inch (3.78 px
per millimetre).

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
the service is there, and `regions` to a manager bound at version 2 while the service reads
regions), the languages it reads, then `done`. It sends them again whenever they
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
interface or without the service (capabilities without `neaten`, also when the service
leaves while the page is open).

## Reports

After `candidates_done(serial, count)`, the app reports once on that serial:
`report(serial, picked, rank, "")`, `accepted`, `corrected` with the person's text, or
`discarded`. The service then forgets the writing, whatever the result. Picked, accepted and
corrected writing is stored as a sample only when the person has let this app learn
(`learn` permission, docs/service.md). The sample is built from the raw strokes the
compositor sent, never from what the app keeps (FR-009). It is the same sample a client of
the recognizer contract stores for the same pen input, units and area (docs/service.md, The
writing's size).

## Writing regions (spec 005)

A surface can hold any number of writing regions: handwritten fields, each with its own area,
ruled lines, field type, languages, reading and neatening, whose value is its text. The
protocol is version 2 of `glassine_handwriting_v1` (`get_region`,
`glassine_handwriting_region_v1`); the compositor's side and its channel to the service are in
GlassineOS/sway (`docs/handwriting-channel.md`), the service's in docs/service.md (Writing
regions).

**Where.** `set_rect` places the region on the surface; its strokes, ruled lines and word
layout are in the region's own coordinates (from its top left corner), so a scroll or a new
layout moves the region without changing anything in it, and nothing is read again. `set_clip`
says which part is visible. A stroke belongs, whole, to the region it starts in; where regions
overlap, the highest layer wins. A region costs the service nothing until it is written in, so
a page of many empty fields is free.

**The value.** With reading on, each change of the region's writing gives a `value`: the text
whole (lines in reading order, separated by a newline), and before it, for that serial, each
word of it (`word`: an ink word, its strokes and how far to move them, `dx`, `dy`; `set_word`:
a word with no ink, set in the person's font, as an outline to fill and its box), the strokes
that left the field for good (`gone`: struck out, replaced by an edit, gestures), and the saved
state. Draw the value in one go. The app never needs the person's font, computes a reading or
matches words to ink. At a pause `candidate` events give ranked alternatives for the value;
`gesture` says a strike, scratch or caret was written (its effect comes with the next value).

**Reports.** `report` names a value or its candidates: `accepted` (the value as it is),
`picked` (a candidate's rank), `corrected` (the person's text). It applies to the writing that
value was read from, never to writing added later, and the ink stays the field's content. A
word becomes a sample only with its own ink from the pen that fits its text, only when the
person lets the app learn, and never a word set in the font.

**Text mode.** `set_mode` (one region, or `ghw_pen_set_mode` for all) turns a region into an
ordinary text field edited with the system's keyboard; it takes no strokes. Before going back
to ink mode, send the edited text with `edit`: words left as they were keep their ink, edited
and added words come back as set words at their place, and the ink after an edited word moves
along its line by the difference in width.

**Stored ink.** Keep the region's strokes (from `stroke`, less those in `gone`), its value and
its saved state (opaque bytes) in the document, and hand all three back with `restore` when it
is opened again: the value and word layout come back as they were, words the person confirmed
or edited are never read again, and the rest is read with the model the system has now. Strokes
alone (no value or state) are read anew. Ink handed back is never a sample.

**Size.** Each region's writing is judged in physical size as above; its ruled lines count only
for it.

### C

```c
static void on_value(void *app, struct ghw_region *r, uint32_t serial, const char *text,
		     const struct ghw_word *w, size_t nw, const uint32_t *gone, size_t ngone,
		     const void *state, size_t state_len)
{
	/* forget the strokes in gone; keep text and state with the document */
	for (size_t i = 0; i < nw; i++) {
		if (w[i].set) draw_outline(w[i].path, w[i].npath);         /* fill, non-zero rule */
		else draw_strokes(w[i].strokes, w[i].nstrokes, w[i].dx, w[i].dy);
	}
}
static const struct ghw_region_listener field = { .stroke = on_stroke, .value = on_value };

struct ghw_region *name = ghw_region_new(pen, "name", &field, app);
if (name) {                                   /* NULL: the compositor has no regions */
	ghw_region_set_rect(name, 40, 120, 600, 80);
	ghw_region_set_guides(name, 60, 60);
	ghw_region_set_context(name, GHW_NORMAL, 0, "");
	ghw_pen_flush(pen);
}
/* later: the user scrolled by dy */
ghw_region_set_rect(name, 40, 120 - dy, 600, 80);
/* text mode and back */
ghw_region_set_mode(name, GHW_MODE_TEXT);
ghw_region_edit(name, edited_text);
ghw_region_set_mode(name, GHW_MODE_INK);
/* opening a document */
ghw_region_restore(name, stored, nstored, value, state, state_len);
```

`client/examples/ghw-regions-print.c` is a complete app with regions: it keeps each region's
strokes, value and state as a document would, and hands them back to a fresh region.

### Python

```python
class Field:
    def stroke(self, k, points): ...
    def value(self, serial, text, words, gone, state):
        for w in words:                       # {"text", "strokes", "dx", "dy"} or {"text", "box", "path"}
            ...

f = pen.region("name", Field())
if f:
    f.set_rect(40, 120, 600, 80)
    f.set_guides(60, 60)
    ...
    f.restore(strokes, value, state)          # strokes: {k: points}
```

