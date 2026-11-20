"""Reading the BRUSH dataset (Kotani, Tellex, Tompkin, "Generating Handwriting via
Decoupled Style Descriptors", ECCV 2020) as sample-format lines, for an evaluation only.

The dataset is not part of this repository and nothing here downloads it. It comes from
the authors' page (https://github.com/brownvc/decoupled-style-descriptors, "BRUSH
dataset"), under their terms: non-commercial research only, publications cite the paper.
Nothing derived from it (ink, features, writer numbers, per-sample results, models) belongs
in this repository, its CI, a pull request, a release or a published log: point
`writerseval --out` at a private directory. Never train on it.

**The files.** One directory per writer, one file per line of writing: a Python pickle of
a list [text, drawing, labels]: `drawing` an (N, 3) array of x, y (pixels, y down) and 1
where a stroke ends; `labels` an (N, len(text)) one-hot array, the character each point
writes. Writers wrote in a box 120 pixels high and 748 wide with a suggested baseline 100
pixels from its top. The `_resample20` and `_resample25` files (respaced, without time)
are skipped. The reader takes the dataset's directory or its ZIP as downloaded, and reads
the pickles with an unpickler that builds only numpy arrays (a pickle can run code).

**As a sample.** Units `em` with the box's height as one em (so the size has no millimetres:
the recognizer takes the reference from the area's line, as for a new person). The area:
a line of the box's height (times `line_scale`), its first guide on the suggested baseline,
the box `GUIDE` of a line above the guide to three lines below it, as `realeval.ink_samples`
lays out synthetic lines. The drawing has no times; only the pause between strokes reaches
the model (featurize.c), so each stroke is timed at `RATE` per point and the pen is up
`pause` seconds between strokes (the one writer's median pause, `timing.FIT_ONE_WRITER`'s
data). Spans from the labels: a character's strokes are those holding any of its points.
"""

import importlib
import io
import pickle
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

BOX_H, BOX_W, BOX_BASE = 120.0, 748.0, 100.0  # the writing box (pixels) and its baseline
GUIDE = 2 / 3  # the area's first guide, in lines below its top (realeval.GUIDE)
RATE = 200.0  # points per second within a stroke (nothing reads it but the pause)
PAUSE = 0.11  # seconds between strokes

