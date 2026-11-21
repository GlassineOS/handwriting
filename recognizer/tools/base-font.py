#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Autograph's base font as pen strokes (spec 003 FR-009, FR-045; docs/recognizer.md, The base
font): the centre lines of an outline font's letters, written to the file the recognizer
reads, so the base is drawn like the person's letters (their ink width, metrics and styles).

    base-font.py [--chars CHARS] [--keep-slant] [--min-match X] [--sheet PNG] FONT.ttf OUT.jsonl

For a monoline font (every stroke one pen's width, round at its ends, as the school print
models are): each glyph is filled at about 160 pixels per x-height (non-zero winding), thinned
to a one-pixel skeleton (Zhang-Suen, then every pixel the skeleton stays connected without
removed), its short spurs pruned (a corner's or an end's thinning leaves them), and traced into
strokes: at a junction the branches that run on most straight continue each other, a dot (a
component that thins to almost nothing) is one point. The strokes are smoothed, simplified,
upright (the font's measured slant taken out, unless --keep-slant), turned so each starts at
its top (a mostly level one at its left; a loop at its top, anticlockwise) and ordered by
where they start, the short marks (dots, accents) last.

The gate (--min-match, 0.8 unless given): each glyph is drawn again from its strokes at the
measured pen width and compared with its filled outline (shared pixels over the pixels of
either); a glyph below it fails the run. --sheet draws every glyph's outline with its strokes
over it (needs pycairo), for looking.

The output: JSON lines. First {"format": "glassine-handwriting-base", "version": 1, "family",
"copyright", "licence", "licence_url", "source" (the file's name), "sha256", "slant" (tan, the
font's own, kept or 0), "measured_slant", "cap_height", "ascender", "descender", "ink_width",
"letter_spacing", "word_spacing"}: lengths in x-heights of the centre lines; then one
{"char": C, "strokes": [[[x, y], ...], ...]} per character, x-heights, y up from the baseline,
x from the glyph's own left edge. A font made from it is a Modified Version of the font under
its licence (SIL Open Font License 1.1): it must not take a Reserved Font Name, and carries the
copyright and licence on.
"""
import argparse
import hashlib
import json
import math
import os
import sys

import numpy as np
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

PX_PER_XH = 160.0
PAD = 6


class FlatPen(BasePen):
    """the outline as polygons (font units), curves flattened"""

    def __init__(self, gs, step=4.0):
        super().__init__(gs)
        self.contours, self.cur, self.step = [], None, step

    def _moveTo(self, p):
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, a, b, c):
        p0 = self.cur[-1]
        n = max(2, int(math.dist(p0, a) + math.dist(a, b) + math.dist(b, c)) // int(self.step))
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            self.cur.append((u ** 3 * p0[0] + 3 * u * u * t * a[0] + 3 * u * t * t * b[0] + t ** 3 * c[0],
                             u ** 3 * p0[1] + 3 * u * u * t * a[1] + 3 * u * t * t * b[1] + t ** 3 * c[1]))

    def _qCurveToOne(self, a, b):
        p0 = self.cur[-1]
        n = max(2, int(math.dist(p0, a) + math.dist(a, b)) // int(self.step))
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            self.cur.append((u * u * p0[0] + 2 * u * t * a[0] + t * t * b[0], u * u * p0[1] + 2 * u * t * a[1] + t * t * b[1]))

    def _closePath(self):
        if self.cur and len(self.cur) > 2:
            self.contours.append(self.cur)
        self.cur = None

    _endPath = _closePath


def fill(contours, s, ox, oy, w, h):
    """the outline filled (non-zero winding) on a w x h grid, pixel (i, j) centred at font
    point ((j + 0.5) / s - ox, (h - i - 0.5) / s - oy): y up in font units, rows down"""
    img = np.zeros((h, w), bool)
    rows, xs, ds = [], [], []
    for c in contours:
        p = np.array(c, float)
        q = np.roll(p, -1, axis=0)
        for (x0, y0), (x1, y1) in zip(p, q):
            # in pixel space: X = (x + ox) * s, Y (up) = (y + oy) * s
            X0, Y0, X1, Y1 = (x0 + ox) * s, (y0 + oy) * s, (x1 + ox) * s, (y1 + oy) * s
            if Y0 == Y1:
                continue
            lo, hi = min(Y0, Y1), max(Y0, Y1)
            k0, k1 = int(math.ceil(lo - 0.5)), int(math.ceil(hi - 0.5))
            if k1 <= k0:
                continue
            k = np.arange(k0, k1)
            yc = k + 0.5
            x = X0 + (yc - Y0) * (X1 - X0) / (Y1 - Y0)
            rows.append(k)
            xs.append(x)
            ds.append(np.full(len(k), 1 if Y1 > Y0 else -1))
    if not rows:
        return img
    rows, xs, ds = np.concatenate(rows), np.concatenate(xs), np.concatenate(ds)
    order = np.lexsort((xs, rows))
    rows, xs, ds = rows[order], xs[order], ds[order]
    cuts = np.flatnonzero(np.diff(rows)) + 1
    for r, x, d in zip(np.split(rows, cuts), np.split(xs, cuts), np.split(ds, cuts)):
        k = r[0]
        if not 0 <= k < h:
            continue
        wn = np.cumsum(d)
        for i in range(len(x) - 1):
            if wn[i] != 0:
                a, b = int(math.ceil(x[i] - 0.5)), int(math.ceil(x[i + 1] - 0.5))
                img[h - 1 - k, max(a, 0):min(b, w)] = True
    return img


def zhang_suen(img):
    """thinned to a skeleton (both sub-iterations, vectorised)"""
    a = np.pad(img.astype(np.uint8), 1)
    while True:
        changed = False
        for it in (0, 1):
            P2, P3, P4 = a[:-2, 1:-1], a[:-2, 2:], a[1:-1, 2:]
            P5, P6, P7 = a[2:, 2:], a[2:, 1:-1], a[2:, :-2]
            P8, P9 = a[1:-1, :-2], a[:-2, :-2]
            c = a[1:-1, 1:-1]
            nb = [P2, P3, P4, P5, P6, P7, P8, P9]
            B = sum(x.astype(int) for x in nb)
            A = sum(((nb[i] == 0) & (nb[(i + 1) % 8] == 1)).astype(int) for i in range(8))
            if it == 0:
                m = (P2 * P4 * P6 == 0) & (P4 * P6 * P8 == 0)
            else:
                m = (P2 * P4 * P8 == 0) & (P2 * P6 * P8 == 0)
            rm = (c == 1) & (B >= 2) & (B <= 6) & (A == 1) & m
            if rm.any():
                c[rm] = 0
                changed = True
        if not changed:
            return a[1:-1, 1:-1].astype(bool)


NB8 = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]


def neighbours(px, p):
    i, j = p
    return [(i + di, j + dj) for di, dj in NB8 if (i + di, j + dj) in px]


def ring_components(px, p):
    """how many 8-connected groups p's neighbours make without p"""
    nb = set(neighbours(px, p))
    seen, comps = set(), 0
    for q in nb:
        if q in seen:
            continue
        comps += 1
        stack = [q]
        seen.add(q)
        while stack:
            a = stack.pop()
            for b in nb:
                if b not in seen and max(abs(a[0] - b[0]), abs(a[1] - b[1])) == 1:
                    seen.add(b)
                    stack.append(b)
    return comps, len(nb)


def thin_more(px):
    """every pixel the skeleton stays connected without (its neighbours one group, two or more
    of them) removed, one at a time: a one-pixel-wide skeleton, so its degrees are true"""
    changed = True
    while changed:
        changed = False
        for p in sorted(px):
            c, n = ring_components(px, p)
            if n >= 2 and c == 1:
                px.discard(p)
                changed = True
    return px


def graph(px):
    """nodes (ends and junctions, adjacent junction pixels as one) and the paths between them:
    (a, b, [pixels]); a loop without nodes is (None, None, [pixels, first again])"""
    deg = {p: len(neighbours(px, p)) for p in px}
    node_of, nodes = {}, []
    for p in px:
        if deg[p] != 2 and p not in node_of:
            k = len(nodes)
            group, stack = [p], [p]
            node_of[p] = k
            while stack:
                a = stack.pop()
                if deg[a] < 3:
                    continue
                for b in neighbours(px, a):
                    if deg[b] >= 3 and b not in node_of:
                        node_of[b] = k
                        group.append(b)
                        stack.append(b)
            nodes.append(group)
    edges, used = [], set()

    def walk(start, first):
        path, prev, cur = [start, first], start, first
        while cur not in node_of:
            used.add(cur)
            nxt = [q for q in neighbours(px, cur) if q != prev and q not in used or (q in node_of and q != prev)]
            nxt = [q for q in nxt if q != prev]
            if not nxt:
                break
            # prefer a step along a row or column, then a node
            nxt.sort(key=lambda q: (q not in node_of, abs(q[0] - cur[0]) + abs(q[1] - cur[1])))
            prev, cur = cur, nxt[0]
            path.append(cur)
        return path

    for k, group in enumerate(nodes):
        for p in group:
            for q in neighbours(px, p):
                if q in node_of:
                    continue
                if q in used:
                    continue
                path = walk(p, q)
                end = path[-1]
                edges.append((k, node_of.get(end), path))
    # loops: degree-2 pixels never reached
    for p in sorted(px):
        if p in used or p in node_of:
            continue
        nb = neighbours(px, p)
        if not nb:
            continue
        used.add(p)
        path = walk(p, nb[0])
        if path[-1] != p:
            path.append(p)
        edges.append((None, None, path))
    # lone pixels (a dot thinned to one)
    for k, group in enumerate(nodes):
        if all(deg[p] == 0 for p in group):
            edges.append((k, k, list(group)))
    return nodes, edges, node_of


def plen(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def centroid(pts):
    a = np.array(pts, float)
    return tuple(a.mean(axis=0))


def strokes_of(img, width_px):
    """the skeleton's strokes, in pixels (row, column): spurs pruned, branches paired at
    junctions by how straight they run on"""
    px = thin_more({(int(i), int(j)) for i, j in zip(*np.nonzero(zhang_suen(img)))})
    spur = 0.75 * width_px
    for _ in range(8):
        nodes, edges, node_of = graph(px)
        cnt = {}
        for a, b, _p in edges:
            for k in (a, b):
                if k is not None:
                    cnt[k] = cnt.get(k, 0) + 1
        gone = False
        for a, b, path in edges:
            if a is None or a == b:
                continue
            ends = [k for k in (a, b) if cnt.get(k, 0) == 1]
            # a short branch from an end into a junction
            if len(ends) == 1 and cnt.get(a if ends[0] == b else b, 0) >= 3 and plen(path) < spur:
                for q in path:
                    if q not in node_of or node_of[q] == ends[0]:
                        px.discard(q)
                gone = True
        if not gone:
            break
    nodes, edges, node_of = graph(px)
    # a small loop at a junction (two branches thinned round a speck of background) is no stroke
    small_loop = [p for a, b, p in edges if a is not None and a == b and len(p) > 2 and plen(p) < 2.5 * width_px]
    # and so is the shorter of two short branches between the same two junctions (an eye)
    by_pair = {}
    for a, b, p in edges:
        if a is not None and b is not None and a != b:
            by_pair.setdefault((min(a, b), max(a, b)), []).append(p)
    for ps in by_pair.values():
        ps.sort(key=plen)
        if len(ps) >= 2 and plen(ps[1]) < 2.5 * width_px:
            small_loop.append(ps[0])
    if small_loop:
        for p in small_loop:
            for q in p:
                if q not in node_of:
                    px.discard(q)
        px = thin_more(px)
        nodes, edges, node_of = graph(px)
    comps = components(px)
    out = []
    # a whole component that is short: a dot
    small = set()
    for comp in comps:
        ln = max((plen(p) for a, b, p in edges if p[0] in comp), default=0)
        span = max(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a in comp for b in comp) if len(comp) < 400 else 1e9
        if span < 0.9 * width_px or ln < 0.5 * width_px:
            out.append([centroid(sorted(comp))])
            small |= comp
    edges = [e for e in edges if e[2][0] not in small]
    # paths as point lists from node centroid to node centroid
    cen = [centroid(g) for g in nodes]
    paths = []
    for a, b, p in edges:
        pts = [tuple(map(float, q)) for q in p]
        if a is not None:
            pts[0] = cen[a]
        if b is not None:
            pts[-1] = cen[b]
        paths.append([a, b, pts])
    # pair branches at each junction: the straightest continuation first
    lookahead = max(3.0, 1.2 * width_px)

    def direction(pts):
        """the way a path leaves its first point"""
        p0 = np.array(pts[0])
        acc = 0.0
        for q in pts[1:]:
            acc = math.dist(pts[0], q)
            if acc >= lookahead:
                break
        q = np.array(q)
        d = q - p0
        n = math.hypot(*d)
        return d / n if n else d

    ends = {}
    for i, (a, b, pts) in enumerate(paths):
        if a is not None:
            ends.setdefault(a, []).append((i, 0))
        if b is not None:
            ends.setdefault(b, []).append((i, 1))
    link = {}
    for k, es in ends.items():
        if len(es) < 2:
            continue
        dirs = {}
        for i, side in es:
            pts = paths[i][2] if side == 0 else paths[i][2][::-1]
            dirs[(i, side)] = direction(pts)
        cand = []
        for x in range(len(es)):
            for y in range(x + 1, len(es)):
                if es[x][0] == es[y][0] and paths[es[x][0]][0] == paths[es[x][0]][1]:
                    continue
                c = float(np.dot(dirs[es[x]], dirs[es[y]]))     # -1: straight on
                cand.append((c, es[x], es[y]))
        cand.sort()
        taken = set()
        for c, e1, e2 in cand:
            if c > -0.5 or e1 in taken or e2 in taken:
                continue
            link[e1], link[e2] = e2, e1
            taken |= {e1, e2}
    seen = set()
    for i, (a, b, pts) in enumerate(paths):
        if i in seen:
            continue
        # walk back to a free end, then forwards
        cur, side = i, 0
        loop_guard = 0
        while (cur, side) in link and loop_guard < len(paths) + 2:
            j, s2 = link[(cur, side)]
            cur, side = j, 1 - s2
            loop_guard += 1
            if cur == i:
                break
        # now (cur, side) is a free end (or the loop closed): run from it
        chain, c, sd = [], cur, side
        guard = 0
        while c not in seen and guard < len(paths) + 2:
            seen.add(c)
            p = paths[c][2] if sd == 0 else paths[c][2][::-1]
            chain.extend(p if not chain else p[1:])
            nxt = link.get((c, 1 - sd))
            if not nxt:
                break
            c, sd = nxt
            guard += 1
        if len(chain) >= 2:
            out.append(chain)
    return out


def components(px):
    left, comps = set(px), []
    while left:
        p = left.pop()
        comp, stack = {p}, [p]
        while stack:
            a = stack.pop()
            for b in neighbours(left, a):
                left.discard(b)
                comp.add(b)
                stack.append(b)
        comps.append(comp)
    return comps


def smooth(pts, win):
    """a moving average along the path (its ends kept), then simplified (Douglas-Peucker)"""
    if len(pts) < 3:
        return pts
    a = np.array(pts, float)
    closed = math.dist(pts[0], pts[-1]) < 1e-6
    k = max(1, int(win))
    if closed:
        b = a[:-1]
        n = len(b)
        s = np.array([b[[(i + d) % n for d in range(-k, k + 1)]].mean(axis=0) for i in range(n)])
        s = np.vstack([s, s[:1]])
    else:
        s = a.copy()
        for i in range(1, len(a) - 1):
            r = min(k, i, len(a) - 1 - i)
            s[i] = a[i - r:i + r + 1].mean(axis=0)
    return rdp([tuple(p) for p in s], 0.35)


def rdp(pts, tol):
    if len(pts) < 3:
        return pts
    a, b = np.array(pts[0]), np.array(pts[-1])
    d = b - a
    n = math.hypot(*d)
    best, at = -1.0, 0
    for i in range(1, len(pts) - 1):
        p = np.array(pts[i])
        dist = abs(d[0] * (a[1] - p[1]) - d[1] * (a[0] - p[0])) / n if n else math.dist(pts[i], pts[0])
        if dist > best:
            best, at = dist, i
    if best <= tol:
        return [pts[0], pts[-1]]
    return rdp(pts[:at + 1], tol)[:-1] + rdp(pts[at:], tol)


def blot(img, p):
    """the filled part of img connected to pixel p (4-connected)"""
    h, w = img.shape
    i, j = int(round(p[0])), int(round(p[1]))
    out = np.zeros_like(img)
    if not (0 <= i < h and 0 <= j < w and img[i, j]):
        return out
    stack = [(i, j)]
    out[i, j] = True
    while stack:
        a, b = stack.pop()
        for c, d in ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)):
            if 0 <= c < h and 0 <= d < w and img[c, d] and not out[c, d]:
                out[c, d] = True
                stack.append((c, d))
    return out


def draw_strokes(strokes, r, w, h):
    """strokes (pixel row, column) drawn at radius r"""
    ii, jj = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w), bool)
    for st in strokes:
        pts = np.array(st, float)
        if len(pts) == 1:
            img |= (ii - pts[0][0]) ** 2 + (jj - pts[0][1]) ** 2 <= r * r
            continue
        for p, q in zip(pts, pts[1:]):
            d = q - p
            L2 = float(d @ d) or 1e-9
            t = np.clip(((ii - p[0]) * d[0] + (jj - p[1]) * d[1]) / L2, 0, 1)
            img |= (ii - p[0] - t * d[0]) ** 2 + (jj - p[1] - t * d[1]) ** 2 <= r * r
    return img


