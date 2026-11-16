"""Tables for model 4 against model 2 from the recognizer program's readings (compare.py):
`python -m glassine_train.report4 EVAL_DIR MODEL_A MODEL_B` prints markdown tables. Synthetic
ink only; aggregate numbers only."""

import sys
from pathlib import Path

from . import compare as C

MODES = [("ink", "ink only"), ("chars", "character model"), ("words", "words"), ("wordschars", "words + character model")]
SETS = [("test", "test book (500 lines)"), ("test_words", "random words (500)"), ("test_symbols", "numbers, punctuation (500)"),
        ("test_letters", "random letters (500)")]
KINDS = ["words", "name", "pseudo", "flat", "uniform", "lone", "codes", *C.CODES]


def pct(x):
    return "–" if x is None else f"{100 * x:.1f}"


def sets_of(prefix):
    return [f"{prefix}-{g}" for g in C.GROUPS]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ev, models = Path(argv[0]), argv[1:]
    ref = "xh"
    for ref in ("xh", "line"):
        print(f"\n### Reference: {'letter height known' if ref == 'xh' else 'line alone'}\n")
        print("| set | " + " | ".join(f"{m} {short}" for m in models for _, short in MODES) + " |")
        print("|---" * (1 + len(models) * len(MODES)) + "|")
        for s, name in SETS:
            row = [pct(C.cer(C.pooled(ev / m, sets_of(s), f"{t}-{ref}"))) for m in models for t, _ in MODES]
            print(f"| {name} | " + " | ".join(row) + " |")
        print("\nLine kinds (test_kinds, three held-out groups pooled, CER %):\n")
        print("| kind | " + " | ".join(f"{m} {short}" for m in models for _, short in MODES) + " |")
        print("|---" * (1 + len(models) * len(MODES)) + "|")
        for k in KINDS:
            row = [pct(C.cer(C.pooled(ev / m, sets_of("test_kinds"), f"{t}-{ref}", k))) for m in models for t, _ in MODES]
            print(f"| {k} | " + " | ".join(row) + " |")
        print("\nLone characters at their size (test_lone, exact top reading %, other case %):\n")
        for t, short in MODES:
            print(f"- {short}: " + "; ".join(
                f"{m}: " + " ".join(f"`{c}` {100 * v['exact']:.0f}/{100 * v['other_case']:.0f}"
                                    for c, v in C.lone_table(C.pooled(ev / m, sets_of("test_lone"), f"{t}-{ref}"),
                                                             ".,oOcCsS").items())
                for m in models))
        print("\nLetters read as digits inside words (texts without digits: test book, words, names, pseudo-words):\n")
        for t, short in MODES:
            out = []
            for m in models:
                rs = C.pooled(ev / m, sets_of("test") + sets_of("test_words"), f"{t}-{ref}")
                rs += [r for k in ("name", "pseudo", "words") for r in C.pooled(ev / m, sets_of("test_kinds"), f"{t}-{ref}", k)]
                b, n = C.digit_in_word(rs)
                out.append(f"{m} {b}/{n}")
            print(f"- {short}: " + ", ".join(out))
        if len(models) == 2:
            print("\nPaired bootstrap, CER of the first minus the second (95 %):\n")
            for t, short in MODES:
                parts = []
                for s, name in SETS + [("test_kinds", "line kinds"), ("test_lone", "lone")]:
                    a = C.pooled(ev / models[0], sets_of(s), f"{t}-{ref}")
                    b = C.pooled(ev / models[1], sets_of(s), f"{t}-{ref}")
                    r = C.boot(a, b)
                    if r:
                        parts.append(f"{name} {100 * r['diff_lo']:+.1f} to {100 * r['diff_hi']:+.1f}")
                print(f"- {short}: " + "; ".join(parts))


if __name__ == "__main__":
    main()
