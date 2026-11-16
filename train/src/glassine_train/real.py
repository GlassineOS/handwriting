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

With a kept test set (`keep_test`, the ids of an earlier split's test session) the test set
never changes as new samples arrive: exactly those ids are the test set (every one must be
in the store), no new sample joins it, and samples written in the test set's sessions that
are not in it are left out of every split (`left_out`), so no ink from a test session
trains. Dev is then whole sessions, the latest ones besides the test sessions, until at
least DEV_MIN samples; train is the rest. A new pull of the store gives the same test set
and moves only the newest sessions into dev.

Test texts stay out of training: a train or dev sample whose text is a test sample's, or
nearly (near_test: the same after case, marks and spaces are dropped, or at least NEAR
alike by difflib for texts of 4 characters or more), is left out (`near_test`).

No test sample ever trains, an adaptation included: the split writes its test ids to
test-ids.txt, and every reader of training ink (load_split, check_store, the stores the
split writes) checks its samples' ids against them and against any further ids file given
(`guard`), and stops when one is there. The test samples are read only by evaluation.

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
NEAR = 0.85
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


def _norm(t: str) -> str:
    import re
    return " ".join(re.sub(r"[^\w\s]", "", t.casefold()).split())


def near_test(text: str, tests: list[str]) -> bool:
    """A text the same as a test text, or nearly (module docstring)."""
    import difflib
    n = _norm(text)
    for t in tests:
        m = _norm(t)
        if n == m:
            return True
        if len(text) >= 4 and len(t) >= 4 and difflib.SequenceMatcher(None, n, m).ratio() >= NEAR:
            return True
    return False


def line_id(ln: str) -> str:
    return "%016x" % fnv1a64(ln.encode("utf-8"))


def test_ids(split_dir: Path, extra: Path | None = None) -> set[str]:
    """The ids no training may see: the split's test set (test-ids.txt, split.json) and an
    ids file given."""
    ids = set()
    if (split_dir / "test-ids.txt").exists():
        ids |= set(kept_test_ids(split_dir / "test-ids.txt"))
    if (split_dir / "split.json").exists():
        ids |= set(json.loads((split_dir / "split.json").read_text())["ids"]["test"])
    if extra:
        ids |= set(kept_test_ids(Path(extra)))
    if not ids:
        raise SystemExit(f"{split_dir}: no test ids to guard against (test-ids.txt, split.json)")
    return ids


def guard(lines: list[str], ids: set[str], what: str):
    """Stop when a line to train on is a test sample."""
    bad = sum(line_id(ln) in ids for ln in lines)
    if bad:
        raise SystemExit(f"STOP: {bad} test sample(s) in {what}: test samples never train (real.py)")


def check_store(store: Path, ids: set[str]):
    """A recognizer store an adaptation learns from: none of its samples is a test sample."""
    raw = (store / "samples.jsonl").read_bytes().decode("utf-8").replace("\0", "\n").split("\n")
    guard([ln for ln in raw if ln.startswith("{")], ids, str(store))


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


def split_kept(recs: list[dict], keep_test: list[str]) -> dict[str, list[str]]:
    """{train, dev, test, left_out} with the test set fixed to `keep_test` (module docstring)."""
    recs = [r for r in recs if r["time"] is not None]
    ids = {r["id"] for r in recs}
    missing = [i for i in keep_test if i not in ids]
    if missing:
        raise SystemExit(f"{len(missing)} of the {len(keep_test)} kept test samples are not in the store")
    kept = set(keep_test)
    sess = sessions([r["time"] for r in recs])
    test_sess = {s for r, s in zip(recs, sess) if r["id"] in kept}
    left = [r["id"] for r, s in zip(recs, sess) if s in test_sess and r["id"] not in kept]
    rest = sorted({s for s in sess if s not in test_sess}, reverse=True)
    dev_sess = set()
    n = 0
    for s in rest:  # whole sessions from the newest until DEV_MIN samples
        if n >= DEV_MIN or len(dev_sess) == len(rest) - 1:  # at least one session stays for train
            break
        dev_sess.add(s)
        n += sum(x == s for x in sess)
    dev = [r["id"] for r, s in zip(recs, sess) if s in dev_sess]
    train = [r["id"] for r, s in zip(recs, sess) if s not in dev_sess and s not in test_sess]
    if not train:
        raise SystemExit("no session left for training besides the kept test set and dev")
    return {"train": train, "dev": dev, "test": [i for i in keep_test], "left_out": left}


def split(recs: list[dict], keep_test: list[str] | None = None) -> dict[str, list[str]]:
    """{train, dev, test}: sample ids, by the rule in the module docstring."""
    if keep_test is not None:
        return split_kept(recs, keep_test)
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


def load_split(path: Path, extra_test_ids: Path | None = None) -> dict[str, list[dict]]:
    """The prepared split (`glassine-train real`): {train, dev, test} -> samples; train and
    dev checked against the test ids (guard)."""
    ids = test_ids(path, extra_test_ids)
    out = {}
    for k in ("train", "dev", "test"):
        lines = [ln for ln in (path / f"{k}.jsonl").read_text().splitlines() if ln]
        if k != "test":
            guard(lines, ids, f"{path}/{k}.jsonl")
        out[k] = [json.loads(ln) for ln in lines]
    return out


def kept_test_ids(path: Path) -> list[str]:
    """The test ids of an earlier split (its split.json), or of a file of ids, one per line."""
    raw = path.read_text()
    if raw.lstrip().startswith("{"):
        return list(json.loads(raw)["ids"]["test"])
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


def prepare(store: Path, out: Path, keep_test: list[str] | None = None) -> dict:
    """Write the split of a store to out/ ({train,dev,test}.jsonl with the original lines,
    {train,dev}.meta for an adaptation store, split.json with ids and counts). Stays on
    the host: the samples are a person's handwriting. With `keep_test`, the test set is
    those ids (split_kept)."""
    recs = read_store(store)
    by = {r["id"]: r for r in recs}
    sp = split(recs, keep_test)
    left_out = sp.pop("left_out", None)
    tests = [by[i]["sample"]["text"] for i in sp["test"]]
    tid = set(sp["test"])
    # the sessions stay as split (dev may fall below DEV_MIN rather than take a training session)
    near = {k: [i for i in sp[k] if near_test(by[i]["sample"]["text"], tests)] for k in ("train", "dev")}
    for k in near:
        sp[k] = [i for i in sp[k] if i not in set(near[k])]
    out.mkdir(parents=True, exist_ok=True)
    for k, ids in sp.items():
        (out / f"{k}.jsonl").write_text("".join(by[i]["line"] + "\n" for i in ids))
    # the test session in two halves by time, for reading only
    tt_ = sorted(sp["test"], key=lambda i: by[i]["time"])
    half = {"test-a": tt_[:len(tt_) // 2], "test-b": tt_[len(tt_) // 2:]}
    for k, ids in half.items():
        (out / f"{k}.jsonl").write_text("".join(by[i]["line"] + "\n" for i in ids))
    meta = (store / "samples.meta").read_text().splitlines()
    lines = {ln.split("\t")[0]: ln for ln in meta[1:]}
    # the recognizer store an adaptation learns from: the training sessions only (no test
    # sample ever trains, an adaptation included)
    (out / "test-ids.txt").write_text("".join(i + "\n" for i in sp["test"]))
    for name, ids in (("store-trainsessions", sp["train"] + sp["dev"]),):
        st = out / name
        st.mkdir(exist_ok=True)
        (st / "samples.jsonl").write_text("".join(by[i]["line"] + "\n" for i in ids))
        (st / "samples.meta").write_text(meta[0] + "\n" + "".join(lines[i] + "\n" for i in ids))
        check_store(st, tid)
    for k in ("train", "dev"):
        guard([by[i]["line"] for i in sp[k]], tid, k)
    rep = {k: len(v) for k, v in sp.items()}
    rep["unusable"] = {k: sum(not usable(by[i]["sample"]) for i in v) for k, v in sp.items()}
    rep["single_chars"] = {k: sum(len(by[i]["sample"]["text"]) == 1 for i in v) for k, v in sp.items()}
    # the confirmation time of the newest sample a model trained here may have seen (train,
    # dev): an adaptation of that model leaves out everything up to it (docs/recognizer.md)
    rep["trained_until"] = max(by[i]["time"] for i in sp["train"] + sp["dev"])
    rep["sessions"] = max(sessions([r["time"] for r in recs if r["time"] is not None])) + 1
    tt = {k: {by[i]["sample"]["text"] for i in v} for k, v in sp.items()}
    rep["test_texts_also_in_train_or_dev"] = len(tt["test"] & (tt["train"] | tt["dev"]))
    rep["near_test_left_out"] = {k: len(v) for k, v in near.items()}
    ids = dict(sp)
    ids["near_test"] = near["train"] + near["dev"]
    if left_out is not None:
        rep["kept_test"] = True
        rep["left_out"] = len(left_out)
        ids["left_out"] = left_out
    (out / "split.json").write_text(json.dumps({"ids": ids, "report": rep}, indent=1))
    return rep


def reference(s: dict, writer_xh: float | None) -> dict:
    """A real sample's references for features with a reference height (features_ref): the
    writer's letter height (`writer_xh`, measured on the training sessions; None when not
    known), the writing area's line height and the baseline of the guide the ink sits on
    (the guides are at two thirds of each line), all in the sample's units."""
    ar = s.get("area") or {}
    line = ar.get("line")
    base = None
    if line:
        ys = np.concatenate([np.asarray(q, float)[:, 1] for q in s["strokes"] if len(q)])
        y0 = ar.get("box", [0, 0])[1]
        first = y0 + line * 2 / 3
        k = int(np.ceil((np.median(ys) - first) / line))
        base = float(first + max(k, 0) * line)
    return {"xh": writer_xh, "line": line, "base": base}
