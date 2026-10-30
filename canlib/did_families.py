"""DID families: the high bytes an ECU keeps its 16-bit DIDs under, and the ones never read.

An ECU keeps its DIDs in a few families that share a high byte (an IGPM's ``BCxx``, a BCM's
``B0xx`` and ``C0xx``), and its IOControl DIDs usually sit in the same families as its
readable records. That is a make-neutral prior, used two ways:

* :func:`did_families` / :func:`family_ranges` give ``scan range`` its smart default;
* :func:`unread_families` finds a family whose IOControl DIDs were never read with ``0x22``,
  which is where an ECU known only by its actuators keeps its live data.

A leaf: it works on a loaded ECU definition plus the id ranges the caller knows were read,
and never touches captures, the device or the CLI.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .id_ranges import IdRange

FAMILY_SIZE = 0x100

# Where an ECU's 16-bit DIDs are known from: its 22/2F PID keys, and the actuators an
# IOControl scan or a curated entry has found.
_PIDS = "pids"
_IOCONTROL_FIELDS = ("iocontrol", "iocontrol_discoveries")
_READ_SERVICE = 0x22
_IOCONTROL_SERVICE = 0x2F


def did_families(ecu_def: Mapping[str, Any], *, include_iocontrol: bool = True) -> dict[int, str]:
    """The DID high bytes an ECU is known to use, each with where it was seen.

    Returns ``{high_byte: "PIDs" | "IOControl" | "PIDs+IOControl"}``, sorted by high byte.
    Only 4-hex-digit identifiers count, so KWP2000 local ids never form a family.
    """
    seen: dict[int, list[str]] = {}

    def note(did: int, label: str) -> None:
        labels = seen.setdefault(did >> 8, [])
        if label not in labels:
            labels.append(label)

    for did, _service in _pid_dids(ecu_def):
        note(did, "PIDs")
    if include_iocontrol:
        for did in _iocontrol_dids(ecu_def):
            note(did, "IOControl")
    return {hi: "+".join(seen[hi]) for hi in sorted(seen)}


def family_ranges(families: Mapping[int, str], slots: int = FAMILY_SIZE) -> tuple[IdRange, ...]:
    """One range per family: its first ``slots`` DIDs (``xx00`` up), the whole family by default."""
    if not 1 <= slots <= FAMILY_SIZE:
        raise ValueError(f"slots must be 1-{FAMILY_SIZE}, got {slots}")
    return tuple(((hi << 8), (hi << 8) + slots - 1) for hi in families)


@dataclass(frozen=True)
class FamilyGap:
    """An IOControl family no ``0x22`` read has reached."""

    high: int
    iocontrol_dids: tuple[int, ...]
    tracked: bool  # an open research entry already plans a read of this family

    @property
    def read_range(self) -> IdRange:
        return (self.high << 8, (self.high << 8) | 0xFF)


def unread_families(
    ecu_def: Mapping[str, Any],
    read: Sequence[IdRange],
    planned: Sequence[IdRange] = (),
) -> list[FamilyGap]:
    """IOControl families none of whose actuator DIDs has been read with ``0x22``.

    ``read`` lists the DID ranges ``0x22`` is known to have covered (sweeps and single
    reads); a family with a defined ``22`` PID is read by definition. ``planned`` lists
    the ranges open research entries mean to read, which marks a gap as tracked rather
    than hiding it. A sweep that reached only some of a family's actuator DIDs closes it:
    the question is whether that family answers ``22`` at all.
    """
    io_by_family: dict[int, set[int]] = {}
    for did in _iocontrol_dids(ecu_def):
        io_by_family.setdefault(did >> 8, set()).add(did)
    read_families = {did >> 8 for did, service in _pid_dids(ecu_def) if service == _READ_SERVICE}

    gaps: list[FamilyGap] = []
    for high in sorted(io_by_family):
        dids = tuple(sorted(io_by_family[high]))
        if high in read_families or any(_covered(did, read) for did in dids):
            continue
        tracked = any(_overlaps((high << 8, (high << 8) | 0xFF), r) for r in planned)
        gaps.append(FamilyGap(high=high, iocontrol_dids=dids, tracked=tracked))
    return gaps


def _covered(did: int, ranges: Sequence[IdRange]) -> bool:
    return any(start <= did <= end for start, end in ranges)


def _overlaps(a: IdRange, b: IdRange) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _pid_dids(ecu_def: Mapping[str, Any]) -> list[tuple[int, int]]:
    """``(did, service)`` for each 22/2F PID key (``22BC07``, ``2FB015``)."""
    out: list[tuple[int, int]] = []
    pids = ecu_def.get(_PIDS) or {}
    if not isinstance(pids, Mapping):
        return out
    for key in pids:
        k = str(key).upper().removeprefix("0X")
        if len(k) != 6:
            continue
        try:
            service, did = int(k[:2], 16), int(k[2:], 16)
        except ValueError:
            continue
        if service in (_READ_SERVICE, _IOCONTROL_SERVICE):
            out.append((did, service))
    return out


def _iocontrol_dids(ecu_def: Mapping[str, Any]) -> list[int]:
    """Actuator DIDs from the curated and discovered IOControl entries and 2F PID keys.

    A discovery that only drew an NRC does not count: some ECUs (this car's DDM) refuse
    every DID of a range with the same NRC, so it proves nothing about the DID.
    """
    out = [did for did, service in _pid_dids(ecu_def) if service == _IOCONTROL_SERVICE]
    for field in _IOCONTROL_FIELDS:
        entries = ecu_def.get(field) or {}
        if not isinstance(entries, Mapping):
            continue
        for key, entry in entries.items():
            if field == "iocontrol_discoveries" and not _answered(entry):
                continue
            k = str(key).upper().removeprefix("0X")
            if len(k) != 4:
                continue
            try:
                out.append(int(k, 16))
            except ValueError:
                continue
    return out


def _answered(entry: Any) -> bool:
    """A discovery the ECU answered positively (it has a response and no NRC)."""
    if not isinstance(entry, Mapping):
        return True
    return bool(entry.get("response")) and entry.get("nrc") is None
