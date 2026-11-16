#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reads stored samples (sample format lines) back through the recognizer program and
measures the readings against their text: character and word error of the top candidate,
how often the text is among the candidates, per prompt set, and against an earlier run
(readings fixed and broken). Nothing is stored; the store given is only read.

    eval-samples.py BIN SAMPLES.jsonl [--models DIR] [--adaptation FILE] [--split held|train|all]
                    [--field FIELD] [--count N] [--raw | --chars] [--prompts DIR] [--save OUT.json]
                    [--against EARLIER.json] [--per-sample]

--split: the adaptation's split (one sample in five by id is held back, docs/recognizer.md);
the head adapted from a store has seen its train part, so its numbers count on `held` only.
--store: a store directory (copied, never written) whose samples are the person's letters
for the size (docs/recognizer.md, Size); without it the size has no reference. --base F:
send the area's first guide line at F line heights below its top. --align: also check each
short text (one or two characters) as prompted writing (the fit, and how many pass 0.6).
--adaptation: the person's adaptation file (adaptation/LANG/current) to read with; without
it, the base model alone. --prompts: the samples app's prompt sets (one .txt per set), to
report per set; samples whose text is in none count as "other".
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def edit(a, b):
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            t = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = t
    return d[len(b)]


def esc(s):
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def unesc(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append({"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}[s[i + 1]])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def num(v):
    return repr(v) if isinstance(v, float) else str(v)


class Rec:
    def __init__(self, binary, env):
        self.p = subprocess.Popen([binary, "-i", "0"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, bufsize=1, env=env)
        self.n = 0
        self.ask("hello", "1", "org.glassine.eval")

    def ask(self, verb, *f):
        self.n += 1
        rid = "q%d" % self.n
        self.p.stdin.write("\t".join([verb, rid, *f]) + "\n")
        self.p.stdin.flush()
        r = self.p.stdout.readline().rstrip("\n").split("\t")
        if r[0] == "error":
            raise RuntimeError(" ".join(r))
        return r[2:]

    def close(self):
        self.p.stdin.close()
        self.p.wait()


def sets_of(prompts):
    m = {}
    if prompts:
        for f in sorted(Path(prompts).glob("*.txt")):
            for ln in f.read_text().splitlines():
                if ln.strip():
                    m.setdefault(ln.strip(), f.stem)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("samples")
    ap.add_argument("--models")
    ap.add_argument("--adaptation")
    ap.add_argument("--split", default="all", choices=("held", "train", "all"))
    ap.add_argument("--field", default="normal")
    ap.add_argument("--before", default="")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--raw", action="store_true", help="ask for the ink's reading alone (read … raw)")
    ap.add_argument("--chars", action="store_true", help="ask for the reading without words (read … chars)")
    ap.add_argument("--prompts")
    ap.add_argument("--save")
    ap.add_argument("--against")
    ap.add_argument("--per-sample", action="store_true")
    ap.add_argument("--store")
    ap.add_argument("--base", type=float)
    ap.add_argument("--align", action="store_true")
    ap.add_argument("--env", action="append", default=[], help="NAME=VALUE for the recognizer")
    ap.add_argument("--unknown-targets", action="store_true",
                    help="the words of every sample's text marked removed in the predictor's store, so they"
                         " read as unknown words (names, codes): what words do to text the list lacks")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="hw-eval-"))
    env = dict(os.environ, XDG_DATA_HOME=str(tmp / "data"), XDG_CONFIG_HOME=str(tmp / "conf"),
               XDG_STATE_HOME=str(tmp / "state"), HOME=str(tmp))
    if a.models:
        env["GLASSINE_HANDWRITING_MODELS"] = a.models
    for kv in a.env:
        k, v = kv.split("=", 1)
        env[k] = v
    if a.store:
        shutil.copytree(a.store, tmp / "data" / "glassine" / "handwriting",
                        ignore=shutil.ignore_patterns("adaptation", "donation"))
    if a.adaptation:
        d = tmp / "data" / "glassine" / "handwriting" / "adaptation" / "en"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(a.adaptation, d / "current")
    if a.unknown_targets:
        import re
        ws = set()
        for ln in open(a.samples, "rb").read().decode("utf-8").replace("\0", "\n").split("\n"):
            if ln.startswith("{") and ln.endswith("}") and '"strokes"' in ln:
                ws |= {w.lower().replace("\u2019", "'") for w in re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)*", json.loads(ln)["text"])}
        d = tmp / "data" / "glassine" / "predict"
        d.mkdir(parents=True)
        (d / "words").write_text("glassine-words 1 %d\n" % len(ws) + "".join("x * %s\n" % w for w in sorted(ws)))
    sets = sets_of(a.prompts)
    rec = Rec(a.bin, env)
    times = []
    # the ids as the store has them: the same hash the recognizer uses (FNV-1a 64 of the line)
    def fnv(b):
        h = 1469598103934665603
        for x in b:
            h = ((h ^ x) * 1099511628211) & (2 ** 64 - 1)
        return h
    # one sample per line; zero bytes (a lost append) end a line, as in the store
    raw_lines = open(a.samples, "rb").read().decode("utf-8").replace("\0", "\n").split("\n")
    raw_lines = [ln for ln in raw_lines if ln.startswith("{") and ln.endswith("}")]
    # records beside the samples (a donation's preferences) are no samples
    raw_lines = [ln for ln in raw_lines if "strokes" in json.loads(ln)]
    # writers: the distinct donor IDs of a donation (none in a store of one's own)
    donors = {json.loads(ln).get("donor") for ln in raw_lines} - {None}
    if donors:
        print("writers: %d donor IDs" % len(donors))
    out = []
    try:
        for ln in raw_lines:
            sid = "%016x" % fnv(ln.encode("utf-8"))
            held = int(sid, 16) % 5 == 0
            if (a.split == "held" and not held) or (a.split == "train" and held):
                continue
            s = json.loads(ln)
            u = s.get("units", {})
            units = "device:%s" % num(u["per_mm"]) if u.get("per_mm") else "em"
            if u.get("per_mm") and u.get("rate"):
                units += ":%s" % num(u["rate"])
            r = rec.ask("begin", "s", s.get("lang", "en"), a.field, units, esc(a.before))
            if r[0] != "ok":
                print("begin:", r, file=sys.stderr)
                continue
            ar = s.get("area")
            if ar:
                v = ar["box"] + [ar["line"], ar.get("orientation", 0)]
                if a.base is not None:
                    v.append(ar["box"][1] + a.base * ar["line"])
                rec.ask("area", "s", ",".join(num(x) for x in v))
            for st in s["strokes"]:
                rec.ask("stroke", "s", " ".join(",".join(num(round(p[2] * 1000, 3)) if i == 2 else num(v)
                                                         for i, v in enumerate(p)) for p in st))
            t0 = time.perf_counter()
            r = rec.ask("read", "s", str(a.count), *(["raw"] if a.raw else ["chars"] if a.chars else []))
            times.append(time.perf_counter() - t0)
            fit = None
            if a.align and len(s["text"]) <= 2:
                f = rec.ask("align", "s", esc(s["text"]))
                fit = None if f[0] == "short" else float(f[0])
            rec.ask("end", "s")
            cands = [unesc(r[2 + 3 * i]) for i in range(int(r[1]))] if r and r[0] == "text" else []
            out.append({"id": sid, "text": s["text"], "set": sets.get(s["text"], "other"), "cands": cands, "fit": fit})
    finally:
        rec.close()
        shutil.rmtree(tmp, ignore_errors=True)

    def stats(rs):
        ce = sum(edit(r["cands"][0] if r["cands"] else "", r["text"]) for r in rs)
        cn = sum(len(r["text"]) for r in rs)
        we = sum(edit((r["cands"][0] if r["cands"] else "").split(), r["text"].split()) for r in rs)
        wn = sum(len(r["text"].split()) for r in rs)
        top = sum(bool(r["cands"]) and r["cands"][0] == r["text"] for r in rs)
        anyc = sum(r["text"] in r["cands"] for r in rs)
        return {"n": len(rs), "cer": ce / max(cn, 1), "wer": we / max(wn, 1), "exact": top / max(len(rs), 1),
                "in_candidates": anyc / max(len(rs), 1)}

    def show(name, st):
        print("%-10s n %4d  CER %5.1f %%  WER %5.1f %%  exact %5.1f %%  in %d candidates %5.1f %%" % (
            name, st["n"], 100 * st["cer"], 100 * st["wer"], 100 * st["exact"], a.count, 100 * st["in_candidates"]))

    show("all", stats(out))
    for name in sorted({r["set"] for r in out}):
        show(name, stats([r for r in out if r["set"] == name]))
    # short texts (one or two characters) by kind: what the size is for
    def kind(t):
        if not any(c.isalnum() for c in t):
            return "marks"
        if t.isdigit():
            return "digits"
        return "capitals" if t.isupper() else "small" if t.islower() else "mixed"
    short = [r for r in out if len(r["text"]) <= 2]
    for name in ("marks", "digits", "capitals", "small", "mixed"):
        rs = [r for r in short if kind(r["text"]) == name]
        if not rs:
            continue
        top = [r["cands"][0] if r["cands"] else "" for r in rs]
        ok = sum(t == r["text"] for t, r in zip(top, rs))
        case = sum(t != r["text"] and t.lower() == r["text"].lower() for t, r in zip(top, rs))
        line = "short %-8s n %4d  exact %5.1f %%  case wrong %d" % (name, len(rs), 100 * ok / len(rs), case)
        if a.align:
            fits = [r["fit"] or 0.0 for r in rs]
            line += "  fit >= 0.6 %5.1f %%" % (100 * sum(f >= 0.6 for f in fits) / len(rs))
        print(line)
    longer = [r for r in out if len(r["text"]) > 2]
    if longer:
        show("3+ chars", stats(longer))
    if times:
        times.sort()
        print("read: median %.1f ms, 95th %.1f ms, max %.1f ms" % (
            1000 * times[len(times) // 2], 1000 * times[int(len(times) * 0.95)], 1000 * times[-1]))
    if a.against:
        old = {r["id"]: r for r in json.load(open(a.against))}
        fixed = broken = changed3 = 0
        for r in out:
            o = old.get(r["id"])
            if not o:
                continue
            was = bool(o["cands"]) and o["cands"][0] == r["text"]
            now = bool(r["cands"]) and r["cands"][0] == r["text"]
            fixed += now and not was
            broken += was and not now
            if len(r["text"]) > 2:
                changed3 += o["cands"] != r["cands"]
        print("against %s: %d fixed, %d broken (exact top candidate); texts of 3+ characters with other"
              " candidates: %d" % (a.against, fixed, broken, changed3))
    if a.per_sample:
        for r in out:
            print("%s\t%s\t%s" % (r["set"], r["text"], " | ".join(r["cands"])))
    if a.save:
        json.dump(out, open(a.save, "w"))


if __name__ == "__main__":
    main()
