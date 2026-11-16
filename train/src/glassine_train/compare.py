"""Readings of synthetic test sets by the recognizer program (`glassine-train inkread`),
compared between models: error per set group and line kind, lone characters at their size,
letters read as digits inside words, and paired bootstraps over lines.

A reading file is `SET.TAG.json` in a model's directory: [{id, text, cands, kind}, ...], the
lines of the set in order, so two models' files of one set pair line by line.
"""

import json
import re
from pathlib import Path

import numpy as np

from .metrics import edit
from .realeval import paired_bootstrap, per_sample, summary

GROUPS = ("heldout", "newprint", "newscript")
CODES = ("date", "email", "number", "ordinal", "part", "phone", "plate", "postcode", "time", "web")
WORDISH = re.compile(r"[A-Za-z0-9]+")


def load(d: Path, set_: str, tag: str) -> list[dict] | None:
    f = d / f"{set_}.{tag}.json"
    return json.loads(f.read_text()) if f.exists() else None


def pooled(d: Path, sets: list[str], tag: str, kind: str | None = None) -> list[dict]:
    out = []
    for s in sets:
        rs = load(d, s, tag) or []
        out += [r for r in rs if kind is None or r.get("kind") == kind or (kind == "codes" and r.get("kind") in CODES)]
    return out


def cer(rs: list[dict]) -> float | None:
    return summary(per_sample(rs))["cer"] if rs else None


def digit_in_word(rs: list[dict]) -> tuple[int, int]:
    """(texts without a digit whose top reading has a digit inside a word, such texts)"""
    n = bad = 0
    for r in rs:
        if any(c.isdigit() for c in r["text"]):
            continue
        n += 1
        top = r["cands"][0] if r["cands"] else ""
        bad += any(any(c.isdigit() for c in w) and any(c.isalpha() for c in w) for w in WORDISH.findall(top))
    return bad, n


def letter_in_number(rs: list[dict]) -> tuple[int, int]:
    """(texts whose digit runs of 2+ digits read with a letter in them, texts with such runs):
    the reverse confusion, on codes and numbers"""
    n = bad = 0
    for r in rs:
        runs = re.findall(r"\d{2,}", r["text"])
        if not runs:
            continue
        n += 1
        top = r["cands"][0] if r["cands"] else ""
        bad += sum(1 for w in WORDISH.findall(top) if re.search(r"\d", w) and re.search(r"[A-Za-z]", w)) > \
            sum(1 for w in WORDISH.findall(r["text"]) if re.search(r"\d", w) and re.search(r"[A-Za-z]", w))
    return bad, n


def lone_table(rs: list[dict], chars: str) -> dict:
    """per character: lines, top reading exact, read as the other case"""
    out = {}
    for c in chars:
        sel = [r for r in rs if r["text"] == c]
        top = [r["cands"][0] if r["cands"] else "" for r in sel]
        out[c] = {"n": len(sel), "exact": sum(t == c for t in top) / max(len(sel), 1),
                  "other_case": sum(t != c and t.lower() == c.lower() for t in top) / max(len(sel), 1)}
    return out


def boot(a: list[dict], b: list[dict], seed: int = 0) -> dict | None:
    """paired bootstrap of the CER, a − b (lines paired by id and text)"""
    kb = {(r["id"], r["text"]): r for r in b}
    pa = [r for r in a if (r["id"], r["text"]) in kb]
    if not pa:
        return None
    pb = [kb[(r["id"], r["text"])] for r in pa]
    return paired_bootstrap(per_sample(pa), per_sample(pb), seed=seed)
