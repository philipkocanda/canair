"""Vehicle operating-state definitions + auto-suggestion.

A *state* is a named, standardized description of the car's power/operating
condition (e.g. ``deep sleep``, ``acc``, ``ready``, ``charging``) recorded on a
capture session. Historically this field was free text; profiles can now declare
a canonical, ordered list of states in ``profiles/<name>/states.yaml`` so the
vocabulary is consistent across vehicles and comparable between captures.

Each state may carry a ``when:`` predicate over decoded PID values, written as a
small boolean expression referencing parameters by ``ECU.PARAM``::

    states:
      - name: charging
        description: Actively charging (implies plugged)
        when: "BMS.BATTERY_CURRENT < -1"
      - name: ready
        when: "VCU.CAR_READY == 1"
      - name: deep sleep
        when: "__no_response__"

``suggest_state`` evaluates the rules top-to-bottom against the latest decoded
values and returns the first match — implementing the project goal of using
known PIDs to deduce vehicle state.

Predicates are evaluated with a whitelisted-AST evaluator (no ``eval``): only
boolean/comparison operators, ``ECU.PARAM`` names, numeric/string/bool literals,
and the sentinels ``__no_response__`` / ``__responded__`` are permitted.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

# The canonical base power-state vocabulary, shared across every vehicle
# profile. This is THE single definition — schema validators, the research
# backlog, and the `pids`/`research` CLIs all derive their accepted tokens from
# here (via `allowed_states`) rather than keeping their own hardcoded copies.
# A profile's states.yaml may declare additional composite states on top of
# these (e.g. `parked`, `driving`); `allowed_states` returns the union.
POWER_STATES = ("sleep", "plugged", "acc", "acc2", "ready", "charging")

# Backwards-compatible alias (older code/imports referred to BASE_STATES).
BASE_STATES = POWER_STATES


class StatePredicateError(Exception):
    """Raised when a state ``when:`` expression uses disallowed/invalid syntax."""


class _Unknown(Exception):
    """Internal: a referenced parameter wasn't available, so the rule can't match."""


_MISSING = object()

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
    """One declared state: a name, optional description, and optional predicate."""

    name: str
    description: str = ""
    predicate: Callable[[dict, set], bool] | None = None
    expr: str = ""


def _dotted_name(node: ast.AST) -> str:
    """Reconstruct a dotted ``ECU.PARAM`` string from a Name/Attribute chain."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    raise StatePredicateError("invalid name reference in predicate")


def compile_predicate(expr: str) -> Callable[[dict, set], bool]:
    """Compile a ``when:`` expression into a safe callable ``(values, responded)``.

    Raises :class:`StatePredicateError` for disallowed syntax so bad definitions
    fail loudly at load/validate time rather than silently never matching.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as ex:
        raise StatePredicateError(f"syntax error: {ex.msg}") from ex

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise StatePredicateError(f"disallowed syntax: {type(node).__name__}")

    def predicate(values: dict, responded: set) -> bool:
        return bool(_eval(tree.body, values, responded))

    return predicate


def _lookup(name: str, values: dict, responded: set):
    """Resolve a name to a value: sentinels first, then a decoded ECU.PARAM."""
    if name == "__no_response__":
        return not responded
    if name == "__responded__":
        return bool(responded)
    val = values.get(name, _MISSING)
    if val is _MISSING:
        raise _Unknown(name)
    return val


def _eval(node: ast.AST, values: dict, responded: set):
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval(v, values, responded) for v in node.values)
        return any(_eval(v, values, responded) for v in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, values, responded)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, values, responded)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return +_eval(node.operand, values, responded)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, values, responded)
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            right = _eval(comparator, values, responded)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _lookup(_dotted_name(node), values, responded)
    if isinstance(node, ast.Constant):
        return node.value
    raise StatePredicateError(f"disallowed syntax: {type(node).__name__}")


def _compare(op: ast.AST, left, right) -> bool:
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
    """Load and compile the profile's states.yaml. Returns [] when absent.

    Raises :class:`StatePredicateError` when a ``when:`` expression is invalid.
    """
    path = _states_path(profile)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
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
            )
        )
    return rules


def state_names(profile=None) -> list[str]:
    """Declared state names for the active profile (empty when no states.yaml)."""
    try:
        return [r.name for r in load_states(profile)]
    except StatePredicateError:
        return []


def allowed_states(profile=None) -> set[str]:
    """The set of accepted state tokens: base ``POWER_STATES`` plus states.yaml names.

    This is the single vocabulary that every validator/CLI should check against
    (PID/ECU/iocontrol/research declarations *and* capture/scan_log
    observations), so a profile can extend the shared base with its own
    composite states in one place (states.yaml) without editing the tool.
    """
    return set(POWER_STATES) | set(state_names(profile))


def parse_states(value) -> list[str]:
    """Normalize a ``vehicle_states`` value into a lower-cased token list.

    Accepts a comma-separated string (as typed on ``--state``), a list/tuple of
    tokens, or None. Tokens are stripped and lower-cased; empties are dropped.
    Kept deliberately permissive (no vocabulary check) — validation soft-warns
    on unknown tokens elsewhere.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        toks = [str(v).strip() for v in value]
    else:
        toks = [t.strip() for t in str(value).split(",")]
    return [t.lower() for t in toks if t]


def join_states(states) -> str:
    """Human-readable join of a ``vehicle_states`` list (``", "``-separated)."""
    if not states:
        return ""
    if isinstance(states, str):
        return states
    return ", ".join(str(s) for s in states)


def suggest_state(rules: list[StateRule], values: dict, responded: set) -> str | None:
    """Return the first state whose predicate matches the decoded values.

    ``values`` maps ``"ECU.PARAM"`` → decoded numeric value; ``responded`` is the
    set of ECU labels that answered this cycle. Rules referencing a parameter not
    present in ``values`` are skipped (can't be evaluated), so a partial poll
    still suggests the best determinable state.
    """
    for rule in rules:
        if rule.predicate is None:
            continue
        try:
            if rule.predicate(values, responded):
                return rule.name
        except _Unknown:
            continue
    return None


def collect_values(new_queries) -> tuple[dict, set]:
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
                if value is not None:
                    values[f"{ecu}.{name}"] = value
    return values, responded
