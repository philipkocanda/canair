#!/usr/bin/env python3
"""Shared discovery→candidate promotion for the analysis verbs.

``hunt`` and ``correlate`` both turn a strong analysis hit into an enabled,
unverified candidate parameter. The write goes through the same
snapshot → edit → schema-validate → auto-revert gate as ``canair pids
upsert-param`` (via ``pids._guarded``), so a promoted expression that fails
schema validation is rejected and rolled back rather than committed.

A **PCI-crossing read is refused up front** (see :func:`_reject_pci_reads`)
rather than left to that gate: reading an ISO-TP framing byte yields the frame
header instead of data, and profile-wide validation only *warns* about it (so
existing community profiles don't hard-fail), which means the guard alone would
happily commit one.
"""

from __future__ import annotations

from pathlib import Path

_GREEN = "\033[92m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _reject_pci_reads(ecu: str, pid: str, name: str, expr: str) -> None:
    """Raise ``PidsEditError`` if ``expr`` reads or spans an ISO-TP PCI byte.

    Promoting such an expression persists a knowingly-wrong signal: the value
    would include a frame counter/length byte. A multi-byte value whose data
    bytes straddle a PCI byte is still promotable — as a shift composition that
    skips the framing byte (``(B7 << 8) | B9``) rather than a range spanning it
    (``[B7:B9]``).
    """
    from canlib.commands.validate.pids import check_pci_bytes
    from canlib.pids_edit import PidsEditError

    problems = check_pci_bytes(expr, name, pid, ecu)
    if problems:
        detail = "; ".join(problems)
        raise PidsEditError(
            f"refusing to promote {ecu} {pid} {name} = {expr!r}: {detail}. "
            "Re-express it as a shift composition that skips the framing byte."
        )


def write_candidate(ecu: str, pid: str, name: str, expr: str, *, source: str, notes: str) -> Path:
    """Guarded upsert of one enabled, unverified candidate param.

    Returns the written file path. Raises ``PidsEditError``/``SystemExit`` (from
    the guard, or from the PCI pre-check) if the expression is unfit or the edit
    fails schema validation and is reverted.
    """
    from canlib.commands.pids import _guarded
    from canlib.pids_edit import upsert_parameter

    _reject_pci_reads(ecu, pid, name, expr)

    def do():
        upsert_parameter(
            ecu,
            pid,
            name,
            expr,
            source=source,
            notes=notes,
            verified=False,
            enabled=True,
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
