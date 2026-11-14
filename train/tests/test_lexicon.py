import math

import numpy as np

from glassine_train import lexicon
from glassine_train.data import ALPHABET, encode
from glassine_train.model import greedy


def _logp(path, sharp=6.0):
    """CTC output that spells `path` (a list of characters or "" for blank), one per frame."""
    rows = np.full((len(path), len(ALPHABET) + 1), -sharp)
    for t, c in enumerate(path):
        rows[t, 0 if c == "" else encode(c)[0]] = 0.0
    return rows - np.log(np.exp(rows).sum(1, keepdims=True))


def _lex(tmp_path, words, text, order=3):
    src = tmp_path / "lx"
    src.mkdir()
    (src / "words.txt").write_text("\n".join(words) + "\n")
    import collections
    ng = collections.Counter()
    for ln in text:
        s = lexicon.START * (order - 1) + ln
        for k in range(order - 1, len(s)):
            for h in {lexicon._collapse(s[k - n:k]) for n in range(order)}:
                ng[(h, s[k])] += 1
    with open(src / "charlm.txt", "w") as f:
        f.write(f"order {order}\n")
        for (h, c), n in sorted(ng.items()):
            f.write(f"{lexicon._esc(h)}\t{lexicon._esc(c)}\t{n}\n")
    return lexicon.Lexicon(src)


def test_beam_without_lexicon_matches_greedy():
    lp = _logp(["h", "", "e", "l", "", "l", "o", "o", ""])
    best = lexicon.decode(lp, ALPHABET, None, beam=8)[0][0]
    import torch
    g = "".join(ALPHABET[k - 1] for k in greedy(torch.from_numpy(lp)[None], torch.tensor([len(lp)]))[0])
    assert best == g == "hello"


def test_lexicon_fixes_an_ambiguous_letter(tmp_path):
    lex = _lex(tmp_path, ["the", "cat"], ["the cat", "the cat sat"] * 20)
    lp = _logp(["t", "h", "c", "", " ", "c", "a", "t"])
    e, c = encode("e")[0], encode("c")[0]
    lp[2, e], lp[2, c] = math.log(0.4), math.log(0.6)  # the model leans to "thc"
    assert lexicon.decode(lp, ALPHABET, None)[0][0] == "thc cat"
    kw = {"lm_weight": 0.5, "word_bonus": 1.0, "oov_penalty": 1.0}
    assert lexicon.decode(lp, ALPHABET, lex, **kw)[0][0] == "the cat"


def test_char_model_is_a_distribution(tmp_path):
    lex = _lex(tmp_path, ["ab"], ["ab ab", "ba"])
    lm = lex.lm
    for text in ("", "a", "ab ", "zz"):
        h = lm.context(text)
        assert abs(sum(math.exp(lm.logp(h, c)) for c in ALPHABET) - 1.0) < 1e-3 + lm.uniform
    assert lexicon._unesc(lexicon._esc("a \tb\\")) == "a \tb\\"
