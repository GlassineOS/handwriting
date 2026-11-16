# SPDX-License-Identifier: GPL-2.0-or-later
"""The window: one GTK 3 window that draws itself (cairo + Pango in the shell's voices, as the
settings app does): pages instead of scrolling, taps act on release, no animations. The
writing area is its own widget, so the session's ink protocol gets exactly its box through the
GTK ink module (GLASSINE_INK_WIDGETS names its type) and ink appears under the pen.

  glassine-handwriting-samples                 open (or raise) the app
  glassine-handwriting-samples --render OUT.png [--screen S] [--theme T] [--size WxH]
                                               draw one of the design's screens (SCREENS)
                                               into a PNG, from made-up data (no display, no
                                               recognizer, no store)
"""
import collections
import itertools
import math
import os
import queue
import random
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

from glassine_ui import paint as PT  # noqa: E402

from . import APP_ID, client, flow, fontview, neat as N, pages, prompts as P, writers as WR  # noqa: E402
from .look import Hits, Look, draw_ink, guides  # noqa: E402

W0, H0 = 702, 884
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
THEME_FILE = RUN + "/glassine-shell.theme"
ROTATION_FILE = "/run/glassine-rotation"
LINE = float(os.environ.get("GLASSINE_HANDWRITING_LINE", "150"))   # the writing paper's line height
NEAT_PAUSE_MS = 900                 # Neaten my writing: a pause this long neatens the page
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
def read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def documents_dir():
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
    return d or os.path.join(os.path.expanduser("~"), "Documents")


# where a written contribution goes, in the person's Documents
CONTRIB_DIR = "Handwriting contributions"


def free_name(folder, stem, ext):
    os.makedirs(folder, exist_ok=True)
    k = 1
    while os.path.exists(os.path.join(folder, f"{stem}-{k}{ext}")):
        k += 1
    return os.path.join(folder, f"{stem}-{k}{ext}")


def writer_counts(cli):
    """{id: (samples, contribution not saved, contributing)} of the guest stores, for the list"""
    return {wid: (n, d, flow.State(flow.state_path(wid)).donating) for wid, (n, d) in cli.writers().items()}


def save_contribution(cli, state, lic):
    """one file per contribution, in the same folder and named the same for every writer
    (spec 002 FR-021); the next contribution starts empty, with the same licence and number"""
    path = free_name(os.path.join(documents_dir(), CONTRIB_DIR), "handwriting-contribution", ".jsonl")
    cli.donation_write(path)
    cli.donation_erase()
    cli.donation_start(lic)
    state.files += 1
    state.save()
    return path


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


def new_sheet(c):
    """a character's sheet: "seg" (versions, samples, write, pick), "occ" (letters samples, None
    until read), "page", "undo" (the sample just left out), "changed" (relearn it on close),
    "pick", "picks"; one sample opened (FR-033): "open" ((id, place) or None), "vlist" (the
    version whose samples are listed, or None), "vpage", "asking" (Delete's question), "redo"
    (Write it again: None, or {"busy", "shown": the failed check, "old": the sample's line}),
    "redone" (the sample shown took an old one's place)"""
    return {"char": c, "seg": None, "occ": None, "page": 0, "undo": None, "changed": False, "pick": None, "picks": 0,
            "open": None, "vlist": None, "vpage": 0, "asking": False, "redo": None, "redone": False}


