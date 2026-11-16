# SPDX-License-Identifier: GPL-2.0-or-later
"""The pages, as the design draws them (GlassineOS/ui designs/handwriting): the first-start
introduction, Write, Samples, Coverage, Learn with Your handwriting, Donate. Each paint
function draws one page into a cairo context and adds its tap targets; none reads or
writes anything itself, the UI object (ui.py) does."""
import os
import time

import cairo
from glassine_ui import paint as PT

from . import fontview as FV, prompts as P
from .look import (BTN_H, LAND, PAD, bar, btn, btn_w, btns_right, card, checkbox, chevron_row, dialog, draw_ink,
                   footer, header, icon, icon_rows, lbl, note, page_head, radio, sep, soft_box, tabs)

SUB = "Teach the tablet your hand"
SET_NAMES = {P.AUTO: "What needs practice", "words": "Words", "sentences": "Sentences", "pangrams": "Pangrams",
             "letters": "Single letters", "digits": "Digits and punctuation", P.OWN: "My own words"}
SETS = ((P.AUTO, "A mix, weighted towards what it knows least about your hand", "auto_awesome"),
        ("words", "Common words, one at a time", "short_text"), ("sentences", "Short everyday sentences", "notes"),
        ("pangrams", "Sentences that use every letter", "abc"), ("letters", "Each letter, small and capital",
                                                               "match_case"),
        ("digits", "0 to 9, and marks like , . ? ! :", "pin"),
        (P.OWN, "A list you keep in your files. Never donated.", "person_edit"))
LICENCES = (("CC0-1.0", "Free for anyone", "CC0",
             "Anyone may use, change and share your samples for anything, with no conditions."),
            ("CC-BY-4.0", "Free, with a mention", "CC BY 4.0",
             "Anyone may use, change and share them, as long as they say the handwriting was donated to "
             "GlassineOS. Your name is not in it."))
LIC = {k: (n, c) for k, n, c, _ in LICENCES}
# what a donation holds, as hw_donation_write writes it (docs/samples.md)
HOLDS = ["The pen strokes you write for the words the app shows", "Those words themselves",
         "The size of the writing area, and which way the tablet was turned",
         "What the tablet read when you wrote it", "Quick or careful, and your picks (with both versions)",
         "A random donor number, the same in all your donations"]
NEVER = ["Your name, or anything else about you", "Anything else about this tablet", "Dates and times",
         "Anything you write in other apps, or words from your own list unless you add them one by one",
         "A font made from your handwriting"]
WIZ = ("w-welcome", "w-try", "w-private", "w-learning", "w-share")
WIZ_NAMES = ("Welcome", "Try one", "Your samples", "Learning", "Donating")
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


def chrome(cr, ui, hits, w):
    """head and tabs of a page; -> the y below"""
    k = ui.theme
    y = header(cr, k, w, SUB)
    return tabs(cr, k, hits, w, y, ui.tab, ui.go)


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
        state = "Kept" + (" · in the donation" if sh.donated_id else "")
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


def check_note(cr, ui, hits, x, y, w, sh):
    """the ink didn't look like the prompt: Redo first, Keep it and Skip beside it"""
    k = ui.theme
    read = sh.reading[0][0] if sh.reading else ""
    t1 = "This doesn’t quite look like “%s”." % sh.prompt.text
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
    btns_right(cr, k, hits, x + w - 14, y + h - 14 - BTN_H, [
        ("Keep it", lambda: ui.act("keep_anyway"), False), ("Skip", lambda: ui.act("skip"), False),
        ("Redo", lambda: ui.act("redo"), True)])
    return h


def write_head(cr, ui, hits, w, h=None):
    """landscape: the head of a writing page across the whole width (the column below
    holds the rest) -> its height"""
    k = ui.theme
    if h:
        fill(cr, k, w, h)
    if ui.page == "wizard":
        return header(cr, k, w, "Getting started" if k.name == "white" else "getting started", steps=True, step_i=1)
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
    lbl(cr, k, ("Your best" if k.name == "white" else "your best") if best else k["writeThis"], PAD, y + 12)
    if best:
        dropdown(cr, k, hits, w - PAD, y, "Your handwriting", lambda: ui.open_best("intro"))
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


