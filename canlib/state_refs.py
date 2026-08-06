"""Resolve vehicle-state ``when:`` predicate references against the signal registry.

A predicate reads decoded signals by ``ECU.PARAM`` (see :mod:`canlib.states`), but
the evaluator cannot tell a *typo* from a *not-polled-this-cycle* signal: both are
:data:`~canlib.states.UNKNOWN`, so a predicate that can never match looks exactly
like one that simply had no evidence this cycle. That is by design — it is what
keeps a partially-polled cycle from mislabelling a session — and it is also how
renaming a signal once silently disabled a state, with every validator green.

This module is the seam between the state vocabulary and the ``ecus/`` registry.
It answers two questions:

* :func:`check_references` — which references cannot resolve, and why
  (``canair validate states``).
* :func:`states_referencing` — which states read a given signal, so renaming or
  removing it can warn about the predicates it breaks (``canair pids
  rename-param`` / ``rm-param``).

Resolution mirrors how the decoded value map is actually keyed at evaluation time
(``canlib.state_infer._decode_cycle`` and ``canlib.states.collect_values``):
``ECU`` is the **canonical** ECU short name, upper-cased; ``PARAM`` is the
``parameters:`` key **verbatim**; and only *numeric* values are ever stored. A
reference that deviates on any of those axes silently never resolves, so each is
reported with the specific reason rather than a flat "unknown".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .decoding import decodes_to_number
from .states import PREDICATE_SENTINELS, StatePredicateError, predicate_references

# Why a reference cannot resolve. All are hard failures (the predicate can never
# fire); the kind exists so callers can group/filter, not to grade severity.
MALFORMED = "malformed"
UNKNOWN_ECU = "unknown-ecu"
UNKNOWN_PARAM = "unknown-param"
IGNORED_PID = "ignored-pid"
NOT_NUMERIC = "not-numeric"


@dataclass(frozen=True)
class StateRefIssue:
    """One unresolvable ``ECU.PARAM`` reference in a state's ``when:`` predicate."""

    state: str
    ref: str
    kind: str
    message: str

    def __str__(self) -> str:
        return f"state '{self.state}': when: references {self.ref} — {self.message}"


@dataclass(frozen=True)
class _Signal:
    """A defined signal as the registry sees it, for reference resolution."""

    pid: str
    numeric: bool
    ignored: bool


def _signal_map(pids_data: Mapping[str, Any]) -> dict[str, dict[str, _Signal]]:
    """Build ``{ECU_UPPER: {PARAM: _Signal}}`` from the loaded ECU definitions.

    Unlike ``pids.build_ecu_index`` this keeps PIDs whose ``status`` is
    ``ignored``, flagged as such: a predicate pointing at one resolves to nothing
    at runtime, and saying so beats reporting the signal as undefined.
    """
    from .pids import pid_status

    out: dict[str, dict[str, _Signal]] = {}
    for ecu_name, ecu_def in (pids_data.get("ecus") or {}).items():
        signals = out.setdefault(str(ecu_name).upper(), {})
        for pid_code, pid_def in ((ecu_def or {}).get("pids") or {}).items():
            ignored = pid_status(pid_def) == "ignored"
            for param_name, param in ((pid_def or {}).get("parameters") or {}).items():
                prev = signals.get(param_name)
                # A name defined on several PIDs: prefer a live definition, so an
                # ignored duplicate never masks the one that actually decodes.
                if prev is not None and (not prev.ignored or ignored):
                    continue
                signals[param_name] = _Signal(
                    pid=str(pid_code).upper(),
                    numeric=decodes_to_number(param),
                    ignored=ignored,
                )
    return out


def _alias_map(pids_data: Mapping[str, Any]) -> dict[str, str]:
    """``{ALIAS_UPPER: CANONICAL_UPPER}`` for ECUs declaring an ``identity.alias``."""
    out: dict[str, str] = {}
    for ecu_name, ecu_def in (pids_data.get("ecus") or {}).items():
        alias = ((ecu_def or {}).get("identity") or {}).get("alias")
        if alias:
            out[str(alias).upper()] = str(ecu_name).upper()
    return out


