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


# ---------------------------------------------------------------------------- the engine's shape

def featurize_rows(strokes: list[np.ndarray]) -> tuple[np.ndarray, list[np.ndarray]]:
    """The recognizer's own input rows (features.features: tilt removed, own core height,
    resampled every features.STEP) and per stroke the frame of each of its points for a model
    halving the rows once (PointAligner): a point's row is the one nearest it along its
    stroke, its frame (row + 1) // 2. Empty strokes keep an empty map. The C engine maps
    points the same way (recognizer/src/hw/aligner.c)."""
    from . import features as F
    keep = [i for i, q in enumerate(strokes) if len(q)]
    x = F.features([np.asarray(strokes[i], float)[:, :3] for i in keep])
    if not len(x):
        return x, [np.zeros(0, int) for _ in strokes]
    norm = F.normalise([np.asarray(strokes[i], float)[:, :3] for i in keep])
    nf = (len(x) - 1) // 2 + 1
    maps = [np.zeros(0, int) for _ in strokes]
    at = 0
    for i, q in zip(keep, norm):
        k = len(F._resample(q, F.STEP))
        d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(q[:, :2], axis=0), axis=1))]
        row = np.zeros(len(q), int) if k < 2 or d[-1] <= 0 else np.floor(d * (k - 1) / d[-1] + 0.5).astype(int)
        maps[i] = np.minimum((np.clip(row, 0, k - 1) + at + 1) // 2, nf - 1)
        at += k
    return x, maps


class PointAligner(nn.Module):
    """The aligner in the shape the recognizer's C engine runs (model.Recognizer: a
    convolution, one strided convolution, a two-layer bidirectional LSTM; the recognizer's
    own five features; a frame every 0.2 core heights), exported as a .ghm like a base model
    (class 0 the blank, class k + 1 LETTERS[k]). Its log-probabilities come out in this
    module's order (LETTERS, then BLANK), so the chain and its decoding are shared."""

    def __init__(self, hidden: int = 128, conv: int = 128, layers: int = 2):
        super().__init__()
        from .model import Recognizer
        self.m = Recognizer(conv=conv, hidden=hidden, layers=layers, down=1, alphabet=LETTERS)

    def forward(self, x, lens=None):  # (B, T, 5) -> (B, T', NCLASS)
        # as model.Single (the export) but batched: the convolutions masked past each line's
        # end, the LSTM over the padded batch (packing is many times slower to train on the
        # CPU; batches are of lines of about one length)
        m = self.m
        if lens is None:
            lens = torch.full((x.shape[0],), x.shape[1], dtype=torch.long)
        h = m._mask((x * m.scale).transpose(1, 2), lens)
        h = m._mask(m.act(m.c1(h)), lens)
        l3 = lens
        for c in m.strided:
            l3 = (l3 - 1) // 2 + 1
            h = m._mask(m.act(c(h)), l3)
        h, _ = m.lstm(h.transpose(1, 2))
        lp = m.out(m.drop(h)).log_softmax(-1)
        return torch.cat([lp[..., 1:], lp[..., :1]], -1)

    def out_lengths(self, lens):
        return self.m.out_lengths(lens)


def features_for(model, strokes):
    """(features, per stroke the frame of each point) for the model's kind."""
    if isinstance(model, PointAligner):
        return featurize_rows(strokes)
    x, maps, _ = featurize(strokes)
    return x, maps


def decode_delayed(lp: np.ndarray, firsts: list[int], labels: list[int], group: list[int], pen: float) -> np.ndarray | None:
    """The best path through the chain of `labels` (as viterbi), where in addition a whole
    stroke (frames firsts[j] up to the next first) may be a delayed one: all of it one label
    already passed (index into labels, of the group of the current one or the one before:
    `group` per label, a word), at log-penalty pen, the chain staying where it was. Per frame
    the index into labels (-1 the blank); None when the frames are too few."""
    cls, rep = chain(labels)
    owner = []
    k = 0
    for c in cls:
        owner.append(-1 if c == BLANK else k)
        if c != BLANK:
            k += 1
    T, S = len(lp), len(cls)
    if T < S:
        return None
    owner = np.array(owner)
    lab_of_state = np.maximum.accumulate(np.where(owner >= 0, owner, -1))  # a blank: the label before it
    e = lp[:, cls]
    repm = np.array(rep)
    bounds = list(firsts) + [T]
    back = np.zeros((T, S), np.int8)
    alpha = None
    choice = []  # per stroke: (delayed per state, its label per state)
    for j in range(len(firsts)):
        a, b = bounds[j], bounds[j + 1]
        if b <= a:
            choice.append((np.zeros(S, bool), np.zeros(S, int)))
            continue
        if alpha is None:
            cur = np.full(S, NEG)
            cur[0] = e[a, 0]
            t0 = a + 1
        else:
            cur, t0 = alpha, a
        prev = alpha
        for t in range(t0, b):
            stay = np.where(repm, cur, NEG)
            move = np.r_[NEG, cur[:-1]]
            back[t] = move > stay
            cur = np.maximum(stay, move) + e[t]
        dl = np.zeros(S, bool)
        dlab = np.zeros(S, int)
        if prev is not None:
            sums = lp[a:b].sum(0)
            for s in range(S):
                if owner[s] < 0 or prev[s] <= NEG / 2:
                    continue
                here = lab_of_state[s]
                best, bl = NEG, -1
                for c in range(here):
                    if group[c] < group[here] - 1:
                        continue
                    v = sums[labels[c]]
                    if v > best:
                        best, bl = v, c
                if bl >= 0 and prev[s] + best + pen > cur[s]:
                    cur[s] = prev[s] + best + pen
                    dl[s], dlab[s] = True, bl
        choice.append((dl, dlab))
        alpha = cur
    s = S - 1
    out = np.zeros(T, int)
    for j in range(len(firsts) - 1, -1, -1):
        a, b = bounds[j], bounds[j + 1]
        if b <= a:
            continue
        dl, dlab = choice[j]
        if dl[s]:
            out[a:b] = dlab[s]
            continue
        for t in range(b - 1, a - 1, -1):
            out[t] = owner[s]
            if t > (bounds[0]) and back[t, s]:
                s -= 1
    return out


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


def nearest_delayed(text: str, strokes: list[np.ndarray], own: list[np.ndarray]) -> list[np.ndarray]:
    """A delayed stroke (all of it one character, written after ink of a later one) whose
    character comes again in its word or a word beside it goes to the occurrence whose other
    ink is horizontally nearest the stroke's centre (as aligner.c hw_aligner_nearest: the
    decoding gives it the first of equal letters, "it is" puts both dots on the first i)."""
    own = [np.asarray(p, int).copy() for p in own]
    n = len(strokes)
    whole, late, seen = [-1] * n, [False] * n, -1
    for j in range(n):
        p = own[j]
        if not len(p):
            continue
        if p[0] >= 0 and p[0] < len(text) and (p == p[0]).all():
            whole[j], late[j] = int(p[0]), int(p[0]) < seen
        seen = max(seen, int(p.max()))
    word = [text[:k].count(" ") for k in range(len(text))]
    for j in range(n):
        if not late[j]:
            continue
        r = whole[j]
        x = float(np.asarray(strokes[j], float)[:, 0].mean())
        cands, best = 0, None
        for k in range(len(text)):
            if text[k] != text[r] or abs(word[k] - word[r]) > 1:
                continue
            cands += 1
            xs = [np.asarray(strokes[a], float)[own[a] == k, 0] for a in range(n) if not late[a] and len(own[a])]
            xs = np.concatenate(xs) if xs else np.zeros(0)
            if len(xs) and (best is None or abs(xs.mean() - x) < best[0]):
                best = (abs(float(xs.mean()) - x), k)
        if cands > 1 and best is not None:
            own[j][:] = best[1]
    return own


def label_points(model, strokes: list[np.ndarray], text: str, spans: list[list[int]] | None = None,
                 delay: float | None = None, nearest: bool = False) -> list[np.ndarray]:
    """Each point's character (index into text; -1 none): text alone (spans None), or each
    stroke over the letters its span lists. delay (text alone): a stroke may be a delayed one
    of a letter already passed, at that log-penalty (decode_delayed); None: never. nearest
    (with delay): then each delayed stroke to the nearest of equal letters (nearest_delayed;
    the recognizer's GLASSINE_HANDWRITING_NEATEN=nearest=1, measurements, not shipped)."""
    x, maps = features_for(model, strokes)
    out = [np.full(len(q), -1, int) for q in strokes]
    if not len(x):
        return out
    with torch.no_grad():
        lp = model(torch.from_numpy(np.ascontiguousarray(x))[None])[0].numpy()
    if spans is None:
        idx = [i for i, c in enumerate(text) if c in LETTERS]
        if not idx:
            return out
        # decoded over the frames that hold points only (so every letter on the path gets
        # points; a frame between two sparse points holds none), as aligner.c
        occ = np.unique(np.concatenate([m for m in maps if len(m)]))
        lp = lp[occ]
        maps = [np.searchsorted(occ, m) for m in maps]
        if delay is None:
            own = viterbi(lp, class_of(text, idx))
        else:
            firsts = sorted({int(m.min()) for m in maps if len(m)})
            group = [text[:i].count(" ") for i in idx]
            own = decode_delayed(lp, firsts, class_of(text, idx), group, delay)
        if own is None:
            return out
        fc = np.where(own >= 0, np.array(idx)[np.maximum(own, 0)], -1)
        # a one-frame blank between equal letters goes with the letter after it
        for t in range(len(fc) - 1, -1, -1):
            if fc[t] < 0 and t + 1 < len(fc):
                fc[t] = fc[t + 1]
        got = [fc[m] if len(m) else np.zeros(0, int) for m in maps]
        return nearest_delayed(text, strokes, got) if nearest and delay is not None else got
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
