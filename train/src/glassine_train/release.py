"""glassine-train release: a trained run -> a base model package tarball (models/README.md).

Exports the checkpoint to ONNX (and checks it against the run's evaluation), converts it to
the recognizer's ghm file, writes README (recipe, data, results) and LICENSES, adds the
language's prompts.txt, and packs glassine-handwriting-model-LANG-VERSION.tar.gz with a
.sha512 beside it. The same inputs give the same bytes.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TEXT_PY = REPO / "train" / "src" / "glassine_train" / "text.py"
ONNX2GHM = REPO / "recognizer" / "tools" / "onnx2ghm.py"
FILES = ["recognizer.ghm", "README", "LICENSES", "prompts.txt"]

# test sets reported in README: (key in eval-best.json, what it is)
RESULTS = [
    ("test-trainfonts", "test book, training fonts"),
    ("test-heldout", "test book, held-out fonts"),
    ("test-font-kalam", "test book, held-out font kalam"),
    ("test-font-reeniebeanie", "test book, held-out font reeniebeanie"),
    ("test_words-trainfonts", "random words, training fonts"),
    ("test_words-heldout", "random words, held-out fonts"),
    ("test_symbols-trainfonts", "numbers and punctuation, training fonts"),
    ("test_symbols-heldout", "numbers and punctuation, held-out fonts"),
    ("test_letters-trainfonts", "random letters, training fonts"),
    ("test_letters-heldout", "random letters, held-out fonts"),
]

# a model reading the reference height (ghm version 2, ink4 sets): keys get the sets' reference
# suffix (.mxh: the letter height measured as the recognizer measures it, .xh: the generator's)
RESULTS_REF = [
    ("test-heldout", "test book, held-out fonts"),
    ("test-newprint", "test book, new print fonts"),
    ("test-newscript", "test book, new script fonts"),
    ("test_words-heldout", "random words, held-out fonts"),
    ("test_symbols-heldout", "numbers and punctuation, held-out fonts"),
    ("test_letters-heldout", "random letters, held-out fonts"),
    ("test_kinds-heldout", "names, codes, numbers, lone characters, held-out fonts"),
]

MODEL_LICENCE = """\
recognizer.ghm (the model)
  GPL-2.0-or-later, as the rest of GlassineOS/handwriting that produces it (the trainer,
  the recipe and the converter). The full text is in the GlassineOS/handwriting
  repository (LICENSE) and at https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.

  The model contains no text and no font software. It was trained on public-domain
  texts (no terms) rendered as synthetic ink from fonts under the SIL Open Font
  License 1.1 or the Apache License 2.0; these cover the fonts themselves, not what is
  made with them.{real} The
  sources and their licences are listed below so the training can be checked and
  repeated.

README, LICENSES
  GPL-2.0-or-later, as above.

