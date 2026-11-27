# SPDX-License-Identifier: GPL-2.0-or-later
"""A page whose content doesn't fit is split into several pages (no scrolling): at a large
interface size or text size a page can be longer than the window.

A paint function marks its body, the part that may be split: body(cr, y) where it starts
(below the head, which stays on every page), foot(cr, y) where it ends (the footer drawn
from y down, or a dialog over the page, stays as it is; without foot the body runs to the
end of the region). Outside a split (run() below) both do nothing, so a page that fits is
drawn exactly as without them.

run() paints a region: it draws the paint function once into a recording first; when the
body ends below its foot (or the region's end), the body is cut into slices between rows
nothing is drawn on and no tap target crosses, each shown below the head with a row of
its own for the page's place ("Page 2 of 3") and the way to the others, above the foot.
At Default and smaller only a body that runs off its region is split (larger(): there a
body into its foot is split too). Only the tap targets of the slice shown are taken. A block taller than a slice is cut where it
has the least ink and no tap target. When the foot leaves the body too little room for a
slice, the region is drawn again tight (tight() is true): a paint function then may put
what its foot holds into the body instead."""
import math

import cairo

from glassine_ui import size as S

from .look import BTN_H

GAP = 10
ROW = BTN_H + 2 * GAP                   # the pager's row under a split body
LEAD = 40                               # at most this much of the room above the body's first row on later pages
SLACK = 8                               # a body this little into its foot (a card's shadow) is not split
CUT_TAP = 40                            # a tap target cut by a slice's edge is kept when this much of it is in the slice

_run = [None]                           # the region being probed or drawn split
_tight = [False]
TIGHT = "tight"


def active():
    """is a region being split (or looked at for it)?"""
    return _run[0] is not None


def height():
    """the height of the region being split"""
    return _run[0].h


def larger():
    """is the interface or its text larger than Default? At Default and smaller a page is
    drawn as designed, where its body may reach under its foot: only one that runs off its
    window is split there"""
    mm, text = S.metrics()[:2]
    return text > 1.001 or mm > S.MM_PER_PX * 1.1


def tight():
    """is the region drawn tight: its foot's room given to the body where it can be?"""
    return _tight[0]


class _Run:
    def __init__(self, mode, h, hits=None, sl=None):
        self.mode, self.h, self.ft, self.hits, self.sl = mode, h, h, hits, sl
        self.y0 = None
        self.open = False
        self.group = None
        self.foot = None                # probe: what the foot draws
        self.over = False
        self.boxes = []                 # probe: the body's tap targets


def body(cr, y):
    """the body starts at y (below the head) -> y"""
    r = _run[0]
    if r is None or r.y0 is not None:
        return y
    r.y0, r.open = y, True
    if r.mode == "probe":
        cr.push_group()
    else:
        a, b = r.sl
        cr.save()
        cr.rectangle(-1e5, y, 2e5, b - a)
        cr.clip()
        cr.translate(0, y - a)
    return y


def foot(cr, y=None):
    """the body ends; what follows is the foot, drawn from y down (None: the region's end),
    or a dialog over the page"""
    r = _run[0]
    if r is None or not r.open:
        return
    r.open = False
    if y is not None:
        r.ft = y
    if r.mode == "probe":
        r.group = cr.pop_group()
        cr.push_group()                 # the foot: where it starts
        r.foot = True
    else:
        cr.restore()


def over(cr):
    """what follows is drawn over the page (a dialog): no part of the body or the foot"""
    r = _run[0]
    if r is None:
        return
    foot(cr)
    if r.mode == "probe" and r.foot is True:
        r.foot = cr.pop_group()
        cr.save()
        cr.rectangle(0, 0, 0, 0)        # not looked at
        cr.clip()


class _ProbeHits:
    def __init__(self, r):
        self.r = r

    def add(self, x, y, w, h, fn):
        if self.r.open:
            self.r.boxes.append((y, h))


