"""BRUSH-format lines cut into letters (brushletters.py) and text written in letter models'
hands (hands.py), on made-up ink and made-up letter models only: no file of any real dataset,
no person's letters."""

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from glassine_synth.ink import XH, Params

from glassine_train import brush, brushletters as BL, gen, hands as H, timing as T

from test_gen4 import _table
from test_writerseval import _drawing


def _line(text, **kw):
    t, d, lab = _drawing(text, **kw)
    return brush.Line("1", "1", t, d[:, :2], d[:, 2] > 0.5, lab.argmax(1))


# ---------------------------------------------------------------------------- cutting

def test_clean_owner_gives_strays_and_spaces_to_a_neighbour():
    t = "ab c"
    o = np.array([0, 0, 0, 1, 0, 0, 1, 1, 1, 2, 2])
    assert BL.clean_owner(o, t).tolist() == [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
    # a space's points and a lone first point
    assert BL.clean_owner(np.array([1, 0, 0, 2, 2, 3, 3]), "ab cd").tolist() == [0, 0, 0, 0, 0, 3, 3]
    assert BL.clean_owner(np.array([2, 2]), "ab cd").tolist() == [-1, -1]


def test_cut_sample_gives_every_letter_its_own_strokes():
    ln = _line("hello you", joined=[(0, 1), (1, 2), (6, 7)])
    s = BL.cut_sample(ln, BL.owners(ln))
    assert s["cut_at_joins"] is True and s["spans_source"] == "exact"
    plain = brush.to_sample(ln)
    assert len(plain["strokes"]) == 5 and len(s["strokes"]) == 8   # h|e|l in one stroke, y|o in one
    # each letter's strokes are its own, a space has none, and every point is still there
    seen = [j for sp in s["spans"] for j in sp]
    assert sorted(seen) == list(range(len(s["strokes"])))
    assert s["spans"][5] == []
    assert sum(len(q) for q in s["strokes"]) == sum(len(q) for q in plain["strokes"])
    # the cut: a piece ends where the next one begins (the next point of the stroke)
    assert s["strokes"][0][-1][2] < s["strokes"][1][0][2]


def test_join_rate_counts_neighbours_one_stroke_writes():
    assert BL.join_rate([_line("ab cd")]) == 0.0
    # 3 neighbouring pairs in words (a-b, c-d, d-e; none across the space), 2 joined
    assert BL.join_rate([_line("ab cde", joined=[(0, 1), (3, 4)])]) == pytest.approx(2 / 3)


def test_owners_needs_an_aligner_for_its_way():
    with pytest.raises(ValueError):
        BL.owners(_line("ab"), "aligner")


@pytest.mark.skipif(not os.environ.get("HW_BIN"), reason="needs the recognizer program (HW_BIN)")
def test_build_letters_counts_joined_writers_letters(tmp_path):
    """a writer who joins every pair still gets letters (the cut samples' letters are usable;
    the mark itself: recognizer/tests/test_letters.py test_strokes_cut_at_joins)"""
    lines = [_line(t, joined=[(0, 1), (1, 2), (4, 5), (5, 6)]) for t in ("abc abc", "abc cab")]
    cut = [BL.cut_sample(ln, BL.owners(ln)) for ln in lines]
    r = BL.build_letters(Path(os.environ["HW_BIN"]), tmp_path / "models", cut, tmp_path / "out", tmp_path)
    assert r["chars"]["a"][0] == 2 and r["chars"]["b"][0] == 2 and (tmp_path / "out" / "model").stat().st_mode & 0o077 == 0


# ---------------------------------------------------------------------------- hands

def _rows(table, chars, letter=0.4):
    rows = [{"spacing": {"letter": letter, "word": 1.2}}, {"metrics": {"x_height": 1}}]
    for c in chars:
        g = table[c]
        st = [[[float(x) / XH, -float(y) / XH] for x, y in q[:, :2]] for q in g.strokes]
        rows.append({"char": c, "covered": True, "variants": [{"strokes": st, "width": g.advance / XH, "use": 1}]})
    return rows


def _hands(n=3, chars="abcdeghilmnorstuvwxyz"):
    tab = _table()
    return [H.Hand(gen.LetterWriter(_rows(tab, [c for c in chars if c in tab], 0.3 + 0.1 * i), f"hand-{i}"), i, 0.3, 7)
            for i in range(n)]


def _gen():
    return gen.Generator(None, T.Fit(), [(0.4, 1.2)], residual=1.0, params=Params(noise=0.0))


def _same(a, b):
    assert a["text"] == b["text"] and a["spans"] == b["spans"] and len(a["strokes"]) == len(b["strokes"])
    for p, q in zip(a["strokes"], b["strokes"]):
        assert np.array_equal(p, q)


def test_one_hand_line_is_the_letter_model_line():
    """a line in one hand without a donor is the ink the letter model writes by itself (the
    generator's hooks change nothing else)"""
    hs, g = _hands(1), _gen()
    st = hs[0].style(np.random.default_rng(1))
    lw = H.LineWriter("cat sat", hs, [0, 0], None, None, warp=0.0)
    a = g.line("cat sat", np.random.default_rng(5), style=st, letters=lw)
    b = g.line("cat sat", np.random.default_rng(5), style=st, letters=hs[0].lw)
    _same(a, b)
    assert a["writer"] == "hand-0"
    # with the letters' own displacement (the default) the ink differs
    c = g.line("cat sat", np.random.default_rng(5), style=st, letters=H.LineWriter("cat sat", hs, [0, 0], None, None))
    assert c["text"] == a["text"] and not np.array_equal(c["strokes"][0], a["strokes"][0])


def test_mixed_and_donor_lines_say_so():
    hs = _hands(3, chars="acestu")
    rng = np.random.default_rng(3)
    tab = _table()
    donor = gen.FontWriter([("f", tab)], {"f": set(tab)})
    lw = H.plan_line("cat sat cut", hs, 0, True, donor, rng)
    assert lw is not None and lw.of[0] == 0 and lw.name.startswith("hands-0,")
    # a character the hand lacks comes from the donor, within DONOR_MAX of the line
    lw = H.plan_line("cats sit", hs, 1, False, donor, rng)  # i lacking: 1 of 7
    assert lw.name == "hand-1+donor" and lw.donated == 1
    assert lw.glyph_at(5, "i", rng)[0] == "f" and lw.glyph_at(0, "c", rng)[0] == "hand-1"
    assert H.plan_line("hmm nor", hs, 1, False, donor, rng) is None   # mostly lacking
    assert H.plan_line("cats sit", hs, 1, False, None, rng) is None   # no donor
    s = _gen().line("cats sit", rng, style=hs[1].style(rng), letters=lw)
    assert s["writer"] == "hand-1+donor" and s["spans"][5]


def test_hands_draw_their_habits_once():
    a, b = _hands(2), _hands(2)
    assert a[0].habits == b[0].habits and a[0].habits != a[1].habits
    assert a[0].style(np.random.default_rng(0)).join == a[0].join


def test_calibrated_joins_match_the_writers_rate():
    hs, g = _hands(1), _gen()
    texts = ["cat sat on a mat", "the tea was nice", "rain on the ocean"] * 8
    for target in (0.15, 0.4):
        hs[0].join_rate = target
        hs[0].calibrate(g, texts, 1)
        rng = np.random.default_rng(9)
        p = j = 0
        for t in texts * 3:
            s = g.line(t, rng, style=hs[0].style(rng), letters=hs[0].lw)
            a, b = H.joined_pairs(s)
            p, j = p + a, j + b
        assert j / p == pytest.approx(target, abs=0.08)


def test_render_writes_flagged_sets(tmp_path):
    hs, g = _hands(2, chars="acestu"), _gen()
    tab = _table()
    donor = gen.FontWriter([("f", tab)], {"f": set(tab)})
    jobs = [("cat sat", "prose", 1, 0, False), ("cats sit", "prose", 2, 1, False), ("cat sat cut", "prose", 3, 0, True),
            ("hmm nor", "prose", 4, 0, False)]
    m = H.render(tmp_path / "set", jobs, g, hs, donor, 2, {"x": 1})
    assert m["lines"] == 3 and m["failed"] == 1 and m["donor_lines"] == 1 and m["mixed_lines"] == 1
    rows = [json.loads(x) for x in (tmp_path / "set" / "rows.jsonl").read_text().splitlines()]
    assert rows[1]["writer"] == "hand-1+donor" and rows[2]["writer"].startswith("hands-0,")


def test_load_hands(tmp_path):
    tab = _table()
    (tmp_path / "0").mkdir()
    (tmp_path / "0" / "model").write_text("\n".join(json.dumps(r) for r in _rows(tab, "ab")) + "\n")
    (tmp_path / "hands.json").write_text(json.dumps({"way": "labels", "hands": [
        {"hand": 0, "letters": str(tmp_path / "0" / "model"), "join_rate": 0.25}]}))
    hs = H.load_hands(tmp_path / "hands.json", 1)
    assert hs[0].n == 0 and hs[0].join_rate == 0.25 and set(hs[0].lw.chars) == {"a", "b"}
