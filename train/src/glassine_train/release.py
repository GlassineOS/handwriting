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

MODEL_LICENCE = """\
recognizer.ghm (the model)
  GPL-2.0-or-later, as the rest of GlassineOS/handwriting that produces it (the trainer,
  the recipe and the converter). The full text is in the GlassineOS/handwriting
  repository (LICENSE) and at https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.

  The model contains no text and no font software. It was trained on public-domain
  texts (no terms) rendered as synthetic ink from fonts under the SIL Open Font
  License 1.1 or the Apache License 2.0; these cover the fonts themselves, not what is
  made with them. The
  sources and their licences are listed below so the training can be checked and
  repeated.

README, LICENSES
  GPL-2.0-or-later, as above.

prompts.txt (texts for prompted writing and donation)
  CC0-1.0 (https://creativecommons.org/publicdomain/zero/1.0/): written for the
  project and dedicated to the public domain, apart from traditional pangrams, which
  are in the public domain. Writing them may be donated under CC0-1.0 or CC-BY-4.0.
"""


def fail(msg):
    raise SystemExit(f"glassine-train release: {msg}")


def pct(x):
    return f"{100 * x:.1f} %"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(build, run, ckpt, work):
    """glassine-train export in a scratch build dir: ONNX + export.json from the checkpoint"""
    from argparse import Namespace

    from .cli import cmd_export
    rdir = work / "build" / "runs" / run
    rdir.mkdir(parents=True)
    shutil.copy2(build / "runs" / run / ckpt, rdir / ckpt)
    (work / "build" / "data").mkdir()
    (work / "build" / "data" / "test-heldout").symlink_to((build / "data" / "test-heldout").resolve())
    cwd = os.getcwd()
    os.chdir(work)
    try:
        cmd_export(Namespace(run=run, ckpt=ckpt))
    finally:
        os.chdir(cwd)
    return rdir / "recognizer.onnx", json.loads((rdir / "export.json").read_text())


def readme(a, run_args, meta, ev, exp, ghm, test_meta):
    fonts = meta["provenance"]["fonts"]
    texts = meta["provenance"]["text"]
    train_fonts = meta["fonts"]
    held = sorted(f for f in fonts if f not in train_fonts)
    L = []
    w = L.append
    w(f"glassine-handwriting base model: {a.lang}, version {a.version}")
    w("")
    w("The base model of glassine-handwriting (GlassineOS/handwriting, docs/recognizer.md)")
    w(f"for the language '{a.lang}'. recognizer.ghm is model file format ghm version 1.")
    w("")
    w(f"recognizer.ghm  sha256 {sha256(ghm)}")
    w("")
    w("Recipe")
    w("------")
    w(f"Trainer: GlassineOS/handwriting commit {a.commit}, train/ (train/README.md has the")
    w("full recipe, the features and the model). Trained with:")
    w("")
    w("  (cd synth && uv run glassine-synth fetch)")
    w("  cd train && uv run glassine-train data")
    args = " ".join(f"--{k.replace('_', '-')} {v}" for k, v in run_args.items()
                    if k not in ("cmd", "run", "data", "val") and v not in (None, False))
    w(f"  uv run glassine-train train --run {a.run} {args}")
    w(f"  uv run glassine-train release --run {a.run} --lang {a.lang} --version {a.version} ...")
    w("")
    w(f"Model: convolutions + 2-layer bidirectional LSTM + CTC, {exp['params']} parameters;")
    w("the checkpoint with the best validation character error was kept.")
    w("")
    w("Data")
    w("----")
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
    w("the honest proxy for a new hand; there is no measurement on real handwriting yet.")
    w("")
    for k, what in RESULTS:
        if k in ev:
            w(f"  {what:44s} {pct(ev[k]['cer']):>7s}   ({ev[k]['lines']} lines)")
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


def licenses(meta, fonts_dir):
    fonts = meta["provenance"]["fonts"]
    texts = meta["provenance"]["text"]
    man = {f["dir"]: f for f in json.loads((fonts_dir / "manifest.json").read_text())}
    L = [MODEL_LICENCE, "Training data", "-------------", "",
         "Texts, public domain (catalogue record \"Public domain in the USA.\", every author died",
         "before 1956):"]
    for k in sorted(texts, key=lambda k: texts[k]["title"]):
        t = texts[k]
        L.append(f"  {t['title']}, {t['author']}: {t['source']}")
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


def pack(stage, name, out):
    """deterministic tar.gz: sorted, fixed owner and time, no gzip name or time"""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        d = tarfile.TarInfo(name)
        d.type, d.mode, d.mtime = tarfile.DIRTYPE, 0o755, 0
        tar.addfile(d)
        for f in FILES:
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
    ap.add_argument("--tolerance", type=float, default=0.002,
                    help="largest difference of the held-out error through ONNX vs the run's evaluation")
    a = ap.parse_args(argv)
    if not a.version.isdigit():
        fail("--version must be a plain number")
    build = a.build.resolve()
    rundir = build / "runs" / a.run
    for p in (rundir / a.ckpt, rundir / "args.json", rundir / "eval-best.json",
              build / "data" / "train" / "meta.json", build / "data" / "test-heldout",
              REPO / "models" / a.lang / "prompts.txt", a.fonts / "manifest.json"):
        if not p.exists():
            fail(f"missing {p}")
    run_args = json.loads((rundir / "args.json").read_text())
    meta = json.loads((build / "data" / run_args.get("data", "train") / "meta.json").read_text())
    ev = json.loads((rundir / "eval-best.json").read_text())
    tm = build / "data" / "test-heldout" / "meta.json"
    test_meta = json.loads(tm.read_text()) if tm.exists() else None

    name = f"glassine-handwriting-model-{a.lang}-{a.version}"
    a.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-", dir=a.out) as tmp:
        tmp = Path(tmp)
        onnx, exp = export(build, a.run, a.ckpt, tmp / "export")
        want = ev["test-heldout"]["cer"]
        got = exp["fp32_test_heldout_cer"]
        if abs(got - want) > a.tolerance:
            fail(f"held-out error through ONNX {got:.4f}, the run's evaluation says {want:.4f}: "
                 "checkpoint and trainer don't match")
        stage = tmp / "stage"
        stage.mkdir()
        subprocess.run([sys.executable, str(ONNX2GHM), str(onnx), "--charset-from", str(TEXT_PY),
                        "-o", str(stage / "recognizer.ghm")], check=True)
        shutil.copyfile(REPO / "models" / a.lang / "prompts.txt", stage / "prompts.txt")
        (stage / "README").write_text(readme(a, run_args, meta, ev, exp, stage / "recognizer.ghm", test_meta))
        (stage / "LICENSES").write_text(licenses(meta, a.fonts))
        # the unpacked model directory too, for make check-model
        mdir = a.out / name / a.lang
        shutil.rmtree(a.out / name, ignore_errors=True)
        mdir.mkdir(parents=True)
        for f in FILES:
            shutil.copyfile(stage / f, mdir / f)
        path, sha = pack(stage, name, a.out)
    print(f"{path}\nsha512 {sha}\nmodel dir {mdir.parent} (make -C recognizer check-model MODEL_DIR=...)")
