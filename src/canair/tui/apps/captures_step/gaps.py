"""What the ``captures --step`` viewer says happened between two frames.

Stepping from one frame to the next can jump across a recorded gap (a disconnect, a
pause, an ECU falling silent, a PID taken out of the poll set). The frame after one says
so, from the session's ``link_events`` (:mod:`canair.captures.link_events`), so a value
that "jumped" across a minute of missing data is not read as a step change.

It also says what the recording switched in between (an output on, a routine started), from
the session's ``actuations`` (:mod:`canair.captures.actuations`), so a value that changed right
after a toggle is read against it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from canair.analysis.fill import format_hold_duration
from canair.captures.actuations import describe
from canair.captures.link_events import (
    DISCONNECT,
    ECU_SILENT,
    PAUSED,
    UNPOLLED,
    gaps_between,
)
from canair.captures.types import CaptureEntry
from canair.states.spans import parse_time_key


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


def actuations_before(
    frame: Sequence[CaptureEntry],
    anchor: datetime,
    previous: Sequence[CaptureEntry],
    previous_anchor: datetime | None,
) -> list[Mapping[str, Any]]:
    """The session's actuations sent after the previous frame and up to this one.

    Every one in the session counts, whatever ECU or PID the frame shows: an output on one ECU
    moves signals on another. When the previous frame belongs to another session (or there is
    none), everything from the session's start counts.
    """
    if not frame:
        return []
    lead = frame[0]
    same_session = bool(previous) and _session(previous[0]) == _session(lead)
    lo = _key(previous_anchor) if same_session and previous_anchor is not None else -1.0
    hi = _key(anchor)
    return [
        a
        for a in lead.get("session_actuations") or []
        if (at := parse_time_key(a.get("at"))) is not None and lo < at <= hi
    ]


def describe_actuation(entry: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """``IGPM iocontrol BC0C on (ok) at 14:02:11``."""
    rx = str(entry.get("rx") or "")
    return f"{names.get(rx.upper(), rx)} {describe(entry)} at {_clock(entry.get('at'))}"


#: The JSON field each finder fills on a frame of ``captures --step --json``.
FINDERS = (("gaps_before", gaps_before), ("actuations_before", actuations_before))


def frame_pair(frames: Sequence[Any], captures: Sequence[CaptureEntry], n: int) -> tuple | None:
    """Frame ``n``'s captures and anchor, and frame ``n - 1``'s, as the finders take them."""
    if not 0 <= n < len(frames):
        return None
    frame, prev = frames[n], frames[n - 1] if n else None
    here = [captures[i] for i in frame.indices if i is not None]
    before = [captures[i] for i in prev.indices if i is not None] if prev else []
    return here, frame.anchor_dt, before, prev.anchor_dt if prev else None


def between_lines(
    gaps: Sequence[Mapping[str, Any]],
    switched: Sequence[Mapping[str, Any]],
    names: Mapping[str, str],
) -> dict[str, str]:
    """The frame header's ``gaps`` and ``actuated`` lines (empty when nothing happened)."""
    return {
        "gaps": " · ".join(describe_gap(e, names) for e in gaps),
        "actuated": " · ".join(describe_actuation(a, names) for a in switched),
    }
