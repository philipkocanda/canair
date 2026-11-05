"""Suggesting states from decoded values."""

from __future__ import annotations

from typing import TYPE_CHECKING

from canair.states.implies import implied_closure, most_specific_states
from canair.states.predicates import StateRule

if TYPE_CHECKING:
    from canair.expression.results import EcuFrame


def satisfied_states(rules: list[StateRule], states) -> set[str]:
    """Every state ``states`` satisfies: the tokens themselves plus what they imply.

    The read direction of the ``implies:`` DAG. Because DRIVING implies READY, a
    capture tagged ``[DRIVING]`` satisfies a READY query — the hierarchy says
    driving *is* a narrower reading of ready. The converse does not hold, so a
    ``[READY]`` capture does not satisfy a DRIVING query.
    """
    out: set[str] = set()
    for s in states or []:
        tok = str(s).upper()
        out.add(tok)
        out |= implied_closure(rules, tok)
    return out


def suggest_state(rules: list[StateRule], values: dict, responded: set | None) -> str | None:
    """Return the single most representative state whose predicate matches.

    Arbitrates the matches by the profile's ``implies:`` hierarchy: a state that
    another match *implies* is redundant, so DRIVING (which implies READY) wins
    over READY when both are true. Among states the hierarchy leaves unrelated
    (say PLUGGED and READY) the first in file order is returned, since there is
    no declared reason to prefer either — use :func:`most_specific_states` to see
    them all. Prefer :func:`suggest_states` for offline back-fill, where a
    session is naturally composite (e.g. ``READY, PARKED``).
    """
    matched, _false = suggest_states(rules, values, responded)
    specific = most_specific_states(rules, matched)
    return specific[0] if specific else None


def suggest_states(
    rules: list[StateRule], values: dict, responded: set | None
) -> tuple[list[str], list[str]]:
    """Evaluate every rule, returning ``(matched, definitely_false)``.

    ``values`` maps ``"ECU.PARAM"`` → decoded numeric value; ``responded`` is the
    set of ECU labels that answered this cycle, or ``None`` when response
    information isn't observable (the offline case — a stored capture existing
    *is* a response, so ``__no_response__`` can't be evaluated).

    - ``matched`` — states whose predicate is definitely ``True``, in file order.
      Multiple can match (the composite case); a predicate that resolves to
      :data:`UNKNOWN` (references an unpolled param) is neither matched nor false.
      The ``implies:`` hierarchy is deliberately *not* applied here — a recorded
      session should keep every state it was in. Reduce to the representative
      subset with :func:`most_specific_states` when a display needs one label.
    - ``definitely_false`` — states whose predicate is definitely ``False``. This
      lets a caller flag a *conflict* between an already-recorded state and the
      decoded evidence without needing an explicit state-axis model.

    ``ALL`` and predicate-less (vocabulary-only) states are never evaluated.
    """
    matched: list[str] = []
    definitely_false: list[str] = []
    for rule in rules:
        if rule.predicate is None:
            continue
        result = rule.predicate(values, responded)
        if result is True:
            matched.append(rule.name)
        elif result is False:
            definitely_false.append(rule.name)
    return matched, definitely_false


def collect_values(new_queries: list[EcuFrame]) -> tuple[dict[str, float], set[str]]:
    """Extract ``{ECU.PARAM: value}`` + responded-ECU set from decoded results.

    ``new_queries`` is a list of ``(ecu_label, pid_results)`` where each result
    carries a ``params`` list of ``(name, value, unit, expr, error, verified,
    display)`` rows (as produced by :func:`canair.expression.decoding.decode_param_rows`).
    An ECU is "responded" when it returned any result; rows whose value is None
    (decode error) are skipped.
    """
    import re

    values: dict[str, float] = {}
    responded: set[str] = set()
    for ecu_label, pid_results in new_queries or []:
        m = re.match(r"(\w+)", ecu_label or "")
        if not m:
            continue
        ecu = m.group(1).upper()
        if pid_results:
            responded.add(ecu)
        for entry in pid_results or []:
            for row in entry.get("params", []) or []:
                name, value = row[0], row[1]
                # Only numeric values feed state predicates; a typed text value
                # (ascii/date) has no place in a numeric comparison.
                if isinstance(value, (int, float)):
                    values[f"{ecu}.{name}"] = value
    return values, responded
