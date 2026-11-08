"""What an actuation bank needs from its owner: a diagnostic session, and a place to report.

An actuation bank (:class:`~canair.live.actuate.iocontrol_actuate.IoBank`,
:class:`~canair.live.actuate.routines_execute.RoutineBank`) sends one ECU's IOControl or
RoutineControl requests. It opens no session itself: it asks its owner through
:class:`ActuationSessions`, so whoever owns the sessions (``canair io``, ``canair routines``, the
``read`` pipeline, the monitor) keeps owning them and keeps them alive its own way.

* :class:`OwnSessions` — the adapter for a command that runs one ECU's view: a
  :class:`~canair.transport.session_manager.SessionManager` of its own, kept alive by its
  background task, re-entered when an actuator needs another session mode.
* :class:`SharedSessions` — the adapter over a manager somebody else keeps alive (the ``read``
  pipeline, the monitor): it opens or switches a session and never touches the keepalive.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal, Protocol

from canair.ecus.pids import DEFAULT_IOCONTROL_SESSION_MODE
from canair.transport.session_manager import SessionManager
from canair.uds.subfunctions import parse_session_type

_logger = logging.getLogger("iocontrol-tui")

#: How a status message reads: the view colours it and gives it a glyph.
StatusLevel = Literal["error", "warning", "success", "info"]

#: Where a bank reports what it is doing and what went wrong.
Report = Callable[[str, StatusLevel], None]


class ActuationSessions(Protocol):
    """Opens the diagnostic session an actuation needs."""

    async def ensure(self, tx_id: int, mode: str | None) -> bool:
        """Open session ``mode`` on ``tx_id`` unless it is open; True when one is.

        ``None`` accepts whatever session is open, and opens the default one if none is.
        """
        ...


class OwnSessions:
    """One ECU's session, owned and kept alive by the command that runs its view.

    ``active`` and ``mode`` are what the view's header shows: a mode the ECU refused is kept
    with ``active`` False ("unconfirmed").
    """

    def __init__(self, sm: SessionManager, *, active: bool = False, mode: str | None = None):
        self.sm = sm
        self.active = active
        self.mode = mode

    async def ensure(self, tx_id: int, mode: str | None) -> bool:
        if self.active and (mode is None or mode == self.mode):
            return True
        wanted = mode or DEFAULT_IOCONTROL_SESSION_MODE
        # Stop the previous keepalive first, so a retry or a mode switch never leaves an
        # orphaned TesterPresent loop running, and forget the old session with it, so a
        # failed re-entry can't leave the header claiming a session with no keepalive.
        await self.sm.stop_background_keepalive()
        self.active = False
        self.mode = None
        _logger.info("Opening diagnostic session (10 %s) on 0x%03X", wanted, tx_id)
        outcome = await self.sm.open_session(tx_id, mode=wanted, force=True, report=False)
        self.sm.start_background_keepalive()
        self.active = outcome.ok
        self.mode = wanted
        _logger.info("Session established: %s (%s)", outcome.ok, outcome.problem() or "ok")
        return self.active

    async def close(self) -> None:
        """Stop the keepalive. The session itself times out on the ECU."""
        await self.sm.stop_background_keepalive()


class SharedSessions:
    """Sessions on a manager its owner keeps alive: open or switch, never the keepalive."""

    def __init__(self, sm: Callable[[], SessionManager]):
        # A callable: the monitor replaces its manager when it reconnects.
        self._sm = sm

    async def ensure(self, tx_id: int, mode: str | None) -> bool:
        sm = self._sm()
        open_mode = sm.session_mode(tx_id)
        wanted = parse_session_type(mode or DEFAULT_IOCONTROL_SESSION_MODE)
        if open_mode is not None and (mode is None or open_mode == wanted):
            return True
        outcome = await sm.open_session(tx_id, mode=wanted, force=True, report=False)
        return outcome.ok
