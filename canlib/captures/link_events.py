"""Link events: when a recording could not see the car, and why.

A ``monitor --save`` session that loses its link keeps recording across the gap, so its
timestamps show a hole but nothing says what caused it. A run-length (``keep:changes``)
session cannot even show the hole: an absent row there means "the value did not change".
A session's ``link_events`` list records it instead. See
``plans/2026-11-02-capture-link-events.md``.

**Gaps** are intervals in which nothing was gathered for some scope, so no stored value
is known to have held across one:

* ``disconnect`` — no ECU answered coherently (the link failed);
* ``paused`` — the user paused polling;
* ``ecu_silent`` — one ECU (``rx``) stopped answering while the others still did;
* ``unpolled`` — one PID (``rx`` + ``pid``), or a whole ECU (``rx`` only), was taken out
  of the poll set.

The other kinds explain sparse data without being a gap: ``backoff`` (an interval in
which one slow ECU was polled ``factor`` times less often) and ``resync`` (an instant at
which the transport realigned a desynchronised pipe).

Deliberately not part of ``state_spans``: spans describe the car, are derived from stored
payloads (and so rebuilt by a back-fill), and cannot express a gap. A link event describes
the recorder, exists only because the recorder saw it, and is never recomputed. Events
carry enums, addresses and counts only, never free text: an exception message can name a
device address, and capture files are contributed upstream.

This module is a **pure leaf** (same discipline as :mod:`canlib.states.spans`): no
capture-store, profile or transport imports.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal, NotRequired, TypedDict, get_args

from canlib.states.spans import parse_time_key

LinkEventKind = Literal["disconnect", "paused", "ecu_silent", "unpolled", "backoff", "resync"]
LINK_EVENT_KINDS: tuple[LinkEventKind, ...] = get_args(LinkEventKind)

DisconnectCause = Literal["error", "stale", "manual"]
"""Why a disconnect began: the transport raised (``error``), the liveness check found
no coherent answer for several poll cycles (``stale``), or the user asked for a
reconnect (``manual``)."""
DISCONNECT_CAUSES: tuple[DisconnectCause, ...] = get_args(DisconnectCause)

DISCONNECT: LinkEventKind = "disconnect"
PAUSED: LinkEventKind = "paused"
ECU_SILENT: LinkEventKind = "ecu_silent"
UNPOLLED: LinkEventKind = "unpolled"
BACKOFF: LinkEventKind = "backoff"
RESYNC: LinkEventKind = "resync"

# Kinds in which nothing was gathered for their scope: they bound forward fill, and a
# payload timed inside a closed one contradicts it.
GAP_KINDS: frozenset[str] = frozenset({DISCONNECT, PAUSED, ECU_SILENT, UNPOLLED})
# Gaps that cover every ECU.
SESSION_GAP_KINDS: frozenset[str] = frozenset({DISCONNECT, PAUSED})
# Gaps whose bounds are answers or the poll loop itself, so a payload timed inside a
# closed one is a contradiction. An ``unpolled`` is bounded by the picker instead, and a
# poll already in flight when it applied may still land a reply inside it.
ANSWER_BOUNDED_GAP_KINDS: frozenset[str] = frozenset({DISCONNECT, PAUSED, ECU_SILENT})
# Kinds that span time, and so carry an ``until`` (absent = to the end of the recording).
INTERVAL_KINDS: frozenset[str] = GAP_KINDS | {BACKOFF}

# Journal record types (see canlib.captures.journal).
JOURNAL_EVENT = "event"
JOURNAL_EVENT_END = "event_end"

MIDNIGHT = "00:00:00.000"

_RX_RE = re.compile(r"^0x[0-9A-Fa-f]{3,8}$")


class LinkEvent(TypedDict):
    """One entry of a session's ``link_events`` (mirrors ``captures_schema.json``)."""

    at: str
    kind: LinkEventKind
    until: NotRequired[str]
    rx: NotRequired[str]
    pid: NotRequired[str]
    cause: NotRequired[DisconnectCause]
    factor: NotRequired[int]
    count: NotRequired[int]


def format_time(moment: datetime) -> str:
    """``HH:MM:SS.fff``, the precision the monitor stamps captures with."""
    return moment.strftime("%H:%M:%S.%f")[:-3]


def is_rx(value: Any) -> bool:
    """Whether ``value`` is a hex response address (``0x7EC``), the only scope recorded."""
    return isinstance(value, str) and bool(_RX_RE.match(value))