class UI:
    """the pages' state and actions; redraw() repaints. The pages (pages.py) read this
    state and call these actions; none of them talks to the recognizer itself."""

    INKS = 64                           # samples' strokes kept for the grids

    # the session shown (spec 002 FR-015): its recognizer, command line, place and prompts
    rec = property(lambda self: self.session.rec, lambda self, v: setattr(self.session, "rec", v))
    cli = property(lambda self: self.session.cli, lambda self, v: setattr(self.session, "cli", v))
    state = property(lambda self: self.session.state, lambda self, v: setattr(self.session, "state", v))
    writer = property(lambda self: self.session.writer, lambda self, v: setattr(self.session, "writer", v))
    guest = property(lambda self: self.session.guest)

    def __init__(self, redraw, demo=False, kw=None):
        self.redraw = redraw
        self.kw = kw or {}              # the recognizer's binary and environment for new sessions
        # the app opens in the owner's session, also after it was closed in a guest's (FR-015)
        self.session = self.owner = WR.Session(writer=False, **self.kw)
        self.writers = WR.Writers(os.devnull if demo else None)
        self.page = "write"
        self.tab = "write"
        self.theme = Look(read_file(THEME_FILE) or "white")
        self.msg = ""
        self.busy = 0                   # calls queued or running in the worker
        self.on_switch = None           # the window's: the writing area emptied for the next session
        self.wcounts = {}               # {writer id: (samples, contribution not saved)} for the list
        self.naming = None              # {"kind": add|rename, "id", "text"}: a writer's name being typed
        self.name_text = None           # the window's private name field, read when the name is used
        self.name_rect = None           # where the dialog's field is (pages.name_dialog)
        self.removing = None            # the Remove question: {"id", "name", "samples", "unsaved", "donor", "saved"}
        self.done = None                # after Finish: {"id", "name", "saved", "samples"}
        self.adult = False              # the guest's 18-or-older box: ticked on screen only, kept nowhere
        self.gflow = None               # a guest's age and licence steps: "intro" or "tab" (from Contribute)
        self.loaded = demo              # counts, plan, donation, learning, adaptations are in
        self.cov_loaded = demo          # the coverage is read
        self.refreshing = False         # a refresh is queued or running
        self.again = False              # and another one is wanted after it
        self.counts = (0, 0)
        self.fresh = 0
        self.samples = []               # [(id, time, text)]
        self.sp = 0                     # samples page
        self.detail = None              # (id, sample dict)
        self.asking_delete = False
        self.inks = collections.OrderedDict()   # {id: sample dict}: what the grids show
        self.ink_wanted = set()         # being fetched
        self.donated = set()            # ids in the donation
        self.marked = {}                # {id: (written, pair)}
        self.prefs = []                 # [(chosen, other, cchar, ochar)]
        self.plan = None
        self.adapts = {}
        self.adapting = None            # True while a run goes
        self.adapt_plan = None          # (learns from, kept aside) of the run
        self.adapt_round = None         # (round, rounds)
        self.waiting_charge = False
        self.result = None
        self.donation = (None, 0, None)
        self.learning = True
        self.picking_licence = False
        self.licence_pick = None        # the licence card picked, not yet confirmed
        self.saved = None               # the donation file just written
        self.earlier = []               # [(id, time, text)] kept before donating was on, to offer
        self.earlier_out = set()        # ids the person leaves out
        self.reviewing = False          # the list of the earlier samples is open
        self.ep = 0                     # its page
        self.confirm = None             # (title, text, yes-label, fn) for a destructive action
        self.sets_open = False
        self.best = None                # Learn's Your handwriting: None, "intro", "pick", "font"
        self.font = None                # the font page: {"status": cli.letters(), "model": fontview.parse()}
        self.font_busy = None           # "making" while the letter model and the font are built
        self.font_name = None           # a name chosen before the font is installed
        self.letters_job = None         # (done, total) while the letter model is built
        self.letters_loading = 0        # letters status reads running (off the worker)
        self.letters_then = []          # what waits for the build running now
        self.pick = None                # {"a": (id, strokes, k), "b": ..., "chosen": i}
        # the character sheet over the font page (spec 003 FR-031, FR-033): new_sheet()
        self.sheet = None
        self.relearn = None             # (char, done, total) while one character is learned again
        self.relearn_then = []          # what waits for it
        self.renaming = None            # {"text"}: the font's name being typed
        self.entry_text = None          # the window's text field, read when the name is used
        self.rename_rect = None         # where the dialog's field is (pages.rename_dialog)
        self.pick_loading = False
        self.neat = None                # Neaten my writing: a neat.Neat while the page is open
        self.neat_strength = N.DEFAULT
        self.neat_orig = False          # the original shown instead
        self.neat_shown = {}            # {stroke index: [(x, y)]}: the neatened strokes
        self.neat_text = ""             # what was read, line after line
        self.neat_gen = 0               # counts the clears: a result for ink cleared since is dropped
        self.neat_busy = False
        self.neat_again = None          # the payload to neaten once the running call is done
        self.pen_ink = None             # a neat.PenInk while the page goes through the pen interface
        self.wstep = "w-welcome"        # the introduction's step
        self.tried = False              # its one prompt was written
        self.demo = demo
        self.work = Worker() if not demo else None
        if not demo:
            self.writer = flow.Writer(self.rec, self.cli, self.state)
            self.refresh()

    def view_reset(self):
        """a session's pages as they start: nothing of the session before stays on screen"""
        self.counts, self.fresh, self.samples, self.sp, self.detail = (0, 0), 0, [], 0, None
        self.asking_delete = False
        self.inks.clear()
        self.ink_wanted = set()
        # the learning run's state and result stay: they are the owner's, shown on Learn alone
        self.donated, self.marked, self.prefs, self.plan, self.adapts = set(), {}, [], None, {}
        self.donation, self.learning = (None, 0, None), True
        self.picking_licence, self.licence_pick, self.saved = False, None, None
        self.earlier, self.earlier_out, self.reviewing, self.ep = [], set(), False, 0
        self.confirm, self.sets_open, self.best, self.pick, self.pick_loading = None, False, None, None, False
        self.msg, self.loaded, self.cov_loaded = "", False, False
        self.waiting_charge = False     # a learning run waiting for the charger is called off
        self.wstep, self.tried, self.adult, self.gflow = "w-welcome", False, False, None
        self.naming = self.removing = None
        self.page = self.tab = "write"

    # ---------------------------------------------------------- background

    def run(self, fn, done=None):
        """fn in the worker; done(r) on the main loop, only while the session that asked is
        still the one shown (fn binds the session's objects itself, when it is queued)"""
        if self.demo:
            return
        self.busy += 1
        s = self.session

        def fin(r, err):
            self.busy -= 1
            if err:
                print("glassine-handwriting-samples: error: %s" % err, file=sys.stderr, flush=True)
                if s is self.session:
                    self.msg = "Something went wrong: %s" % err
            elif done and s is self.session:
                done(r)
            self.redraw()
        self.work.do(fn, fin)

    def shown_state(self):
        """what the pages show from a refresh: a refresh that changes none of it repaints
        nothing (each repaint is a refresh of the e-ink panel)"""
        return (self.counts, self.plan, self.donation, self.learning, self.adapts, self.samples, self.earlier,
                self.fresh, self.loaded, self.cov_loaded, self.reviewing, self.donated, self.marked, self.prefs,
                self.page, self.wstep, len(self.writer.counted) if self.writer else 0, self.wcounts)

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
        ss = self.session

        def get():
            t = time.monotonic()
            rec, cli, st = ss.rec, ss.cli, ss.state
            rows = rec.list()
            ss.writer.set_coverage(rows)
            don = cli.donation()
            earlier = cli.donation_earlier() if st.donating and don[0] else []
            donated = cli.donation_ids() if don[0] else set()
            short = [(x[0], x[1], x[4]) for x in rows]
            # a guest store has no adaptation and no learning switch (spec 002 FR-018); the
            # owner's session lists the other writers' stores with their counts (FR-014)
            g = ss.guest
            r = (ss.writer.counts(rows=short), None if g else cli.plan(st.lang), don, True if g else cli.learning(),
                 {} if g else cli.adaptations(st.lang), short, earlier, donated, rec.marked(), rec.preferences(),
                 {} if g else writer_counts(cli))
            log("refresh %.0f ms, %d samples" % ((time.monotonic() - t) * 1000, len(rows)))
            return r

        def got(r, err):
            self.refreshing = False
            if ss is not self.session:          # a session left meanwhile: its readings aren't shown
                if self.again:
                    self.refresh()
                return
            before = self.shown_state()
            if err:
                print("glassine-handwriting-samples: error: %s" % err, file=sys.stderr, flush=True)
                self.msg = "Something went wrong: %s" % err
            else:
                (self.counts, self.plan, self.donation, self.learning, self.adapts, self.samples, self.earlier,
                 self.donated, self.marked, self.prefs, self.wcounts) = r
                self.earlier_out &= {e[0] for e in self.earlier}
                if not self.earlier:
                    self.reviewing = False
                self.fresh = self.plan["new"] if self.plan else 0
                first = not self.loaded
                self.loaded = self.cov_loaded = True
                if first:
                    self.first_start()
            if err or self.shown_state() != before:
                self.redraw()
            if self.again:
                self.refresh()
        self.work.do(get, got, low=True)

    def first_start(self):
        if self.guest:
            # a guest's introduction, the first time their session opens (FR-019): shown once,
            # also when they finish before its end
            if not self.state.introduced:
                self.state.introduced = True
                self.state.save()
                self.page, self.wstep, self.gflow = "wizard", "g-hello", "intro"
            return
        if flow.needs_intro(self.state, self.counts[1]):
            self.page = "wizard"
            self.wstep = "w-welcome"

    # ---------------------------------------------------------- other writers (spec 002 FR-014 to FR-025)

    def enter(self, session):
        """a session shown in the window: its pages start afresh, the writing area empty"""
        self.close_neat()
        if self.sheet:
            self.sheet = None
        self.renaming = None
        self.session = session
        self.view_reset()
        if self.on_switch:
            self.on_switch()
        self.refresh()
        self.redraw()

    def open_others(self):
        """Someone else writes: the list of writers, from the owner's session only"""
        if self.guest:
            return
        self.go("others")

    def open_writer(self, wid):
        """a writer's session: their store named in every request, their place in the prompts"""
        name = self.writers.name(wid)
        if self.guest or name is None or self.demo:
            return
        self.done = None
        self.enter(WR.Session(wid, name, **self.kw))

    def finish(self):
        """Finish (FR-022): back to the owner's session in one step. The guest's calls queued
        before it go first; then an unsaved contribution is saved as a file (the next one starts
        empty, with the same licence and number), and their recognizer stops"""
        s = self.session
        if not s.guest:
            return
        self.done = {"id": s.wid, "name": s.name, "saved": None, "samples": None, "busy": True,
                     "donating": s.state.donating}
        done = self.done

        def go():
            try:
                lic, n, _ = s.cli.donation()
                saved = None
                if s.state.donating and lic and n:
                    saved = save_contribution(s.cli, s.state, lic)
                return saved, len(s.cli.samples())
            finally:
                s.close()

        def fin(r, err):
            self.busy -= 1
            done["busy"] = False
            if err:
                print("glassine-handwriting-samples: error: %s" % err, file=sys.stderr, flush=True)
                done["error"] = str(err)
            else:
                done["saved"], done["samples"] = r
            self.refresh()
            self.redraw()
        self.busy += 1
        self.work.do(go, fin)
        self.enter(self.owner)

    def close_done(self):
        self.done = None
        self.redraw()

    def ask_name(self, wid=None):
        """Add a writer (wid None) or Rename: the name in a private field (FR-014)"""
        self.naming = {"kind": "rename" if wid else "add", "id": wid, "text": self.writers.name(wid) or ""}
        self.redraw()

    def name_changed(self, text):
        """a repaint only when what the dialog says changes (each one refreshes the panel)"""
        if not self.naming:
            return
        before = self.writers.problem(self.naming["text"], self.naming["id"])
        self.naming["text"] = text
        if self.writers.problem(text, self.naming["id"]) != before:
            self.redraw()

    def name_problem(self):
        n = self.naming
        return self.writers.problem(n["text"], n["id"]) if n else None

    def cancel_name(self):
        self.naming = None
        self.redraw()

    def use_writer_name(self):
        """Add writer: a new store, then the name beside its id in the list; Use this name:
        only the label changes"""
        n = self.naming
        if not n:
            return
        if self.name_text:
            n["text"] = self.name_text()
        if self.writers.problem(n["text"], n["id"]):
            self.redraw()
            return
        self.naming = None
        name = WR.clean(n["text"])
        if n["kind"] == "rename":
            self.writers.rename(n["id"], name)
            self.redraw()
            return
        cli = self.owner.cli
        self.run(cli.writer_new, lambda wid: (self.writers.add(wid, name), self.refresh()))
        self.redraw()

    def ask_remove(self, wid):
        """the Remove question names what goes (FR-023): read from the writer's store first"""
        name = self.writers.name(wid)
        if name is None or self.guest:
            return
        if self.page != "others":
            self.go("others")
        self.done = None

        def get():
            gc = client.Cli(writer=wid, **self.kw)
            lic, n, donor = gc.donation()
            st = flow.State(flow.state_path(wid))
            return {"id": wid, "name": name, "samples": len(gc.samples()), "unsaved": n if lic else 0,
                    "donor": donor, "files": st.files, "saved": None}
        self.run(get, lambda r: setattr(self, "removing", r))

    def save_for_removal(self):
        """Save their contribution first: the question stays open, without that line"""
        rm = self.removing
        if not rm or not rm["unsaved"]:
            return
        wid = rm["id"]

        def go():
            gc = client.Cli(writer=wid, **self.kw)
            return save_contribution(gc, flow.State(flow.state_path(wid)), gc.donation()[0])

        def got(p):
            if self.removing is rm:
                rm["saved"], rm["unsaved"], rm["files"] = p, 0, rm["files"] + 1
        self.run(go, got)

    def cancel_remove(self):
        self.removing = None
        self.redraw()

    def remove_writer(self):
        """the store gone for good when the request returns, then the name leaves the list and
        their place in the prompts goes"""
        rm, self.removing = self.removing, None
        if not rm or self.guest:
            return
        cli = self.owner.cli

        def go():
            cli.writer_remove(rm["id"])
            WR.forget_place(rm["id"])

        def got(_):
            self.writers.remove(rm["id"])
            self.msg = "%s was removed." % rm["name"]
            self.refresh()
        self.run(go, got)
        self.redraw()

    # ---------------------------------------------------------- navigation

    def go(self, page):
        if self.guest and page in ("learn", "neat", "others"):
            page = "write"              # not in a guest's session (spec 002 FR-018)
        self.close_neat()
        self.page = page
        self.tab = page
        self.detail = None
        self.asking_delete = False
        self.confirm = None
        self.msg = ""
        self.sets_open = False
        self.saved = None
        self.picking_licence = False
        self.reviewing = False
        if self.sheet:
            self.close_char()
        self.renaming = None
        if page != "learn":
            self.best = None
            self.pick = None
        if page != "write" and not self.demo:
            self.refresh()
        self.redraw()

    def open_sets(self):
        self.sets_open = True
        self.redraw()

    def close_sets(self):
        self.sets_open = False
        self.redraw()

    def cancel_confirm(self):
        self.confirm = None
        self.redraw()

    def need(self):
        """about how many samples learning starts with"""
        pl = self.plan or {}
        return pl.get("min_train", 40) + pl.get("min_held", 10)

    # ---------------------------------------------------------- the introduction

    def wizard_to(self, step):
        self.wstep = step
        self.msg = ""
        self.adult = False              # asked anew each time the step shows (FR-019)
        if step == "w-try" and self.writer and self.writer.shown and self.tried:
            self.wstep = "w-try"
        self.redraw()

    def wizard_next(self):
        if self.guest:
            return self.guest_next()
        nxt = {"w-welcome": "w-try", "w-try": "w-private", "w-private": "w-learning", "w-learning": "w-share"}
        s = self.wstep
        if s in nxt:
            return self.wizard_to(nxt[s])
        if s == "w-licence" and self.licence_pick:
            self.donating(True, self.licence_pick)
            return self.wizard_to("w-ready")
        if s == "w-ready":
            return self.wizard_done()

    def wizard_decline(self):
        """Not now: donating stays off; no licence, no donor ID"""
        if self.guest:
            return self.guest_decline()
        self.licence_pick = None
        if self.state.donating:
            self.donating(False)
        self.wizard_to("w-ready")

    def wizard_skip(self):
        if self.guest:
            return self.guest_done()
        self.wizard_done()

    # a guest's introduction (FR-019): Hello by name, Try one, then the question asked of the
    # writer themselves, 18 or older, the licence. From Contribute only the last two

    GUEST_NEXT = {"g-hello": "w-try", "w-try": "w-share", "w-share": "g-age"}
    GUEST_BACK = {"w-try": "g-hello", "w-share": "w-try", "g-age": "w-share", "w-licence": "g-age"}

    def guest_next(self):
        s = self.wstep
        if s in self.GUEST_NEXT:
            return self.wizard_to(self.GUEST_NEXT[s])
        if s == "g-age" and self.adult:
            if self.donation[0]:            # a licence chosen before: on again with it
                self.donating(True)
                return self.guest_done()
            self.licence_pick = None
            return self.wizard_to("w-licence")
        if s == "w-licence" and self.licence_pick:
            self.donating(True, self.licence_pick)
            return self.guest_done()

    def guest_back(self):
        if self.gflow == "tab" and self.wstep == "g-age":
            return self.go("donate")
        self.wizard_to(self.GUEST_BACK.get(self.wstep, "g-hello"))

    def guest_decline(self):
        """Not now, at the question or at 18 or older: contributing stays off, the writer goes
        on writing (or back to Contribute, which asks again)"""
        self.licence_pick = None
        self.adult = False
        if self.state.donating:
            self.donating(False)
        self.guest_done()

    def guest_done(self):
        self.adult = False
        self.go("donate" if self.gflow == "tab" else "write")
        self.gflow = None

    def toggle_adult(self):
        self.adult = not self.adult
        self.redraw()

    def wizard_done(self):
        self.state.introduced = True
        if not self.demo:
            self.state.save()
        self.go("write")

    def pick_licence(self, key):
        self.licence_pick = key
        self.redraw()

    # ---------------------------------------------------------- writing

    def submit(self, strokes, units, area, prompt):
        """the ink written to prompt (taken by Next, the next prompt is already up) is read
        and kept in the background"""
        if self.page == "wizard":
            self.tried = True
        wr = self.writer                # the session it was written in keeps it (FR-016)

        def go():
            return wr.submit(strokes, units, area, prompt)

        def fin(sh):
            log("kept" if sh else "nothing", sh and (sh.status, sh.sample_id, sh.why, sh.fit,
                                                     sh.reading[:1] if sh.reading else None))
            note(sh)
            self.msg = ""
            if sh and sh.status == "kept" and sh.sample_id and self.sheet:
                self.sheet_changed()
                self.sheet["occ"] = None
                self.load_font()
            if sh and sh.status == "kept":
                self.counts = (self.counts[0] + bool(sh.sample_id), self.counts[1] + bool(sh.sample_id))
                self.fresh += bool(sh.sample_id)
                if sh.sample_id and (sh.prompt.written or sh.prompt.pair):
                    self.marked = dict(self.marked)
                    self.marked[sh.sample_id] = (sh.prompt.written, sh.prompt.pair)
                    self.samples = self.samples + [(sh.sample_id, int(time.time()), sh.prompt.text)]
                if sh.donated_id:
                    self.donation = (self.donation[0], self.donation[1] + 1, self.donation[2])
        self.run(go, fin)

    def probe(self, strokes, units, area, done):
        """at a pause: does the ink already fit the whole prompt? (then it is submitted)"""
        p, rec = self.writer.prompt, self.rec

        def go():
            rec.write("p", p.lang, units, area, strokes)
            try:
                return rec.align("p", p.text)
            finally:
                rec.end("p")
        self.work.do(go, lambda r, err: done(r if not err else None))

    def act(self, name):
        fn = getattr(self.writer, name)
        was = self.writer.shown
        if self.page == "wizard" and name == "redo":
            self.tried = False

        def fin(_):
            if was and name == "keep_anyway":
                note(was)
            elif was:
                print("glassine-handwriting-samples: %s sample: %s (%s)" % (
                    was.prompt.set, name, was.sample_id or "nothing kept"), file=sys.stderr, flush=True)
            self.refresh()
        self.run(fn, fin)

    def skip(self):
        self.act("skip")

    def choose_set(self, name):
        """a prompt set; in a guest's session also Write it your best and Which one is nicer?,
        which are sets there (spec 002 FR-018: no Learn tab)"""
        self.sets_open = False
        if name == "best":
            return self.start_best()
        if name == "pick":
            self.go("write")
            self.page = "learn"
            return self.start_pick()
        wr = self.writer

        def go():
            if wr.best is not None:
                wr.stop_best()
            return wr.choose_set(name)
        self.run(go)
        self.redraw()

    def practise(self):
        """Coverage's Practise these: the prompts that favour the weak characters"""
        self.choose_set(P.AUTO)
        self.go("write")

    def careful_today(self):
        day0 = flow.day_start()
        return sum(1 for sid, t, _ in self.samples if t >= day0 and self.marked.get(sid, ("",))[0] == "careful")

    # ---------------------------------------------------------- samples

    def samples_page(self, d):
        self.sp += d
        self.redraw()

    def want_inks(self, ids):
        """the grids' samples: fetched in the background (only the ones on the page, never all
        of them), repainted once they are in"""
        if self.demo:
            return
        miss = [i for i in ids if i not in self.inks and i not in self.ink_wanted]
        if not miss:
            for i in ids:
                if i in self.inks:
                    self.inks.move_to_end(i)
            return
        self.ink_wanted |= set(miss)
        rec = self.rec

        def get():
            out = {}
            for i in miss:
                try:
                    out[i] = rec.sample(i)
                except client.Error:
                    out[i] = {}
            return out

        def got(r, err):
            self.ink_wanted -= set(miss)
            if r and rec is self.rec:
                self.inks.update(r)
                while len(self.inks) > self.INKS:
                    self.inks.popitem(last=False)
                self.redraw()
        self.work.do(get, got, low=True)

    def open_sample(self, sid):
        self.asking_delete = False
        rec = self.rec
        self.run(lambda: rec.sample(sid), lambda s: setattr(self, "detail", (sid, s)))

    def close_sample(self):
        self.detail = None
        self.asking_delete = False
        self.redraw()

    def ask_delete(self):
        self.asking_delete = True
        self.redraw()

    def keep_sample(self):
        self.asking_delete = False
        self.redraw()

    def delete_sample(self, sid, then=None):
        """from the samples and from the donation (Writer.delete; the character sheet's Delete
        is this one, spec 002 FR-009)"""
        wr = self.writer
        self.detail = None
        self.asking_delete = False
        self.inks.pop(sid, None)
        self.run(lambda: wr.delete(sid), lambda _: (then and then(), self.refresh()))

    def ask_erase_all(self):
        """the session's own samples only (spec 002 FR-024): the owner's leaves every other
        writer's store as it is, a guest's leaves the owner's and everyone else's"""
        n = len(self.samples)
        text = ("They’re removed from this tablet for good. Your contribution keeps its copies until you erase it in "
                "Contribute.")
        if not self.guest:
            text += " What it has already learned stays until it learns again. Other writers’ samples are kept."
        self.confirm = ("Erase all %d samples?" % n, text, "Erase all", self.erase_all)
        self.redraw()

    def erase_all(self):
        self.confirm = None
        cli, wr = self.cli, self.writer

        def go():
            n = cli.erase()
            wr.forgotten()
            return n
        self.inks.clear()
        self.run(go, lambda n: (setattr(self, "msg", "%d samples erased." % n), self.refresh()))

    def export(self):
        path = free_name(os.path.join(documents_dir(), "Handwriting"), "handwriting-samples", ".jsonl")
        cli = self.cli
        self.run(lambda: cli.export(path), lambda _: setattr(self, "msg", "Saved in Documents › Handwriting as " +
                                                                  os.path.basename(path)))

    def import_latest(self):
        if self.guest:                  # a guest store takes no import (docs/recognizer.md)
            return
        folder = os.path.join(documents_dir(), "Handwriting")
        try:
            files = sorted((os.path.join(folder, f) for f in os.listdir(folder)
                            if f.startswith("handwriting-samples-") and f.endswith(".jsonl")), key=os.path.getmtime)
        except OSError:
            files = []
        if not files:
            self.msg = "No saved samples in Documents › Handwriting."
            self.redraw()
            return
        cli = self.cli
        self.run(lambda: cli.import_(files[-1]),
                 lambda n: (setattr(self, "msg", "%d samples added from %s" % (n, os.path.basename(files[-1]))),
                            self.refresh()))

    def donate_one(self, sid):
        cli = self.cli
        self.run(lambda: cli.donation_add(sid),
                 lambda _: (setattr(self, "msg", "Added to your contribution."), self.refresh()))

    # ---------------------------------------------------------- learning

    def adapt(self):
        if self.guest:
            return

        def got(charging):
            if charging:
                self.waiting_charge = False
                self.start_adapt()
            else:
                self.waiting_charge = True
                GLib.timeout_add_seconds(10, self.poll_charge)
        self.run(self.cli.charging, got)

    def poll_charge(self):
        if not self.waiting_charge:
            return False
        self.work.do(self.cli.charging, lambda r, err: (r and self.waiting_charge and self.adapt()))
        return False

    def start_adapt(self):
        """a run in its own thread (it takes minutes; the writing's requests must not wait for
        it); the page shows its plan and rounds as the run reports them"""
        self.result = None
        self.adapting = True
        self.adapt_plan = self.adapt_round = None
        cli, lang = self.cli, self.state.lang   # the owner's, also when a guest's session opens meanwhile

        def line(kind, f):
            if kind == "plan":
                self.adapt_plan = (int(f[0]), int(f[1]))
            elif kind == "epoch":
                self.adapt_round = (int(f[0]), int(f[1]))
            GLib.idle_add(lambda: (self.redraw(), False)[1])

        def go():
            try:
                last = cli.adapt(lang, on_line=line)
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

    def ask_revert(self):
        self.confirm = ("Use the version before?", "It goes back to what it read you with before it last learned. "
                        "What it learned last is set aside.", "Use it", self.revert)
        self.redraw()

    def revert(self):
        self.confirm = None
        self.result = None
        cli, lang = self.cli, self.state.lang
        self.run(lambda: cli.revert(lang), lambda _: self.refresh())

    # ---------------------------------------------------------- Your handwriting

    def open_best(self, mode):
        """Learn's Your handwriting (mode "intro"), or back to Learn (None); either way the
        careful writing stops. In a guest's session: back to the sets"""
        if self.guest:
            self.go("write")
            return self.open_sets()
        self.close_neat()
        if self.sheet:
            self.close_char()
        self.renaming = None
        if self.writer and self.writer.best is not None:
            self.run(self.writer.stop_best)
        self.page = self.tab = "learn"
        self.best = mode
        self.pick = None
        self.sets_open = False
        self.redraw()

    def start_best(self):
        self.best = None
        self.run(self.writer.start_best)
        self.go("write")

    def open_font(self):
        """Your font: the letters it has, a preview, what is missing"""
        if self.guest:
            return
        self.best = "font"
        self.msg = ""
        self.sheet = None
        self.redraw()
        self.load_font()

    def letters_bg(self, fn, done):
        """the letter model's calls on a thread of their own: a count or a build of the
        letters can take a while, and the recognizer's worker stays free for writing"""
        def go():
            try:
                r, err = fn(), None
            except Exception as e:      # shown on the page, the app keeps running
                r, err = None, e
            GLib.idle_add(lambda: (done(r, err), False)[1])
        threading.Thread(target=go, daemon=True).start()

    def load_font(self, build=False):
        """the counts and the model as they are now (spec 003 FR-030); build: a missing or
        out-of-date model is built in the background (FR-008)"""
        if self.demo or self.guest:     # the letter model is the owner's (docs/recognizer.md)
            return
        cli = self.cli

        def get():
            st = cli.letters()
            text = cli.letters_model() if st["state"] != "none" else ""
            return {"status": st, "model": fontview.parse(text) if text else None}

        def got(r, err):
            self.letters_loading -= 1
            if err:
                print("glassine-handwriting-samples: error: %s" % err, file=sys.stderr, flush=True)
                self.msg = "Your letters couldn’t be read: %s" % err
            else:
                self.font = r
                st = r["status"]
                if build and st["state"] != "current" and st["samples"] and not self.letters_job:
                    self.build_letters()
            self.redraw()
        self.letters_loading += 1
        self.letters_bg(get, got)

    def build_letters(self, then=None):
        """the letter model built now, at the lowest priority, with its progress on the page;
        never the font (FR-011). then(ok) runs once it is done (also when a build was running)"""
        if then:
            self.letters_then.append(then)
        if self.letters_job or self.demo:
            return
        if self.relearn:                # one character first, then the whole model
            self.relearn_then.append(lambda: self.build_letters())
            return
        self.letters_job = (0, 0)

        def progress(done, total):
            def show():
                if self.letters_job:
                    step = (lambda j: int(10 * j[0] / j[1]) if j[1] else 0)
                    old, self.letters_job = self.letters_job, (done, total)
                    if step(old) != step(self.letters_job):     # a repaint per tenth, no more
                        self.redraw()
                return False
            GLib.idle_add(show)

        def fin(r, err):
            self.letters_job = None
            then, self.letters_then = self.letters_then, []
            if err:
                print("glassine-handwriting-samples: error: %s" % err, file=sys.stderr, flush=True)
                self.msg = "Your letters couldn’t be learned: %s" % err
            if self.best == "font" or self.page == "neat":
                self.load_font()
            for f in then:
                f(not err)
            self.redraw()
        cli = self.cli                  # the owner's: a guest's session may open while it builds
        self.letters_bg(lambda: cli.letters_build(progress), fin)
        self.redraw()

    def letters_progress(self):
        """0 to 1 while the letter model is built, None when it isn't"""
        if not self.letters_job:
            return None
        done, total = self.letters_job
        return done / total if total else 0.0

    def make_font(self):
        """the letter model brought up to date, then the font built and installed (under its
        name; the first time, the name chosen here or the default)"""
        if self.font_busy or self.demo:
            return
        if self.relearn:                # the character being learned again goes in too
            self.relearn_then.append(self.make_font)
            self.font_busy = "waiting"
            self.redraw()
            return
        self.font_busy = "making"
        self.msg = ""
        name, cli = self.font_name, self.cli

        def fin(r, err):
            self.busy -= 1
            self.font_busy = None
            if err:
                print("glassine-handwriting-samples: error: %s" % err, file=sys.stderr, flush=True)
                self.msg = "The font couldn’t be made: %s" % err
            else:
                self.font_name = None
                self.msg = "Installed as %s. It is in the font lists of your apps." % r[0]
                self.load_font()
            self.redraw()

        def built(ok):
            if not ok:
                self.font_busy = None
                return
            self.busy += 1
            self.work.do(lambda: cli.font_install(name), fin)
        self.build_letters(then=built)
        self.redraw()

    def ask_remove_font(self):
        self.confirm = ("Remove your font?", "Apps go back to their usual font where you used it. Your samples stay, "
                        "and you can make the font again any time.", "Remove", self.remove_font)
        self.redraw()

    def remove_font(self):
        self.confirm = None
        self.run(self.cli.font_remove, lambda _: (setattr(self, "msg", "Your font was removed."), self.load_font()))

    def font_shown_name(self):
        st = (self.font or {}).get("status") or {}
        return self.font_name or (st.get("font") or (fontview.DEFAULT_NAME,))[0]

    def rename_font(self):
        """the name, typed in a dialog in the page's upper half (the keyboard takes the lower)"""
        self.renaming = {"text": self.font_shown_name()}
        self.redraw()

    def cancel_rename(self):
        self.renaming = None
        self.redraw()

    def use_name(self):
        """Use this name: an installed font is made again under it (its letters as they are);
        otherwise it is the name the first install uses"""
        if not self.renaming:
            return
        name = (self.entry_text() if self.entry_text else self.renaming["text"]).strip()[:40]
        self.renaming = None
        if not name or name == self.font_shown_name():
            self.redraw()
            return
        st = (self.font or {}).get("status") or {}
        self.font_name = name
        if st.get("font"):
            self.make_font()
        self.redraw()

    def write_missing(self):
        """Write these now: the sheet of the first character the font lacks, on Write more"""
        f = self.font or {}
        miss = fontview.missing(f.get("model"), status=f.get("status"))
        if not miss:
            return
        self.open_char(miss[0])

    # ---------------------------------------------------------- the character sheet

    def char_info(self, c):
        """(usable samples, its line in the model or None, in the font)"""
        f = self.font or {}
        st, model = f.get("status") or {}, f.get("model")
        d = model["chars"].get(c) if model else None
        n = st.get("chars", {}).get(c, (0, 0))[0] if st.get("chars") is not None else int((d or {}).get("samples", 0))
        fd = fontview.font_view(model)["chars"].get(c) if model else None
        return n, d, bool(fd and fd.get("covered") and fd.get("variants"))

    def open_char(self, c):
        """a key of the font page: the sheet of that character, on its versions, or on Write
        more while it has fewer than 3 samples"""
        n, d, _ = self.char_info(c)
        seg = "versions" if d and d.get("variants") else "write" if n < fontview.MIN else "samples"
        self.sheet = new_sheet(c)
        self.sheet_seg(seg)

    def sheet_seg(self, seg):
        sh = self.sheet
        if not sh or sh["seg"] == seg:
            return
        if sh["seg"] == "write" and self.writer and self.writer.best is not None and not self.demo:
            self.run(self.writer.stop_best)
        sh["seg"] = seg
        if seg == "write" and not self.demo:
            n, _, _ = self.char_info(sh["char"])
            prompts = flow.char_prompts(sh["char"], self.writer.sets, self.state.lang, 3 if n < fontview.MIN else 4)
            sh["prompts"] = prompts
            self.writer.start_list(prompts)
        elif seg == "samples" and sh["occ"] is None:
            self.load_occ()
        elif seg == "pick":
            self.char_pick()
        self.redraw()

    def load_occ(self):
        """the character's samples, how each counts (letters samples), and their ink"""
        sh = self.sheet
        if not sh or self.demo:
            return
        c = sh["char"]

        def got(r, err):
            if self.sheet is not sh:
                return
            if err:
                self.msg = "Its samples couldn’t be read: %s" % err
            else:
                sh["occ"] = r
                self.want_inks(sorted({o[0] for o in r}))
            self.redraw()
        cli = self.cli
        self.letters_bg(lambda: cli.letters_samples(c), got)

    def sheet_changed(self):
        if self.sheet:
            self.sheet["changed"] = True

    def close_char(self):
        """the sheet closes: a changed character is learned again in the background, the
        preview shows it once that is done; the installed font stays (FR-032)"""
        sh, self.sheet = self.sheet, None
        if not sh:
            return
        if sh["seg"] == "write" and self.writer and self.writer.best is not None and not self.demo:
            self.run(self.writer.stop_best)
        st = (self.font or {}).get("status") or {}
        if sh["changed"] and st.get("state") != "none":
            self.relearn_char(sh["char"])
        self.load_font()
        self.redraw()

    def relearn_char(self, c):
        if self.demo:
            return
        if self.letters_job:            # after the whole build that runs now
            self.letters_then.append(lambda ok: self.relearn_char(c))
            return
        if self.relearn:
            self.relearn_then.append(lambda: self.relearn_char(c))
            return
        self.relearn = (c, 0, 0)

        def progress(done, total):
            def show():
                if self.relearn:
                    old, self.relearn = self.relearn, (c, done, total)
                    if old[2] == 0 or int(10 * old[1] / old[2]) != int(10 * done / max(total, 1)):
                        self.redraw()
                return False
            GLib.idle_add(show)

        def fin(r, err):
            self.relearn = None
            if err:
                print("glassine-handwriting-samples: error: %s" % err, file=sys.stderr, flush=True)
                self.msg = "%s couldn’t be learned again: %s" % (c, err)
            then, self.relearn_then = self.relearn_then, []
            if self.font_busy == "waiting":
                self.font_busy = None
            self.load_font()
            for f in then:
                f()
            self.redraw()
        cli = self.cli
        self.letters_bg(lambda: cli.letters_build(progress, char=c), fin)
        self.redraw()

    def relearn_progress(self):
        if not self.relearn:
            return None
        _, done, total = self.relearn
        return done / total if total else 0.0

    def _mark(self, kind, ids, then=None):
        c = self.sheet["char"]
        self.sheet_changed()
        if self.demo:
            if then:
                then()
            self.redraw()
            return
        cli = self.cli
        self.run(lambda: cli.letters_mark(kind, c, ids), lambda _: then and then())

    def mark_version(self, v, what):
        """a version's Favourite (again: no favourite), Leave out, Put back"""
        sh = self.sheet
        _, d, _ = self.char_info(sh["char"])
        if not d or v >= len(d.get("variants") or []):
            return
        var = d["variants"][v]
        ids = var.get("ids") or []
        if what == "favourite":
            kind = "clear" if var.get("favourite") else "favourite"
            for x in d["variants"]:
                x.pop("favourite", None)
            if kind == "favourite":
                var["favourite"] = True
        elif what == "out":
            kind = "way-out"
            var["left_out"] = True
            var.pop("favourite", None)
        else:
            kind = "clear"
            var.pop("left_out", None)
        self._drop_view()
        self._mark(kind, ids)
        self.redraw()

    def _drop_view(self):
        m = (self.font or {}).get("model")
        if m:
            m.pop("_view", None)

    def mark_sample(self, sid):
        """a sample's box: left out of the character (with Undo), or put back"""
        sh = self.sheet
        occ = sh.get("occ") or []
        st = next((o[2] for o in occ if o[0] == sid), None)
        if st not in ("usable", "out"):
            return
        new = "out" if st == "usable" else "usable"
        sh["occ"] = [(o[0], o[1], new if o[0] == sid else o[2], *o[3:]) for o in occ]
        sh["undo"] = sid if new == "out" else None
        self._mark("out" if new == "out" else "clear", [sid])
        self.redraw()

    def undo_out(self):
        sh = self.sheet
        if sh and sh.get("undo"):
            self.mark_sample(sh["undo"])
            sh["undo"] = None
            self.redraw()

    def sheet_page(self, p):
        self.sheet["page"] = p
        self.redraw()

    # ---- one of the character's samples (spec 003 FR-033)

    def sheet_sample(self, sid, pos):
        """a tile tapped: that sample opened in the sheet (its whole ink, fetched when the grid
        hasn't got it)"""
        self.sheet.update(open=(sid, pos), asking=False, redo=None, redone=False)
        self.want_inks([sid])
        self.redraw()

    def version_samples(self, v):
        """a version's Show its samples: exactly the samples that form it (its ids)"""
        sh = self.sheet
        sh.update(vlist=v, vpage=0, open=None)
        if sh["occ"] is None:
            self.load_occ()
        self.redraw()

    def version_ids(self, v):
        _, d, _ = self.char_info(self.sheet["char"])
        vs = (d or {}).get("variants") or []
        return list(vs[v].get("ids") or []) if 0 <= v < len(vs) else []

    def sheet_vpage(self, p):
        self.sheet["vpage"] = p
        self.redraw()

    def sheet_back(self):
        """Back: from a sample to the list it was opened from (Samples or the version's), from
        a version's list to the versions"""
        sh = self.sheet
        if sh["open"]:
            sh.update(open=None, asking=False, redone=False)
        else:
            sh["vlist"] = None
        self.redraw()

    def opened(self):
        """the opened sample: (id, place, its line in letters samples or None, its sample dict or
        None while it is fetched)"""
        sh = self.sheet
        if not sh or not sh["open"]:
            return None
        sid, pos = sh["open"]
        o = next((o for o in sh["occ"] or [] if o[0] == sid and o[1] == pos), None)
        return sid, pos, o, self.inks.get(sid)

    def ask_sheet_delete(self, on=True):
        self.sheet["asking"] = on
        self.redraw()

    def sheet_delete(self):
        """Delete in the sheet: the same deletion as in Samples (delete_sample), then the list
        it was opened from, read again"""
        sh = self.sheet
        sid = sh["open"][0]
        sh.update(open=None, asking=False, undo=None if sh["undo"] == sid else sh["undo"])
        sh["occ"] = [o for o in sh["occ"] or [] if o[0] != sid]
        self.sheet_changed()

        def then():
            if self.sheet is sh:
                self.load_occ()
            self.load_font()
        self.delete_sample(sid, then)
        self.redraw()

    def redo_start(self):
        """Write it again: the same text in a writing area inside the sheet (the old sample's
        line held for it)"""
        got = self.opened()
        if not got or not got[3]:
            return
        self.sheet.update(redo={"busy": False, "shown": None, "old": got[3]}, asking=False, redone=False)
        self.msg = ""
        if self.on_switch:              # the area starts empty (Write's own ink stays as it is)
            self.on_switch()
        self.redraw()

    def redo_cancel(self):
        """Cancel: the old sample as it was"""
        if self.on_switch:
            self.on_switch()
        self.sheet["redo"] = None
        self.redraw()

    def redo_again(self):
        """after a failed check: the new ink goes, the old sample still stays"""
        r = self.sheet["redo"]
        if r:
            r["shown"] = None
        self.redraw()

    def redo_done(self, canvas):
        """Done: the new ink read and checked; once it is kept it takes the old one's place
        (Writer.rewrite) and the sheet shows the new sample, which is learned on close
        (FR-032)"""
        sh = self.sheet
        r = sh and sh["redo"]
        got = self.opened()
        if not r or r["busy"] or not got or not canvas.strokes:
            return
        sid, pos, old = got[0], got[1], r["old"]
        strokes, units, area = canvas.payload()
        canvas.clear()
        r.update(busy=True, shown=None)
        wr, donated, c = self.writer, sid in self.donated, sh["char"]

        def go():
            try:
                return wr.rewrite(sid, old, strokes, units, area, donated=donated)
            except client.Error as e:
                return str(e)

        def fin(new):
            r["busy"] = False
            here = self.sheet is sh and sh["redo"] is r
            if isinstance(new, flow.Shown) and new.status == "kept" and new.sample_id:
                log("written again", sid, "->", new.sample_id)
                self.inks.pop(sid, None)
                self.donated = (self.donated - {sid}) | ({new.donated_id} if new.donated_id else set())
                if here:
                    sh.update(redo=None, open=(new.sample_id, pos), redone=True,
                              undo=None if sh["undo"] == sid else sh["undo"])
                    self.sheet_changed()
                    self.load_occ()
                elif ((self.font or {}).get("status") or {}).get("state") != "none":
                    self.relearn_char(c)        # the sheet closed meanwhile: learned again as on close
                self.load_font()
                self.refresh()
            elif not here:
                return
            elif isinstance(new, flow.Shown) and new.status == "check":
                r["shown"] = new
            else:
                sh["redo"] = None
                self.msg = ("Something went wrong: %s. The old one stays as it was." % new if isinstance(new, str) else
                            "Not kept: learning is off. The old one stays as it was." if new and new.why == "learning"
                            else "Not kept. The old one stays as it was.")
                self.load_occ()
        self.run(go, fin)
        self.redraw()

    def char_pick(self):
        """Which one is nicer? for this character: two of its usable samples written
        different ways, none of a version left out"""
        sh = self.sheet
        sh["pick"] = None
        if self.demo:
            return
        c = sh["char"]
        _, d, _ = self.char_info(c)
        out = {i for v in (d or {}).get("variants", []) if v.get("left_out") for i in v.get("ids") or []}

        cli = self.cli

        def get():
            occ = sh["occ"] if sh["occ"] is not None else cli.letters_samples(c)
            use = [o for o in occ if o[2] == "usable" and o[0] not in out]
            random.shuffle(use)
            use.sort(key=lambda o: o[3])
            got, seen = [], set()
            for o in use:                      # one of each version first
                if o[3] in seen and len({u[3] for u in use}) > 1:
                    continue
                st = flow.crop(self.rec.sample(o[0]), o[1])
                if st:
                    got.append((o[0], st, o[1]))
                    seen.add(o[3])
                if len(got) == 2:
                    return occ, {"a": got[0], "b": got[1], "char": c, "chosen": None}
            return occ, None

        def got(r):
            if self.sheet is sh:
                sh["occ"] = r[0]
                sh["pick"] = r[1] or False
        self.run(get, got)

    def char_choose(self, i):
        sh = self.sheet
        pk = sh and sh.get("pick")
        if not pk or pk.get("chosen") is not None:
            return
        pk["chosen"] = i
        a, b = (pk["a"], pk["b"]) if i == 0 else (pk["b"], pk["a"])
        sh["picks"] += 1
        self.sheet_changed()
        self.redraw()
        rec = self.rec
        self.run(lambda: rec.prefer(a[0], b[0], a[2], b[2]), lambda _: (self.refresh(), self.char_pick()))

    def char_picks(self):
        """the picks for the sheet's character so far"""
        sh = self.sheet
        if not sh:
            return 0
        text = {r[0]: r[2] for r in self.samples}
        c = sh["char"]
        return sum(1 for ch, _, cc, _ in self.prefs if cc is not None and ch in text and cc < len(text[ch])
                   and text[ch][cc] == c)

    # ---------------------------------------------------------- Neaten my writing

    def open_neat(self):
        """Neaten my writing: free writing, shown neatened; nothing becomes a sample. It
        neatens towards the letter model: a missing or out-of-date one is built meanwhile"""
        if self.guest:
            return
        self.neat = N.Neat(self.rec, self.state.lang)
        self.neat_shown, self.neat_text, self.neat_orig = {}, "", False
        self.page, self.tab, self.best = "neat", "learn", None
        self.msg = ""
        self.load_font()
        self.redraw()

    def close_neat(self):
        if self.neat is None:
            return
        n, self.neat = self.neat, None
        self.neat_gen += 1
        self.neat_shown, self.neat_text, self.neat_again = {}, "", None
        if self.page == "neat":
            self.page, self.best = "learn", "intro"
        if self.work:
            self.work.do(n.close)

    def neaten(self, payload):
        """the page's ink (the writing area's payload) neatened at the strength chosen; a
        call while one runs waits for it, only the newest ink goes"""
        if self.neat is None or self.demo or self.pen_ink:
            return                        # through the pen interface the service neatens
        if self.neat_busy:
            self.neat_again = payload
            return
        self.neat_busy = True
        nt, gen, st = self.neat, self.neat_gen, self.neat_strength
        strokes, units, area = payload

        def go():
            return nt.run(strokes, units, area, st, LINE, area[6])

        def fin(r, err):
            self.neat_busy = False
            if err:
                print("glassine-handwriting-samples: neaten: %s" % err, file=sys.stderr, flush=True)
                self.msg = "Couldn’t neaten it: %s" % err
            elif gen == self.neat_gen and nt is self.neat:
                self.neat_shown = r[0]
                self.neat_text = " · ".join(v for _, v in sorted(r[2].items()) if v)
                self.msg = ""
            again, self.neat_again = self.neat_again, None
            if again and gen == self.neat_gen and nt is self.neat:
                self.neaten(again)
            self.redraw()
        self.work.do(go, fin)

    def neat_cleared(self):
        self.neat_gen += 1
        self.neat_shown, self.neat_text, self.neat_again = {}, "", None
        self.redraw()

    def set_strength(self, s, payload):
        self.neat_strength = s
        self.neat_orig = False
        if self.pen_ink:                  # the service neatens what it holds again
            self.pen_ink.pen.set_neaten(True, s)
        elif payload[0]:
            self.neaten(payload)
        self.redraw()

    def show_original(self, on):
        self.neat_orig = on
        self.redraw()

    def start_pick(self):
        self.best = "pick"
        self.pick = None
        self.next_pick()

    def next_pick(self):
        """two versions of one character from the person's own samples: chosen from the
        listing (no strokes), then only those two are fetched"""
        self.pick = None
        self.pick_loading = True
        self.redraw()
        if self.demo:
            return
        rows, prefs, rec = list(self.samples), list(self.prefs), self.rec

        def get():
            return flow.load_pick(rec, rows, prefs)

        def got(r):
            self.pick_loading = False
            self.pick = r
        self.run(get, got)

    def choose_pick(self, i):
        pk = self.pick
        if not pk or pk.get("chosen") is not None:
            return
        pk["chosen"] = i
        a, b = (pk["a"], pk["b"]) if i == 0 else (pk["b"], pk["a"])
        self.state.picks[flow.today()] = self.picks_today() + 1
        self.state.save()
        self.redraw()
        rec = self.rec

        def go():
            rec.prefer(a[0], b[0], a[2], b[2])
        self.run(go, lambda _: (self.refresh(), self.next_pick()))

    def picks_today(self):
        return self.state.picks.get(flow.today(), 0)

    # ---------------------------------------------------------- donation

    def turn_on(self):
        """the licence is chosen once per donation: asked when none is started. A guest
        confirms being 18 or older first, every time (FR-019: the answer is kept nowhere)"""
        if self.guest:
            self.page, self.wstep, self.gflow, self.adult = "wizard", "g-age", "tab", False
            self.msg = ""
            return self.redraw()
        if self.donation[0]:
            self.donating(True)
        else:
            self.picking_licence = True
            self.licence_pick = None
            self.redraw()

    def cancel_licence(self):
        self.picking_licence = False
        self.licence_pick = None
        self.redraw()

    def donating(self, on, licence=None):
        cli, st = self.cli, self.state

        def go():
            if on and licence and not cli.donation()[0]:
                cli.donation_start(licence)
            st.donating = on
            st.save()
        self.picking_licence = False
        self.run(go, lambda _: self.refresh())

    def ask_turn_off(self):
        """Turn off is undone by Turn on: no question"""
        self.donating(False)

    def review_earlier(self):
        self.reviewing = True
        self.ep = 0
        self.redraw()

    def close_review(self):
        self.reviewing = False
        self.redraw()

    def flip_earlier(self, sid):
        self.earlier_out ^= {sid}
        self.redraw()

    def earlier_page(self, d):
        self.ep += d
        self.redraw()

    def add_earlier(self):
        """the earlier samples the person didn't leave out go into the donation"""
        ids = [e[0] for e in self.earlier if e[0] not in self.earlier_out]
        self.reviewing = False
        cli = self.cli
        self.run(lambda: cli.donation_add_all(ids),
                 lambda n: (setattr(self, "msg", "%d earlier samples added to your contribution." % n), self.refresh()))

    def write_donation(self):
        lic, s = self.donation[0], self.session
        self.run(lambda: save_contribution(s.cli, s.state, lic), lambda path: (setattr(self, "saved", path),
                                                                               self.refresh()))

    def ask_new_donor(self):
        self.confirm = ("Get a new contributor number?", "Your next contributions then count as another writer’s. The ones you "
                        "already sent keep the old one.", "Get a new one", self.new_donor)
        self.redraw()

    def new_donor(self):
        self.confirm = None
        self.run(self.cli.donor_new, lambda _: self.refresh())

    def ask_erase_donation(self):
        self.confirm = ("Erase your contribution?", "The copy set aside for contributing goes. Your own samples stay.", "Erase",
                        self.erase_donation)
        self.redraw()

    def erase_donation(self):
        self.confirm = None
        lic, cli, st = self.donation[0], self.cli, self.state

        def go():
            cli.donation_erase()
            if lic and st.donating:
                cli.donation_start(lic)
        self.run(go, lambda _: self.refresh())


