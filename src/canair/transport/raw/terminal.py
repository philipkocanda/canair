"""``RawTerminal`` — a WiCANTerminal-compatible adapter over raw CAN (SLCAN + ISO-TP).

Presents the small surface the live modes use on a ``WiCANTerminal``
(``set_header`` / ``send_uds`` / ``send_command`` / ``enter_extended_session`` /
``close``) but drives the bus with python-can + client-side ISO-TP. This lets the
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
from collections.abc import AsyncIterator

import can
import isotp

from canair.safety.blocklist import SessionPolicy, enforce_command_safety
from canair.transport import keepalive
from canair.transport.ecu_log import log_response
from canair.transport.functional import (
    CAN_FD_REFUSAL,
    DEFAULT_WINDOW_S,
    FunctionalResult,
    functional_request_bytes,
    record_functional_losses,
)
from canair.transport.raw.functional import raw_functional_exchange
from canair.transport.raw.isotp_stack import build_isotp_stack
from canair.transport.raw.uds_client import PENDING_RECV_TIMEOUT, PENDING_TOTAL_TIMEOUT
from canair.transport.stats import TransportStats
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
from canair.uds.frame_counts import FrameCountLedger, frames_for_payload
from canair.uds.nrc import is_response_pending
from canair.uds.parse import parse_uds_response
from canair.uds.response import UdsResponse
from canair.uds.services import DEFAULT_SESSION_REQUEST, TESTER_PRESENT_REQUEST

logging.getLogger("isotp").setLevel(logging.ERROR)


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
        addr_map: dict[int, EcuAddress] | None = None,
        rx_offset: int = DEFAULT_RX_OFFSET,
        mode: AddressingMode = DEFAULT_MODE,
    ):
        from canair.transport.isotp_params import ISOTP_MAX_REQUEST_BYTES, build_isotp_params
        from canair.transport.raw.slcan_tcp import SlcanTcpBus

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

    # -- WiCANTerminal-compatible surface -----------------------------------
    async def connect(self) -> None:  # bus already opened in __init__
        return None

    async def init_elm(self, *_a, **_k) -> None:  # no ELM to initialise
        return None

    async def set_header(self, tx_id: int) -> None:
        self._cur = tx_id

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """No-op: the raw path has no shared header state to protect.

        Every ISO-TP exchange carries its own addressing, so there is nothing a
        concurrent keepalive could retarget (the ELM327 counterpart must serialise
        ``ATSH`` + request — see ``Elm327Terminal.transaction``).
        """
        yield

    async def send_uds(
        self,
        service_pid: str,
        timeout: float | None = None,
        expected_sid: int | None = None,
        expected_echo: bytes | None = None,
        retries: int = 0,
    ) -> UdsResponse:
        await enforce_command_safety(service_pid, self.unsafe, self.session_policy, self._cur)
        try:
            req = bytes.fromhex(service_pid.replace(" ", ""))
        except ValueError:
            return parse_uds_response("?")
        attempt = 0
        while True:
            resp_bytes = await self._exchange(req, timeout)
            raw = "NO DATA" if resp_bytes is None else resp_bytes.hex().upper()
            log_response(service_pid, raw)
            resp = parse_uds_response(
                raw,
                expected_sid=expected_sid,
                expected_echo=expected_echo,
                protocol=self.session_policy.protocol(self._cur),
            )
            if resp_bytes is not None and self._classic_can:
                # parse_uds_response counts response *lines*, and this path hands it
                # exactly one — the already-reassembled message — so its count is
                # always 1. The true figure is what the ISO-TP layer put on the bus,
                # which the payload length fixes.
                resp["isotp_frame_count"] = frames_for_payload(len(resp_bytes))
                if resp.get("ok"):
                    self.frame_counts.observe((self._cur, service_pid), resp["isotp_frame_count"])
            self.diag.record_response(
                resp,
                ecu=(f"0x{self._cur:03X}" if self._cur is not None else None),
                pid=service_pid,
            )
            # Retry only a non-answer (NO DATA / timeout); an NRC is definitive.
            if resp.get("ok") or resp.get("nrc") is not None or attempt >= retries:
                return resp
            attempt += 1

    async def send_command(self, cmd: str, timeout: float | None = None) -> str:
        """AT commands are a no-op ('OK'); UDS hex is sent and returned as hex."""
        await enforce_command_safety(cmd, self.unsafe, self.session_policy, self._cur)
        c = cmd.strip()
        if c.upper().startswith("AT"):
            return "OK"
        try:
            req = bytes.fromhex(c.replace(" ", ""))
        except ValueError:
            return "?"
        resp = await self._exchange(req, timeout)
        return "NO DATA" if resp is None else resp.hex().upper()

    async def send_functional(
        self, tx_id: int, request: str, window: float = DEFAULT_WINDOW_S
    ) -> FunctionalResult:
        """Broadcast ``request`` on the functional id ``tx_id`` and collect every reply.

        Raises :class:`~canair.safety.blocklist.SafetyBlocked` for anything but an allowed
        read (:func:`~canair.safety.blocklist.check_functional_request`), and ValueError on
        a CAN-FD profile (the request and its flow control are classic frames).
        """
        req = functional_request_bytes(tx_id, request)
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

    async def enter_extended_session(
        self, wake: bool = False, mode: str = "03"
    ) -> tuple[bool, asyncio.Task | None]:
        """Enter a diagnostic session (default 10 03) on the current ECU + keepalive.

        Mirrors WiCANTerminal.enter_extended_session; the TesterPresent loop
        targets the ECU that was current at entry.

        Args:
            wake: If True, send a default session request (10 01) first to wake the
                ECU from deep sleep before entering the session.
            mode: DiagnosticSessionControl sub-function (hex, no 0x). Default
                ``"03"`` (UDS extendedDiagnosticSession); use ``"81"`` for the
                KWP2000 standardDiagnosticSession on ECUs that reject 10 03.
        """
        mode = mode.upper().removeprefix("0X").zfill(2)
        tx = self._cur
        # set_header must have run before a session is entered (send_uds below
        # would raise otherwise), so the keepalive target is always known.
        assert tx is not None
        if wake:
            await self.send_uds(DEFAULT_SESSION_REQUEST, timeout=3.0)
            await asyncio.sleep(0.3)
        resp = await self.send_uds(f"10{mode}", timeout=3.0)
        if resp.get("ok"):
            print(f"  Session (10 {mode}) established.")
        elif resp.get("nrc") is not None:
            print(f"  WARNING: session NRC 0x{resp['nrc']:02X} ({resp['nrc_desc']}) — continuing.")

        async def _tester_loop():
            try:
                while True:
                    await asyncio.sleep(2.0)
                    with _suppress():
                        reply = await self._exchange_tx(
                            tx, bytes.fromhex(TESTER_PRESENT_REQUEST), 1.5
                        )
                        if keepalive.unsupported_bytes(reply):
                            keepalive.report_unsupported(tx, transport=self.diag.transport)
                            return
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(_tester_loop())
        return resp.get("ok", False), task

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
                self._params,
                fc_id=addr.fc_id,
            )
            st.start()
            self._stacks[tx_id] = st
            time.sleep(0.05)  # brief settle for a freshly-started stack
        return st

    async def _exchange(self, req: bytes, timeout: float | None):
        if self._cur is None:
            raise RuntimeError("RawTerminal.send_uds called before set_header")
        return await self._exchange_tx(self._cur, req, timeout)

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
            r = st.recv(block=True, timeout=t)
            if r is None:
                return None
            r = bytes(r)
            # Wait through UDS ResponsePending (0x78) so slow services (DTC reads,
            # routines) return their final answer instead of the "still working"
            # placeholder — matching the ELM327 path.
            pending_deadline = time.monotonic() + PENDING_TOTAL_TIMEOUT
            while is_response_pending(r) and time.monotonic() < pending_deadline:
                nxt = st.recv(block=True, timeout=PENDING_RECV_TIMEOUT)
                if nxt is None:
                    break
                r = bytes(nxt)
            return r

        self.cmd_count += 1
        t0 = time.monotonic()
        try:
            return await asyncio.get_event_loop().run_in_executor(None, _io)
        finally:
            elapsed = time.monotonic() - t0
            self.cmd_time += elapsed
            # Record RTT for real UDS requests only (skip 3E00 keepalives).
            if req != b"\x3e\x00":
                self.timings.record(f"0x{tx_id:03X}", req.hex().upper(), elapsed)


class _suppress:
    """contextlib.suppress(Exception) without importing contextlib per-call."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True
