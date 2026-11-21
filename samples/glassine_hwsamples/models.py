# SPDX-License-Identifier: GPL-2.0-or-later
"""Learn › Models (spec 006): which model reads and neatens, the shipped one or one the person
installed themselves, installing a model file from Documents › Handwriting and removing it. Drawn
with the app's existing parts until the page has a board of its own in GlassineOS/ui."""

import os

from glassine_ui import paint as PT

from .look import BTN_H, LAND, PAD, btn, btn_w, btns_right, card, icon, lbl, radio, soft_box

KINDS = (("recognizer", "Reading", "The model that reads your writing in every app."),
         ("aligner", "Neatening", "The model that tells which ink belongs to which letter, for neatening and "
                                  "your font."))

# a refusal's reason (docs/recognizer.md, Installed models) in the person's words
REASONS = {
    "missing": "Its file is gone.",
    "unreadable": "Its file can’t be read.",
    "too-big": "It is too big for this tablet.",
    "not-a-model": "It isn’t a handwriting model.",
    "format-version": "It was made for another version of the handwriting recognizer.",
    "features": "It reads writing in a way this version doesn’t.",
    "damaged": "The file is damaged, perhaps cut short while copying.",
    "kind-unknown": "It doesn’t say what kind of model it is.",
    "not-finite": "Its numbers are broken.",
}
NAMES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "nl": "Dutch"}
FOLDER = "Handwriting"          # in Documents, the folder the app keeps its files in


def lang_name(lang):
    return NAMES.get(lang.split("-")[0].split("_")[0], lang)


def why(reason, detail, lang):
    """a refusal in plain words"""
    if reason == "kind":
        return "It is a model for %s, not for this." % ("neatening" if detail == "aligner" else "reading")
    if reason == "alphabet":
        if detail.startswith("U+") or detail.startswith("twice"):
            return "Its list of letters is broken (%s)." % detail
        return "It lacks letters %s needs: %s." % (lang_name(lang), detail.replace("space", "the space"))
    if reason == "language":
        return "This tablet has no %s to go with it." % lang_name(detail or lang)
    return REASONS.get(reason, "It can’t be used (%s)." % reason)


# ---------------------------------------------------------------- the work behind the page

def load(ui):
    cli, lang = ui.cli, ui.state.lang
    ui.run(lambda: cli.models(lang), lambda d: (setattr(ui, "models", d), ui.redraw()))


def newest(folder):
    """the newest model file in folder, or None"""
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".ghm")]
    except OSError:
        return None
    files = [f for f in files if os.path.isfile(f)]
    return max(files, key=os.path.getmtime) if files else None


def install(ui):
    from .ui import documents_dir
    f = newest(os.path.join(documents_dir(), FOLDER))
    if not f:
        ui.msg = "No model file (.ghm) in Documents › %s." % FOLDER
        ui.redraw()
        return
    cli, lang = ui.cli, ui.state.lang

    def done(r):
        if r[0] == "installed":
            what = "reading" if r[1] == "recognizer" else "neatening"
            ui.msg = "Installed %s for %s, in use from your next writing." % (os.path.basename(f), what)
        else:
            ui.msg = "%s isn’t installed. %s" % (os.path.basename(f), why(r[1], r[2], lang))
        load(ui)
    ui.run(lambda: cli.model_install(f, lang), done)


def use(ui, kind, choice):
    cli = ui.cli
    ui.run(lambda: cli.model_use(kind, choice), lambda _: load(ui))


def ask_remove(ui, kind):
    ui.confirm = ("Remove the model you installed?",
                  "The shipped model %s from your next writing. The file in Documents stays." %
                  ("reads" if kind == "recognizer" else "neatens"), "Remove", lambda: remove(ui, kind))
    ui.redraw()


def remove(ui, kind):
    ui.confirm = None
    cli, lang = ui.cli, ui.state.lang
    ui.run(lambda: cli.model_remove(kind, lang), lambda _: load(ui))


# ---------------------------------------------------------------- the page