def ui_go(ui, page):
    ui.go(page)


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
        self.inks = {"write": [], "neat": []}   # Write's ink and Neaten my writing's, each kept apart
        self.cur = None
        self.timer = None
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.TOUCH_MASK)
        self.connect("realize", lambda w: w.get_window().set_event_compression(False))
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("button-release-event", self.on_release)
        self.connect("touch-event", lambda w, e: True)      # fingers don't write (palms rest on it)
        self.connect("size-allocate", lambda w, a: self.pen_place())
        self.pen_tried = False

    # ---- Neaten my writing through the pen interface (neat.PenInk), when there is one

    def pen_sync(self):
        """the pen interface on while Neaten my writing is open (and the window is a Wayland
        surface whose compositor has it), off on every other page"""
        want = self.ui.page == "neat" and not self.ui.demo and self.get_window() is not None
        pi = self.ui.pen_ink
        if want and pi is None and not self.pen_tried:
            self.pen_tried = True
            mod = N.pen_module()
            pen = mod.Pen.for_gdk_window(self.get_window()) if mod else None
            if pen is None:
                return
            pi = N.PenInk(pen, self.inks["neat"], self.pen_changed)
            if not pi.neatens:            # the compositor has it, the service isn't there
                pen.close()
                log("pen interface without neatening: the direct path")
                return
            pen.listener = pi
            self.ui.pen_ink = pi
            self.pen_place()
            pen.set_neaten(True, self.ui.neat_strength)
            pen.set_text(True)              # "Read as"
            log("pen interface on")
        elif not want and pi is not None:
            pi.pen.close()
            self.ui.pen_ink = None
        if not want:
            self.pen_tried = False

    def pen_place(self):
        pi = self.ui.pen_ink
        win = self.get_window()
        if not pi or not win:
            return
        x = y = 0
        while win is not None and win.get_window_type() == Gdk.WindowType.CHILD:
            px, py = win.get_position()      # the surface: the toplevel's window, shadow and all
            x, y = x + px, y + py
            win = win.get_parent()
        a = self.get_allocation()
        pi.place(x, y, a.width, a.height, LINE, LINE * 0.62)

    def pen_changed(self):
        pi = self.ui.pen_ink
        if pi and not pi.neatens:         # the service left: the direct path from here on
            pi.settle()
            pi.pen.close()
            self.ui.pen_ink = None
            log("pen interface lost neatening: the direct path")
            if self.strokes:
                self.ui.neaten(self.payload())
        self.ui.neat_text = self.ui.pen_ink.text if self.ui.pen_ink else self.ui.neat_text
        self.queue_draw()
        self.on_change()

    def slot(self):
        """whose ink: Neaten my writing's, Write it again's, or Write's"""
        return "neat" if self.ui.page == "neat" else "redo" if redoing(self.ui) else "write"

    @property
    def strokes(self):
        return self.inks.setdefault(self.slot(), [])

    @strokes.setter
    def strokes(self, v):
        self.inks[self.slot()] = v

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
        if self.ui.pen_ink and self.ui.page == "neat":
            self.ui.pen_ink.down(self.cur)  # shown until the compositor's stroke for it comes
        else:
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
        cur, self.cur = self.cur, None
        self.on_change()
        if self.ui.pen_ink and self.ui.page == "neat":
            pi = self.ui.pen_ink
            GLib.timeout_add(1000, lambda: pi.lost(cur) and False)
            return True
        if self.ui.page == "neat":
            self.timer = GLib.timeout_add(NEAT_PAUSE_MS, self.paused)
            return True
        if redoing(self.ui):              # Write it again waits for Done (never the Write page's prompt)
            return True
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
        if not self.strokes or self.cur is not None or redoing(self.ui):
            return False
        if self.ui.page == "neat":        # a pause on Neaten my writing: the ink comes back neater
            self.ui.neaten(self.payload())
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
        if self.ui.page == "neat" and self.ui.pen_ink:
            self.ui.pen_ink.page = self.strokes
            self.ui.pen_ink.clear()
        if self.ui.page == "neat":
            self.ui.neat_cleared()
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
        # the first guide line (guides(): 0.62 of a line from the top): where letters sit
        return strokes, units, (0, 0, a.width, a.height, LINE, rot, LINE * 0.62)

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
        area_frame(cr, self.ui, a.width, a.height)
        cr.set_source_rgba(*PT.color(t.pen))
        cr.set_line_width(max(2.0, LINE / 110 * 1.5))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        pending = self.ui.pen_ink.pending if self.ui.pen_ink and self.ui.page == "neat" else []
        for s in neat_view(self.ui, self.strokes) + pending:
            cr.move_to(s[0][0], s[0][1])
            for p in s[1:]:
                cr.line_to(p[0], p[1])
            if len(s) == 1:
                cr.line_to(s[0][0] + 0.1, s[0][1])
            cr.stroke()
        return True


