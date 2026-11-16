# SPDX-License-Identifier: GPL-2.0-or-later
"""Who is calling (spec 004 FR-001): the operating system says, not the app. A peer on the
service's socket is its process (SO_PEERCRED): another user is refused; a sandboxed app is
its sandbox's application id (Flatpak: /.flatpak-info in its root); any other process is
`host.` and the program it runs. A name an app gives itself never counts."""
import configparser
import os
import re
import socket
import struct

INTERPRETERS = re.compile(r"^(python[0-9.]*|perl|ruby|node|sh|bash|dash|lua[0-9.]*|gjs)$")
NAME = re.compile(r"[^A-Za-z0-9._-]")


def clean(name):
    """an identity as the recognizer takes a client name (letters, digits, . _ -, up to 63)"""
    return NAME.sub("_", name)[:63] or "unknown"


def peer(sock):
    """(pid, uid) of a unix socket's peer"""
    pid, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return pid, uid


def sandbox_app(pid, proc="/proc"):
    """the application id of a sandboxed process, None for one that isn't"""
    info = os.path.join(proc, str(pid), "root", ".flatpak-info")
    try:
        with open(info, encoding="utf-8") as f:
            c = configparser.ConfigParser(interpolation=None)
            c.read_file(f)
        return c.get("Application", "name", fallback=None) or "unknown"
    except FileNotFoundError:
        return None
    except (OSError, configparser.Error):
        return "unknown"            # a sandbox we can't read: no name it could pick


def host_app(pid, proc="/proc"):
    """the program an unsandboxed process runs: its executable's name, for an interpreter
    the script's"""
    try:
        exe = os.path.basename(os.readlink(os.path.join(proc, str(pid), "exe")))
    except OSError:
        exe = "unknown"
    if INTERPRETERS.match(exe):
        try:
            with open(os.path.join(proc, str(pid), "cmdline"), "rb") as f:
                args = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
            script = next((a for a in args[1:] if not a.startswith("-")), None)
            if script:
                exe = os.path.basename(script)
                if exe.endswith(".py"):
                    exe = exe[:-3]
        except OSError:
            pass
    return "host." + exe


PROC = os.environ.get("GLASSINE_HWSERVICE_PROC", "/proc")     # tests: a directory laid out like /proc


def identity(pid, proc=None):
    """(app id, sandboxed)"""
    proc = proc or PROC
    s = sandbox_app(pid, proc)
    if s is not None:
        return clean(s), True
    return clean(host_app(pid, proc)), False
