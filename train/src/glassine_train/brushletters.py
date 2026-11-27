"""Letter models of BRUSH's training writers (the dataset: brush.py), so text can be written
in their hands (hands.py): each writer's lines cut into letters by a point labelling and
given to the recognizer's own letter-model build.

**Why the strokes are cut first.** The build makes a character's versions from letters
written apart; a stroke shared by letters of a word is cut only by versions already made
(docs/recognizer.md, "Letters written joined"). A writer who joins most letters has too few
written apart for that to start. So each stroke is cut where its points' character changes,
and the sample says so (`cut_at_joins`, docs/samples.md): a letter's stroke that ends where
the next one's begins is the cut, not the letters touching. Runs of fewer than two points
(a label's stray point) and points of a space go with the run before them (the first run:
with the next).

**The labelling.** `labels`: BRUSH's own point labels (the dataset authors' segmentation
network, not marked by people). `aligner`: the point aligner (aligner.py) with the strokes'
characters known from the labels' spans, which places the cuts itself. On held-back lines of
the training writers the two give letter models equally close to the writers' own letters
(train/README.md, "BRUSH's hands"); the labels need no model and are the default.

**What a model lacks.** Cut joins leave no join shapes, entries or exits: hands.py draws a
joining stroke between letters at the writer's measured rate (`join_rate`). BRUSH's strokes
were put in left-to-right order, so delayed strokes are not the writer's either. A character
needs three usable samples (BRUSH has about one of each digit per writer: no digits).

Everything written here is the dataset's: letter models and the hands list stay in private
directories (0700, files 0600), never in this repository, its CI, a release or a log; the
manifest names writers by their place in the sorted list, not by their numbers.
"""

import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from . import brush

WAYS = ("labels", "aligner")


def clean_owner(o: np.ndarray, text: str) -> np.ndarray:
    """A stroke's points' characters with spaces' points and runs of fewer than two points
    given to the run before them (the first run's to the next)."""
    o = np.array([c if 0 <= c < len(text) and text[c] != " " else -1 for c in np.asarray(o).tolist()], int)
    if (o < 0).all():
        return o
    for i in range(len(o)):
        if o[i] < 0 and i and o[i - 1] >= 0:
            o[i] = o[i - 1]
    for i in range(len(o) - 1, -1, -1):
        if o[i] < 0:
            o[i] = o[i + 1]
    while True:
        cut = np.nonzero(np.diff(o))[0] + 1
        runs = np.split(np.arange(len(o)), cut)
        short = next((r for r, ix in enumerate(runs) if len(ix) < 2), None)
        if short is None or len(runs) < 2:
            return o
        o[runs[short]] = o[runs[short - 1][-1]] if short else o[runs[1][0]]


def owners(ln: brush.Line, way: str = "labels", model=None, xh: float = 0.2) -> list[np.ndarray]:
    """Per stroke its points' characters, cleaned (clean_owner), by `way`."""
    idx = brush.strokes_of(ln)
    if way == "labels":
        return [clean_owner(ln.owner[i], ln.text) for i in idx]
    if way != "aligner" or model is None:
        raise ValueError(f"way {way!r} needs an aligner" if way == "aligner" else f"unknown way {way!r}")
    from . import aligner as A, alignrun
    d = alignrun.brush_line(ln, xh, np.random.default_rng(0))
    lab = A.label_points(model, [np.asarray(q, float) for q in d["strokes"]], d["text"], d["spans"])
    return [clean_owner(o, ln.text) for o in lab]


def cut_sample(ln: brush.Line, own: list[np.ndarray]) -> dict:
    """The line as a sample (brush.to_sample) with each stroke cut where its characters
    change, `cut_at_joins` set."""
    s = brush.to_sample(ln)
    strokes, spans = [], [[] for _ in ln.text]
    for st, o in zip(s["strokes"], own):
        cut = np.nonzero(np.diff(o))[0] + 1
        for piece, oo in zip(np.split(np.asarray(st), cut), np.split(np.asarray(o), cut)):
            if not len(piece):
                continue
            if oo[0] >= 0:
                spans[int(oo[0])].append(len(strokes))
            strokes.append(piece.tolist())
    s["strokes"], s["spans"], s["cut_at_joins"] = strokes, [sorted(x) for x in spans], True
    return s


