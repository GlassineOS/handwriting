"""Lexicon decoding: CTC prefix beam search with a word list and a character n-gram model.

A reference implementation in plain Python (no PyTorch): the recognizer program can port it
as it is. Input is the model's output for one line, log-probabilities of shape (frames, 1 + K)
with class 0 = CTC blank and class k = ALPHABET[k - 1]. The model itself is not changed.

Score of a hypothesis (all terms in natural log):

    log P_ctc(text | ink)
    + lm_weight   * sum over characters of log P_lm(char | previous ORDER-1 characters)
    + char_bonus  * number of characters
    + word_bonus  * number of finished words found in the word list
    - oov_penalty * number of finished words not in it (letters only; numbers and
                    punctuation pass without either)

A word is a maximal run of letters with apostrophes inside it ("don't"); it is finished when a
character that is not a letter or apostrophe follows, or at the end of the line. Word lookup
ignores case. The character model sees the line as written (case kept), starting after a
line-start marker, so "after a space" and "at the start" are different contexts.

Character model: interpolated Witten-Bell over counts of character n-grams,

    P(c | h) = (C(h c) + T(h) * P(c | h')) / (C(h) + T(h)),   h' = h without its first char,

down to a uniform distribution over the alphabet plus one; C(h) = sum of C(h c) kept, T(h) =
number of distinct c kept after h. Only counts are stored, so the model is a table of
(context, next character, count).

Files (`build`): `words.txt` (one lower-case word per line, most frequent first) and
`charlm.txt` (first line `order N`; then `context<TAB>char<TAB>count`, the context escaped as
in `_esc`, `^` = line start). Both are derived from the sources in `SOURCES`; their licence is
written next to them as `LICENSE.txt`.
"""

import bz2
import collections
import hashlib
import math
import re
import urllib.request
from pathlib import Path

from .text import CHARS, clean

# The same pinned sources as the word predictor's English model (GlassineOS/shell
# tools/predict-model/en.sources): kind, file, sha256, url.
SOURCES = [
    ("wikipedia", "simplewiki.parquet",
     "31bded16768a47c286becd292079122f5d7d4397a17b87d4250a00ccd581e6f0",
     "https://huggingface.co/datasets/wikimedia/wikipedia/resolve/"
     "b04c8d1ceb2f5cd4588862100d08de323dccfbaa/20231101.simple/train-00000-of-00001.parquet"),
    ("tatoeba", "eng_sentences.tsv.bz2",
     "07f33d399f0a55b917202abbd901f23c39c54a6dd562252985ce8c7e168db6ef",
     "https://web.archive.org/web/20261105045929id_/"
     "https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences.tsv.bz2"),
]
TATOEBA_DROP = {"Tom", "Mary"}  # stock names in nearly every English Tatoeba sentence
LICENSE = """The word list and character counts in this directory are derived from the Simple
English Wikipedia (text from the wikimedia/wikipedia dataset, CC BY-SA 3.0 and GFDL, by the
Wikipedia contributors, https://simple.wikipedia.org) and from English sentences of the
Tatoeba project (CC BY 2.0 FR, by the Tatoeba contributors, https://tatoeba.org). They are
licensed CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0/). Built with
GlassineOS/handwriting train/src/glassine_train/lexicon.py from its SOURCES.
"""

WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*")
START = "^"  # line-start marker in character-model contexts (not in CHARS)


def _is_letter(c: str) -> bool:
    return c.isascii() and c.isalpha()


# ---------------------------------------------------------------------------- sources

