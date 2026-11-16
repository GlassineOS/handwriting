"""Ink sets for the next base model: the pen's points, not features, so features with a
reference height (FR-018) or without can be made from the same data.

A set is a directory:

    xy.i16      int16 (points, 2): x, y in units of 1/XY_SCALE em (y down)
    t.f32       float32 (points,): seconds from the line's first point
    strokes.npy int64 (strokes + 1,): first point of each stroke
    lines.npy   int64 (lines + 1,): first stroke of each line
    rows.jsonl  per line: text, kind, writer, xh, line, base (em), spans, seed
    meta.json   counts, sizes, seeds, generator parameters, sources

`xh` is the writer's letter height, `line` the line height of the writing area, `base` the
baseline's y (all in em, the ink's units): the references a model reads size by.
"""

import json
from pathlib import Path

import numpy as np

XY_SCALE = 512  # int16 units per em: 1/512 em steps, up to 64 em


class Writer:
    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.fxy = open(out / "xy.i16.tmp", "wb")
        self.ft = open(out / "t.f32.tmp", "wb")
        self.frows = open(out / "rows.jsonl.tmp", "w")
        self.stroke_off = [0]
        self.line_off = [0]
        self.npts = 0
        self.kinds = {}
        self.chars = 0

    def add(self, smp: dict, kind: str, seed: int):
        for q in smp["strokes"]:
            xy = np.clip(np.round(q[:, :2] * XY_SCALE), -32767, 32767).astype(np.int16)
            self.fxy.write(xy.tobytes())
            self.ft.write(q[:, 2].astype(np.float32).tobytes())
            self.npts += len(q)
            self.stroke_off.append(self.npts)
        self.line_off.append(len(self.stroke_off) - 1)
        row = {"text": smp["text"], "kind": kind, "writer": smp.get("writer"), "xh": round(smp["xh"], 5),
               "line": round(smp["line"], 5), "base": round(smp["base"], 5), "spans": smp["spans"], "seed": seed}
        self.frows.write(json.dumps(row, separators=(",", ":")) + "\n")
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        self.chars += len(smp["text"])

    def close(self, meta: dict):
        for f in (self.fxy, self.ft, self.frows):
            f.close()
        np.save(self.out / "strokes.npy", np.array(self.stroke_off, np.int64))
        np.save(self.out / "lines.npy", np.array(self.line_off, np.int64))
        for n in ("xy.i16", "t.f32", "rows.jsonl"):
            (self.out / f"{n}.tmp").rename(self.out / n)
        meta = {"format": "glassine-ink", "version": 1, "xy_scale": XY_SCALE, "lines": len(self.line_off) - 1,
                "strokes": len(self.stroke_off) - 1, "points": self.npts, "characters": self.chars,
                "kinds": dict(sorted(self.kinds.items())), **meta}
        meta["bytes"] = sum(f.stat().st_size for f in self.out.iterdir() if f.is_file())
        (self.out / "meta.json").write_text(json.dumps(meta, indent=1))
        return meta


class InkStore:
    """A set read back: store[i] = (strokes [(n, 3) x, y em, t s], row)."""

    def __init__(self, path: Path):
        self.path = path
        self.meta = json.loads((path / "meta.json").read_text())
        self.xy = np.memmap(path / "xy.i16", np.int16, "r").reshape(-1, 2)
        self.t = np.memmap(path / "t.f32", np.float32, "r")
        self.so = np.load(path / "strokes.npy")
        self.lo = np.load(path / "lines.npy")
        self.rows = [json.loads(ln) for ln in (path / "rows.jsonl").read_text().splitlines()]
        self.scale = 1.0 / self.meta["xy_scale"]

    def __len__(self):
        return len(self.rows)

    def strokes(self, i: int) -> list[np.ndarray]:
        out = []
        for s in range(self.lo[i], self.lo[i + 1]):
            a, b = self.so[s], self.so[s + 1]
            out.append(np.c_[np.asarray(self.xy[a:b], np.float64) * self.scale, np.asarray(self.t[a:b], np.float64)])
        return out

    def __getitem__(self, i: int):
        return self.strokes(i), self.rows[i]
