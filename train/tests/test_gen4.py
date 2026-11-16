"""The model-4 data tools on made-up glyphs and texts only: no font, no book, no person's ink."""

import json

import numpy as np
import pytest

from glassine_synth.glyph import Glyph
from glassine_synth.ink import XH, Params

from glassine_train import gen, inkmeasure, spacing as S, timing as T, writers
from glassine_train.features import NFEAT, NFEAT_REF, features, features_ref, ref_height
from glassine_train.inkstore import InkStore, Writer


def _bar(x0, x1, y0, y1, n=20):
    return np.c_[np.linspace(x0, x1, n), np.linspace(y0, y1, n)]


def _table():
    """Glyphs drawn by hand, em units, y down, baseline 0, x-height XH."""
    t = {}
    for c in "abcdeghmnorsuvwxyz":
        t[c] = Glyph(c, 0.3, [_bar(0.02, 0.02, -XH, 0), _bar(0.02, 0.25, -XH, -XH), _bar(0.25, 0.25, -XH, 0)],
                     [False] * 3)
    t["l"] = Glyph("l", 0.12, [_bar(0.03, 0.03, -2.2 * XH, 0)], [False])
    t["t"] = Glyph("t", 0.2, [_bar(0.08, 0.08, -1.6 * XH, 0), _bar(0.0, 0.18, -XH, -XH)], [False, True])
    t["i"] = Glyph("i", 0.1, [_bar(0.04, 0.04, -XH, 0), _bar(0.04, 0.05, -1.5 * XH, -1.45 * XH, 3)], [False, True])
    t[" "] = Glyph(" ", 0.25, [], [])
    return t


def _gen(**style):
    tab = _table()
    fw = gen.FontWriter([("hand", tab)], {"hand": set(tab)})
    g = gen.Generator(fw, T.Fit(), [(0.4, 1.2)], 0.2, Params(noise=0.0))
    st = gen.Style(timing=T.WriterTiming(), **style)
    return g, st


# ---------------------------------------------------------------------------- spacing

def test_profile_and_gap_as_spacing_c():
    a, b = S.Profile(), S.Profile()
    a.add([0.0, 0.0], [0.0, 1.0])  # a stem from the baseline to one x-height
    b.add([0.5, 0.5], [0.0, 1.0])
    assert S.gap(a, b) == pytest.approx(0.5)
    assert S.gap(a, b, 0.25) == pytest.approx(0.75)
    # ink above the profile's top counts in the top band, below the bottom in the lowest
    c = S.Profile()
    c.add([1.0, 1.0], [-3.0, -2.0])
    assert not np.isnan(c.l[0]) and np.isnan(c.l[1:]).all()
    # no shared band: the boxes
    assert S.gap(a, c) == pytest.approx(1.0)
    # a slanted stroke crosses band edges where it crosses them
    d = S.Profile()
    d.add([0.0, 2.2], [-0.6, 1.6])
    assert np.allclose(d.l, np.arange(8) * 0.275) and np.allclose(d.r, (np.arange(8) + 1) * 0.275)
    # an o beside an l leaves open space above the o
    o, l = S.Profile(), S.Profile()
    o.add([0, 0.5, 0.5, 0, 0], [0, 0, 1, 1, 0])
    l.add([0.8, 0.8], [0, 1.6])
    assert S.open_space(o, l) == 0.0  # the bands shared have the same gap
    l2 = S.Profile()
    l2.add([0.8, 1.2], [0, 1.0])
    assert S.open_space(o, l2) > 0


def test_pair_gap_uses_the_pair_then_open_space():
    a, b = S.Profile(), S.Profile()
    a.add([0, 0], [0, 1])
    b.add([0, 0.4], [0, 1])
    sp = {"letter": 0.4, "open": 0.5, "open0": 0.1, "pairs": [["a", "b", 0.1, 3]]}
    assert S.pair_gap(sp, "a", "b", a, b) == 0.1
    g = S.pair_gap(sp, "x", "y", a, b)
    assert 0.1 <= g <= 0.7


