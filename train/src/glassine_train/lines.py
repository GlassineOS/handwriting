"""Training text for the next base model (spec 001 FR-020): line kinds with the language's
real character statistics.

Every line has a kind; `corpus()` mixes them in the shares of `SHARES`:

- `prose`: the training books' sentences cut into lines of 1 to 8 words (text.chunks);
- `words`: words of the training books' vocabulary in random order (text.random_words);
- `pseudo`: pseudo-words drawn letter by letter from the character model (charlm.py, built
  from the training books only), never a word of the books' vocabulary or of the word
  list, so no word is learned by heart but letter sequences are the language's;
- `flat`: pseudo-words drawn at a higher temperature (FLAT_T): the character model's
  probabilities flattened, so the ink model does not learn the language's letter statistics
  as a second character model;
- `name`: given names and surnames from public name lists (NAME_SOURCES, pinned by sha256),
  written as names are (Mary, M. Smith, Smith, Mary, Dr Smith);
- code kinds, each its own: `ordinal` (ordinals and measures: 4th, 5 km, 250 g, 10%),
  `time`, `date`, `phone`, `postcode`, `plate`, `part` (part and model numbers: R2D2,
  X-200, ISO 9001), `email`, `web`, `number` (numbers and prices); a third of them inside a
  few words of the books, as codes are written in a sentence;
- `uniform`: a small remainder of letter and digit strings, every character equally likely;
- `lone`: one character alone (letters, digits, punctuation) or a short run of marks, for
  ink at its true size (FR-018).

The test book (text.TEST) is never used: neither its sentences nor its words. Names are
split by a hash: one in NAME_HELD_MOD is held out for the test sets.

The alphabet CHARS4 is the trainer's CHARS with the characters email and web addresses
and codes need appended (so class numbers of the old characters stay).
"""

import hashlib
import io
import math
import re
import urllib.request
import zipfile
import zlib
from pathlib import Path

import numpy as np

from . import text
from .charlm import Model

CHARS4 = text.CHARS + "@/&+#%_=$"
FLAT_T = 2.0  # temperature of the flattened pseudo-words
NAME_HELD_MOD = 10

# Shares of the training lines by kind (they sum to 1); the code kinds share CODES equally.
CODE_KINDS = ["ordinal", "time", "date", "phone", "postcode", "plate", "part", "email", "web", "number"]
CODES = 0.17
SHARES = {"prose": 0.38, "words": 0.13, "pseudo": 0.10, "flat": 0.06, "name": 0.07,
          "uniform": 0.03, "lone": 0.06} | {k: CODES / len(CODE_KINDS) for k in CODE_KINDS}

# Name lists: US federal government works (17 U.S.C. 105: not subject to copyright, public
# domain in the USA, like the books), U.S. Census Bureau.
NAME_SOURCES = [
    {"file": "census1990.female.first", "kind": "given",
     "url": "https://www2.census.gov/topics/genealogy/1990surnames/dist.female.first",
     "sha256": "bd2f310fc4e5d5e5ea122c9d4342c9821145823118eb20db1647f305ec77b358",
     "licence": "public domain (U.S. Census Bureau, a U.S. federal government work, 17 U.S.C. 105)"},
    {"file": "census1990.male.first", "kind": "given",
     "url": "https://www2.census.gov/topics/genealogy/1990surnames/dist.male.first",
     "sha256": "0a5078ef6effe3b483d15b0f7f95047662126c9bfb624ecd5e5b978fc0f2470b",
     "licence": "public domain (U.S. Census Bureau, a U.S. federal government work, 17 U.S.C. 105)"},
    {"file": "census2010.surnames.zip", "kind": "surname",
     "url": "https://www2.census.gov/topics/genealogy/2010surnames/names.zip",
     "sha256": "117c41cb4668727b7627b2845b6df3f83eb2a22a1813f42c0ff4bdcab86de135",
     "licence": "public domain (U.S. Census Bureau, a U.S. federal government work, 17 U.S.C. 105)"},
]
SURNAME_MIN_COUNT = 1000  # surnames borne by at least this many people (2010 census)


# ---------------------------------------------------------------------------- names

def fetch_names(dest: Path) -> list[dict]:
    """The name lists in dest/, checked against their pins."""
    dest.mkdir(parents=True, exist_ok=True)
    for s in NAME_SOURCES:
        f = dest / s["file"]
        if not f.exists():
            req = urllib.request.Request(s["url"], headers={"User-Agent": "glassine-train"})
            with urllib.request.urlopen(req, timeout=120) as r:
                f.write_bytes(r.read())
        if hashlib.sha256(f.read_bytes()).hexdigest() != s["sha256"]:
            raise SystemExit(f"{f}: sha256 differs from lines.NAME_SOURCES")
    return NAME_SOURCES


