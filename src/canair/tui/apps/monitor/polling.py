"""The monitor app's poll loop: tick the controller, repaint, re-home a dropped session.

:class:`PollingMixin` is mixed into :class:`~canair.tui.apps.monitor.app.MonitorApp`, which
owns the widgets it repaints and the ``paused`` / ``_stopping`` flags it reads.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from textual.app import App

from canair.core.eventlog import log_event, log_exception
from canair.live.monitor.controller import MonitorController
from canair.transport.errors import transport_error_types


class PollingMixin:
    """The poll worker of the monitor app."""

    controller: MonitorController
    paused: bool
    _stopping: bool

    @property
    def _app(self) -> App:
        assert isinstance(self, App)
        return self

    if TYPE_CHECKING:

        def _refresh_body(self) -> None: ...
        def _update_status(self) -> None: ...

    def _note_pause(self, on: bool) -> None:
        """Record that polling actually stopped (``on``) or resumed.

        Called by the poll loop rather than wherever ``paused`` is set (the space key,
        a modal dialog), because a cycle already in flight finishes first: the gap in
        the recording starts when polling stops, not when it was asked to.
        """
        tracker = getattr(self.controller, "link_events", None)
        if tracker is not None:
            tracker.paused(on)

    async def _poll_loop(self) -> None:
        stopped = False
        while True:
            if self.paused != stopped:
                stopped = self.paused
                self._note_pause(stopped)
            # Chunked wait, re-asking the schedule each time, so pause/quit stay
            # responsive and a live rate change or a just-added PID takes effect at
            # once rather than after a sleep chosen before it.
            if self.paused or self.controller.seconds_until_due() > 0:
                await asyncio.sleep(0.05)
                # A pause stops polling, not the sessions it opened.
                await self.controller.sessions.keep_alive()
                continue
            try:
                await self.controller.poll_once()
            except transport_error_types() as e:
                # The link died mid-cycle. Same handling as a poll that set
                # `disconnected` itself: re-home rather than abandon the run.
                log_event(
                    "bus",
                    f"poll cycle failed: {type(e).__name__}: {e}",
                    transport=self.controller.transport_type,
                )
                self.controller.mark_disconnected("error")
            except Exception as e:
                # Not the transport — a genuine internal fault. Reconnecting
                # would not fix it and would hide it, so stop cleanly rather
                # than leaving a silently-frozen UI.
                log_exception("unexpected monitor poll failure", e)
                self.controller.disconnected = True
                self._app.exit()
                return
            if self.controller.disconnected:
                # Try to re-home the dropped session (auto-failover / --wait)
                # in place, keeping the --save journal recording across the gap.
                if not await self._attempt_reconnect():
                    self._app.exit()
                    return
                self._refresh_body()
                continue
            self._refresh_body()
            # Yield even when the next tick is already due (a cycle that overran
            # its period), so back-to-back polls never starve the UI.
            await asyncio.sleep(0.05)

    async def _attempt_reconnect(self) -> bool:
        """Re-home a dropped session; True to resume, False to exit the app.

        On a give-up the controller stays ``disconnected`` so ``mode_monitor``
        reports the drop; a user quit during the attempt clears it for a clean stop.
        """
        c = self.controller
        reconnect = getattr(c, "reconnect", None)
        if reconnect is None:
            return False

        def _notice(msg: str) -> None:  # thread-safe: plain attribute write
            c.reconnect_note = msg

        c.reconnecting = True
        c.reconnect_note = "connection dropped — reconnecting…"
        self._update_status()
        try:
            ok = await reconnect(
                c, getattr(c, "session_steps", None), stop=lambda: self._stopping, notice=_notice
            )
        finally:
            c.reconnecting = False
        if not ok and self._stopping:
            c.disconnected = False  # deliberate stop, not a failure
        return ok
