# SPDX-License-Identifier: GPL-2.0-or-later
"""The window: one GTK 3 window that draws itself (cairo + Pango in the shell's voices, as the
settings app does): pages instead of scrolling, taps act on release, no animations. The
writing area is its own widget, so the session's ink protocol gets exactly its box through the
GTK ink module (GLASSINE_INK_WIDGETS names its type) and ink appears under the pen.

  glassine-handwriting-samples                 open (or raise) the app
  glassine-handwriting-samples --render OUT.png [--page P] [--theme T] [--size WxH]
                                               draw one page into a PNG (no display needed)
"""
import itertools
import math
import os
import queue
import statistics
import sys
import threading
import time

# the writing area's type, for the GTK ink module (read when GTK loads its modules)
CANVAS_TYPE = "GlassineHandwritingCanvas"
_w = [x for x in os.environ.get("GLASSINE_INK_WIDGETS", "GtkXournal").split(",") if x]
if CANVAS_TYPE not in _w:
    os.environ["GLASSINE_INK_WIDGETS"] = ",".join(_w + [CANVAS_TYPE])

import cairo  # noqa: E402
import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from glassine_ui import paint as PT, voices  # noqa: E402

from . import APP_ID, client, flow, prompts as P  # noqa: E402

W0, H0 = 702, 884
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
THEME_FILE = RUN + "/glassine-shell.theme"
ROTATION_FILE = "/run/glassine-rotation"
LINE = float(os.environ.get("GLASSINE_HANDWRITING_LINE", "150"))   # the writing paper's line height
PAUSE_MS = {"word": 1500, "character": 1200, "sentence": 2500, "pangram": 2500}
LOG = os.environ.get("GLASSINE_HWSAMPLES_LOG") == "1"      # input and flow events on stderr


def log(*a):
    if LOG:
        print("hwsamples:", *a, file=sys.stderr, flush=True)


def note(sh):
    """what became of a written sample, always on stderr (the session log): the sample's id,
    or why nothing was kept; no handwriting and no text"""
    if not sh:
        return
    p = sh.prompt
    what = ("kept %s" % sh.sample_id if sh.sample_id else
            "check failed, fit %.2f (waiting: Keep it, Redo or Skip)" % (sh.fit or 0) if sh.status == "check" else
            "donated %s" % sh.donated_id if sh.donated_id else "not kept: %s" % (sh.why or sh.status))
    if sh.donated_id and sh.sample_id:
        what += ", donated %s" % sh.donated_id
    print("glassine-handwriting-samples: %s sample: %s" % (p.set if p else "-", what), file=sys.stderr, flush=True)
TABS = (("write", "Write"), ("samples", "Samples"), ("coverage", "Coverage"), ("learn", "Learn"), ("donate", "Donate"))
SET_NAMES = {P.AUTO: "What needs practice", "words": "Words", "sentences": "Sentences", "pangrams": "Pangrams",
             "letters": "Single letters", "digits": "Digits and punctuation", P.OWN: "My own words"}
LICENCES = (("CC0-1.0", "CC0 (public domain)"), ("CC-BY-4.0", "CC BY 4.0 (with credit)"))


def read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def documents_dir():
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
    return d or os.path.join(os.path.expanduser("~"), "Documents")


def free_name(folder, stem, ext):
    os.makedirs(folder, exist_ok=True)
    k = 1
    while os.path.exists(os.path.join(folder, f"{stem}-{k}{ext}")):
        k += 1
    return os.path.join(folder, f"{stem}-{k}{ext}")


class Theme:
    def __init__(self, name):
        g = voices.voice(name)
        s, k = g["scheme"], g["card"]
        self.name = g["name"]
        self.ink, self.paper, self.sheet = s["ink"], s["paper"], s["sheet"]
        self.dim, self.rule, self.soft = s["dim"], s["rule"], s["ink-soft"]
        self.accent, self.on_accent = s["accent"], s["on-accent"]
        self.F = k["F"]
        self.row, self.sub, self.val, self.big, self.head, self.title = k["row"], k["sub"], k["val"], k["big"], \
            k["head"], k["title"]

    def font(self, px, weight=500, lh=1.25):
        return f"{weight} {px}px/{lh} {self.F}"


def rgba(col, a=1.0):
    c = PT.color(col)
    return (c[0], c[1], c[2], a * c[3]) if c else (0, 0, 0, a)


# ---------------------------------------------------------------- drawing helpers


class Hits:
    """the tap targets of one paint: boxes with what a tap does"""

    def __init__(self):
        self.boxes = []

    def add(self, x, y, w, h, fn):
        self.boxes.append((x, y, w, h, fn))

    def at(self, x, y):
        return self.find(x, y)[1]

    def find(self, x, y):
        """(the box, what a tap on it does); the box stays the same across repaints while
        the function is made anew each time"""
        for bx, by, bw, bh, fn in reversed(self.boxes):
            if bx <= x < bx + bw and by <= y < by + bh:
                return (round(bx), round(by), round(bw), round(bh)), fn
        return None, None


def pill(cr, t, hits, x, y, label, fn, primary=False, w=None, on=True):
    h = 48
    tw, _ = PT.measure(cr, label, t.row)
    w = w or tw + 40
    if primary and on:
        PT.box(cr, x, y, w, h, "999px", bg=t.accent)
        col = t.on_accent
    else:
        PT.box(cr, x, y, w, h, "999px", bg=t.paper, shadow="inset 0 0 0 1.5px " + (t.ink if on else t.dim))
        col = t.ink if on else t.dim
    PT.text(cr, label, t.row, x + w / 2, y + (h - PT.measure(cr, label, t.row)[1]) / 2, col, align=1)
    if on and fn:
        hits.add(x, y, w, h, fn)
    return w


def pills_right(cr, t, hits, right, y, items):
    """[(label, fn, primary)] laid out from the right edge leftwards; returns the left edge"""
    x = right
    for label, fn, primary in reversed(items):
        w = PT.measure(cr, label, t.row)[0] + 40
        x -= w
        pill(cr, t, hits, x, y, label, fn, primary, w=w, on=fn is not None)
        x -= 12
    return x


