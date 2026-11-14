"""glassine-train: text, datasets, training, evaluation and export for the prototype."""

import argparse
import json
import time
from pathlib import Path

import numpy as np
from glassine_synth import fetch as font_fetch
from glassine_synth.ink import Params

from . import text
from .data import build

FONTS = Path("../synth/build/fonts")
ALL_FONTS = ["caveat", "indieflower", "patrickhand", "kalam", "architectsdaughter",
             "gochihand", "dancingscript", "shadowsintolight", "reeniebeanie"]
HELD_OUT = ["kalam", "reeniebeanie"]  # never in training; the honest proxy for unseen hands
TRAIN_FONTS = [f for f in ALL_FONTS if f not in HELD_OUT]
NO_VARIANTS = {"reverse_loop": 0.0, "delay": 0.0, "extra_lift": 0.0}
EVAL_SIZES = {"test": 2000, "test_words": 1000, "test_symbols": 500, "test_letters": 500}


def _provenance():
    return {f["dir"]: {"name": f["name"], "license": f["license"], "source": f["source"],
                       "commit": f["upstream_commit"]} for f in font_fetch.load(FONTS)}


def cmd_data(a):
    raw = Path("build/text")
    text.fetch(raw)
    c = text.corpus(raw, seed=1)
    (raw / "corpus.json").write_text(json.dumps({k: len(v) for k, v in c.items()}))
    prov = {"fonts": _provenance(), "text": json.loads((raw / "manifest.json").read_text())}
    out = Path("build/data")
    tr = c["train"][:a.train_lines]
    jobs = [("train", tr, TRAIN_FONTS, Params(), 11)]
    if a.ablation:
        jobs.append(("train-novar", tr, TRAIN_FONTS, Params(**NO_VARIANTS), 11))
    jobs.append(("val", c["val"][:2000], TRAIN_FONTS, Params(), 12))
    for split, n in EVAL_SIZES.items():
        texts = c[split][:n]
        for group, fonts in (("trainfonts", TRAIN_FONTS), ("heldout", HELD_OUT)):
            jobs.append((f"{split}-{group}", texts, fonts, Params(), 13))
            if a.ablation and split == "test":
                jobs.append((f"{split}-{group}-novar", texts, fonts, Params(**NO_VARIANTS), 13))
        if split == "test":
            for f in HELD_OUT:
                jobs.append((f"test-font-{f}", texts, [f], Params(), 13))
    for name, texts, fonts, params, seed in jobs:
        if (out / name / "meta.json").exists():
            print(f"{name}: exists")
            continue
        t = time.time()
        build(out / name, texts, fonts, params, seed, FONTS, a.workers, prov)
        print(f"{name}: {time.time() - t:.0f} s", flush=True)


def cmd_train(a):
    from .train import train
    cfg = {"conv": a.conv, "hidden": a.hidden, "layers": a.layers, "dropout": a.dropout,
           "down": a.down}
    out = Path("build/runs") / a.run
    out.mkdir(parents=True, exist_ok=True)
    (out / "args.json").write_text(json.dumps(vars(a), default=str, indent=1))
    train(Path("build/data") / a.data, Path("build/data") / a.val, out, a.minutes, cfg,
          a.batch, a.lr, a.threads, a.eval_every, a.seed, steps_per_s=a.steps_per_s,
          augment=a.augment)


def cmd_eval(a):
    import torch
    from .data import InkSet
    from .metrics import confusions, rates
    from .train import load, recognise
    torch.set_num_threads(a.threads)
    run = Path("build/runs") / a.run
    m = load(run / a.ckpt)
    res = {}
    for d in sorted(Path("build/data").iterdir()):
        if not d.name.startswith("test") or not (d / "meta.json").exists():
            continue
        refs, hyps = recognise(m, InkSet(d))
        r = rates(refs, hyps)
        if d.name in ("test-heldout", "test-trainfonts"):
            r["confusions"] = confusions(refs, hyps)
            r["examples"] = [[x, y] for x, y in list(zip(refs, hyps))[:12]]
        res[d.name] = r
        print(f"{d.name:28s} CER {r['cer']:.4f} (no case {r['cer_nocase']:.4f}) "
              f"WER {r['wer']:.4f} lines {r['line_acc']:.3f}", flush=True)
    (run / f"eval-{a.ckpt.removesuffix('.pt')}.json").write_text(json.dumps(res, indent=1))


