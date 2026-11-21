# SPDX-License-Identifier: GPL-2.0-or-later
"""How the app looks: the design's voice tokens on the shell's schemes (e-ink greys; the
voice changes type, edges and rules), and the parts every page is made of. Everything is
drawn with cairo and Pango through glassine_ui.paint, as the settings app draws."""
import re

from glassine_ui import paint as PT, voices

from .tokens import VOICES

PAD = 20                                  # the page's side gutter


def _resolve(v, scheme):
    if isinstance(v, dict):
        return {k: _resolve(x, scheme) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve(x, scheme) for x in v]
    if isinstance(v, str):
        return re.sub(r"@([a-z-]+)", lambda m: scheme.get(m.group(1), "#000000"), v)
    return v


class Look(dict):
    """one voice: its tokens (k["row"], k["btnSh"], ...) and its scheme's colours"""

    def __init__(self, name):
        name = name if name in VOICES else "white"
        g = voices.voice(name)
        s = g["scheme"]
        super().__init__(_resolve(VOICES[name], s))
        self.name = name
        self.s = s
        self.ink, self.paper, self.sheet, self.soft = s["ink"], s["paper"], s["sheet"], s["ink-soft"]
        self.rule, self.dim, self.hi, self.accent, self.on_accent = s["rule"], s["dim"], s["highlight"], \
            s["accent"], s["on-accent"]
        self.pen = s["pen"]
        # the old names the pages used, so a voice change needs no other code
        self.F = self["F"]
        self.row, self.sub, self.title = self["row"], self["sub"], self["displayS"]

    def font(self, px, weight=500, lh=1.25):
        return f"{weight} {px}px/{lh} {self.F}"

    def caps(self):
        return (self["lblVar"], self["lblCase"])

    def eyebrow(self, i, name, total=5):
        """the wizard's step line, in the voice's own way of numbering (a guest's introduction
        has three steps)"""
        if self.name == "white":
            return "Step %d of %d · %s" % (i, total, name)
        if self.name == "editorial":
            return "§ %s · %s" % (self["nums"][i - 1], name.lower())
        return "%s. %s" % (self["nums"][i - 1], name.lower())

    @property
    def script(self):
        """the font a sample word is shown in where there is no ink (the design's Kalam)"""
        return "400 %dpx/1.1 'Kalam',cursive"


# icons: the design's Material Symbols names that glassine_ui doesn't map yet
PT.ICONS.update({k: v for k, v in {
    "grid_view": ["view-grid-symbolic", "view-app-grid-symbolic"],
    "edit": ["document-edit-symbolic", "accessories-text-editor-symbolic"],
    "abc": ["format-text-bold-symbolic", "accessories-character-map-symbolic", "font-x-generic-symbolic"],
    "volunteer_activism": ["emblem-favorite-symbolic", "starred-symbolic"],
    "tablet": ["computer-symbolic", "input-tablet-symbolic"],
    "battery_charging_full": ["battery-full-charging-symbolic", "battery-level-100-charged-symbolic"],
    "fact_check": ["object-select-symbolic", "emblem-ok-symbolic"],
    "outgoing_mail": ["mail-send-symbolic", "mail-unread-symbolic"],
    "toggle_off": ["checkbox-symbolic", "radio-symbolic"],
    "auto_awesome": ["starred-symbolic", "emblem-favorite-symbolic"],
    "short_text": ["format-justify-left-symbolic", "view-list-symbolic"],
    "notes": ["format-justify-fill-symbolic", "view-list-symbolic"],
    "match_case": ["format-text-italic-symbolic", "font-x-generic-symbolic"],
    "pin": ["input-dialpad-symbolic", "accessories-calculator-symbolic"],
    "person_edit": ["avatar-default-symbolic", "system-users-symbolic"],
    "undo": ["edit-undo-symbolic"],
    "add_to_photos": ["list-add-symbolic"],
    "check_box": ["checkbox-checked-symbolic", "object-select-symbolic"],
    "check_box_outline_blank": ["checkbox-symbolic"],
    "star_border": ["non-starred-symbolic", "starred-symbolic"],
    "block": ["action-unavailable-symbolic", "process-stop-symbolic", "dialog-error-symbolic"],
    "group": ["system-users-symbolic", "avatar-default-symbolic"],
    "person_add": ["contact-new-symbolic", "list-add-symbolic"],
    "label": ["bookmark-new-symbolic", "tag-symbolic", "text-x-generic-symbolic"],
    "lock_open": ["changes-allow-symbolic", "system-lock-screen-symbolic"],
    "tag": ["insert-text-symbolic", "format-text-strikethrough-symbolic", "text-x-generic-symbolic"],
    "compare": ["view-dual-symbolic", "object-flip-horizontal-symbolic"],
    "alternate_email": ["mail-unread-symbolic", "insert-text-symbolic", "text-x-generic-symbolic"],
}.items() if k not in PT.ICONS})


