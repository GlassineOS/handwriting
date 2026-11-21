#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host acceptance of installed models (spec 006): a model file the person installs
themselves as a private replacement for the shipped recognizer or aligner. A test model fails
each check and is refused with its reason (installed or placed by hand) while the shipped model
reads; a usable one reads as the same file shipped would; falling back when it goes missing or
bad; a file replaced in place is never served stale; a guest reads with the shipped model; an
export and a contribution hold nothing of it; what is learned on top of it is kept apart; the
kind recorded in the file; end to end with a model written by the export's own writer
(recognizer/tools/onnx2ghm.py pack). Synthetic ink and random weights only.

    HW_BIN=path/to/glassine-handwriting python3 tests/test_local.py
"""
import os
import random
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))
import test_recognizer as TR  # noqa: E402

CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,;:!?'-() "


def mkmodel(path, *args):
    subprocess.run([sys.executable, str(HERE / "mkmodel.py"), str(path), *args], check=True)
    return Path(path)


def export_model(path, seed=1, kind="recognizer", charset=CHARS, strided=3, conv=16, hidden=16, layers=1):
    """a small model written by the export's own writer (onnx2ghm.pack), as the trainer's
    export path writes one: random weights, the format's tensor order"""
    import numpy as np
    import onnx2ghm
    rng = np.random.default_rng(seed)
    nfeat, k, c1 = 5, 5, conv // 2
    nclass = len(charset) + 1

    def w(*shape):
        return rng.uniform(-0.3, 0.3, size=shape).astype(np.float32)
    t = [np.array([10, 10, 1, 1, 1], np.float32), w(c1, nfeat, k), w(c1)]
    cin = c1
    for _ in range(strided):
        t += [w(conv, cin, k), w(conv)]
        cin = conv
    for layer in range(layers):
        i = cin if layer == 0 else 2 * hidden
        for _ in range(2):
            t += [w(4 * hidden, i), w(4 * hidden, hidden), w(4 * hidden)]
    t += [w(nclass, 2 * hidden), w(nclass)]
    Path(path).write_bytes(onnx2ghm.pack(1, nfeat, c1, conv, strided, k, hidden, layers, nclass, charset, t, 0, kind))
    return Path(path)


def patched(src, dst, fn):
    b = bytearray(Path(src).read_bytes())
    fn(b)
    Path(dst).write_bytes(bytes(b))
    return Path(dst)


