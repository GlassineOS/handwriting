"""Data, training and measures for the point-labelling aligner (aligner.py; a prototype,
not wired into the product).

- `aligndata`: synthetic lines with each point's character (gen.Generator.line(points=True)),
  writers drawn per line with a high chance of pen-down joins inside words; for the test
  fonts also one writer per font, each with lines written apart (for a letter model of that
  writer, as a person's letters) and joined test lines.
- `aligntrain`: the chain loss (aligner.chain_loss) on the training set.
- `alignmeasure`: on joined test ink, where each join between two letters of one stroke is
  cut: the distance along the stroke from the cut to the join (x-heights; 0 inside the join),
  and the share of letters left without ink in a stroke that holds them. Methods: the
  aligner (text alone, and with the stroke-level spans), the recognizer's alignment
  (`align`), and point labels given from elsewhere (a file).

Synthetic ink only: no person's ink in this module's sets.
"""

import json
import multiprocessing as mp
import pickle
import time
from pathlib import Path

import numpy as np
import torch

from . import aligner as A

_G = None


def _setup(fonts: list[str], workers: int, names="build/names", gclm="build/release/glassine-handwriting-chars-en-1/chars.gclm",
           wordlist="build/lexicon/en/words.txt", given="ssa", held=False, joins=None, bar_chars=None):
    from . import build4, gen, lines as L
    from .cli import font_splits
    from .timing import FIT_ONE_WRITER, Fit
    L.fetch_names(Path(names), given)
    lg = L.Generator(Path("build/text"), Path(gclm), Path(names), Path(wordlist), held=held, given=given)
    sp = font_splits()
    tabs, covers = build4.font_tables(sp["train"] + sp["dev"] + sp["test"], Path("build/cache"), workers)
    by = dict(tabs)
    pool, open0, _ = build4.style_pool([(f, by[f]) for f in sp["train"]], build4.bigrams(lg.train_s), [], gen.CALIB)
    g = gen.Generator(gen.FontWriter([(f, by[f]) for f in fonts], covers), Fit(**FIT_ONE_WRITER), pool, open0,
                      joins=joins, bar_chars=bar_chars or gen.BAR_CHARS)
    return g, lg, pool


def _style(rng, pool, join=(0.5, 1.0), real=False):
    """A writer drawn for aligner data: joins inside words likelier than the generator's; with
    real, also the ways real ink differs from straight bridges and tidy bars (REAL)."""
    from . import gen
    st = gen.draw_style(rng, pool)
    st.join = float(rng.uniform(*join))
    if real:
        for k, (lo, hi) in REAL.items():
            setattr(st, k, float(rng.uniform(lo, hi)))
    return st


# the "real" writers (aligner data 2): curved joins (a learned join shape where one fits,
# else a drawn bow), bars delayed more often and reaching over the next letter or running
# on into it pen down, delayed strokes after a later letter or after the next word is begun
REAL = {"curve": (0.5, 1.0), "bar_delay": (0.1, 0.7), "mid": (0.03, 0.3), "reach": (0.0, 0.8),
        "bar_lead": (0.0, 0.5), "late_word": (0.0, 0.2)}


def _pack(smp: dict) -> dict:
    return {"text": smp["text"], "strokes": [q.astype(np.float32) for q in smp["strokes"]], "spans": smp["spans"],
            "owner": [o.astype(np.int16) for o in smp["point_owner"]], "xh": smp["xh"], "line": smp["line"]}


def _render(job):
    t, seed, mine = job
    g, pool, real, lw = _G
    rng = np.random.default_rng(seed)
    for _ in range(3):
        try:
            if mine and lw is not None and lw.covers(t):
                st = lw.style(rng)
                st.join = float(rng.uniform(0.5, 1.0))
                for k, (lo, hi) in REAL.items():
                    setattr(st, k, float(rng.uniform(lo, hi)))
                return _pack(g.line(t, rng, st, letters=lw, points=True))
            return _pack(g.line(t, rng, _style(rng, pool, real=real), points=True))
        except ValueError:
            continue
    return None


