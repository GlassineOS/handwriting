import numpy as np
import torch

from glassine_train import text
from glassine_train.data import ALPHABET, collate, ctc_min_frames, decode, encode
from glassine_train.features import NFEAT, features, normalise
from glassine_train.metrics import confusions, edit, rates
from glassine_train.model import Recognizer, Single, greedy


def _ink(scale=1.0, tilt=0.0, shift=(0.0, 0.0)):
    # two strokes: a loop and a short bar, x, y, t
    a = np.linspace(0, 2 * np.pi, 60)
    loop = np.c_[0.2 * np.cos(a) + 0.3, 0.2 * np.sin(a) - 0.2, np.linspace(0, 0.3, 60)]
    bar = np.c_[np.linspace(0.6, 1.2, 30), np.full(30, -0.2), np.linspace(0.5, 0.7, 30)]
    out = []
    c, s = np.cos(tilt), np.sin(tilt)
    for q in (loop, bar):
        q = q.copy()
        q[:, :2] *= scale
        q[:, :2] = q[:, :2] @ np.array([[c, -s], [s, c]]).T + shift
        out.append(q)
    return out


def test_features_ignore_scale_and_position():
    f1 = features(_ink())
    f2 = features(_ink(scale=37.0, shift=(500.0, -80.0)))
    assert f1.shape == f2.shape and f1.shape[1] == NFEAT
    assert np.allclose(f1, f2, atol=1e-4)


def test_pen_up_marks_second_stroke_only():
    f = features(_ink())
    assert f[0, 3] == 0 and f[:, 3].sum() == 1
    assert f[f[:, 3] == 1, 4][0] > 0  # the pause before the bar


def test_normalise_removes_small_tilt():
    flat = normalise(_ink())
    tilted = normalise(_ink(tilt=0.1))
    assert abs(np.polyfit(*np.concatenate(tilted)[:, :2].T, 1)[0]
               - np.polyfit(*np.concatenate(flat)[:, :2].T, 1)[0]) < 0.02


def test_single_point_stroke():
    f = features([np.array([[0.0, 0.0, 0.0]]), np.array([[1.0, 0.0, 0.1], [2.0, 0.5, 0.2]])])
    assert np.isfinite(f).all() and len(f) >= 2


def test_codec_and_ctc_length():
    s = "Hello, (world) 42!"
    assert decode(encode(s)) == s
    assert min(encode(s)) >= 1 and max(encode(ALPHABET)) == len(ALPHABET)
    assert ctc_min_frames("ll") == 3 and ctc_min_frames("ab") == 2


def test_metrics():
    assert edit("kitten", "sitting") == 3
    r = rates(["a b", "Cd"], ["a c", "cd"])
    assert r["cer"] == 2 / 5 and r["cer_nocase"] == 1 / 5 and r["wer"] == 2 / 3
    assert ("b>c", 1) in confusions(["a b"], ["a c"])


def test_padding_does_not_change_output_and_export_wrapper_matches():
    torch.manual_seed(0)
    m = Recognizer(conv=32, hidden=16, layers=2, down=3).eval()
    xs = [torch.from_numpy(features(_ink(scale=k))) for k in (1.0, 2.0)]
    xs.append(torch.randn(23, NFEAT))
    x, lens, _, _, _ = collate([(q, "ab") for q in xs])
    with torch.no_grad():
        lp, ol = m(x, lens)
        for i, q in enumerate(xs):
            one = Single(m)(q[None])[0]
            assert one.shape[0] == ol[i]
            assert torch.allclose(one, lp[i, :ol[i]], atol=1e-5)
    assert len(greedy(lp, ol)) == 3


def test_text_rules():
    rng = np.random.default_rng(0)
    lines = text.chunks(["The cat sat on the mat, quietly.", "Café “quotes” — dash."], rng)
    assert lines and all(all(c in text.CHARS for c in ln) and len(ln) <= 40 for ln in lines)
    assert text.clean("“Hi” — there") == "'Hi' - there"
    for ln in text.symbols(50, rng) + text.letters(50, rng) + text.random_words(["ab", "cd"], 20, rng):
        assert ln and all(c in text.CHARS for c in ln)


def test_gutenberg_markers_and_rights():
    raw = "header\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\nbody text\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\nlicence"
    assert text.strip_gutenberg(raw).strip() == "body text"
    ok = '<dcterms:rights>Public domain in the USA.</dcterms:rights><pgterms:deathdate x="y">1870</pgterms:deathdate>'
    assert text.check_rights(ok) == [1870]
    for bad in (ok.replace("1870", "1990"), ok.replace("Public domain in the USA.", "Copyrighted.")):
        try:
            text.check_rights(bad)
        except ValueError:
            continue
        raise AssertionError("accepted " + bad)


