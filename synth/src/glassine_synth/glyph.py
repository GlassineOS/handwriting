"""Turn a font glyph into pen strokes: render, skeletonise, walk the skeleton graph.

Coordinates are in em units, x to the right, y down, origin at the glyph's left baseline.
"""

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.morphology import skeletonize
from scipy import ndimage

EM = 256  # render size in pixels per em
SPUR = 0.05  # spurs shorter than this (em) are pruned
NEIGH = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class Glyph:
    char: str
    advance: float  # em
    strokes: list[np.ndarray] = field(default_factory=list)  # each (n, 2), em units
    delayable: list[bool] = field(default_factory=list)  # dot or cross, may move to word end


def _render(font: ImageFont.FreeTypeFont, char: str) -> tuple[np.ndarray, float, float]:
    pad = EM
    img = Image.new("L", (3 * EM + 2 * pad, 2 * EM + 2 * pad), 0)
    base = (pad, pad + int(1.2 * EM))
    ImageDraw.Draw(img).text(base, char, fill=255, font=font, anchor="ls")
    return np.asarray(img) > 127, base[0], base[1]


def _graph(sk: np.ndarray):
    """Split a skeleton into nodes (end points, junction clusters) and edges (pixel paths)."""
    ys, xs = np.nonzero(sk)
    pix = set(zip(ys.tolist(), xs.tolist()))
    nb = {p: [(p[0] + dy, p[1] + dx) for dy, dx in NEIGH if (p[0] + dy, p[1] + dx) in pix]
          for p in pix}
    deg = {p: len(n) for p, n in nb.items()}
    junc = np.zeros_like(sk)
    for p, d in deg.items():
        if d >= 3:
            junc[p] = True
    lab, _ = ndimage.label(junc, structure=np.ones((3, 3)))
    node_of = {}
    for p, d in deg.items():
        if d >= 3:
            node_of[p] = ("j", int(lab[p]))
        elif d <= 1:
            node_of[p] = ("e", p)
    edges, seen = [], set()

    def walk(start, nxt):
        path = [start, nxt]
        prev, cur = start, nxt
        while cur not in node_of:
            step = [q for q in nb[cur] if q != prev and (cur, q) not in seen and q not in path[-3:]]
            if not step:
                break
            seen.add((cur, step[0]))
            seen.add((step[0], cur))
            prev, cur = cur, step[0]
            path.append(cur)
        return path

    for p, n in node_of.items():
        for q in nb[p]:
            if (p, q) in seen or (q in node_of and node_of[q] == n):
                continue
            seen.add((p, q))
            seen.add((q, p))
            path = walk(p, q)
            end = node_of.get(path[-1], ("e", path[-1]))
            edges.append([n, end, path])
    # pure loops (o, 0) have no nodes: start at the top pixel
    left = {p for p in pix if p not in node_of and not any((p, q) in seen for q in nb[p])}
    while left:
        top = min(left, key=lambda p: (p[0], p[1]))
        n = ("e", top)
        node_of[top] = n
        q = nb[top][0]
        seen.add((top, q))
        seen.add((q, top))
        path = walk(top, q)
        path.append(top)
        edges.append([n, n, path])
        left -= set(path)
    return edges


def _prune(edges, spur_px: float):
    while True:
        deg = {}
        for a, b, _ in edges:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        drop = [e for e in edges if e[0] != e[1] and len(e[2]) < spur_px
                and ((deg[e[0]] == 1 and deg[e[1]] >= 3) or (deg[e[1]] == 1 and deg[e[0]] >= 3))]
        if not drop:
            return edges
        edges = [e for e in edges if e not in drop]


def _dir(path, n=8):
    a, b = np.array(path[0], float), np.array(path[min(n, len(path) - 1)], float)
    v = b - a
    return v / (np.linalg.norm(v) + 1e-9)


def _walk_strokes(edges):
    """Walk edges into strokes: start top-left, continue on the straightest edge."""
    unused = list(range(len(edges)))
    strokes = []

    def ends(i):
        return edges[i][0], edges[i][1]

    def deg(node):
        return sum((edges[i][0] == node) + (edges[i][1] == node) for i in unused)

    def pos(node):
        for a, b, p in edges:
            if a == node:
                return p[0]
            if b == node:
                return p[-1]

    while unused:
        nodes = {n for i in unused for n in ends(i)}
        odd = [n for n in nodes if deg(n) % 2 == 1] or list(nodes)
        start = min(odd, key=lambda n: pos(n)[1] + pos(n)[0])  # (y, x): x + y, top-left
        node, path, heading = start, [], None
        while True:
            cand = []
            for i in unused:
                a, b, p = edges[i]
                if a == node:
                    cand.append((i, p))
                elif b == node:
                    cand.append((i, p[::-1]))
            if not cand:
                break
            if heading is None:
                i, p = max(cand, key=lambda c: len(c[1]))
            else:
                i, p = max(cand, key=lambda c: float(np.dot(heading, _dir(c[1]))))
            unused.remove(i)
            path.extend(p if not path else p[1:])
            heading = -_dir(p[::-1])
            a, b, _ = edges[i]
            node = b if (a == node and a != b) else a if b == node else b
        if len(path) > 1:
            strokes.append(path)
    return strokes


def _orient(stroke: np.ndarray) -> np.ndarray:
    """Closed strokes start at the top and run counter-clockwise (as seen on screen)."""
    if np.linalg.norm(stroke[0] - stroke[-1]) < 0.03:
        k = int(np.argmin(stroke[:, 1]))
        stroke = np.concatenate([stroke[k:], stroke[1:k + 1]])
        x, y = stroke[:, 0], stroke[:, 1]
        area = np.sum(x[:-1] * y[1:] - x[1:] * y[:-1])
        if area > 0:  # y down: positive area = clockwise on screen
            stroke = stroke[::-1]
    return stroke


def _smooth(s: np.ndarray, step: float = 0.01) -> np.ndarray:
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(s, axis=0), axis=1))]
    if d[-1] < step:
        return s[[0, -1]] if len(s) > 1 else s
    t = np.linspace(0, d[-1], max(2, int(d[-1] / step) + 1))
    r = np.c_[np.interp(t, d, s[:, 0]), np.interp(t, d, s[:, 1])]
    if len(r) > 4:
        k = np.array([1, 2, 3, 2, 1], float) / 9
        inner = np.c_[np.convolve(r[:, 0], k, "same"), np.convolve(r[:, 1], k, "same")]
        r[2:-2] = inner[2:-2]
    return r


def glyph(font: ImageFont.FreeTypeFont, char: str) -> Glyph:
    adv = font.getlength(char) / EM
    g = Glyph(char, adv)
    if char.isspace():
        return g
    mask, ox, oy = _render(font, char)
    if not mask.any():
        return g
    sk = skeletonize(mask)
    edges = _prune(_graph(sk), SPUR * EM)
    for path in _walk_strokes(edges):
        pts = np.array([(x - ox, y - oy) for y, x in path], float) / EM
        s = _orient(_smooth(pts))
        g.strokes.append(s)
    g.strokes.sort(key=lambda s: (round(float(s[:, 0].min()) * 10), float(s[:, 1].min())))
    for s in g.strokes:
        w, h = np.ptp(s[:, 0]), np.ptp(s[:, 1])
        dot = char in "ij" and max(w, h) < 0.15 and s[:, 1].max() < -0.4
        cross = char in "tf" and w > 2.5 * h and w > 0.15
        g.delayable.append(bool(dot or cross))
    return g


def load_font(path, size: int = EM) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)
