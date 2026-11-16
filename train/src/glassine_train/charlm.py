"""The character model: how likely each character is after the ones before it.

The recognizer reads with it (docs/recognizer.md, Characters; spec 001 FR-019): the
handwriting model is CTC, each frame read on its own, so on its own it has no notion that
"h" after "lig" is far likelier than "4". The character model is an interpolated
Witten-Bell model over character n-grams:

    P(c | h) = (C(h c) + T(h) * P(c | h')) / (C(h) + T(h)),   h' = h without its oldest char,

from the empty context down to a uniform distribution over the character set; C(h) is the
sum of the counts kept after h, T(h) the number of distinct characters kept after h. A
context is kept with all its counts when it was seen at least `min_hist` times, so every
kept distribution is whole; a rarer context backs off to its shorter one.

Text: the paragraphs of the trainer's public-domain training books (text.py TRAIN, never
the test book), cleaned as the trainer cleans them, every other character a space. Each
paragraph is counted as if a space came before it, so the start of a reading without text
before it (a space as its context) is the start of a word. A paragraph whose CRC-32 is
divisible by HELD_MOD is left out: it is the held-out text for the bits per character and
for the decoder's tuning lines (`held`). The book files are pinned by sha256 (SOURCES).
Generated number and code lines (`codes`, CODES of the characters) are counted beside the
books, so digits get their real neighbours: after digits, spaces and signs, letters only in
units, ordinals and codes.

File (`chars.gclm`, version 1, little-endian), all counts as integers so the same text
gives the same bytes on any machine:

    header, 48 bytes: "GHCM", then eleven uint32: version (1), header bytes (48), order N,
        symbols V, charset bytes, nodes, edges, successors, smoothing (1 = Witten-Bell),
        0, 0
    charset: UTF-8, symbol k = its k-th character, padded with zeros to a multiple of 4
    edge_start[nodes + 1], succ_start[nodes + 1]: uint32
    edges[edges]: uint32, symbol << 24 | node
    succ[successors]: uint32, symbol << 24 | count (count < 2^24)

Node 0 is the empty context. A node's children (edges edge_start[i] .. edge_start[i+1])
extend its context by one older character (the symbol of the edge), sorted by symbol; its
successors (succ_start[i] .. succ_start[i+1]) are the characters seen after the context,
sorted by symbol, with their counts. Nodes are in order of context length.

Standard library only (the recognizer's check imports this file without numpy).
"""

import hashlib
import json
import math
import re
import struct
import zlib
from pathlib import Path

MAGIC = b"GHCM"
VERSION = 1
HEADER = 48
WITTEN_BELL = 1
ORDER = 5  # characters of context + 1
MIN_HIST = 20  # a context is kept when seen this often
CODES = 0.03  # number and code lines: this share of the characters
HELD_MOD = 50  # paragraphs with crc32 % HELD_MOD == 0 are held out
MAX_COUNT = (1 << 24) - 1

# the training books' plain-text editions (text.py TRAIN), as fetched: id, sha256
SOURCES = [
    (1342, "3f6bb9d6f78e0293b56acd4714dd68cb7d6d1d293402031ce9d5a216bcaf9d75"),
    (11, "01b38ea4c710a84bc18d0bd41271a5a1a92b94e97b2812f4dece97d4a694725e"),
    (1661, "922e2a12ccb43a4c9544c260b2166c6ad2097aeb5957faeee113f173bb857cd0"),
    (84, "2b97b7f8aad508649481d8dd4f6b54f9b965ddb3ba634e9151c815f537b49e42"),
    (98, "d54c2b80d40a40b982cd88852c6180bb944d95acdb028af3d0e01a1750681784"),
    (120, "5bc08275eacea8640b1aa185c645283bb8c564d7b23df6b03437d229b4fc6ebd"),
    (76, "d617a37aa7ae1e1a93dcde2634db2bccb86824e31e29a55230bdbf77d6872d59"),
    (35, "2892e919000e17c83e1dac51b30f4675db50536b644d7579fe8a89bb399a9bdc"),
    (1260, "13414dee2951c3ee731d76d2ffd822016b2479c892162760c5d0eb2aa5fa7631"),
]


