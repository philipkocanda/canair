"""The ``implies:`` and ``excludes:`` relations between states."""

from __future__ import annotations

from canair.states.predicates import StateRule
from canair.states.tokens import parse_states

# ---------------------------------------------------------------------------
# Specificity hierarchy (`implies:`)
# ---------------------------------------------------------------------------
#
# States are not one axis, and more than one is genuinely true at a time: a car
# being driven is READY *and* DRIVING, one taking a charge is PLUGGED *and*
# CHARGING. Recording keeps every match (a session is composite), but any
# single-state display has to choose one, and it must not choose by file order —
# that would make an editorial decision (where a state was typed) load-bearing.
#
# `implies:` declares the real relation instead: DRIVING is a *specialization* of
# READY. The most specific matches are the ones no other match implies, which is
# also the smallest set that still entails every match — nothing is lost by
# showing only them. Implication is the semantic relation, not a priority
# number, so it is self-documenting and needs no renumbering when a state is
# inserted. The hierarchy must be a DAG (`load_states` refuses a cycle) and its
# targets must be declared states (`canair validate states` checks that).


def parse_implies(value) -> tuple[str, ...]:
    """Normalize an ``implies:`` value into an UPPER-cased, de-duplicated tuple.

    Accepts a list/tuple of names, a comma-separated string, or None. Kept
    deliberately permissive about *which* names appear (an undeclared target is
    reported by ``canair validate states``, not raised here) so one stale
    reference cannot make the whole profile unloadable.
    """
    return tuple(dict.fromkeys(parse_states(value)))


# `excludes:` is `implies:`'s complement and parses identically, so it shares the
# normalization. It is a *separate name* rather than a shared generic call because
# the two relations mean opposite things and a reader at the call site needs to
# know which one is being handled.
parse_excludes = parse_implies


def _implies_map(rules: list[StateRule]) -> dict[str, tuple[str, ...]]:
    """``{UPPER state: direct implications}`` for the declared rules."""
    return {r.name.upper(): r.implies for r in rules}


def implication_cycle(rules: list[StateRule]) -> list[str] | None:
    """Return one ``implies:`` cycle as a name path, or None when the graph is a DAG.

    The returned path starts and ends on the same state (``["A", "B", "A"]``) so
    the error message can show the loop. A state implying itself is a cycle too.
    """
    direct = _implies_map(rules)
    # 0 = unvisited, 1 = on the current path, 2 = fully explored.
    mark: dict[str, int] = {}

    def walk(node: str, path: list[str]) -> list[str] | None:
        mark[node] = 1
        for nxt in direct.get(node, ()):
            if nxt not in direct:
                continue  # undeclared target: validate's problem, not a cycle
            if mark.get(nxt) == 1:
                return [*path[path.index(nxt) :], nxt]
            if mark.get(nxt, 0) == 0:
                found = walk(nxt, [*path, nxt])
                if found:
                    return found
        mark[node] = 2
        return None

    for name in direct:
        if mark.get(name, 0) == 0:
            found = walk(name, [name])
            if found:
                return found
    return None


def implied_closure(rules: list[StateRule], state: str) -> set[str]:
    """Every state ``state`` implies, transitively (UPPER-cased, excluding itself).

    Safe on a cyclic graph (the visited set terminates the walk), so callers that
    run before validation cannot hang.
    """
    direct = _implies_map(rules)
    out: set[str] = set()
    stack = list(direct.get(str(state).upper(), ()))
    while stack:
        nxt = stack.pop()
        if nxt in out:
            continue
        out.add(nxt)
        stack.extend(direct.get(nxt, ()))
    out.discard(str(state).upper())
    return out


# ---------------------------------------------------------------------------
# Exclusivity (`excludes:`)
# ---------------------------------------------------------------------------
# `implies:` explains why states legitimately overlap; `excludes:` marks the
# overlaps that cannot be real. A session tagged both DRIVING and PARKED was not
# both at once — it was each in turn, and its captures must be separated by a
# `state_spans` timeline before a state filter or a state-axis bucket means
# anything. Declaring the pair is what lets the tool tell that apart from an
# honestly simultaneous `READY, PARKED`, instead of warning on every multi-state
# session and training the user to ignore it.
#
# The relation is symmetric: declaring it on either side is enough.


def exclusive_pairs(rules: list[StateRule]) -> set[frozenset[str]]:
    """Every declared mutually-exclusive pair, as unordered ``{A, B}`` pairs.

    Self-pairs are dropped rather than raised — validation reports those; a
    caller running before validation must not blow up on a malformed file.
    """
    pairs: set[frozenset[str]] = set()
    for rule in rules:
        name = rule.name.upper()
        for other in rule.excludes:
            if other != name:
                pairs.add(frozenset((name, other)))
    return pairs


def contradictory_states(rules: list[StateRule], states) -> list[tuple[str, str]]:
    """Declared-exclusive pairs present together in ``states``, sorted for output.

    A non-empty result means the tokens cannot all describe one instant, so
    whatever they are attached to spans time (a recording) or is wrong (a live
    suggestion, i.e. a predicate that matches when it should not).
    """
    have = {str(s).upper() for s in states or []}
    out: list[tuple[str, str]] = []
    for pair in exclusive_pairs(rules):
        if pair <= have:
            a, b = sorted(pair)
            out.append((a, b))
    return sorted(out)


def exclusion_conflicts(rules: list[StateRule]) -> list[tuple[str, str]]:
    """Pairs a profile declares as BOTH exclusive and implied — a contradiction.

    ``A implies B`` asserts B holds whenever A does; ``A excludes B`` asserts it
    never can. One of the two is wrong, and left unchecked the pair would make
    every session carrying A look self-contradictory.
    """
    out: list[tuple[str, str]] = []
    for pair in exclusive_pairs(rules):
        a, b = sorted(pair)
        if b in implied_closure(rules, a) or a in implied_closure(rules, b):
            out.append((a, b))
    return sorted(out)


def most_specific_states(rules: list[StateRule], matched) -> list[str]:
    """Reduce ``matched`` to the states no other match implies, in input order.

    ``[READY, DRIVING]`` becomes ``[DRIVING]`` when DRIVING implies READY. States
    the hierarchy leaves unrelated all survive, so a car both plugged in and
    driveable still reports ``[READY, PLUGGED]`` rather than silently dropping
    one. The graph is a DAG once :func:`load_states` has accepted it, so a state
    is never dropped by something it implies in turn.
    """
    tokens = [str(s) for s in matched or []]
    if len(tokens) < 2:
        return tokens
    entailed: set[str] = set()
    for tok in tokens:
        entailed |= implied_closure(rules, tok)
    return [t for t in tokens if t.upper() not in entailed]
