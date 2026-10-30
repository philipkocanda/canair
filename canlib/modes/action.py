"""Run a profile-defined action: ``canair action run NAME``.

The order is the safety argument, so it is fixed here:

1. **Plan, before touching the bus.** Every request is resolved to its request id
   and checked against the command blocklist. A refused request must be granted
   exactly by one of the profile's quirks (:func:`canlib.quirks.action_grants`),
   or the action is refused outright; ``--unsafe`` plays no part.
2. **Check the car.** The action's ``reads`` are read live and its ``require``
   condition evaluated on them. Only TRUE passes: FALSE refuses, and UNKNOWN (a
   signal that did not read) refuses after one re-read, which covers an ECU that
   was still waking up.
3. **Confirm**, unless ``--yes``. Without a terminal to ask on, ``--yes`` is
   required.
4. **Check again** with fresh reads, so a verdict from before a long pause at the
   prompt is never the one the requests go out on.
5. **Send**, with the grants active only for these requests, then restore the
   ordinary policy. Nothing else is sent afterwards.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from canlib.actions import FUNCTIONAL, Action, ActionError, ActionRequest
from canlib.safety import SafetyBlocked, SessionPolicy, check_command_safety
from canlib.transport.protocol import Terminal

# How long a request that expects no reply waits before it counts as sent.
_NO_RESPONSE_WAIT_S = 1.0
# Pause before the one re-read that an UNKNOWN verdict gets.
_REREAD_DELAY_S = 0.5


@dataclass(frozen=True)
class PlannedRequest:
    request: ActionRequest
    tx_id: int


@dataclass
class Verdict:
    """The ``require`` condition's result on one round of live reads."""

    result: str  # "TRUE" / "FALSE" / "UNKNOWN"
    signals: dict[str, float | None] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.result == "TRUE"


def functional_id(pids_data: dict) -> int:
    """The functional request id for the profile's addressing mode."""
    from canlib.addressing import functional_request_id, resolve_mode

    return functional_request_id(resolve_mode(pids_data))


def plan_requests(action: Action, pids_data: dict) -> list[PlannedRequest]:
    """Resolve each request's target to a request id (raises :class:`ActionError`)."""
    from canlib.ecus import canonical_ecu_name_safe
    from canlib.pids import build_ecu_index

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
    from canlib.quirks import action_grants

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


async def read_verdict(sm, action: Action, pids_data: dict, verbose: bool) -> Verdict:
    """Read the action's ``reads`` live and evaluate ``require`` on them."""
    from canlib.modes.multi_exec import _exec_query
    from canlib.pids import build_ecu_index
    from canlib.query import parse_selector
    from canlib.states import UNKNOWN, collect_values, compile_predicate, predicate_references

    index = build_ecu_index(pids_data)
    frames = []
    for tok in action.reads:
        sel = parse_selector(tok)
        got = await _exec_query(
            sm, sel.ecu, list(sel.pids), index, pids_data, verbose, return_results=True, quiet=True
        )
        if got:
            frames.append(got)
    values, responded = collect_values(frames)
    result = compile_predicate(action.require)(values, responded)
    signals = {r: values.get(r) for r in predicate_references(action.require) if "." in r}
    label = "UNKNOWN" if result is UNKNOWN else ("TRUE" if result else "FALSE")
    return Verdict(label, signals)


async def checked_verdict(sm, action: Action, pids_data: dict, verbose: bool) -> Verdict:
    """:func:`read_verdict`, re-read once if a signal did not read."""
    verdict = await read_verdict(sm, action, pids_data, verbose)
    if verdict.result == "UNKNOWN":
        await asyncio.sleep(_REREAD_DELAY_S)
        verdict = await read_verdict(sm, action, pids_data, verbose)
    return verdict


def _format_verdict(verdict: Verdict) -> list[str]:
    lines = [f"  Condition: {verdict.result}"]
    for ref, value in verdict.signals.items():
        shown = "not read" if value is None else f"{value:g}"
        lines.append(f"    {ref} = {shown}")
    return lines


