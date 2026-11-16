"""Model-4 training text (lines.py) on a made-up character model and made-up names: no book,
no name list."""

import zlib

import numpy as np
import pytest

from glassine_train import charlm, lines


def _toy_model(tmp_path):
    txt = ["the cat sat on the mat", "a dog ran in the fog", "then they went on and on"] * 30
    data = charlm.encode(charlm.prune(charlm.count(txt, 4), 2), "abcdefghijklmnopqrstuvwxyz' -", 4)
    f = tmp_path / "toy.gclm"
    f.write_bytes(data)
    return f


def test_pseudo_words_are_not_words(tmp_path):
    reject = {"the", "cat", "sat", "on", "mat", "a", "dog", "ran", "in", "fog", "then", "they", "went", "and"}
    p = lines.Pseudo(_toy_model(tmp_path), reject)
    rng = np.random.default_rng(0)
    ws = [p.word(rng) for _ in range(200)] + [p.word(rng, lines.FLAT_T) for _ in range(200)]
    assert all(w and w.lower() not in reject and w[0].isalpha() for w in ws)
    d = p.dist(" th")
    assert d.sum() == pytest.approx(1.0) and d[p.m.sym["e"]] > d[p.m.sym["q"]]


def test_codes_and_names_in_the_alphabet(tmp_path):
    p = lines.Pseudo(_toy_model(tmp_path), set())
    names = {"given": [("Mary", 2.0), ("Ann", 1.0), ("Jo", 1.0)] * 5,
             "surname": [("Smith", 3.0), ("Okafor", 1.0), ("Lee", 1.0)] * 5}
    nm = lines.Names({k: [(n + str(i), w) for i, (n, w) in enumerate(v)] for k, v in names.items()}, held=False)
    cd = lines.Codes(["word", "other"], p, nm)
    rng = np.random.default_rng(1)
    for k in lines.CODE_KINDS:
        for _ in range(50):
            s = cd.make(k, rng)
            assert s and len(s) <= 40 and all(c in lines.CHARS4 for c in s), (k, s)
    assert any("@" in cd.make("email", rng) for _ in range(10))
    assert abs(sum(lines.SHARES.values()) - 1) < 1e-9
    assert lines.CHARS4.startswith(lines.text.CHARS)
    held = [n for n in ("Mary", "Smith", "Okafor", "Lee", "Ann", "Jo") if lines.name_held(n)]
    assert all(zlib.crc32(n.encode()) % lines.NAME_HELD_MOD == 0 for n in held)