_ALLOWED = {("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
            ("numpy", "ndarray"), ("numpy", "dtype")}


class _Unpickler(pickle.Unpickler):
    """Only numpy arrays and their dtypes: anything else in the file is refused."""

    def find_class(self, module, name):
        if (module, name) not in _ALLOWED:
            raise pickle.UnpicklingError(f"not allowed in a BRUSH file: {module}.{name}")
        if module == "numpy.core.multiarray":  # numpy 2 moved it
            module = "numpy._core.multiarray"
        return getattr(importlib.import_module(module), name)


def loads(b: bytes):
    return _Unpickler(io.BytesIO(b)).load()


@dataclass
class Line:
    writer: str
    drawing: str
    text: str
    xy: np.ndarray  # (N, 2) pixels
    ends: np.ndarray  # (N,) bool, a stroke ends here
    owner: np.ndarray  # (N,) the character each point writes


def _parse(writer: str, drawing: str, b: bytes) -> Line | None:
    obj = loads(b)
    if not (isinstance(obj, (list, tuple)) and len(obj) == 3 and isinstance(obj[0], str)):
        return None
    text, d, lab = obj
    d, lab = np.asarray(d, float), np.asarray(lab, float)
    if d.ndim != 2 or d.shape[1] < 3 or lab.shape != (len(d), len(text)) or not len(d):
        return None
    return Line(writer, drawing, text, d[:, :2], d[:, 2] > 0.5, lab.argmax(1))


def _member(name: str) -> tuple[str, str] | None:
    """(writer, drawing) of a dataset file's path, None for everything else."""
    parts = name.replace("\\", "/").strip("/").split("/")
    if len(parts) < 2 or "__MACOSX" in parts or any(p.startswith(".") for p in parts):
        return None
    w, f = parts[-2], parts[-1]
    if "_resample" in f or not w.isdigit() or not f.isdigit():
        return None
    return w, f


def read(path: Path, writers: set[str] | None = None):
    """Every line of the dataset (a directory or the ZIP), as `Line`s, in path order."""
    path = Path(path)
    if path.is_file():
        with zipfile.ZipFile(path) as z:
            for name in sorted(z.namelist()):
                m = _member(name)
                if m and (writers is None or m[0] in writers):
                    ln = _parse(*m, z.read(name))
                    if ln:
                        yield ln
        return
    for f in sorted(path.rglob("*")):
        m = _member(str(f.relative_to(path))) if f.is_file() else None
        if m and (writers is None or m[0] in writers):
            ln = _parse(*m, f.read_bytes())
            if ln:
                yield ln


def index(path: Path) -> dict[str, list[str]]:
    """{writer: [drawing, ...]} without reading the files."""
    path = Path(path)
    names = zipfile.ZipFile(path).namelist() if path.is_file() else \
        [str(f.relative_to(path)) for f in path.rglob("*") if f.is_file()]
    out: dict[str, list[str]] = {}
    for n in sorted(names):
        m = _member(n)
        if m:
            out.setdefault(m[0], []).append(m[1])
    return out


def strokes_of(ln: Line) -> list[np.ndarray]:
    """Point indices per stroke."""
    cut = np.nonzero(ln.ends)[0] + 1
    return [s for s in np.split(np.arange(len(ln.xy)), cut) if len(s)]


def densify(q: np.ndarray, step: float) -> np.ndarray:
    """A stroke's points with points added along a Catmull-Rom curve so none is more than
    `step` from the next (the dataset's points are sparse)."""
    if len(q) < 2:
        return q
    p = np.vstack([q[:1], q, q[-1:]])
    out = [q[:1]]
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        n = max(1, int(np.ceil(np.linalg.norm(p2 - p1) / step)))
        u = (np.arange(1, n + 1) / n)[:, None]
        seg = 0.5 * (2 * p1 + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u ** 2
                     + (-p0 + 3 * p1 - 3 * p2 + p3) * u ** 3)
        out.append(seg)
    return np.vstack(out)


def to_sample(ln: Line, line_scale: float = 1.0, pause: float = PAUSE, step: float = 0.0) -> dict:
    """The line as a sample-format prompted sample with exact spans (module docstring).
    `step` > 0: points densified to at most that many pixels apart."""
    em = BOX_H
    strokes, spans = [], [[] for _ in ln.text]
    t = 0.0
    for j, idx in enumerate(strokes_of(ln)):
        for c in set(ln.owner[idx].tolist()):
            spans[c].append(j)
        q = ln.xy[idx]
        if step > 0:
            q = densify(q, step)
        ts = t + np.arange(len(q)) / RATE
        strokes.append(np.c_[np.round(q / em, 5), np.round(ts, 4)].tolist())
        t = ts[-1] + pause
    for c, ch in enumerate(ln.text):
        if ch == " ":
            spans[c] = []
    line = BOX_H * line_scale / em
    base = BOX_BASE / em
    x1 = max(BOX_W, float(ln.xy[:, 0].max()) + BOX_H) / em
    y0 = base - GUIDE * line
    return {"version": 1, "text": ln.text, "lang": "en", "source": "prompted", "spans_source": "exact",
            "units": {"xy": "em"}, "strokes": strokes, "spans": [sorted(s) for s in spans],
            "area": {"box": [0.0, round(y0, 6), round(x1, 5), round(y0 + 4 * line, 6)], "line": round(line, 6),
                     "orientation": 0}}


def stroke_traits(ln: Line) -> dict:
    """What a line's ink does, from the point labels: characters written in a stroke that
    also writes another character (joined), and strokes written after a later character of
    their word had ink (delayed)."""
    st = strokes_of(ln)
    owners = [set(ln.owner[i].tolist()) for i in st]
    word = np.cumsum([c == " " for c in ln.text])
    joined = set()
    for o in owners:
        letters = {c for c in o if ln.text[c] != " "}
        if len(letters) > 1:
            joined |= letters
    delayed = 0
    seen_max: dict[int, int] = {}
    for o in owners:
        lo = min(o)
        w = int(word[lo])
        if seen_max.get(w, -1) > lo:
            delayed += 1
        for c in o:
            seen_max[int(word[c])] = max(seen_max.get(int(word[c]), -1), c)
    letters = sum(c != " " for c in ln.text)
    return {"letters": letters, "joined": len(joined), "delayed": delayed, "strokes": len(st)}