def area_frame(cr, ui, w, h):
    """the writing area's paper: in the character sheet framed by the sheet, on Neaten my
    writing a box, elsewhere the guides only"""
    k = ui.theme
    if sheet_writing(ui) and not pages.LAND[0]:
        pages.sheet_area(cr, k, w, h)
        cr.save()
        cr.rectangle(pages.SHEET_M + 20, 4, w - 2 * pages.SHEET_M - 40, h - 8)
        cr.clip()
        guides(cr, k, pages.SHEET_M + 24, 0, w - 2 * pages.SHEET_M - 48, h, LINE)
        cr.restore()
    elif ui.page == "neat" or sheet_writing(ui):
        P_ = pages.PAD
        PT.box(cr, P_, 4, w - 2 * P_, h - 8, k["cardR"], bg=None, shadow="inset 0 0 0 1.5px " + k.ink)
        guides(cr, k, P_, 0, w - 2 * P_, h, LINE)
    else:
        guides(cr, k, 0, 0, w, h, LINE)


def neat_view(ui, strokes):
    """the strokes as the page shows them: on Neaten my writing each neatened one in its
    neatened form (unless the original is shown), the ones written since as written"""
    shown = ui.pen_ink.shown if ui.pen_ink else ui.neat_shown
    if ui.page != "neat" or ui.neat_orig or not shown:
        return strokes
    return [shown.get(i, s) for i, s in enumerate(strokes)]


