"""Multi-ECU diagnostic session manager.

Tracks active extended diagnostic sessions across multiple ECUs and handles
interleaved TesterPresent (3E00) keepalives. Since ELM327 is a serial protocol,
keepalives must be sent sequentially by switching ATSH headers between the
foreground ECU and each background ECU.

Usage:
    sm = SessionManager(terminal, verbose=True)
    await sm.open_session(0x770, wake=False)  # IGPM
    await sm.open_session(0x7A5, wake=True)   # SKM (from deep sleep)

    # Before each foreground command, refresh stale sessions:
    await sm.keepalive_stale(threshold=1.5)

    # Restore foreground header after keepalive sweep:
    await terminal.set_header(foreground_tx_id)

    # Background loop (for REPL idle or --hold):
    task = sm.start_background_keepalive(interval=2.0)
    ...
    task.cancel()

    # Cleanup:
    await sm.close_all()
"""

import asyncio
import time

from .terminal import WiCANTerminal


class SessionManager:
    """Manages extended diagnostic sessions for multiple ECUs simultaneously."""

    def __init__(self, terminal: WiCANTerminal, verbose: bool = False):
        self.terminal = terminal
        self.verbose = verbose
        # {tx_id: last_keepalive_timestamp}
        self._sessions: dict[int, float] = {}
        self._bg_task: asyncio.Task | None = None

    @property
    def active_sessions(self) -> list[int]:
        """List of TX IDs with active sessions."""
        return list(self._sessions.keys())

    def has_session(self, tx_id: int) -> bool:
        return tx_id in self._sessions

    async def open_session(self, tx_id: int, wake: bool = False, mode: str = "03") -> bool:
        """Enter a diagnostic session on an ECU.

        Args:
            tx_id: ECU CAN arbitration ID (e.g., 0x770 for IGPM).
            wake: Send 1001 first to wake from deep sleep.
            mode: DiagnosticSessionControl sub-function (hex, no 0x). Default
                ``"03"`` (UDS extendedDiagnosticSession). Use ``"81"`` for the
                KWP2000 standardDiagnosticSession on powertrain ECUs that reject
                ``10 03`` (e.g. the BMS). Programming/unknown modes are refused by
                the command-safety guard unless ``--unsafe``.

        Returns:
            True if session was established (or at least attempted).
        """
        mode = mode.upper().removeprefix("0X").zfill(2)
        req = f"10{mode}"
        await self.terminal.set_header(tx_id)

        if not wake and tx_id in self._sessions:
            # Already in an extended session on this ECU — refresh rather than
            # re-sending 10xx (a repeated `session <ECU>` step / REPL command).
            self._sessions[tx_id] = time.monotonic()
            if self.verbose:
                print(f"  [session] 0x{tx_id:03X}: already active — refreshed.")
            return True

        if wake:
            if self.verbose:
                print(f"  [session] Sending wake-up (1001) to 0x{tx_id:03X}...")
            await self.terminal.send_uds("1001", timeout=15.0)
            await asyncio.sleep(0.5)

        if self.verbose:
            print(f"  [session] Entering session (10{mode}) on 0x{tx_id:03X}...")
        resp = await self.terminal.send_uds(req, timeout=5.0)

        if resp.get("ok"):
            if self.verbose:
                print(f"  [session] 0x{tx_id:03X}: session established.")
        elif resp.get("nrc") is not None:
            nrc = resp["nrc"]
            desc = resp["nrc_desc"]
            print(f"  [session] 0x{tx_id:03X}: NRC 0x{nrc:02X} ({desc}) -- continuing anyway")
        else:
            error = resp.get("error", "unknown")
            print(f"  [session] 0x{tx_id:03X}: failed ({error}) -- continuing anyway")

        self._sessions[tx_id] = time.monotonic()
        return resp.get("ok", False)

    async def send_keepalive(self, tx_id: int):
        """Send TesterPresent (3E00) to a specific ECU."""
        await self.terminal.set_header(tx_id)
        try:
            await self.terminal.send_command("3E00", timeout=1.5)
            self._sessions[tx_id] = time.monotonic()
            if self.verbose:
                print(f"  [session] 3E00 -> 0x{tx_id:03X}", end="")
        except Exception:
            pass

    def mark_active(self, tx_id: int) -> None:
        """Note real traffic to an ECU as keepalive-equivalent.

        Any UDS request the ECU answers already resets its S3 (session timeout)
        timer, so an ECU we're actively and successfully polling is not "stale"
        and needs no extra 3E00. Callers invoke this after a successful read so
        :meth:`keepalive_stale` won't inject a redundant TesterPresent (and, if
        the header differs, an ATSH/ATFCSH switch) into a hot polling loop.
        No-op when the ECU has no tracked session.
        """
        if tx_id in self._sessions:
            self._sessions[tx_id] = time.monotonic()

    async def keepalive_stale(self, threshold: float = 1.5):
        """Send keepalives to all sessions that haven't been refreshed recently.

        Args:
            threshold: Seconds since last keepalive before a session is considered stale.
        """
        now = time.monotonic()
        stale = [tx for tx, ts in self._sessions.items() if now - ts > threshold]
        for tx_id in stale:
            await self.send_keepalive(tx_id)

    async def keepalive_all(self):
        """Send keepalives to ALL active sessions (regardless of staleness)."""
        for tx_id in list(self._sessions.keys()):
            await self.send_keepalive(tx_id)

    def start_background_keepalive(self, interval: float = 2.0) -> asyncio.Task:
        """Start a background task that sends keepalives to all tracked sessions.

        Returns the task (caller must cancel it when done).
        """
        if self._bg_task and not self._bg_task.done():
            self._bg_task.cancel()

        async def _loop():
            try:
                while True:
                    await asyncio.sleep(interval)
                    await self.keepalive_stale(threshold=interval * 0.75)
            except asyncio.CancelledError:
                pass

        self._bg_task = asyncio.create_task(_loop())
        return self._bg_task

    def stop_background_keepalive(self):
        """Stop the background keepalive task if running."""
        if self._bg_task and not self._bg_task.done():
            self._bg_task.cancel()
            self._bg_task = None

    async def close_session(self, tx_id: int):
        """Return an ECU to default session (1001)."""
        if tx_id in self._sessions:
            await self.terminal.set_header(tx_id)
            try:
                await self.terminal.send_command("1001", timeout=2.0)
            except Exception:
                pass
            del self._sessions[tx_id]
            if self.verbose:
                print(f"  [session] 0x{tx_id:03X}: closed (returned to default session)")

    async def close_all(self):
        """Close all active sessions."""
        self.stop_background_keepalive()
        for tx_id in list(self._sessions.keys()):
            await self.close_session(tx_id)
