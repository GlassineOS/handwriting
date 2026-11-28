"""The GPU kit (gpu/): bundle and unbundle, the status page, run.sh's container options.
Synthetic data only."""

import hashlib
import http.client
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

import numpy as np
import pytest

from glassine_train import bundle as B
from glassine_train import status as S

KIT = Path(__file__).resolve().parents[1] / "gpu"


def _set(path: Path, n=4, meta=True):
    path.mkdir(parents=True)
    np.arange(n * 10 * 5, dtype=np.float16).tofile(path / "feat.f16")
    np.save(path / "offsets.npy", np.arange(0, n * 10 + 1, 10))
    (path / "texts.json").write_text(json.dumps(["ab"] * n))
    if meta:
        (path / "meta.json").write_text(json.dumps({"n": n}))
    return path


def _bundle(tmp_path, monkeypatch, extra=()):
    from glassine_train.cli import main
    monkeypatch.chdir(tmp_path)
    for name in ("train5", "val5.xh", "dev5.xh", "brush5-dev"):
        _set(tmp_path / "build" / "data" / name, meta=name != "brush5-dev")
    _set(tmp_path / "elsewhere" / "extra")
    (tmp_path / "build" / "runs" / "m4").mkdir(parents=True)
    (tmp_path / "build" / "runs" / "m4" / "best.pt").write_bytes(b"checkpoint")
    out = tmp_path / "b.tar"
    main(["bundle", "--out", str(out), "--", "--run", "m5", "--data", "train5", "--val", "val5.xh",
          "--select", f"dev5.xh,brush5-dev,{tmp_path / 'elsewhere' / 'extra'}", "--augment", "--hidden", "176",
          "--init", "build/runs/m4/best.pt", "--minutes", "600", *extra])
    return out


def test_bundle_holds_exactly_the_run_inputs_and_unpacks_checked(tmp_path, monkeypatch):
    out = _bundle(tmp_path, monkeypatch)
    assert out.stat().st_mode & 0o777 == 0o600
    side = (tmp_path / "b.tar.sha256").read_text().split()
    assert side == [hashlib.sha256(out.read_bytes()).hexdigest(), "b.tar"]
    with tarfile.open(out) as t:
        names = sorted(t.getnames())
        man = json.loads(t.extractfile("manifest.json").read())
    want = ["data/brush5-dev/feat.f16", "data/brush5-dev/offsets.npy", "data/brush5-dev/texts.json"]
    for s in ("dev5.xh", "extra", "train5", "val5.xh"):
        want += [f"data/{s}/{f}" for f in ("feat.f16", "meta.json", "offsets.npy", "texts.json")]
    assert names == sorted(want + ["init/m4/best.pt", "manifest.json"])
    a = man["train_args"]
    assert a[a.index("--select") + 1] == "dev5.xh,brush5-dev,extra"  # no machine path in the manifest
    assert a[a.index("--init") + 1] == "build/init/m4/best.pt" and "--augment" in a
    assert str(tmp_path) not in json.dumps(man)
    # the arguments parse back to the same run
    from glassine_train.job import _train_ns
    ns = _train_ns(a)
    assert (ns.run, ns.hidden, ns.minutes, ns.augment, ns.device) == ("m5", 176, 600.0, True, "cpu")

    dest = tmp_path / "server" / "data"
    B.unpack(out, dest)
    assert dest.stat().st_mode & 0o777 == 0o700
    assert (dest / "data" / "train5" / "feat.f16").read_bytes() == \
        (tmp_path / "build" / "data" / "train5" / "feat.f16").read_bytes()
    assert (dest / "init" / "m4" / "best.pt").stat().st_mode & 0o777 == 0o600
    assert all(d.stat().st_mode & 0o777 == 0o700 for d in dest.rglob("*") if d.is_dir())
    assert B.is_bundle_dir(dest) and not B.is_bundle_dir(tmp_path)
    with pytest.raises(SystemExit, match="not empty"):
        B.unpack(out, dest)


def test_bundle_refuses_resume_and_an_existing_file(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="--resume"):
        _bundle(tmp_path, monkeypatch, ["--resume"])
    (tmp_path / "b.tar").unlink(missing_ok=True)


