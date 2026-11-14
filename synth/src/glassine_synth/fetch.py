"""Fetch handwriting fonts from google/fonts with their licence and metadata."""

import json
import re
import tomllib
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/{repo}/{ref}/ofl/{dir}/{name}"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "glassine-synth"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _field(meta: str, key: str) -> str | None:
    m = re.search(rf'^{key}: "([^"]*)"', meta, re.M)
    return m.group(1) if m else None


def parse_metadata(meta: str) -> dict:
    fonts = []
    for block in re.findall(r"^fonts \{(.*?)^\}", meta, re.M | re.S):
        fonts.append({
            "filename": _field(block.replace("\n  ", "\n"), "filename"),
            "style": _field(block.replace("\n  ", "\n"), "style"),
            "weight": re.search(r"weight: (\d+)", block).group(1),
            "copyright": _field(block.replace("\n  ", "\n"), "copyright"),
        })
    src = re.search(r'repository_url: "([^"]*)"', meta)
    commit = re.search(r'commit: "([^"]*)"', meta)
    return {
        "name": _field(meta, "name"),
        "designer": _field(meta, "designer"),
        "license": _field(meta, "license"),
        "categories": re.findall(r'^category: "([^"]*)"', meta, re.M),
        "fonts": fonts,
        "upstream": src.group(1) if src else None,
        "upstream_commit": commit.group(1) if commit else None,
    }


def fetch(manifest: Path, out: Path) -> list[dict]:
    cfg = tomllib.loads(manifest.read_text())
    repo, ref = cfg["source"]["repository"], cfg["source"]["ref"]
    out.mkdir(parents=True, exist_ok=True)
    families = []
    for fam in cfg["family"]:
        d = fam["dir"]
        url = lambda name: RAW.format(repo=repo, ref=ref, dir=d, name=name)
        meta = parse_metadata(_get(url("METADATA.pb")).decode())
        if meta["license"] != "OFL":
            raise SystemExit(f"{d}: licence {meta['license']}, only OFL is accepted")
        if "HANDWRITING" not in meta["categories"]:
            raise SystemExit(f"{d}: category {meta['categories']}, not HANDWRITING")
        fdir = out / d
        fdir.mkdir(exist_ok=True)
        (fdir / "OFL.txt").write_bytes(_get(url("OFL.txt")))
        files = []
        for f in meta["fonts"]:
            if f["style"] != "normal" or f["weight"] != "400":
                continue
            target = fdir / f["filename"]
            if not target.exists():
                target.write_bytes(_get(url(f["filename"])))
            files.append(f["filename"])
        meta.update(dir=d, files=sorted(set(files)), source=f"{repo}@{ref}")
        families.append(meta)
        print(f"{d}: {meta['name']} ({meta['license']}), {len(meta['files'])} file(s)")
    (out / "manifest.json").write_text(json.dumps(families, indent=2) + "\n")
    return families


def load(out: Path) -> list[dict]:
    path = out / "manifest.json"
    if not path.exists():
        raise SystemExit(f"{path} missing: run `glassine-synth fetch` first")
    return json.loads(path.read_text())
