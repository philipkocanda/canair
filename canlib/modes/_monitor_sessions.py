"""Live monitor — diagnostic session-type backend.

:class:`MonitorSessions` is the collaborator that owns the monitor's diagnostic
*session-type* concern: which ``10 xx`` session each ECU currently has open, the
safety-policy check that gates a switch, the picker's "known-good" suggestions
(from the profile's recorded ``sessions:`` scan history), and the live mid-run
switch itself (TUI 't').

Factored out of :class:`canlib.modes.monitor.MonitorController` (mirrors
:class:`canlib.modes._monitor_state.MonitorStateSuggester` and
:class:`canlib.modes._monitor_record.MonitorRecorder`) so the session-type logic
is a self-contained unit rather than another arm of the controller god object.
The controller keeps thin delegating methods for the TUI/tested surface and holds
the transport state (``sm``/``raw_client``/``_ecu_index``) the sessions read via
their ``self.c`` back-ref.

The raw backend has no :class:`~canlib.session_manager.SessionManager` (sessions
and keepalives are not tracked there at all), so its opened-session mode is held
here as ``raw_modes``: ECU name (upper) -> last-opened ``10 xx`` sub-function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .monitor import MonitorController


class MonitorSessions:
    """Diagnostic session-type status, safety check, and live switching."""

    def __init__(self, controller: MonitorController):
        self.c = controller
        # Raw-backend opened session modes: ECU name (upper) -> "10 xx"
        # sub-function (2-hex-digit str). The ELM backend tracks this in its
        # SessionManager instead.
        self.raw_modes: dict[str, str] = {}
        # ECU_NAME -> {tx_id, sessions: {mode_hex: {name, supported, ...}}},
        # lazily built from the profile's recorded `sessions:` scan history (see
        # ..pids.build_sessions_index). Backs the picker's "known-good"
        # suggestions; rebuilt lazily after a PID reload.
        self._index: dict | None = None

    def invalidate_index(self) -> None:
        """Drop the cached sessions index (rebuilt lazily on next access)."""
        self._index = None

    def record_opened(self, ecu_name: str, mode: str) -> None:
        """Remember that the raw backend opened ``10 <mode>`` on ``ecu_name``."""
        self.raw_modes[ecu_name.upper()] = mode

    def active_modes(self) -> dict[str, str]:
        """ECU_NAME -> currently-open "10 xx" sub-function (2-hex-digit str).

        Sourced from SessionManager on the ELM backend or ``raw_modes`` on the
        raw backend (which has no SessionManager at all). Omits any ECU with no
        tracked session — e.g. one polled without a preceding `session` step
        never sent `10 xx` and has nothing to show.
        """
        c = self.c
        if not c._ecu_index:
            return {}
        out: dict[str, str] = {}
        for ecu_name, info in c._ecu_index.items():
            if c.raw:
                mode = self.raw_modes.get(ecu_name)
            else:
                mode = c.sm.session_mode(info["tx_id"]) if c.sm else None
            if mode:
                out[ecu_name] = mode
        return out

    def check(self, ecu_name: str, mode: str) -> str | None:
        """The command blocklist's verdict on ``10 <mode>`` for one ECU (None = safe).

        Through the same :class:`~canlib.safety.SessionPolicy` the terminals use,
        so the picker, a live switch and the raw-backend setup can never disagree
        with what the transport itself would refuse.
        """
        from ..safety import SessionPolicy

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
        Either way only modes canlib.safety permits for *this* ECU's protocol
        are offered — never a programming session, and never ``0x82`` on an ECU
        that is not known to speak KWP2000 (on UDS it is programmingSession).
        """
        from ..uds_layout import SUBFUNCTION_NAMES

        names = SUBFUNCTION_NAMES.get(0x10, {})
        if self._index is None:
            from ..pids import build_sessions_index

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
        from ..uds_layout import SUBFUNCTION_NAMES

        c = self.c
        ecu = ecu_name.upper()
        info = (c._ecu_index or {}).get(ecu)
        if not info:
            return f"Unknown ECU {ecu_name}."
        mode = str(mode).upper().removeprefix("0X").zfill(2)
        blocked = self.check(ecu, mode)
        if blocked:
            return blocked
        label = SUBFUNCTION_NAMES.get(0x10, {}).get(int(mode, 16), "")
        suffix = f" ({label})" if label else ""
        tx_id = info["tx_id"]
        if c.raw:
            assert c.raw_client is not None  # c.raw ⟺ raw_client was provided
            try:
                c.raw_client.read(ecu, bytes.fromhex(f"10{mode}"), timeout=2.0)
            except Exception as exc:
                return f"{ecu}: session switch failed ({exc})"
            self.raw_modes[ecu] = mode
            return f"{ecu}: switched to session 0x10{mode}{suffix}."
        assert c.sm is not None  # not c.raw ⟺ sm was constructed
        ok = await c.sm.open_session(tx_id, mode=mode, force=True)
        verb = "switched to" if ok else "attempted"
        return f"{ecu}: {verb} session 0x10{mode}{suffix}."
