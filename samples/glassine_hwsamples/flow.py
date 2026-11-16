# SPDX-License-Identifier: GPL-2.0-or-later
"""The writing session (spec 002 Scenarios 1, 2, 5; FR-007 to FR-012): a prompt is shown,
the person writes it, the sample is checked and kept (or shown as failing the check), and
Redo, Skip and Discard act on the shown sample until the next one is kept."""

import collections
import dataclasses
import json
import random
import os
import secrets
import threading
import time
from dataclasses import dataclass, field

from . import prompts as P
from .client import Error

SESSION = "w"


def state_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "glassine", "handwriting-samples", "state.json")


class State:
    """the app's place: the chosen set, the prompts shown in each set's round, the current
    prompt, donating on or off. No samples: those are the recognizer's."""

    def __init__(self, path=None):
        self.path = path or state_path()
        self.set = P.AUTO
        self.used = {}
        self.current = None          # (set, text) of the prompt on screen
        self.donating = False
        self.lang = "en"
        self.introduced = False      # the first-start introduction was shown (or not needed)
        self.best_used = {}          # Your handwriting's prompts shown in this round
        self.picks = {}              # {day: picks made}, for "N picks today"
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.set = d.get("set", P.AUTO)
            self.used = {k: list(v) for k, v in d.get("used", {}).items()}
            cur = d.get("current")
            self.current = tuple(cur) if cur else None
            self.donating = bool(d.get("donating", False))
            self.lang = d.get("lang", "en")
            self.introduced = bool(d.get("introduced", False))
            self.best_used = {k: list(v) for k, v in d.get("best_used", {}).items()}
            self.picks = {k: int(v) for k, v in d.get("picks", {}).items()}
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"set": self.set, "used": self.used, "current": list(self.current) if self.current else None,
                       "donating": self.donating, "lang": self.lang, "introduced": self.introduced,
                       "best_used": self.best_used, "picks": dict(list(self.picks.items())[-7:])}, f)
        os.replace(tmp, self.path)


@dataclass
class Shown:
    """the sample on screen after writing: kept, or waiting for the person (the check failed)"""
    prompt: P.Prompt
    strokes: list
    units: str
    area: tuple
    reading: list                    # [(text, score)] at the time of writing
    status: str                      # kept, check, not-kept
    sample_id: str = None            # in the person's samples
    donated_id: str = None           # in the donation
    exact: bool = False
    fit: float = None
    why: str = ""                    # not-kept: why (learning off, ...)
    advanced: bool = False           # the next prompt was shown before the reading
    notes: list = field(default_factory=list)