def try_top(cr, ui, hits, w, h=None, head=True):
    """the wizard's Try one: a real first prompt"""
    k = ui.theme
    if h:
        fill(cr, k, w, h)
    if head:
        y = wizard_head(cr, ui, w, 1)
    else:
        lbl(cr, k, k.eyebrow(2, WIZ_NAMES[1]), PAD, 26)
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


def wizard_head(cr, ui, w, i):
    k = ui.theme
    hh = header(cr, k, w, "Getting started" if k.name == "white" else "getting started", steps=True, step_i=i,
                done=ui.wstep == "w-ready")
    if ui.wstep == "w-ready":
        return hh + 40
    lbl(cr, k, k.eyebrow(i + 1, WIZ_NAMES[i]), PAD, hh + 26)
    return hh + 52


def wizard_foot(cr, ui, hits, w, h):
    k = ui.theme
    s = ui.wstep
    back = {"w-try": "w-welcome", "w-try-done": "w-welcome", "w-private": "w-try", "w-learning": "w-private",
            "w-share": "w-learning", "w-licence": "w-share", "w-ready": "w-share"}.get(s)
    land = w > h
    status = {"w-share": "Either way is fine; change it any time in Donate." if land else "Your choice, either way", "w-licence": "" if ui.licence_pick else "Pick one to go on"}.get(s, "")
    label = {"w-welcome": "Begin", "w-licence": "Donate with this licence", "w-ready": "Start writing"}.get(s, "Continue")
    nxt = ui.wizard_next if (s != "w-licence" or ui.licence_pick) else None
    if s == "w-try" and ui.tried and not (ui.writer and ui.writer.shown):
        nxt = None                       # its reading is still coming
    if s == "w-share":
        label = None
    choices = [("Not now", "schedule", ui.wizard_decline), ("Yes, I’d like to donate", "volunteer_activism",
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
    y = wizard_head(cr, ui, w, i)
    cw = w - 2 * PAD
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
             "donate it.")])
    elif s in ("w-private", "w-learning"):
        if s == "w-private":
            y = page_head(cr, k, PAD, y, cw, None, "Your samples stay here",
                          "Each word you write is kept as a sample. They belong to you.", small=True)
            art = ("tablet", "Kept on this tablet only. The app doesn’t use the internet.")
            rows = [("grid_view", "Look at any sample", "Samples shows everything you have written here."),
                    ("delete", "Delete what you don’t want", "One at a time, or all at once."),
                    ("folder", "Take a copy with you", "Export saves them all as one file in your Documents."),
                    ("volunteer_activism", "Share only if you choose to", "Next, you can decide about donating. It is "
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
        y = page_head(cr, k, PAD, y, cw, None, "Would you like to donate your samples?",
                      "The handwriting reader in GlassineOS is open source. It learns to read everyone from handwriting "
                      "that people choose to donate. Your samples could help it, if you like.", small=True)
        y = holds_lists(cr, k, PAD, y, cw) + 14
        y = how_note(cr, k, PAD, y, cw, "outgoing_mail", "Nothing is sent by itself. Donating sets a copy aside on the "
                     "tablet. When you like, the app saves it as one file in your Documents, and you send that file to "
                     "the project yourself.") + 18
        if w > h:
            return wizard_foot(cr, ui, hits, w, h)       # the two answers are in the foot
        bw = (cw - 14) / 2
        btn(cr, k, hits, PAD, y, "Not now", ui.wizard_decline, icon_name="schedule", w=bw, h=60)
        btn(cr, k, hits, PAD + bw + 14, y, "Yes, I’d like to donate", lambda: ui.wizard_to("w-licence"),
            icon_name="volunteer_activism", w=bw, h=60)
        PT.text(cr, "Either way is fine. You can change your mind any time in Donate.", k["sub"], w / 2, y + 76, k.soft,
                align=1)
    elif s == "w-licence":
        y = page_head(cr, k, PAD, y, cw, None, "How may others use your samples?",
                      "Pick the licence for your donation. Both let anyone use it; you choose once.", small=True)
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
               "Off · you can turn it on any time in Donate")
        sep(cr, k, PAD, y, cw)
        for ic, name, value in (("school", "Learning", "On its own, while the tablet charges"),
                                ("lock", "Your samples", "Kept on this tablet"), ("volunteer_activism", "Donating", don)):
            icon(cr, ic, 22, PAD + 12, y + 30, k.ink)
            lbl(cr, k, name, PAD + 40, y + 22)
            PT.text(cr, value, k["row"], PAD + 170, y + 18, k.ink, maxw=cw - 170)
            y += 60
            sep(cr, k, PAD, y, cw)
        icon(cr, "draw", 18, PAD + 10, y + 26, k.soft)
        PT.text(cr, "You can also teach it your nicest handwriting, any time, from Learn.", k["sub"], PAD + 32, y + 16,
                k.soft, maxw=cw - 32)
    wizard_foot(cr, ui, hits, w, h)


def holds_lists(cr, k, x, y, w):
    """what a donation holds and what it never holds, side by side; -> the y below"""
    cw = (w - 14) / 2
    hs = []
    for head, items in (("What a donation holds", HOLDS), ("What it never holds", NEVER)):
        hh = 40
        for it in items:
            hh += PT.measure(cr, it, k["sub"], maxw=cw - 52, wrap=True)[1] + 10
        hs.append(hh + 8)
    h = max(hs)
    for i, (head, items, ic) in enumerate((("What a donation holds", HOLDS, "check"),
                                          ("What it never holds", NEVER, "remove"))):
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
    PT.text(cr, "To pick the other licence later, erase the donation in Donate and start a new one.", k["sub"], x, y,
            k.soft, maxw=w, wrap=True)
    return y + 30


# ---------------------------------------------------------------- What to write


def sets_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Write", "What to write",
                  "Choose what the prompts are made of. “What needs practice” mixes everything and favours the letters "
                  "you have written least, or that it reads wrong most often.", small=True, keep_lead=True)
    cols = 2 if LAND[0] else 1
    cw = (w - 2 * PAD - 10 * (cols - 1)) / cols
    y0 = y
    for i, (name, desc, ic) in enumerate(SETS):
        on = ui.state.set == name
        n = len(ui.writer.sets.get(name, [])) if ui.writer and name not in (P.AUTO,) else None
        rh = 62
        x, y = PAD + (i % cols) * (cw + 10), y0 + (i // cols) * (rh + 4)
        if on:
            st = k["sel"]
            PT.box(cr, x, y, cw, rh, k["rowR"], bg=st["bg"], shadow=st["sh"])
            icon(cr, "check", 22, x + cw - 22, y + rh / 2, k.ink)
        icon(cr, ic, 22, x + 20, y + rh / 2, k.ink)
        label = SET_NAMES[name] + ("" if n is None or name != P.OWN else "  (%d)" % n)
        PT.text(cr, label, k["row"], x + 48, y + 10, k.ink, maxw=cw - 100)
        PT.text(cr, desc, k["sub"], x + 48, y + 34, k.soft, maxw=cw - 100)
        hits.add(x, y, cw, rh, lambda name=name: ui.choose_set(name))
    y = y0 + ((len(SETS) + cols - 1) // cols) * (62 + 4)
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


def samples_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    if ui.detail:
        return sample_detail(cr, ui, hits, w, h, y)
    rows = list(reversed(ui.samples))
    n = len(rows)
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Samples", "%d sample%s" % (n, "" if n == 1 else "s"),
                  "Everything you have written here, newest first. Tap one to look at it or delete it." if n else
                  "Nothing written yet. Write shows the first prompt.", small=True)
    cols = grid_cols(w)
    gap = 10
    tw = (w - 2 * PAD - gap * (cols - 1)) / cols
    th = 82
    per_col = max(1, int((h - y - 100) // (th + gap)))
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
    btns_right(cr, k, hits, w - PAD, by, [
        ("Import", ui.import_latest, False), ("Export", ui.export if n else None, False),
        ("Erase all", ui.ask_erase_all if n else None, False)])
    msg_line(cr, ui, by - 30, w)


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
    rows.append(("Donation", "In your donation" if don else "Not in your donation", add))
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
        text = ("It’s removed from your samples, and from your donation, for good. What the tablet has already "
                "learned from it stays until it learns again." if don else
                "It’s removed from your samples for good. What the tablet has already learned from it stays until it "
                "learns again.")
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
          ("Digits and marks", "0123456789.,?!:;’-()\"&@"))
FEW = 5                                  # fewer than this: "a few" (shaded)
WRONG = 0.3                              # read wrong this often (with 3 or more samples): the dot


def coverage_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Coverage", "How much of each character",
                  "The number is how many of your samples have it. Shaded ones could use more; a dot marks the ones it "
                  "often reads wrong.", small=True)
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
    if ui.best == "intro":
        return best_intro(cr, ui, hits, w, h)
    if ui.best == "pick":
        return best_pick(cr, ui, hits, w, h)
    if ui.best == "font":
        return best_font(cr, ui, hits, w, h)
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
    chevron_row(cr, k, hits, PAD, y, w - 2 * PAD, "draw", "Your handwriting",
                "Teach it what your nicest writing looks like", lambda: ui.open_best("intro"))
    by = h - 20 - BTN_H
    x = btns_right(cr, k, hits, w - PAD, by, btns) if btns else w - PAD
    if foot:
        PT.text(cr, foot, k["sub"], PAD, by + 15, k.soft, maxw=x - PAD - 10)
    msg_line(cr, ui, by - 30, w)


# ---------------------------------------------------------------- Your handwriting


def best_intro(cr, ui, hits, w, h):
    k = ui.theme
    y = chrome(cr, ui, hits, w)
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Learn", "Your handwriting",
                  "Show it what your nicest writing looks like: a few words written with care, and now and then a "
                  "choice between two of your own versions.", small=True, keep_lead=True)
    cw = w - 2 * PAD
    for name, desc, fn, art in (
            ("Write it your best", "A word written slowly and carefully. Sometimes once quickly first, then once with "
             "care.", ui.start_best, "Thursday"),
            ("Which one is nicer?", "Two of your own versions side by side. Tap the one you like better.",
             ui.start_pick, "g   g")):
        _, th = PT.measure(cr, desc, k["sub"], maxw=cw - 230, wrap=True)
        ch = max(108, th + 60)
        card(cr, k, PAD, y, cw, ch)
        soft_box(cr, k, PAD + 18, y + 16, 132, ch - 32)
        script_word(cr, k, art, PAD + 28, y + ch / 2 - 22, 30)
        PT.text(cr, name, k["row"], PAD + 168, y + 28, k.ink, weight=600, maxw=cw - 230)
        PT.text(cr, desc, k["sub"], PAD + 168, y + 54, k.ink, maxw=cw - 230, wrap=True)
        icon(cr, "chevron_right", 24, PAD + cw - 30, y + ch / 2, k.ink)
        hits.add(PAD, y, cw, ch, fn)
        y += ch + 14
    desc = "Your letters as a font for any app. See which ones it has, then install it."
    _, th = PT.measure(cr, desc, k["sub"], maxw=cw - 230, wrap=True)
    ch = max(108, th + 60)
    card(cr, k, PAD, y, cw, ch)
    soft_box(cr, k, PAD + 18, y + 16, 132, ch - 32)
    script_word(cr, k, "Aa Bb", PAD + 28, y + ch / 2 - 22, 30)
    PT.text(cr, "Your font", k["row"], PAD + 168, y + 28, k.ink, weight=600, maxw=cw - 230)
    PT.text(cr, desc, k["sub"], PAD + 168, y + 54, k.ink, maxw=cw - 230, wrap=True)
    icon(cr, "chevron_right", 24, PAD + cw - 30, y + ch / 2, k.ink)
    hits.add(PAD, y, cw, ch, ui.open_font)
    y += ch + 14
    t = "Tidying notes you wrote in a hurry. What you teach it now is kept for then."
    _, th = PT.measure(cr, t, k["sub"], maxw=cw - 70, wrap=True)
    soft_box(cr, k, PAD, y, cw, th + 46)
    icon(cr, "schedule", 22, PAD + 26, y + 26, k.ink)
    lbl(cr, k, "Coming later" if k.name == "white" else "coming later", PAD + 54, y + 16)
    PT.text(cr, t, k["sub"], PAD + 54, y + 36, k.ink, maxw=cw - 70, wrap=True)
    careful = sum(1 for v in ui.marked.values() if v[0] == "careful")
    picks = len(ui.prefs)
    s = ("%d careful sample%s and %d pick%s so far. They are kept like your other samples. If you donate, a pick goes "
         "along only with both of its versions; a font made from your writing never does."
         % (careful, "" if careful == 1 else "s", picks, "" if picks == 1 else "s"))
    by = h - 20 - BTN_H
    btn(cr, k, hits, PAD, by, "Learn", lambda: ui.open_best(None), icon_name="arrow_back")
    _, th = PT.measure(cr, s, k["sub"], maxw=cw, wrap=True)
    PT.text(cr, s, k["sub"], PAD, by - 16 - th, k.soft, maxw=cw, wrap=True)


