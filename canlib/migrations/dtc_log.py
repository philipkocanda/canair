"""Move a profile's DTC log out of the profile directory into user state, and tag it.

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

Entries recorded before the log carried the ECU revision (or the variant) are then
tagged with what the profile alone settles — the only variant it declares, the only
revision the car can carry (:func:`implied_tags`) — and otherwise left untagged, read as
the selected car. :func:`canlib.dtc.log.log_file` runs all of it the first time a
process touches a profile's log; :func:`migrate_all` runs it for every profile, before a
reinstall and in ``canair config``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    """Bring ``profile``'s DTC log up to date at ``dest``: fold in its legacy logs, then tag
    the entries recorded before tagging. Returns the legacy files absorbed."""
    absorbed = _move(profile, dest)
    tagged = backfill_tags(profile, dest)
    if tagged:
        print(
            f"note: tagged {tagged} earlier DTC-log entr(ies) in {dest} with the variant or "
            "ECU revision their car can only have been.",
            file=sys.stderr,
        )
    return absorbed


def _move(profile: Profile, dest: Path) -> list[Path]:
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


def implied_tags(
    entry: Mapping[str, Any], variants: Mapping[str, Any], ecus: Mapping[str, Any]
) -> dict[str, Any]:
    """The tag fields ``entry`` lacks that the profile alone settles — nothing is guessed.

    The variant, when the profile declares only one. A multi-revision ECU's revision, when
    the entry's car (or, with no variant, any car) can carry only one of them; only for the
    ECUs the entry covers (every ECU for a full sweep).
    """
    from canlib.dtc.scope import ALL_SCOPE, display_name
    from canlib.variants.revisions import canonical_ecu, declared, possible_revisions

    out: dict[str, Any] = {}
    variant = entry.get("variant")
    if not variant and len(variants) == 1:
        variant = out["variant"] = next(iter(variants))
    scope = str(entry.get("scope", ""))
    names = list(ecus) if scope == ALL_SCOPE else [canonical_ecu(display_name(scope), ecus)]
    value = entry.get("revisions")
    tags: Mapping[str, Any] = value if isinstance(value, Mapping) else {}
    tagged = {str(k).upper() for k in tags}
    found: dict[str, str] = {}
    for name in names:
        ecu_def = ecus.get(name) if name else None
        if not isinstance(ecu_def, Mapping) or len(declared(ecu_def)) < 2:
            continue
        if str(name).upper() in tagged:
            continue
        possible = possible_revisions(str(name), ecu_def, variants.get(variant))
        if len(possible) == 1:
            found[str(name)] = possible[0]
    if found:
        out["revisions"] = {**tags, **found}
    return out


def backfill_tags(profile: Profile, dest: Path) -> int:
    """Give the entries of the log at ``dest`` the tags :func:`implied_tags` settles.

    Entries recorded before DTC logs were tagged with the ECU revision (or before the
    variant was known) otherwise read as whatever the car is resolved to later — wrong
    once that ECU is another revision. Returns the number of entries tagged."""
    if not dest.exists():
        return 0
    from canlib.pids import load_raw_pids
    from canlib.variants import load_variants

    try:
        variants = load_variants(profile)
        ecus = load_raw_pids(profile).get("ecus") or {}
    except Exception:
        return 0  # an unloadable profile settles nothing; try again next time
    from canlib.yaml_rt import round_trip_yaml

    data = round_trip_yaml().load(dest.read_text())
    n = 0
    for section in SECTIONS:
        for entry in (data or {}).get(section) or []:
            if not isinstance(entry, dict):
                continue
            fields = implied_tags(entry, variants, ecus)
            if fields:
                entry.update(fields)
                n += 1
    if n:
        from canlib.dtc.log import write_log

        write_log(data, dest)
    return n


def migrate_all() -> None:
    """Bring every discoverable profile's DTC log up to date.

    A log is otherwise moved only when its profile is next used, so a profile left
    untouched would still keep its log where a reinstall (``canair update``) deletes it.
    """
    from canlib.dtc.log import log_file
    from canlib.profile import ProfileError, resolve_profile, selectable_profiles

    for name in selectable_profiles():
        try:
            log_file(resolve_profile(name))
        except (ProfileError, OSError):
            continue
