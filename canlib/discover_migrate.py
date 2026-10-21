"""Repair discover captures saved before each responder recorded its probed TX.

Before the fix, a discover capture's ``scan_results.responding[]`` entries held
only an ``rx``, and for an address not in the ECU registry that ``rx`` was
computed as ``TX + 0x08`` regardless of the profile's ``addressing:`` block — so
on a ``+0x20`` profile it disagreed with the RX the sweep printed, and three
unregistered probes landed on registered ECUs' response addresses.

The probed TX was never stored, but the old writer makes it recoverable exactly:
it labelled a *registered* responder ``"NAME (status)"`` (with that ECU's
registry ``rx_id`` as its ``rx``) and an *unregistered* one with the bare status
(``"positive"`` / ``"NRC 0x12"``, with ``rx = TX + 0x08``). So the label says
which inverse to apply. The repaired entry carries ``tx`` and the ``rx`` the
current code would record (:func:`canlib.ecus.rx_for_tx` under the profile's
addressing), with fields in the builder's order.

Idempotent: an entry that already has ``tx`` is left alone, and a file with
nothing to repair is not rewritten. A responder whose label names an ECU that no
longer resolves is reported and skipped, never guessed. The session's
``transport`` cannot be recovered from the data — correct it with
``captures uds --set-transport``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import capture_io
from .addressing import AddressingMode
from .ecus import EcuNameCollision, EcuRegistryEntry, build_name_tx_index, rx_for_tx

# The offset the old writer applied to an unregistered TX, whatever the profile.
LEGACY_FALLBACK_OFFSET = 0x08

_NAMED_LABEL = re.compile(r"^(?P<name>\S+) \((?P<status>.+)\)$")


@dataclass(frozen=True)
class Addressing:
    """What the repair resolves addresses against: one profile's registry and rule."""

    ecus: Mapping[int, EcuRegistryEntry]
    rx_offset: int
    mode: AddressingMode


@dataclass(frozen=True)
class Repair:
    """One responder entry given its ``tx`` (and, possibly, a corrected ``rx``)."""

    session_idx: int
    response: str
    old_rx: str
    tx: str
    rx: str


@dataclass(frozen=True)
class Skip:
    """One responder entry that could not be repaired, and why."""

    session_idx: int
    response: str
    reason: str


@dataclass
class DiscoverMigrationResult:
    """Outcome of repairing one capture file's discover captures."""

    path: Path
    repairs: list[Repair] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    written: bool = False  # False in --dry-run, or when nothing needed repairing


def _hex(can_id: int) -> str:
    return f"0x{can_id:03X}"


def infer_tx(entry: Mapping[str, Any], names: Mapping[str, int]) -> int | str:
    """The TX a legacy responder entry was probed at, or the reason it is unknown.

    ``names`` maps upper-cased ECU name/alias to TX id.
    """
    rx_text = entry.get("rx")
    if not isinstance(rx_text, str):
        return "no rx (run `canair captures migrate-rx` first)"
    try:
        old_rx = int(rx_text, 16)
    except ValueError:
        return f"rx {rx_text!r} is not a hex address"

    match = _NAMED_LABEL.match(str(entry.get("response", "")))
    if match is None:
        # Unregistered when recorded: the old writer used TX + 0x08.
        return old_rx - LEGACY_FALLBACK_OFFSET
    name = match.group("name")
    tx = names.get(name.upper())
    if tx is None:
        return f"ECU {name!r} is no longer in the registry"
    return tx


def _repair_entry(entry: dict[str, Any], tx: int, addressing: Addressing) -> str:
    """Rewrite ``entry`` in place as ``{tx, rx, …rest}``; return the new ``rx``."""
    rx = _hex(rx_for_tx(tx, addressing.ecus, addressing.rx_offset, addressing.mode))
    rest = {k: v for k, v in entry.items() if k not in ("tx", "rx")}
    entry.clear()
    entry.update({"tx": _hex(tx), "rx": rx, **rest})
    return rx


def _is_discover(capture: Any) -> bool:
    return (
        isinstance(capture, dict)
        and capture.get("rx") == "broadcast"
        and str(capture.get("pid", "")).startswith("discover ")
    )


def repair_doc(data: Any, addressing: Addressing) -> tuple[list[Repair], list[Skip]]:
    """Repair every legacy discover responder in a parsed capture doc, in place."""
    try:
        names = build_name_tx_index(addressing.ecus)
    except EcuNameCollision:
        # An ambiguous name could map a label to the wrong TX; refuse them all.
        names = {}
    repairs: list[Repair] = []
    skipped: list[Skip] = []
    sessions = data.get("sessions", []) if isinstance(data, dict) else []
    for idx, session in enumerate(sessions or []):
        if not isinstance(session, dict):
            continue
        for cap in session.get("captures", []) or []:
            if not _is_discover(cap):
                continue
            for entry in (cap.get("scan_results") or {}).get("responding", []) or []:
                if not isinstance(entry, dict) or "tx" in entry:
                    continue
                response = str(entry.get("response", ""))
                tx = infer_tx(entry, names)
                if isinstance(tx, str):
                    skipped.append(Skip(idx, response, tx))
                    continue
                old_rx = str(entry.get("rx"))
                rx = _repair_entry(entry, tx, addressing)
                repairs.append(Repair(idx, response, old_rx, _hex(tx), rx))
    return repairs, skipped


def migrate_file(
    path: Path, addressing: Addressing, *, dry_run: bool = False
) -> DiscoverMigrationResult:
    """Repair one capture file's discover captures (idempotent)."""
    data = capture_io.load_capture_file(path)
    repairs, skipped = repair_doc(data, addressing)
    write = bool(repairs) and not dry_run
    if write:
        capture_io.dump_capture_file(path, data)
    return DiscoverMigrationResult(path, repairs, skipped, written=write)


def migrate_dir(
    captures_dir: Path, addressing: Addressing, *, dry_run: bool = False
) -> list[DiscoverMigrationResult]:
    """Repair the discover captures in every capture file in ``captures_dir``."""
    return [
        migrate_file(p, addressing, dry_run=dry_run)
        for p in capture_io.iter_capture_files(captures_dir)
    ]
