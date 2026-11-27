"""Real ink as ink sets (inkstore.py) for a base model's training: the BRUSH dataset
(brush.py; its terms and the citation are there) and a person's own prompted samples (a
split made by `glassine-train real`). The sets are featurized like the generator's
(`featurize --ref mix --measured`) and merged into its training set (`mergeset --add`), so
real ink reaches the model through the same features, references and characters.

Everything this writes holds other people's ink, so it goes only to private directories
(made 0700 here); nothing of it belongs in a repository, CI, a pull request, a release or a
published log. What is printed is counts.

**BRUSH, split by writer and by text.** Writers wrote the same prompts, so holding out
writers alone would let a test text be learned from other writers. Texts are grouped first:
two texts whose normalized forms (real._norm) are alike as real.near_test finds them are in
one group (`text_groups`), so a near copy of a held-out text is held out with it. Each
writer and each group (by its first normalized text) goes to a part by a hash of the seed
and its name (`part_of`), so the split is the same on every machine and needs no list in
the repository: test writers (`test_writers`), dev writers (`dev_writers`), the rest train;
held-out groups (`held_texts`). Train is the train writers' lines of the other groups; dev
and test are the dev and test writers' lines of the held-out groups. `split.json` records the parts' writers and
drawings (private: `writerseval --split` reads the same lines).

**As ink.** One em is the writing box's height (brush.BOX_H): the line is `line_scale`
em, drawn per line and copy log-uniform in `line_scale` (the writing area's line over the
box, so the letters' size against the line varies as the generator's does), the baseline
the box's suggested one. The drawing has no times: points are timed at brush.RATE per
second and strokes `pause` seconds apart (lognormal around it, per line). Characters
outside the alphabet (BRUSH has `"`, `[`, `]`, `*`, `<`, `>`) are left out with their own
strokes, spaces tidied after them; a line where such a character shares a stroke with
another is left out. `xh` is the writer's small letters as the recognizer measures them
(inkmeasure.small_height), the median over their training lines.

**A person's samples.** The split's train (and, as a set of its own, dev) samples, never
its test session (real.load_split checks the ids), in the alphabet and with a writing
area; a guest writer's store never (their samples are evaluation only). One em is the
area's line; copies after the first are new drawings (real.ink_augment).
"""

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from . import brush
from .inkmeasure import small_height
from .inkstore import Writer

GUEST_STORES = "handwriting-writers"  # the recognizer's guest stores' directory (spec 001 FR-021)


def private_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def part_of(seed: int, what: str, name: str, shares: list[tuple[str, float]], rest: str) -> str:
    """The part a writer or a text belongs to: a hash of seed, kind and name, as a number in
    [0, 1), against the parts' shares in order."""
    h = int.from_bytes(hashlib.sha256(f"{seed}:{what}:{name}".encode()).digest()[:8], "little") / 2 ** 64
    acc = 0.0
    for part, share in shares:
        acc += share
        if h < acc:
            return part
    return rest


def strip_outside(ln: brush.Line, alphabet: str) -> brush.Line | None:
    """The line without its characters outside `alphabet` and their strokes (module
    docstring); None when such a character shares a stroke with another, or nothing is left."""
    text = ln.text
    bad = {i for i, c in enumerate(text) if c not in alphabet}
    if not bad:
        return ln
    st = brush.strokes_of(ln)
    keep_pt = np.ones(len(ln.xy), bool)
    for idx in st:
        own = set(ln.owner[idx].tolist())
        if own & bad:
            if own - bad:
                return None
            keep_pt[idx] = False
    # the characters kept, spaces tidied: none at the ends, none twice
    kept = [i for i in range(len(text)) if i not in bad]
    out = []
    for i in kept:
        if text[i] == " " and (not out or text[out[-1]] == " "):
            continue
        out.append(i)
    while out and text[out[-1]] == " ":
        out.pop()
    new = {old: k for k, old in enumerate(out)}
    if not out or not keep_pt.any():
        return None
    own = ln.owner[keep_pt]
    if not all(int(o) in new for o in set(own.tolist())):
        return None  # ink of a space left out (not seen in the dataset)
    # a stroke end at a dropped point moves to the kept point before it
    ends = ln.ends.copy()
    for j in np.nonzero(~keep_pt & ends)[0]:
        k = j - 1
        while k >= 0 and not keep_pt[k]:
            k -= 1
        if k >= 0:
            ends[k] = True
    return brush.Line(ln.writer, ln.drawing, "".join(text[i] for i in out), ln.xy[keep_pt], ends[keep_pt],
                      np.array([new[int(o)] for o in own]))