def render(texts: list[str], g, pool, seed: int, workers: int, real: bool = False, letters=None,
           share: float = 0.0) -> list[dict]:
    """letters: a LetterWriter that writes about `share` of the lines (those it covers; the
    others in the fonts)."""
    global _G
    _G = (g, pool, real, letters)
    r = np.random.default_rng(seed)
    seeds = r.integers(0, 2**62, len(texts)).tolist()
    mine = (r.random(len(texts)) < share).tolist() if letters is not None else [False] * len(texts)
    with mp.get_context("fork").Pool(workers) as p:
        return [s for s in p.imap(_render, list(zip(texts, seeds, mine)), chunksize=32) if s is not None]


TEXT_SHARES = {"prose": 0.6, "words": 0.25, "pseudo": 0.15}


def make_sets(out: Path, n_train: int, n_dev: int, test_lines: int, letter_lines: int, workers: int, seed: int = 71,
              real: bool = False, letters: Path | None = None, share: float = 0.0):
    """train (training fonts), dev (dev fonts), and per test font one writer: its letter
    lines (no joins, no delayed strokes) and its joined test lines. real: the writers of
    REAL (curved joins, bars and dots as people write them, the 5's roof a bar); letters: a
    letter model file (docs/recognizer.md) whose learned join shapes the curved joins take
    and whose versions write `share` of the training lines (its person's ink: keep the sets
    as private as the model)."""
    from . import gen
    from .cli import font_splits
    out.mkdir(parents=True, exist_ok=True)
    sp = font_splits()
    joins = lw = None
    if letters is not None:
        rows = [json.loads(ln) for ln in Path(letters).read_text(encoding="utf-8").splitlines() if ln.strip()]
        joins = gen.JoinShapes.from_rows(rows)
        lw = gen.LetterWriter(rows, "person")
    bars = gen.BAR_CHARS + "5" if real else None
    for name, fonts, n, s in (("train", sp["train"], n_train, seed), ("dev", sp["dev"], n_dev, seed + 1)):
        f = out / f"{name}.pkl"
        if f.exists():
            continue
        g, lg, pool = _setup(fonts, workers, joins=joins, bar_chars=bars)
        texts = [t for t, _ in lg.lines(n, s, TEXT_SHARES)]
        t0 = time.time()
        f.write_bytes(pickle.dumps(render(texts, g, pool, s + 100, workers, real, lw if name == "train" else None, share)))
        print(f"{name}: {n} lines, {time.time() - t0:.0f} s", flush=True)
    f = out / "test.pkl"
    if f.exists():
        return
    test = {}
    fonts = sp["test"]
    g, lg, pool = _setup(fonts, workers, held=True, joins=joins, bar_chars=bars)
    rng = np.random.default_rng(seed + 2)
    alpha = "abcdefghijklmnopqrstuvwxyz"
    tables = dict(g.fonts.tables)
    covers = dict(zip([f for f, _ in g.fonts.tables], g.fonts.covers))
    for fi, font in enumerate(fonts):
        gf = gen.Generator(gen.FontWriter([(font, tables[font])], {font: covers[font]}), g.timing.f, pool, g.open0,
                           joins=joins, bar_chars=bars or gen.BAR_CHARS)
        st = _style(rng, pool, real=real)
        st.one_font = True
        plain = gen.Style(**{**st.__dict__, "join": 0.0, "dot_delay": 0.0, "bar_delay": 0.0, "reach": 0.0,
                             "bar_lead": 0.0, "late_word": 0.0})
        texts = [t for t, _ in lg.lines(test_lines, seed + 10 + fi, {"prose": 0.5, "words": 0.5})]
        # the letter lines: every small letter, capital and digit at least four times, then words
        chars = list(alpha + alpha.upper() + "0123456789") * 4
        rng.shuffle(chars)
        lt = ["".join(chars[i:i + 5]) for i in range(0, len(chars), 5)]
        lt += [t for t, _ in lg.lines(max(0, letter_lines - len(lt)), seed + 50 + fi, {"words": 1.0})]
        r = np.random.default_rng(seed + 200 + fi)
        test[font] = {"letters": [_pack(gf.line(t, r, plain, points=True)) for t in lt],
                      "test": [_pack(gf.line(t, r, st, points=True)) for t in texts]}
        print(f"test {font}: {len(test[font]['letters'])} letter lines, {len(test[font]['test'])} test lines", flush=True)
    f.write_bytes(pickle.dumps(test))