def best_pick(cr, ui, hits, w, h):
    """two of the person's own versions of one character, ink only, large"""
    k = ui.theme
    y = chrome(cr, ui, hits, w)
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Your handwriting", "Which one is nicer?",
                  "Tap the one you like better." if ui.pick else None, small=True)
    pk = ui.pick
    by = h - 20 - BTN_H
    if not pk:
        PT.text(cr, "Looking for two versions…" if ui.pick_loading else
                "Not enough to choose from yet: write a few more words, and two versions of the same letter come up "
                "here.", k["row"], PAD, y, k.soft, maxw=w - 2 * PAD, wrap=True)
        btn(cr, k, hits, PAD, by, "Back", lambda: ui.open_best("intro"), icon_name="arrow_back")
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


def draw_hand(cr, k, model, text, x, y, xh, maxw, col=None):
    """text in the person's font as it will look (fontview.layout), wrapped at spaces; a
    character the font lacks in the system's font. y: the first baseline. -> the last
    baseline"""
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
    ink = max(1.5, m.get("ink_width", 0.07) * xh)
    cr.save()
    cr.set_line_width(ink)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for i, ln in enumerate(lines):
        by = y + i * line_h
        for c, strokes, cx, adv in FV.layout(model, ln.rstrip()):
            if strokes is None:
                PT.text(cr, c, "400 %dpx/1 sans-serif" % max(8, int(xh * 1.5)), x + cx * xh, by - xh * 1.25,
                        k.soft)
                continue
            cr.set_source_rgba(*PT.color(col or k.pen))
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


