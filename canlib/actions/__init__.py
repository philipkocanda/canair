"""Profile-defined actions: named, guarded requests a profile declares in ``actions.yaml``.

An *action* is something a car can be told to do that canair has no built-in
command for, because it is make-specific: putting a Hyundai/Kia to sleep with the
functional ``10 D1``, for instance. The profile, not canair's code, knows which
requests do it and which live signals prove it is safe to send them::

    actions:
      sleep:
        description: Put every ECU to sleep.
        reads: [IGPM:22BC03, IGPM:22BC07]
        require: IGPM.IGN1_SWITCH == 0 and IGPM.IG3_RELAY_OUTPUT == 0
        requests:
          - {to: functional, send: 10D1, response: none}

Two rules keep a profile from using this to get around canair's safety policy:

- **Every request still passes the command blocklist** (:mod:`canlib.safety`).
  A request it refuses can only be sent when a code-owned quirk grants exactly
  that request (:func:`canlib.quirks.action_grants`), and never with
  ``--unsafe``. So an action can't send ``11 01`` or a programming session.
- **Every action is guarded.** ``reads`` and ``require`` are mandatory; the
  condition is evaluated on values read live just before the requests are sent,
  and only a TRUE result passes (a signal that was not read is UNKNOWN, which
  refuses). The condition may not use ``or`` or ``not``: an alternative source
  would make it looser, never stricter.

This module is device-free: the model, the structural checks shared by the
loader, the editor and ``canair validate actions``, and :func:`load_actions`. The
live run is :mod:`canlib.modes.action`.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from canlib import yaml_io

if TYPE_CHECKING:
    from canlib.profile import Profile

# The request target that means "the functional (broadcast) request id".
FUNCTIONAL: Final = "functional"

ResponseMode = Literal["none", "expected"]
RESPONSE_MODES: Final[tuple[ResponseMode, ...]] = ("none", "expected")

ACTION_FIELDS: Final = frozenset({"description", "reads", "require", "requests"})
REQUEST_FIELDS: Final = frozenset({"to", "send", "response"})

# An action name: a lower-cased token, like a group name.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_HEX_RE = re.compile(r"^(?:[0-9A-F]{2})+$")


class ActionError(ValueError):
    """An actions.yaml definition (or a reference to one) is invalid."""


@dataclass(frozen=True)
class ActionRequest:
    """One request an action sends: its target, bytes, and whether a reply is expected."""

    target: str
    send: str
    response: ResponseMode = "expected"

    def describe(self) -> str:
        return f"{self.target}:{self.send}" + (":none" if self.response == "none" else "")


@dataclass(frozen=True)
class Action:
    """A named, guarded profile action."""

    name: str
    description: str
    reads: tuple[str, ...]
    require: str
    requests: tuple[ActionRequest, ...]


def normalize_name(name: str) -> str:
    """Validate an action name and return it lower-cased."""
    norm = str(name).strip().lower()
    if not _NAME_RE.match(norm):
        raise ActionError(
            f"invalid action name {name!r}: use lower-case letters/digits with optional "
            "internal '-'/'_'"
        )
    return norm


def parse_request_spec(spec: str) -> ActionRequest:
    """Parse the CLI form ``TARGET:HEX[:none|:expected]`` into an :class:`ActionRequest`."""
    parts = [p.strip() for p in str(spec).split(":")]
    if len(parts) not in (2, 3) or not all(parts):
        raise ActionError(
            f"request {spec!r}: expected TARGET:HEX[:none], e.g. functional:10D1:none"
        )
    response = parts[2].lower() if len(parts) == 3 else "expected"
    return _request({"to": parts[0], "send": parts[1], "response": response}, f"request {spec!r}")


def _request(raw: Any, where: str) -> ActionRequest:
    if not isinstance(raw, Mapping):
        raise ActionError(f"{where}: must be a mapping with 'to' and 'send'")
    unknown = set(raw) - REQUEST_FIELDS
    if unknown:
        raise ActionError(f"{where}: unknown field(s) {', '.join(sorted(map(str, unknown)))}")
    target = str(raw.get("to") or "").strip()
    if not target:
        raise ActionError(f"{where}: 'to' must name an ECU or 'functional'")
    send = str(raw.get("send") or "").replace(" ", "").upper()
    if not _HEX_RE.match(send):
        raise ActionError(f"{where}: 'send' must be whole hex bytes, got {raw.get('send')!r}")
    response = str(raw.get("response") or "expected").strip().lower()
    if response not in RESPONSE_MODES:
        raise ActionError(f"{where}: 'response' must be one of {', '.join(RESPONSE_MODES)}")
    norm_target = FUNCTIONAL if target.lower() == FUNCTIONAL else target.upper()
    return ActionRequest(norm_target, send, response)


def _reads(raw: Any, where: str) -> tuple[str, ...]:
    from canlib.query import QueryError, parse_selector

    if not isinstance(raw, list) or not raw:
        raise ActionError(f"{where}: 'reads' must be a non-empty list of ECU:PID selectors")
    out: list[str] = []
    for tok in raw:
        text = str(tok).strip()
        try:
            sel = parse_selector(text)
        except QueryError as e:
            raise ActionError(f"{where}: invalid read selector {text!r}: {e}") from None
        if not sel.pids:
            raise ActionError(
                f"{where}: read selector {text!r} must name its PIDs (ECU:PID), so the "
                "check reads exactly what the condition needs"
            )
        out.append(text)
    return tuple(out)


def _require(raw: Any, reads: Sequence[str], where: str) -> str:
    from canlib.query import parse_selector
    from canlib.states import StatePredicateError, compile_predicate, predicate_references

    expr = " ".join(str(raw or "").split())
    if not expr:
        raise ActionError(f"{where}: 'require' must be a condition over ECU.SIGNAL values")
    try:
        compile_predicate(expr)
        refs = predicate_references(expr)
    except StatePredicateError as e:
        raise ActionError(f"{where}: invalid 'require' condition: {e}") from None
    for node in ast.walk(ast.parse(expr, mode="eval")):
        if isinstance(node, ast.Or) or isinstance(node, ast.Not):
            raise ActionError(
                f"{where}: 'require' may not use 'or' or 'not' (every clause must hold)"
            )
    signals = [r for r in refs if "." in r]
    if not signals:
        raise ActionError(f"{where}: 'require' must test at least one ECU.SIGNAL")
    read_ecus = {parse_selector(r).ecu for r in reads}
    for ref in signals:
        ecu = ref.split(".", 1)[0].upper()
        if ecu not in read_ecus:
            raise ActionError(
                f"{where}: 'require' tests {ref}, but no 'reads' selector reads {ecu}, so it "
                "could never be TRUE"
            )
    return expr


def parse_action(name: str, raw: Any) -> Action:
    """Build an :class:`Action` from its YAML mapping, raising :class:`ActionError`."""
    key = normalize_name(name)
    where = f"action {key!r}"
    if not isinstance(raw, Mapping):
        raise ActionError(f"{where}: must be a mapping")
    unknown = set(raw) - ACTION_FIELDS
    if unknown:
        raise ActionError(f"{where}: unknown field(s) {', '.join(sorted(map(str, unknown)))}")
    reads = _reads(raw.get("reads"), where)
    require = _require(raw.get("require"), reads, where)
    requests_raw = raw.get("requests")
    if not isinstance(requests_raw, list) or not requests_raw:
        raise ActionError(f"{where}: 'requests' must be a non-empty list")
    requests = tuple(_request(r, f"{where} request {i + 1}") for i, r in enumerate(requests_raw))
    description = str(raw.get("description") or "").strip()
    return Action(key, description, reads, require, requests)


def parse_actions_doc(doc: Any) -> dict[str, Action]:
    """Parse a whole actions.yaml document (``{actions: {name: ...}}``)."""
    if doc is None:
        return {}
    if not isinstance(doc, Mapping) or not isinstance(doc.get("actions"), Mapping):
        raise ActionError("actions.yaml: top level must be a mapping with an 'actions:' mapping")
    actions: dict[str, Action] = {}
    for name, raw in doc["actions"].items():
        action = parse_action(str(name), raw)
        if action.name in actions:
            raise ActionError(f"duplicate action name {action.name!r}")
        actions[action.name] = action
    return actions


def registry_issues(action: Action, pids_data: Mapping[str, Any]) -> list[str]:
    """Problems resolving an action against the ECU registry (empty when it resolves).

    Checks that every request target and read ECU exists, that every read PID is
    defined, and that every signal the condition tests resolves (the same check
    ``canair validate states`` runs on state predicates).
    """
    from canlib.pids import build_ecu_index
    from canlib.query import parse_selector
    from canlib.states.refs import check_references

    index = build_ecu_index(dict(pids_data))
    issues: list[str] = []
    where = f"action {action.name!r}"
    for req in action.requests:
        if req.target != FUNCTIONAL and req.target not in index:
            issues.append(f"{where}: request target {req.target!r} is not a registered ECU")
    for tok in action.reads:
        sel = parse_selector(tok)
        entry = index.get(sel.ecu)
        if entry is None:
            issues.append(f"{where}: read {tok!r} names unknown ECU {sel.ecu!r}")
            continue
        defined = [k.upper() for k in entry["pids"]]
        for pid in sel.pids:
            # The query planner's own match: BC03 names 22BC03.
            if not any(k == pid or k.endswith(pid) for k in defined):
                issues.append(f"{where}: read {tok!r}: PID {pid} is not defined on {sel.ecu}")
    for issue in check_references([(action.name, action.require)], pids_data):
        issues.append(f"{where}: 'require' tests {issue.ref} — {issue.message}")
    return issues


def load_actions(profile: Profile | None = None) -> dict[str, Action]:
    """The active (or given) profile's actions; empty when it has no actions.yaml."""
    from canlib.profile import active

    path = (profile or active()).actions_file
    if not path.exists():
        return {}
    return parse_actions_doc(yaml_io.safe_load(path.read_text()))
