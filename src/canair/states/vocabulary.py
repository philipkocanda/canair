"""A profile's state vocabulary: ``vehicle_states.yaml`` loaded and its names."""

from __future__ import annotations

from canair.core import yaml_io
from canair.profile import Profile
from canair.states.implies import (
    exclusion_conflicts,
    implication_cycle,
    parse_excludes,
    parse_implies,
)
from canair.states.predicates import StatePredicateError, StateRule, compile_predicate
from canair.states.tokens import ALL_STATE, POWER_STATES, unquoted_boolean_error


def load_states(profile: Profile) -> list[StateRule]:
    """Load and compile the profile's vehicle_states.yaml. Returns [] when absent.

    Raises :class:`StatePredicateError` when a ``when:`` expression is invalid or
    the ``implies:`` hierarchy contains a cycle.
    """
    path = profile.states_file
    if not path.exists():
        return []
    data = yaml_io.safe_load(path.read_text()) or {}
    rules: list[StateRule] = []
    for entry in data.get("states", []) or []:
        if not isinstance(entry, dict) or "name" not in entry:
            raise StatePredicateError("each state needs a 'name'")
        for key in ("name", "implies", "excludes"):
            if msg := unquoted_boolean_error(f"state {key}", entry.get(key)):
                raise StatePredicateError(msg)
        expr = entry.get("when") or ""
        pred = compile_predicate(expr) if expr else None
        rules.append(
            StateRule(
                name=str(entry["name"]),
                description=str(entry.get("description", "")),
                predicate=pred,
                expr=expr,
                implies=parse_implies(entry.get("implies")),
                excludes=parse_excludes(entry.get("excludes")),
            )
        )
    # A cycle makes "more specific than" meaningless, so refuse the file outright
    # rather than let the arbitration silently pick an arbitrary member.
    cycle = implication_cycle(rules)
    if cycle:
        raise StatePredicateError(f"implies: cycle {' -> '.join(cycle)}")
    # An implied pair that also claims exclusivity is a straight contradiction;
    # every downstream answer built on it would be arbitrary.
    conflicts = exclusion_conflicts(rules)
    if conflicts:
        pairs = "; ".join(f"{a} / {b}" for a, b in conflicts)
        raise StatePredicateError(f"excludes: contradicts implies: for {pairs}")
    return rules


def state_names(profile: Profile) -> list[str]:
    """Declared state names for the active profile (empty when no vehicle_states.yaml)."""
    try:
        return [r.name for r in load_states(profile)]
    except StatePredicateError:
        return []


def allowed_states(profile: Profile) -> set[str]:
    """The set of accepted state tokens: base ``POWER_STATES`` + ``ALL`` + vehicle_states.yaml names.

    This is the single vocabulary that every validator/CLI should check against
    (PID/ECU/iocontrol/research declarations *and* capture/scan_log
    observations), so a profile can extend the shared base with its own
    composite states in one place (vehicle_states.yaml) without editing the tool.
    Tokens are compared case-insensitively (they are canonically UPPERCASE).
    """
    return {ALL_STATE} | set(POWER_STATES) | {n.upper() for n in state_names(profile)}


def state_options(profile: Profile) -> list[tuple[str, str]]:
    """Ordered ``(name, description)`` pairs for the profile's state vocabulary.

    The list is what a picker (e.g. the monitor save dialog) offers: every
    declared state from vehicle_states.yaml first, in file order, each with its
    ``description``, followed by any base ``POWER_STATES`` not already declared
    (so the shared base is always selectable even in a bare profile) and finally
    the ``ALL`` meta-token. Names are unique and UPPER-cased to match
    :func:`parse_states`/:func:`allowed_states`.
    """
    seen: set[str] = set()
    options: list[tuple[str, str]] = []
    try:
        rules = load_states(profile)
    except StatePredicateError:
        rules = []
    for rule in rules:
        name = rule.name.upper()
        if name in seen:
            continue
        seen.add(name)
        options.append((name, rule.description))
    for name in POWER_STATES:
        if name in seen:
            continue
        seen.add(name)
        options.append((name, ""))
    if ALL_STATE not in seen:
        options.append((ALL_STATE, "Applicable in every vehicle state."))
    return options


def order_states(tokens, profile: Profile) -> list[str]:
    """De-duplicate ``tokens`` and order them by the declared vocabulary.

    Declared states come first in ``vehicle_states.yaml`` order, then any tokens
    absent from the vocabulary (alphabetical), with the ``ALL`` meta-token last.
    Tokens are UPPER-cased to match the canonical vocabulary.
    """
    order = {n.upper(): i for i, n in enumerate(state_names(profile))}

    def key(tok: str):
        if tok == ALL_STATE:
            return (2, "")
        if tok in order:
            return (0, order[tok])
        return (1, tok)

    return sorted(dict.fromkeys(t.upper() for t in tokens), key=key)