def _confirm(action: Action, yes: bool, out) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise SafetyBlocked(
            f"action {action.name!r} changes the car's state; re-run with --yes to confirm "
            "(no terminal to ask on)"
        )
    print(f"\n  Type '{action.name}' to send, anything else to cancel: ", end="", file=out)
    try:
        answer = input()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() != action.name:
        raise SafetyBlocked(f"action {action.name!r} cancelled")


async def _send(terminal: Terminal, planned: list[PlannedRequest], policy: SessionPolicy):
    """Send the requests under ``policy``; stop at the first unexpected failure."""
    base = terminal.session_policy
    terminal.session_policy = policy
    results: list[dict[str, Any]] = []
    try:
        for p in planned:
            no_reply = p.request.response == "none"
            async with terminal.transaction():
                await terminal.set_header(p.tx_id)
                resp = await terminal.send_uds(
                    p.request.send, timeout=_NO_RESPONSE_WAIT_S if no_reply else None
                )
            row: dict[str, Any] = {
                "to": f"0x{p.tx_id:03X}",
                "send": p.request.send,
                "response": resp.get("hex") if resp.get("ok") else None,
                "nrc": resp.get("nrc"),
            }
            if resp.get("nrc") is not None:
                row["outcome"] = f"refused: NRC 0x{resp['nrc']:02X} {resp.get('nrc_desc', '')}"
            elif resp.get("ok"):
                row["outcome"] = "answered"
            elif no_reply:
                row["outcome"] = "sent (no reply expected)"
            else:
                row["outcome"] = f"no reply: {resp.get('error') or 'timeout'}"
            results.append(row)
            if not no_reply and not resp.get("ok"):
                break
    finally:
        terminal.session_policy = base
    return results


async def mode_action(
    terminal: Terminal,
    pids_data: dict,
    name: str,
    *,
    yes: bool = False,
    dry_run: bool = False,
    as_json: bool = False,
    verbose: bool = False,
) -> None:
    """Check, confirm and send one profile action (see the module docstring)."""
    from canlib.actions import load_actions
    from canlib.session_manager import SessionManager

    out = sys.stderr if as_json else sys.stdout
    actions = load_actions()
    action = actions.get(name.strip().lower())
    if action is None:
        known = ", ".join(actions) or "none"
        raise ActionError(f"no action {name!r} in this profile (defined: {known})")

    planned = plan_requests(action, pids_data)
    policy = action_policy(action, planned, terminal.session_policy, pids_data)

    print(f"\n  Action '{action.name}': {action.description or '(no description)'}", file=out)
    for p in planned:
        print(f"    will send {p.request.send} to 0x{p.tx_id:03X}", file=out)
    print(f"  Checking: {action.require}", file=out)

    sm = SessionManager(terminal, verbose=verbose)
    verdict = await checked_verdict(sm, action, pids_data, verbose)
    for line in _format_verdict(verdict):
        print(line, file=out)
    if not verdict.passed:
        raise SafetyBlocked(
            f"action {action.name!r} refused: its condition is {verdict.result}, not TRUE"
        )

    report: dict[str, Any] = {
        "action": action.name,
        "condition": verdict.result,
        "signals": verdict.signals,
        "dry_run": dry_run,
        "sent": [],
    }
    if dry_run:
        print("  Dry run: condition met, nothing sent.", file=out)
    else:
        _confirm(action, yes, out)
        verdict = await read_verdict(sm, action, pids_data, verbose)
        if not verdict.passed:
            for line in _format_verdict(verdict):
                print(line, file=out)
            raise SafetyBlocked(
                f"action {action.name!r} refused: the condition became {verdict.result} "
                "before sending"
            )
        report["sent"] = await _send(terminal, planned, policy)
        for row in report["sent"]:
            print(f"  {row['send']} → {row['to']}: {row['outcome']}", file=out)
        print(
            "  Done. Anything that talks to the car afterwards (canair, the WiCAN's AutoPID "
            "polling, a phone app) can wake it again.",
            file=out,
        )
    if as_json:
        json.dump(report, sys.stdout, indent=2, default=str)
        print()
