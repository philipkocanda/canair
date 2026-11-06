"""State selectors: an ECU's states, the ``--state`` matcher and capture buckets."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from canair.profile import Profile
from canair.states.implies import implied_closure, most_specific_states
from canair.states.predicates import StatePredicateError, StateRule
from canair.states.suggest import satisfied_states
from canair.states.tokens import ALL_STATE, NO_STATE_KEY, join_states, parse_states
from canair.states.vocabulary import allowed_states, load_states, order_states


def ecu_states(ecu_def, profile: Profile) -> list[str]:
    """Resolve which vehicle states an ECU is readable/awake in.

    Uses the ECU-level ``vehicle_states`` when present; otherwise falls back to
    the union of every PID's ``vehicle_states``. Tokens are UPPER-cased,
    de-duplicated, and ordered by the profile's declared vocabulary. Returns an
    empty list when neither level declares any state.
    """
    if not isinstance(ecu_def, dict):
        return []
    top = ecu_def.get("vehicle_states")
    if top:
        return order_states(parse_states(top), profile)
    tokens: list[str] = []
    for pid_def in (ecu_def.get("pids") or {}).values():
        if isinstance(pid_def, dict):
            tokens.extend(parse_states(pid_def.get("vehicle_states")))
    return order_states(tokens, profile)


def ecus_in_state(state, pids_data, profile: Profile, rules=None) -> list[dict]:
    """ECUs readable in ``state`` (the reverse of :func:`ecu_states`).

    ``pids_data`` is a ``load_pids()`` mapping. An ECU matches when ``state`` is
    among its resolved states (ECU-level, else the PID union); an ECU tagged
    ``ALL`` is readable in *every* state, so it matches any query. It also matches
    through the ``implies:`` hierarchy: an ECU readable whenever IG3 holds is
    readable in READY, which implies IG3 — the same widening ``--state`` applies.
    Each returned record carries ``name``/``tx_id``, a ``source`` of ``"ecu"``
    (ECU-level field), ``"pids"`` (union of PID states) or ``"all"`` (matched via
    ``ALL``), and ``via`` — the broader state it matched through, else ``None``.
    ``rules`` defaults to the profile's vocabulary. Sorted by ECU name.
    """
    target = str(state).strip().upper()
    if rules is None:
        try:
            rules = load_states(profile)
        except StatePredicateError:
            rules = []
    broader = implied_closure(rules, target)
    out: list[dict] = []
    for name, ecu_def in (pids_data.get("ecus") or {}).items():
        if not isinstance(ecu_def, dict):
            continue
        states = ecu_states(ecu_def, profile)
        if not states:
            continue
        source = "ecu" if ecu_def.get("vehicle_states") else "pids"
        rec = {"name": name, "tx_id": ecu_def.get("tx_id"), "via": None}
        if ALL_STATE in states and target != ALL_STATE:
            out.append({**rec, "source": "all"})
        elif target in states:
            out.append({**rec, "source": source})
        elif via := next((s for s in states if s in broader), None):
            out.append({**rec, "source": source, "via": via})
    out.sort(key=lambda r: str(r["name"]).upper())
    return out


def state_bucket_key(states, rules: list[StateRule] | None = None, *, profile: Profile) -> str:
    """Canonical grouping key for a capture's states, for state-axis analysis.

    Discriminability buckets captures by this key, so it must name the *logical*
    state and nothing else. Three normalizations get folded in: the tokens are
    UPPER-cased (legacy recordings stored them lower-case, which split every
    bucket in two), ordered by the profile's vocabulary (so ``PARKED, SLEEP`` and
    ``SLEEP, PARKED`` are one bucket), and reduced by ``implies:`` (so a DRIVING
    capture buckets with DRIVING whether or not the recording also listed the
    READY it entails).

    Returns ``"(no state)"`` for an untagged capture — an explicit bucket rather
    than a silent drop, since "unknown" is a real and often large population.
    """
    tokens = (
        parse_states(states) if isinstance(states, str) else [str(s).upper() for s in states or []]
    )
    if not tokens:
        return NO_STATE_KEY
    if rules is None:
        rules = load_states(profile)
    ordered = order_states(tokens, profile)
    return join_states(most_specific_states(rules, ordered)) or NO_STATE_KEY


def format_state_selector(selector) -> str:
    """Render a ``--state`` selector back into its own grammar, for a scope banner.

    Alternatives keep their comma and conjoined groups join with ``+``, so the
    echo reads the way the flag was typed (``ready,driving + parked``).
    """
    groups = []
    for raw in selector if isinstance(selector, list | tuple) else [selector]:
        tokens = parse_states(raw)
        if tokens:
            groups.append(",".join(tokens))
    return " + ".join(groups)


def state_matcher(
    selector, rules: list[StateRule] | None = None, *, profile: Profile
) -> Callable[[Any], bool]:
    """Build a predicate testing a capture's states against a ``--state`` selector.

    ``selector`` is the parsed flag: one string, or a list of them when the flag
    was repeated. Commas *within* one value are alternatives (``READY,DRIVING``
    means either) and repeats are conjunctive (``--state CHARGING --state
    PARKED`` means both), so the two axes are expressible without a second flag.
    Matching is by token through :func:`satisfied_states`, never by substring:
    ``ACC`` no longer selects ACC2 by accident, though ACC2 still satisfies ACC
    because it declares ``implies: [ACC]``.

    An empty selector — and the ``ALL`` meta-token — matches everything. The
    returned predicate memoizes per distinct state tuple, since a filter pass
    sees tens of thousands of captures drawn from a handful of state sets.
    """
    groups: list[frozenset[str]] = []
    for raw in selector if isinstance(selector, list | tuple) else [selector]:
        tokens = parse_states(raw)
        if not tokens or ALL_STATE in tokens:
            continue
        groups.append(frozenset(tokens))
    if not groups:
        return lambda _states: True
    if rules is None:
        rules = load_states(profile)
    cache: dict[tuple[str, ...], bool] = {}

    def matches(states: Any) -> bool:
        key = tuple(str(s).upper() for s in states or [])
        hit = cache.get(key)
        if hit is None:
            have = satisfied_states(rules, key)
            hit = all(not g.isdisjoint(have) for g in groups)
            cache[key] = hit
        return hit

    return matches


def unknown_state_tokens(selector, profile: Profile) -> list[str]:
    """Selector tokens that are not in the profile's state vocabulary, in order.

    For a command that wants to warn that ``--state DRIVNG`` can only ever match
    nothing. Reports rather than raises, so a typo does not become a hard failure
    on a profile whose vocabulary is still being written.
    """
    allowed = allowed_states(profile)
    out: list[str] = []
    for raw in selector if isinstance(selector, list | tuple) else [selector]:
        for tok in parse_states(raw):
            if tok not in allowed and tok not in out:
                out.append(tok)
    return out
