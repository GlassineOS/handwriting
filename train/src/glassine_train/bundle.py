"""A training run's inputs as one tarball, for training on another machine (gpu/README.md).

The bundle holds exactly what `glassine-train train` reads for one run: the featurized
datasets of --data, --val and --select (feat.f16, offsets.npy, texts.json, meta.json) and
the --init checkpoint, a manifest with the training arguments rewritten to the bundle's
layout and every file's size and sha256, and a FILE.sha256 beside it (sha256sum's format).
It may hold data used under non-commercial terms or a person's own writing: it stays on
machines that person controls, readable only by the training user, and is deleted after
the run. Standard library only: unpacking needs no trainer.

Layout, mirrored on the training machine under its working directory:
    manifest.json
    data/NAME/{feat.f16,offsets.npy,texts.json,meta.json}   -> build/data/NAME
    init/RUN/FILE                                           -> build/init/RUN/FILE
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

FORMAT = "glassine-train-bundle 1"
SET_FILES = ("feat.f16", "offsets.npy", "texts.json", "meta.json")
NOTICE = ("This bundle may hold data used under non-commercial terms (BRUSH) or a person's own "
          "writing. Keep it on machines that person controls, readable only by the training "
          "user, and delete it and its unpacked copy after the run.")
CHUNK = 1 << 20


class _Hashing(io.RawIOBase):
    """A file read (or written) through, its sha256 kept."""

    def __init__(self, fh, write=False):
        self.fh, self.h, self.n, self._write = fh, hashlib.sha256(), 0, write

    def readable(self):
        return not self._write

    def writable(self):
        return self._write

    def readinto(self, b):
        n = self.fh.readinto(b)
        if n:
            self.h.update(memoryview(b)[:n])
            self.n += n
        return n

    def write(self, b):
        self.h.update(b)
        self.n += len(b)
        return self.fh.write(b)


def _set_name(arg: str) -> str:
    """A dataset argument as its name in the bundle: a plain relative name stays, an
    absolute path (or one with ..) becomes its last part."""
    p = PurePosixPath(arg)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return Path(arg).name
    return str(p)


def plan(ns, build: Path = Path("build")) -> tuple[list[tuple[Path, str]], dict]:
    """(source file, path in the bundle) for every file a run with these training arguments
    reads, and the argument values rewritten to the bundle's layout."""
    if getattr(ns, "resume", False):
        raise SystemExit("bundle: --resume goes on with a run on the machine that has it (its OUT)")
    files, new = [], {}
    names = {}

    def add_set(arg):
        name = _set_name(arg)
        src = build / "data" / arg
        if names.setdefault(name, src) != src:
            raise SystemExit(f"bundle: two datasets named {name}")
        for f in SET_FILES:
            if (src / f).is_file():
                files.append((src / f, f"data/{name}/{f}"))
            elif f != "meta.json":
                raise SystemExit(f"bundle: {src / f} missing (not a featurized dataset)")
        return name

    new["data"] = add_set(ns.data)
    new["val"] = add_set(ns.val)
    new["select"] = ",".join(add_set(x) for x in ns.select.split(",") if x)
    if ns.init:
        src = Path(ns.init)
        if not src.is_file():
            raise SystemExit(f"bundle: --init {src}: no such checkpoint")
        dst = f"init/{src.parent.name or 'init'}/{src.name}"
        files.append((src, dst))
        new["init"] = "build/" + dst
    seen = set()
    files = [(s, d) for s, d in files if not (d in seen or seen.add(d))]
    return files, new


def argv_of(parser: argparse.ArgumentParser, ns) -> list[str]:
    """The namespace as an argument list for the same parser (store_true flags as flags)."""
    out = []
    for act in parser._actions:
        if not act.option_strings or act.dest == "help":
            continue
        v = getattr(ns, act.dest, None)
        if isinstance(act, argparse._StoreTrueAction):
            if v:
                out.append(act.option_strings[-1])
        elif v is not None:
            out += [act.option_strings[-1], str(v)]
    return out


