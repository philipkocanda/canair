"""The ``when:`` predicate: its parser and the three-valued (Kleene) evaluator."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass

from canair.core.edit_errors import EditError


class StatePredicateError(EditError):
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
    resolve it against the signal registry (see :mod:`canair.states.refs`).

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
    registry by :mod:`canair.states.refs` (``canair validate states``).
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
        # :mod:`canair.states.refs`, which resolves references against the registry.
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
