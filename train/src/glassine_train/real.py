"""Real ink: a person's stored samples (sample format v1, `docs/samples.md`) for training
and evaluation.

The store is the recognizer's own pair of files, `samples.jsonl` (one sample per line) and
`samples.meta` (id, client, time, source, language per sample; `docs/recognizer.md`). A
sample's id is the FNV-1a 64 hash of its line, as in the recognizer.

The split is by writing session, never by id, so held-back ink is ink written on another
occasion (other pen grip, mood, pace), as a new day's writing is:

- sessions: samples ordered by time, a new session after a pause of more than
  SESSION_GAP seconds;
- test: the last session, never used for any choice;
- dev (checkpoint selection, early stopping): the trailing work blocks (split at pauses of
  more than BLOCK_GAP seconds) of the session before it, taken from its end until at least
  DEV_MIN samples;
- train: the rest.

Real ink is kept as strokes, not features: rotation, point jitter, resampling, time warp
and stroke dropout act on the pen's points, so the features are computed per draw.
"""

import json
from pathlib import Path

import numpy as np
import torch

from .data import augment as feature_augment
from .features import features
from .text import CHARS

SESSION_GAP = 1800  # s
BLOCK_GAP = 600  # s
DEV_MIN = 80


def fnv1a64(b: bytes) -> int:
    h = 1469598103934665603
    for x in b:
        h = ((h ^ x) * 1099511628211) & (2**64 - 1)
    return h


def read_store(store: Path) -> list[dict]:
    """[{id, time, line, sample}] for every sample of a store, in file order. Zero bytes
    (a lost append) end a line, as in the recognizer."""
    raw = (store / "samples.jsonl").read_bytes().decode("utf-8").replace("\0", "\n").split("\n")
    meta = {}
    mp = store / "samples.meta"
    if mp.exists():
        for ln in mp.read_text().splitlines()[1:]:
            f = ln.split("\t")
            if len(f) >= 3:
                meta[f[0]] = int(f[2])
    out = []
    for ln in raw:
        if not (ln.startswith("{") and ln.endswith("}")):
            continue
        s = json.loads(ln)
        if s.get("version") != 1:
            continue
        sid = "%016x" % fnv1a64(ln.encode("utf-8"))
        out.append({"id": sid, "time": meta.get(sid), "line": ln, "sample": s})
    return out


def sessions(times: list[int], gap: float = SESSION_GAP) -> list[int]:
    """Session number per sample (0 = earliest) for times in seconds."""
    order = np.argsort(times, kind="stable")
    sess = [0] * len(times)
    k = 0
    for a, b in zip(order, order[1:]):
        if times[b] - times[a] > gap:
            k += 1
        sess[b] = k
    return sess


def split(recs: list[dict]) -> dict[str, list[str]]:
    """{train, dev, test}: sample ids, by the rule in the module docstring."""
    recs = [r for r in recs if r["time"] is not None]
    times = [r["time"] for r in recs]
    sess = sessions(times)
    last = max(sess)
    if last < 1:
        raise SystemExit("one writing session only: nothing to hold back by session")
    test = [r["id"] for r, s in zip(recs, sess) if s == last]
    prev = sorted([(r["time"], r["id"]) for r, s in zip(recs, sess) if s == last - 1])
    dev = []
    end = len(prev)
    for i in range(len(prev) - 1, 0, -1):  # block starts, from the end
        if prev[i][0] - prev[i - 1][0] > BLOCK_GAP:
            dev = [sid for _, sid in prev[i:]]
            if len(dev) >= DEV_MIN:
                break
    if len(dev) < DEV_MIN:  # no pause late enough: the session's last DEV_MIN samples
        dev = [sid for _, sid in prev[max(0, end - DEV_MIN):]]
    held = set(test) | set(dev)
    train = [r["id"] for r in recs if r["id"] not in held]
    return {"train": train, "dev": dev, "test": test}


def usable(s: dict) -> bool:
    """A sample the trainer can learn from: known characters, some ink."""
    return bool(s["text"]) and all(c in CHARS for c in s["text"]) and any(len(q) for q in s["strokes"])


def strokes_of(s: dict) -> list[np.ndarray]:
    """(n, 3) arrays of x, y, t (seconds) per stroke; extra point fields dropped."""
    return [np.asarray(q, float)[:, :3] for q in s["strokes"] if len(q)]


# ---------------------------------------------------------------- augmentation on the ink

def _resample_time(q: np.ndarray, rate: float) -> np.ndarray:
    """The stroke as a pen reporting `rate` points per second would have given it."""
    if len(q) < 3 or q[-1, 2] - q[0, 2] <= 0:
        return q
    t = np.arange(q[0, 2], q[-1, 2], 1.0 / rate)
    if len(t) < 2:
        return q[[0, -1]]
    t = np.r_[t, q[-1, 2]] if t[-1] < q[-1, 2] else t
    return np.c_[np.interp(t, q[:, 2], q[:, 0]), np.interp(t, q[:, 2], q[:, 1]), t]


