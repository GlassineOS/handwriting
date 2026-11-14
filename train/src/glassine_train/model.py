"""Recognizer: 1-D convolutions (2**down x downsampling) + bidirectional LSTM + CTC over characters."""

import torch
from torch import nn

from .data import ALPHABET
from .features import NFEAT


class Recognizer(nn.Module):
    def __init__(self, conv: int = 128, hidden: int = 128, layers: int = 2, dropout: float = 0.1,
                 down: int = 2):
        super().__init__()
        self.cfg = {"conv": conv, "hidden": hidden, "layers": layers, "dropout": dropout, "down": down}
        # dx, dy are about one resampling step (0.1); bring them to unit scale like the rest
        self.register_buffer("scale", torch.tensor([10.0, 10.0, 1.0, 1.0, 1.0]))
        self.c1 = nn.Conv1d(NFEAT, conv // 2, 5, padding=2)
        self.strided = nn.ModuleList(
            [nn.Conv1d(conv // 2 if i == 0 else conv, conv, 5, stride=2, padding=2) for i in range(down)])
        self.act = nn.GELU()
        self.lstm = nn.LSTM(conv, hidden, layers, batch_first=True, bidirectional=True,
                            dropout=dropout if layers > 1 else 0.0)
        self.drop = nn.Dropout(dropout)
        self.out = nn.Linear(2 * hidden, len(ALPHABET) + 1)

    def out_lengths(self, lens: torch.Tensor) -> torch.Tensor:
        for _ in self.strided:
            lens = (lens - 1) // 2 + 1
        return lens

    def _mask(self, h, lens):
        t = torch.arange(h.shape[2], device=h.device)
        return h * (t[None, :] < lens[:, None]).unsqueeze(1).to(h.dtype)

    def forward(self, x: torch.Tensor, lens: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """x (B, T, NFEAT), lens (B,) -> log-probs (B, T', C), lens (B,). Padding has no effect."""
        h = self._mask((x * self.scale).transpose(1, 2), lens)
        h = self._mask(self.act(self.c1(h)), lens)
        l3 = lens
        for c in self.strided:
            l3 = (l3 - 1) // 2 + 1
            h = self._mask(self.act(c(h)), l3)
        h = h.transpose(1, 2)
        packed = nn.utils.rnn.pack_padded_sequence(h, l3.cpu(), batch_first=True, enforce_sorted=False)
        h, _ = self.lstm(packed)
        h, _ = nn.utils.rnn.pad_packed_sequence(h, batch_first=True, total_length=int(l3.max()))
        return self.out(self.drop(h)).log_softmax(-1), l3



class Single(nn.Module):
    """Export wrapper: one line, no padding, plain LSTM call (packing does not export)."""

    def __init__(self, m: Recognizer):
        super().__init__()
        self.m = m

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # x (1, T, NFEAT) -> (1, T', C)
        m = self.m
        h = m.act(m.c1((x * m.scale).transpose(1, 2)))
        for c in m.strided:
            h = m.act(c(h))
        h, _ = m.lstm(h.transpose(1, 2))
        return m.out(h).log_softmax(-1)


def greedy(logp: torch.Tensor, lens: torch.Tensor) -> list[list[int]]:
    best = logp.argmax(-1)
    out = []
    for b in range(best.shape[0]):
        seq, prev = [], 0
        for k in best[b, :int(lens[b])].tolist():
            if k != prev and k != 0:
                seq.append(k)
            prev = k
        out.append(seq)
    return out
