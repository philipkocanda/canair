#!/usr/bin/env python3
"""Shared discovery→candidate promotion for the analysis verbs.

``hunt`` and ``correlate`` both turn a strong analysis hit into an enabled,
unverified candidate parameter. The write goes through the same
snapshot → edit → schema-validate → auto-revert gate as ``canair pids
upsert-param`` (via ``pids._guarded``), so a promoted expression that fails
schema validation (e.g. a PCI-crossing multibyte read) is rejected and rolled
back rather than committed.
"""

from __future__ import annotations

from pathlib import Path

_GREEN = "\033[92m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def write_candidate(
    ecu: str, pid: str, name: str, expr: str, *, source: str, notes: str
) -> Path:
    """Guarded upsert of one enabled, unverified candidate param.

    Returns the written file path. Raises ``PidsEditError``/``SystemExit`` (from
    the guard) if the edit fails schema validation and is reverted.
    """
    from canlib.commands.pids import _guarded
    from canlib.pids_edit import upsert_parameter

    def do():
        upsert_parameter(
            ecu, pid, name, expr,
            source=source, notes=notes, verified=False, enabled=True,
        )

    return _guarded(ecu, None, do, validate=True)


def print_promoted(ecu: str, pid: str, name: str, expr: str, r: float, fpath: Path) -> None:
    print(
        f"{_GREEN}✓ promoted{_RESET} {ecu} {pid} {name} = {_BOLD}{expr}{_RESET} "
        f"{_DIM}(r={r:+.3f}, {fpath.name}){_RESET}"
    )
    print(
        f"  {_DIM}Review + verify, then: canair pids upsert-param {ecu} {pid} {name} "
        f'"{expr}" --verified{_RESET}'
    )
