# SPDX-License-Identifier: GPL-2.0-or-later
"""Neaten my writing (spec 003 Scenario 4): what the person writes on the page is neatened by
the recognizer (docs/recognizer.md, Neatening) one line of the writing area at a time, at the
strength they chose. The raw strokes stay the app's; nothing written here becomes a sample
(no confirm, no donate: FR-016, FR-024)."""

import math

STRENGTHS = ((0.25, "Light"), (0.5, "Medium"), (0.75, "Strong"), (1.0, "Neatest"))
DEFAULT = 0.5
SESSION = "neaten"


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