class Moved:
    """tap targets of a part drawn elsewhere in the window"""

    def __init__(self, hits, dx, dy):
        self.hits, self.dx, self.dy = hits, dx, dy

    def add(self, x, y, w, h, fn):
        self.hits.add(x + self.dx, y + self.dy, w, h, fn)


class Shifted:
    """tap targets of a part drawn lower down in the same widget"""

    def __init__(self, hits, dy):
        self.hits, self.dy = hits, dy

    def add(self, x, y, w, h, fn):
        self.hits.add(x, y + self.dy, w, h, fn)


def writing(ui):
    """the pages with the writing area: Write (also Your best) and the introduction's Try one"""
    if ui.confirm or not ui.loaded:
        return False
    return ((ui.page == "write" and not ui.sets_open) or (ui.page == "wizard" and ui.wstep == "w-try") or
            ui.page == "neat" or sheet_writing(ui))


def sheet_writing(ui):
    """the character sheet on Write more, or writing a sample again: a writing page inside
    the sheet"""
    return bool(ui.page == "learn" and ui.best == "font" and ui.sheet and not ui.renaming and
                (redoing(ui) or (ui.sheet["seg"] == "write" and not pages.drilled(ui.sheet))))


def redoing(ui):
    """Write it again of a sample opened in the sheet (spec 003 FR-033)"""
    return bool(ui.sheet and ui.sheet.get("redo") and ui.sheet.get("open"))


