"""glassine-train: text, datasets, training, evaluation and export for the prototype."""

import argparse
import json
import time
from pathlib import Path

import numpy as np
from glassine_synth import fetch as font_fetch
from glassine_synth.ink import Params

from . import text
from .data import build, dataset_alphabet

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
LONE_SIZE = ".,oOcCsS"  # gen4's test_lone sets: alike but for their size (FR-018)


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
    (out / ("args-resume.json" if a.resume else "args.json")).write_text(json.dumps(vars(a), default=str, indent=1))
    train(Path("build/data") / a.data, Path("build/data") / a.val, out, a.minutes, cfg,
          a.batch, a.lr, a.threads, a.eval_every, a.seed, steps_per_s=a.steps_per_s,
          augment=a.augment, select=[Path("build/data") / x for x in a.select.split(",") if x],
          init=Path(a.init) if a.init else None, resume=a.resume)


def cmd_mergeset(a):
    """A dataset (featurize's format) from --base without its lines of --drop-kinds (the
    kinds of the ink set it was made from) and with the lines of --add appended: to replace
    some kinds of a large set without featurizing it again."""
    from .inkstore import InkStore
    base, out = Path("build/data") / a.base, Path("build/data") / a.out
    bm = json.loads((base / "meta.json").read_text())
    kinds = [r["kind"] for r in InkStore(Path(bm["from"])).rows]
    boff = np.load(base / "offsets.npy")
    btx = json.loads((base / "texts.json").read_text())
    assert len(kinds) == len(btx) == len(boff) - 1, "the base set and its ink set differ"
    nf = bm["nfeat"]
    keep = [i for i, k in enumerate(kinds) if k not in set(a.drop_kinds)]
    parts = [(base, keep, boff, btx)]
    for d in a.add:
        p = Path("build/data") / d
        m = json.loads((p / "meta.json").read_text())
        if m["nfeat"] != nf or dataset_alphabet(p) != dataset_alphabet(base):
            raise SystemExit(f"{p}: inputs or characters differ from the base set's")
        tx = json.loads((p / "texts.json").read_text())
        parts.append((p, list(range(len(tx))), np.load(p / "offsets.npy"), tx))
    out.mkdir(parents=True, exist_ok=True)
    offs, texts, pos = [0], [], 0
    with open(out / "feat.f16", "wb") as fh:
        for p, idx, off, tx in parts:
            feat = np.memmap(p / "feat.f16", np.float16, "r").reshape(-1, nf)
            for i in idx:
                fh.write(np.ascontiguousarray(feat[off[i]:off[i + 1]]).tobytes())
                pos += int(off[i + 1] - off[i])
                offs.append(pos)
                texts.append(tx[i])
    np.save(out / "offsets.npy", np.asarray(offs, np.int64))
    (out / "texts.json").write_text(json.dumps(texts))
    meta = dict(bm, n=len(texts), points=pos, merged={"base": a.base, "dropped_kinds": a.drop_kinds, "added": a.add,
                                                       "kept": len(keep)})
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"{out}: {len(texts)} lines ({len(keep)} of {a.base}, dropped {len(btx) - len(keep)} of {a.drop_kinds})")


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
        if ds.nfeat != m.c1.in_channels:  # another kind of input (features with or without a reference)
            continue
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
    ds = InkSet(Path("build/data") / (a.data or ("test-heldout" if m.c1.in_channels == 5 else "test-heldout.xh")))
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
            hyps.append(decode(greedy(torch.from_numpy(o), torch.tensor([o.shape[1]]))[0], m.alphabet))
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


def cmd_charlm(a):
    """The character model (charlm.py) from the training books, and its release tarball."""
    from . import charlm
    raw = Path("build/text")
    text.fetch(raw)
    info, path, sha = charlm.release(raw, Path(a.out), a.lang, a.version, a.order, a.min_hist)
    print(json.dumps({k: v for k, v in info.items() if k != "sources"}))
    print(path, sha)


def cmd_real(a):
    """A person's store, split by session for training and evaluation (real.py)."""
    from .real import kept_test_ids, prepare
    keep = kept_test_ids(Path(a.keep_test)) if a.keep_test else None
    print(json.dumps(prepare(Path(a.store), Path(a.out), keep), indent=1))


def cmd_finetune(a):
    from .data import InkSet
    from .finetune import finetune
    from .real import RealSet, load_split
    data = Path("build/data")
    sp = load_split(Path(a.real), Path(a.test_ids) if a.test_ids else None)
    real = 1.0 - a.synth_share
    hand = a.synth_share * a.hand_frac if a.hand else 0.0
    sources = []
    if real > 0:
        sources.append({"name": "real", "ds": RealSet(sp["train"], augment=True), "share": real, "real": True})
    if a.synth_share - hand > 0:
        for name in a.synth:
            sources.append({"name": name, "ds": InkSet(data / name, augment=True),
                            "share": (a.synth_share - hand) / len(a.synth), "real": False})
    if hand > 0:
        sources.append({"name": a.hand, "ds": InkSet(data / a.hand, augment=True), "share": hand, "real": False})
    out = Path("build/runs") / a.run
    out.mkdir(parents=True, exist_ok=True)
    (out / "args.json").write_text(json.dumps(vars(a), default=str, indent=1))
    res = finetune(Path(a.init), out, sources, RealSet(sp["dev"]),
                   InkSet(data / a.synth_dev, a.synth_dev_limit) if a.synth_dev else None,
                   a.steps, a.lr, a.batch, a.threads, a.l2sp, a.adv, a.eval_every, a.patience,
                   a.avg, a.seed, a.workers)
    print(json.dumps(res, indent=1))


