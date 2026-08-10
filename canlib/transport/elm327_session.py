"""Opening a diagnostic session on an ELM327 adapter, and keeping it alive.

Split out of :mod:`canlib.transport.elm327_terminal` because session lifecycle is
not part of speaking ELM327: it is a UDS-level ritual (DiagnosticSessionControl,
then TesterPresent every 2s) layered *on top* of the byte engine. The terminal
keeps the ``enter_extended_session`` method because
:class:`canlib.transport.protocol.Terminal` requires it; the policy lives here.

Known duplication, deliberately not resolved here:
:meth:`canlib.transport.raw_terminal.RawTerminal.enter_extended_session` is a
second, *divergent* implementation of this ritual — it has no failure retry, and
its TesterPresent is unvalidated and exception-suppressed. Unifying them changes
raw-path behaviour, so it belongs in its own change rather than inside an
extraction.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from ..uds_parse import UdsResponse

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .elm327_terminal import Elm327Terminal

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
    mode = mode.upper().removeprefix("0X").zfill(2)
    req = f"10{mode}"
    if wake:
        await _wake(terminal)

    resp = await terminal.send_uds(req, timeout=5.0)
    if resp.get("ok"):
        print(f"  Session (10 {mode}) established.")
    elif resp.get("nrc") is not None:
        _report_nrc(
            "Session request",
            resp,
            "Continuing anyway -- some ECUs may not need extended session.",
        )
    else:
        error = resp.get("error", "unknown")
        print(f"  Session request failed: {error} — retrying in 0.5s...")
        await asyncio.sleep(0.5)
        resp = await terminal.send_uds(req, timeout=5.0)
        if resp.get("ok"):
            print(f"  Session (10 {mode}) established (on retry).")
        elif resp.get("nrc") is not None:
            _report_nrc("Session retry", resp, "Continuing anyway.")
        else:
            print(f"  WARNING: Session retry also failed: {resp.get('error', 'unknown')}")
            print("  Continuing anyway.")

    task = asyncio.create_task(_tester_present_loop(terminal))
    return resp.get("ok", False), task


async def _wake(terminal: Elm327Terminal) -> None:
    """Nudge a deep-sleeping ECU with a default session request before the real one."""
    await terminal.send_command(_WAKE_TIMEOUT_CMD)
    wake_resp = await terminal.send_uds("1001", timeout=3.0)
    if not wake_resp.get("ok"):
        # First frame may just trigger the transceiver — retry.
        wake_resp = await terminal.send_uds("1001", timeout=3.0)
    if wake_resp.get("ok"):
        print("  Wake-up: ECU responded.")
    await terminal.send_command(terminal.elm_timeout_cmd)  # restore


def _report_nrc(what: str, resp: UdsResponse, note: str) -> None:
    """Warn about a refused session without treating it as fatal.

    Not every ECU needs an extended session, and several answer the request with
    an NRC while happily serving the reads that follow.
    """
    print(f"  WARNING: {what} returned NRC 0x{resp['nrc']:02X} ({resp['nrc_desc']})")
    print(f"  {note}")


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
                resp = await terminal.send_uds("3E00", timeout=1.5, expected_sid=0x3E)
                if terminal.verbose:
                    state = "ok" if resp.get("ok") else resp.get("error", "failed")
                    print(f"  [tester] 3E00 keepalive: {state}", file=sys.stderr)
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