def _title(n: str) -> str:
    n = n.strip().lower()
    for pre in ("mc", "o'"):
        if n.startswith(pre) and len(n) > len(pre) + 1:
            return pre.capitalize() + n[len(pre)].upper() + n[len(pre) + 1:]
    return "-".join(p.capitalize() for p in n.split("-"))


def load_names(dest: Path) -> dict[str, list[tuple[str, float]]]:
    """{given, surname}: (name, weight), weight the square root of how common the name is, so
    rare names get their share and common ones still come first."""
    out = {"given": {}, "surname": {}}
    for s in NAME_SOURCES:
        raw = (dest / s["file"]).read_bytes()
        if s["file"].endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                csv = z.read("Names_2010Census.csv").decode("utf-8", "replace")
            for ln in csv.splitlines()[1:]:
                f = ln.split(",")
                if len(f) < 3 or not f[0].isalpha() or not f[2].isdigit() or int(f[2]) < SURNAME_MIN_COUNT:
                    continue
                out["surname"][_title(f[0])] = math.sqrt(int(f[2]))
        else:
            for ln in raw.decode().splitlines():
                f = ln.split()
                if len(f) >= 2 and f[0].isalpha():
                    w = math.sqrt(float(f[1]) + 1e-4)
                    n = _title(f[0])
                    out["given"][n] = max(w, out["given"].get(n, 0))
    return {k: sorted(v.items()) for k, v in out.items()}


def name_held(n: str) -> bool:
    return zlib.crc32(n.encode()) % NAME_HELD_MOD == 0


class Names:
    def __init__(self, names: dict, held: bool):
        self.lists = {}
        for k, v in names.items():
            v = [(n, w) for n, w in v if name_held(n) == held]
            w = np.array([x for _, x in v])
            self.lists[k] = ([n for n, _ in v], w / w.sum())

    def pick(self, kind: str, rng) -> str:
        ns, p = self.lists[kind]
        return ns[int(rng.choice(len(ns), p=p))]

    def line(self, rng) -> str:
        g = lambda: self.pick("given", rng)
        s = lambda: self.pick("surname", rng)
        k = rng.random()
        if k < 0.2:
            out = g()
        elif k < 0.35:
            out = s()
        elif k < 0.7:
            out = f"{g()} {s()}"
        elif k < 0.78:
            out = f"{g()[0]}. {s()}"
        elif k < 0.84:
            out = f"{s()}, {g()}"
        elif k < 0.88:
            out = f"{g()} {g()[0]}. {s()}"
        elif k < 0.92:
            out = f"{rng.choice(['Dr', 'Mr', 'Mrs', 'Ms', 'Dr.', 'Mr.'])} {s()}"
        elif k < 0.96:
            out = f"{s()}-{s()}"
        else:
            out = f"{g()} and {g()}"
        if rng.random() < 0.04:
            out = out.upper()
        return out[:40].strip()


# ---------------------------------------------------------------------------- pseudo-words

LETTERS = "abcdefghijklmnopqrstuvwxyz"


class Pseudo:
    """Pseudo-words drawn from the character model: letters (and inner ' or -) after the
    word's letters so far, until it draws a space; at temperature T the probabilities are
    raised to 1/T and renormalised. Words of `reject` (the books' and the word list's) are
    drawn again."""

    def __init__(self, gclm: Path, reject: set[str]):
        self.m = Model(Path(gclm).read_bytes())
        self.reject = reject
        self.allowed = [c for c in self.m.charset if c in LETTERS or c in "'- "]
        self.idx = np.array([self.m.sym[c] for c in self.allowed])
        self.space = self.allowed.index(" ")
        self.cache: dict[str, np.ndarray] = {}

    def dist(self, ctx: str) -> np.ndarray:
        """P(next character | ctx) over the whole character set, as charlm.Model.logp."""
        m = self.m
        ctx = ctx[-(m.order - 1):]
        hit = self.cache.get(ctx)
        if hit is not None:
            return hit
        path, node = [0], 0
        for x in reversed(ctx):
            if x not in m.sym:
                break
            node = m._child(node, m.sym[x])
            if node < 0:
                break
            path.append(node)
        p = np.full(m.V, 1.0 / m.V)
        for nd in path:
            a, b = m.ss[nd], m.ss[nd + 1]
            T = b - a
            if not T:
                continue
            e = np.array(m.succ[a:b], np.int64)
            cnt = np.zeros(m.V)
            cnt[e >> 24] = e & 0xFFFFFF
            p = (cnt + T * p) / (cnt.sum() + T)
        if len(self.cache) < 2_000_000:
            self.cache[ctx] = p
        return p

    def word(self, rng, temp: float = 1.0, max_len: int = 14) -> str:
        for _ in range(100):
            w = ""
            while len(w) < max_len:
                p = self.dist(" " + w)[self.idx]
                if not w or w[-1] in "'-":  # a word starts and continues with a letter
                    p = p.copy()
                    p[[i for i, c in enumerate(self.allowed) if c not in LETTERS]] = 0
                if temp != 1.0:
                    p = p ** (1.0 / temp)
                p = p / p.sum()
                c = self.allowed[int(rng.choice(len(p), p=p))]
                if c == " ":
                    break
                w += c
            w = w.rstrip("'-")
            if w and w.lower() not in self.reject:
                return w
        while not w or w.lower() in self.reject:  # rarely: a letter more makes it no word
            w += LETTERS[int(rng.integers(26))]
        return w

    def line(self, rng, temp: float = 1.0) -> str:
        k = int(rng.integers(1, 7))
        ws = []
        for _ in range(k):
            w = self.word(rng, temp)
            r = rng.random()
            ws.append(w.capitalize() if r < 0.15 else w.upper() if r < 0.18 else w)
        return " ".join(ws)[:40].strip()