class Writer:
    def __init__(self, rec, cli, state, sets=None, coverage=None):
        # Next picks the next prompt in the UI thread while the reading of the last one runs
        # in the worker: the prompt state (used, current, coverage) changes under this lock,
        # never held across a recognizer call (Next would wait for it)
        self.lock = threading.RLock()
        self.rec, self.cli, self.state = rec, cli, state
        self.sets = sets if sets is not None else P.load(state.lang)
        self.cov = coverage or P.Coverage()
        self.shown = None
        self.prompt = None
        self.kept_now = 0
        self.counted = {}               # {id: (text, read)}: the samples in the coverage
        # samples whose check failed, waiting for the person (Keep it, Redo, Skip): shown
        # one after the other, oldest first, before any sample read after them, so none
        # goes unseen while the person writes on
        self.checks = []
        # Your handwriting (spec 003): None, or the careful writing's place: its step
        # ("careful", "quick", then "pair" for the careful half of a pair), the pair's link,
        # how many were written
        self.best = None
        self.prompt = self._restore() or self.next_prompt()

    # ------------------------------------------------------------ prompts

    def _restore(self):
        cur = self.state.current
        if not cur:
            return None
        for p in self.sets.get(cur[0], []):
            if p.text == cur[1]:
                return p
        return None

    def next_prompt(self):
        if self.best is not None:
            return self._best_next()
        with self.lock:
            p = P.choose(self.sets, self.state.set, self.cov, self.state.used)
            self.prompt = p
            self.state.current = (p.set, p.text) if p else None
            self.state.save()
            return p

    def choose_set(self, name):
        self.state.set = name
        self.shown = self.checks[0] if self.checks else None
        return self.next_prompt()

    # ------------------------------------------------------------ Your handwriting

    QUICK_EVERY = 3                     # every third careful word is written quickly first

    def start_best(self, chars=None):
        """Write it your best: words written with care, now and then once quickly first;
        with chars (the font's missing characters), each of them on its own, carefully, in
        turn"""
        with self.lock:
            self.best = {"step": "careful", "pair": "", "n": 0, "chars": list(chars or [])}
            self.shown = None
            return self._best_next()

    def start_list(self, prompts):
        """the character sheet's Write more: these prompts, each written once with care, in
        turn; then none"""
        with self.lock:
            self.best = {"step": "careful", "pair": "", "n": 0, "chars": [], "list": list(prompts)}
            self.shown = None
            return self._best_next()

    def stop_best(self):
        with self.lock:
            self.best = None
            self.shown = self.checks[0] if self.checks else None
            return self.next_prompt()

    def _best_next(self, same=None):
        with self.lock:
            b = self.best
            if same is not None:
                base = same
            elif "list" in b:
                base = b["list"][b["n"]] if b["n"] < len(b["list"]) else None
                b["step"], b["pair"] = "careful", ""
            elif b.get("chars"):
                c = b["chars"][b["n"] % len(b["chars"])]
                base = P.Prompt(c, "letters", self.state.lang)
                b["step"], b["pair"] = "careful", ""
            else:
                base = P.choose(self.sets, "words", self.cov, self.state.best_used)
                b["step"] = "quick" if b["n"] % self.QUICK_EVERY == self.QUICK_EVERY - 1 else "careful"
                b["pair"] = secrets.token_hex(8) if b["step"] == "quick" else ""
            if base is None:
                self.prompt = None
                return None
            written = "quick" if b["step"] == "quick" else "careful"
            self.prompt = dataclasses.replace(base, written=written, pair=b["pair"])
            return self.prompt

    def _best_advance(self, p):
        with self.lock:
            b = self.best
            if b["step"] == "quick":
                b["step"] = "pair"              # the same word again, slowly
                self._best_next(same=dataclasses.replace(p, written="", pair=""))
                return
            P.mark_used(self.state.best_used, p)
            b["n"] += 1
            self.state.save()
            self._best_next()

    def redo_quick(self):
        """the careful half's Redo: the quick one goes, the pair starts again"""
        sh = self.shown
        with self.lock:
            b = self.best
            if not b or b["step"] != "pair":
                return self.prompt
        if sh and sh.prompt.written == "quick":
            self._take_back(sh)
        with self.lock:
            self._resolved(sh) if sh else None
            b["step"] = "quick"
            b["pair"] = secrets.token_hex(8)
            base = dataclasses.replace(self.prompt, written="", pair="")
            self.prompt = dataclasses.replace(base, written="quick", pair=b["pair"])
            return self.prompt

    def reload_sets(self):
        self.sets = P.load(self.state.lang)

    # ------------------------------------------------------------ writing

    def take(self):
        """Next: the prompt on screen is written; the next one shows at once, the reading
        of the ink (submit with this prompt) follows"""
        with self.lock:
            p = self.prompt
            if p:
                self._advance(p)
            return p

    def submit(self, strokes, units, area, prompt=None):
        """the person wrote the prompt (paused or tapped Next): read, check, keep;
        prompt: the one handed out by take() (already followed by the next)"""
        advanced = prompt is not None
        p = prompt or self.prompt
        if not p or not strokes:
            return None
        self.rec.write(SESSION, p.lang, units, area, strokes)
        try:
            reading = self.rec.read(SESSION)
            sh = Shown(p, strokes, units, area, reading, "kept", advanced=advanced)
            self._store(sh, keep=False)
        finally:
            self.rec.end(SESSION)
        with self.lock:
            if sh.status == "check":
                self.checks.append(sh)
            self.shown = self.checks[0] if self.checks else sh
            if sh.status != "check" and not advanced:
                self._advance(p)
        return sh

    def _resolved(self, sh, then=None):
        """the person acted on sh: the next waiting check shows, else then"""
        if sh in self.checks:
            self.checks.remove(sh)
        self.shown = self.checks[0] if self.checks else then

    def keep_anyway(self):
        """the check failed and the person says they wrote the prompt"""
        sh = self.shown
        if not sh or sh.status != "check":
            return sh
        self.rec.write(SESSION, sh.prompt.lang, sh.units, sh.area, sh.strokes)
        try:
            self._store(sh, keep=True)
        finally:
            self.rec.end(SESSION)
        with self.lock:
            if not sh.advanced:
                self._advance(sh.prompt)
            self._resolved(sh, sh)
        return sh

    def _store(self, sh, keep):
        p = sh.prompt
        if p.written or p.pair:
            self.rec.mark(SESSION, p.written, p.pair)
        r = self.rec.confirm_prompted(SESSION, p.text, keep=keep)
        if r[0] == "ok":
            sh.sample_id, sh.exact = r[1], r[2] == "exact"
        elif r[1] == "fit":
            sh.status, sh.fit = "check", r[2]
            return
        else:
            sh.why = r[1]               # learning off, ...: not in the person's samples
        # a donation takes only what was written to a shipped prompt while donating is on
        if self.state.donating and p.shipped:
            d = self.rec.donate(SESSION, p.text, keep=keep)
            if d[0] == "ok":
                sh.donated_id = d[2]
            elif d[1] == "fit":
                sh.status, sh.fit = "check", d[2]
                if sh.sample_id:        # both or neither: take the stored one back
                    self.rec.forget(sh.sample_id)
                    sh.sample_id = None
                return
            else:
                sh.notes.append("donation: " + d[1])
        sh.status = "kept" if sh.sample_id or sh.donated_id else "not-kept"
        if sh.sample_id:
            with self.lock:
                pair = (p.text, sh.reading[0][0] if sh.reading else None)
                if sh.sample_id not in self.counted:
                    self.cov.add(*pair)
                    self.counted[sh.sample_id] = pair
                self.kept_now += 1

    def _advance(self, p):
        if self.best is not None and p.written:
            return self._best_advance(p)
        with self.lock:
            P.mark_used(self.state.used, p)
            self.next_prompt()

    def _take_back(self, sh):
        for i in {sh.sample_id, sh.donated_id} - {None}:
            self.rec.forget(i)
        if sh.sample_id:
            with self.lock:
                self.kept_now -= 1
                self._uncount(sh.sample_id)
        sh.sample_id = sh.donated_id = None

    def redo(self):
        """the shown sample goes; its prompt is written again now"""
        sh = self.shown
        if not sh:
            return self.prompt
        self._take_back(sh)
        with self.lock:
            used = self.state.used.get(sh.prompt.set, [])
            if sh.prompt.text in used:
                used.remove(sh.prompt.text)
            self._resolved(sh)
            self.prompt = sh.prompt
            self.state.current = (sh.prompt.set, sh.prompt.text)
            self.state.save()
            return self.prompt

    def discard(self):
        """the shown sample goes; its prompt may come again in this round"""
        sh = self.shown
        if not sh:
            return self.prompt
        self._take_back(sh)
        with self.lock:
            used = self.state.used.get(sh.prompt.set, [])
            if sh.prompt.text in used:
                used.remove(sh.prompt.text)
            self._resolved(sh)
            if sh.status == "check":
                if not sh.advanced:
                    self._advance(sh.prompt)
                used = self.state.used.get(sh.prompt.set, [])
                if sh.prompt.text in used:
                    used.remove(sh.prompt.text)
            self.state.save()
            return self.prompt

    def skip(self):
        """nothing is kept: the shown sample goes (if any), and the prompt isn't asked again
        in this round"""
        sh = self.shown
        if sh:
            self._take_back(sh)
            with self.lock:
                self._resolved(sh)
                if sh.status == "check" and not sh.advanced:
                    self._advance(sh.prompt)
                self.state.save()
                return self.prompt
        if self.prompt:
            self._advance(self.prompt)
        return self.prompt

    # ------------------------------------------------------------ counts and coverage

    def load_coverage(self, rows=None):
        """coverage from this app's samples (their text and the reading at the time of
        writing, from the recognizer's listing: no strokes are read)"""
        return self.set_coverage(self.rec.list() if rows is None else rows)

    def set_coverage(self, rows):
        """rows: rec.list(); only the samples that came or went since the last listing are
        counted in or out, so a listing of thousands costs what changed"""
        with self.lock:
            now = {r[0]: (r[4], r[5]) for r in rows}
            for sid in [s for s in self.counted if s not in now]:
                self._uncount(sid)
            for sid, pair in now.items():
                if sid not in self.counted:
                    self.cov.add(*pair)
                    self.counted[sid] = pair
            return self.cov

    def _uncount(self, sid):
        pair = self.counted.pop(sid, None)
        if pair:
            self.cov.remove(*pair)

    def forgotten(self, sid=None):
        """a sample deleted elsewhere (None: all of them): not counted from here on"""
        with self.lock:
            for s in list(self.counted) if sid is None else [sid]:
                self._uncount(s)

    def counts(self, now=None, rows=None):
        """(today, total) of this app's samples; rows: cli.samples() when the caller has them"""
        now = time.time() if now is None else now
        lt = time.localtime(now)
        day0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        rows = self.cli.samples() if rows is None else rows
        return sum(1 for r in rows if r[1] >= day0), len(rows)