def orient(st):
    """from its top (a mostly level stroke: its left); a loop from its top, anticlockwise"""
    if len(st) < 2:
        return st
    closed = math.dist(st[0], st[-1]) < 1e-6
    if closed:
        ring = st[:-1]
        top = max(range(len(ring)), key=lambda i: (ring[i][1], -ring[i][0]))
        ring = ring[top:] + ring[:top]
        area = sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
                   for i in range(len(ring)))
        if area < 0:
            ring = [ring[0]] + ring[1:][::-1]
        return ring + [ring[0]]
    (x0, y0), (x1, y1) = st[0], st[-1]
    if abs(x1 - x0) > 2 * abs(y1 - y0):
        return st if x0 <= x1 else st[::-1]
    return st if y0 >= y1 else st[::-1]


def font_slant(glyphs):
    """tan of the lean of the long, mostly upright straight runs of the stems (x per y)"""
    num = den = 0.0
    for c in "lidhbkftjILTHEFNMKP14":
        for st in glyphs.get(c, []):
            for p, q in zip(st, st[1:]):
                dy = q[1] - p[1]
                dx = q[0] - p[0]
                if abs(dy) > 0.25 and abs(dx) < 0.5 * abs(dy):
                    num += dx * dy * abs(dy)
                    den += dy * dy * abs(dy)
    return num / den if den else 0.0


