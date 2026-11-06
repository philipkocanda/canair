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
    sm.start_background_keepalive(interval=2.0)
    ...
    await sm.stop_background_keepalive()

    # Cleanup:
    await sm.close_all()
"""

import asyncio
import sys
import time

from canair.core.eventlog import log_event
from canair.ecus.wake import WakePlan
from canair.transport import keepalive
from canair.transport.protocol import Terminal
from canair.uds.requests import session_request
from canair.uds.services import (
    DEFAULT_SESSION_REQUEST,
    DIAGNOSTIC_SESSION_CONTROL,
    TESTER_PRESENT,
    TESTER_PRESENT_REQUEST,
)


def _status(*args, **kwargs) -> None:
    """Session progress and verbose traces: stderr, so a command's stdout stays its result."""
    print(*args, file=sys.stderr, **kwargs)


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
        # Why the background keepalive stopped on its own: the link failed under it.
        # Nothing awaits that task, so the error is kept here for the caller to act on.
        self.link_error: ConnectionError | None = None
        # ECUs that answered TesterPresent with serviceNotSupported: asking again is wasted
        # bus time (see canair.transport.keepalive). Their session stays tracked.
        self._no_keepalive: set[int] = set()
        # Seconds spent sending keepalives, inline or from the background task. The
        # live monitor takes it out of an ECU's poll cost: on a serial ELM327 pipe a
        # keepalive to another ECU is not time the polled ECU held everyone up.
        self.keepalive_seconds = 0.0

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
        # Short per-prime timeout so frames fire densely (floor to keep a single
        # frame's round-trip feasible on either transport).
        prime_timeout = max(0.1, plan.interval_s)
        if self.verbose:
            _status(
                f"  [wake] 0x{tx_id:03X}: rapid_read up to {plan.attempts}× "
                f"{plan.prime} @ ~{int(prime_timeout * 1000)}ms..."
            )
        awake = False
        # One transaction for the whole burst: a keepalive slipped in between two primes
        # would re-point the header and leave a gap in the burst.
        async with self.terminal.transaction():
            await self.terminal.set_header(tx_id)
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
                        _status(f"  [wake] 0x{tx_id:03X}: awake (attempt {attempt + 1})")
                    break
        if not awake and self.verbose:
            _status(
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
        """The "10 xx" sub-function this ECU last accepted, or None if none is known."""
        return self._session_modes.get(tx_id)

    def track(self, tx_id: int, mode: str | None = None) -> None:
        """Keep alive a session that was opened without :meth:`open_session`.

        The SKM relay wake opens its own session; tracking it here gives it the same
        keepalive as every other session.
        """
        self._sessions[tx_id] = time.monotonic()
        if mode is not None:
            self._session_modes[tx_id] = mode

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
                :mod:`canair.ecus.wake`). When ``wake`` is set and a plan is given, its
                rapid-fire prime loop is used instead of the single ``10 01`` —
                the way to rouse a fast-sleeping ECU (e.g. a Smart Key Module).
                The plan's ``session_mode`` overrides ``mode`` when it differs
                from the default.
            force: Resend ``10 xx`` even if a session is already tracked open on
                this ECU. Without this, an already-open session only has its
                keepalive timestamp refreshed (see below) — which would silently
                no-op a live mode switch (e.g. the monitor TUI's session-type
                picker moving an ECU from extended to KWP standard session).

        The whole exchange (header, wake, request) is one transaction, so a background
        keepalive cannot re-point the header in between. The session is tracked (and kept
        alive) whatever the reply, but its mode is recorded only when the ECU accepts it.

        Returns:
            True if the ECU accepted the session request.
        """
        if wake_plan is not None and wake_plan.session_mode:
            mode = wake_plan.session_mode
        mode = mode.upper().removeprefix("0X").zfill(2)
        req = session_request(mode)

        if not wake and not force and tx_id in self._sessions:
            # Already in an extended session on this ECU — refresh rather than
            # re-sending 10xx (a repeated `session <ECU>` step / REPL command).
            self._sessions[tx_id] = time.monotonic()
            if self.verbose:
                _status(f"  [session] 0x{tx_id:03X}: already active — refreshed.")
            return True

        async with self.terminal.transaction():
            await self.terminal.set_header(tx_id)
            if wake:
                if wake_plan is not None:
                    # Profile-declared ritual — rapid-fire prime loop (fast-sleepers).
                    await self.rapid_read_wake(tx_id, wake_plan)
                else:
                    if self.verbose:
                        _status(f"  [session] Sending wake-up (1001) to 0x{tx_id:03X}...")
                    await self.terminal.send_uds(
                        DEFAULT_SESSION_REQUEST,
                        timeout=15.0,
                        expected_sid=DIAGNOSTIC_SESSION_CONTROL,
                    )
                    await asyncio.sleep(0.5)

            if self.verbose:
                _status(f"  [session] Entering session (10{mode}) on 0x{tx_id:03X}...")
            # Checked by service, so a stale reply to an earlier read is not taken for
            # an open session (see elm327_session for why the sub-function is not).
            resp = await self.terminal.send_uds(
                req, timeout=5.0, expected_sid=DIAGNOSTIC_SESSION_CONTROL
            )

        if resp.get("ok"):
            if self.verbose:
                _status(f"  [session] 0x{tx_id:03X}: session established.")
        elif resp.get("nrc") is not None:
            nrc = resp["nrc"]
            desc = resp["nrc_desc"]
            _status(f"  [session] 0x{tx_id:03X}: NRC 0x{nrc:02X} ({desc}) -- continuing anyway")
        else:
            error = resp.get("error", "unknown")
            _status(f"  [session] 0x{tx_id:03X}: failed ({error}) -- continuing anyway")

        self._sessions[tx_id] = time.monotonic()
        if resp.get("ok"):
            # A refusal leaves the ECU in whatever session it was in, so only an accepted
            # request changes the mode shown for it.
            self._session_modes[tx_id] = mode
        return resp.get("ok", False)

    async def send_keepalive(self, tx_id: int):
        """Send TesterPresent (3E00) to a specific ECU.

        Validated (``expected_sid=TESTER_PRESENT``) and transactional on purpose. An
        unchecked keepalive is how a transient link stall turns into a permanent
        response offset: it consumes the late reply still owed to the *previous*
        request, sees that reply's ELM prompt, and leaves its own ``7E00``
        buffered for whoever reads next — with no error and no diagnostic trace.
        Validating it lets ``send_uds`` realign the pipe instead of hiding the
        desync.
        """
        try:
            async with self.terminal.transaction():
                # Timed inside the transaction: a background keepalive queued behind a
                # slow read must not count that read's time as its own.
                t0 = time.monotonic()
                try:
                    await self.terminal.set_header(tx_id)
                    resp = await self.terminal.send_uds(
                        TESTER_PRESENT_REQUEST, timeout=1.5, expected_sid=TESTER_PRESENT
                    )
                finally:
                    self.keepalive_seconds += time.monotonic() - t0
        except ConnectionError:
            # The pipe could not be realigned — surface it so the caller's
            # reconnect path runs rather than silently polling a broken link.
            raise
        except Exception:
            resp = {}
        if keepalive.unsupported(resp) and tx_id not in self._no_keepalive:
            self._no_keepalive.add(tx_id)
            keepalive.report_unsupported(tx_id, transport=self.terminal.diag.transport)
        # Refresh regardless of the reply: the *request* went out, so the ECU's S3
        # timer was reset either way, and this timestamp only rate-limits how often
        # keepalive_stale re-sends. Gating it on success would hammer an ECU that
        # never answers with a 3E00 on every poll cycle.
        self._sessions[tx_id] = time.monotonic()
        if self.verbose:
            state = "ok" if resp.get("ok") else resp.get("error", "failed")
            _status(f"  [session] 3E00 -> 0x{tx_id:03X}: {state}")

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

        Raises:
            ConnectionError: the background keepalive found the link dead
                (:attr:`link_error`); the caller's reconnect path takes it from here.
        """
        if self.link_error is not None:
            raise self.link_error
        now = time.monotonic()
        stale = [
            tx
            for tx, ts in self._sessions.items()
            if now - ts > threshold and tx not in self._no_keepalive
        ]
        for tx_id in stale:
            await self.send_keepalive(tx_id)

    async def keepalive_all(self):
        """Send keepalives to ALL active sessions (regardless of staleness)."""
        for tx_id in list(self._sessions.keys()):
            if tx_id not in self._no_keepalive:
                await self.send_keepalive(tx_id)

    def start_background_keepalive(self, interval: float = 2.0) -> asyncio.Task:
        """Start a background task that sends keepalives to all tracked sessions.

        Returns the task; stop it with :meth:`stop_background_keepalive`. When the link
        fails under it, the task ends and leaves the error in :attr:`link_error`.
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
            except ConnectionError as exc:
                self.link_error = exc
                log_event(
                    "bus",
                    f"background keepalive stopped: {exc}",
                    transport=self.terminal.diag.transport,
                )

        self._bg_task = asyncio.create_task(_loop())
        return self._bg_task

    async def stop_background_keepalive(self) -> None:
        """Stop the background keepalive task and wait until it has ended."""
        task, self._bg_task = self._bg_task, None
        if task is None:
            return
        task.cancel()
        # asyncio.wait, not a bare await: it never raises the task's own outcome, and a
        # cancellation of the caller still propagates.
        await asyncio.wait({task})

    async def close_session(self, tx_id: int):
        """Return an ECU to default session (1001)."""
        if tx_id in self._sessions:
            try:
                async with self.terminal.transaction():
                    await self.terminal.set_header(tx_id)
                    await self.terminal.send_uds(
                        DEFAULT_SESSION_REQUEST,
                        timeout=2.0,
                        expected_sid=DIAGNOSTIC_SESSION_CONTROL,
                    )
            except Exception:
                pass
            del self._sessions[tx_id]
            self._session_modes.pop(tx_id, None)
            if self.verbose:
                _status(f"  [session] 0x{tx_id:03X}: closed (returned to default session)")

    async def close_all(self):
        """Close all active sessions."""
        await self.stop_background_keepalive()
        for tx_id in list(self._sessions.keys()):
            await self.close_session(tx_id)
