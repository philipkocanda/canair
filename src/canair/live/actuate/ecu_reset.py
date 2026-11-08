"""``canair reset ECU TYPE``: send one ECUReset (``0x11``), then wait for the ECU to answer again.

ECUReset is in the command blocklist. This sends one reset to one ECU under an exact grant on
the terminal's session policy, for that send only (:mod:`canair.safety.reset`), so ``raw`` and
``read`` still refuse it. The caller checks ``safety.allow_reset`` and asks the user first.

An ECU answers ``51 {type}`` and then restarts, or restarts before it answers, so no reply does
not prove that nothing happened. After the restart the ECU is in its default session: a session
opened for the request (``--session``) is gone, and so is any security access or IOControl
override. canair then sends TesterPresent until the ECU answers again, and reports how long that
took and whether the ECU was ever silent. A first answer with no silence before it means canair
could not see the restart, not that there was none.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass

from canair.ecus.wake import WakePlan
from canair.render import ansi, glyphs, messages
from canair.render.paint import Painter
from canair.safety.blocklist import SafetyBlocked
from canair.safety.reset import reset_block, reset_type_name
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.echo import request_echo
from canair.uds.nrc import (
    KWP_NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_DIAGNOSTIC_SESSION,
    NRC_CONDITIONS_NOT_CORRECT,
    NRC_SECURITY_ACCESS_DENIED,
    NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION,
    NRC_SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION,
)
from canair.uds.protocol import KWP, UDS
from canair.uds.requests import ecu_reset_request
from canair.uds.response import UdsResponse
from canair.uds.services import ECU_RESET, TESTER_PRESENT, TESTER_PRESENT_REQUEST

# Seconds. The reply may wait on a responsePending (0x78), which the terminal handles.
_REPLY_TIMEOUT_S = 5.0
# A TesterPresent while the ECU restarts, and the gap between two of them. The first waits a
# moment, so it does not reach the ECU in the instant between its reply and its restart.
_POLL_TIMEOUT_S = 0.5
_POLL_GAP_S = 0.2
_FIRST_POLL_DELAY_S = 0.2

_SESSION_HINT = "the ECU resets only in another session: retry with --session (10 03)"
_NRC_HINTS = {
    NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION: _SESSION_HINT,
    KWP_NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_DIAGNOSTIC_SESSION: _SESSION_HINT,
    NRC_SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION: "the ECU takes this reset type only in "
    "another session: retry with --session (10 03)",
    NRC_CONDITIONS_NOT_CORRECT: "the ECU's own conditions for a reset are not met: many ECUs "
    "reset only with the ignition on and the engine or drive system off",
    NRC_SECURITY_ACCESS_DENIED: "the ECU resets only after SecurityAccess (0x27), which canair "
    "does not unlock",
}


@dataclass(frozen=True)
class Restart:
    """What the TesterPresent polls after a reset saw."""

    #: Seconds from the reset request to the ECU's first answer, or None if it never answered.
    back_after_s: float | None
    #: Whether a poll went unanswered before the ECU answered (or until the wait ran out).
    went_silent: bool


async def mode_ecu_reset(
    terminal: Terminal,
    tx_id: int,
    reset_type: int,
    *,
    ecu: str,
    session: bool = False,
    wake: bool = False,
    wake_plan: WakePlan | None = None,
    restart_timeout: float = 10.0,
    as_json: bool = False,
    verbose: bool = False,
) -> bool:
    """Reset ``tx_id`` with ``reset_type``; True when it accepted the reset and answered again.

    ``ecu`` is the ECU as the report names it. ``session``/``wake`` open the extended session
    first (some ECUs reset only there). ``restart_timeout`` 0 skips the wait for the ECU.
    Raises :class:`SafetyBlocked` when :func:`canair.safety.reset.reset_block` refuses.
    """
    protocol = terminal.session_policy.protocol(tx_id)
    blocked = reset_block(reset_type, protocol, tx_id)
    if blocked is not None:
        raise SafetyBlocked(blocked)
    request = ecu_reset_request(reset_type)
    type_name = reset_type_name(reset_type)
    stream = sys.stderr if as_json else sys.stdout
    paint = Painter(stream)

    def say(text: str = "") -> None:
        print(text, file=stream)

    say(f"\n  ECU reset: {paint(ecu, ansi.raw.BOLD + ansi.raw.CYAN)}")
    say(f"  Command: {request} (ECUReset {type_name}, {_protocol_label(protocol)})\n")

    sm = SessionManager(terminal, verbose=verbose)
    if session or wake:
        await sm.open_session(tx_id, wake=wake, wake_plan=wake_plan, force=True)
    started = time.monotonic()
    response = await send_reset(terminal, tx_id, request)
    # Whatever the reply, the session canair opened may be gone: never keep it alive.
    sm.table.forget(tx_id)

    nrc = response.get("nrc")
    accepted = bool(response.get("ok"))
    if accepted:
        say(f"  {paint(glyphs.OK, ansi.raw.GREEN)} {ecu} accepted the reset: {response['hex']}")
    elif nrc is not None:
        nrc_text = paint(f"NRC 0x{nrc:02X}", ansi.raw.YELLOW)
        say(f"  {paint(glyphs.FAIL, ansi.raw.RED)} {nrc_text}: {response.get('nrc_desc', '')}")
    else:
        error = response.get("error") or "no reply"
        say(f"  {paint(glyphs.FAIL, ansi.raw.RED)} no reply ({error})")

    restart = None
    if nrc is None and restart_timeout > 0:
        restart = await wait_for_restart(terminal, tx_id, started, restart_timeout)
        say(_restart_line(restart, ecu, restart_timeout, paint))
    say()

    if as_json:
        print(json.dumps(_document(ecu, tx_id, request, type_name, protocol, response, restart)))
    _report(ecu, response, restart, restart_timeout)
    return accepted and (restart is None or restart.back_after_s is not None)


async def send_reset(terminal: Terminal, tx_id: int, request: str) -> UdsResponse:
    """Send ECUReset ``request`` to ``tx_id`` under an exact grant for this one send."""
    base = terminal.session_policy
    expected = request_echo(request, base.protocol(tx_id))
    terminal.session_policy = base.granting({(tx_id, request)})
    try:
        async with terminal.transaction():
            await terminal.set_header(tx_id)
            return await terminal.send_uds(
                request,
                timeout=_REPLY_TIMEOUT_S,
                expected_sid=ECU_RESET,
                expected_echo=expected[1] if expected is not None else None,
            )
    finally:
        terminal.session_policy = base


async def wait_for_restart(
    terminal: Terminal, tx_id: int, started: float, timeout: float
) -> Restart:
    """Send TesterPresent to ``tx_id`` until it answers, for ``timeout`` s after ``started``.

    A negative response is an answer too: the ECU is running again.
    """
    await asyncio.sleep(_FIRST_POLL_DELAY_S)
    went_silent = False
    while True:
        async with terminal.transaction():
            await terminal.set_header(tx_id)
            resp = await terminal.send_uds(
                TESTER_PRESENT_REQUEST, timeout=_POLL_TIMEOUT_S, expected_sid=TESTER_PRESENT
            )
        if resp.get("ok") or resp.get("nrc") is not None:
            return Restart(round(time.monotonic() - started, 2), went_silent)
        went_silent = True
        if time.monotonic() - started >= timeout:
            return Restart(None, went_silent)
        await asyncio.sleep(_POLL_GAP_S)


def _protocol_label(protocol: str) -> str:
    if protocol == UDS:
        return "UDS"
    return "KWP2000" if protocol == KWP else "protocol unknown"


def _restart_line(restart: Restart, ecu: str, timeout: float, paint: Painter) -> str:
    if restart.back_after_s is None:
        mark = paint(glyphs.WARN, ansi.raw.YELLOW)
        return f"  {mark} {ecu} did not answer TesterPresent within {timeout:g} s"
    mark = paint(glyphs.OK, ansi.raw.GREEN)
    silent = "" if restart.went_silent else " (it never went silent)"
    return f"  {mark} {ecu} answered again {restart.back_after_s:g} s after the reset{silent}"


def _document(
    ecu: str,
    tx_id: int,
    request: str,
    type_name: str,
    protocol: str,
    response: UdsResponse,
    restart: Restart | None,
) -> dict:
    nrc = response.get("nrc")
    return {
        "ecu": ecu,
        "tx_id": f"0x{tx_id:03X}",
        "protocol": protocol,
        "reset_type": type_name,
        "command": request,
        "accepted": bool(response.get("ok")),
        "response": response.get("hex") if response.get("ok") else None,
        "nrc": f"0x{nrc:02X}" if nrc is not None else None,
        "nrc_desc": response.get("nrc_desc") if nrc is not None else None,
        "error": None if response.get("ok") or nrc is not None else response.get("error"),
        "restart": asdict(restart) if restart is not None else None,
    }


def _report(ecu: str, response: UdsResponse, restart: Restart | None, timeout: float) -> None:
    """The notes, warnings and hints on stderr that go with the result."""
    nrc = response.get("nrc")
    if nrc is not None:
        messages.error(f"{ecu} refused the reset: NRC 0x{nrc:02X} ({response.get('nrc_desc')})")
        if hint := _NRC_HINTS.get(nrc):
            messages.hint(hint)
        return
    if not response.get("ok"):
        messages.warning(f"{ecu} did not answer the reset; it may have restarted before it replied")
    if restart is not None and restart.back_after_s is None:
        messages.warning(
            f"{ecu} did not answer within {timeout:g} s: it may still be starting, or it went "
            "to sleep"
        )
        messages.hint("read it again later, or wait longer with --restart-timeout")
    elif response.get("ok") or (restart is not None and restart.went_silent):
        messages.note(f"{ecu} is in its default session now")
    if response.get("ok") or (restart is not None and restart.went_silent):
        messages.hint(
            f"other ECUs may have stored lost-communication DTCs while {ecu} restarted; "
            "check with `canair dtc --all`"
        )