def fetch(dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    for _, file, sha, url in SOURCES:
        f = dest / file
        if not f.exists() or hashlib.sha256(f.read_bytes()).hexdigest() != sha:
            print(f"fetching {file}", flush=True)
            req = urllib.request.Request(url, headers={"User-Agent": "glassine-train"})
            with urllib.request.urlopen(req, timeout=600) as r:
                f.write_bytes(r.read())
            if hashlib.sha256(f.read_bytes()).hexdigest() != sha:
                raise SystemExit(f"{file}: checksum differs from SOURCES (upstream changed?)")


def _normalise(line: str) -> str:
    """The recognizer's character set only: other characters become spaces."""
    line = clean(line)
    line = "".join(c if c in CHARS else " " for c in line)
    return re.sub(r" +", " ", line).strip()


def lines(dest: Path):
    """Running text, one paragraph or sentence per line, in the recognizer's characters.
    The same selection as the predictor's extract.py (paragraphs of 4 words or more, no
    headings, lists or table rows; Tatoeba without the stock names)."""
    import pyarrow.parquet as pq
    for kind, file, _, _ in SOURCES:
        if kind == "wikipedia":
            pf = pq.ParquetFile(dest / file)
            for b in pf.iter_batches(columns=["text"], batch_size=2000):
                for text in b.column(0).to_pylist():
                    for ln in text.split("\n"):
                        ln = ln.strip()
                        if len(ln.split()) < 4 or ln.startswith(("|", "{", "!", "*")):
                            continue
                        ln = _normalise(ln)
                        if ln:
                            yield ln
        else:
            for row in bz2.open(dest / file, "rt", encoding="utf-8"):
                parts = row.rstrip("\n").split("\t")
                if len(parts) < 3 or not parts[2].strip():
                    continue
                words = set(w.strip(".,!?;:'\"()") for w in parts[2].split())
                words |= set(w.split("'")[0] for w in words)
                if TATOEBA_DROP & words:
                    continue
                ln = _normalise(parts[2])
                if ln:
                    yield ln


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace(" ", "\\s")


def _unesc(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"t": "\t", "s": " ", "\\": "\\"}[m.group(1)], s)


def build(dest: Path, out: Path, words: int = 50_000, order: int = 6, min_count: int = 3,
          max_lines: int | None = None):
    """Count words and character n-grams over the sources; write words.txt, charlm.txt."""
    out.mkdir(parents=True, exist_ok=True)
    wc = collections.Counter()
    ng = collections.Counter()
    for i, ln in enumerate(lines(dest)):
        if max_lines and i >= max_lines:
            break
        wc.update(w.lower() for w in WORD.findall(ln))
        s = START * (order - 1) + ln
        for k in range(order - 1, len(s)):
            seen = set()
            for n in range(order):
                h = _collapse(s[k - n:k])
                if h not in seen:
                    seen.add(h)
                    ng[(h, s[k])] += 1
        if i % 200_000 == 0:
            print(f"  {i} lines, {len(ng)} n-grams", flush=True)
    kept = {(h, c): n for (h, c), n in ng.items() if n >= min_count or not h}
    top = [w for w, _ in wc.most_common(words)]
    (out / "words.txt").write_text("\n".join(top) + "\n")
    with open(out / "charlm.txt", "w") as f:
        f.write(f"order {order}\n")
        for (h, c), n in sorted(kept.items()):
            f.write(f"{_esc(h)}\t{_esc(c)}\t{n}\n")
    (out / "LICENSE.txt").write_text(LICENSE)
    print(f"{out}: {len(top)} words, {len(kept)} n-grams (order {order}, count >= {min_count})")


def prune(src: Path, out: Path, min_count: int, words: int | None = None, order: int | None = None):
    """A smaller lexicon from a built one: n-grams seen fewer than min_count times dropped
    (always keeping the empty context), optionally fewer words and a lower order."""
    out.mkdir(parents=True, exist_ok=True)
    ws = (src / "words.txt").read_text().split()
    (out / "words.txt").write_text("\n".join(ws[:words] if words else ws) + "\n")
    rows = open(src / "charlm.txt").read().split("\n")
    o = int(rows[0].split()[1])
    order = min(order or o, o)
    kept = 0
    with open(out / "charlm.txt", "w") as f:
        f.write(f"order {order}\n")
        for r in rows[1:]:
            if not r:
                continue
            h, c, n = r.split("\t")
            hl = len(_unesc(h))
            if hl >= order or (h and int(n) < min_count):
                continue
            f.write(r + "\n")
            kept += 1
    (out / "LICENSE.txt").write_text((src / "LICENSE.txt").read_text())
    print(f"{out}: {kept} n-grams, order {order}, count >= {min_count}")


def _collapse(h: str) -> str:
    """'^^^ab' -> '^ab': the context knows the line started, not how long ago."""
    k = len(h) - len(h.lstrip(START))
    return (START if k else "") + h[k:]


# ---------------------------------------------------------------------------- model

