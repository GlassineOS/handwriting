"""Fine-tuning a trained recognizer on real ink mixed with synthetic ink.

Every batch has a fixed make-up: `share` of each source (real ink oversampled: drawn with
replacement, a new augmented drawing each time). The loss is CTC plus

- a penalty toward the starting weights (L2-SP: l2sp / 2 · Σ (θ − θ₀)²), so a few hundred
  real samples move the model only as far as they must;
- optionally a domain head (gradient reversal): per encoder frame a small classifier tells
  real from synthetic ink; its gradient is reversed into the encoder, so the encoder's
  frames carry less of what tells the two apart (Ganin and Lempitsky 2015). The head is
  only for training; it is not part of the exported model.

The learning rate warms up over 5 % of the steps and falls on a cosine. The model is read
on the real dev set every `eval_every` steps; training stops after `patience` reads without
a better one. The kept model is the average of the `avg` best checkpoints by dev error
when that reads dev at least as well as the single best one, else the single best.
"""

import json
import math
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .data import InkSet, collate, ctc_min_frames, decode
from .metrics import rates
from .model import Recognizer, greedy
from .train import load


class _Reverse(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, lam):
        ctx.lam = lam
        return x.view_as(x)

    @staticmethod
    def backward(ctx, g):
        return -ctx.lam * g, None


class DomainHead(nn.Module):
    def __init__(self, dim: int, hidden: int = 64):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(dim, hidden), nn.GELU(), nn.Linear(hidden, 1))

    def forward(self, h, lam):
        return self.net(_Reverse.apply(h, lam)).squeeze(-1)


def encode(m: Recognizer, x, lens):
    """Encoder frames (B, T', 2H) and their lengths: Recognizer.forward without the output."""
    h = m._mask((x * m.scale).transpose(1, 2), lens)
    h = m._mask(m.act(m.c1(h)), lens)
    l3 = lens
    for c in m.strided:
        l3 = (l3 - 1) // 2 + 1
        h = m._mask(m.act(c(h)), l3)
    h = h.transpose(1, 2)
    packed = nn.utils.rnn.pack_padded_sequence(h, l3.cpu(), batch_first=True, enforce_sorted=False)
    h, _ = m.lstm(packed)
    h, _ = nn.utils.rnn.pad_packed_sequence(h, batch_first=True, total_length=int(l3.max()))
    return h, l3


def feasible(ds, down: int = 3) -> np.ndarray:
    """Indices of lines long enough for CTC after downsampling."""
    out = []
    for i in range(len(ds)):
        f = ds.length(i)
        for _ in range(down):
            f = (f - 1) // 2 + 1
        if f >= ctc_min_frames(ds.texts[i]):
            out.append(i)
    return np.array(out)


class Mixed(torch.utils.data.Dataset):
    """Several datasets behind one index space: (source, index) packed as one integer."""

    def __init__(self, sets):
        self.sets = sets

    def __len__(self):
        return sum(len(s) for s in self.sets)

    def __getitem__(self, k):
        src, i = divmod(k, 1 << 40)
        x, t = self.sets[src][i]
        return x, t, src


class MixedBatches(torch.utils.data.Sampler):
    """Endless batches: counts[s] lines of source s each (with replacement)."""

    def __init__(self, pools, counts, seed):
        self.pools, self.counts, self.seed = pools, counts, seed

    def __iter__(self):
        rng = np.random.default_rng(self.seed)
        while True:
            b = []
            for s, (pool, n) in enumerate(zip(self.pools, self.counts)):
                b += [(s << 40) + int(i) for i in rng.choice(pool, n)]
            yield b


def _collate(batch):
    x, lens, y, ylens, texts = collate([(a, b) for a, b, _ in batch])
    return x, lens, y, ylens, texts, torch.tensor([s for _, _, s in batch])


@torch.no_grad()
def read(m, ds, batch=64):
    m.eval()
    order = sorted(range(len(ds)), key=ds.length)
    refs, hyps = [], []
    for k in range(0, len(order), batch):
        items = [ds[i] for i in order[k:k + batch]]
        x, lens, _, _, texts = collate(items)
        lp, ol = m(x, lens)
        hyps += [decode(s) for s in greedy(lp, ol)]
        refs += texts
    return rates(refs, hyps)