def _resolve(
    ref: str,
    signals: Mapping[str, Mapping[str, _Signal]],
    aliases: Mapping[str, str],
) -> tuple[str, str] | None:
    """Return ``(kind, message)`` when ``ref`` cannot resolve, else ``None``."""
    ecu, dot, param = ref.partition(".")
    if not dot or not param or "." in param:
        sentinels = " / ".join(sorted(PREDICATE_SENTINELS))
        return (
            MALFORMED,
            f"not an ECU.PARAM reference (nor a {sentinels} sentinel)",
        )

    ecu_signals = signals.get(ecu.upper())
    if ecu_signals is None:
        canonical = aliases.get(ecu.upper())
        if canonical:
            return (
                UNKNOWN_ECU,
                f"'{ecu}' is an alias for {canonical}; a predicate must name the "
                f"canonical ECU ({canonical}.{param})",
            )
        return (UNKNOWN_ECU, f"no ECU '{ecu}' in the registry")
    if ecu != ecu.upper():
        return (
            UNKNOWN_ECU,
            f"the decoded value map is keyed by UPPERCASE ECU name — write {ecu.upper()}.{param}",
        )

    signal = ecu_signals.get(param)
    if signal is None:
        case_match = next((n for n in ecu_signals if n.upper() == param.upper()), None)
        if case_match:
            return (
                UNKNOWN_PARAM,
                f"signal names are matched case-sensitively — did you mean {ecu}.{case_match}?",
            )
        other = sorted(e for e, sigs in signals.items() if param in sigs)
        if other:
            return (
                UNKNOWN_PARAM,
                f"{ecu} defines no signal '{param}' (it is defined on {', '.join(other)})",
            )
        return (UNKNOWN_PARAM, f"{ecu} defines no signal '{param}'")

    if signal.ignored:
        return (
            IGNORED_PID,
            f"{ecu} {signal.pid} has status: ignored, so {param} is never polled or decoded",
        )
    if not signal.numeric:
        return (
            NOT_NUMERIC,
            f"{ecu} {signal.pid} {param} does not decode to a number "
            "(only numeric signals reach a predicate)",
        )
    return None


def check_references(
    predicates: Iterable[tuple[str, str]],
    pids_data: Mapping[str, Any],
) -> list[StateRefIssue]:
    """Report every unresolvable reference across ``(state_name, when_expr)`` pairs.

    ``pids_data`` is the loaded ECU registry (``pids.load_pids()``). A predicate
    whose syntax is invalid is skipped — that is ``compile_predicate``'s error to
    raise, not this check's to duplicate.
    """
    signals = _signal_map(pids_data)
    aliases = _alias_map(pids_data)
    issues: list[StateRefIssue] = []
    for state, expr in predicates:
        if not expr:
            continue
        try:
            refs = predicate_references(expr)
        except StatePredicateError:
            continue
        for ref in refs:
            found = _resolve(ref, signals, aliases)
            if found is not None:
                kind, message = found
                issues.append(StateRefIssue(state=state, ref=ref, kind=kind, message=message))
    return issues


def profile_predicates(profile=None) -> list[tuple[str, str]]:
    """``(state_name, when_expr)`` for every state declaring a predicate.

    Tolerant of a broken vocabulary (returns ``[]``), matching
    :func:`canlib.states.state_names` — a caller warning about references should
    never be the thing that fails on a syntax error elsewhere in the file.
    """
    from .states import load_states

    try:
        rules = load_states(profile)
    except StatePredicateError:
        return []
    return [(r.name, r.expr) for r in rules if r.expr]


def states_referencing(ecu: str, param: str, *, profile=None) -> list[str]:
    """States whose ``when:`` predicate reads ``ECU.PARAM``.

    The reverse lookup behind the rename/remove warnings: renaming a signal a
    predicate depends on breaks that state silently, so the editors report it.
    Matching is case-insensitive — a reference in the wrong case is broken too,
    and a warning that misses it is worse than one that is slightly generous.
    """
    want = f"{str(ecu).strip()}.{str(param).strip()}".upper()
    out: list[str] = []
    for state, expr in profile_predicates(profile):
        try:
            refs = predicate_references(expr)
        except StatePredicateError:
            continue
        if any(r.upper() == want for r in refs) and state not in out:
            out.append(state)
    return out