class _PageHits:
    """the tap targets of a slice: the body's moved to where the slice is shown, those
    outside it left out"""

    def __init__(self, hits, r):
        self.hits, self.r = hits, r

    def add(self, x, y, w, h, fn):
        r = self.r
        if r.open:
            a, b = r.sl
            y += r.y0 - a
            lo, hi = r.y0, r.y0 + (b - a)
            if y < lo - 0.5 or y + h > hi + 0.5:
                # a target the cut goes through (one under the whole slice, or a block taller
                # than a slice): the slice's part of it, when enough of it is there to tap
                t, e = max(y, lo), min(y + h, hi)
                if e - t < min(h, CUT_TAP):
                    return
                y, h = t, e - t
        self.hits.add(x, y, w, h, fn)


def least(draw, h):
    """the least height region h of draw(cr, hits) can be split in: its head, its foot, the
    pager's row and room for a slice"""
    cr = cairo.Context(cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))
    r = _Run("probe", h)
    _run[0] = r
    try:
        draw(cr, _ProbeHits(r))
    finally:
        _run[0] = None
    return (r.y0 or 0) + (h - r.ft) + ROW + 2 * BTN_H


class Plan:
    def __init__(self, y0, ft, slices):
        self.y0, self.ft, self.slices = y0, ft, slices


def plan(draw, w, h):
    """a recording of draw(cr, hits) -> a Plan when its body doesn't fit, else None (TIGHT:
    it doesn't fit, and leaves too little room for a slice)"""
    rec = cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None)
    cr = cairo.Context(rec)
    r = _Run("probe", h)
    _run[0] = r
    try:
        draw(cr, _ProbeHits(r))
    finally:
        _run[0] = None
    if r.y0 is None:
        return None
    if r.open:
        r.group = cr.pop_group()
    if r.foot is True:
        r.foot = cr.pop_group()
    x, y, ew, eh = r.group.get_surface().ink_extents()
    end = y + eh
    limit = h                           # the body may reach the foot's first ink (larger than Default)
    if r.foot is not None and larger():
        fx, fy, fw, fh = r.foot.get_surface().ink_extents()
        if fh > 0:
            limit = min(limit, fy)
    if eh <= 0 or end <= limit + SLACK:
        return None
    avail = int(math.floor(r.ft - r.y0 - ROW))
    if avail < 2 * BTN_H:
        return TIGHT
    y0 = r.y0
    n = int(math.ceil(end - y0)) + 1
    iw = max(1, int(math.ceil(w)))
    img = cairo.ImageSurface(cairo.FORMAT_A8, iw, n)
    c = cairo.Context(img)
    c.translate(0, -y0)
    c.set_source(r.group)
    c.paint()
    img.flush()
    data, st = img.get_data(), img.get_stride()
    zero = bytes(st)
    empty = [data[i * st:(i + 1) * st] == zero for i in range(n)]
    free = [True] * n                   # no tap target crosses the row (one taller than a slice can't be kept whole)
    for by, bh in r.boxes:
        if bh < avail:
            for i in range(max(0, int(math.floor(by - y0)) + 1), min(n, int(math.ceil(by + bh - y0)))):
                free[i] = False
    blank = [e and f for e, f in zip(empty, free)]

    def ink(i):
        return st - data[i * st:(i + 1) * st].count(0)
    first = next((i for i in range(n) if not empty[i]), 0)
    lead = min(first, LEAD)
    total = n - 1
    slices, s = [], 0
    while total - s > avail:
        cut = next((i for i in range(s + avail, s, -1) if blank[i]), None)
        if cut is None:                 # through a block: where it has the least ink, no tap target
            rows = range(s + avail, s + avail // 2, -1)
            cut = min((i for i in rows if free[i]), key=ink, default=None) or min(rows, key=ink)
            nxt = cut
        else:
            j = next((i for i in range(cut, n) if not blank[i]), n - 1)
            nxt = max(cut, j - lead)
        slices.append((y0 + s, y0 + cut))
        s = nxt
    slices.append((y0 + s, y0 + total))
    if len(slices) < 2:
        return None
    return Plan(y0, r.ft, slices)


def draw_page(cr, hits, h, p, k, draw):
    """draw(cr, hits) with slice k of plan p shown -> what draw returns"""
    r = _Run("page", h, sl=p.slices[k])
    _run[0] = r
    try:
        out = draw(cr, _PageHits(hits, r))
    finally:
        _run[0] = None
        if r.open:
            cr.restore()
    return out