def icon(cr, name, sz, cx, cy, col):
    return PT.icon(cr, name, sz, cx, cy, col)


# ---------------------------------------------------------------- tap targets


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


# ---------------------------------------------------------------- text


def lbl(cr, k, s, x, y, col=None, align=0):
    """a small label (WRITE THIS, the eyebrows): the voice's label type"""
    return PT.text(cr, s, k["lbl"], x, y, col or k["lblC"], caps=k.caps(), ls=PT.ls_em(k["lblLs"]), align=align)


def para(cr, k, s, f, x, y, w, col=None):
    return PT.text(cr, s, f, x, y, col or k.ink, maxw=w, wrap=True)


LAND = [False]                            # the page being drawn is landscape (set per paint)


def chevron_h(cr, k, w, sub, min_h=62):
    """the height chevron_row takes"""
    return max(min_h, 26 + 22 + (PT.measure(cr, sub, k["sub"], maxw=w - 100, wrap=True)[1] if sub else 0))


def page_head(cr, k, x, y, w, eyebrow, title, lead=None, small=False, keep_lead=False, land_hide=False):
    """eyebrow, title, lead; -> the y below them. Landscape is short: a tab page there drops
    its lead (unless it says something the person must read first, keep_lead) or its whole
    head (land_hide)"""
    if LAND[0] and eyebrow:
        if land_hide:
            return y
        if not keep_lead:
            lead = None
    if eyebrow:
        _, h = lbl(cr, k, eyebrow, x, y)
        y += h + 8
    if title:
        _, h = PT.text(cr, title, k["displayS" if small else "display"], x, y, k.ink, maxw=w, wrap=True,
                       ls=PT.ls_em(k["displayLs"]))
        y += h + 8
    if lead:
        _, h = PT.text(cr, lead, k["lead"], x, y, k.soft, maxw=w, wrap=True)
        y += h + 4
    return y + 14


# ---------------------------------------------------------------- buttons, cards


BTN_H = 48


def btn_w(cr, k, label, icon_name=None):
    return PT.measure(cr, label, k["btnF"])[0] + (40 if not icon_name else 64)


def btn(cr, k, hits, x, y, label, fn, pri=False, icon_name=None, w=None, h=BTN_H, icon_after=False):
    """a button as the design draws it; fn None: drawn inactive (no target). -> its width"""
    w = w or btn_w(cr, k, label, icon_name)
    on = fn is not None
    if pri and on:
        PT.box(cr, x, y, w, h, k["btnR"], bg=k["priBg"], shadow=k["priSh"])
        col = k["priFg"]
    elif on:
        # an outer shadow under a see-through button would show through it (CSS hides it)
        bg = k["btnBg"] if PT.color(k["btnBg"]) else k.paper
        PT.box(cr, x, y, w, h, k["btnR"], bg=bg, shadow=k["btnSh"])
        col = k.ink
    else:
        PT.box(cr, x, y, w, h, k["btnR"], bg=None, shadow="inset 0 0 0 1.5px " + k.dim)
        col = k.dim
    tw, th = PT.measure(cr, label, k["btnF"])
    if icon_name:
        gap = 8
        total = tw + gap + 22
        lx = x + (w - total) / 2
        ix = lx + tw + gap + 11 if icon_after else lx + 11
        tx = lx if icon_after else lx + 22 + gap
        icon(cr, icon_name, 22, ix, y + h / 2, col)
        PT.text(cr, label, k["btnF"], tx, y + (h - th) / 2, col)
    else:
        PT.text(cr, label, k["btnF"], x + w / 2, y + (h - th) / 2, col, align=1)
    if on:
        hits.add(x, y, w, h, fn)
    return w


