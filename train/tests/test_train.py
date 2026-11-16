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