def parts(ui):
    """the top and bottom painters of a writing page"""
    if ui.page == "wizard":
        return pages.try_top, pages.try_bottom
    if ui.page == "neat":
        return pages.neat_top, pages.neat_bottom
    if redoing(ui):
        return pages.sheet_redo_top, pages.sheet_redo_bottom
    if sheet_writing(ui):
        return pages.sheet_write_top, pages.sheet_write_bottom
    return pages.write_top, pages.write_bottom


RESERVE = 160          # room under the prompt for the last sample or the failed check


def _probe():
    return cairo.Context(cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))


def top_height(ui, w, head=True):
    """the writing page's top: measured without the last sample, plus room for it, so the
    writing area keeps its place while readings come in"""
    top, _ = parts(ui)
    if sheet_writing(ui):           # the sheet makes room for a failed check while it shows
        return int(top(_probe(), ui, Hits(), w, head=head))
    wr = ui.writer
    sh = wr.shown if wr else None
    if wr:
        wr.shown = None
    try:
        h = int(top(_probe(), ui, Hits(), w, head=head))
    finally:
        if wr:
            wr.shown = sh
    return h + (RESERVE if ui.page == "write" else 0)


def bottom_height(ui, w, canvas=None, foot=True):
    if ui.page == "wizard":
        y = pages.try_bottom(_probe(), ui, Hits(), w, 2000, canvas, foot=False)
        return int(y) + 12 + (pages.WIZ_FOOT if foot else 0)
    return int(parts(ui)[1](_probe(), ui, Hits(), w, 200, canvas))


def side_width(w):
    return max(300, int(w * 0.34))


def layout(ui, W, H, canvas=None):
    """where the parts of a writing page go: {name: (x, y, w, h)}. Portrait: the top, the
    writing area, the bottom. Landscape: the head across, a column beside the area, and
    the introduction's foot across"""
    if W > H * 1.1:
        pages.LAND[0] = True
        hh = int(pages.write_head(_probe(), ui, Hits(), W))
        fh = pages.WIZ_FOOT if ui.page == "wizard" else 0
        sw = side_width(W) if not sheet_writing(ui) else max(420, int(W * 0.46))
        out = {"head": (0, 0, W, hh), "side": (0, hh, sw, H - hh - fh), "area": (sw, hh, W - sw, H - hh - fh)}
        if fh:
            out["foot"] = (0, H - fh, W, fh)
        return out
    pages.LAND[0] = False
    th, bh = top_height(ui, W), bottom_height(ui, W, canvas)
    return {"top": (0, 0, W, th), "area": (0, th, W, H - th - bh), "bottom": (0, H - bh, W, bh)}


def paint_part(name, cr, ui, hits, w, h, canvas):
    """one part of a writing page, at its own origin"""
    top, bottom = parts(ui)
    k = ui.theme
    PT.fill(cr, 0, 0, w, h, k.paper)
    if name == "head":
        pages.write_head(cr, ui, hits, w)
    elif name == "top":
        pages.LAND[0] = False
        top(cr, ui, hits, w)
    elif name == "bottom":
        bottom(cr, ui, hits, w, h, canvas)
    elif name == "foot":
        cr.save()
        cr.translate(0, -(2000 - h))
        pages.wizard_foot(cr, ui, Shifted(hits, -(2000 - h)), w, 2000)
        cr.restore()
    elif name == "side":
        pages.LAND[0] = True
        PT.fill(cr, w - 1.5, 0, 1.5, h, k.ink)
        top(cr, ui, hits, w - 2, head=False)
        bh = bottom_height(ui, w - 2, canvas, foot=False)
        cr.save()
        cr.translate(0, h - bh)
        if ui.page == "wizard":
            bottom(cr, ui, Shifted(hits, h - bh), w - 2, bh, canvas, foot=False)
        else:
            bottom(cr, ui, Shifted(hits, h - bh), w - 2, bh, canvas)
        cr.restore()


class Window(Gtk.ApplicationWindow):
    """a writing page is its parts (layout()) around the writing area, each its own widget;
    every other page is one self-drawn widget"""

    PARTS = ("head", "top", "side", "bottom", "foot")

    def __init__(self, app, ui):
        super().__init__(application=app, title="Handwriting")
        self.ui = ui
        self.set_default_size(W0, H0)
        self.stack = Gtk.Stack()
        overlay = Gtk.Overlay()
        overlay.add(self.stack)
        self.add(overlay)
        # the font's name is typed here, over the field the rename dialog draws (the
        # on-screen keyboard comes up for it)
        self.entry = Gtk.Entry(has_frame=False, max_length=40, halign=Gtk.Align.START, valign=Gtk.Align.START,
                               no_show_all=True)
        self.entry.connect("activate", lambda e: ui.use_name())
        overlay.add_overlay(self.entry)
        ui.entry_text = self.entry.get_text
        # a writer's name: a private field (spec 002 FR-014, shell spec 006 FR-007). GTK 3 has no
        # private hint; the password purpose is one (the keyboard suggests and learns nothing,
        # and offers no handwriting), with the text shown
        self.name_entry = Gtk.Entry(has_frame=False, max_length=WR.MAX_NAME + 20, halign=Gtk.Align.START,
                                    valign=Gtk.Align.START, no_show_all=True, visibility=True,
                                    input_purpose=Gtk.InputPurpose.PASSWORD,
                                    input_hints=Gtk.InputHints.NO_SPELLCHECK | Gtk.InputHints.NO_EMOJI)
        self.name_entry.connect("activate", lambda e: ui.use_writer_name())
        self.name_entry.connect("changed", lambda e: ui.name_changed(e.get_text()))
        overlay.add_overlay(self.name_entry)
        ui.name_text = self.name_entry.get_text
        self.canvas = Canvas(ui, lambda: [self.panels[n].queue_draw() for n in ("bottom", "side")])
        self.fixed = Gtk.Layout()             # unlike Gtk.Fixed, it asks no size of its own: the window can turn
        self.panels = {}
        for n in self.PARTS:
            p = Panel(lambda cr, hits, w, h, n=n: paint_part(n, cr, ui, hits, w, h, self.canvas))
            self.panels[n] = p
            self.fixed.put(p, 0, 0)
        self.fixed.put(self.canvas, 0, 0)
        self.full = Panel(lambda cr, hits, w, h: pages.paint_full(cr, ui, hits, w, h))
        self.stack.add_named(self.fixed, "write")
        self.stack.add_named(self.full, "full")
        self.geom = None
        self.connect("size-allocate", lambda w, a: GLib.idle_add(self.fit))
        ui.on_switch = self.canvas.clear

    def fit(self):
        """the writing page's parts placed for the window; only while the area is empty (or
        the window turned), so ink never moves under the pen"""
        a = self.stack.get_allocation()
        if a.width <= 1 or not writing(self.ui):
            return False
        land = a.width > a.height * 1.1
        if self.canvas.strokes and self.geom and self.geom[0] == land:
            return False
        lay = layout(self.ui, a.width, a.height, self.canvas)
        geom = (land, a.width, a.height, tuple(sorted(lay.items())))
        if geom == self.geom:
            return False
        self.geom = geom
        for n, p in self.panels.items():
            if n in lay:
                x, y, w, h = lay[n]
                self.fixed.move(p, x, y)
                p.set_size_request(w, h)
                p.show()
            else:
                p.hide()
        x, y, w, h = lay["area"]
        self.fixed.move(self.canvas, x, y)
        self.canvas.set_size_request(w, h)
        return False

    def redraw(self):
        w = writing(self.ui)
        self.stack.set_visible_child_name("write" if w else "full")
        if w:
            self.fit()
        for p in list(self.panels.values()) + [self.full, self.canvas]:
            p.queue_draw()
        self.canvas.pen_sync()
        self.place_entry()

    def place_entry(self):
        self.place_name()
        ui = self.ui
        if not ui.renaming:
            self.entry.hide()
            return
        a = self.stack.get_allocation()
        if a.width <= 1:
            return
        ui.rename_rect = None
        pages.paint_full(_probe(), ui, Hits(), a.width, a.height)
        if not ui.rename_rect:
            return
        x, y, w, h = (int(v) for v in ui.rename_rect)
        self.entry.set_margin_start(x)
        self.entry.set_margin_top(y)
        self.entry.set_size_request(w, h)
        if not self.entry.get_visible():
            self.entry.set_text(ui.renaming["text"])
            self.entry.show()
            self.entry.grab_focus()


    def place_name(self):
        """the private field over the name dialog's box; emptied once the dialog closes"""
        ui = self.ui
        if not ui.naming:
            if self.name_entry.get_visible():
                self.name_entry.hide()
                self.name_entry.set_text("")
            return
        a = self.stack.get_allocation()
        if a.width <= 1:
            return
        ui.name_rect = None
        pages.paint_full(_probe(), ui, Hits(), a.width, a.height)
        if not ui.name_rect:
            return
        x, y, w, h = (int(v) for v in ui.name_rect)
        self.name_entry.set_margin_start(x)
        self.name_entry.set_margin_top(y)
        self.name_entry.set_size_request(w, h)
        if not self.name_entry.get_visible():
            self.name_entry.set_text(ui.naming["text"])
            self.name_entry.show()
            self.name_entry.grab_focus()


