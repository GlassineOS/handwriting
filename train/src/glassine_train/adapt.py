"""Per-writer adaptation (spec FR-009): a frozen encoder plus a small trainable head.

The encoder (convolutions + LSTM) runs once per confirmed sample; its frames are cached.
Adaptation then trains only the head on the cached frames: the output layer, optionally
with a residual bottleneck adapter in front of it that starts as the identity.
"""

import time

import numpy as np
import torch
from torch import nn

from .data import encode
from .model import Recognizer, greedy


class Head(nn.Module):
    """Adapter (2H -> r -> 2H, residual, zero-initialised: starts as the identity) + output."""

    def __init__(self, out: nn.Linear, rank: int = 0):
        super().__init__()
        self.out = nn.Linear(out.in_features, out.out_features)
        self.out.load_state_dict(out.state_dict())
        self.rank = rank
        if rank:
            self.down = nn.Linear(out.in_features, rank)
            self.up = nn.Linear(rank, out.in_features)
            nn.init.zeros_(self.up.weight)
            nn.init.zeros_(self.up.bias)

    def forward(self, h):
        if self.rank:
            h = h + self.up(torch.relu(self.down(h)))
        return self.out(h).log_softmax(-1)


@torch.no_grad()
def encode_lines(m: Recognizer, xs: list[torch.Tensor]) -> list[torch.Tensor]:
    """Encoder frames (T', 2H) per line, one line at a time (as on the device)."""
    m.eval()
    out = []
    for x in xs:
        h = m.act(m.c1((x[None] * m.scale).transpose(1, 2)))
        for c in m.strided:
            h = m.act(c(h))
        h, _ = m.lstm(h.transpose(1, 2))
        out.append(h[0])
    return out


def train_head(head: Head, frames: list[torch.Tensor], texts: list[str], epochs: int = 5,
               lr: float = 1e-3, batch: int = 8, seed: int = 0, wd: float = 0.0) -> float:
    """Train the head with CTC on cached frames; returns seconds spent."""
    torch.manual_seed(seed)
    ctc = nn.CTCLoss(blank=0, zero_infinity=True)
    opt = torch.optim.Adam(head.parameters(), lr=lr, weight_decay=wd)
    rng = np.random.default_rng(seed)
    t = time.perf_counter()
    head.train()
    for _ in range(epochs):
        for k in range(0, len(frames), batch):
            sel = list(range(k, min(k + batch, len(frames))))
            hs = [frames[i] for i in sel]
            lens = torch.tensor([len(h) for h in hs])
            h = nn.utils.rnn.pad_sequence(hs, batch_first=True)
            ys = [torch.tensor(encode(texts[i])) for i in sel]
            loss = ctc(head(h).transpose(0, 1), torch.cat(ys), lens, torch.tensor([len(y) for y in ys]))
            opt.zero_grad()
            loss.backward()
            opt.step()
        perm = rng.permutation(len(frames))
        frames = [frames[i] for i in perm]
        texts = [texts[i] for i in perm]
    head.eval()
    return time.perf_counter() - t


@torch.no_grad()
def read_with_head(head: Head, frames: list[torch.Tensor]) -> list[list[int]]:
    out = []
    for h in frames:
        lp = head(h[None])
        out += greedy(lp, torch.tensor([len(h)]))
    return out
