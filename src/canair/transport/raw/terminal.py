"""``RawTerminal`` — a WiCANTerminal-compatible adapter over raw CAN (SLCAN + ISO-TP).

Presents the small surface the live modes use on a ``WiCANTerminal``
(``set_header`` / ``transaction`` / ``send_uds`` / ``send_command`` / ``close``) but drives
the bus with python-can + client-side ISO-TP. This lets the
existing ELM-path modes (scan, discover, identity, iocontrol, routines, and the
*-scan probers) run unchanged over the ``slcan-tcp`` transport.

One ISO-TP stack is created lazily per target ECU over a shared Notifier. Each
ECU's full addressing (mode + RX + any extended target/source bytes + a
flow-control override) comes from an ``addr_map`` entry (resolved by the
registry); an unknown TX id (a discovery sweep) falls back to the profile's
``rx_offset``/``mode``. Responses are formatted back through
:func:`parse_uds_response` so the returned dict is byte-for-byte the same shape
the modes already expect (ok / hex / bytes / nrc / nrc_desc / error), including
SID/DID echo validation.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Mapping
from typing import Any

import can
import isotp

from canair.safety.blocklist import SessionPolicy, enforce_command_safety
from canair.transport.ecu_log import log_response
from canair.transport.functional import (
    CAN_FD_REFUSAL,
    DEFAULT_WINDOW_S,
    FunctionalResult,
    functional_request_bytes,
    record_functional_losses,
)
from canair.transport.isotp_params import ISOTP_MAX_REQUEST_BYTES, build_isotp_params
from canair.transport.raw.functional import raw_functional_exchange
from canair.transport.raw.isotp_stack import build_isotp_stack
from canair.transport.raw.slcan_tcp import SlcanTcpBus
from canair.transport.stats import TransportStats
from canair.transport.timeouts import (
    PENDING_RECV_TIMEOUT,
    PENDING_TOTAL_TIMEOUT,
    busy_retry_delay,
)
from canair.transport.timing import TimingRecorder
from canair.uds.addressing import (
    DEFAULT_MODE,
    DEFAULT_RX_OFFSET,
    AddressingMode,
    EcuAddress,
    build_isotp_address,
    physical_request_id,
    resolve_rx,
)
from canair.uds.echo import positive_response_suppressed
from canair.uds.frame_counts import FrameCountLedger, count_key, frames_for_payload
from canair.uds.nrc import is_negative_response, is_response_pending
from canair.uds.parse import parse_uds_response
from canair.uds.response import UdsResponse
from canair.uds.services import TESTER_PRESENT_REQUEST

logging.getLogger("isotp").setLevel(logging.ERROR)

# How long a request with the suppress-positive-response bit waits for an NRC: P2server
# (50 ms by ISO 14229-2) with room for the bus and the SLCAN gateway.
SUPPRESSED_NRC_WAIT_S = 0.25


def _recv_answer(stack, sid: int, deadline: float) -> bytes | None:
    """The next message on ``stack`` that may answer a request on ``sid``, by ``deadline``.

    A negative response that names another service answers an earlier request, so it
    is skipped rather than returned (or waited through, if it is a ResponsePending).
    """
    while True:
        msg = stack.recv(block=True, timeout=max(0.0, deadline - time.monotonic()))
        if msg is None:
            return None
        reply = bytes(msg)
        if not is_negative_response(reply) or is_negative_response(reply, sid):
            return reply


class RawTerminal:
    """Raw-CAN drop-in for WiCANTerminal (SLCAN over TCP + client-side ISO-TP)."""

    def __init__(
        self,
        host: str,
        port: int,
        bitrate: int = 500_000,
        *,
        verbose: bool = False,
        unsafe: bool = False,
        timeout: float = 2.0,
        isotp_config: dict | None = None,
        ecu_isotp: Mapping[int, Mapping[str, Any]] | None = None,
        addr_map: dict[int, EcuAddress] | None = None,
        rx_offset: int = DEFAULT_RX_OFFSET,
        mode: AddressingMode = DEFAULT_MODE,
    ):
        self.host = host
        self.verbose = verbose
        self.unsafe = unsafe
        # Each ECU's session protocol + the profile's quirk allowances
        # (canair.safety.blocklist.SessionPolicy); set by whoever builds the terminal from a
        # profile. The default knows no ECU, so the stricter reading applies.
        self.session_policy = SessionPolicy()
        self.timeout = timeout
        # Full per-ECU addressing (from the registry: mode + RX + any extended
        # target/source bytes + flow-control override). An unknown TX id (e.g. a
        # discovery sweep) falls back to the profile's rx_offset/mode below.
        self.addr_map: dict[int, EcuAddress] = addr_map or {}
        self.rx_offset = rx_offset
        # Profile-default addressing mode for TX ids not in addr_map.
        self.mode = mode
        # Parity attributes some callers read.
        self.cmd_count = 0
        self.cmd_time = 0.0
        self.elm_timeout_cmd = ""
        # Per-(ECU, PID) round-trip timing (surfaced by `canair read --timings`).
        self.timings = TimingRecorder()
        # We run ISO-TP ourselves, so a long request is segmented rather than
        # refused; the ceiling is ISO-TP's own single-message limit.
        self.max_request_bytes = ISOTP_MAX_REQUEST_BYTES
        # This transport drives python-can directly, so it can put a crafted broadcast
        # frame on the bus (send_frame); an ELM327 cannot.
        self.sends_raw_frames = True
        # Per-exchange outcome tally (drops/errors/decode), same as WiCANTerminal
        # and RawUdsClient — read by the monitor + stamped into capture provenance.
        self.diag = TransportStats(transport="slcan-tcp")
        # Optional per-ECU response budget {tx_id: seconds} (see canair.transport.timeouts).
        self.ecu_timeouts: dict[int, float] = {}

        self.bus = SlcanTcpBus(host, port=port, bitrate=bitrate)
        # Measured at the TCP handshake, so the flow-control budgets below and
        # every per-request deadline can account for the network's share.
        self.link = self.bus.link
        self.notifier = can.Notifier(self.bus, [], timeout=0.1)
        self._params = build_isotp_params(isotp_config, self.link.budget)
        # An ECU with its own `isotp:` block gets its stack built from both
        # (canair.transport.isotp_params.ECU_ISOTP_FIELDS); every other one from _params.
        self._ecu_params = {
            tx: build_isotp_params(isotp_config, self.link.budget, block)
            for tx, block in (ecu_isotp or {}).items()
        }
        # A classic-CAN frame carries at most 7 payload bytes, which fixes the frame
        # count a given response length occupies. On CAN-FD (up to 64) it does not,
        # so the count is simply not derived rather than guessed wrong.
        self._classic_can = (
            not self._params.get("can_fd") and self._params.get("tx_data_length", 8) == 8
        )
        # What this session observed about response frame counts. The raw path runs
        # ISO-TP itself so it has no adapter to hint, but the *fact* is the same one
        # an ELM327 measures, and persisting it is what lets the generated AutoPID
        # profile skip the device's own wait.
        self.frame_counts = FrameCountLedger()
        self._stacks: dict[int, isotp.NotifierBasedCanStack] = {}
        self._cur: int | None = None
        # The ECU a request goes to is `_cur`, set by `set_header`: shared state, the
        # same as an ELM327 header. One lock guards it and the exchange, so another task
        # can neither re-point a request nor read from the same stack mid-exchange.
        self._lock = asyncio.Lock()
        self._lock_owner: asyncio.Task | None = None

    # -- WiCANTerminal-compatible surface -----------------------------------
    async def connect(self) -> None:  # bus already opened in __init__
        return None

    async def init_elm(self, *_a, **_k) -> None:  # no ELM to initialise
        return None

    async def set_header(self, tx_id: int) -> None:
        async with self.transaction():
            self._cur = tx_id

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None]:
        """Hold the terminal across several calls as one exchange.

        The raw path addresses every frame explicitly, but *which* ECU a request goes
        to is ``_cur``, set by :meth:`set_header`. Without the lock a concurrent task (a
        keepalive) could re-point it between a caller's ``set_header`` and its request,
        or read the reply off the same ISO-TP stack. Re-entrant per task, like
        ``Elm327Terminal.transaction``; each request holds it on its own as well.
        """
        if self._lock_owner is asyncio.current_task():
            yield
            return
        async with self._lock:
            self._lock_owner = asyncio.current_task()
            try:
                yield
            finally:
                self._lock_owner = None

    async def send_uds(
        self,
        service_pid: str,
        timeout: float | None = None,
        expected_sid: int | None = None,
        expected_echo: bytes | None = None,
        retries: int = 0,
    ) -> UdsResponse:
        async with self.transaction():
            # Read once: the safety check, every attempt and the bookkeeping all name
            # the ECU this request was meant for, even across the `--unsafe` prompt.
            tx = self._cur
            await enforce_command_safety(service_pid, self.unsafe, self.session_policy, tx)
            try:
                req = bytes.fromhex(service_pid.replace(" ", ""))
            except ValueError:
                return parse_uds_response("?")
            protocol = self.session_policy.protocol(tx)
            if timeout is None and positive_response_suppressed(service_pid, protocol):
                # Only an NRC can come back, within P2: no need to wait out a reply budget.
                timeout = SUPPRESSED_NRC_WAIT_S + (self.link.budget or 0.0)
            attempt = busy = 0
            while True:
                resp_bytes = await self._exchange(tx, req, timeout)
                raw = "NO DATA" if resp_bytes is None else resp_bytes.hex().upper()
                log_response(service_pid, raw)
                resp = parse_uds_response(
                    raw,
                    expected_sid=expected_sid,
                    expected_echo=expected_echo,
                    protocol=protocol,
                    request=service_pid,
                )
                if resp_bytes is not None and self._classic_can:
                    # parse_uds_response counts response *lines*, and this path hands it
                    # exactly one — the already-reassembled message — so its count is
                    # always 1. The true figure is what the ISO-TP layer put on the bus,
                    # which the payload length fixes.
                    resp["isotp_frame_count"] = frames_for_payload(len(resp_bytes))
                    if resp.get("ok"):
                        self.frame_counts.observe(
                            count_key(tx, service_pid, protocol), resp["isotp_frame_count"]
                        )
                self.diag.record_response(
                    resp,
                    ecu=(f"0x{tx:03X}" if tx is not None else None),
                    pid=service_pid,
                    tx_id=tx,
                )
                delay = busy_retry_delay(resp, service_pid, protocol, busy)
                if delay is not None:
                    busy += 1
                    await asyncio.sleep(delay)
                    continue
                # Retry only a non-answer (NO DATA / timeout); any other NRC is definitive.
                if resp.get("ok") or resp.get("nrc") is not None or attempt >= retries:
                    return resp
                attempt += 1

    async def send_command(self, cmd: str, timeout: float | None = None) -> str:
        """AT commands are a no-op ('OK'); UDS hex is sent and returned as hex."""
        async with self.transaction():
            tx = self._cur
            await enforce_command_safety(cmd, self.unsafe, self.session_policy, tx)
            c = cmd.strip()
            if c.upper().startswith("AT"):
                return "OK"
            try:
                req = bytes.fromhex(c.replace(" ", ""))
            except ValueError:
                return "?"
            resp = await self._exchange(tx, req, timeout)
            return "NO DATA" if resp is None else resp.hex().upper()

    async def send_functional(
        self, tx_id: int, request: str, window: float = DEFAULT_WINDOW_S
    ) -> FunctionalResult:
        """Broadcast ``request`` on the functional id ``tx_id`` and collect every reply.

        Raises :class:`~canair.safety.blocklist.SafetyBlocked` for anything but an allowed
        read (:func:`~canair.safety.blocklist.check_functional_request`), and ValueError on
        a CAN-FD profile (the request and its flow control are classic frames).
        """
        req = functional_request_bytes(tx_id, request, self.session_policy)
        if not self._classic_can:
            raise ValueError(CAN_FD_REFUSAL)
        result = await raw_functional_exchange(
            self.bus,
            self.notifier,
            tx_id=tx_id,
            request=req,
            padding=int(self._params["tx_padding"]),
            stmin=int(self._params["stmin"]),
            window=window,
            flow_control_to=self._functional_fc_target,
        )
        self.cmd_count += 1
        record_functional_losses(self.diag, result)
        return result

    async def send_frame(self, can_id: int, data: bytes) -> None:
        """Put one crafted broadcast frame on the bus (classic CAN, no reply collected).

        For a guarded profile action that injects a non-diagnostic frame. Raises
        ``ValueError`` on a CAN-FD profile (the frame is a classic 8-byte frame), as
        the functional path does.
        """
        if not self._classic_can:
            raise ValueError(CAN_FD_REFUSAL)
        self.bus.send(
            can.Message(
                arbitration_id=can_id,
                data=bytes(data),
                is_extended_id=can_id > 0x7FF,
            )
        )
        self.cmd_count += 1

    def _functional_fc_target(self, rx_id: int) -> int | None:
        """Where a functional responder's flow control goes (None: its stack sends it).

        A stack canair already runs for the ECU listening on ``rx_id`` answers the
        First Frame itself. Stacks are keyed by request id, which is not where flow
        control goes when the ECU has an ``fc_id``, so match on what each listens to.
        """
        for stack_tx in self._stacks:
            addr = self.addr_map.get(stack_tx)
            listens = (
                addr.rx_id
                if addr is not None
                else resolve_rx(stack_tx, rx_offset=self.rx_offset, mode=self.mode)
            )
            if listens == rx_id:
                return None
        return physical_request_id(rx_id, self.addr_map, self.rx_offset, self.mode)

    @contextlib.asynccontextmanager
    async def short_ecu_wait(self) -> AsyncGenerator[None]:
        """No adapter wait to shorten: each request's timeout is canair's own here."""
        yield

    async def close(self) -> None:
        for st in self._stacks.values():
            with _suppress():
                st.stop()
        with _suppress():
            self.notifier.stop()
        with _suppress():
            self.bus.shutdown()

    # -- internals ----------------------------------------------------------
    def _stack(self, tx_id: int) -> isotp.NotifierBasedCanStack:
        st = self._stacks.get(tx_id)
        if st is None:
            addr = self.addr_map.get(tx_id)
            if addr is None:
                # Unknown ECU (discovery sweep): resolve RX from the profile
                # defaults, no extended/flow-control bytes.
                rx_id = resolve_rx(tx_id, rx_offset=self.rx_offset, mode=self.mode)
                addr = EcuAddress(tx_id, rx_id, self.mode)
            st = build_isotp_stack(
                self.bus,
                self.notifier,
                build_isotp_address(addr),
                self._ecu_params.get(tx_id, self._params),
                fc_id=addr.fc_id,
            )
            st.start()
            self._stacks[tx_id] = st
            time.sleep(0.05)  # brief settle for a freshly-started stack
        return st

    async def _exchange(self, tx_id: int | None, req: bytes, timeout: float | None):
        if tx_id is None:
            raise RuntimeError("RawTerminal.send_uds called before set_header")
        return await self._exchange_tx(tx_id, req, timeout)

    async def _exchange_tx(self, tx_id: int, req: bytes, timeout: float | None):
        # An explicit timeout is an instruction; a budget is the ECU's think time
        # plus the network's measured share (see LinkLatency).
        t = (
            timeout
            if timeout is not None
            else self.ecu_timeouts.get(tx_id, self.timeout) + (self.link.budget or 0.0)
        )

        def _io():
            # Create/settle the ISO-TP stack in the executor thread so the one-time
            # blocking settle never stalls the event loop.
            st = self._stack(tx_id)
            while st.available():
                st.recv()
            st.send(req)
            deadline = time.monotonic() + t
            r = _recv_answer(st, req[0], deadline)
            if r is None:
                return None
            # Wait through UDS ResponsePending (0x78) so slow services (DTC reads,
            # routines) return their final answer instead of the "still working"
            # placeholder — matching the ELM327 path.
            pending_cap = time.monotonic() + PENDING_TOTAL_TIMEOUT
            while is_response_pending(r, req[0]) and time.monotonic() < pending_cap:
                nxt = _recv_answer(st, req[0], time.monotonic() + PENDING_RECV_TIMEOUT)
                if nxt is None:
                    break
                r = nxt
            return r

        self.cmd_count += 1
        t0 = time.monotonic()
        try:
            return await asyncio.get_event_loop().run_in_executor(None, _io)
        finally:
            elapsed = time.monotonic() - t0
            self.cmd_time += elapsed
            # Record RTT for real UDS requests only (skip 3E00 keepalives).
            if req.hex().upper() != TESTER_PRESENT_REQUEST:
                self.timings.record(f"0x{tx_id:03X}", req.hex().upper(), elapsed)


class _suppress:
    """contextlib.suppress(Exception) without importing contextlib per-call."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True
