#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Chooses the samples app's word pool for a language: common words, so that the
language's frequent letter pairs each come up several times.

    word-pool.py --keep words.txt --size 3000 [--exclude FILE] TEXT... > words.txt

How common a word is comes from counting it in the TEXTs (public-domain books; the
counts are not kept, only the choice). A word counts when it is mostly written in
lower case (not a name), has 2 to 12 letters, and appears in at least --books of the
texts; words with apostrophes don't count. The words of --keep come first and stay.
Then the commonest words up to --common, then words chosen for the letter pairs that
are still short of --times uses (pairs that make at least --pair of all pairs in the
texts), then the commonest again up to --size. --exclude: words never to take
(separated by spaces or lines; # comments).
"""
import argparse
import collections
import re
import sys

WORD = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*")


def body(text):
    """the book without a Project Gutenberg header and licence"""
    a = re.search(r"\*\*\* ?START OF[^\n]*\n", text)
    b = re.search(r"\*\*\* ?END OF", text)
    return text[a.end() if a else 0:b.start() if b else len(text)]


def pairs(w):
    return [w[i:i + 2] for i in range(len(w) - 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("texts", nargs="+")
    ap.add_argument("--keep")
    ap.add_argument("--exclude")
    ap.add_argument("--size", type=int, default=3000)
    ap.add_argument("--common", type=int, default=2000)
    ap.add_argument("--times", type=int, default=5)
    ap.add_argument("--pair", type=float, default=0.0005)
    ap.add_argument("--books", type=int, default=3)
    a = ap.parse_args()

    count, lower, books = collections.Counter(), collections.Counter(), collections.Counter()
    for f in a.texts:
        seen = set()
        for m in WORD.finditer(body(open(f, encoding="utf-8", errors="replace").read())):
            t = m.group(0)
            if "'" in t or "\u2019" in t:
                continue                # forms with apostrophes are written for the set by hand
            w = t.lower()
            count[w] += 1
            lower[w] += t == w
            seen.add(w)
        books.update(seen)
    exclude = set()
    if a.exclude:
        exclude = {w.lower() for ln in open(a.exclude) if not ln.startswith("#") for w in ln.split()}
    ok = [w for w in count if 2 <= len(w) <= 12 and books[w] >= a.books and lower[w] >= 0.6 * count[w]
          and w not in exclude]
    ok.sort(key=lambda w: (-count[w], w))

    keep = []
    if a.keep:
        keep = [ln.strip() for ln in open(a.keep) if ln.strip()]
    chosen, have = list(keep), {w.lower() for w in keep}

    def take(w):
        chosen.append(w)
        have.add(w)

    for w in ok:
        if len(chosen) >= min(a.common, a.size):
            break
        if w not in have:
            take(w)
    # the letter pairs of the texts (inside words, as written)
    pc = collections.Counter()
    for w, n in count.items():
        for p in pairs(w):
            pc[p] += n
    total = sum(pc.values())
    frequent = {p for p, n in pc.items() if n >= a.pair * total}
    uses = collections.Counter(p for w in chosen for p in pairs(w.lower()) if p in frequent)

    def gain(w):
        return sum(1 for p in set(pairs(w)) if p in frequent and uses[p] < a.times)

    rest = [w for w in ok if w not in have]
    while len(chosen) < a.size:
        best, bg = None, 0
        for w in rest:
            g = gain(w)
            if g > bg or (g == bg and g and count[w] > count[best]):
                best, bg = w, g
        if not best:
            break
        take(best)
        rest.remove(best)
        uses.update(p for p in pairs(best) if p in frequent)
    for w in rest:
        if len(chosen) >= a.size:
            break
        take(w)
    short = sorted(p for p in frequent if uses[p] < a.times)
    print("word-pool: %d words, %d frequent pairs, short of %d uses: %s" % (
        len(chosen), len(frequent), a.times, " ".join(short) or "none"), file=sys.stderr)
    sys.stdout.write("".join(w + "\n" for w in chosen))


if __name__ == "__main__":
    main()
