"""The many-writers evaluation's reading of datasets, on made-up ink only: no file of any
real dataset is used in tests (the BRUSH files are written here in their format)."""

import os
import pickle
import zipfile

import numpy as np
import pytest

from glassine_train import brush
from glassine_train import writerseval as W


def _drawing(text, joined=(), late_dot=None):
    """A made-up line in the BRUSH layout: one stroke per character (a short wave), the
    characters in `joined` (index pairs) written in one stroke, and optionally a dot for
    character `late_dot` written last."""
    pts, lab, x = [], [], 10.0
    groups, i = [], 0
    while i < len(text):
        if text[i] == " ":
            i += 1
            x += 30
            continue
        g = [i]
        while any(p == (g[-1], g[-1] + 1) for p in joined):
            g.append(g[-1] + 1)
        groups.append((g, x))
        x += 22 * len(g)
        i = g[-1] + 1
    for g, x0 in groups:
        n = 6 * len(g)
        xs = x0 + np.linspace(0, 20 * len(g), n)
        ys = 100 - 20 * np.abs(np.sin(np.linspace(0, np.pi * len(g), n)))
        for k in range(n):
            pts.append([xs[k], ys[k], 1.0 if k == n - 1 else 0.0])
            lab.append(g[min(k // 6, len(g) - 1)])
    if late_dot is not None:
        pts += [[40.0, 60.0, 0.0], [41.0, 60.5, 1.0]]
        lab += [late_dot, late_dot]
    d = np.array(pts)
    oh = np.zeros((len(d), len(text)))
    oh[np.arange(len(d)), lab] = 1
    return [text, d, oh]


def _write(root, files):
    for (w, f), obj in files.items():
        p = root / "BRUSH" / w
        p.mkdir(parents=True, exist_ok=True)
        (p / f).write_bytes(pickle.dumps(obj))


FILES = {("1", "1"): _drawing("ab cd"), ("1", "2"): _drawing("hello", joined=[(0, 1), (1, 2)]),
         ("2", "1"): _drawing("in it", late_dot=0), ("2", "7"): _drawing("x")}


def test_reads_a_directory_and_a_zip_alike(tmp_path):
    _write(tmp_path, FILES)
    (tmp_path / "BRUSH" / "1" / "1_resample20").write_bytes(pickle.dumps(_drawing("zz")))
    (tmp_path / "BRUSH" / ".DS_Store").write_bytes(b"\0\0")
    z = tmp_path / "BRUSH.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in sorted((tmp_path / "BRUSH").rglob("*")):
            if f.is_file():
                zf.write(f, str(f.relative_to(tmp_path)))
        zf.writestr("__MACOSX/BRUSH/1/._1", b"\0")
    a = [(ln.writer, ln.drawing, ln.text) for ln in brush.read(tmp_path / "BRUSH")]
    b = [(ln.writer, ln.drawing, ln.text) for ln in brush.read(z)]
    assert a == b == [("1", "1", "ab cd"), ("1", "2", "hello"), ("2", "1", "in it"), ("2", "7", "x")]
    assert brush.index(z) == {"1": ["1", "2"], "2": ["1", "7"]}


def test_refuses_anything_but_arrays(tmp_path):
    class Boom:
        def __reduce__(self):
            return (os.system, ("true",))
    with pytest.raises(pickle.UnpicklingError):
        brush.loads(pickle.dumps(["x", Boom(), None]))


def test_sample_spans_area_and_traits():
    ln = brush._parse("1", "2", pickle.dumps(_drawing("hello", joined=[(0, 1), (1, 2)])))
    s = brush.to_sample(ln)
    assert s["version"] == 1 and s["units"] == {"xy": "em"} and s["spans_source"] == "exact"
    assert len(s["strokes"]) == 3
    assert s["spans"] == [[0], [0], [0], [1], [2]]
    # one stroke's points in order, the pause between strokes
    t = [q[0][2] for q in s["strokes"]]
    assert all(b > a for a, b in zip(t, t[1:]))
    a = s["area"]
    assert a["line"] == pytest.approx(1.0)
    assert a["box"][1] + brush.GUIDE * a["line"] == pytest.approx(brush.BOX_BASE / brush.BOX_H)
    assert brush.stroke_traits(ln) == {"letters": 5, "joined": 3, "delayed": 0, "strokes": 3}
    late = brush._parse("2", "1", pickle.dumps(_drawing("in it", late_dot=0)))
    tr = brush.stroke_traits(late)
    assert tr["delayed"] == 1 and tr["joined"] == 0
    assert brush.to_sample(late)["spans"][2] == []
    s2 = brush.to_sample(ln, line_scale=2.0, step=2.0)
    assert s2["area"]["line"] == pytest.approx(2.0)
    assert sum(map(len, s2["strokes"])) > sum(map(len, s["strokes"]))


def test_densify_keeps_the_points():
    q = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]])
    d = brush.densify(q, 1.0)
    assert np.allclose(d[0], q[0]) and np.allclose(d[-1], q[-1])
    assert len(d) > 15 and np.linalg.norm(np.diff(d, axis=0), axis=1).max() <= 1.5


def test_load_draws_per_writer_and_keeps_the_charset(tmp_path):
    _write(tmp_path, {**FILES, ("2", "9"): _drawing("a<b")})
    data = W.load_brush(tmp_path / "BRUSH", 2, 0, W.COMMON, {})
    assert sorted(data) == ["1", "2"]
    assert all(len(v) == 2 for v in data.values())
    assert all("<" not in s["text"] for v in data.values() for s in v)
    again = W.load_brush(tmp_path / "BRUSH", 2, 0, W.COMMON, {})
    assert [s["text"] for s in data["2"]] == [s["text"] for s in again["2"]]


def test_error_types():
    e = W.error_types([{"text": "Ab c1.", "cands": ["ab c7"]}, {"text": "x y", "cands": ["xy"]}])
    assert e["edits"] == 4
    assert e["rate"]["capital"] == 1.0 and e["rate"]["digit"] == 1.0 and e["rate"]["punct"] == 1.0
    assert e["rate"]["space"] == pytest.approx(0.5) and e["share"]["space"] == pytest.approx(0.25)
    assert e["case_only_share"] == pytest.approx(0.25)
    assert W.ops("abc", "abc") == []
