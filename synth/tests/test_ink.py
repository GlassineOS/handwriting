import numpy as np
from PIL import ImageFont

from glassine_synth.glyph import Glyph
from glassine_synth.ink import Params, line


def _table():
    # two synthetic glyphs: "l" one down stroke, "i" stem plus a delayable dot
    stem = np.c_[np.zeros(20), np.linspace(-0.7, 0, 20)]
    dot = np.array([[0.0, -0.62], [0.01, -0.61]])
    return {
        "l": Glyph("l", 0.3, [stem], [False]),
        "i": Glyph("i", 0.3, [stem * [1, 0.6], dot], [False, True]),
    }


def test_spans_cover_text_and_time_increases():
    rng = np.random.default_rng(3)
    smp = line("il li", [("a", _table()), ("b", _table())], rng, Params())
    assert len(smp["spans"]) == 5
    assert smp["spans"][2] == []  # the space has no strokes
    assert all(smp["spans"][k] for k in (0, 1, 3, 4))
    t = [s[0, 2] for s in smp["strokes"]]
    assert t == sorted(t)
    for s in smp["strokes"]:
        assert np.all(np.diff(s[:, 2]) > 0)


def test_delayed_dot_moves_to_word_end():
    rng = np.random.default_rng(0)
    p = Params(delay=1.0, extra_lift=0.0)
    smp = line("il", [("a", _table())], rng, p)
    # strokes in time order: i stem, l stem, then the i dot
    assert smp["spans"][0] == [0, 2]
    assert smp["spans"][1] == [1]


def test_shape_variation_keeps_spans_and_is_off_by_default():
    text = "il li"
    base = line(text, [("a", _table())], np.random.default_rng(5), Params())
    again = line(text, [("a", _table())], np.random.default_rng(5), Params(warp=0.0, zones=0.0))
    assert all(np.array_equal(x, y) for x, y in zip(base["strokes"], again["strokes"]))
    var = line(text, [("a", _table())], np.random.default_rng(5), Params(warp=0.03, zones=0.2))
    assert [bool(s) for s in var["spans"]] == [bool(s) for s in base["spans"]]
    assert all(np.isfinite(s).all() for s in var["strokes"])
    assert not all(np.allclose(x[:, :2], y[:, :2]) for x, y in zip(base["strokes"], var["strokes"])
                   if x.shape == y.shape)


def test_zones_stretch_only_outside_the_x_height():
    from glassine_synth.ink import XH, _zones
    q = np.array([[0.0, -0.9], [0.0, -0.2], [0.0, 0.3]])
    z = _zones(q, 2.0, 0.5)
    assert np.allclose(z[:, 1], [-XH - 2 * (0.9 - XH), -0.2, 0.15])


def test_new_parameters_off_by_default():
    a = line("il li", [("a", _table())], np.random.default_rng(5), Params())
    b = line("il li", [("a", _table())], np.random.default_rng(5),
             Params(asc_scale=1.0, desc_scale=1.0, join=0.0, pulse_floor=0.0))
    assert all(np.array_equal(x, y) for x, y in zip(a["strokes"], b["strokes"]))


def test_joins_keep_every_character_spanned():
    from glassine_synth.ink import preset
    p = preset("real1", join=1.0, join_dist=5.0, extra_lift=0.0, delay=0.0)
    smp = line("ilil il", [("a", _table())], np.random.default_rng(2), p)
    plain = line("ilil il", [("a", _table())], np.random.default_rng(2), Params(extra_lift=0.0, delay=0.0))
    assert len(smp["strokes"]) < len(plain["strokes"])
    assert all(smp["spans"][k] for k in range(7) if k != 4)
    for s in smp["strokes"]:
        assert np.all(np.diff(s[:, 2]) > 0)


def test_timed_glyph_keeps_its_timing():
    q = np.c_[np.zeros(30), np.linspace(-0.4, 0, 30), np.linspace(0, 0.29, 30)]
    tab = {"l": Glyph("l", 0.3, [q], [False])}
    smp = line("ll", [("h", tab)], np.random.default_rng(1), Params(extra_lift=0.0))
    for s in smp["strokes"]:
        assert len(s) == 30 and np.allclose(np.diff(s[:, 2]), 0.01)
    assert smp["strokes"][1][0, 2] > smp["strokes"][0][-1, 2]


def test_stats_measure_a_line():
    from glassine_synth.stats import measure
    a = np.linspace(0, 2 * np.pi, 40)
    tab = {**_table(), "o": Glyph("o", 0.4, [np.c_[0.2 + 0.2 * np.cos(a), -0.225 + 0.225 * np.sin(a)]], [False])}
    smp = line("lol oil", [("a", tab)], np.random.default_rng(4), Params())
    s = {"text": smp["text"], "strokes": [x.tolist() for x in smp["strokes"]], "spans": smp["spans"]}
    m = measure([s])
    assert m["samples"] == 1 and m["strokes_per_char"] > 0 and m["speed_xh_per_s"]["n"] > 0