def best_font(cr, ui, hits, w, h):
    """Your font (spec 003 Scenario 3): a preview in the font, which letters it has, what is
    missing with a way to write it now, the name, make and install"""
    k = ui.theme
    y = chrome(cr, ui, hits, w)
    y = page_head(cr, k, PAD, y + 18, w - 2 * PAD, "Your handwriting", "Your font",
                  "A letter is in it once you have written it on its own 3 times; the rest comes from the usual font.",
                  small=True)
    cw = w - 2 * PAD
    by = h - 20 - BTN_H
    f = ui.font or {}
    st, model = f.get("status") or {}, f.get("model")
    if not ui.font:
        PT.text(cr, "Looking at your letters…", k["row"], PAD, y, k.soft, maxw=cw, wrap=True)
        btn(cr, k, hits, PAD, by, "Back", lambda: ui.open_best("intro"), icon_name="arrow_back")
        return
    # the preview
    ph = 112
    soft_box(cr, k, PAD, y, cw, ph)
    lbl(cr, k, "Preview" if model else "No font yet", PAD + 18, y + 14)
    if model and any(c[2] for c in FV.coverage(model)):
        draw_hand(cr, k, model, FV.PREVIEW, PAD + 18, y + 66, 14, cw - 36)
    else:
        PT.text(cr, "Write a few words with care first: once letters have 3 samples, they show here.", k["sub"],
                PAD + 18, y + 44, k.ink, maxw=cw - 36, wrap=True)
    y += ph + 16
    # the letters
    cov = FV.coverage(model)
    have = sum(1 for c in cov if c[2])
    lbl(cr, k, "In your font: %d of %d" % (have, len(cov)), PAD, y)
    y += 22
    cols = 26 if w > 900 else 13
    gap = 5
    cwid = (cw - gap * (cols - 1)) / cols
    chh = 38
    for i, (c, n, ok) in enumerate(cov):
        cx, cy = PAD + (i % cols) * (cwid + gap), y + (i // cols) * (chh + gap)
        if ok:
            PT.box(cr, cx, cy, cwid, chh, k["cellR"], bg=k.paper, shadow="inset 0 0 0 1px " + k.rule)
            PT.text(cr, c, k["row"], cx + cwid / 2, cy + 8, k.ink, align=1)
        else:
            PT.box(cr, cx, cy, cwid, chh, k["cellR"], bg=k.hi if n else k.paper,
                   shadow="inset 0 0 0 1.5px " + k.soft)
            PT.text(cr, c, k["sub"], cx + cwid / 2, cy + 2, k.soft, align=1)
            PT.text(cr, "%d/%d" % (min(n, FV.MIN), FV.MIN), k["mono"], cx + cwid / 2, cy + chh - 17, k.soft,
                    align=1)
    y += ((len(cov) + cols - 1) // cols) * (chh + gap) + 10
    miss = FV.missing(model)
    if miss:
        t = "Not in it yet: " + " ".join(miss[:24]) + (" …" if len(miss) > 24 else "")
        bw = btn_w(cr, k, "Write these now", "edit")
        _, th = PT.measure(cr, t, k["sub"], maxw=cw - bw - 16, wrap=True)
        PT.text(cr, t, k["sub"], PAD, y + 4, k.ink, maxw=cw - bw - 16, wrap=True)
        btn(cr, k, hits, PAD + cw - bw, y, "Write these now", ui.write_missing, icon_name="edit", w=bw)
        y += max(th, BTN_H) + 14
    # the name and what is installed
    sep(cr, k, PAD, y, cw)
    y += 14
    name = ui.font_shown_name()
    tw, _ = lbl(cr, k, "Name", PAD, y + 15)
    PT.text(cr, name, k["row"], PAD + tw + 14, y + 11, k.ink, weight=600, maxw=cw - tw - 160)
    rw = btn_w(cr, k, "Rename")
    btn(cr, k, hits, PAD + cw - rw, y, "Rename", ui.rename_font if not ui.font_busy else None, w=rw)
    y += BTN_H + 10
    inst = st.get("font")
    if ui.font_busy:
        foot = "Making your font… The tablet stays usable meanwhile."
    elif inst and st.get("state") == "current":
        foot = "Installed, version %d. Apps show it in their font lists." % inst[1]
    elif inst:
        foot = "Installed, version %d. You wrote more since: Update makes it again under the same name." % inst[1]
    elif have:
        foot = "Not installed yet. Only you have it on this tablet; it is never donated."
    else:
        foot = ""
    btn(cr, k, hits, PAD, by, "Back", lambda: ui.open_best("intro"), icon_name="arrow_back")
    btns = []
    if inst and not ui.font_busy:
        btns.append(("Remove", ui.ask_remove_font, False))
    label = "Update" if inst else "Install"
    can = have and not ui.font_busy and not (inst and st.get("state") == "current" and not ui.font_name)
    btns.append((label, ui.make_font if can else None, True))
    btns_right(cr, k, hits, w - PAD, by, btns)
    if ui.msg:
        msg_line(cr, ui, by - 30, w)
    elif foot:
        PT.text(cr, foot, k["sub"], PAD, by - 30, k.soft, maxw=cw, wrap=True)


# ---------------------------------------------------------------- Donate


def donate_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = chrome(cr, ui, hits, w)
    lic, n, donor = ui.donation
    on = bool(ui.state.donating and lic)
    cw = w - 2 * PAD
    by = h - 20 - BTN_H
    if ui.picking_licence:
        y = page_head(cr, k, PAD, y + 18, cw, "Donate", "How may others use your samples?",
                      "Pick the licence for your donation. Both let anyone use it; you choose once.", small=True, keep_lead=True)
        licence_cards(cr, ui, hits, PAD, y, cw, ui.licence_pick, ui.pick_licence)
        btns_right(cr, k, hits, w - PAD, by, [
            ("Cancel", ui.cancel_licence, False),
            ("Turn on donating", (lambda: ui.donating(True, ui.licence_pick)) if ui.licence_pick else None, True)])
        return
    if on and ui.reviewing:
        return earlier_review(cr, ui, hits, w, h, y)
    if not on:
        y = page_head(cr, k, PAD, y + 18, cw, "Donate", "Help the reader for everyone",
                      "The handwriting reader is open source. It learns from handwriting people choose to donate. "
                      "Donating is off unless you turn it on.", small=True)
        card(cr, k, PAD, y, cw, 62)
        icon(cr, "toggle_off", 26, PAD + 28, y + 31, k.ink)
        PT.text(cr, "Donating is off", k["row"], PAD + 54, y + 10, k.ink, weight=600)
        PT.text(cr, "Nothing is set aside. Your samples stay yours alone.", k["sub"], PAD + 54, y + 34, k.soft,
                maxw=cw - 70)
        y = holds_lists(cr, k, PAD, y + 76, cw) + 14
        y = how_note(cr, k, PAD, y, cw, "folder", "Nothing is sent by itself. When you choose, the app saves your "
                     "donation as one file in your Documents, and you send it yourself.") + 14
        y = donor_row(cr, ui, hits, PAD, y, cw, donor)
        msg_line(cr, ui, by - 30, w)
        bw = btn_w(cr, k, "Turn on…")
        btn(cr, k, hits, w - PAD - bw, by, "Turn on…", ui.turn_on, w=bw)
        return
    y = page_head(cr, k, PAD, y + 18, cw, "Donate", "Thank you for donating",
                  None if ui.saved else "Every sample you keep for a word or sentence the app shows is added. Samples "
                  "you wrote before can be added too, below.", small=True, land_hide=True)
    if ui.saved:
        card(cr, k, PAD, y, cw, 62)
        icon(cr, "check_circle", 24, PAD + 26, y + 31, k.ink)
        PT.text(cr, "Saved as " + os.path.basename(ui.saved), k["row"], PAD + 52, y + 10, k.ink, maxw=cw - 70)
        PT.text(cr, "In Documents › Handwriting donations. Send it to the project whenever it suits you.", k["sub"],
                PAD + 52, y + 34, k.soft, maxw=cw - 70)
        y += 76
    soft_box(cr, k, PAD, y, cw, 76)
    icon(cr, "volunteer_activism", 26, PAD + 28, y + 38, k.ink)
    PT.text(cr, "Donating is on", k["row"], PAD + 54, y + 16, k.ink, weight=600)
    PT.text(cr, "%s (%s). New samples are added as you write." % LIC.get(lic, (lic, lic)), k["sub"], PAD + 54, y + 42,
            k.soft, maxw=cw - 190)
    PT.text(cr, str(n), k["num"], PAD + cw - 18, y + 12, k.ink, align=2)
    PT.text(cr, "sample" if n == 1 else "samples", k["mono"], PAD + cw - 18, y + 50, k.ink, align=2)
    y += 90
    # what a donation holds: shown when there is room for it (it was shown before turning on)
    rest = (62 + 12 if ui.earlier else 0) + 60 + 30
    probe = cairo.Context(cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))
    if holds_lists(probe, k, PAD, y, cw) + 14 + rest <= by - 10:
        y = holds_lists(cr, k, PAD, y, cw) + 14
    if ui.earlier:
        e = len(ui.earlier)
        y += chevron_row(cr, k, hits, PAD, y, cw, "add_to_photos", "Add earlier samples",
                         "%d sample%s written before donating %s not in it. Look through them, if you like." % (
                             e, "" if e == 1 else "s", "is" if e == 1 else "are"), ui.review_earlier, shaded=False) + 12
    y = donor_row(cr, ui, hits, PAD, y, cw, donor)
    msg_line(cr, ui, by - 30, w)
    btns_right(cr, k, hits, w - PAD, by, [
        ("Erase donation", ui.ask_erase_donation if n else None, False), ("Turn off", ui.ask_turn_off, False),
        ("Save as a file", ui.write_donation if n else None, True)])


def donor_row(cr, ui, hits, x, y, w, donor):
    """quiet: what the donor ID is, and a way to count as another writer from now on"""
    if not donor:
        return y
    k = ui.theme
    t = "Donor ID: %s… — a random number, the same in all your donations, so they count as one writer's. It says " \
        "nothing about you." % donor[:4]
    gw, _ = PT.measure(cr, "Get a new one", k["sub"])
    _, th = PT.text(cr, t, k["sub"], x, y, k.soft, maxw=w - gw - 30, wrap=True)
    PT.text(cr, "Get a new one", k["sub"], x + w, y, k.ink, align=2)
    PT.rule(cr, x + w - gw, y + 20, gw, "1px solid " + k.ink)
    hits.add(x + w - gw - 10, y - 10, gw + 20, 44, ui.ask_new_donor)
    return y + max(th, 24) + 14


def earlier_review(cr, ui, hits, w, h, y):
    """the prompted samples kept before donating was on: a tap leaves one out; Not now adds none"""
    k = ui.theme
    cw = w - 2 * PAD
    total = len(ui.earlier)
    y = page_head(cr, k, PAD, y + 18, cw, "Donate", "Add your earlier samples too?",
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


# ---------------------------------------------------------------- the whole window


def loading_page(cr, ui, hits, w, h):
    k = ui.theme
    fill(cr, k, w, h)
    y = header(cr, k, w, SUB)
    PT.text(cr, ui.msg or "Loading…", k["row"], PAD, y + 24, k.soft, maxw=w - 2 * PAD, wrap=True)


PAGES = {"samples": samples_page, "coverage": coverage_page, "learn": learn_page, "donate": donate_page}


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
