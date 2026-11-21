# SPDX-License-Identifier: GPL-2.0-or-later
"""glassine-handwriting-service: handwriting for apps (spec 004), one per session.

  glassine-handwriting-service [--socket PATH] [--pen-fd N]

The service interface on PATH (default $XDG_RUNTIME_DIR/glassine-handwriting/service); with
--pen-fd, the compositor's channel on that inherited socket (the compositor starts the
service with one end of a socket pair: nobody else can reach it). It leaves when the
compositor's channel closes; without one, on SIGTERM. One recognizer process (engine) serves
both and every app."""
import argparse
import asyncio
import glob
import os
import signal
import socket
import sys

from . import api, pen
from .engine import Engine


def data_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "glassine", "handwriting")


def languages():
    d = os.environ.get("GLASSINE_HANDWRITING_MODELS", "/usr/share/glassine/handwriting")
    return sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(d, "*", "recognizer.ghm")))


def features():
    langs = languages()
    return {"neaten": bool(langs), "text": bool(langs), "regions": True,
            "letters": os.path.exists(os.path.join(data_dir(), "letters", "model")),
            "languages": langs}


def default_socket():
    run = os.environ.get("XDG_RUNTIME_DIR")
    if not run:
        sys.exit("glassine-handwriting-service: no XDG_RUNTIME_DIR")
    return os.path.join(run, "glassine-handwriting", "service")


async def amain(a):
    engine = Engine()
    p = pen.Pen(engine, features)
    service = api.Api(engine, p, features)
    path = a.socket or default_socket()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    srv = None
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(path)             # another service is there: it keeps the service socket
        print("glassine-handwriting-service: %s is in use" % path, file=sys.stderr)
    except OSError:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        srv = await asyncio.start_unix_server(service.serve, path, limit=1 << 24)
        os.chmod(path, 0o600)
    finally:
        probe.close()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sg in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sg, stop.set)
    if a.pen_fd is not None:
        sock = socket.socket(fileno=a.pen_fd)
        r, w = await asyncio.open_unix_connection(sock=sock, limit=1 << 24)

        async def run_pen():
            await p.serve(r, w)
            stop.set()                  # the compositor is gone: so is its session
        asyncio.ensure_future(run_pen())
    elif srv is None:
        return 1
    await stop.wait()
    if srv:
        srv.close()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    await engine.close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="glassine-handwriting-service")
    ap.add_argument("--socket")
    ap.add_argument("--pen-fd", type=int)
    a = ap.parse_args(argv)
    return asyncio.run(amain(a))
