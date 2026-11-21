"""Joined ink from connected school fonts, for the point aligner (alignrun `--joined`).

The generator's fonts lay letters out one by one; even its script fonts come out as many pen
lifts per word, and the pen-down joins it makes are bridges drawn between glyph strokes
(gen.connector). A connected school font (the Playwrite family, SIL Open Font License; the
manifest `synth/joined.toml`) draws each letter with its entry and exit strokes, and its
contextual alternates (OpenType `calt`) pick the form that meets the letter before it at the
right height. Here a word is shaped with HarfBuzz (`calt` on), each shaped glyph's centre
line is walked as one pen path from its entry to its exit (stems written down and up again
are retraced, as a pen does), and the letters of a word are written in one stroke where the
font joins them. Dots, bars and other parts apart from a letter's body are strokes of their
own, written after their letter or at the word's end.

Each point keeps its letter (the glyph it comes from, by HarfBuzz's cluster); a stretch
either side of where two letters meet (`SEAM` x-heights along the stroke) is the join
(`-1`, as gen.Generator.line labels a bridge), so a cut anywhere in the connector counts.

Writers: shape variation stays continuous along the stroke (every change a smooth function
of the place on the page, applied to the whole word), so joins stay joined: slant, letter
width, ascender and descender scale, a smooth displacement field with bumps in the letters
(their loops and bows change), a bend, baseline drift, tilt, the timing of gen.Generator.
`Writer` is drawn once per synthetic writer (`draw_writer`), its line-to-line spread around
it per line.

Synthetic ink only: fonts under the OFL, text from lines.Generator.
"""

import io
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

XH_EM = 0.45  # x-height after loading (em), as glassine_synth.ink.XH
EM = 200  # raster: pixels per em
SEAM = 0.2  # x-heights along the stroke either side of a join point: the join
JOIN_GAP = 0.06  # em: an exit end and the next entry start closer than this are joined
DOT_LEN = 0.12  # em: a component this short is a dot


# ---------------------------------------------------------------------------- outlines

class _FlatPen:
    """fontTools pen: the outline as polygons (font units, y up), curves flattened."""

    def __init__(self, glyphset, step: float = 8.0):
        from fontTools.pens.basePen import BasePen

        outer = self

        class P(BasePen):
            def _moveTo(self, p):
                outer.cur = [p]

            def _lineTo(self, p):
                outer.cur.append(p)

            def _curveToOne(self, a, b, c):
                p0 = outer.cur[-1]
                n = max(2, int((math.dist(p0, a) + math.dist(a, b) + math.dist(b, c)) / step))
                for i in range(1, n + 1):
                    t = i / n
                    u = 1 - t
                    outer.cur.append((u ** 3 * p0[0] + 3 * u * u * t * a[0] + 3 * u * t * t * b[0] + t ** 3 * c[0],
                                      u ** 3 * p0[1] + 3 * u * u * t * a[1] + 3 * u * t * t * b[1] + t ** 3 * c[1]))

            def _qCurveToOne(self, a, b):
                p0 = outer.cur[-1]
                n = max(2, int((math.dist(p0, a) + math.dist(a, b)) / step))
                for i in range(1, n + 1):
                    t = i / n
                    u = 1 - t
                    outer.cur.append((u * u * p0[0] + 2 * u * t * a[0] + t * t * b[0],
                                      u * u * p0[1] + 2 * u * t * a[1] + t * t * b[1]))

            def _closePath(self):
                if outer.cur and len(outer.cur) > 2:
                    outer.contours.append(outer.cur)
                outer.cur = None

            _endPath = _closePath

        self.contours, self.cur = [], None
        self.pen = P(glyphset)


def _fill(contours, scale: float, ox: float, oy: float, w: int, h: int) -> np.ndarray:
    """Non-zero winding fill of the contours (font units, y up) on a w x h raster (y down)."""
    from skimage.draw import polygon
    acc = np.zeros((h, w), np.int16)
    for c in contours:
        a = np.asarray(c, float)
        x = (a[:, 0] + ox) * scale
        y = h - (a[:, 1] + oy) * scale
        area = float(np.sum(a[:-1, 0] * a[1:, 1] - a[1:, 0] * a[:-1, 1]))
        rr, cc = polygon(y, x, (h, w))
        acc[rr, cc] += 1 if area > 0 else -1
    return acc != 0