# ---------------------------------------------------------------------------- joined fonts

JOINED = Path("../synth/joined.toml")
JOINED_FONTS = Path("../synth/build/fonts-joined")
JOINED_WGHT = 150.0  # the fonts' weight: thin enough that the skeleton keeps loops and counters


def joined_splits(manifest: Path = JOINED) -> dict[str, list[str]]:
    """The joined fonts (joined.toml) by split: train, dev, test; print fonts left out."""
    import tomllib
    out = {"train": [], "dev": [], "test": []}
    for f in tomllib.loads(manifest.read_text())["family"]:
        if f.get("style") != "print":
            out[f.get("split", "train")].append(f["dir"])
    return out


def _joined_job(job):
    font, texts, seed, per_line = job
    from . import joined as J
    fonts = J.load_fonts(JOINED_FONTS, [font], JOINED_WGHT)
    g = J.JoinedGenerator(fonts)
    rng = np.random.default_rng(seed)
    w = J.draw_writer(rng, font)
    out = []
    for t in texts:
        for _ in range(3):
            try:
                out.append(_pack(g.line(t, rng, J.draw_writer(rng, font) if per_line else w)))
                break
            except ValueError:
                continue
    return font, out


def make_joined(out: Path, n_train: int, n_dev: int, test_lines: int, workers: int, seed: int = 91):
    """joined-train, joined-dev (lines of the train and dev fonts, a writer drawn per line) and
    joined-test (one writer per test font, its lines prose and words) from the joined fonts."""
    from . import lines as L
    out.mkdir(parents=True, exist_ok=True)
    sp = joined_splits()
    L.fetch_names(Path("build/names"), "ssa")
    for name, n, s in (("train", n_train, seed), ("dev", n_dev, seed + 1), ("test", test_lines, seed + 2)):
        f = out / f"joined-{name}.pkl"
        if f.exists() or not n:
            continue
        lg = L.Generator(Path("build/text"), Path("build/release/glassine-handwriting-chars-en-1/chars.gclm"),
                         Path("build/names"), Path("build/lexicon/en/words.txt"), held=name == "test", given="ssa")
        fonts = sp[name]
        rng = np.random.default_rng(s)
        jobs = []
        if name == "test":
            for i, font in enumerate(fonts):
                texts = [t for t, _ in lg.lines(n, s + 10 + i, {"prose": 0.5, "words": 0.5})]
                jobs.append((font, texts, s + 100 + i, False))
        else:
            texts = [t for t, _ in lg.lines(n, s, TEXT_SHARES)]
            pick = rng.integers(0, len(fonts), len(texts))
            for i, font in enumerate(fonts):
                jobs.append((font, [t for t, k in zip(texts, pick) if k == i], s + 100 + i, True))
        t0 = time.time()
        with mp.get_context("fork").Pool(min(workers, len(jobs))) as p:
            res = p.map(_joined_job, jobs, chunksize=1)
        if name == "test":
            data = {font: {"letters": [], "test": lines} for font, lines in res}
        else:
            data = [x for _, lines in res for x in lines]
            np.random.default_rng(s + 7).shuffle(data)
        f.write_bytes(pickle.dumps(data))
        print(f"joined {name}: {sum(len(v) for _, v in res)} lines, {time.time() - t0:.0f} s", flush=True)