def cmd_realeval(a):
    """Real held-back ink through the recognizer program (realeval.py): raw and with words,
    the base model alone, with a given head, and with a fresh adaptation."""
    from . import realeval as R
    real = Path(a.real)
    predict = Path(a.predict).resolve()
    name = a.name or a.run
    out = Path(a.out) / name
    out.mkdir(parents=True, exist_ok=True)
    extra = (["--store", (real / a.letters).resolve()] if a.letters else []) + \
        (["--base", a.base] if a.base is not None else [])
    if a.models:
        models = Path(a.models).resolve()
    else:
        models = (out / "models").resolve()
        until = json.loads((real / "split.json").read_text())["report"]["trained_until"] if a.cutoff else 0
        R.export_ghm(_run_dir(a) / a.ckpt, models, until)
    heads = {"base": None}
    if a.head:
        heads["given"] = Path(a.head).resolve()
    res = {"models": str(models)}
    if a.adapt:
        from .real import check_store, test_ids
        check_store(real / a.adapt_store, test_ids(real, Path(a.test_ids) if a.test_ids else None))
        info = R.adapt(models, real / a.adapt_store, predict, (out / "fresh.gha").resolve(), tuple(a.adapt_arg))
        res["adapt"] = info
        if "head" in info:
            heads["fresh"] = Path(info["head"])
    keep = {}
    for split in a.sets:
        for hname, head in heads.items():
            for mode in a.modes:
                more = list(extra) + (["--chars"] if mode == "chars" else [])
                rs = R.read_samples(models, (real / f"{split}.jsonl").resolve(), predict, mode == "raw", head, more)
                p = R.per_sample(rs)
                key = f"{split}/{hname}/{mode}"
                res[key] = R.summary(p)
                keep[key] = {k: v.tolist() for k, v in p.items()}
                print(f"{name:24s} {key:20s} " + " ".join(f"{k} {v:.4f}" if isinstance(v, float) else f"{k} {v}"
                                                          for k, v in res[key].items()), flush=True)
    if a.synth_dev and not a.models:
        import torch
        from .data import InkSet
        from .train import evaluate
        torch.set_num_threads(a.threads)
        m = _model(a)
        for d in a.synth_dev:
            res[f"synth/{d}"] = evaluate(m, InkSet(Path("build/data") / d))
            print(f"{name:24s} synth/{d:14s} cer {res[f'synth/{d}']['cer']:.4f}", flush=True)
    (out / "result.json").write_text(json.dumps(res, indent=1))
    (out / "per-sample.json").write_text(json.dumps(keep))


WRITER_MODES = [("ink", "raw"), ("chars", "chars"), ("words", "words"), ("words+chars", "words")]


def cmd_writereval(a):
    """Another writer's samples read as a new person would be: no head, no letters, every
    decoder mode, each model against the first (paired bootstrap over the samples). An
    evaluation set only: nothing here learns from it. Only aggregates are written."""
    from . import realeval as R
    smp = Path(a.samples)
    smp = (smp / "samples.jsonl" if smp.is_dir() else smp).resolve()
    predict = Path(a.predict).resolve()
    models = []
    for spec in a.models:  # NAME=NOCHARS_DIR,CHARS_DIR
        name, dirs = spec.split("=", 1)
        nc, ch = (Path(d).resolve() for d in dirs.split(","))
        models.append((name, nc, ch))
    extra = ("--base", a.base) if a.base is not None else ()
    per, res = {}, {"samples": sum(1 for ln in smp.read_text().splitlines() if ln.strip()), "modes": {}}
    for name, nc, ch in models:
        for mode, how in WRITER_MODES:
            d = ch if mode in ("chars", "words+chars") else nc
            rs = R.read_samples(d, smp, predict, how == "raw", None, extra + (("--chars",) if how == "chars" else ()))
            per[name, mode] = R.per_sample(rs)
            s = R.summary(per[name, mode])
            res["modes"].setdefault(mode, {})[name] = s
            print(f"{name:12s} {mode:12s} n {s['n']} cer {100 * s['cer']:.1f} wer {100 * s['wer']:.0f} "
                  f"top1 {100 * s['top1']:.0f} top3 {100 * s['top3']:.0f}", flush=True)
    first = models[0][0]
    for name, *_ in models[1:]:
        for mode, _ in WRITER_MODES:
            b = R.paired_bootstrap(per[name, mode], per[first, mode])
            res["modes"][mode][f"{name}-{first}"] = b
            print(f"{name} − {first} {mode:12s} CER {100 * b['diff_lo']:+.1f} to {100 * b['diff_hi']:+.1f} "
                  f"P({name} better) {b['p_a_better']:.2f}", flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1))


def cmd_writerseval(a):
    """Many writers' ink read as new people, per writer (writerseval.py). The dataset is the
    user's own copy (a path; nothing is downloaded), the results go to a private --out."""
    from . import writerseval as W
    from .lines import CHARS4
    path = Path(a.dataset)
    charset = {"common": W.COMMON, "model4": CHARS4}[a.charset]
    if a.format == "brush":
        conv = {"line_scale": a.line_scale, "pause": a.pause, "step": a.step}
        only = json.loads(Path(a.split).read_text())["parts"][a.part] if a.split else None
        data = W.load_brush(path, a.per_writer, a.seed, charset, conv, only)
    else:
        data = W.load_samples(path, a.per_writer, a.seed, charset)
    if a.writers:
        data = dict(list(data.items())[:a.writers])
    models = []
    for spec in a.models:  # NAME=NOCHARS_DIR,CHARS_DIR
        name, dirs = spec.split("=", 1)
        nc, ch = (Path(d).resolve() for d in dirs.split(","))
        models.append((name, nc, ch))
    s = W.run(data, models, a.modes, Path(a.out), Path(a.predict).resolve(), a.workers)
    print(W.report(s), flush=True)


def cmd_brushset(a):
    """BRUSH as ink sets for training (realink.py): train, dev and split.json, private."""
    from .realink import write_brush
    r = write_brush(Path(a.dataset), Path(a.out), a.seed, a.copies, tuple(a.line_scale),
                    test_writers=a.test_writers, dev_writers=a.dev_writers, held_texts=a.held_texts)
    print(json.dumps({"counts": r["counts"], **{p: {k: r[p][k] for k in ("lines", "points")} for p in ("train", "dev")}},
                     indent=1))


def cmd_personset(a):
    """A person's split as ink sets for training (realink.py), private."""
    from .realink import write_person
    r = write_person(Path(a.split), Path(a.out), a.copies, a.seed)
    print(json.dumps({p: {k: r[p][k] for k in ("lines", "points", "samples")} for p in r}, indent=1))