# ---------------------------------------------------------------------------- pen path

def _components(edges):
    """Edge indices grouped by connected nodes."""
    parent = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b, _ in edges:
        parent[find(a)] = find(b)
    groups = {}
    for i, (a, b, _) in enumerate(edges):
        groups.setdefault(find(a), []).append(i)
    return list(groups.values())


def _pos(edges, node):
    for a, b, p in edges:
        if a == node:
            return np.array(p[0], float)
        if b == node:
            return np.array(p[-1], float)


def _plen(p):
    a = np.asarray(p, float)
    return float(np.linalg.norm(np.diff(a, axis=0), axis=1).sum()) if len(a) > 1 else 0.0


def _shortest(edges, idx, s, t):
    """Edge indices of a shortest path from node s to t among edges idx (Dijkstra)."""
    import heapq
    adj = {}
    for i in idx:
        a, b, p = edges[i]
        L = _plen(p)
        adj.setdefault(a, []).append((b, i, L))
        adj.setdefault(b, []).append((a, i, L))
    dist, prev, pq = {s: 0.0}, {}, [(0.0, 0, s)]
    k = 1
    while pq:
        d, _, u = heapq.heappop(pq)
        if u == t:
            break
        if d > dist.get(u, math.inf):
            continue
        for v, i, L in adj.get(u, []):
            if d + L < dist.get(v, math.inf):
                dist[v], prev[v] = d + L, (u, i)
                heapq.heappush(pq, (d + L, k, v))
                k += 1
    if t not in dist:
        return None, math.inf
    out, u = [], t
    while u != s:
        u, i = prev[u]
        out.append(i)
    return out, dist[t]


def _direction(path, n=6):
    a = np.asarray(path, float)
    v = a[min(n, len(a) - 1)] - a[0]
    return v / (np.linalg.norm(v) + 1e-9)


def euler_walk(edges, idx, s, t):
    """One pen path over every edge of a connected skeleton (edges idx), from node s to node
    t: edges are doubled along shortest paths until only s and t are odd (a stem written
    down and up again is retraced), then walked (Fleury) taking at every node the edge that
    runs on straightest. Returns the path's pixels (row, col)."""
    use = list(idx)
    deg = {}
    for i in use:
        a, b, _ = edges[i]
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    want = {n: 0 for n in deg}
    if s != t:
        want[s] = want[t] = 1
    bad = [n for n in deg if deg[n] % 2 != want[n]]
    # pair the nodes of wrong parity greedily by path length, doubling those paths
    while len(bad) >= 2:
        best = None
        for i in range(len(bad)):
            for j in range(i + 1, len(bad)):
                path, d = _shortest(edges, idx, bad[i], bad[j])
                if path is not None and (best is None or d < best[0]):
                    best = (d, i, j, path)
        if best is None:
            break
        _, i, j, path = best
        use += path
        bad = [n for k, n in enumerate(bad) if k not in (i, j)]
    # Fleury: never take a bridge while another edge is left
    left = list(range(len(use)))
    node, out, heading = s, [], None

    def ends(k):
        return edges[use[k]][0], edges[use[k]][1]

    def reachable(start, avoid):
        seen, stack = {start}, [start]
        while stack:
            u = stack.pop()
            for k in left:
                if k == avoid:
                    continue
                a, b = ends(k)
                for x, y in ((a, b), (b, a)):
                    if x == u and y not in seen:
                        seen.add(y)
                        stack.append(y)
        return seen

    while left:
        cand = []
        for k in left:
            a, b = ends(k)
            p = edges[use[k]][2]
            if a == node:
                cand.append((k, p, b))
            elif b == node:
                cand.append((k, p[::-1], a))
        if not cand:
            break
        if len(cand) > 1:
            n_before = len(reachable(node, None))
            ok = [c for c in cand if len(reachable(c[2], c[0])) == n_before]
            cand = ok or cand
        if heading is None:
            k, p, nxt = max(cand, key=lambda c: len(c[1]))
        else:
            k, p, nxt = max(cand, key=lambda c: float(np.dot(heading, _direction(c[1]))))
        left.remove(k)
        out.extend(p if not out else p[1:])
        heading = -_direction(p[::-1])
        node = nxt
    return out


