"""BRUSH-format lines as aligner sets (alignrun.brush_line, make_brush) and training from a
checkpoint, on made-up ink only: no file of any real dataset is used (BRUSH-format files
are written in the tests)."""

import json
import pickle
import stat

import numpy as np
import torch

from glassine_train import aligner as A
from glassine_train import alignrun as R
from glassine_train import brush

from test_writerseval import _drawing, _write


def _line(text, **kw):
    t, d, lab = _drawing(text, **kw)
    return brush.Line("1", "1", t, d[:, :2], d[:, 2] > 0.5, lab.argmax(1))


def test_brush_line_keeps_owners_spans_and_draws_pauses():
    ln = _line("hello you", joined=[(0, 1), (1, 2)])
    rng = np.random.default_rng(0)
    a = R.brush_line(ln, 0.2, rng)
    assert a["text"] == ln.text and a["xh"] == 0.2
    assert len(a["strokes"]) == len(brush.strokes_of(ln)) == len(a["owner"])
    own = np.concatenate(a["owner"])
    assert np.array_equal(own, ln.owner)  # no point of a space here
    assert a["spans"][0] == a["spans"][1] == a["spans"][2] == [0]
    assert a["spans"][5] == []
    # points 10 ms apart within a stroke; pauses drawn per stroke, all positive and not one value
    q = a["strokes"][0]
    assert np.allclose(np.diff(q[:, 2]), R.BRUSH_DT, atol=1e-5)
    gaps = [b[0, 2] - p[-1, 2] for p, b in zip(a["strokes"][:-1], a["strokes"][1:])]
    assert min(gaps) > 0 and len(set(np.round(gaps, 6))) > 1
    assert R.joined_in_stroke(ln) and not R.joined_in_stroke(_line("ab cd"))
    # the aligner's training rows are made from it (each frame a character of the text)
    x, cls, fcls = R.prepare([a])[0]
    assert len(x) and set(fcls.tolist()) <= {A.LETTERS.find(c) for c in ln.text if c != " "}


def test_make_brush_writes_private_sets_by_the_split(tmp_path):
    texts = ["hello there", "quiet morning", "lazy dog", "brown fox", "small cat", "big hat"]
    files = {(str(w), str(i)): _drawing(t, joined=[(0, 1)]) for w in range(1, 41) for i, t in enumerate(texts)}
    _write(tmp_path, files)
    out = tmp_path / "out"
    c = R.make_brush(tmp_path / "BRUSH", out, seed=1, joined_copies=2, letter_lines=3)
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    for f in ("train.pkl", "dev.pkl", "dev-writers.pkl", "test.pkl"):
        assert stat.S_IMODE((out / f).stat().st_mode) == 0o600
    json.dumps(c)  # counts only
    tr = pickle.loads((out / "train.pkl").read_bytes())
    test = pickle.loads((out / "test.pkl").read_bytes())
    assert c["train"]["written"] == len(tr) == 2 * c["train"]["lines"]  # every line here is joined
    assert test and all(len(w["letters"]) <= 3 and w["test"] for w in test.values())
    held = {s["text"] for w in test.values() for s in w["test"]}
    assert held and not held & {s["text"] for s in tr}
    # a test writer's letter lines are its lines of the training texts
    assert all(s["text"] not in held for w in test.values() for s in w["letters"])
    assert all(s["xh"] > 0 for s in tr)


def test_train_from_a_checkpoint_and_save_every(tmp_path):
    ln = [R.brush_line(_line(t, joined=[(0, 1)]), 0.2, np.random.default_rng(i))
          for i, t in enumerate(["hello", "ab cd", "lazy dog", "in it"] * 6)]
    d = tmp_path / "d"
    d.mkdir()
    (d / "train.pkl").write_bytes(pickle.dumps(ln))
    (d / "dev.pkl").write_bytes(pickle.dumps(ln[:4]))
    R.train(d, tmp_path / "a.pt", steps=4, batch=4, hidden=16, threads=1, ce=1.0)
    R.train(d, tmp_path / "b.pt", steps=4, batch=4, threads=1, ce=1.0, init=tmp_path / "a.pt", save_every=2)
    ck = torch.load(tmp_path / "b.pt", weights_only=True)
    assert ck["hidden"] == 16 and ck["kind"] == "point"
    assert (tmp_path / "b-step2.pt").exists() and (tmp_path / "b-step4.pt").exists()
    m = R.load(tmp_path / "b.pt")
    assert isinstance(m, A.PointAligner)