def cmd_inkread(a):
    """Synthetic ink sets through the recognizer program (realeval.py ink_samples): the
    readings of one model directory in one mode, with the letter height known or the line
    alone, saved per line beside a summary."""
    from . import realeval as R
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    env = {"GLASSINE_HANDWRITING_SIZE": f"{R.XH_EM},{2.5 * R.XH_EM}"} if a.ref == "xh" else {}
    if a.size_env:
        env["GLASSINE_HANDWRITING_SIZE"] = a.size_env
    res = {}
    ratios = None
    if a.measured:  # the writers' small letters measured over all the sets (lone characters have none)
        from .inkmeasure import writer_ratios
        from .inkstore import InkStore
        ratios = writer_ratios([InkStore(Path(s)) for s in a.sets])
    for ink in a.sets:
        name = Path(ink).name
        smp = Path(a.samples or out) / f"{name}{f'.first{a.limit}' if a.limit else ''}.samples.jsonl"
        if not smp.exists():
            tmp = smp.with_suffix(".tmp")
            R.ink_samples(Path(ink), tmp, a.limit, ratios)
            tmp.rename(smp)
        if a.prepare:
            continue
        kinds = [json.loads(ln)["kind"] for ln in smp.read_text().splitlines()]
        extra = ["--base", R.GUIDE] + (["--chars"] if a.mode == "chars" else []) + \
            (["--field-by-kind"] if a.field_by_kind else [])
        rs = R.read_samples(Path(a.models).resolve(), smp.resolve(), Path(a.predict).resolve(), a.mode == "raw",
                            None, extra, env)
        for r, k in zip(rs, kinds):
            r["kind"] = k
        (out / f"{name}.{a.tag}.json").write_text(json.dumps(rs))
        p = R.per_sample(rs)
        res[name] = R.summary(p)
        print(f"{a.tag:28s} {name:28s} " + " ".join(f"{k} {v:.4f}" if isinstance(v, float) else f"{k} {v}"
                                                   for k, v in res[name].items()), flush=True)
    (out / f"summary.{a.tag}.json").write_text(json.dumps(res, indent=1))


def _short_lines(c, max_words):
    return [t for t in c["train"] if len(t.split()) <= max_words]


def cmd_synthset(a):
    """Training ink in the 158 fonts with a measured parameter preset (synth PRESETS)."""
    from glassine_synth.ink import preset
    raw = Path("build/text")
    c = text.corpus(raw, seed=1)
    prov = {"fonts": _provenance(), "text": json.loads((raw / "manifest.json").read_text())}
    texts = _short_lines(c, a.max_words)[:a.lines]
    out = Path("build/data") / a.name
    build(out, texts, font_splits()["train"], preset(a.preset), a.seed, FONTS, a.workers, prov)


