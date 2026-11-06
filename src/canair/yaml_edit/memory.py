"""Editor for an ECU's ``memory:`` section (see :mod:`canair.ecus.memory`).

Two writers share one shape: ``canair memory set`` sets the format, the largest block and
the session, and ``canair scan memory`` records the regions it mapped (and the largest
block it found). The section is tool-owned, so it is rendered whole from its parsed value
and swapped in by text surgery (:func:`canair.yaml_edit.text.edit_ecu_text`): every other
byte of the file is left alone, and the write is re-parsed, checked and reverted on failure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from canair.core import yaml_io
from canair.ecus.memory import (
    MEMORY_FIELDS,
    SECTION,
    MemoryRegion,
    memory_format_of,
    section_problems,
)
from canair.profile.model import Profile
from canair.uds.memory import MemoryFormat, MemoryRequestError, memory_request, parse_memory_token

from .ecu import EcusEditError, find_ecu_file_by_tx, tx_key
from .text import _find_ecu_block, _keyed_block, edit_ecu_text, find_ecu_file

_CLEARABLE = ("format", "max_block", "session")


def _ecu_of(tx_id: int, ecus_dir: Path | None, *, profile: Profile) -> tuple[Path, str, dict]:
    """The file, YAML key and parsed mapping of the ECU whose ``tx_id`` this is."""
    fpath = find_ecu_file_by_tx(tx_id, ecus_dir, profile=profile)
    if fpath is None:
        raise EcusEditError(f"ECU {tx_key(tx_id)} not registered; call `canair ecu add` first")
    data = yaml_io.safe_load(fpath.read_text()) or {}
    for name, ecu_def in data.items():
        if isinstance(ecu_def, dict) and ecu_def.get("tx_id") == tx_id:
            return fpath, str(name), ecu_def
    raise EcusEditError(f"{fpath.name}: no ECU with tx_id {tx_key(tx_id)}")


def _num(value: Any) -> int:
    return value if isinstance(value, int) else int(str(value), 0)


def _render(section: Mapping[str, Any], width: int) -> list[str]:
    lines = [f"  {SECTION}:"]
    for key in MEMORY_FIELDS:
        if key not in section or section[key] is None:
            continue
        value = section[key]
        if key == "max_block":
            lines.append(f"    max_block: {int(value)}")
        elif key == "regions":
            lines.append("    regions:")
            for r in value:
                lines.append(f"      - start: 0x{_num(r['start']):0{width}X}")
                lines.append(f"        end: 0x{_num(r['end']):0{width}X}")
                lines.append(f"        result: {r['result']}")
                if r.get("nrc") is not None:
                    lines.append(f"        nrc: 0x{_num(r['nrc']):02X}")
                for text_key in ("software", "scanned"):
                    if r.get(text_key) is not None:
                        escaped = str(r[text_key]).replace("\\", "\\\\").replace('"', '\\"')
                        lines.append(f'        {text_key}: "{escaped}"')
        else:
            lines.append(f'    {key}: "{value}"')
    return lines


def _write(
    fpath: Path, name: str, section: dict[str, Any], width: int, *, profile: Profile
) -> Path:
    errors, _ = section_problems(section)
    if errors:
        raise EcusEditError("; ".join(errors))
    rendered = "".join(line + "\n" for line in _render(section, width)) if section else ""

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, name)
        block = _keyed_block(text, SECTION, 2, ecu_start, ecu_end)
        if block is not None:
            hdr, _line_end, _body_start, body_end, _inline = block
            return text[:hdr] + rendered + text[body_end:]
        body = text[ecu_start:ecu_end].rstrip("\n")
        if not rendered:
            return text
        return text[:ecu_start] + body + "\n" + rendered + text[ecu_end:]

    def check(ecu_def: dict) -> None:
        got = ecu_def.get(SECTION) or {}
        if {k: got.get(k) for k in ("format", "max_block", "session")} != {
            k: section.get(k) for k in ("format", "max_block", "session")
        }:
            raise EcusEditError(f"memory: mismatch after edit (got {got!r})")

    return edit_ecu_text(name, transform, check, pids_dir=fpath.parent, profile=profile)


def memory_pid_key(
    ecu_name: str, pid: str, ecus_dir: Path | None = None, *, profile: Profile
) -> str:
    """The PID key a ``23/ADDRESS+SIZE`` token names on this ECU; any other PID unchanged.

    The key is the full request, built with the ECU's memory format, so an editor can be
    handed the token a user reads with (``canair pids upsert-param BCM 23/FEDE1234+02 …``).
    """
    try:
        if parse_memory_token(pid) is None:
            return pid
    except MemoryRequestError as e:
        raise EcusEditError(str(e)) from None
    fpath = find_ecu_file(ecu_name, ecus_dir, profile=profile)
    data = yaml_io.safe_load(fpath.read_text()) or {}
    ecu_def = next((v for k, v in data.items() if str(k).upper() == ecu_name.upper()), None)
    fmt = memory_format_of(ecu_def)
    if fmt is None:
        raise EcusEditError(
            f"{ecu_name} has no memory format for {pid}: set one with `canair memory set "
            f"{ecu_name} --format 24`, or run `canair scan memory`"
        )
    address, size = parse_memory_token(pid) or (0, 0)
    try:
        return memory_request(fmt, address, size)
    except MemoryRequestError as e:
        raise EcusEditError(str(e)) from None


def set_memory(
    tx_id: int,
    *,
    fmt: MemoryFormat | None = None,
    max_block: int | None = None,
    session: str | None = None,
    clear: Iterable[str] = (),
    ecus_dir: Path | None = None,
    profile: Profile,
) -> Path:
    """Set the ECU's memory ``format``/``max_block``/``session``; ``clear`` names fields to drop.

    Returns the file written. Raises :class:`EcusEditError` for an unknown ECU or a value
    the section's checks refuse.
    """
    fpath, name, ecu_def = _ecu_of(tx_id, ecus_dir, profile=profile)
    section = dict(ecu_def.get(SECTION) or {})
    for key in clear:
        if key not in _CLEARABLE:
            raise EcusEditError(f"cannot clear memory field {key!r}")
        section.pop(key, None)
    if fmt is not None:
        section["format"] = fmt.name
    if max_block is not None:
        section["max_block"] = int(max_block)
    if session is not None:
        section["session"] = session.strip().upper()
    width = 2 * fmt.address_bytes if fmt is not None else 8
    return _write(fpath, name, section, width, profile=profile)


def _overlaps(entry: Mapping, region: MemoryRegion) -> bool:
    try:
        start, end = _num(entry.get("start")), _num(entry.get("end"))
    except (TypeError, ValueError):
        return False
    return start <= region.end and region.start <= end


def record_scan(
    tx_id: int,
    regions: list[MemoryRegion],
    *,
    fmt: MemoryFormat | None = None,
    max_block: int | None = None,
    ecus_dir: Path | None = None,
    profile: Profile,
) -> Path:
    """Record what ``canair scan memory`` found: its regions, and the format and block it used.

    A new region replaces every recorded region it overlaps; the rest are kept, so a scan of
    one range never erases what a scan of another found. Regions stay sorted by address.
    """
    fpath, name, ecu_def = _ecu_of(tx_id, ecus_dir, profile=profile)
    section = dict(ecu_def.get(SECTION) or {})
    if fmt is not None:
        section["format"] = fmt.name
    if max_block is not None:
        section["max_block"] = int(max_block)
    old = section.get("regions")
    kept = [
        dict(e)
        for e in (old if isinstance(old, list) else [])
        if isinstance(e, Mapping) and not any(_overlaps(e, r) for r in regions)
    ]
    new = [
        {
            "start": r.start,
            "end": r.end,
            "result": str(r.result),
            "nrc": r.nrc,
            "software": r.software,
            "scanned": r.scanned,
        }
        for r in regions
    ]
    section["regions"] = sorted(kept + new, key=lambda e: _num(e["start"]))
    width = 2 * fmt.address_bytes if fmt is not None else 8
    return _write(fpath, name, section, width, profile=profile)
