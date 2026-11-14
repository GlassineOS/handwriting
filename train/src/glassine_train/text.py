"""Training text: public-domain books plus generated word, number and punctuation lines.

Every book is a public-domain work, taken from its plain-text edition. `fetch` checks each
book's catalogue record: rights "Public domain in the USA." and every author dead before
1956 (so the work is also free under life + 70 years rules). The distributor's header,
licence block and footer are cut away; only the work's own text is used.
"""

import json
import re
import unicodedata
import urllib.request
from pathlib import Path

import numpy as np

URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"
RDF = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.rdf"
DIED_BEFORE = 1956

# id, title, author (death year). TRAIN books give training text; TEST books are never
# used for training, so the held-out evaluation texts are unseen sentences.
TRAIN = [
    (1342, "Pride and Prejudice", "Jane Austen (1817)"),
    (11, "Alice's Adventures in Wonderland", "Lewis Carroll (1898)"),
    (1661, "The Adventures of Sherlock Holmes", "Arthur Conan Doyle (1930)"),
    (84, "Frankenstein", "Mary Shelley (1851)"),
    (98, "A Tale of Two Cities", "Charles Dickens (1870)"),
    (120, "Treasure Island", "Robert Louis Stevenson (1894)"),
    (76, "Adventures of Huckleberry Finn", "Mark Twain (1910)"),
    (35, "The Time Machine", "H. G. Wells (1946)"),
    (1260, "Jane Eyre", "Charlotte Bronte (1855)"),
]
TEST = [
    (55, "The Wonderful Wizard of Oz", "L. Frank Baum (1919)"),
]

CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,;:!?'-() "
_MAP = {"‘": "'", "’": "'", "“": "'", "”": "'", '"': "'",
        "—": "-", "–": "-", "_": "", "*": "", "æ": "ae", "Æ": "Ae"}
PD = "Public domain in the USA."


def strip_gutenberg(raw: str) -> str:
    """Keep only the text between the START and END markers."""
    m = re.search(r"\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", raw)
    e = re.search(r"\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG EBOOK", raw)
    if not m or not e:
        raise ValueError("no START/END markers")
    return raw[m.end():e.start()]


def clean(s: str) -> str:
    s = "".join(_MAP.get(c, c) for c in s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip()


def sentences(body: str) -> list[str]:
    paras = [clean(p) for p in re.split(r"\n\s*\n", body)]
    out = []
    for p in paras:
        if len(p) < 20 or p.isupper():  # chapter headings, tables of contents
            continue
        out += [x for x in re.split(r"(?<=[.!?])\s+", p) if x]
    return out


def _get(url: str, f: Path) -> str:
    if not f.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "glassine-train"})
        with urllib.request.urlopen(req, timeout=60) as r:
            f.write_bytes(r.read())
    return f.read_text(encoding="utf-8-sig")


def check_rights(rdf: str) -> list[int]:
    """Raise unless the record says public domain and every author died before DIED_BEFORE."""
    m = re.search(r"<dcterms:rights>(.*?)</dcterms:rights>", rdf)
    if not m or m.group(1).strip() != PD:
        raise ValueError(f"rights: {m.group(1) if m else 'missing'}")
    deaths = [int(d) for d in re.findall(r"<pgterms:deathdate[^>]*>(-?\d+)<", rdf)]
    if not deaths or max(deaths) >= DIED_BEFORE:
        raise ValueError(f"author death years {deaths}")
    return deaths


