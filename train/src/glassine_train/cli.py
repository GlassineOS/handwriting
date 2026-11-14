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
# The 7-font baseline's fonts: seven to train, two held out. Their datasets keep their names
# (`train`, `val`, `test-trainfonts`, `test-heldout`, ...), so old and new models are measured
# on the same ink.
ORIG_TRAIN = ["caveat", "indieflower", "patrickhand", "architectsdaughter", "gochihand",
              "dancingscript", "shadowsintolight"]
HELD_OUT = ["kalam", "reeniebeanie"]  # never in training; the honest proxy for unseen hands
TRAIN_FONTS = ORIG_TRAIN
NO_VARIANTS = {"reverse_loop": 0.0, "delay": 0.0, "extra_lift": 0.0}
EVAL_SIZES = {"test": 2000, "test_words": 1000, "test_symbols": 500, "test_letters": 500}
VARIATION = {"warp": 0.025, "zones": 0.15}  # letter-shape variation for --variation


def font_splits() -> dict[str, list[str]]:
    """Fonts by split and style from the synth manifest (`split`, `style` in fonts.toml):
    train, dev, test (all held out), test-print / test-script (held out, new to this set:
    the original two are reported on their own)."""
    out = {"train": [], "dev": [], "test": [], "test-print": [], "test-script": []}
    for f in font_fetch.load(FONTS):
        sp = f.get("split", "train")
        out[sp].append(f["dir"])
        if sp == "test" and f["dir"] not in HELD_OUT:
            out[f"test-{f['style']}"].append(f["dir"])
    if set(HELD_OUT) - set(out["test"]):
        raise SystemExit(f"fonts.toml must keep {HELD_OUT} in the test split")
    return out


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
    sp = font_splits()
    tr = c["train"][:a.train_lines]
    # the 7-font baseline's sets
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
    # every training font, a random subset, and the letter-shape variation
    nt = len(sp["train"])
    jobs.append((f"train-f{nt}", tr, sp["train"], Params(), 11))
    for k in a.subsets:
        sub = sorted(np.random.default_rng(5).permutation(sp["train"])[:k].tolist())
        jobs.append((f"train-f{k}", tr, sub, Params(), 11))
    if a.variation:
        jobs.append((f"train-f{nt}-var", tr, sp["train"], Params(**VARIATION), 11))
    if a.copies > 1:  # every text line rendered `copies` times, each time new ink
        jobs.append((f"train-f{nt}-var-x{a.copies}", tr * a.copies, sp["train"], Params(**VARIATION), 31))
    # dev fonts (model selection, decoder tuning) and the new held-out fonts, default ink only
    jobs.append(("dev", c["val"][:2000], sp["dev"], Params(), 12))
    for split, n in EVAL_SIZES.items():
        for style in ("print", "script"):
            jobs.append((f"{split}-new{style}", c[split][:n], sp[f"test-{style}"], Params(), 13))
    for f in sp["test-print"] + sp["test-script"]:  # one font at a time, for per-font numbers
        jobs.append((f"test-font-{f}", c["test"][:300], [f], Params(), 13))
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
          augment=a.augment, select=Path("build/data") / a.select if a.select else None)


def _model(a):
    from .train import load
    p = Path(a.run)
    return load(p / a.ckpt if p.is_dir() else Path("build/runs") / a.run / a.ckpt)


def _run_dir(a) -> Path:
    p = Path(a.run)
    return p if p.is_dir() else Path("build/runs") / a.run