# ---------------------------------------------------------------------------- codes

UNITS = ["km", "m", "cm", "mm", "kg", "g", "mg", "l", "ml", "h", "min", "s", "ms", "mph", "kph",
         "kB", "MB", "GB", "TB", "kW", "W", "V", "mA", "Hz", "kHz", "ft", "in", "lb", "oz", "pt"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
UP = "ABCDEFGHJKLMNPRSTUVWXYZ"
TLDS = ["com", "org", "net", "de", "uk", "co.uk", "io", "eu", "fr", "nl", "info", "edu", "gov", "ch", "at"]


def _ord(n: int) -> str:
    return str(n) + ("th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


class Codes:
    """Code and number lines as they are written. `word()` gives a word for addresses and
    for the words around a code (pseudo-words and book words)."""

    def __init__(self, vocab: list[str], pseudo: Pseudo, names: Names):
        self.vocab, self.pseudo, self.names = vocab, pseudo, names

    def _w(self, rng) -> str:
        return self.vocab[int(rng.integers(len(self.vocab)))]

    def _label(self, rng) -> str:  # a domain or user name part
        k = rng.random()
        if k < 0.4:
            s = self.pseudo.word(rng).lower()
        elif k < 0.7:
            s = self._w(rng)
        else:
            s = self.names.pick("surname", rng).lower()
        s = re.sub(r"[^a-z]", "", s)
        return s or "mail"

    def _n(self, rng, lo, hi) -> int:
        return int(rng.integers(lo, hi + 1))

    def make(self, kind: str, rng) -> str:
        n = lambda lo, hi: self._n(rng, lo, hi)
        pick = lambda xs: xs[int(rng.integers(len(xs)))]
        if kind == "ordinal":
            k = rng.random()
            if k < 0.3:
                s = _ord(n(1, 100) if rng.random() < 0.8 else n(100, 999))
            elif k < 0.75:
                v = str(n(1, 999)) if rng.random() < 0.7 else f"{n(0, 99)}.{n(0, 9)}"
                s = v + pick(["", " "]) + pick(UNITS)
            elif k < 0.85:
                s = f"{n(1, 100)}%"
            elif k < 0.93:
                s = f"{n(1, 99)} x {n(1, 99)}" + pick(["", " cm", " mm", " m", " in"])
            else:
                s = pick(["No. ", "no. ", "#", "page ", "p. ", "ch. ", "vol. "]) + str(n(1, 400))
        elif kind == "time":
            h, m = n(0, 23), n(0, 59)
            s = pick([f"{h}:{m:02d}", f"{h:02d}:{m:02d}", f"{h % 12 or 12}:{m:02d} {pick(['am', 'pm'])}",
                      f"{h % 12 or 12}{pick(['am', 'pm'])}", f"{h % 12 or 12}.{m:02d}{pick(['am', 'pm', ''])}",
                      f"{h}h{m:02d}", f"{h:02d}:{m:02d}:{n(0, 59):02d}", f"{h}:{m:02d}-{(h + 1) % 24}:{m:02d}"])
        elif kind == "date":
            d, mo, y = n(1, 28), n(1, 12), n(1900, 2099)
            s = pick([f"{d}/{mo}/{y}", f"{mo}/{d}/{y % 100:02d}", f"{d:02d}.{mo:02d}.{y}", f"{y}-{mo:02d}-{d:02d}",
                      f"{d} {MONTHS[mo - 1]} {y}", f"{MONTHS[mo - 1]} {d}, {y}", f"{d} {MONTHS[mo - 1][:3]} {y}",
                      f"{DAYS[n(0, 6)][:3]} {d} {MONTHS[mo - 1][:3]}", f"{_ord(d)} {MONTHS[mo - 1]}",
                      f"{d}-{mo}-{y}", f"{MONTHS[mo - 1]} {y}", f"{d}.{mo}."])
        elif kind == "phone":
            s = pick([f"({n(200, 999)}) {n(200, 999)}-{n(0, 9999):04d}", f"{n(200, 999)}-{n(200, 999)}-{n(0, 9999):04d}",
                      f"+{n(1, 99)} {n(10, 999)} {n(100, 9999)} {n(1000, 9999)}", f"0{n(10, 9999)} {n(100000, 9999999)}",
                      f"0{n(100, 999)} {n(100, 999)} {n(1000, 9999)}", f"+{n(1, 99)}-{n(100, 999)}-{n(1000000, 9999999)}",
                      f"{n(100, 999)} {n(1000, 9999)}", f"0{n(1000000000, 9999999999)}"])
        elif kind == "postcode":
            L = lambda: pick(UP)
            s = pick([f"{n(10000, 99999)}", f"{n(10000, 99999)}-{n(1000, 9999)}",
                      f"{L()}{L()}{n(1, 99)} {n(1, 9)}{L()}{L()}", f"{L()}{n(1, 9)} {n(1, 9)}{L()}{L()}",
                      f"{n(1000, 9999)} {L()}{L()}", f"{L()}{n(1, 9)}{L()} {n(1, 9)}{L()}{n(1, 9)}",
                      f"{n(1000, 9999)}", f"{L()}-{n(1000, 99999)}"])
        elif kind == "plate":
            L = lambda k: "".join(pick(UP) for _ in range(k))
            s = pick([f"{L(n(1, 3))}-{L(n(1, 2))} {n(1, 9999)}", f"{L(2)}{n(10, 99)} {L(3)}", f"{n(1, 9)}{L(3)}{n(100, 999)}",
                      f"{L(2)} {n(1000, 9999)}", f"{L(3)} {n(100, 999)}", f"{L(2)}-{n(100, 999)}-{L(2)}",
                      f"{n(10, 99)}-{L(3)}-{n(1, 9)}"])
        elif kind == "part":
            L = lambda k: "".join(pick(UP) for _ in range(k))
            s = pick([f"{L(1)}{n(1, 9)}{L(1)}{n(1, 9)}", f"{L(1)}-{n(10, 9999)}", f"{L(n(1, 3))}{n(1, 99)}",
                      f"{L(2)}{n(100, 9999)}-{L(1)}", f"{pick(['ISO', 'DIN', 'EN', 'IEC', 'RFC'])} {n(100, 99999)}",
                      f"v{n(0, 9)}.{n(0, 20)}" + (f".{n(0, 30)}" if rng.random() < 0.5 else ""),
                      f"{pick(['PN', 'SN', 'Ref', 'Art', 'Mk'])} {n(100, 99999)}" + pick(["", f"-{L(1)}"]),
                      f"{L(1)}{n(1, 9)}", f"{n(1, 9)}{L(n(1, 2))}{n(1, 999)}",
                      "".join(pick(UP + "0123456789") for _ in range(n(4, 10)))])
        elif kind == "email":
            user = self._label(rng)
            k = rng.random()
            if k < 0.35:
                user = self.names.pick("given", rng).lower() + pick([".", "_", "", "-"]) + self._label(rng)
            elif k < 0.55:
                user += str(n(1, 999))
            dom = self._label(rng) + "." + pick(TLDS)
            s = f"{user}@{dom}"
        elif kind == "web":
            dom = self._label(rng) + pick(["", "-" + self._label(rng)]) + "." + pick(TLDS)
            path = "/" + self._label(rng) if rng.random() < 0.4 else ""
            s = pick(["www.", "https://", "http://", "", ""]) + dom + path
        else:  # number
            s = pick([str(n(0, 10 ** n(1, 7))), f"{n(0, 999)}.{n(0, 99)}", f"${n(1, 999)}.{n(0, 99):02d}",
                      f"{n(1, 999)},{n(0, 999):03d}", f"-{n(1, 999)}", f"{n(1, 99)}.{n(0, 999)}%",
                      f"{n(1, 9)}/{n(2, 12)}", f"{n(1, 999)} + {n(1, 999)} = {n(2, 1999)}",
                      f"{n(1, 99)}-{n(1, 99)}", f"${n(1, 99)}", f"{n(1, 9)},{n(0, 99):02d}"])
        # a third inside a few words, as a code stands in a sentence
        if rng.random() < (0.1 if kind in ("email", "web") else 1 / 3):
            before = [self._w(rng) for _ in range(int(rng.integers(0, 3)))]
            after = [self._w(rng) for _ in range(int(rng.integers(0 if before else 1, 3)))]
            t = " ".join(before + [s] + after)
            if len(t) <= 40:
                s = t[0].upper() + t[1:] if before and rng.random() < 0.3 else t
        return s[:40].strip()


# ---------------------------------------------------------------------------- the rest

def uniform(rng) -> str:
    alpha = list(text.CHARS[:62])
    return " ".join("".join(rng.choice(alpha, int(rng.integers(1, 8)))) for _ in range(int(rng.integers(1, 4))))


LONE_PUNCT = ".,;:!?'-()@/&+#%_=$"


def lone(rng) -> str:
    k = rng.random()
    if k < 0.35:
        return str(rng.choice(list(LETTERS)))
    if k < 0.6:
        return str(rng.choice(list(LETTERS.upper())))
    if k < 0.75:
        return str(rng.integers(0, 10))
    if k < 0.93:
        return str(rng.choice(list(LONE_PUNCT)))
    return str(rng.choice(["...", "?!", "!!", "--", "()", ":)", "'", ".,", "?", "!"]))


# ---------------------------------------------------------------------------- corpus

def _books(raw: Path, seed: int):
    """The training books' sentences as text.corpus splits them (train, val), the same lines
    for the same seed, and the test book's (never in training)."""
    import json
    rng = np.random.default_rng(seed)
    man = json.loads((raw / "manifest.json").read_text())
    tr, te = [], []
    for bid, m in man.items():
        s = text.sentences(text.strip_gutenberg((raw / f"pg{bid}.txt").read_text(encoding="utf-8-sig")))
        (tr if m["split"] == "train" else te).extend(s)
    rng.shuffle(tr)
    return tr[2000:], tr[:2000], te


def reject_words(raw: Path, wordlist: Path | None) -> set[str]:
    """Words a pseudo-word must not be: every word of the books (all of them, the test book's
    too) and of the word list."""
    tr, val, te = _books(raw, 1)
    out = set(text.vocab(tr + val + te))
    if wordlist and wordlist.exists():
        out |= {w.strip().lower() for w in wordlist.read_text().splitlines() if w.strip()}
    return out


class Generator:
    """Lines of every kind (module docstring); `held` draws names from the held-out part."""

    def __init__(self, raw: Path, gclm: Path, names_dir: Path, wordlist: Path | None, held: bool = False):
        tr, self.val_s, self.test_s = _books(raw, 1)
        self.train_s = tr
        self.vocab = text.vocab(tr)
        self.pseudo = Pseudo(gclm, reject_words(raw, wordlist))
        self.names = Names(load_names(names_dir), held)
        self.codes = Codes(self.vocab, self.pseudo, self.names)

    def one(self, kind: str, rng) -> str:
        if kind == "words":
            return text.random_words(self.vocab, 1, rng)[0]
        if kind == "pseudo":
            return self.pseudo.line(rng)
        if kind == "flat":
            return self.pseudo.line(rng, FLAT_T)
        if kind == "name":
            return self.names.line(rng)
        if kind == "uniform":
            return uniform(rng)
        if kind == "lone":
            return lone(rng)
        if kind in CODE_KINDS:
            return self.codes.make(kind, rng)
        raise ValueError(kind)

    def lines(self, n: int, seed: int, shares: dict = SHARES, prose_from: list[str] | None = None) -> list[tuple[str, str]]:
        """n (text, kind) lines in the shares given, shuffled; prose from `prose_from`
        sentences (default: the training books' training part)."""
        rng = np.random.default_rng(seed)
        counts = {k: int(round(n * v)) for k, v in shares.items()}
        counts[max(counts, key=counts.get)] += n - sum(counts.values())
        out = []
        if counts.get("prose"):
            sents = list(prose_from if prose_from is not None else self.train_s)
            prose = []
            while len(prose) < counts["prose"]:
                rng.shuffle(sents)
                prose += text.chunks(sents, rng)
            out += [(t, "prose") for t in prose[:counts["prose"]]]
        for k, c in counts.items():
            if k == "prose":
                continue
            out += [(self.one(k, rng), k) for _ in range(c)]
        out = [(t, k) for t, k in out if t and all(ch in CHARS4 for ch in t)]
        rng.shuffle(out)
        return out