def needs_intro(state, total):
    """the introduction comes once, on the first start (spec 002 FR-012). A store that
    already has samples (written before, or imported) has had its first start: it is
    marked so, and the introduction never comes"""
    if state.introduced:
        return False
    if total > 0:
        state.introduced = True
        state.save()
        return False
    return True


def day_start(now=None):
    lt = time.localtime(time.time() if now is None else now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def today(now=None):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


# ---------------------------------------------------------------- Which one is nicer?

PICK_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def pick_candidates(rows, prefs, rng=None):
    """the characters to compare, best first (spec 003 FR-004: those with the fewest
    preferences, among characters at least two samples have), each with the samples that
    have it: [(char, [(id, k)])], k the character's place in the sample's text. From the
    listing alone: no strokes are read. rows: [(id, time, text)]; prefs: [(chosen, other,
    cchar, ochar)]"""
    rng = rng or random.Random()
    text = {r[0]: r[2] for r in rows}
    done = collections.Counter()
    for c, o, cc, oc in prefs:
        if cc is not None and c in text and cc < len(text[c]):
            done[text[c][cc]] += 1
    have = collections.defaultdict(list)
    for sid, _, t in rows:
        for k, ch in enumerate(t):
            if ch in PICK_CHARS:
                have[ch].append((sid, k))
    out = []
    for ch, places in have.items():
        ids = {s for s, _ in places}
        if len(ids) < 2:
            continue
        first = {}
        for s, k in places:
            first.setdefault(s, k)
        out.append((done[ch], rng.random(), ch, list(first.items())))
    out.sort()
    return [(ch, places) for _, _, ch, places in out]


def crop(sample, k):
    """the strokes of the character at k (by its span), or [] when it isn't known"""
    spans = sample.get("spans") or []
    st = sample.get("strokes") or []
    if k >= len(spans) or not spans[k]:
        return []
    return [st[i] for i in spans[k] if i < len(st)]


def load_pick(rec, rows, prefs, rng=None, tries=6):
    """two of the person's versions of one character: {"a": (id, strokes, k), "b": ...,
    "char": c, "chosen": None}, or None when there is nothing to choose from. Fetches only
    the samples it shows (and a few more when a span isn't known)"""
    rng = rng or random.Random()
    fetched = 0
    for ch, places in pick_candidates(rows, prefs, rng)[:8]:
        rng.shuffle(places)
        got = []
        for sid, k in places:
            if fetched >= tries:
                return None
            fetched += 1
            try:
                st = crop(rec.sample(sid), k)
            except Exception:
                st = []
            if st:
                got.append((sid, st, k))
            if len(got) == 2:
                return {"a": got[0], "b": got[1], "char": ch, "chosen": None}
    return None


def char_prompts(c, sets, lang, n):
    """the character sheet's Write more for c: n prompts, the character on its own and in a
    word in turn (a word of the shipped ones that has it; a capital: one that starts with
    it), each written with care"""
    words = [p.text for name in ("words", "digits") for p in sets.get(name, []) if c in p.text and " " not in p.text]
    if not words and c.isalpha():
        low = c.lower()
        words = [p.text[0].upper() + p.text[1:] for p in sets.get("words", []) if p.text.startswith(low)]
    words = sorted(set(w for w in words if w != c), key=lambda w: (len(w), w))
    out = []
    for i in range(n):
        if i % 2 and words:
            w = words[(i // 2) % len(words)]
            out.append(P.Prompt(w, "words", lang, written="careful"))
        else:
            out.append(P.Prompt(c, "letters", lang, written="careful"))
    return out