def brush_ink(ln: brush.Line, line_scale: float, pause: float) -> dict:
    """The line as a generator sample (strokes x, y em and t s; spans; line and base em)."""
    em = brush.BOX_H
    strokes, spans = [], [[] for _ in ln.text]
    t = 0.0
    for j, idx in enumerate(brush.strokes_of(ln)):
        for c in set(ln.owner[idx].tolist()):
            if ln.text[c] != " ":
                spans[c].append(j)
        ts = t + np.arange(len(idx)) / brush.RATE
        strokes.append(np.c_[ln.xy[idx] / em, ts])
        t = ts[-1] + pause
    return {"text": ln.text, "strokes": strokes, "spans": [sorted(s) for s in spans], "line": float(line_scale),
            "base": brush.BOX_BASE / em}


def _median_xh(samples: list[dict]) -> float:
    v = [h for s in samples if (h := small_height(s["strokes"], s["spans"], s["text"])) > 0]
    return float(np.median(v)) if len(v) >= 5 else 0.0


def text_groups(texts: list[str]) -> dict[str, str]:
    """{normalized text: its group's first normalized text}: texts alike as real.near_test
    finds them (the same, or at least real.NEAR alike for four characters or more), joined."""
    import difflib
    from .real import NEAR
    t = sorted(set(texts))
    par = list(range(len(t)))

    def root(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    for i in range(len(t)):
        if len(t[i]) < 4:
            continue
        sm = difflib.SequenceMatcher(None, "", t[i])
        for j in range(i + 1, len(t)):
            if len(t[j]) < 4:
                continue
            sm.set_seq1(t[j])
            if sm.real_quick_ratio() >= NEAR and sm.quick_ratio() >= NEAR and sm.ratio() >= NEAR:
                a, b = root(i), root(j)
                par[max(a, b)] = min(a, b)
    return {x: t[root(i)] for i, x in enumerate(t)}


def brush_split(path: Path, seed: int, test_writers: float, dev_writers: float, held_texts: float,
                alphabet: str, others: dict | None = None) -> tuple[dict[str, list[brush.Line]], dict]:
    """{part: lines} (train, dev, test) and the split's record (module docstring). `others`:
    a dict to receive the lines each part leaves out (a dev or test writer's lines of the
    training texts, a training writer's of the held-out texts), by the writer's part."""
    from .real import _norm
    by = {"train": [], "dev": [], "test": []}
    stats = {"read": 0, "outside_alphabet_left_out": 0, "stripped": 0, "other_part_left_out": 0}
    lines = []
    for ln in brush.read(path):
        stats["read"] += 1
        s = strip_outside(ln, alphabet)
        if s is None:
            stats["outside_alphabet_left_out"] += 1
            continue
        stats["stripped"] += s is not ln
        lines.append((ln.text, s))
    group = text_groups([_norm(t) for t, _ in lines])
    held_groups = {g for g in set(group.values())
                   if part_of(seed, "text", g, [("held", held_texts)], "train") == "held"}
    for orig, s in lines:
        wp = part_of(seed, "writer", s.writer, [("test", test_writers), ("dev", dev_writers)], "train")
        if (group[_norm(orig)] in held_groups) != (wp != "train"):
            stats["other_part_left_out"] += 1
            if others is not None:
                others.setdefault(wp, []).append(s)
            continue
        by[wp].append(s)
    rec = {"seed": seed, "shares": {"test_writers": test_writers, "dev_writers": dev_writers, "held_texts": held_texts},
           "parts": {p: {} for p in by}, "counts": stats}
    for p, ls in by.items():
        for s in ls:
            rec["parts"][p].setdefault(s.writer, []).append(s.drawing)
        stats[p] = {"writers": len(rec["parts"][p]), "lines": len(ls)}
    stats["text_groups"] = len(set(group.values()))
    stats["held_groups"] = len(held_groups)
    return by, rec


def write_brush(path: Path, out: Path, seed: int = 5, copies: int = 4, line_scale: tuple = (0.7, 2.5),
                pause: float = brush.PAUSE, pause_sigma: float = 0.35, test_writers: float = 0.2,
                dev_writers: float = 0.05, held_texts: float = 0.2, alphabet: str | None = None) -> dict:
    """out/train (copies per line), out/dev (one copy, line_scale 1) and out/split.json."""
    from .lines import CHARS4
    alphabet = alphabet or CHARS4
    private_dir(out)
    by, rec = brush_split(path, seed, test_writers, dev_writers, held_texts, alphabet)
    (out / "split.json").write_text(json.dumps(rec))
    os.chmod(out / "split.json", 0o600)
    rng = np.random.default_rng(seed)
    meta_common = {"source": "brush", "alphabet": alphabet, "seed": seed, "line_scale": list(line_scale),
                   "pause": pause, "pause_sigma": pause_sigma}
    res = {"counts": rec["counts"]}
    for part, n_copies in (("train", copies), ("dev", 1)):
        lines = by[part]
        samples = []
        for c in range(n_copies):
            for ln in lines:
                if part == "train":
                    sc = float(np.exp(rng.uniform(np.log(line_scale[0]), np.log(line_scale[1]))))
                    pz = float(pause * rng.lognormal(0, pause_sigma))
                else:
                    sc, pz = 1.0, pause
                s = brush_ink(ln, sc, pz)
                s["writer"] = f"brush:{ln.writer}"
                samples.append(s)
        xh = {}
        for s in samples[:len(lines)]:
            xh.setdefault(s["writer"], []).append(s)
        xh = {w: _median_xh(v) for w, v in xh.items()}
        good = [v for v in xh.values() if v > 0]
        fallback = float(np.median(good)) if good else 0.25
        w = Writer(private_dir(out / part))
        for i, s in enumerate(samples):
            s["xh"] = xh.get(s["writer"]) or fallback
            w.add(s, "brush", seed * 1_000_003 + i)
        res[part] = w.close({**meta_common, "copies": n_copies, "writers": len(xh)})
    return res


def write_person(split: Path, out: Path, copies: int = 6, seed: int = 5, alphabet: str | None = None) -> dict:
    """out/train (copies per sample) and out/dev from a person's split (module docstring)."""
    from .lines import CHARS4
    from .real import ink_augment, load_split, reference, strokes_of
    alphabet = alphabet or CHARS4
    rep = json.loads((split / "split.json").read_text())["report"]
    store = rep.get("store")
    if not store or GUEST_STORES in Path(store).parts:
        raise SystemExit(f"{split}: not a split of the person's own store (a guest's samples are "
                         "evaluation only; the split must record its store, glassine-train real)")
    sp = load_split(split)
    private_dir(out)
    rng = np.random.default_rng(seed)
    res = {}
    for part, n_copies in (("train", copies), ("dev", 1)):
        base = []
        for s in sp[part]:
            ar = s.get("area") or {}
            if s.get("source") != "prompted" or not ar.get("line") or not s["text"] \
                    or not all(c in alphabet for c in s["text"]) or len(s.get("spans") or []) != len(s["text"]):
                continue
            st = strokes_of(s)
            if not st or len(st) != len(s["strokes"]):  # spans index the strokes: none may be empty
                continue
            k = 1.0 / ar["line"]
            ref = reference(s, None)
            base.append({"text": s["text"], "spans": s["spans"], "writer": "person", "line": 1.0,
                         "base": ref["base"] * k, "strokes": [np.c_[q[:, :2] * k, q[:, 2]] for q in st]})
        xh = _median_xh(base) or 0.2
        w = Writer(private_dir(out / part))
        i = 0
        for c in range(n_copies):
            for s in base:
                s2 = dict(s, xh=xh)
                if c:
                    s2["strokes"] = ink_augment(s["strokes"], rng)
                w.add(s2, "person", seed * 1_000_003 + i)
                i += 1
        res[part] = w.close({"source": "person", "alphabet": alphabet, "copies": n_copies, "samples": len(base)})
    return res
