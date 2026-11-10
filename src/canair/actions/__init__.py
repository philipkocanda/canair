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

An action can also hold something on while it reads, and hand it back afterwards::

      ig3_read:
        reads: [IGPM:22BC03, IGPM:22BC07]
        require: IGPM.IGN1_SWITCH == 0 and IGPM.IG3_RELAY_OUTPUT == 0
        hold: [IGPM]                       # sessions canair keeps alive throughout
        requests:
          - {to: igpm, send: 2FBC4403}     # IOControl ON
        then: {wait: 3, read: [VCU:2101, BMS:2101]}
        release:
          - {to: igpm, send: 2FBC4400}     # sent at the end, also on Ctrl-C or an error

An IOControl request that drives an output (``0x2F``/``0x30``) must have a ``release``
that hands the same output back (:func:`canair.safety.actuation.release_mismatch`), as
``canair io`` requires: an action never leaves an output driven on purpose.

An action may also inject a non-diagnostic broadcast CAN frame, under ``frames:``::

      remote-climate-start:
        reads: [VCU:2101]
        require: VCU.VCU_IG3_ON == 1
        frames:
          - id: 0x587                             # the arbitration id (must be < 0x700)
            data: "10 00 00 00 00 00 00 00"        # the 8-byte payload template
            mask: "10 00 00 00 00 00 00 0F"        # the only bits this frame may drive
            counter: {byte: 7, nibble: low, step: 1}   # advance a free-running counter
            rate_ms: 100
            duration_s: 30

A frame is far more dangerous than a diagnostic request: it is unauthenticated and reaches
ECUs directly, with no reply to confirm it. Four independent guards gate it: the profile
declares the exact frame (never a free-form id/payload); ``safety.allow_frame_injection`` is a
fail-closed config key; the action's ``reads``/``require`` still gate it; and it is refused on
any id ``>= 0x700`` (so an action can never forge a diagnostic response) and on any transport
but ``slcan-tcp``. The ``mask`` is the author's statement of which bits the frame may set: a
``data`` (or ``counter``) bit outside it is a load error, which is how every dangerous bit of a
frame is provably held at 0.

Two rules keep a profile from using this to get around canair's safety policy:

- **Every request still passes the command blocklist** (:mod:`canair.safety.blocklist`).
  A request it refuses can only be sent when a code-owned quirk grants exactly
  that request (:func:`canair.safety.quirks.action_grants`), and never with
  ``--unsafe``. So an action can't send ``11 01`` or a programming session.
- **Every action is guarded.** ``reads`` and ``require`` are mandatory; the
  condition is evaluated on values read live just before the requests are sent,
  and only a TRUE result passes (a signal that was not read is UNKNOWN, which
  refuses). The condition may not use ``or`` or ``not``: an alternative source
  would make it looser, never stricter.

This module is device-free: the model, the structural checks shared by the
loader, the editor and ``canair validate actions``, and :func:`load_actions`. The
live run is :mod:`canair.live.actuate.action`.

Belongs: parsing a profile's ``actions.yaml`` into guarded actions and planning their requests.
Not here: running one against the car (``live/actuate/action.py``) and the ``canair action``
command.
Imports: ``safety``, ``uds``, ``ecus`` and ``core``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from canair.actions.errors import ActionError
from canair.actions.frames import FrameRequest as FrameRequest
from canair.actions.frames import _frame
from canair.actions.frames import parse_frame_spec as parse_frame_spec
from canair.core import yaml_io
from canair.ecus.pids import build_ecu_index
from canair.query import QueryError, parse_selector, pids_named_by
from canair.safety.actuation import ACTUATE, classify, parse_io_request, release_mismatch
from canair.safety.blocklist import check_session_mode
from canair.states.predicates import StatePredicateError, compile_predicate, predicate_references
from canair.states.refs import check_references
from canair.uds.protocol import UNKNOWN
from canair.uds.subfunctions import format_session_type, parse_session_type

