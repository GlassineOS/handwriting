"""The GPU kit's job runner, the container's entry point (gpu/README.md).

    python -m glassine_train.job train [--bundle DIR] [options] -- TRAIN ARGS
    python -m glassine_train.job selftest [--device cuda]

`train` runs `glassine-train train` with the bundle's arguments followed by TRAIN ARGS (a
later option wins), niced, its output kept in build/runs/RUN/train.log; then `export` on
the CPU (ONNX of best.pt, checked on the run's --val set). A status page (status.py, its own
process) serves the run meanwhile and for --linger seconds after. job.json in the run
directory says where the job is. `selftest` trains a few steps on synthetic ink, reads the
checkpoint back with the GPU hidden and exports it: a pass/fail of the whole path before
any real data is copied over.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_child: subprocess.Popen | None = None
_stopped = False


def _on_term(signum, frame):
    global _stopped
    _stopped = True
    if _child and _child.poll() is None:
        _child.terminate()


def _write_job(run: Path, **kw):
    path = run / "job.json"
    try:
        job = json.loads(path.read_text())
    except (OSError, ValueError):
        job = {}
    job.update(kw, updated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=1))
    tmp.replace(path)


def _tee(cmd: list[str], log: Path, nice: int = 0, cwd=None, env=None) -> int:
    """Run cmd, its output line by line to stdout and appended to log; the exit status."""
    global _child
    def pre():
        if nice:
            os.nice(nice)
    with open(log, "a", buffering=1) as fh:
        _child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  bufsize=1, preexec_fn=pre, cwd=cwd, env=env)
        for ln in _child.stdout:
            sys.stdout.write(ln)
            sys.stdout.flush()
            fh.write(ln)
        rc = _child.wait()
    _child = None
    return rc


def _train_ns(args: list[str]):
    from .cli import _train_args
    return _train_args(argparse.ArgumentParser(prog="glassine-train train")).parse_args(args)


def cmd_train(a, rest: list[str]) -> int:
    args = []
    if a.bundle:
        args = json.loads((Path(a.bundle) / "manifest.json").read_text())["train_args"]
    args += rest
    if a.bench:  # a few hundred steps, the schedule fixed, evaluation only at the end
        args += ["--run", time.strftime("bench-%Y%m%d-%H%M%S"), "--minutes", str(a.bench),
                 "--steps-per-s", str(a.bench_steps / (a.bench * 60)), "--eval-every", "1000000000"]
    ns = _train_ns(args)
    run = Path("build/runs") / ns.run
    run.mkdir(parents=True, exist_ok=True)
    log = run / "train.log"
    status = None
    if a.status_port:
        status = subprocess.Popen([sys.executable, "-m", "glassine_train.status", str(run), "--port",
                                   str(a.status_port), "--bind", a.status_bind]
                                  + (["--reachable-on", a.status_reachable] if a.status_reachable else []))
    _write_job(run, state="training", started=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               args=args, bench=bool(a.bench))
    t0 = time.time()
    rc = _tee([sys.executable, "-m", "glassine_train.cli", "train", *args], log, a.nice)
    state = "stopped" if _stopped else ("trained" if rc == 0 else "failed")
    _write_job(run, state=state, train_exit=rc, train_s=round(time.time() - t0))
    if a.bench and rc == 0:
        from .status import parse_log
        p = parse_log(log.read_text())
        print(f"BENCH {p['step']} steps; {p['rate']} steps/s (the trainer's mean over the run, "
              f"device {ns.device}, batch {ns.batch}, workers {ns.workers}, threads {ns.threads})", flush=True)
    if rc == 0 and not _stopped and a.export and not a.bench and (run / "best.pt").exists():
        _write_job(run, state="exporting")
        rc = _tee([sys.executable, "-m", "glassine_train.cli", "export", "--run", ns.run, "--data", ns.val],
                  log, a.nice)
        state = "stopped" if _stopped else ("done" if rc == 0 else "export failed")
        _write_job(run, state=state, export_exit=rc)
    if status:
        end = time.time() + a.linger
        if a.linger > 0 and not _stopped:
            print(f"job: {state}; the status page stays up {a.linger} s (run.sh stop ends it)", flush=True)
        while not _stopped and time.time() < end and status.poll() is None:
            time.sleep(1)
        status.terminate()
        status.wait()
    return rc


def _synthetic_set(path: Path, n: int = 64, seed: int = 0):
    """Ink of random loops and bars labelled with 1-3 letters: for the self test only."""
    import numpy as np
    from .features import features
    rng = np.random.default_rng(seed)
    path.mkdir(parents=True)
    xs, texts = [], []
    for i in range(n):
        k = int(rng.integers(1, 4))
        strokes, x = [], 0.0
        for j in range(k):
            a = np.linspace(0, 2 * np.pi, 40)
            r = 0.2 + 0.05 * rng.random()
            strokes.append(np.c_[r * np.cos(a) + x + 0.3, r * np.sin(a) - 0.2, np.linspace(j, j + 0.3, 40)])
            x += 0.6
        xs.append(features(strokes))
        texts.append("".join(rng.choice(list("abc"), k)))
    np.concatenate(xs).astype(np.float16).tofile(path / "feat.f16")
    np.save(path / "offsets.npy", np.cumsum([0] + [len(f) for f in xs]))
    (path / "texts.json").write_text(json.dumps(texts))


def cmd_selftest(a) -> int:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _synthetic_set(d / "build" / "data" / "st")
        log = d / "selftest.log"
        steps = []
        cmd = [sys.executable, "-m", "glassine_train.cli", "train", "--run", "st", "--data", "st", "--val", "st",
               "--select", "", "--device", a.device, "--minutes", "2", "--steps-per-s", str(40 / 120),
               "--eval-every", "20", "--batch", "8", "--workers", "2", "--threads", "2",
               "--conv", "32", "--hidden", "16"]
        t0 = time.time()
        rc = _tee(cmd, log, cwd=d)
        steps.append(("train 40 steps on " + a.device, rc == 0, time.time() - t0))
        if rc == 0:
            env = dict(os.environ, CUDA_VISIBLE_DEVICES="")
            chk = ("import torch; from pathlib import Path; from glassine_train.train import load; "
                   "assert not torch.cuda.is_available(); m = load(Path('build/runs/st/best.pt')); "
                   "ck = torch.load('build/runs/st/last.pt', weights_only=False); "
                   "assert all(v.device.type == 'cpu' for v in ck['state'].values()); "
                   "lp, _ = m(torch.zeros(1, 80, m.c1.in_channels), torch.tensor([80])); print('cpu read ok', lp.shape)")
            t0 = time.time()
            steps.append(("checkpoint read with no GPU", _tee([sys.executable, "-c", chk], log, cwd=d, env=env) == 0,
                          time.time() - t0))
            t0 = time.time()
            steps.append(("export (ONNX, CPU)", _tee([sys.executable, "-m", "glassine_train.cli", "export", "--run",
                                                      "st", "--data", "st"], log, cwd=d, env=env) == 0,
                          time.time() - t0))
            if a.device != "cpu":
                t0 = time.time()
                rc2 = _tee(cmd + ["--resume", "--minutes", "2"], log, cwd=d)
                steps.append(("resume (nothing left: returns)", rc2 == 0, time.time() - t0))
    ok = all(s[1] for s in steps)
    for name, good, t in steps:
        print(f"selftest: {'PASS' if good else 'FAIL'} {name} ({t:.0f} s)", flush=True)
    print(f"selftest: {'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    rest = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="python -m glassine_train.job")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--bundle", help="an unpacked bundle: its training arguments come first")
    t.add_argument("--nice", type=int, default=10)
    t.add_argument("--status-port", type=int, default=8686, help="0: no status page")
    t.add_argument("--status-bind", default="127.0.0.1")
    t.add_argument("--status-reachable", help="the address the port is published on (for the warning)")
    t.add_argument("--linger", type=int, default=86400, help="seconds the status page stays up after the job")
    t.add_argument("--no-export", dest="export", action="store_false")
    t.add_argument("--bench", type=float, default=0, help="a benchmark of at most this many minutes")
    t.add_argument("--bench-steps", type=int, default=300)
    s = sub.add_parser("selftest")
    s.add_argument("--device", default="cuda")
    a = ap.parse_args(argv)
    os.umask(0o077)  # the run's files: the training user's only
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    sys.exit(cmd_train(a, rest) if a.cmd == "train" else cmd_selftest(a))


if __name__ == "__main__":
    main()
