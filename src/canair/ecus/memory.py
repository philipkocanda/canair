"""An ECU's ``memory:`` section: how its memory reads are formed, and what a scan found.

::

    memory:
      format: "24"          # UDS ALFID (size nibble, address nibble), or kwp
      max_block: 128        # the largest read that worked, over the transport in use
      session: "03"         # the session the reads need, when not the default
      regions:              # written by `canair scan memory`
        - {start: 0xFEDE0000, end: 0xFEDFFFFF, result: readable, software: "220", scanned: …}

A KWP2000 ECU's form is fixed, so it reads memory with no ``format:``. The section is
written by ``canair memory set`` and ``canair scan memory``
(:mod:`canair.yaml_edit.memory`) and checked by :mod:`canair.validation.memory`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from canair.uds.memory import (
    MAX_READ_BYTES,
    MemoryFormat,
    MemoryRequestError,
    RegionResult,
    default_format,
    parse_format,
)
from canair.uds.protocol import DiagProtocol, classify_protocol

SECTION: Final = "memory"
MEMORY_FIELDS: Final = ("format", "max_block", "session", "regions")
REGION_FIELDS: Final = ("start", "end", "result", "nrc", "software", "scanned")


@dataclass(frozen=True)
class MemoryRegion:
    """One stretch of addresses a scan found answering the same way."""

    start: int
    end: int
    result: str
    nrc: int | None = None
    software: str | None = None
    scanned: str | None = None

    @property
    def readable(self) -> bool:
        return self.result == RegionResult.READABLE


@dataclass(frozen=True)
class EcuMemory:
    """The parsed ``memory:`` section (``format`` already defaulted for a KWP2000 ECU)."""

    format: MemoryFormat | None
    max_block: int | None = None
    session: str | None = None
    regions: tuple[MemoryRegion, ...] = ()


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError:
            return None
    return None


def protocol_of(ecu_def: Mapping) -> DiagProtocol:
    return classify_protocol((ecu_def.get("identity") or {}).get("id_protocol"))


def memory_format_of(ecu_def: Mapping | None) -> MemoryFormat | None:
    """The format the ECU's memory reads take: declared, or the one its protocol fixes."""
    if not isinstance(ecu_def, Mapping):
        return None
    section = ecu_def.get(SECTION)
    if isinstance(section, Mapping) and section.get("format") is not None:
        try:
            return parse_format(section["format"])
        except MemoryRequestError:
            return None
    return default_format(protocol_of(ecu_def))


def _region(entry: Mapping) -> MemoryRegion | None:
    start, end = _int(entry.get("start")), _int(entry.get("end"))
    if start is None or end is None:
        return None
    software = entry.get("software")
    scanned = entry.get("scanned")
    return MemoryRegion(
        start,
        end,
        str(entry.get("result") or ""),
        _int(entry.get("nrc")),
        None if software is None else str(software),
        None if scanned is None else str(scanned),
    )


def ecu_memory(ecu_def: Mapping | None) -> EcuMemory | None:
    """The ECU's memory section, or ``None`` when it has none and no fixed form.

    Tolerant: a malformed field reads as absent (``canair validate pids`` reports it).
    """
    if not isinstance(ecu_def, Mapping):
        return None
    fmt = memory_format_of(ecu_def)
    section = ecu_def.get(SECTION)
    if not isinstance(section, Mapping):
        return EcuMemory(fmt) if fmt is not None else None
    max_block = _int(section.get("max_block"))
    session = section.get("session")
    regions = tuple(
        r
        for r in (_region(e) for e in section.get("regions") or [] if isinstance(e, Mapping))
        if r is not None
    )
    return EcuMemory(
        fmt,
        max_block if max_block and max_block > 0 else None,
        None if session is None else str(session).upper(),
        regions,
    )


def section_problems(section: Any) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for one ``memory:`` mapping, each without a location prefix."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(section, Mapping):
        return ["memory: must be a mapping"], []
    for key in section:
        if key not in MEMORY_FIELDS:
            warnings.append(f"memory: unknown field '{key}'")
    if "format" in section:
        try:
            parse_format(section["format"])
        except MemoryRequestError as e:
            errors.append(f"memory: {e}")
    if "max_block" in section:
        mb = section["max_block"]
        if isinstance(mb, bool) or not isinstance(mb, int) or not 1 <= mb <= MAX_READ_BYTES:
            errors.append(f"memory: max_block must be an integer 1-{MAX_READ_BYTES}")
    if "session" in section:
        s = section["session"]
        if not isinstance(s, str) or len(s) != 2 or _int(f"0x{s}") is None:
            errors.append('memory: session must be a quoted two-digit hex session mode ("03")')
    regions = section.get("regions")
    if regions is not None and not isinstance(regions, list):
        errors.append("memory: regions must be a list")
    results = {r.value for r in RegionResult}
    for i, entry in enumerate(regions or [] if isinstance(regions, list) else []):
        where = f"memory: regions[{i}]"
        if not isinstance(entry, Mapping):
            errors.append(f"{where} must be a mapping")
            continue
        for key in entry:
            if key not in REGION_FIELDS:
                warnings.append(f"{where}: unknown field '{key}'")
        start, end = _int(entry.get("start")), _int(entry.get("end"))
        if start is None or end is None or start < 0 or end < start:
            errors.append(f"{where}: start and end must be addresses with start <= end")
        if entry.get("result") not in results:
            errors.append(f"{where}: result must be one of {', '.join(sorted(results))}")
        nrc = entry.get("nrc")
        if nrc is not None and (_int(nrc) is None or not 0 <= (_int(nrc) or 0) <= 0xFF):
            errors.append(f"{where}: nrc must be a byte")
    return errors, warnings
