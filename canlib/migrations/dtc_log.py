"""Move a profile's DTC log out of the profile directory into user state.

The DTC log used to be ``<profile>/dtc_log.yaml``. That put the only record of a car's
fault history inside a directory that a reinstall (a ``uv tool`` snapshot), a re-clone
or a ``git clean`` deletes, so it now lives at ``<config dir>/dtc_log/<profile>.yaml``
(:func:`canlib.dtc.log.log_file`). This folds every legacy file into it: the profile's
own ``dtc_log.yaml`` in each layer, and a user-state log still filed under one of the
profile's old names (``aliases:``).

Entries are merged, never replaced — each one is kept once, in timestamp order — and a
legacy file is deleted only after the merged log is written. A file that cannot be
deleted (a read-only install) is left alone; it is merged again next time, which changes
nothing because the entries are already there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from canlib import yaml_io

if TYPE_CHECKING:
    from canlib.profile import Profile

LEGACY_NAME = "dtc_log.yaml"
SECTIONS = ("scans", "clears")


def legacy_sources(profile: Profile, dest: Path) -> list[Path]:
    """Every legacy DTC log ``profile`` still has, beside ``dest``."""
    from canlib.profile_names import declared_aliases

    candidates = [profile.root / LEGACY_NAME, *(o / LEGACY_NAME for o in profile.overlays)]
    candidates += [dest.with_name(f"{old}.yaml") for old in declared_aliases(profile.root)]
    seen: list[Path] = []
    for path in candidates:
        if path != dest and path.is_file() and path not in seen:
            seen.append(path)
    return seen


def _load(path: Path) -> dict:
    try:
        data = yaml_io.safe_load(path.read_text()) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def merged(docs: list[dict]) -> dict[str, list]:
    """``docs``' scans and clears, each entry once, in timestamp order (stable)."""
    out: dict[str, list] = {}
    for section in SECTIONS:
        seen: set[str] = set()
        entries = []
        for doc in docs:
            for entry in doc.get(section) or []:
                if not isinstance(entry, dict):
                    continue
                key = json.dumps(entry, sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    entries.append(entry)
        if entries:
            out[section] = sorted(entries, key=lambda e: str(e.get("timestamp", "")))
    return out


def migrate(profile: Profile, dest: Path) -> list[Path]:
    """Fold ``profile``'s legacy DTC logs into ``dest``; returns the files absorbed."""
    sources = legacy_sources(profile, dest)
    if not sources:
        return []
    current = _load(dest) if dest.exists() else {}
    result = merged([current, *(_load(p) for p in sources)])
    if result != {s: current[s] for s in SECTIONS if current.get(s)}:
        from canlib.dtc.log import write_log

        write_log(result, dest)
    absorbed = []
    for path in sources:
        try:
            path.unlink()
        except OSError:
            continue
        absorbed.append(path)
    if absorbed:
        print(
            f"note: moved the DTC log into user state, {dest} (from "
            + ", ".join(str(p) for p in absorbed)
            + "), so a reinstall or re-clone can no longer erase it.",
            file=sys.stderr,
        )
    return absorbed
