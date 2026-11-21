"""A point-labelling aligner (prototype, not in the product): a small network that gives
every point of a line its character, trained on synthetic ink where the generator knows
each point's character (gen.Generator.line(points=True)).

**Loss.** A CTC variant for labelling every point (after the description in Kotani, Tellex
and Tompkin, "Generating Handwriting via Decoupled Style Descriptors", ECCV 2020; our own
code): every frame belongs to a character, so there are no blank runs. A blank is allowed
only between two equal characters in a row (`ll`), and there it is required and lasts one
frame: without it the two would be one run. The states form one chain, the characters in
order with a one-frame blank between equal neighbours; a character's state repeats, a
blank's does not. The loss is minus the log of the sum over the chain's paths; the target
is the line's characters with ink in the order of writing (runs of one character; a dot
written after the word is a second run of its letter), never the text's spaces.

**Inference.** The best path through the chain of a given text (Viterbi): with the text
alone, the characters in text order; with the stroke-level spans known (a sample's
`spans`), each stroke on its own over the letters listed for it, in order.

**Input.** The ink as features.py has it (tilt removed, scaled to its own core height,
resampled every STEP core heights, twice features.STEP), per frame: the move from the frame before, y, pen up,
the pause, and the move's direction; each original point takes the label of its nearest
frame along its stroke.
"""

import numpy as np
import torch
import torch.nn as nn

from .features import CLIP, normalise
from .lines import CHARS4

LETTERS = CHARS4.replace(" ", "")
BLANK = len(LETTERS)
NCLASS = len(LETTERS) + 1
NFEAT = 7
NEG = -1e30
STEP = 0.2  # frames every this many core heights (features.py resamples at half of it)


def resample_map(q: np.ndarray, step: float) -> tuple[np.ndarray, np.ndarray]:
    """(frames (k, 3), the frame nearest each point (n,)) for one stroke resampled by arc."""
    xy = q[:, :2]
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
    if len(q) < 2 or d[-1] < step:
        return q[:1].copy(), np.zeros(len(q), int)
    u = np.linspace(0, d[-1], int(np.ceil(d[-1] / step)) + 1)
    fr = np.c_[np.interp(u, d, q[:, 0]), np.interp(u, d, q[:, 1]), np.interp(u, d, q[:, 2])]
    near = np.clip(np.rint(d / (u[1] - u[0])).astype(int), 0, len(u) - 1)
    return fr, near


def featurize(strokes: list[np.ndarray]) -> tuple[np.ndarray, list[np.ndarray], list[int]]:
    """(features (T, NFEAT), per stroke the frame of each of its points, per stroke its first
    frame). Strokes as (n, 3) arrays of x, y (down), t; empty strokes keep an empty map."""
    keep = [i for i, q in enumerate(strokes) if len(q)]
    norm = normalise([np.asarray(strokes[i], float)[:, :3] for i in keep])
    rows, maps, firsts = [], [np.zeros(0, int) for _ in strokes], [0] * len(strokes)
    prev = prev_t = None
    at = 0
    for k, (i, q) in enumerate(zip(keep, norm)):
        fr, near = resample_map(q, STEP)
        f = np.zeros((len(fr), NFEAT))
        start = prev if prev is not None else fr[0, :2]
        f[:, 0:2] = np.diff(np.vstack([start, fr[:, :2]]), axis=0)
        f[:, 2] = fr[:, 1]
        if k:
            f[0, 3] = 1.0
            f[0, 4] = np.log1p(max(0.0, fr[0, 2] - prev_t) * 10)
        n = np.linalg.norm(f[:, 0:2], axis=1, keepdims=True)
        f[:, 5:7] = np.where(n > 1e-9, f[:, 0:2] / np.maximum(n, 1e-9), 0)
        if k:
            f[0, 5:7] = 0  # the pen-up jump has no direction of writing
        rows.append(f)
        maps[i], firsts[i] = near + at, at
        at += len(fr)
        prev, prev_t = fr[-1, :2], fr[-1, 2]
    out = np.concatenate(rows) if rows else np.zeros((0, NFEAT))
    np.clip(out, -CLIP, CLIP, out=out)
    return out.astype(np.float32), maps, firsts


def frame_truth(maps: list[np.ndarray], owners: list[np.ndarray], T: int) -> np.ndarray:
    """Each frame's character (index into the text; -1 a join between characters): that of
    the original point nearest it, by the points' frames."""
    lab = np.full(T, -2, int)
    for m, o in zip(maps, owners):
        for f, c in zip(m, o):
            if lab[f] == -2:
                lab[f] = c
    # frames between two points' frames: the point before
    last = -1
    for t in range(T):
        if lab[t] == -2:
            lab[t] = last
        last = lab[t]
    return lab


def target_of(frame_chars: np.ndarray) -> list[int]:
    """The writing-order target: runs of one character (joins left out)."""
    out = []
    for c in frame_chars:
        if c >= 0 and (not out or out[-1] != c):
            out.append(int(c))
    return out


def chain(labels: list[int]) -> tuple[list[int], list[bool]]:
    """The states for class labels: (class per state, may repeat), a blank between equals."""
    cls, rep = [], []
    for i, c in enumerate(labels):
        if i and labels[i - 1] == c:
            cls.append(BLANK)
            rep.append(False)
        cls.append(c)
        rep.append(True)
    return cls, rep