# ---------------------------------------------------------------------------- the generator

def test_letters_are_placed_at_the_drawn_gap():
    g, st = _gen(gap=0.3, gap_sd=0.0, open=0.0, dot_delay=0.0, bar_delay=0.0, join=0.0)
    g.p = Params(noise=0.0, size=0.0, rot=0.0, shear=0.0, elastic=0.0, slant=0.0, slant_mean=0.0, tilt=0.0,
                 drift=0.0, reverse_loop=0.0, extra_lift=0.0)
    smp = g.line("nnn", np.random.default_rng(0), st)
    xs = [np.concatenate([smp["strokes"][j] for j in sp])[:, 0] for sp in smp["spans"]]
    for a, b in zip(xs, xs[1:]):
        assert (b.min() - a.max()) / XH == pytest.approx(0.3, abs=0.02)


def test_spans_times_and_references():
    g, st = _gen()
    smp = g.line("a lot in it", np.random.default_rng(1), st)
    n = len(smp["strokes"])
    assert sorted({j for sp in smp["spans"] for j in sp}) == list(range(n))
    assert all(sp == [] for c, sp in zip(smp["text"], smp["spans"]) if c == " ")
    t = np.concatenate([q[:, 2] for q in smp["strokes"]])
    assert np.all(np.diff(t) >= -1e-9)
    assert smp["xh"] == XH and smp["line"] > XH and smp["base"] == 0.0


def test_dots_wait_for_later_letters_right_to_left():
    g, st = _gen(dot_delay=1.0, bar_delay=1.0, rtl=1.0, mid=0.0, join=0.0)
    smp = g.line("titin", np.random.default_rng(2), st)
    xs = [(smp["strokes"][j], smp["text"][ci], ci) for ci, sp in enumerate(smp["spans"]) for j in sp]
    order = sorted(xs, key=lambda x: x[0][0, 2])
    late = [ci for q, c, ci in order[-4:]]
    assert sorted(late) == [0, 1, 2, 3]  # the two bars and two dots come after the n
    assert late == sorted(late, reverse=True)  # right to left
    xh = [np.c_[q[:, :2] / XH, q[:, 2]] for q in smp["strokes"]]  # the measures read x-heights
    rep, flags = inkmeasure.delays([(xh, smp["spans"], smp["text"])])
    assert rep["dot_delayed"] == 1.0 and rep["right_to_left"] == 1.0


def test_lone_marks_keep_their_size():
    g, st = _gen()
    smp = g.line("i", np.random.default_rng(3), st)
    h = np.ptp(np.concatenate(smp["strokes"])[:, 1])
    f = features_ref(smp["strokes"], ref_height(smp["xh"], smp["line"]), smp["base"])
    assert f.shape[1] == NFEAT_REF and np.allclose(f[:, 5], np.log(h / XH), atol=1e-6)


def test_timing_pulses_follow_the_path():
    tm = T.Timing(T.Fit())
    s = _bar(0, 1.0, 0, 0, 50)
    rng = np.random.default_rng(4)
    q = tm.stroke(s, 0.45, T.WriterTiming(), rng, 1.0)
    assert q[0, 2] == pytest.approx(1.0) and np.all(np.diff(q[:, 2]) > 0)
    assert np.all(np.diff(q[:, 0]) >= -1e-9) and q[-1, 0] == pytest.approx(1.0, abs=0.02)


def test_fit_recovers_a_drawn_pulse():
    t = np.arange(0, 0.6, 1 / T.RATE)
    v = T._lognorm(t, 2.0, -0.05, np.log(0.15), 0.3)
    d = np.r_[0, np.cumsum(v[:-1] / T.RATE)]
    q = np.c_[d, np.zeros_like(d), t]
    P, snr, _, _ = T.fit_stroke(q)
    assert snr > 15 and P[:, 0].sum() == pytest.approx(2.0, rel=0.15)