def cmd_export(a):
    import onnxruntime as ort
    import torch
    from .data import InkSet, decode
    from .model import Single
    from .train import load
    run = Path("build/runs") / a.run
    m = load(run / a.ckpt)
    single = Single(m).eval()
    path = run / "recognizer.onnx"
    ds = InkSet(Path("build/data/test-heldout"))
    x0 = ds[0][0][None]
    torch.onnx.export(single, (x0,), path, input_names=["ink"], output_names=["logp"],
                      dynamic_axes={"ink": {1: "points"}, "logp": {1: "frames"}},
                      opset_version=17, dynamo=False)
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
    diffs, times, pts = [], [], []
    torch.set_num_threads(1)
    for i in range(0, len(ds), max(1, len(ds) // 200)):
        x = ds[i][0][None]
        t = time.perf_counter()
        o = sess.run(None, {"ink": x.numpy()})[0]
        times.append(time.perf_counter() - t)
        pts.append(x.shape[1])
        with torch.no_grad():
            ref, _ = m(x, torch.tensor([x.shape[1]]))
        diffs.append(float(np.abs(o - ref.numpy()).max()))
    rep = {"onnx_bytes": path.stat().st_size, "params": sum(p.numel() for p in m.parameters()),
           "max_abs_diff_vs_torch": max(diffs), "lines": len(times),
           "ms_per_line_1thread_x86_median": 1000 * float(np.median(times)),
           "ms_per_line_1thread_x86_p95": 1000 * float(np.percentile(times, 95)),
           "points_per_line_median": float(np.median(pts))}
    # int8 weights (dynamic quantisation): size and accuracy through onnxruntime
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from .metrics import rates
    from .model import greedy
    q8 = run / "recognizer-int8.onnx"
    quantize_dynamic(str(path), str(q8), weight_type=QuantType.QInt8)
    rep["int8_bytes"] = q8.stat().st_size
    for name, f in (("fp32", path), ("int8", q8)):
        s8 = ort.InferenceSession(str(f), so, providers=["CPUExecutionProvider"])
        refs, hyps, tt = [], [], []
        for i in range(len(ds)):
            x, ref = ds[i]
            t = time.perf_counter()
            o = s8.run(None, {"ink": x[None].numpy()})[0]
            tt.append(time.perf_counter() - t)
            hyps.append(decode(greedy(torch.from_numpy(o), torch.tensor([o.shape[1]]))[0]))
            refs.append(ref)
        r = rates(refs, hyps)
        rep[f"{name}_test_heldout_cer"] = r["cer"]
        rep[f"{name}_test_heldout_wer"] = r["wer"]
        rep[f"{name}_ms_per_line_median"] = 1000 * float(np.median(tt))
    (run / "export.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


def cmd_adapt(a):
    """Head-only adaptation to one held-out font (a stand-in for a new writer) + timings."""
    import torch
    from .adapt import Head, encode_lines, read_with_head, train_head
    from .data import InkSet, decode
    from .metrics import rates
    from .train import load
    torch.set_num_threads(1)
    run = Path("build/runs") / a.run
    m = load(run / a.ckpt)
    raw = Path("build/text")
    c = text.corpus(raw, seed=1)
    prov = {"fonts": _provenance(), "text": json.loads((raw / "manifest.json").read_text())}
    texts = c["val"][2000:2000 + max(a.sizes)]  # training-book text, never in val or test
    ad = Path("build/data") / f"adapt-{a.font}"
    if not (ad / "meta.json").exists():
        build(ad, texts, [a.font], Params(), 21, FONTS, 4, prov)
    lines = lambda ds, n: [ds[i] for i in range(min(n, len(ds)))]
    adapt_set = lines(InkSet(ad), max(a.sizes))
    evals = {"same font (test-font-%s)" % a.font: lines(InkSet(Path(f"build/data/test-font-{a.font}")), a.eval_lines)}
    for other in [f"test-font-{f}" for f in HELD_OUT if f != a.font] + ["test-trainfonts"]:
        evals[other] = lines(InkSet(Path("build/data") / other), a.eval_lines)

    enc_params = sum(p.numel() for n, p in m.named_parameters() if not n.startswith("out."))
    rep = {"font": a.font, "encoder_params": enc_params}
    # timings, one thread: encoder forward per line, full fine-tune step per line
    t = []
    for x, _ in adapt_set[:100]:
        s = time.perf_counter()
        encode_lines(m, [x])
        t.append(time.perf_counter() - s)
    rep["encoder_ms_per_line_median"] = 1000 * float(np.median(t))
    full = load(run / a.ckpt)
    full.train()
    opt = torch.optim.Adam(full.parameters(), 1e-4)
    ctc = torch.nn.CTCLoss(zero_infinity=True)
    from .data import encode
    t = []
    for x, s_ in adapt_set[:50]:
        s = time.perf_counter()
        lp, ol = full(x[None], torch.tensor([len(x)]))
        loss = ctc(lp.transpose(0, 1), torch.tensor(encode(s_)), ol, torch.tensor([len(s_)]))
        opt.zero_grad()
        loss.backward()
        opt.step()
        t.append(time.perf_counter() - s)
    rep["full_finetune_ms_per_line_median"] = 1000 * float(np.median(t))

    cache = {k: encode_lines(m, [x for x, _ in v]) for k, v in evals.items()}
    refs = {k: [s_ for _, s_ in v] for k, v in evals.items()}
    ad_frames = encode_lines(m, [x for x, _ in adapt_set])
    ad_texts = [s_ for _, s_ in adapt_set]

    def score(head):
        return {k: round(rates(refs[k], [decode(h) for h in read_with_head(head, cache[k])])["cer"], 4)
                for k in cache}

    rep["before"] = score(Head(m.out))
    rep["runs"] = []
    for rank in a.ranks:
        for n in a.sizes:
            head = Head(m.out, rank)
            sec = train_head(head, ad_frames[:n], ad_texts[:n], epochs=a.epochs, lr=a.lr)
            r = {"rank": rank, "head_params": sum(p.numel() for p in head.parameters()),
                 "lines": n, "epochs": a.epochs, "train_s_1thread": round(sec, 2), "cer": score(head)}
            print(json.dumps(r), flush=True)
            rep["runs"].append(r)
    print("before", json.dumps(rep["before"]))
    (run / f"adapt-{a.font}.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: v for k, v in rep.items() if k not in ("runs",)}, indent=1))


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["release"]:  # its own options: glassine-train release --help
        from .release import main as release
        return release(argv[1:])
    ap = argparse.ArgumentParser(prog="glassine-train", epilog="glassine-train release --help: "
                                 "a trained run as a base model package (models/README.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("data", help="fetch text, render train/val/test datasets")
    d.add_argument("--train-lines", type=int, default=300_000)
    d.add_argument("--workers", type=int, default=8)
    d.add_argument("--ablation", action="store_true", help="also render without order variants")
    t = sub.add_parser("train")
    t.add_argument("--data", default="train")
    t.add_argument("--val", default="val", help="dataset for model selection")
    t.add_argument("--run", required=True)
    t.add_argument("--minutes", type=float, default=60)
    t.add_argument("--steps-per-s", type=float, help="measured rate, sets the LR schedule length")
    t.add_argument("--conv", type=int, default=128)
    t.add_argument("--hidden", type=int, default=128)
    t.add_argument("--layers", type=int, default=2)
    t.add_argument("--dropout", type=float, default=0.1)
    t.add_argument("--down", type=int, default=3, help="strided convolutions (2**down downsampling)")
    t.add_argument("--batch", type=int, default=32)
    t.add_argument("--lr", type=float, default=2e-3)
    t.add_argument("--threads", type=int, default=8)
    t.add_argument("--eval-every", type=int, default=2000)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--augment", action="store_true", help="random slant and stretch per line")
    e = sub.add_parser("eval")
    e.add_argument("--run", required=True)
    e.add_argument("--ckpt", default="best.pt")
    e.add_argument("--threads", type=int, default=8)
    x = sub.add_parser("export")
    x.add_argument("--run", required=True)
    x.add_argument("--ckpt", default="best.pt")
    ad = sub.add_parser("adapt", help="head-only adaptation to one held-out font")
    ad.add_argument("--run", required=True)
    ad.add_argument("--ckpt", default="best.pt")
    ad.add_argument("--font", default="reeniebeanie")
    ad.add_argument("--sizes", type=int, nargs="+", default=[50, 200])
    ad.add_argument("--ranks", type=int, nargs="+", default=[0, 32])
    ad.add_argument("--epochs", type=int, default=5)
    ad.add_argument("--lr", type=float, default=1e-3)
    ad.add_argument("--eval-lines", type=int, default=1000)
    a = ap.parse_args(argv)
    {"adapt": cmd_adapt, "data": cmd_data, "train": cmd_train, "eval": cmd_eval, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
