"""Link events: when a recording could not see the car, and why.

A ``monitor --save`` session that loses its link keeps recording across the gap, so its
timestamps show a hole but nothing says what caused it. A run-length (``keep:changes``)
session cannot even show the hole: an absent row there means "the value did not change".
A session's ``link_events`` list records it instead: intervals in which no ECU answered
coherently (``disconnect``) and instants at which the transport had to realign itself
(``resync``). See ``plans/2026-11-02-capture-link-events.md``.

Deliberately not part of ``state_spans``: spans describe the car, are derived from stored
payloads (and so rebuilt by a back-fill), and cannot express a gap. A link event describes
the recorder, exists only because the recorder saw it, and is never recomputed.

Events carry enums and counts only, never free text: an exception message can name a
device address, and capture files are contributed upstream.

This module is a **pure leaf** (same discipline as :mod:`canlib.states.spans`): no
capture-store, profile or transport imports.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal, NotRequired, TypedDict, get_args

from canlib.states.spans import parse_time_key

LinkEventKind = Literal["disconnect", "resync"]
LINK_EVENT_KINDS: tuple[LinkEventKind, ...] = get_args(LinkEventKind)

DisconnectCause = Literal["error", "stale", "manual"]
"""Why a disconnect began: the transport raised (``error``), the liveness check found
no coherent answer for several poll cycles (``stale``), or the user asked for a
reconnect (``manual``)."""
DISCONNECT_CAUSES: tuple[DisconnectCause, ...] = get_args(DisconnectCause)

DISCONNECT: LinkEventKind = "disconnect"
RESYNC: LinkEventKind = "resync"

# Kinds that span time, and so carry an ``until`` (absent = to the end of the recording).
INTERVAL_KINDS: frozenset[str] = frozenset({DISCONNECT})

# Journal record types (see canlib.captures.journal).
JOURNAL_EVENT = "event"
JOURNAL_EVENT_END = "event_end"

MIDNIGHT = "00:00:00.000"


class LinkEvent(TypedDict):
    """One entry of a session's ``link_events`` (mirrors ``captures_schema.json``)."""

    at: str
    kind: LinkEventKind
    until: NotRequired[str]
    cause: NotRequired[DisconnectCause]
    count: NotRequired[int]


def format_time(moment: datetime) -> str:
    """``HH:MM:SS.fff``, the precision the monitor stamps captures with."""
    return moment.strftime("%H:%M:%S.%f")[:-3]


# ---------------------------------------------------------------------------
# Journal records → per-day event lists
# ---------------------------------------------------------------------------


def _event_from_record(rec: Mapping[str, Any]) -> LinkEvent | None:
    kind = rec.get("kind")
    at = rec.get("at")
    if kind not in LINK_EVENT_KINDS or parse_time_key(at) is None:
        return None
    event: LinkEvent = {"at": str(at), "kind": kind}
    cause = rec.get("cause")
    if kind == DISCONNECT and cause in DISCONNECT_CAUSES:
        event["cause"] = cause
    count = rec.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count > 1:
        event["count"] = count
    return event


def events_by_date(records: Iterable[Mapping[str, Any]], fallback_date: str) -> dict[str, list]:
    """Pair a journal's ``event``/``event_end`` records into per-day ``link_events`` lists.

    An interval event's start carries an ``id`` its end repeats. An unmatched start stays
    open (``until`` absent: it lasted until the recording ended); an unmatched end is
    ignored. An interval ending on a later day than it began is split the way a session's
    captures are: open-ended on its start day, then from midnight to its end on the next
    (and every whole day between, open-ended). Each day's list is sorted by ``at``.
    """
    starts: dict[Any, tuple[str, LinkEvent]] = {}
    ends: dict[Any, tuple[str, str]] = {}
    placed: list[tuple[str, LinkEvent]] = []
    for rec in records:
        rtype = rec.get("type")
        rdate = str(rec.get("date") or fallback_date)
        if rtype == JOURNAL_EVENT:
            event = _event_from_record(rec)
            if event is None:
                continue
            if event["kind"] in INTERVAL_KINDS and rec.get("id") is not None:
                starts[rec["id"]] = (rdate, event)
            placed.append((rdate, event))
        elif rtype == JOURNAL_EVENT_END and rec.get("id") is not None:
            if parse_time_key(rec.get("at")) is not None:
                ends[rec["id"]] = (rdate, str(rec["at"]))

    by_day: dict[str, list] = {}
    for rdate, event in placed:
        by_day.setdefault(rdate, []).append(event)
    for eid, (start_day, event) in starts.items():
        end = ends.get(eid)
        if end is None:
            continue
        end_day, end_at = end
        if end_day == start_day:
            event["until"] = end_at
            continue
        for day in _days_after(start_day, end_day):
            copy: LinkEvent = {**event, "at": MIDNIGHT}
            if day == end_day:
                copy["until"] = end_at
            by_day.setdefault(day, []).append(copy)
    for events in by_day.values():
        events.sort(key=lambda e: parse_time_key(e.get("at")) or 0.0)
    return by_day


def _days_after(start: str, end: str) -> list[str]:
    """The ISO dates after ``start`` up to and including ``end`` (empty if unparseable)."""
    try:
        lo, hi = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    except ValueError:
        return []
    days: list[str] = []
    day = lo + timedelta(days=1)
    while day <= hi:
        days.append(day.isoformat())
        day += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def session_link_events(session: Mapping[str, Any]) -> list[LinkEvent]:
    """A session's ``link_events``, or ``[]`` (tolerant of a malformed block)."""
    events = session.get("link_events")
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, Mapping) and e.get("kind") in LINK_EVENT_KINDS]


def gap_intervals(events: Sequence[Mapping[str, Any]]) -> list[tuple[float, float | None]]:
    """Each disconnect as ``(start, end)`` seconds since midnight; ``end`` None = open.

    Sorted by start. Instant events are not gaps and are left out.
    """
    out: list[tuple[float, float | None]] = []
    for e in events:
        if e.get("kind") not in INTERVAL_KINDS:
            continue
        start = parse_time_key(e.get("at"))
        if start is None:
            continue
        out.append((start, parse_time_key(e.get("until"))))
    out.sort(key=lambda gap: gap[0])
    return out


def in_closed_gap(gaps: Sequence[tuple[float, float | None]], key: float) -> bool:
    """Whether ``key`` falls strictly inside a closed gap (nothing answered then)."""
    return any(end is not None and start < key < end for start, end in gaps)


class GapSummary(TypedDict):
    """What :func:`summarize` reports for one session."""

    disconnects: int
    gap_seconds: float
    resyncs: int


def summarize(events: Sequence[Mapping[str, Any]], session_end: float | None) -> GapSummary:
    """Disconnect count, total gap time and resync count for one session.

    An open disconnect is measured to ``session_end`` (seconds since midnight, the
    session's last capture), or counted with no duration when that is unknown.
    """
    disconnects = 0
    seconds = 0.0
    for start, end in gap_intervals(events):
        disconnects += 1
        stop = end if end is not None else session_end
        if stop is not None and stop > start:
            seconds += stop - start
    resyncs = sum(int(e.get("count") or 1) for e in events if e.get("kind") == RESYNC)
    return {"disconnects": disconnects, "gap_seconds": seconds, "resyncs": resyncs}