def draw_ink(cr, t, strokes, x, y, w, h, width=2.0):
    """strokes [(x, y, ...)] scaled into a box, keeping their shape"""
    pts = [p for s in strokes for p in s]
    if not pts:
        return
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    k = min(w / max(x1 - x0, 1), h / max(y1 - y0, 1), 1.0)
    ox, oy = x + (w - (x1 - x0) * k) / 2, y + (h - (y1 - y0) * k) / 2
    cr.save()
    cr.set_source_rgba(*rgba(t.ink))
    cr.set_line_width(width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for s in strokes:
        if not s:
            continue
        cr.move_to(ox + (s[0][0] - x0) * k, oy + (s[0][1] - y0) * k)
        for p in s[1:]:
            cr.line_to(ox + (p[0] - x0) * k, oy + (p[1] - y0) * k)
        if len(s) == 1:
            cr.line_to(ox + (s[0][0] - x0) * k + 0.1, oy + (s[0][1] - y0) * k)
        cr.stroke()
    cr.restore()


def guides(cr, t, w, h, line, ctl=40):
    """the keyboard's writing paper: one dashed guide per line, at two thirds of it"""
    cr.save()
    cr.set_source_rgba(*rgba(t.ink, 0.35))
    cr.set_line_width(1.5)
    cr.set_dash([ctl / 8, ctl / 8])
    y = line * 2 / 3
    while y < h:
        cr.move_to(ctl * 0.75, y)
        cr.line_to(w - ctl * 0.75, y)
        cr.stroke()
        y += line
    cr.restore()


# ---------------------------------------------------------------- the work behind the pages


class Worker:
    """one thread for the recognizer's calls; results come back on GTK's main loop. Low
    calls (reading the coverage, in small steps) go only when nothing else waits"""

    def __init__(self):
        self.q = queue.PriorityQueue()
        self.seq = itertools.count()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            _, _, fn, done = self.q.get()
            try:
                r, err = fn(), None
            except Exception as e:      # shown on the page, the app keeps running
                r, err = None, e
            if done:                    # bound now: the next call rebinds these before the main loop runs
                GLib.idle_add(lambda done=done, r=r, err=err: (done(r, err), False)[1])

    def do(self, fn, done=None, low=False):
        self.q.put((int(low), next(self.seq), fn, done))

    def drain(self, timeout):
        """waits until the calls queued now (not the low ones) are done: at exit, a sample
        written last is still read and kept"""
        ev = threading.Event()
        self.q.put((0, next(self.seq), ev.set, None))
        return ev.wait(timeout)


class UI:
    """the pages' state and actions; redraw() repaints"""

    def __init__(self, redraw, demo=False):
        self.redraw = redraw
        self.state = flow.State()
        self.rec = client.Recognizer()
        self.cli = client.Cli()
        self.writer = None
        self.page = "write"
        self.theme = Theme(read_file(THEME_FILE) or "white")
        self.msg = ""
        self.busy = 0                   # calls queued or running in the worker
        self.loaded = demo              # counts, plan, donation, learning, adaptations are in
        self.cov_loaded = demo          # the coverage is read
        self.refreshing = False         # a refresh is queued or running
        self.again = False              # and another one is wanted after it
        self.counts = (0, 0)
        self.fresh = 0
        self.samples = []               # [(id, time, text)]
        self.sp = 0                     # samples page
        self.detail = None              # (id, sample dict)
        self.plan = None
        self.adapts = {}
        self.adapting = None            # progress text while a run goes
        self.waiting_charge = False
        self.result = None
        self.donation = (None, 0, None)
        self.learning = True
        self.picking_licence = False
        self.earlier = []               # [(id, time, text)] kept before donating was on, to offer
        self.earlier_out = set()        # ids the person leaves out
        self.reviewing = False          # the list of the earlier samples is open
        self.ep = 0                     # its page
        self.confirm = None             # (question, yes-label, fn) for a destructive action
        self.sets_open = False
        self.demo = demo
        self.work = Worker() if not demo else None
        if not demo:
            self.writer = flow.Writer(self.rec, self.cli, self.state)
            self.refresh()

    # ---------------------------------------------------------- background

    def run(self, fn, done=None):
        if self.demo:
            return
        self.busy += 1
        self.redraw()

        def fin(r, err):
            self.busy -= 1
            if err:
                print("glassine-handwriting-samples: error: %s" % err, file=sys.stderr, flush=True)
                self.msg = "Something went wrong: %s" % err
            elif done:
                done(r)
            self.redraw()
        self.work.do(fn, fin)

    def shown_state(self):
        """what the pages show from a refresh: a refresh that changes none of it repaints
        nothing (each repaint is a refresh of the e-ink panel)"""
        return (self.counts, self.plan, self.donation, self.learning, self.adapts, self.samples, self.earlier,
                self.fresh, self.loaded, self.cov_loaded, self.reviewing, len(self.writer.counted) if self.writer else 0)

    def refresh(self):
        """counts, samples, coverage, the donation, the adaptation: in the background, after
        anything the person's writing waits for (low), one at a time, and repainted only when
        something shown changed"""
        if self.demo:
            return
        if self.refreshing:
            self.again = True
            return
        self.refreshing = True
        self.again = False

        def get():
            t = time.monotonic()
            rows = self.rec.list()
            self.writer.set_coverage(rows)
            don = self.cli.donation()
            earlier = self.cli.donation_earlier() if self.state.donating and don[0] else []
            r = (self.writer.counts(rows=[(x[0], x[1], x[4]) for x in rows]), self.cli.plan(self.state.lang), don,
                 self.cli.learning(), self.cli.adaptations(self.state.lang), [(x[0], x[1], x[4]) for x in rows],
                 earlier)
            log("refresh %.0f ms, %d samples" % ((time.monotonic() - t) * 1000, len(rows)))
            return r

        def got(r, err):
            self.refreshing = False
            before = self.shown_state()
            if err:
                print("glassine-handwriting-samples: error: %s" % err, file=sys.stderr, flush=True)
                self.msg = "Something went wrong: %s" % err
            else:
                self.counts, self.plan, self.donation, self.learning, self.adapts, self.samples, self.earlier = r
                self.earlier_out &= {e[0] for e in self.earlier}
                if not self.earlier:
                    self.reviewing = False
                self.fresh = self.plan["new"]
                self.loaded = self.cov_loaded = True
            if err or self.shown_state() != before:
                self.redraw()
            if self.again:
                self.refresh()
        self.work.do(get, got, low=True)

    # ---------------------------------------------------------- writing

    def submit(self, strokes, units, area, prompt):
        """the ink written to prompt (taken by Next, the next prompt is already up) is read
        and kept in the background"""
        def go():
            return self.writer.submit(strokes, units, area, prompt)

        def fin(sh):
            log("kept" if sh else "nothing", sh and (sh.status, sh.sample_id, sh.why, sh.fit,
                                                     sh.reading[:1] if sh.reading else None))
            note(sh)
            self.msg = ""
            if sh and sh.status == "kept":
                self.counts = (self.counts[0] + bool(sh.sample_id), self.counts[1] + bool(sh.sample_id))
                self.fresh += bool(sh.sample_id)
                if sh.donated_id:
                    self.donation = (self.donation[0], self.donation[1] + 1, self.donation[2])
        self.run(go, fin)

    def probe(self, strokes, units, area, done):
        """at a pause: does the ink already fit the whole prompt? (then it is submitted)"""
        p = self.writer.prompt

        def go():
            self.rec.write("p", p.lang, units, area, strokes)
            try:
                return self.rec.align("p", p.text)
            finally:
                self.rec.end("p")
        self.work.do(go, lambda r, err: done(r if not err else None))

    def act(self, name):
        fn = getattr(self.writer, name)
        was = self.writer.shown

        def fin(_):
            if was and name == "keep_anyway":
                note(was)
            elif was:
                print("glassine-handwriting-samples: %s sample: %s (%s)" % (
                    was.prompt.set, name, was.sample_id or "nothing kept"), file=sys.stderr, flush=True)
            self.refresh()
        self.run(fn, fin)

    def choose_set(self, name):
        self.sets_open = False
        self.run(lambda: self.writer.choose_set(name))

    # ---------------------------------------------------------- samples

    def open_sample(self, sid):
        self.run(lambda: self.rec.sample(sid), lambda s: setattr(self, "detail", (sid, s)))

    def delete_sample(self, sid):
        def go():
            self.rec.forget(sid)
            self.writer.forgotten(sid)
        self.detail = None
        self.confirm = None
        self.run(go, lambda _: self.refresh())

    def erase_all(self):
        self.confirm = None

        def go():
            n = self.cli.erase()
            self.writer.forgotten()
            return n
        self.run(go, lambda n: (setattr(self, "msg", "%d samples erased." % n), self.refresh()))

    def export(self):
        path = free_name(os.path.join(documents_dir(), "Handwriting"), "handwriting-samples", ".jsonl")
        self.run(lambda: self.cli.export(path), lambda _: setattr(self, "msg", "Exported to " + path))

    def import_latest(self):
        folder = os.path.join(documents_dir(), "Handwriting")
        try:
            files = sorted((os.path.join(folder, f) for f in os.listdir(folder)
                            if f.startswith("handwriting-samples-") and f.endswith(".jsonl")), key=os.path.getmtime)
        except OSError:
            files = []
        if not files:
            self.msg = "No export file in " + folder
            self.redraw()
            return
        self.run(lambda: self.cli.import_(files[-1]),
                 lambda n: (setattr(self, "msg", "%d samples added from %s" % (n, os.path.basename(files[-1]))),
                            self.refresh()))

    def donate_one(self, sid):
        self.run(lambda: self.cli.donation_add(sid),
                 lambda _: (setattr(self, "msg", "Added to the donation."), self.refresh()))

    # ---------------------------------------------------------- learning

    def adapt(self):
        def check():
            return self.cli.charging()

        def got(charging):
            if charging:
                self.waiting_charge = False
                self.start_adapt()
            else:
                self.waiting_charge = True
                GLib.timeout_add_seconds(10, self.poll_charge)
        self.run(check, got)

    def poll_charge(self):
        if not self.waiting_charge:
            return False
        self.work.do(self.cli.charging, lambda r, err: (r and self.waiting_charge and self.adapt()))
        return False

    def start_adapt(self):
        self.result = None
        self.adapting = "Starting"

        def line(kind, f):
            if kind == "plan":
                self.adapting = "Learning from %s samples, %s held back to check" % (f[0], f[1])
            elif kind == "epoch":
                self.adapting = "Pass %s of %s" % (f[0], f[1])
            GLib.idle_add(lambda: (self.redraw(), False)[1])

        def go():
            # its own thread: a run takes minutes, and the writing's requests (the worker's)
            # must not wait for it
            try:
                last = self.cli.adapt(self.state.lang, on_line=line)
            except Exception as e:      # shown on the page, the app keeps running
                print("glassine-handwriting-samples: error: %s" % e, file=sys.stderr, flush=True)
                last = ("skip", ["error"])
            GLib.idle_add(lambda: (fin(last), False)[1])

        def fin(last):
            self.adapting = None
            self.result = last
            self.redraw()
            self.refresh()
        self.redraw()
        threading.Thread(target=go, daemon=True).start()

    def revert(self):
        self.confirm = None
        self.run(lambda: self.cli.revert(self.state.lang), lambda _: self.refresh())

    # ---------------------------------------------------------- donation

    def donating(self, on, licence=None):
        def go():
            if on and licence:
                self.cli.donation_start(licence)
            self.state.donating = on
            self.state.save()
        self.picking_licence = False
        self.run(go, lambda _: self.refresh())

    def add_earlier(self):
        """the earlier samples the person didn't leave out go into the donation"""
        ids = [e[0] for e in self.earlier if e[0] not in self.earlier_out]
        self.reviewing = False
        self.run(lambda: self.cli.donation_add_all(ids),
                 lambda n: (setattr(self, "msg", "%d earlier samples added to the donation." % n), self.refresh()))

    def write_donation(self):
        lic = self.donation[0]
        path = free_name(os.path.join(documents_dir(), "Handwriting donations"), "handwriting-donation", ".jsonl")

        def go():
            self.cli.donation_write(path)
            # one file per donation: the next starts empty, with the same licence
            self.cli.donation_erase()
            self.cli.donation_start(lic)
        self.run(go, lambda _: (setattr(self, "msg", "Written to " + path), self.refresh()))

    def new_donor(self):
        self.confirm = None
        self.run(self.cli.donor_new, lambda _: self.refresh())

    def erase_donation(self):
        self.confirm = None

        def go():
            lic = self.donation[0]
            self.cli.donation_erase()
            if lic and self.state.donating:
                self.cli.donation_start(lic)
        self.run(go, lambda _: self.refresh())


# ---------------------------------------------------------------- pages


def header(cr, ui, hits, w):
    t = ui.theme
    PT.fill(cr, 0, 0, w, 64, t.paper)
    PT.text(cr, "Handwriting", t.title, 20, 10, t.ink)
    x = 20
    y = 40
    for key, label in TABS:
        tw, th = PT.measure(cr, label, t.sub)
        on = ui.page == key
        PT.text(cr, label, t.sub, x, y, t.ink, weight=600 if on else 400)
        if on:
            cr.set_source_rgba(*rgba(t.ink))
            cr.rectangle(x, y + th + 2, tw, 2)
            cr.fill()
        hits.add(x - 8, 30, tw + 16, 34, lambda k=key: ui_go(ui, k))
        x += tw + 22
    if ui.busy:
        PT.text(cr, "…", t.row, w - 20, 14, t.ink, align=2)
    PT.rule(cr, 0, 63, w, "1px solid " + t.ink)


def ui_go(ui, page):
    ui.page = page
    ui.detail = None
    ui.confirm = None
    ui.msg = ""
    ui.sets_open = False
    if page != "write" and not ui.demo:
        ui.refresh()
    ui.redraw()


def write_top(cr, ui, hits, w, h):
    """the prompt, and the sample just written with what was read"""
    t = ui.theme
    PT.fill(cr, 0, 0, w, h, t.paper)
    header(cr, ui, hits, w)
    wr = ui.writer
    p = wr.prompt if wr else P.Prompt("The quick brown fox jumps over the lazy dog.", "pangrams", "en")
    st = ui.state
    label = SET_NAMES.get(st.set, st.set) + "  ▾"
    lw, _ = PT.measure(cr, label, t.sub)
    PT.text(cr, label, t.sub, 20, 76, t.soft)
    hits.add(12, 70, lw + 16, 30, lambda: (setattr(ui, "sets_open", True), ui.redraw()))
    if p is None:
        PT.text(cr, "Nothing to write in this set." + (" Add your own words." if st.set == P.OWN else ""),
                t.row, 20, 110, t.ink, maxw=w - 40, wrap=True)
    else:
        size = 34 if len(p.text) < 24 else 26
        PT.text(cr, p.text, t.font(size, 500, 1.2), 20, 102, t.ink, maxw=w - 40, wrap=True)
    sh = wr.shown if wr else None
    y = h - 112
    PT.rule(cr, 20, y - 8, w - 40, "1px solid " + t.rule)
    if sh:
        box_w = 150
        PT.box(cr, 20, y, box_w, 64, "6px", bg=t.sheet)
        draw_ink(cr, t, sh.strokes, 26, y + 6, box_w - 12, 52, 1.5)
        read = sh.reading[0][0] if sh.reading else "(nothing)"
        if sh.status == "check":
            what = "This doesn't look like “%s”." % sh.prompt.text
            PT.text(cr, what, t.row, 184, y, t.ink, maxw=w - 204)
            PT.text(cr, "Read as “%s”" % read, t.sub, 184, y + 26, t.soft, maxw=w - 204)
            pills_right(cr, t, hits, w - 20, y + 56, [("Redo", lambda: ui.act("redo"), False),
                                                      ("Skip", lambda: ui.act("skip"), False),
                                                      ("Keep it", lambda: ui.act("keep_anyway"), True)])
        else:
            kept = "Kept" if sh.sample_id else "Donated" if sh.donated_id else "Not kept"
            if sh.status == "not-kept" and sh.why == "learning":
                kept = "Not kept: learning is off"
            extra = ("" if sh.exact or not sh.sample_id else " (letters placed by guess)") + \
                (" · donated" if sh.donated_id and sh.sample_id else "")
            PT.text(cr, "“%s” · %s%s" % (sh.prompt.text, kept, extra), t.row, 184, y, t.ink, maxw=w - 204)
            PT.text(cr, "Read before learning: “%s”" % read, t.sub, 184, y + 26, t.soft, maxw=w - 204)
            pills_right(cr, t, hits, w - 20, y + 56, [("Redo", lambda: ui.act("redo"), False),
                                                      ("Discard", lambda: ui.act("discard"), False)])
    else:
        PT.text(cr, "Write it on the lines below, then pause or tap Next.", t.sub, 20, y, t.soft)
    if ui.msg:
        PT.text(cr, ui.msg, t.sub, 20, h - 22, t.ink, maxw=w - 40)


def write_bottom(cr, ui, hits, w, h, canvas):
    t = ui.theme
    PT.fill(cr, 0, 0, w, h, t.paper)
    PT.rule(cr, 0, 0, w, "1px solid " + t.ink)
    today, total = ui.counts
    PT.text(cr, "Today %d · total %d · new since learning %d" % (today, total, ui.fresh) if ui.loaded else "Loading…",
            t.sub, 20, 16, t.soft)
    has_ink = bool(canvas and canvas.strokes)
    pills_right(cr, t, hits, w - 20, h - 64, [
        ("Clear", (lambda: canvas.clear()) if has_ink else None, False),
        ("Skip", (lambda: ui.act("skip")) if not has_ink else None, False),
        ("Next", (lambda: canvas.submit()) if has_ink else None, True)])


def sets_page(cr, ui, hits, w, h):
    t = ui.theme
    PT.fill(cr, 0, 0, w, h, t.paper)
    header(cr, ui, hits, w)
    PT.text(cr, "What to write", t.title, 20, 84, t.ink)
    y = 130
    for name in (P.AUTO,) + P.SETS + (P.OWN,):
        n = len(ui.writer.sets.get(name, [])) if ui.writer and name != P.AUTO else None
        label = SET_NAMES[name] + ("" if n is None else "  (%d)" % n)
        on = ui.state.set == name
        PT.box(cr, 20, y, w - 40, 52, "8px", bg=t.sheet if on else t.paper, shadow="inset 0 0 0 1.5px " + t.ink)
        PT.text(cr, label, t.row, 36, y + 15, t.ink, weight=600 if on else 500)
        hits.add(20, y, w - 40, 52, lambda k=name: ui.choose_set(k))
        y += 62
    PT.text(cr, "Your own words are in %s, one per line. They stay on this device and are never donated as a set."
            % P.own_words_path(), t.sub, 20, y + 8, t.soft, maxw=w - 40, wrap=True)
    pill(cr, t, hits, 20, h - 72, "Back", lambda: (setattr(ui, "sets_open", False), ui.redraw()))


def samples_page(cr, ui, hits, w, h):
    t = ui.theme
    PT.fill(cr, 0, 0, w, h, t.paper)
    header(cr, ui, hits, w)
    if ui.detail:
        sid, s = ui.detail
        PT.text(cr, s.get("text", ""), t.title, 20, 84, t.ink, maxw=w - 40, wrap=True)
        rd = s.get("reading") or []
        info = "Read when written: “%s”" % rd[0]["text"] if rd else "No reading kept"
        info += " · letters %s" % ("placed exactly" if s.get("spans_source") == "exact" else "placed by guess")
        PT.text(cr, info, t.sub, 20, 120, t.soft, maxw=w - 40)
        PT.box(cr, 20, 150, w - 40, h - 330, "8px", bg=t.sheet)
        draw_ink(cr, t, s.get("strokes", []), 36, 166, w - 72, h - 362, 2.0)
        n = sum(len(q) for q in s.get("strokes", []))
        u = s.get("units", {})
        PT.text(cr, "%d strokes, %d points as the pen gave them%s" % (
            len(s.get("strokes", [])), n, ", %g per mm" % u["per_mm"] if u.get("per_mm") else ""),
            t.sub, 20, h - 168, t.soft)
        items = [("Back", lambda: (setattr(ui, "detail", None), ui.redraw()), False)]
        if ui.donation[0] and ui.state.donating:
            items.append(("Add to donation", lambda: ui.donate_one(sid), False))
        items.append(("Delete", lambda: ask(ui, "Delete this sample?", "Delete", lambda: ui.delete_sample(sid)), True))
        pills_right(cr, t, hits, w - 20, h - 72, items)
        return
    PT.text(cr, "Samples from this app: %d" % len(ui.samples), t.title, 20, 84, t.ink)
    per = max(1, (h - 300) // 52)
    pages = max(1, math.ceil(len(ui.samples) / per))
    ui.sp = min(ui.sp, pages - 1)
    rows = list(reversed(ui.samples))[ui.sp * per:(ui.sp + 1) * per]
    y = 130
    for sid, when, text in rows:
        PT.rule(cr, 20, y + 51, w - 40, "1px solid " + t.rule)
        PT.text(cr, text, t.row, 20, y + 14, t.ink, maxw=w - 200)
        PT.text(cr, time.strftime("%d %b %H:%M", time.localtime(when)), t.sub, w - 20, y + 16, t.soft, align=2)
        hits.add(20, y, w - 40, 52, lambda i=sid: ui.open_sample(i))
        y += 52
    if pages > 1:
        PT.text(cr, "Page %d of %d" % (ui.sp + 1, pages), t.sub, w / 2, h - 158, t.soft, align=1)
        pill(cr, t, hits, 20, h - 170, "‹", (lambda: (setattr(ui, "sp", ui.sp - 1), ui.redraw())) if ui.sp else None,
             w=60, on=ui.sp > 0)
        pill(cr, t, hits, w - 80, h - 170, "›", lambda: (setattr(ui, "sp", ui.sp + 1), ui.redraw()), w=60,
             on=ui.sp < pages - 1)
    pills_right(cr, t, hits, w - 20, h - 72, [
        ("Import", ui.import_latest, False), ("Export", ui.export, False),
        ("Erase all", (lambda: ask(ui, "Erase all %d samples from this app? Your adaptation stays until it is "
                                   "redone." % len(ui.samples), "Erase", ui.erase_all)) if ui.samples else None, False)])
    if ui.msg:
        PT.text(cr, ui.msg, t.sub, 20, h - 112, t.ink, maxw=w - 40)


def coverage_page(cr, ui, hits, w, h):
    t = ui.theme
    PT.fill(cr, 0, 0, w, h, t.paper)
    header(cr, ui, hits, w)
    PT.text(cr, "Samples per character", t.title, 20, 84, t.ink)
    PT.text(cr, "Count, and how often it was read wrongly when written. Prompts favour the weak ones.",
            t.sub, 20, 114, t.soft, maxw=w - 40, wrap=True)
    cov = ui.writer.cov if ui.writer else P.Coverage()
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,;:!?'-()"
    cols = 6
    cw, ch = (w - 40) / cols, 50
    y0 = 160
    for i, (c, n, err) in enumerate(cov.table(chars)):
        x, y = 20 + (i % cols) * cw, y0 + (i // cols) * ch
        if y + ch > h - 10:
            break
        weak = n < 3 or (err is not None and err > 0.3)
        PT.box(cr, x + 2, y + 2, cw - 4, ch - 4, "6px", bg=t.sheet if weak else t.paper,
               shadow="inset 0 0 0 1px " + t.rule)
        PT.text(cr, c, t.font(22, 500, 1), x + 12, y + 12, t.ink)
        PT.text(cr, "%d%s" % (n, "" if err is None else " · %d%%" % round(err * 100)), t.sub, x + cw - 10, y + 16,
                t.soft, align=2)


def pct(v):
    return "%.1f%%" % (100 * v)


def learn_page(cr, ui, hits, w, h):
    t = ui.theme
    PT.fill(cr, 0, 0, w, h, t.paper)
    header(cr, ui, hits, w)
    PT.text(cr, "Learn your handwriting", t.title, 20, 84, t.ink)
    pl = ui.plan or {"train": 0, "held": 0, "new": 0, "min_train": 40, "min_held": 10, "charging": False}
    y = 124
    lines = ["%d new samples since the last time it learned." % pl["new"],
             "It learns from %d of your samples and checks itself on %d others it doesn't learn from."
             % (pl["train"], pl["held"])]
    if pl["train"] < pl["min_train"] or pl["held"] < pl["min_held"]:
        lines.append("It needs at least %d to learn from and %d to check on: keep writing."
                     % (pl["min_train"], pl["min_held"]))
    if not ui.learning:
        lines.append("Learning is off in Settings: nothing is learned or kept.")
    for s in lines:
        _, th = PT.text(cr, s, t.row, 20, y, t.ink, maxw=w - 40, wrap=True)
        y += th + 10
    y += 6
    if ui.adapting:
        PT.text(cr, "Learning… " + ui.adapting, t.row, 20, y, t.ink, maxw=w - 40, wrap=True)
        PT.text(cr, "You can keep using the device; it runs at the lowest priority.", t.sub, 20, y + 30, t.soft)
    elif ui.waiting_charge:
        PT.text(cr, "The device isn't charging. Learning takes a while, so it waits for the charger and "
                "starts once it is plugged in while this app is open; otherwise it learns by itself the next "
                "time the device is idle and charging.", t.row, 20, y, t.ink, maxw=w - 40, wrap=True)
    elif ui.result:
        kind, f = ui.result
        if kind == "result":
            before, after = float(f[1]), float(f[2])
            if f[0] == "kept":
                s = "Better: errors on your held-back samples went from %s to %s. It uses the new one now." % (
                    pct(before), pct(after))
            else:
                s = "Not better (%s before, %s with the new one), so it keeps the one it had." % (pct(before),
                                                                                                    pct(after))
        else:
            s = {"too-few": "Not enough samples yet.", "nothing-new": "Nothing new to learn from.",
                 "charging": "The device isn't charging.", "unplugged": "Stopped: the charger was unplugged.",
                 "learning": "Learning is off in Settings.", "erased": "Samples were erased meanwhile; dropped."
                 }.get(f[0] if f else "", "It didn't run.")
        PT.text(cr, s, t.row, 20, y, t.ink, maxw=w - 40, wrap=True)
    cur, prev = ui.adapts.get("current"), ui.adapts.get("previous")
    y = h - 200
    PT.rule(cr, 20, y - 10, w - 40, "1px solid " + t.rule)
    if cur:
        PT.text(cr, "In use since %s: %s → %s on held-back samples." % (
            time.strftime("%d %b %H:%M", time.localtime(cur["time"])), pct(cur["before"]), pct(cur["after"])),
            t.sub, 20, y, t.soft, maxw=w - 40)
    else:
        PT.text(cr, "In use: the base model only.", t.sub, 20, y, t.soft)
    if prev:
        PT.text(cr, "The one before is kept (%s); you can go back to it." % time.strftime(
            "%d %b %H:%M", time.localtime(prev["time"])), t.sub, 20, y + 24, t.soft, maxw=w - 40)
    can = not ui.adapting and not ui.busy and ui.learning
    items = []
    if prev and not ui.adapting:
        items.append(("Go back", lambda: ask(ui, "Go back to the adaptation before?", "Go back", ui.revert), False))
    if ui.waiting_charge:
        items.append(("Stop waiting", lambda: (setattr(ui, "waiting_charge", False), ui.redraw()), False))
    else:
        items.append(("Learn now", ui.adapt if can else None, True))
    pills_right(cr, t, hits, w - 20, h - 72, items)


def donate_page(cr, ui, hits, w, h):
    t = ui.theme
    PT.fill(cr, 0, 0, w, h, t.paper)
    header(cr, ui, hits, w)
    PT.text(cr, "Help the base models", t.title, 20, 84, t.ink)
    y = 124
    _, th = PT.text(cr, "When donating is on, every sample you keep for a shipped prompt also goes into a donation. "
                    "You write the donation into a file and send it to the project yourself; nothing is sent from "
                    "here. It holds only your strokes and the prompt, under the licence you choose, and a random "
                    "donor number, the same in all your donations: no name, device, app or time.", t.row, 20, y, t.ink, maxw=w - 40, wrap=True)
    y += th + 18
    lic, n, donor = ui.donation
    if ui.picking_licence:
        PT.text(cr, "Choose the licence for this donation:", t.row, 20, y, t.ink)
        y += 36
        for key, label in LICENCES:
            PT.box(cr, 20, y, w - 40, 52, "8px", bg=t.paper, shadow="inset 0 0 0 1.5px " + t.ink)
            PT.text(cr, label, t.row, 36, y + 15, t.ink)
            hits.add(20, y, w - 40, 52, lambda k=key: ui.donating(True, k))
            y += 62
        pill(cr, t, hits, 20, h - 72, "Cancel", lambda: (setattr(ui, "picking_licence", False), ui.redraw()))
        return
    on = ui.state.donating and lic
    if on and ui.reviewing and ui.earlier:
        earlier_review(cr, ui, hits, w, h, y)
        return
    PT.text(cr, "Donating: %s" % ("on, " + lic if on else "off"), t.row, 20, y, t.ink, weight=600)
    y += 34
    PT.text(cr, "%d samples in this donation." % n, t.row, 20, y, t.ink)
    y += 34
    if on and ui.earlier:
        # the prompted samples kept before donating was on: offered, never added unasked
        k = len(ui.earlier)
        PT.box(cr, 20, y, w - 40, 112, "8px", bg=t.paper, shadow="inset 0 0 0 1.5px " + t.rule)
        PT.text(cr, "%d sample%s you kept before for shipped prompts can go into this donation too. "
                "Review them and leave out any you like; nothing is added unless you add them."
                % (k, "" if k == 1 else "s"), t.row, 36, y + 14, t.ink, maxw=w - 200, wrap=True)
        pill(cr, t, hits, w - 150, y + 34, "Review", lambda: (setattr(ui, "reviewing", True), setattr(ui, "ep", 0),
                                                            ui.redraw()),
             primary=True)
        y += 124
    if donor:
        # quiet: what the donor ID is, and a way to start over as a new writer
        _, th = PT.text(cr, "Donor ID: %s… — a random number, the same in all your donations, so they count as one "
                        "writer's. It says nothing about you." % donor[:4], t.sub, 20, y, t.soft, maxw=w - 200,
                        wrap=True)
        PT.text(cr, "Get a new one", t.sub, w - 20, y, t.ink, align=2)
        tw, _ = PT.measure(cr, "Get a new one", t.sub)
        hits.add(w - 28 - tw, y - 8, tw + 16, 40, lambda: ask(
            ui, "Get a new donor ID? Your next donations then count as another writer's.", "Get a new one",
            ui.new_donor))
        y += max(th, 30) + 14
    if ui.msg:
        PT.text(cr, ui.msg, t.sub, 20, y, t.ink, maxw=w - 40, wrap=True)
    pills_right(cr, t, hits, w - 20, h - 72, [
        ("Erase", (lambda: ask(ui, "Erase the donation? Your own samples stay.", "Erase", ui.erase_donation))
         if n else None, False),
        ("Write file", ui.write_donation if n and lic else None, False),
        ("Turn off", lambda: ui.donating(False), False) if on else ("Turn on", lambda: turn_on(ui), True)])


def earlier_review(cr, ui, hits, w, h, y):
    """the earlier samples, each in or left out by a tap; Add adds those in, Cancel none"""
    t = ui.theme
    keep = [e for e in ui.earlier if e[0] not in ui.earlier_out]
    PT.text(cr, "Samples kept before: tap one to leave it out.", t.row, 20, y, t.ink, weight=600)
    y += 40
    rows = max(1, int(h - 100 - y) // 56)
    pages = (len(ui.earlier) + rows - 1) // rows
    ui.ep = min(ui.ep, pages - 1)
    for sid, when, text in ui.earlier[ui.ep * rows:(ui.ep + 1) * rows]:
        inside = sid not in ui.earlier_out
        PT.box(cr, 20, y, w - 40, 48, "8px", bg=t.sheet if inside else t.paper,
               shadow="inset 0 0 0 1.5px " + (t.ink if inside else t.rule))
        PT.text(cr, ("✓  " if inside else "—  ") + text, t.row, 36, y + 12, t.ink if inside else t.soft,
                maxw=w - 220)
        PT.text(cr, time.strftime("%d %b", time.localtime(when)), t.sub, w - 36, y + 15, t.soft, align=2)

        def flip(i=sid):
            ui.earlier_out ^= {i}
            ui.redraw()
        hits.add(20, y, w - 40, 48, flip)
        y += 56
    def page(d):
        ui.ep += d
        ui.redraw()
    more = []
    if pages > 1:
        PT.text(cr, "Page %d of %d" % (ui.ep + 1, pages), t.sub, 20, y, t.soft)
        more = [("Back", (lambda: page(-1)) if ui.ep else None, False),
                ("More", (lambda: page(1)) if ui.ep < pages - 1 else None, False)]
    pills_right(cr, t, hits, w - 20, h - 72, more + [
        ("Cancel", lambda: (setattr(ui, "reviewing", False), ui.redraw()), False),
        ("Add %d" % len(keep), ui.add_earlier if keep else None, True)])


def turn_on(ui):
    """the licence is chosen once per donation: asked when none is started"""
    if ui.donation[0]:
        ui.donating(True)
    else:
        ui.picking_licence = True
        ui.redraw()


def ask(ui, question, yes, fn):
    ui.confirm = (question, yes, fn)
    ui.redraw()


def confirm_sheet(cr, ui, hits, w, h):
    t = ui.theme
    q, yes, fn = ui.confirm
    cr.set_source_rgba(*rgba(t.paper, 0.85))
    cr.paint()
    hits.boxes.clear()
    bh = 190
    PT.box(cr, 20, h - bh - 20, w - 40, bh, "12px", bg=t.paper, shadow="inset 0 0 0 1.5px " + t.ink)
    PT.text(cr, q, t.row, 40, h - bh, t.ink, maxw=w - 80, wrap=True)
    pills_right(cr, t, hits, w - 40, h - 92, [("Cancel", lambda: (setattr(ui, "confirm", None), ui.redraw()), False),
                                             (yes, fn, True)])


PAGES = {"samples": samples_page, "coverage": coverage_page, "learn": learn_page, "donate": donate_page}


def loading_page(cr, ui, hits, w, h):
    """before the first values are in: nothing that looks like a fact"""
    t = ui.theme
    PT.fill(cr, 0, 0, w, h, t.paper)
    header(cr, ui, hits, w)
    PT.text(cr, ui.msg or "Loading…", t.row, 20, 84, t.soft, maxw=w - 40, wrap=True)


def paint_full(cr, ui, hits, w, h):
    if ui.sets_open:
        sets_page(cr, ui, hits, w, h)
    elif not ui.loaded or (ui.page == "coverage" and not ui.cov_loaded):
        loading_page(cr, ui, hits, w, h)
    else:
        PAGES[ui.page](cr, ui, hits, w, h)
    if ui.confirm:
        confirm_sheet(cr, ui, hits, w, h)


# ---------------------------------------------------------------- widgets


class Panel(Gtk.DrawingArea):
    """a self-drawn part of the window; taps act on release"""

    def __init__(self, paint):
        super().__init__()
        self.paint = paint
        self.hits = Hits()
        self.down = None
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.TOUCH_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", lambda w, e: self.press(e.x, e.y))
        self.connect("button-release-event", lambda w, e: self.release(e.x, e.y))
        self.connect("touch-event", self.on_touch)

    def on_draw(self, w, cr):
        a = self.get_allocation()
        self.hits = Hits()
        self.paint(cr, self.hits, a.width, a.height)
        return True

    def press(self, x, y):
        self.down = self.hits.find(x, y)[0]
        log("tap down", round(x), round(y), self.down)
        return True

    def release(self, x, y):
        box, fn = self.hits.find(x, y)
        log("tap up", round(x), round(y), box, "acts" if fn and box == self.down else "nothing")
        # a repaint between press and release makes new functions for the same boxes:
        # the box decides
        if fn and box is not None and box == self.down:
            fn()
        self.down = None
        return True

    def on_touch(self, w, e):
        if e.type == Gdk.EventType.TOUCH_BEGIN:
            return self.press(e.x, e.y)
        if e.type == Gdk.EventType.TOUCH_END:
            return self.release(e.x, e.y)
        return True


class Canvas(Gtk.DrawingArea):
    """the writing area: the keyboard's writing paper; every point the pen reports between
    pen down and pen up, nothing while it hovers"""

    __gtype_name__ = CANVAS_TYPE

    def __init__(self, ui, on_change):
        super().__init__()
        self.ui, self.on_change = ui, on_change
        self.strokes, self.cur = [], None
        self.timer = None
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.TOUCH_MASK)
        self.connect("realize", lambda w: w.get_window().set_event_compression(False))
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("button-release-event", self.on_release)
        self.connect("touch-event", lambda w, e: True)      # fingers don't write (palms rest on it)

    @staticmethod
    def point(e):
        def axis(a):
            # PyGObject returns (found, value) on some versions, the value alone
            # (None when the device has no such axis) on others
            r = e.get_axis(a)
            if isinstance(r, tuple):
                return r[1] if r[0] else None
            return r
        p, tx, ty = axis(Gdk.AxisUse.PRESSURE), axis(Gdk.AxisUse.XTILT), axis(Gdk.AxisUse.YTILT)
        # GTK hands tilt over normalized to -1..1 (from the pen's -90..90 degrees); the
        # recognizer and the sample format take degrees
        tx, ty = (None if v is None else v * 90 for v in (tx, ty))
        return (e.x, e.y, e.time, p, tx, ty)

    def pen(self, e):
        d = e.get_source_device()
        return d is None or d.get_source() != Gdk.InputSource.TOUCHSCREEN

    def on_press(self, w, e):
        log("press", e.button, e.get_source_device().get_source() if e.get_source_device() else None,
            round(e.x), round(e.y))
        if e.button != 1 or not self.pen(e):
            return True
        self.stop_timer()
        self.cur = [self.point(e)]
        self.strokes.append(self.cur)
        self.queue_draw()
        return True

    def on_motion(self, w, e):
        if self.cur is None:
            return True                   # hover (the pen above the screen): not stored
        p = self.point(e)
        last = self.cur[-1]
        self.cur.append(p)
        self.queue_draw_area(int(min(last[0], p[0])) - 4, int(min(last[1], p[1])) - 4,
                             int(abs(p[0] - last[0])) + 8, int(abs(p[1] - last[1])) + 8)
        return True

    def on_release(self, w, e):
        if self.cur is None:
            return True
        self.cur.append(self.point(e))
        log("release", len(self.cur), "points,", len(self.strokes), "strokes")
        self.cur = None
        self.on_change()
        p = self.ui.writer.prompt if self.ui.writer else None
        self.timer = GLib.timeout_add(PAUSE_MS.get(p.kind if p else "word", 1500), self.paused)
        return True

    def stop_timer(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None

    def paused(self):
        """a pause: submitted when the ink already fits the whole prompt, else it waits for
        more ink or Next"""
        self.timer = None
        if not self.strokes or self.cur is not None:
            return False
        if self.ui.busy:                  # look again once the recognizer is free
            self.timer = GLib.timeout_add(500, self.paused)
            return False
        strokes, units, area = self.payload()
        n = len(self.strokes)
        p = self.ui.writer.prompt

        def fits(score):
            log("pause: fit", score)
            # still the same ink (Next clears it) to the same prompt (Redo brings one back)
            if score is not None and score >= 0.6 and len(self.strokes) == n and self.cur is None \
                    and self.ui.writer.prompt is p:
                self.submit()
        self.ui.probe(strokes, units, area, fits)
        return False

    def clear(self):
        self.stop_timer()
        self.strokes, self.cur = [], None
        self.queue_draw()
        self.on_change()

    def payload(self):
        """the strokes with one width for every point (pressure, tilt only when every point
        has them), the units with the scale and the report rate, and the area"""
        pts = [p for s in self.strokes for p in s]
        has_p = all(p[3] is not None for p in pts)
        has_t = has_p and all(p[4] is not None and p[5] is not None for p in pts)
        k = 6 if has_t else 4 if has_p else 3
        strokes = [[p[:k] for p in s] for s in self.strokes]
        dts = [b[2] - a[2] for s in self.strokes for a, b in zip(s, s[1:]) if b[2] > a[2]]
        rate = round(1000 / statistics.median(dts)) if len(dts) >= 8 else 0
        units = "device:%s" % client.fmt(round(self.per_mm(), 4)) + (":%d" % rate if rate else "")
        a = self.get_allocation()
        rot = read_file(ROTATION_FILE)
        rot = int(rot) if rot in ("0", "90", "180", "270") else 0
        return strokes, units, (0, 0, a.width, a.height, LINE, rot)

    def per_mm(self):
        """logical px per mm of the screen the window is on (whatever its rotation)"""
        win = self.get_window()
        disp = Gdk.Display.get_default()
        mon = disp.get_monitor_at_window(win) if win else disp.get_primary_monitor()
        if not mon:
            return 1.0
        g = mon.get_geometry()
        wmm, hmm = mon.get_width_mm(), mon.get_height_mm()
        if wmm <= 0 or hmm <= 0:
            return 1.0
        return math.sqrt(g.width * g.height / (wmm * hmm))

    def submit(self):
        """Next: the next prompt and an empty page at once; the ink goes to the recognizer,
        whose result shows at the top with the ink (the worker reads one call after the other)"""
        wr = self.ui.writer
        if not self.strokes or self.cur is not None or not wr or not wr.prompt:
            return
        self.stop_timer()
        strokes, units, area = self.payload()
        p = wr.take()
        log("submit", len(strokes), "strokes", units)
        self.clear()
        self.ui.submit(strokes, units, area, p)

    def on_draw(self, w, cr):
        t = self.ui.theme
        a = self.get_allocation()
        PT.fill(cr, 0, 0, a.width, a.height, t.paper)
        guides(cr, t, a.width, a.height, LINE)
        cr.set_source_rgba(*rgba(t.ink))
        cr.set_line_width(max(2.0, LINE / 110 * 1.5))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for s in self.strokes:
            cr.move_to(s[0][0], s[0][1])
            for p in s[1:]:
                cr.line_to(p[0], p[1])
            if len(s) == 1:
                cr.line_to(s[0][0] + 0.1, s[0][1])
            cr.stroke()
        return True


class Window(Gtk.ApplicationWindow):
    def __init__(self, app, ui):
        super().__init__(application=app, title="Handwriting")
        self.ui = ui
        self.set_default_size(W0, H0)
        self.stack = Gtk.Stack()
        self.add(self.stack)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.canvas = Canvas(ui, lambda: self.bottom.queue_draw())
        self.top = Panel(lambda cr, hits, w, h: write_top(cr, ui, hits, w, h))
        self.top.set_size_request(-1, 300)
        self.bottom = Panel(lambda cr, hits, w, h: write_bottom(cr, ui, hits, w, h, self.canvas))
        self.bottom.set_size_request(-1, 110)
        box.pack_start(self.top, False, True, 0)
        box.pack_start(self.canvas, True, True, 0)
        box.pack_start(self.bottom, False, True, 0)
        self.full = Panel(lambda cr, hits, w, h: paint_full(cr, ui, hits, w, h))
        self.stack.add_named(box, "write")
        self.stack.add_named(self.full, "full")

    def redraw(self):
        writing = self.ui.page == "write" and not self.ui.sets_open and not self.ui.confirm
        self.stack.set_visible_child_name("write" if writing else "full")
        for w in (self.top, self.bottom, self.full):
            w.queue_draw()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.win = None

    def do_activate(self):
        if not self.win:
            self.ui = UI(lambda: self.win and self.win.redraw())
            self.win = Window(self, self.ui)
            self.win.show_all()
            self.win.redraw()
            self.watch_theme()
        self.win.present()

    def do_shutdown(self):
        if self.win:
            if self.ui.work and not self.ui.work.drain(60):
                print("glassine-handwriting-samples: exit before the last samples were kept", file=sys.stderr, flush=True)
            self.ui.rec.close()
        Gtk.Application.do_shutdown(self)

    def watch_theme(self):
        self.mon = Gio.File.new_for_path(THEME_FILE).monitor_file(Gio.FileMonitorFlags.NONE, None)

        def changed(*a):
            name = read_file(THEME_FILE)
            if name and name != self.ui.theme.name:
                self.ui.theme = Theme(name)
                self.win.redraw()
                self.win.canvas.queue_draw()
        self.mon.connect("changed", changed)


def render(argv):
    """--render OUT.png [--page P] [--theme T] [--size WxH]: the demo state on one page"""
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    W, H = (int(v) for v in opt("--size", "%dx%d" % (W0, H0)).split("x"))
    ui = UI(lambda: None, demo=True)
    ui.theme = Theme(opt("--theme", "white"))
    page = opt("--page", "write")
    ui.counts, ui.fresh = (23, 312), 41
    ui.plan = {"train": 250, "held": 62, "new": 41, "min_train": 40, "min_held": 10, "charging": True}
    ui.adapts = {"current": {"time": 1763200000, "train": 210, "held": 52, "before": 0.182, "after": 0.121},
                 "previous": None}
    ui.donation = ("CC0-1.0", 57, "3f2a9c1e-5b7d-4e2a-9f10-6c8d2b4a7e31")
    ui.state.donating = True
    ui.samples = [("%016x" % i, 1763200000 + 60 * i, w) for i, w in enumerate(
        ["quiet", "Jackdaws love my big sphinx of quartz.", "jump", "Q", "7:30", "o'clock"] * 3)]
    if page == "sets":
        ui.sets_open, page = True, "write"
    if page in ("donate-earlier", "donate-review"):
        ui.earlier = [("%016x" % (99 + i), 1762900000 + 3600 * i, w) for i, w in enumerate(
            ["The quick brown fox jumps over the lazy dog", "zebra", "minimum", "fifty", "1984"] * 3)]
        ui.earlier_out = {ui.earlier[1][0]}
        ui.reviewing, page = page == "donate-review", "donate"
    if page == "result":
        page, ui.result = "learn", ("result", ["kept", "0.121", "0.094", "250", "62"])
    ui.page = page
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surf)
    hits = Hits()
    if page == "write" and not ui.sets_open:
        top, bottom = 300, 110
        cr.save()
        write_top(cr, ui, hits, W, top)
        cr.restore()
        cr.save()
        cr.translate(0, top)
        PT.fill(cr, 0, 0, W, H - top - bottom, ui.theme.paper)
        guides(cr, ui.theme, W, H - top - bottom, LINE)
        cr.restore()
        cr.save()
        cr.translate(0, H - bottom)
        write_bottom(cr, ui, hits, W, bottom, None)
        cr.restore()
    else:
        paint_full(cr, ui, hits, W, H)
    surf.write_to_png(opt("--render"))


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if "--render" in argv:
        render(argv)
        return 0
    return App().run([argv[0]])
