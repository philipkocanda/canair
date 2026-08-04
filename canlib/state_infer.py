"""Offline vehicle-state inference over stored captures.

Re-decodes historical capture payloads and evaluates the profile's
``vehicle_states.yaml`` predicates to infer which power/operating state(s) a
session was in — the offline analogue of the live monitor's span-aware state
back-fill (:mod:`canlib.modes._monitor_record`), for sessions recorded before
that existed or with a missing/incorrect state.

Nothing here talks to the device; it is pure analysis over ``captures/`` (like
:mod:`canlib.align`). A capture existing *is* a response, so ``responded`` is
``None`` (unobservable) — a predicate using ``__no_response__`` / ``__responded__``
cleanly abstains (evaluates to :data:`canlib.states.UNKNOWN`) rather than firing
on absent evidence.

Cross-ECU predicates (e.g. ``BMS.X < -1 or OBC.Y > 0.5``) need both signals at
roughly one instant, so timed captures are grouped into **pseudo-cycles** — all
captures within ``cycle_tol`` seconds of the cycle's first. A round-robin monitor
sweep over several ECUs spans ~8-10 s, so the default tolerance matches the
stepper's join tolerance (10 s), not :data:`canlib.align.DEFAULT_JOIN_TOL_S`.
Untimed captures (common in legacy sessions) collapse to a single whole-session
cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from canlib.capture_dates import entry_datetime
from canlib.commands._captures_query import _resolve_defs
from canlib.decoding import decode_param_rows
from canlib.states import StateRule, _order_states, suggest_states

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Default pseudo-cycle window (seconds). Wider than align's 5.0s join tolerance:
# a full round-robin monitor cycle over several ECUs spans ~8-10s, so a cross-ECU
# predicate needs the looser window to see co-polled signals as one instant.
DEFAULT_CYCLE_TOL_S = 10.0


@dataclass
class SessionInference:
    """Inference result for one capture session."""

    inferred: list[str] = field(default_factory=list)
    """States matched in at least one cycle (union), vocabulary-ordered."""
    definitely_false: list[str] = field(default_factory=list)
    """States a predicate proved false in a cycle and never matched — the basis
    for flagging a conflict with an already-recorded state."""
    n_cycles: int = 0
    """Number of pseudo-cycles the session's captures grouped into."""
    n_decoded_params: int = 0
    """Distinct ``ECU.PARAM`` values decoded across the session."""
    timed: bool = True
    """False when the session has no usable timestamps (a single whole-session
    cycle) — cross-ECU predicates are less reliable then."""


def _bucket_cycles(
    captures: Sequence[Mapping[str, Any]], cycle_tol: float
) -> tuple[list[list[Mapping[str, Any]]], bool]:
    """Group a session's captures into pseudo-cycles.

    Timed captures are windowed within ``cycle_tol`` seconds of each cycle's
    first; untimed captures collapse into one whole-session cycle. Returns
    ``(cycles, timed)`` where ``timed`` is False when nothing carried a usable
    timestamp.
    """
    timed_caps = []
    untimed_caps = []
    for cap in captures:
        dt = entry_datetime(cap)
        if dt is None:
            untimed_caps.append(cap)
        else:
            timed_caps.append((dt, cap))

    cycles: list[list[Mapping[str, Any]]] = []
    timed_caps.sort(key=lambda pair: pair[0])
    current: list[Mapping[str, Any]] = []
    anchor = None
    for dt, cap in timed_caps:
        if anchor is None or (dt - anchor).total_seconds() > cycle_tol:
            if current:
                cycles.append(current)
            current = [cap]
            anchor = dt
        else:
            current.append(cap)
    if current:
        cycles.append(current)

    # Untimed captures — one combined cycle (can't be ordered/windowed).
    if untimed_caps:
        cycles.append(list(untimed_caps))

    timed = bool(timed_caps)
    return cycles, timed


def _decode_cycle(cycle: Sequence[Mapping[str, Any]], ecu_index: dict) -> dict[str, float | str]:
    """Decode one pseudo-cycle's captures into ``{ECU.PARAM: value}`` (numeric only)."""
    values: dict[str, float | str] = {}
    for cap in cycle:
        payload = cap.get("payload")
        ecu = str(cap.get("ecu") or "").upper()
        pid = str(cap.get("pid") or "").upper()
        if not payload or not ecu or not pid:
            continue
        parameters, _tx = _resolve_defs(ecu_index, ecu, pid)
        if not parameters:
            continue
        try:
            rows = decode_param_rows(payload, parameters)
        except Exception:
            continue
        for name, value, _unit, _expr, error, _verified, _display in rows:
            if error is not None or not isinstance(value, (int, float)):
                continue
            values[f"{ecu}.{name}"] = value
    return values


def infer_session_states(
    captures: Sequence[Mapping[str, Any]],
    rules: list[StateRule],
    ecu_index: dict,
    *,
    cycle_tol: float = DEFAULT_CYCLE_TOL_S,
    profile=None,
) -> SessionInference:
    """Infer the vehicle state(s) of one session from its decoded captures.

    ``captures`` are the (payload-bearing) capture entries of a single session,
    ``rules`` the compiled state rules (``states.load_states()``), ``ecu_index``
    the PID definition index (``build_ecu_index(load_pids())``).
    """
    cycles, timed = _bucket_cycles(captures, cycle_tol)

    matched_union: dict[str, None] = {}  # ordered set
    false_any: set[str] = set()
    all_params: set[str] = set()

    for cycle in cycles:
        values = _decode_cycle(cycle, ecu_index)
        all_params.update(values)
        if not values:
            continue
        matched, definitely_false = suggest_states(rules, values, None)
        for name in matched:
            matched_union.setdefault(name, None)
        false_any.update(definitely_false)

    # A state matched in any cycle wins over a definitely-false in another
    # (states change across a session; a positive match is evidence it occurred).
    definitely_false = [s for s in false_any if s not in matched_union]

    return SessionInference(
        inferred=_order_states(list(matched_union), profile=profile),
        definitely_false=_order_states(definitely_false, profile=profile),
        n_cycles=len(cycles),
        n_decoded_params=len(all_params),
        timed=timed,
    )
