"""Many writers read as new people: a dataset of other people's ink, read writer by writer
through the recognizer program (realeval.read_samples), every model in the same modes, and
measured per writer (spec 001: how a base model reads a person it has not seen).

What a new person gets on the device: the base model alone, no adaptation head, no letters
of theirs for the size (the area's line is the reference), the field `normal`; the words and
the character model as the model directory has them. An evaluation only: nothing here learns
from the ink.

The dataset is the user's: a path, never downloaded here. Formats:

- `brush`: the BRUSH dataset as its authors distribute it (brush.py; where it comes from,
  its terms and the paper to cite are there).
- `samples`: sample-format lines (docs/samples.md) whose `donor` tells the writers apart.

Everything per sample (texts, readings, ink) goes to `--out`, which must be a private
directory; what is printed is aggregates over writers.
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import realeval as R
from .metrics import edit
from .text import CHARS

MODES = {"ink": ("nochars", True, ()), "chars": ("chars", False, ("--chars",)),
         "words": ("nochars", False, ()), "words+chars": ("chars", False, ())}


def _rng(seed: int, writer: str) -> np.random.Generator:
    h = int.from_bytes(hashlib.sha256(f"{seed}:{writer}".encode()).digest()[:8], "little")
    return np.random.default_rng(h)


def load_brush(path: Path, per_writer: int, seed: int, charset: str, conv: dict) -> dict[str, list[dict]]:
    """{writer: [sample, ...]}: per writer up to `per_writer` lines drawn at random among
    those whose text is all in `charset`, each with its ink's traits (`traits`)."""
    from . import brush
    by: dict[str, list] = {}
    for ln in brush.read(path):
        if all(c in charset for c in ln.text):
            by.setdefault(ln.writer, []).append(ln)
    out = {}
    for w, lines in sorted(by.items()):
        r = _rng(seed, w)
        pick = sorted(r.permutation(len(lines))[:per_writer]) if per_writer else range(len(lines))
        out[w] = []
        for i in pick:
            s = brush.to_sample(lines[i], **conv)
            s["traits"] = brush.stroke_traits(lines[i])
            out[w].append(s)
    return out


def load_samples(path: Path, per_writer: int, seed: int, charset: str) -> dict[str, list[dict]]:
    by: dict[str, list] = {}
    for f in ([path] if path.is_file() else sorted(path.rglob("*.jsonl"))):
        for ln in f.read_text().splitlines():
            if ln.startswith("{") and '"strokes"' in ln:
                s = json.loads(ln)
                if s.get("donor") and all(c in charset for c in s["text"]):
                    by.setdefault(s["donor"], []).append(s)
    out = {}
    for w, ss in sorted(by.items()):
        r = _rng(seed, w)
        pick = sorted(r.permutation(len(ss))[:per_writer]) if per_writer else range(len(ss))
        out[w] = [ss[i] for i in pick]
    return out


def ops(hyp: str, ref: str) -> list[tuple[str, str, str]]:
    """The edits turning ref into hyp: (op, ref char, hyp char), op in sub/del/ins."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), int)
    d[:, 0], d[0, :] = np.arange(n + 1), np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]))
    out, i, j = [], n, m
    while i or j:
        if i and j and d[i, j] == d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            if ref[i - 1] != hyp[j - 1]:
                out.append(("sub", ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i and d[i, j] == d[i - 1, j] + 1:
            out.append(("del", ref[i - 1], ""))
            i -= 1
        else:
            out.append(("ins", "", hyp[j - 1]))
            j -= 1
    return out


def char_class(c: str) -> str:
    if c == " ":
        return "space"
    if c.isdigit():
        return "digit"
    if c.isupper():
        return "capital"
    if c.isalpha():
        return "small"
    return "punct"


def error_types(rs: list[dict]) -> dict:
    """Edits of the top readings by kind: each class's edits over its characters in the
    texts (a substitution or deletion counts for the text's character, an insertion for
    the inserted one), case-only substitutions apart, and every kind's share of all edits."""
    edits = {k: 0 for k in ("space", "digit", "capital", "small", "punct")}
    case = 0
    count = {k: 0 for k in edits}
    for r in rs:
        for c in r["text"]:
            count[char_class(c)] += 1
        for op, a, b in ops(r["cands"][0] if r["cands"] else "", r["text"]):
            c = a if op != "ins" else b
            if op == "sub" and a.lower() == b.lower():
                case += 1
            edits[char_class(c)] += 1
    total = sum(edits.values())
    return {"rate": {k: edits[k] / max(count[k], 1) for k in edits}, "chars": count,
            "share": {k: edits[k] / max(total, 1) for k in edits}, "case_only_share": case / max(total, 1),
            "edits": total}


