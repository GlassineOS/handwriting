# SPDX-License-Identifier: GPL-2.0-or-later
"""Other writers (spec 002 FR-014 to FR-025): the list of guest writers the owner keeps, and
a writer's session. The list holds only names and the random ids of the recognizer's stores;
a name is a label here and in the header, and never goes to the recognizer, a sample, a file
name or a log. A session names its store in every recognizer process and command it starts."""

import json
import os

from . import client, flow, prompts as P

MAX_NAME = 40


def list_path():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "glassine", "handwriting-samples", "writers.json")


def clean(name):
    """a name as kept: one line, spaces at the ends dropped"""
    return " ".join((name or "").split())


class Writers:
    """[{id, name}] in the order added; directory 0700, file 0600 (FR-025)"""

    def __init__(self, path=None):
        self.path = path or list_path()
        self.items = []
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.items = [{"id": w["id"], "name": w["name"]} for w in d.get("writers", [])
                          if client.WRITER.fullmatch(str(w.get("id", ""))) and isinstance(w.get("name"), str)]
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            pass

    def save(self):
        d = os.path.dirname(self.path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"writers": self.items}, f)
        os.replace(tmp, self.path)

    def name(self, wid):
        return next((w["name"] for w in self.items if w["id"] == wid), None)

    def problem(self, name, wid=None):
        """why a name can't be used (None: it can): empty, long, taken (FR-014: 1 to 40
        characters, unique in the list; wid: the writer being renamed)"""
        name = clean(name)
        if not name:
            return "empty"
        if len(name) > MAX_NAME:
            return "long"
        if any(w["name"].casefold() == name.casefold() and w["id"] != wid for w in self.items):
            return "taken"
        return None

    def add(self, wid, name):
        if self.problem(name):
            raise ValueError(self.problem(name))
        client.writer_args(wid)
        self.items.append({"id": wid, "name": clean(name)})
        self.save()

    def rename(self, wid, name):
        if self.problem(name, wid):
            raise ValueError(self.problem(name, wid))
        for w in self.items:
            if w["id"] == wid:
                w["name"] = clean(name)
        self.save()

    def remove(self, wid):
        self.items = [w for w in self.items if w["id"] != wid]
        self.save()


class Session:
    """one writer's session: the owner's (wid None) or a guest's. Its recognizer process, its
    commands, its place in the prompts; every request goes to its store alone (FR-016). kw:
    the recognizer's binary and environment (the tests' own)"""

    def __init__(self, wid=None, name=None, rec=None, cli=None, state=None, writer=True, **kw):
        self.wid, self.name = wid, name
        self.rec = rec or client.Recognizer(writer=wid, **kw)
        self.cli = cli or client.Cli(writer=wid, **kw)
        self.state = state or flow.State(flow.state_path(wid))
        if wid and self.state.set == P.OWN:
            self.state.set = P.AUTO
        self.writer = flow.Writer(self.rec, self.cli, self.state, own_words=not wid) if writer else None

    @property
    def guest(self):
        return self.wid is not None

    def close(self):
        self.rec.close(end=True)


def forget_place(wid):
    """a removed writer's place in the prompts goes with them (FR-023)"""
    try:
        os.unlink(flow.state_path(wid))
    except FileNotFoundError:
        pass
