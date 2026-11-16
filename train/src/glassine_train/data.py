"""Datasets: text lines rendered by glassine-synth, turned into features, stored as a
float16 memmap (features of all lines back to back) plus offsets and texts."""

import json
import multiprocessing as mp
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch
from glassine_synth.cli import font_tables
from glassine_synth.ink import Params, line

from .features import NFEAT, features
from .text import CHARS

ALPHABET = CHARS  # class k+1 = ALPHABET[k]; class 0 = CTC blank
DOWNSAMPLE = 8  # input points per model frame at most (model.py, down=3)

_TABLES = None
_PARAMS = None
_LOADED: dict[str, dict] = {}  # font id -> glyph table, kept across builds in one process


def tables(font_cache: Path, fonts: list[str], workers: int = 8):
    missing = [f for f in fonts if f not in _LOADED]
    if missing:
        for fid, t in font_tables(font_cache, ALPHABET.replace(" ", ""), missing, workers):
            _LOADED[fid] = t
    if set(fonts) - set(_LOADED):
        raise SystemExit(f"fonts missing from {font_cache}: {sorted(set(fonts) - set(_LOADED))}")
    return [(f, _LOADED[f]) for f in fonts]  # order matters: line() draws fonts by index


def encode(s: str) -> list[int]:
    return [ALPHABET.index(c) + 1 for c in s]


def decode(ids) -> str:
    return "".join(ALPHABET[i - 1] for i in ids)


def ctc_min_frames(s: str) -> int:
    """Frames CTC needs: one per label plus a blank between equal neighbours."""
    return len(s) + sum(a == b for a, b in zip(s, s[1:]))


def _one(args):
    text, seed = args
    smp = line(text, _TABLES, np.random.default_rng(seed), _PARAMS)
    return features(smp["strokes"]).astype(np.float16)


def build(out: Path, texts: list[str], fonts: list[str], params: Params, seed: int,
          font_cache: Path, workers: int = 8, provenance: dict | None = None,
          glyph_tables: list | None = None):
    """Render texts (one sample each) with only `fonts` (or the given glyph tables),
    store features under out/."""
    global _TABLES, _PARAMS
    _TABLES = glyph_tables if glyph_tables is not None else tables(font_cache, fonts, workers)
    _PARAMS = params
    out.mkdir(parents=True, exist_ok=True)
    seeds = np.random.default_rng(seed).integers(0, 2**62, len(texts))
    offsets = [0]
    short = 0
    tmp = out / "feat.f16.tmp"
    with open(tmp, "wb") as fh, mp.get_context("fork").Pool(workers) as pool:
        for i, f in enumerate(pool.imap(_one, zip(texts, seeds.tolist()), chunksize=64)):
            fh.write(f.tobytes())
            offsets.append(offsets[-1] + len(f))
            short += len(f) // DOWNSAMPLE < ctc_min_frames(texts[i])
            if (i + 1) % 20000 == 0:
                print(f"  {out.name}: {i + 1}/{len(texts)}", flush=True)
    tmp.rename(out / "feat.f16")
    np.save(out / "offsets.npy", np.array(offsets, np.int64))
    (out / "texts.json").write_text(json.dumps(texts))
    meta = {"n": len(texts), "points": offsets[-1], "fonts": sorted(fonts), "seed": seed,
            "params": asdict(params), "too_short_for_ctc": int(short)}
    if provenance:
        meta["provenance"] = provenance
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"{out}: {len(texts)} lines, {offsets[-1]} points, {short} too short for CTC", flush=True)


def augment(f: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Random slant and stretch of a whole line, applied to the features directly (the map
    is linear, so moves map like positions): writers differ more than seven fonts do."""
    sh = rng.normal(0, 0.15)
    sx, sy = rng.lognormal(0, 0.12), rng.lognormal(0, 0.08)
    out = f.copy()
    out[:, 0] = sx * f[:, 0] + sh * f[:, 1]
    out[:, 1] = sy * f[:, 1]
    out[:, 2] = sy * f[:, 2]
    out[:, 4] = f[:, 4] * rng.lognormal(0, 0.3)
    return out


class InkSet(torch.utils.data.Dataset):
    def __init__(self, path: Path, limit: int | None = None, augment: bool = False):
        self.augment = augment
        self.off = np.load(path / "offsets.npy")
        self.texts = json.loads((path / "texts.json").read_text())
        self.feat = np.memmap(path / "feat.f16", np.float16, "r").reshape(-1, NFEAT)
        self.n = len(self.texts) if limit is None else min(limit, len(self.texts))

    def __len__(self):
        return self.n

    def length(self, i):
        return int(self.off[i + 1] - self.off[i])

    def __getitem__(self, i):
        x = np.asarray(self.feat[self.off[i]:self.off[i + 1]], np.float32)
        if self.augment:
            x = augment(x, np.random.default_rng())
        return torch.from_numpy(x), self.texts[i]


def collate(batch):
    xs, texts = zip(*batch)
    lens = torch.tensor([len(x) for x in xs])
    x = torch.nn.utils.rnn.pad_sequence(xs, batch_first=True)
    ys = [torch.tensor(encode(t)) for t in texts]
    ylens = torch.tensor([len(y) for y in ys])
    return x, lens, torch.cat(ys) if ys else torch.zeros(0, dtype=torch.long), ylens, list(texts)


class BucketSampler(torch.utils.data.Sampler):
    """Batches of similar length (less padding), in random order each epoch."""

    def __init__(self, ds: InkSet, batch: int, seed: int = 0, down: int = 3):
        self.ds, self.batch, self.seed, self.epoch = ds, batch, seed, 0
        self.lens = np.array([ds.length(i) for i in range(len(ds))])
        frames = self.lens.copy()
        for _ in range(down):
            frames = (frames - 1) // 2 + 1
        need = np.array([ctc_min_frames(t) for t in ds.texts[:len(ds)]])
        self.keep = np.nonzero(frames >= need)[0]  # CTC cannot label the rest; left out
        self.dropped = len(ds) - len(self.keep)

    def __iter__(self):
        rng = np.random.default_rng(self.seed + self.epoch)
        self.epoch += 1
        idx = rng.permutation(self.keep)
        out = []
        for k in range(0, len(idx), self.batch * 50):
            chunk = idx[k:k + self.batch * 50]
            chunk = chunk[np.argsort(self.lens[chunk])]
            out += [chunk[j:j + self.batch].tolist() for j in range(0, len(chunk), self.batch)]
        rng.shuffle(out)
        return iter(out)

    def __len__(self):
        return (len(self.keep) + self.batch - 1) // self.batch
