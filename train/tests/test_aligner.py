"""The point-labelling aligner's loss and decoding on tiny made-up cases (no ink of anyone)."""

import itertools

import numpy as np
import torch

from glassine_train import aligner as A


def _paths(labels, T):
    """Every frame labelling the chain allows, by brute force: no blank first, last or twice
    in a row; a blank only between two frames of one class; the runs (a blank ends one) spell
    the labels."""
    out = []
    for seq in itertools.product(range(A.NCLASS), repeat=T):
        if seq[0] == A.BLANK or seq[-1] == A.BLANK:
            continue
        if any(c == A.BLANK and not (seq[i - 1] == seq[i + 1] != A.BLANK) for i, c in enumerate(seq[1:-1], 1)):
            continue
        runs = [c for i, c in enumerate(seq) if c != A.BLANK and (i == 0 or seq[i - 1] != c)]
        if runs == list(labels):
            out.append(seq)
    return out


def test_chain_loss_is_the_sum_over_allowed_paths(monkeypatch):
    monkeypatch.setattr(A, "NCLASS", 3)
    monkeypatch.setattr(A, "BLANK", 2)
    rng = np.random.default_rng(0)
    for labels, T in (([0, 1], 4), ([0, 0], 4), ([1, 0, 0], 5), ([0], 3), ([1, 1, 1], 6)):
        lp = torch.log_softmax(torch.from_numpy(rng.normal(0, 1, (1, T, 3))), -1)
        got = A.chain_loss(lp, torch.tensor([T]), [labels]).item()
        paths = _paths(labels, T)
        assert paths, labels
        tot = torch.logsumexp(torch.tensor([sum(lp[0, t, c].item() for t, c in enumerate(p)) for p in paths]), 0)
        assert abs(got + tot.item()) < 1e-6, (labels, got, -tot.item())
        best = max(paths, key=lambda p: sum(lp[0, t, c].item() for t, c in enumerate(p)))
        own = A.viterbi(lp[0].numpy(), labels)
        cls, _ = A.chain(labels)
        assert [labels[o] if o >= 0 else A.BLANK for o in own] == list(best)


def test_chain_loss_batches_lines_of_other_lengths(monkeypatch):
    monkeypatch.setattr(A, "NCLASS", 3)
    monkeypatch.setattr(A, "BLANK", 2)
    rng = np.random.default_rng(1)
    lp = torch.log_softmax(torch.from_numpy(rng.normal(0, 1, (2, 6, 3))), -1)
    both = A.chain_loss(lp, torch.tensor([6, 4]), [[0, 1], [1, 1]]).item()
    a = A.chain_loss(lp[:1], torch.tensor([6]), [[0, 1]]).item()
    b = A.chain_loss(lp[1:, :4], torch.tensor([4]), [[1, 1]]).item()
    assert abs(both - a - b) < 1e-6
    assert A.viterbi(lp[0, :2].numpy(), [0, 0]) is None  # two frames can't hold l, blank, l


def test_featurize_maps_every_point_to_a_frame():
    t = np.linspace(0, 1, 40)
    s1 = np.c_[t, np.sin(6 * t) * 0.3, t]
    s2 = np.c_[t + 1.2, np.cos(6 * t) * 0.3, t + 1.5]
    x, maps, firsts = A.featurize([s1, np.zeros((0, 3)), s2])
    assert x.shape[1] == A.NFEAT and len(maps[1]) == 0
    assert len(maps[0]) == 40 and len(maps[2]) == 40
    assert maps[0].min() == 0 and maps[2].min() == firsts[2] and maps[2].max() == len(x) - 1
    assert np.all(np.diff(maps[0]) >= 0) and maps[0].max() < firsts[2]
    lab = A.frame_truth(maps, [np.r_[np.zeros(20, int), np.ones(20, int)], np.zeros(0, int), np.full(40, 3)], len(x))
    assert A.target_of(lab) == [0, 1, 3]


def test_label_points_by_spans_keeps_each_strokes_letters():
    torch.manual_seed(0)
    m = A.Aligner(hidden=8, layers=1).eval()
    t = np.linspace(0, 1, 60)
    s1 = np.c_[t * 2, np.sin(9 * t) * 0.3, t]
    s2 = np.c_[t * 0.3 + 2.5, t * 0 - 0.2, t + 2]
    out = A.label_points(m, [s1, s2], "ab c", [[0], [0], [], [1]])
    assert set(out[0]) == {0, 1} and set(out[1]) == {3}
    assert list(dict.fromkeys(out[0])) == [0, 1]  # in order, one run each
    free = A.label_points(m, [s1, s2], "ab c")
    assert all(c in (0, 1, 3) for o in free for c in o)


def test_cut_errors_and_span_agreement():
    from glassine_train import alignrun as R
    q = np.c_[np.arange(10.0), np.zeros(10), np.arange(10.0)]
    truth = np.array([0, 0, 0, -1, -1, 1, 1, 1, 1, 1])  # the join runs from point 2 to point 5
    assert R.joins_of(truth) == [(2, 5, 0, 1)]
    assert R.cut_errors(q, truth, np.array([0, 0, 0, 0, 1, 1, 1, 1, 1, 1]), 1.0) == [0.0]
    assert R.cut_errors(q, truth, np.array([0] * 8 + [1, 1]), 1.0) == [2.5]  # cut at 7.5
    assert R.cut_errors(q, truth, np.zeros(10, int), 1.0) == [None]
    s = R.summarise([0.0, 2.5, None])
    assert s["joins"] == 3 and abs(s["letter_lost"] - 1 / 3) < 1e-9 and s["within_join"] == 0.5
    lab = R.points_from_align(["0:0-4", "0:5-9,1", "", "2"], [q, q[:3], q[:2]])
    assert lab[0].tolist() == [0] * 5 + [1] * 5 and lab[1].tolist() == [1, 1, 1] and lab[2].tolist() == [3, 3]
    smp = {"text": "ab c", "spans": [[0], [0, 1], [], [2]], "spans_source": "exact"}
    good = [np.array([0, 0, 1, 1]), np.array([1, 1]), np.array([3])]
    bad = [np.array([0, 0, 0, 0]), np.array([1, 1]), np.array([3])]
    m = R.measure_spans([smp], {"good": [good], "bad": [bad]})
    assert m["good"]["agree"] == 1.0 and m["good"]["shared_strokes"] == 1
    assert m["bad"]["agree_shared"] == 0.0 and m["bad"]["letters_lost_shared"] == 0.5