def mix(out: Path, parts: list[tuple[Path, Path]]):
    """A training directory of several: train.pkl and dev.pkl, each the parts' lists together,
    shuffled (training reads the first 400 dev lines)."""
    out.mkdir(parents=True, exist_ok=True)
    for name, k in (("train", 0), ("dev", 1)):
        data = []
        for p in parts:
            data += pickle.loads(p[k].read_bytes())
        order = np.random.default_rng(5).permutation(len(data))
        data = [data[i] for i in order]
        (out / f"{name}.pkl").write_bytes(pickle.dumps(data))
        print(f"{name}: {len(data)} lines", flush=True)


# ---------------------------------------------------------------------------- training

def prepare(lines: list[dict], kind: str = "point") -> list[tuple[np.ndarray, list[int], np.ndarray]]:
    """(features, class labels of the writing-order target, each frame's class or -1 on a
    join) per line; kind "point" the engine-shaped aligner's rows (A.PointAligner), else the
    prototype's frames."""
    out = []
    for s in lines:
        st = [np.asarray(q, float) for q in s["strokes"]]
        if kind == "point":
            x, maps = A.featurize_rows(st)
            T = (len(x) - 1) // 2 + 1 if len(x) else 0
        else:
            x, maps, _ = A.featurize(st)
            T = len(x)
        if not T:
            continue
        fc = A.frame_truth(maps, [np.asarray(o, int) for o in s["owner"]], T)
        tgt = A.target_of(fc)
        cls = A.class_of(s["text"], tgt)
        if not tgt or A.BLANK in cls or len(A.chain(cls)[0]) > T:
            continue
        fcls = np.array([A.LETTERS.find(s["text"][c]) if c >= 0 else -1 for c in fc])
        out.append((x, cls, fcls))
    return out


def train(data: Path, out: Path, steps: int, batch: int = 24, lr: float = 2e-3, hidden: int = 128,
          threads: int = 8, seed: int = 0, minutes: float = 1e9, ce: float = 0.0, kind: str = "point"):
    """The chain loss per frame; ce > 0 adds that weight of a frame-level cross-entropy on
    the generator's own point labels (joins left out), which the chain loss alone doesn't
    need but which keeps the alignment from settling where the likelihood is as good."""
    torch.set_num_threads(threads)
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    tr = prepare(pickle.loads((data / "train.pkl").read_bytes()), kind)
    dv = prepare(pickle.loads((data / "dev.pkl").read_bytes()), kind)[:400]
    print(f"train {len(tr)} lines, dev {len(dv)}", flush=True)
    m = A.PointAligner(hidden) if kind == "point" else A.Aligner(hidden)
    opt = torch.optim.AdamW(m.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps, pct_start=0.1)
    order = np.argsort([len(x) for x, *_ in tr])
    buckets = [order[i:i + batch] for i in range(0, len(order), batch)]

    def run(items):
        T = max(len(x) for x, *_ in items)
        X = torch.zeros(len(items), T, items[0][0].shape[1])
        Ty = max(len(y) for *_, y in items)
        Y = torch.full((len(items), Ty), -1, dtype=torch.long)
        for b, (x, _, y) in enumerate(items):
            X[b, :len(x)] = torch.from_numpy(x)
            Y[b, :len(y)] = torch.from_numpy(y)
        if kind == "point":
            lp = m(X, torch.tensor([len(x) for x, *_ in items]))
        else:
            lp = m(X)
        lens = torch.tensor([len(y) for *_, y in items])
        loss = A.chain_loss(lp, lens, [c for _, c, _ in items]) / lens.sum()
        if ce > 0:
            loss = loss + ce * torch.nn.functional.nll_loss(lp.reshape(-1, A.NCLASS), Y.reshape(-1), ignore_index=-1)
        return loss

    best = float("inf")
    t0 = time.time()
    for step in range(steps):
        m.train()
        idx = buckets[int(rng.integers(len(buckets)))]
        loss = run([tr[i] for i in idx])
        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(m.parameters(), 1.0)
        opt.step()
        sched.step()
        if (step + 1) % 250 == 0 or step + 1 == steps:
            m.eval()
            with torch.no_grad():
                dl = float(np.mean([run(dv[i:i + 32]).item() for i in range(0, len(dv), 32)]))
            print(f"step {step + 1} loss {loss.item():.4f} dev {dl:.4f} {time.time() - t0:.0f} s", flush=True)
            if dl < best:
                best = dl
                out.parent.mkdir(parents=True, exist_ok=True)
                torch.save({"state": m.state_dict(), "hidden": hidden, "step": step + 1, "dev": dl, "kind": kind}, out)
        if time.time() - t0 > minutes * 60:
            break


