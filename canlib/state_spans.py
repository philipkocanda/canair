"""Temporal vehicle-state spans: what the car was doing *when*, within one session.

A capture session's ``vehicle_states`` is a **union** over the whole recording — the
honest answer to "what did this session cover?", but the wrong answer to "what was
the car doing when this byte was sampled?". A single ``monitor --save`` run across a
drive → charge → drive trip is tagged ``DRIVING, CHARGING, …``, so filtering its
captures by ``CHARGING`` returns the driving ones too.

The fix is to model what is physically true: **vehicle state is piecewise constant in
time**. A span is one such piece — a start time plus the states that held from it
until the next span begins. Session #10 of the bundled Ioniq profile needs 52 spans
for 6,872 captures, so the temporal detail costs ~0.2% of the capture store.

Two deliberate shape choices:

* **A span carries only its start** (``at``), not a start *and* an end. Half-open
  intervals cannot express a gap, so there is never a "which span owns this capture?"
  question to arbitrate at read time — a question that *would* arise for spans
  recorded live, where a poll cycle that decodes nothing leaves a hole. They cannot
  express an overlap either, so the representation is total by construction.
* **Times are ``HH:MM:SS[.fff]`` strings** on the session's own day, exactly as a
  capture's ``time`` field is stored (the date lives on the session, so a span needs
  none). Like :func:`canlib.capture_dates.entry_datetime`, a session that crosses
  midnight is out of scope.

This module is a **pure leaf**: no capture-model, profile or numpy imports, so the
raw-CAN broadcast domain can reuse it unchanged (same discipline as
:mod:`canlib.counters`). Building spans from stored captures is
:mod:`canlib.state_infer`'s job; resolving a capture *through* them is
:mod:`canlib.capture_store`'s.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


class StateSpan(TypedDict):
    """One piece of a session's timeline: the states holding from ``at`` onward.

    Mirrors the ``state_spans`` item in ``canlib/schema/captures_schema.json``.
    ``states`` may legitimately be empty — "nothing matched here" is a different
    answer from "no information", and :func:`states_at` keeps them distinct.
    """

    at: str
    states: list[str]


def parse_time_key(raw: Any) -> float | None:
    """Seconds-since-midnight ordering key for an ``HH:MM:SS[.fff]`` time, or None.

    Spans and captures are compared on this rather than lexicographically: string
    order is only correct while every hour is zero-padded, which an imported capture
    need not honour.
    """
    if raw is None:
        return None
    parts = str(raw).strip().split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2]) if len(parts) == 3 else 0.0
    except ValueError:
        return None
    return hours * 3600.0 + minutes * 60.0 + seconds


def build_spans(observations: Iterable[tuple[Any, Sequence[str]]]) -> list[StateSpan]:
    """Coalesce timed state observations into spans, in chronological order.

    ``observations`` are ``(time, states)`` pairs — typically one per pseudo-cycle
    from :func:`canlib.state_infer.state_observations`, or one per poll cycle from a
    live recording. Consecutive observations carrying the *same* state set collapse
    into a single span, which is what makes the representation compact. Observations
    with an unparseable time are dropped (they cannot be placed on the timeline).

    ``states`` is compared as an ordered sequence, so a caller should hand over a
    canonically ordered list (``suggest_states`` returns vocabulary order) or two
    equivalent sets will fail to coalesce.
    """
    timed: list[tuple[float, str, list[str]]] = []
    for raw_at, states in observations:
        key = parse_time_key(raw_at)
        if key is None:
            continue
        timed.append((key, str(raw_at).strip(), [str(s) for s in states or []]))
    timed.sort(key=lambda item: item[0])

    spans: list[StateSpan] = []
    for _key, at, states in timed:
        if spans and spans[-1]["states"] == states:
            continue
        spans.append({"at": at, "states": states})
    return spans


def span_keys(spans: Sequence[Mapping[str, Any]]) -> list[float]:
    """Sorted ordering keys for ``spans``, for repeated :func:`states_at` lookups.

    Resolving a whole session means one lookup per capture, so the caller hoists the
    key list out of the loop rather than re-parsing every span each time.
    """
    return [parse_time_key(s.get("at")) or 0.0 for s in spans]


def states_at(
    spans: Sequence[Mapping[str, Any]],
    when: Any,
    keys: Sequence[float] | None = None,
) -> list[str] | None:
    """The states holding at ``when``, or None when the timeline cannot answer.

    Returns None for an absent/unparseable ``when``, for empty ``spans``, and for a
    time *before* the first span — all of which mean "no information". An empty list
    is a real answer: a span whose predicates matched nothing.

    Pass ``keys`` from :func:`span_keys` when resolving many captures against the
    same session.
    """
    if not spans:
        return None
    key = parse_time_key(when)
    if key is None:
        return None
    if keys is None:
        keys = span_keys(spans)
    idx = bisect_right(keys, key) - 1
    if idx < 0:
        return None
    return [str(s) for s in spans[idx].get("states") or []]


def span_state_union(spans: Sequence[Mapping[str, Any]]) -> set[str]:
    """Every state appearing in any span.

    The invariant a back-fill must preserve: this equals the session's recorded
    ``vehicle_states``, so spans are a strict *refinement* of the union rather than a
    contradiction of it. ``canair validate captures`` checks it.
    """
    out: set[str] = set()
    for span in spans:
        out.update(str(s) for s in span.get("states") or [])
    return out