def cmd_eval(a):
    import torch
    from .data import InkSet
    from .metrics import confusions, rates
    from .train import recognise
    torch.set_num_threads(a.threads)
    run = _run_dir(a)
    m = _model(a)
    lex, kw = None, None
    if a.lexicon:
        from .lexicon import Lexicon
        lex = Lexicon(Path(a.lexicon))
        kw = json.loads((run / "decoder.json").read_text())["params"]
    res = {}
    for d in sorted(Path("build/data").iterdir()):
        if not d.name.startswith(("test", "dev")) or not (d / "meta.json").exists():
            continue
        if a.only and not any(d.name.startswith(o) for o in a.only):
            continue
        ds = InkSet(d, a.limit)
        refs, hyps = recognise(m, ds)
        r = rates(refs, hyps)
        if d.name in ("test-heldout", "test-trainfonts", "test-newprint", "test-newscript"):
            r["confusions"] = confusions(refs, hyps)
            r["examples"] = [[x, y] for x, y in list(zip(refs, hyps))[:12]]
        line = (f"{d.name:28s} CER {r['cer']:.4f} (no case {r['cer_nocase']:.4f}) "
                f"WER {r['wer']:.4f} lines {r['line_acc']:.3f}")
        if lex is not None:
            refs_l, hyps_l = decode_set(m, ds, lex, kw, a.workers)
            r["lexicon"] = rates(refs_l, hyps_l)
            line += f" | lexicon CER {r['lexicon']['cer']:.4f} WER {r['lexicon']['wer']:.4f}"
        res[d.name] = r
        print(line, flush=True)
    # the new held-out fonts pooled (print and script sets: same texts, six fonts each)
    for split in EVAL_SIZES:
        a_, b_ = res.get(f"{split}-newprint"), res.get(f"{split}-newscript")
        if a_ and b_:
            pooled = {"cer": (a_["cer"] + b_["cer"]) / 2, "wer": (a_["wer"] + b_["wer"]) / 2}
            if "lexicon" in a_:
                pooled["lexicon_cer"] = (a_["lexicon"]["cer"] + b_["lexicon"]["cer"]) / 2
                pooled["lexicon_wer"] = (a_["lexicon"]["wer"] + b_["lexicon"]["wer"]) / 2
            res[f"{split}-new"] = pooled
            print(f"{split + '-new':28s} " + " ".join(f"{k} {v:.4f}" for k, v in pooled.items()))
    tag = a.ckpt.removesuffix(".pt") + ("-lexicon" if lex is not None else "")
    out = run / f"eval-{tag}.json"
    if (a.only or a.limit) and out.exists():  # a partial run adds to the full results
        res = {**json.loads(out.read_text()), **res}
    out.write_text(json.dumps(res, indent=1))


def cmd_tune(a):
    """Grid search of the decoder weights on the dev fonts (never on test fonts)."""
    import itertools
    import torch
    from .data import InkSet
    from .lexicon import Lexicon
    from .metrics import rates
    torch.set_num_threads(a.threads)
    run = _run_dir(a)
    m = _model(a)
    lex = Lexicon(Path(a.lexicon))
    ds = InkSet(Path("build/data") / a.data, a.limit)
    from .train import recognise
    refs, hyps = recognise(m, ds)
    greedy_cer = rates(refs, hyps)["cer"]
    print(f"greedy CER {greedy_cer:.4f} on {a.data} ({len(ds)} lines)", flush=True)
    grid = list(itertools.product(a.lm_weight, a.char_bonus, a.word_bonus, a.oov_penalty))
    best = None
    rows = []
    for lw, cb, wb, op in grid:
        kw = {"beam": a.beam, "lm_weight": lw, "char_bonus": cb, "word_bonus": wb, "oov_penalty": op}
        r_, h_ = decode_set(m, ds, lex, kw, a.workers)
        r = rates(r_, h_)
        rows.append({"params": kw, "cer": r["cer"], "wer": r["wer"]})
        print(json.dumps(rows[-1]), flush=True)
        if best is None or r["cer"] < best["cer"]:
            best = rows[-1]
    best = dict(best)
    best.update(greedy_cer=greedy_cer, data=a.data, lexicon=a.lexicon, grid=rows)
    (run / "decoder.json").write_text(json.dumps(best, indent=1))
    print("best", json.dumps({k: v for k, v in best.items() if k != "grid"}))


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


def cmd_lexicon(a):
    from . import lexicon
    if a.prune_from:
        lexicon.prune(Path(a.prune_from), Path(a.out), a.min_count, a.words, a.order)
        return
    src = Path("build/lexicon")
    lexicon.fetch(src)
    counts = Path(a.out + "-counts")  # order 6, every n-gram seen 3 times or more
    if not (counts / "charlm.txt").exists():
        lexicon.build(src, counts, a.words, 6, 3, a.max_lines)
    lexicon.prune(counts, Path(a.out), a.min_count, a.words, a.order)