class App(Gtk.Application):
    """one window (spec 002 FR-015): the application id makes the app single instance, so
    opening it again while it runs shows the window as it is; a session switch happens inside
    it, and each start begins in the owner's session"""

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
            self.ui.owner.rec.close()
        Gtk.Application.do_shutdown(self)

    def watch_theme(self):
        self.mon = Gio.File.new_for_path(THEME_FILE).monitor_file(Gio.FileMonitorFlags.NONE, None)

        def changed(*a):
            name = read_file(THEME_FILE)
            if name and name != self.ui.theme.name:
                self.ui.theme = Look(name)
                self.win.sizes = None
                self.win.redraw()
                self.win.canvas.queue_draw()
        self.mon.connect("changed", changed)


# ---------------------------------------------------------------- the design's screens, drawn


SCREENS = ("wizard.welcome", "wizard.try", "wizard.try-done", "wizard.samples", "wizard.learning", "wizard.donate",
           "wizard.licence", "wizard.ready", "write.prompt", "write.ink", "write.kept", "write.check", "write.sentence",
           "write.sets", "samples.list", "samples.detail", "samples.delete", "coverage", "learn.few", "learn.waiting",
           "learn.running", "learn.better", "learn.kept", "best.intro", "best.write", "best.quick", "best.careful",
           "best.pick", "font.none", "font.learning", "font.current", "font.installed", "font.outdated",
           "font.rename", "font.relearn", "char.versions", "char.samples", "char.write", "char.pick", "char.missing",
           "char.sample", "char.redo", "char.delete", "char.version-samples",
           "neat.write", "neat.original", "neat.unlearned", "donate.off", "donate.licence", "donate.earlier", "donate.on", "donate.saved",
           "others.list", "others.empty", "others.add", "others.rename", "others.taken", "others.long", "others.remove",
           "others.remove-plain", "others.done", "others.done-kept", "guest.hello", "guest.try", "guest.share", "guest.age",
           "guest.age-ticked", "guest.licence", "guest.write", "guest.best", "guest.sets", "guest.samples", "guest.coverage",
           "guest.contribute-off", "guest.contribute", "guest.saved")
# a guest's screens are the owner's pages in that writer's session (made-up names, no one's)
GUEST_SCREENS = {"hello": "wizard.hello", "try": "wizard.try", "share": "wizard.donate", "age": "wizard.age",
                 "age-ticked": "wizard.age", "licence": "wizard.licence", "write": "write.prompt", "best": "best.write",
                 "sets": "write.sets", "samples": "samples.list", "coverage": "coverage", "contribute-off": "donate.off",
                 "contribute": "donate.on", "saved": "donate.saved"}
DEMO_WRITERS = (("00000000000000a1", "Guest writer", 48, 12, True), ("00000000000000a2", "Second writer", 9, 0, False),
                ("00000000000000a3", "Book club guest", 0, 0, False))
DEMO_WORDS = ["quiet", "Jackdaws love my big sphinx of quartz.", "jump", "Q", "7:30", "o’clock", "garden", "bright",
              "yellow", "Thursday", "minimum", "fifty"]


def demo_ink(text, seed=1, x0=0.0):
    """made-up handwriting for the drawn screens: a loop per letter (no one's real writing)"""
    rnd = random.Random(seed)
    strokes, spans, x = [], [], x0
    for ch in text:
        if ch == " ":
            spans.append([])
            x += 18
            continue
        tall = ch.isupper() or ch in "bdfhklt0123456789"
        h = 34 if tall else 20
        pts = []
        for i in range(19):
            a = i / 18 * 2 * math.pi
            pts.append((x + 9 + 8 * math.sin(a) + rnd.uniform(-.6, .6), 40 - h / 2 - h / 2 * math.cos(a) +
                        rnd.uniform(-.6, .6), 10 * i))
        spans.append([len(strokes)])
        strokes.append(pts)
        x += 20 + rnd.uniform(-2, 2)
    return strokes, spans


def demo_font(installed, state="current"):
    """a made-up letter model (loops, as demo_ink draws them; no one's real writing) with the
    letters a person who wrote a few words with care has, and its status; t written three
    ways (the favourite, another, one left out), Q with one sample"""
    rnd = random.Random(4)
    chars = {}
    few = {"q": 2, "x": 1, "z": 0, "F": 2, "G": 1, "J": 0, "K": 1, "Q": 1, "U": 2, "V": 0, "X": 0, "Y": 1, "Z": 0,
           "7": 2, "8": 1}
    for c in fontview.TARGET:
        n = few.get(c, 24 if c == "t" else 5)
        tall = c.isupper() or c in "bdfhklt0123456789"
        vs = []
        for v in range(3 if n >= 3 else 0):
            pts = [[0.3 + 0.28 * math.sin(i / 18 * 2 * math.pi) + rnd.uniform(-.01, .01),
                    (1.5 if tall else 1.0) * (0.5 - 0.5 * math.cos(i / 18 * 2 * math.pi)) + rnd.uniform(-.01, .01)]
                   for i in range(19)]
            k = (13, 6, 3)[v] if c == "t" else 2
            vs.append({"width": 0.6, "weight": 1, "members": 2, "use": k, "strokes": [pts],
                       "ids": ["%016x" % (100 * (v + 1) + j) for j in range(k)]})
        d = {"char": c, "samples": n, "covered": n >= 3, "variants": vs}
        if c == "t":
            vs[0]["favourite"] = True
            vs[1]["strokes"] = [vs[1]["strokes"][0][:10], vs[1]["strokes"][0][9:]]
            vs[2]["left_out"] = True
            d["slots"] = [0, 1, 0, 0, 1, 0, 0, 1, 0, 0]
        chars[c] = d
    model = {"head": {}, "metrics": {"cap_height": 1.5, "ascender": 1.6, "descender": -0.5, "slant": 0.1,
                                     "letter_spacing": 0.2, "word_spacing": 0.9, "ink_width": 0.08}, "chars": chars}
    counts = {c: (d["samples"], len(d["variants"])) for c, d in chars.items()}
    status = {"state": state, "covered": sum(1 for d in chars.values() if d["covered"]),
              "samples": 160, "chars": counts, "font": ("My handwriting", 3) if installed else None,
              "font_same": True}
    if state == "none":
        model = None
    return {"status": status, "model": model}


# t's samples as the design's boards have them: version A 13, B 6, C 3, two that can't be used
DEMO_OCC = (("t", 0, "usable", 0), ("that", 0, "usable", 0), ("water", 2, "usable", 1),
            ("little", 2, "joined-after", -1), ("start", 1, "usable", 0), ("tea", 0, "usable", 1),
            ("not", 2, "usable", 0), ("it", 1, "usable", 2), ("after", 2, "usable", 0), ("t", 0, "usable", 1),
            ("street", 1, "unread", -1), ("out", 2, "usable", 0), ("time", 0, "usable", 0), ("but", 2, "usable", 1),
            ("best", 3, "usable", 0), ("hot", 2, "usable", 2), ("into", 2, "usable", 0), ("ten", 0, "usable", 1),
            ("tree", 0, "usable", 0), ("sit", 2, "usable", 0), ("its", 1, "usable", 1), ("left", 3, "usable", 0),
            ("cat", 2, "usable", 2), ("top", 0, "usable", 0))
DEMO_READ = {"little": "litle", "street": "stveet"}


