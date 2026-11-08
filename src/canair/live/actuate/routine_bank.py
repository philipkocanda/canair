"""RoutineControl actuation — one ECU's routines, their last outcome, and the requests.

:class:`RoutineBank` sends ``31 {SF} {RID}`` to one UDS ECU and remembers what each routine was
last asked and answered. ``canair routines``' view
(:class:`canair.live.actuate.routines.RoutinesController`) and the monitor's actuator pane both
drive one. Like :class:`~canair.live.actuate.iocontrol_actuate.IoBank` it opens no session
itself (:class:`~.ports.ActuationSessions`), reaches the terminal through a callable, and sets
the header and sends inside one ``terminal.transaction()``.

A KWP2000 ECU never gets a bank: there ``0x31`` starts a routine whatever its sub-function
(:func:`canair.live.actuate.routines_execute.check_routines_protocol`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from canair.captures.actuations import ActuationAction
from canair.render import glyphs
from canair.transport.protocol import Terminal
from canair.uds.nrc import nrc_abbrev
from canair.uds.requests import routine_control_request
from canair.uds.response import UdsResponse
from canair.uds.subfunctions import RoutineControlType

from .ports import ActuationSessions, Exchanged, Report
from .routines_execute import send_routine, sf_label

_logger = logging.getLogger("routines-tui")

#: The outcome of the last request to a RID: a positive reply, an NRC, or a failure.
RoutineState = Literal["ok", "nrc", "error"]

#: The action each sub-function is recorded as (see :data:`~.ports.Exchanged`).
_ACTIONS: dict[int, ActuationAction] = {
    RoutineControlType.START_ROUTINE: "start",
    RoutineControlType.STOP_ROUTINE: "stop",
    RoutineControlType.REQUEST_ROUTINE_RESULTS: "results",
}


class RoutineBank:
    """One ECU's routines: the last sub-function sent to each, and what came back."""

    def __init__(
        self,
        terminal: Callable[[], Terminal],
        tx_id: int,
        routines: dict[str, dict],
        sessions: ActuationSessions,
        report: Report,
    ) -> None:
        self._terminal = terminal
        self.tx_id = tx_id
        self.sessions = sessions
        self.report = report
        # {RID: {label, nrc, nrc_desc, response, verified, notes}}
        self.routines: dict[str, dict] = {}
        self.state: dict[str, RoutineState | None] = {}
        self.last_response: dict[str, str] = {}
        self.last_sf: dict[str, int] = {}  # which sub-function was last sent
        self.busy = False
        #: Told about each request sent (the monitor records them).
        self.on_exchange: Exchanged | None = None
        self.set_routines(routines)

    @property
    def terminal(self) -> Terminal:
        return self._terminal()

    def set_routines(self, routines: dict[str, dict]) -> None:
        """Use ``routines`` (after a reload), keeping the state of every RID already known."""
        self.routines = routines
        for rid in routines:
            self.state.setdefault(rid, None)

    async def send(self, rid: str, sub_function: int) -> UdsResponse | None:
        """Send ``31 {SF} {RID_HI} {RID_LO}`` and record the outcome; the reply, or None."""
        hex_cmd = routine_control_request(sub_function, int(rid, 16))
        self.busy = True
        self.report(f"Sending {sf_label(sub_function)}: {rid}{glyphs.ELLIPSIS}", "info")
        _logger.info("SF %s %s cmd=%s", sf_label(sub_function), rid, hex_cmd)
        resp: UdsResponse | None = None
        sent_at = datetime.now()
        try:
            await self.sessions.ensure(self.tx_id, None)
            sent_at = datetime.now()
            terminal = self.terminal
            async with terminal.transaction():
                await terminal.set_header(self.tx_id)
                resp = await send_routine(terminal, self.tx_id, hex_cmd)
            _logger.info("SF %s resp: %s", rid, resp)
            self.last_sf[rid] = sub_function
            if resp["ok"]:
                self.state[rid] = "ok"
                self.last_response[rid] = resp["hex"]
            elif resp.get("nrc") is not None:
                self.state[rid] = "nrc"
                nrc = resp["nrc"]
                assert nrc is not None
                protocol = terminal.session_policy.protocol(self.tx_id)
                self.last_response[rid] = f"NRC 0x{nrc:02X} {nrc_abbrev(nrc, protocol)}"
            else:
                self.state[rid] = "error"
                self.last_response[rid] = resp.get("error", "unknown error")
            return resp
        except Exception as e:
            _logger.error("SF %s %s exception: %s", rid, sub_function, e, exc_info=True)
            self.state[rid] = "error"
            self.last_response[rid] = str(e)
            return None
        finally:
            self.report("", "info")
            self.busy = False
            action = _ACTIONS.get(sub_function)
            if self.on_exchange is not None and action is not None:
                self.on_exchange(action, rid, hex_cmd, resp, sent_at)
