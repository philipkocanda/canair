"""Run a profile-defined action: ``canair action run NAME``.

The order is the safety argument, so it is fixed here:

1. **Plan, before touching the bus.** Every request is resolved to its request id
   and checked against the command blocklist. A refused request must be granted
   exactly by one of the profile's quirks (:func:`canair.safety.quirks.action_grants`),
   or the action is refused outright; ``--unsafe`` plays no part.
2. **Check the car.** The action's ``reads`` are read live and its ``require``
   condition evaluated on them. Only TRUE passes: FALSE refuses, and UNKNOWN (a
   signal that did not read) refuses after one re-read, which covers an ECU that
   was still waking up.
3. **Confirm**, unless ``--yes``: the action's name is typed. Without a terminal to
   ask on, ``--yes`` is required (exit 2); a wrong answer ends the run (exit 1).
4. **Check again** with fresh reads, so a verdict from before a long pause at the
   prompt is never the one the requests go out on.
5. **Send**, with the grants active only for these requests, then restore the
   ordinary policy. The ``hold:`` sessions open first and stay alive to the end.
6. **Read** the ``then:`` selectors, after its ``wait``, if every request was answered.
7. **Release**: the ``release:`` requests go out whenever a request did, also after a
   refusal, an error or Ctrl-C, and only then do the held sessions close. Nothing else is
   sent afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from canair.actions import Action, ActionError, load_actions
from canair.actions.plan import PlannedRequest, action_policy, plan_requests
from canair.core.prompt import typed_confirm
from canair.ecus.pids import build_ecu_index
from canair.live.read.exec import exec_query
from canair.live.read.multi import mode_multi
from canair.profile.model import Profile
from canair.query import parse_selector
from canair.safety.blocklist import SafetyBlocked, SessionPolicy
from canair.states.predicates import UNKNOWN, compile_predicate, predicate_references
from canair.states.suggest import collect_values
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.addressing import FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID
from canair.uds.echo import request_echo
from canair.uds.nrc import is_negative_response, is_response_pending
from canair.uds.services import positive_response_sid

# How long a request that expects no reply waits before it counts as sent.
_NO_RESPONSE_WAIT_S = 1.0
# Pause before the one re-read that an UNKNOWN verdict gets.
_REREAD_DELAY_S = 0.5


@dataclass
class Verdict:
    """The ``require`` condition's result on one round of live reads."""

    result: str  # "TRUE" / "FALSE" / "UNKNOWN"
    signals: dict[str, float | None] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.result == "TRUE"


async def read_verdict(
    sm, action: Action, pids_data: dict, verbose: bool, *, profile: Profile
) -> Verdict:
    """Read the action's ``reads`` live and evaluate ``require`` on them."""

    index = build_ecu_index(pids_data)
    frames = []
    for tok in action.reads:
        sel = parse_selector(tok)
        got = await exec_query(
            sm,
            sel.ecu,
            list(sel.pids),
            index,
            pids_data,
            verbose,
            return_results=True,
            quiet=True,
            profile=profile,
        )
        if got:
            frames.append(got)
    values, responded = collect_values(frames)
    result = compile_predicate(action.require)(values, responded)
    signals = {r: values.get(r) for r in predicate_references(action.require) if "." in r}
    label = "UNKNOWN" if result is UNKNOWN else ("TRUE" if result else "FALSE")
    return Verdict(label, signals)


async def checked_verdict(
    sm, action: Action, pids_data: dict, verbose: bool, *, profile: Profile
) -> Verdict:
    """:func:`read_verdict`, re-read once if a signal did not read."""
    verdict = await read_verdict(sm, action, pids_data, verbose, profile=profile)
    if verdict.result == "UNKNOWN":
        await asyncio.sleep(_REREAD_DELAY_S)
        verdict = await read_verdict(sm, action, pids_data, verbose, profile=profile)
    return verdict


def _format_verdict(verdict: Verdict) -> list[str]:
    lines = [f"  Condition: {verdict.result}"]
    for ref, value in verdict.signals.items():
        shown = "not read" if value is None else f"{value:g}"
        lines.append(f"    {ref} = {shown}")
    return lines


def _confirm(action: Action, yes: bool) -> None:
    """Ask for the action's name; a decline ends the run (exit 1), no terminal is exit 2."""
    if not typed_confirm(f"Send action {action.name!r} to the car?", action.name, yes=yes):
        raise SystemExit(1)


