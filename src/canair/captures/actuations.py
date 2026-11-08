"""Actuations: what a recording switched on the car, and when.

A ``monitor --save --actuate`` session drives outputs and runs routines while it polls. Its
``actuations`` list records each request, so the analysis tools can line a toggle up with the
signal changes it caused (``investigate --events``, ``captures --step``). See
``plans/2026-11-07-monitor-actuator-pane.md``.

One entry per request, at the time it was sent:

* ``kind`` — ``iocontrol`` (an output, ``id`` its DID) or ``routine`` (``id`` its RID);
* ``action`` — ``on``, ``off``, ``adjust``, ``release`` (an output handed back on exit or
  after a reconnect), ``start``, ``stop``, ``results``, or ``held``: no request, an output that
  was still on when this segment began (``n`` in the monitor), so a segment says which outputs
  were driven from its first row;
* ``request`` and ``response`` — the hex sent and the positive reply; ``outcome`` is ``ok``,
  ``nrc`` (with ``nrc``) or ``error`` (no reply). ``held`` carries none of the three.

Like :mod:`canair.captures.link_events`, an entry is observed, never derived, and carries enums,
addresses and hex only: no labels and no free text, because capture files are contributed
upstream. Labels come from the profile when the capture is read.

This module is a **pure leaf**: no capture-store, profile or transport imports.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict, get_args

from canair.captures.link_events import format_time, is_rx
from canair.states.spans import parse_time_key

ActuationKind = Literal["iocontrol", "routine"]
ACTUATION_KINDS: tuple[ActuationKind, ...] = get_args(ActuationKind)

ActuationAction = Literal["on", "off", "adjust", "release", "start", "stop", "results", "held"]
ACTUATION_ACTIONS: tuple[ActuationAction, ...] = get_args(ActuationAction)

ActuationOutcome = Literal["ok", "nrc", "error"]
ACTUATION_OUTCOMES: tuple[ActuationOutcome, ...] = get_args(ActuationOutcome)

HELD: ActuationAction = "held"

#: The journal record type (see :mod:`canair.captures.journal`).
JOURNAL_ACTUATION = "actuation"

_HEX_RE = re.compile(r"^(?:[0-9A-F]{2})+$")
_ID_RE = re.compile(r"^[0-9A-F]{2,8}$")
_NRC_RE = re.compile(r"^0x[0-9A-F]{2}$")


class Actuation(TypedDict):
    """One entry of a session's ``actuations`` (mirrors ``captures_schema.json``)."""

    at: str
    rx: str
    kind: ActuationKind
    id: str
    action: ActuationAction
    request: NotRequired[str]
    outcome: NotRequired[ActuationOutcome]
    response: NotRequired[str]
    nrc: NotRequired[str]


def actuation_record(
    at: datetime,
    rx: str,
    kind: ActuationKind,
    ident: str,
    action: ActuationAction,
    *,
    request: str | None = None,
    outcome: ActuationOutcome | None = None,
    response: str | None = None,
    nrc: int | None = None,
) -> dict:
    """The journal record of one actuation (``date`` and ``at`` from ``at``)."""
    rec: dict = {
        "type": JOURNAL_ACTUATION,
        "date": at.strftime("%Y-%m-%d"),
        "at": format_time(at),
        "rx": rx,
        "kind": kind,
        "id": ident.upper(),
        "action": action,
    }
    if request:
        rec["request"] = request.upper()
    if outcome:
        rec["outcome"] = outcome
    if response and outcome == "ok":
        rec["response"] = response.upper().replace(" ", "")
    if nrc is not None and outcome == "nrc":
        rec["nrc"] = f"0x{nrc:02X}"
    return rec


def _hex(value: Any) -> str | None:
    return value if isinstance(value, str) and _HEX_RE.match(value) else None


def _from_record(rec: Mapping[str, Any]) -> Actuation | None:
    """The validated actuation a journal record (or a stored entry) holds, or None."""
    at, rx, kind, ident, action = (rec.get(k) for k in ("at", "rx", "kind", "id", "action"))
    if parse_time_key(at) is None or not is_rx(rx):
        return None
    if kind not in ACTUATION_KINDS or action not in ACTUATION_ACTIONS:
        return None
    if not isinstance(ident, str) or not _ID_RE.match(ident):
        return None
    entry: Actuation = {"at": str(at), "rx": str(rx), "kind": kind, "id": ident, "action": action}
    if action == HELD:
        return entry
    request = _hex(rec.get("request"))
    if request is not None:
        entry["request"] = request
    outcome = rec.get("outcome")
    if outcome in ACTUATION_OUTCOMES:
        entry["outcome"] = outcome
        response = _hex(rec.get("response"))
        if outcome == "ok" and response is not None:
            entry["response"] = response
        nrc = rec.get("nrc")
        if outcome == "nrc" and isinstance(nrc, str) and _NRC_RE.match(nrc):
            entry["nrc"] = nrc
    return entry


def actuations_by_date(records: Iterable[Mapping[str, Any]], fallback_date: str) -> dict[str, list]:
    """A journal's ``actuation`` records as per-day ``actuations`` lists, each sorted by ``at``."""
    by_day: dict[str, list] = {}
    for rec in records:
        if rec.get("type") != JOURNAL_ACTUATION:
            continue
        entry = _from_record(rec)
        if entry is not None:
            by_day.setdefault(str(rec.get("date") or fallback_date), []).append(entry)
    for entries in by_day.values():
        entries.sort(key=lambda e: parse_time_key(e.get("at")) or 0.0)
    return by_day


def session_actuations(session: Mapping[str, Any]) -> list[Actuation]:
    """A session's ``actuations``, or ``[]`` (tolerant of a malformed block)."""
    entries = session.get("actuations")
    if not isinstance(entries, list):
        return []
    out: list[Actuation] = []
    for item in entries:
        if isinstance(item, Mapping):
            entry = _from_record(item)
            if entry is not None:
                out.append(entry)
    return out


def actuations_in(entries: Iterable[Mapping[str, Any]]) -> list[tuple[datetime, Actuation]]:
    """Every actuation of the sessions ``entries`` come from, once each, in time order.

    ``entries`` are flat capture entries (``captures/store.py``): each carries its session's
    ``session_actuations`` and ``date``, and the ``_path`` and ``_session_idx`` that tell two
    sessions apart.
    """
    seen: set[tuple[Any, Any]] = set()
    out: list[tuple[datetime, Actuation]] = []
    for entry in entries:
        session = (entry.get("_path") or entry.get("file"), entry.get("_session_idx", 0))
        if session in seen:
            continue
        seen.add(session)
        for act in entry.get("session_actuations") or ():
            try:
                when = datetime.fromisoformat(f"{entry.get('date', '')}T{act['at']}")
            except (KeyError, ValueError):
                continue
            out.append((when, act))
    out.sort(key=lambda pair: pair[0])
    return out


def describe(entry: Mapping[str, Any]) -> str:
    """``iocontrol BC0C on (ok)`` — one actuation as a timeline line says it."""
    text = f"{entry.get('kind', '')} {entry.get('id', '')} {entry.get('action', '')}"
    outcome = entry.get("outcome")
    if outcome == "nrc" and entry.get("nrc"):
        return f"{text} (NRC {entry['nrc']})"
    return f"{text} ({outcome})" if outcome else text
