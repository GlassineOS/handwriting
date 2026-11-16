"""Contributors as synthetic writers (spec 001 FR-020): a contributor's letter model
(versions in proportion of use, spacing) writes training text as one more writer, beside
the fonts.

The list is a JSON file, one entry per contribution:

    [{"contributor": "N", "letters": "PATH/letters/model", "licence": "CC0-1.0"}, ...]

An entry is used only when
- its licence covers derived models (LICENCES: the licences a contribution can carry,
  docs/samples.md; CC-BY-4.0 asks for attribution, kept in the manifest), and
- its contributor is not in the real test set (`test_contributors`): a writer the base model
  is measured on is never one it was generated from.

Entries that fail are left out with the reason (`load(..., strict=False)`) or stop the run.
The manifest records contributor numbers and licences, never paths or ink.
"""

import json
from pathlib import Path

from .gen import LetterWriter

LICENCES = {"CC0-1.0": "public domain dedication", "CC-BY-4.0": "attribution"}


def check(entry: dict, test_contributors: set[str]) -> str | None:
    """None when the entry may be a writer, else why not."""
    c = str(entry.get("contributor", ""))
    if not c:
        return "no contributor number"
    if entry.get("licence") not in LICENCES:
        return f"licence {entry.get('licence')!r} does not cover derived models"
    if c in test_contributors:
        return "in the real test set"
    if not entry.get("letters") or not Path(entry["letters"]).exists():
        return "no letter model"
    return None


def load(path: Path, test_contributors: set[str], strict: bool = True) -> list[tuple[LetterWriter, dict]]:
    """[(writer, manifest entry)] for the usable entries of a contributors list."""
    out = []
    for e in json.loads(Path(path).read_text()):
        why = check(e, {str(x) for x in test_contributors})
        meta = {"contributor": str(e.get("contributor", "")), "licence": e.get("licence")}
        if why:
            if strict:
                raise SystemExit(f"contributor {meta['contributor']}: {why}")
            continue
        rows = [json.loads(ln) for ln in Path(e["letters"]).read_text().splitlines() if ln.strip()]
        out.append((LetterWriter(rows, f"contributor-{meta['contributor']}"), meta))
    return out