prompts.txt (texts for prompted writing and donation)
  CC0-1.0 (https://creativecommons.org/publicdomain/zero/1.0/): written for the
  project and dedicated to the public domain, apart from traditional pangrams, which
  are in the public domain. Writing them may be donated under CC0-1.0 or CC-BY-4.0.
"""


def texts_of(ink):
    """the training texts: glassine-synth's provenance, or the ink4 generator's sources"""
    if "text" in ink["provenance"]:
        return ink["provenance"]["text"]
    return {k: {f: b[f] for f in ("title", "author", "split", "source")} for k, b in ink["sources"]["books"].items()}


def fail(msg):
    raise SystemExit(f"glassine-train release: {msg}")


def pct(x):
    return f"{100 * x:.1f} %"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(build, run, ckpt, work, data="test-heldout"):
    """glassine-train export in a scratch build dir: ONNX + export.json from the checkpoint,
    the held-out error on `data`"""
    from argparse import Namespace

    from .cli import cmd_export
    rdir = work / "build" / "runs" / run
    rdir.mkdir(parents=True)
    shutil.copy2(build / "runs" / run / ckpt, rdir / ckpt)
    (work / "build" / "data").mkdir()
    (work / "build" / "data" / data).symlink_to((build / "data" / data).resolve())
    cwd = os.getcwd()
    os.chdir(work)
    try:
        cmd_export(Namespace(run=run, ckpt=ckpt, data=data))
    finally:
        os.chdir(cwd)
    return rdir / "recognizer.onnx", json.loads((rdir / "export.json").read_text())


def readme(a, run_args, meta, ev, exp, ghm, test_meta, suffix=None):
    """suffix: a model reading the reference height (ghm version 2), its sets' suffix"""
    ink = meta.get("ink_meta", meta)
    fonts = ink["provenance"]["fonts"]
    texts = texts_of(ink)
    train_fonts = ink["fonts"]
    held = sorted(f for f in fonts if f not in train_fonts)
    L = []
    w = L.append
    w(f"glassine-handwriting base model: {a.lang}, version {a.version}")
    w("")
    w("The base model of glassine-handwriting (GlassineOS/handwriting, docs/recognizer.md)")
    w(f"for the language '{a.lang}'. recognizer.ghm is model file format ghm version {2 if suffix else 1}.")
    if suffix:
        w("It reads the ink scaled by a reference height, the person's small letters as the")
        w("recognizer measures them or else a fifth of the writing area's line, and the ink's")
        w("size as an input of its own (spec 001 FR-018); a recognizer that reads version 2")
        w("is needed.")
    w("")
    w(f"recognizer.ghm  sha256 {sha256(ghm)}")
    w("")
    w("Recipe")
    w("------")
    w(f"Trainer: GlassineOS/handwriting commit {a.commit}, train/ (train/README.md has the")
    w("full recipe, the features and the model). Trained with:")
    w("")
    w("  (cd synth && uv run glassine-synth fetch)")
    if suffix:
        w("  cd train  # then the ink sets and their features as in train/README.md (model 4)")
        chain, r = [], run_args
        while r:                                   # a run that went on from another (--init)
            chain.insert(0, r)
            init = r.get("init")
            r = json.loads((a.build / "runs" / Path(init).parent.name / "args.json").read_text()) if init else None
        for r in chain:
            args = " ".join(f"--{k.replace('_', '-')} {v}" for k, v in r.items()
                            if k not in ("cmd", "run") and v not in (None, False))
            w(f"  uv run glassine-train train --run {r['run']} {args}".replace(" --augment True", " --augment"))
    else:
        w("  cd train && uv run glassine-train data")
        args = " ".join(f"--{k.replace('_', '-')} {v}" for k, v in run_args.items()
                        if k not in ("cmd", "run", "data", "val") and v not in (None, False))
        w(f"  uv run glassine-train train --run {a.run} {args}")
    w(f"  uv run glassine-train release --run {a.run} --lang {a.lang} --version {a.version} ...")
    w("")
    w(f"Model: convolutions + 2-layer bidirectional LSTM + CTC, {exp['params']} parameters;")
    w("the checkpoint with the best " + ("character error on the selection sets (--select)" if run_args.get("select")
                                          else "validation character error") + " was kept.")
    w("")
    w("Data")
    w("----")
    if suffix:
        w(f"Synthetic ink only: {meta['n']} lines (text, names, codes, numbers, lone characters)")
        w("rendered as online handwriting by the trainer's generator (train/README.md, model 4)")
        w(f"from {len(train_fonts)} fonts, seed {ink['seed']}. No ink of any person is in this model or")
        w("its data. The generator's timing (speed, pauses, delayed strokes) and one of its")
        w(f"{ink['style_pool']} spacing styles are aggregate numbers measured on one writer's samples,")
        w("given to the project for training. Names: U.S. Social Security Administration given")
        w("names and 2010 census surnames (public domain).")
    else:
        w(f"Synthetic ink only: {meta['n']} text lines rendered as online handwriting by")
        w(f"glassine-synth (GlassineOS/handwriting synth/) from {len(train_fonts)} fonts, seed {meta['seed']}.")
        w("No real handwriting and no sample of any person is in this model.")
    w("")
    w("Fonts (SIL Open Font License 1.1 or Apache License 2.0, from google/fonts; upstream commit):")
    for d in sorted(fonts):
        f = fonts[d]
        role = "held out, never trained on" if d in held else "training"
        w(f"  {f['name']} ({d}), {role}: {f['commit']}")
    w("")
    w("Text (public domain; Project Gutenberg plain-text editions):")
    for k in sorted(texts, key=lambda k: (texts[k]["split"], texts[k]["title"])):
        t = texts[k]
        w(f"  {t['title']}, {t['author']}: {t['split']}, {t['source']}")
    w("")
    w("Results")
    w("-------")
    w("Character error rate (edit distance / reference length), greedy decoding, no word")
    w("list, measured on synthetic ink. The held-out fonts never appear in training and are")
    if suffix:
        w("a proxy for a new hand.")
    else:
        w("the honest proxy for a new hand; there is no measurement on real handwriting yet.")
    w("")
    if suffix:
        w("Letter height known (" + ("each writer's small letters measured as the recognizer measures"
          " a person's" if suffix == ".mxh" else "the generator's x-height") + "):")
    for k, what in (RESULTS_REF if suffix else RESULTS):
        k = k + (suffix or "")
        if k in ev:
            w(f"  {what:56s} {pct(ev[k]['cer']):>7s}   ({ev[k]['lines']} lines)")
    if suffix:
        w("The real-ink results (one writer, a held-back session; and one other writer) are in")
        w("train/README.md (Results, model 4).")
    w("")
    w(f"Export check: ONNX matches the checkpoint to {exp['max_abs_diff_vs_torch']:.1e}; held-out")
    w(f"character error through ONNX {pct(exp['fp32_test_heldout_cer'])}.")
    if test_meta:
        w("")
        w(f"The test sets were rendered with seed {test_meta.get('seed', '?')} from text never used in training.")
    w("")
    w("Files")
    w("-----")
    w("  recognizer.ghm  the model")
    w("  prompts.txt     texts for prompted writing and donation, one per line")
    w("  LICENSES        the licences of these files and of the training data")
    w("  README          this file")
    return "\n".join(L) + "\n"


REAL_LICENCE = """ It was also trained on real handwriting:
  samples of {writers} writer, given by the writer to the project for training this
  model. The samples are not distributed with the model or anywhere else."""


REAL_SETS = [("test", "the writer's held-back session")]


def readme_finetune(a, run_args, meta, ev, exp, ghm, split, rev, base_rev):
    """README of a model fine-tuned on real ink (glassine-train finetune)."""
    rep = split["report"]
    L = []
    w = L.append
    w(f"glassine-handwriting base model: {a.lang}, version {a.version}")
    w("")
    w("The base model of glassine-handwriting (GlassineOS/handwriting, docs/recognizer.md)")
    w(f"for the language '{a.lang}'. recognizer.ghm is model file format ghm version 1.")
    w("It includes real handwriting from one writer.")
    w("")
    w(f"recognizer.ghm  sha256 {sha256(ghm)}")
    w("")
    w("Recipe")
    w("------")
    w(f"Trainer: GlassineOS/handwriting commit {a.commit}, train/ (train/README.md has the")
    w("full recipe). The synthetic base model (model 2's recipe) was fine-tuned with:")
    w("")
    args = " ".join(f"--{k.replace('_', '-')} {' '.join(map(str, v)) if isinstance(v, list) else v}"
                    for k, v in run_args.items()
                    if k not in ("cmd", "run", "init", "real", "threads", "workers") and v not in (None, False))
    w(f"  uv run glassine-train finetune --run {a.run} {args}")
    w("")
    w(f"Model: convolutions + 2-layer bidirectional LSTM + CTC, {exp['params']} parameters.")
    w("Every batch is a fixed mix of real ink (oversampled, augmented on the pen's points),")
    w("synthetic ink in 158 fonts and text drawn in the writer's own letters; a penalty keeps")
    w("the weights near the synthetic model's; the average of the best checkpoints on the")
    w("writer's dev ink was kept.")
    w("")
    w("Data")
    w("----")
    w(f"Real handwriting: {rep['train'] + rep['dev']} prompted samples of one writer (single words")
    w(f"and characters) from {rep['sessions'] - 1} writing sessions, given by the writer for training;")
    w(f"{rep['train']} to learn from, {rep['dev']} to choose the checkpoint. A later session of")
    w(f"{rep['test']} samples is held back as the test set and was never trained on. The samples")
    w("are not distributed. The model file records when its training data ends: an adaptation")
    w("on the device uses only samples confirmed after that.")
    w(f"Synthetic ink: {meta['n']} text lines rendered by glassine-synth from {len(meta['fonts'])} fonts,")
    w("and text drawn in the writer's letters cut from their training samples.")
    w("")
    fonts, texts = meta["provenance"]["fonts"], meta["provenance"]["text"]
    w("Fonts (SIL Open Font License 1.1 or Apache License 2.0, from google/fonts; upstream commit):")
    for d in sorted(fonts):
        f = fonts[d]
        role = "training" if d in meta["fonts"] else "held out, never trained on"
        w(f"  {f['name']} ({d}), {role}: {f['commit']}")
    w("")
    w("Text (public domain; Project Gutenberg plain-text editions):")
    for k in sorted(texts, key=lambda k: (texts[k]["split"], texts[k]["title"])):
        t = texts[k]
        w(f"  {t['title']}, {t['author']}: {t['split']}, {t['source']}")
    w("")
    w("Results")
    w("-------")
    w("Real ink: the writer's held-back session, read by the recognizer program; character")
    w("error, word error, the top reading exact, the text among the top three. 'words' reads")
    w("with the word list (FR-015), 'ink' without. One writer: not a measure for other hands.")
    w("")
    for name, r in (("this model", rev), ("model 2 (synthetic only)", base_rev)):
        for mode, lab in (("raw", "ink"), ("words", "words")):
            x = r.get(f"test/base/{mode}")
            if x:
                w(f"  {name:28s} {lab:6s} CER {pct(x['cer']):>7s}  WER {pct(x['wer']):>7s}  "
                  f"top {pct(x['top1']):>7s}  top 3 {pct(x['top3']):>7s}  ({x['n']} samples)")
    w("")
    w("Synthetic ink, greedy decoding, character error:")
    w("")
    for k, what in RESULTS:
        if k in ev:
            w(f"  {what:44s} {pct(ev[k]['cer']):>7s}   ({ev[k]['lines']} lines)")
    if "dev" in ev:
        w(f"  {'dev fonts':44s} {pct(ev['dev']['cer']):>7s}   ({ev['dev']['lines']} lines)")
    w("")
    w(f"Export check: ONNX matches the checkpoint to {exp['max_abs_diff_vs_torch']:.1e}; held-out")
    w(f"character error through ONNX {pct(exp['fp32_test_heldout_cer'])}.")
    w("")
    w("Files")
    w("-----")
    w("  recognizer.ghm  the model")
    w("  prompts.txt     texts for prompted writing and donation, one per line")
    w("  LICENSES        the licences of these files and of the training data")
    w("  README          this file")
    return "\n".join(L) + "\n"


def licenses(meta, fonts_dir, writers=0):
    meta = meta.get("ink_meta", meta)
    fonts = meta["provenance"]["fonts"]
    texts = texts_of(meta)
    man = {f["dir"]: f for f in json.loads((fonts_dir / "manifest.json").read_text())}
    words = {1: "one", 2: "two", 3: "three"}
    real = REAL_LICENCE.format(writers=words.get(writers, str(writers))) if writers else ""
    L = [MODEL_LICENCE.format(real=real), "Training data", "-------------", "",
         "Texts, public domain (catalogue record \"Public domain in the USA.\", every author died",
         "before 1956):"]
    for k in sorted(texts, key=lambda k: texts[k]["title"]):
        t = texts[k]
        L.append(f"  {t['title']}, {t['author']}: {t['source']}")
    for n in meta.get("sources", {}).get("names", []):
        L.append(f"Names ({n['kind']}), {n['licence']}: {n['url']} (sha256 {n['sha256']})")
    L += ["", "Fonts, used only to render training ink. SIL Open Font License 1.1:"]
    ofl = apache = None
    rest = []
    for d in sorted(fonts):
        m = man.get(d)
        if m is None:
            fail(f"{fonts_dir}/manifest.json: no font {d}")
        if m["upstream_commit"] != fonts[d]["commit"]:
            fail(f"font {d}: fetched {m['upstream_commit']}, trained on {fonts[d]['commit']}")
        if m["license"] == "APACHE2":
            # the Apache licence text carries no notice: the copyright from the font's metadata
            apache = apache or (fonts_dir / d / "LICENSE.txt").read_text()
            notes = sorted({f["copyright"] for f in m["fonts"] if f.get("copyright")})
            rest.append(f"  {m['name']} ({m['upstream']}, {m['upstream_commit']}):")
            rest += [f"    {c}" for c in notes]
            continue
        # the copyright notice as the font's own OFL.txt states it, then the licence text
        txt = (fonts_dir / d / "OFL.txt").read_text()
        i = txt.find("This Font Software is licensed")
        if i < 0:
            fail(f"{fonts_dir}/{d}/OFL.txt: not the OFL")
        L.append(f"  {m['name']} ({m['upstream']}, {m['upstream_commit']}):")
        L += [f"    {r.strip()}" for r in txt[:i].splitlines() if r.strip()]
        if ofl is None:
            ofl = txt[i:]
    if rest:
        L += ["", "Fonts, used only to render training ink. Apache License 2.0:"] + rest
    L += ["", "SIL Open Font License, Version 1.1, as distributed with the fonts:", "", ofl.rstrip()]
    if apache:
        L += ["", "Apache License, Version 2.0, as distributed with the fonts:", "", apache.rstrip()]
    return "\n".join(L) + "\n"


def pack(stage, name, out, files=FILES):
    """deterministic tar.gz: sorted, fixed owner and time, no gzip name or time"""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        d = tarfile.TarInfo(name)
        d.type, d.mode, d.mtime = tarfile.DIRTYPE, 0o755, 0
        tar.addfile(d)
        for f in files:
            data = (stage / f).read_bytes()
            ti = tarfile.TarInfo(f"{name}/{f}")
            ti.size, ti.mode, ti.mtime = len(data), 0o644, 0
            tar.addfile(ti, io.BytesIO(data))
    path = out / f"{name}.tar.gz"
    with open(path, "wb") as fh, gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0, compresslevel=9) as gz:
        gz.write(raw.getvalue())
    sha = hashlib.sha512(path.read_bytes()).hexdigest()
    (out / f"{name}.tar.gz.sha512").write_text(f"{sha}  {path.name}\n")
    return path, sha