def cmd_hand(a):
    """Text rendered in a person's own letters (hand.py), from the training sessions only."""
    from glassine_synth.ink import preset
    from .hand import covered, shapes, tables
    from .real import load_split
    sp = load_split(Path(a.real))
    sh, rep = shapes(sp["train"])
    held = {s["text"] for s in sp["dev"] + sp["test"]}
    raw = Path("build/text")
    c = text.corpus(raw, seed=1)
    rng = np.random.default_rng(a.seed)
    pool = [w.strip() for w in (Path("../samples/prompts/en/words.txt")).read_text().splitlines() if w.strip()]
    prompts = [w.strip() for w in Path("../models/en/prompts.txt").read_text().splitlines() if w.strip()]
    ok = lambda t: covered(t, sh) and t not in held
    pool, prompts = [t for t in pool if ok(t)], [t for t in prompts if ok(t)]
    books = [t for t in _short_lines(c, 4) if ok(t)]
    n = a.lines
    texts = (rng.choice(pool, n // 2).tolist() + rng.choice(prompts, n // 10).tolist()
             + books[:n - n // 2 - n // 10])
    rng.shuffle(texts)
    params = preset("real1", join=0.0, reverse_loop=0.0, delay=0.0, extra_lift=0.0, warp=0.015,
                    zones=0.08, asc_scale=1.0, desc_scale=1.0, font_per_letter=1.0)
    out = Path("build/data") / a.name
    build(out, texts, [], params, a.seed, FONTS, a.workers, None, tables(sh, a.tables, a.seed))
    rep.update(lines=len(texts), pool_words=len(pool), prompt_lines=len(prompts), book_lines=len(books),
               missing=sorted(set(text.CHARS) - set(sh) - {" "}))
    (out / "hand.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))


def cmd_inkstats(a):
    """Real ink measured beside generated ink of the same texts (glassine_synth.stats):
    aggregate numbers only."""
    from glassine_synth.ink import PRESETS, line
    from glassine_synth.stats import measure
    from .data import tables
    from .real import load_split
    sp = load_split(Path(a.real))
    real = sp["train"] + sp["dev"]  # the held-back session stays out of every choice
    fonts = sorted(np.random.default_rng(3).permutation(font_splits()["train"])[:a.fonts].tolist())
    T = tables(FONTS, fonts, a.workers)
    res = {"real": measure(real)}
    for name in ["default"] + a.presets:
        p = Params() if name == "default" else Params(**PRESETS[name])
        rng = np.random.default_rng(0)
        out = []
        for s in real:
            g = line(s["text"], T, rng, p)
            out.append({"text": s["text"], "strokes": [q.tolist() for q in g["strokes"]], "spans": g["spans"]})
        res[name] = measure(out)
    print(json.dumps(res, indent=1))


def _gen4_setup(a, held: bool):
    from . import build4, gen, lines as L
    from .timing import Fit
    L.fetch_names(Path(a.names), a.given)
    lg = L.Generator(Path("build/text"), Path(a.gclm), Path(a.names), Path(a.wordlist), held=held, given=a.given)
    from .timing import FIT_ONE_WRITER
    f = json.loads(Path(a.fit).read_text()) if a.fit else {"fit": FIT_ONE_WRITER}
    fit = Fit(**f["fit"])
    return build4, gen, L, lg, fit, f


def cmd_gen4(a):
    """The next base model's ink sets (build4.py): train, val, dev, dev_kinds, the test sets."""
    import hashlib
    from . import writers as W
    build4, gen, L, lg, fit, fj = _gen4_setup(a, held=False)
    _, _, _, lg_held, _, _ = _gen4_setup(a, held=True)
    sp = font_splits()
    out = Path(a.out)
    big = build4.bigrams(lg.train_s)
    every = sp["train"] + sp["dev"] + sp["test"]
    tabs, covers = build4.font_tables(every, Path("build/cache"), a.workers)
    by = dict(tabs)
    extra = []
    if a.pool_style:  # a letter model's spacing as one style among the fonts'
        extra = [tuple(map(float, x.split(","))) for x in a.pool_style]
    contrib = W.load(Path(a.writers), set(a.test_contributor)) if a.writers else []
    extra += [(w.spacing.get("letter", 0.4), w.spacing.get("word", 1.2)) for w, _ in contrib]
    calib = gen.CALIB
    if a.calib:
        g0, g1, w0, w1 = map(float, a.calib.split(","))
        calib = {"gap": (g0, g1), "word": (w0, w1)}
    pool, open0, per_font = build4.style_pool([(f, by[f]) for f in sp["train"]], big, extra, calib)
    fw = lambda fonts: gen.FontWriter([(f, by[f]) for f in fonts], covers)
    mk = lambda fonts: gen.Generator(fw(fonts), fit, pool, open0)
    c = text.corpus(Path("build/text"), seed=1)
    groups = {"heldout": HELD_OUT, "newprint": sp["test-print"], "newscript": sp["test-script"]}
    kinds = [k for k in L.SHARES if k != "prose"]
    base_meta = {"generator": "glassine_train.gen", "alphabet": L.CHARS4, "line_kinds": L.SHARES,
                 "sources": build4.source_pins(Path(a.fit) if a.fit else None, a.given), "style_pool": len(pool), "open0": open0,
                 "fonts": {"train": sp["train"]}, "provenance": {"fonts": _provenance()}}
    jobs = []
    if not a.only or "train" in a.only or a.train_name in a.only:
        # --kinds: lines of these kinds only, in their shares (to replace them in a set)
        shares = {k: L.SHARES[k] / sum(L.SHARES[j] for j in a.kinds) for k in a.kinds} if a.kinds else L.SHARES
        tr = lg.lines(a.lines, a.seed, shares)
        jobs.append((a.train_name, tr, sp["train"], a.seed + 1))
    if contrib and (not a.only or "train_contributors" in a.only):
        # contributors as writers: each writes --contrib-lines lines of the text it covers
        rng = np.random.default_rng(a.seed + 7)
        cj = []
        for wi, (w, _) in enumerate(contrib):
            texts = [x for x in lg.lines(a.contrib_lines * 3, int(rng.integers(1 << 30))) if w.covers(x[0])]
            cj += [(t, k, s_, wi) for (t, k, s_) in build4.jobs_of(texts[:a.contrib_lines], int(rng.integers(1 << 30)))]
        g = gen.Generator(None, fit, pool, open0)
        build4.render_set(out / "train_contributors", cj, g, [w for w, _ in contrib], a.workers,
                          dict(base_meta, contributors=[m for _, m in contrib]))
    jobs.append(("val", [(t, "prose") for t in c["val"][:2000]], ORIG_TRAIN, 12))
    jobs.append(("dev", [(t, "prose") for t in c["val"][:2000]], sp["dev"], 12))
    jobs.append(("dev_kinds", [(lg_held.one(k, np.random.default_rng(1000 + i)), k) for k in kinds
                               for i in range(100)], sp["dev"], 14))
    test_kinds = [(lg_held.one(k, np.random.default_rng(5000 + i)), k) for k in kinds for i in range(100)]
    for name, n in EVAL_SIZES.items():
        texts = [(t, {"test": "prose", "test_words": "words", "test_symbols": "symbols",
                      "test_letters": "uniform"}[name]) for t in c[name][:n]]
        for g, fonts in groups.items():
            jobs.append((f"{name}-{g}", texts, fonts, 13))
    for g, fonts in groups.items():
        jobs.append((f"test_kinds-{g}", test_kinds, fonts, 15))
    # lone characters whose forms differ mostly in size, and the marks a dot or tick makes,
    # written alone at their true size (FR-018): 40 of each per held-out group
    for g, fonts in groups.items():
        jobs.append((f"test_lone-{g}", [(ch, "lone") for ch in LONE_SIZE for _ in range(40)], fonts, 16))
    for f in HELD_OUT + sp["test-print"] + sp["test-script"]:
        jobs.append((f"test-font-{f}", [(t, "prose") for t in c["test"][:300]], [f], 13))
    for name, texts, fonts, seed in jobs:
        if a.only and not any(name.startswith(o) for o in a.only):
            continue
        texts = [(t, k) for t, k in texts if all(ch in L.CHARS4 for ch in t)]
        meta = dict(base_meta, fonts=sorted(fonts), seed=seed)
        build4.render_set(out / name, build4.jobs_of(texts, seed), mk(fonts), None, a.workers, meta)
    old = json.loads((out / "manifest.json").read_text()) if a.only and (out / "manifest.json").exists() else None
    man = {"sets": {}, "sources": base_meta["sources"], "alphabet": L.CHARS4, "line_kinds": L.SHARES,
           "style_pool": {"fonts": per_font, "extra": len(extra), "calibration": calib}, "timing_fit": {k: v for k, v in fj["fit"].items()},
           "contributors": [m for _, m in contrib]}
    for d in sorted(out.iterdir()):
        if (d / "meta.json").exists():
            m = json.loads((d / "meta.json").read_text())
            man["sets"][d.name] = {k: m[k] for k in ("lines", "strokes", "points", "characters", "kinds", "bytes",
                                                     "seed", "fonts") if k in m}
    if old:  # a partial run (--only) adds its sets to the manifest it finds, the rest stays
        old["sets"].update({k: v for k, v in man["sets"].items() if any(k.startswith(o) for o in a.only)})
        man = old
    (out / "manifest.json").write_text(json.dumps(man, indent=1, default=float))


def cmd_gen4_letters(a):
    """Lines in a letter model's own versions, spacing and timing (gen.LetterWriter): one
    synthetic writer, timed by --fit as fitted (no writer drawn around it) and with the fit's
    delayed-stroke habits. Its output stays where the letter model is (a person's
    handwriting): never in a repository, a release or a log."""
    from . import build4
    build4_, gen, L, lg, fit, fj = _gen4_setup(a, held=False)
    rows = [json.loads(ln) for ln in Path(a.letters).read_text().splitlines() if ln.strip()]
    lw = gen.LetterWriter(rows, a.name)
    lw.habits = fj.get("delays", {})  # the writer's own, when the fit is of their ink
    texts = []
    rng = np.random.default_rng(a.seed)
    tries = 0
    while len(texts) < a.lines and tries < a.lines * 50:
        k = list(L.SHARES)[int(rng.choice(len(L.SHARES), p=np.array(list(L.SHARES.values()))))]
        if k == "prose":
            ch = text.chunks([lg.train_s[int(rng.integers(len(lg.train_s)))]], rng)
            if not ch:
                continue
            t = ch[int(rng.integers(len(ch)))]
        else:
            t = lg.one(k, rng)
        tries += 1
        if lw.covers(t):
            texts.append((t, k))
    g = gen.Generator(None, fit, [(lw.spacing.get("letter", 0.4), lw.spacing.get("word", 1.2))], residual=1.0)
    meta = {"generator": "glassine_train.gen", "writer": "letter model", "alphabet": L.CHARS4,
            "covered": "".join(sorted(lw.chars)), "seed": a.seed, "letters_inputs": rows[0].get("inputs")}
    build4_.render_set(Path(a.out), build4_.jobs_of(texts, a.seed), g, lw, a.workers, meta)


def cmd_brushletters(a):
    """Letter models of BRUSH's training writers (brushletters.py), private: OUT/<n>/model
    and OUT/hands.json."""
    from . import brushletters as BL
    from .lines import CHARS4
    from .realink import brush_split
    from .alignrun import _writer_xh
    by, _ = brush_split(Path(a.dataset), a.seed, a.test_writers, a.dev_writers, a.held_texts, CHARS4)
    per = {}
    for ln in by[a.part]:
        per.setdefault(ln.writer, []).append(ln)
    if a.writers:
        per = {w: per[w] for w in sorted(per)[:a.writers]}
    man = BL.build_all(per, Path(a.bin), Path(a.models), Path(a.out), a.way, Path(a.aligner) if a.aligner else None,
                       _writer_xh(by[a.part]) if a.way == "aligner" else None, a.workers, Path(a.tmp) if a.tmp else None)
    cov = [len(h["covered"]) for h in man["hands"]]
    print(json.dumps({"hands": len(cov), "covered_median": float(np.median(cov)) if cov else 0}))


def cmd_gen4_hands(a):
    """Lines in the hands of a hands list (hands.py): per hand --lines lines of every kind
    (lines.SHARES) in its own hand and --mixed of them with words in 2-3 hands, as two ink
    sets (OUT/hands, OUT/hands_mixed). What they write is the hands' (private)."""
    from . import hands as H
    build4, gen, L, lg, fit, fj = _gen4_setup(a, held=False)
    hands = H.load_hands(Path(a.hands), a.seed)
    tabs, covers = build4.font_tables(a.donor_fonts or font_splits()["train"], Path("build/cache"), a.workers)
    donor = gen.FontWriter(tabs, covers)
    g = gen.Generator(None, fit, [(0.4, 1.2)], residual=1.0)
    rng = np.random.default_rng(a.seed)
    cal = [t for t, _ in lg.lines(a.calibrate_lines, int(rng.integers(1 << 30)), {"prose": 1.0})]
    H.calibrate_all(g, hands, cal, a.workers, a.seed)
    n_mixed = int(round(a.lines * a.mixed))
    jobs = {"hands": [], "hands_mixed": []}
    for h in hands:
        texts = []
        for t, k in lg.lines(a.lines * 4, int(rng.integers(1 << 30))):
            if len(texts) >= a.lines:
                break
            if all(c in L.CHARS4 for c in t) and H.plan_line(t, hands, hands.index(h), False, donor,
                                                               np.random.default_rng(0)) is not None:
                texts.append((t, k))
        for i, (t, k, s) in enumerate(build4.jobs_of(texts, int(rng.integers(1 << 30)))):
            jobs["hands_mixed" if i < n_mixed else "hands"].append((t, k, s, hands.index(h), i < n_mixed))
    meta = {"generator": "glassine_train.hands", "alphabet": L.CHARS4, "seed": a.seed, "hands": len(hands),
            "joins": {"curve": H.CURVE, "calibrated": [round(h.join, 3) for h in hands]},
            "donor_max": H.DONOR_MAX}
    out = Path(a.out)
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, js in jobs.items():
        m = H.render(out / name, js, g, hands, donor, a.workers, dict(meta, mixed=name == "hands_mixed"))
        print(f"{name}: {m['lines']} lines, donor {m['donor_lines']}, mixed {m['mixed_lines']}, failed {m['failed']}")


def cmd_dynfit(a):
    """The timing model (timing.py) fitted to real ink, with delayed strokes and spacing:
    aggregate numbers only, written to --out."""
    from . import inkmeasure as M, timing as T
    from .real import load_split, usable
    sp = load_split(Path(a.real))
    ss = [s for s in sp["train"] + sp["dev"] if usable(s)]  # never the test session
    xs, xh = M.to_xh(ss)
    d, flags = M.delays(xs)
    f, rep = T.fit([(s, q) for s, q, _ in xs], workers=a.workers, cache=Path(a.cache) if a.cache else None)
    f = T.fit_pauses(f, [(s, q) for s, q, _ in xs], flags)
    out = {"fit": T.asdict_fit(f), "report": rep, "delays": d, "pauses": M.pauses(xs, flags),
           "gaps": M.letter_gaps(xs), "dynamics": T.dynamics([(s, q) for s, q, _ in xs]), "samples": len(xs)}
    Path(a.out).write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({k: v for k, v in out.items() if k != "dynamics"}, indent=1, default=float))


def cmd_featurize(a):
    """Model input from an ink set: the shipped features (--ref none) or the reference-height
    prototype (features_ref; --ref xh, line, or mix: the line alone for --line-share)."""
    import multiprocessing as mp
    from .features import NFEAT, NFEAT_REF
    from .inkstore import InkStore
    src = Path(a.ink)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    st = InkStore(src)
    n = len(st) if not a.limit else min(a.limit, len(st))
    rng = np.random.default_rng(a.seed)
    use_line = rng.random(n) < (a.line_share if a.ref == "mix" else 1.0 if a.ref == "line" else 0.0)
    ratios = None
    if a.measured:
        from .inkmeasure import writer_ratios
        ratios = writer_ratios(st)
    global _FZ
    _FZ = (st, a.ref, use_line, ratios)
    offsets = [0]
    tmp = out / "feat.f16.tmp"
    with open(tmp, "wb") as fh, mp.get_context("fork").Pool(a.workers) as pool:
        for f in pool.imap(_featurize_one, range(n), chunksize=256):
            fh.write(f.astype(np.float16).tobytes())
            offsets.append(offsets[-1] + len(f))
    tmp.rename(out / "feat.f16")
    np.save(out / "offsets.npy", np.array(offsets, np.int64))
    (out / "texts.json").write_text(json.dumps([r["text"] for r in st.rows[:n]]))
    meta = {"n": n, "points": offsets[-1], "nfeat": NFEAT if a.ref == "none" else NFEAT_REF, "ref": a.ref,
            "line_share": a.line_share if a.ref == "mix" else None, "from": str(src), "ink_meta": st.meta,
            "measured": ratios}
    (out / "meta.json").write_text(json.dumps(meta, indent=1, default=float))
    print(f"{out}: {n} lines, {offsets[-1]} points")


_FZ = None


def _featurize_one(i):
    from .features import features, features_ref, ref_height
    st, ref, use_line, ratios = _FZ
    strokes, row = st[i]
    if ref == "none":
        return features(strokes)
    xh = row["xh"] * ratios.get(row["writer"], ratios["*"]) if ratios else row["xh"]
    h = ref_height(None if use_line[i] else xh, row["line"])
    return features_ref(strokes, h, row["base"])


def cmd_inkcheck(a):
    """Measures of an ink set against real ink's (dynfit's file): dynamics, delayed strokes,
    letter gaps, line kinds, sizes; and a sheet of lines to look at. Aggregates only."""
    from . import build4, inkmeasure as M, timing as T
    from .inkstore import InkStore
    st = InkStore(Path(a.ink))
    rng = np.random.default_rng(a.seed)
    idx = rng.choice(len(st), min(a.n, len(st)), replace=False)
    xs = []
    for i in idx:
        strokes, row = st[i]
        xs.append(([np.c_[q[:, :2] / row["xh"], q[:, 2]] for q in strokes], row["spans"], row["text"]))
    d, flags = M.delays(xs)
    syn = {"dynamics": T.dynamics([(s, q) for s, q, _ in xs]), "delays": d, "pauses": M.pauses(xs, flags),
           "gaps": M.letter_gaps(xs, lambda strokes, own, text: 0.0), "alignment": M.alignment(xs)}
    out = {"set": st.meta.get("kinds"), "lines": len(st), "bytes": st.meta.get("bytes"), "synthetic": syn}
    if a.real:
        r = json.loads(Path(a.real).read_text())
        out["real"] = {k: r[k] for k in ("dynamics", "delays", "pauses", "gaps") if k in r}
    if a.sheet:
        build4.sheet(st, [int(i) for i in idx[:a.sheet_lines]], Path(a.sheet))
    print(json.dumps(out, indent=1, default=float))


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
    t.add_argument("--select", default="dev", help="datasets for model selection (fonts never trained on), "
                   "comma-separated: the mean of their CERs; '' = val")
    t.add_argument("--run", required=True)
    t.add_argument("--minutes", type=float, default=60)
    t.add_argument("--steps-per-s", type=float, help="measured rate, sets the LR schedule length")
    t.add_argument("--init", help="a checkpoint to go on from (its architecture; --conv, --hidden etc. are ignored)")
    t.add_argument("--resume", action="store_true", help="go on with a stopped run from its last.pt (optimizer, "
                   "schedule and place in the data as they were; give the run's other options again)")
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
    x.add_argument("--data", help="dataset to check and time on (default: test-heldout, .xh for a reference model)")
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
    cl = sub.add_parser("charlm", help="the character model from the training books, as a release tarball")
    cl.add_argument("--lang", default="en")
    cl.add_argument("--version", type=int, required=True)
    cl.add_argument("--order", type=int, default=5)
    cl.add_argument("--min-hist", type=int, default=20)
    cl.add_argument("--out", default="build/release")
    rl = sub.add_parser("real", help="a person's sample store, split by writing session")
    rl.add_argument("store", help="directory with samples.jsonl and samples.meta")
    rl.add_argument("--out", default="build/real")
    rl.add_argument("--keep-test", help="an earlier split.json (or a file of ids): its test set stays the test set")
    ft = sub.add_parser("finetune", help="fine-tune on real ink mixed with synthetic ink")
    ft.add_argument("--test-ids", help="more ids no training may see (beside the split's test-ids.txt)")
    ft.add_argument("--run", required=True)
    ft.add_argument("--init", default="build/runs/m2/best.pt")
    ft.add_argument("--real", default="build/real")
    ft.add_argument("--synth", nargs="+", default=["train-f158-var-x2"], help="synthetic datasets (equal parts)")
    ft.add_argument("--synth-share", type=float, default=0.5, help="synthetic part of every batch (fonts + hand)")
    ft.add_argument("--hand", help="dataset rendered in the person's own letters (glassine-train hand)")
    ft.add_argument("--hand-frac", type=float, default=0.5, help="the hand's part of the synthetic share")
    ft.add_argument("--synth-dev", default="dev")
    ft.add_argument("--synth-dev-limit", type=int, default=500)
    ft.add_argument("--steps", type=int, default=2500)
    ft.add_argument("--lr", type=float, default=3e-4)
    ft.add_argument("--batch", type=int, default=32)
    ft.add_argument("--l2sp", type=float, default=0.01)
    ft.add_argument("--adv", type=float, default=0.0, help="domain head weight (0 = none)")
    ft.add_argument("--eval-every", type=int, default=100)
    ft.add_argument("--patience", type=int, default=8)
    ft.add_argument("--avg", type=int, default=5)
    ft.add_argument("--threads", type=int, default=6)
    ft.add_argument("--workers", type=int, default=3)
    ft.add_argument("--seed", type=int, default=0)
    we = sub.add_parser("writereval", help="another writer's samples, read as a new person (evaluation only)")
    we.add_argument("samples", help="a store directory or a samples file")
    we.add_argument("--models", nargs="+", required=True, help="NAME=NOCHARS_DIR,CHARS_DIR; the first is the baseline")
    we.add_argument("--predict", default="build/predict")
    we.add_argument("--base", default="0.6666667", help="the guides' place in each line of the area")
    we.add_argument("--out", help="aggregates as JSON")
    wv = sub.add_parser("writerseval", help="many writers' ink read as new people, per writer (evaluation only)")
    wv.add_argument("dataset", help="the dataset as you obtained it (BRUSH: its directory or ZIP; see brush.py)")
    wv.add_argument("--format", choices=["brush", "samples"], default="brush")
    wv.add_argument("--models", nargs="+", required=True, help="NAME=NOCHARS_DIR,CHARS_DIR; the first is the baseline")
    wv.add_argument("--modes", nargs="+", default=["words+chars"], choices=["ink", "chars", "words", "words+chars"])
    wv.add_argument("--out", required=True, help="a PRIVATE directory: per-sample ink and readings go here")
    wv.add_argument("--predict", default="build/predict")
    wv.add_argument("--per-writer", type=int, default=40, help="lines per writer, drawn at random (0 = all)")
    wv.add_argument("--writers", type=int, help="only the first N writers")
    wv.add_argument("--charset", choices=["common", "model4"], default="common",
                    help="keep lines whose text every model can write (common) or model 4 can")
    wv.add_argument("--seed", type=int, default=0)
    wv.add_argument("--line-scale", type=float, default=1.0, help="brush: the area's line over the box height")
    wv.add_argument("--pause", type=float, default=0.11, help="brush: seconds between strokes")
    wv.add_argument("--step", type=float, default=0.0, help="brush: densify points to this many pixels apart")
    wv.add_argument("--workers", type=int, default=8)
    wv.add_argument("--split", help="brush: a split.json of brushset; read only the lines of --part")
    wv.add_argument("--part", default="test", choices=["train", "dev", "test"])
    bs = sub.add_parser("brushset", help="BRUSH as ink sets for training, split by writer and text (realink.py)")
    bs.add_argument("dataset", help="BRUSH as you obtained it from its official source (directory or ZIP)")
    bs.add_argument("--out", required=True, help="a PRIVATE directory (made 0700)")
    bs.add_argument("--seed", type=int, default=5)
    bs.add_argument("--copies", type=int, default=4, help="training copies per line (new line scale and pauses)")
    bs.add_argument("--line-scale", type=float, nargs=2, default=[0.7, 2.5], help="the area's line over the box, log-uniform")
    bs.add_argument("--test-writers", type=float, default=0.2)
    bs.add_argument("--dev-writers", type=float, default=0.05)
    bs.add_argument("--held-texts", type=float, default=0.2)
    ps = sub.add_parser("personset", help="a person's split (glassine-train real) as ink sets for training (realink.py)")
    ps.add_argument("split", help="the split's directory (its test session never trains)")
    ps.add_argument("--out", required=True, help="a PRIVATE directory (made 0700)")
    ps.add_argument("--copies", type=int, default=6, help="copies per sample, new drawings after the first")
    ps.add_argument("--seed", type=int, default=5)
    re_ = sub.add_parser("realeval", help="real held-back ink through the recognizer program")
    re_.add_argument("--run", default="")
    re_.add_argument("--ckpt", default="best.pt")
    re_.add_argument("--name", help="result name (default: the run)")
    re_.add_argument("--models", help="a released model directory instead of exporting the run")
    re_.add_argument("--head", help="an adaptation file to read with as well")
    re_.add_argument("--adapt", action="store_true", help="also a fresh adaptation on the training sessions")
    re_.add_argument("--adapt-store", default="store-trainsessions", help="the store a fresh adaptation learns from")
    re_.add_argument("--test-ids", help="more ids an adaptation must not learn from (beside the split's test-ids.txt)")
    re_.add_argument("--adapt-arg", action="append", default=[], help="an option for adapt (e.g. --min-held=5)")
    re_.add_argument("--cutoff", action="store_true", help="the exported model records its training data's end")
    re_.add_argument("--real", default="build/real")
    re_.add_argument("--predict", default="build/predict", help="the word predictor's models (words)")
    re_.add_argument("--sets", nargs="+", default=["test"])
    re_.add_argument("--synth-dev", nargs="*", default=["dev"])
    re_.add_argument("--threads", type=int, default=4)
    re_.add_argument("--out", default="build/realeval", help="where the results go (a writer's own: a private directory)")
    re_.add_argument("--modes", nargs="+", default=["raw", "words"], choices=["raw", "chars", "words"],
                     help="ink alone, the character model alone (read … chars), the field's reading")
    re_.add_argument("--letters", help="a store (in --real) whose samples are the person's letters for the size")
    re_.add_argument("--base", type=float, help="the area's first guide at this many line heights below its top")
    ir = sub.add_parser("inkread", help="synthetic ink sets read by the recognizer program")
    ir.add_argument("sets", nargs="+", help="ink set directories (gen4)")
    ir.add_argument("--models", required=True, help="a model directory (LANG/recognizer.ghm, chars.gclm or not)")
    ir.add_argument("--mode", default="raw", choices=["raw", "chars", "words"])
    ir.add_argument("--ref", default="xh", choices=["xh", "line"], help="the letter height known, or the line alone")
    ir.add_argument("--size-env", help="$GLASSINE_HANDWRITING_SIZE instead (e.g. noweigh, off)")
    ir.add_argument("--predict", default="build/predict")
    ir.add_argument("--out", required=True)
    ir.add_argument("--tag", required=True, help="name of this reading in the output files")
    ir.add_argument("--limit", type=int)
    ir.add_argument("--samples", help="where the sets' sample files are kept (default --out), shared between runs")
    ir.add_argument("--prepare", action="store_true", help="only write the sample files")
    ir.add_argument("--field-by-kind", action="store_true",
                    help="each line in the field of its kind (a date in a date field; eval-samples.py KIND_FIELD), "
                         "not all in a text field")
    ir.add_argument("--measured", action="store_true",
                    help="scale each line by its writer's letter height as the recognizer measures it, not the "
                         "generator's (keep these sample files apart: --samples)")
    ss = sub.add_parser("synthset", help="training ink of short lines with a measured preset")
    ss.add_argument("--name", default="train-f158-real1")
    ss.add_argument("--preset", default="real1")
    ss.add_argument("--lines", type=int, default=120_000)
    ss.add_argument("--max-words", type=int, default=3)
    ss.add_argument("--seed", type=int, default=41)
    ss.add_argument("--workers", type=int, default=6)
    hd = sub.add_parser("hand", help="text rendered in a person's own letters (training sessions)")
    hd.add_argument("--name", default="hand")
    hd.add_argument("--real", default="build/real")
    hd.add_argument("--lines", type=int, default=40_000)
    hd.add_argument("--tables", type=int, default=16)
    hd.add_argument("--seed", type=int, default=43)
    hd.add_argument("--workers", type=int, default=4)
    ist = sub.add_parser("inkstats", help="real ink measured beside generated ink of the same texts")
    ist.add_argument("--real", default="build/real")
    ist.add_argument("--presets", nargs="*", default=["real1"])
    ist.add_argument("--fonts", type=int, default=40, help="random training fonts to render with")
    ist.add_argument("--workers", type=int, default=8)
    ms = sub.add_parser("mergeset", help="a dataset with some kinds of lines replaced")
    ms.add_argument("--base", required=True)
    ms.add_argument("--drop-kinds", nargs="*", default=[])
    ms.add_argument("--add", nargs="*", default=[])
    ms.add_argument("--out", required=True)
    g4 = sub.add_parser("gen4", help="the next base model's ink sets (build4.py)")
    g4.add_argument("--out", default="build/ink4")
    g4.add_argument("--lines", type=int, default=600_000)
    g4.add_argument("--seed", type=int, default=41)
    g4.add_argument("--workers", type=int, default=8)
    g4.add_argument("--only", nargs="*", help="set name prefixes")
    g4.add_argument("--kinds", nargs="*", help="the training set's lines of these kinds only (lines.SHARES)")
    g4.add_argument("--train-name", default="train", help="the training set's name")
    g4.add_argument("--fit", help="timing fit (glassine-train dynfit); default timing.FIT_ONE_WRITER")
    g4.add_argument("--gclm", default="build/release/glassine-handwriting-chars-en-1/chars.gclm")
    g4.add_argument("--names", default="build/names")
    g4.add_argument("--calib", help="GAP_MEDIAN,GAP_SD,WORD_MEDIAN,WORD_SIGMA: where the fonts' spacing moves (gen.CALIB)")
    g4.add_argument("--given", default="ssa", choices=["ssa", "census"], help="the given-name lists (lines.GIVEN_SOURCES)")
    g4.add_argument("--wordlist", default="build/lexicon/en/words.txt", help="words a pseudo-word must not be")
    g4.add_argument("--pool-style", nargs="*", default=[], help="GAP,WORD: a letter model's spacing as one style")
    g4.add_argument("--writers", help="contributors' letter models (writers.py)")
    g4.add_argument("--test-contributor", nargs="*", default=[], help="contributors in the real test set")
    g4.add_argument("--contrib-lines", type=int, default=20_000, help="lines per contributor writer")
    gl = sub.add_parser("gen4-letters", help="lines in one letter model's own letters")
    gl.add_argument("letters", help="a letter model file (letters/model)")
    gl.add_argument("--out", required=True)
    gl.add_argument("--name", default="letters")
    gl.add_argument("--lines", type=int, default=20_000)
    gl.add_argument("--seed", type=int, default=47)
    gl.add_argument("--workers", type=int, default=8)
    gl.add_argument("--fit", required=True)
    gl.add_argument("--gclm", default="build/release/glassine-handwriting-chars-en-1/chars.gclm")
    gl.add_argument("--names", default="build/names")
    gl.add_argument("--given", default="ssa", choices=["ssa", "census"], help="the given-name lists (lines.GIVEN_SOURCES)")
    gl.add_argument("--wordlist", default="build/lexicon/en/words.txt")
    bl = sub.add_parser("brushletters", help="letter models of BRUSH's training writers (private)")
    bl.add_argument("dataset", help="the BRUSH directory or ZIP")
    bl.add_argument("--out", required=True)
    bl.add_argument("--bin", required=True, help="the recognizer program")
    bl.add_argument("--models", required=True, help="its models directory")
    bl.add_argument("--way", default="labels", choices=["labels", "aligner"])
    bl.add_argument("--aligner", help="the point aligner's checkpoint (--way aligner)")
    bl.add_argument("--part", default="train", choices=["train", "dev", "test"])
    bl.add_argument("--seed", type=int, default=5)
    bl.add_argument("--test-writers", type=float, default=0.2)
    bl.add_argument("--dev-writers", type=float, default=0.05)
    bl.add_argument("--held-texts", type=float, default=0.2)
    bl.add_argument("--writers", type=int, help="the first N writers only")
    bl.add_argument("--workers", type=int, default=8)
    bl.add_argument("--tmp")
    gh = sub.add_parser("gen4-hands", help="lines in letter models' hands (hands.py; private)")
    gh.add_argument("hands", help="a hands list (brushletters' hands.json)")
    gh.add_argument("--out", required=True)
    gh.add_argument("--lines", type=int, default=200, help="lines per hand")
    gh.add_argument("--mixed", type=float, default=0.3, help="share of them with words in 2-3 hands")
    gh.add_argument("--donor-fonts", nargs="*", help="donor fonts (default: the training fonts)")
    gh.add_argument("--calibrate-lines", type=int, default=20)
    gh.add_argument("--seed", type=int, default=53)
    gh.add_argument("--workers", type=int, default=8)
    gh.add_argument("--fit", help="timing fit (glassine-train dynfit); default timing.FIT_ONE_WRITER")
    gh.add_argument("--gclm", default="build/release/glassine-handwriting-chars-en-1/chars.gclm")
    gh.add_argument("--names", default="build/names")
    gh.add_argument("--given", default="ssa", choices=["ssa", "census"])
    gh.add_argument("--wordlist", default="build/lexicon/en/words.txt")
    df = sub.add_parser("dynfit", help="timing, delayed strokes and spacing of real ink (aggregates)")
    df.add_argument("--real", default="build/real")
    df.add_argument("--out", required=True)
    df.add_argument("--workers", type=int, default=8)
    df.add_argument("--cache", help="the strokes' fitted pulses, kept to refit faster")
    fz = sub.add_parser("featurize", help="model input from an ink set")
    fz.add_argument("ink")
    fz.add_argument("--out", required=True)
    fz.add_argument("--ref", choices=["none", "xh", "line", "mix"], default="none")
    fz.add_argument("--line-share", type=float, default=0.3)
    fz.add_argument("--measured", action="store_true",
                    help="the letter height as the recognizer measures it (each writer's small letters), not the generator's")
    fz.add_argument("--limit", type=int)
    fz.add_argument("--seed", type=int, default=0)
    fz.add_argument("--workers", type=int, default=8)
    ic = sub.add_parser("inkcheck", help="an ink set measured against real ink (aggregates) and a sheet")
    ic.add_argument("ink")
    ic.add_argument("--real", help="dynfit's file")
    ic.add_argument("--n", type=int, default=2000)
    ic.add_argument("--seed", type=int, default=0)
    ic.add_argument("--sheet", help="a PNG of some lines")
    ic.add_argument("--sheet-lines", type=int, default=24)
    a = ap.parse_args(argv)
    {"inkcheck": cmd_inkcheck, "mergeset": cmd_mergeset, "writereval": cmd_writereval, "writerseval": cmd_writerseval, "brushset": cmd_brushset, "personset": cmd_personset, "gen4": cmd_gen4, "gen4-letters": cmd_gen4_letters, "brushletters": cmd_brushletters, "gen4-hands": cmd_gen4_hands, "dynfit": cmd_dynfit, "featurize": cmd_featurize,
     "inkstats": cmd_inkstats, "inkread": cmd_inkread, "synthset": cmd_synthset, "hand": cmd_hand, "realeval": cmd_realeval, "real": cmd_real, "charlm": cmd_charlm, "finetune": cmd_finetune, "lexicon": cmd_lexicon, "tune": cmd_tune, "adapt": cmd_adapt, "data": cmd_data, "train": cmd_train, "eval": cmd_eval, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