def load(path: Path):
    """A checkpoint: the engine-shaped aligner, or the prototype (no "kind")."""
    ck = torch.load(path, weights_only=True)
    m = A.PointAligner(ck["hidden"]) if ck.get("kind") == "point" else A.Aligner(ck["hidden"])
    m.load_state_dict(ck["state"])
    return m.eval()


def export_ghm(ckpt: Path, out: Path) -> Path:
    """The engine-shaped aligner as a .ghm (the base model format, recognizer/tools/onnx2ghm.py:
    class 0 the blank, then LETTERS), for recognizer/src/hw/aligner.c."""
    import subprocess
    import sys
    import tempfile
    from .model import Single
    m = load(ckpt)
    assert isinstance(m, A.PointAligner)
    tool = Path(__file__).resolve().parents[3] / "recognizer" / "tools" / "onnx2ghm.py"
    with tempfile.TemporaryDirectory() as t:
        onnx = Path(t) / "aligner.onnx"
        x0 = torch.zeros(1, 200, m.m.c1.in_channels)
        torch.onnx.export(Single(m.m).eval(), (x0,), onnx, input_names=["ink"], output_names=["logp"],
                          dynamic_axes={"ink": {1: "points"}, "logp": {1: "frames"}}, opset_version=17, dynamo=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(tool), str(onnx), "--charset", A.LETTERS, "--kind", "aligner", "-o", str(out)],
                       check=True, stdout=subprocess.DEVNULL)
    return out


# ---------------------------------------------------------------------------- measures

def joins_of(owner: np.ndarray) -> list[tuple[int, int, int, int]]:
    """The joins between letters in one stroke's true point labels: (last point of the
    letter before, first point of the letter after, letter before, letter after)."""
    out = []
    last = None  # (index, char) of the last labelled point
    for i, c in enumerate(owner):
        if c < 0:
            continue
        if last is not None and c != last[1]:
            out.append((last[0], i, int(last[1]), int(c)))
        last = (i, c)
    return out


def cut_errors(stroke: np.ndarray, owner: np.ndarray, pred: np.ndarray, xh: float) -> list[float | None]:
    """Per true join of the stroke: the distance along the stroke (x-heights) from the
    predicted cut to the join (0 when it falls within it), None when the prediction gives
    one of the two letters no point of this stroke."""
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(stroke[:, :2], axis=0), axis=1))]
    out = []
    for a, b, c0, c1 in joins_of(owner):
        p0 = np.nonzero(pred == c0)[0]
        p1 = np.nonzero(pred == c1)[0]
        if not len(p0) or not len(p1):
            out.append(None)
            continue
        # the predicted cut: between the last point of c0 before the first of c1 and that one
        j1 = int(p1[0])
        before = p0[p0 < j1]
        j0 = int(before[-1]) if len(before) else int(p0[0])
        cut = (d[j0] + d[j1]) / 2
        lo, hi = d[a], d[b]
        out.append(float(max(0.0, lo - cut, cut - hi) / xh))
    return out


