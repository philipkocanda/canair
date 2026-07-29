"""Capture-store field migration: rename the ``ecu`` field to ``rx``.

The persisted capture record's ``ecu`` field actually holds the ECU's CAN
*response* address (RX = request TX + 8, e.g. ``"0x7EC"``), not an ECU name — so
it was renamed ``ecu`` → ``rx`` to stop it being confused with the in-memory
resolved short name (which keeps the ``ecu`` key). See
``plans/2026-07-28-captures-rx-field-rename-and-typing.md``.

This module rewrites existing capture files in place, swapping the key at both
the capture level and inside ``scan_results.responding[]``, preserving field
order (the key is renamed in place, not moved to the end). It is idempotent — a
file already on ``rx`` is left untouched — and each rewrite goes through
:func:`canlib.capture_io.dump_capture_file` (atomic). Backs the user-facing
``canair captures migrate-rx`` subcommand. Distinct from ``capture_migrate.py``
(the YAML→JSON store cutover), which is structure-opaque and won't rename fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import capture_io


@dataclass
class FieldMigrationResult:
    """Outcome of migrating one capture file's ``ecu`` → ``rx``."""

    path: Path
    renamed: int  # number of ``ecu`` keys renamed (capture + responding entries)
    written: bool  # False in --dry-run, or when nothing needed renaming


def _rename_key(d: dict[str, Any]) -> int:
    """Rename ``ecu`` → ``rx`` in ``d`` in place, preserving field order.

    Returns 1 if a rename happened, else 0. A pre-existing ``rx`` wins (the
    stale ``ecu`` is dropped) — this shouldn't occur but keeps the result valid.
    """
    if "ecu" not in d:
        return 0
    rebuilt = {
        ("rx" if k == "ecu" else k): v for k, v in d.items() if not (k == "ecu" and "rx" in d)
    }
    d.clear()
    d.update(rebuilt)
    return 1


def _rename_doc(data: Any) -> int:
    """Rename ``ecu`` → ``rx`` throughout a parsed capture doc. Returns the count."""
    renamed = 0
    if not isinstance(data, dict):
        return 0
    for session in data.get("sessions", []) or []:
        if not isinstance(session, dict):
            continue
        for cap in session.get("captures", []) or []:
            if not isinstance(cap, dict):
                continue
            renamed += _rename_key(cap)
            sr = cap.get("scan_results")
            if isinstance(sr, dict):
                for entry in sr.get("responding", []) or []:
                    if isinstance(entry, dict):
                        renamed += _rename_key(entry)
    return renamed


def migrate_file(path: Path, *, dry_run: bool = False) -> FieldMigrationResult:
    """Rename ``ecu`` → ``rx`` in one capture file (idempotent).

    Nothing is written when the file has no ``ecu`` key (already migrated) or in
    ``dry_run``.
    """
    data = capture_io.load_capture_file(path)
    renamed = _rename_doc(data)
    write = renamed > 0 and not dry_run
    if write:
        capture_io.dump_capture_file(path, data)
    return FieldMigrationResult(path, renamed, written=write)


def migrate_dir(captures_dir: Path, *, dry_run: bool = False) -> list[FieldMigrationResult]:
    """Rename ``ecu`` → ``rx`` in every capture file in ``captures_dir``."""
    return [migrate_file(p, dry_run=dry_run) for p in capture_io.iter_capture_files(captures_dir)]