def btns_right(cr, k, hits, right, y, items, gap=10):
    """[(label, fn, pri)] from the right edge leftwards; -> the left edge"""
    x = right
    for it in reversed(items):
        label, fn, pri = it[:3]
        ic = it[3] if len(it) > 3 else None
        w = btn_w(cr, k, label, ic)
        x -= w
        btn(cr, k, hits, x, y, label, fn, pri, ic, w=w)
        x -= gap
    return x + gap


def card(cr, k, x, y, w, h, on=False, bg=None):
    PT.box(cr, x, y, w, h, k["cardR"], bg=k["cardOnBg"] if on else (bg or k.paper),
           shadow=k["cardOnSh"] if on else k["cardFlat"])


def note(cr, k, x, y, w, h):
    """a calm note (the failed check, the delete question)"""
    PT.box(cr, x, y, w, h, k["cardR"], bg=k.paper, shadow=k["noteSh"])


def soft_box(cr, k, x, y, w, h):
    """a shaded panel (the welcome's sample, the art of a wizard step)"""
    PT.box(cr, x, y, w, h, k["cardR"], bg=k.sheet)


def sep(cr, k, x, y, w, strong=False):
    PT.rule(cr, x, y, w, k["sepStrong" if strong else "sep"])


def icon_rows(cr, k, x, y, w, rows, icon_sz=24, pad=14, line=True):
    """rows of an icon, a label and a line under it, separated by rules; -> the y below"""
    if line:
        sep(cr, k, x, y, w)
    for ic, label, sub in rows:
        ty = y + pad
        _, h1 = PT.text(cr, label, k["row"], x + 44, ty, k.ink, maxw=w - 44)
        h2 = 0
        if sub:
            _, h2 = PT.text(cr, sub, k["sub"], x + 44, ty + h1 + 2, k.soft, maxw=w - 44, wrap=True)
        icon(cr, ic, icon_sz, x + 12, ty + 12, k.ink)
        y = ty + h1 + 2 + h2 + pad
        if line:
            sep(cr, k, x, y, w)
    return y


def chevron_row(cr, k, hits, x, y, w, ic, label, sub, fn, shaded=True, min_h=62):
    """a row that opens something (Samples, Add earlier samples); -> its height"""
    _, h2 = PT.measure(cr, sub, k["sub"], maxw=w - 100, wrap=True) if sub else (0, 0)
    h = max(min_h, 26 + 22 + h2)
    if shaded:
        PT.box(cr, x, y, w, h, k["rowR"], bg=k.sheet)
    else:
        card(cr, k, x, y, w, h)
    icon(cr, ic, 24, x + 26, y + h / 2, k.ink)
    PT.text(cr, label, k["row"], x + 52, y + 13, k.ink, maxw=w - 100)
    if sub:
        PT.text(cr, sub, k["sub"], x + 52, y + 36, k.soft, maxw=w - 100, wrap=True)
    icon(cr, "chevron_right", 24, x + w - 26, y + h / 2, k.ink)
    hits.add(x, y, w, h, fn)
    return h


def bar(cr, k, x, y, w, n, on, h=10):
    """a step bar: n cells, the first `on` filled (one repaint per step, no animation)"""
    gap = 6
    cw = (w - gap * (n - 1)) / max(n, 1)
    for i in range(n):
        PT.box(cr, x + i * (cw + gap), y, cw, h, k["barR"], bg=k.ink if i < on else k.paper,
               shadow="inset 0 0 0 1.5px " + k.ink)


# ---------------------------------------------------------------- chrome


# Samples and Coverage are pages beneath Learn (spec 002 FR-026)
TABS = (("write", "Write"), ("font", "Autograph"), ("learn", "Learn"), ("donate", "Contribute"))
GUEST_TABS = (("write", "Write"), ("donate", "Contribute"))     # a guest's session (spec 002 FR-018)


def brand_mark(cr, k, cx, cy, col):
    """the app's mark: a pen over a stroke of ink"""
    if icon(cr, "stylus_note", 26, cx, cy, col):
        return
    icon(cr, "draw", 26, cx, cy, col)


