# SPDX-License-Identifier: GPL-2.0-or-later
"""The pages, as the design draws them (GlassineOS/ui designs/handwriting): the first-start
introduction, Write, Your font, Learn with Samples and Coverage, Contribute. Each paint
function draws one page into a cairo context and adds its tap targets; none reads or
writes anything itself, the UI object (ui.py) does."""
import math
import os
import time

import cairo
from glassine_ui import paint as PT

from . import fontview as FV, neat as N, prompts as P
from .look import (BTN_H, GUEST_TABS, LAND, PAD, TABS, bar, btn, btn_w, btns_right, card, checkbox, chevron_h, chevron_row, dialog, draw_ink,
                   footer, header, icon, icon_rows, ink_bounds, lbl, note, page_head, radio, sep, soft_box, tabs)

SUB = "Teach the tablet your hand"
SET_NAMES = {P.AUTO: "What needs practice", "words": "Words", "sentences": "Sentences", "pangrams": "Pangrams",
             "letters": "Single letters", "digits": "Digits and punctuation", "symbols": "Symbols",
             P.OWN: "My own words"}
SET_NAMES.update({"best": "Write it your best", "pick": "Which one is nicer?"})
SETS = ((P.AUTO, "A mix, weighted towards what it knows least about your hand", "auto_awesome"),
        ("words", "Common words, one at a time", "short_text"), ("sentences", "Short everyday sentences", "notes"),
        ("pangrams", "Sentences that use every letter", "abc"), ("letters", "Each letter, small and capital",
                                                               "match_case"),
        ("digits", "0 to 9, and marks like , . ? ! :", "pin"),
        ("symbols", "@ / & + # % _ = $, alone and in addresses", "alternate_email"),
        (P.OWN, "A list you keep in your files. Never contributed.", "person_edit"))
# a guest's sets (spec 002 FR-018): the shipped ones, and the careful samples, which are on Your
# font in the owner's session; no word list of their own, and no picks (they teach a font)
GSETS = ((P.AUTO, "A mix, weighted towards what you have written least", "auto_awesome"),
         *(x for x in SETS if x[0] not in (P.AUTO, P.OWN)),
         ("best", "A word written slowly and carefully, sometimes once quickly first", "draw"))
LICENCES = (("CC0-1.0", "Free for anyone", "CC0",
             "Anyone may use, change and share your samples for anything, with no conditions."),
            ("CC-BY-4.0", "Free, with a mention", "CC BY 4.0",
             "Anyone may use, change and share them, as long as they say the handwriting was contributed to "
             "GlassineOS. Your name is not in it."))
LIC = {k: (n, c) for k, n, c, _ in LICENCES}
# what a contribution holds, as hw_donation_write writes it (docs/samples.md)
HOLDS = ["The pen strokes you write for the words the app shows", "Those words themselves",
         "The size of the writing area, and which way the tablet was turned",
         "What the tablet read when you wrote it", "Quick or careful, and your picks (with both versions)"]
GHOLDS = HOLDS[:-1] + ["Quick or careful"]      # a guest makes no picks
# the contributor number is in the list nowhere: it has a card of its own (donor_row)
NEVER = ["Your name, or anything else about you", "Anything else about this tablet", "Dates and times",
         "Anything you write in other apps, or words from your own list unless you add them one by one",
         "A font made from your handwriting"]
GNEVER = ["Your name, or the name this tablet knows you by", "Your age, or anything else about you",
          "Anything about this tablet or its owner", "Dates and times", "Anything you write outside this session"]
WIZ = ("w-welcome", "w-try", "w-private", "w-learning", "w-share")
WIZ_NAMES = ("Welcome", "Try one", "Your samples", "Learning", "Contributing")
WIZ_STEP = {"w-try-done": 1, "w-licence": 4, "w-ready": 4}
PAPER_R = "16px"


def when(t, now=None):
    """Today, 14:02 / Yesterday / 12 Nov"""
    now = time.time() if now is None else now
    lt, ln = time.localtime(t), time.localtime(now)
    day = lambda x: (x.tm_year, x.tm_yday)
    if day(lt) == day(ln):
        return time.strftime("Today, %H:%M", lt)
    if day(lt) == day(time.localtime(now - 86400)):
        return "Yesterday"
    return time.strftime("%-d %b", lt) if lt.tm_year == ln.tm_year else time.strftime("%-d %b %Y", lt)


def weekday(t, now=None):
    """'today at 14:20', 'on Tuesday', 'on 12 Nov'"""
    now = time.time() if now is None else now
    if now - t < 86400 and time.localtime(t).tm_yday == time.localtime(now).tm_yday:
        return time.strftime("today at %H:%M", time.localtime(t))
    if now - t < 6 * 86400:
        return time.strftime("on %A", time.localtime(t))
    return time.strftime("on %-d %b", time.localtime(t))


def per100(v):
    return int(round(100 * float(v)))


def fill(cr, k, w, h):
    PT.fill(cr, 0, 0, w, h, k.paper)


def head(cr, ui, hits, w, sub=None, **kw):
    """the app's head; on its right the way into the other writers in the owner's session, or
    whose session it is and Finish in a guest's, on every page (spec 002 FR-015) -> height"""
    k = ui.theme
    if ui.guest:
        sub = "A guest’s session" if k.name == "white" else "a guest’s session"
        kw = {}
    h = header(cr, k, w, sub or SUB, **kw)
    if kw.get("steps") or ui.page == "others" or not ui.loaded and not ui.guest:
        return h
    bh = 44
    y = (h - bh) / 2
    if ui.guest:
        fw = btn_w(cr, k, "Finish")
        btn(cr, k, hits, w - PAD - fw, y, "Finish", ui.finish, pri=True, w=fw, h=bh)
        name_chip(cr, k, w - PAD - fw - 10, y, bh, ui.session.name, maxw=min(280, w - fw - 290))
    elif ui.page != "wizard":
        bw = btn_w(cr, k, "Someone else writes", "group")
        btn(cr, k, hits, w - PAD - bw, y, "Someone else writes", ui.open_others, icon_name="group", w=bw, h=bh)
    return h


def name_chip(cr, k, right, y, h, name, maxw=280):
    """the writer whose writing is collected: their name, ending at right"""
    tw = min(PT.measure(cr, name, k["row"], weight=600)[0], maxw - 56)
    cw = tw + 56
    PT.box(cr, right - cw, y, cw, h, k["btnR"], bg=k.paper, shadow="inset 0 0 0 2px " + k.ink)
    icon(cr, "person", 22, right - cw + 21, y + h / 2, k.ink)
    _, th = PT.measure(cr, name, k["row"], weight=600)
    PT.text(cr, name, k["row"], right - cw + 40, y + (h - th) / 2, k.ink, weight=600, maxw=tw + 1)
    return cw


def chrome(cr, ui, hits, w):
    """head and tabs of a page; -> the y below"""
    k = ui.theme
    y = head(cr, ui, hits, w)
    return tabs(cr, k, hits, w, y, ui.tab, ui.go, GUEST_TABS if ui.guest else TABS)


def msg_line(cr, ui, y, w):
    if ui.msg:
        PT.text(cr, ui.msg, ui.theme["sub"], PAD, y, ui.theme.ink, maxw=w - 2 * PAD, wrap=True)


def script_word(cr, k, text, x, y, px, col=None):
    """a sample word where no ink is at hand (the welcome's), in a hand-like face"""
    return PT.text(cr, text, "400 %dpx/1.1 'Kalam',cursive" % px, x, y, col or k.pen)


# ---------------------------------------------------------------- the writing pages
# Write, Your best and the wizard's Try one: a self-drawn top and bottom with the
# writing area (its own widget) between them. top() and bottom() return their height.


def dropdown(cr, k, hits, right, y, label, fn):
    tw, th = PT.measure(cr, label, k["sub"])
    w = tw + 52
    PT.box(cr, right - w, y, w, 40, k["btnR"], bg=k["btnBg"] if PT.color(k["btnBg"]) else k.paper, shadow=k["btnSh"])
    PT.text(cr, label, k["sub"], right - w + 14, y + (40 - th) / 2, k.ink)
    icon(cr, "expand_more", 22, right - 22, y + 20, k.ink)
    hits.add(right - w, y, w, 40, fn)


def chip(cr, k, x, y, label, on=False, ic=None, done=False, outline=True):
    f = k["sub"]
    tw, th = PT.measure(cr, label, f)
    w = tw + 20 + (24 if ic or done else 0)
    if on:
        st = k["segOn"]
        PT.box(cr, x, y, w, 32, k["segR"], bg=st["bg"], shadow=st["sh"])
        col = st["fg"]
    else:
        PT.box(cr, x, y, w, 32, k["segR"], bg=k.paper, shadow="inset 0 0 0 1.5px " + (k.ink if outline else k.rule))
        col = k.ink if outline or done else k.soft
    tx = x + 10
    if ic or done:
        icon(cr, ic or "check", 18, x + 20, y + 16, col)
        tx += 24
    PT.text(cr, label, f, tx, y + (32 - th) / 2, col, weight=600 if on else None)
    return w


NARROW = 480                                # a column this narrow stacks its parts


def shown_strip(cr, ui, hits, x, y, w):
    """the sample just written: small, with what was read; Redo, Discard -> height"""
    k = ui.theme
    wr = ui.writer
    sh = wr.shown if wr else None
    if not sh:
        return 0
    if sh.status == "check":
        return check_note(cr, ui, hits, x, y, w, sh)
    pair_quick = sh.prompt.written == "quick" and wr.best and wr.best.get("step") == "pair"
    narrow = w < NARROW
    h = 76 + (BTN_H + 10 if narrow else 0)
    card(cr, k, x, y, w, h)
    PT.box(cr, x + 10, y + 10, 130, 56, "8px", bg=k.sheet)
    draw_ink(cr, k, sh.strokes, x + 18, y + 14, 114, 48, 1.6)
    read = sh.reading[0][0] if sh.reading else ""
    if pair_quick:
        state, sub = "The quick one", "Kept. Now the careful one."
    elif sh.status == "kept":
        state = "Kept" + (" · in the contribution" if sh.donated_id else "")
        sub = "Read as “%s”" % read if read else "Nothing read"
    else:
        state = "Not kept: learning is off" if sh.why == "learning" else "Not kept"
        sub = "Read as “%s”" % read if read else ""
    tw = w - 154 - (12 if narrow else 200)
    PT.text(cr, state, k["row"], x + 154, y + 16, k.ink, maxw=tw)
    PT.text(cr, sub, k["sub"], x + 154, y + 40, k.soft, maxw=tw)
    items = [("Redo", lambda: ui.act("redo_quick" if pair_quick else "redo"), False)]
    if not pair_quick:
        items.append(("Discard", lambda: ui.act("discard"), False))
    btns_right(cr, k, hits, x + w - 12, y + 76 if narrow else y + (h - BTN_H) / 2, items)
    return h


def check_note(cr, ui, hits, x, y, w, sh, redo=False):
    """the ink didn't look like the prompt: Redo first, Keep it and Skip beside it. redo: Write
    it again's check, which keeps nothing then (spec 003 FR-033): only writing it again"""
    k = ui.theme
    read = sh.reading[0][0] if sh.reading else ""
    t1 = "This doesn’t quite look like “%s”." % sh.prompt.text
    if redo:
        t2 = ("The tablet read “%s”. " % read if read else "The tablet couldn’t read it. ") + \
            "It isn’t kept, and the old one stays as it was."
    else:
        t2 = ("The tablet read “%s”. That may be its mistake: if you wrote “%s”, keep it." % (read, sh.prompt.text)
              if read else "The tablet couldn’t read it. That may be its mistake: if you wrote “%s”, keep it."
              % sh.prompt.text)
    narrow = w < NARROW
    tx, tw = (x + 14, w - 28) if narrow else (x + 160, w - 174)
    ty = y + 82 if narrow else y + 14
    _, h1 = PT.measure(cr, t1, k["row"], maxw=tw, wrap=True)
    _, h2 = PT.measure(cr, t2, k["sub"], maxw=tw, wrap=True)
    h = (ty - y) + max(0 if narrow else 56, h1 + 4 + h2) + 16 + BTN_H + 14
    note(cr, k, x, y, w, h)
    PT.box(cr, x + 14, y + 14, 130, 56, "8px", bg=k.sheet)
    draw_ink(cr, k, sh.strokes, x + 22, y + 18, 114, 48, 1.6)
    PT.text(cr, t1, k["row"], tx, ty, k.ink, weight=600, maxw=tw, wrap=True)
    PT.text(cr, t2, k["sub"], tx, ty + h1 + 4, k.soft, maxw=tw, wrap=True)
    btns_right(cr, k, hits, x + w - 14, y + h - 14 - BTN_H, [("Write it again", ui.redo_again, True)] if redo else [
        ("Keep it", lambda: ui.act("keep_anyway"), False), ("Skip", lambda: ui.act("skip"), False),
        ("Redo", lambda: ui.act("redo"), True)])
    return h


def write_head(cr, ui, hits, w, h=None):
    """landscape: the head of a writing page across the whole width (the column below
    holds the rest) -> its height"""
    k = ui.theme
    if h:
        fill(cr, k, w, h)
    if ui.page == "wizard" and not ui.guest:
        return header(cr, k, w, "Getting started" if k.name == "white" else "getting started", steps=True, step_i=1)
    if ui.page == "wizard":
        return head(cr, ui, hits, w)
    return chrome(cr, ui, hits, w)


def write_top(cr, ui, hits, w, h=None, head=True):
    """Write and Your best: the head, the prompt, its set, the last sample"""
    k = ui.theme
    if h:
        fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w) if head else 0
    wr = ui.writer
    p = wr.prompt if wr else None
    best = bool(wr and wr.best is not None)
    y += 20
    if ui.done and not ui.guest and head:
        y += done_note(cr, ui, hits, PAD, y, w - 2 * PAD) + 20
    lbl(cr, k, ("Your best" if k.name == "white" else "your best") if best else k["writeThis"], PAD, y + 12)
    if best and ui.guest:               # a set there (spec 002 FR-018)
        dropdown(cr, k, hits, w - PAD, y, "Write it your best", ui.open_sets)
    elif best:
        dropdown(cr, k, hits, w - PAD, y, "Your font", ui.open_best)
    else:
        dropdown(cr, k, hits, w - PAD, y, SET_NAMES.get(ui.state.set, ui.state.set), ui.open_sets)
    y += 56
    if p is None:
        _, th = PT.text(cr, "Nothing to write in this set." + (" Add your own words." if ui.state.set == P.OWN else ""),
                        k["row"], PAD, y, k.ink, maxw=w - 2 * PAD, wrap=True)
        y += th + 16
    else:
        f = k["promptS"] if p.kind in ("sentence", "pangram") or len(p.text) > 16 or w < NARROW else k["prompt"]
        _, th = PT.text(cr, p.text, f, PAD, y, k.ink, maxw=w - 2 * PAD, wrap=True)
        y += th + 14
    if best and p is not None:
        step = wr.best.get("step")
        x = PAD
        quick = step == "quick"
        cw = chip(cr, k, x, y, "Quick sample" if quick else "Careful sample", ic="bolt" if quick else "draw") + 10
        if step in ("quick", "pair"):
            if w < NARROW:
                y += 40
            else:
                x += cw
            x += chip(cr, k, x, y, "1 Quick", on=quick, done=step == "pair", outline=step == "pair") + 6
            chip(cr, k, x, y, "2 Careful", on=step == "pair", outline=False)
        y += 44
        n = ("First, write it quickly, the way you would in a note." if quick else
             "Now once more, slowly. Write it the way you like it best." if step == "pair" else
             "Take your time. Write it the way you like it best.")
        _, th = PT.text(cr, n, k["sub"], PAD, y, k.ink, maxw=w - 2 * PAD, wrap=True)
        y += th + 14
    sh = shown_strip(cr, ui, hits, PAD, y, w - 2 * PAD)
    y += sh + (14 if sh else 0)
    return y


def write_bottom(cr, ui, hits, w, h, canvas):
    """the counts and Clear, Skip, Next (a narrow column puts the counts above) -> height"""
    k = ui.theme
    fill(cr, k, w, h)
    wr = ui.writer
    has_ink = bool(canvas and canvas.strokes)
    checking = bool(wr and wr.shown and wr.shown.status == "check")
    if wr and wr.best is not None:
        n = ui.careful_today()
        line = "%d careful sample%s today" % (n, "" if n == 1 else "s")
    elif ui.loaded and ui.guest:         # the writer's own counts; nothing about learning
        line = "Today %d · %d in all" % ui.counts
    elif ui.loaded:
        line = "Today %d · %d in all · %d new to learn from" % (ui.counts[0], ui.counts[1], ui.fresh)
    else:
        line = ""
    narrow = w < NARROW
    by = 46 if narrow else 16
    quick = bool(wr and wr.best and wr.best.get("step") == "quick")
    x = btns_right(cr, k, hits, w - PAD, by, [
        ("Clear", (lambda: canvas.clear()) if has_ink and not checking else None, False),
        ("Skip", ui.skip if not has_ink and not checking else None, False),
        ("Next: careful" if quick and not narrow else "Next", (lambda: canvas.submit()) if has_ink and not checking else None, True)])
    if narrow:
        PT.text(cr, line, k["sub"], PAD, 14, k.soft, maxw=w - 2 * PAD)
    else:
        PT.text(cr, line, k["sub"], PAD, by + 15, k.soft, maxw=x - PAD - 12)
    if ui.msg:
        PT.text(cr, ui.msg, k["sub"], PAD, by + BTN_H + 8, k.ink, maxw=w - 2 * PAD)
    return by + BTN_H + 20


def neat_top(cr, ui, hits, w, h=None, head=True):
    """Neaten my writing: the head and what it does"""
    k = ui.theme
    if h:
        fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w) if head else 0
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Your font", "Neaten my writing",
                  "Write anything below, in a hurry if you like. When you pause, it shows neater, in your own hand. "
                  "Nothing you write here is kept.", small=True, keep_lead=True)
    return y + 6


def neat_unlearned(ui):
    st = (ui.font or {}).get("status") or {}
    return ui.font is not None and st.get("state") == "none"


def neat_bottom(cr, ui, hits, w, h, canvas):
    """the strength, Neatened or Original, Clear, what was read, the letters' state -> height"""
    k = ui.theme
    fill(cr, k, w, h)
    has_ink = bool(canvas and canvas.strokes)
    y = 16
    unl = neat_unlearned(ui)
    if ui.letters_progress() is not None:
        _, th = PT.text(cr, "Learning your letters… %d%%" % round(100 * ui.letters_progress()), k["sub"], PAD, y,
                        k.ink, maxw=w - 2 * PAD)
        y += th + 10
    elif unl:
        t = "Letters not learned yet: for now it only straightens the lines and evens out sizes and spacing."
        bw = btn_w(cr, k, "Learn letters now")
        _, th = PT.measure(cr, t, k["sub"], maxw=w - 2 * PAD - bw - 50, wrap=True)
        nh = max(th, BTN_H) + 24
        PT.box(cr, PAD, y, w - 2 * PAD, nh, k["cardR"], bg=k.sheet)
        icon(cr, "info", 22, PAD + 24, y + nh / 2, k.ink)
        PT.text(cr, t, k["sub"], PAD + 48, y + (nh - th) / 2, k.ink, maxw=w - 2 * PAD - bw - 50, wrap=True)
        btn(cr, k, hits, w - PAD - bw - 12, y + (nh - BTN_H) / 2, "Learn letters now", ui.build_letters, w=bw)
        y += nh + 12
    lbl(cr, k, "Strength" if k.name == "white" else "strength", PAD, y)
    y += 22
    segs(cr, k, hits, PAD, y, w - 2 * PAD,
         [(s, name, None if ui.neat_orig else (lambda s=s: ui.set_strength(s, canvas.payload())))
          for s, name in N.STRENGTHS], None if ui.neat_orig else
         next((s for s, _ in N.STRENGTHS if abs(ui.neat_strength - s) < 1e-6), None))
    y += BTN_H + 12
    segs(cr, k, hits, PAD, y, 200, [(False, "Neatened", lambda: ui.show_original(False)),
                                    (True, "Original", lambda: ui.show_original(True))], ui.neat_orig)
    btns_right(cr, k, hits, w - PAD, y, [("Clear", (lambda: canvas.clear()) if has_ink else None, False)])
    y += BTN_H + 14
    if ui.msg:
        _, th = PT.text(cr, ui.msg, k["sub"], PAD, y, k.ink, maxw=w - 2 * PAD, wrap=True)
        y += th + 8
    elif ui.neat_busy:
        PT.text(cr, "Neatening…", k["row"], PAD, y, k.soft, maxw=w - 2 * PAD)
        y += 28
    elif ui.neat_text:
        _, th = PT.text(cr, "Read as “%s”" % ui.neat_text, k["row"], PAD, y, k.ink, weight=600, maxw=w - 2 * PAD)
        y += th + 2
        _, th = PT.text(cr, "Showing what you wrote. Neatened brings the neat version back." if ui.neat_orig else
                        "Each letter keeps its shape. Nothing here becomes a sample." if unl else
                        "Shown in your own letters. Nothing here becomes a sample.", k["sub"], PAD, y, k.ink,
                        maxw=w - 2 * PAD)
        y += th + 10
    else:
        _, th = PT.text(cr, "Nothing here becomes a sample.", k["sub"], PAD, y, k.ink, maxw=w - 2 * PAD)
        y += th + 10
    btn(cr, k, hits, PAD, y, "Your font", ui.open_best, icon_name="arrow_back")
    return y + BTN_H + 16