BANDS, Y0, Y1 = 8, -0.6, 1.6


def profile(strokes):
    """the ink's left and right reach per height band (letters.h hw_profile, sampled)"""
    l, r = [math.inf] * BANDS, [-math.inf] * BANDS
    bw = (Y1 - Y0) / BANDS
    for st in strokes:
        pts = st if len(st) > 1 else st * 2
        for p, q in zip(pts, pts[1:]):
            n = max(1, int(math.dist(p, q) / 0.01))
            for k in range(n + 1):
                x, y = p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n
                b = min(BANDS - 1, max(0, int((y - Y0) / bw)))
                l[b], r[b] = min(l[b], x), max(r[b], x)
    return l, r


def gap(pa, pb, shift):
    best = math.inf
    for k in range(BANDS):
        if pa[1][k] > -math.inf and pb[0][k] < math.inf:
            best = min(best, pb[0][k] + shift - pa[1][k])
    if best == math.inf:
        best = shift + min(pb[0]) - max(pa[1])
    return best


def main(argv=None):
    ap = argparse.ArgumentParser(prog="base-font.py")
    ap.add_argument("font")
    ap.add_argument("out")
    ap.add_argument("--chars")
    ap.add_argument("--keep-slant", action="store_true")
    ap.add_argument("--min-match", type=float, default=0.8)
    ap.add_argument("--sheet")
    a = ap.parse_args(argv)
    raw = open(a.font, "rb").read()
    f = TTFont(a.font)
    gs = f.getGlyphSet()
    cmap = f.getBestCmap()
    xh_u = f["OS/2"].sxHeight or 0.5 * f["head"].unitsPerEm
    s = PX_PER_XH / xh_u
    hm = f["hmtx"]
    chars = sorted(chr(c) for c in cmap if c > 0x20 and chr(c).isprintable() and not chr(c).isspace())
    if a.chars:
        chars = [c for c in chars if c in a.chars]
    raw_strokes, fails, widths, info = {}, [], [], {}
    for c in chars:
        g = gs[cmap[ord(c)]]
        pen = FlatPen(gs)
        g.draw(pen)
        if not pen.contours:
            continue
        pts = np.concatenate([np.array(k, float) for k in pen.contours])
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        ox, oy = PAD / s - x0, PAD / s - y0
        w, h = int((x1 - x0) * s) + 2 * PAD + 1, int((y1 - y0) * s) + 2 * PAD + 1
        img = fill(pen.contours, s, ox, oy, w, h)
        if not img.any():
            continue
        info[c] = (img, ox, oy, w, h)
        raw_strokes[c] = None
    # the pen's width: the ink's area over the length of its skeleton, over all glyphs
    area = sum(int(v[0].sum()) for v in info.values())
    length = 0.0
    for c, (img, ox, oy, w, h) in info.items():
        length += float(zhang_suen(img).sum())
    width_px = area / length if length else 10.0
    for c, (img, ox, oy, w, h) in info.items():
        st = strokes_of(img, width_px)
        st = [smooth(p, width_px / 4) for p in st]
        if not st:                      # a blot that thins to nothing: a dot at its middle
            ii, jj = np.nonzero(img)
            st = [[(float(ii.mean()), float(jj.mean()))]]
        again = draw_strokes(st, width_px / 2, w, h)
        # a dot is drawn at the pen's width however large the font's is: its blot isn't compared
        keep = np.ones_like(img)
        for t in st:
            if len(t) == 1:
                keep &= ~blot(img, t[0])
        match = (img & again & keep).sum() / max(1, ((img | again) & keep).sum()) if keep.any() else 1.0
        if not (img & keep).any():
            match = 1.0
        if match < a.min_match:
            fails.append((c, match))
        widths.append(match)
        # to font units, y up
        raw_strokes[c] = [[((j + 0.5) / s - ox, (h - i - 0.5) / s - oy) for i, j in p] for p in st]
    half = width_px / 2 / s

    def med(vals, default):
        v = sorted(vals)
        return v[len(v) // 2] if v else default
    base0 = med([min(y for st in raw_strokes[c] for _, y in st) for c in "xzvwnmur" if c in raw_strokes], half)
    top = med([max(y for st in raw_strokes[c] for _, y in st) for c in "xzvwnmur" if c in raw_strokes], xh_u - half)
    xc = top - base0
    glyphs = {}
    for c, st in raw_strokes.items():
        if st is None:
            continue
        glyphs[c] = [[((x / xc), (y - base0) / xc) for x, y in p] for p in st]
    measured = font_slant(glyphs)
    slant = measured if a.keep_slant else 0.0
    out = {}
    lefts = {}
    for c, st in glyphs.items():
        if not a.keep_slant:
            st = [[(x - measured * y, y) for x, y in p] for p in st]
        st = [orient(p) for p in st]
        # dots and marks (short strokes) last, the rest by where they start
        lens = [sum(math.dist(p, q) for p, q in zip(t, t[1:])) for t in st]
        order = sorted(range(len(st)), key=lambda i: (lens[i] < 0.3, round(min(x for x, _ in st[i]), 2),
                                                      -st[i][0][1]))
        st = [st[i] for i in order]
        left = min(x for t in st for x, _ in t)
        lefts[c] = left
        out[c] = [[[round(x - left, 4), round(y, 4)] for x, y in t] for t in st]

    def cap_of(cs, fn, default):
        return med([fn(y for t in out[c] for _, y in t) for c in cs if c in out], default)
    cap = cap_of("EFHIKLTZ", max, 1.7)
    asc = cap_of("bdhkl", max, 1.8)
    desc = cap_of("gjpqy", min, -0.7)
    # the spacing the font sets its letters at, between the centre lines: per pair of
    # neighbouring small letters the narrowest gap of their profiles at the font's advances
    # (its kerning left out), words box to box across its space
    adv = {c: hm[cmap[ord(c)]][0] / xc for c in out}
    lsb = {}
    for c in out:
        # the glyph's own left edge from its origin, after the slant came out
        st = glyphs[c]
        if not a.keep_slant:
            st = [[(x - measured * y, y) for x, y in p] for p in st]
        lsb[c] = min(x for t in st for x, _ in t)
    prof = {c: profile(out[c]) for c in out}
    small = [c for c in "abcdefghijklmnopqrstuvwxyz" if c in out]
    gaps = [gap(prof[x], prof[y], adv[x] - lsb[x] + lsb[y]) for x in small for y in small]
    letter = med(gaps, 0.35)
    sp = cmap.get(32)
    space = hm[sp][0] / xc if sp else 0.6
    words = [adv[x] - lsb[x] - max(r for r in prof[x][1] if r > -math.inf) + space + lsb[y] +
             min(v for v in prof[y][0] if v < math.inf) for x in small for y in small]
    word = med(words, 1.0)
    name = f["name"]
    head = {"format": "glassine-handwriting-base", "version": 1,
            "family": name.getDebugName(1) or "", "copyright": name.getDebugName(0) or "",
            "licence": "OFL-1.1", "licence_url": name.getDebugName(14) or "https://openfontlicense.org",
            "source": os.path.basename(a.font), "sha256": hashlib.sha256(raw).hexdigest(),
            "slant": round(slant, 4), "measured_slant": round(measured, 4), "cap_height": round(cap, 4),
            "ascender": round(asc, 4), "descender": round(desc, 4), "ink_width": round(2 * half / xc, 4),
            "letter_spacing": round(letter, 4), "word_spacing": round(word, 4)}
    with open(a.out, "w", encoding="utf-8") as fo:
        fo.write(json.dumps(head, ensure_ascii=False) + "\n")
        for c in sorted(out):
            fo.write(json.dumps({"char": c, "strokes": out[c]}, ensure_ascii=False) + "\n")
    worst = min(widths) if widths else 0
    print("base-font: %d characters, pen width %.3f x-heights, slant %.3f (%s), match worst %.3f, median %.3f"
          % (len(out), head["ink_width"], measured, "kept" if a.keep_slant else "taken out", worst,
             sorted(widths)[len(widths) // 2] if widths else 0), file=sys.stderr)
    if a.sheet:
        contact_sheet(a.sheet, info, raw_strokes, width_px, s)
    if fails:
        print("base-font: below the match of %.2f: %s" % (a.min_match, " ".join("%s %.3f" % x for x in fails)),
              file=sys.stderr)
        return 1
    return 0


def contact_sheet(path, info, strokes, width_px, s):
    import cairo
    cs = [c for c in sorted(info) if strokes.get(c)]
    cell = 220
    cols = 12
    rows = (len(cs) + cols - 1) // cols
    sur = cairo.ImageSurface(cairo.FORMAT_RGB24, cols * cell, rows * cell)
    cr = cairo.Context(sur)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    for n, c in enumerate(cs):
        img, ox, oy, w, h = info[c]
        k = min((cell - 10) / w, (cell - 10) / h)
        bx, by = (n % cols) * cell + 5, (n // cols) * cell + 5
        ii, jj = np.nonzero(img)
        cr.set_source_rgb(0.8, 0.8, 0.8)
        for i, j in zip(ii, jj):
            cr.rectangle(bx + j * k, by + i * k, k + 0.3, k + 0.3)
        cr.fill()
        for t in strokes[c]:
            pts = [((x + ox) * s * k + bx, (h - (y + oy) * s) * k + by) for x, y in t]
            cr.set_source_rgb(0.8, 0, 0)
            cr.set_line_width(1.5)
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            if len(pts) == 1:
                cr.arc(pts[0][0], pts[0][1], 3, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgb(0, 0, 0.9)
            cr.arc(pts[0][0], pts[0][1], 3, 0, 2 * math.pi)
            cr.fill()
    sur.write_to_png(path)


if __name__ == "__main__":
    sys.exit(main())