def fetch(dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for books, split in ((TRAIN, "train"), (TEST, "test")):
        for bid, title, author in books:
            deaths = check_rights(_get(RDF.format(id=bid), dest / f"pg{bid}.rdf"))
            _get(URL.format(id=bid), dest / f"pg{bid}.txt")
            manifest[bid] = {"title": title, "author": author, "split": split,
                             "source": URL.format(id=bid), "rights": PD, "author_died": deaths}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def chunks(sents: list[str], rng: np.random.Generator, max_len: int = 40) -> list[str]:
    """Cut sentences into lines of 1 to max_len characters at word boundaries, all in CHARS."""
    out = []
    for s in sents:
        words = s.split()
        i = 0
        while i < len(words):
            n = int(rng.integers(1, 9))
            line = " ".join(words[i:i + n])
            while len(line) > max_len and n > 1:
                n -= 1
                line = " ".join(words[i:i + n])
            i += n
            if len(line) <= max_len and line and all(c in CHARS for c in line):
                out.append(line)
    return out


def vocab(sents: list[str]) -> list[str]:
    words = set()
    for s in sents:
        for w in s.split():
            w = w.strip(".,;:!?'-()")
            if w.isalpha():
                words.add(w.lower())
    return sorted(words)


def random_words(words: list[str], n: int, rng: np.random.Generator, max_len: int = 40) -> list[str]:
    """Lines of words in random order: no sentence context, so the model must read shapes."""
    out = []
    while len(out) < n:
        k = int(rng.integers(1, 7))
        ws = [words[int(rng.integers(len(words)))] for _ in range(k)]
        ws = [w.capitalize() if rng.random() < 0.15 else w.upper() if rng.random() < 0.03 else w for w in ws]
        line = " ".join(ws)[:max_len].strip()
        if line:
            out.append(line)
    return out


def symbols(n: int, rng: np.random.Generator) -> list[str]:
    """Numbers, dates, times, prices and punctuation-heavy fragments."""
    out = []
    pun = ".,;:!?'-()"
    for _ in range(n):
        kind = int(rng.integers(5))
        if kind == 0:
            s = str(int(rng.integers(0, 10 ** int(rng.integers(1, 8)))))
        elif kind == 1:
            s = f"{rng.integers(1, 29)}-{rng.integers(1, 13)}-{rng.integers(1900, 2100)}"
        elif kind == 2:
            s = f"{rng.integers(0, 24)}:{rng.integers(0, 60):02d}"
        elif kind == 3:
            s = f"({rng.integers(0, 1000)}) {rng.integers(100, 1000)}-{rng.integers(1000, 10000)}"
        else:
            s = "".join(rng.choice(list("0123456789" + pun + "aeiou")) for _ in range(int(rng.integers(2, 12))))
        out.append(s.strip() or "0")
    return out


def letters(n: int, rng: np.random.Generator) -> list[str]:
    """Random letter strings, every letter equally likely: rare letters and capitals get
    their share, and no word knowledge helps."""
    alpha = list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return [" ".join("".join(rng.choice(alpha, int(rng.integers(1, 8))))
                     for _ in range(int(rng.integers(1, 4)))) for _ in range(n)]


def corpus(raw_dir: Path, seed: int = 1) -> dict[str, list[str]]:
    """Text lines per split: train (book lines + random words + symbols), val (held-out
    lines of the training books), test (lines of the test book), test_words (random words
    from the test book's vocabulary), test_symbols, test_letters."""
    rng = np.random.default_rng(seed)
    man = json.loads((raw_dir / "manifest.json").read_text())
    tr, te = [], []
    for bid, m in man.items():
        s = sentences(strip_gutenberg((raw_dir / f"pg{bid}.txt").read_text(encoding="utf-8-sig")))
        (tr if m["split"] == "train" else te).extend(s)
    rng.shuffle(tr)
    nval = 2000
    val_s, tr_s = tr[:nval], tr[nval:]
    lines = chunks(tr_s, rng)
    words = vocab(tr_s)
    lines += random_words(words, len(lines) // 3, rng)
    lines += symbols(len(lines) // 12, rng)
    lines += letters(len(lines) // 12, rng)
    rng.shuffle(lines)
    return {
        "train": lines,
        "val": chunks(val_s, rng),
        "test": chunks(te, rng),
        "test_words": random_words(vocab(te), 2000, rng),
        "test_symbols": symbols(500, rng),
        "test_letters": letters(500, rng),
    }
