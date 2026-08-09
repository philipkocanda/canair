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
from canlib.capture_store import resolve_pid_defs
from canlib.decoding import decode_param_rows
from canlib.state_spans import build_spans, span_state_union
from canlib.states import (
    StatePredicateError,
    StateRule,
    _order_states,
    predicate_references,
    suggest_states,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from canlib.state_spans import StateSpan

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
        parameters, _tx = resolve_pid_defs(ecu_index, ecu, pid)
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


@dataclass
class SpanInference:
    """Reconstructed state *timeline* for one capture session."""

    spans: list[StateSpan] = field(default_factory=list)
    """Coalesced ``{at, states}`` observations, chronological."""
    n_cycles: int = 0
    """Pseudo-cycles the session's captures grouped into."""
    n_informative: int = 0
    """Cycles that could evaluate at least one predicate — the ones that shaped
    the timeline. The rest carry the previous span forward."""
    n_untimed: int = 0
    """Cycles with no usable timestamp, which cannot be placed on a timeline."""
    union: list[str] = field(default_factory=list)
    """States appearing in at least one span, vocabulary-ordered. Compared
    against the recorded session ``vehicle_states`` to detect a stale union."""


def _predicate_refs(rules: list[StateRule]) -> dict[str, frozenset[str]]:
    """``state → the ECU.PARAM signals its predicate reads`` (upper-cased).

    Used to decide whether a cycle carries *fresh evidence* about a state, which
    is what a latched state needs before it may be retracted.
    """
    refs: dict[str, frozenset[str]] = {}
    for rule in rules:
        if rule.predicate is None or not rule.expr:
            continue
        try:
            refs[rule.name] = frozenset(r.upper() for r in predicate_references(rule.expr))
        except StatePredicateError:  # pragma: no cover — load_states already parsed it
            refs[rule.name] = frozenset()
    return refs


def infer_state_spans(
    captures: Sequence[Mapping[str, Any]],
    rules: list[StateRule],
    ecu_index: dict,
    *,
    cycle_tol: float = DEFAULT_CYCLE_TOL_S,
    profile=None,
) -> SpanInference:
    """Reconstruct *when* each state held during one session.

    The temporal counterpart of :func:`infer_session_states`: instead of unioning
    every cycle's match into one set, each cycle contributes a timestamped
    observation, and :func:`canlib.state_spans.build_spans` coalesces consecutive
    equal observations into spans.

    **A matched state latches until a cycle carries fresh evidence against it.** A
    cycle sees only the signals it happened to poll, so a round-robin sweep
    re-observes ``BMS.CHARGING`` on one cycle and ``VCU.GEAR_P`` on the next.
    Ending a state the moment it is not re-observed made the reconstruction flap
    between the subsets of predicates each cycle could evaluate — 807 cycles of one
    stationary charge produced 312 spans. Requiring a *definite False* instead is
    the other extreme, and too sticky: a predicate that ORs across ECUs
    (``ESC.REAL_SPEED_KMH > 0.5 or VCU.VCU_VEHICLE_SPEED > 0.5 or …``) can never be
    falsified once one of those ECUs stops answering, because Kleene ``False or
    UNKNOWN`` is UNKNOWN — DRIVING then latched through the charge that followed
    the drive.

    So a latched state is retracted when the cycle decoded **any** signal its
    predicate reads and the predicate still did not match. That is evidence about
    *this* state, gathered now; a cycle that decoded none of its signals says
    nothing and leaves it alone.

    A cycle that can evaluate no predicate at all contributes nothing — the
    previous span simply continues. A cycle that held nothing after evaluation
    contributes an explicit empty observation, because "measured, and no state
    applies" is a real answer.
    """
    cycles, _timed = _bucket_cycles(captures, cycle_tol)
    refs_by_state = _predicate_refs(rules)

    observations: list[tuple[Any, list[str]]] = []
    held: set[str] = set()
    n_informative = 0
    n_untimed = 0

    for cycle in cycles:
        when = cycle[0].get("time") if cycle else None
        if when is None:
            n_untimed += 1
            continue
        values = _decode_cycle(cycle, ecu_index)
        if not values:
            continue
        matched, definitely_false = suggest_states(rules, values, None)
        if not matched and not definitely_false:
            continue
        n_informative += 1

        seen = {k.upper() for k in values}
        matched_now = set(matched)
        held |= matched_now
        held -= {
            name
            for name in held
            if name not in matched_now
            and (name in set(definitely_false) or refs_by_state.get(name, frozenset()) & seen)
        }
        observations.append((when, _order_states(sorted(held), profile=profile)))

    spans = build_spans(observations)
    return SpanInference(
        spans=spans,
        n_cycles=len(cycles),
        n_informative=n_informative,
        n_untimed=n_untimed,
        union=_order_states(sorted(span_state_union(spans)), profile=profile),
    )


@dataclass
class SessionSpans:
    """The ``state_spans`` block to store for one session, plus how it was derived."""

    spans: list[StateSpan] = field(default_factory=list)
    carried: list[str] = field(default_factory=list)
    """Recorded states carried into every span because the evidence cannot place
    them in time — see :func:`session_state_spans`."""
    inference: SpanInference = field(default_factory=SpanInference)

    @property
    def is_timeline(self) -> bool:
        """True when the states actually change during the session.

        A single distinct state-set over the whole session has no temporal
        ambiguity for a filter to resolve, so storing it adds bytes and no
        information.
        """
        return len({tuple(s["states"]) for s in self.spans}) > 1


def session_state_spans(
    recorded: Sequence[str],
    captures: Sequence[Mapping[str, Any]],
    rules: list[StateRule],
    ecu_index: dict,
    *,
    cycle_tol: float = DEFAULT_CYCLE_TOL_S,
    profile=None,
) -> SessionSpans:
    """The spans to store for a session recorded as ``recorded``.

    Wraps :func:`infer_state_spans` with the one rule that makes writing spans
    safe: **a back-fill may narrow only what it can see.** A recorded state the
    predicates cannot place on the timeline — one the profile declares without a
    ``when:`` rule at all (``SLEEP``), or one that no cycle ever matched — is
    unioned into *every* span. Without that, resolving a capture through its spans
    would make it stop matching a state it used to match, turning a precision fix
    into data loss.

    Shared by ``captures uds --backfill-state-spans`` and the save-time hook
    (:func:`canlib.captures.annotate_session_spans`) so both derive a timeline the
    same way.
    """
    inf = infer_state_spans(captures, rules, ecu_index, cycle_tol=cycle_tol, profile=profile)
    inferable = {r.name for r in rules if r.predicate is not None}
    placed = set(inf.union)
    carried = _order_states(
        [s for s in recorded if s not in inferable or s not in placed], profile=profile
    )
    spans: list[StateSpan] = inf.spans
    if carried:
        spans = [
            {
                "at": s["at"],
                "states": _order_states(list({*s["states"], *carried}), profile=profile),
            }
            for s in inf.spans
        ]
    return SessionSpans(spans=spans, carried=carried, inference=inf)
