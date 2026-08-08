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

from .transport.protocol import Terminal
from .wake import WakePlan


class SessionManager:
    """Manages extended diagnostic sessions for multiple ECUs simultaneously."""

    def __init__(self, terminal: Terminal, verbose: bool = False):
        self.terminal = terminal
        self.verbose = verbose
        # {tx_id: last_keepalive_timestamp}
        self._sessions: dict[int, float] = {}
        # {tx_id: last "10 xx" sub-function actually sent, 2-hex-digit str}.
        # Populated only when a session is (re-)opened via open_session — a plain
        # keepalive (3E00) never changes it. Read by callers that need to show
        # which diagnostic session type is currently active on an ECU (e.g. the
        # monitor TUI status bar / session-type picker).
        self._session_modes: dict[int, str] = {}
        self._bg_task: asyncio.Task | None = None

    async def rapid_read_wake(self, tx_id: int, plan: WakePlan) -> bool:
        """Rouse a fast-sleeping ECU by firing a cheap request back-to-back.

        Some modules (e.g. a Smart Key Module) power their CAN transceiver only
        briefly — a single ``10 01`` wake races the sleep timer and follow-up
        reads return NO DATA. This fires ``plan.prime`` up to ``plan.attempts``
        times with a **short per-prime timeout** (``plan.interval_ms``) so the
        frames go out *densely* — filling the ECU's sleep window — instead of
        each request blocking for the full response timeout (which, on a
        deep-asleep ECU, would space the "rapid" primes seconds apart and miss
        the window entirely). This mirrors the original ELM ``ATST10`` fast-timer
        technique, made transport-agnostic (uses only ``set_header``/
        ``send_command``, so it runs identically over ``slcan-tcp`` and
        ``wican-ws``).

        Breaks early the moment a prime draws any response (positive or NRC — the
        ECU is awake). Returns True if a response was seen. Even when it returns
        False, the burst of frames may still have roused the transceiver, so the
        caller should proceed to open the session (with a normal timeout).
        """
        await self.terminal.set_header(tx_id)
        # Short per-prime timeout so frames fire densely (floor to keep a single
        # frame's round-trip feasible on either transport).
        prime_timeout = max(0.1, plan.interval_s)
        if self.verbose:
            print(
                f"  [wake] 0x{tx_id:03X}: rapid_read up to {plan.attempts}× "
                f"{plan.prime} @ ~{int(prime_timeout * 1000)}ms..."
            )
        awake = False
        for attempt in range(plan.attempts):
            try:
                resp = await self.terminal.send_command(plan.prime, timeout=prime_timeout)
            except Exception:
                resp = ""
            clean = resp.replace(" ", "").upper()
            # Any non-empty, non-NO-DATA hex (a positive response OR an NRC 7Fxx)
            # means the ECU is awake and answering.
            if clean and "NODATA" not in clean and "?" not in clean:
                awake = True
                if self.verbose:
                    print(f"  [wake] 0x{tx_id:03X}: awake (attempt {attempt + 1})")
                break
        if not awake and self.verbose:
            print(
                f"  [wake] 0x{tx_id:03X}: no prime response after {plan.attempts} — "
                "proceeding to session anyway (frames may have roused it)"
            )
        return awake

    @property
    def active_sessions(self) -> list[int]:
        """List of TX IDs with active sessions."""
        return list(self._sessions.keys())

    def has_session(self, tx_id: int) -> bool:
        return tx_id in self._sessions

    def session_mode(self, tx_id: int) -> str | None:
        """The last "10 xx" sub-function opened on this ECU, or None if untracked."""
        return self._session_modes.get(tx_id)

    async def open_session(
        self,
        tx_id: int,
        wake: bool = False,
        mode: str = "03",
        wake_plan: WakePlan | None = None,
        force: bool = False,
    ) -> bool:
        """Enter a diagnostic session on an ECU.

        Args:
            tx_id: ECU CAN arbitration ID (e.g., 0x770 for IGPM).
            wake: Send 1001 first to wake from deep sleep.
            mode: DiagnosticSessionControl sub-function (hex, no 0x). Default
                ``"03"`` (UDS extendedDiagnosticSession). Use ``"81"`` for the
                KWP2000 standardDiagnosticSession on powertrain ECUs that reject
                ``10 03`` (e.g. the BMS). Programming/unknown modes are refused by
                the command-safety guard unless ``--unsafe``.
            wake_plan: Optional profile-declared wake ritual (see
                :mod:`canlib.wake`). When ``wake`` is set and a plan is given, its
                rapid-fire prime loop is used instead of the single ``10 01`` —
                the way to rouse a fast-sleeping ECU (e.g. a Smart Key Module).
                The plan's ``session_mode`` overrides ``mode`` when it differs
                from the default.
            force: Resend ``10 xx`` even if a session is already tracked open on
                this ECU. Without this, an already-open session only has its
                keepalive timestamp refreshed (see below) — which would silently
                no-op a live mode switch (e.g. the monitor TUI's session-type
                picker moving an ECU from extended to KWP standard session).

        Returns:
            True if session was established (or at least attempted).
        """
        if wake_plan is not None and wake_plan.session_mode:
            mode = wake_plan.session_mode
        mode = mode.upper().removeprefix("0X").zfill(2)
        req = f"10{mode}"
        await self.terminal.set_header(tx_id)

        if not wake and not force and tx_id in self._sessions:
            # Already in an extended session on this ECU — refresh rather than
            # re-sending 10xx (a repeated `session <ECU>` step / REPL command).
            self._sessions[tx_id] = time.monotonic()
            if self.verbose:
                print(f"  [session] 0x{tx_id:03X}: already active — refreshed.")
            return True

        if wake:
            if wake_plan is not None:
                # Profile-declared ritual — rapid-fire prime loop (fast-sleepers).
                await self.rapid_read_wake(tx_id, wake_plan)
                await self.terminal.set_header(tx_id)
            else:
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
        self._session_modes[tx_id] = mode
        return resp.get("ok", False)

    async def send_keepalive(self, tx_id: int):
        """Send TesterPresent (3E00) to a specific ECU.

        Validated (``expected_sid=0x3E``) and transactional on purpose. An
        unchecked keepalive is how a transient link stall turns into a permanent
        response offset: it consumes the late reply still owed to the *previous*
        request, sees that reply's ELM prompt, and leaves its own ``7E00``
        buffered for whoever reads next — with no error and no diagnostic trace.
        Validating it lets ``send_uds`` realign the pipe instead of hiding the
        desync.
        """
        try:
            async with self.terminal.transaction():
                await self.terminal.set_header(tx_id)
                resp = await self.terminal.send_uds("3E00", timeout=1.5, expected_sid=0x3E)
        except ConnectionError:
            # The pipe could not be realigned — surface it so the caller's
            # reconnect path runs rather than silently polling a broken link.
            raise
        except Exception:
            resp = {}
        # Refresh regardless of the reply: the *request* went out, so the ECU's S3
        # timer was reset either way, and this timestamp only rate-limits how often
        # keepalive_stale re-sends. Gating it on success would hammer an ECU that
        # never answers with a 3E00 on every poll cycle.
        self._sessions[tx_id] = time.monotonic()
        if self.verbose:
            state = "ok" if resp.get("ok") else resp.get("error", "failed")
            print(f"  [session] 3E00 -> 0x{tx_id:03X}: {state}", end="")

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
            self._session_modes.pop(tx_id, None)
            if self.verbose:
                print(f"  [session] 0x{tx_id:03X}: closed (returned to default session)")

    async def close_all(self):
        """Close all active sessions."""
        self.stop_background_keepalive()
        for tx_id in list(self._sessions.keys()):
            await self.close_session(tx_id)
