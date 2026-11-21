"""Joined ink (joined.py) on made-up skeletons and a made-up font: no font file, no one's ink."""

import numpy as np

from glassine_train import joined as J


def _edge(a, b, n=6):
    """an edge from node a to node b as a pixel path between their positions"""
    pa, pb = np.array(a[1], float), np.array(b[1], float)
    path = [tuple(np.round(pa + (pb - pa) * t).astype(int)) for t in np.linspace(0, 1, n)]
    return [a, b, path]


def test_walk_retraces_a_stem_to_reach_the_exit():
    # an "h"-like skeleton: entry at the bottom left, a junction, a stem up and a bow to the exit
    e0, j, top, ex = ("e", (20, 0)), ("j", 1), ("e", (0, 2)), ("e", (20, 10))
    edges = [_edge(e0, (j, (10, 1))), _edge((j, (10, 1)), top), _edge((j, (10, 1)), ex)]
    edges = [[e0, j, edges[0][2]], [j, top, edges[1][2]], [j, ex, edges[2][2]]]
    path = J.euler_walk(edges, [0, 1, 2], e0, ex)
    assert path[0] == edges[0][2][0] and path[-1] == edges[2][2][-1]
    # every edge written, the stem twice (up and down again): one pen path, no lift
    for _, _, p in edges:
        assert all(q in path for q in p)
    assert sum(1 for q in path if q == edges[1][2][-1]) == 1  # the stem's top reached once


class _Font:
    """two letters "a", "b" and a connector between them, each a straight stroke (em, y down)"""

    width_scale = 1.0

    def __init__(self):
        line = lambda x0, x1, y0, y1: np.c_[np.linspace(x0, x1, 20), np.linspace(y0, y1, 20)]
        self.g = {0: J.JGlyph(line(0.0, 0.3, 0.0, -0.2)), 1: J.JGlyph(line(0.3, 0.5, -0.2, 0.0)),
                  2: J.JGlyph(line(0.0, 0.3, 0.0, -0.2), [line(0.1, 0.2, -0.6, -0.6)])}

    def covers(self, text):
        return set(text) <= set("ab ")

    def shape(self, word):
        out, x = [], 0.0
        for i, c in enumerate(word):
            out.append((0 if c == "a" else 2, i, x, 0.0))
            if i + 1 < len(word):
                out.append((1, i, x, 0.0))
            x += 0.5
        return out

    def is_connector(self, gid):
        return gid == 1

    def glyph(self, gid):
        return self.g[gid]


def test_a_joined_word_is_one_stroke_with_its_join_marked():
    g = J.JoinedGenerator({"f": _Font()})
    rng = np.random.default_rng(3)
    w = J.Writer(font="f", lift=0.0, dot_delay=1.0, warp=0.0, bend=0.0, drift=0.0)
    from glassine_train.timing import WriterTiming
    w.timing = WriterTiming()
    s = g.line("ab", rng, w)
    # the body of a, the connector, the body of b in one stroke; b's bar after it
    assert len(s["strokes"]) == 2
    own = s["point_owner"][0]
    assert own[0] == 0 and own[-1] == 1 and (own == -1).any()
    # the join is one run between the two letters, recorded with them
    assert len(s["joins"][0]) == 1 and s["joins"][0][0][2:] == [0, 1]
    assert s["spans"] == [[0], [0, 1]]
    assert set(s["point_owner"][1]) == {1}
