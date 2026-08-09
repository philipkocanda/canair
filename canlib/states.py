"""Vehicle operating-state definitions + auto-suggestion.

A *state* is a named, standardized description of the car's power/operating
condition (e.g. ``DEEPSLEEP``, ``ACC``, ``READY``, ``CHARGING``) recorded on a
capture session. Historically this field was free text; profiles can now declare
a canonical, ordered list of states in ``profiles/<name>/vehicle_states.yaml`` so the
vocabulary is consistent across vehicles and comparable between captures.

Each state may carry a ``when:`` predicate over decoded PID values, written as a
small boolean expression referencing parameters by ``ECU.PARAM``::

    states:
      - name: CHARGING
        description: Actively charging (implies plugged)
        when: "BMS.BATTERY_CURRENT < -1"
      - name: READY
        when: "VCU.CAR_READY == 1"
      - name: DEEPSLEEP
        when: "__no_response__"

``suggest_states`` evaluates the rules against the latest decoded values and
returns every match (a session is naturally composite, e.g. ``READY, PARKED``) —
implementing the project goal of using known PIDs to deduce vehicle state.

More than one state is genuinely true at once, so a state may declare which
broader states it is a *specialization* of::

    states:
      - name: DRIVING
        implies: [READY]
        when: "ESC.REAL_SPEED_KMH > 0.5"

``most_specific_states`` uses that hierarchy to reduce a composite match to the
states nothing else implies, which is how a single-state display picks DRIVING
over READY while both are true. ``suggest_state`` is that single-result wrapper.

Predicates are evaluated with a whitelisted-AST evaluator (no ``eval``): only
boolean/comparison operators, ``ECU.PARAM`` names, numeric/string/bool literals,
and the sentinels ``__no_response__`` / ``__responded__`` are permitted.
Evaluation is three-valued (Kleene): a sub-expression that depends on an
unavailable parameter is :data:`UNKNOWN` rather than ``False``, so a rule is
suggested only on positive evidence and never mislabels a partially-polled cycle
(``UNKNOWN or True == True``, ``UNKNOWN and False == False``).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from canlib import yaml_io

if TYPE_CHECKING:
    from canlib.modes.multi_batch import EcuFrame

# The canonical base power-state vocabulary shared across every vehicle profile.
# This is the **powertrain-neutral ignition-switch ladder** every road vehicle
# has. The universal switch positions are OFF → ACC → ON → START; canair names
# them with YAML-safe, vendor-agnostic tokens:
#
#     SLEEP   OFF / LOCK — ignition off, ECUs asleep / 12V standby   (Hyundai IGN0)
#     ACC     accessory power                                        (Hyundai IGN1/ACC)
#     RUN     ignition on — electronics live / driveable             (Hyundai IGN2)
#     CRANK   starter engaged (ICE cranking)                         (Hyundai IGN3/START)
#
# `RUN` (not `ON`) and `SLEEP` (not `OFF`) are used because `ON`/`OFF`/`YES`/`NO`
# are YAML 1.1 booleans that would parse as `True`/`False` in a bare
# `vehicle_states:` list; `RUN` also reads unambiguously where a bare `IGN`
# invites "which IGN level?" (vendors number them, e.g. Hyundai IGN0-3).
#
# Only the universal switch positions live here. Finer, vendor-specific rungs
# (Hyundai's separate IGN1/IGN2 relays, an `ACC2` sub-level) and powertrain modes
# (EV `PLUGGED`/`READY`/`CHARGING`; the EV `READY` is that car's name for the
# driveable state) are NOT baked in — a profile declares those in its
# `vehicle_states.yaml`, so an ICE profile never inherits EV states and an EV
# profile never inherits ICE-only ones. `allowed_states` returns the union
# (base + ALL + the profile's own).
#
# State tokens are UPPERCASE (like the CAN-bus segment codes) — a visual cue
# that they're a controlled vocabulary, not free prose. Input is normalized to
# uppercase (:func:`parse_states`), so any casing typed on the CLI is accepted.
POWER_STATES = ("SLEEP", "ACC", "RUN", "CRANK")

# The conventional token meaning "applicable/readable in every vehicle state"
# — the state analogue of the ``ALL`` CAN-bus gateway code. It is documentary:
# a PID/DID/research entry tagged ``[ALL]`` is available regardless of power
# state. It carries no ``when:`` predicate (nothing to auto-suggest) and is
# always an accepted token (see :func:`allowed_states`).
ALL_STATE = "ALL"

# Bucket label for a capture carrying no state at all. An explicit population, so
# a state-axis analysis reports how much of its evidence is untagged instead of
# quietly analyzing a subset.
NO_STATE_KEY = "(no state)"

# Backwards-compatible alias (older code/imports referred to BASE_STATES).
BASE_STATES = POWER_STATES


class StatePredicateError(Exception):
    """Raised when a state ``when:`` expression uses disallowed/invalid syntax."""


class _UnknownType:
    """Kleene "unknown" truth value for a predicate that can't be decided.

    A predicate references parameters that weren't polled (offline: not captured
    in a cycle), or the ``__no_response__`` / ``__responded__`` sentinels in a
    context where response information isn't observable. Rather than collapse to
    ``False`` (which would silently mislabel), such sub-expressions evaluate to
    :data:`UNKNOWN` and propagate through boolean logic with three-valued
    (Kleene) semantics: ``UNKNOWN or True == True``, ``UNKNOWN and False ==
    False``, everything else touching an ``UNKNOWN`` stays ``UNKNOWN``. A rule
    that resolves to ``UNKNOWN`` is neither suggested nor treated as a conflict.
    """

    _instance: _UnknownType | None = None

    def __new__(cls) -> _UnknownType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __bool__(self) -> bool:  # pragma: no cover - guarded against in callers
        raise TypeError("UNKNOWN has no boolean value; use three-valued helpers")


UNKNOWN = _UnknownType()

# A predicate value is True, False, or UNKNOWN.
Tristate = bool | _UnknownType

_MISSING = object()

# The non-signal identifiers a predicate may read. They describe the polling
# *cycle* rather than a decoded value, so they never name a signal — callers
# resolving references against the registry must skip them.
PREDICATE_SENTINELS = frozenset({"__no_response__", "__responded__"})

# AST node types permitted in a state predicate.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Name,
    ast.Attribute,
    ast.Load,
    ast.Constant,
)


@dataclass(frozen=True)
class StateRule:
    """One declared state: a name, optional description, predicate, and relations.

    ``implies`` names the broader states this one is a *specialization* of — a car
    that is DRIVING is necessarily READY, one that is CHARGING is necessarily
    PLUGGED. It is the profile's declarative specificity hierarchy, and it is
    what lets a single-state display pick DRIVING over READY when both match
    (see :func:`most_specific_states`) without the file's order having to double
    as a priority list.

    ``excludes`` is its complement: states that cannot hold at the *same instant*
    (a car is not DRIVING and PARKED at once). Overlap is normal and mostly
    legitimate — the two relations are what separate a genuine simultaneous
    reading from an impossible one, which is the difference between a session
    whose state union is exact and one whose captures need a
    ``state_spans`` timeline (see :func:`contradictory_states`).
    """

    name: str
    description: str = ""
    predicate: Callable[[dict, set | None], Tristate] | None = None
    expr: str = ""
    implies: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


def _dotted_name(node: ast.AST) -> str:
    """Reconstruct a dotted ``ECU.PARAM`` string from a Name/Attribute chain."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    raise StatePredicateError("invalid name reference in predicate")


