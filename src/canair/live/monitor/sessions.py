"""Live monitor — diagnostic session-type backend.

:class:`MonitorSessions` is the collaborator that owns the monitor's diagnostic
*session-type* concern: which ``10 xx`` session each ECU currently has open, the
safety-policy check that gates a switch, the picker's "known-good" suggestions
(from the profile's recorded ``sessions:`` scan history), and the live mid-run
switch itself (TUI 't').

Factored out of :class:`canair.live.monitor.controller.MonitorController` (mirrors
:class:`canair.live.monitor.state.MonitorStateSuggester` and
:class:`canair.live.monitor.record.MonitorRecorder`) so the session-type logic
is a self-contained unit rather than another arm of the controller god object.
Callers read it as ``controller.sessions``; the controller holds the transport state
(``sm``/``raw_client``/``_ecu_index``) the sessions read via their ``self.c`` back-ref.

Both backends open and track sessions with a
:class:`~canair.transport.session_manager.SessionManager` (:meth:`MonitorSessions.new_manager`).
The ELM327 one keeps them alive from its background task. The raw backend's polls run on an
executor thread, so its manager works over a
:class:`~canair.transport.raw.client_terminal.RawClientTerminal` and keeps the sessions alive
between polls: the poll loops call :meth:`MonitorSessions.keep_alive`, and every answered poll
counts as a keepalive. A request through it may also run while a poll is in flight (a live
session switch): the raw client's per-ECU lock makes it wait for that ECU's request only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from canair.ecus.names import ecu_index_key
from canair.ecus.pids import build_sessions_index
from canair.safety.blocklist import SafetyBlocked, SessionPolicy
from canair.transport.errors import transport_error_types
from canair.transport.raw.client_terminal import RawClientTerminal
from canair.transport.session_manager import (
    DEFAULT_SESSION_MODE,
    KEEPALIVE_INTERVAL_S,
    SessionManager,
)
from canair.uds.addressing import EcuAddress
from canair.uds.subfunctions import SUBFUNCTION_NAMES, format_session_type, parse_session_type

from ..read.exec import exec_session
from ..read.multi import build_query_plan

if TYPE_CHECKING:
    from canair.ecus.pids import EcuIndexEntry

    from .controller import MonitorController


def _normalized(mode: int | str) -> str:
    """The two-digit form of a session type given as a byte or as hex text."""
    return format_session_type(parse_session_type(mode))


class MonitorSessions:
    """Diagnostic session-type status, safety check, and live switching."""

    def __init__(self, controller: MonitorController):
        self.c = controller
        # ECU_NAME -> {tx_id, sessions: {mode_hex: {name, supported, ...}}},
        # lazily built from the profile's recorded `sessions:` scan history (see
        # ..pids.build_sessions_index). Backs the picker's "known-good"
        # suggestions; rebuilt lazily after a PID reload.
        self._index: dict | None = None

    def new_manager(self) -> SessionManager:
        """A session manager over the controller's current client, for either backend."""
        c = self.c
        if c.raw:
            assert c.raw_client is not None  # c.raw ⟺ raw_client was provided
            terminal = RawClientTerminal(c.raw_client, self._ecu_at)
            return SessionManager(terminal, verbose=c.verbose)
        return SessionManager(c.terminal, verbose=c.verbose)

    def _ecu_at(self, tx_id: int) -> tuple[str, EcuAddress] | None:
        """The raw client's name and the address of the ECU at request id ``tx_id``."""
        for name, info in (self.c._ecu_index or {}).items():
            if info.get("tx_id") == tx_id:
                return name, info["address"]
        return None

    async def open_raw(self, session_steps: list[dict] | None) -> None:
        """Open the sessions the steps ask for on the raw backend, before polling.

        A mode the safety policy refuses raises :class:`SafetyBlocked` before anything is
        sent, as the ELM path does. ``--wake`` uses the ECU's ``wake:`` ritual.
        """
        c = self.c
        assert c._ecu_index is not None  # set by setup()
        for step in session_steps or []:
            if step["type"] != "session":
                continue
            tgt = ecu_index_key(step["target"], c._ecu_index, profile=self.c.profile)
            if tgt is None:
                continue
            raw_mode = step.get("mode", DEFAULT_SESSION_MODE)
            try:
                mode = _normalized(raw_mode)
            except ValueError:
                mode = str(raw_mode)  # check() below refuses it by name
            blocked = self.check(tgt, mode)
            if blocked:
                raise SafetyBlocked(f"session {tgt} --mode {mode}: {blocked}")
            await exec_session(
                c.sm, tgt, step.get("wake", False), c._ecu_index, mode, profile=c.profile
            )

    async def keep_alive(self) -> None:
        """Refresh the raw backend's idle sessions (between polls; ELM327 has its own task)."""
        c = self.c
        if not c.raw or c.disconnected or not c.sm.active_sessions:
            return
        try:
            await c.sm.keepalive_stale(threshold=KEEPALIVE_INTERVAL_S)
        except transport_error_types():
            c.mark_disconnected("error")

    def note_answer(self, ecu: str) -> None:
        """An ECU answered a poll: that reset its session timer as a keepalive would."""
        info = (self.c._ecu_index or {}).get(ecu)
        if info is not None:
            self.c.sm.mark_active(info["tx_id"])

    def warmup_reads(self) -> list[tuple[str, EcuIndexEntry, str]]:
        """``(ECU key, its index entry, first planned request)`` per polled ECU step.

        The first request after idle is slow, so each backend sends this once before the
        first displayed cycle.
        """

        c = self.c
        assert c._ecu_index is not None  # built by setup() before this runs
        reads: list[tuple[str, EcuIndexEntry, str]] = []
        for step in c.query_steps:
            ecu = ecu_index_key(step["ecu"], c._ecu_index, profile=self.c.profile)
            if ecu is None:
                continue
            info = c._ecu_index[ecu]
            plan = build_query_plan(
                info, step.get("pids", []), quiet=True, include_static=c.include_static
            )
            if plan:
                reads.append((ecu, info, plan[0][0]))
        return reads

    def invalidate_index(self) -> None:
        """Drop the cached sessions index (rebuilt lazily on next access)."""
        self._index = None

    def active_modes(self) -> dict[str, str]:
        """ECU_NAME -> currently-open "10 xx" sub-function (2-hex-digit str).

        Omits any ECU with no session it accepted — e.g. one polled without a preceding
        `session` step never sent `10 xx` and has nothing to show.
        """
        c = self.c
        out: dict[str, str] = {}
        for ecu_name, info in (c._ecu_index or {}).items():
            sub = c.sm.session_mode(info["tx_id"])
            if sub is not None:
                out[ecu_name] = format_session_type(sub)
        return out

    def check(self, ecu_name: str, mode: str) -> str | None:
        """The command blocklist's verdict on ``10 <mode>`` for one ECU (None = safe).

        Through the same :class:`~canair.safety.blocklist.SessionPolicy` the terminals use,
        so the picker, a live switch and the raw-backend setup can never disagree
        with what the transport itself would refuse.
        """

        if len(mode) != 2 or any(c not in "0123456789ABCDEFabcdef" for c in mode):
            return f"Invalid session mode {mode!r} (expected one hex byte, e.g. 03)."
        info = (self.c._ecu_index or {}).get(ecu_name.upper())
        tx_id = info["tx_id"] if info else None
        return SessionPolicy.from_profile(self.c.pids_data).check(int(mode, 16), tx_id)

    def known_types(self, ecu_name: str) -> list[tuple[str, str, bool | None]]:
        """(mode_hex, label, supported) options for the session-type picker.

        Prefers the ECU's own recorded ``sessions:`` scan history (written by
        ``canair scan sessions`` — see ..pids.build_sessions_index) so the
        picker shows what's *actually confirmed* on this ECU (``supported``
        True/False); falls back to a fixed candidate list with
        ``supported=None`` (untested) when the ECU has no scan history yet.
        Either way only modes canair.safety.blocklist permits for *this* ECU's protocol
        are offered — never a programming session, and never ``0x82`` on an ECU
        that is not known to speak KWP2000 (on UDS it is programmingSession).
        """

        names = SUBFUNCTION_NAMES.get(0x10, {})
        if self._index is None:
            self._index = build_sessions_index(self.c.pids_data)
        entry = self._index.get(ecu_name.upper())
        if entry and entry.get("sessions"):
            out = []
            for mode, sdef in sorted(entry["sessions"].items()):
                if self.check(ecu_name, mode):
                    continue
                label = sdef.get("name") or names.get(int(mode, 16), "")
                out.append((mode, label, sdef.get("supported")))
            return out
        fallback = ("01", "03", "81", "82", "83")
        return [
            (m, names.get(int(m, 16), ""), None)
            for m in fallback
            if self.check(ecu_name, m) is None
        ]

    async def switch(self, ecu_name: str, mode: str) -> str:
        """Force-switch one ECU to a new diagnostic session type, live.

        Sends ``10 <mode>`` even if a session of a *different* type is already
        tracked open — SessionManager.open_session's default refresh-only path
        would otherwise silently no-op the switch. Returns a human status
        string for the TUI's flash message.
        """

        c = self.c
        ecu = ecu_name.upper()
        info = (c._ecu_index or {}).get(ecu)
        if not info:
            return f"Unknown ECU {ecu_name}."
        try:
            mode = _normalized(mode)
        except ValueError:
            return f"Invalid session mode {mode!r} (expected one hex byte, e.g. 03)."
        blocked = self.check(ecu, mode)
        if blocked:
            return blocked
        held = c.actuation.switch_refusal(ecu) if c.actuation is not None else None
        if held:
            return held
        label = SUBFUNCTION_NAMES.get(0x10, {}).get(int(mode, 16), "")
        suffix = f" ({label})" if label else ""
        try:
            outcome = await c.sm.open_session(info["tx_id"], mode=mode, force=True, report=False)
        except transport_error_types() as exc:
            return f"{ecu}: session switch failed ({exc})"
        if c.actuation is not None:
            c.actuation.forget_mode(info["tx_id"])  # the user chose this ECU's session now
        if outcome.ok:
            return f"{ecu}: switched to session 0x10{mode}{suffix}."
        return f"{ecu}: session 0x10{mode}{suffix} not opened — {outcome.problem()}."
