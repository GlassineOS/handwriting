"""A read-only status page for one training run (gpu/README.md). Standard library only, its
own process: it reads the run directory (train.log, the trainer's output; log.jsonl; the
kit's job.json) and never touches training.

    python -m glassine_train.status RUN_DIR [--port 8686] [--bind 127.0.0.1]

Pages: / (HTML, refreshes itself), /status.json, /download/NAME for the files in DOWNLOADS
only, straight from RUN_DIR (regular files, never a symlink). Nothing else is served: no
listing, no other path. It binds to 127.0.0.1 unless told otherwise; reach it from another
machine through `ssh -L`. A tool for the person training, not part of the recognizer (which
opens no network connection).
"""

import argparse
import html
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

DOWNLOADS = ("best.pt", "last.pt", "recognizer.onnx", "recognizer-int8.onnx", "export.json",
             "args.json", "log.jsonl", "train.log")
STEP = re.compile(r"^step (\d+)/(\d+) loss ([-+0-9.eEinfa]+) ([0-9.]+) step/s lr ([-+0-9.eE]+)")
PLANNED = re.compile(r"planned (\d+) steps")
LOG_TAIL = 4 << 20


def parse_log(text: str) -> dict:
    """The trainer's output (train.py's print lines) as series."""
    out = {"steps": [], "vals": [], "total": None, "rate": None, "lr": None, "step": 0}
    for ln in text.splitlines():
        if m := STEP.match(ln):
            s, t, loss, rate, lr = int(m[1]), int(m[2]), float(m[3]), float(m[4]), float(m[5])
            out["steps"].append((s, loss))
            out.update(step=s, total=t, rate=rate, lr=lr)
        elif ln.startswith("val {"):
            try:
                v = json.loads(ln[4:])
            except ValueError:
                continue
            cer = (v.get("select") or {}).get("cer", v.get("cer"))
            out["vals"].append((int(v.get("step", 0)), float(cer), float(v.get("cer", cer))))
            out["step"] = max(out["step"], int(v.get("step", 0)))
        elif m := PLANNED.search(ln):
            out["total"] = int(m[1])
    return out


_gpu = {"t": 0.0, "v": None}
_gpu_lock = threading.Lock()


def gpu() -> list[dict] | None:
    """nvidia-smi's view (cached 5 s), None without one."""
    with _gpu_lock:
        if time.time() - _gpu["t"] < 5:
            return _gpu["v"]
        _gpu["t"], _gpu["v"] = time.time(), None
        if not shutil.which("nvidia-smi"):
            return None
        q = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
        try:
            r = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return None
        rows = []
        for ln in r.stdout.strip().splitlines():
            f = [x.strip() for x in ln.split(",")]
            if len(f) == 6:
                rows.append(dict(zip(("name", "util_pct", "mem_used_mib", "mem_total_mib", "temp_c", "power_w"), f)))
        _gpu["v"] = rows or None
        return _gpu["v"]


def _read(path: Path, limit: int = LOG_TAIL) -> str:
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def status(run: Path) -> dict:
    text = _read(run / "train.log")
    p = parse_log(text)
    try:
        job = json.loads((run / "job.json").read_text())
    except (OSError, ValueError):
        job = {}
    total, step, rate = p["total"], p["step"], p["rate"]
    eta = (total - step) / rate if total and rate and step < total else None
    best = min(p["vals"], key=lambda v: v[1]) if p["vals"] else None
    return {"run": run.name, "state": job.get("state", "unknown"), "job": job, "step": step, "total": total,
            "loss": p["steps"][-1][1] if p["steps"] else None, "lr": p["lr"], "steps_per_s": rate,
            "eta_s": eta, "dev_cer": p["vals"][-1][1] if p["vals"] else None,
            "best": {"step": best[0], "dev_cer": best[1]} if best else None,
            "loss_series": p["steps"], "cer_series": [(s, c) for s, c, _ in p["vals"]],
            "gpu": gpu(), "log_tail": text.splitlines()[-40:],
            "downloads": [n for n in DOWNLOADS if _regular(run / n)], "time": time.time()}


def svg_plot(points, label, w=560, h=170) -> str:
    if len(points) < 1:
        return f'<p class="dim">{html.escape(label)}: no points yet</p>'
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, x1 = min(xs), max(xs) if max(xs) > min(xs) else min(xs) + 1
    y0, y1 = min(ys), max(ys) if max(ys) > min(ys) else min(ys) + 1e-9
    L, R, T, B = 52, 10, 18, 22

    def X(x):
        return L + (x - x0) / (x1 - x0) * (w - L - R)

    def Y(y):
        return T + (1 - (y - y0) / (y1 - y0)) * (h - T - B)
    pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in points)
    dots = "".join(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="2.5"/>' for x, y in points) if len(points) < 60 else ""
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(label)}">'
            f'<text x="{L}" y="12" class="t">{html.escape(label)}</text>'
            f'<line x1="{L}" y1="{h - B}" x2="{w - R}" y2="{h - B}" class="ax"/>'
            f'<line x1="{L}" y1="{T}" x2="{L}" y2="{h - B}" class="ax"/>'
            f'<text x="{L - 4}" y="{T + 4}" class="n" text-anchor="end">{y1:.3g}</text>'
            f'<text x="{L - 4}" y="{h - B}" class="n" text-anchor="end">{y0:.3g}</text>'
            f'<text x="{L}" y="{h - 6}" class="n">{x0}</text>'
            f'<text x="{w - R}" y="{h - 6}" class="n" text-anchor="end">step {x1}</text>'
            f'<polyline points="{pts}" class="ln"/><g class="pt">{dots}</g></svg>')


