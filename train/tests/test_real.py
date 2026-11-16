"""Real-ink handling on made-up samples: no person's samples are used in tests."""

import json

import numpy as np
import torch

from glassine_train.finetune import DomainHead, finetune
from glassine_train.hand import covered, shapes, tables
from glassine_train.model import Recognizer
from glassine_train.real import RealSet, fnv1a64, ink_augment, load_split, prepare, read_store, split


def _sample(text, rng, y0=250.0):
    """A made-up prompted word in device units: one stroke per character, on a guide."""
    strokes, spans, t = [], [], 0.0
    x = 100.0
    for i, c in enumerate(text):
        tall = c in "bdfhklt"
        n = 30
        y = np.linspace(y0 - (40 if tall else 15), y0, n) + rng.normal(0, 0.3, n)
        xs = x + np.linspace(0, 8, n)
        ts = t + np.arange(n) * 0.003
        strokes.append(np.c_[xs, y, ts, np.full(n, 0.5)].round(3).tolist())
        spans.append([i])
        x += 14
        t = ts[-1] + 0.1
    return {"version": 1, "text": text, "lang": "en", "source": "prompted", "spans_source": "exact",
            "units": {"xy": "device", "per_mm": 4.5, "rate": 333}, "strokes": strokes, "spans": spans,
            "area": {"box": [0, 0, 700, 474], "line": 150, "orientation": 0}}


def _store(tmp, times, texts):
    rng = np.random.default_rng(0)
    lines = [json.dumps(_sample(t, rng)) for t in texts]
    (tmp / "samples.jsonl").write_text("".join(ln + "\n" for ln in lines))
    meta = "".join("%016x\tapp\t%d\tprompted\ten\n" % (fnv1a64(ln.encode()), tm) for ln, tm in zip(lines, times))
    (tmp / "samples.meta").write_text("glassine-handwriting-meta 1 2\n" + meta)


def test_split_by_session_and_blocks(tmp_path):
    # session 0: 100 samples, a 15 min pause after the 60th; session 1: 30 samples
    times = [i * 20 for i in range(60)] + [60 * 20 + 900 + i * 20 for i in range(40)]
    times += [10_000 + i * 20 for i in range(30)]
    texts = ["word%d" % i for i in range(130)]
    _store(tmp_path, times, texts)
    recs = read_store(tmp_path)
    sp = split(recs)
    assert len(sp["test"]) == 30
    by = {r["id"]: r["time"] for r in recs}
    assert min(by[i] for i in sp["test"]) > max(by[i] for i in sp["train"] + sp["dev"])
    assert not set(sp["train"]) & set(sp["dev"]) and not set(sp["train"]) & set(sp["test"])
    assert len(sp["train"]) + len(sp["dev"]) + len(sp["test"]) == 130
    # the block after the pause (40) is below DEV_MIN and no earlier pause: the last 80
    assert len(sp["dev"]) == 80


def test_prepare_writes_an_adaptation_store(tmp_path):
    times = [i * 10 for i in range(100)] + [5000 + i * 10 for i in range(20)]
    _store(tmp_path, times, ["ab"] * 60 + ["cd"] * 60)
    rep = prepare(tmp_path, tmp_path / "out")
    sp = load_split(tmp_path / "out")
    assert rep["test"] == 20 and len(sp["test"]) == 20
    st = (tmp_path / "out" / "store-trainsessions" / "samples.meta").read_text().splitlines()
    assert st[0] == "glassine-handwriting-meta 1 2" and len(st) == 1 + rep["train"] + rep["dev"]


def test_ink_augment_keeps_order_and_time():
    rng = np.random.default_rng(1)
    s = _sample("hello", rng)
    st = [np.asarray(q)[:, :3] for q in s["strokes"]]
    for k in range(20):
        out = ink_augment(st, np.random.default_rng(k), dropout=0.0)
        assert len(out) == len(st)
        t = np.concatenate([q[:, 2] for q in out])
        assert np.all(np.diff(t) >= 0) and t[0] == 0
    ds = RealSet([s], augment=True)
    x, text = ds[0]
    assert text == "hello" and x.shape[1] == 5 and torch.isfinite(x).all()


