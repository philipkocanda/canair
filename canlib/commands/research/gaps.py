"""``canair research --gaps``: IOControl DID families never read with ``0x22``.

The bridge between the profile and the :mod:`canlib.did_families` leaf. It gathers the
DID ranges ``0x22`` is known to have covered on each ECU, from three places:

* ``scan_log:`` entries for service ``0x22`` (their free-text ``range`` lists),
* ``research:`` scan entries that are no longer pending (their targets),
* saved ``scan range`` sessions (``scan 22 …``) and saved single ``22`` reads,

and the ranges still-open research entries plan to read, which mark a gap as tracked.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from canlib.did_families import FamilyGap, unread_families
from canlib.id_ranges import IdRange, format_id_ranges, parse_id_ranges

_READ = 0x22
# A 4-hex DID or DID range inside free text; a 6-hex token is a PID with its 22 prefix.
_DID = r"([0-9A-Fa-f]{4}|22[0-9A-Fa-f]{4})"
_TOKEN = re.compile(rf"(?<![0-9A-Fa-f]){_DID}(?:-{_DID})?(?![0-9A-Fa-f])")


def did_ranges_in_text(text: str) -> list[IdRange]:
    """DID ranges and single DIDs in free text (``"0101-010F,B001-B010"``, ``"22BE01-22BE03"``)."""
    out: list[IdRange] = []
    for match in _TOKEN.finditer(str(text)):
        start = _did(match.group(1))
        end = _did(match.group(2)) if match.group(2) else start
        if start <= end:
            out.append((start, end))
    return out


def _did(token: str) -> int:
    return int(token[2:] if len(token) == 6 else token, 16)


def _service(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip().removeprefix("0x").removeprefix("0X"), 16)
    except ValueError:
        return None


def _research_target_ranges(entry: Mapping[str, Any]) -> list[IdRange]:
    """The read DIDs a research ``scan`` entry targets, or none if it is not a 22 read."""
    if entry.get("type") != "scan":
        return []
    target = str(entry.get("target", "")).strip().upper()
    if target.startswith("22 "):
        return did_ranges_in_text(target[3:])
    if re.match(r"^22[0-9A-F]{4}", target):
        return did_ranges_in_text(target)
    return []


def profile_read_ranges(ecu_def: Mapping[str, Any]) -> tuple[list[IdRange], list[IdRange]]:
    """``(read, planned)`` 0x22 ranges recorded in the ECU's own definition."""
    read: list[IdRange] = []
    for entry in ecu_def.get("scan_log") or []:
        if isinstance(entry, Mapping) and _service(entry.get("service")) == _READ:
            read.extend(did_ranges_in_text(entry.get("range", "")))
    planned: list[IdRange] = []
    for entry in ecu_def.get("research") or []:
        if not isinstance(entry, Mapping):
            continue
        ranges = _research_target_ranges(entry)
        (planned if entry.get("status") == "pending" else read).extend(ranges)
    return read, planned


def capture_read_ranges(captures: Iterable[Mapping[str, Any]]) -> dict[str, list[IdRange]]:
    """0x22 ranges covered by saved scans and reads, keyed by upper-cased ECU name."""
    out: dict[str, list[IdRange]] = {}
    for cap in captures:
        ecu = str(cap.get("ecu") or "").upper()
        pid = str(cap.get("pid") or "").strip().upper()
        if not ecu:
            continue
        if pid.startswith("SCAN 22 "):
            if not _scan_answered(cap.get("scan_results")):
                continue
            spec = pid[len("SCAN 22 ") :].split(" ", 1)[0]
            try:
                out.setdefault(ecu, []).extend(parse_id_ranges(spec))
            except ValueError:
                continue
        elif len(pid) == 6 and pid.startswith("22"):
            try:
                did = int(pid[2:], 16)
            except ValueError:
                continue
            out.setdefault(ecu, []).append((did, did))
    return out


def _scan_answered(results: Any) -> bool:
    """Whether a saved sweep drew any answer; one the ECU slept through read nothing."""
    if not isinstance(results, Mapping):
        return False
    if results.get("responding"):
        return True
    match = re.search(r"(\d+) NRC", str(results.get("rejected") or ""))
    return bool(match and int(match.group(1)))


def find_gaps(
    ecus: Mapping[str, Mapping[str, Any]],
    captured: Mapping[str, list[IdRange]] | None = None,
    *,
    ecu: str | None = None,
) -> list[dict[str, Any]]:
    """One record per unread family, ordered by ECU then family."""
    records: list[dict[str, Any]] = []
    for name, ecu_def in ecus.items():
        if not isinstance(ecu_def, Mapping) or (ecu and name.upper() != ecu.upper()):
            continue
        read, planned = profile_read_ranges(ecu_def)
        read.extend((captured or {}).get(name.upper(), []))
        for gap in unread_families(ecu_def, read, planned):
            records.append(_record(name, gap))
    return records


def _record(ecu: str, gap: FamilyGap) -> dict[str, Any]:
    dids = gap.iocontrol_dids
    span = f"{dids[0]:04X}" if len(dids) == 1 else f"{dids[0]:04X}-{dids[-1]:04X}"
    rng = format_id_ranges((gap.read_range,), 4)
    return {
        "ecu": ecu,
        "family": f"{gap.high:02X}xx",
        "iocontrol_dids": [f"{d:04X}" for d in dids],
        "iocontrol_span": span,
        "tracked": gap.tracked,
        "command": f"canair scan range {ecu} --service read-did --range {rng} --save",
    }