class Installed(unittest.TestCase):
    def setUp(self):
        self.e = TR.Env()
        self.d = self.e.dir / "files"
        self.d.mkdir()
        self.mine = mkmodel(self.d / "mine.ghm", "--seed", "9", "--kind", "recognizer")
        self.ink = TR.INK[13]

    def tearDown(self):
        self.e.close()

    @property
    def local(self):
        return self.e.data / "glassine" / "handwriting-models" / "en"

    def model(self, *args, check=True):
        return self.e.run("model", *args, check=check)

    def status(self):
        return {tuple(f[:2]): f[2:] for f in (ln.split("\t") for ln in self.model().stdout.splitlines())}

    def recognize(self, c=None, s=None):
        s = s or self.ink
        fields = ("en", "normal", "em", "3", "", ";".join(TR.pts(q) for q in s["strokes"]))
        if c:
            return c.ask("recognize", *fields)[2:]
        c = self.e.start("-i", "0")
        try:
            return c.ask("recognize", *fields)[2:]
        finally:
            c.close()

    def as_shipped(self, path):
        """the reading of the same ink with path installed as the shipped model"""
        e = TR.Env()
        try:
            shutil.copy(path, e.models / "en" / "recognizer.ghm")
            c = e.start("-i", "0")
            r = c.ask("recognize", "en", "normal", "em", "3", "",
                      ";".join(TR.pts(q) for q in self.ink["strokes"]))[2:]
            c.close()
            return r
        finally:
            e.close()

    # ------------------------------------------------------------ the checks (FR-008)

    def faults(self):
        d, mine = self.d, self.mine
        hdr = lambda i, v: (lambda b: struct.pack_into("<I", b, 4 * i, v))  # noqa: E731
        big = d / "big.ghm"
        big.write_bytes(mine.read_bytes())
        with open(big, "r+b") as f:
            f.truncate((64 << 20) + 1)
        nan = bytearray(mine.read_bytes())
        struct.pack_into("<f", nan, len(nan) - 4 * 80, float("nan"))
        (d / "nan.ghm").write_bytes(bytes(nan))
        return {
            "not-a-model": (patched(mine, d / "magic.ghm", lambda b: b.__setitem__(slice(0, 4), b"XXXX")), ""),
            "format-version": (patched(mine, d / "v9.ghm", hdr(1, 9)), "9"),
            "features": (patched(mine, d / "feat.ghm", hdr(3, 6)), ""),
            "damaged": (patched(mine, d / "cut.ghm", lambda b: b.__delitem__(slice(len(b) - 16, None))), ""),
            "kind": (mkmodel(d / "al.ghm", "--aligner"), "aligner"),
            "kind-unknown": (mkmodel(d / "nospace.ghm", "--charset", CHARS.replace(" ", "")), ""),
            "alphabet": (mkmodel(d / "few.ghm", "--charset", "abcdefgh ", "--kind", "recognizer"), None),
            "alphabet-repeated": (mkmodel(d / "rep.ghm", "--charset", CHARS + "a", "--kind", "recognizer"), "twice a"),
            "too-big": (big, None),
            "not-finite": (d / "nan.ghm", ""),
            "kind-recorded-unknown": (patched(mine, d / "k7.ghm", hdr(14, 7)), ""),
        }

    def test_each_check_refuses(self):
        """every fault is refused with its reason, at install and placed by hand; nothing is
        kept; the shipped model reads"""
        shipped = self.recognize()
        reasons = {"alphabet-repeated": "alphabet", "kind-recorded-unknown": "kind-unknown"}
        for name, (path, detail) in self.faults().items():
            want = reasons.get(name, name)
            with self.subTest(name):
                p = self.model("check", "-k", "recognizer", str(path), check=False)
                self.assertEqual(p.returncode, 1, p.stdout)
                f = p.stdout.rstrip("\n").split("\t")
                self.assertEqual(f[:2], ["refused", want], p.stdout)
                if detail is not None:
                    self.assertEqual(f[2], detail or "-")
                if name != "kind":   # (an aligner installs as an aligner: its kind is the file's)
                    p = self.model("install", str(path), check=False)
                    self.assertEqual((p.returncode, p.stdout.split("\t")[:2]), (1, ["refused", want]))
                    self.assertFalse(self.local.exists() and any(self.local.iterdir()))
                self.assertFalse((self.e.conf / "glassine" / "handwriting.conf").exists())
                # placed by hand and chosen: refused, the shipped model reads
                self.local.mkdir(parents=True, exist_ok=True)
                shutil.copy(path, self.local / "recognizer.ghm")
                self.model("use", "recognizer", "local")
                st = self.status()
                self.assertEqual(st[("local", "recognizer")][:2], ["refused", want])
                self.assertEqual(st[("inuse", "recognizer")], ["shipped"])
                self.assertEqual(self.recognize(), shipped)
                (self.local / "recognizer.ghm").unlink()
                (self.e.conf / "glassine" / "handwriting.conf").unlink()
        # a language the device has no shipped files for
        p = self.model("check", "-l", "de", str(self.mine), check=False)
        self.assertEqual(p.stdout, "refused\tlanguage\tde\n")
        # an aligner placed as one checks; a recognizer checked as an aligner is the wrong kind
        al = mkmodel(self.d / "al2.ghm", "--aligner")
        self.assertEqual(self.model("check", "-k", "aligner", str(al)).stdout, "ok\taligner\n")
        self.assertEqual(self.model("check", "-k", "aligner", str(self.mine), check=False).stdout,
                         "refused\tkind\trecognizer\n")

    def test_kind_recorded_or_by_shape(self):
        """a file without a recorded kind is told by its shape; a recorded kind decides"""
        old = mkmodel(self.d / "old.ghm")                               # nothing recorded, a space
        self.assertEqual(self.model("check", str(old)).stdout, "ok\trecognizer\n")
        olda = mkmodel(self.d / "olda.ghm", "--strided", "1", "--charset", CHARS.replace(" ", ""))
        self.assertEqual(self.model("check", str(olda)).stdout, "ok\taligner\n")
        rec_al = mkmodel(self.d / "recal.ghm", "--strided", "1", "--kind", "aligner")   # a space, recorded aligner
        self.assertEqual(self.model("check", str(rec_al)).stdout, "ok\taligner\n")
        # the recorded word is one the engine before this read as zero: the same reading
        self.assertEqual(self.as_shipped(self.mine), self.as_shipped(patched(self.mine, self.d / "k0.ghm",
                                                                            lambda b: struct.pack_into("<I", b, 56, 0))))
        # a recorded aligner put in the shipped recognizer's place is not read as one
        shutil.copy(rec_al, self.e.models / "en" / "recognizer.ghm")
        self.assertEqual(self.recognize(), ["no-model"])

    # ------------------------------------------------------------ choosing and falling back

    def test_reads_as_shipped_and_switches_without_restart(self):
        c = self.e.start("-i", "0")
        shipped = self.recognize(c)
        self.assertEqual(self.model("install", str(self.mine)).stdout, "installed\trecognizer\ten\n")
        self.assertEqual(oct((self.local / "recognizer.ghm").stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(self.local.stat().st_mode & 0o777), "0o700")
        self.assertEqual(self.mine.read_bytes(), (self.local / "recognizer.ghm").read_bytes())
        self.assertTrue(self.mine.exists())
        mine = self.recognize(c)
        self.assertNotEqual(mine, shipped)
        self.assertEqual(mine, self.as_shipped(self.mine))
        st = self.status()
        self.assertEqual((st[("use", "recognizer")], st[("inuse", "recognizer")]), (["local"], ["local"]))
        self.assertEqual(st[("local", "recognizer")][0], "ready")
        self.model("use", "recognizer", "shipped")
        self.assertEqual(self.recognize(c), shipped)
        self.model("use", "recognizer", "local")
        self.assertEqual(self.recognize(c), mine)
        # the file removed: the shipped model chosen and read from the next reading
        self.assertEqual(self.model("remove", "recognizer").stdout, "removed\n")
        self.assertEqual(self.recognize(c), shipped)
        st = self.status()
        self.assertEqual((st[("use", "recognizer")], st[("inuse", "recognizer")]), (["shipped"], ["shipped"]))
        self.assertEqual(self.model("remove", "recognizer").stdout, "none\n")
        # a good file placed by hand afterwards waits for the person's choice
        self.local.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.mine, self.local / "recognizer.ghm")
        self.assertEqual(self.recognize(c), shipped)
        self.assertEqual(self.status()[("use", "recognizer")], ["shipped"])
        # chosen again, then damaged: the shipped model from the next reading
        self.model("use", "recognizer", "local")
        self.assertEqual(self.recognize(c), mine)
        (self.local / "recognizer.ghm").write_bytes(self.mine.read_bytes()[:-40])
        self.assertEqual(self.recognize(c), shipped)
        # good again: used again
        shutil.copy(self.mine, self.local / "recognizer.ghm")
        self.assertEqual(self.recognize(c), mine)
        rc, err = c.close()
        self.assertEqual((rc, err), (0, ""))

    def test_replaced_in_place_never_stale(self):
        """the same strokes read again after the file was replaced (in place, the same name and
        size) are read by the new file, in the same process"""
        c = self.e.start("-i", "0")
        self.model("install", str(self.mine))
        first = self.recognize(c)
        other = mkmodel(self.d / "other.ghm", "--seed", "11", "--kind", "recognizer")
        self.assertEqual(other.stat().st_size, self.mine.stat().st_size)
        with open(self.local / "recognizer.ghm", "r+b") as f:       # overwritten, the same inode
            f.write(other.read_bytes())
        second = self.recognize(c)
        self.assertNotEqual(second, first)
        self.assertEqual(second, self.as_shipped(other))
        self.model("install", str(self.mine))                       # replaced by rename
        self.assertEqual(self.recognize(c), first)
        c.close()

    def test_two_languages(self):
        """an installed model for English, none for German: German reads with its shipped model"""
        de = self.e.models / "de"
        de.mkdir()
        mkmodel(de / "recognizer.ghm", "--seed", "3")
        (de / "prompts.txt").write_text("Der Hund.\n")
        self.model("install", str(self.mine))
        st = {tuple(f[:2]): f[2:] for f in (ln.split("\t") for ln in self.model("-l", "de").stdout.splitlines())}
        self.assertEqual((st[("inuse", "recognizer")], st[("local", "recognizer")]), (["shipped"], ["none"]))

    # ------------------------------------------------------------ the person's alone

    def test_guest_reads_with_shipped(self):
        w = self.e.run("writer", "new").stdout.strip()
        f = self.d / "line.jsonl"
        import json
        f.write_text(json.dumps(self.ink) + "\n")
        shipped = self.e.run("read", str(f)).stdout
        self.model("install", str(self.mine))
        self.assertNotEqual(self.e.run("read", str(f)).stdout, shipped)
        self.assertEqual(self.e.run("--writer", w, "read", str(f)).stdout, shipped)
        p = self.e.run("--writer", w, "model", check=False)
        self.assertEqual((p.returncode, p.stderr), (2, "writer-store\n"))

    def test_export_and_contribution_hold_nothing_of_it(self):
        c = self.e.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        self.assertEqual(self.e.run("donation", "start", "CC0-1.0").returncode, 0)
        rng = random.Random(4)
        for i in range(3):
            c.ask("begin", "s", "en", "normal", "device:10", "")
            for q in TR.shapes("olx"[i], rng):
                c.ask("stroke", "s", q)
            self.assertEqual(c.ask("confirm", "s", "picked", "olx"[i])[2], "ok")
        c.close()
        for sid in self.e.run("samples", "list").stdout.split("\n"):
            if sid:
                self.e.run("donation", "add", sid.split("\t")[0])

        def files(tag):
            self.e.run("samples", "export", str(self.d / f"export-{tag}.jsonl"))
            self.e.run("donation", "write", str(self.d / f"contrib-{tag}.jsonl"))
            return (self.d / f"export-{tag}.jsonl").read_bytes(), (self.d / f"contrib-{tag}.jsonl").read_bytes()
        before = files("before")
        self.model("install", str(self.mine))
        mkmodel(self.d / "al.ghm", "--aligner")
        self.model("install", str(self.d / "al.ghm"))
        after = files("after")
        self.assertEqual(before, after)
        blob = self.mine.read_bytes()[64:96]
        self.assertFalse(any(blob in f for f in after))
        self.assertFalse(any(b"handwriting-models" in f for f in after))
        # an erase of the samples leaves the installed model
        self.e.run("samples", "erase")
        self.assertTrue((self.local / "recognizer.ghm").exists())

    def test_learning_on_top_kept_apart(self):
        """an adaptation made on top of the installed recognizer goes to a place of its own; the
        shipped model's stays as it was and is shown again when the choice goes back"""
        c = self.e.start("-i", "0")
        c.ask("hello", "1", "org.example.keyboard")
        rng = random.Random(6)
        for i in range(40):
            ch = "olxz"[i % 4]
            c.ask("begin", "s", "en", "normal", "device:10", "")
            for q in TR.shapes(ch, rng):
                c.ask("stroke", "s", q)
            c.ask("confirm", "s", "picked", ch)
        c.close()
        args = ["-f", "--min-train", "20", "--min-held", "5", "--epochs", "5", "--lr", "1e-2"]
        ad = self.e.store / "adaptation"

        def tree(d):
            return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()} \
                if d.exists() else {}
        self.e.run("adapt", *args)
        shipped_tree, shipped_show = tree(ad / "en"), self.e.run("adaptation", "show").stdout
        self.model("install", str(self.mine))
        self.assertTrue(self.e.run("adaptation", "show").stdout.startswith("current\tnone\n"))
        out = self.e.run("adapt", *args).stdout
        self.assertEqual(tree(ad / "en"), shipped_tree)
        self.assertEqual(sorted(p.name for p in ad.iterdir()) if ad.exists() else [],
                         sorted((["en"] if (ad / "en").exists() else []) + (["en@local"] if (ad / "en@local").exists()
                                                                            else [])))
        if "kept" in out:
            self.assertTrue((ad / "en@local" / "current").exists())
        self.model("use", "recognizer", "shipped")
        self.assertEqual(self.e.run("adaptation", "show").stdout, shipped_show)

    # ------------------------------------------------------------ end to end

    def test_end_to_end_with_an_exported_model(self):
        """a small model written by the export's writer: put in a folder, installed, used,
        removed"""
        folder = self.e.dir / "home" / "Documents" / "Handwriting"
        folder.mkdir(parents=True)
        f = export_model(folder / "replacement.ghm", seed=21)
        c = self.e.start("-i", "0")
        shipped = self.recognize(c)
        self.assertEqual(self.model("check", str(f)).stdout, "ok\trecognizer\n")
        self.assertEqual(self.model("install", str(f)).stdout, "installed\trecognizer\ten\n")
        mine = self.recognize(c)
        self.assertNotEqual(mine, shipped)
        self.assertEqual(mine, self.as_shipped(f))
        self.assertEqual(self.model("remove", "recognizer").stdout, "removed\n")
        self.assertEqual(self.recognize(c), shipped)
        self.assertTrue(f.exists())
        a = export_model(folder / "aligner.ghm", seed=22, kind="aligner", charset=CHARS.replace(" ", ""), strided=1)
        self.assertEqual(self.model("install", str(a)).stdout, "installed\taligner\ten\n")
        self.assertEqual(self.status()[("inuse", "aligner")], ["local"])
        self.assertEqual(self.status()[("inuse", "recognizer")], ["shipped"])
        c.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)