def _count(value: Any, minimum: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
        return value
    return None


# ---------------------------------------------------------------------------
# Journal records → per-day event lists
# ---------------------------------------------------------------------------


def _event_from_record(rec: Mapping[str, Any]) -> LinkEvent | None:
    """The validated event a journal record opens, or None when it is not one."""
    kind = rec.get("kind")
    at = rec.get("at")
    if kind not in LINK_EVENT_KINDS or parse_time_key(at) is None:
        return None
    event: LinkEvent = {"at": str(at), "kind": kind}
    rx = rec.get("rx")
    if kind in (ECU_SILENT, UNPOLLED, BACKOFF):
        if not is_rx(rx):
            return None  # an ECU-scoped event with no ECU says nothing
        event["rx"] = str(rx)
    pid = rec.get("pid")
    if kind == UNPOLLED and isinstance(pid, str) and pid:
        event["pid"] = pid.upper()
    cause = rec.get("cause")
    if kind == DISCONNECT and cause in DISCONNECT_CAUSES:
        event["cause"] = cause
    factor = _count(rec.get("factor"), 2)
    if kind == BACKOFF:
        if factor is None:
            return None
        event["factor"] = factor
    count = _count(rec.get("count"), 2)
    if kind == RESYNC and count is not None:
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


def _same_rx(a: Any, b: Any) -> bool:
    try:
        return int(str(a), 16) == int(str(b), 16)
    except ValueError:
        return str(a).upper() == str(b).upper()


def applies_to(event: Mapping[str, Any], rx: Any = None, pid: Any = None) -> bool:
    """Whether ``event`` covers a capture from ``rx``/``pid``.

    An event with no ``rx`` covers every ECU, one with no ``pid`` every PID of its ECU.
    ``rx=None`` asks about the session as a whole, so only unscoped events match.
    """
    scope_rx = event.get("rx")
    if scope_rx is None:
        return True
    if rx is None or not _same_rx(scope_rx, rx):
        return False
    scope_pid = event.get("pid")
    return scope_pid is None or (pid is not None and str(scope_pid).upper() == str(pid).upper())


Gap = tuple[float, float | None]
"""A gap as ``(start, end)`` seconds since midnight; ``end`` None = to the recording's end."""


def gap_intervals(
    events: Sequence[Mapping[str, Any]],
    rx: Any = None,
    pid: Any = None,
    kinds: frozenset[str] = GAP_KINDS,
) -> list[Gap]:
    """The gaps covering a capture from ``rx``/``pid`` (see :func:`applies_to`), by start."""
    out: list[Gap] = []
    for e in events:
        if e.get("kind") not in kinds or not applies_to(e, rx, pid):
            continue
        start = parse_time_key(e.get("at"))
        if start is None:
            continue
        out.append((start, parse_time_key(e.get("until"))))
    out.sort(key=lambda gap: gap[0])
    return out


def in_closed_gap(gaps: Sequence[Gap], key: float) -> bool:
    """Whether ``key`` falls strictly inside a closed gap (nothing was gathered then)."""
    return any(end is not None and start < key < end for start, end in gaps)


@dataclass
class KindSummary:
    """How often one kind of event happened in a session, and for how long."""

    count: int = 0
    seconds: float = 0.0
    # Scoped kinds: the ECUs (response addresses) involved, first-seen order.
    rxs: list[str] = field(default_factory=list)
    # backoff: the largest slowdown seen.
    factor: int = 0


def summarize(
    events: Sequence[Mapping[str, Any]], session_end: float | None
) -> dict[str, KindSummary]:
    """Per kind: count, total duration, ECUs and largest factor, for one session.

    An open interval is measured to ``session_end`` (seconds since midnight, the
    session's last capture), or counted with no duration when that is unknown. A
    ``resync`` counts its ``count``. Kinds that did not occur are absent.
    """
    out: dict[str, KindSummary] = {}
    for e in events:
        kind = str(e.get("kind"))
        if kind not in LINK_EVENT_KINDS:
            continue
        s = out.setdefault(kind, KindSummary())
        if kind == RESYNC:
            s.count += int(e.get("count") or 1)
            continue
        s.count += 1
        start = parse_time_key(e.get("at"))
        stop = parse_time_key(e.get("until"))
        if stop is None:
            stop = session_end
        if start is not None and stop is not None and stop > start:
            s.seconds += stop - start
        rx = e.get("rx")
        if isinstance(rx, str) and rx not in s.rxs:
            s.rxs.append(rx)
        factor = e.get("factor")
        if isinstance(factor, int) and factor > s.factor:
            s.factor = factor
    return out


def gaps_between(
    events: Sequence[Mapping[str, Any]],
    lo: float,
    hi: float,
    scopes: Iterable[tuple[Any, Any]] = (),
) -> list[Mapping[str, Any]]:
    """The gap events overlapping ``(lo, hi]`` that cover the session or one of ``scopes``.

    ``scopes`` are the ``(rx, pid)`` of the captures in view (see :func:`applies_to`).
    For a viewer stepping from one instant to the next: what happened in between.
    """
    wanted = list(scopes)
    out = []
    for e in events:
        if e.get("kind") not in GAP_KINDS:
            continue
        if e.get("rx") is not None and not any(applies_to(e, rx, pid) for rx, pid in wanted):
            continue
        start = parse_time_key(e.get("at"))
        end = parse_time_key(e.get("until"))
        if start is None or start > hi or (end is not None and end <= lo):
            continue
        out.append(e)
    return out
