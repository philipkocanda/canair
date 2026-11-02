"""What the ``captures --step`` viewer says happened between two frames.

Stepping from one frame to the next can jump across a recorded gap (a disconnect, a
pause, an ECU falling silent, a PID taken out of the poll set). The frame after one says
so, from the session's ``link_events`` (:mod:`canlib.captures.link_events`), so a value
that "jumped" across a minute of missing data is not read as a step change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from canlib.analysis.fill import format_hold_duration
from canlib.captures.link_events import (
    DISCONNECT,
    ECU_SILENT,
    PAUSED,
    UNPOLLED,
    gaps_between,
)
from canlib.captures.types import CaptureEntry
from canlib.states.spans import parse_time_key


def _key(dt: datetime) -> float:
    return dt.hour * 3600.0 + dt.minute * 60.0 + dt.second + dt.microsecond / 1e6


def _clock(raw: Any) -> str:
    return str(raw).split(".")[0]


def describe_gap(event: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """``disconnect 14:02:11-14:03:20 (1m)``, ``BMS silent from 14:05:00``, …"""
    kind = event.get("kind")
    rx = str(event.get("rx") or "")
    name = names.get(rx.upper(), rx)
    what = {
        DISCONNECT: "disconnect",
        PAUSED: "paused",
        ECU_SILENT: f"{name} silent",
        UNPOLLED: f"{name}:{event['pid']} unpolled" if event.get("pid") else f"{name} unpolled",
    }.get(str(kind), str(kind))
    start, end = parse_time_key(event.get("at")), parse_time_key(event.get("until"))
    if end is None or start is None:
        return f"{what} from {_clock(event.get('at'))}"
    length = format_hold_duration(end - start)
    return f"{what} {_clock(event.get('at'))}-{_clock(event.get('until'))} ({length})"


def gaps_before(
    frame: Sequence[CaptureEntry],
    anchor: datetime,
    previous: Sequence[CaptureEntry],
    previous_anchor: datetime | None,
) -> list[Mapping[str, Any]]:
    """The recorded gaps between the previous frame and this one that cover what it shows.

    Read from the session of the frame's first capture; when the previous frame belongs
    to another session (or there is none), everything from the session's start counts.
    """
    if not frame:
        return []
    lead = frame[0]
    events = lead.get("session_link_events") or []
    if not events:
        return []
    same_session = bool(previous) and _session(previous[0]) == _session(lead)
    lo = _key(previous_anchor) if same_session and previous_anchor is not None else -1.0
    hi = _key(anchor)
    # Only what is new since the previous frame: one that began, or ended, in between.
    # An open gap that began earlier would otherwise repeat on every later frame.
    return [e for e in gaps_between(events, lo, hi, _scopes(frame)) if _new_since(e, lo, hi)]


def _new_since(event: Mapping[str, Any], lo: float, hi: float) -> bool:
    start = parse_time_key(event.get("at"))
    end = parse_time_key(event.get("until"))
    return (start is not None and start > lo) or (end is not None and end <= hi)


def _session(entry: CaptureEntry) -> tuple[str, int]:
    return (str(entry.get("_path") or entry.get("file", "")), int(entry.get("_session_idx", 0)))


def _scopes(entries: Iterable[CaptureEntry]) -> list[tuple[str, str]]:
    return [(str(e.get("ecu_addr")), str(e.get("pid"))) for e in entries if e.get("ecu_addr")]