def join_rate(lines: list[brush.Line]) -> float:
    """The share of neighbouring letters of a word (no space between) that one stroke
    writes both of: the writer's pen-down joins."""
    pairs = joined = 0
    for ln in lines:
        own = [clean_owner(ln.owner[i], ln.text) for i in brush.strokes_of(ln)]
        together = set()
        for o in own:
            for a, b in zip(o[:-1], o[1:]):
                if a != b and a >= 0 and b >= 0:
                    together.add((min(a, b), max(a, b)))
        for k in range(len(ln.text) - 1):
            if ln.text[k] != " " and ln.text[k + 1] != " ":
                pairs += 1
                joined += (k, k + 1) in together
    return joined / pairs if pairs else 0.0


def _private_write(p: Path, text: str):
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def build_letters(binary: Path, models: Path, samples: list[dict], out: Path, tmp: Path | None = None) -> dict:
    """The recognizer's letter model of the samples (prompted, imported into a store of its
    own): out/model (the model file) and out/status.txt; returns {"covered": N, "chars":
    {char: (usable samples, versions)}}."""
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hw-brushletters-", dir=tmp) as t:
        os.chmod(t, 0o700)
        td = Path(t)
        env = dict(os.environ, HOME=t, XDG_DATA_HOME=str(td / "data"), XDG_CONFIG_HOME=str(td / "conf"),
                   XDG_STATE_HOME=str(td / "state"), XDG_CACHE_HOME=str(td / "cache"),
                   GLASSINE_HANDWRITING_MODELS=str(models))
        rows = [json.dumps({"format": "glassine-handwriting-export", "version": 1})]
        rows += [json.dumps({"sample": s, "client": "org.glassine.train", "confirmed": 1700000000 + i,
                             "source": "prompted"}) for i, s in enumerate(samples)]
        (td / "in.jsonl").write_text("\n".join(rows) + "\n")
        for args, limit in ((["samples", "import", str(td / "in.jsonl")], 900), (["letters", "build", "-f"], 3600)):
            p = subprocess.run([str(binary), *args], env=env, capture_output=True, text=True, timeout=limit)
            if p.returncode:
                raise RuntimeError(f"{args[0]} {args[1]}: {p.returncode} {p.stderr.strip()[-300:]}")
        st = subprocess.run([str(binary), "letters", "status"], env=env, capture_output=True, text=True,
                            timeout=600, check=True).stdout
        model = td / "data" / "glassine" / "handwriting" / "letters" / "model"
        shutil.copy(model, out / "model")
        os.chmod(out / "model", 0o600)
        _private_write(out / "status.txt", st)
    chars, covered = {}, 0
    for ln in st.splitlines():
        f = ln.split("\t")
        if f[0] == "char" and len(f) >= 4:
            chars[f[1]] = (int(f[2]), int(f[3]))
        elif f[0] == "covered":
            covered = int(f[1])
    return {"covered": covered, "chars": chars}


def _job(a):
    n, lines, binary, models, out, way, ckpt, xh, tmp = a
    model = None
    if way == "aligner":
        from .alignrun import load
        model = load(Path(ckpt))
    samples = [cut_sample(ln, owners(ln, way, model, xh)) for ln in lines]
    r = build_letters(Path(binary), Path(models), samples, Path(out) / str(n), Path(tmp) if tmp else None)
    return n, r, join_rate(lines), len(lines)


def build_all(per_writer: dict[str, list[brush.Line]], binary: Path, models: Path, out: Path, way: str = "labels",
              ckpt: Path | None = None, xh: dict | None = None, workers: int = 4, tmp: Path | None = None) -> dict:
    """A letter model per writer (out/<n>/model, n its place among the sorted writers) and
    out/hands.json: per hand its model's path, covered characters and measured join rate."""
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    names = sorted(per_writer)
    jobs = [(n, per_writer[w], str(binary), str(models), str(out), way, str(ckpt) if ckpt else None,
             (xh or {}).get(w, 0.2), str(tmp) if tmp else None) for n, w in enumerate(names)]
    hands = []
    with ProcessPoolExecutor(workers) as ex:
        for n, r, jr, nl in ex.map(_job, jobs):
            hands.append({"hand": n, "letters": str(out / str(n) / "model"), "lines": nl, "join_rate": round(jr, 4),
                          "covered": "".join(sorted(c for c, v in r["chars"].items() if v[1] > 0))})
    man = {"way": way, "hands": hands}
    _private_write(out / "hands.json", json.dumps(man, indent=1))
    return man