def _q(v):
    v = np.asarray(v, float)
    return {"median": float(np.median(v)), "q1": float(np.quantile(v, 0.25)), "q3": float(np.quantile(v, 0.75)),
            "min": float(v.min()), "max": float(v.max()), "mean": float(v.mean())}


def per_writer(rs: list[dict]) -> dict[str, dict]:
    by: dict[str, list] = {}
    for r in rs:
        by.setdefault(r["writer"], []).append(r)
    return {w: R.summary(R.per_sample(v)) for w, v in by.items()}


def by_traits(rs: list[dict]) -> dict:
    """CER by the share of a line's letters written in shared strokes, and by delayed strokes."""
    bins = {"joined 0": lambda t: t["joined"] == 0,
            "joined <1/3": lambda t: 0 < t["joined"] < t["letters"] / 3,
            "joined 1/3-2/3": lambda t: t["letters"] / 3 <= t["joined"] <= 2 * t["letters"] / 3,
            "joined >2/3": lambda t: t["joined"] > 2 * t["letters"] / 3,
            "no delayed stroke": lambda t: t["delayed"] == 0, "delayed strokes": lambda t: t["delayed"] > 0}
    out = {}
    for name, f in bins.items():
        sel = [r for r in rs if r.get("traits") and f(r["traits"])]
        if sel:
            out[name] = {"lines": len(sel), **R.summary(R.per_sample(sel))}
    return out


def writer_traits(data: dict[str, list[dict]]) -> dict[str, dict]:
    """Per writer: the share of letters in shared strokes, of lines with delayed strokes,
    and the small letters' height over the area's line (glassine_synth.stats.measure)."""
    from glassine_synth.stats import measure
    out = {}
    for w, ss in data.items():
        tr = [s["traits"] for s in ss if s.get("traits")]
        m = measure(ss)
        xh = m.get("writer_x_height_units", float("nan"))
        line = ss[0]["area"]["line"] if ss and ss[0].get("area") else float("nan")
        out[w] = {"joined": sum(t["joined"] for t in tr) / max(sum(t["letters"] for t in tr), 1) if tr else float("nan"),
                  "delayed_lines": float(np.mean([t["delayed"] > 0 for t in tr])) if tr else float("nan"),
                  "xh_line": float(xh / line) if line == line else float("nan")}
    return out


