# SPDX-License-Identifier: GPL-2.0-or-later
"""The density of an output of unknown size (spec 004 FR-017): when the compositor doesn't
know an output's physical size, the person sets its points per millimetre once in Settings
(`output.set`, docs/service.md), and it is kept for that output, by the name the compositor
gives it. Kept in $XDG_CONFIG_HOME/glassine/handwriting-outputs.json. Until it is set the
units stay the pen's own (`device`) and the size prior is applied at the default density
(area.DEFAULT_PX_PER_MM)."""
import json
import math
import os


def path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "glassine", "handwriting-outputs.json")


def load():
    """output name -> points per millimetre"""
    try:
        with open(path(), encoding="utf-8") as f:
            d = json.load(f)
        out = d.get("outputs", {}) if isinstance(d, dict) else {}
        return {str(k): float(v) for k, v in out.items()
                if isinstance(v, (int, float)) and math.isfinite(v) and v > 0}
    except (OSError, ValueError, AttributeError):
        return {}


def density(name):
    """the density the person set for an output, None when they haven't"""
    return load().get(name) if name else None


def set_density(name, value):
    """value > 0 (points per millimetre), or None (back to unknown)"""
    if not isinstance(name, str) or not name or len(name) > 255:
        raise ValueError("output")
    if value is not None and not (isinstance(value, (int, float)) and math.isfinite(value) and 0 < value < 1000):
        raise ValueError("density")
    d = load()
    if value is None:
        d.pop(name, None)
    else:
        d[name] = float(value)
    f = path()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    tmp = f + ".tmp"
    with open(tmp, "w", encoding="utf-8") as o:
        json.dump({"version": 1, "outputs": d}, o, indent=1, sort_keys=True)
    os.replace(tmp, f)


def units(u, name):
    """the compositor's units u, `device` made `device:PER_MM` with the person's density for
    the output when they set one"""
    if u == "device":
        d = density(name)
        if d:
            return "device:%s" % (repr(d) if not float(d).is_integer() else int(d))
    return u