def main(argv=None):
    ap = argparse.ArgumentParser(prog="glassine-train release", description=__doc__.split("\n")[0])
    ap.add_argument("--build", type=Path, default=Path("build"), help="the trainer's build dir (runs/, data/)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--ckpt", default="best.pt")
    ap.add_argument("--lang", required=True)
    ap.add_argument("--version", required=True, help="the model's version: a plain number, never reused")
    ap.add_argument("--commit", required=True, help="the GlassineOS/handwriting commit the run was trained with")
    ap.add_argument("--fonts", type=Path, default=Path("../synth/build/fonts"), help="glassine-synth fetch's fonts")
    ap.add_argument("--out", type=Path, default=Path("build/release"))
    ap.add_argument("--real", type=Path, default=Path("build/real"),
                    help="the real-ink split a fine-tuned run was trained on (glassine-train real)")
    ap.add_argument("--base-realeval", type=Path, default=Path("build/realeval/m2/result.json"),
                    help="the previous base model's real-ink results, for the README")
    ap.add_argument("--tolerance", type=float, default=0.002,
                    help="largest difference of the held-out error through ONNX vs the run's evaluation")
    a = ap.parse_args(argv)
    if not a.version.isdigit():
        fail("--version must be a plain number")
    build = a.build.resolve()
    rundir = build / "runs" / a.run
    run_args = json.loads((rundir / "args.json").read_text()) if (rundir / "args.json").exists() else {}
    tuned = run_args.get("cmd") == "finetune"
    data = run_args["synth"][0] if tuned else run_args.get("data", "train")
    meta = json.loads((build / "data" / data / "meta.json").read_text()) if (build / "data" / data / "meta.json").exists() else {}
    suffix = (".mxh" if meta.get("measured") else ".xh") if meta.get("nfeat") == 6 else None
    held = "test-heldout" + (suffix or "")
    need = [rundir / a.ckpt, rundir / "args.json", rundir / "eval-best.json",
            build / "data" / data / "meta.json", build / "data" / held,
            REPO / "models" / a.lang / "prompts.txt", a.fonts / "manifest.json"]
    if tuned:
        need += [a.real / "split.json", build / "realeval" / a.run / "result.json", a.base_realeval]
    for p in need:
        if not p.exists():
            fail(f"missing {p}")
    ev = json.loads((rundir / "eval-best.json").read_text())
    tm = build / "data" / held / "meta.json"
    test_meta = json.loads(tm.read_text()) if tm.exists() else None
    if test_meta and "ink_meta" in test_meta:
        test_meta = test_meta["ink_meta"]

    name = f"glassine-handwriting-model-{a.lang}-{a.version}"
    a.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-", dir=a.out) as tmp:
        tmp = Path(tmp)
        onnx, exp = export(build, a.run, a.ckpt, tmp / "export", held)
        want = ev[held]["cer"]
        got = exp["fp32_test_heldout_cer"]
        if abs(got - want) > a.tolerance:
            fail(f"held-out error through ONNX {got:.4f}, the run's evaluation says {want:.4f}: "
                 "checkpoint and trainer don't match")
        stage = tmp / "stage"
        stage.mkdir()
        until = json.loads((a.real / "split.json").read_text())["report"]["trained_until"] if tuned else 0
        # a model's own characters (model 4's go beyond text.py's CHARS); onnx2ghm writes version 2
        # for one reading the reference height
        from .train import load
        alphabet = load(rundir / a.ckpt).alphabet
        subprocess.run([sys.executable, str(ONNX2GHM), str(onnx), "--charset", alphabet,
                        "-o", str(stage / "recognizer.ghm"), "--trained-until", str(until)], check=True)
        shutil.copyfile(REPO / "models" / a.lang / "prompts.txt", stage / "prompts.txt")
        if tuned:
            split = json.loads((a.real / "split.json").read_text())
            rev = json.loads((build / "realeval" / a.run / "result.json").read_text())
            base_rev = json.loads(a.base_realeval.read_text())
            (stage / "README").write_text(readme_finetune(a, run_args, meta, ev, exp, stage / "recognizer.ghm",
                                                          split, rev, base_rev))
        else:
            (stage / "README").write_text(readme(a, run_args, meta, ev, exp, stage / "recognizer.ghm", test_meta,
                                                 suffix))
        (stage / "LICENSES").write_text(licenses(meta, a.fonts, 1 if tuned else 0))
        # the unpacked model directory too, for make check-model
        mdir = a.out / name / a.lang
        shutil.rmtree(a.out / name, ignore_errors=True)
        mdir.mkdir(parents=True)
        for f in FILES:
            shutil.copyfile(stage / f, mdir / f)
        path, sha = pack(stage, name, a.out)
    print(f"{path}\nsha512 {sha}\nmodel dir {mdir.parent} (make -C recognizer check-model MODEL_DIR=...)")
