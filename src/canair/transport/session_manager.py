"""Multi-ECU diagnostic session manager.

Opens diagnostic sessions (``10 xx``) on any number of ECUs, wakes a sleeping one first when
asked (the profile's ``wake:`` ritual, else a ``10 01``), and keeps each session alive with
TesterPresent (``3E 00``) on its own header. Since an ELM327 is a serial pipe and the raw
terminal shares its current target the same way, every exchange here is one
``terminal.transaction()``, so a keepalive from the background task cannot re-point a
foreground request, nor the other way round.

The state (which ECUs, which mode, when last refreshed) is a
:class:`~canair.transport.session_table.SessionTable`; this class is the I/O over it.

Usage::

    sm = SessionManager(terminal)
    await sm.open_session(0x770)               # IGPM, extended session
    await sm.open_session(0x7A5, wake=True)    # SKM, from deep sleep

    await sm.keepalive_stale()                 # before a foreground request
    sm.start_background_keepalive()            # while idle (REPL, --hold)
    await sm.stop_background_keepalive()

    await sm.close_all()                       # back to the default session
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from canair.core.eventlog import log_event
from canair.ecus.wake import WakePlan
from canair.render import messages
from canair.transport import keepalive
from canair.transport.protocol import Terminal
from canair.transport.session_table import SessionTable
from canair.uds.echo import request_echo
from canair.uds.requests import session_request
from canair.uds.services import (
    DEFAULT_SESSION_REQUEST,
    DIAGNOSTIC_SESSION_CONTROL,
    TESTER_PRESENT,
    TESTER_PRESENT_REQUEST,
)
from canair.uds.subfunctions import SessionType, format_session_type, parse_session_type

#: The session :meth:`SessionManager.open_session` enters unless told otherwise.
DEFAULT_SESSION_MODE = SessionType.EXTENDED_DIAGNOSTIC_SESSION

# Seconds. One set for every command (plans/2026-11-05-one-session-owner.md, decision 1).
WAKE_TIMEOUT_S = 3.0  # each 10 01 of a default wake
SESSION_TIMEOUT_S = 5.0  # the 10 xx request
KEEPALIVE_TIMEOUT_S = 1.5  # a 3E 00
# TesterPresent cadence: well inside the UDS S3 timer (nominally 5 s), so one lost keepalive
# does not end the session. A session is idle once nothing refreshed it for KEEPALIVE_IDLE_S.
KEEPALIVE_INTERVAL_S = 2.0
KEEPALIVE_IDLE_S = 1.5


def _status(*args, **kwargs) -> None:
    """Verbose traces: stderr, so a command's stdout stays its result."""
    print(*args, file=sys.stderr, **kwargs)


@dataclass(frozen=True)
class SessionOutcome:
    """What an ECU said to a session request.

    ``ok`` is True when it accepted (or a session of that kind was already tracked open and
    nothing was sent). ``nrc``/``nrc_desc`` carry a refusal and ``error`` a non-answer.
    """

    tx_id: int
    mode: int
    ok: bool
    nrc: int | None = None
    nrc_desc: str | None = None
    error: str | None = None

    def problem(self) -> str | None:
        """One line on why the session is not open, or None when it is."""
        if self.ok:
            return None
        request = f"10 {format_session_type(self.mode)}"
        if self.nrc is not None:
            desc = f" ({self.nrc_desc})" if self.nrc_desc else ""
            return f"0x{self.tx_id:03X} refused session {request}: NRC 0x{self.nrc:02X}{desc}"
        return f"no answer from 0x{self.tx_id:03X} to session {request} ({self.error or 'unknown'})"