if TYPE_CHECKING:
    from canair.profile.model import Profile

# The request target that means "the functional (broadcast) request id".
FUNCTIONAL: Final = "functional"

ResponseMode = Literal["none", "expected"]
RESPONSE_MODES: Final[tuple[ResponseMode, ...]] = ("none", "expected")

ACTION_FIELDS: Final = frozenset(
    {"description", "reads", "require", "hold", "requests", "then", "release", "frames"}
)
REQUEST_FIELDS: Final = frozenset({"to", "send", "response"})
THEN_FIELDS: Final = frozenset({"wait", "read"})
# The longest a `then:` may wait: an action holds sessions (and maybe outputs) meanwhile.
MAX_THEN_WAIT_S: Final = 60.0

# An action name: a lower-cased token, like a group name.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_HEX_RE = re.compile(r"^(?:[0-9A-F]{2})+$")


@dataclass(frozen=True)
class ActionRequest:
    """One request an action sends: its target, bytes, and whether a reply is expected."""

    target: str
    send: str
    response: ResponseMode = "expected"

    def describe(self) -> str:
        return f"{self.target}:{self.send}" + (":none" if self.response == "none" else "")


@dataclass(frozen=True)
class HeldSession:
    """A diagnostic session canair opens before the requests and keeps alive to the end."""

    ecu: str
    mode: str = "03"

    def describe(self) -> str:
        return f"{self.ecu}:{self.mode}"


@dataclass(frozen=True)
class ThenReads:
    """What an action reads after its requests: wait ``wait`` seconds, then read ``read``."""

    wait: float
    read: tuple[str, ...]


@dataclass(frozen=True)
class Action:
    """A named, guarded profile action."""

    name: str
    description: str
    reads: tuple[str, ...]
    require: str
    requests: tuple[ActionRequest, ...]
    hold: tuple[HeldSession, ...] = ()
    then: ThenReads | None = None
    release: tuple[ActionRequest, ...] = ()
    frames: tuple[FrameRequest, ...] = ()


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


def parse_hold_spec(spec: Any) -> HeldSession:
    """Parse ``ECU`` or ``ECU:MODE`` (MODE two hex digits, default ``03``)."""
    ecu, _, mode = str(spec).strip().partition(":")
    if not ecu.strip():
        raise ActionError(f"hold {spec!r}: expected ECU or ECU:MODE, e.g. IGPM:03")
    try:
        sub = parse_session_type(mode or "03")
    except ValueError as e:
        raise ActionError(f"hold {spec!r}: {e}") from None
    blocked = check_session_mode(sub)
    if blocked is not None:
        raise ActionError(f"hold {spec!r}: {blocked}")
    return HeldSession(ecu.strip().upper(), format_session_type(sub))