def _dur(s):
    if s is None:
        return "–"
    s = int(s)
    return f"{s // 3600} h {s % 3600 // 60:02d} min" if s >= 3600 else f"{s // 60} min {s % 60:02d} s"


def page(st: dict) -> str:
    e = html.escape

    def num(v, f="{:.4g}"):
        return "–" if v is None else f.format(v)
    rows = [("state", e(st["state"])),
            ("step", f'{st["step"]} / {st["total"] or "?"}'),
            ("loss (running)", num(st["loss"])),
            ("dev CER (last)", num(st["dev_cer"])),
            ("best", f'{num(st["best"]["dev_cer"])} at step {st["best"]["step"]}' if st["best"] else "–"),
            ("learning rate", num(st["lr"], "{:.2e}")),
            ("steps/s", num(st["steps_per_s"], "{:.2f}")),
            ("ETA", _dur(st["eta_s"]))]
    for g in st["gpu"] or []:
        rows.append(("GPU", e(f'{g["name"]}: {g["util_pct"]} %, {g["mem_used_mib"]} / {g["mem_total_mib"]} MiB, '
                              f'{g["temp_c"]} °C, {g["power_w"]} W')))
    table = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    dl = "".join(f'<li><a href="/download/{e(n)}">{e(n)}</a></li>' for n in st["downloads"]) or "<li>none yet</li>"
    log = e("\n".join(st["log_tail"]))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="15"><title>Training {e(st["run"])}</title>
<style>
:root{{--bg:#fff;--fg:#1b1b1b;--dim:#6b6b6b;--line:#2b5fad;--rule:#ccc}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#e8e8e8;--dim:#9a9a9a;--line:#7fa8e8;--rule:#444}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;margin:0 auto;max-width:900px;padding:16px}}
table{{border-collapse:collapse}}th{{text-align:left;padding:2px 16px 2px 0;font-weight:500;color:var(--dim)}}
svg{{width:100%;max-width:560px;display:block;margin:12px 0}}.ln{{fill:none;stroke:var(--line);stroke-width:1.6}}
.pt circle{{fill:var(--line)}}.ax{{stroke:var(--rule)}}.t,.n{{fill:var(--dim);font-size:11px}}
pre{{overflow-x:auto;font-size:12px;border-top:1px solid var(--rule);padding-top:8px}}.dim{{color:var(--dim)}}
</style></head><body><h1>{e(st["run"])}</h1><table>{table}</table>
{svg_plot(st["loss_series"], "loss (running mean)")}{svg_plot(st["cer_series"], "dev CER")}
<h2>Downloads</h2><ul>{dl}</ul><h2>Log</h2><pre>{log}</pre>
<p class="dim">Read-only; refreshes every 15 s. <a href="/status.json">status.json</a></p></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "glassine-train-status"
    sys_version = ""
    run: Path = Path(".")

    def log_message(self, fmt, *args):  # no access log
        pass

    def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            return self._send(200, page(status(self.run)).encode(), "text/html; charset=utf-8")
        if path == "/status.json":
            return self._send(200, json.dumps(status(self.run)).encode(), "application/json")
        if path.startswith("/download/"):
            name = unquote(path[len("/download/"):])
            if name in DOWNLOADS:
                return self._file(self.run / name, name)
        return self._send(404, b"not found\n")

    def _file(self, p: Path, name: str):
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return self._send(404, b"not found\n")
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return self._send(404, b"not found\n")
        with os.fdopen(fd, "rb") as fh:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(st.st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                shutil.copyfileobj(fh, self.wfile, 1 << 20)


def serve(run: Path, port: int = 8686, bind: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = type("RunHandler", (Handler,), {"run": run})
    srv = ThreadingHTTPServer((bind, port), handler)
    srv.daemon_threads = True
    return srv


def _loopback(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return addr == "localhost"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m glassine_train.status")
    ap.add_argument("run", type=Path, help="the run directory (build/runs/RUN)")
    ap.add_argument("--port", type=int, default=8686)
    ap.add_argument("--bind", default="127.0.0.1", help="address to listen on; anything but loopback "
                    "lets everyone who reaches it read the run and download its model")
    ap.add_argument("--reachable-on", help="where the port is really reachable (a container's published "
                    "address, when it listens on 0.0.0.0 inside); the warning goes by this")
    a = ap.parse_args(argv)
    shown = a.reachable_on or a.bind
    if not _loopback(shown):
        print(f"status: WARNING: reachable on {shown}, not loopback: anyone who reaches this port can "
              "read the run and download its checkpoints (no password)", file=sys.stderr, flush=True)
    srv = serve(a.run, a.port, a.bind)
    print(f"status: listening on {a.bind}:{srv.server_address[1]} for {a.run.name}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
