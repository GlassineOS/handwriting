# SPDX-License-Identifier: GPL-2.0-or-later
"""Per app, what the person allows (spec 004 Scenario 4): `learn` (its confirmations keep
samples) and `samples` (it may see and delete the samples kept through it). Each is
`allowed`, `refused` or unasked (not in the file). Reading ink and neatening need neither.
Kept in $XDG_CONFIG_HOME/glassine/handwriting-apps.json; only the person's own tools (an
unsandboxed caller: Settings, the samples app) change it."""
import json
import os

KINDS = ("learn", "samples")
STATES = ("allowed", "refused")
# the system's own handwriting clients: the samples app keeps samples by design
BUILTIN = {"host.glassine-handwriting-samples": {"learn": "allowed", "samples": "allowed"}}


def path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "glassine", "handwriting-apps.json")


def load():
    try:
        with open(path(), encoding="utf-8") as f:
            d = json.load(f)
        apps = d.get("apps", {}) if isinstance(d, dict) else {}
        return {a: {k: v for k, v in p.items() if k in KINDS and v in STATES}
                for a, p in apps.items() if isinstance(p, dict)}
    except (OSError, ValueError):
        return {}


UNNAMED = "unknown"     # a sandbox without an application id: never allowed, it could be any app


def state(app, kind):
    """allowed, refused or unasked"""
    if app == UNNAMED:
        return "refused"
    return load().get(app, {}).get(kind) or BUILTIN.get(app, {}).get(kind) or "unasked"


def all_states(app):
    return {k: state(app, k) for k in KINDS}


def set_state(app, kind, value):
    """value: allowed, refused, or None (back to unasked)"""
    if kind not in KINDS or (value is not None and value not in STATES) or app == UNNAMED:
        raise ValueError(kind)
    apps = load()
    p = apps.setdefault(app, {})
    if value is None:
        p.pop(kind, None)
    else:
        p[kind] = value
    if not p:
        apps.pop(app)
    f = path()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    tmp = f + ".tmp"
    with open(tmp, "w", encoding="utf-8") as o:
        json.dump({"version": 1, "apps": apps}, o, indent=1, sort_keys=True)
    os.replace(tmp, f)