def _hold(raw: Any, where: str) -> tuple[HeldSession, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ActionError(f"{where}: 'hold' must be a list of ECU or ECU:MODE")
    return tuple(parse_hold_spec(item) for item in raw)


def parse_then(raw: Any, where: str) -> ThenReads | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ActionError(f"{where}: 'then' must be a mapping with 'read' (and 'wait')")
    unknown = set(raw) - THEN_FIELDS
    if unknown:
        raise ActionError(
            f"{where}: 'then' has unknown field(s) {', '.join(sorted(map(str, unknown)))}"
        )
    try:
        wait = float(raw.get("wait") or 0)
    except (TypeError, ValueError):
        raise ActionError(
            f"{where}: 'then.wait' must be seconds, got {raw.get('wait')!r}"
        ) from None
    if not 0 <= wait <= MAX_THEN_WAIT_S:
        raise ActionError(f"{where}: 'then.wait' must be 0-{MAX_THEN_WAIT_S:g} s, got {wait:g}")
    read = raw.get("read")
    if not isinstance(read, list) or not read:
        raise ActionError(f"{where}: 'then.read' must be a non-empty list of ECU[:PID] selectors")
    for tok in read:
        try:
            parse_selector(str(tok).strip())
        except QueryError as e:
            raise ActionError(f"{where}: invalid then.read selector {tok!r}: {e}") from None
    return ThenReads(wait, tuple(str(tok).strip() for tok in read))


def _unreleased(requests: Sequence[ActionRequest], release: Sequence[ActionRequest]) -> list[str]:
    """The IOControl requests that drive an output no ``release`` request hands back."""
    missing = []
    for req in requests:
        io = parse_io_request(req.send)
        if io is None or classify(req.send, UNKNOWN) != ACTUATE:
            continue
        if not any(
            rel.target == req.target and release_mismatch(req.send, rel.send) is None
            for rel in release
        ):
            missing.append(f"{req.target}:{req.send}")
    return missing


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
    frames_raw = raw.get("frames")
    if requests_raw is not None and not isinstance(requests_raw, list):
        raise ActionError(f"{where}: 'requests' must be a list")
    if frames_raw is not None and not isinstance(frames_raw, list):
        raise ActionError(f"{where}: 'frames' must be a list")
    if not requests_raw and not frames_raw:
        raise ActionError(f"{where}: needs at least one 'requests' or 'frames' entry")
    requests = tuple(
        _request(r, f"{where} request {i + 1}") for i, r in enumerate(requests_raw or [])
    )
    frames = tuple(_frame(f, f"{where} frame {i + 1}") for i, f in enumerate(frames_raw or []))
    release_raw = raw.get("release")
    if release_raw is not None and not isinstance(release_raw, list):
        raise ActionError(f"{where}: 'release' must be a list of requests")
    release = tuple(
        _request(r, f"{where} release {i + 1}") for i, r in enumerate(release_raw or [])
    )
    missing = _unreleased(requests, release)
    if missing:
        raise ActionError(
            f"{where}: {', '.join(missing)} drives an output, so 'release' must hand it back "
            "(an IOControl request on the same ECU and identifier, e.g. returnControlToECU)"
        )
    description = str(raw.get("description") or "").strip()
    return Action(
        key,
        description,
        reads,
        require,
        requests,
        hold=_hold(raw.get("hold"), where),
        then=parse_then(raw.get("then"), where),
        release=release,
        frames=frames,
    )


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

    index = build_ecu_index(dict(pids_data))
    issues: list[str] = []
    where = f"action {action.name!r}"
    for req in (*action.requests, *action.release):
        if req.target != FUNCTIONAL and req.target not in index:
            issues.append(f"{where}: request target {req.target!r} is not a registered ECU")
    for held in action.hold:
        if held.ecu not in index:
            issues.append(f"{where}: hold {held.ecu!r} is not a registered ECU")
    for tok in action.then.read if action.then else ():
        sel = parse_selector(tok)
        entry = index.get(sel.ecu)
        if entry is None:
            issues.append(f"{where}: then.read {tok!r} names unknown ECU {sel.ecu!r}")
            continue
        for pid in sel.pids:
            if not pids_named_by(pid, entry["pids"]):
                issues.append(f"{where}: then.read {tok!r}: PID {pid} is not defined on {sel.ecu}")
    for tok in action.reads:
        sel = parse_selector(tok)
        entry = index.get(sel.ecu)
        if entry is None:
            issues.append(f"{where}: read {tok!r} names unknown ECU {sel.ecu!r}")
            continue
        for pid in sel.pids:
            # The query planner's own match: BC03 names 22BC03.
            if not pids_named_by(pid, entry["pids"]):
                issues.append(f"{where}: read {tok!r}: PID {pid} is not defined on {sel.ecu}")
    for issue in check_references([(action.name, action.require)], pids_data):
        issues.append(f"{where}: 'require' tests {issue.ref} — {issue.message}")
    return issues


def load_actions(profile: Profile) -> dict[str, Action]:
    """The profile's actions; empty when it has no actions.yaml."""
    path = profile.actions_file
    if not path.exists():
        return {}
    return parse_actions_doc(yaml_io.safe_load(path.read_text()))