def demo(screen, voice="white"):
    """the UI object in the state a screen of the design shows (no recognizer, no store)"""
    ui = UI(lambda: None, demo=True)
    ui.theme = Look(voice)
    sec, _, sub = screen.partition(".")
    guest = sec == "guest"
    if guest:
        ui.session = WR.Session(DEMO_WRITERS[0][0], DEMO_WRITERS[0][1], writer=False, state=flow.State(os.devnull))
        screen = GUEST_SCREENS[sub]
    st = ui.state
    st.save = lambda: None
    st.set, st.used, st.current = P.AUTO, {}, None
    st.donating, st.picks = False, {flow.today(): 4}
    sets = {"words": [P.Prompt(w, "words", "en") for w in ("quiet", "garden", "bright", "yellow", "Thursday")],
            "sentences": [P.Prompt("A small step every day.", "sentences", "en")],
            "pangrams": [P.Prompt("Jackdaws love my big sphinx of quartz.", "pangrams", "en")],
            "letters": [P.Prompt("g", "letters", "en")], "digits": [P.Prompt("7:30", "digits", "en")], P.OWN: []}
    w = ui.writer = flow.Writer(None, None, st, sets=sets)
    w.prompt = sets["words"][0]
    now = time.time()
    ui.counts, ui.fresh = (12, 148), 23
    ui.plan = {"train": 120, "held": 28, "new": 23, "min_train": 40, "min_held": 10, "charging": False}
    ui.adapts = {"current": {"time": now - 3 * 86400, "train": 110, "held": 26, "before": 0.182, "after": 0.121},
                 "previous": {"time": now - 9 * 86400}}
    ui.samples = [("%016x" % i, now - 3000 * (40 - i), DEMO_WORDS[i % len(DEMO_WORDS)]) for i in range(40)]
    for i, (sid, t, text) in enumerate(ui.samples):
        s, sp = demo_ink(text, i)
        ui.inks[sid] = {"strokes": s, "spans": sp, "text": text}
    ui.donated = {r[0] for r in ui.samples[::3]}
    ui.marked = {r[0]: ("careful", "") for r in ui.samples[-5:]}
    ui.prefs = [("0", "1", 0, 0)] * 4
    for c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,?!:;’-()\"&@":
        n = 0 if c in "QXZ&@\"" else 2 if c in "jkvwxyzJKV(),;" else 12
        for _ in range(n):
            w.cov.add(c, c if c not in "eS" else "?")
    ink = lambda text: demo_ink(text, 7)[0]
    shown = lambda text, status="kept", read=None, **kw: flow.Shown(
        P.Prompt(text, "words", "en"), ink(text), "em", (0, 0, 702, 474, 150, 0), [(read or text, -1.0)], status,
        sample_id="00000000000000aa" if status == "kept" else None, **kw)
    gsub = sub
    sec, _, sub = screen.partition(".")
    ui.writers.items = [{"id": i, "name": n} for i, n, *_ in DEMO_WRITERS]
    ui.wcounts = {i: (n, d, on) for i, _, n, d, on in DEMO_WRITERS}
    if guest:
        ui.counts = (6, 48)
        st.donating = gsub in ("contribute", "saved")
        if gsub == "age-ticked":
            ui.adult = True
    if sec == "others":
        others_demo(ui, sub)
    elif sec == "wizard" and guest:
        ui.page = "wizard"
        ui.wstep = {"hello": "g-hello", "try": "w-try", "donate": "w-share", "age": "g-age", "licence": "w-licence"}[sub]
        ui.gflow = "intro"
        ui.counts, ui.fresh = (0, 0), 0
        if sub == "licence":
            ui.licence_pick = "CC0-1.0"
    elif sec == "wizard":
        ui.page = "wizard"
        ui.wstep = {"welcome": "w-welcome", "try": "w-try", "try-done": "w-try", "samples": "w-private",
                    "learning": "w-learning", "donate": "w-share", "licence": "w-licence", "ready": "w-ready"}[sub]
        if sub == "try-done":
            ui.tried = True
            w.shown = shown("quiet")
            w.prompt = sets["words"][1]
        if sub == "licence":
            ui.licence_pick = "CC0-1.0"
        ui.counts, ui.fresh = (0, 0), 0
    elif sec == "write":
        ui.page = ui.tab = "write"
        if sub == "kept":
            w.shown = shown("garden")
            w.prompt = sets["words"][2]
        elif sub == "check":
            w.shown = shown("yellow", "check", read="yellar", fit=0.3)
            w.prompt = sets["words"][1]
        elif sub == "sentence":
            st.set = "sentences"
            w.prompt = sets["sentences"][0]
        elif sub == "sets":
            ui.sets_open = True
    elif sec == "samples":
        ui.page = ui.tab = "samples"
        if sub in ("detail", "delete"):
            sid = ui.samples[-3][0]
            ui.detail = (sid, dict(ui.inks[sid], reading=[{"text": ui.inks[sid]["text"]}],
                                   area={"orientation": 0}))
            ui.donated.add(sid)
            ui.asking_delete = sub == "delete"
    elif sec == "coverage":
        ui.page = ui.tab = "coverage"
    elif sec == "learn":
        ui.page = ui.tab = "learn"
        if sub == "few":
            ui.counts, ui.fresh, ui.adapts = (6, 18), 18, {}
            ui.plan = dict(ui.plan, train=14, held=4, new=18)
        elif sub == "running":
            ui.adapting, ui.adapt_plan, ui.adapt_round = True, (120, 28), (3, 8)
        elif sub == "better":
            ui.fresh, ui.result = 0, ("result", ["kept", "0.182", "0.121", "120", "28"])
        elif sub == "kept":
            ui.fresh, ui.result = 0, ("result", ["rejected", "0.121", "0.127", "120", "28"])
    elif sec == "best":
        if sub == "intro":
            ui.page = ui.tab = "learn"
            ui.best = "intro"
        elif sub == "pick":
            ui.page = ui.tab = "learn"
            ui.best = "pick"
            a, sa = demo_ink("g", 3)
            b, sb = demo_ink("g", 11)
            ui.pick = {"a": ("a", a, 0), "b": ("b", b, 0), "char": "g", "chosen": None}
        else:
            ui.page = ui.tab = "write"
            w.best = {"step": {"write": "careful", "quick": "quick", "careful": "pair"}[sub], "pair": "0" * 16, "n": 2}
            w.prompt = P.Prompt("Thursday", "words", "en", written="quick" if sub == "quick" else "careful")
            if sub == "careful":
                w.shown = shown("Thursday")
                w.shown.prompt = P.Prompt("Thursday", "words", "en", written="quick", pair="0" * 16)
    elif sec == "font":
        ui.page = ui.tab = "learn"
        ui.best = "font"
        ui.font = demo_font(sub in ("installed", "outdated", "rename", "relearn"),
                            {"none": "none", "learning": "none", "outdated": "outdated"}.get(sub, "current"))
        if sub == "learning":
            ui.letters_job = (36, 60)
        elif sub == "relearn":
            ui.relearn = ("t", 24, 60)
        elif sub == "rename":
            ui.renaming = {"text": "Garden letters"}
    elif sec == "char":
        ui.page = ui.tab = "learn"
        ui.best = "font"
        ui.font = demo_font(True)
        c = "Q" if sub == "missing" else "t"
        ui.sheet = new_sheet(c)
        ui.sheet["seg"] = {"versions": "versions", "version-samples": "versions", "pick": "pick"}.get(
            sub, "samples" if sub in ("samples", "sample", "redo", "delete") else "write")
        if sub in ("samples", "sample", "redo", "delete", "version-samples"):
            occ = []
            vs = ui.font["model"]["chars"]["t"]["variants"]
            for v in vs:
                v["ids"] = []
            for i, (word, pos, state, var) in enumerate(DEMO_OCC):
                sid = "%016x" % (900 + i)
                s_, sp = demo_ink(word, 30 + i)
                ui.inks[sid] = {"strokes": s_, "spans": sp, "text": word, "written": ("quick", "careful")[i % 3 > 0],
                                "reading": [{"text": DEMO_READ.get(word, word), "score": -1.0}]}
                ui.samples.append((sid, now - 60 * (17 - i) if i < 6 else now - 86400 * (1 if i < 12 else 7), word))
                if sub == "samples" and i == 4:
                    state, var = "out", -1          # left out just now, with Undo
                occ.append((sid, pos, state, var, word))
                if var >= 0:                    # the versions name these samples
                    vs[var]["ids"].append(sid)
            ui.sheet["occ"] = occ
            if sub == "samples":
                ui.sheet["undo"] = occ[4][0]
            elif sub == "version-samples":
                ui.sheet["vlist"] = 1
            else:
                ui.sheet.update(open=(occ[2][0], occ[2][1]), asking=sub == "delete",
                                redo={"busy": False, "shown": None, "old": ui.inks[occ[2][0]]} if sub == "redo" else None)
        if sub == "pick":
            a, _ = demo_ink("t", 3)
            b, _ = demo_ink("t", 11)
            ui.sheet["pick"] = {"a": ("a", a, 0), "b": ("b", b, 0), "char": "t", "chosen": None}
            ui.sheet["picks"] = 2
        if ui.sheet["seg"] == "write":
            n = 1 if c == "Q" else 4
            ps = flow.char_prompts(c, {"words": [P.Prompt(t, "words", "en") for t in ("that", "after", "Quiet")]},
                                   "en", 3 if c == "Q" else 4)
            ui.sheet["prompts"] = ps
            w.best = {"step": "careful", "pair": "", "n": 0 if c == "Q" else 1, "chars": [], "list": ps}
            w.prompt = ps[w.best["n"]]
            del n
    elif sec == "neat":
        ui.page, ui.tab = "neat", "learn"
        ui.font = demo_font(False, "none" if sub == "unlearned" else "current")
        ui.neat_text = "Call back about the train at ten"
        ui.neat_orig = sub == "original"
    elif sec == "donate":
        ui.page = ui.tab = "donate"
        if sub == "off":
            ui.donation = (None, 0, None)
        elif sub == "licence":
            ui.donation, ui.picking_licence, ui.licence_pick = (None, 0, None), True, "CC-BY-4.0"
        else:
            st.donating = True
            ui.donation = ("CC0-1.0", 57, "3f2a9c1e-5b7d-4e2a-9f10-6c8d2b4a7e31")
            ui.earlier = [r for r in ui.samples if r[0] not in ui.donated][:14]
            if sub == "earlier":
                ui.reviewing = True
                ui.earlier_out = {ui.earlier[1][0], ui.earlier[4][0]}
            if sub == "saved":
                ui.saved = os.path.join(documents_dir(), CONTRIB_DIR, "handwriting-contribution-3.jsonl")
    if guest and sec == "best":
        w.best["step"] = "careful"
    return ui


def others_demo(ui, sub):
    """Someone else writes and its questions, and the owner's Write after a guest's Finish"""
    (a, an, *_), (b, bn, *_), (c, cn, *_) = DEMO_WRITERS
    ui.page = ui.tab = "others"
    if sub == "empty":
        ui.writers.items = []
    elif sub in ("add", "taken", "long"):
        ui.writers.items = ui.writers.items[:2]
        ui.naming = {"kind": "add", "id": None, "text": {"add": cn, "taken": an, "long": "x" * 41}[sub]}
    elif sub == "rename":
        ui.naming = {"kind": "rename", "id": b, "text": "Second guest"}
    elif sub == "remove":
        ui.removing = {"id": a, "name": an, "samples": 48, "unsaved": 12, "donor": "4c1d", "files": 2, "saved": None}
    elif sub == "remove-plain":
        ui.removing = {"id": b, "name": bn, "samples": 9, "unsaved": 0, "donor": None, "files": 0, "saved": None}
    elif sub in ("done", "done-kept"):
        ui.page = ui.tab = "write"
        saved = os.path.join(documents_dir(), CONTRIB_DIR, "handwriting-contribution-6.jsonl") if sub == "done" else None
        ui.done = {"id": a if saved else b, "name": an if saved else bn, "saved": saved, "samples": 48 if saved else 9,
                   "donating": bool(saved)}


class Drawn:
    """the writing area of a drawn screen: its ink, nothing more"""

    def __init__(self, ink):
        self.strokes = ink or []

    def clear(self):
        self.strokes = []

    def submit(self):
        self.strokes = []

    def payload(self):
        return self.strokes, "device", (0, 0, 1, 1, LINE, 0, LINE * 0.62)


def paint(cr, ui, W, H, ink=None):
    """one whole window as the app draws it -> the tap targets"""
    hits = Hits()
    canvas = Drawn(ink)
    if not writing(ui):
        pages.paint_full(cr, ui, hits, W, H)
        return hits
    lay = layout(ui, W, H, canvas)
    for name, (x, y, w, h) in lay.items():
        cr.save()
        cr.translate(x, y)
        if name == "area":
            PT.fill(cr, 0, 0, w, h, ui.theme.paper)
            area_frame(cr, ui, w, h)
            if ink:
                draw_ink(cr, ui.theme, ink, 40, LINE * 0.62 - 80, w * 0.6, 90, 3.0, align="left")
        else:
            paint_part(name, cr, ui, Moved(hits, x, y), w, h, canvas)
        cr.restore()
    return hits


def render(argv):
    """--render OUT.png [--screen S] [--theme T] [--size WxH]: one of the design's screens"""
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    W, H = (int(v) for v in opt("--size", "%dx%d" % (W0, H0)).split("x"))
    screen = opt("--screen", opt("--page", "write.prompt"))
    ui = demo(screen, opt("--theme", "white"))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    paint(cairo.Context(surf), ui, W, H, demo_ink("quiet", 5)[0] if screen in ("write.ink", "wizard.try", "guest.try") else
          demo_ink("Call back about", 5)[0] if screen.startswith("neat.") else
          demo_ink("water", 5)[0] if screen == "char.redo" else None)
    surf.write_to_png(opt("--render"))


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if "--render" in argv:
        render(argv)
        return 0
    return App().run([argv[0]])
