# SPDX-License-Identifier: GPL-2.0-or-later
"""Neaten my writing (spec 003 Scenario 4): what the person writes on the page is neatened by
the recognizer (docs/recognizer.md, Neatening) one line of the writing area at a time, at the
strength they chose. The raw strokes stay the app's; nothing written here becomes a sample
(no confirm, no donate: FR-016, FR-024). The page is a playground: at Neatest it gives each
line's own top reading (the one shown as "Read as") as the text, so the words are set in the
person's font at once, a word read wrongly set as what it was read as (FR-043)."""

import math
import os
import sys

STRENGTHS = ((0.25, "Light"), (0.5, "Medium"), (0.75, "Strong"), (1.0, "Neatest"))
DEFAULT = 0.5
SESSION = "neaten"
PEN_PYTHON = "/usr/lib/glassine/python"     # where the handwriting package puts the binding


def lines(strokes, line, first):
    """{line index: [stroke indices]}: each stroke on the writing area's line it is written
    on (the guides every `line`, the first `first` from the top; letters sit on a guide, a
    descender reaches a little below it)"""
    out = {}
    for i, s in enumerate(strokes):
        ys = sorted(p[1] for p in s)
        mid = ys[len(ys) // 2]
        out.setdefault(max(0, math.ceil((mid - first - 0.25 * line) / line)), []).append(i)
    return out


def parse_points(text):
    return [tuple(float(v) for v in p.split(",")[:2]) for p in text.split(" ")]


class Neat:
    """the page's ink and its neatened version: run() is called by the worker with the raw
    strokes (as the writing area's payload gives them) and keeps per line what came back,
    so a line that didn't change isn't sent again"""

    def __init__(self, rec, lang="en"):
        self.rec, self.lang = rec, lang
        self.done = {}              # line -> (key, {stroke index: [(x, y)]}, {stroke index: how}, text)
        self.open = False

    def run(self, strokes, units, area, strength, line, first):
        """-> ({stroke index: [(x, y)]}, {stroke index: how}, {line: text}) for every stroke"""
        by_line = lines(strokes, line, first)
        out, how, texts = {}, {}, {}
        for k, idx in sorted(by_line.items()):
            key = (strength, units, tuple(area), tuple(tuple(map(tuple, strokes[i])) for i in idx))
            d = self.done.get(k)
            if not d or d[0] != key:
                # one session, begun again for each line (a begin replaces it)
                self.rec.write(SESSION, self.lang, units, area, [strokes[i] for i in idx])
                self.open = True
                text, res = self.rec.neaten(SESSION, strength)
                if strength >= 1 and text:
                    text, res = self.rec.neaten(SESSION, strength, text)
                d = (key, {idx[n]: parse_points(p) for n, _, p in res}, {idx[n]: h for n, h, _ in res}, text)
                self.done[k] = d
            out.update(d[1])
            how.update(d[2])
            texts[k] = d[3]
        for k in list(self.done):
            if k not in by_line:
                del self.done[k]
        return out, how, texts

    def close(self):
        """the page is left: its sessions end, nothing is kept"""
        if self.open:
            self.rec.end(SESSION)
        self.open = False
        self.done.clear()


def pen_module():
    """glassine_handwriting (the pen interface's binding), or None"""
    if os.environ.get("GLASSINE_HWSAMPLES_NO_PEN") == "1":
        return None
    try:
        import glassine_handwriting
    except ImportError:
        sys.path.append(PEN_PYTHON)
        try:
            import glassine_handwriting
        except ImportError:
            return None
    return glassine_handwriting


class PenInk:
    """the page's ink through the pen interface (docs/pen-interface.md): the compositor's
    strokes are the page's, its neat strokes what is shown, the service reads and neatens
    (word by word, line by line on the guides). The pen's own drawing on the page waits
    in `pending` until the compositor's stroke for it comes; one that never comes (a mouse)
    joins the page as written after a second.

    page: the list the page draws (the canvas's strokes); shown: {index: [(x, y)]} the
    neatened ones; offset: the writing area's origin on the window's surface. The service
    sets no word in the font (it has no confirmed text, FR-043), so at Neatest the page
    neatens its own ink itself (Neat) and shows that."""

    def __init__(self, pen, page, on_change):
        self.pen, self.page, self.on_change = pen, page, on_change
        self.pending = []
        self.index = {}             # the compositor's stroke number -> index on the page
        self.shown = {}
        self.text = ""
        self.offset = (0, 0)
        self.neatens = "neaten" in pen.capabilities     # False: the service isn't there (or left)

    def place(self, x, y, w, h, line, first):
        """the writing area on the surface and its guides (in the area's coordinates)"""
        self.offset = (x, y)
        self.pen.set_area(x, y, w, h)
        self.pen.set_guides(line, first + y)

    def down(self, stroke):
        self.pending.append(stroke)

    def lost(self, stroke):
        """a second after the pen lifted: no stroke from the compositor for it"""
        if stroke in self.pending:
            self.pending.remove(stroke)
            self.page.append(stroke)
            self.on_change()

    def clear(self):
        self.pen.clear()
        self.pending, self.index, self.shown, self.text = [], {}, {}, ""

    # the binding's listener
    def stroke(self, k, points):
        if self.pending:
            self.pending.pop(0)
        ox, oy = self.offset
        self.index[k] = len(self.page)
        self.page.append([(x - ox, y - oy, t, p, tx, ty) for x, y, t, p, tx, ty in points])
        self.on_change()

    def neat(self, k, how, points):
        i = self.index.get(k)
        if i is None:
            return
        ox, oy = self.offset
        self.shown[i] = [(x - ox, y - oy) for x, y, *_ in points]
        self.on_change()

    def capabilities(self, caps):
        self.neatens = "neaten" in caps
        self.on_change()

    def settle(self):
        """the page goes back to the direct path: the pen's own strokes still waiting join it"""
        self.page.extend(self.pending)
        self.pending = []

    def reading(self, text, strokes):
        self.text = text
        self.on_change()