def try_top(cr, ui, hits, w, h=None, head=True):
    """the wizard's Try one: a real first prompt"""
    k = ui.theme
    if h:
        fill(cr, k, w, h)
    if head:
        y = wizard_head(cr, ui, hits, w, 1)
    else:
        lbl(cr, k, k.eyebrow(2, WIZ_NAMES[1], 3 if ui.guest else 5), PAD, 26)
        y = 52
    y = page_head(cr, k, PAD, y, w - 2 * PAD, None, "Try one now", "Write the word on the line below, the way you "
                  "always write.", small=True)
    p = ui.writer.prompt if ui.writer else None
    lw, lh = lbl(cr, k, k["writeThis"], PAD, y + 22)
    if p:
        PT.text(cr, p.text, k["prompt"] if w >= NARROW else k["promptS"], PAD + lw + 18, y, k.ink,
                maxw=w - 2 * PAD - lw - 18)
    return y + 74


def try_bottom(cr, ui, hits, w, h, canvas, foot=True):
    k = ui.theme
    fill(cr, k, w, h)
    wr = ui.writer
    sh = wr.shown if wr else None
    y = 14
    has_ink = bool(canvas and canvas.strokes)
    if ui.tried and sh:
        read = sh.reading[0][0] if sh.reading else ""
        if sh.status == "check":
            y += check_note(cr, ui, hits, PAD, y, w - 2 * PAD, sh) + 12
        else:
            t1 = ("Read as “%s”. That’s your first sample." % read) if read else "That’s your first sample."
            if sh.status != "kept":
                t1 = "Written, but not kept: learning is off." if sh.why == "learning" else "Written, but not kept."
            t2 = "The next word would be up already. Here, Continue goes on."
            tw = w - 2 * PAD - 70
            _, h1 = PT.measure(cr, t1, k["row"], maxw=tw, wrap=True)
            _, h2 = PT.measure(cr, t2, k["sub"], maxw=tw, wrap=True)
            ch = max(62, h1 + h2 + 24)
            card(cr, k, PAD, y, w - 2 * PAD, ch)
            icon(cr, "check_circle", 24, PAD + 26, y + 31, k.ink)
            PT.text(cr, t1, k["row"], PAD + 52, y + 10, k.ink, maxw=tw, wrap=True)
            PT.text(cr, t2, k["sub"], PAD + 52, y + 12 + h1, k.soft, maxw=tw, wrap=True)
            y += ch + 12
    elif ui.tried:
        PT.text(cr, "Reading it…", k["sub"], PAD, y + 15, k.soft)
        y += 62
    else:
        hint = "When you’re done, pause for a moment or tap Next."
        if w < NARROW:
            _, th = PT.text(cr, hint, k["sub"], PAD, y, k.soft, maxw=w - 2 * PAD, wrap=True)
            y += th + 10
        x = btns_right(cr, k, hits, w - PAD, y, [("Clear", (lambda: canvas.clear()) if has_ink else None, False),
                                                 ("Next", (lambda: canvas.submit()) if has_ink else None, True)])
        if w >= NARROW:
            PT.text(cr, hint, k["sub"], PAD, y + 15, k.soft, maxw=x - PAD - 10)
        y += BTN_H + 14
    rows = [("undo", "Slipped? Clear starts again. After Next, the last one stays on screen with Redo, in case you "
             "change your mind.", ""),
            ("help", "If what you wrote doesn’t look like the word, it asks: Keep it, Redo or Skip. Any answer is "
             "fine.", "")]
    y = tips(cr, k, PAD, y, w - 2 * PAD, rows)
    if foot:
        wizard_foot(cr, ui, hits, w, h)
    return y


def tips(cr, k, x, y, w, rows):
    sep(cr, k, x, y, w)
    y += 10
    for ic, text, _ in rows:
        _, th = PT.text(cr, text, k["sub"], x + 34, y, k.ink, maxw=w - 34, wrap=True)
        icon(cr, ic, 20, x + 10, y + 10, k.ink)
        y += th + 10
    return y


WIZ_FOOT = 84


GWIZ_NAMES = ("Hello", "Try one", "Contributing")       # a guest's introduction (spec 002 FR-019)
GWIZ_STEP = {"g-hello": 0, "w-try": 1, "w-share": 2, "g-age": 2, "w-licence": 2}


def wizard_head(cr, ui, hits, w, i):
    k = ui.theme
    if ui.guest:                            # whose session it is and Finish, from the first step on
        hh = head(cr, ui, hits, w)
        i = GWIZ_STEP.get(ui.wstep, 0)
        lbl(cr, k, k.eyebrow(i + 1, GWIZ_NAMES[i], 3), PAD, hh + 26)
        return hh + 52
    hh = header(cr, k, w, "Getting started" if k.name == "white" else "getting started", steps=True, step_i=i,
                done=ui.wstep == "w-ready")
    if ui.wstep == "w-ready":
        return hh + 40
    lbl(cr, k, k.eyebrow(i + 1, WIZ_NAMES[i]), PAD, hh + 26)
    return hh + 52


def guest_foot(cr, ui, hits, w, h):
    """a guest's introduction: no Back on Hello, Skip to writing up to Try one, Not now beside
    Continue on 18 or older"""
    k = ui.theme
    s = ui.wstep
    land = w > h
    status = {"w-share": "Either way is fine; change it any time in Contribute." if land else "Your choice, either way",
              "w-licence": "" if ui.licence_pick else "Pick one to go on"}.get(s, "")
    label = {"g-age": "Continue to the licence", "w-licence": "Contribute with this licence"}.get(s, "Continue")
    nxt = ui.wizard_next
    if s == "g-age" and not ui.adult or s == "w-licence" and not ui.licence_pick:
        nxt = None
    if s == "w-try" and ui.tried and not (ui.writer and ui.writer.shown):
        nxt = None                       # its reading is still coming
    if s == "w-share":
        label = None
    choices = []
    if s == "w-share" and land:
        choices = [("Not now", "schedule", ui.wizard_decline),
                   ("Yes, I’d like to contribute", "volunteer_activism", lambda: ui.wizard_to("g-age"))]
    elif s == "g-age":
        choices = [("Not now", "schedule", ui.wizard_decline)]
    footer(cr, k, hits, w, h - WIZ_FOOT, WIZ_FOOT, choices=choices,
           back=ui.guest_back if s != "g-hello" else None, status=status,
           skip=ui.wizard_skip if s in ("g-hello", "w-try") else None, next_=nxt, next_label=label)


def wizard_foot(cr, ui, hits, w, h):
    if ui.guest:
        return guest_foot(cr, ui, hits, w, h)
    k = ui.theme
    s = ui.wstep
    back = {"w-try": "w-welcome", "w-try-done": "w-welcome", "w-private": "w-try", "w-learning": "w-private",
            "w-share": "w-learning", "w-licence": "w-share", "w-ready": "w-share"}.get(s)
    land = w > h
    status = {"w-share": "Change your mind any time in Contribute." if land else "Your choice, either way", "w-licence": "" if ui.licence_pick else "Pick one to go on"}.get(s, "")
    label = {"w-welcome": "Begin", "w-licence": "Contribute with this licence", "w-ready": "Start writing"}.get(s, "Continue")
    nxt = ui.wizard_next if (s != "w-licence" or ui.licence_pick) else None
    if s == "w-try" and ui.tried and not (ui.writer and ui.writer.shown):
        nxt = None                       # its reading is still coming
    if s == "w-share":
        label = None
    choices = [("Not now", "schedule", ui.wizard_decline), ("Yes, I’d like to contribute", "volunteer_activism",
                                                         lambda: ui.wizard_to("w-licence"))] if s == "w-share" and land else []
    footer(cr, k, hits, w, h - WIZ_FOOT, WIZ_FOOT, choices=choices, back=(lambda: ui.wizard_to(back)) if back else None,
           status=status, skip=ui.wizard_skip if s not in ("w-share", "w-licence", "w-ready") else None,
           next_=nxt, next_label=label, next_icon="edit" if s == "w-ready" else "arrow_forward")


# ---------------------------------------------------------------- the wizard's other steps


def wizard_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    s = ui.wstep
    i = WIZ_STEP.get(s, WIZ.index(s) if s in WIZ else 0)
    y = wizard_head(cr, ui, hits, w, i)
    cw = w - 2 * PAD
    if ui.guest and s in ("g-hello", "g-age", "w-share"):
        guest_step(cr, ui, hits, w, h, y)
        return wizard_foot(cr, ui, hits, w, h)
    if s == "w-welcome":
        y = page_head(cr, k, PAD, y, cw, None, "Teach this tablet your handwriting",
                      "You write a few words it shows you. It learns how you write, so it reads you better everywhere "
                      "you write with the pen.")
        land = w > h
        bw = (cw - 20) / 2 if land else cw          # landscape: the sample beside the three points
        soft_box(cr, k, PAD, y, bw, 214)
        lbl(cr, k, k["writeThis"], PAD + 18, y + 18)
        PT.text(cr, "quiet", k["promptS"], PAD + 18, y + 40, k.ink)
        PT.box(cr, PAD + 18, y + 80, bw - 36, 94, PAPER_R, bg=k.paper, shadow="inset 0 0 0 1.5px " + k.ink)
        cr.save()
        cr.set_source_rgba(*PT.color(k.rule))
        cr.set_dash([3, 3])
        cr.move_to(PAD + 42, y + 130)
        cr.line_to(PAD + bw - 42, y + 130)
        cr.stroke()
        cr.restore()
        script_word(cr, k, "quiet", PAD + 48, y + 84, 40)
        PT.text(cr, "One word at a time, on lines like the keyboard’s writing paper.", k["sub"], PAD + 18, y + 186,
                k.soft, maxw=bw - 36)
        rx, ry = (PAD + bw + 20, y) if land else (PAD, y + 232)
        icon_rows(cr, k, rx, ry, bw, [
            ("edit", "Write what it shows", "Letters, words and short sentences, one at a time. A few minutes now and "
             "then is plenty."),
            ("school", "It learns your hand", "Over time it reads your writing better: here, and on the keyboard’s "
             "writing paper."),
            ("lock", "It stays on this tablet", "Your writing is kept here and nowhere else, unless you export or "
             "contribute it.")])
    elif s in ("w-private", "w-learning"):
        if s == "w-private":
            y = page_head(cr, k, PAD, y, cw, None, "Your samples stay here",
                          "Each word you write is kept as a sample. They belong to you.", small=True)
            art = ("tablet", "Kept on this tablet only. The app doesn’t use the internet.")
            rows = [("grid_view", "Look at any sample", "Samples, in Learn, shows everything you have written here."),
                    ("delete", "Delete what you don’t want", "One at a time, or all at once."),
                    ("folder", "Take a copy with you", "Export saves them all as one file in your Documents."),
                    ("volunteer_activism", "Share only if you choose to", "Next, you can decide about contributing. It is "
                     "off unless you turn it on.")]
        else:
            y = page_head(cr, k, PAD, y, cw, None, "Learning happens on its own",
                          "You write; the tablet does the rest, at a time that suits it.", small=True)
            art = ("battery_charging_full", "It learns while the tablet is charging and resting. There’s nothing to "
                   "start and nothing to wait for.")
            need = ui.need()
            rows = [("edit_note", "It starts after about %d samples" % need, "About five minutes of writing, spread "
                     "over as many days as you like."),
                    ("fact_check", "It checks itself first", "It sets a few of your samples aside and keeps what it "
                     "learned only if it now reads those better."),
                    ("history", "You can always go back", "Learn shows how it’s going, and keeps the version before so "
                     "you can return to it.")]
        _, th = PT.measure(cr, art[1], k["row"], maxw=cw - 100, wrap=True)
        ah = max(84, th + 40)
        soft_box(cr, k, PAD, y, cw, ah)
        icon(cr, art[0], 40, PAD + 42, y + ah / 2, k.ink)
        PT.text(cr, art[1], k["row"], PAD + 84, y + (ah - th) / 2, k.ink, maxw=cw - 100, wrap=True)
        icon_rows(cr, k, PAD, y + ah + 16, cw, rows)
    elif s == "w-share":
        y = page_head(cr, k, PAD, y, cw, None, "Would you like to contribute your samples?",
                      "The handwriting reader in GlassineOS is open source. It learns to read everyone from handwriting "
                      "that people choose to contribute. Your samples could help it, if you like.", small=True)
        y = holds_lists(cr, k, PAD, y, cw) + 14
        y = how_note(cr, k, PAD, y, cw, "outgoing_mail", "Nothing is sent by itself. Contributing sets a copy aside on the "
                     "tablet. When you like, the app saves it as one file in your Documents, and you send that file to "
                     "the project yourself.") + 18
        if w > h:
            return wizard_foot(cr, ui, hits, w, h)       # the two answers are in the foot
        bw = (cw - 14) / 2
        btn(cr, k, hits, PAD, y, "Not now", ui.wizard_decline, icon_name="schedule", w=bw, h=60)
        btn(cr, k, hits, PAD + bw + 14, y, "Yes, I’d like to contribute", lambda: ui.wizard_to("w-licence"),
            icon_name="volunteer_activism", w=bw, h=60)
        PT.text(cr, "Either way is fine. You can change your mind any time in Contribute.", k["sub"], w / 2, y + 76, k.soft,
                align=1)
    elif s == "w-licence":
        y = page_head(cr, k, PAD, y, cw, None, "How may others use your samples?",
                      "Pick the licence for your contribution. Both let anyone use it; you choose once.", small=True)
        licence_cards(cr, ui, hits, PAD, y, cw, ui.licence_pick, lambda key: ui.pick_licence(key))
    elif s == "w-ready":
        brand = icon(cr, "stylus_note", 48, PAD + 34, y + 24, k.ink) or icon(cr, "draw", 48, PAD + 34, y + 24, k.ink)
        y += 70
        lbl(cr, k, k["readyEyebrow"], PAD, y)
        y = page_head(cr, k, PAD, y + 24, cw, None, "Ready when you are.",
                      "Write a few words whenever you like. A handful today and a handful tomorrow is just as good as "
                      "many at once.", small=True)
        lic = ui.donation[0]
        don = ("On · %s (%s)" % LIC[lic] if ui.state.donating and lic in LIC else
               "Off · you can turn it on any time in Contribute")
        sep(cr, k, PAD, y, cw)
        for ic, name, value in (("school", "Learning", "On its own, while the tablet charges"),
                                ("lock", "Your samples", "Kept on this tablet"), ("volunteer_activism", "Contributing", don)):
            icon(cr, ic, 22, PAD + 12, y + 30, k.ink)
            lbl(cr, k, name, PAD + 40, y + 22)
            PT.text(cr, value, k["row"], PAD + 170, y + 18, k.ink, maxw=cw - 170)
            y += 60
            sep(cr, k, PAD, y, cw)
        icon(cr, "draw", 18, PAD + 10, y + 26, k.soft)
        PT.text(cr, "You can also make a font of your handwriting, any time, in Your font.", k["sub"], PAD + 32, y + 16,
                k.soft, maxw=cw - 32)
    wizard_foot(cr, ui, hits, w, h)


def guest_step(cr, ui, hits, w, h, y):
    """a guest's Hello, the question asked of them, and 18 or older (spec 002 FR-019): it
    promises nothing a guest's session doesn't do, and says plainly what the owner can do"""
    k = ui.theme
    cw = w - 2 * PAD
    s = ui.wstep
    land = w > h
    name = ui.session.name or ""
    if s == "g-hello":
        y = page_head(cr, k, PAD, y, cw, None, "Hello, " + name, None if land else
                      "The tablet’s owner has asked you to write a few words here. This is what happens to them.",
                      small=land)
        if not land:
            art = "This is %s’s session. What you write here is kept apart from the owner’s writing." % name
            wt = 600 if k.name == "white" else 400
            _, th = PT.measure(cr, art, k["row"], weight=wt, maxw=cw - 110, wrap=True)
            ah = max(88, th + 40)
            soft_box(cr, k, PAD, y, cw, ah)
            icon(cr, "person", 44, PAD + 46, y + ah / 2, k.ink)
            PT.text(cr, art, k["row"], PAD + 92, y + (ah - th) / 2, k.ink, weight=wt, maxw=cw - 110, wrap=True)
            y += ah + 18
        icon_rows(cr, k, PAD, y, cw, [
            ("edit", "Write what it shows", "A word or a short sentence at a time. Stop whenever you like: Finish, at the "
             "top, hands the tablet back."),
            ("inventory_2", "Your samples are kept here, apart", "On this tablet, in a place of their own, apart from the "
             "owner’s and from anyone else’s."),
            ("block", "Nothing here learns from them", "Your writing isn’t used to read anyone’s handwriting on this "
             "tablet."),
            ("visibility", "The owner can open them", "There is no password. The tablet’s owner can open your session and "
             "your samples without you, and remove them."),
            ("volunteer_activism", "Contributing is up to you", "Next, you can choose whether to contribute your samples. "
             "It is off unless you turn it on.")])
    elif s == "g-age":
        y = page_head(cr, k, PAD, y, cw, None, "Are you 18 or older?", "Please answer this yourself, before you choose a "
                      "licence.", small=land)
        sub = "Only an adult can agree to a licence for their own handwriting."
        _, th = PT.measure(cr, sub, k["sub"], maxw=cw - 100, wrap=True)
        bh = max(80, th + 52)
        card(cr, k, PAD, y, cw, bh, on=ui.adult)
        checkbox(cr, k, PAD + 18, y + (bh - 30) / 2, ui.adult, sz=30)
        PT.text(cr, "I am 18 or older", k["row"], PAD + 64, y + 16, k.ink, weight=600, maxw=cw - 84)
        PT.text(cr, sub, k["sub"], PAD + 64, y + 42, k.soft, maxw=cw - 84, wrap=True)
        hits.add(PAD, y, cw, bh, ui.toggle_adult)
        y += bh + 18
        icon_rows(cr, k, PAD, y, cw, [
            ("visibility_off", "Your answer isn’t kept", "It is asked once, here, and saved nowhere: not with your samples "
             "and not in a contribution."),
            ("schedule", "Younger, or not sure?", "Choose Not now. You go on writing as before; nothing of yours is "
             "contributed."),
            ("volunteer_activism", "You can come back to it", "Contribute offers this question again, whenever you like.")])
    else:
        y = page_head(cr, k, PAD, y, cw, None, "Would you like to contribute your samples?",
                      "The handwriting reader in GlassineOS is open source. It learns to read everyone from handwriting "
                      "that people choose to contribute. Only you can say yes for your samples.", small=True)
        y = holds_lists(cr, k, PAD, y, cw, never=GNEVER) + 14
        y = how_note(cr, k, PAD, y, cw, "outgoing_mail", "Nothing is sent by itself. Contributing sets a copy aside on the "
                     "tablet. Finish, or Save as a file, puts it in one file in this tablet’s Documents, for the owner to "
                     "send to the project.") + 18
        if land:
            return
        bw = (cw - 14) / 2
        btn(cr, k, hits, PAD, y, "Not now", ui.wizard_decline, icon_name="schedule", w=bw, h=60)
        btn(cr, k, hits, PAD + bw + 14, y, "Yes, I’d like to contribute", lambda: ui.wizard_to("g-age"),
            icon_name="volunteer_activism", w=bw, h=60)
        PT.text(cr, "Either way is fine. You can change your mind any time in Contribute.", k["sub"], w / 2, y + 76, k.soft,
                align=1)