def chain_loss(logp: torch.Tensor, lens: torch.Tensor, targets: list[list[int]]) -> torch.Tensor:
    """Minus the log-likelihood of each line under its chain, summed over the batch.
    logp (B, T, NCLASS) log-softmax; targets: class labels per line."""
    B, T, _ = logp.shape
    chains = [chain(t) for t in targets]
    S = max(len(c) for c, _ in chains)
    cls = torch.full((B, S), BLANK, dtype=torch.long)
    rep = torch.zeros((B, S), dtype=torch.bool)
    last = torch.zeros(B, dtype=torch.long)
    for b, (c, r) in enumerate(chains):
        cls[b, :len(c)] = torch.tensor(c)
        rep[b, :len(r)] = torch.tensor(r)
        last[b] = len(c) - 1
    emit = logp.gather(2, cls[:, None, :].expand(B, T, S))  # (B, T, S)
    neg = torch.full((B, 1), NEG, dtype=logp.dtype)
    alpha = torch.cat([emit[:, 0, :1], torch.full((B, S - 1), NEG, dtype=logp.dtype)], 1)
    ends = torch.full((B,), NEG, dtype=logp.dtype)
    for t in range(1, T):
        stay = torch.where(rep, alpha, torch.full_like(alpha, NEG))
        move = torch.cat([neg, alpha[:, :-1]], 1)
        alpha = torch.logaddexp(stay, move) + emit[:, t]
        done = lens == t + 1
        if done.any():
            ends = torch.where(done, alpha.gather(1, last[:, None])[:, 0], ends)
    one = lens == 1
    if one.any():
        a0 = torch.cat([emit[:, 0, :1], torch.full((B, S - 1), NEG, dtype=logp.dtype)], 1)
        ends = torch.where(one, a0.gather(1, last[:, None])[:, 0], ends)
    return -(ends.clamp_min(NEG / 2)).sum()


def viterbi(logp: np.ndarray, labels: list[int]) -> np.ndarray | None:
    """The best path's class index into `labels` per frame (-1 the blank), or None when the
    frames are too few for the chain."""
    cls, rep = chain(labels)
    owner = []
    k = 0
    for i, c in enumerate(cls):
        owner.append(-1 if c == BLANK else k)
        if c != BLANK:
            k += 1
    T, S = len(logp), len(cls)
    if T < S:
        return None
    e = logp[:, cls]
    a = np.full(S, NEG)
    a[0] = e[0, 0]
    back = np.zeros((T, S), np.int8)  # 1: came from the state before
    repm = np.array(rep)
    for t in range(1, T):
        stay = np.where(repm, a, NEG)
        move = np.r_[NEG, a[:-1]]
        back[t] = move > stay
        a = np.maximum(stay, move) + e[t]
    s = S - 1
    path = np.zeros(T, int)
    for t in range(T - 1, -1, -1):
        path[t] = s
        if t and back[t, s]:
            s -= 1
    return np.array(owner)[path]


class Aligner(nn.Module):
    def __init__(self, hidden: int = 128, layers: int = 2):
        super().__init__()
        self.conv = nn.Sequential(nn.Conv1d(NFEAT, hidden, 5, padding=2), nn.GELU(),
                                  nn.Conv1d(hidden, hidden, 5, padding=2), nn.GELU())
        self.rnn = nn.LSTM(hidden, hidden, layers, batch_first=True, bidirectional=True, dropout=0.1 if layers > 1 else 0.0)
        self.out = nn.Linear(2 * hidden, NCLASS)

    def forward(self, x):  # (B, T, NFEAT) -> (B, T, NCLASS) log-probabilities
        h = self.conv(x.transpose(1, 2)).transpose(1, 2)
        h, _ = self.rnn(h)
        return torch.log_softmax(self.out(h), -1)


def class_of(text: str, idx: list[int]) -> list[int]:
    return [LETTERS.index(text[i]) if text[i] in LETTERS else BLANK for i in idx]


def label_points(model: Aligner, strokes: list[np.ndarray], text: str, spans: list[list[int]] | None = None) -> list[np.ndarray]:
    """Each point's character (index into text; -1 none): text alone (spans None), or each
    stroke over the letters its span lists."""
    x, maps, firsts = featurize(strokes)
    with torch.no_grad():
        lp = model(torch.from_numpy(x)[None])[0].numpy()
    out = [np.full(len(q), -1, int) for q in strokes]
    if spans is None:
        idx = [i for i, c in enumerate(text) if c in LETTERS]
        if not idx:
            return out
        own = viterbi(lp, class_of(text, idx))
        if own is None:
            return out
        fc = np.where(own >= 0, np.array(idx)[np.maximum(own, 0)], -1)
        # a one-frame blank between equal letters goes with the letter after it
        for t in range(len(fc) - 1, -1, -1):
            if fc[t] < 0 and t + 1 < len(fc):
                fc[t] = fc[t + 1]
        return [fc[m] if len(m) else np.zeros(0, int) for m in maps]
    for j, q in enumerate(strokes):
        if not len(q):
            continue
        chars = sorted(c for c, sp in enumerate(spans) if j in sp and text[c] in LETTERS)
        if not chars:
            continue
        m = maps[j]
        f0, f1 = int(m.min()), int(m.max()) + 1
        own = viterbi(lp[f0:f1], class_of(text, chars)) if len(chars) > 1 else np.zeros(f1 - f0, int)
        if own is None:
            out[j][:] = chars[0]
            continue
        for t in range(len(own) - 1, -1, -1):
            if own[t] < 0:
                own[t] = own[t + 1] if t + 1 < len(own) else 0
        out[j] = np.array(chars)[own[m - f0]]
    return out
