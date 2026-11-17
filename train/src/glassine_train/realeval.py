"""A model read on real held-back ink through the recognizer program itself.

The run's checkpoint is exported (ONNX, then the recognizer's model file), and the
recognizer reads the held-back samples with `recognizer/tools/eval-samples.py`: the ink's
reading alone (`raw`) and with words (FR-015), three candidates each. Optionally a fresh
adaptation (the device's own `glassine-handwriting adapt`, on the training sessions'
store) is trained against the exported model and read with too. Character error, word
error, the top candidate exact, and the text among the top three are computed here from
the saved candidates, so two models can be compared sample by sample (paired bootstrap).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from .metrics import edit

REPO = Path(__file__).resolve().parents[3]
BIN = REPO / "recognizer" / "glassine-handwriting"
EVAL = REPO / "recognizer" / "tools" / "eval-samples.py"
ONNX2GHM = REPO / "recognizer" / "tools" / "onnx2ghm.py"


def export_ghm(ckpt: Path, models: Path, trained_until: int = 0) -> Path:
    """models/en/recognizer.ghm from a checkpoint."""
    import torch
    from .model import Single
    from .train import load
    m = load(ckpt)
    d = models / "en"
    d.mkdir(parents=True, exist_ok=True)
    onnx = models / "recognizer.onnx"
    x0 = torch.zeros(1, 200, m.c1.in_channels)
    torch.onnx.export(Single(m).eval(), (x0,), onnx, input_names=["ink"], output_names=["logp"],
                      dynamic_axes={"ink": {1: "points"}, "logp": {1: "frames"}}, opset_version=17,
                      dynamo=False)
    # the model's characters (model 4 adds @/&+#%_=$ after text.py's CHARS); onnx2ghm writes
    # version 2 for a model reading the reference-height features
    subprocess.run([sys.executable, str(ONNX2GHM), str(onnx), "--charset", m.alphabet,
                    "-o", str(d / "recognizer.ghm"), "--trained-until", str(trained_until)],
                   check=True, stdout=subprocess.DEVNULL)
    onnx.unlink()
    return d / "recognizer.ghm"


def _env(tmp: Path, models: Path, predict: Path) -> dict:
    return dict(os.environ, XDG_DATA_HOME=str(tmp / "data"), XDG_CONFIG_HOME=str(tmp / "conf"),
                XDG_STATE_HOME=str(tmp / "state"), HOME=str(tmp),
                GLASSINE_HANDWRITING_MODELS=str(models), GLASSINE_PREDICT_MODELS=str(predict))


def adapt(models: Path, store: Path, predict: Path, out: Path, extra: tuple = ()) -> dict:
    """A fresh adaptation of the model in `models` on a store, as the device runs it."""
    with tempfile.TemporaryDirectory(prefix="hw-adapt-") as t:
        tmp = Path(t)
        d = tmp / "data" / "glassine" / "handwriting"
        d.mkdir(parents=True)
        for f in ("samples.jsonl", "samples.meta"):
            shutil.copy(store / f, d / f)
        r = subprocess.run([str(BIN), "adapt", "-f", "-l", "en", *extra], env=_env(tmp, models, predict),
                           capture_output=True, text=True, timeout=3600)
        last = [ln.split("\t") for ln in r.stdout.splitlines() if ln.startswith("result")]
        head = d / "adaptation" / "en" / "current"
        info = {"result": last[-1][1:] if last else r.stdout[-200:] + r.stderr[-200:]}
        if head.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(head, out)
            info["head"] = str(out)
        return info


def read_samples(models: Path, samples: Path, predict: Path, raw: bool, head: Path | None = None,
                 extra: tuple = (), env: dict | None = None) -> list[dict]:
    """[{id, text, cands}] as the recognizer reads them (three candidates); `extra`: more
    options for eval-samples.py (--store, --base, --chars), `env` for the recognizer."""
    with tempfile.TemporaryDirectory(prefix="hw-eval-") as t:
        save = Path(t) / "out.json"
        cmd = [sys.executable, str(EVAL), str(BIN), str(samples), "--models", str(models),
               "--count", "3", "--save", str(save)]
        if raw:
            cmd.append("--raw")
        if head:
            cmd += ["--adaptation", str(head)]
        cmd += [str(x) for x in extra]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       env=dict(os.environ, GLASSINE_PREDICT_MODELS=str(predict), **(env or {})), timeout=3600)
        return json.loads(save.read_text())


def per_sample(rs: list[dict]) -> dict:
    """Arrays per sample: char edits, chars, word edits, words, top-1 exact, in top 3."""
    top = [r["cands"][0] if r["cands"] else "" for r in rs]
    return {"ce": np.array([edit(h, r["text"]) for h, r in zip(top, rs)]),
            "cn": np.array([len(r["text"]) for r in rs]),
            "we": np.array([edit(h.split(), r["text"].split()) for h, r in zip(top, rs)]),
            "wn": np.array([len(r["text"].split()) for r in rs]),
            "top1": np.array([bool(r["cands"]) and r["cands"][0] == r["text"] for r in rs]),
            "top3": np.array([r["text"] in r["cands"][:3] for r in rs])}


def summary(p: dict) -> dict:
    return {"n": int(len(p["cn"])), "cer": float(p["ce"].sum() / p["cn"].sum()),
            "wer": float(p["we"].sum() / p["wn"].sum()), "top1": float(p["top1"].mean()),
            "top3": float(p["top3"].mean())}


def paired_bootstrap(a: dict, b: dict, n: int = 10000, seed: int = 0) -> dict:
    """P(CER of a < CER of b) and the 95 % interval of the CER difference a − b,
    resampling samples (the same ones for both)."""
    rng = np.random.default_rng(seed)
    k = len(a["cn"])
    idx = rng.integers(0, k, (n, k))
    da = a["ce"][idx].sum(1) / a["cn"][idx].sum(1)
    db = b["ce"][idx].sum(1) / b["cn"][idx].sum(1)
    d = da - db
    return {"p_a_better": float((d < 0).mean()), "diff_lo": float(np.quantile(d, 0.025)),
            "diff_hi": float(np.quantile(d, 0.975))}


# ---------------------------------------------------------------- synthetic ink, read the same way

XH_EM = 0.1  # the letter height synthetic samples are scaled to (GLASSINE_HANDWRITING_SIZE gives it)
GUIDE = 2 / 3  # the area's guides: two thirds down each line, as real.reference assumes


def ink_samples(ink: Path, out: Path, limit: int | None = None, ratios: dict | None = None) -> int:
    """An ink set (inkstore.py) as sample-format lines, so the recognizer program reads it as
    it reads a person's: each line scaled so its letter height is XH_EM em, in a writing
    area of its own line height whose guide (GUIDE of a line below the box's top) is the
    line's baseline. With $GLASSINE_HANDWRITING_SIZE=XH_EM,2.5·XH_EM the letter height is
    known; without it the recognizer has the area's line alone. ratios: the letter height is
    the writer's small letters as the recognizer measures them (inkmeasure.writer_ratios), as
    it is for a person, instead of the generator's."""
    from .inkstore import InkStore
    st = InkStore(ink)
    n = len(st) if limit is None else min(limit, len(st))
    with open(out, "w") as fh:
        for i in range(n):
            strokes, row = st[i]
            strokes = [q for q in strokes if len(q)]
            if not strokes:  # a mark too small to keep any ink (a few lone dots): nothing to read
                continue
            k = XH_EM / (row["xh"] * (ratios.get(row["writer"], ratios["*"]) if ratios else 1.0))
            line, base = row["line"] * k, row["base"] * k
            pts = np.concatenate(strokes)
            x0, x1 = float(pts[:, 0].min()) * k - line, float(pts[:, 0].max()) * k + line
            y0 = base - GUIDE * line
            area = {"box": [round(x0, 5), round(y0, 6), round(x1, 5), round(y0 + 4 * line, 6)], "line": round(line, 6)}
            sm = {"version": 1, "text": row["text"], "lang": "en", "source": "prompted", "spans_source": "exact",
                  "units": {"xy": "em"}, "area": area, "kind": row["kind"],
                  "strokes": [np.c_[np.round(q[:, :2] * k, 6), np.round(q[:, 2], 4)].tolist() for q in strokes]}
            fh.write(json.dumps(sm, separators=(",", ":")) + "\n")
    return n