def summarise(errs: list[float | None]) -> dict:
    v = np.array([e for e in errs if e is not None])
    n = len(errs)
    return {"joins": n, "letter_lost": (n - len(v)) / max(n, 1),
            "median": float(np.median(v)) if len(v) else float("nan"),
            "mean": float(v.mean()) if len(v) else float("nan"),
            "within_join": float((v == 0).mean()) if len(v) else float("nan"),
            "within_0.25": float((v <= 0.25).mean()) if len(v) else float("nan"),
            "within_0.5": float((v <= 0.5).mean()) if len(v) else float("nan"),
            # every join, a lost letter counting as a miss
            "all_within_0.25": float(np.sum(v <= 0.25) / max(n, 1))}


def points_from_align(segments: list[str], strokes: list[np.ndarray]) -> list[np.ndarray]:
    """Point labels from the recognizer's ALIGN/SEGMENTS (one entry per character: `N` the
    whole stroke or `N:A-B` points A to B)."""
    out = [np.full(len(q), -1, int) for q in strokes]
    for c, seg in enumerate(segments):
        if not seg or seg == "-":
            continue
        for part in seg.split(","):
            if ":" in part:
                n, r = part.split(":")
                a, b = map(int, r.split("-"))
                out[int(n)][a:b + 1] = c
            elif part:
                out[int(part)][:] = c
    return out


def to_sample(s: dict, xh_em: float = 0.1) -> dict:
    """A synthetic line as a prompted sample (as realeval.ink_samples lays it out)."""
    k = xh_em / s["xh"]
    line = s["line"] * k
    pts = np.concatenate([q for q in s["strokes"] if len(q)])
    x0, x1 = float(pts[:, 0].min()) * k - line, float(pts[:, 0].max()) * k + line
    y0 = -2 / 3 * line
    return {"version": 1, "text": s["text"], "lang": "en", "source": "prompted", "spans_source": "exact",
            "units": {"xy": "em"}, "spans": s["spans"],
            "area": {"box": [round(x0, 5), round(y0, 6), round(x1, 5), round(y0 + 4 * line, 6)], "line": round(line, 6)},
            "strokes": [np.c_[np.round(np.asarray(q[:, :2], float) * k, 6), np.round(np.asarray(q[:, 2], float), 4)].tolist()
                        for q in s["strokes"]]}


def delayed_line(owners: list[np.ndarray]) -> bool:
    """A stroke of a character written after a later character's ink (a late dot or bar)."""
    seen = -1
    for o in owners:
        o = [int(c) for c in o if c >= 0]
        if o and min(o) < seen:
            return True
        if o:
            seen = max(seen, max(o))
    return False


def ownership(strokes: list[np.ndarray], truth: list[np.ndarray], pred: list[np.ndarray], acc: dict):
    """Whole-line ownership into acc: points (labelled by the truth) given their own
    character, and delayed strokes (all of one character, written after a later character's
    ink: a late dot or bar) whose points at least nine in ten go to it."""
    seen = -1
    for q, o, p in zip(strokes, truth, pred):
        o = np.asarray(o, int)
        p = np.asarray(p, int)
        lab = o >= 0
        acc["points"] = acc.get("points", 0) + int(lab.sum())
        acc["points_right"] = acc.get("points_right", 0) + int((p[lab] == o[lab]).sum())
        own = set(o[lab].tolist())
        if len(own) == 1 and seen > next(iter(own)):
            acc["delayed"] = acc.get("delayed", 0) + 1
            acc["delayed_right"] = acc.get("delayed_right", 0) + int(np.mean(p[lab] == o[lab]) >= 0.9)
        if own:
            seen = max(seen, max(own))


