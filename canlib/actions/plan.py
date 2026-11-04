"""Plan a profile action before it touches the bus: resolve targets, check the blocklist.

Pure and device-free. The live run (:mod:`canlib.live.action`) plans first and sends only what
this module has resolved and checked, and ``canair validate actions`` calls it to prove an
action could run at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from canlib.actions import FUNCTIONAL, Action, ActionError, ActionRequest
from canlib.safety.blocklist import SafetyBlocked, SessionPolicy, check_command_safety


@dataclass(frozen=True)
class PlannedRequest:
    request: ActionRequest
    tx_id: int


def functional_id(pids_data: dict) -> int:
    """The functional request id for the profile's addressing mode."""
    from canlib.uds.addressing import functional_request_id, resolve_mode

    return functional_request_id(resolve_mode(pids_data))


def plan_requests(action: Action, pids_data: dict) -> list[PlannedRequest]:
    """Resolve each request's target to a request id (raises :class:`ActionError`)."""
    from canlib.ecus import canonical_ecu_name_safe
    from canlib.ecus.pids import build_ecu_index

    index = build_ecu_index(pids_data)
    planned: list[PlannedRequest] = []
    for req in action.requests:
        if req.target == FUNCTIONAL:
            planned.append(PlannedRequest(req, functional_id(pids_data)))
            continue
        entry = index.get(req.target) or index.get(canonical_ecu_name_safe(req.target).upper())
        if entry is None:
            raise ActionError(f"action {action.name!r}: unknown request target {req.target!r}")
        planned.append(PlannedRequest(req, int(entry["tx_id"])))
    return planned


def action_policy(
    action: Action, planned: list[PlannedRequest], base: SessionPolicy, pids_data: dict
) -> SessionPolicy:
    """The policy the requests go out under, or :class:`SafetyBlocked` if one is refused.

    The base policy plus only those quirk grants this action needs, so the
    exemption is no wider than the requests themselves.
    """
    from canlib.safety.quirks import action_grants

    available = action_grants(pids_data)
    needed: set[tuple[int, str]] = set()
    for p in planned:
        if check_command_safety(p.request.send, base, p.tx_id) is None:
            continue
        key = (p.tx_id, p.request.send)
        if key not in available:
            blocked = check_command_safety(p.request.send, base, p.tx_id)
            raise SafetyBlocked(
                f"action {action.name!r}: {p.request.send} to 0x{p.tx_id:03X} is refused by the "
                f"command blocklist ({blocked}), and none of the profile's quirks grants it. "
                "Actions never take --unsafe."
            )
        needed.add(key)
    policy = base.granting(needed)
    for p in planned:
        blocked = check_command_safety(p.request.send, policy, p.tx_id)
        if blocked is not None:  # a hard block no grant can lift
            raise SafetyBlocked(f"action {action.name!r}: {blocked}")
    return policy