class CharLM:
    def __init__(self, path: Path, alphabet: str = CHARS):
        rows = open(path).read().split("\n")
        self.order = int(rows[0].split()[1])
        self.alphabet = alphabet
        self.next: dict[str, dict[str, int]] = collections.defaultdict(dict)
        for r in rows[1:]:
            if not r:
                continue
            h, c, n = r.split("\t")
            self.next[_unesc(h)][_unesc(c)] = int(n)
        self.tot = {h: sum(d.values()) for h, d in self.next.items()}
        self.uniform = 1.0 / (len(alphabet) + 1)
        self._cache: dict[str, dict[str, float]] = {}

    def context(self, text: str) -> str:
        h = (START + text)[-(self.order - 1):] if self.order > 1 else ""
        return _collapse(h)

    def _p(self, h: str, c: str) -> float:
        lower = self._p(h[1:], c) if h else self.uniform
        d = self.next.get(h)
        if not d:
            return lower
        t = len(d)
        return (d.get(c, 0) + t * lower) / (self.tot[h] + t)

    def logp(self, h: str, c: str) -> float:
        """log P(c | context h); h from context()."""
        d = self._cache.get(h)
        if d is None:
            d = self._cache[h] = {}
        v = d.get(c)
        if v is None:
            v = d[c] = math.log(self._p(h, c))
        return v


class Lexicon:
    def __init__(self, path: Path, words: int | None = None):
        ws = (path / "words.txt").read_text().split()
        self.words = set(ws[:words] if words else ws)
        self.lm = CharLM(path / "charlm.txt")


# ---------------------------------------------------------------------------- decoder

def _lse(a: float, b: float) -> float:
    if a == -math.inf:
        return b
    if b == -math.inf:
        return a
    m = max(a, b)
    return m + math.log(math.exp(a - m) + math.exp(b - m))


def decode(logp, alphabet: str, lex: Lexicon | None, beam: int = 16, lm_weight: float = 0.5,
           char_bonus: float = 0.0, word_bonus: float = 0.0, oov_penalty: float = 0.0,
           prune: float = 10.0, nbest: int = 1):
    """CTC prefix beam search. logp: (frames, 1 + K) natural-log probabilities (a numpy array
    or nested lists). Returns the best `nbest` (text, score) pairs, best first.

    Per frame only classes within `prune` of the frame's best are extended. Each beam entry
    keeps the CTC log-probability of its prefix ending in blank (pb) and in its last
    character (pnb), and a separate language score (lm) that does not depend on alignment."""
    rows = logp.tolist() if hasattr(logp, "tolist") else logp
    NEG = -math.inf
    # prefix -> [pb, pnb, lm]
    beams = {"": [0.0, NEG, 0.0]}

    def lang(prefix: str, c: str) -> float:
        """Language score of appending c to prefix (no CTC)."""
        if lex is None:
            return char_bonus
        s = lm_weight * lex.lm.logp(lex.lm.context(prefix), c) + char_bonus
        if not (_is_letter(c) or c == "'"):
            s += _finish(prefix)
        return s

    def _finish(prefix: str) -> float:
        m = re.search(r"[A-Za-z']*$", prefix)
        w = m.group(0).strip("'") if m else ""
        if not w or not any(_is_letter(x) for x in w):
            return 0.0
        return word_bonus if w.lower() in lex.words else -oov_penalty

    for row in rows:
        top = max(row)
        cand = [k for k, v in enumerate(row) if v >= top - prune]
        nxt: dict[str, list[float]] = {}

        def add(prefix, pb, pnb, lm):
            e = nxt.get(prefix)
            if e is None:
                nxt[prefix] = [pb, pnb, lm]
            else:
                e[0] = _lse(e[0], pb)
                e[1] = _lse(e[1], pnb)

        for prefix, (pb, pnb, lm) in beams.items():
            tot = _lse(pb, pnb)
            last = prefix[-1] if prefix else None
            for k in cand:
                p = row[k]
                if k == 0:
                    add(prefix, tot + p, NEG, lm)
                    continue
                c = alphabet[k - 1]
                if c == last:
                    add(prefix, NEG, pnb + p, lm)  # repeat collapses
                    if pb > NEG:  # blank in between: a new, doubled character
                        add(prefix + c, NEG, pb + p, lm + lang(prefix, c))
                else:
                    add(prefix + c, NEG, tot + p, lm + lang(prefix, c))
        beams = dict(sorted(nxt.items(), key=lambda kv: -(_lse(kv[1][0], kv[1][1]) + kv[1][2]))[:beam])

    final = []
    for prefix, (pb, pnb, lm) in beams.items():
        s = _lse(pb, pnb) + lm
        if lex is not None and prefix:
            s += _finish(prefix)  # the last word ends with the line
        final.append((prefix, s))
    final.sort(key=lambda x: -x[1])
    return final[:nbest]