def spearman(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3:
        return float("nan")
    ra, rb = np.argsort(np.argsort(a[ok])), np.argsort(np.argsort(b[ok]))
    return float(np.corrcoef(ra, rb)[0, 1])


def run(data: dict[str, list[dict]], models: list[tuple[str, Path, Path]], modes: list[str], out: Path,
        predict: Path, workers: int = 8, base: float = 2 / 3) -> dict:
    """Reads every writer's samples with every model in every mode; per-sample results in
    out/results, aggregates returned (and in out/summary.json)."""
    smp = out / "samples"
    smp.mkdir(parents=True, exist_ok=True)
    (out / "results").mkdir(exist_ok=True)
    for w, ss in data.items():
        with open(smp / f"{w}.jsonl", "w") as fh:
            for s in ss:
                fh.write(json.dumps({k: v for k, v in s.items() if k != "traits"}, separators=(",", ":")) + "\n")
    jobs = [(name, mode, w) for name, *_ in models for mode in modes for w in data]
    dirs = {name: {"nochars": nc, "chars": ch} for name, nc, ch in models}

    def one(job):
        name, mode, w = job
        kind, raw, extra = MODES[mode]
        rs = R.read_samples(dirs[name][kind], (smp / f"{w}.jsonl").resolve(), predict, raw, None,
                            ("--base", base) + extra)
        ss, k = data[w], 0
        for r in rs:  # in order; a sample the recognizer refused is missing from rs
            while k < len(ss) and ss[k]["text"] != r["text"]:
                k += 1
            r["writer"] = w
            r["traits"] = ss[k].get("traits") if k < len(ss) else None
            k += 1
        return job, rs

    res: dict[tuple, list] = {}
    with ThreadPoolExecutor(workers) as ex:
        for (name, mode, w), rs in ex.map(one, jobs):
            res.setdefault((name, mode), []).extend(rs)
    wt = writer_traits(data)
    summary = {"writers": len(data), "lines": sum(len(v) for v in data.values()), "models": {}}
    for (name, mode), rs in sorted(res.items()):
        (out / "results" / f"{name}.{mode}.json").write_text(json.dumps(rs))
        pw = per_writer(rs)
        ws = sorted(pw)
        cer = [pw[w]["cer"] for w in ws]
        summary["models"].setdefault(name, {})[mode] = {
            "pooled": R.summary(R.per_sample(rs)),
            "writer_cer": _q(cer), "writer_wer": _q([pw[w]["wer"] for w in ws]),
            "writer_top1": _q([pw[w]["top1"] for w in ws]), "writer_top3": _q([pw[w]["top3"] for w in ws]),
            "errors": error_types(rs), "by_traits": by_traits(rs),
            "cer_vs_joined": spearman(cer, [wt[w]["joined"] for w in ws]),
            "cer_vs_delayed": spearman(cer, [wt[w]["delayed_lines"] for w in ws]),
            "cer_vs_size": spearman(cer, [wt[w]["xh_line"] for w in ws]),
            "per_writer": pw}
    if len(models) > 1:
        a = models[0][0]
        for name, *_ in models[1:]:
            for mode in modes:
                pa = per_writer(res[a, mode])
                pb = per_writer(res[name, mode])
                d = np.array([pb[w]["cer"] - pa[w]["cer"] for w in sorted(pa)])
                summary["models"][name][mode]["writer_cer_minus_" + a] = {
                    **_q(d), "writers_better": int((d < 0).sum()), "writers_worse": int((d > 0).sum())}
    summary["writer_traits"] = {"joined": _q([t["joined"] for t in wt.values()]),
                                "delayed_lines": _q([t["delayed_lines"] for t in wt.values()]),
                                "xh_line": _q([t["xh_line"] for t in wt.values() if t["xh_line"] == t["xh_line"]])}
    summary["per_writer_traits"] = wt
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def report(summary: dict) -> str:
    """Aggregates over writers only (no writer, text or reading)."""
    lines = [f"{summary['writers']} writers, {summary['lines']} lines"]
    t = summary["writer_traits"]
    lines.append("writers: letters in shared strokes median {:.2f} (q1 {:.2f}, q3 {:.2f}); lines with delayed strokes "
                 "median {:.2f}; small letters / line median {:.3f} (q1 {:.3f}, q3 {:.3f})".format(
                     t["joined"]["median"], t["joined"]["q1"], t["joined"]["q3"], t["delayed_lines"]["median"],
                     t["xh_line"]["median"], t["xh_line"]["q1"], t["xh_line"]["q3"]))
    for name, modes in summary["models"].items():
        for mode, m in modes.items():
            c, p = m["writer_cer"], m["pooled"]
            lines.append(f"{name:10s} {mode:12s} pooled CER {100 * p['cer']:.1f} WER {100 * p['wer']:.1f} "
                         f"top1 {100 * p['top1']:.1f} top3 {100 * p['top3']:.1f} | per writer CER median "
                         f"{100 * c['median']:.1f} q1 {100 * c['q1']:.1f} q3 {100 * c['q3']:.1f} worst {100 * c['max']:.1f} "
                         f"| WER median {100 * m['writer_wer']['median']:.1f}")
            e = m["errors"]
            lines.append("    edits by kind (rate over the kind's characters / share of edits): " + ", ".join(
                f"{k} {100 * e['rate'][k]:.1f}/{100 * e['share'][k]:.0f}" for k in e["rate"])
                + f"; case only {100 * e['case_only_share']:.0f} % of edits")
            lines.append("    CER by ink: " + ", ".join(f"{k} {100 * v['cer']:.1f} ({v['lines']})"
                                                       for k, v in m["by_traits"].items()))
            lines.append(f"    writers' CER against shared strokes rho {m['cer_vs_joined']:.2f}, delayed "
                         f"{m['cer_vs_delayed']:.2f}, size {m['cer_vs_size']:.2f}")
            for k, v in m.items():
                if k.startswith("writer_cer_minus_"):
                    lines.append(f"    minus {k[17:]} per writer: median {100 * v['median']:+.1f}, q1 {100 * v['q1']:+.1f}, "
                                 f"q3 {100 * v['q3']:+.1f}; better for {v['writers_better']}, worse for {v['writers_worse']}")
    return "\n".join(lines)


COMMON = CHARS  # the characters every base model reads (model 2's; model 4 adds symbols)