def _tamper(src: Path, dst: Path, fn):
    """A copy of the bundle with its members changed by fn(name, data) -> (name, data) | None."""
    with tarfile.open(src) as t, tarfile.open(dst, "w") as o:
        for m in t.getmembers():
            got = fn(m.name, t.extractfile(m).read())
            if got:
                ti = tarfile.TarInfo(got[0])
                ti.size = len(got[1])
                o.addfile(ti, io.BytesIO(got[1]))
        return dst


@pytest.mark.parametrize("change", ["flip", "extra", "traversal", "missing"])
def test_unbundle_refuses_a_changed_bundle(tmp_path, monkeypatch, change):
    out = _bundle(tmp_path, monkeypatch)

    def fn(name, data):
        if change == "flip" and name == "data/train5/texts.json":
            return name, data.replace(b"ab", b"ba")
        if change == "missing" and name == "data/train5/texts.json":
            return None
        if change == "traversal" and name == "data/train5/texts.json":
            return "../../outside.json", data
        return name, data
    bad = _tamper(out, tmp_path / "bad.tar", fn)
    if change == "extra":
        with tarfile.open(bad, "a") as t:
            t.addfile(tarfile.TarInfo("extra.txt"), io.BytesIO(b""))
    dest = tmp_path / "dest"
    with pytest.raises(SystemExit):
        B.unpack(bad, dest)
    assert not any(dest.rglob("*")) and not (tmp_path / "outside.json").exists()


def _log_of_a_real_run(tmp_path):
    """train.log as the job writes it: a tiny real training run's output."""
    from glassine_train import train as T
    from test_train import _tiny_set
    import contextlib
    data = _tiny_set(tmp_path / "data", ["ab", "ba", "a", "b", "abab", "bab", "aab", "bb"])
    buf = io.StringIO()
    run = tmp_path / "runs" / "r1"
    with contextlib.redirect_stdout(buf):
        T.train(data, data, run, 220 / 60 / 1.0, {"conv": 16, "hidden": 8, "layers": 1, "dropout": 0.0, "down": 3},
                batch=4, threads=1, eval_every=100, steps_per_s=1.0, workers=0)
    (run / "train.log").write_text(buf.getvalue())
    return run


def test_status_parses_the_trainer_output(tmp_path):
    run = _log_of_a_real_run(tmp_path)
    st = S.status(run)
    assert st["total"] == 220 and st["step"] == 220
    assert [s for s, _ in st["loss_series"]] == [100, 200]
    assert [s for s, _ in st["cer_series"]] == [100, 200, 220]
    assert st["lr"] is not None and st["steps_per_s"] > 0 and st["eta_s"] is None
    assert set(st["downloads"]) >= {"best.pt", "last.pt", "log.jsonl", "train.log"}
    html = S.page(st)
    assert "<svg" in html and "r1" in html


def _get(port, path, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path)
    r = c.getresponse()
    return r.status, r.read(), dict(r.getheaders())


def test_status_page_serves_only_the_run_files(tmp_path):
    run = tmp_path / "out" / "r1"
    run.mkdir(parents=True)
    (run / "train.log").write_text("planned 300 steps\nstep 100/300 loss 2.5 3.00 step/s lr 1.00e-03\n"
                                   "<script>alert(1)</script>\n")
    (run / "best.pt").write_bytes(b"BEST")
    (tmp_path / "out" / "secret.txt").write_text("secret")
    (tmp_path / "out" / "r2").mkdir()
    (run / "notes.txt").write_text("not a download")
    (run / "recognizer.onnx").symlink_to(tmp_path / "out" / "secret.txt")
    (run / "export.json").mkdir()
    srv = S.serve(run, 0, "127.0.0.1")
    assert srv.server_address[0] == "127.0.0.1"
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        code, body, _ = _get(port, "/")
        assert code == 200 and b"<script>alert" not in body and b"&lt;script&gt;" in body
        assert b"100 / 300" in body and b"1 min 06 s" in body  # ETA: 200 steps at 3/s
        code, body, h = _get(port, "/download/best.pt")
        assert code == 200 and body == b"BEST" and "attachment" in h["Content-Disposition"]
        st = json.loads(_get(port, "/status.json")[1])
        assert st["downloads"] == ["best.pt", "train.log"]
        for path in ("/download/../secret.txt", "/download/%2e%2e/secret.txt", "/download/..%2fsecret.txt",
                     "/download/%2fetc%2fpasswd", "/download//etc/passwd", "/download/notes.txt",
                     "/download/recognizer.onnx", "/download/export.json", "/download/", "/download",
                     "/../secret.txt", "/%2e%2e/secret.txt", "/r2", "/out/", "/train.log", "/best.pt",
                     "//etc/passwd", "/etc/passwd", "/download/best.pt/..", "/download/train.log%00"):
            code, body, _ = _get(port, path)
            assert code == 404 and b"secret" not in body, path
        for method in ("POST", "PUT", "DELETE"):
            assert _get(port, "/download/best.pt", method)[0] >= 400
        assert (run / "best.pt").read_bytes() == b"BEST"
    finally:
        srv.shutdown()