def _logps(m, ds):
    """Model output (frames, classes) per line, in dataset order."""
    import torch
    from .train import loader
    out = [None] * len(ds)
    order = sorted(range(len(ds)), key=ds.length)
    k = 0
    with torch.no_grad():
        for x, lens, _, _, _ in loader(ds, 64, workers=0):
            lp, ol = m(x, lens)
            for b in range(len(lens)):
                out[order[k]] = lp[b, :int(ol[b])].numpy()
                k += 1
    return out


_LEX = None


def _dec(args):
    from . import lexicon
    from .data import ALPHABET
    lp, kw = args
    return lexicon.decode(lp, ALPHABET, _LEX, **kw)[0][0]


def decode_set(m, ds, lex, kw, workers):
    """(refs, hyps) for a dataset with lexicon decoding, lines decoded in parallel."""
    import multiprocessing as mp
    global _LEX
    _LEX = lex
    lps = _logps(m, ds)
    with mp.get_context("fork").Pool(workers) as pool:
        hyps = pool.map(_dec, [(lp, kw) for lp in lps], chunksize=8)
    return list(ds.texts[:len(ds)]), hyps


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
    d.add_argument("--subsets", type=int, nargs="*", default=[], help="also train sets of N random training fonts")
    d.add_argument("--variation", action="store_true", help="also the full training set with letter-shape variation")
    d.add_argument("--copies", type=int, default=1, help="also a variation set with every line rendered N times")
    t = sub.add_parser("train")
    t.add_argument("--data", default="train")
    t.add_argument("--val", default="val", help="dataset shown during training (training fonts)")
    t.add_argument("--select", default="dev", help="dataset for model selection (fonts never trained on); '' = val")
    t.add_argument("--run", required=True)
    t.add_argument("--minutes", type=float, default=60)
    t.add_argument("--steps-per-s", type=float, help="measured rate, sets the LR schedule length")
    t.add_argument("--conv", type=int, default=128)
    t.add_argument("--hidden", type=int, default=176)
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
    e.add_argument("--lexicon", help="lexicon directory; decoder weights from the run's decoder.json")
    e.add_argument("--workers", type=int, default=8, help="processes for lexicon decoding")
    e.add_argument("--only", nargs="*", help="dataset name prefixes")
    e.add_argument("--limit", type=int, help="first N lines of each set")
    tu = sub.add_parser("tune", help="decoder weights for lexicon decoding, on the dev fonts")
    tu.add_argument("--run", required=True)
    tu.add_argument("--ckpt", default="best.pt")
    tu.add_argument("--lexicon", default="build/lexicon/en")
    tu.add_argument("--data", default="dev")
    tu.add_argument("--limit", type=int, default=1000)
    tu.add_argument("--threads", type=int, default=4)
    tu.add_argument("--workers", type=int, default=8)
    tu.add_argument("--beam", type=int, default=16)
    tu.add_argument("--lm-weight", type=float, nargs="+", default=[0.3, 0.5, 0.8])
    tu.add_argument("--char-bonus", type=float, nargs="+", default=[0.0, 1.0, 2.0])
    tu.add_argument("--word-bonus", type=float, nargs="+", default=[0.0, 1.0])
    tu.add_argument("--oov-penalty", type=float, nargs="+", default=[0.0, 2.0])
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
    lx = sub.add_parser("lexicon", help="fetch the word-list sources, build words + character model")
    lx.add_argument("--out", default="build/lexicon/en")
    lx.add_argument("--words", type=int, default=50_000)
    lx.add_argument("--order", type=int, default=5)
    lx.add_argument("--min-count", type=int, default=50)
    lx.add_argument("--max-lines", type=int)
    lx.add_argument("--prune-from", help="instead: a smaller copy of this built lexicon")
    a = ap.parse_args(argv)
    {"lexicon": cmd_lexicon, "tune": cmd_tune, "adapt": cmd_adapt, "data": cmd_data, "train": cmd_train, "eval": cmd_eval, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