def state_line(d, kind, lang):
    """(in use, the installed one's state) in words"""
    inuse, mine = d["inuse"].get(kind, "none"), d["local"].get(kind)
    chosen = d["use"].get(kind) == "local"
    now = {"local": "The one you installed is in use.", "shipped": "The shipped model is in use.",
           "none": "None is in use: this tablet ships none."}[inuse]
    if mine is None:
        return now, "Nothing installed." if not chosen else "Nothing installed, so the shipped model is in use."
    if mine[0] == "refused":
        return now, "The one you installed can’t be used. " + why(mine[1], mine[2], lang)
    return now, "Installed, %.1f MB." % (mine[1] / 1e6)


def models_page(cr, ui, hits, w, h):
    from .pages import chrome, fill, sub_head
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    lang = ui.state.lang
    y = sub_head(cr, ui, hits, w, y, "Models",
                 "Which model reads and neatens your %s writing: the shipped one, or one you installed yourself. "
                 "Yours is for you alone; guests always use the shipped one." % lang_name(lang))
    d = ui.models
    cw = w - 2 * PAD
    # turned: the two kinds side by side
    cols = 2 if LAND[0] else 1
    kw = (cw - 14 * (cols - 1)) / cols
    y0 = bottom = y
    for i, (kind, name, desc) in enumerate(KINDS):
        x = PAD + (i % cols) * (kw + 14)
        y = y0 if cols > 1 else bottom
        lbl(cr, k, name, x, y)
        y += 24
        cur = d["use"].get(kind, "shipped") if d else "shipped"
        mine = d["local"].get(kind) if d else None
        for choice, title in (("shipped", "Shipped model"), ("local", "The one you installed")):
            on = cur == choice
            sub = desc if choice == "shipped" else ("Nothing installed yet." if mine is None else
                                                     state_line(d, kind, lang)[1])
            _, th = PT.measure(cr, sub, k["sub"], maxw=kw - 76, wrap=True)
            ch = th + 54
            card(cr, k, x, y, kw, ch, on=on)
            radio(cr, k, x + 28, y + 28, on)
            PT.text(cr, title, k["row"], x + 52, y + 14, k.ink, weight=600, maxw=kw - 76)
            PT.text(cr, sub, k["sub"], x + 52, y + 40, k.ink if choice == "local" else k.soft, maxw=kw - 76,
                    wrap=True)
            if not on and d:
                hits.add(x, y, kw, ch, lambda kind=kind, choice=choice: use(ui, kind, choice))
            y += ch + 8
        if d:
            now, _ = state_line(d, kind, lang)
            rw = btn_w(cr, k, "Remove")
            PT.text(cr, now, k["sub"], x, y + 12, k.soft, maxw=kw - (rw + 14 if mine else 0))
            if mine:
                btn(cr, k, hits, x + kw - rw, y, "Remove", lambda kind=kind: ask_remove(ui, kind), w=rw)
        y += BTN_H + 16
        bottom = max(bottom, y)
    y = bottom
    # where a model comes from (spec 006 FR-021): a file the person has, under its terms
    text = ("Install takes the newest model file (.ghm) in Documents › %s. Install only a model you obtained "
            "yourself, and keep to the terms of the model and the handwriting it was trained on. It stays on this "
            "tablet: never in an export or a contribution." % FOLDER)
    _, th = PT.measure(cr, text, k["sub"], maxw=cw - 60, wrap=True)
    soft_box(cr, k, PAD, y, cw, th + 28)
    icon(cr, "info", 22, PAD + 24, y + 22, k.ink)
    PT.text(cr, text, k["sub"], PAD + 48, y + 14, k.ink, maxw=cw - 60, wrap=True)
    by = h - 20 - BTN_H
    left = btns_right(cr, k, hits, w - PAD, by, [("Install", lambda: install(ui), True)])
    if ui.msg:                  # what Install did, on the button's row
        _, mh = PT.measure(cr, ui.msg, k["sub"], maxw=left - 14 - PAD, wrap=True)
        PT.text(cr, ui.msg, k["sub"], PAD, by + (BTN_H - mh) / 2 if mh <= BTN_H else by + BTN_H - mh, k.ink, maxw=left - 14 - PAD, wrap=True)
