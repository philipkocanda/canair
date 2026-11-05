"""Editor for an ECU's ``memory:`` section (see :mod:`canair.ecus.memory`).

Two writers share one shape: ``canair pids set-memory`` sets the format, the largest block
and the session, and ``canair scan memory`` records the regions it mapped (and the
largest block it found). Both go through :func:`canair.yaml_edit.ecu.edit_ecu`, so the
write is validated, comment-preserving and reverted on any failure.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarint import HexCapsInt
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from canair.ecus.memory import MEMORY_FIELDS, SECTION, MemoryRegion, section_problems
from canair.uds.memory import MemoryFormat

from .ecu import EcusEditError, edit_ecu, find_ecu_file_by_tx, tx_key


@contextmanager
def _edit_ecu_def(tx_id: int, ecus_dir: Path | None) -> Iterator[tuple[Path, CommentedMap]]:
    """Open the ECU whose ``tx_id`` this is; yields its file and its own mapping."""
    fpath = find_ecu_file_by_tx(tx_id, ecus_dir)
    if fpath is None:
        raise EcusEditError(f"ECU {tx_key(tx_id)} not registered; call `canair ecu add` first")
    with edit_ecu(fpath, None) as doc:
        ecu_def = next(
            (
                v
                for v in doc.data.values()
                if isinstance(v, CommentedMap) and v.get("tx_id") == tx_id
            ),
            None,
        )
        if ecu_def is None:
            raise EcusEditError(f"{fpath.name}: no ECU with tx_id {tx_key(tx_id)}")
        yield fpath, ecu_def


def _section(ecu_def: CommentedMap) -> CommentedMap:
    section = ecu_def.get(SECTION)
    if section is None:
        section = CommentedMap()
        ecu_def[SECTION] = section
    if not isinstance(section, CommentedMap):
        raise EcusEditError("memory: must be a mapping to edit it")
    return section


def _ordered(section: CommentedMap) -> None:
    """Put the section's keys in the documented order (each value keeps its comments)."""
    for key in [k for k in MEMORY_FIELDS if k in section]:
        section.move_to_end(key)


def _checked(section: CommentedMap) -> None:
    errors, _ = section_problems(section)
    if errors:
        raise EcusEditError("; ".join(errors))


def set_memory(
    tx_id: int,
    *,
    fmt: MemoryFormat | None = None,
    max_block: int | None = None,
    session: str | None = None,
    clear: Iterable[str] = (),
    ecus_dir: Path | None = None,
) -> Path:
    """Set the ECU's memory ``format``/``max_block``/``session``; ``clear`` names fields to drop.

    Returns the file written. Raises :class:`EcusEditError` for an unknown ECU or a value
    the section's checks refuse.
    """
    with _edit_ecu_def(tx_id, ecus_dir) as (fpath, ecu_def):
        section = _section(ecu_def)
        for key in clear:
            if key not in ("format", "max_block", "session"):
                raise EcusEditError(f"cannot clear memory field {key!r}")
            section.pop(key, None)
        if fmt is not None:
            section["format"] = DoubleQuotedScalarString(fmt.name)
        if max_block is not None:
            section["max_block"] = int(max_block)
        if session is not None:
            section["session"] = DoubleQuotedScalarString(session.strip().upper())
        _ordered(section)
        _checked(section)
        if not section:
            del ecu_def[SECTION]
    return fpath


def _region_map(region: MemoryRegion, width: int) -> CommentedMap:
    entry = CommentedMap()
    entry["start"] = HexCapsInt(region.start, width=width)
    entry["end"] = HexCapsInt(region.end, width=width)
    entry["result"] = str(region.result)
    if region.nrc is not None:
        entry["nrc"] = HexCapsInt(region.nrc, width=2)
    if region.software is not None:
        entry["software"] = DoubleQuotedScalarString(region.software)
    if region.scanned is not None:
        entry["scanned"] = DoubleQuotedScalarString(region.scanned)
    entry.fa.set_flow_style()
    return entry


def record_scan(
    tx_id: int,
    regions: list[MemoryRegion],
    *,
    fmt: MemoryFormat | None = None,
    max_block: int | None = None,
    ecus_dir: Path | None = None,
) -> Path:
    """Record what ``canair scan memory`` found: its regions, and the format and block it used.

    A new region replaces every recorded region it overlaps; the rest are kept, so a scan of
    one range never erases what a scan of another found. Regions stay sorted by address.
    """
    width = 2 * fmt.address_bytes if fmt is not None else 8
    with _edit_ecu_def(tx_id, ecus_dir) as (fpath, ecu_def):
        section = _section(ecu_def)
        if fmt is not None:
            section["format"] = DoubleQuotedScalarString(fmt.name)
        if max_block is not None:
            section["max_block"] = int(max_block)
        old = section.get("regions")
        kept = [
            e
            for e in (old if isinstance(old, list) else [])
            if not any(_overlaps(e, r) for r in regions)
        ]
        merged = kept + [_region_map(r, width) for r in regions]
        merged.sort(key=lambda e: int(e.get("start", 0)))
        seq = CommentedSeq(merged)
        section["regions"] = seq
        _ordered(section)
        _checked(section)
    return fpath


def _overlaps(entry, region: MemoryRegion) -> bool:
    try:
        start, end = int(entry.get("start")), int(entry.get("end"))
    except (AttributeError, TypeError, ValueError):
        return False
    return start <= region.end and region.start <= end
