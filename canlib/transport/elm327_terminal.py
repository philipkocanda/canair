"""``Elm327Terminal`` — the transport-agnostic ELM327 protocol engine.

canair reaches an ELM327-style adapter (a WiCAN Pro's WebSocket terminal, a
direct WiFi ELM327 clone over TCP, or — future — a serial dongle) through this
one engine. It owns the ELM327 *conversation*: ELM init, ``ATSH``/``ATFCSH``
header caching, command serialisation and transactions, the timing loop that
collects a reply, pipe realignment, and ``send_uds`` request/retry orchestration.
Bytes move only through an injected
:class:`~canlib.transport.channel.Channel`. Swapping the channel is the whole of
"support a new ELM327 wire" (the "keep the WiCAN replaceable" rule): the delicate
timing loop lives here exactly once, never duplicated per transport.

Three concerns deliberately live in sibling modules, each testable without a
channel, a clock, or an event loop:

- :mod:`~canlib.transport.elm327_pipe` — the ``>``-prompt ledger that decides
  *which* reply answers the command in hand.
- :mod:`~canlib.transport.elm327_session` — DiagnosticSessionControl and the
  TesterPresent keepalive.
- :mod:`~canlib.transport.elm327_frame_count` — expected-response-count policy.

Wire bindings are separate too: :class:`canlib.transport.elm327_tcp.Elm327TcpTerminal`
and :class:`canlib.terminal.WiCANTerminal`.

It structurally satisfies :class:`canlib.transport.protocol.Terminal`, so every
live mode drives it through the shared ``dispatch_mode`` unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
import time
from collections.abc import AsyncIterator

from ..link_latency import LinkLatency
from ..log import log_command, log_response
from ..safety import enforce_command_safety
from ..timing import TimingRecorder
from ..transport_stats import TransportStats
from ..uds_parse import CAT_DECODE, CAT_STALE, UdsResponse, parse_uds_response
from .channel import Channel
from .elm327_frame_count import FrameCountCache
from .elm327_pipe import ResponsePipe, compact
from .elm327_session import enter_extended_session

# Response classes that prove the pipe is carrying something other than this
# request's answer, and so warrant realigning it. See the call site in `send_uds`
# for why `no_data` is not one of them.
_RESYNC_ON = frozenset({CAT_STALE, CAT_DECODE})


# Largest UDS request an ELM327 will accept as one exchange. The adapter writes
# the bytes you give it into a single CAN frame and prepends the ISO-TP PCI byte
# itself, leaving 7 for the request; it does not segment, and rejects anything
# longer with a bare `?` rather than an NRC. So a `22` + 2-bytes-per-DID batch fits
# only 3 DIDs — see `multi_batch.transport_did_cap`.
_ELM_MAX_REQUEST_BYTES = 7

# Default ELM327 ECU-wait budget in seconds, used when `elm_timeout_cmd` can't be
# parsed. `ATST hh` sets the wait to hh * 4.096 ms, so ATST96 (0x96 = 150) is ~614ms.
_ELM_ST_UNIT = 0.004096
_DEFAULT_ELM_BUDGET = 0x96 * _ELM_ST_UNIT

# Floor for the link-latency slack added to the ELM's own budget when deciding how
# long "quiet" must last for the pipe to be provably empty. It is only a floor:
# the slack is normally the *measured* round trip (`LinkLatency`), because the
# failure this guards against is a stalled link delivering a late reply after a
# too-short drain window, and on a cellular/VPN path the link term is the dominant
# one and unknowable up front. Keeping the old constant as the floor means a fast
# link never gets a tighter window than the one already known to work.
_LINK_LATENCY_MARGIN = 0.5

# ELM327 commands the adapter answers from memory — no CAN traffic, no hardware
# work — so their round trip *is* the link's, which is the only thing
# `LinkLatency` may be fed. An allowlist on purpose: `ATZ` resets the chip and
# `ATSP` can probe the bus, and either would inflate the estimate with time the
# network never spent. Header writes dominate the hot path (one pair per ECU
# switch), so this samples often enough to track a link that changes while
# driving. Matched as prefixes, since the header writes carry an argument
# (`ATSH7E4`); none of them is a prefix of a command that does more work.
_LINK_PROBE_CMDS = ("ATI", "ATSH", "ATFCSH")

# Ceiling on one resync: enough for several quiet windows, short enough that a
# wedged adapter escalates to a reconnect instead of stalling the poll loop.
_RESYNC_MAX_SECONDS = 3.0

# Ceiling on the quiet window itself. Generous, because on a hotspot/VPN path the
# adapter's own budget plus a measured round trip is legitimately seconds — but
# bounded, so one absurd measurement cannot wedge the poll loop.
_RESYNC_QUIET_MAX = 8.0

# State-free ELM327 command used as the post-drain alignment probe. Any AT command
# would do — what matters is that *a* prompt-terminated reply comes back, not what
# it says (the WiCAN answers ATI with "OBDLink MX", a real ELM327 with
# "ELM327 v1.5"), so this never matches on reply text.
_RESYNC_PROBE = "ATI"


class Elm327Terminal:
    """ELM327 protocol engine over an injected byte :class:`Channel`."""

    def __init__(
        self,
        channel: Channel,
        timeout: float = 3.0,
        verbose: bool = False,
        unsafe: bool = False,
        hk_f1xx_offset: bool = False,
        expected_responses: bool = True,
    ):
        self._channel = channel
        # Connection host if the channel is host-based (WebSocket/TCP), else None
        # — the channel is the single source of truth (no duplicate on subclasses).
        self.host: str | None = getattr(channel, "host", None)
        self.timeout = timeout
        self.verbose = verbose
        self.unsafe = unsafe
        # Profile HK F1xx -1 identity-DID quirk (tolerate 62F187 for a 22F188
        # request); forwarded to parse_uds_response's echo validation.
        self.hk_f1xx_offset = hk_f1xx_offset
        # Learned expected-response-count digits, which let the adapter return as
        # soon as a reply is complete instead of waiting out its ATST budget. The
        # cache is per-connection, so a reconnect re-learns rather than trusting a
        # count measured on a link that has since been rebuilt.
        self._frame_counts = FrameCountCache(enabled=expected_responses)
        # The prompt ledger that attributes each reply to the command that earned
        # it — the primary defence against a desynchronised pipe. See
        # :mod:`canlib.transport.elm327_pipe` for why counting prompts is the only
        # per-exchange framing signal the ELM327 protocol offers.
        self._pipe = ResponsePipe()
        self.elm_timeout_cmd = "ATST96"  # current ELM327 timeout command
        self._cmd_lock = asyncio.Lock()  # serialize all ELM327 commands
        # The task currently holding `_cmd_lock`, so a multi-command *transaction*
        # (see `transaction()`) can nest send_command calls without deadlocking on
        # the non-reentrant asyncio.Lock.
        self._lock_owner: asyncio.Task | None = None
        # Guards against a resync recursing into itself: `_resync` sends its own
        # probe command, which must not re-trigger the dirty-pipe check.
        self._resyncing = False
        # Lightweight instrumentation: total ELM commands sent and time spent
        # waiting on them. Callers (e.g. the monitor) snapshot these to report
        # per-cycle command counts / ELM latency.
        self.cmd_count = 0
        self.cmd_time = 0.0
        # Per-(ECU, PID) round-trip timing (surfaced by `canair read --timings`).
        self.timings = TimingRecorder()
        # Live link round-trip estimate, fed from `_LINK_PROBE_CMDS` — the commands
        # the adapter answers without touching the bus, so the sample is the
        # network's time and not the car's. Sizes the resync quiet window; a
        # reconnect deliberately does *not* clear it, since the link that just
        # dropped is usually the same one coming back and the estimate is most
        # valuable precisely then.
        self.link = LinkLatency()
        # The adapter builds one CAN frame per request and never segments, so a
        # request longer than this is rejected outright rather than split.
        self.max_request_bytes = _ELM_MAX_REQUEST_BYTES
        # Per-exchange outcome tally (drops/errors/decode) — the sibling of
        # `timings` read by the monitor for its live status line and stamped into
        # recorded-capture provenance.
        self.diag = TransportStats(transport=getattr(channel, "transport_name", "elm327"))
        # Optional per-ECU response budget {tx_id: seconds}. When a read passes no
        # explicit timeout, the current header's budget (else self.timeout) applies.
        self.ecu_timeouts: dict[int, float] = {}
        # Cached ELM header state so repeated set_header() for the same ECU is a
        # no-op (headers only change on ECU switch). Kept coherent for any caller
        # by inspecting commands in _send_command_locked (ATSH/ATFCSH set it,
        # ATZ/ATD/ATWS reset it).
        self._cur_header: int | None = None
        self._cur_fc_header: int | None = None

    async def connect(self):
        """Open the underlying channel and reset ELM/session state."""
        await self._channel.connect()
        self._cur_header = None
        self._cur_fc_header = None
        self._pipe.reset()
        self._resyncing = False
        # A learned frame count measures one link. A reconnect may have re-homed
        # the session onto a different device, so forget them all rather than
        # trusting a count across the boundary.
        self._frame_counts.reset()

    async def close(self):
        """Close the underlying channel."""
        await self._channel.close()

    async def drain(self) -> None:
        """Discard any buffered/late adapter output (delegates to the channel).

        Exposed for modes that must clear stale frames before a delicate exchange
        (e.g. the SKM relay-wake) without reaching into the transport internals.
        """
        await self._channel.drain()

    async def recv_frame(self, timeout: float) -> str | None:
        """Read the next chunk of decoded adapter output, ``None`` on timeout.

        A *passive* receive — no command is sent. Used by modes that must collect
        additional frames an ECU emits *after* the initial response (the SKM
        relay-wake waits out flow-control/ResponsePending frames this way). The
        text is already decoded by the channel (any WebSocket JSON envelope
        stripped), so callers never touch the raw socket. Raises
        :class:`ConnectionError` if the link drops.
        """
        return await self._channel.recv(timeout)

    async def send_command(self, cmd: str, timeout: float | None = None) -> str:
        """Send an ELM327 command and wait for the response.

        There are two timeout levels:
        1. ELM327 ATST timeout (~614ms at ATST96) -- how long the ELM327 chip waits
           for an ECU response before returning "NO DATA". This governs actual CAN timing.
        2. Channel timeout (this parameter) -- max wait for the ELM327 chip to reply
           over the link. Only matters if the link stalls or NRC 0x78
           (ResponsePending) extends the exchange.

        All commands are serialized via an asyncio.Lock to prevent TesterPresent
        keepalive from colliding with user commands on the single ELM327 channel.

        Args:
            cmd: ELM327 command (without CR terminator)
            timeout: channel-level timeout in seconds (default: self.timeout)

        Returns:
            Raw response text (may contain multiple lines)

        Raises:
            ValueError: If the command is blocked by the safety check.
        """
        if timeout is None:
            timeout = self.timeout

        await enforce_command_safety(cmd, self.unsafe)

        # Already inside a transaction on this task: the lock is held, and
        # asyncio.Lock is not reentrant, so acquiring again would deadlock.
        if self._lock_owner is asyncio.current_task():
            return await self._send_command_locked(cmd, timeout)

        async with self._cmd_lock:
            self._lock_owner = asyncio.current_task()
            try:
                return await self._send_command_locked(cmd, timeout)
            finally:
                self._lock_owner = None

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Hold the command lock across several commands as one atomic exchange.

        ``_cmd_lock`` alone serialises individual *commands*, which is not enough:
        a UDS read is ``ATSH`` + ``ATFCSH`` + the request, and the background
        TesterPresent keepalive (a different task) could acquire the lock between
        them and re-point the header — sending the request to the wrong ECU, whose
        reply then fails echo validation and desynchronises the pipe. Callers that
        must not be interleaved wrap the whole sequence::

            async with terminal.transaction():
                await terminal.set_header(tx_id)
                resp = await terminal.send_uds(pid, ...)

        Nested ``send_command``/``send_uds`` calls on the same task reuse the held
        lock (see ``_lock_owner``). Keep genuinely independent work — e.g.
        ``keepalive_stale()`` — *outside* the transaction, so it isn't serialised
        behind an unrelated exchange.
        """
        if self._lock_owner is asyncio.current_task():
            # Already transacting on this task — reuse the outer transaction so
            # nesting is harmless.
            yield
            return
        async with self._cmd_lock:
            self._lock_owner = asyncio.current_task()
            try:
                yield
            finally:
                self._lock_owner = None

    def _elm_response_budget(self) -> float:
        """Seconds the adapter itself may spend waiting for an ECU, from ``ATST``.

        The ELM327 guarantees it emits *something* (data, ``NO DATA``, an error)
        within its ``ATST`` timeout, so this is the basis for deciding how long
        silence must last before the pipe is provably empty — see :meth:`_resync`.
        """
        cu = self.elm_timeout_cmd.upper().replace(" ", "")
        if cu.startswith("ATST"):
            try:
                return int(cu[4:], 16) * _ELM_ST_UNIT
            except ValueError:
                pass
        return _DEFAULT_ELM_BUDGET

    async def _resync(self, reason: str) -> None:
        """Realign the command/response pipe, or raise :class:`ConnectionError`.

        Called when the pipe is known to be offset — either the previous command
        left it dirty, or a reply failed echo/SID validation (proof that a *past*
        request's response was just consumed). Blindly draining is not enough: the
        window must outlast anything still in flight, or the late reply survives
        the drain and the offset becomes permanent (each subsequent stale reply
        arrives prompt-terminated, so the dirty-pipe flag never sets again).

        Soundness comes from the adapter's own contract rather than a tuned
        constant: a reply is due within ``ATST`` (:meth:`_elm_response_budget`), so
        silence for longer than that plus link latency means nothing is in flight.
        The link term is *measured* (:class:`~canlib.link_latency.LinkLatency`),
        with ``_LINK_LATENCY_MARGIN`` only as a floor — guessing it is what made
        this recovery fail on the hotspot/VPN path it was written for.
        A single state-free probe then confirms alignment — we require only that
        *a* prompt-terminated reply comes back, never specific text.

        Raises:
            ConnectionError: the probe never came back, so the pipe cannot be
                trusted. Live modes translate this into a full reconnect (rebuild
                the socket, re-init ELM, re-open sessions), which is the only
                remaining way to recover.
        """
        if self._resyncing:
            # The probe below is itself a command; never recurse.
            return
        self._resyncing = True
        # Not capped by `self.timeout`: that is a per-command budget tuned for a
        # LAN, and letting it veto a measured round trip is exactly how the drain
        # ended up shorter than the reply it was chasing. Capped by its own ceiling
        # instead, so a pathological measurement escalates to a reconnect rather
        # than stalling the poll loop.
        quiet = min(
            _RESYNC_QUIET_MAX,
            self._elm_response_budget() + self.link.allowance(_LINK_LATENCY_MARGIN),
        )
        try:
            await self._channel.drain(
                per_recv_timeout=quiet,
                max_seconds=max(_RESYNC_MAX_SECONDS, quiet),
            )
            # The drain discarded whatever the adapter still owed, so the prompt
            # ledger must be zeroed with it — otherwise the probe below would wait
            # for prompts that were just thrown away.
            self._pipe.clear_backlog()
            reply = await self._send_command_locked(_RESYNC_PROBE, quiet)
            if self._pipe.dirty or not reply.strip():
                raise ConnectionError(
                    f"ELM327 pipe resync failed ({reason}): "
                    f"no reply to {_RESYNC_PROBE} within {quiet:.1f}s"
                )
        finally:
            self._resyncing = False
        self.diag.record_resync(reason)
        if self.verbose:
            print(f"  [resync] pipe realigned after {reason}", file=sys.stderr)

    async def _send_command_locked(self, cmd: str, timeout: float) -> str:
        """Send command while holding the lock (internal)."""
        log_command(cmd)
        self._track_header(cmd)

        # A dirty pipe is normally resolved by prompt accounting: the abandoned
        # command's prompt is still owed, so this command waits for *its* prompt
        # and discards the late reply positively, with no timing guess. Fall back
        # to a drain-and-probe resync only when accounting has nothing to work
        # with — see `ResponsePipe.needs_resync`.
        if self._pipe.needs_resync():
            await self._resync("dirty pipe")

        self.cmd_count += 1
        _t0 = time.monotonic()
        await self._channel.send(cmd + "\r")

        deadline = time.monotonic() + timeout
        # The carry may already hold this command's whole reply, so harvest before
        # waiting on the channel at all. A ResponsePending frame restarts the
        # deadline: the ECU asked for more time, so the wait is not the request's.
        if self._pipe.begin():
            deadline = time.monotonic() + timeout

        while not self._pipe.satisfied:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            msg = await self._channel.recv(min(remaining, 1.0))
            if msg is None:
                continue
            if self._pipe.feed(msg):
                deadline = time.monotonic() + timeout

        read = self._pipe.finish()
        for block in read.stale:
            self.diag.record(
                CAT_STALE,
                detail=f"discarded a late reply to an earlier command: {compact(block)[:40]!r}",
            )

        raw = read.block
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        raw = re.sub(r"[\x00-\x09\x0b-\x1f]", "", raw)

        result = raw.strip()
        log_response(cmd, result)
        elapsed = time.monotonic() - _t0
        self.cmd_time += elapsed
        cu = cmd.replace(" ", "").upper()
        # Feed the link estimate only from commands the adapter answers by itself,
        # and only when a reply actually arrived — a timed-out command's elapsed is
        # the deadline, which says nothing about the link.
        if read.clean and cu.startswith(_LINK_PROBE_CMDS):
            self.link.observe(elapsed)
        # Record RTT for real UDS requests only (skip AT setup + 3E00 keepalives).
        if not cu.startswith("AT") and cu != "3E00" and self._cur_header is not None:
            self.timings.record(f"0x{self._cur_header:03X}", cu, elapsed)
        return result

    async def init_elm(self, init_string: str):
        """Send ELM327 initialization commands (``;``-separated AT commands)."""
        resp = await self.send_command("ATZ", timeout=3.0)
        if self.verbose:
            print(f"  [init] ATZ -> {resp!r}", file=sys.stderr)

        for cmd in init_string.rstrip(";").split(";"):
            cmd = cmd.strip()
            if not cmd:
                continue
            resp = await self.send_command(cmd)
            if self.verbose:
                print(f"  [init] {cmd} -> {resp!r}", file=sys.stderr)

    def _track_header(self, cmd: str) -> None:
        """Keep the cached ELM header state coherent with a command being sent.

        ATSH/ATFCSH set the (flow-control) header; ATZ/ATWS/ATD reset ELM
        defaults (clearing the header). A malformed header sets the cache to
        None so the next set_header() re-sends. This runs for *every* command,
        so direct sends (e.g. skm_wakeup) can't desync the cache.
        """
        cu = cmd.upper().replace(" ", "")

        def _hx(s: str) -> int | None:
            try:
                return int(s, 16)
            except ValueError:
                return None

        if cu.startswith("ATFCSH"):
            self._cur_fc_header = _hx(cu[6:])
        elif cu.startswith("ATSH"):
            self._cur_header = _hx(cu[4:])
        elif cu in ("ATZ", "ATWS", "ATD"):
            self._cur_header = None
            self._cur_fc_header = None

    async def set_header(self, tx_id: int):
        """Set the ELM327 header (target ECU TX ID).

        Cached: the ATSH/ATFCSH pair is skipped when the header is already set
        to ``tx_id``, so polling many PIDs on one ECU (or re-selecting the same
        ECU across cycles) costs zero header round-trips after the first.
        """
        hex_id = f"{tx_id:03X}"
        if self._cur_header != tx_id:
            resp = await self.send_command(f"ATSH{hex_id}")
            if self.verbose:
                print(f"  [header] ATSH{hex_id} -> {resp!r}", file=sys.stderr)

        if self._cur_fc_header != tx_id:
            resp = await self.send_command(f"ATFCSH{hex_id}")
            if self.verbose:
                print(f"  [header] ATFCSH{hex_id} -> {resp!r}", file=sys.stderr)

    async def send_uds(
        self,
        service_pid: str,
        timeout: float | None = None,
        expected_sid: int | None = None,
        expected_did: int | None = None,
        expected_echo: bytes | None = None,
        retries: int = 0,
    ) -> UdsResponse:
        """Send a UDS request and parse the response.

        Args:
            service_pid: UDS request hex string (e.g., "2101", "22C00B")
            timeout: channel-level timeout in seconds (see send_command docstring)
            expected_sid: If set, the parser validates the response echoes this SID
                (catches stale frames from previous requests).
            expected_did: If set along with ``expected_sid``, the parser also
                validates the DID echo in bytes 1..2 of the positive response.
            expected_echo: Variable-width identifier echo (see
                ``parse_uds_response``); validates the 1-byte service-21 PID or
                2-byte service-22 DID a positive response must repeat.
            retries: Re-send on a *non-answer* (timeout / NO DATA / transport
                error) up to this many extra times. A definitive negative
                response (NRC) or a positive response is returned immediately —
                only silence is retried. The Ioniq's first request after idle
                often times out (see profile note); one retry recovers it.
                A reply that fails echo/SID validation is retried too, but only
                after the pipe has been realigned (see :meth:`_resync`) — a plain
                re-send into an offset pipe just draws the next stale reply.

        Returns:
            Parsed response dict from parse_uds_response()
        """
        if timeout is None:
            # Per-ECU budget for the current header, else the client default.
            timeout = self.ecu_timeouts.get(self._cur_header, self.timeout)
        attempt = 0
        # The header cannot move under us here: `transaction()` locks out the
        # keepalive, `send_uds` never re-points it, and `_resync` probes with a
        # command `_track_header` does not treat as a reset.
        counting = self._frame_counts.attempt((self._cur_header, service_pid))
        # One transaction for the whole retry sequence: a keepalive slipping in
        # between attempts would re-point the header and invalidate the retry.
        async with self.transaction():
            while True:
                command = counting.command(service_pid)
                raw = await self.send_command(command, timeout=timeout)
                resp = parse_uds_response(
                    raw,
                    expected_sid=expected_sid,
                    expected_did=expected_did,
                    expected_echo=expected_echo,
                    hk_f1xx_offset=self.hk_f1xx_offset,
                )
                self.diag.record_response(
                    resp,
                    ecu=(f"0x{self._cur_header:03X}" if self._cur_header is not None else None),
                    pid=service_pid,
                )

                verdict = counting.verdict(resp)
                if verdict.retry_plain:
                    # `_resync` is idempotent and skips when already realigning, so
                    # a drop mid-recovery cannot recurse. The retry is not funded
                    # from `retries`, so the optimization can cost latency but
                    # never a reading.
                    if verdict.realign:
                        await self._resync(f"{resp.get('error_kind')} response to {command}")
                    if self.verbose:
                        print(f"  [count] {command}: {verdict.note}", file=sys.stderr)
                    continue

                if resp.get("error_kind") in _RESYNC_ON:
                    # Two different proofs that the pipe holds something that is
                    # not this request's answer:
                    #
                    # - `stale`: validation caught an *older request's* reply, so a
                    #   newer one is still queued. Leaving the offset in place is
                    #   the original bug — every later reply arrives
                    #   prompt-terminated, so the dirty-pipe flag never trips
                    #   again and the session never recovers.
                    # - `decode`: the adapter sent bytes that are not a UDS
                    #   response at all — a connect banner, an `?`, line noise.
                    #   Prompt accounting cannot catch this one, because a banner
                    #   carries its own prompt and so looks like a paid debt; only
                    #   its content gives it away.
                    #
                    # Realign unconditionally, even when not retrying: the caller
                    # may never send another request on this PID.
                    #
                    # `no_data` deliberately does *not* qualify. An ECU that simply
                    # did not answer is the normal case during a scan, and draining
                    # on every miss would cost a probe round trip per unmapped PID.
                    await self._resync(f"{resp.get('error_kind')} response to {service_pid}")

                if resp.get("ok") or resp.get("nrc") is not None or attempt >= retries:
                    return resp
                attempt += 1
                if self.verbose:
                    print(
                        f"  [retry] {service_pid}: {resp.get('error', 'no response')}"
                        f" — retry {attempt}/{retries}",
                        file=sys.stderr,
                    )

    async def enter_extended_session(
        self, wake: bool = False, mode: str = "03"
    ) -> tuple[bool, asyncio.Task | None]:
        """Enter a diagnostic session and start the TesterPresent keepalive.

        Required by :class:`canlib.transport.protocol.Terminal`; the ritual itself
        lives in :mod:`canlib.transport.elm327_session`, since it is UDS-level
        policy rather than part of speaking ELM327.

        Returns:
            ``(success, tester_task)`` — the caller owns cancelling the task.
        """
        return await enter_extended_session(self, wake=wake, mode=mode)