def measure_synthetic(test: dict, model, other: dict | None = None, delay: float | None = None) -> dict:
    """Cut errors per method over the joins of the test lines (all lines, and those without
    delayed strokes), and on the strokes the shape cut changed (`shape_text` against
    `pre_text`, when given). `other`: {writer: {"lines": [{method: per-stroke labels}]}}."""
    errs: dict[str, dict[str, list]] = {}
    owns: dict[str, dict] = {}
    cut_strokes = 0
    strokes_with_joins = 0
    for font, w in test.items():
        o_lines = (other or {}).get(font, {}).get("lines", [{}] * len(w["test"]))
        for s, ol in zip(w["test"], o_lines):
            st = [np.asarray(q, float) for q in s["strokes"]]
            truth = [np.asarray(o, int) for o in s["owner"]]
            methods = dict(ol)
            if model is not None:
                methods["aligner_text"] = A.label_points(model, st, s["text"])
                methods["aligner_spans"] = A.label_points(model, st, s["text"], s["spans"])
                if delay is not None:
                    methods["aligner_text_delay"] = A.label_points(model, st, s["text"], delay=delay)
            for name, lab in methods.items():
                if lab is not None and not name.startswith("pre_"):
                    ownership(st, truth, lab, owns.setdefault(name, {}))
            late = delayed_line(truth)
            for j, (q, o) in enumerate(zip(st, truth)):
                if not joins_of(o):
                    continue
                strokes_with_joins += 1
                changed = "pre_text" in methods and methods.get("shape_text") is not None and \
                    not np.array_equal(methods["shape_text"][j], methods["pre_text"][j])
                cut_strokes += changed
                for name, lab in methods.items():
                    if lab is None or name.startswith("pre_"):
                        continue
                    e = cut_errors(q, o, np.asarray(lab[j]), s["xh"])
                    errs.setdefault(name, {}).setdefault("all", []).extend(e)
                    if not late:
                        errs[name].setdefault("no delayed strokes", []).extend(e)
                    if changed:
                        errs[name].setdefault("strokes the shape cut changed", []).extend(e)
    out = {name: {k: summarise(v) for k, v in d.items()} for name, d in errs.items()}
    for name, a in owns.items():
        out.setdefault(name, {})["ownership"] = {
            "points_right": a.get("points_right", 0) / max(a.get("points", 0), 1),
            "delayed": a.get("delayed", 0), "delayed_right": a.get("delayed_right", 0) / max(a.get("delayed", 0), 1)}
    out["_strokes"] = {"with joins": strokes_with_joins, "shape cut changed": cut_strokes}
    return out