# ---------------------------------------------------------------------------- text

def held(p: str) -> bool:
    return zlib.crc32(p.encode("utf-8")) % HELD_MOD == 0


def paragraphs(raw: Path, charset: str) -> list[str]:
    """The training books' paragraphs (pinned files in raw/), in the character set."""
    from .text import TRAIN, clean, strip_gutenberg
    if sorted(b for b, _ in SOURCES) != sorted(b for b, _, _ in TRAIN):
        raise SystemExit("charlm.SOURCES must list exactly the training books of text.TRAIN")
    out = []
    for bid, sha in SOURCES:
        f = raw / f"pg{bid}.txt"
        if hashlib.sha256(f.read_bytes()).hexdigest() != sha:
            raise SystemExit(f"{f}: sha256 differs from charlm.SOURCES (the edition changed?)")
        body = strip_gutenberg(f.read_text(encoding="utf-8-sig"))
        for p in re.split(r"\n\s*\n", body):
            p = clean(p)
            p = "".join(c if c in charset else " " for c in p)
            p = re.sub(r" +", " ", p).strip()
            if len(p) >= 20 and not p.isupper():  # headings, tables of contents
                out.append(p)
    return out


# ---------------------------------------------------------------------------- numbers and codes

class _Draw:
    """deterministic draws (splitmix64), the same on every Python"""

    def __init__(self, seed: int):
        self.x = seed & (2**64 - 1)

    def below(self, n: int) -> int:
        self.x = (self.x + 0x9E3779B97F4A7C15) & (2**64 - 1)
        z = self.x
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & (2**64 - 1)
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & (2**64 - 1)
        return (z ^ (z >> 31)) % n

    def pick(self, seq):
        return seq[self.below(len(seq))]


UNITS = ["km", "m", "cm", "mm", "kg", "g", "l", "ml", "h", "min", "s", "pm", "am", "mph", "kb", "MB", "GB"]
UPPER = "ABCDEFGHJKLMNPRSTUVWXYZ"