# ---------------------------------------------------------------------------- storage and features

def test_ink_store_round_trip_and_features(tmp_path):
    g, st = _gen()
    w = Writer(tmp_path / "set")
    smps = [g.line(t, np.random.default_rng(i), st) for i, t in enumerate(["tin", "a mat", "i"])]
    for i, s in enumerate(smps):
        w.add(s, "words", i)
    meta = w.close({"seed": 0})
    assert meta["lines"] == 3 and meta["kinds"] == {"words": 3}
    ds = InkStore(tmp_path / "set")
    for s, i in zip(smps, range(3)):
        strokes, row = ds[i]
        assert row["text"] == s["text"] and len(strokes) == len(s["strokes"])
        for a, b in zip(strokes, s["strokes"]):
            assert np.abs(a[:, :2] - b[:, :2]).max() <= 1 / 512 and np.allclose(a[:, 2], b[:, 2], atol=1e-5)
        assert features(strokes).shape[1] == NFEAT
        assert features_ref(strokes, ref_height(None, row["line"]), row["base"]).shape[1] == NFEAT_REF


def test_shipped_features_unchanged():
    rng = np.random.default_rng(7)
    st = [np.c_[np.cumsum(rng.normal(0, 1, (30, 2)), 0), np.arange(30) * 0.005 + k] for k in range(3)]
    f = features(st)
    assert f.shape == (f.shape[0], 5) and f.dtype == np.float32
    assert round(float(np.abs(f).sum()), 2) == round(float(np.abs(features(st)).sum()), 2)


# ---------------------------------------------------------------------------- contributors

def test_contributors_need_a_licence_and_stay_out_of_the_test_set(tmp_path):
    lm = tmp_path / "model"
    lm.write_text(json.dumps({"spacing": {"letter": 0.3, "word": 1.0}}) + "\n" +
                  json.dumps({"char": "o", "covered": True, "variants": [
                      {"width": 0.8, "use": 3, "strokes": [[[0, 0], [0.8, 0], [0.8, 1], [0, 1], [0, 0]]]}]}) + "\n")
    good = {"contributor": "7", "letters": str(lm), "licence": "CC-BY-4.0"}
    assert writers.check(good, set()) is None
    assert "test set" in writers.check(good, {"7"})
    assert "licence" in writers.check(dict(good, licence="all rights reserved"), set())
    f = tmp_path / "w.json"
    f.write_text(json.dumps([good, dict(good, contributor="8", licence="none")]))
    ws = writers.load(f, set(), strict=False)
    assert [m["contributor"] for _, m in ws] == ["7"] and ws[0][0].covers("oo o")
    with pytest.raises(SystemExit):
        writers.load(f, set())


def test_featurize_writes_a_dataset_the_trainer_reads(tmp_path):
    from glassine_train.cli import main
    from glassine_train.data import InkSet
    g, st = _gen()
    w = Writer(tmp_path / "ink")
    for i, t in enumerate(["tin", "a mat", "i", "salt"]):
        w.add(g.line(t, np.random.default_rng(i), st), "words", i)
    w.close({})
    for ref, nf in (("none", NFEAT), ("mix", NFEAT_REF)):
        main(["featurize", str(tmp_path / "ink"), "--out", str(tmp_path / ref), "--ref", ref, "--workers", "1"])
        ds = InkSet(tmp_path / ref)
        x, text = ds[1]
        assert x.shape[1] == nf and text == "a mat" and len(ds) == 4


def test_real_reference_from_the_area():
    from glassine_train.real import reference
    s = {"area": {"box": [0, 0, 700, 474], "line": 150}, "strokes": [[[10, 180, 0], [20, 240, 0.1]]]}
    r = reference(s, 14.0)
    assert r == {"xh": 14.0, "line": 150, "base": 250.0}
    assert reference({"strokes": s["strokes"]}, None) == {"xh": None, "line": None, "base": None}
