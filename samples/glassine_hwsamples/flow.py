# SPDX-License-Identifier: GPL-2.0-or-later
"""The writing session (spec 002 Scenarios 1, 2, 5; FR-007 to FR-012): a prompt is shown,
the person writes it, the sample is checked and kept (or shown as failing the check), and
Redo, Skip and Discard act on the shown sample until the next one is kept."""

import json
import os
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
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.set = d.get("set", P.AUTO)
            self.used = {k: list(v) for k, v in d.get("used", {}).items()}
            cur = d.get("current")
            self.current = tuple(cur) if cur else None
            self.donating = bool(d.get("donating", False))
            self.lang = d.get("lang", "en")
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"set": self.set, "used": self.used, "current": list(self.current) if self.current else None,
                       "donating": self.donating, "lang": self.lang}, f)
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
        with self.lock:
            p = P.choose(self.sets, self.state.set, self.cov, self.state.used)
            self.prompt = p
            self.state.current = (p.set, p.text) if p else None
            self.state.save()
            return p

    def choose_set(self, name):
        self.state.set = name
        self.shown = None
        return self.next_prompt()

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
        self.shown = None
        self.rec.write(SESSION, p.lang, units, area, strokes)
        try:
            reading = self.rec.read(SESSION)
            sh = Shown(p, strokes, units, area, reading, "kept", advanced=advanced)
            self._store(sh, keep=False)
        finally:
            self.rec.end(SESSION)
        with self.lock:
            self.shown = sh
            if sh.status != "check" and not advanced:
                self._advance(p)
        return sh

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
        if not sh.advanced:
            self._advance(sh.prompt)
        return sh

    def _store(self, sh, keep):
        p = sh.prompt
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
                self.cov.add(p.text, sh.reading[0][0] if sh.reading else "")
                self.kept_now += 1

    def _advance(self, p):
        with self.lock:
            P.mark_used(self.state.used, p)
            self.next_prompt()

    def _take_back(self, sh):
        for i in {sh.sample_id, sh.donated_id} - {None}:
            self.rec.forget(i)
        if sh.sample_id:
            with self.lock:
                self.kept_now -= 1
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
            self.shown = None
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
            self.shown = None
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
                self.shown = None
                if sh.status == "check" and not sh.advanced:
                    self._advance(sh.prompt)
                self.state.save()
                return self.prompt
        if self.prompt:
            self._advance(self.prompt)
        return self.prompt

    # ------------------------------------------------------------ counts and coverage

    def load_coverage(self, cache=None):
        """coverage from this app's samples (their text and the reading at the time of
        writing); cache: {id: (text, read)} kept by the caller between calls"""
        cache = {} if cache is None else cache
        cov = P.Coverage()
        for sid in self.rec.sample_ids():
            if sid not in cache:
                try:
                    s = self.rec.sample(sid)
                except Error:
                    continue
                rd = s.get("reading") or []
                cache[sid] = (s.get("text", ""), rd[0]["text"] if rd else None)
            cov.add(*cache[sid])
        self.cov = cov
        return cov

    def counts(self, now=None):
        """(today, total) of this app's samples"""
        now = time.time() if now is None else now
        lt = time.localtime(now)
        day0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        rows = self.cli.samples()
        return sum(1 for r in rows if r[1] >= day0), len(rows)
