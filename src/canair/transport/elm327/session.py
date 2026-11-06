"""Opening a diagnostic session on an ELM327 adapter, and keeping it alive.

Split out of :mod:`canair.transport.elm327.terminal` because session lifecycle is
not part of speaking ELM327: it is a UDS-level ritual (DiagnosticSessionControl,
then TesterPresent every 2s) layered *on top* of the byte engine. The terminal
keeps the ``enter_extended_session`` method because
:class:`canair.transport.protocol.Terminal` requires it; the policy lives here.

Known duplication, deliberately not resolved here:
:meth:`canair.transport.raw.terminal.RawTerminal.enter_extended_session` is a
second, *divergent* implementation of this ritual — it has no failure retry, and
its TesterPresent is unvalidated and exception-suppressed. Unifying them changes
raw-path behaviour, so it belongs in its own change rather than inside an
extraction.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from canair.render import messages
from canair.transport import keepalive
from canair.uds.requests import session_request
from canair.uds.response import UdsResponse
from canair.uds.services import (
    DEFAULT_SESSION_REQUEST,
    DIAGNOSTIC_SESSION_CONTROL,
    TESTER_PRESENT,
    TESTER_PRESENT_REQUEST,
)
from canair.uds.subfunctions import format_session_type, parse_session_type

if TYPE_CHECKING:  # pragma: no cover - typing only
    from canair.transport.elm327.terminal import Elm327Terminal

# TesterPresent cadence. Well inside the UDS S3 timer (nominally 5s), so one lost
# keepalive does not end the session.
_KEEPALIVE_INTERVAL = 2.0

# Fast ELM327 ECU-wait budget used only while waking a deep-sleeping ECU: some
# modules (the Ioniq's SKM) have a ~2s sleep timer and need rapid CAN traffic to
# stay awake, which a 600ms-per-attempt budget cannot deliver.
_WAKE_TIMEOUT_CMD = "ATST10"  # 64ms


async def enter_extended_session(
    terminal: Elm327Terminal, wake: bool = False, mode: str = "03"
) -> tuple[bool, asyncio.Task | None]:
    """Enter a diagnostic session (default ``10 03``) and start TesterPresent.

    Args:
        terminal: the ELM327 engine to drive.
        wake: If True, send a default session request (10 01) first to wake the
            ECU from deep sleep before entering the session.
        mode: DiagnosticSessionControl sub-function (hex, no 0x). Default ``"03"``
            (UDS extendedDiagnosticSession); use ``"81"`` for the KWP2000
            standardDiagnosticSession on ECUs that reject 10 03.

    Returns:
        ``(success, tester_task)`` — success indicates if the session was
        established; the caller owns cancelling the keepalive task.
    """
    mode = format_session_type(parse_session_type(mode))
    req = session_request(mode)
    if wake:
        await _wake(terminal)

    # A session reply is checked by its service only: a stale reply to an earlier read
    # must not count as an open session, but the sub-function echo is left alone, since
    # some KWP2000 ECUs are not known to repeat it.
    resp = await terminal.send_uds(req, timeout=5.0, expected_sid=DIAGNOSTIC_SESSION_CONTROL)
    if resp.get("ok"):
        print(f"  Session (10 {mode}) established.", file=sys.stderr)
    elif resp.get("nrc") is not None:
        _report_nrc(
            "Session request",
            resp,
            "continuing anyway — some ECUs may not need an extended session",
        )
    else:
        error = resp.get("error", "unknown")
        print(f"  Session request failed: {error} — retrying in 0.5s…", file=sys.stderr)
        await asyncio.sleep(0.5)
        resp = await terminal.send_uds(req, timeout=5.0, expected_sid=DIAGNOSTIC_SESSION_CONTROL)
        if resp.get("ok"):
            print(f"  Session (10 {mode}) established (on retry).", file=sys.stderr)
        elif resp.get("nrc") is not None:
            _report_nrc("Session retry", resp, "continuing anyway")
        else:
            messages.warning(
                f"session retry also failed: {resp.get('error', 'unknown')} — continuing anyway"
            )

    task = asyncio.create_task(_tester_present_loop(terminal))
    return resp.get("ok", False), task


async def _wake(terminal: Elm327Terminal) -> None:
    """Nudge a deep-sleeping ECU with a default session request before the real one."""
    await terminal.send_command(_WAKE_TIMEOUT_CMD)
    wake_resp = await terminal.send_uds(
        DEFAULT_SESSION_REQUEST, timeout=3.0, expected_sid=DIAGNOSTIC_SESSION_CONTROL
    )
    if not wake_resp.get("ok"):
        # First frame may just trigger the transceiver — retry.
        wake_resp = await terminal.send_uds(
            DEFAULT_SESSION_REQUEST, timeout=3.0, expected_sid=DIAGNOSTIC_SESSION_CONTROL
        )
    if wake_resp.get("ok"):
        print("  Wake-up: ECU responded.", file=sys.stderr)
    await terminal.send_command(terminal.elm_timeout_cmd)  # restore


def _report_nrc(what: str, resp: UdsResponse, note: str) -> None:
    """Warn about a refused session without treating it as fatal.

    Not every ECU needs an extended session, and several answer the request with
    an NRC while happily serving the reads that follow.
    """
    messages.warning(f"{what} returned NRC 0x{resp['nrc']:02X} ({resp['nrc_desc']}) — {note}")


async def _tester_present_loop(terminal: Elm327Terminal) -> None:
    """Send ``3E 00`` every 2s to keep the diagnostic session alive."""
    try:
        while True:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            try:
                # Validated, not fire-and-forget: an unchecked keepalive is how a
                # transient stall becomes a permanent pipe offset — it consumes the
                # late reply owed to the previous request, sees a prompt, and leaves
                # its own reply buffered for the next reader. expected_sid makes
                # that mismatch visible so send_uds can resync instead of hiding it.
                resp = await terminal.send_uds(
                    TESTER_PRESENT_REQUEST, timeout=1.5, expected_sid=TESTER_PRESENT
                )
                if terminal.verbose:
                    state = "ok" if resp.get("ok") else resp.get("error", "failed")
                    print(f"  [tester] 3E00 keepalive: {state}", file=sys.stderr)
                if keepalive.unsupported(resp):
                    # No TesterPresent in any session: the loop has nothing left to do.
                    header = terminal._cur_header
                    if header is not None:
                        keepalive.report_unsupported(header, transport=terminal.diag.transport)
                    return
            except ConnectionError:
                # The pipe could not be realigned; the session is done for. Let the
                # task end so the caller's reconnect path takes over rather than
                # looping on a broken link.
                raise
            except Exception:
                pass
    except asyncio.CancelledError:
        pass
    except ConnectionError:
        pass