def test_augment_is_a_linear_map_of_the_line():
    from glassine_train.data import augment
    f = features(_ink())
    g = augment(f, np.random.default_rng(1))
    assert g.shape == f.shape and (g[:, 3] == f[:, 3]).all()
    # positions rebuilt from the moves keep their order of pen lifts and stay finite
    assert np.isfinite(g).all() and not np.allclose(g[:, :2], f[:, :2])


def test_adapter_head_starts_as_the_model_and_encoder_matches():
    from glassine_train.adapt import Head, encode_lines, read_with_head, train_head
    torch.manual_seed(0)
    m = Recognizer(conv=32, hidden=16, layers=1, down=3).eval()
    x = torch.from_numpy(features(_ink()))
    frames = encode_lines(m, [x])
    with torch.no_grad():
        ref = Single(m)(x[None])[0]
    for rank in (0, 4):
        head = Head(m.out, rank)
        with torch.no_grad():
            assert torch.allclose(head(frames[0][None])[0], ref, atol=1e-5)
    head = Head(m.out, 4)
    before = [p.clone() for p in m.parameters()]
    train_head(head, frames * 4, ["ab"] * 4, epochs=2)
    assert all(torch.equal(a, b) for a, b in zip(before, m.parameters()))  # encoder frozen
    assert len(read_with_head(head, frames)) == 1


def test_reference_model_inputs_and_characters():
    """a model 4 run: six inputs and CHARS4 kept in the checkpoint's cfg, model 2's cfg as before"""
    from glassine_train.features import NFEAT_REF, features_ref
    from glassine_train.lines import CHARS4
    m = Recognizer(conv=32, hidden=16, layers=1, down=3, nfeat=NFEAT_REF, alphabet=CHARS4).eval()
    assert m.cfg["nfeat"] == NFEAT_REF and m.cfg["alphabet"] == CHARS4 and m.out.out_features == len(CHARS4) + 1
    assert "nfeat" not in Recognizer(conv=32, hidden=16, layers=1, down=3).cfg
    again = Recognizer(**m.cfg)
    again.load_state_dict(m.state_dict())
    x = torch.from_numpy(features_ref(_ink(), 0.2, 0.0))
    with torch.no_grad():
        lp, _ = again(x[None], torch.tensor([len(x)]))
    assert lp.shape[-1] == len(CHARS4) + 1
    assert decode(encode("a@b", CHARS4), CHARS4) == "a@b"
    # the old characters keep their classes
    assert all(encode(c, CHARS4) == encode(c) for c in ALPHABET)


def test_augment_moves_the_size_with_the_stretch():
    from glassine_train.data import _extent, augment
    from glassine_train.features import features_ref
    f = features_ref(_ink(), 0.2, 0.0)
    for seed in range(5):
        g = augment(f, np.random.default_rng(seed))
        assert np.allclose(g[:, 5] - f[:, 5], np.log(_extent(g) / _extent(f)), atol=1e-6)
        assert np.ptp(g[:, 5]) < 1e-6  # still the same in every row


def test_training_goes_on_from_a_checkpoint(tmp_path):
    """--init: the run starts from the checkpoint's weights and keeps its architecture"""
    from glassine_train.data import build
    from glassine_train.train import load, train
    torch.manual_seed(0)
    m = Recognizer(conv=32, hidden=16, layers=1, down=3)
    torch.save({"cfg": m.cfg, "state": m.state_dict(), "step": 0, "val": {}}, tmp_path / "init.pt")
    data = tmp_path / "data"
    import json
    data.mkdir()
    xs = [features(_ink(scale=1 + 0.1 * i)) for i in range(8)]
    off = np.cumsum([0] + [len(x) for x in xs])
    np.concatenate(xs).astype(np.float16).tofile(data / "feat.f16")
    np.save(data / "offsets.npy", off)
    (data / "texts.json").write_text(json.dumps(["ab"] * 8))
    train(data, data, tmp_path / "run", 0.05, {"conv": 999, "hidden": 999, "layers": 9, "dropout": 0.0, "down": 3},
          batch=4, threads=1, eval_every=1, steps_per_s=1.0, init=tmp_path / "init.pt")
    got = load(tmp_path / "run" / "last.pt")
    assert got.cfg == m.cfg


def _tiny_set(path, texts):
    import json
    path.mkdir()
    xs = [features(_ink(scale=1 + 0.1 * i)) for i in range(len(texts))]
    np.concatenate(xs).astype(np.float16).tofile(path / "feat.f16")
    np.save(path / "offsets.npy", np.cumsum([0] + [len(x) for x in xs]))
    (path / "texts.json").write_text(json.dumps(texts))
    return path