def test_status_binds_to_loopback_by_default_and_warns_otherwise():
    import inspect
    assert inspect.signature(S.serve).parameters["bind"].default == "127.0.0.1"
    assert S._loopback("127.0.0.1") and S._loopback("::1") and not S._loopback("0.0.0.0")
    r = subprocess.run([sys.executable, "-c", "import sys; from glassine_train.status import main; "
                        "sys.argv=['x','/nonexistent','--port','0','--bind','0.0.0.0']; "
                        "import threading,os; threading.Timer(1.0, lambda: os._exit(0)).start(); main()"],
                       capture_output=True, text=True, timeout=30)
    assert "WARNING" in r.stderr


# run.sh, against a docker that only records its arguments

def _kit(tmp_path, *args, data=True, env=None):
    fake = tmp_path / "bin" / "docker"
    if not fake.exists():
        fake.parent.mkdir(parents=True)
        # records its arguments; "running" once a container was started (ps -q prints an id)
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$DOCKER_LOG"\nprintf "%s\\n" "--END--" >> "$DOCKER_LOG"\n'
                        'case "$1 $2" in "run -d") touch "$DOCKER_LOG.up" ;; "ps -q") [ -e "$DOCKER_LOG.up" ] && echo 1f2e ;; esac\n'
                        'exit 0\n')
        fake.chmod(0o755)
    home = tmp_path / "home"
    if data and not (home / "glassine-train" / "data").exists():
        d = home / "glassine-train" / "data"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"format": B.FORMAT, "train_args": []}, indent=1))
    log = tmp_path / "docker.log"
    log.unlink(missing_ok=True)
    Path(str(log) + ".up").unlink(missing_ok=True)
    e = dict(os.environ, HOME=str(home), DOCKER=str(fake), DOCKER_LOG=str(log), RUN_SH_SETTLE="0", **(env or {}))
    r = subprocess.run(["bash", str(KIT / "run.sh"), *args], capture_output=True, text=True, env=e, timeout=60)
    calls = [c.strip("\n").split("\n") for c in log.read_text().split("--END--\n") if c.strip()] if log.exists() else []
    return r, calls, home


def test_run_sh_train_options(tmp_path):
    r, calls, home = _kit(tmp_path, "train", "--", "--run", "m5", "--batch", "64")
    assert r.returncode == 0, r.stderr
    run = [c for c in calls if c[0] == "run"][0]
    j = " ".join(run)
    data = home / "glassine-train" / "data"
    assert f"{data}:/work/bundle:ro" in run and f"{home}/glassine-train/out:/work/build/runs:rw" in run
    for want in ("--gpus all", "--cpus 8", "--memory 16g", "--memory-swap 16g", "--shm-size 2g", "--read-only",
                 "-p 127.0.0.1:8686:8686", "--name glassine-train", "--nice 10", "--init",
                 "--device cuda --threads 4 --workers 4 --run m5 --batch 64"):
        assert want in j, want
    assert f"--user {os.getuid()}:{os.getgid()}" in j
    assert "--rm" not in run  # the job's container stays (its output) until the next start removes it
    assert ["rm", "glassine-train"] in calls
    assert (home / "glassine-train" / "out").stat().st_mode & 0o777 == 0o700
    r, calls, _ = _kit(tmp_path, "--cpu-only", "--cpus", "3", "--status-port", "0", "train")
    j = " ".join([c for c in calls if c[0] == "run"][-1])
    assert "--gpus" not in j and "--device cpu" in j and "--cpus 3" in j and "-p " not in j and "--network none" in j
    r, calls, _ = _kit(tmp_path, "--writable-root", "train")
    assert "--read-only" not in [c for c in calls if c[0] == "run"][-1]
    r, calls, _ = _kit(tmp_path, "--status-bind", "0.0.0.0", "train")
    assert "WARNING" in r.stderr and "-p 0.0.0.0:8686:8686" in " ".join([c for c in calls if c[0] == "run"][-1])