def header(cr, k, w, sub, steps=None, step_i=None, done=False):
    """the app's head; steps: the wizard's five, the current one step_i (0-based) -> height"""
    h = PT.px(k["headH"])
    PT.fill(cr, 0, 0, w, h, k["headBg"])
    if k["stitch"] == "block":
        for yy in (7, h - 7):
            PT.rule(cr, 7, yy, w - 14, "1.5px dashed " + k.s["gilt"])
    fg = k["headFg"]
    brand_mark(cr, k, PAD + 18, h / 2, k["brandC"] if k.name != "stationery" else fg)
    _, bh = PT.text(cr, "Handwriting", k["brand"], PAD + 46, h / 2 - 19, k["brandC"],
                    caps=(k["brandVar"], "none"), ls=PT.ls_em(k["brandLs"]))
    PT.text(cr, sub, k["hsub"], PAD + 46, h / 2 + 3, fg)
    if steps:
        sw = PT.px(k["stepW"])
        x = w - PAD - 5 * sw - 4 * 6
        for i in range(5):
            st = k["stepOn"] if i == step_i and not done else k["stepDone"] if done or i < step_i else k["stepTodo"]
            PT.box(cr, x, h / 2 - sw / 2, sw, sw, k["stepR"] if k["stepR"] != "50%" else "999px", bg=st["bg"],
                   shadow=st["sh"])
            _, th = PT.measure(cr, k["nums"][i], k["stepFont"])
            PT.text(cr, k["nums"][i], k["stepFont"], x + sw / 2, h / 2 - th / 2, st["fg"], align=1)
            x += sw + 6
    PT.rule(cr, 0, h - 1.5, w, k["headRule"] if k["headRule"] != "none" else "1px solid " + k.rule)
    return h


TAB_H = 56


def tabs(cr, k, hits, w, y, cur, go, items=TABS):
    """the pages, one tap away; -> the y below"""
    PT.fill(cr, 0, y, w, TAB_H, k.paper)
    n = len(items)
    cw = (w - 24) / n
    for i, (key, label) in enumerate(items):
        x = 12 + i * cw
        on = key == cur
        if on:
            st = k["segOn"]
            PT.box(cr, x + 2, y + 6, cw - 4, TAB_H - 12, k["segR"], bg=st["bg"], shadow=st["sh"])
        _, th = PT.measure(cr, label, k["row"])
        PT.text(cr, label, k["row"], x + cw / 2, y + (TAB_H - th) / 2, k["segOn"]["fg"] if on else k.ink,
                weight=600 if on else None, align=1)
        hits.add(x, y, cw, TAB_H, lambda key=key: go(key))
    sep(cr, k, 0, y + TAB_H - 1, w)
    return y + TAB_H


def footer(cr, k, hits, w, y, h, back=None, status="", skip=None, next_=None, next_label="Continue",
           next_icon="arrow_forward", choices=()):
    """the wizard's foot: Back, a status line, Skip to writing, the next step"""
    PT.fill(cr, 0, y, w, h, k.paper)
    PT.rule(cr, 0, y, w, k["footRule"])
    by = y + (h - BTN_H) / 2
    left = PAD
    if back:
        left += btn(cr, k, hits, PAD, by, "Back", back, icon_name="arrow_back") + 14
    right = w - PAD
    if next_label:
        nw = btn_w(cr, k, next_label, next_icon)
        btn(cr, k, hits, right - nw, by, next_label, next_, pri=True, icon_name=next_icon, w=nw, icon_after=True)
        right -= nw + 14
    for label, ic, fn in reversed(choices):
        cw = btn_w(cr, k, label, ic)
        btn(cr, k, hits, right - cw, by, label, fn, icon_name=ic, w=cw)
        right -= cw + 10
    if skip and right - PT.measure(cr, "Skip to writing", k["btnF"])[0] - 20 < left:
        skip = None                       # no room (a narrow column): Continue goes on anyway
    if skip:
        sw, sh = PT.measure(cr, "Skip to writing", k["btnF"])
        sx = right - sw - 8
        PT.text(cr, "Skip to writing", k["btnF"], sx, by + (BTN_H - sh) / 2, k.ink)
        PT.rule(cr, sx, by + (BTN_H + sh) / 2 + 2, sw, "1.5px solid " + k.ink)
        hits.add(sx - 8, by, sw + 16, BTN_H, skip)
        right = sx - 12
    if status:
        PT.text(cr, status, k["sub"], (left + right) / 2, by + 15, k.soft, align=1, maxw=max(10, right - left))