def test_a_resumed_run_matches_an_uninterrupted_one(tmp_path, monkeypatch):
    """--resume: a run stopped after a checkpoint goes on to the same weights as one never stopped
    (optimizer, LR schedule, place in the epoch and dropout's random state come back)"""
    import pytest
    from glassine_train import train as T
    data = _tiny_set(tmp_path / "data", ["ab", "ba", "a", "b", "abab", "bab", "aab", "bb"])
    cfg = {"conv": 16, "hidden": 8, "layers": 2, "dropout": 0.3, "down": 3}
    # 7 steps of 2 batches an epoch: the checkpoint after step 3 falls in the middle of epoch 1
    kw = dict(batch=4, lr=1e-2, threads=1, eval_every=3, steps_per_s=0.01)
    minutes = 7 / 60 / 0.01
    T.train(data, data, tmp_path / "whole", minutes, cfg, **kw)

    class Stop(Exception):
        pass
    save = torch.save

    def crash(obj, path):  # the process ends right after writing its first checkpoint
        save(obj, path)
        if str(path).endswith("last.pt.tmp"):
            raise Stop
    monkeypatch.setattr(T.torch, "save", crash)
    with pytest.raises(Stop):
        T.train(data, data, tmp_path / "cut", minutes, cfg, **kw)
    monkeypatch.setattr(T.torch, "save", save)
    (tmp_path / "cut" / "last.pt.tmp").replace(tmp_path / "cut" / "last.pt")
    assert torch.load(tmp_path / "cut" / "last.pt", weights_only=False)["step"] == 3
    T.train(data, data, tmp_path / "cut", minutes, cfg, resume=True, **kw)
    a = torch.load(tmp_path / "whole" / "last.pt", weights_only=False)
    b = torch.load(tmp_path / "cut" / "last.pt", weights_only=False)
    assert a["step"] == b["step"] == 7
    for k in a["state"]:
        assert torch.equal(a["state"][k], b["state"][k]), k
    assert a["resume"]["sched"]["last_epoch"] == b["resume"]["sched"]["last_epoch"] == 7


def test_resume_needs_a_resume_state(tmp_path):
    """a last.pt from before resuming existed: a clear stop, not a silent fresh schedule"""
    import pytest
    from glassine_train.train import train
    data = _tiny_set(tmp_path / "data", ["ab"] * 4)
    m = Recognizer(conv=16, hidden=8, layers=1, down=3)
    (tmp_path / "run").mkdir()
    torch.save({"cfg": m.cfg, "state": m.state_dict(), "step": 2, "val": {}}, tmp_path / "run" / "last.pt")
    with pytest.raises(SystemExit):
        train(data, data, tmp_path / "run", 1.0, m.cfg, batch=2, threads=1, resume=True)


def test_device_choice_and_a_missing_gpu_stop_cleanly(tmp_path):
    """--device: cpu by default, auto falls back to the CPU, a GPU asked for and not there (or a
    device name the trainer doesn't know) stops with a message before any data is read"""
    import pytest
    from glassine_train.train import pick_device, train
    assert pick_device().type == "cpu" and pick_device("cpu").type == "cpu"
    if not torch.cuda.is_available():
        assert pick_device("auto").type == "cpu"
        for name in ("cuda", "cuda:0"):
            with pytest.raises(SystemExit, match="no usable CUDA GPU"):
                train(tmp_path / "missing", tmp_path / "missing", tmp_path / "run", 1.0, {}, device=name)
    with pytest.raises((SystemExit, RuntimeError)):
        pick_device("tpu")
    assert not (tmp_path / "run").exists()


def test_cpu_runs_are_exact_with_the_default_device_and_workers(tmp_path):
    """the CPU default stays bit for bit: the same seed gives the same weights and optimizer
    state with the options left out or given as their defaults; checkpoints hold CPU tensors
    (the loader workers' count is part of a run's random stream: the same count, the same run)"""
    from glassine_train import train as T
    data = _tiny_set(tmp_path / "data", ["ab", "ba", "a", "b", "abab", "bab", "aab", "bb"])
    cfg = {"conv": 16, "hidden": 8, "layers": 2, "dropout": 0.3, "down": 3}
    kw = dict(batch=4, lr=1e-2, threads=1, eval_every=3, steps_per_s=0.01)
    minutes = 7 / 60 / 0.01
    T.train(data, data, tmp_path / "a", minutes, cfg, **kw)
    T.train(data, data, tmp_path / "b", minutes, cfg, device="cpu", workers=2, **kw)
    a = torch.load(tmp_path / "a" / "last.pt", weights_only=False)
    b = torch.load(tmp_path / "b" / "last.pt", weights_only=False)
    assert a["step"] == b["step"] == 7
    for k in a["state"]:
        assert a["state"][k].device.type == "cpu"
        assert torch.equal(a["state"][k], b["state"][k]), k
    for i, s in a["resume"]["opt"]["state"].items():
        for name, v in s.items():
            assert torch.equal(v, b["resume"]["opt"]["state"][i][name]), (i, name)
    assert "cuda_rng" not in a["resume"]


def test_cpu_copy_of_nested_state():
    from glassine_train.train import _cpu
    t = torch.arange(3.0)
    got = _cpu({"a": [t, (t, 1)], "b": "x"})
    assert torch.equal(got["a"][0], t) and got["a"][1][1] == 1 and got["b"] == "x"
