"""Keep a profile's DTC log in user state, in the current format, and tagged.

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

A log in an earlier format is converted (:func:`upgrade`): format 1 named each ECU by a
display label, which the profile now supplies, and stored entries derived from others.

Entries recorded before the log carried the ECU revision (or the variant) are then
tagged with what the profile alone settles — the only variant it declares, the only
revision the car can carry (:func:`implied_tags`) — and otherwise left untagged, read as
the selected car. :func:`canlib.dtc.log.log_file` runs all of it the first time a
process touches a profile's log; :func:`migrate_all` runs it for every profile, before a
reinstall and in ``canair config``.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
    """Bring ``profile``'s DTC log up to date at ``dest``: fold in its legacy logs, convert
    an earlier format, then tag the entries recorded before tagging. Returns the legacy
    files absorbed."""
    addresses = _addresses(profile)
    if addresses is None:
        return []  # without the profile no label can be resolved; try again next time
    absorbed = _move(profile, dest, addresses)
    tagged = backfill_tags(profile, dest, addresses)
    if tagged:
        print(
            f"note: tagged {tagged} earlier DTC-log entr(ies) in {dest} with the variant or "
            "ECU revision their car can only have been.",
            file=sys.stderr,
        )
    return absorbed


@dataclass(frozen=True)
class _Addresses:
    """``profile``'s addressing, for converting and tagging its log without it being active."""

    to_rx: Callable[[int], str]
    names: Mapping[str, str]  # rx -> ECU name


def _addresses(profile: Profile) -> _Addresses | None:
    from canlib.addressing import resolve_mode, resolve_rx_offset
    from canlib.ecus import load_ecus, rx_for_tx

    try:
        registry = load_ecus(profile.ecus_dir) if profile.ecus_dir.is_dir() else {}
        meta = profile.meta
    except Exception:
        return None
    offset, mode = resolve_rx_offset(meta), resolve_mode(meta)

    def to_rx(tx: int) -> str:
        return f"0x{rx_for_tx(tx, registry, offset, mode):03X}"

    return _Addresses(to_rx, {to_rx(tx): str(info["name"]) for tx, info in registry.items()})


def _move(profile: Profile, dest: Path, addresses: _Addresses) -> list[Path]:
    from canlib.dtc.log import FORMAT, write_log

    sources = legacy_sources(profile, dest)
    current = _load(dest) if dest.exists() else {}
    outdated = bool(current) and current.get("format") != FORMAT
    if not sources and not outdated:
        return []
    docs = [current, *(_load(p) for p in sources)]
    result = merged([upgrade(d, addresses.to_rx) for d in docs])
    if outdated or result != {s: current[s] for s in SECTIONS if current.get(s)}:
        write_log(result, dest)
    if outdated:
        print(
            f"note: rewrote the DTC log {dest} to name each ECU by its response address "
            "(its name comes from the profile).",
            file=sys.stderr,
        )
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


# ── format 1: ECUs named by a display label ("BMS (0x7E4)") ─────────────────

_LABEL_TX = re.compile(r"(?:^|\()0x([0-9A-Fa-f]+)\)?$")


def _label_tx(label: object) -> int | None:
    """The request id in a format-1 label (``BMS (0x7E4)`` or ``0x7E4``), or None."""
    m = _LABEL_TX.search(str(label or "").strip())
    return int(m.group(1), 16) if m else None


def upgrade(doc: Mapping[str, Any], to_rx: Callable[[int], str]) -> dict[str, list]:
    """``doc``'s entries in the current format (a current-format doc is returned as is).

    Format 1 named an ECU by a display label plus its request id, and stored what follows
    from the readings: a clean "post-clear" scan invented by every ``--clear``, and a
    ``detected`` clear for codes gone since the previous scan. The labels become response
    addresses (one the profile can't place is kept as written), and both kinds of derived
    entry are dropped — :mod:`canlib.dtc.log` works them out when read."""
    from canlib.dtc.log import FORMAT

    if doc.get("format") == FORMAT:
        return {s: list(doc.get(s) or []) for s in SECTIONS}

    def ref(label: object, info: Mapping | None = None) -> str:
        tx = _label_tx((info or {}).get("tx")) or _label_tx(label)
        return to_rx(tx) if tx is not None else str(label)

    scans = []
    for entry in doc.get("scans") or []:
        if not isinstance(entry, Mapping):
            continue
        ecus = entry.get("ecus") or {}
        scope = entry.get("scope")
        if (
            entry.get("label") == "post-clear"
            and scope != "all"
            and not any((info or {}).get("dtcs") for info in ecus.values())
        ):
            continue
        new: dict[str, Any] = {}
        for key, value in entry.items():
            if key == "scope":
                new[key] = value if value == "all" else ref(value)
            elif key == "ecus":
                new[key] = {
                    ref(label, info): {k: v for k, v in (info or {}).items() if k != "tx"}
                    for label, info in (value or {}).items()
                }
            elif key == "unread":
                new[key] = [ref(u) for u in value or []]
            else:
                new[key] = value
        scans.append(new)

    clears = []
    for entry in doc.get("clears") or []:
        if not isinstance(entry, Mapping) or entry.get("type") == "detected":
            continue
        new = {
            "timestamp": entry.get("timestamp"),
            "rx": ref(entry.get("ecu") or entry.get("scope")),
        }
        for key in ("protocol", "group", "codes", "label", "variant", "revisions"):
            if key in entry:
                new[key] = entry[key]
        clears.append(new)
    return {"scans": scans, "clears": clears}


def implied_tags(
    entry: Mapping[str, Any],
    variants: Mapping[str, Any],
    ecus: Mapping[str, Any],
    names: Mapping[str, str],
) -> dict[str, Any]:
    """The tag fields ``entry`` lacks that the profile alone settles — nothing is guessed.

    The variant, when the profile declares only one. A multi-revision ECU's revision, when
    the entry's car (or, with no variant, any car) can carry only one of them; only for the
    ECUs the entry covers (every ECU for a full sweep). ``names`` maps rx to ECU name.
    """
    from canlib.dtc.scope import ALL_SCOPE
    from canlib.variants.revisions import declared, possible_revisions

    out: dict[str, Any] = {}
    variant = entry.get("variant")
    if not variant and len(variants) == 1:
        variant = out["variant"] = next(iter(variants))
    ref = entry.get("rx") if "rx" in entry else entry.get("scope")
    covered = list(ecus) if ref == ALL_SCOPE else [names.get(str(ref))]
    value = entry.get("revisions")
    tags: Mapping[str, Any] = value if isinstance(value, Mapping) else {}
    tagged = {str(k).upper() for k in tags}
    found: dict[str, str] = {}
    for name in covered:
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


def backfill_tags(profile: Profile, dest: Path, addresses: _Addresses | None = None) -> int:
    """Give the entries of the log at ``dest`` the tags :func:`implied_tags` settles.

    Entries recorded before DTC logs were tagged with the ECU revision (or before the
    variant was known) otherwise read as whatever the car is resolved to later — wrong
    once that ECU is another revision. Returns the number of entries tagged."""
    addresses = addresses or _addresses(profile)
    if not dest.exists() or addresses is None:
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
            fields = implied_tags(entry, variants, ecus, addresses.names)
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