@dataclass
class JGlyph:
    """A shaped glyph's pen strokes (em, y down, origin at its pen position on the baseline):
    its body (the largest part) as a skeleton graph walked between two of its free ends
    (`walk`; `body` the walk from its leftmost end to its rightmost), `rest` the parts apart
    from it (dots, bars, accents) as strokes."""
    body: np.ndarray | None
    rest: list[np.ndarray] = field(default_factory=list)
    graph: tuple | None = None  # (edges, edge indices, node -> position (em), free ends)
    to_em: object = None
    walks: dict = field(default_factory=dict)

    def ends(self) -> list:
        return list(self.graph[3]) if self.graph else []

    def end_pos(self, n) -> np.ndarray:
        return self.graph[2][n]

    def walk(self, s, t) -> np.ndarray:
        """The body from free end s to free end t (None: the default ends)."""
        if self.graph is None or s is None or t is None:
            return self.body
        k = (s, t)
        if k not in self.walks:
            edges, idx, _, _ = self.graph
            path = euler_walk(edges, idx, s, t)
            self.walks[k] = self.to_em(path) if len(path) > 1 else self.body
        return self.walks[k]


def glyph_strokes(mask: np.ndarray, ox_px: float, base_px: float, scale_em: float) -> JGlyph:
    """Strokes of one glyph's filled raster: skeleton, graph (glassine_synth.glyph), the
    largest part kept as a graph to walk between the ends its neighbours meet."""
    from glassine_synth.glyph import SPUR, _graph, _prune, _smooth
    from skimage.morphology import skeletonize
    if not mask.any():
        return JGlyph(None)
    sk = skeletonize(mask)
    edges = _prune(_graph(sk), SPUR * EM)
    if not edges:
        return JGlyph(None)
    comps = _components(edges)

    def px_em(p):
        return np.array([(p[1] - ox_px) / EM, (p[0] - base_px) / EM], float) * scale_em

    def to_em(path):
        return _smooth(np.array([px_em(p) for p in path], float))

    def comp_len(c):
        return sum(_plen(edges[i][2]) for i in c)
    comps.sort(key=comp_len, reverse=True)
    out = JGlyph(None, to_em=to_em)
    for ci, c in enumerate(comps):
        nodes = {n for i in c for n in edges[i][:2]}
        deg = {n: sum((edges[i][0] == n) + (edges[i][1] == n) for i in c) for n in nodes}
        endsn = [n for n in nodes if deg[n] == 1]
        pos = {n: _pos(edges, n) for n in nodes}
        if ci == 0 and len(endsn) >= 2:
            s = min(endsn, key=lambda n: pos[n][1])  # leftmost end
            t = max((n for n in endsn if n != s), key=lambda n: pos[n][1])  # rightmost
            out.graph = (edges, c, {n: px_em(pos[n]) for n in endsn}, endsn)
        else:
            odd = [n for n in nodes if deg[n] % 2] or list(nodes)
            s = min(odd, key=lambda n: pos[n][0] + pos[n][1])  # top-left
            rest = [n for n in odd if n != s]
            t = max(rest, key=lambda n: float(np.linalg.norm(pos[n] - pos[s]))) if rest else s
        path = euler_walk(edges, c, s, t)
        if len(path) < 2:
            continue
        if ci == 0:
            out.body = to_em(path)
        else:
            out.rest.append(to_em(path))
    return out