async def _send(terminal: Terminal, planned: list[PlannedRequest], policy: SessionPolicy):
    """Send the requests under ``policy``; stop at the first unexpected failure."""
    base = terminal.session_policy
    terminal.session_policy = policy
    results: list[dict[str, Any]] = []
    try:
        for p in planned:
            no_reply = p.request.response == "none"
            if p.tx_id in (FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID) and not no_reply:
                row = await _send_functional(terminal, p)
            else:
                row = await _send_physical(terminal, p, no_reply)
            results.append(row)
            if not no_reply and row["outcome"] != "answered":
                break
    finally:
        terminal.session_policy = base
    return results


async def _send_physical(terminal: Terminal, p: PlannedRequest, no_reply: bool) -> dict[str, Any]:
    """Send one request to one id, checked against the reply its request fixes.

    The guard reads went to the same ECUs just before, so a late reply to one of them must
    read as stale, not as this request's answer.
    """
    send = p.request.send
    expected = request_echo(send, terminal.session_policy.protocol(p.tx_id))
    async with terminal.transaction():
        await terminal.set_header(p.tx_id)
        resp = await terminal.send_uds(
            send,
            timeout=_NO_RESPONSE_WAIT_S if no_reply else None,
            expected_sid=int(send[:2], 16),
            expected_echo=expected[1] if expected is not None else None,
        )
    row: dict[str, Any] = {
        "to": f"0x{p.tx_id:03X}",
        "send": send,
        "response": resp.get("hex") if resp.get("ok") and not resp.get("suppressed") else None,
        "nrc": resp.get("nrc"),
    }
    if resp.get("nrc") is not None:
        row["outcome"] = f"refused: NRC 0x{resp['nrc']:02X} {resp.get('nrc_desc', '')}"
    elif resp.get("ok") and not resp.get("suppressed"):
        row["outcome"] = "answered"
    elif no_reply or resp.get("suppressed"):
        row["outcome"] = "sent (no reply expected)"
    else:
        row["outcome"] = f"no reply: {resp.get('error') or 'timeout'}"
    return row


async def _send_functional(terminal: Terminal, p: PlannedRequest) -> dict[str, Any]:
    """Broadcast one request that expects replies, and report every responder.

    The one-ECU send path would keep a single reply, so "answered" could mean one ECU
    agreed while another refused. Here the action counts as answered only when every
    responder accepted it and at least one did.
    """
    send = p.request.send
    result = await terminal.send_functional(p.tx_id, send)
    sid = int(send[:2], 16)
    replies = [
        m
        for m in result.messages
        if m.payload[:1] == bytes([positive_response_sid(sid) & 0xFF])
        or is_negative_response(m.payload, sid)
    ]
    responders = [
        {
            "from": f"0x{m.rx_id:X}",
            "response": m.payload.hex().upper(),
            "nrc": m.payload[2] if is_negative_response(m.payload, sid) else None,
        }
        for m in replies
        if not is_response_pending(m.payload, sid)
    ]
    refused = [r for r in responders if r["nrc"] is not None]
    if refused:
        names = ", ".join(f"{r['from']} (NRC 0x{r['nrc']:02X})" for r in refused)
        outcome = f"refused by {names}"
    elif responders:
        outcome = "answered"
    else:
        outcome = "no reply"
    return {
        "to": f"0x{p.tx_id:03X}",
        "send": send,
        "response": None,
        "nrc": refused[0]["nrc"] if refused else None,
        "responders": responders,
        "outcome": outcome,
    }


def _print_rows(rows: list[dict[str, Any]], out) -> None:
    for row in rows:
        print(f"  {row['send']} → {row['to']}: {row['outcome']}", file=out)
        for r in row.get("responders", ()):
            print(f"    {r['from']}: {r['response']}", file=out)


@dataclass(frozen=True)
class _Run:
    """Everything one action run needs once its plan and policy are settled."""

    terminal: Terminal
    sm: SessionManager
    action: Action
    pids_data: dict
    planned: list[PlannedRequest]
    releases: list[PlannedRequest]
    policy: SessionPolicy
    out: Any
    verbose: bool
    profile: Profile
    # How the then: reads are kept (``canair action run --save``).
    save: bool = False
    label: str = ""
    vehicle_states: Any = None
    notes: str | None = None
    as_json: bool = False


async def _run_held(run: _Run, report: dict[str, Any]) -> None:
    """Hold the sessions, send the requests, read, and always send the release."""
    index = build_ecu_index(run.pids_data)
    async with contextlib.AsyncExitStack() as stack:
        for held in run.action.hold:
            outcome = await stack.enter_async_context(
                run.sm.held(int(index[held.ecu]["tx_id"]), mode=held.mode, close=True, report=False)
            )
            if not outcome.ok:
                raise SafetyBlocked(
                    f"action {run.action.name!r}: could not open the {held.mode} session on "
                    f"{held.ecu} ({outcome.problem()}); nothing was sent"
                )
        try:
            report["sent"] = await _send(run.terminal, run.planned, run.policy)
            _print_rows(report["sent"], run.out)
            complete = len(report["sent"]) == len(run.planned) and all(
                row["outcome"] in ("answered", "sent (no reply expected)") for row in report["sent"]
            )
            if run.action.then and complete:
                report["then"] = await _read_then(run)
        finally:
            if run.releases:
                print("  Releasing…", file=run.out)
                report["released"] = await _send_releases(run)
                _print_rows(report["released"], run.out)