class SessionManager:
    """Opens, wakes, keeps alive and closes diagnostic sessions on any number of ECUs."""

    def __init__(
        self, terminal: Terminal, verbose: bool = False, table: SessionTable | None = None
    ):
        self.terminal = terminal
        self.verbose = verbose
        self.table = table if table is not None else SessionTable()
        self._bg_task: asyncio.Task | None = None
        # Why the background keepalive stopped on its own: the link failed under it.
        # Nothing awaits that task, so the error is kept here for the caller to act on.
        self.link_error: ConnectionError | None = None
        # Seconds spent sending keepalives, inline or from the background task. The
        # live monitor takes it out of an ECU's poll cost: on a serial ELM327 pipe a
        # keepalive to another ECU is not time the polled ECU held everyone up.
        self.keepalive_seconds = 0.0

    # -- state ---------------------------------------------------------------
    @property
    def active_sessions(self) -> list[int]:
        """TX ids of every tracked session."""
        return self.table.tracked()

    def has_session(self, tx_id: int) -> bool:
        return self.table.has(tx_id)

    def session_mode(self, tx_id: int) -> int | None:
        """The session type this ECU last accepted, or None if none is known."""
        return self.table.mode(tx_id)

    def track(self, tx_id: int, mode: int | str | None = None) -> None:
        """Keep alive a session that was opened without :meth:`open_session`.

        The SKM relay wake opens its own session; tracking it here gives it the same
        keepalive as every other session.
        """
        self.table.track(tx_id, None if mode is None else parse_session_type(mode))

    def mark_active(self, tx_id: int) -> None:
        """Note real traffic to an ECU as keepalive-equivalent.

        Any request the ECU answers resets its session timer, so an ECU being polled
        needs no extra ``3E 00``. Callers invoke this after an answered read so
        :meth:`keepalive_stale` does not inject a redundant TesterPresent (and, on an
        ELM327, a header switch) into a hot polling loop. No-op without a session.
        """
        self.table.mark_active(tx_id)

    # -- opening ---------------------------------------------------------------
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

    async def open_session(
        self,
        tx_id: int,
        wake: bool = False,
        mode: int | str = DEFAULT_SESSION_MODE,
        wake_plan: WakePlan | None = None,
        force: bool = False,
        report: bool = True,
    ) -> SessionOutcome:
        """Enter a diagnostic session on an ECU.

        Args:
            tx_id: ECU CAN arbitration ID (e.g., 0x770 for IGPM).
            wake: Wake the ECU first: its ``wake_plan`` when it has one, else ``10 01``.
            mode: DiagnosticSessionControl sub-function, as a byte or its hex text.
                Default ``03`` (UDS extendedDiagnosticSession). Use ``81`` for the
                KWP2000 standardDiagnosticSession on powertrain ECUs that reject
                ``10 03`` (e.g. the BMS). Programming/unknown modes are refused by
                the command-safety guard unless ``--unsafe``.
            wake_plan: Optional profile-declared wake ritual (see
                :mod:`canair.ecus.wake`). When ``wake`` is set and a plan is given, its
                rapid-fire prime loop is used instead of the single ``10 01`` —
                the way to rouse a fast-sleeping ECU (e.g. a Smart Key Module).
                The plan's ``session_mode`` overrides ``mode``.
            force: Resend ``10 xx`` even if a session is already tracked open on
                this ECU. Without this, an already-open session only has its
                keepalive timestamp refreshed — which would silently no-op a live mode
                switch (e.g. the monitor TUI's session-type picker moving an ECU from
                extended to KWP standard session).
            report: Print a refusal or a non-answer as a warning (a caller that shows
                the outcome itself, such as a TUI, passes False).

        The whole exchange (header, wake, request) is one transaction, so a background
        keepalive cannot re-point the header in between. The session is tracked (and kept
        alive) whatever the reply, but its mode is recorded only when the ECU accepts it.
        """
        if wake_plan is not None and wake_plan.session_mode:
            mode = wake_plan.session_mode
        sub = parse_session_type(mode)

        if not wake and not force and self.table.has(tx_id):
            # Already in a session on this ECU — refresh rather than re-sending 10xx (a
            # repeated `session <ECU>` step / REPL command).
            self.table.track(tx_id)
            if self.verbose:
                _status(f"  [session] 0x{tx_id:03X}: already active — refreshed.")
            return SessionOutcome(tx_id, sub, ok=True)

        async with self.terminal.transaction():
            await self.terminal.set_header(tx_id)
            if wake:
                await self._wake(tx_id, wake_plan)
            if self.verbose:
                _status(f"  [session] Entering session (10{sub:02X}) on 0x{tx_id:03X}...")
            resp = await self._session_request(tx_id, sub)
            if not resp.get("ok") and resp.get("nrc") is None:
                # No answer at all (the ECU was still waking, a frame was lost): ask once
                # more. A refusal is an answer and is not retried.
                resp = await self._session_request(tx_id, sub)

        outcome = SessionOutcome(
            tx_id,
            sub,
            ok=bool(resp.get("ok")),
            nrc=resp.get("nrc"),
            nrc_desc=resp.get("nrc_desc"),
            error=resp.get("error"),
        )
        self.table.opened(tx_id, sub, accepted=outcome.ok)
        if outcome.ok and self.verbose:
            _status(f"  [session] 0x{tx_id:03X}: session established.")
        problem = outcome.problem()
        if problem and report:
            messages.warning(f"{problem} — continuing anyway")
        return outcome

    async def _wake(self, tx_id: int, wake_plan: WakePlan | None) -> None:
        """Rouse a sleeping ECU with the adapter's own wait shortened, so frames go out densely.

        The profile's ritual when the ECU declares one, else ``10 01`` (asked twice when
        the first draws no answer: the first frame may only wake the transceiver).
        """
        async with self.terminal.short_ecu_wait():
            if wake_plan is not None:
                await self.rapid_read_wake(tx_id, wake_plan)
                return
            if self.verbose:
                _status(f"  [session] Sending wake-up (1001) to 0x{tx_id:03X}...")
            for _attempt in range(2):
                resp = await self.terminal.send_uds(
                    DEFAULT_SESSION_REQUEST,
                    timeout=WAKE_TIMEOUT_S,
                    expected_sid=DIAGNOSTIC_SESSION_CONTROL,
                )
                if resp.get("ok"):
                    return

    async def _session_request(self, tx_id: int, sub: int):
        # Checked by service, so a stale reply to an earlier read is not taken for an open
        # session, and on a UDS ECU by the session type it repeats. The echo is left alone
        # on any other ECU, since some KWP2000 ECUs are not known to repeat it.
        return await self._send_checked(
            tx_id, session_request(sub), DIAGNOSTIC_SESSION_CONTROL, SESSION_TIMEOUT_S
        )

    async def _send_checked(self, tx_id: int, request: str, sid: int, timeout: float):
        """Send ``request`` to ``tx_id``, checked by ``sid`` and the echo its protocol fixes."""
        expected = request_echo(request, self.terminal.session_policy.protocol(tx_id))
        return await self.terminal.send_uds(
            request,
            timeout=timeout,
            expected_sid=sid,
            expected_echo=expected[1] if expected is not None else None,
        )

    @contextlib.asynccontextmanager
    async def held(
        self,
        tx_id: int,
        *,
        mode: int | str = DEFAULT_SESSION_MODE,
        wake: bool = False,
        wake_plan: WakePlan | None = None,
        close: bool = False,
        report: bool = True,
    ) -> AsyncIterator[SessionOutcome]:
        """Hold a session on one ECU for the block, kept alive while the block runs.

        Opens it (always sending the request, so a second ``held`` can change the mode),
        runs the background keepalive, and on exit stops it. The ECU is returned to its
        default session only with ``close``; otherwise it is left to time out, as a
        single-ECU command always has.
        """
        outcome = await self.open_session(
            tx_id, wake=wake, mode=mode, wake_plan=wake_plan, force=True, report=report
        )
        self.start_background_keepalive()
        try:
            yield outcome
        finally:
            await self.stop_background_keepalive()
            if close:
                await self.close_session(tx_id)
            else:
                self.table.forget(tx_id)

    # -- keeping alive ---------------------------------------------------------
    async def send_keepalive(self, tx_id: int):
        """Send TesterPresent (3E00) to a specific ECU.

        Validated (by service, and on a UDS ECU by its ``00`` echo) and transactional on
        purpose. An unchecked keepalive is how a transient link stall turns into a
        permanent response offset: it consumes the late reply still owed to the
        *previous* request, sees that reply's ELM prompt, and leaves its own ``7E00``
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
                    resp = await self._send_checked(
                        tx_id, TESTER_PRESENT_REQUEST, TESTER_PRESENT, KEEPALIVE_TIMEOUT_S
                    )
                finally:
                    self.keepalive_seconds += time.monotonic() - t0
        except ConnectionError:
            # The pipe could not be realigned — surface it so the caller's
            # reconnect path runs rather than silently polling a broken link.
            raise
        except Exception:
            resp = {}
        if keepalive.unsupported(resp) and self.table.refused_keepalive(tx_id):
            keepalive.report_unsupported(tx_id, transport=self.terminal.diag.transport)
        # Refresh regardless of the reply: the *request* went out, so the ECU's S3
        # timer was reset either way, and this timestamp only rate-limits how often
        # keepalive_stale re-sends. Gating it on success would hammer an ECU that
        # never answers with a 3E00 on every poll cycle.
        self.table.mark_active(tx_id)
        if self.verbose:
            state = "ok" if resp.get("ok") else resp.get("error", "failed")
            _status(f"  [session] 3E00 -> 0x{tx_id:03X}: {state}")

    async def keepalive_stale(self, threshold: float = KEEPALIVE_IDLE_S):
        """Send keepalives to all sessions that haven't been refreshed recently.

        Args:
            threshold: Seconds since last keepalive before a session is considered stale.

        Raises:
            ConnectionError: the background keepalive found the link dead
                (:attr:`link_error`); the caller's reconnect path takes it from here.
        """
        if self.link_error is not None:
            raise self.link_error
        self._note_answers()
        for tx_id in self.table.stale(threshold):
            await self.send_keepalive(tx_id)

    def _note_answers(self) -> None:
        """Count every request an ECU answered as a keepalive, whoever sent it.

        A command that holds a session and keeps reading (a scan) never calls
        :meth:`mark_active`; the terminal's tally of answers says the session timer was
        reset all the same, so no ``3E 00`` goes out while the ECU is busy.
        """
        answered = self.terminal.diag.answered
        for tx_id in self.table.tracked():
            at = answered.get(tx_id)
            refreshed = self.table.refreshed_at(tx_id)
            if at is not None and (refreshed is None or at > refreshed):
                self.table.track(tx_id, at=at)

    async def keepalive_all(self):
        """Send keepalives to ALL active sessions (regardless of staleness)."""
        for tx_id in self.table.tracked():
            if self.table.wants_keepalive(tx_id):
                await self.send_keepalive(tx_id)

    def start_background_keepalive(self, interval: float = KEEPALIVE_INTERVAL_S) -> asyncio.Task:
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

    @property
    def keepalive_running(self) -> bool:
        """Is the background keepalive task running?"""
        return self._bg_task is not None and not self._bg_task.done()

    async def stop_background_keepalive(self) -> None:
        """Stop the background keepalive task and wait until it has ended."""
        task, self._bg_task = self._bg_task, None
        if task is None:
            return
        task.cancel()
        # asyncio.wait, not a bare await: it never raises the task's own outcome, and a
        # cancellation of the caller still propagates.
        await asyncio.wait({task})

    # -- closing ---------------------------------------------------------------
    async def close_session(self, tx_id: int):
        """Return an ECU to default session (1001)."""
        if not self.table.has(tx_id):
            return
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
        self.table.forget(tx_id)
        if self.verbose:
            _status(f"  [session] 0x{tx_id:03X}: closed (returned to default session)")

    async def close_all(self):
        """Close all active sessions."""
        await self.stop_background_keepalive()
        for tx_id in self.table.tracked():
            await self.close_session(tx_id)