def _parse_predicate(expr: str) -> ast.Expression:
    """Parse a ``when:`` expression, rejecting any node outside the whitelist."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as ex:
        raise StatePredicateError(f"syntax error: {ex.msg}") from ex
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise StatePredicateError(f"disallowed syntax: {type(node).__name__}")
    return tree


def predicate_references(expr: str) -> list[str]:
    """Every non-sentinel identifier a ``when:`` expression reads, in source order.

    What remains after dropping :data:`PREDICATE_SENTINELS` is what the predicate
    expects to find in the decoded ``{ECU.PARAM: value}`` map, so a caller can
    resolve it against the signal registry (see :mod:`canlib.state_refs`).

    A *malformed* reference (a bare word, a triple-dotted name) is returned as-is
    rather than rejected: it parses fine and merely fails to resolve, which is
    exactly the silent breakage a caller wants to report.
    """
    tree = _parse_predicate(expr)
    # A Name/Attribute nested inside another Attribute is the head of a dotted
    # chain, not a reference of its own: `VCU.GEAR_P` reads one name, not two.
    nested = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    nodes = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.Name, ast.Attribute)) and id(n) not in nested
    ]
    nodes.sort(key=lambda n: (n.lineno, n.col_offset))
    refs: list[str] = []
    for node in nodes:
        name = _dotted_name(node)
        if name not in PREDICATE_SENTINELS and name not in refs:
            refs.append(name)
    return refs


def compile_predicate(expr: str) -> Callable[[dict, set | None], Tristate]:
    """Compile a ``when:`` expression into a safe callable ``(values, responded)``.

    The callable returns a three-valued result (``True`` / ``False`` /
    :data:`UNKNOWN`): ``UNKNOWN`` when the expression depends on a parameter that
    isn't in ``values`` (or on ``__no_response__`` / ``__responded__`` when
    ``responded`` is ``None`` — i.e. response information isn't observable, as
    for a stored capture).

    Raises :class:`StatePredicateError` for disallowed syntax so bad definitions
    fail loudly at load/validate time rather than silently never matching. Note
    that a *well-formed reference to a signal that does not exist* is not a
    syntax error and cannot be caught here — it is indistinguishable from a
    not-polled signal at evaluation time, so it is checked separately against the
    registry by :mod:`canlib.state_refs` (``canair validate states``).
    """
    tree = _parse_predicate(expr)

    def predicate(values: dict, responded: set | None) -> Tristate:
        result = _eval(tree.body, values, responded)
        # A well-formed predicate resolves to True/False/UNKNOWN; a `when:` that
        # is a bare value (not a boolean expression) can't decide a state.
        if result is True or result is False or result is UNKNOWN:
            return result
        return UNKNOWN

    return predicate


def _lookup(name: str, values: dict, responded: set | None):
    """Resolve a name to a value: sentinels first, then a decoded ECU.PARAM.

    Returns :data:`UNKNOWN` when the name can't be resolved (a not-polled param,
    or a response sentinel when ``responded`` is ``None``).
    """
    if name in PREDICATE_SENTINELS:
        if responded is None:
            return UNKNOWN
        return (not responded) if name == "__no_response__" else bool(responded)
    val = values.get(name, _MISSING)
    if val is _MISSING:
        # Indistinguishable here from a typo'd/renamed signal name — see
        # :mod:`canlib.state_refs`, which resolves references against the registry.
        return UNKNOWN
    return val


def _eval(node: ast.AST, values: dict, responded: set | None) -> Tristate | float | str:
    """Evaluate a predicate node with three-valued (Kleene) logic.

    Boolean nodes return a :data:`Tristate`; leaf value nodes (a decoded param,
    a numeric/string literal, unary minus) return the underlying value or
    :data:`UNKNOWN` when a referenced param is absent.
    """
    if isinstance(node, ast.BoolOp):
        operands = [_eval(v, values, responded) for v in node.values]
        if isinstance(node.op, ast.And):
            # False dominates; otherwise UNKNOWN if any operand is unknown.
            if any(o is False for o in operands):
                return False
            if any(o is UNKNOWN for o in operands):
                return UNKNOWN
            return True
        # Or: True dominates; otherwise UNKNOWN if any operand is unknown.
        if any(o is True for o in operands):
            return True
        if any(o is UNKNOWN for o in operands):
            return UNKNOWN
        return False
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = _eval(node.operand, values, responded)
        if operand is UNKNOWN:
            return UNKNOWN
        return not operand
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = _eval(node.operand, values, responded)
        if isinstance(operand, (int, float)):
            return -operand
        return UNKNOWN
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        operand = _eval(node.operand, values, responded)
        if isinstance(operand, (int, float)):
            return +operand
        return UNKNOWN
    if isinstance(node, ast.Compare):
        left = _eval(node.left, values, responded)
        result: Tristate = True
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            right = _eval(comparator, values, responded)
            link = _compare(op, left, right)
            if link is False:
                return False  # a definitely-false link short-circuits the chain
            if link is UNKNOWN:
                result = UNKNOWN  # keep scanning: a later link may be definitely False
            left = right
        return result
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _lookup(_dotted_name(node), values, responded)
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, (bool, int, float, str)):
            return value
        return UNKNOWN  # bytes/None/complex are not valid predicate literals
    raise StatePredicateError(f"disallowed syntax: {type(node).__name__}")


def _compare(op: ast.AST, left, right) -> Tristate:
    if left is UNKNOWN or right is UNKNOWN:
        return UNKNOWN
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    raise StatePredicateError(f"disallowed comparison: {type(op).__name__}")


# ---------------------------------------------------------------------------
# Loading + suggestion
# ---------------------------------------------------------------------------


def _states_path(profile=None) -> Path:
    from .profile import active

    prof = profile or active()
    return prof.states_file


def load_states(profile=None) -> list[StateRule]:
    """Load and compile the profile's vehicle_states.yaml. Returns [] when absent.

    Raises :class:`StatePredicateError` when a ``when:`` expression is invalid or
    the ``implies:`` hierarchy contains a cycle.
    """
    path = _states_path(profile)
    if not path.exists():
        return []
    data = yaml_io.safe_load(path.read_text()) or {}
    rules: list[StateRule] = []
    for entry in data.get("states", []) or []:
        if not isinstance(entry, dict) or "name" not in entry:
            raise StatePredicateError("each state needs a 'name'")
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


def state_names(profile=None) -> list[str]:
    """Declared state names for the active profile (empty when no vehicle_states.yaml)."""
    try:
        return [r.name for r in load_states(profile)]
    except StatePredicateError:
        return []


def allowed_states(profile=None) -> set[str]:
    """The set of accepted state tokens: base ``POWER_STATES`` + ``ALL`` + vehicle_states.yaml names.

    This is the single vocabulary that every validator/CLI should check against
    (PID/ECU/iocontrol/research declarations *and* capture/scan_log
    observations), so a profile can extend the shared base with its own
    composite states in one place (vehicle_states.yaml) without editing the tool.
    Tokens are compared case-insensitively (they are canonically UPPERCASE).
    """
    return {ALL_STATE} | set(POWER_STATES) | {n.upper() for n in state_names(profile)}


def state_options(profile=None) -> list[tuple[str, str]]:
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


def parse_states(value) -> list[str]:
    """Normalize a ``vehicle_states`` value into an UPPER-cased token list.

    Accepts a comma-separated string (as typed on ``--state``), a list/tuple of
    tokens, or None. Tokens are stripped and UPPER-cased; empties are dropped.
    Kept deliberately permissive (no vocabulary check) — validation soft-warns
    on unknown tokens elsewhere. Casing is normalized here so any casing typed
    on the CLI (``charging``/``Charging``/``CHARGING``) lands as the canonical
    UPPERCASE token.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        toks = [str(v).strip() for v in value]
    else:
        toks = [t.strip() for t in str(value).split(",")]
    return [t.upper() for t in toks if t]