def finetune(init: Path, out: Path, sources: list[dict], dev: torch.utils.data.Dataset,
             synth_dev: InkSet | None, steps: int, lr: float = 3e-4, batch: int = 32,
             threads: int = 6, l2sp: float = 0.01, adv: float = 0.0, eval_every: int = 100,
             patience: int = 8, avg: int = 5, seed: int = 0, workers: int = 3,
             min_steps: int = 300) -> dict:
    """sources: [{name, ds, share, real}] (shares sum to 1). Writes best.pt, log.jsonl,
    result.json under out/."""
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    out.mkdir(parents=True, exist_ok=True)
    m = load(init)
    ref = {k: v.detach().clone() for k, v in m.state_dict().items()}
    dom = DomainHead(m.out.in_features) if adv > 0 else None
    params = list(m.parameters()) + (list(dom.parameters()) if dom else [])
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=0.0)
    warm = max(1, int(0.05 * steps))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * (0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * min(s, steps) / steps))))
    ctc = nn.CTCLoss(blank=0, zero_infinity=True)
    shares = np.array([s["share"] for s in sources], float)
    counts = np.floor(shares / shares.sum() * batch).astype(int)
    counts[np.argmax(shares)] += batch - counts.sum()
    pools = [feasible(s["ds"]) if not s["real"] else np.arange(len(s["ds"])) for s in sources]
    is_real = torch.tensor([bool(s["real"]) for s in sources])
    dl = torch.utils.data.DataLoader(Mixed([s["ds"] for s in sources]),
                                     batch_sampler=MixedBatches(pools, counts.tolist(), seed),
                                     collate_fn=_collate, num_workers=workers,
                                     persistent_workers=workers > 0, prefetch_factor=4 if workers else None)
    log = open(out / "log.jsonl", "w")
    first = read(m, dev)
    first.update(step=0)
    if synth_dev is not None:
        first["synth_dev"] = read(m, synth_dev)
    log.write(json.dumps(first) + "\n")
    print("step 0", json.dumps(first), flush=True)
    top = []  # (dev cer, step, state)
    best, since, step, run, t0 = 9.0, 0, 0, 0.0, time.time()
    for x, lens, y, ylens, _, src in dl:
        m.train()
        if dom is not None:
            h, ol = encode(m, x, lens)
            logp = m.out(m.drop(h)).log_softmax(-1)
            p = step / steps
            lam = adv * (2 / (1 + math.exp(-10 * p)) - 1)
            d = dom(h, lam)
            mask = (torch.arange(d.shape[1])[None] < ol[:, None]).float()
            tgt = is_real[src].float()[:, None].expand_as(d)
            dl_ = (nn.functional.binary_cross_entropy_with_logits(d, tgt, reduction="none") * mask).sum() / mask.sum()
        else:
            logp, ol = m(x, lens)
            dl_ = torch.zeros(())
        loss = ctc(logp.transpose(0, 1), y, ol, ylens)
        pen = sum(((p_ - ref[n]) ** 2).sum() for n, p_ in m.named_parameters())
        total = loss + 0.5 * l2sp * pen + dl_
        opt.zero_grad()
        total.backward()
        nn.utils.clip_grad_norm_(params, 5.0)
        opt.step()
        sched.step()
        step += 1
        run = 0.95 * run + 0.05 * loss.item() if step > 1 else loss.item()
        if step % eval_every == 0 or step >= steps:
            r = read(m, dev)
            r.update(step=step, loss=run, l2sp=float(pen.detach()), domain=float(dl_.detach()),
                     minutes=(time.time() - t0) / 60)
            if synth_dev is not None:
                r["synth_dev"] = read(m, synth_dev)
            log.write(json.dumps(r) + "\n")
            log.flush()
            print(f"step {step} dev cer {r['cer']:.4f} synth {r.get('synth_dev', {}).get('cer', 0):.4f} "
                  f"loss {run:.3f} dom {float(dl_.detach()):.3f} {step / (time.time() - t0):.2f} step/s", flush=True)
            st = {k: v.detach().clone() for k, v in m.state_dict().items()}
            top.append((r["cer"], step, st))
            top = sorted(top, key=lambda z: (z[0], -z[1]))[:avg]
            if r["cer"] < best - 1e-9:
                best, since = r["cer"], 0
            else:
                since += 1
            if (since >= patience and step >= min_steps) or step >= steps:
                break
    # the kept model: the average of the best checkpoints, or the single best
    cfg = m.cfg
    single = top[0]
    avg_state = {k: sum(z[2][k].float() for z in top) / len(top) for k in single[2]}
    m.load_state_dict(avg_state)
    r_avg = read(m, dev)
    choice = "avg" if r_avg["cer"] <= single[0] + 1e-9 else "single"
    state = avg_state if choice == "avg" else single[2]
    m.load_state_dict(state)
    res = {"steps": step, "best_step": single[1], "best_dev_cer": single[0], "avg_dev_cer": r_avg["cer"],
           "avg_steps": [z[1] for z in top], "kept": choice, "dev": read(m, dev),
           "start_dev": {k: first[k] for k in ("cer", "wer")}, "counts": dict(zip([s["name"] for s in sources], counts.tolist())),
           "minutes": (time.time() - t0) / 60}
    if synth_dev is not None:
        res["synth_dev"] = read(m, synth_dev)
        res["start_synth_dev"] = first["synth_dev"]["cer"]
    torch.save({"cfg": cfg, "state": state, "step": step, "val": res}, out / "best.pt")
    (out / "result.json").write_text(json.dumps(res, indent=1))
    return res
