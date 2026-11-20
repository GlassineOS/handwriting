#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The letter model measured on a store (spec 003 FR-006): the model is built in a temporary
copy of the store (the store given is only read) with GLASSINE_HANDWRITING_LETTERS_STATS, once
per way of averaging asked for (GLASSINE_HANDWRITING_LETTERS_AVG), and only aggregate numbers
are printed, per way: how far the samples written like a variant are from it (RMS,
x-heights: by arc length as neatening measures, and registered), how many samples it is made
of in effect, its height, enclosed area and roughness as averaged over those of the samples it is made of
(1: kept, no stretch needed; also for the variants made of at least 5 samples in effect,
where averaging can blur), how many needed a stretch over 2 %, and the build's time.

    eval-letters.py BIN STORE_DIR [--avg mean --avg dba] [--keep DIR] [--tmp DIR]

--keep DIR leaves each built model in DIR/AVG/ (a private directory: the model is the
person's letters); --tmp DIR the directory the temporary copy is made in (for a person's
store, one as private as the store).
"""
import argparse
import os
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else float("nan")


def med(v):
    return statistics.median(v) if v else float("nan")


def summary(rows):
    """aggregates over variants; the shape measures also over those made of at least 5
    samples in effect (where averaging can blur)"""
    out = {"variants": len(rows), "rms_arc": med([r["arc"] for r in rows]), "rms_reg": med([r["reg"] for r in rows]),
           "effective_members": med([r["eff"] for r in rows])}
    for tag, rs in (("", rows), ("_eff5", [r for r in rows if r["eff"] >= 5])):
        h = [r["hv"] / r["hm"] for r in rs if r["hm"] > 0.1]
        ar = [r["av"] / r["am"] for r in rs if r["am"] > 0.05]
        ro = [r["rv"] / r["rm"] for r in rs if r["rm"] > 1e-6]
        out |= {f"n{tag}": len(rs), f"height{tag}": med(h), f"height{tag}_p10": q(h, 0.1),
                f"stretch_over_2pc{tag}": sum(x < 1 / 1.02 for x in h), f"area{tag}": med(ar), f"area{tag}_p10": q(ar, 0.1),
                f"roughness{tag}": med(ro), f"roughness{tag}_p90": q(ro, 0.9)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("store")
    ap.add_argument("--avg", action="append")
    ap.add_argument("--keep", type=Path)
    ap.add_argument("--tmp")
    a = ap.parse_args()
    store = Path(a.store)
    for avg in a.avg or ["mean", "dba"]:
        with tempfile.TemporaryDirectory(dir=a.tmp) as td:
            os.chmod(td, 0o700)
            hw = Path(td) / "data" / "glassine" / "handwriting"
            hw.mkdir(parents=True)
            for f in ("samples.jsonl", "samples.meta", "samples.index", "marks", "preferences"):
                if (store / f).exists():
                    shutil.copy(store / f, hw / f)
            stats = Path(td) / "stats"
            env = dict(os.environ, HOME=td, XDG_DATA_HOME=str(Path(td) / "data"), XDG_CONFIG_HOME=str(Path(td) / "config"),
                       GLASSINE_HANDWRITING_LETTERS_STATS=str(stats), GLASSINE_HANDWRITING_LETTERS_AVG=avg)
            t0 = time.perf_counter()
            p = subprocess.run([a.bin, "letters", "build", "-f"], env=env, capture_output=True, text=True)
            dt = time.perf_counter() - t0
            if p.returncode:
                print(f"{avg}: build failed: {p.stderr.strip()[:200]}")
                continue
            rows = []
            for ln in stats.read_text().splitlines() if stats.exists() else []:
                f = ln.split("\t")
                if f[0] != "variant":
                    continue
                rows.append({"cp": int(f[1]), "n": int(f[2]), "arc": float(f[4]), "reg": float(f[5]), "eff": float(f[6]),
                             "hm": float(f[7]), "hv": float(f[8]), "am": float(f[9]), "av": float(f[10]),
                             "rm": float(f[11]), "rv": float(f[12])})
            s = summary([r for r in rows if r["cp"]])
            j = summary([r for r in rows if not r["cp"]])
            print(f"{avg}: build {dt:.1f} s; {p.stdout.strip()}")
            print("  letters: " + ", ".join(f"{k} {v:.3f}" if isinstance(v, float) else f"{k} {v}" for k, v in s.items()))
            if j["variants"]:
                print("  joined:  " + ", ".join(f"{k} {v:.3f}" if isinstance(v, float) else f"{k} {v}" for k, v in j.items()))
            if a.keep:
                d = a.keep / avg
                d.mkdir(parents=True, exist_ok=True)
                os.chmod(a.keep, 0o700)
                shutil.copy(hw / "letters" / "model", d / "model")


if __name__ == "__main__":
    main()