def join_states(states) -> str:
    """Human-readable join of a ``vehicle_states`` list (``", "``-separated)."""
    if not states:
        return ""
    if isinstance(states, str):
        return states
    return ", ".join(str(s) for s in states)


def _order_states(tokens, profile=None) -> list[str]:
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


def ecu_states(ecu_def, profile=None) -> list[str]:
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
        return _order_states(parse_states(top), profile)
    tokens: list[str] = []
    for pid_def in (ecu_def.get("pids") or {}).values():
        if isinstance(pid_def, dict):
            tokens.extend(parse_states(pid_def.get("vehicle_states")))
    return _order_states(tokens, profile)


def ecus_in_state(state, pids_data, profile=None) -> list[dict]:
    """ECUs readable in ``state`` (the reverse of :func:`ecu_states`).

    ``pids_data`` is a ``load_pids()`` mapping. An ECU matches when ``state`` is
    among its resolved states (ECU-level, else the PID union); an ECU tagged
    ``ALL`` is readable in *every* state, so it matches any query. Each returned
    record carries ``name``/``tx_id`` and a ``source`` of ``"ecu"`` (ECU-level
    field), ``"pids"`` (union of PID states), or ``"all"`` (matched via ``ALL``).
    Sorted by ECU name.
    """
    target = str(state).strip().upper()
    out: list[dict] = []
    for name, ecu_def in (pids_data.get("ecus") or {}).items():
        if not isinstance(ecu_def, dict):
            continue
        states = ecu_states(ecu_def, profile)
        if not states:
            continue
        source = "ecu" if ecu_def.get("vehicle_states") else "pids"
        if ALL_STATE in states and target != ALL_STATE:
            out.append({"name": name, "tx_id": ecu_def.get("tx_id"), "source": "all"})
        elif target in states:
            out.append({"name": name, "tx_id": ecu_def.get("tx_id"), "source": source})
    out.sort(key=lambda r: str(r["name"]).upper())
    return out


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


def state_bucket_key(states, rules: list[StateRule] | None = None, *, profile=None) -> str:
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
    ordered = _order_states(tokens, profile)
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
    selector, rules: list[StateRule] | None = None, *, profile=None
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


def unknown_state_tokens(selector, profile=None) -> list[str]:
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


# ---------------------------------------------------------------------------
# Suggestion
# ---------------------------------------------------------------------------


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
    display)`` rows (as produced by :func:`canlib.decoding.decode_param_rows`).
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
