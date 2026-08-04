"""Locating the per-ECU definition files of a profile.

``<profile>/ecus/`` holds one YAML file per ECU, and three questions get asked
of that directory: *which files are ECU definitions* (the loader and the
validator), *which file defines ECU X* (the name-keyed editors), and *which file
defines the ECU at CAN address 0x7XX* (the tx_id-keyed editors, which see an
address before they know a name).

They live here together so the answers cannot drift apart — the two lookups were
previously separate implementations in ``pids_edit`` and ``ecus_edit``, both
exporting a function called ``find_ecu_file`` with different arguments, return
types and failure modes. This is also the single place that decides *which root*
owns an ECU definition, which is what a mutative command needs to know.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from .profile import Profile

# A file whose name starts with "_" is scratch/disabled, never an ECU definition.
_SKIP_PREFIX = "_"

# A top-level ECU key: "IGPM:" at zero indent, nothing else on the line.
_ECU_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_\-]*):\s*$", re.MULTILINE)


def ecus_dir(dir_or_none: Path | str | None = None, *, profile: Profile | None = None) -> Path:
    """Resolve the ECU definitions directory (default: the active profile's)."""
    if dir_or_none is not None:
        return Path(dir_or_none)
    if profile is not None:
        return profile.ecus_dir
    from .profile import active

    return active().ecus_dir


def iter_ecu_files(
    dir_or_none: Path | str | None = None,
    *,
    profile: Profile | None = None,
    include_disabled: bool = False,
) -> Iterator[Path]:
    """Yield the ECU definition files, sorted.

    ``_``-prefixed files are scratch/disabled and excluded from everything that
    treats the directory as the vehicle's definition (loading, editing, address
    lookup). ``include_disabled=True`` is for the validator, which reports on a
    parked file too so re-enabling it doesn't surface a surprise.
    """
    for path in sorted(ecus_dir(dir_or_none, profile=profile).glob("*.yaml")):
        if include_disabled or not path.name.startswith(_SKIP_PREFIX):
            yield path


def find_by_name(
    ecu_name: str, dir_or_none: Path | str | None = None, *, profile: Profile | None = None
) -> Path | None:
    """The file defining ``ecu_name`` (case-insensitive), or None.

    Matches the top-level ECU key textually rather than parsing the YAML, so a
    file the editors could still repair by hand is found rather than skipped.
    """
    target = ecu_name.strip().upper()
    for path in iter_ecu_files(dir_or_none, profile=profile):
        for match in _ECU_KEY_RE.finditer(path.read_text()):
            if match.group(1).upper() == target:
                return path
    return None


def find_by_tx(
    tx_id: int, dir_or_none: Path | str | None = None, *, profile: Profile | None = None
) -> tuple[Path | None, str | None]:
    """The file and ECU name for the ECU whose ``tx_id`` matches, else (None, None).

    Requires parsing (``tx_id`` is a value, not a key), so an unparseable file is
    skipped rather than aborting the search.
    """
    from .yaml_rt import round_trip_yaml

    yaml = round_trip_yaml()
    for path in iter_ecu_files(dir_or_none, profile=profile):
        try:
            with open(path) as f:
                data = yaml.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for name, ecu_def in data.items():
            if isinstance(ecu_def, dict) and ecu_def.get("tx_id") == tx_id:
                return path, name
    return None, None
