"""Capture-store migration: legacy per-day YAML → JSON.

Converts a profile's ``captures/YYYY-MM-DD.yaml`` → ``captures/YYYY-MM-DD.json``
for the JSON capture-store cutover.
Capture data is JSON-only (no dual-format read path), so this is the supported
path for a profile created before the cutover; it backs the user-facing
``canair captures migrate`` subcommand.

Each file is round-trip verified before anything is written or deleted: the
YAML is parsed, re-encoded as JSON, decoded again, and asserted structurally
equal to the original. This catches YAML→JSON type drift (e.g. an unquoted
scalar that YAML reads as a ``date`` rather than a string) *before* touching the
tree, rather than silently corrupting data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import capture_io, yaml_io


class MigrationError(Exception):
    """A capture file could not be safely converted (nothing was written)."""


@dataclass
class MigrationResult:
    """Outcome of migrating one capture file."""

    yaml_path: Path
    json_path: Path
    sessions: int
    captures: int
    written: bool  # False in --dry-run


def _count(data: Any) -> tuple[int, int]:
    """(#sessions, #captures) for a parsed capture doc; (0, 0) if malformed."""
    if not isinstance(data, dict):
        return 0, 0
    sessions = data.get("sessions")
    if not isinstance(sessions, list):
        return 0, 0
    caps = sum(len(s.get("captures", [])) for s in sessions if isinstance(s, dict))
    return len(sessions), caps


def _verify_round_trip(data: Any, path: Path) -> None:
    """Assert ``data`` survives a JSON round-trip unchanged, else raise."""
    import json

    try:
        reencoded = json.loads(json.dumps(data, ensure_ascii=False))
    except TypeError as e:
        raise MigrationError(
            f"{path.name}: contains a value that isn't JSON-native "
            f"(likely an unquoted date/time scalar YAML parsed as a non-string): {e}. "
            "Quote it in the source, then retry."
        ) from e
    if reencoded != data:
        raise MigrationError(
            f"{path.name}: JSON round-trip changed the data (YAML→JSON type drift); "
            "not migrated. Inspect the file for ambiguous scalars."
        )


def migrate_file(yaml_path: Path, *, dry_run: bool = False) -> MigrationResult:
    """Convert one ``*.yaml`` capture file to ``*.json`` (round-trip verified).

    On success the JSON file is written and the YAML removed (unless ``dry_run``).
    Raises :class:`MigrationError` (writing nothing) if the target JSON already
    exists or the round-trip check fails.
    """
    json_path = yaml_path.with_suffix(capture_io.CAPTURE_SUFFIX)
    if json_path.exists():
        raise MigrationError(
            f"{json_path.name} already exists alongside {yaml_path.name}; "
            "resolve the conflict manually (an interrupted migration?)."
        )
    data = yaml_io.safe_load(yaml_path.read_text())
    _verify_round_trip(data, yaml_path)
    n_sessions, n_captures = _count(data)
    if not dry_run:
        capture_io.dump_capture_file(json_path, data)
        yaml_path.unlink()
    return MigrationResult(yaml_path, json_path, n_sessions, n_captures, written=not dry_run)


def migrate_dir(captures_dir: Path, *, dry_run: bool = False) -> list[MigrationResult]:
    """Migrate every legacy ``*.yaml`` capture file in ``captures_dir``.

    Fail-fast: the first :class:`MigrationError` aborts (later files untouched),
    so a partial run never mixes verified and unverified conversions silently.
    """
    results: list[MigrationResult] = []
    for yaml_path in capture_io.find_legacy_yaml(captures_dir):
        results.append(migrate_file(yaml_path, dry_run=dry_run))
    return results