def pack(out: Path, files: list[tuple[Path, str]], train_args: list[str]) -> dict:
    """Write the tarball (mode 0600) and out.sha256 in one pass over the files."""
    if out.exists():
        raise SystemExit(f"bundle: {out} exists")
    manifest = {"format": FORMAT, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "train_args": train_args, "notice": NOTICE, "files": {}}
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as raw:
            hw = _Hashing(raw, write=True)
            with tarfile.open(fileobj=hw, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for src, dst in files:
                    ti = tarfile.TarInfo(dst)
                    ti.size, ti.mode, ti.mtime = src.stat().st_size, 0o600, 0
                    with open(src, "rb") as fh:
                        hr = _Hashing(fh)
                        tar.addfile(ti, io.BufferedReader(hr, CHUNK))
                    if hr.n != ti.size:
                        raise SystemExit(f"bundle: {src} changed while it was packed")
                    manifest["files"][dst] = {"bytes": ti.size, "sha256": hr.h.hexdigest()}
                    print(f"  {dst} {ti.size} bytes", flush=True)
                body = json.dumps(manifest, indent=1).encode()
                ti = tarfile.TarInfo("manifest.json")
                ti.size, ti.mode, ti.mtime = len(body), 0o600, 0
                tar.addfile(ti, io.BytesIO(body))
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    side = out.with_name(out.name + ".sha256")
    side.write_text(f"{hw.h.hexdigest()}  {out.name}\n")
    side.chmod(0o600)
    total = sum(f["bytes"] for f in manifest["files"].values())
    print(f"{out}: {len(files)} files, {total} bytes of data; sha256 in {side.name}", flush=True)
    print(NOTICE, flush=True)
    return manifest


def _member_path(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts or "" in p.parts:
        raise SystemExit(f"unbundle: unsafe path in the bundle: {name!r}")
    return p


def unpack(tar_path: Path, dest: Path) -> dict:
    """Unpack into dest (absent or empty; made 0700, files 0600), checking that the members
    are exactly the manifest's regular files and every sha256. Nothing outside dest is
    written; on any mismatch nothing is left in it."""
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"unbundle: {dest} is not empty")
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest.chmod(0o700)
    part = dest / ".partial"
    try:
        with tarfile.open(tar_path, "r:") as tar:
            members = tar.getmembers()
            byname = {m.name: m for m in members}
            if "manifest.json" not in byname or len(byname) != len(members):
                raise SystemExit("unbundle: no manifest, or a name twice: not a training bundle")
            mf = byname["manifest.json"]
            if not mf.isreg() or mf.size > 64 << 20:
                raise SystemExit("unbundle: bad manifest member")
            manifest = json.loads(tar.extractfile(mf).read())
            if manifest.get("format") != FORMAT:
                raise SystemExit(f"unbundle: format {manifest.get('format')!r}, expected {FORMAT!r}")
            want = manifest["files"]
            if set(byname) - {"manifest.json"} != set(want):
                raise SystemExit("unbundle: the bundle's files and its manifest differ")
            for name, info in want.items():
                rel = _member_path(name)
                m = byname[name]
                if not m.isreg() or m.size != info["bytes"]:
                    raise SystemExit(f"unbundle: {name}: not a regular file of the manifest's size")
                target = part / rel
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                h = hashlib.sha256()
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as fo, tar.extractfile(m) as fi:
                    while b := fi.read(CHUNK):
                        h.update(b)
                        fo.write(b)
                if h.hexdigest() != info["sha256"]:
                    raise SystemExit(f"unbundle: {name}: sha256 differs from the manifest")
                print(f"  {name} ok", flush=True)
        fd = os.open(part / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fo:
            json.dump(manifest, fo, indent=1)
        for d in [part, *(q for q in part.rglob("*") if q.is_dir())]:
            d.chmod(0o700)  # mkdir(parents=True) makes the parents by the umask
        for p in part.iterdir():
            p.rename(dest / p.name)
        part.rmdir()
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
    print(f"{dest}: {len(want)} files checked", flush=True)
    print(manifest.get("notice", NOTICE), flush=True)
    return manifest


def is_bundle_dir(path: Path) -> bool:
    """A directory unpack made (its manifest says so): the only kind clean removes."""
    try:
        m = path / "manifest.json"
        if m.is_symlink() or not stat.S_ISREG(m.lstat().st_mode):
            return False
        return json.loads(m.read_text()).get("format") == FORMAT
    except (OSError, ValueError):
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m glassine_train.bundle")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("unpack", help="unpack and check a bundle")
    u.add_argument("tar", type=Path)
    u.add_argument("dest", type=Path)
    c = sub.add_parser("check", help="exit 0 if DIR is an unpacked bundle")
    c.add_argument("dir", type=Path)
    g = sub.add_parser("args", help="the bundle's training arguments, one per line")
    g.add_argument("dir", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "unpack":
        unpack(a.tar, a.dest)
    elif a.cmd == "check":
        sys.exit(0 if is_bundle_dir(a.dir) else 1)
    else:
        for x in json.loads((a.dir / "manifest.json").read_text())["train_args"]:
            print(x)


if __name__ == "__main__":
    main()