def measure_spans(samples: list[dict], labels: dict[str, list]) -> dict:
    """Agreement with stroke-level spans (real prompted ink with exact spans: the only truth
    it has): per method, the share of strokes whose letters (characters with a point in the
    stroke) are exactly the span's, over all strokes and over the strokes the spans share
    among several letters, and the span letters left without a point in such strokes."""
    out = {}
    for name, per in labels.items():
        n = agree = nsh = agree_sh = lost = letters_sh = extra = 0
        for s, lab in zip(samples, per):
            if lab is None or s.get("spans_source") != "exact":
                continue
            want = {}
            for c, sp in enumerate(s["spans"]):
                if s["text"][c] != " ":
                    for j in sp:
                        want.setdefault(j, set()).add(c)
            for j, w in want.items():
                got = {int(c) for c in np.asarray(lab[j]) if c >= 0 and s["text"][int(c)] != " "}
                n += 1
                agree += got == w
                if len(w) > 1:
                    nsh += 1
                    agree_sh += got == w
                    lost += len(w - got)
                    letters_sh += len(w)
                    extra += len(got - w)
        out[name] = {"strokes": n, "agree": agree / max(n, 1), "shared_strokes": nsh,
                     "agree_shared": agree_sh / max(nsh, 1), "letters_lost_shared": lost / max(letters_sh, 1),
                     "extra_letters_per_shared_stroke": extra / max(nsh, 1)}
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="glassine-train-align")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("data")
    d.add_argument("--out", default="build/align")
    d.add_argument("--train", type=int, default=20000)
    d.add_argument("--dev", type=int, default=600)
    d.add_argument("--test-lines", type=int, default=40)
    d.add_argument("--letter-lines", type=int, default=60)
    d.add_argument("--workers", type=int, default=12)
    d.add_argument("--real", action="store_true", help="the writers of REAL (curved joins, bars and dots as written)")
    d.add_argument("--letters", type=Path, help="a letter model file: its join shapes, and its versions write --share of train")
    d.add_argument("--share", type=float, default=0.0)
    j = sub.add_parser("joined", help="lines of the joined fonts (joined.toml), with their point labels")
    j.add_argument("--out", default="build/align-joined")
    j.add_argument("--train", type=int, default=12000)
    j.add_argument("--dev", type=int, default=300)
    j.add_argument("--test-lines", type=int, default=40)
    j.add_argument("--workers", type=int, default=12)
    x = sub.add_parser("mix", help="one training directory from several: TRAIN.pkl:DEV.pkl ...")
    x.add_argument("out", type=Path)
    x.add_argument("parts", nargs="+")
    t = sub.add_parser("train")
    t.add_argument("--data", default="build/align")
    t.add_argument("--out", default="build/align/aligner.pt")
    t.add_argument("--steps", type=int, default=6000)
    t.add_argument("--batch", type=int, default=24)
    t.add_argument("--lr", type=float, default=2e-3)
    t.add_argument("--hidden", type=int, default=128)
    t.add_argument("--threads", type=int, default=8)
    t.add_argument("--minutes", type=float, default=1e9)
    t.add_argument("--ce", type=float, default=0.0, help="weight of a frame cross-entropy on the generator's labels")
    t.add_argument("--kind", choices=["point", "prototype"], default="point")
    e = sub.add_parser("export")
    e.add_argument("ckpt", type=Path)
    e.add_argument("out", type=Path)
    m = sub.add_parser("measure")
    m.add_argument("test", type=Path)
    m.add_argument("ckpt", type=Path)
    m.add_argument("out", type=Path)
    m.add_argument("--delay", type=float, action="append", help="decode with delayed strokes at this penalty")
    m.add_argument("--other", action="append", default=[],
                   help="TAG=PKL: another method's labels per writer and line (as the comparison driver writes them)")
    a = ap.parse_args(argv)
    if a.cmd == "data":
        make_sets(Path(a.out), a.train, a.dev, a.test_lines, a.letter_lines, a.workers, real=a.real,
                  letters=a.letters, share=a.share)
    elif a.cmd == "joined":
        make_joined(Path(a.out), a.train, a.dev, a.test_lines, a.workers)
    elif a.cmd == "mix":
        mix(a.out, [tuple(Path(q) for q in p.split(":")) for p in a.parts])
    elif a.cmd == "export":
        export_ghm(a.ckpt, a.out)
    elif a.cmd == "measure":
        test = pickle.loads(a.test.read_bytes())
        if isinstance(test, list):  # a dev set (to choose --delay on): its lines as one writer's
            test = {"dev": {"test": test}}
        model = load(a.ckpt)
        other: dict = {}
        for o in a.other:
            tag, f = o.split("=", 1)
            for font, w in pickle.loads(Path(f).read_bytes()).items():
                lines = other.setdefault(font, {"lines": [{} for _ in w["lines"]]})["lines"]
                for d, ln in zip(lines, w["lines"]):
                    # (TAG "main" keeps the names: shape_text against pre_text marks the strokes it cut)
                    d.update({(k if tag == "main" else f"{tag}_{k}"): v for k, v in ln.items()})
        res = {}
        for d in a.delay or [None]:
            res["delay" if d is None else f"delay {d}"] = measure_synthetic(test, model, other or None, delay=d)
        a.out.write_text(json.dumps(res, indent=1))
    else:
        train(Path(a.data), Path(a.out), a.steps, a.batch, a.lr, a.hidden, a.threads, minutes=a.minutes, ce=a.ce,
              kind=a.kind)


if __name__ == "__main__":
    main()
