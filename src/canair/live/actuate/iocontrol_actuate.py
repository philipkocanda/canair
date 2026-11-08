"""IOControl actuation — one ECU's actuators, their state, and the requests that drive them.

:class:`IoBank` sends ON, OFF, ShortTermAdjustment and the release of each IOControl entry of
one ECU, and remembers what each DID was last told and answered. ``canair io``'s view
(:class:`canair.live.actuate.iocontrol.IOControlController`), the ``read`` pipeline's
``iocontrol`` step and the monitor's actuator pane all drive one, so the refusals and the
exit-time release are written once.

A bank opens no session itself: it asks its owner's :class:`~.ports.ActuationSessions` for the
session mode each entry declares. It reaches the terminal through a callable, because the
monitor replaces its terminal when it reconnects, and every request sets the header and sends
inside one ``terminal.transaction()``, so a keepalive or a poll can never re-point it between
the two.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal, get_args

from canair.captures.actuations import ActuationAction
from canair.ecus.pids import IoControlCommand
from canair.render import glyphs
from canair.safety.actuation import on_refusal, parse_io_request
from canair.transport.protocol import Terminal
from canair.uds.nrc import nrc_abbrev
from canair.uds.protocol import KWP, DiagProtocol
from canair.uds.requests import io_control_request
from canair.uds.response import UdsResponse
from canair.uds.services import (
    KWP_INPUT_OUTPUT_CONTROL_BY_LOCAL_IDENTIFIER,
    identifier_width,
    request_sid_of,
)
from canair.uds.subfunctions import IoControlParameter

from .ports import ActuationSessions, Exchanged, Report

# Per-DID actuator state — the single source of truth for these sentinels.
#
# `release_all` (the exit-time safety net that switches every actuator back off)
# selects its work with `state[did] == ACTUATOR_ON`. If a writer ever drifted to a
# different spelling ("ON", True, an enum), that comparison would quietly match
# nothing and the view would exit leaving vehicle outputs energised — a silent,
# physical failure. Typing the dict makes every write and comparison checked.
#
# `None` means idle/unknown (never actuated this session) and is deliberately NOT
# in this union: it is the initial value, not an outcome.
ActuatorState = Literal["on", "off", "error"]
ACTUATOR_ON: ActuatorState = "on"
ACTUATOR_OFF: ActuatorState = "off"
ACTUATOR_ERROR: ActuatorState = "error"
ACTUATOR_STATES: tuple[ActuatorState, ...] = get_args(ActuatorState)

# Same named logger the view attaches its per-session file handler to.
_logger = logging.getLogger("iocontrol-tui")


def _response_id_width(response_sid: int) -> int:
    """How many identifier bytes a positive response echoes after its SID."""
    return identifier_width(request_sid_of(response_sid))


async def send_iocontrol(terminal: Terminal, hex_cmd: str) -> UdsResponse:
    """Send an IOControl request, validating the reply's service and identifier echo.

    A reply left over from an earlier request would otherwise be taken for this
    one, and could mark an output released while it is still driven.
    """
    io = parse_io_request(hex_cmd)
    if io is None:
        return await terminal.send_uds(hex_cmd, timeout=3.0)
    return await terminal.send_uds(
        hex_cmd, timeout=3.0, expected_sid=io.service, expected_echo=io.identifier
    )


#: The action each kind of request is recorded as (see :data:`~.ports.Exchanged`).
_ACTIONS: dict[str, ActuationAction] = {
    "ON": "on",
    "OFF": "off",
    "REL": "release",
    "ADJ": "adjust",
}


def _nrc_text(resp: UdsResponse, protocol: DiagProtocol) -> str:
    nrc = resp["nrc"]
    assert nrc is not None
    return f"NRC 0x{nrc:02X} {nrc_abbrev(nrc, protocol)}"


class IoBank:
    """One ECU's IOControl actuators: what each was told, what it answered, and the requests.

    ``state`` is per DID: ``None`` until the session actuates it, then an
    :data:`ActuatorState`. ``busy`` is set while a request is on the bus.
    """

    def __init__(
        self,
        terminal: Callable[[], Terminal],
        tx_id: int,
        protocol: DiagProtocol,
        cmds: dict[str, IoControlCommand],
        sessions: ActuationSessions,
        report: Report,
    ) -> None:
        self._terminal = terminal
        self.tx_id = tx_id
        # A KWP2000 ECU's actuators are service 0x30 (manufacturer-defined control option): an
        # ON needs a declared release, and UDS-shaped value keys are refused (safety.actuation).
        self.protocol: DiagProtocol = protocol
        self.sessions = sessions
        self.report = report
        self.all_cmds: dict[str, IoControlCommand] = {}
        # Typed so a drifted sentinel can't silently disable release_all's
        # switch-everything-back-off safety net (see ActuatorState).
        self.state: dict[str, ActuatorState | None] = {}
        self.last_response: dict[str, str] = {}  # DID → last response text
        self.last_value: dict[str, bytes] = {}  # DID → last ShortTermAdjustment value bytes
        # The controlStatusRecord tail of the last reply, as hex; None = no reply yet.
        self.status_bytes: dict[str, str | None] = {}
        self.busy = False  # one request at a time
        self.stopping = False  # the owner is closing: a status poll stops
        #: Told about each ON/OFF/adjust/release sent (the monitor records them).
        self.on_exchange: Exchanged | None = None
        self.set_commands(cmds)

    @property
    def terminal(self) -> Terminal:
        return self._terminal()

    def set_commands(self, cmds: dict[str, IoControlCommand]) -> None:
        """Use ``cmds`` (after a reload), keeping the state of every DID already known."""
        self.all_cmds = cmds
        for did in cmds:
            self.state.setdefault(did, None)
            self.status_bytes.setdefault(did, None)

    def active_dids(self) -> list[str]:
        """The DIDs switched on and not released."""
        return [d for d, s in self.state.items() if s == ACTUATOR_ON]

    async def _ensure_session(self, did: str | None) -> bool:
        cmd = self.all_cmds.get(did) if did else None
        if cmd is not None and not cmd["session"]:
            return True
        return await self.sessions.ensure(self.tx_id, cmd["session_mode"] if cmd else None)

    def extract_status_bytes(self, did: str, resp: UdsResponse) -> None:
        """Extract the controlStatusRecord tail from an IOControl response and store it.

        A positive response echoes the service and identifier: ``6F {DID_HI}
        {DID_LO} [tail…]`` for UDS ``0x2F``, ``70 {LID} [tail…]`` for KWP2000
        ``0x30``. We store the tail bytes (anything after that echo) as an
        uppercase hex string in ``status_bytes[did]``.

        - Positive with tail: ``"AA BB"`` (space-separated bytes)
        - Positive with no tail: ``""``
        - Negative (NRC): ``f"NRC {nrc:02X} {abbrev}"``
        - Transport error: ``"ERR"``
        """
        if resp.get("ok"):
            b = resp.get("bytes") or []
            echo = 1 + _response_id_width(b[0]) if b else 0
            tail = b[echo:] if len(b) >= echo else []
            self.status_bytes[did] = " ".join(f"{x:02X}" for x in tail)
        elif resp.get("nrc") is not None:
            self.status_bytes[did] = (
                f"NRC {resp['nrc']:02X} {nrc_abbrev(resp['nrc'], self.protocol)}"
            )
        else:
            self.status_bytes[did] = "ERR"

    def on_refusal(self, did: str) -> str | None:
        """Why the ON request of ``did`` must not be sent, or None.

        An actuating ON goes out only when its release is known, so that exit,
        Ctrl-C and a dropped session can hand the output back (``canair.safety.actuation``).
        """
        cmd = self.all_cmds[did]
        reason = on_refusal(cmd["on"], cmd.get("release", ""), self.protocol)
        return f"{did}: ON refused — {reason}" if reason else None

    async def _exchange(
        self, did: str, hex_cmd: str, kind: str, on_ok: ActuatorState
    ) -> UdsResponse | None:
        """Send ``hex_cmd`` for ``did`` in its session and record the outcome; the reply."""
        cmd = self.all_cmds.get(did)
        label = cmd["label"] if cmd else ""
        self.busy = True
        self.report(f"Sending {kind}: {did} ({label}){glyphs.ELLIPSIS}", "info")
        _logger.info("%-3s %s %s cmd=%s", kind, did, label, hex_cmd)
        resp: UdsResponse | None = None
        sent_at = datetime.now()
        try:
            await self._ensure_session(did)
            sent_at = datetime.now()
            resp = await self._send(hex_cmd)
            _logger.info("%-3s %s resp: %s", kind, did, resp)
            self.extract_status_bytes(did, resp)
            if resp["ok"]:
                self.state[did] = on_ok
                self.last_response[did] = resp["hex"]
            elif resp.get("nrc") is not None:
                self.state[did] = ACTUATOR_ERROR
                self.last_response[did] = _nrc_text(resp, self.protocol)
            else:
                self.state[did] = ACTUATOR_ERROR
                self.last_response[did] = resp.get("error", "unknown error")
            return resp
        except Exception as e:
            _logger.error("%-3s %s exception: %s", kind, did, e, exc_info=True)
            self.state[did] = ACTUATOR_ERROR
            self.last_response[did] = str(e)
            return None
        finally:
            self.report("", "info")
            self.busy = False
            if self.on_exchange is not None:
                self.on_exchange(_ACTIONS[kind], did, hex_cmd, resp, sent_at)

    async def send_on(self, did: str) -> None:
        """Send ON command for a DID."""
        hex_cmd = self.all_cmds[did]["on"]
        if not hex_cmd:
            self.state[did] = ACTUATOR_ERROR
            self.last_response[did] = "no ON cmd defined"
            return
        refusal = self.on_refusal(did)
        if refusal:
            self.report(refusal, "error")
            self.last_response[did] = "no release known"
            _logger.warning("ON  %s refused: %s", did, refusal)
            return
        await self._exchange(did, hex_cmd, "ON", ACTUATOR_ON)

    async def send_off(self, did: str) -> None:
        """Send OFF command for a DID."""
        await self._send_back(did, self.all_cmds[did]["off"], "OFF")

    async def send_release(self, did: str) -> None:
        """Hand ``did`` back to the ECU: its release, else its OFF command.

        This is what exit, Ctrl-C and the watchdog send. On UDS the release is
        returnControlToECU, which every curated OFF also is; on KWP2000 it is the
        request the entry declares, since ``0x30`` has no standard one.
        """
        cmd = self.all_cmds[did]
        await self._send_back(did, cmd.get("release") or cmd["off"], "REL")

    async def _send_back(self, did: str, hex_cmd: str, kind: str) -> None:
        if not hex_cmd:
            self.last_response[did] = f"no {'OFF' if kind == 'OFF' else 'release'} cmd defined"
            return
        # The OFF belongs to the actuator's own session: after a switch to another
        # actuator's mode (or a dropped session), sending it in the wrong one would be
        # refused and leave the output energised. _exchange re-enters it.
        await self._exchange(did, hex_cmd, kind, ACTUATOR_OFF)

    async def _send(self, hex_cmd: str) -> UdsResponse:
        terminal = self.terminal
        async with terminal.transaction():
            await terminal.set_header(self.tx_id)
            return await send_iocontrol(terminal, hex_cmd)

    async def toggle(self, did: str) -> str | None:
        """Toggle: if ON → OFF, otherwise → ON.

        A DID with no simple ON command (``on: ""`` in the YAML) needs
        ShortTermAdjustment value bytes instead, which is the common case for
        HVAC F0xx actuators. Then nothing is sent and the value the view should
        prompt for is returned: the last value sent to this DID in the session
        (``last_value``), else ``00``.
        """
        if self.state.get(did) == ACTUATOR_ON:
            await self.send_off(did)
            return None
        if not self.all_cmds[did]["on"]:
            refusal = self.adjust_unsupported(did)
            if refusal:
                self.report(refusal, "error")
                return None
            return self.last_value[did].hex().upper() if did in self.last_value else "00"
        await self.send_on(did)
        return None

    def adjust_unsupported(self, did: str) -> str | None:
        """Why the value keys (``w``/``+``/``-``) can't drive ``did``, or None.

        They build UDS ShortTermAdjustment (``2F {DID} 03 {value}``); a KWP2000
        actuator (service ``0x30``) has no such request, and its control-option
        byte is manufacturer-defined, so guessing one could actuate something.
        """
        if self.is_kwp(did):
            return (
                f"{did}: value adjust (2F…03) is UDS-only — this is a KWP2000 0x30 "
                "actuator; use its own on/off commands"
            )
        return None

    def is_kwp(self, did: str | None = None) -> bool:
        """True for a KWP2000 ECU, or a ``did`` whose commands use service 0x30."""
        if self.protocol == KWP:
            return True
        if did is None:
            return False
        cmd = self.all_cmds.get(did)
        if cmd is None:
            return False
        for request in (cmd.get("on"), cmd.get("off"), cmd.get("release")):
            try:
                io = parse_io_request(request or "")
            except ValueError:
                continue
            if io is not None and io.service == KWP_INPUT_OUTPUT_CONTROL_BY_LOCAL_IDENTIFIER:
                return True
        return False

    def adjust_request(self, did: str, value_bytes: bytes) -> str:
        """The ShortTermAdjustment request for ``did``, with the entry's enable mask."""
        cmd = self.all_cmds.get(did)
        mask = bytes.fromhex(cmd.get("enable_mask") or "") if cmd else b""
        return io_control_request(
            int(did, 16), IoControlParameter.SHORT_TERM_ADJUSTMENT, value_bytes, mask
        )

    async def send_adjust(self, did: str, value_bytes: bytes) -> None:
        """Send ShortTermAdjustment (2F{DID}03{value}) for a DID."""
        refusal = self.adjust_unsupported(did)
        if refusal:
            self.report(refusal, "error")
            _logger.warning("ADJ %s refused: %s", did, refusal)
            return
        resp = await self._exchange(did, self.adjust_request(did, value_bytes), "ADJ", ACTUATOR_ON)
        if resp is not None and (resp["ok"] or resp.get("nrc") is not None):
            self.last_value[did] = value_bytes  # an NRC still keeps it, for +/- stepping

    async def poll_status_once(self, dids: Sequence[str]) -> None:
        """Poll each of ``dids`` once by sending its release (UDS: ``2F {DID} 00``).

        ISO 14229-1 §10.4 sub-function 00 is *returnControlToECU* — it hands the
        addressed I/O back to the ECU's own control logic. This is often benign,
        but it is **NOT a guaranteed silent read**: an ECU may re-assert the
        actuator's default drive state when control is returned, which on
        relay/solenoid-backed DIDs (e.g. IGPM door lock/unlock ``BC10``/``BC11``,
        trunk ``BC09``, charge-cable lock ``BC3F``/``BC41``, defogger ``BC0C``)
        produces an audible click. Because of this the whole poll loop is
        opt-in (``--poll``); ECUs that support the DID return a positive response
        including the current ``controlStatusRecord`` tail bytes, which are stored
        in ``status_bytes[did]`` and rendered in the "Status" column.

        Bails early if the bank is busy (mid-ON/OFF/ADJUST) or stopping; the
        scheduling loop retries on the next tick.

        Each DID is polled inside the session its entry declares, since 0x2F typically
        returns NRC 0x7F without one. A DID with no known release is skipped: a KWP2000
        ``0x30`` entry has one only when the profile declares it, because that
        service's control-option byte is manufacturer-defined and ``00`` is not a
        release everywhere (it starts a one-shot command on Nissan).
        """
        for did in dids:
            if self.stopping or self.busy:
                return
            cmd = self.all_cmds[did]
            req = cmd.get("release", "")
            if not req:
                continue
            if not await self._ensure_session(did):
                _logger.warning("poll: could not establish a session for %s", did)
                return
            try:
                resp = await self._send(req)
            except Exception as exc:
                self.status_bytes[did] = "ERR"
                _logger.warning("poll %s exception: %s", did, exc)
                continue
            _logger.debug("poll %s req=%s resp=%s", did, req, resp)
            self.extract_status_bytes(did, resp)

    async def release_all(self) -> None:
        """Release every active actuator (:meth:`send_release`), whatever the view shows.

        An actuator stays energised when a view moves away from it, so this goes by
        ``state``, never by the DIDs on screen.
        """
        for did in self.active_dids():
            try:
                await self.send_release(did)
            except Exception:
                pass


async def status_poll_loop(
    bank: IoBank, dids: Callable[[], Sequence[str]], interval: float = 3.0
) -> None:
    """Poll ``dids()``'s status record every ``interval`` seconds until the bank stops."""
    _logger.info("Status poll loop started (interval=%.1fs)", interval)
    try:
        while not bank.stopping:
            if not bank.busy:
                try:
                    await bank.poll_status_once(dids())
                except Exception as exc:
                    _logger.warning("Status poll error: %s", exc)
            await asyncio.sleep(interval)
    finally:
        _logger.info("Status poll loop ended")