def codes(n: int, seed: int = 1) -> list[str]:
    """Numbers and codes as they are written, so digits get their real neighbours (after a
    digit, a space, a sign; letters only in units, ordinals and codes): numbers, decimals,
    prices, times, dates, years, ordinals, measures, phone numbers, postcodes, number plates
    and part numbers."""
    r = _Draw(seed)
    num = lambda lo, hi: str(lo + r.below(hi - lo + 1))
    out = []
    for _ in range(n):
        k = r.below(12)
        if k == 0:
            s = num(0, 10 ** (1 + r.below(6)))
        elif k == 1:
            s = num(0, 999) + "." + num(0, 99)
        elif k == 2:
            s = num(1, 500) + "." + "%02d" % r.below(100)
        elif k == 3:
            s = "%d:%02d" % (r.below(24), r.below(60))
        elif k == 4:
            d, m, y = num(1, 28), num(1, 12), num(1900, 2099)
            s = r.pick([f"{d}-{m}-{y}", f"{d}.{m}.{y}", f"{y}-{int(m):02d}-{int(d):02d}", f"{d} {m} {y}"])
        elif k == 5:
            s = num(1700, 2099)
        elif k == 6:
            d = 1 + r.below(100)
            s = str(d) + ("th" if 10 <= d % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th"))
        elif k == 7:
            s = num(1, 999) + r.pick(["", " "]) + r.pick(UNITS)
        elif k == 8:
            area = "0" + num(10, 999)
            s = r.pick([f"({area}) ", area + " ", area + "-", ""]) + num(100, 999) + r.pick([" ", "-", ""]) + \
                num(1000, 9999)
        elif k == 9:
            s = r.pick([num(10000, 99999), r.pick(UPPER) + r.pick(UPPER) + num(1, 99) + " " + num(1, 9) +
                        r.pick(UPPER) + r.pick(UPPER)])
        elif k == 10:
            s = "".join(r.pick(UPPER) for _ in range(1 + r.below(3))) + r.pick(["-", " "]) + \
                "".join(r.pick(UPPER) for _ in range(r.below(3))) + r.pick([" ", ""]) + num(1, 9999)
        else:
            s = "".join(r.pick(UPPER + "0123456789") for _ in range(3 + r.below(6)))
        out.append(s.strip())
    return out


# ---------------------------------------------------------------------------- counting

def count(lines, order: int) -> list[dict]:
    """counts[k][(h, c)] for contexts h of k characters (a space before each line)."""
    counts = [dict() for _ in range(order)]
    for ln in lines:
        s = " " + ln
        for i in range(1, len(s)):
            c = s[i]
            for k in range(min(order, i + 1)):
                key = (s[i - k:i], c)
                d = counts[k]
                d[key] = d.get(key, 0) + 1
    return counts


def prune(counts: list[dict], min_hist: int) -> list[dict]:
    out = []
    for k, d in enumerate(counts):
        hist = {}
        for (h, _), n in d.items():
            hist[h] = hist.get(h, 0) + n
        out.append({key: n for key, n in d.items() if k == 0 or hist[key[0]] >= min_hist})
    return out


# ---------------------------------------------------------------------------- the file

def encode(counts: list[dict], charset: str, order: int) -> bytes:
    sym = {c: i for i, c in enumerate(charset)}
    succ: dict[tuple, list] = {}
    for d in counts:
        for (h, c), n in d.items():
            if c not in sym or any(x not in sym for x in h):
                continue
            succ.setdefault(tuple(sym[x] for x in reversed(h)), []).append((sym[c], n))
    succ.setdefault((), [])
    nodes = sorted(succ, key=lambda r: (len(r), r))  # r: the context, newest character first
    for r in nodes:
        if r and r[:-1] not in succ:
            raise ValueError("a context without its shorter one")
    ids = {r: i for i, r in enumerate(nodes)}
    children: dict[tuple, list] = {r: [] for r in nodes}
    for r in nodes:
        if r:
            children[r[:-1]].append((r[-1], ids[r]))
    if len(nodes) >= 1 << 24:
        raise ValueError("too many contexts")
    es, ss, edges, sv = [0], [0], [], []
    for r in nodes:
        for s, i in sorted(children[r]):
            edges.append(s << 24 | i)
        for s, n in sorted(succ[r]):
            sv.append(s << 24 | min(n, MAX_COUNT))
        es.append(len(edges))
        ss.append(len(sv))
    cs = charset.encode("utf-8")
    cs += b"\0" * (-len(cs) % 4)
    head = MAGIC + struct.pack("<11I", VERSION, HEADER, order, len(charset), len(charset.encode("utf-8")),
                               len(nodes), len(edges), len(sv), WITTEN_BELL, 0, 0)
    assert len(head) == HEADER
    arr = lambda a: struct.pack(f"<{len(a)}I", *a)
    return head + cs + arr(es) + arr(ss) + arr(edges) + arr(sv)


class Model:
    """A chars.gclm file read back; logp() as the recognizer computes it."""

    def __init__(self, data: bytes):
        if data[:4] != MAGIC:
            raise ValueError("not a character model")
        (ver, hb, self.order, nsym, csb, nn, ne, nsucc, sm, _, _) = struct.unpack("<11I", data[4:HEADER])
        if ver != VERSION or hb != HEADER or sm != WITTEN_BELL:
            raise ValueError("a character model this version doesn't read")
        self.charset = data[HEADER:HEADER + csb].decode("utf-8")
        self.sym = {c: i for i, c in enumerate(self.charset)}
        o = HEADER + csb + (-csb % 4)
        u = lambda n: struct.unpack_from(f"<{n}I", data, o)
        self.es = u(nn + 1); o += 4 * (nn + 1)
        self.ss = u(nn + 1); o += 4 * (nn + 1)
        self.edges = u(ne); o += 4 * ne
        self.succ = u(nsucc); o += 4 * nsucc
        if o != len(data):
            raise ValueError("size doesn't match the header")
        self.V = nsym

    def _child(self, node: int, s: int) -> int:
        for e in self.edges[self.es[node]:self.es[node + 1]]:
            if e >> 24 == s:
                return e & 0xFFFFFF
        return -1

    def logp(self, context: str, c: str) -> float:
        """natural log of P(c | the last order-1 characters of context); a character
        outside the set: its context ends there"""
        if c not in self.sym:
            return math.log(1.0 / self.V)
        h = []
        for x in reversed(context[-(self.order - 1):] if self.order > 1 else ""):
            if x not in self.sym:
                break
            h.append(self.sym[x])
        path, node = [0], 0
        for s in h:
            node = self._child(node, s)
            if node < 0:
                break
            path.append(node)
        cs, p = self.sym[c], 1.0 / self.V
        for node in path:
            a, b = self.ss[node], self.ss[node + 1]
            T = b - a
            if not T:
                continue
            C = n = 0
            for e in self.succ[a:b]:
                C += e & 0xFFFFFF
                if e >> 24 == cs:
                    n = e & 0xFFFFFF
            p = (n + T * p) / (C + T)
        return math.log(p)


def bits_per_char(m: Model, lines) -> float:
    lp = n = 0
    for ln in lines:
        s = " " + ln
        for i in range(1, len(s)):
            lp += m.logp(s[:i], s[i])
            n += 1
    return -lp / n / math.log(2)


# ---------------------------------------------------------------------------- building

LICENSE = """The character model chars.gclm is derived from the plain-text editions of nine
public-domain books (Project Gutenberg; each catalogue record says "Public domain in the
USA." and every author died before 1956): {books}. It holds counts of character sequences
only, no text. Built with GlassineOS/handwriting train/src/glassine_train/charlm.py; the
model file is GPL-2.0-or-later like the recognizer that reads it.
"""


def build(raw: Path, out: Path, order: int = ORDER, min_hist: int = MIN_HIST, codes_share: float = CODES) -> dict:
    """chars.gclm, chars.json (recipe, sizes, held-out bits per character) and CHARS-LICENSE
    in out/, from the pinned books in raw/ (text.fetch)."""
    from .text import CHARS, TRAIN
    paras = paragraphs(raw, CHARS)
    train = [p for p in paras if not held(p)]
    test = [p for p in paras if held(p)]
    nchar = sum(map(len, train))
    extra, n = [], 0
    for c in codes(int(nchar * codes_share / 6) + 1):
        if n >= nchar * codes_share:
            break
        extra.append(c)
        n += len(c) + 1
    data = encode(prune(count(train + extra, order), min_hist), CHARS, order)
    out.mkdir(parents=True, exist_ok=True)
    (out / "chars.gclm").write_bytes(data)
    m = Model(data)
    info = {"format": "gclm", "version": VERSION, "order": order, "min_hist": min_hist,
            "smoothing": "witten-bell", "bytes": len(data), "contexts": len(m.es) - 1,
            "successors": len(m.succ), "sha256": hashlib.sha256(data).hexdigest(),
            "paragraphs": len(train), "characters": nchar, "code_lines": len(extra),
            "code_characters": n, "held_paragraphs": len(test),
            "held_bits_per_char": round(bits_per_char(m, test), 4),
            "sources": [{"id": b, "sha256": s, "url": f"https://www.gutenberg.org/cache/epub/{b}/pg{b}.txt"}
                        for b, s in SOURCES]}
    (out / "chars.json").write_text(json.dumps(info, indent=1) + "\n")
    books = "; ".join(f"{t}, {a}" for _, t, a in TRAIN)
    (out / "CHARS-LICENSE").write_text(LICENSE.format(books=books))
    return info


FILES = ["chars.gclm", "chars.json", "CHARS-LICENSE"]


def release(raw: Path, out: Path, lang: str, version: int, order: int = ORDER, min_hist: int = MIN_HIST,
            codes_share: float = CODES):
    """glassine-handwriting-chars-LANG-VERSION.tar.gz (and .sha512) in out/: the directory's
    files beside the base model's (docs/recognizer.md, Base models)."""
    from .release import pack
    name = f"glassine-handwriting-chars-{lang}-{version}"
    stage = out / name
    info = build(raw, stage, order, min_hist, codes_share)
    path, sha = pack(stage, name, out, FILES)
    return info, path, sha
