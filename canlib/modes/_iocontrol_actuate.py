"""IOControl TUI — the CAN-facing actuator backend.

:class:`IOControlActuator` owns the diagnostic-session + actuation behaviour of
the interactive IOControl TUI: opening the extended session, sending ON/OFF/
ShortTermAdjustment, background status polling (``2F {DID} 00``), and
release/cleanup on exit. Extracting it from :class:`canlib.modes.iocontrol._IOControlTUI`
(mirroring :class:`canlib.modes.monitor_raw.MonitorRawPoller`) keeps the
CAN-facing side-effecting logic in a focused, self-contained unit while the TUI
class keeps view state, keyboard input, rendering, and the ``ecus/`` edits.

All display/session state lives on the TUI; the actuator reads and updates it
through its typed back-reference ``self.t`` (so the renderer, which reads those
same attributes off the TUI, is unchanged).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal, get_args

from ..pids import DEFAULT_IOCONTROL_SESSION_MODE
from ..uds_parse import UdsResponse, nrc_abbrev

if TYPE_CHECKING:
    from .iocontrol import _IOControlTUI

# Per-DID actuator state — the single source of truth for these sentinels.
#
# `release_all` (the exit-time safety net that switches every actuator back off)
# selects its work with `state[did] == ACTUATOR_ON`. If a writer ever drifted to a
# different spelling ("ON", True, an enum), that comparison would quietly match
# nothing and the TUI would exit leaving vehicle outputs energised — a silent,
# physical failure. Typing the dict makes every write and comparison checked.
#
# `None` means idle/unknown (never actuated this session) and is deliberately NOT
# in this union: it is the initial value, not an outcome.
ActuatorState = Literal["on", "off", "error"]
ACTUATOR_ON: ActuatorState = "on"
ACTUATOR_OFF: ActuatorState = "off"
ACTUATOR_ERROR: ActuatorState = "error"
ACTUATOR_STATES: tuple[ActuatorState, ...] = get_args(ActuatorState)

# Same named logger the TUI attaches its per-session file handler to.
_tui_logger = logging.getLogger("iocontrol-tui")


class IOControlActuator:
    """Diagnostic-session + actuation backend for :class:`_IOControlTUI`."""

    def __init__(self, tui: _IOControlTUI) -> None:
        self.t = tui

    async def ensure_session(self, mode: str | None = None) -> None:
        """Open a diagnostic session + TesterPresent unless a suitable one is open.

        ``mode`` is the sub-function the actuator needs; ``None`` accepts whatever
        session is open (opening the default ``03`` if none is). A different mode
        than the open one re-enters, since the ECU only honours one at a time.
        """
        t = self.t
        if t._session_active and (mode is None or mode == t._session_mode):
            return
        wanted = mode or DEFAULT_IOCONTROL_SESSION_MODE
        # Stop the previous keepalive first, so a retry or a mode switch never
        # leaves an orphaned TesterPresent loop running — and forget the old
        # session with it, so a failed re-entry below can't leave the header
        # claiming a session that no longer has a keepalive.
        await self._stop_keepalive()
        t._session_active = False
        t._session_mode = None
        _tui_logger.info("Opening diagnostic session (10 %s) on 0x%03X", wanted, t.tx_id)
        await t.terminal.set_header(t.tx_id)
        ok, t._tester_task = await t.terminal.enter_extended_session(mode=wanted)
        t._session_active = ok
        t._session_mode = wanted
        _tui_logger.info("Session established: %s", ok)

    async def _stop_keepalive(self) -> None:
        task, self.t._tester_task = self.t._tester_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def extract_status_bytes(self, did: str, resp: UdsResponse) -> None:
        """Extract the controlStatusRecord tail from a 0x2F response and store it.

        A positive 0x2F response is `6F {DID_HI} {DID_LO} [tail bytes...]`.
        We store the tail bytes (anything after the 3-byte echo) as an
        uppercase hex string in ``tui.status_bytes[did]``.

        - Positive with tail: ``"AA BB"`` (space-separated bytes)
        - Positive with no tail: ``""``
        - Negative (NRC): ``f"NRC {nrc:02X} {abbrev}"``
        - Transport error: ``"ERR"``
        """
        if resp.get("ok"):
            b = resp.get("bytes") or []
            tail = b[3:] if len(b) >= 3 else []
            self.t.status_bytes[did] = " ".join(f"{x:02X}" for x in tail)
        elif resp.get("nrc") is not None:
            self.t.status_bytes[did] = f"NRC {resp['nrc']:02X} {nrc_abbrev(resp['nrc'])}"
        else:
            self.t.status_bytes[did] = "ERR"

    async def send_on(self, did: str) -> None:
        """Send ON command for a DID."""
        t = self.t
        cmd = t.cmds[did]
        hex_cmd = cmd["on"]
        if not hex_cmd:
            t.state[did] = ACTUATOR_ERROR
            t.last_response[did] = "no ON cmd defined"
            return

        t._busy = True
        t._status = f"Sending ON: {did} ({cmd['label']})..."
        _tui_logger.info("ON  %s %s cmd=%s", did, cmd["label"], hex_cmd)
        try:
            if cmd["session"]:
                await self.ensure_session(cmd["session_mode"])
            resp = await t.terminal.send_uds(hex_cmd, timeout=3.0)
            _tui_logger.info("ON  %s resp: %s", did, resp)
            self.extract_status_bytes(did, resp)
            if resp["ok"]:
                t.state[did] = ACTUATOR_ON
                t.last_response[did] = resp["hex"]
            elif resp.get("nrc") is not None:
                t.state[did] = ACTUATOR_ERROR
                t.last_response[did] = f"NRC 0x{resp['nrc']:02X} {nrc_abbrev(resp['nrc'])}"
            else:
                t.state[did] = ACTUATOR_ERROR
                t.last_response[did] = resp.get("error", "unknown error")
            t._status = ""
        except Exception as e:
            _tui_logger.error("ON  %s exception: %s", did, e, exc_info=True)
            t.state[did] = ACTUATOR_ERROR
            t.last_response[did] = str(e)
            t._status = ""
        finally:
            t._busy = False

    async def send_off(self, did: str) -> None:
        """Send OFF command for a DID."""
        t = self.t
        cmd = t.cmds[did]
        hex_cmd = cmd["off"]
        if not hex_cmd:
            t.last_response[did] = "no OFF cmd defined"
            return

        t._busy = True
        t._status = f"Sending OFF: {did} ({cmd['label']})..."
        _tui_logger.info("OFF %s %s cmd=%s", did, cmd["label"], hex_cmd)
        try:
            # The OFF belongs to the actuator's own session: after a switch to
            # another actuator's mode (or a dropped session), sending it in the
            # wrong one would be refused and leave the output energised.
            if cmd["session"]:
                await self.ensure_session(cmd["session_mode"])
            resp = await t.terminal.send_uds(hex_cmd, timeout=3.0)
            _tui_logger.info("OFF %s resp: %s", did, resp)
            self.extract_status_bytes(did, resp)
            if resp["ok"]:
                t.state[did] = ACTUATOR_OFF
                t.last_response[did] = resp["hex"]
            elif resp.get("nrc") is not None:
                t.state[did] = ACTUATOR_ERROR
                t.last_response[did] = f"NRC 0x{resp['nrc']:02X} {nrc_abbrev(resp['nrc'])}"
            else:
                t.state[did] = ACTUATOR_ERROR
                t.last_response[did] = resp.get("error", "unknown error")
            t._status = ""
        except Exception as e:
            _tui_logger.error("OFF %s exception: %s", did, e, exc_info=True)
            t.state[did] = ACTUATOR_ERROR
            t.last_response[did] = str(e)
            t._status = ""
        finally:
            t._busy = False

    async def toggle(self, did: str) -> None:
        """Toggle: if ON → OFF, otherwise → ON.

        If the DID has no simple ON command (``on: ""`` in the YAML), open
        the hex value prompt instead of erroring. This is the common case
        for HVAC F0xx actuators and other DIDs that require
        ShortTermAdjustment value bytes. The prompt is seeded with the last
        value sent to this DID in the current session (``last_value``) or
        ``00`` if none has been sent yet.
        """
        t = self.t
        if t.state[did] == ACTUATOR_ON:
            await self.send_off(did)
            return
        if not t.cmds[did]["on"]:
            # No simple ON — open the hex value prompt. Seed with the last
            # value sent this session (if any) so +/- stepping still works
            # naturally afterwards.
            refusal = self.adjust_unsupported(did)
            if refusal:
                t._status = refusal
                return
            seed = t.last_value[did].hex().upper() if did in t.last_value else "00"
            t._hex_input = seed
            return
        await self.send_on(did)

    def adjust_unsupported(self, did: str) -> str | None:
        """Why the value keys (``v``/``+``/``-``) can't drive ``did``, or None.

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
        if self.t.kwp:
            return True
        if did is None:
            return False
        cmd = self.t.cmds.get(did)
        if cmd is None:
            return False
        commands = (cmd.get("on"), cmd.get("off"))
        return any(str(c or "").upper().startswith("30") for c in commands)

    async def send_adjust(self, did: str, value_bytes: bytes) -> None:
        """Send ShortTermAdjustment (2F{DID}03{value}) for a DID."""
        t = self.t
        refusal = self.adjust_unsupported(did)
        if refusal:
            t._status = refusal
            _tui_logger.warning("ADJ %s refused: %s", did, refusal)
            return
        did_hex = did.upper()
        hex_cmd = f"2F{did_hex}03{value_bytes.hex().upper()}"

        t._busy = True
        t._status = f"Adjust: {did} → {hex_cmd}"
        _tui_logger.info("ADJ %s cmd=%s", did, hex_cmd)
        try:
            cmd = t.cmds.get(did)
            if cmd is None or cmd["session"]:
                await self.ensure_session(cmd["session_mode"] if cmd else None)
            resp = await t.terminal.send_uds(hex_cmd, timeout=3.0)
            _tui_logger.info("ADJ %s resp: %s", did, resp)
            self.extract_status_bytes(did, resp)
            if resp["ok"]:
                t.state[did] = ACTUATOR_ON
                t.last_response[did] = resp["hex"]
                t.last_value[did] = value_bytes
            elif resp.get("nrc") is not None:
                t.state[did] = ACTUATOR_ERROR
                t.last_response[did] = f"NRC 0x{resp['nrc']:02X} {nrc_abbrev(resp['nrc'])}"
                t.last_value[did] = value_bytes  # still store for +/- stepping
            else:
                t.state[did] = ACTUATOR_ERROR
                t.last_response[did] = resp.get("error", "unknown error")
            t._status = ""
        except Exception as e:
            _tui_logger.error("ADJ %s exception: %s", did, e, exc_info=True)
            t.state[did] = ACTUATOR_ERROR
            t.last_response[did] = str(e)
            t._status = ""
        finally:
            t._busy = False

    async def poll_status_once(self) -> None:
        """Poll every DID once by sending ``2F {DID} 00`` (returnControlToECU).

        ISO 14229-1 §10.4 sub-function 00 is *returnControlToECU* — it hands the
        addressed I/O back to the ECU's own control logic. This is often benign,
        but it is **NOT a guaranteed silent read**: an ECU may re-assert the
        actuator's default drive state when control is returned, which on
        relay/solenoid-backed DIDs (e.g. IGPM door lock/unlock ``BC10``/``BC11``,
        trunk ``BC09``, charge-cable lock ``BC3F``/``BC41``, defogger ``BC0C``)
        produces an audible click. Because of this the whole poll loop is
        opt-in (``--poll``); ECUs that support the DID return a positive response
        including the current ``controlStatusRecord`` tail bytes, which are stored
        in ``tui.status_bytes[did]`` and rendered in the "Status" column.

        Bails early if the TUI is busy (mid-ON/OFF/ADJUST) or quitting; the
        scheduling loop in ``status_poll_loop`` retries on the next tick.

        Each DID is polled inside the session its entry declares (the same
        :meth:`ensure_session` the ON/OFF paths use), since 0x2F typically returns
        NRC 0x7F without one. UDS-only: on a KWP2000 ECU nothing is sent, because
        service 0x30's control-option byte is manufacturer-defined and ``00`` is
        not guaranteed to be a release there (on Nissan it is a one-shot command).
        """
        t = self.t
        if self.is_kwp():
            _tui_logger.warning("poll: refused — KWP2000 ECU, 2F{DID}00 is UDS-only")
            return

        for did in t.dids:
            if t._quit or t._busy:
                return
            cmd = t.cmds[did]
            if self.is_kwp(did):
                continue
            if cmd["session"]:
                await self.ensure_session(cmd["session_mode"])
                if not t._session_active:
                    _tui_logger.warning("poll: could not establish a session for %s", did)
                    return
            # Make sure we're talking to the right ECU header.
            await t.terminal.set_header(t.tx_id)
            did_hex = did.upper()
            req = f"2F{did_hex}00"
            try:
                resp = await t.terminal.send_uds(req, timeout=3.0)
            except Exception as exc:
                t.status_bytes[did] = "ERR"
                _tui_logger.warning("poll %s exception: %s", did, exc)
                continue
            _tui_logger.debug("poll %s req=%s resp=%s", did, req, resp)
            self.extract_status_bytes(did, resp)

    async def status_poll_loop(self, interval: float = 3.0) -> None:
        """Background loop: poll every DID's status bytes every ``interval`` seconds."""
        t = self.t
        t._status_polling = True
        _tui_logger.info("Status poll loop started (interval=%.1fs)", interval)
        try:
            while not t._quit:
                if not t._busy:
                    try:
                        await self.poll_status_once()
                    except Exception as exc:
                        _tui_logger.warning("Status poll error: %s", exc)
                await asyncio.sleep(interval)
        finally:
            t._status_polling = False
            _tui_logger.info("Status poll loop ended")

    async def release_all(self) -> None:
        """Send OFF for all active actuators."""
        t = self.t
        active = [d for d in t.dids if t.state[d] == ACTUATOR_ON]
        for did in active:
            try:
                await self.send_off(did)
            except Exception:
                pass

    async def cleanup(self) -> None:
        """Release actuators and close session."""
        await self.release_all()
        await self._stop_keepalive()
