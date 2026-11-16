# SPDX-License-Identifier: GPL-2.0-or-later
"""Prompts, coverage and the choice of the next prompt (spec 002 FR-002 to FR-004).

Shipped prompt sets live beside the base model, LANG/prompts/SET.txt (one prompt per line),
with LANG/prompts/SOURCES naming each file's source and licence. The person's own word list
is a file in their data directory; it is never shipped and never donated as a set."""

import functools
import math
import os
from dataclasses import dataclass

# the sets in the order they are offered; "auto" draws from the shipped ones by need
SETS = ("words", "sentences", "pangrams", "letters", "digits", "symbols")
OWN = "own"
AUTO = "auto"
KIND = {"words": "word", "sentences": "sentence", "pangrams": "pangram", "letters": "character",
        "digits": "character", "symbols": "word", OWN: "word"}


@dataclass(frozen=True)
class Prompt:
    text: str
    set: str
    lang: str
    source: str = ""
    licence: str = ""
    written: str = ""                # Your handwriting: careful or quick (spec 003 FR-001)
    pair: str = ""                   # the link a quick and a careful sample share (FR-002)

    @property
    def kind(self):
        if self.set == "digits" and len(self.text) > 1:
            return "word"
        if self.set == "symbols" and len(self.text) == 1:
            return "character"
        return KIND.get(self.set, "word")

    @property
    def shipped(self):
        return self.set != OWN


def models_dir():
    return os.environ.get("GLASSINE_HANDWRITING_MODELS", "/usr/share/glassine/handwriting")


def own_words_path():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "glassine", "handwriting-samples", "words.txt")


def read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [x.rstrip("\r\n") for x in f if x.strip()]
    except OSError:
        return []


def sources(lang, root=None):
    """{file: (licence, source)} from SOURCES: FILE<TAB>LICENCE<TAB>SOURCE lines"""
    out = {}
    for line in read_lines(os.path.join(root or models_dir(), lang, "prompts", "SOURCES")):
        if line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) >= 3:
            out[f[0]] = (f[1], f[2])
    return out


def load(lang, root=None, own_path=None):
    """{set: [Prompt]}: the shipped sets of the language and the person's own words"""
    d = os.path.join(root or models_dir(), lang, "prompts")
    src = sources(lang, root)
    sets = {}
    for name in SETS:
        lic, where = src.get(name + ".txt", ("", ""))
        seen = set()
        items = []
        for t in read_lines(os.path.join(d, name + ".txt")):
            if t not in seen:
                seen.add(t)
                items.append(Prompt(t, name, lang, where, lic))
        if items:
            sets[name] = items
    own = []
    for t in read_lines(own_path or own_words_path()):
        if t not in {p.text for p in own}:
            own.append(Prompt(t, OWN, lang))
    sets[OWN] = own
    return sets


def save_own(words, own_path=None):
    path = own_path or own_words_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for w in words:
            w = " ".join(w.split())
            if w:
                f.write(w + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- coverage


def char_errors(text, read):
    """per character of text: 1 when the reading got it wrong (substituted or left out), else 0
    (an edit-distance alignment)"""
    a, b = text, read or ""
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    wrong = [0] * n
    i, j = n, m
    while i > 0:
        if j > 0 and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            wrong[i - 1] = int(a[i - 1] != b[j - 1])
            i, j = i - 1, j - 1
        elif d[i][j] == d[i - 1][j] + 1:
            wrong[i - 1] = 1
            i -= 1
        else:
            j -= 1
    return wrong


def pairs(text):
    return [text[i:i + 2] for i in range(len(text) - 1) if text[i] != " " and text[i + 1] != " "]


class Coverage:
    """samples and error per character and letter pair, from the person's samples (their
    text and what the recognizer read when they were written)"""

    def __init__(self):
        self.chars = {}         # char -> [samples, wrong]
        self.pairs = {}         # pair -> samples

    def add(self, text, read, k=1):
        """a sample counted in (k=-1: counted out again)"""
        wrong = char_errors(text, read) if read is not None else [0] * len(text)
        for c, w in zip(text, wrong):
            if c == " ":
                continue
            e = self.chars.setdefault(c, [0, 0])
            e[0] += k
            e[1] += k * w
            if e[0] <= 0:
                del self.chars[c]
        for p in pairs(text):
            n = self.pairs.get(p, 0) + k
            if n > 0:
                self.pairs[p] = n
            else:
                self.pairs.pop(p, None)

    def remove(self, text, read):
        self.add(text, read, -1)

    def count(self, c):
        return self.chars.get(c, (0, 0))[0]

    def error(self, c):
        n, w = self.chars.get(c, (0, 0))
        return (w + 0.5) / (n + 2)     # a little prior: an unseen character is half wrong

    def need(self, c):
        return 1 / (1 + self.count(c)) + self.error(c)

    def pair_need(self, p):
        return 1 / (1 + self.pairs.get(p, 0))

    def table(self, charset):
        """[(char, samples, error)] for the coverage view"""
        return [(c, self.count(c), self.chars.get(c, (0, 0))[1] / self.count(c) if self.count(c) else None)
                for c in charset if c != " "]


@functools.lru_cache(maxsize=8192)
def units_of(text):
    """the characters and letter pairs of a text, once each"""
    return tuple(c for c in set(text) if c != " "), tuple(set(pairs(text)))


def score(p, cov, memo=None):
    """how much writing p would teach: its weakest units count most, its length a little;
    memo: {unit: need} shared while scoring many prompts against the same coverage"""
    chars, prs = units_of(p.text)
    if memo is None:
        memo = {}
    units = []
    for c in chars:
        if c not in memo:
            memo[c] = cov.need(c)
        units.append(memo[c])
    for x in prs:
        k = ("pair", x)
        if k not in memo:
            memo[k] = cov.pair_need(x)
        units.append(memo[k])
    if not units:
        return 0.0
    units.sort(reverse=True)
    top = units[:4]
    return sum(top) / len(top) + 0.1 * math.log1p(len(units))


def choose(sets, which, cov, used):
    """the next prompt of a set (or AUTO: the shipped ones), never one shown in this round of
    the set; used: {set: [texts]} (updated: a set worked through starts a new round)"""
    names = [s for s in SETS if s in sets] if which == AUTO else [which]
    pool = []
    for name in names:
        items = sets.get(name) or []
        if not items:
            continue
        seen = set(used.get(name, []))
        fresh = [p for p in items if p.text not in seen]
        if not fresh:                    # worked through: a new round
            used[name] = []
            fresh = list(items)
        pool.extend(fresh)
    if not pool:
        return None
    memo = {}
    best = max(range(len(pool)), key=lambda i: (score(pool[i], cov, memo), -i))
    return pool[best]


def mark_used(used, p):
    used.setdefault(p.set, [])
    if p.text not in used[p.set]:
        used[p.set].append(p.text)
