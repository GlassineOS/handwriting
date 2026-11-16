import math

from glassine_train import charlm
from glassine_train.text import CHARS

LINES = ["the cat sat on the mat", "a hat on the cat", "the 4th of May at 10:30"]


def test_same_text_same_bytes_and_normalised():
    a = charlm.encode(charlm.prune(charlm.count(LINES, 4), 1), CHARS, 4)
    b = charlm.encode(charlm.prune(charlm.count(list(LINES), 4), 1), CHARS, 4)
    assert a == b
    m = charlm.Model(a)
    for ctx in (" ", "the c", "4", "zzz"):
        assert abs(sum(math.exp(m.logp(ctx, c)) for c in CHARS) - 1) < 1e-9


def test_context_helps_and_pruning_backs_off():
    m = charlm.Model(charlm.encode(charlm.prune(charlm.count(LINES * 5, 5), 1), CHARS, 5))
    assert m.logp("the c", "a") > m.logp("the c", "4")
    p = charlm.Model(charlm.encode(charlm.prune(charlm.count(LINES, 5), 1000), CHARS, 5))
    assert len(p.es) - 1 == 1  # only the empty context is left


def test_codes_deterministic_and_in_charset():
    c = charlm.codes(500, seed=3)
    assert c == charlm.codes(500, seed=3)
    assert set("".join(c)) <= set(CHARS)
    assert any(x.endswith("th") and x[0].isdigit() for x in c)
