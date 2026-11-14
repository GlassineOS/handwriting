"""Training loop (CTC, AdamW, one-cycle schedule) and evaluation."""

import json
import time
from pathlib import Path

import torch
from torch import nn

from .data import BucketSampler, InkSet, collate, decode
from .metrics import rates
from .model import Recognizer, greedy


def loader(ds, batch, shuffle_seed=None, workers=2, down=3):
    if shuffle_seed is None:  # evaluation: fixed order, sorted batches
        order = sorted(range(len(ds)), key=ds.length)
        sampler = [order[i:i + batch] for i in range(0, len(order), batch)]
    else:
        sampler = BucketSampler(ds, batch, shuffle_seed, down)
        print(f"{sampler.dropped} lines too short for CTC left out", flush=True)
    return torch.utils.data.DataLoader(ds, batch_sampler=sampler, collate_fn=collate,
                                       num_workers=workers, persistent_workers=workers > 0)


@torch.no_grad()
def recognise(model, ds, batch=64):
    model.eval()
    refs, hyps = [], []
    for x, lens, _, _, texts in loader(ds, batch, workers=0):
        logp, ol = model(x, lens)
        hyps += [decode(s) for s in greedy(logp, ol)]
        refs += texts
    return refs, hyps


def evaluate(model, ds, batch=64):
    refs, hyps = recognise(model, ds, batch)
    return rates(refs, hyps)


def train(data: Path, val: Path, out: Path, minutes: float, cfg: dict, batch: int = 32,
          lr: float = 2e-3, threads: int = 8, eval_every: int = 2000, seed: int = 0,
          val_limit: int = 2000, steps_per_s: float | None = None, augment: bool = False):
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    out.mkdir(parents=True, exist_ok=True)
    ds, vs = InkSet(data, augment=augment), InkSet(val, val_limit)
    model = Recognizer(**cfg)
    nparam = sum(p.numel() for p in model.parameters())
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    ctc = nn.CTCLoss(blank=0, zero_infinity=False)
    # the schedule needs a step count: from a measured rate, else a guess refined after 200 steps
    total = int(minutes * 60 * (steps_per_s or 3.0))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=total, pct_start=0.05)
    log = open(out / "log.jsonl", "a")
    print(f"params {nparam}, {len(ds)} lines, planned {total} steps", flush=True)
    t0, step, best, run = time.time(), 0, 9.0, 0.0
    deadline = t0 + minutes * 60
    dl = loader(ds, batch, seed, down=model.cfg["down"])
    while step < total:
        for x, lens, y, ylens, _ in dl:
            model.train()
            logp, ol = model(x, lens)
            loss = ctc(logp.transpose(0, 1), y, ol, ylens)
            if not torch.isfinite(loss):
                raise RuntimeError(f"step {step}: loss {loss.item()} (a line too short for CTC?)")
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            sched.step()
            step += 1
            run = 0.98 * run + 0.02 * loss.item() if step > 1 else loss.item()
            if step % 100 == 0:
                el = time.time() - t0
                print(f"step {step}/{total} loss {run:.3f} {step / el:.2f} step/s lr {sched.get_last_lr()[0]:.2e}", flush=True)
            if step % eval_every == 0 or step >= total or time.time() > deadline:
                r = evaluate(model, vs)
                r.update(step=step, loss=run, minutes=(time.time() - t0) / 60)
                print("val", json.dumps(r), flush=True)
                log.write(json.dumps(r) + "\n")
                log.flush()
                ck = {"cfg": model.cfg, "state": model.state_dict(), "step": step, "val": r}
                torch.save(ck, out / "last.pt")
                if r["cer"] < best:
                    best = r["cer"]
                    torch.save(ck, out / "best.pt")
            if step >= total or time.time() > deadline:
                break
        else:
            continue
        break
    return best


def load(path: Path) -> Recognizer:
    ck = torch.load(path, weights_only=False)
    m = Recognizer(**ck["cfg"])
    m.load_state_dict(ck["state"])
    m.eval()
    return m