def checkbox(cr, k, x, y, on, sz=22):
    if on:
        PT.box(cr, x, y, sz, sz, "4px", bg=k.accent)
        cr.save()
        cr.set_source_rgba(*PT.color(k.on_accent))
        cr.set_line_width(2.2)
        cr.move_to(x + sz * .24, y + sz * .52)
        cr.line_to(x + sz * .43, y + sz * .7)
        cr.line_to(x + sz * .78, y + sz * .32)
        cr.stroke()
        cr.restore()
    else:
        PT.box(cr, x, y, sz, sz, "4px", shadow="inset 0 0 0 1.5px " + k.ink)


def radio(cr, k, cx, cy, on, r=11):
    cr.save()
    cr.set_source_rgba(*PT.color(k.ink))
    cr.set_line_width(2)
    cr.new_sub_path()
    cr.arc(cx, cy, r, 0, 6.2832)
    cr.stroke()
    if on:
        cr.new_sub_path()
        cr.arc(cx, cy, r * .5, 0, 6.2832)
        cr.fill()
    cr.restore()


def dialog(cr, k, hits, w, h, title, text, buttons):
    """a question over the page (a dimmed sheet below it does nothing when tapped)"""
    hits.add(0, 0, w, h, lambda: None)
    dw = min(w - 2 * PAD, 620)
    x = (w - dw) / 2
    _, th = PT.measure(cr, text, k["sub"], maxw=dw - 40, wrap=True)
    dh = 20 + 28 + th + 20 + BTN_H + 20
    y = h - dh - 40
    PT.box(cr, x, y, dw, dh, k["cardR"], bg=k.paper, shadow=k["dialogSh"])
    PT.text(cr, title, k["row"], x + 20, y + 20, k.ink, weight=600, maxw=dw - 40)
    PT.text(cr, text, k["sub"], x + 20, y + 48, k.ink, maxw=dw - 40, wrap=True)
    btns_right(cr, k, hits, x + dw - 20, y + dh - 20 - BTN_H, buttons)


# ---------------------------------------------------------------- ink


def ink_bounds(strokes):
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def draw_ink(cr, k, strokes, x, y, w, h, width=2.0, scale=None, align="left"):
    """strokes fitted into the box (aspect kept); scale: a fixed one instead"""
    b = ink_bounds(strokes)
    if not b:
        return
    bw, bh = max(b[2] - b[0], 1e-6), max(b[3] - b[1], 1e-6)
    s = scale or min(w / bw, h / bh)
    ox = x + ((w - bw * s) / 2 if align == "center" else 0)
    oy = y + (h - bh * s) / 2
    cr.save()
    cr.set_source_rgba(*PT.color(k.pen))
    cr.set_line_width(width)
    cr.set_line_cap(1)
    cr.set_line_join(1)
    for st in strokes:
        if not st:
            continue
        cr.move_to(ox + (st[0][0] - b[0]) * s, oy + (st[0][1] - b[1]) * s)
        for p in st[1:]:
            cr.line_to(ox + (p[0] - b[0]) * s, oy + (p[1] - b[1]) * s)
        if len(st) == 1:
            cr.rel_line_to(0.1, 0)
        cr.stroke()
    cr.restore()


def guides(cr, k, x, y, w, h, line, first=None):
    """the writing paper's guide lines (dashed, as the keyboard's paper)"""
    cr.save()
    cr.set_source_rgba(*PT.color(k.rule))
    cr.set_line_width(1)
    cr.set_dash([3, 3])
    yy = y + (first if first is not None else line * 0.62)
    while yy < y + h - 4:
        cr.move_to(x + 16, yy)
        cr.line_to(x + w - 16, yy)
        cr.stroke()
        yy += line
    cr.restore()
