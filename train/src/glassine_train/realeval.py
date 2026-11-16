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
TEXT_PY = REPO / "train" / "src" / "glassine_train" / "text.py"


def export_ghm(ckpt: Path, models: Path, trained_until: int = 0) -> Path:
    """models/en/recognizer.ghm from a checkpoint."""
    import torch
    from .model import Single
    from .train import load
    m = load(ckpt)
    d = models / "en"
    d.mkdir(parents=True, exist_ok=True)
    onnx = models / "recognizer.onnx"
    x0 = torch.zeros(1, 200, 5)
    torch.onnx.export(Single(m).eval(), (x0,), onnx, input_names=["ink"], output_names=["logp"],
                      dynamic_axes={"ink": {1: "points"}, "logp": {1: "frames"}}, opset_version=17,
                      dynamo=False)
    subprocess.run([sys.executable, str(ONNX2GHM), str(onnx), "--charset-from", str(TEXT_PY),
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


def read_samples(models: Path, samples: Path, predict: Path, raw: bool, head: Path | None = None) -> list[dict]:
    """[{id, text, cands}] as the recognizer reads them (three candidates)."""
    with tempfile.TemporaryDirectory(prefix="hw-eval-") as t:
        save = Path(t) / "out.json"
        cmd = [sys.executable, str(EVAL), str(BIN), str(samples), "--models", str(models),
               "--count", "3", "--save", str(save)]
        if raw:
            cmd.append("--raw")
        if head:
            cmd += ["--adaptation", str(head)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       env=dict(os.environ, GLASSINE_PREDICT_MODELS=str(predict)), timeout=3600)
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