class JoinedFont:
    """A connected font at one weight: shaping (HarfBuzz, `calt` on) and each glyph's strokes
    (cached by glyph id), scaled to the x-height XH_EM."""

    def __init__(self, path: Path, wght: float | None = None):
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont
        f = TTFont(str(path))
        if "fvar" in f and wght is not None:
            from fontTools.varLib.instancer import instantiateVariableFont
            f = instantiateVariableFont(f, {"wght": wght})
        buf = io.BytesIO()
        f.save(buf)
        self.tt = TTFont(io.BytesIO(buf.getvalue()))
        self.order = self.tt.getGlyphOrder()
        self.gs = self.tt.getGlyphSet()
        self.upem = self.tt["head"].unitsPerEm
        xh = self.tt["OS/2"].sxHeight or 0.5 * self.upem
        self.k = XH_EM * self.upem / xh  # font units -> em at the common x-height: u / upem * k
        self.cmap = self.tt.getBestCmap()
        self.hb = hb.Font(hb.Face(hb.Blob(buf.getvalue())))
        self.cache: dict[int, JGlyph] = {}
        # the joins' gap: an exit end and the next entry start sit about a pen width apart
        # (each skeleton ends half a width inside its outline); JOIN_GAP is for a width of 0.1 em
        self.width_scale = max(1.0, self.pen_width() / 0.1 * 1.5)

    def is_connector(self, gid: int) -> bool:
        """A glyph the font puts between two letters to join them (Playwrite: cnct.*)."""
        return self.order[gid].startswith("cnct")

    def pen_width(self) -> float:
        """The pen's width (em): the ink's area over its skeleton's length, letters l and o."""
        from skimage.morphology import skeletonize
        area = length = 0.0
        for ch in "lo":
            gid = self.order.index(self.cmap[ord(ch)])
            m = self._mask(gid)
            if m is not None:
                area += m.sum()
                length += skeletonize(m).sum()
        return float(area / max(length, 1) / EM * self.k)

    def covers(self, text: str) -> bool:
        return all(c == " " or ord(c) in self.cmap for c in text)

    def shape(self, word: str) -> list[tuple[int, int, float, float]]:
        """(glyph id, character index, x, y) per glyph, em at the common x-height."""
        import uharfbuzz as hb
        buf = hb.Buffer()
        buf.add_str(word)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"calt": True, "kern": True, "liga": False})
        x = 0.0
        out = []
        s = self.k / self.upem
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            out.append((info.codepoint, info.cluster, (x + pos.x_offset) * s, -pos.y_offset * s))
            x += pos.x_advance
        return out

    def glyph(self, gid: int) -> JGlyph:
        g = self.cache.get(gid)
        if g is None:
            g = self._make(gid)
            self.cache[gid] = g
        return g

    def _mask(self, gid: int):
        m = self._raster(gid)
        return None if m is None else m[0]

    def _raster(self, gid: int):
        pen = _FlatPen(self.gs)
        self.gs[self.order[gid]].draw(pen.pen)
        if not pen.contours:
            return None
        pts = np.concatenate([np.asarray(c, float) for c in pen.contours])
        (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
        scale = EM / self.upem
        pad = 4
        ox, oy = pad / scale - x0, pad / scale - y0
        w, h = int((x1 - x0) * scale) + 2 * pad + 1, int((y1 - y0) * scale) + 2 * pad + 1
        return _fill(pen.contours, scale, ox, oy, w, h), ox * scale, h - oy * scale

    def _make(self, gid: int) -> JGlyph:
        r = self._raster(gid)
        if r is None:
            return JGlyph(None)
        # pixel (row, col) -> em: x = (col - ox_px) / EM, y = (row - base_px) / EM (y down)
        mask, ox_px, base_px = r
        return glyph_strokes(mask, ox_px, base_px, self.k)


# ---------------------------------------------------------------------------- writers

@dataclass
class Writer:
    """A synthetic writer of a joined font: their slant, proportions, shape habits and timing."""
    font: str
    slant: float = 0.1  # shear, positive leans right
    width: float = 1.0  # letter width scale
    asc: float = 1.0  # ascender / capital scale
    desc: float = 1.0
    warp: float = 0.02  # sd of a bump of the displacement field (em)
    bend: float = 0.01  # amplitude of the smooth bend (em)
    drift: float = 0.03  # baseline wander (em)
    word: float = 1.35  # word gap, box to box (x-heights)
    dot_delay: float = 0.8  # chance a dot or bar waits for the word's end
    rtl: float = 0.7  # chance a word's delayed strokes go right to left
    lift: float = 0.04  # chance a join is a pen lift after all (the letters written apart there)
    xh_line: float = 0.14
    timing: object = None


def draw_writer(rng, font: str, broaden: dict | None = None) -> Writer:
    from .gen import XH_LINE
    from .timing import BROADEN
    from .timing import draw_writer as draw_timing
    a, sd, lo, hi = XH_LINE
    return Writer(font=font, slant=float(rng.normal(0.1, 0.12)), width=float(rng.lognormal(0, 0.12)),
                  asc=float(rng.lognormal(0.15, 0.15)), desc=float(rng.lognormal(0.05, 0.15)),
                  warp=float(rng.uniform(0.01, 0.035)), bend=float(rng.uniform(0.0, 0.02)),
                  drift=float(rng.uniform(0.0, 0.05)), word=float(rng.lognormal(np.log(1.35), 0.2)),
                  dot_delay=float(rng.beta(6, 2)), rtl=float(rng.beta(4, 1.5)), lift=float(rng.uniform(0.0, 0.12)),
                  xh_line=float(np.clip(rng.lognormal(np.log(a), sd), lo, hi)),
                  timing=draw_timing(rng, BROADEN if broaden is None else broaden))


def _around(w: Writer, rng) -> Writer:
    """The writer on one line: a little spread around their habits."""
    return replace(w, slant=float(w.slant + rng.normal(0, 0.03)), width=float(w.width * rng.lognormal(0, 0.03)),
                   asc=float(w.asc * rng.lognormal(0, 0.04)), desc=float(w.desc * rng.lognormal(0, 0.04)))


# ---------------------------------------------------------------------------- lines

def _field(rng, boxes, amp):
    """A smooth displacement field: a few Gaussian bumps inside each box (x0, x1, y0, y1)."""
    cs, rs, vs = [], [], []
    for x0, x1, y0, y1 in boxes:
        k = int(rng.integers(1, 4))
        cs.append(np.c_[rng.uniform(x0, x1, k), rng.uniform(y0, y1, k)])
        rs.append(rng.uniform(0.08, 0.22, k))
        vs.append(rng.normal(0, amp, (k, 2)))
    if not cs:
        return lambda q: q
    c, r, v = np.concatenate(cs), np.concatenate(rs), np.concatenate(vs)

    def f(q):
        w = np.exp(-((q[:, None, :] - c[None]) ** 2).sum(-1) / (2 * r[None] ** 2))
        return q + w @ v
    return f


def _zones(q, asc, desc):
    y = q[:, 1]
    y = np.where(y < -XH_EM, -XH_EM + (y + XH_EM) * asc, y)
    y = np.where(y > 0, y * desc, y)
    return np.c_[q[:, 0], y]


class JoinedGenerator:
    """Lines of joined ink (gen.Generator.line's sample, with points=True's point labels)."""

    def __init__(self, fonts: dict[str, JoinedFont], fit=None, residual: float | None = None):
        from .timing import FIT_ONE_WRITER, Fit, Timing
        self.fonts = fonts
        f = fit or Fit(**FIT_ONE_WRITER)
        self.timing = Timing(f) if residual is None else Timing(f, residual)

    def line(self, text: str, rng, writer: Writer, points: bool = True) -> dict:
        from .timing import RATE
        jf = self.fonts[writer.font]
        if not jf.covers(text):
            raise ValueError("the font doesn't cover the line")
        w = _around(writer, rng)
        tilt = np.radians(rng.normal(0, 1.0))
        ph, freq = rng.uniform(0, 2 * np.pi), rng.uniform(0.1, 0.4)
        # words: shaped, each glyph's strokes placed
        words, start = [], 0
        for tok in text.split(" "):
            if tok:
                words.append((start, tok))
            start += len(tok) + 1
        gap = JOIN_GAP * jf.width_scale
        pen_x = 0.0
        pieces = []  # per word: list of (char index, connector?, body or None, rest)
        for wi, (c0, tok) in enumerate(words):
            sh = jf.shape(tok)
            if sorted({cl for _, cl, _, _ in sh}) != list(range(len(tok))):
                raise ValueError("a character without a glyph of its own")
            letters_seen = set()
            sg = []
            for gid, cl, gx, gy in sh:
                conn = jf.is_connector(gid)
                if not conn:
                    if cl in letters_seen:
                        raise ValueError("a character of two letter glyphs")
                    letters_seen.add(cl)
                sg.append((c0 + cl, conn, jf.glyph(gid), np.array([gx, gy])))
            # each body walked from the end the pen arrives at (the one before ends there) to
            # the end the next glyph starts from
            glyphs, p = [], None
            for k, (ci, conn, g, off) in enumerate(sg):
                s = t = None
                ends = g.ends()
                if len(ends) >= 2:
                    E = {n: g.end_pos(n) + off for n in ends}
                    if p is not None:
                        s0 = min(ends, key=lambda n: float(np.linalg.norm(E[n] - p)))
                        if np.linalg.norm(E[s0] - p) < gap:
                            s = s0
                    if k + 1 < len(sg) and sg[k + 1][2].ends():
                        nx = sg[k + 1]
                        En = [nx[2].end_pos(m) + nx[3] for m in nx[2].ends()]
                        far = lambda n: min(float(np.linalg.norm(E[n] - q)) for q in En)
                        t0 = min((n for n in ends if n != s), key=far)
                        if far(t0) < gap:
                            t = t0
                    if s is None and t is not None:
                        s = min((n for n in ends if n != t), key=lambda n: float(E[n][0]))
                    if t is None and s is not None:
                        t = max((n for n in ends if n != s), key=lambda n: float(E[n][0]))
                body = g.walk(s, t)
                p = None if body is None else body[-1] + off
                mv = lambda q, off=off: (q + off) * [w.width, 1.0]
                glyphs.append((ci, conn, None if body is None else mv(body), [mv(q) for q in g.rest]))
            allp = [q for _, _, b, r in glyphs for q in ([b] if b is not None else []) + r]
            if not allp:
                continue
            lo = min(float(q[:, 0].min()) for q in allp)
            right = max(float(q[:, 0].max()) for q in allp)
            dx = (pen_x - lo) if wi else -lo
            glyphs = [(ci, cn, None if b is None else b + [dx, 0.0], [q + [dx, 0.0] for q in r]) for ci, cn, b, r in glyphs]
            pen_x = right + dx + w.word * XH_EM * rng.lognormal(0, 0.2)
            # the word's shape changes: zones, a displacement field with bumps in its letters, a bend
            boxes = []
            for ci, cn, b, r in glyphs:
                qs = ([b] if b is not None else []) + r
                if qs and not cn:
                    a = np.concatenate(qs)
                    boxes.append((a[:, 0].min(), a[:, 0].max(), a[:, 1].min(), a[:, 1].max()))
            fld = _field(rng, boxes, w.warp)
            kb, phb = rng.uniform(1.0, 3.0), rng.uniform(0, 2 * np.pi)
            amp = rng.normal(0, w.bend, 2)

            def shape(q, fld=fld, kb=kb, phb=phb, amp=amp):
                q = fld(_zones(q, w.asc, w.desc))
                q = q + np.c_[amp[0] * np.sin(kb * q[:, 1] * np.pi + phb), amp[1] * np.sin(kb * q[:, 0] * np.pi + phb)]
                q = q.copy()
                q[:, 0] += -w.slant * q[:, 1]
                q[:, 1] += w.drift * np.sin(freq * q[:, 0] * 2 * np.pi + ph)
                return q
            pieces.append([(ci, cn, None if b is None else shape(b), [shape(q) for q in r]) for ci, cn, b, r in glyphs])
        if not pieces:
            raise ValueError("no ink")

        # pen order: a word's letters and connectors in one stroke where they meet; the parts
        # apart from a letter after it, or (mostly) at the word's end. Labels per path point:
        # (n, char) or (n, (char before, char after)) for a join.
        seq = []  # (path, labels, delayed)
        for glyphs in pieces:
            held, cur, lab = [], None, None
            for ci, cn, b, rest in glyphs:
                if b is not None:
                    tag = (ci, ci + 1) if cn else ci
                    near = cur is not None and np.linalg.norm(b[0] - cur[-1]) < gap
                    if near and (cn or rng.random() >= w.lift):
                        prev = lab[-1][1]
                        n = max(2, int(np.linalg.norm(b[0] - cur[-1]) / 0.01))
                        bridge = np.linspace(cur[-1], b[0], n)[1:-1]
                        btag = prev if isinstance(prev, tuple) else tag if isinstance(tag, tuple) else (prev, ci)
                        cur = np.vstack([cur, bridge, b])
                        lab += [(len(bridge), btag), (len(b), tag)]
                    else:
                        if cur is not None:
                            seq.append((cur, lab, False))
                        cur, lab = b, [(len(b), tag)]
                for q in rest:
                    if rng.random() < w.dot_delay:
                        held.append((ci, q))
                    else:
                        if cur is not None:
                            seq.append((cur, lab, False))
                            cur, lab = None, None
                        seq.append((q, [(len(q), ci)], False))
            if cur is not None:
                seq.append((cur, lab, False))
            if len(held) > 1 and rng.random() < w.rtl:
                held = held[::-1]
            seq += [(q, [(len(q), ci)], True) for ci, q in held]

        rot = np.array([[np.cos(tilt), -np.sin(tilt)], [np.sin(tilt), np.cos(tilt)]])
        out, owner, powner, joins = [], [], [], []
        t, last, last_ci = 0.0, None, None
        seam = SEAM * XH_EM
        for q, pcs, dl in seq:
            q = q @ rot.T
            d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(q, axis=0), axis=1))]
            labp = np.concatenate([np.full(n, -1 if isinstance(c, tuple) else c) for n, c in pcs])
            # a join: the run of join pieces (connector glyph and the bridges either side),
            # from the path point before it to the one after; at least `seam` either side of
            # its middle (a join where two letters only touch)
            jn = []
            k, run = 0, None
            for n, c in list(pcs) + [(0, None)]:
                if isinstance(c, tuple):
                    run = (run[0], c) if run else (k, c)
                elif run is not None:
                    i0, i1 = max(0, run[0] - 1), min(len(d) - 1, k)
                    a, b = float(d[i0]), float(d[i1])
                    mid = (a + b) / 2
                    a, b = max(0.0, min(a, mid - seam)), min(float(d[-1]), max(b, mid + seam))
                    labp[(d >= a) & (d <= b)] = -1
                    jn.append([a, b, int(run[1][0]), int(run[1][1])])
                    run = None
                k += n
            cis = []
            for _, c in pcs:
                for x in (c if isinstance(c, tuple) else (c,)):
                    if int(x) not in cis:
                        cis.append(int(x))
            if last is not None:
                jump = float(np.linalg.norm(q[0] - last)) / XH_EM
                t += (self.timing.delayed_pause(w.timing, rng) if dl else
                      self.timing.pause("between", jump, w.timing, rng))
            arr = self.timing.stroke(q, XH_EM, w.timing, rng, t)
            if points:
                pos = self.timing.last_pos
                kk = np.clip(np.searchsorted(d, pos, side="right") - 1, 0, len(q) - 1)
                k2 = np.minimum(kk + 1, len(q) - 1)
                near = np.where(pos - d[kk] <= d[k2] - pos, kk, k2)
                powner.append(labp[near].astype(int))
                joins.append(jn)
            arr[:, :2] += rng.normal(0, 0.002, arr[:, :2].shape)
            t = float(arr[-1, 2])
            out.append(arr)
            owner.append(cis)
            last = q[-1]
        spans = [[] for _ in text]
        for si, cis in enumerate(owner):
            for c in cis:
                spans[c].append(si)
        res = {"text": text, "strokes": out, "spans": spans, "xh": XH_EM, "line": XH_EM / w.xh_line, "base": 0.0,
               "writer": w.font, "rate": RATE}
        if points:
            res["point_owner"], res["joins"] = powner, joins
        return res


def load_fonts(root: Path, dirs: list[str], wght: float | None = None) -> dict[str, JoinedFont]:
    """The fetched joined fonts (glassine-synth fetch with joined.toml) by directory."""
    out = {}
    for d in dirs:
        files = sorted((root / d).glob("*.ttf"))
        if files:
            out[d] = JoinedFont(files[0], wght)
    return out