async def _send_releases(run: _Run) -> list[dict[str, Any]]:
    """Send every release, even after one fails: each hands back a different output."""
    rows: list[dict[str, Any]] = []
    for p in run.releases:
        try:
            rows.extend(await _send(run.terminal, [p], run.policy))
        except Exception as exc:  # a dropped link must not skip the next release
            rows.append(
                {"to": f"0x{p.tx_id:03X}", "send": p.request.send, "outcome": f"failed: {exc}"}
            )
    return rows


async def _read_then(run: _Run) -> list[dict] | None:
    """Wait, then read the ``then:`` selectors through the ``canair read`` pipeline."""
    then = run.action.then
    assert then is not None
    if then.wait:
        print(f"  Waiting {then.wait:g} s…", file=run.out)
        await asyncio.sleep(then.wait)
    steps = [f"query {' '.join(then.read)}"]
    kwargs: dict[str, Any] = {
        "no_repl": True,
        "save": run.save,
        "label": run.label,
        "vehicle_states": run.vehicle_states,
        "notes": run.notes,
        "profile": run.profile,
    }
    if not run.as_json:
        await mode_multi(run.terminal, steps, run.pids_data, run.verbose, **kwargs)
        return None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await mode_multi(run.terminal, steps, run.pids_data, run.verbose, as_json=True, **kwargs)
    return json.loads(buf.getvalue() or '{"results": []}')["results"]


async def mode_action(
    terminal: Terminal,
    pids_data: dict,
    name: str,
    *,
    yes: bool = False,
    dry_run: bool = False,
    as_json: bool = False,
    verbose: bool = False,
    save: bool = False,
    label: str | None = None,
    vehicle_states=None,
    notes: str | None = None,
    profile: Profile,
) -> None:
    """Check, confirm and send one profile action (see the module docstring).

    ``save`` and its metadata keep the ``then:`` reads in captures/. The label defaults to
    ``action NAME``, so nothing is asked while the action holds an output.
    """
    out = sys.stderr if as_json else sys.stdout
    actions = load_actions(profile=profile)
    action = actions.get(name.strip().lower())
    if action is None:
        known = ", ".join(actions) or "none"
        raise ActionError(f"no action {name!r} in this profile (defined: {known})")

    planned = plan_requests(action, pids_data, profile=profile)
    releases = plan_requests(action, pids_data, profile=profile, release=True)
    policy = action_policy(action, [*planned, *releases], terminal.session_policy, pids_data)

    print(f"\n  Action '{action.name}': {action.description or '(no description)'}", file=out)
    for held in action.hold:
        print(f"    will hold a {held.mode} session on {held.ecu}", file=out)
    for p in planned:
        print(f"    will send {p.request.send} to 0x{p.tx_id:03X}", file=out)
    if action.then:
        then = f"wait {action.then.wait:g} s, then read {' '.join(action.then.read)}"
        print(f"    will {then}", file=out)
    for p in releases:
        print(f"    will release with {p.request.send} to 0x{p.tx_id:03X}", file=out)
    print(f"  Checking: {action.require}", file=out)

    sm = SessionManager(terminal, verbose=verbose)
    verdict = await checked_verdict(sm, action, pids_data, verbose, profile=profile)
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
        _confirm(action, yes)
        verdict = await read_verdict(sm, action, pids_data, verbose, profile=profile)
        if not verdict.passed:
            for line in _format_verdict(verdict):
                print(line, file=out)
            raise SafetyBlocked(
                f"action {action.name!r} refused: the condition became {verdict.result} "
                "before sending"
            )
        run = _Run(
            terminal,
            sm,
            action,
            pids_data,
            planned,
            releases,
            policy,
            out,
            verbose,
            profile,
            save=save,
            label=label or f"action {action.name}",
            vehicle_states=vehicle_states,
            notes=notes,
            as_json=as_json,
        )
        await _run_held(run, report)
        if action.then or action.release:
            print("  Done.", file=out)
        else:
            print(
                "  Done. Anything that talks to the car afterwards (canair, the WiCAN's AutoPID "
                "polling, a phone app) can wake it again.",
                file=out,
            )
    if as_json:
        json.dump(report, sys.stdout, indent=2, default=str)
        print()