def test_run_sh_needs_a_bundle_and_clean_deletes_only_one(tmp_path):
    r, _, home = _kit(tmp_path, "train", data=False)
    assert r.returncode != 0 and "unpack" in r.stderr
    # not a bundle: kept
    other = tmp_path / "precious"
    other.mkdir()
    (other / "file").write_text("x")
    r, _, _ = _kit(tmp_path, "--data", str(other), "clean")
    assert r.returncode != 0 and (other / "file").exists()
    r, _, _ = _kit(tmp_path, "--data", str(home), "clean")
    assert r.returncode != 0 and home.exists()
    # the unpacked bundle, the tarball and the resume state go; the model and logs stay
    data = home / "glassine-train" / "data"
    _kit(tmp_path, "ps")
    run = home / "glassine-train" / "out" / "m5"
    run.mkdir(parents=True)
    for f in ("best.pt", "last.pt", "recognizer.onnx", "train.log", "log.jsonl"):
        (run / f).write_text(f)
    tar = tmp_path / "b.tar"
    tar.write_text("t")
    (tmp_path / "b.tar.sha256").write_text("s")
    r, _, _ = _kit(tmp_path, "clean", "--bundle", str(tar))
    assert r.returncode == 0, r.stderr
    assert not data.exists() and not tar.exists() and not (tmp_path / "b.tar.sha256").exists()
    assert sorted(p.name for p in run.iterdir()) == ["best.pt", "log.jsonl", "recognizer.onnx", "train.log"]


def test_run_sh_unpack_checks_the_sum(tmp_path, monkeypatch):
    tar = tmp_path / "b.tar"
    tar.write_bytes(b"bundle")
    (tmp_path / "b.tar.sha256").write_text("0" * 64 + "  b.tar\n")
    r, calls, _ = _kit(tmp_path, "--data", str(tmp_path / "new"), "unpack", str(tar), data=False)
    assert r.returncode != 0 and not calls
    (tmp_path / "b.tar.sha256").write_text(hashlib.sha256(b"bundle").hexdigest() + "  b.tar\n")
    r, calls, _ = _kit(tmp_path, "--data", str(tmp_path / "new"), "unpack", str(tar), data=False)
    assert r.returncode == 0, r.stderr
    j = " ".join(calls[0])
    assert "--network none" in j and f"{tmp_path}:/in:ro" in j and "glassine_train.bundle unpack /in/b.tar /data" in j
    assert (tmp_path / "new").stat().st_mode & 0o777 == 0o700


def test_run_sh_reports_a_job_that_ends_at_once(tmp_path):
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir(parents=True)
    fake.write_text('#!/bin/sh\n[ "$1" = logs ] && echo "glassine-train: error: unrecognized arguments: --bogus"\nexit 0\n')
    fake.chmod(0o755)
    _kit(tmp_path, "ps")  # the bundle directory
    r, _, _ = _kit(tmp_path, "train", "--", "--bogus")
    assert r.returncode != 0 and "ended at once" in r.stderr and "--bogus" in r.stderr


def test_status_shows_the_gpu(tmp_path, monkeypatch):
    smi = tmp_path / "bin" / "nvidia-smi"
    smi.parent.mkdir()
    smi.write_text("#!/bin/sh\necho 'NVIDIA GeForce RTX 3060, 87, 2310, 12288, 61, 142.50'\n")
    smi.chmod(0o755)
    monkeypatch.setenv("PATH", f"{smi.parent}:{os.environ['PATH']}")
    monkeypatch.setitem(S._gpu, "t", 0.0)
    run = tmp_path / "r"
    run.mkdir()
    st = S.status(run)
    assert st["gpu"] == [{"name": "NVIDIA GeForce RTX 3060", "util_pct": "87", "mem_used_mib": "2310",
                          "mem_total_mib": "12288", "temp_c": "61", "power_w": "142.50"}]
    assert "RTX 3060: 87 %, 2310 / 12288 MiB" in S.page(st)
    monkeypatch.setitem(S._gpu, "t", 0.0)