def test_hand_glyphs_from_whole_strokes():
    rng = np.random.default_rng(2)
    sh, rep = shapes([_sample("hello", rng), _sample("lab", rng), _sample("e", rng)])
    assert set(sh) == set("heloab") and rep["shapes"] == 9
    g = sh["l"][0]
    y = np.concatenate(g.strokes)[:, 1]
    assert y.max() < 0.1 and y.min() < -0.45  # on the baseline, taller than the x-height
    assert covered("hole be", sh) and not covered("help", sh)
    assert len(tables(sh, 3)) == 3


def test_finetune_moves_little_and_writes_best(tmp_path):
    torch.manual_seed(0)
    m = Recognizer(conv=16, hidden=8, down=3)
    torch.save({"cfg": m.cfg, "state": m.state_dict(), "step": 0}, tmp_path / "init.pt")
    rng = np.random.default_rng(3)
    data = [_sample(w, rng) for w in ("hello", "bed", "hole", "lob")] * 3
    real = RealSet(data, augment=True)
    res = finetune(tmp_path / "init.pt", tmp_path / "run", [{"name": "real", "ds": real, "share": 1.0, "real": True}],
                   RealSet(data[:4]), None, steps=6, eval_every=3, batch=4, threads=1, workers=0, adv=0.1,
                   min_steps=0)
    assert (tmp_path / "run" / "best.pt").exists() and res["steps"] == 6 and res["kept"] in ("avg", "single")
    h = torch.randn(2, 5, 16, requires_grad=True)
    DomainHead(16)(h, 1.0).sum().backward()
    assert h.grad is not None


def test_kept_test_set_stays_fixed(tmp_path):
    # sessions: 0 (50), 1 (40, the test session), 2 (30), 3 (60)
    starts = [0, 10_000, 20_000, 30_000]
    sizes = [50, 40, 30, 60]
    times = [s + i * 20 for s, n in zip(starts, sizes) for i in range(n)]
    texts = ["word%d" % i for i in range(len(times))]
    _store(tmp_path, times[:90], texts[:90])  # the first pull ends with the test session
    first = split(read_store(tmp_path))
    assert len(first["test"]) == 40
    _store(tmp_path, times, texts)  # a later pull with two more sessions
    recs = read_store(tmp_path)
    sp = split(recs, first["test"])
    assert sp["test"] == first["test"] and sp["left_out"] == []
    by = {r["id"]: r["time"] for r in recs}
    # dev = whole newest sessions until DEV_MIN (session 3 alone has 60 < 80: sessions 3 and 2)
    assert sorted(by[i] // 10_000 for i in sp["dev"]) == [2] * 30 + [3] * 60
    assert sorted({by[i] // 10_000 for i in sp["train"]}) == [0]
    rep = prepare(tmp_path, tmp_path / "out", first["test"])
    assert rep["test"] == 40 and rep["kept_test"] and rep["left_out"] == 0


def test_kept_test_set_leaves_out_its_sessions_and_needs_every_id(tmp_path):
    times = [i * 20 for i in range(50)] + [10_000 + i * 20 for i in range(30)] + [20_000 + i for i in range(5)]
    _store(tmp_path, times, ["w%d" % i for i in range(85)])
    recs = read_store(tmp_path)
    test = [r["id"] for r in recs if 10_000 <= r["time"] < 10_000 + 20 * 20]  # 20 of the 30
    sp = split(recs, test)
    assert len(sp["left_out"]) == 10 and not set(sp["left_out"]) & set(sp["train"] + sp["dev"])
    try:
        split(recs, test + ["0123456789abcdef"])
    except SystemExit as e:
        assert "not in the store" in str(e)
    else:
        raise AssertionError("a missing kept test id must fail")