def ink_augment(strokes: list[np.ndarray], rng: np.random.Generator, rot_deg: float = 2.0,
                jitter: float = 0.006, rate: tuple = (120.0, 333.0), warp: float = 0.2,
                pause_warp: float = 0.3, dropout: float = 0.03) -> list[np.ndarray]:
    """A new drawing of the same text by the same hand: a small rotation of the whole
    sample, point jitter (sd `jitter` × ink height), the points resampled at a random pen
    rate, each stroke's duration and each pen-up pause scaled (lognormal sds `warp`,
    `pause_warp`), and rarely (`dropout`) one short stroke left out, as when a dot is
    missed by the pen."""
    pts = np.concatenate(strokes)
    h = max(np.ptp(pts[:, 1]), np.ptp(pts[:, 0]) / 4, 1e-6)
    a = np.radians(rng.normal(0, rot_deg))
    c, s_ = np.cos(a), np.sin(a)
    cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
    out = []
    r = rng.uniform(*rate)
    for q in strokes:
        q = q.copy()
        x, y = q[:, 0] - cx, q[:, 1] - cy
        q[:, 0], q[:, 1] = cx + x * c - y * s_, cy + x * s_ + y * c
        q = _resample_time(q, r)
        q[:, :2] += rng.normal(0, jitter * h, q[:, :2].shape)
        out.append(q)
    if dropout and len(out) >= 3 and rng.random() < dropout:
        lens = np.array([np.linalg.norm(np.diff(q[:, :2], axis=0), axis=1).sum() for q in out])
        small = np.nonzero(lens < 0.1 * lens.sum())[0]
        if len(small):
            out.pop(int(rng.choice(small)))
    # time warp: stroke durations and pauses scaled, times rebuilt in order
    t, prev_end, res = 0.0, None, []
    for q in out:
        d = (q[:, 2] - q[0, 2]) * rng.lognormal(0, warp)
        if prev_end is not None:
            t += max(0.0, q[0, 2] - prev_end) * rng.lognormal(0, pause_warp)
        prev_end = q[-1, 2]
        res.append(np.c_[q[:, :2], t + d])
        t = t + (d[-1] if len(d) else 0.0)
    return res


class RealSet(torch.utils.data.Dataset):
    """Real samples as (features, text); with `augment` a new drawing each time (ink-level
    augmentation, then the trainer's slant and stretch on the features)."""

    def __init__(self, samples: list[dict], augment: bool = False):
        self.items = [(strokes_of(s), s["text"]) for s in samples if usable(s)]
        self.texts = [t for _, t in self.items]
        self.augment = augment
        self._feat = None if augment else [features(st) for st, _ in self.items]

    def __len__(self):
        return len(self.items)

    def length(self, i):
        if self._feat is not None:
            return len(self._feat[i])
        return len(features(self.items[i][0]))

    def __getitem__(self, i):
        st, text = self.items[i]
        if not self.augment:
            return torch.from_numpy(self._feat[i]), text
        rng = np.random.default_rng()
        f = features(ink_augment(st, rng))
        return torch.from_numpy(feature_augment(f, rng)), text


def load_split(path: Path) -> dict[str, list[dict]]:
    """The prepared split (`glassine-train real`): {train, dev, test} -> samples."""
    return {k: [json.loads(ln) for ln in (path / f"{k}.jsonl").read_text().splitlines() if ln]
            for k in ("train", "dev", "test")}


def prepare(store: Path, out: Path) -> dict:
    """Write the split of a store to out/ ({train,dev,test}.jsonl with the original lines,
    {train,dev}.meta for an adaptation store, split.json with ids and counts). Stays on
    the host: the samples are a person's handwriting."""
    recs = read_store(store)
    sp = split(recs)
    by = {r["id"]: r for r in recs}
    out.mkdir(parents=True, exist_ok=True)
    for k, ids in sp.items():
        (out / f"{k}.jsonl").write_text("".join(by[i]["line"] + "\n" for i in ids))
    # the test session in two halves by time: an adaptation learns from the first (with
    # everything before it), and is read on the second, as on a device after an update
    tt_ = sorted(sp["test"], key=lambda i: by[i]["time"])
    half = {"test-a": tt_[:len(tt_) // 2], "test-b": tt_[len(tt_) // 2:]}
    for k, ids in half.items():
        (out / f"{k}.jsonl").write_text("".join(by[i]["line"] + "\n" for i in ids))
    meta = (store / "samples.meta").read_text().splitlines()
    lines = {ln.split("\t")[0]: ln for ln in meta[1:]}
    # recognizer stores: the training sessions, and those plus the test session's first half
    for name, ids in (("store-trainsessions", sp["train"] + sp["dev"]),
                      ("store-plus-test-a", sp["train"] + sp["dev"] + half["test-a"])):
        st = out / name
        st.mkdir(exist_ok=True)
        (st / "samples.jsonl").write_text("".join(by[i]["line"] + "\n" for i in ids))
        (st / "samples.meta").write_text(meta[0] + "\n" + "".join(lines[i] + "\n" for i in ids))
    rep = {k: len(v) for k, v in sp.items()}
    rep["unusable"] = {k: sum(not usable(by[i]["sample"]) for i in v) for k, v in sp.items()}
    rep["single_chars"] = {k: sum(len(by[i]["sample"]["text"]) == 1 for i in v) for k, v in sp.items()}
    # the confirmation time of the newest sample a model trained here may have seen (train,
    # dev): an adaptation of that model leaves out everything up to it (docs/recognizer.md)
    rep["trained_until"] = max(by[i]["time"] for i in sp["train"] + sp["dev"])
    rep["sessions"] = max(sessions([r["time"] for r in recs if r["time"] is not None])) + 1
    tt = {k: {by[i]["sample"]["text"] for i in v} for k, v in sp.items()}
    rep["test_texts_also_in_train_or_dev"] = len(tt["test"] & (tt["train"] | tt["dev"]))
    (out / "split.json").write_text(json.dumps({"ids": sp, "report": rep}, indent=1))
    return rep