def holds_lists(cr, k, x, y, w, never=NEVER, holds=HOLDS):
    """what a contribution holds and what it never holds, side by side; -> the y below"""
    cw = (w - 14) / 2
    hs = []
    for head, items in (("What a contribution holds", holds), ("What it never holds", never)):
        hh = 40
        for it in items:
            hh += PT.measure(cr, it, k["sub"], maxw=cw - 52, wrap=True)[1] + 10
        hs.append(hh + 8)
    h = max(hs)
    for i, (head, items, ic) in enumerate((("What a contribution holds", holds, "check"),
                                          ("What it never holds", never, "remove"))):
        bx = x + i * (cw + 14)
        card(cr, k, bx, y, cw, h)
        lbl(cr, k, head, bx + 12, y + 14)
        sep(cr, k, bx + 12, y + 34, cw - 24)
        yy = y + 44
        for it in items:
            icon(cr, ic, 18, bx + 22, yy + 10, k.ink)
            _, th = PT.text(cr, it, k["sub"], bx + 40, yy, k.ink, maxw=cw - 52, wrap=True)
            yy += th + 10
    return y + h


def how_note(cr, k, x, y, w, ic, text):
    _, th = PT.measure(cr, text, k["sub"], maxw=w - 60, wrap=True)
    h = th + 28
    soft_box(cr, k, x, y, w, h)
    icon(cr, ic, 22, x + 24, y + 22, k.ink)
    PT.text(cr, text, k["sub"], x + 48, y + 14, k.ink, maxw=w - 60, wrap=True)
    return y + h


def licence_cards(cr, ui, hits, x, y, w, cur, pick):
    k = ui.theme
    for key, name, code, desc in LICENCES:
        on = cur == key
        _, th = PT.measure(cr, desc, k["sub"], maxw=w - 76, wrap=True)
        h = th + 54
        card(cr, k, x, y, w, h, on=on)
        radio(cr, k, x + 28, y + 28, on)
        nw, _ = PT.text(cr, name, k["row"], x + 52, y + 14, k.ink, weight=600)
        lbl(cr, k, code, x + 52 + nw + 10, y + 18, k.soft)
        PT.text(cr, desc, k["sub"], x + 52, y + 40, k.ink, maxw=w - 76, wrap=True)
        hits.add(x, y, w, h, lambda key=key: pick(key))
        y += h + 12
    PT.text(cr, "To pick the other licence later, erase the contribution in Contribute and start a new one.", k["sub"], x, y,
            k.soft, maxw=w, wrap=True)
    return y + 30


# ---------------------------------------------------------------- What to write


