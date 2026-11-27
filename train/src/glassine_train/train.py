"""Training loop (CTC, AdamW, one-cycle schedule) and evaluation."""

import functools
import json
import time
from pathlib import Path

import torch
from torch import nn

from .data import ALPHABET, BucketSampler, InkSet, collate, decode
from .metrics import rates
from .model import Recognizer, greedy


def loader(ds, batch, shuffle_seed=None, workers=2, down=3):
    alphabet = getattr(ds, "alphabet", None) or ALPHABET
    if shuffle_seed is None:  # evaluation: fixed order, sorted batches, no labels needed
        order = sorted(range(len(ds)), key=ds.length)
        sampler = [order[i:i + batch] for i in range(0, len(order), batch)]
        alphabet = None
    else:
        sampler = BucketSampler(ds, batch, shuffle_seed, down)
        print(f"{sampler.dropped} lines too short for CTC left out", flush=True)
    return torch.utils.data.DataLoader(ds, batch_sampler=sampler,
                                       collate_fn=functools.partial(collate, alphabet=alphabet),
                                       num_workers=workers, persistent_workers=workers > 0)


@torch.no_grad()
def recognise(model, ds, batch=64):
    model.eval()
    refs, hyps = [], []
    for x, lens, _, _, texts in loader(ds, batch, workers=0):
        # a line without ink (a font without the glyph) reads as nothing
        logp, ol = model(x, lens.clamp(min=1))
        hyps += ["" if n == 0 else decode(s, model.alphabet) for s, n in zip(greedy(logp, ol), lens.tolist())]
        refs += texts
    return refs, hyps


def evaluate(model, ds, batch=64):
    refs, hyps = recognise(model, ds, batch)
    return rates(refs, hyps)


def train(data: Path, val: Path, out: Path, minutes: float, cfg: dict, batch: int = 32,
          lr: float = 2e-3, threads: int = 8, eval_every: int = 2000, seed: int = 0,
          val_limit: int = 2000, steps_per_s: float | None = None, augment: bool = False,
          select: Path | list[Path] | None = None, init: Path | None = None, resume: bool = False):
    """Checkpoints every eval_every steps; best.pt is the lowest CER on `select` (fonts never
    trained on; several sets: the mean of their CERs) if given, else on `val`. The model's
    inputs and characters are the training set's."""
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    out.mkdir(parents=True, exist_ok=True)
    ds, vs = InkSet(data, augment=augment), InkSet(val, val_limit)
    sel = [select] if isinstance(select, Path) else list(select or [])
    ss = [InkSet(p, val_limit) for p in sel]
    for d in [vs, *ss]:
        if d.nfeat != ds.nfeat or not set("".join(d.texts)) <= set(ds.alphabet):
            raise SystemExit(f"{d.feat.filename}: inputs or characters differ from the training set's")
    state = None
    if resume:  # the same run after a stop: out/last.pt with its optimizer, schedule and place
        state = torch.load(out / "last.pt", weights_only=False)
        if "resume" not in state:
            raise SystemExit(f"{out / 'last.pt'}: no resume state (written before resuming was possible)")
        init = out / "last.pt"
    if init:  # go on from a checkpoint: its architecture, inputs and characters
        model = load(init)
        if model.c1.in_channels != ds.nfeat or model.alphabet != ds.alphabet:
            raise SystemExit(f"{init}: inputs or characters differ from the training set's")
    else:
        model = Recognizer(**cfg, nfeat=ds.nfeat, alphabet=ds.alphabet)
    nparam = sum(p.numel() for p in model.parameters())
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    ctc = nn.CTCLoss(blank=0, zero_infinity=False)
    # the schedule needs a step count: from a measured rate, else a guess refined after 200 steps
    total = int(minutes * 60 * (steps_per_s or 3.0))
    if state:
        total = state["resume"]["total"]
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=total, pct_start=0.05)
    log = open(out / "log.jsonl", "a")
    print(f"params {nparam}, {len(ds)} lines, planned {total} steps", flush=True)
    t0, step, best, run, spent = time.time(), 0, 9.0, 0.0, 0.0
    dl = loader(ds, batch, seed, down=model.cfg["down"])
    epoch, done = 0, 0  # the sampler's epoch and the batches of it already trained on
    if state:
        r = state["resume"]
        opt.load_state_dict(r["opt"])
        sched.load_state_dict(r["sched"])
        step, best, run, spent, epoch, done = state["step"], r["best"], r["run"], r["spent"], r["epoch"], r["done"]
        print(f"resumed at step {step} (epoch {epoch}, batch {done})", flush=True)
    s0 = step
    deadline = t0 + minutes * 60 - spent
    while step < total:
        dl.batch_sampler.epoch, dl.batch_sampler.skip = epoch, done
        it = iter(dl)  # draws the workers' seed from torch's generator: the saved state comes after it
        if state:
            torch.set_rng_state(state.pop("resume")["torch_rng"])
            state = None
        for x, lens, y, ylens, _ in it:
            done += 1
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
                print(f"step {step}/{total} loss {run:.3f} {(step - s0) / el:.2f} step/s lr {sched.get_last_lr()[0]:.2e}", flush=True)
            if step % eval_every == 0 or step >= total or time.time() > deadline:
                r = evaluate(model, vs)
                if ss:
                    each = {p.name: evaluate(model, d) for p, d in zip(sel, ss)}
                    r["select"] = {"cer": sum(e["cer"] for e in each.values()) / len(each), "sets": each}
                r.update(step=step, loss=run, minutes=(spent + time.time() - t0) / 60)
                print("val", json.dumps(r), flush=True)
                log.write(json.dumps(r) + "\n")
                log.flush()
                ck = {"cfg": model.cfg, "state": model.state_dict(), "step": step, "val": r}
                score = r["select"]["cer"] if ss else r["cer"]
                if score < best:
                    best = score
                    torch.save(ck, out / "best.pt")
                # last.pt also carries what a resumed run needs to go on exactly as this one would
                ck["resume"] = {"opt": opt.state_dict(), "sched": sched.state_dict(), "total": total, "best": best,
                                "run": run, "spent": spent + time.time() - t0, "epoch": epoch, "done": done,
                                "torch_rng": torch.get_rng_state()}
                torch.save(ck, out / "last.pt.tmp")
                (out / "last.pt.tmp").replace(out / "last.pt")
            if step >= total or time.time() > deadline:
                break
        else:
            epoch, done = epoch + 1, 0
            continue
        break
    return best


def load(path: Path) -> Recognizer:
    ck = torch.load(path, weights_only=False)
    m = Recognizer(**ck["cfg"])
    m.load_state_dict(ck["state"])
    m.eval()
    return m
