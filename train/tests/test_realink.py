"""Real ink as training sets (realink.py), on made-up ink only: no file of any real dataset
or person is used here (BRUSH-format files and a store are written in the tests)."""

import json
import stat

import numpy as np
import pytest

from glassine_train import brush, realink
from glassine_train.inkstore import InkStore
from glassine_train.real import prepare

from test_real import _store
from test_writerseval import _drawing, _write


def _line(text, **kw):
    t, d, lab = _drawing(text, **kw)
    return brush.Line("1", "1", t, d[:, :2], d[:, 2] > 0.5, lab.argmax(1))


def test_strip_drops_characters_outside_with_their_strokes():
    ln = _line('say "hi" now')
    s = realink.strip_outside(ln, "abcdefghijklmnopqrstuvwxyz ")
    assert s.text == "say hi now"
    assert len(s.xy) == len(ln.xy) - 2 * 6  # the two quotes' strokes
    assert set(s.owner.tolist()) == {i for i, c in enumerate(s.text) if c != " "}
    assert len(brush.strokes_of(s)) == len(brush.strokes_of(ln)) - 2
    # no character outside: the line itself; a quote sharing a stroke with a letter: none
    assert realink.strip_outside(ln, 'abcdefghijklmnopqrstuvwxyz "') is ln
    assert realink.strip_outside(_line('a"b', joined=[(0, 1)]), "ab") is None
    assert realink.strip_outside(_line('"'), "ab") is None


def test_parts_by_hash_are_stable():
    shares = [("test", 0.2), ("dev", 0.05)]
    a = [realink.part_of(5, "writer", str(w), shares, "train") for w in range(400)]
    assert a == [realink.part_of(5, "writer", str(w), shares, "train") for w in range(400)]
    assert 40 < a.count("test") < 120 and a.count("dev") < 40
    assert a != [realink.part_of(6, "writer", str(w), shares, "train") for w in range(400)]


def test_near_texts_are_one_group():
    g = realink.text_groups(["the quick brown fox", "the quick brown fax", "an other line", "ab"])
    assert g["the quick brown fox"] == g["the quick brown fax"] != g["an other line"]
    assert g["ab"] == "ab"


def test_brush_split_keeps_test_texts_and_writers_out_of_training(tmp_path):
    texts = ["hello there", "hello therm", "quiet morning", "lazy dog", "brown fox", "small cat"]
    files = {(str(w), str(i)): _drawing(t) for w in range(1, 31) for i, t in enumerate(texts)}
    _write(tmp_path, files)
    out = tmp_path / "out"
    r = realink.write_brush(tmp_path / "BRUSH", out, seed=1, copies=2, held_texts=0.4)
    rec = json.loads((out / "split.json").read_text())
    assert stat.S_IMODE(out.stat().st_mode) == 0o700 and stat.S_IMODE((out / "split.json").stat().st_mode) == 0o600
    tr, te = rec["parts"]["train"], rec["parts"]["test"]
    assert tr and te and not set(tr) & set(te) and not set(tr) & set(rec["parts"]["dev"])
    held = {texts[int(d)] for ds in te.values() for d in ds}
    trained = {texts[int(d)] for ds in tr.values() for d in ds}
    assert held and not held & trained
    # the near pair is held out together or trained together
    assert ("hello there" in held) == ("hello therm" in held)
    st = InkStore(out / "train")
    assert len(st) == 2 * sum(len(v) for v in tr.values()) == r["train"]["lines"]
    strokes, row = st[0]
    assert row["kind"] == "brush" and row["writer"].startswith("brush:") and row["xh"] > 0
    assert 0.7 <= row["line"] <= 2.5 and abs(row["base"] - brush.BOX_BASE / brush.BOX_H) < 1e-4
    assert all(len(sp) for c, sp in zip(row["text"], row["spans"]) if c != " ")
    assert st.meta["alphabet"]


def test_person_set_from_the_persons_split_only(tmp_path):
    store = tmp_path / "handwriting"
    store.mkdir()
    times = [i * 10 for i in range(100)] + [5000 + i * 10 for i in range(20)]
    _store(store, times, ["word%d" % i for i in range(120)])
    prepare(store, tmp_path / "split")
    r = realink.write_person(tmp_path / "split", tmp_path / "out", copies=3)
    st = InkStore(tmp_path / "out" / "train")
    assert r["train"]["samples"] * 3 == len(st)
    strokes, row = st[0]
    assert row["line"] == 1.0 and row["writer"] == "person" and 0 < row["xh"] < 1
    assert {r_["text"] for r_ in st.rows}.isdisjoint({"word%d" % i for i in range(100, 120)})  # the test session
    # a guest's store never trains
    guest = tmp_path / "handwriting-writers" / "0123456789abcdef"
    guest.mkdir(parents=True)
    _store(guest, times, ["word%d" % i for i in range(120)])
    prepare(guest, tmp_path / "gsplit")
    with pytest.raises(SystemExit):
        realink.write_person(tmp_path / "gsplit", tmp_path / "gout")