def sets_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    g = ui.guest
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Write", "What to write",
                  "Choose what the prompts are made of. “What needs practice” mixes everything and favours the letters "
                  "you have written least" + ("." if g else ", or that it reads wrong most often."), small=True,
                  keep_lead=True)
    cols = 2 if LAND[0] else 1
    cw = (w - 2 * PAD - 10 * (cols - 1)) / cols
    y0 = y
    sets = GSETS if g else SETS
    rh = 54 if g else 62
    for i, (name, desc, ic) in enumerate(sets):
        best = bool(ui.writer and ui.writer.best is not None)
        on = name == "best" if best else ui.state.set == name
        n = len(ui.writer.sets.get(name, [])) if ui.writer and name not in (P.AUTO,) else None
        x, y = PAD + (i % cols) * (cw + 10), y0 + (i // cols) * (rh + 4)
        if on:
            st = k["sel"]
            PT.box(cr, x, y, cw, rh, k["rowR"], bg=st["bg"], shadow=st["sh"])
            icon(cr, "check", 22, x + cw - 22, y + rh / 2, k.ink)
        icon(cr, ic, 22, x + 20, y + rh / 2, k.ink)
        label = SET_NAMES[name] + ("" if n is None or name != P.OWN else "  (%d)" % n)
        PT.text(cr, label, k["row"], x + 48, y + rh / 2 - 21, k.ink, maxw=cw - 100)
        PT.text(cr, desc, k["sub"], x + 48, y + rh / 2 + 3, k.soft, maxw=cw - 100)
        hits.add(x, y, cw, rh, lambda name=name: ui.choose_set(name))
    y = y0 + ((len(sets) + cols - 1) // cols) * (rh + 4)
    if g:                                # what a guest's session doesn't have, said once
        how_note(cr, k, PAD, y + 6, w - 2 * PAD, "info", "No learning, font, neatening or own word list here: those are "
                 "the owner’s.")
        btn(cr, k, hits, PAD, h - 20 - BTN_H, "Back", ui.close_sets, icon_name="arrow_back")
        return
    path = P.own_words_path()
    home = os.path.expanduser("~")
    path = "~" + path[len(home):] if path.startswith(home + "/") else path
    PT.text(cr, "Your own words are in %s, one per line." % path, k["sub"], PAD, y + 10, k.soft,
            maxw=w - 2 * PAD, wrap=True)
    btn(cr, k, hits, PAD, h - 20 - BTN_H, "Back", ui.close_sets, icon_name="arrow_back")


# ---------------------------------------------------------------- Samples


def grid_cols(w):
    return 4 if w > 900 else 3


def sample_tile(cr, ui, hits, x, y, tw, th, sid, t, text, fn, mark=None, sel=None):
    """one sample in a grid: its ink (once fetched), when, the donation's mark or a checkbox"""
    k = ui.theme
    on = sel is None or sel
    PT.box(cr, x, y, tw, th, k["cellR"] if k.name != "white" else "10px", bg=k.sheet if on else k.paper,
           shadow=k["cardFlat"] if on else "inset 0 0 0 1px " + k.rule)
    foot = 26
    smp = ui.inks.get(sid)
    if smp and smp.get("strokes"):
        draw_ink(cr, k, smp["strokes"], x + 10, y + 8, tw - 20, th - foot - 16, 1.6)
    else:
        PT.text(cr, text, k["row"], x + 10, y + 12, k.soft, maxw=tw - 20)
    PT.fill(cr, x + 1.5, y + th - foot - 1.5, tw - 3, foot, k.paper)
    PT.rule(cr, x, y + th - foot - 2, tw, "1px solid " + k.ink)
    PT.text(cr, when(t) if sel is not False else "Left out", k["sub"], x + 10, y + th - foot + 3, k.ink if on else k.soft,
            maxw=tw - 44)
    if sel is not None:
        checkbox(cr, k, x + tw - 30, y + th - foot + 1, sel, sz=20)
    elif mark:
        icon(cr, "volunteer_activism", 18, x + tw - 18, y + th - foot / 2 - 2, k.ink)
    hits.add(x, y, tw, th, fn)


def pager(cr, ui, hits, x, y, page, pages, go):
    k = ui.theme
    btn(cr, k, hits, x, y, "", (lambda: go(-1)) if page > 0 else None, icon_name="chevron_left", w=BTN_H)
    lw, _ = PT.text(cr, "Page %d of %d" % (page + 1, pages), k["mono"], x + BTN_H + 12, y + 16, k.ink)
    btn(cr, k, hits, x + BTN_H + 24 + lw, y, "", (lambda: go(1)) if page < pages - 1 else None,
        icon_name="chevron_right", w=BTN_H)


def sub_head(cr, ui, hits, w, y, title, lead):
    """the head of a page beneath a tab (Samples, Coverage): no eyebrow, the title with the way
    back beside it (to Learn; a guest's Samples to Contribute), the lead (dropped when the
    tablet is turned) -> the y below"""
    k = ui.theme
    back = "Contribute" if ui.guest else "Learn"
    bw = btn_w(cr, k, back, "arrow_back")
    tw = w - 2 * PAD - bw - 14
    _, th = PT.measure(cr, title, k["displayS"], maxw=tw, wrap=True)
    y += 18
    PT.text(cr, title, k["displayS"], PAD, y + max(0, (BTN_H - th) / 2), k.ink, maxw=tw, wrap=True,
            ls=PT.ls_em(k["displayLs"]))
    btn(cr, k, hits, w - PAD - bw, y + max(0, (th - BTN_H) / 2), back, lambda: ui.go("donate" if ui.guest else "learn"),
        icon_name="arrow_back", w=bw)
    y += max(th, BTN_H) + 8
    if lead and not LAND[0]:
        _, lh = PT.text(cr, lead, k["lead"], PAD, y, k.soft, maxw=w - 2 * PAD, wrap=True)
        y += lh + 4
    return y + 14


def samples_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    if ui.detail:
        return sample_detail(cr, ui, hits, w, h, y)
    rows = list(reversed(ui.samples))
    n = len(rows)
    y = sub_head(cr, ui, hits, w, y, "%d sample%s" % (n, "" if n == 1 else "s"),
                 ("Everything you have written here, newest first. Tap one to look at it or delete it." +
                  (" Export takes a copy of them with you, as one file." if ui.guest else "")) if n else
                 "Nothing written yet. Write shows the first prompt.")
    cols = grid_cols(w)
    gap = 10
    tw = (w - 2 * PAD - gap * (cols - 1)) / cols
    th = 82
    per_col = max(1, int((h - y - 100 - (0 if ui.guest else 30)) // (th + gap)))
    per = cols * per_col
    pages = max(1, (n + per - 1) // per)
    ui.sp = min(ui.sp, pages - 1)
    shown = rows[ui.sp * per:(ui.sp + 1) * per]
    ui.want_inks([r[0] for r in shown])
    for i, (sid, t, text) in enumerate(shown):
        sample_tile(cr, ui, hits, PAD + (i % cols) * (tw + gap), y + (i // cols) * (th + gap), tw, th, sid, t, text,
                    lambda sid=sid: ui.open_sample(sid), mark=sid in ui.donated)
    by = h - 20 - BTN_H
    if pages > 1:
        pager(cr, ui, hits, PAD, by, ui.sp, pages, ui.samples_page)
    # a guest store takes no import; the owner's Erase all and Export are the owner's samples
    # alone (spec 002 FR-024)
    btns_right(cr, k, hits, w - PAD, by, ([] if ui.guest else [("Import", ui.import_latest, False)]) + [
        ("Export", ui.export if n else None, False), ("Erase all", ui.ask_erase_all if n else None, False)])
    if ui.guest:
        msg_line(cr, ui, by - 30, w)
        return
    icon(cr, "group", 20, PAD + 10, by - 22, k.soft)
    PT.text(cr, "Only your samples. Other writers’ are kept apart, in their own sessions.", k["sub"], PAD + 30, by - 32,
            k.soft, maxw=w - 2 * PAD - 30)
    msg_line(cr, ui, by - 62, w)


def orientation(a):
    rot = (a or {}).get("orientation", 0)
    return {0: "upright", 90: "turned to the right", 180: "upside down", 270: "turned to the left"}.get(rot, "upright")


def sample_detail(cr, ui, hits, w, h, y):
    k = ui.theme
    sid, smp = ui.detail
    t = next((r[1] for r in ui.samples if r[0] == sid), None)
    y += 16
    ph = 260 if h > 800 else 200
    PT.box(cr, PAD, y, w - 2 * PAD, ph, PAPER_R, bg=k.paper, shadow="inset 0 0 0 1.5px " + k.ink)
    draw_ink(cr, k, smp.get("strokes", []), PAD + 24, y + 20, w - 2 * PAD - 48, ph - 40, 2.6)
    y += ph + 14
    rd = smp.get("reading") or []
    read = "“%s”" % rd[0]["text"] if rd else "Nothing"
    don = sid in ui.donated
    rows = [("Written for", "“%s”" % smp.get("text", ""), None),
            ("The tablet read", read, None),
            ("Written", "%s, %s" % (when(t) if t else "", orientation(smp.get("area"))), None)]
    if smp.get("written"):
        rows.append(("Written as", {"careful": "A careful sample", "quick": "A quick sample"}[smp["written"]], None))
    add = None
    if not don and ui.donation[0] and ui.state.donating:
        add = ("Add", lambda: ui.donate_one(sid))
    rows.append(("Contribution", "In your contribution" if don else "Not in your contribution", add))
    sep(cr, k, PAD, y, w - 2 * PAD, strong=True)
    for name, value, b in rows:
        lbl(cr, k, name, PAD, y + 20)
        PT.text(cr, value, k["row"], PAD + 160, y + 16, k.ink, maxw=w - 2 * PAD - 260)
        if b:
            btn(cr, k, hits, w - PAD - btn_w(cr, k, b[0]), y + 4, b[0], b[1], h=40)
        y += 54
        sep(cr, k, PAD, y, w - 2 * PAD)
    by = h - 20 - BTN_H
    if ui.asking_delete:
        text = ("It’s removed from your samples, and from your contribution, for good." if don else
                "It’s removed from your samples for good.")
        if not ui.guest:
            text += " What the tablet has already learned from it stays until it learns again."
        _, th = PT.measure(cr, text, k["sub"], maxw=w - 2 * PAD - 40, wrap=True)
        nh = 20 + 30 + th + 16 + BTN_H + 16
        ny = h - 14 - nh
        hits.add(0, 0, w, h, lambda: None)
        PT.box(cr, PAD, ny, w - 2 * PAD, nh, k["cardR"], bg=k.paper, shadow=k["dialogSh"])
        PT.text(cr, "Delete this sample?", k["row"], PAD + 20, ny + 20, k.ink, weight=600)
        PT.text(cr, text, k["sub"], PAD + 20, ny + 50, k.ink, maxw=w - 2 * PAD - 40, wrap=True)
        btns_right(cr, k, hits, w - PAD - 16, ny + nh - 16 - BTN_H,
                   [("Keep it", ui.keep_sample, False), ("Delete", lambda: ui.delete_sample(sid), True)])
        return
    btn(cr, k, hits, PAD, by, "All samples", ui.close_sample, icon_name="arrow_back")
    bw = btn_w(cr, k, "Delete", "delete")
    btn(cr, k, hits, w - PAD - bw, by, "Delete", ui.ask_delete, icon_name="delete", w=bw)
    msg_line(cr, ui, by - 30, w)


# ---------------------------------------------------------------- Coverage


GROUPS = (("Small letters", "abcdefghijklmnopqrstuvwxyz"), ("Capitals", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
          ("Digits and marks", "0123456789.,?!:;’-()\"&@/+#%_=$"))
FEW = 5                                  # fewer than this: "a few" (shaded)
WRONG = 0.3                              # read wrong this often (with 3 or more samples): the dot


def coverage_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    y = sub_head(cr, ui, hits, w, y, "How much of each character",
                 "The number is how many of your samples have it. Shaded ones could use more; a dot marks the ones "
                 "it often reads wrong.")
    cov = ui.writer.cov if ui.writer else P.Coverage()
    cols = 26 if w > 900 else 13
    gap = 5
    cw = (w - 2 * PAD - gap * (cols - 1)) / cols
    ch = 52 if w > 900 else 58
    for name, chars in GROUPS:
        lbl(cr, k, name, PAD, y)
        y += 22
        for i, c in enumerate(chars):
            cell(cr, k, PAD + (i % cols) * (cw + gap), y + (i // cols) * (ch + gap), cw, ch, c,
                 cov.count(c if c != "’" else "'") + (cov.count(c) if c == "’" else 0),
                 cov.chars.get(c, (0, 0)))
        y += ((len(chars) + cols - 1) // cols) * (ch + gap) + 12
    by = h - 20 - BTN_H
    sep(cr, k, PAD, by - 14, w - 2 * PAD)
    x = PAD
    for kind, label in (("plenty", "Plenty"), ("few", "A few"), ("none", "None yet"), ("wrong", "Often read wrong")):
        cell(cr, k, x, by + 10, 26, 28, "a", {"plenty": 9, "few": 2, "none": 0, "wrong": 9}[kind],
             (9, 9) if kind == "wrong" else (0, 0), small=True)
        tw, _ = PT.text(cr, label, k["sub"], x + 34, by + 15, k.ink)
        x += 34 + tw + 18
    pw = btn_w(cr, k, "Practise these")
    btn(cr, k, hits, w - PAD - pw, by, "Practise these", ui.practise, pri=True, w=pw)


def cell(cr, k, x, y, w, h, c, n, stats, small=False):
    wrong = stats[0] >= 3 and stats[1] / max(stats[0], 1) >= WRONG
    if n == 0:
        PT.box(cr, x, y, w, h, k["cellR"], bg=k.paper, shadow="inset 0 0 0 1.5px " + k.soft)
    else:
        PT.box(cr, x, y, w, h, k["cellR"], bg=k.hi if n < FEW else k.paper, shadow="inset 0 0 0 1px " + k.rule)
    if small:
        PT.text(cr, c, k["sub"], x + w / 2, y + 4, k.ink, align=1)
    else:
        PT.text(cr, c, k["cellF"], x + w / 2, y + 7, k.ink, align=1)
        PT.text(cr, str(n), k["mono"], x + w / 2, y + h - 20, k.soft, align=1)
    if wrong:
        cr.save()
        cr.set_source_rgba(*PT.color(k.ink))
        cr.arc(x + w - 6, y + 6, 3, 0, 6.2832)
        cr.fill()
        cr.restore()


# ---------------------------------------------------------------- Learn


def learn_state(ui):
    """few, waiting, running, better, kept, nothing (what the Learn page says)"""
    if ui.adapting:
        return "running"
    if ui.result and ui.result[0] == "result":
        return "better" if ui.result[1][0] == "kept" else "kept"
    pl = ui.plan or {}
    if pl and (pl.get("train", 0) < pl.get("min_train", 0) or pl.get("held", 0) < pl.get("min_held", 0)) \
            and not ui.adapts.get("current"):
        return "few"
    if ui.fresh > 0:
        return "waiting"
    if ui.adapts.get("current"):
        return "better"
    return "few"


def learn_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Learn", "How it’s learning", None, small=True, land_hide=True)
    st = learn_state(ui)
    today, total = ui.counts
    sep(cr, k, PAD, y - 6, w - 2 * PAD, strong=True)
    cw = (w - 2 * PAD) / 3
    for i, (n, label) in enumerate(((today, "today"), (total, "in all"), (ui.fresh, "new since it last learned"))):
        x = PAD + i * cw
        if i:
            PT.rule(cr, x, y - 6, 0, k["sep"]) if False else None
            cr.save()
            cr.set_source_rgba(*PT.color(k.rule))
            cr.set_line_width(1)
            cr.move_to(x + .5, y - 6)
            cr.line_to(x + .5, y + 58)
            cr.stroke()
            cr.restore()
        PT.text(cr, str(n), k["num"], x + 12, y + 6, k.ink)
        PT.text(cr, label, k["sub"], x + 12, y + 40, k.soft, maxw=cw - 20)
    y += 64
    sep(cr, k, PAD, y, w - 2 * PAD)
    y += 14
    pl = ui.plan or {"train": 0, "held": 0, "new": 0, "min_train": 40, "min_held": 10, "charging": False}
    cur, prev = ui.adapts.get("current"), ui.adapts.get("previous")
    need = ui.need()
    rows, foot, btns, barv = [], "", [], None
    if st == "few":
        ic, title = "edit_note", "Keep writing"
        text = "It starts learning once you have about %d samples. %d more to go." % (need, max(0, need - total))
        barv = (10, min(10, total * 10 // max(need, 1)), "%d of about %d" % (total, need))
        btns = [("Write more", lambda: ui.go("write"), True)]
    elif st == "waiting":
        ic = "battery_charging_full"
        title = "Ready to learn from %d new sample%s" % (ui.fresh, "" if ui.fresh == 1 else "s")
        text = ("It starts by itself the next time the tablet is charging and resting. Nothing to do." if
                not ui.waiting_charge else "It starts as soon as the charger is in.")
        foot = "Learn now starts as soon as the charger is in."
        btns = [("Learn now", ui.adapt if ui.learning and not ui.waiting_charge else None, True)]
    elif st == "running":
        ic, title = "school", "Learning from your handwriting"
        text = "From %d of your samples. It keeps %d others aside to check itself. You can go on using the tablet " \
               "meanwhile." % (ui.adapt_plan[0], ui.adapt_plan[1]) if ui.adapt_plan else \
            "You can go on using the tablet meanwhile."
        if ui.adapt_round:
            barv = (ui.adapt_round[1], ui.adapt_round[0], "Round %d of %d" % ui.adapt_round)
        foot = "If the charger comes out, it stops and tries again later."
    elif st == "better":
        ic, title = "check_circle", "It reads you better now"
        b, a = (ui.result[1][1], ui.result[1][2]) if ui.result else (cur["before"], cur["after"])
        text = "On the samples it kept aside, it used to misread %d letters in 100. Now it misreads %d. It uses what " \
               "it learned from now on." % (per100(b), per100(a))
    else:
        ic, title = "info", "It kept what it had"
        b, a = ui.result[1][1], ui.result[1][2]
        text = "It tried, but it didn’t read you better this time (%d letters in 100 misread before, %d with the new " \
               "try), so nothing changed. More samples usually help." % (per100(b), per100(a))
        btns = [("Write more", lambda: ui.go("write"), True)]
    if ui.result and ui.result[0] == "skip" and st in ("waiting", "few"):
        text += " " + {"unplugged": "The last try stopped: the charger came out.", "charging":
                       "The tablet isn’t charging.", "learning": "Learning is off in Settings."}.get(
            (ui.result[1] or [""])[0], "")
    if not ui.learning:
        text = "Learning is off in Settings: nothing is learned or kept."
    tw = w - 2 * PAD - 70
    _, t1 = PT.measure(cr, title, k["row"], maxw=tw, wrap=True)
    _, t2 = PT.measure(cr, text, k["row"], maxw=tw, wrap=True)
    bh = 18 + t1 + 4 + t2 + (40 if barv else 0) + 18
    note(cr, k, PAD, y, w - 2 * PAD, bh)
    icon(cr, ic, 26, PAD + 30, y + 30, k.ink)
    PT.text(cr, title, k["row"], PAD + 56, y + 18, k.ink, weight=600, maxw=tw, wrap=True)
    PT.text(cr, text, k["row"], PAD + 56, y + 22 + t1, k.ink, maxw=tw, wrap=True)
    if barv:
        bar(cr, k, PAD + 56, y + 30 + t1 + t2, tw, barv[0], barv[1])
        PT.text(cr, barv[2], k["mono"], PAD + 56, y + 46 + t1 + t2, k.ink)
    y += bh + 14
    if cur:
        r1 = ("person", "What it uses now", ("What it learned %s, until this is done" if st == "running" else
                                             "What it learned from your handwriting, %s") % weekday(cur["time"]))
    else:
        r1 = ("public", "What it uses now", "What it knows from everyone’s handwriting. Nothing learned from yours yet.")
    rows = [r1]
    if prev and st != "running":
        rows.append(("history", "The version before", "Kept, in case you want to go back to it"))
        btns = [("Use the version before", ui.ask_revert, False)] + btns
    y = icon_rows(cr, k, PAD, y, w - 2 * PAD, rows, pad=10) + 14
    # the pages beneath Learn, side by side
    n = len(ui.samples)
    links = (("grid_view", "Samples", "%d, newest first. Look at one, delete or export them" % n if n else
              "Nothing written yet", lambda: ui.go("samples")),
             ("abc", "Coverage", "How much of each character it has seen from you", lambda: ui.go("coverage")))
    lw = (w - 2 * PAD - 14) / 2
    lh = max(chevron_h(cr, k, lw, sub) for _, _, sub, _ in links)
    for i, (ic, name, sub, fn) in enumerate(links):
        chevron_row(cr, k, hits, PAD + i * (lw + 14), y, lw, ic, name, sub, fn, min_h=lh)
    by = h - 20 - BTN_H
    x = btns_right(cr, k, hits, w - PAD, by, btns) if btns else w - PAD
    if foot:
        PT.text(cr, foot, k["sub"], PAD, by + 15, k.soft, maxw=x - PAD - 10)
    msg_line(cr, ui, by - 30, w)


# ---------------------------------------------------------------- Your font


def font_page(cr, ui, hits, w, h):
    """the Your font tab: the font page, or Which one is nicer? opened from it"""
    fill(cr, ui.theme, w, h)
    if ui.best == "pick":
        return best_pick(cr, ui, hits, w, h)
    return best_font(cr, ui, hits, w, h)


def best_pick(cr, ui, hits, w, h):
    """two of the person's own versions of one character, ink only, large"""
    k = ui.theme
    y = chrome(cr, ui, hits, w)
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Your font", "Which one is nicer?",
                  "Tap the one you like better." if ui.pick else None, small=True)
    pk = ui.pick
    by = h - 20 - BTN_H
    if not pk:
        PT.text(cr, "Looking for two versions…" if ui.pick_loading else
                "Not enough to choose from yet: write a few more words, and two versions of the same letter come up "
                "here.", k["row"], PAD, y, k.soft, maxw=w - 2 * PAD, wrap=True)
        btn(cr, k, hits, PAD, by, "Back", ui.open_best, icon_name="arrow_back")
        return
    gap = 16
    land = w > h
    tw = (w - 2 * PAD - gap) / 2
    th = by - 20 - y
    for i, side in enumerate(("a", "b")):
        x = PAD + i * (tw + gap)
        on = pk.get("chosen") == i
        PT.box(cr, x, y, tw, th, k["cardR"], bg=k.paper, shadow=k["cardOnSh"] if on else k["cardFlat"])
        st = pk[side][1]
        guide = y + th * 0.6
        cr.save()
        cr.set_source_rgba(*PT.color(k.rule))
        cr.set_dash([3, 3])
        cr.move_to(x + 16, guide)
        cr.line_to(x + tw - 16, guide)
        cr.stroke()
        cr.restore()
        box = min(tw - 60, th * 0.5 if not land else th * 0.6)
        draw_ink(cr, k, st, x + (tw - box) / 2, y + th * 0.6 - box * 0.8, box, box, 4.0, align="center")
        if on:
            icon(cr, "check_circle", 30, x + tw - 28, y + 28, k.ink)
        hits.add(x, y, tw, th, lambda i=i: ui.choose_pick(i))
    n = ui.picks_today()
    PT.text(cr, "%d pick%s today" % (n, "" if n == 1 else "s"), k["sub"], PAD, by + 15, k.soft)
    btns_right(cr, k, hits, w - PAD, by, [("Both fine", ui.next_pick, False), ("Skip", ui.next_pick, False)])


def hand_lines(model, text, xh, maxw):
    """text wrapped at spaces for draw_hand -> (lines, line height)"""
    m = model["metrics"]
    line_h = xh * (m.get("ascender", 1.5) - m.get("descender", -0.5)) * 1.15
    words, cur = [], []
    for c in text:
        cur.append(c)
        if c == " ":
            words.append("".join(cur))
            cur = []
    if cur:
        words.append("".join(cur))
    lines, ln = [], ""
    for wd in words:
        tryw = FV.layout(model, (ln + wd).rstrip())
        width = (tryw[-1][2] + tryw[-1][3]) * xh if tryw else 0
        if ln and width > maxw:
            lines.append(ln)
            ln = wd
        else:
            ln += wd
    lines.append(ln)
    return lines, line_h


def hand_fit(model, text, xh, maxw, maxh):
    """the largest x-height up to xh at which text wraps into maxw by maxh (from the first
    line's top to the last line's descender)"""
    m = model["metrics"]
    while xh > 6:
        lines, line_h = hand_lines(model, text, xh, maxw)
        if xh * m.get("ascender", 1.5) + (len(lines) - 1) * line_h - xh * m.get("descender", -0.5) <= maxh:
            break
        xh *= 0.9
    return xh


def styled_stroke(cr, model, var, j, pts, style, nib, xh):
    """one stroke in Calligraphy or Brush as the font outlines it (ttf.c outline_styled):
    pts in device units; the pieces filled one by one, so overlaps are one ink"""
    if style == "calligraphy":
        lo, hi = FV.nib_widths(model)
        a = math.radians(nib)
        hx, hy = (hi - lo) / 2 * xh * math.cos(a), -(hi - lo) / 2 * xh * math.sin(a)
        cr.set_line_width(max(0.6, lo * xh))
        segs = list(zip(pts, pts[1:])) or [(pts[0], pts[0])]
        for (px, py), (nx, ny) in segs:
            # the nib swept from one point to the next, rounded by the thinnest line
            cr.move_to(px + hx, py + hy)
            cr.line_to(nx + hx, ny + hy)
            cr.line_to(nx - hx, ny - hy)
            cr.line_to(px - hx, py - hy)
            cr.close_path()
            cr.fill_preserve()
            cr.stroke()
        return
    r = [max(0.3, w * xh / 2) for w in FV.widths(model, var, j, style, nib)]
    for i, (px, py) in enumerate(pts):
        cr.arc(px, py, r[i], 0, 2 * math.pi)
        cr.fill()
        if i + 1 < len(pts):
            # the hull of this point's disc and the next one's (ttf.c disc_pair)
            nx, ny = pts[i + 1]
            d = math.hypot(nx - px, ny - py)
            if d <= abs(r[i] - r[i + 1]):
                continue
            f, be = math.atan2(ny - py, nx - px), math.acos((r[i] - r[i + 1]) / d)
            for (cx, cy, rr, an) in ((px, py, r[i], f + be), (nx, ny, r[i + 1], f + be), (nx, ny, r[i + 1], f - be),
                                     (px, py, r[i], f - be)):
                cr.line_to(cx + rr * math.cos(an), cy + rr * math.sin(an))
            cr.close_path()
            cr.fill()


def draw_hand(cr, k, model, text, x, y, xh, maxw, col=None, style="regular", nib=FV.NIB):
    """text in the person's font as it will look (fontview.layout), wrapped at spaces, in one
    of the font's styles (FR-034); a character the font lacks in the system's font. y: the
    first baseline. -> the last baseline"""
    m = model["metrics"]
    lines, line_h = hand_lines(model, text, xh, maxw)
    ink = max(1.5, m.get("ink_width", 0.07) * xh)
    cr.save()
    cr.set_line_width(ink)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for i, ln in enumerate(lines):
        by = y + i * line_h
        for c, strokes, cx, adv, var in FV.layout(model, ln.rstrip()):
            if strokes is None:
                PT.text(cr, c, "400 %dpx/1 sans-serif" % max(8, int(xh * 1.5)), x + cx * xh, by - xh * 1.25,
                        k.soft)
                continue
            cr.set_source_rgba(*PT.color(col or k.pen))
            if style != "regular" and var:
                for j, st in enumerate(strokes):
                    if st:
                        styled_stroke(cr, model, var, j, [(x + (cx + px) * xh, by - py * xh) for px, py in st], style,
                                      nib, xh)
                cr.set_line_width(ink)
                continue
            for st in strokes:
                if not st:
                    continue
                cr.move_to(x + (cx + st[0][0]) * xh, by - st[0][1] * xh)
                for px, py in st[1:]:
                    cr.line_to(x + (cx + px) * xh, by - py * xh)
                if len(st) == 1:
                    cr.rel_line_to(0.1, 0)
                cr.stroke()
    cr.restore()
    return y + (len(lines) - 1) * line_h


def font_mode(ui):
    """where the font page stands: none, learning, relearn, outdated, relearned (installed,
    the preview has letters the installed font hasn't), installed, current"""
    st = (ui.font or {}).get("status") or {}
    if ui.letters_progress() is not None:
        return "learning"
    if ui.relearn:
        return "relearn"
    if st.get("state") == "none":
        return "none"
    if st.get("state") == "outdated":
        return "outdated"
    if st.get("font"):
        return "installed" if st.get("font_same", True) else "relearned"
    return "current"


def segs(cr, k, hits, x, y, w, items, cur, h=BTN_H, flat=False):
    """a row of equal segments [(key, label, fn or None)]; flat: the others see-through on a
    shaded card, a thin rule round each"""
    gap = 6
    n = len(items)
    sw = (w - gap * (n - 1)) / n
    for i, (key, label, fn) in enumerate(items):
        sx = x + i * (sw + gap)
        on = key == cur
        if on:
            st = k["segOn"]
            PT.box(cr, sx, y, sw, h, k["segR"], bg=st["bg"], shadow=st["sh"])
            col = st["fg"]
        else:
            PT.box(cr, sx, y, sw, h, k["segR"], bg=None if flat else k.paper,
                   shadow="inset 0 0 0 %spx " % ("1" if flat else "1.5") + (k.rule if fn else k.dim))
            col = k.ink if fn else k.dim
        f = k["row"] if PT.measure(cr, label, k["row"], weight=600 if on else None)[0] <= sw - (4 if flat else 16) \
            else k["sub"]
        tw, th = PT.measure(cr, label, f)
        PT.text(cr, label, f, sx + sw / 2, y + (h - th) / 2, col, align=1, weight=600 if on else None,
                maxw=sw - (4 if flat else 8))
        if fn and not on:
            hits.add(sx, y, sw, h, fn)


def dashed_box(cr, k, x, y, w, h, col=None, r=10):
    cr.save()
    cr.set_source_rgba(*PT.color(col or k.ink))
    cr.set_line_width(1.2)
    cr.set_dash([3, 3])
    x0, y0, x1, y1 = x + 0.6, y + 0.6, x + w - 0.6, y + h - 0.6
    cr.new_path()
    cr.arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x1 - r, y0 + r, r, 1.5 * math.pi, 0)
    cr.arc(x1 - r, y1 - r, r, 0, 0.5 * math.pi)
    cr.arc(x0 + r, y1 - r, r, 0.5 * math.pi, math.pi)
    cr.close_path()
    cr.stroke()
    cr.restore()


def baseline(cr, k, x0, x1, y):
    cr.save()
    cr.set_source_rgba(*PT.color(k.rule))
    cr.set_line_width(1)
    cr.set_dash([3, 3])
    cr.move_to(x0, y)
    cr.line_to(x1, y)
    cr.stroke()
    cr.restore()


def draw_var(cr, k, var, m, x, y, w, h, col=None):
    """one variant of the letter model, as large as fits the box, on its baseline"""
    asc, desc = m.get("ascender", 1.5), m.get("descender", -0.5)
    vw = max(var.get("width", 0.6), 0.3)
    xh = min(h / (asc - desc), w / (vw + 0.4))
    base = y + (h - (asc - desc) * xh) / 2 + asc * xh
    ox = x + (w - vw * xh) / 2
    cr.save()
    cr.set_source_rgba(*PT.color(col or k.pen))
    cr.set_line_width(max(2.0, m.get("ink_width", 0.07) * xh))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for st in var.get("strokes") or []:
        if not st:
            continue
        cr.move_to(ox + st[0][0] * xh, base - st[0][1] * xh)
        for px, py in st[1:]:
            cr.line_to(ox + px * xh, base - py * xh)
        if len(st) == 1:
            cr.rel_line_to(0.1, 0)
        cr.stroke()
    cr.restore()
    return base


def char_name(c):
    if c.isdigit():
        return "Digit %s" % c
    if c.isalpha():
        return ("Capital %s" if c.isupper() else "Small %s") % c
    return "The mark %s" % c


KEY_GAP = 4


def font_keys(cr, ui, hits, x, y, w, cols, kh, cov):
    """the keyboard of the font's characters: plain when in it, shaded with a sample or two,
    outlined with none; each opens its character -> the y below"""
    k = ui.theme
    kw = (w - KEY_GAP * (cols - 1)) / cols
    for i, (c, n, ok) in enumerate(cov):
        cx, cy = x + (i % cols) * (kw + KEY_GAP), y + (i // cols) * (kh + KEY_GAP)
        if ok:
            PT.box(cr, cx, cy, kw, kh, k["cellR"], bg=k.paper, shadow="inset 0 0 0 1px " + k.rule)
            _, th = PT.measure(cr, c, k["row"])
            PT.text(cr, c, k["row"], cx + kw / 2, cy + (kh - th) / 2, k.ink, align=1)
        else:
            PT.box(cr, cx, cy, kw, kh, k["cellR"], bg=k.hi if n else k.paper,
                   shadow="inset 0 0 0 1.5px " + k.ink)
            PT.text(cr, c, k["row"], cx + kw / 2, cy + 2, k.ink, align=1)
            PT.text(cr, "%d/%d" % (min(n, FV.MIN), FV.MIN), k["mono"], cx + kw / 2, cy + kh - 17, k.ink, align=1)
        hits.add(cx, cy, kw, kh, lambda c=c: ui.open_char(c))
    return y + ((len(cov) + cols - 1) // cols) * (kh + KEY_GAP) - KEY_GAP


def styles_shown(ui, mode):
    """the font's styles can be picked (FR-034): while the preview shows letters"""
    f = ui.font or {}
    model = f.get("model")
    return bool(model and mode not in ("none", "learning") and
                any(ok for _, _, ok in FV.coverage(model, status=f.get("status"))))


STYLE_W = 350                   # the style control's width: the design's, with room for "Calligraphy" in every voice


def style_segs(cr, ui, hits, x, y, w):
    """Regular / Calligraphy / Brush: the style the preview is drawn in"""
    segs(cr, ui.theme, hits, x, y, w, [(key, label, lambda key=key: ui.set_font_style(key)) for key, label in FV.STYLES],
         ui.font_style, flat=True)


def preview_card(cr, ui, hits, x, y, w, mode, land=False):
    """the preview of the font with where the letter model stands -> its height. Upright, the
    styles take the head row's middle (an Update letters moves into it, its note under the
    preview); turned, they sit above the keys (best_font)"""
    k = ui.theme
    f = ui.font or {}
    model = f.get("model")
    cov = FV.coverage(model, status=f.get("status"))
    have = any(ok for _, _, ok in cov)
    st_card = styles_shown(ui, mode) and not land
    act_head = st_card and mode == "outdated"
    rel = ui.relearn[0] if ui.relearn else ""
    if mode == "learning":
        note_ = "Learning your letters… %d%%" % round(100 * ui.letters_progress())
    elif mode == "relearn":
        note_ = "Learning %s again… %d%%" % (rel, round(100 * ui.relearn_progress()))
    elif mode == "none":
        note_ = "Not learned yet"
    elif mode == "outdated":
        note_ = "You wrote more since"
    elif mode == "relearned":
        note_ = "Newer than your installed font"
    elif model and any(c in FV.TARGET and not ok for c, _, ok in FV.coverage(model, FV.PREVIEW.replace(" ", "").replace(".", ""), f.get("status"))):
        note_ = "Plain letters aren’t in it yet"
    else:
        note_ = ""
    iw = w - 36
    show = model and have and mode not in ("none", "learning")
    pv_h = 112 if land else 56
    if show:
        xh = hand_fit(model, FV.PREVIEW, 22 if not land else 18, iw, pv_h)
        body = pv_h
    else:
        t = ("The counts below come straight from your samples. The preview comes once your letters are learned."
             if mode == "none" else "The preview comes when this is done. You can go on using the tablet meanwhile."
             if mode == "learning" else "Write a few words with care first: once letters have 3 samples, they show here.")
        _, body = PT.measure(cr, t, k["sub"], maxw=iw, wrap=True)
    extra = 0
    if mode == "none" or (mode == "outdated" and not act_head):
        extra = BTN_H + 12
    elif mode in ("learning", "relearn"):
        extra = 22
    tail = ""
    if mode == "relearn":
        tail = "The preview shows the new %s when this is done." % rel
        if land and (f.get("status") or {}).get("font"):
            tail += " Your installed font stays as it is until you update it."
    elif act_head:
        tail = "You wrote more since. Update letters, or it does this on its own while the tablet charges."
    th_ = PT.measure(cr, tail, k["sub"], maxw=iw, wrap=True)[1] + 8 if tail else 0
    hh = BTN_H if st_card else 22
    ph = 14 + hh + 10 + body + (12 + extra if extra else 0) + th_ + 16
    soft_box(cr, k, x, y, w, ph)
    lw, _ = lbl(cr, k, "Preview", x + 18, y + 16 + (hh - 22) / 2)
    nx = x + 18 + lw + 12
    if st_card:
        style_segs(cr, ui, hits, nx, y + 14, STYLE_W)
        nx += STYLE_W + 12
    if act_head:
        bw = btn_w(cr, k, "Update letters")
        btn(cr, k, hits, x + w - 18 - bw, y + 14, "Update letters", ui.build_letters, w=bw)
    elif note_:
        PT.text(cr, note_, k["sub"], x + w - 18, y + 12 + (hh - 22) / 2, k.ink, maxw=x + w - 18 - nx, align=2)
    yy = y + 14 + hh + 10
    # a character learned again: its progress above the preview it changes
    bar_top = mode == "relearn" and show
    if bar_top:
        bar(cr, k, x + 18, yy, iw, 10, int(10 * (ui.relearn_progress() or 0)), h=10)
        yy += 22
    if show:
        cr.save()
        cr.rectangle(x, yy - 8, w, body + 16)
        cr.clip()
        draw_hand(cr, k, model, FV.PREVIEW, x + 18, yy + xh * model["metrics"].get("ascender", 1.5), xh, iw,
                  style=ui.font_style if styles_shown(ui, mode) else "regular", nib=ui.nib_value())
        cr.restore()
    else:
        PT.text(cr, t, k["sub"], x + 18, yy, k.ink, maxw=iw, wrap=True)
    yy += body + 12
    if mode == "none" or (mode == "outdated" and not act_head):
        label = "Learn letters now" if mode == "none" else "Update letters"
        bw = btn(cr, k, hits, x + 18, yy, label, ui.build_letters)
        PT.text(cr, "Or it does this on its own while the tablet charges.", k["sub"], x + 18 + bw + 12,
                yy + 14, k.soft, maxw=iw - bw - 12)
        yy += BTN_H + 12
    elif mode in ("learning", "relearn") and not bar_top:
        p_ = ui.letters_progress() if mode == "learning" else ui.relearn_progress()
        bar(cr, k, x + 18, yy, iw, 10, int(10 * (p_ or 0)), h=10)
        yy += 22
    if tail:
        PT.text(cr, tail, k["sub"], x + 18, yy, k.soft, maxw=iw, wrap=True)
    return ph


def font_status_line(ui, mode):
    st = (ui.font or {}).get("status") or {}
    inst = st.get("font")
    if ui.font_busy == "making":
        return "Making your font… The tablet stays usable meanwhile."
    if ui.font_busy == "waiting":
        return "Your font is made once %s is learned again." % (ui.relearn[0] if ui.relearn else "the letter")
    # the three styles go in together, under one name (FR-034)
    if not inst:
        if mode == "current":
            return "Not installed. Install puts in all three styles, under this one name. Only you have it; it is never contributed."
        return "Not installed. Only you have it, on this tablet. It is never contributed."
    if mode == "outdated":
        return "Installed, version %d. Update makes all three styles again, under the same name." % inst[1]
    if mode in ("relearn", "relearned"):
        return "Installed, version %d. Update puts the new letters in all three styles." % inst[1]
    if ui.nib_changed():
        return "Installed, version %d. Update puts the new nib angle in it." % inst[1]
    return "Installed, version %d. Apps list it under this one name, in all three styles." % inst[1]


def font_name_block(cr, ui, hits, x, y, w, mode, stacked=False):
    """the name, the install state and Rename between two rules -> the y below"""
    k = ui.theme
    sep(cr, k, x, y, w)
    y += 12
    rename = ui.rename_font if not ui.font_busy else None
    tw, _ = lbl(cr, k, "Name", x, y + 6)
    nx = x + tw + 14 if stacked else x + 70
    rw = btn_w(cr, k, "Rename")
    PT.text(cr, ui.font_shown_name(), k["row"], nx, y, k.ink, weight=600, maxw=w - (nx - x) - (0 if stacked else rw + 12))
    line = font_status_line(ui, mode)
    if stacked:
        _, lh = PT.text(cr, line, k["sub"], x, y + 30, k.ink, maxw=w, wrap=True)
        btn(cr, k, hits, x, y + 30 + lh + 12, "Rename", rename, w=rw)
        y += 30 + lh + 12 + BTN_H + 12
    else:
        _, lh = PT.text(cr, line, k["sub"], nx, y + 26, k.ink, maxw=w - (nx - x) - rw - 12, wrap=True)
        btn(cr, k, hits, x + w - rw, y + max(0, (26 + lh - BTN_H) / 2), "Rename", rename, w=rw)
        y += max(26 + lh, BTN_H) + 12
    sep(cr, k, x, y, w)
    return y


def font_buttons(cr, ui, hits, w, by, mode, land):
    k = ui.theme
    st = (ui.font or {}).get("status") or {}
    inst = st.get("font")
    f = ui.font or {}
    have = any(ok for _, _, ok in FV.coverage(f.get("model"), status=st))
    miss = FV.missing(f.get("model"), status=st)
    x = PAD                     # no Back: the font page is the Your font tab
    if land and miss:
        x += btn(cr, k, hits, x, by, "Write these now", ui.write_missing, icon_name="edit") + 10
    busy = bool(ui.font_busy)
    if not inst:
        can = have and mode in ("current", "outdated", "relearned", "relearn") and not busy
        right = btns_right(cr, k, hits, w - PAD, by, [("Install", ui.make_font if can else None, True)])
    else:
        stale = mode in ("outdated", "relearned", "relearn") or bool(ui.font_name) or ui.nib_changed()
        right = btns_right(cr, k, hits, w - PAD, by, [("Remove", ui.ask_remove_font if not busy else None, False),
                                                      ("Update", ui.make_font if stale and not busy else None, True)])
    if ui.font_style == "calligraphy" and styles_shown(ui, mode):
        nib_stepper(cr, ui, hits, x, right - 10, by)


def nib_stepper(cr, ui, hits, x0, x1, y):
    """Calligraphy's nib angle (FR-034), centred between x0 and x1: Nib angle, −, the angle, +"""
    k = ui.theme
    n = ui.nib_value()
    t = "%d°" % n
    lw_, lh = PT.measure(cr, "Nib angle", k["sub"])
    tw, th = PT.measure(cr, t, k["row"])
    tw = max(tw, PT.measure(cr, "60°", k["row"])[0])
    total = lw_ + 12 + BTN_H + 14 + tw + 14 + BTN_H
    x = x0 + max(0, (x1 - x0 - total) / 2)
    PT.text(cr, "Nib angle", k["sub"], x, y + (BTN_H - lh) / 2, k.soft)
    x += lw_ + 12
    for d, ic in ((-1, "remove"), (1, "add")):
        on = FV.NIB_MIN <= n + d * FV.NIB_STEP <= FV.NIB_MAX and not ui.font_busy
        btn(cr, k, hits, x, y, "", (lambda d=d: ui.nib_step(d)) if on else None, w=BTN_H)
        icon(cr, ic, 22, x + BTN_H / 2, y + BTN_H / 2, k.ink if on else k.dim)
        if d < 0:
            x += BTN_H + 14
            PT.text(cr, t, k["row"], x + tw / 2, y + (BTN_H - th) / 2, k.ink, align=1, weight=600)
            x += tw + 14


# the rest of Your font, as rows below the font's letters (spec 003 FR-042)
FONT_PARTS = (("draw", "Write it your best", "start_best"), ("compare", "Which one is nicer?", "start_pick"),
              ("edit_note", "Neaten my writing", "open_neat"))
PART_H = 60


def font_parts(cr, ui, hits, x, y, w):
    """Write it your best, Which one is nicer? and Neaten my writing, side by side"""
    k = ui.theme
    gap = 10
    cw = (w - gap * (len(FONT_PARTS) - 1)) / len(FONT_PARTS)
    for i, (ic, label, fn) in enumerate(FONT_PARTS):
        bx = x + i * (cw + gap)
        PT.box(cr, bx, y, cw, PART_H, k["rowR"], bg=k.sheet)
        icon(cr, ic, 22, bx + 24, y + PART_H / 2, k.ink)
        tw = cw - 46 - 34
        _, th = PT.measure(cr, label, k["row"], maxw=tw, wrap=True)
        PT.text(cr, label, k["row"], bx + 46, y + (PART_H - th) / 2, k.ink, maxw=tw, wrap=True)
        icon(cr, "chevron_right", 22, bx + cw - 20, y + PART_H / 2, k.ink)
        hits.add(bx, y, cw, PART_H, getattr(ui, fn))


def best_font(cr, ui, hits, w, h):
    """Your font (spec 003 Scenario 3, the design's font.* boards): a preview in the font
    with where the letter model stands, a key per character (each opens its sheet), the
    name, install and update. The counts come from the samples, built model or not (FR-030)"""
    k = ui.theme
    if ui.sheet:
        return char_sheet(cr, ui, hits, w, h)
    y = chrome(cr, ui, hits, w)
    if ui.renaming:
        return rename_dialog(cr, ui, hits, w, h, y)
    land = w > h
    # the tab's own page: no eyebrow; turned, no head at all, so the keys keep their width
    y = y + 18 if land else page_head(
        cr, k, PAD, y + 18, w - 2 * PAD, None, "Your handwriting as a font",
        "A character is in it once it has 3 good samples. Tap any key to see how you write it.", small=True)
    cw = w - 2 * PAD
    by = h - 20 - BTN_H
    py = by - 14 - PART_H                     # the rows to the rest of Your font
    f = ui.font or {}
    st, model = f.get("status") or {}, f.get("model")
    if not ui.font:
        PT.text(cr, "Counting your letters…", k["row"], PAD, y, k.soft, maxw=cw, wrap=True)
        font_parts(cr, ui, hits, PAD, py, cw)
        return
    mode = font_mode(ui)
    cov = FV.coverage(model, status=st)
    if mode in ("none", "learning") or not model:
        cov = [(c, n, n >= FV.MIN) for c, n, _ in cov]
    have = sum(1 for c in cov if c[2])
    miss = [c for c, _, ok in cov if not ok]
    where = "with enough samples" if mode in ("none", "learning") else "in your font"
    # turned, the styles take the count line's room: the label is shorter, the line drops the
    # shaded keys' note
    head = "Letters · %d of %d" % (have, len(cov)) + ("" if land else " " + where)
    sub = ((where[0].upper() + where[1:] + "; " if land else "") + "%d not in it yet" % len(miss) +
           ("" if land else "; shaded ones have a sample or two")) if miss else "Every one is in it."
    if land:
        lw_ = int(cw * 0.32)
        rx, rw = PAD + lw_ + 24, cw - lw_ - 24
        ph = preview_card(cr, ui, hits, PAD, y, lw_, mode, land=True)
        font_name_block(cr, ui, hits, PAD, y + ph + 14, lw_, mode, stacked=True)
        sh = styles_shown(ui, mode)
        sw = min(STYLE_W, int(rw * 0.55))
        lbl(cr, k, head, rx, y + (6 if sh else 0))
        PT.text(cr, sub, k["sub"], rx, y + (26 if sh else 20), k.ink, maxw=rw - (sw + 12 if sh else 0))
        if sh:
            style_segs(cr, ui, hits, rx + rw - sw, y, sw)
        ky = y + (BTN_H + 8 if sh else 48)
        kh = max(32, min(44, int((py - 10 - ky) / ((len(cov) + 11) // 12)) - KEY_GAP))
        font_keys(cr, ui, hits, rx, ky, rw, 12, kh, cov)
    else:
        ph = preview_card(cr, ui, hits, PAD, y, cw, mode)
        y += ph + 14
        bw = btn_w(cr, k, "Write these now", "edit")
        lbl(cr, k, head, PAD, y + 4)
        PT.text(cr, sub, k["sub"], PAD, y + 24, k.ink, maxw=cw - bw - 12)
        if miss:
            btn(cr, k, hits, PAD + cw - bw, y, "Write these now", ui.write_missing, icon_name="edit", w=bw)
        y += max(BTN_H, 44) + 10
        # keys as tall as the design's while the page has room (a short window: smaller)
        rows = (len(cov) + 12) // 13
        name_h = 12 + max(26 + 20, BTN_H) + 12 + 24
        kh = max(34, min(46, int((py - 16 - name_h - y - 10) / rows) - KEY_GAP))
        y = font_keys(cr, ui, hits, PAD, y, cw, 13, kh, cov) + 10
        font_name_block(cr, ui, hits, PAD, y, cw, mode)
    font_parts(cr, ui, hits, PAD, py, cw)
    font_buttons(cr, ui, hits, w, by, mode, land)
    if ui.msg:
        msg_line(cr, ui, by - 30, w)


def rename_dialog(cr, ui, hits, w, h, y):
    """the name, typed in a small dialog in the upper half: the keyboard comes up below"""
    k = ui.theme
    hits.add(0, y, w, h - y, lambda: None)
    st = (ui.font or {}).get("status") or {}
    dw = w - 2 * PAD
    x = PAD
    y += 20
    text = ("Apps list your font under this name. It is installed, so “Use this name” makes it again under the new "
            "name; your letters stay as they are." if st.get("font") else
            "Apps list your font under this name once it is installed. Only you have it, on this tablet.")
    _, th = PT.measure(cr, text, k["sub"], maxw=dw - 40, wrap=True)
    dh = 20 + 30 + 56 + 14 + th + 14 + BTN_H + 20
    PT.box(cr, x, y, dw, dh, k["cardR"], bg=k.paper, shadow=k["dialogSh"])
    PT.text(cr, "Name of your font", k["row"], x + 20, y + 20, k.ink, weight=600, maxw=dw - 40)
    fy = y + 20 + 34
    PT.box(cr, x + 20, fy, dw - 40, 52, k["cellR"], bg=k.paper, shadow="inset 0 0 0 1.5px " + k.ink)
    ui.rename_rect = (x + 34, fy + 6, dw - 68, 40)
    if ui.demo:
        tw, tth = PT.measure(cr, ui.renaming["text"], k["row"])
        PT.text(cr, ui.renaming["text"], k["row"], x + 36, fy + (52 - tth) / 2, k.ink)
        PT.fill(cr, x + 38 + tw, fy + 13, 1.5, 26, k.ink)
    PT.text(cr, text, k["sub"], x + 20, fy + 52 + 14, k.ink, maxw=dw - 40, wrap=True)
    btns_right(cr, k, hits, x + dw - 20, y + dh - 20 - BTN_H,
               [("Cancel", ui.cancel_rename, False), ("Use this name", ui.use_name, True)])


# ---------------------------------------------------------------- the character sheet


SHEET_M = 10                                # the sheet's margin inside the content area


def sheet_info(ui):
    c = ui.sheet["char"]
    n, d, inf = ui.char_info(c)
    nv = len([v for v in (d or {}).get("variants") or []])
    return c, n, d, inf, nv


def drilled(sh):
    """a sample or a version's samples open in the sheet: no segments then (spec 003 FR-033)"""
    return bool(sh and (sh.get("open") or sh.get("vlist") is not None))


def sheet_head(cr, ui, hits, x, y, w, tabs=True):
    """the sheet's head: the character, where it stands, Close; its segments (tabs) -> the y
    below"""
    k = ui.theme
    c, n, d, inf, nv = sheet_info(ui)
    m = ((ui.font or {}).get("model") or {}).get("metrics", {})
    soft_box(cr, k, x, y, 64, 64)
    vs = [v for v in (d or {}).get("variants") or [] if not v.get("left_out")]
    if vs:
        draw_var(cr, k, vs[0], m, x + 8, y + 6, 48, 52)
    else:
        _, th = PT.measure(cr, c, k.script % 40)
        PT.text(cr, c, k.script % 40, x + 32, y + (64 - th) / 2, k.ink, align=1)
    PT.text(cr, char_name(c), k["row"], x + 80, y + 10, k.ink, weight=600, maxw=w - 230)
    if inf:
        line = "In your font · %d samples%s" % (n, " · %d ways you write it" % nv if nv > 1 else "")
    else:
        line = "Not in your font yet · %d of %d samples" % (min(n, FV.MIN), FV.MIN) if n < FV.MIN else \
            "Not in your font yet · %d samples" % n
    PT.text(cr, line, k["sub"], x + 80, y + 36, k.ink, maxw=w - 230)
    cw = btn_w(cr, k, "Close", "close")
    btn(cr, k, hits, x + w - cw, y + 8, "Close", ui.close_char, icon_name="close", w=cw)
    y += 64 + 14
    if not tabs:
        return y
    learned = bool(d and d.get("variants"))
    segs(cr, k, hits, x, y, w, [("versions", "Versions", (lambda: ui.sheet_seg("versions")) if learned else None),
                                 ("samples", "Samples", lambda: ui.sheet_seg("samples")),
                                 ("write", "Write more", lambda: ui.sheet_seg("write")),
                                 ("pick", "Compare", (lambda: ui.sheet_seg("pick")) if learned and nv > 1 else None)],
         ui.sheet["seg"])
    y += BTN_H + 6
    sep(cr, k, x, y, w)
    return y + 14


def sheet_foot_text(ui):
    c, n, d, inf, nv = sheet_info(ui)
    st = (ui.font or {}).get("status") or {}
    if st.get("state") == "none":
        return ("What you write here counts at once. Your letters aren’t learned yet: Learn letters now on the font "
                "page puts %s in the preview." % c)
    if n < FV.MIN:
        return ("What you write here counts at once. Once %s has 3 samples, it is learned in the background and "
                "comes into the preview." % c)
    return ("When you close this, %s is learned again in the background and the preview shows it. Your installed "
            "font changes only when you update it." % c)


def sheet_foot(cr, ui, x, y, w):
    """the line at the sheet's foot -> its height"""
    k = ui.theme
    t = sheet_foot_text(ui)
    _, th = PT.measure(cr, t, k["sub"], maxw=w - 36, wrap=True)
    sep(cr, k, x, y, w)
    icon(cr, "sync", 22, x + 11, y + 14 + th / 2, k.ink)
    PT.text(cr, t, k["sub"], x + 34, y + 14, k.ink, maxw=w - 36, wrap=True)
    return th + 28


def sheet_frame(cr, k, w, y, h, top=True, bottom=True):
    """the sheet's card: its edges between y and h (open at a side the writing area continues)"""
    x0, x1 = SHEET_M, w - SHEET_M
    cr.save()
    cr.set_source_rgba(*PT.color(k.ink))
    cr.set_line_width(1.5)
    r = 12
    cr.new_path()
    if top:
        cr.move_to(x0 + 0.75, h)
        cr.line_to(x0 + 0.75, y + r)
        cr.arc(x0 + r, y + r, r - 0.75, math.pi, 1.5 * math.pi)
        cr.line_to(x1 - r, y + 0.75)
        cr.arc(x1 - r, y + r, r - 0.75, 1.5 * math.pi, 0)
        cr.line_to(x1 - 0.75, h)
    elif bottom:
        cr.move_to(x0 + 0.75, y)
        cr.line_to(x0 + 0.75, h - r)
        cr.arc_negative(x0 + r, h - r, r - 0.75, math.pi, 0.5 * math.pi)
        cr.line_to(x1 - r, h - 0.75)
        cr.arc_negative(x1 - r, h - r, r - 0.75, 0.5 * math.pi, 0)
        cr.line_to(x1 - 0.75, y)
    cr.stroke()
    cr.restore()


def char_sheet(cr, ui, hits, w, h):
    """a character's sheet over the font page (the design's char.* boards): its versions,
    its samples, Write more, Which one is nicer?"""
    k = ui.theme
    y0 = chrome(cr, ui, hits, w)
    hits.add(0, y0, w, h - y0, lambda: None)
    y0 += SHEET_M
    PT.box(cr, SHEET_M, y0, w - 2 * SHEET_M, h - y0 - SHEET_M, k["cardR"], bg=k.paper,
           shadow="inset 0 0 0 1.5px " + k.ink)
    x, cw = SHEET_M + 20, w - 2 * SHEET_M - 40
    sh = ui.sheet
    y = sheet_head(cr, ui, hits, x, y0 + 16, cw, tabs=not drilled(sh))
    ft = PT.measure(cr, sheet_foot_text(ui), k["sub"], maxw=cw - 36, wrap=True)[1] + 28
    fy = h - SHEET_M - 12 - ft
    sheet_foot(cr, ui, x, fy, cw)
    seg = sh["seg"]
    if sh["open"]:
        sheet_opened(cr, ui, hits, x, y, cw, fy - 12 - y)
    elif sh["vlist"] is not None:
        sheet_vlist(cr, ui, hits, x, y, cw, fy - 12 - y)
    elif seg == "versions":
        sheet_versions(cr, ui, hits, x, y, cw, fy - 12 - y)
    elif seg == "samples":
        sheet_samples(cr, ui, hits, x, y, cw, fy - 12 - y)
    elif seg == "pick":
        sheet_pick(cr, ui, hits, x, y, cw, fy - 12 - y)
    if ui.msg and not sh["open"]:       # an opened sample has it above its buttons
        PT.text(cr, ui.msg, k["sub"], x, fy - 26, k.ink, maxw=cw)
    if sh["open"] and sh["asking"]:
        delete_question(cr, ui, hits, x, w, h)


LETTERS = "ABCDE"


def sheet_versions(cr, ui, hits, x, y, w, h):
    k = ui.theme
    c, n, d, inf, nv = sheet_info(ui)
    m = ((ui.font or {}).get("model") or {}).get("metrics", {})
    vs = (d or {}).get("variants") or []
    per = max(1, min(len(vs), 5 if w > 900 else 3))
    gap = 14
    cw = (w - gap * (per - 1)) / per
    row = w > 900                       # turned: Favourite and Leave out side by side, the art lower
    art = 88 if row else 150
    ch = 12 + art + 12 + 26 + 44 + 10 + BTN_H + 10 + BTN_H + 12 + (0 if row else BTN_H + 10)
    for i, v in enumerate(vs):
        cx, cy = x + (i % per) * (cw + gap), y + (i // per) * (ch + gap)
        out, fav = v.get("left_out"), v.get("favourite")
        if out:
            dashed_box(cr, k, cx, cy, cw, ch)
        else:
            PT.box(cr, cx, cy, cw, ch, k["cardR"], bg=k.paper,
                   shadow=k["cardOnSh"] if fav else "inset 0 0 0 1.5px " + k.ink)
        PT.box(cr, cx + 12, cy + 12, cw - 24, art, k["cellR"], bg=k.sheet)
        base = draw_var(cr, k, v, m, cx + 24, cy + 22, cw - 48, art - 26)
        baseline(cr, k, cx + 24, cx + cw - 24, base)
        if fav:
            icon(cr, "star", 26, cx + cw - 32, cy + 32, k.ink)
        ty = cy + 12 + art + 12
        ids = len(v.get("ids") or [])
        if out:
            PT.text(cr, "Left out", k["row"], cx + 12, ty, k.ink, weight=600, maxw=cw - 24)
            desc = "Not in your font. Its %d sample%s stay in Samples." % (ids, "" if ids == 1 else "s")
        else:
            PT.text(cr, "%d in 10" % FV.in_ten(FV.font_view((ui.font or {}).get("model"))["chars"][c],
                                              [j for j, u in enumerate(vs) if not u.get("left_out")].index(i)),
                    k["row"], cx + 12, ty, k.ink, weight=600, maxw=cw - 24)
            desc = "%s · %d sample%s" % (FV.strokes_desc(v), ids, "" if ids == 1 else "s")
        PT.text(cr, desc, k["sub"], cx + 12, ty + 26, k.ink, maxw=cw - 24, wrap=True)
        by = ty + 26 + 44 + 10
        bw = cw - 24
        hw = (bw - 10) / 2 if row else bw
        btn(cr, k, hits, cx + 12, by, "Favourite", None if out else (lambda i=i: ui.mark_version(i, "favourite")),
            pri=bool(fav), icon_name="star" if fav else "star_border", w=hw)
        bx, by2 = (cx + 12 + hw + 10, by) if row else (cx + 12, by + BTN_H + 10)
        btn(cr, k, hits, bx, by2, "Put back" if out else "Leave out",
            lambda i=i, out=out: ui.mark_version(i, "back" if out else "out"), icon_name="undo" if out else "block", w=hw)
        btn(cr, k, hits, cx + 12, by2 + BTN_H + 10, "Show its samples", lambda i=i: ui.version_samples(i), w=bw)
    rows = (len(vs) + per - 1) // per
    bottom = y + h
    y += rows * (ch + gap) + 4
    t = ("Your font switches between the ways you write %s, each about as often as you do. A favourite is used more. "
         "Leaving one out doesn’t delete anything." % c)
    _, th = PT.measure(cr, t, k["sub"], maxw=w - 70, wrap=True)
    if not row and y + th + 28 <= bottom:      # turned, the cards take the room
        PT.box(cr, x, y, w, th + 28, k["cardR"], bg=k.sheet)
        icon(cr, "info", 22, x + 26, y + 14 + th / 2, k.ink)
        PT.text(cr, t, k["sub"], x + 52, y + 14, k.ink, maxw=w - 70, wrap=True)


def occ_label(o, c):
    """a sample's line in the sheet: its version, or why it can't be used"""
    sid, pos, st, var, text = o
    if st == "usable":
        return "Version %s" % LETTERS[var] if 0 <= var < len(LETTERS) else "Counts"
    if st == "out":
        return "Left out"
    if st == "way-out":
        return "Version %s, left out" % LETTERS[var] if 0 <= var < len(LETTERS) else "Version left out"
    if st == "joined-after":
        nxt = text[pos + 1] if pos + 1 < len(text) else ""
        return "Joined to the next %s" % nxt if nxt.strip() else "Joined to the next letter"
    if st == "joined-before":
        prv = text[pos - 1] if pos > 0 else ""
        return "Joined to the %s before" % prv if prv.strip() else "Joined to the letter before"
    if st == "odd":
        return "Unlike the others in size"
    return "Word not read clearly"


def sample_word(cr, ui, x, y, w, h, o):
    """the sample's ink (or its text in the script font while the ink is fetched), the
    character underlined"""
    k = ui.theme
    sid, pos, st, var, text = o
    s = ui.inks.get(sid)
    if s and s.get("strokes"):
        strokes = s["strokes"]
        b = ink_bounds(strokes)
        if b:
            bw, bh = max(b[2] - b[0], 1e-6), max(b[3] - b[1], 1e-6)
            sc = min((w - 20) / bw, (h - 22) / bh, 0.6)
            draw_ink(cr, k, strokes, x + 10, y + 6, w - 20, h - 22, 2.2, scale=sc)
            spans = s.get("spans") or []
            mine = [strokes[i] for i in (spans[pos] if pos < len(spans) and spans[pos] else []) if i < len(strokes)]
            mb = ink_bounds(mine)
            if mb:
                oy = y + 6 + (h - 22 - bh * sc) / 2
                ux0, ux1 = x + 10 + (mb[0] - b[0]) * sc, x + 10 + (mb[2] - b[0]) * sc
                PT.fill(cr, ux0, oy + bh * sc + 4, max(ux1 - ux0, 6), 2, k.ink)
            return
    f = k.script % 26
    pre = text[:pos]
    pw = PT.measure(cr, pre, f)[0] if pre else 0
    cw_, th = PT.measure(cr, text[pos], f)
    PT.text(cr, text, f, x + 10, y + 8, k.ink, maxw=w - 20)
    PT.fill(cr, x + 10 + pw, y + 8 + th, max(cw_, 6), 2, k.ink)


def card_versions(ui, occ):
    """the samples' versions as the cards show them (the stored model's order, by its ids)"""
    _, d, _ = ui.char_info(ui.sheet["char"])
    vof = {i: n for n, v in enumerate((d or {}).get("variants") or []) for i in v.get("ids") or []}
    return [(o[0], o[1], o[2], vof.get(o[0], -1) if o[2] in ("usable", "way-out") else o[3], o[4]) for o in occ]


def occ_tile(cr, ui, hits, tx, ty, tw, th, o, c):
    """one of the character's samples: its word, the character underlined, how it counts; a
    tap opens it (FR-033)"""
    k = ui.theme
    st = o[2]
    top_h = th - 34
    if st in ("usable", "way-out"):
        PT.box(cr, tx, ty, tw, th, k["cellR"], bg=k.paper, shadow="inset 0 0 0 1.5px " + k.ink)
        PT.box(cr, tx + 1.5, ty + 1.5, tw - 3, top_h - 1.5, k["cellR"], bg=k.sheet)
    elif st == "out":
        PT.box(cr, tx, ty, tw, th, k["cellR"], bg=k.paper, shadow="inset 0 0 0 1px " + k.rule)
    else:
        dashed_box(cr, k, tx, ty, tw, th)
    sample_word(cr, ui, tx, ty, tw, top_h, o)
    baseline(cr, k, tx + 4, tx + tw - 4, ty + top_h)
    PT.text(cr, occ_label(o, c), k["sub"], tx + 10, ty + top_h + 9, k.ink, maxw=tw - 20)
    hits.add(tx, ty, tw, th, lambda: ui.sheet_sample(o[0], o[1]))


def sheet_samples(cr, ui, hits, x, y, w, h):
    k = ui.theme
    sh = ui.sheet
    c = sh["char"]
    occ = sh.get("occ")
    if occ is None:
        PT.text(cr, "Reading its samples…", k["row"], x, y, k.soft, maxw=w)
        return
    if not occ:
        PT.text(cr, "No sample has %s yet: Write more puts some in." % c, k["row"], x, y, k.soft, maxw=w, wrap=True)
        return
    occ = card_versions(ui, occ)
    use = sum(1 for o in occ if o[2] == "usable")
    out = sum(1 for o in occ if o[2] in ("out", "way-out"))
    bad = len(occ) - use - out
    parts = ["%d of %d samples count towards %s" % (use, len(occ), c)]
    if bad:
        parts.append("%d can’t be used" % bad)
    if out:
        parts.append("%d left out" % out)
    PT.text(cr, " · ".join(parts), k["sub"], x, y, k.ink, maxw=w)
    y += 28
    cols = 6 if w > 900 else 4
    gap = 10
    tw = (w - gap * (cols - 1)) / cols
    th = 96
    undo = sh.get("undo")
    hint = "Tap a sample to see it whole: write it again, delete it, or leave it out of your font."
    nh = 76 if undo else PT.measure(cr, hint, k["sub"], maxw=w, wrap=True)[1] + 10
    rows = max(1, int((h - 28 - nh - BTN_H - 24) // (th + gap)))
    per = rows * cols
    pages = max(1, (len(occ) + per - 1) // per)
    page = min(sh.get("page", 0), pages - 1)
    for i, o in enumerate(occ[page * per:(page + 1) * per]):
        occ_tile(cr, ui, hits, x + (i % cols) * (tw + gap), y + (i // cols) * (th + gap), tw, th, o, c)
    gy = y + rows * (th + gap)
    if undo:
        word = next((o[4] for o in occ if o[0] == undo), "")
        t = ("“%s” is left out of your font. It isn’t deleted: it stays in Samples and still helps the tablet read "
             "you." % word)
        uw = btn_w(cr, k, "Undo", "undo")
        note(cr, k, x, gy, w, nh - 10)
        icon(cr, "info", 22, x + 26, gy + (nh - 10) / 2, k.ink)
        PT.text(cr, t, k["sub"], x + 52, gy + 12, k.ink, maxw=w - uw - 80, wrap=True)
        btn(cr, k, hits, x + w - uw - 12, gy + (nh - 10 - BTN_H) / 2, "Undo", ui.undo_out, icon_name="undo", w=uw)
        gy += nh
    else:
        PT.text(cr, hint, k["sub"], x, gy, k.soft, maxw=w, wrap=True)
        gy += nh
    if pages > 1:
        pager(cr, ui, hits, x, gy + 4, page, pages, lambda d: ui.sheet_page(page + d))


def sheet_pick(cr, ui, hits, x, y, w, h):
    k = ui.theme
    sh = ui.sheet
    c = sh["char"]
    pk = sh.get("pick")
    PT.text(cr, "Which one is nicer?", k["row"], x, y, k.ink, weight=600, maxw=w)
    PT.text(cr, "Two of the ways you write %s. Tap the one you like better." % c, k["sub"], x, y + 26, k.ink, maxw=w)
    y += 60
    by = y + h - 60 - BTN_H
    if not pk:
        PT.text(cr, "Looking for two versions…" if pk is None else
                "Nothing to compare yet: %s needs samples written two ways." % c, k["row"], x, y, k.soft, maxw=w,
                wrap=True)
        return
    gap = 16
    tw = (w - gap) / 2
    th = by - 20 - y
    for i, side in enumerate(("a", "b")):
        cx = x + i * (tw + gap)
        on = pk.get("chosen") == i
        PT.box(cr, cx, y, tw, th, k["cardR"], bg=k.paper, shadow=k["cardOnSh"] if on else "inset 0 0 0 1.5px " + k.ink)
        guide = y + th * 0.68
        baseline(cr, k, cx + 16, cx + tw - 16, guide)
        box = min(tw - 60, th * 0.55)
        draw_ink(cr, k, pk[side][1], cx + (tw - box) / 2, guide - box * 0.9, box, box * 0.9, 4.0, align="center")
        if on:
            icon(cr, "check_circle", 30, cx + tw - 28, y + 28, k.ink)
        hits.add(cx, y, tw, th, lambda i=i: ui.char_choose(i))
    n = ui.char_picks() + sh.get("picks", 0)
    PT.text(cr, "%d pick%s for %s so far" % (n, "" if n == 1 else "s", c), k["sub"], x, by + 15, k.ink)
    btns_right(cr, k, hits, x + w, by, [("Both fine", ui.char_pick, False), ("Skip", ui.char_pick, False)])


# ---- one sample opened in the sheet (spec 003 FR-033)


def opened_state(ui, o, c):
    """how the opened sample counts for the character, in words (None while it is read)"""
    if not o:
        return None
    o = card_versions(ui, [o])[0]
    st, var = o[2], o[3]
    v = " · version %s" % LETTERS[var] if 0 <= var < len(LETTERS) else ""
    if st == "usable":
        return "Counts towards %s%s" % (c, v)
    if st in ("out", "way-out"):
        return occ_label(o, c)
    why = occ_label(o, c)
    return "Can’t be used: " + why[:1].lower() + why[1:]


def opened_rows(ui):
    """the opened sample's rows, (label, value): the text it was written for, what the tablet
    read when it was written (only when the sample kept that reading), when and how it was
    written, how it counts for the character"""
    sid, pos, o, smp = ui.opened()
    smp = smp or {}
    rows = [("Written for", "“%s”" % (smp.get("text") or (o[4] if o else "")))]
    rd = smp.get("reading") or []
    if rd and rd[0].get("text") is not None:
        rows.append(("The tablet read", "“%s”" % rd[0]["text"]))
    t = next((r[1] for r in ui.samples if r[0] == sid), None)
    how = ", ".join(v for v in (when(t) if t else "", smp.get("written") or "") if v)
    if how:
        rows.append(("Written", how))
    st = opened_state(ui, o, ui.sheet["char"])
    if st:
        rows.append(("In your font", st))
    return rows


def _ink(cr, strokes, f, col, width):
    cr.save()
    cr.set_source_rgba(*PT.color(col))
    cr.set_line_width(width)
    cr.set_line_cap(1)
    cr.set_line_join(1)
    for st in strokes:
        if st:
            cr.move_to(*f(st[0]))
            for p in st[1:]:
                cr.line_to(*f(p))
            if len(st) == 1:
                cr.rel_line_to(0.1, 0)
            cr.stroke()
    cr.restore()


def whole_sample(cr, ui, x, y, w, h, smp, pos, text):
    """the whole sample, the character's strokes (its span) picked out on the highlight, the
    rest softer; its text in the script font while the ink is fetched"""
    k = ui.theme
    strokes = (smp or {}).get("strokes") or []
    b = ink_bounds(strokes)
    if not b:
        f = k.script % 60
        tw, th = PT.measure(cr, text, f)
        PT.text(cr, text, f, x + (w - tw) / 2, y + (h - th) / 2, k.soft, maxw=w)
        return
    bw, bh = max(b[2] - b[0], 1e-6), max(b[3] - b[1], 1e-6)
    s = min((w - 40) / bw, (h - 40) / bh)
    ox, oy = x + (w - bw * s) / 2, y + (h - bh * s) / 2
    f = lambda p: (ox + (p[0] - b[0]) * s, oy + (p[1] - b[1]) * s)
    spans = smp.get("spans") or []
    mine = set(spans[pos]) if pos < len(spans) and spans[pos] else set()
    mb = ink_bounds([strokes[i] for i in mine if i < len(strokes)])
    if mb:
        (x0, y0), (x1, y1) = f(mb[:2]), f(mb[2:])
        PT.box(cr, x0 - 8, y0 - 8, x1 - x0 + 16, y1 - y0 + 16, k["cellR"], bg=k.hi)
    wd = max(2.6, min(6.0, s * 3))
    _ink(cr, [st for i, st in enumerate(strokes) if i not in mine], f, k.soft, wd)
    _ink(cr, [strokes[i] for i in sorted(mine) if i < len(strokes)], f, k.pen, wd)


def back_button(cr, ui, hits, x, y, label):
    k = ui.theme
    btn(cr, k, hits, x, y, label, ui.sheet_back, icon_name="arrow_back", w=btn_w(cr, k, label, "arrow_back"))
    return y + BTN_H + 14


def opened_note(o):
    """the line under the rows: what leaving it out means, or how it can count"""
    st = o[2] if o else None
    if st == "out":
        return "Left out, not deleted: it still helps the tablet read you. Put back returns it to your font."
    if st == "way-out":
        return "Its version is left out of your font. Put back on the version returns it."
    if st and st != "usable":
        return "Writing it again is how it can count."
    return "Leaving it out of the font doesn’t delete it: it stays in Samples and still helps the tablet read you."


def sheet_opened(cr, ui, hits, x, y, w, h):
    """one sample opened from Samples or from a version's list: the whole word, its rows,
    Leave out of the font (Put back), Delete and Write it again"""
    k = ui.theme
    sh = ui.sheet
    sid, pos, o, smp = ui.opened()
    if smp is None:
        ui.want_inks([sid])
    v = sh["vlist"]
    bottom = y + h
    y = back_button(cr, ui, hits, x, y, "Version %s" % LETTERS[v] if v is not None and v < len(LETTERS) else "Samples")
    text = (smp or {}).get("text") or (o[4] if o else "")
    land = w > 900
    ih = 130 if land else 176
    rows = opened_rows(ui)
    rx, rw = x, w
    if land:                            # turned: the word beside its rows
        iw = int(w * 0.42)
        ih = max(ih, len(rows) * 46)
        rx, rw = x + iw + 20, w - iw - 20
    else:
        iw = w
    PT.box(cr, x, y, iw, ih, k["rowR"], bg=k.sheet)
    baseline(cr, k, x + 20, x + iw - 20, y + ih * 0.74)
    whole_sample(cr, ui, x, y, iw, ih, smp, pos, text)
    ry = y if land else y + ih + 14
    sep(cr, k, rx, ry, rw, strong=True)
    for name, value in rows:
        _, vh = PT.measure(cr, value, k["row"], maxw=rw - 164, wrap=True)
        rh = max(46, vh + 16)
        lbl(cr, k, name, rx, ry + (rh - 12) / 2)
        PT.text(cr, value, k["row"], rx + 164, ry + (rh - vh) / 2, k.ink, maxw=rw - 164, wrap=True)
        ry += rh
        sep(cr, k, rx, ry, rw)
    y = max(ry, y + ih) + 14
    if sh["redone"]:
        t = "Written again. The new “%s” took the old one’s place." % text
        _, th = PT.measure(cr, t, k["sub"], maxw=w - 70, wrap=True)
        note(cr, k, x, y, w, th + 22)
        icon(cr, "check_circle", 24, x + 28, y + 11 + th / 2, k.ink)
        PT.text(cr, t, k["sub"], x + 54, y + 11, k.ink, maxw=w - 70, wrap=True)
        y += th + 22 + 12
    if not sh["asking"]:
        PT.text(cr, opened_note(o), k["sub"], x, y, k.soft, maxw=w, wrap=True)
    by = bottom - BTN_H
    if ui.msg:
        PT.text(cr, ui.msg, k["sub"], x, by - 30, k.ink, maxw=w)
    st = o[2] if o else None
    if st in ("usable", "out"):         # an unusable sample (and one of a left-out version) has no Leave out
        lab, ic = ("Put back", "undo") if st == "out" else ("Leave out of the font", "block")
        btn(cr, k, hits, x, by, lab, lambda: ui.mark_sample(sid), icon_name=ic, w=btn_w(cr, k, lab, ic))
    rx = x + w
    for lab, ic, fn in (("Write it again", "draw", ui.redo_start if smp else None),
                        ("Delete", "delete", lambda: ui.ask_sheet_delete(True))):
        bw = btn_w(cr, k, lab, ic)
        rx -= bw
        btn(cr, k, hits, rx, by, lab, fn, icon_name=ic, w=bw)
        rx -= 10


def delete_question(cr, ui, hits, x, w, h):
    """Delete's question over the sheet: the same deletion as in Samples (spec 002 FR-009)"""
    k = ui.theme
    _, _, o, smp = ui.opened()
    text = (smp or {}).get("text") or (o[4] if o else "")
    t = ("The whole %s “%s” goes for good, with all its letters: from your font, from what the tablet learns to "
         "read you, and from your contribution. It’s the same as deleting it in Samples." %
         ("word" if " " not in text else "sample", text))
    dw = w - 2 * x
    _, th = PT.measure(cr, t, k["sub"], maxw=dw - 44, wrap=True)
    nh = 20 + 30 + th + 16 + BTN_H + 20
    ny = h - SHEET_M - 14 - nh
    hits.add(0, 0, w, h, lambda: None)
    PT.box(cr, x, ny, dw, nh, k["cardR"], bg=k.paper, shadow=k["dialogSh"])
    PT.text(cr, "Delete this sample?", k["row"], x + 22, ny + 20, k.ink, weight=600)
    PT.text(cr, t, k["sub"], x + 22, ny + 52, k.ink, maxw=dw - 44, wrap=True)
    btns_right(cr, k, hits, x + dw - 22, ny + nh - 20 - BTN_H,
               [("Keep it", lambda: ui.ask_sheet_delete(False), False), ("Delete", ui.sheet_delete, True)])


def vlist_occ(ui):
    """a version's samples: one line of letters samples per id the version names that is
    still stored (its ids, spec 003 FR-033)"""
    sh = ui.sheet
    by = {}
    for o in card_versions(ui, sh["occ"] or []):
        if o[0] not in by or (by[o[0]][2] not in ("usable", "way-out", "out") and o[2] in ("usable", "way-out", "out")):
            by[o[0]] = o
    return [by[i] for i in ui.version_ids(sh["vlist"]) if i in by]


def sheet_vlist(cr, ui, hits, x, y, w, h):
    """a version's Show its samples: the version, then exactly its samples, each opening as
    from Samples"""
    k = ui.theme
    sh = ui.sheet
    c, n, d, inf, nv = sheet_info(ui)
    vs = (d or {}).get("variants") or []
    v = sh["vlist"]
    bottom = y + h
    y = back_button(cr, ui, hits, x, y, "Versions")
    if not 0 <= v < len(vs):
        return
    var = vs[v]
    m = ((ui.font or {}).get("model") or {}).get("metrics", {})
    PT.box(cr, x, y, 72, 88, k["rowR"], bg=k.sheet)
    base = draw_var(cr, k, var, m, x + 8, y + 8, 56, 64)
    baseline(cr, k, x + 8, x + 64, base)
    ids = len(var.get("ids") or [])
    if var.get("left_out"):
        t1 = "Version %s · left out" % LETTERS[v]
    else:
        on = [j for j, u in enumerate(vs) if not u.get("left_out")]
        t1 = "Version %s · %d in 10" % (LETTERS[v], FV.in_ten(FV.font_view((ui.font or {}).get("model"))["chars"][c],
                                                            on.index(v)))
    PT.text(cr, t1, k["row"], x + 86, y + 22, k.ink, weight=600, maxw=w - 86)
    PT.text(cr, "%s · %d sample%s" % (FV.strokes_desc(var), ids, "" if ids == 1 else "s"), k["sub"], x + 86, y + 50,
            k.ink, maxw=w - 86)
    y += 88 + 14
    if sh["occ"] is None:
        PT.text(cr, "Reading its samples…", k["row"], x, y, k.soft, maxw=w)
        return
    occ = vlist_occ(ui)
    cols = 6 if w > 900 else 4
    gap = 10
    tw = (w - gap * (cols - 1)) / cols
    th = 96
    hint = ("The samples that make this way of writing it. Tap one to see it whole: write it again, delete it, or "
            "leave it out of your font.")
    hh = PT.measure(cr, hint, k["sub"], maxw=w, wrap=True)[1] + 14
    rows = max(1, int((bottom - y - hh - BTN_H - 10) // (th + gap)))
    per = rows * cols
    pages = max(1, (len(occ) + per - 1) // per)
    page = min(sh.get("vpage", 0), pages - 1)
    shown = occ[page * per:(page + 1) * per]
    ui.want_inks([o[0] for o in shown])
    for i, o in enumerate(shown):
        occ_tile(cr, ui, hits, x + (i % cols) * (tw + gap), y + (i // cols) * (th + gap), tw, th, o, c)
    y += ((len(shown) + cols - 1) // cols) * (th + gap) + 4
    PT.text(cr, hint, k["sub"], x, y, k.soft, maxw=w, wrap=True)
    if pages > 1:
        pager(cr, ui, hits, x, y + hh, page, pages, lambda dd: ui.sheet_vpage(page + dd))


# ---- writing a sample again (FR-033): the same text, checked as usual, kept in its place


def sheet_redo_top(cr, ui, hits, w, h=None, head=True):
    """Write it again in the sheet: its head, the text, what happens to the old one, a
    failed check"""
    k = ui.theme
    if h:
        fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w) if head else 0
    y += SHEET_M
    land = not head
    x, cw = (SHEET_M + 20, w - 2 * SHEET_M - 40) if not land else (PAD, w - 2 * PAD)
    top = y
    y = sheet_head(cr, ui, hits, x, y + 16, cw, tabs=False)
    chip(cr, k, x, y, "Write it again", ic="draw")
    y += 32 + 16
    _, _, o, smp = ui.opened()
    text = (smp or {}).get("text") or (o[4] if o else "")
    what = "letter" if len(text) == 1 else "word" if " " not in text else "text"
    t = "The same %s again, slowly and with care. The old one stays until this one is kept." % what
    f = k["promptS"] if len(text) > 16 or cw < NARROW else k["prompt"]
    pw, ph = PT.measure(cr, text, f, maxw=cw, wrap=True)
    PT.text(cr, text, f, x, y, k.ink, maxw=cw, wrap=True)
    _, th = PT.measure(cr, t, k["sub"], maxw=cw - pw - 16, wrap=True)
    if cw - pw - 16 >= 200 and th < ph:
        PT.text(cr, t, k["sub"], x + pw + 16, y + ph - th - 6, k.ink, maxw=cw - pw - 16, wrap=True)
        y += ph + 12
    else:
        _, th = PT.text(cr, t, k["sub"], x, y + ph + 6, k.ink, maxw=cw, wrap=True)
        y += ph + 6 + th + 12
    shown = (ui.sheet.get("redo") or {}).get("shown")
    if shown:
        y += check_note(cr, ui, hits, x, y, cw, shown, redo=True) + 12
    if head:
        sheet_frame(cr, k, w, top, y + 1 if not h else h, top=True, bottom=False)
    return y


def sheet_redo_bottom(cr, ui, hits, w, h, canvas):
    """Clear, Cancel, Done and the sheet's foot -> height"""
    k = ui.theme
    fill(cr, k, w, h)
    land = pages_land()
    x, cw = (SHEET_M + 20, w - 2 * SHEET_M - 40) if not land else (PAD, w - 2 * PAD)
    r = ui.sheet.get("redo") or {}
    has_ink = bool(canvas and canvas.strokes)
    free = not r.get("busy") and not r.get("shown")
    narrow = cw < NARROW
    by = 46 if narrow else 14
    bx = btns_right(cr, k, hits, x + cw, by, [
        ("Clear", (lambda: canvas.clear()) if has_ink and free else None, False),
        ("Cancel", ui.redo_cancel if not r.get("busy") else None, False),
        ("Done", (lambda: ui.redo_done(canvas)) if has_ink and free else None, True)])
    line = "Checking it…" if r.get("busy") else "It is checked as usual before it’s kept"
    if narrow:
        PT.text(cr, line, k["sub"], x, 14, k.soft, maxw=cw)
    else:
        PT.text(cr, line, k["sub"], x, by + 15, k.soft, maxw=bx - x - 12, wrap=True)
    y = by + BTN_H + 14
    if ui.msg:
        PT.text(cr, ui.msg, k["sub"], x, y, k.ink, maxw=cw)
        y += 24
    y += sheet_foot(cr, ui, x, y, cw)
    y += 12 + SHEET_M
    if not land:
        sheet_frame(cr, k, w, 0, y - SHEET_M, top=False, bottom=True)
    return y


def sheet_write_top(cr, ui, hits, w, h=None, head=True):
    """Write more in the sheet: its head, how far the character is, the prompts"""
    k = ui.theme
    if h:
        fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w) if head else 0
    y += SHEET_M
    land = not head
    x, cw = (SHEET_M + 20, w - 2 * SHEET_M - 40) if not land else (PAD, w - 2 * PAD)
    top = y
    y = sheet_head(cr, ui, hits, x, y + 16, cw)
    c, n, d, inf, nv = sheet_info(ui)
    if n < FV.MIN:
        ch = 88
        PT.box(cr, x, y, cw, ch, k["cardR"], bg=k.paper, shadow=k["cardOnSh"])
        sx = x + 14
        for i in range(FV.MIN):
            if i < n:
                soft_box(cr, k, sx, y + 12, 56, 64)
                _, tth = PT.measure(cr, c, k.script % 34)
                PT.text(cr, c, k.script % 34, sx + 28, y + 12 + (64 - tth) / 2, k.ink, align=1)
            else:
                dashed_box(cr, k, sx, y + 12, 56, 64)
                _, tth = PT.measure(cr, str(i + 1), k["mono"])
                PT.text(cr, str(i + 1), k["mono"], sx + 28, y + 12 + (64 - tth) / 2, k.ink, align=1)
            sx += 64
        more = FV.MIN - n
        PT.text(cr, "%d more, and %s is in your font" % (more, c), k["row"], sx + 8, y + 14, k.ink, weight=600,
                maxw=x + cw - sx - 22)
        PT.text(cr, "Write it with care, on its own or in a word. Until then, apps show the usual font’s %s." % c,
                k["sub"], sx + 8, y + 40, k.ink, maxw=x + cw - sx - 22, wrap=True)
        y += ch + 14
    wr = ui.writer
    prompts = (wr.best or {}).get("list") if wr and wr.best else None
    prompts = prompts or ui.sheet.get("prompts") or []
    done = (wr.best or {}).get("n", 0) if wr and wr.best else 0
    cx = x
    for i, p in enumerate(prompts):
        label = "%d %s" % (i + 1, p.text)
        cx += chip(cr, k, cx, y, label, on=i == done, done=i < done, outline=i != done) + 8
    y += 32 + 16
    p = wr.prompt if wr else None
    sh = wr.shown if wr else None
    if sh and sh.status == "check":
        # the ink didn't read as the prompt: Redo, Keep it or Skip before going on
        y += check_note(cr, ui, hits, x, y, cw, sh) + 12
    elif p is None or done >= len(prompts):
        _, th = PT.text(cr, "All %d written. Each one counts for %s now." % (len(prompts), c), k["row"], x, y, k.ink,
                        maxw=cw, wrap=True)
        y += th + 14
    else:
        pw, ph = PT.measure(cr, p.text, k["prompt"])
        PT.text(cr, p.text, k["prompt"], x, y, k.ink)
        how = "On its own, slowly and with care." if p.text == c else "In a word, slowly and with care."
        _, sh_ = PT.measure(cr, how, k["sub"])
        PT.text(cr, how, k["sub"], x + pw + 16, y + ph - sh_ - 6, k.ink, maxw=cw - pw - 16)
        y += ph + 12
    if head:
        sheet_frame(cr, k, w, top, y + 1 if not h else h, top=True, bottom=False)
    return y


def sheet_write_bottom(cr, ui, hits, w, h, canvas):
    """the prompt count, Clear, Skip, Next and the sheet's foot -> height"""
    k = ui.theme
    fill(cr, k, w, h)
    land = pages_land()
    x, cw = (SHEET_M + 20, w - 2 * SHEET_M - 40) if not land else (PAD, w - 2 * PAD)
    wr = ui.writer
    has_ink = bool(canvas and canvas.strokes)
    checking = bool(wr and wr.shown and wr.shown.status == "check")
    prompts = ((wr.best or {}).get("list") if wr and wr.best else None) or []
    done = (wr.best or {}).get("n", 0) if wr and wr.best else 0
    live = bool(wr and wr.prompt is not None and done < len(prompts))
    narrow = cw < NARROW
    by = 46 if narrow else 14
    bx = btns_right(cr, k, hits, x + cw, by, [
        ("Clear", (lambda: canvas.clear()) if has_ink and not checking else None, False),
        ("Skip", ui.skip if live and not has_ink and not checking else None, False),
        ("Next", (lambda: canvas.submit()) if live and has_ink and not checking else None, True)])
    line = ("Prompt %d of %d · each one is kept as a careful sample" % (min(done + 1, len(prompts)), len(prompts))
            if prompts else "")
    if checking:
        line = "Redo it, keep it as it is, or skip it first."
    if narrow:
        PT.text(cr, line, k["sub"], x, 14, k.ink, maxw=cw)
    else:
        PT.text(cr, line, k["sub"], x, by + 15, k.ink, maxw=bx - x - 12, wrap=True)
    y = by + BTN_H + 14
    if ui.msg:
        PT.text(cr, ui.msg, k["sub"], x, y, k.ink, maxw=cw)
        y += 24
    y += sheet_foot(cr, ui, x, y, cw)
    y += 12 + SHEET_M
    if not land:
        sheet_frame(cr, k, w, 0, y - SHEET_M, top=False, bottom=True)
    return y


def pages_land():
    return LAND[0]


def sheet_area(cr, k, w, h):
    """the writing area inside the sheet: the sheet's sides and the area's box"""
    cr.save()
    PT.fill(cr, SHEET_M, 0, 1.5, h, k.ink)
    PT.fill(cr, w - SHEET_M - 1.5, 0, 1.5, h, k.ink)
    cr.restore()
    PT.box(cr, SHEET_M + 20, 4, w - 2 * SHEET_M - 40, h - 8, k["cardR"], bg=None, shadow="inset 0 0 0 1.5px " + k.ink)


# ---------------------------------------------------------------- Contribute


def donate_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    lic, n, donor = ui.donation
    on = bool(ui.state.donating and lic)
    cw = w - 2 * PAD
    by = h - 20 - BTN_H
    if ui.picking_licence:
        y = page_head(cr, k, PAD, y + 18, cw, "Contribute", "How may others use your samples?",
                      "Pick the licence for your contribution. Both let anyone use it; you choose once.", small=True, keep_lead=True)
        licence_cards(cr, ui, hits, PAD, y, cw, ui.licence_pick, ui.pick_licence)
        btns_right(cr, k, hits, w - PAD, by, [
            ("Cancel", ui.cancel_licence, False),
            ("Turn on contributing", (lambda: ui.donating(True, ui.licence_pick)) if ui.licence_pick else None, True)])
        return
    if on and ui.reviewing:
        return earlier_review(cr, ui, hits, w, h, y)
    g = ui.guest
    never = GNEVER if g else NEVER
    holds = GHOLDS if g else HOLDS
    if not on:
        y = page_head(cr, k, PAD, y + 18, cw, "Contribute", "Help the reader for everyone",
                      "The handwriting reader is open source. It learns from handwriting people choose to contribute. "
                      "Contributing is off unless you turn it on" + (", here." if g else "."), small=True)
        card(cr, k, PAD, y, cw, 62)
        icon(cr, "toggle_off", 26, PAD + 28, y + 31, k.ink)
        PT.text(cr, "Contributing is off", k["row"], PAD + 54, y + 10, k.ink, weight=600)
        PT.text(cr, "Nothing is set aside. Your samples stay here, in your session." if g else
                "Nothing is set aside. Your samples stay yours alone.", k["sub"], PAD + 54, y + 34, k.soft, maxw=cw - 70)
        y = holds_lists(cr, k, PAD, y + 76, cw, never=never, holds=holds) + 14
        y = how_note(cr, k, PAD, y, cw, "folder", "Nothing is sent by itself. When you choose, your contribution is saved "
                     "as one file in this tablet’s Documents, for the owner to send to the project." if g else
                     "Nothing is sent by itself. When you choose, the app saves your contribution as one file in your "
                     "Documents, and you send it yourself.") + 14
        y = donor_row(cr, ui, hits, PAD, y, cw, donor)
        if g:
            n_ = len(ui.samples)
            chevron_row(cr, k, hits, PAD, y, cw, "grid_view", "Your samples", plural(n_, "sample") +
                        ". Look at one, delete or export them.", lambda: ui.go("samples"), shaded=False)
        msg_line(cr, ui, by - 30, w)
        bw = btn_w(cr, k, "Turn on…")
        btn(cr, k, hits, w - PAD - bw, by, "Turn on…", ui.turn_on, w=bw)
        return
    y = page_head(cr, k, PAD, y + 18, cw, "Contribute", "Thank you for contributing",
                  None if ui.saved or g else "Every sample you keep for a word or sentence the app shows is added. "
                  "Samples you wrote before can be added too, below.", small=True, land_hide=True)
    if ui.saved:
        # right after saving, which file was saved for whom (spec 002 FR-021)
        folder = os.path.basename(os.path.dirname(ui.saved))
        t1 = "Saved as " + os.path.basename(ui.saved) + (", for " + ui.session.name if g else "")
        t2 = ("In Documents › %s, beside the owner’s. Your next contribution starts empty, with the same licence and "
              "number." % folder if g else "In Documents › %s. Send it to the project whenever it suits you." % folder)
        _, h1 = PT.measure(cr, t1, k["row"], maxw=cw - 70)
        _, h2 = PT.measure(cr, t2, k["sub"], maxw=cw - 70, wrap=True)
        ch = h1 + h2 + 26
        card(cr, k, PAD, y, cw, ch)
        icon(cr, "check_circle", 24, PAD + 26, y + ch / 2, k.ink)
        PT.text(cr, t1, k["row"], PAD + 52, y + 10, k.ink, maxw=cw - 70)
        PT.text(cr, t2, k["sub"], PAD + 52, y + 14 + h1, k.soft, maxw=cw - 70, wrap=True)
        y += ch + 14
    on_t = "%s (%s). New samples are added as you write" % LIC.get(lic, (lic, lic)) + ("; Finish saves them." if g else ".")
    _, oh = PT.measure(cr, on_t, k["sub"], maxw=cw - 190, wrap=True)
    bh = max(76, oh + 58)
    soft_box(cr, k, PAD, y, cw, bh)
    icon(cr, "volunteer_activism", 26, PAD + 28, y + bh / 2, k.ink)
    PT.text(cr, "Contributing is on", k["row"], PAD + 54, y + 16, k.ink, weight=600)
    PT.text(cr, on_t, k["sub"], PAD + 54, y + 42, k.soft, maxw=cw - 190, wrap=True)
    PT.text(cr, str(n), k["num"], PAD + cw - 18, y + bh / 2 - 26, k.ink, align=2)
    PT.text(cr, "sample" if n == 1 else "samples", k["mono"], PAD + cw - 18, y + bh / 2 + 12, k.ink, align=2)
    y += bh + 14
    # what a contribution holds: shown when there is room for it (it was shown before turning on)
    y = donor_row(cr, ui, hits, PAD, y, cw, donor)
    # a guest's own samples (beneath this tab, spec 002 FR-018) and the earlier ones, side by side
    rows = []
    if g:
        n_ = len(ui.samples)
        rows.append(("grid_view", "Your samples", ("%d. Look at, delete or export them." % n_) if ui.earlier else
                     plural(n_, "sample") + ". Look at one, delete or export them.", lambda: ui.go("samples")))
    if ui.earlier:
        e = len(ui.earlier)
        rows.append(("add_to_photos", "Add earlier samples",
                     "%d sample%s written before contributing %s not in it. Look through them, if you like." % (
                         e, "" if e == 1 else "s", "is" if e == 1 else "are"), ui.review_earlier))
    rw_ = (cw - 12 * (len(rows) - 1)) / max(len(rows), 1)
    rh = max((chevron_h(cr, k, rw_, sub) for _, _, sub, _ in rows), default=0)
    rest = (rh + 12 if rows else 0) + 30
    probe = cairo.Context(cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))
    if holds_lists(probe, k, PAD, y, cw, never=never, holds=holds) + 14 + rest <= by - 10:
        y = holds_lists(cr, k, PAD, y, cw, never=never, holds=holds) + 14
    for i, (ic, name, sub, fn) in enumerate(rows):
        chevron_row(cr, k, hits, PAD + i * (rw_ + 12), y, rw_, ic, name, sub, fn, shaded=False, min_h=rh)
    y += rh + 12 if rows else 0
    msg_line(cr, ui, by - 30, w)
    btns_right(cr, k, hits, w - PAD, by, [
        ("Erase contribution", ui.ask_erase_donation if n else None, False), ("Turn off", ui.ask_turn_off, False),
        ("Save as a file", ui.write_donation if n else None, True)])


def donor_label(donor):
    """the contributor number as shown: its first eight characters"""
    d = donor.replace("-", "").upper()
    return d[:4] + "-" + d[4:8]


def donor_row(cr, ui, hits, x, y, w, donor):
    """the contributor number, the owner's and each writer's shown alike (spec 002 FR-020),
    and a way to count as another writer from now on"""
    if not donor:
        return y
    k = ui.theme
    sub = "Your contributions carry this instead of a name. It isn’t made from anything about you."
    bw = btn_w(cr, k, "Get a new one")
    tw = w - 56 - bw - 26
    _, th = PT.measure(cr, sub, k["sub"], maxw=tw, wrap=True)
    nw, nh = PT.measure(cr, "Your contributor number", k["row"])
    ch = max(64, nh + th + 20)
    PT.box(cr, x, y, w, ch, k["cardR"], bg=k.paper, shadow=k["cardFlat"])
    tw_, th_ = PT.measure(cr, "#", k["row"], weight=600)
    PT.text(cr, "#", k["row"], x + 29 - tw_ / 2, y + (ch - th_) / 2, k.ink, weight=600)
    PT.text(cr, "Your contributor number", k["row"], x + 56, y + 8, k.ink, maxw=tw)
    PT.text(cr, donor_label(donor), k["mono"], x + 66 + nw, y + 10, k.ink)
    PT.text(cr, sub, k["sub"], x + 56, y + 10 + nh, k.soft, maxw=tw, wrap=True)
    btn(cr, k, hits, x + w - 8 - bw, y + (ch - 44) / 2, "Get a new one", ui.ask_new_donor, w=bw, h=44)
    return y + ch + 14


def earlier_review(cr, ui, hits, w, h, y):
    """the prompted samples kept before contributing was on: a tap leaves one out; Not now adds none"""
    k = ui.theme
    cw = w - 2 * PAD
    total = len(ui.earlier)
    y = page_head(cr, k, PAD, y + 18, cw, "Contribute", "Add your earlier samples too?",
                  "You’ve already written %d sample%s for words the app showed. Tap any you’d rather leave out. If you "
                  "say not now, none are added, and you can come back here any time." % (total, "" if total == 1 else "s"),
                  small=True, keep_lead=True)
    cols = grid_cols(w)
    gap = 10
    tw = (w - 2 * PAD - gap * (cols - 1)) / cols
    th = 74
    by = h - 20 - BTN_H
    rows = max(1, int((by - y - 150) // (th + gap)))
    per = cols * rows
    pages = max(1, (total + per - 1) // per)
    ui.ep = min(ui.ep, pages - 1)
    shown = ui.earlier[ui.ep * per:(ui.ep + 1) * per]
    ui.want_inks([e[0] for e in shown])
    for i, (sid, t, text) in enumerate(shown):
        inside = sid not in ui.earlier_out
        sample_tile(cr, ui, hits, PAD + (i % cols) * (tw + gap), y + (i // cols) * (th + gap), tw, th, sid, t, text,
                    lambda sid=sid: ui.flip_earlier(sid), sel=inside)
    y += rows * (th + gap) + 4
    if pages > 1:
        pager(cr, ui, hits, PAD, y, ui.ep, pages, ui.earlier_page)
    keep = total - len(ui.earlier_out)
    PT.text(cr, "%d of %d to add" % (keep, total), k["sub"], w - PAD, y + 15, k.soft, align=2)
    y += BTN_H + 14
    how_note(cr, k, PAD, y, cw, "info", "Only samples written for words and sentences the app showed are here. Words "
             "from your own list and everyday writing can only be added one at a time, in Samples.")
    btns_right(cr, k, hits, w - PAD, by, [
        ("Not now", ui.close_review, False),
        ("Add %d sample%s" % (keep, "" if keep == 1 else "s") if keep else "Add none",
         ui.add_earlier if keep else ui.close_review, True)])


# ---------------------------------------------------------------- other writers (spec 002 FR-014 to FR-025)


OTHER_NOTES = (("lock", "Kept apart", "Their samples never mix with yours or with each other’s, and nothing here learns "
                "from them."),
               ("volunteer_activism", "Contributing is theirs to choose", "Only the writer can turn it on, in their own "
                "session."),
               ("label", "The name stays here", "It is a label in this list, never in a sample, an export or a "
                "contribution."),
               ("lock_open", "No password", "Whoever holds the tablet can open any writer’s session. Finish, in theirs, "
                "brings back yours."))


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else many or one + "s")


def writer_sub(ui, wid):
    n, _, on = ui.wcounts.get(wid, (0, 0, False))
    t = plural(n, "sample") if n else "No samples yet"
    return t + " · contributing" if on else t


def others_page(cr, ui, hits, w, h):
    """Someone else writes: each writer with their samples, open, rename, remove; Add a
    writer; what a writer is, said plainly"""
    k = ui.theme
    fill(cr, k, w, h)
    y = head(cr, ui, hits, w)
    land = w > h
    cw = w - 2 * PAD
    y = page_head(cr, k, PAD, y + 18, cw, "Other writers" if k.name == "white" else "other writers",
                  "Someone else writes", "Let family or friends write here too. Each writer gets samples and a contribution "
                  "of their own, kept apart from yours.", small=True, keep_lead=True)
    nw = 340 if land else cw
    lw = cw - nw - 24 if land else cw
    items = ui.writers.items
    lbl(cr, k, "Writers", PAD, y)
    if items:
        PT.text(cr, "%d besides you" % len(items), k["sub"], PAD + lw, y - 2, k.soft, align=2)
    y0 = y = y + 26
    by = h - 20 - BTN_H
    if not items:
        t = "Add one when someone would like to write here. You can add as many as you like."
        _, th = PT.measure(cr, t, k["sub"], maxw=lw - 110, wrap=True)
        bh = max(88, th + 60)
        card(cr, k, PAD, y, lw, bh)
        icon(cr, "group", 52, PAD + 44, y + bh / 2, k.ink)
        PT.text(cr, "No other writers yet", k["row"], PAD + 88, y + (bh - th) / 2 - 14, k.ink, maxw=lw - 110)
        PT.text(cr, t, k["sub"], PAD + 88, y + (bh - th) / 2 + 12, k.soft, maxw=lw - 110, wrap=True)
        y += bh + 14
    else:
        rh = 68
        # as many rows as fit above the notes (portrait) or the Back button (landscape)
        room = (by - 20 - y) if land else (by - 24 - y - 66 - notes_height(cr, k, nw))
        per = max(1, int(room // rh))
        sep(cr, k, PAD, y, lw)
        for it in items[:per]:
            wid = it["id"]
            PT.box(cr, PAD, y + 1, lw, rh - 2, "0", bg=k.paper)
            icon(cr, "person", 26, PAD + 18, y + rh / 2, k.ink)
            tw = lw - 200
            PT.text(cr, it["name"], k["row"], PAD + 46, y + 12, k.ink, weight=600, maxw=tw)
            PT.text(cr, writer_sub(ui, wid), k["sub"], PAD + 46, y + 38, k.soft, maxw=tw)
            icon(cr, "chevron_right", 24, PAD + lw - 126, y + rh / 2, k.ink)
            hits.add(PAD, y, lw - 116, rh, lambda wid=wid: ui.open_writer(wid))
            btn(cr, k, hits, PAD + lw - 102, y + 10, "", lambda wid=wid: ui.ask_name(wid), icon_name="edit", w=48)
            btn(cr, k, hits, PAD + lw - 48, y + 10, "", lambda wid=wid: ui.ask_remove(wid), icon_name="delete", w=48)
            y += rh
            sep(cr, k, PAD, y, lw)
        if len(items) > per:
            PT.text(cr, "and %d more" % (len(items) - per), k["sub"], PAD, y + 8, k.soft)
            y += 30
        y += 14
    btn(cr, k, hits, PAD, y, "Add a writer", ui.ask_name, pri=not items, icon_name="person_add", h=52)
    nx, ny = (PAD + lw + 24, y0 - 26) if land else (PAD, y + 52 + 18)
    other_notes(cr, k, nx, ny, nw)
    btn(cr, k, hits, PAD, by, "Back to your writing", lambda: ui.go("write"), icon_name="arrow_back")
    msg_line(cr, ui, by - 30, w)
    if ui.naming:
        name_dialog(cr, ui, hits, w, h)
    elif ui.removing:
        remove_dialog(cr, ui, hits, w, h)


def notes_height(cr, k, w):
    return other_notes(_probe(), k, 0, 0, w)


def _probe():
    return cairo.Context(cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))


def other_notes(cr, k, x, y, w):
    """the side card: what a writer is -> its height"""
    hs = []
    for ic, label, sub in OTHER_NOTES:
        _, h1 = PT.measure(cr, label, k["row"], maxw=w - 76)
        _, h2 = PT.measure(cr, sub, k["sub"], maxw=w - 76, wrap=True)
        hs.append(h1 + h2 + 20)
    total = sum(hs) + 8
    PT.box(cr, x, y, w, total, k["cardR"], bg=k.sheet)
    yy = y + 4
    for i, (ic, label, sub) in enumerate(OTHER_NOTES):
        if i:
            sep(cr, k, x + 18, yy, w - 36)
        icon(cr, ic, 22, x + 30, yy + 18, k.ink)
        _, h1 = PT.text(cr, label, k["row"], x + 56, yy + 8, k.ink, maxw=w - 76)
        PT.text(cr, sub, k["sub"], x + 56, yy + 10 + h1, k.soft, maxw=w - 76, wrap=True)
        yy += hs[i]
    return total


NAME_ERR = {"taken": "There’s already a writer called “%s”. Pick another name.",
            "long": "That’s more than 40 characters. Pick a shorter name."}


def name_dialog(cr, ui, hits, w, h):
    """a writer's name, in the page's upper half (the keyboard takes the lower): a private
    field, 1 to 40 characters, unique (FR-014); the window puts its field over the box"""
    k = ui.theme
    n = ui.naming
    hits.add(0, 0, w, h, lambda: None)
    fill(cr, k, w, h)
    head(cr, ui, hits, w)
    hh = PT.px(k["headH"])
    dw = w - 2 * PAD
    x, y = PAD, hh + 20
    rename = n["kind"] == "rename"
    old = ui.writers.name(n["id"]) if rename else None
    prob = ui.name_problem()
    note = ("Only the label changes. Their samples, contribution and contributor number stay as they are." if rename else
            "Only a label in this list, 1 to 40 characters. It never goes into a sample, an export or a contribution.")
    err = NAME_ERR[prob].replace("%s", n["text"].strip()) if prob in NAME_ERR else None
    _, nh = PT.measure(cr, note, k["sub"], maxw=dw - 44, wrap=True)
    eh = PT.measure(cr, err, k["row"], weight=600, maxw=dw - 80, wrap=True)[1] + 12 if err else 0
    dh = 20 + 34 + 26 + 56 + 14 + eh + nh + 16 + BTN_H + 20
    PT.box(cr, x, y, dw, dh, k["cardR"], bg=k.paper, shadow=k["dialogSh"])
    PT.text(cr, "Rename " + old if rename else "A new writer", k["row"], x + 22, y + 20, k.ink, weight=600, maxw=dw - 44)
    yy = y + 62
    lbl(cr, k, "Name", x + 22, yy)
    yy += 22
    fw = dw - 44
    PT.box(cr, x + 22, yy, fw, 56, k["btnR"], bg=k.paper, shadow="inset 0 0 0 2px " + k.ink)
    pw, ph = PT.measure(cr, "Private", k["sub"])
    PT.text(cr, "Private", k["sub"], x + 22 + fw - 12 - pw, yy + (56 - ph) / 2, k.soft)
    icon(cr, "lock", 18, x + 22 + fw - 24 - pw, yy + 28, k.soft)
    ui.name_rect = (x + 26, yy + 4, fw - pw - 52, 48)
    if ui.demo:                          # the drawn screens show the typed name (the window's field does)
        PT.text(cr, n["text"], k["row"], x + 38, yy + 15, k.ink, maxw=fw - pw - 70)
    yy += 56 + 14
    if err:
        icon(cr, "error", 22, x + 33, yy + 12, k.ink)
        PT.text(cr, err, k["row"], x + 54, yy, k.ink, weight=600, maxw=dw - 80, wrap=True)
        yy += eh
    PT.text(cr, note, k["sub"], x + 22, yy, k.soft, maxw=dw - 44, wrap=True)
    btns_right(cr, k, hits, x + dw - 22, y + dh - 20 - BTN_H, [
        ("Cancel", ui.cancel_name, False),
        ("Use this name" if rename else "Add writer", ui.use_writer_name if not prob else None, True)])
    kb = 230 if w > h else 330
    PT.box(cr, PAD, h - 20 - kb, dw, kb, k["cardR"], bg=k.sheet, shadow=k["cardFlat"])
    icon(cr, "keyboard", 28, w / 2, h - 20 - kb / 2 - 30, k.ink)
    PT.text(cr, "The keyboard comes up here, as for a password field:", k["sub"], w / 2, h - 20 - kb / 2 - 6, k.soft, align=1)
    PT.text(cr, "no suggestions, and it learns nothing from what is typed.", k["sub"], w / 2, h - 20 - kb / 2 + 20, k.soft,
            align=1)


def remove_dialog(cr, ui, hits, w, h):
    """what goes for good and what stays (FR-023); Save their contribution first while one is
    not saved, the question staying open after it"""
    k = ui.theme
    rm = ui.removing
    who = rm["name"]
    goes = [("Their %s" % plural(rm["samples"], "sample")) if rm["samples"] else "Their samples (none yet)"]
    if rm["unsaved"]:
        goes.append("Their contribution not saved as a file yet: %s" % plural(rm["unsaved"], "sample"))
    if rm["donor"]:
        goes.append("Their contributor number")
    goes.append("Where they are in the prompts")
    f = rm["files"]
    stays = ["The %s already saved for them, in Documents › %s" % (plural(f, "contribution file"), CONTRIB) if f else
             "Contribution files saved earlier, in Documents › %s" % CONTRIB, "Your own samples, and every other writer’s"]
    saved = ("Saved %s for %s, in Documents › %s." % (os.path.basename(rm["saved"]), who, CONTRIB)) if rm["saved"] else None
    dw = w - 2 * PAD
    tw = dw - 44 - 30

    def rows_h(items):
        return sum(PT.measure(cr, t, k["sub"], maxw=tw, wrap=True)[1] + 8 for t in items)
    sh = PT.measure(cr, saved, k["sub"], maxw=dw - 44 - 50, wrap=True)[1] + 22 + 12 if saved else 0
    dh = 20 + 34 + sh + 24 + rows_h(goes) + 12 + 24 + rows_h(stays) + 16 + BTN_H + 20
    hits.add(0, 0, w, h, lambda: None)
    x, y = PAD, h - 20 - dh
    PT.box(cr, x, y, dw, dh, k["cardR"], bg=k.paper, shadow=k["dialogSh"])
    PT.text(cr, "Remove %s?" % who, k["row"], x + 22, y + 20, k.ink, weight=600, maxw=dw - 44)
    yy = y + 54
    if saved:
        bh = sh - 12
        PT.box(cr, x + 22, yy, dw - 44, bh, k["cardR"], bg=k.sheet)
        icon(cr, "check_circle", 22, x + 44, yy + bh / 2, k.ink)
        PT.text(cr, saved, k["sub"], x + 66, yy + 11, k.ink, maxw=dw - 44 - 50, wrap=True)
        yy += sh
    for head_, items, ic in (("Goes for good", goes, "remove"), ("Stays as it is", stays, "check")):
        lbl(cr, k, head_, x + 22, yy)
        yy += 24
        for t in items:
            icon(cr, ic, 20, x + 32, yy + 10, k.ink)
            _, th = PT.text(cr, t, k["sub"], x + 52, yy, k.ink, maxw=tw, wrap=True)
            yy += th + 8
        yy += 12
    btns_right(cr, k, hits, x + dw - 22, y + dh - 20 - BTN_H, [("Cancel", ui.cancel_remove, False)] +
               ([("Save their contribution first", ui.save_for_removal, False)] if rm["unsaved"] else []) +
               [("Remove", ui.remove_writer, True)])


CONTRIB = "Handwriting contributions"


def done_note(cr, ui, hits, x, y, w):
    """after Finish, on the owner's Write page: what was saved for whom, that the writer is
    kept, Remove beside it (FR-022) -> its height"""
    k = ui.theme
    d = ui.done
    who = d["name"]
    if d.get("busy"):
        text = "Saving what %s wrote…" % who
    elif d.get("error"):
        text = "Their contribution couldn’t be saved: %s. %s is kept for next time." % (d["error"], who)
    elif d["saved"]:
        text = "Saved %s for %s, in Documents › %s. %s is kept for next time." % (
            os.path.basename(d["saved"]), who, CONTRIB, who)
    else:
        n = d["samples"] or 0
        text = ("Nothing to save. " if d.get("donating", True) else "Nothing to save: %s isn’t contributing. " % who) + \
            "Their %s %s kept for next time." % (plural(n, "sample"), "is" if n == 1 else "are")
    bw = btn_w(cr, k, "Remove") + btn_w(cr, k, "OK") + 10
    narrow = w < 560
    tw = w - 32 - 40 - (0 if narrow else bw + 16)
    _, t1 = PT.measure(cr, who + " has finished", k["row"], weight=600, maxw=tw)
    _, t2 = PT.measure(cr, text, k["sub"], maxw=tw, wrap=True)
    nh = max(BTN_H, t1 + t2 + 4) + 24 + (BTN_H + 12 if narrow else 0)
    PT.box(cr, x, y, w, nh, k["cardR"], bg=k.paper, shadow=k["noteSh"])
    icon(cr, "check_circle" if d["saved"] else "person", 26, x + 30, y + 12 + (nh - (BTN_H + 12 if narrow else 0) - 24) / 2,
         k.ink)
    PT.text(cr, who + " has finished", k["row"], x + 56, y + 12, k.ink, weight=600, maxw=tw)
    PT.text(cr, text, k["sub"], x + 56, y + 14 + t1, k.soft, maxw=tw, wrap=True)
    btns_right(cr, k, hits, x + w - 14, y + nh - 12 - BTN_H if narrow else y + (nh - BTN_H) / 2, [
        ("Remove", (lambda: ui.ask_remove(d["id"])) if not d.get("busy") else None, False), ("OK", ui.close_done, False)])
    return nh


# ---------------------------------------------------------------- the whole window


def loading_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = head(cr, ui, hits, w)
    PT.text(cr, ui.msg or "Loading…", k["row"], PAD, y + 24, k.soft, maxw=w - 2 * PAD, wrap=True)


PAGES = {"samples": samples_page, "coverage": coverage_page, "font": font_page, "learn": learn_page, "donate": donate_page,
         "others": others_page}


def paint_full(cr, ui, hits, w, h):
    LAND[0] = w > h
    if not ui.loaded:
        loading_page(cr, ui, hits, w, h)
    elif ui.page == "wizard":
        wizard_page(cr, ui, hits, w, h)
    elif ui.sets_open:
        sets_page(cr, ui, hits, w, h)
    else:
        PAGES.get(ui.page, samples_page)(cr, ui, hits, w, h)
    if ui.confirm:
        title, text, yes, fn = ui.confirm
        cr.set_source_rgba(1, 1, 1, 0.6)
        cr.paint()
        dialog(cr, ui.theme, hits, w, h, title, text,
               [("Cancel", ui.cancel_confirm, False), (yes, fn, True)])
