"""``RawClientTerminal`` — the session surface of a ``RawUdsClient``, for SessionManager.

The raw monitor polls through :class:`~canair.transport.raw.uds_client.RawUdsClient`, a
blocking, pipelined client keyed by ECU name, and has no ``Terminal``. Opening, waking and
keeping its sessions alive is still :class:`~canair.transport.session_manager.SessionManager`'s
job, so this adapter gives it the calls it makes (``set_header``, ``transaction``,
``send_uds``, ``send_command``, ``short_ecu_wait``) over ``RawUdsClient.read``, run off the
event loop. Nothing else goes through it: polling stays on the client's own pipelined path.

The client allows one exchange per ECU at a time and locks each ECU for it, so a read here may
run while the monitor's ``poll()`` runs on another thread: it waits for the poll's request to
the same ECU, if any (``plans/2026-10-27-raw-owed-ledger-blackout.md``, §2). Each read first
gives its ECU a stack (``ensure_ecu``), so a session on an ECU that is not polled works too.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable
from typing import Protocol

from canair.safety.blocklist import SessionPolicy
from canair.transport.functional import FunctionalResult
from canair.transport.isotp_params import ISOTP_MAX_REQUEST_BYTES
from canair.transport.stats import TransportStats
from canair.transport.timing import TimingRecorder
from canair.uds.addressing import EcuAddress
from canair.uds.frame_counts import FrameCountLedger
from canair.uds.parse import parse_uds_response
from canair.uds.response import UdsResponse


class _Client(Protocol):
    diag: TransportStats
    timings: TimingRecorder
    session_policy: SessionPolicy

    def read(self, ecu: str, request: bytes, timeout: float | None = None) -> bytes: ...

    def ensure_ecu(self, name: str, address: EcuAddress) -> None: ...


#: The ECU a request id belongs to: its name in the client, and its address.
EcuLookup = Callable[[int], tuple[str, EcuAddress] | None]


class RawClientTerminal:
    """``SessionManager``'s view of a raw pipelined client; ``ecu_for`` maps a TX id to an ECU."""

    def __init__(self, client: _Client, ecu_for: EcuLookup) -> None:
        self.client = client
        self._ecu_for = ecu_for
        self.frame_counts = FrameCountLedger()
        self.max_request_bytes = ISOTP_MAX_REQUEST_BYTES
        # The monitor never injects a broadcast frame; an action (which can) runs over
        # RawTerminal, not this pipelined client.
        self.sends_raw_frames = False
        self._cur: int | None = None
        self._lock = asyncio.Lock()
        self._lock_owner: asyncio.Task | None = None

    # The client's own, read when used: the adapter is built before anything is sent.
    @property
    def diag(self) -> TransportStats:
        return self.client.diag

    @diag.setter
    def diag(self, value: TransportStats) -> None:
        self.client.diag = value

    @property
    def timings(self) -> TimingRecorder:
        return self.client.timings

    @timings.setter
    def timings(self, value: TimingRecorder) -> None:
        self.client.timings = value

    @property
    def session_policy(self) -> SessionPolicy:
        return self.client.session_policy

    @session_policy.setter
    def session_policy(self, value: SessionPolicy) -> None:
        self.client.session_policy = value

    async def set_header(self, tx_id: int) -> None:
        self._cur = tx_id

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None]:
        if self._lock_owner is asyncio.current_task():
            yield
            return
        async with self._lock:
            self._lock_owner = asyncio.current_task()
            try:
                yield
            finally:
                self._lock_owner = None

    @contextlib.asynccontextmanager
    async def short_ecu_wait(self) -> AsyncGenerator[None]:
        """No adapter wait to shorten: each request's timeout is canair's own here."""
        yield

    async def _read(self, request: str, timeout: float | None) -> bytes | None:
        tx = self._cur
        found = self._ecu_for(tx) if tx is not None else None
        if found is None:
            raise RuntimeError(f"no ECU in the monitor answers to 0x{tx or 0:03X}")
        ecu, address = found
        client = self.client

        def _exchange() -> bytes:
            client.ensure_ecu(ecu, address)
            return client.read(ecu, bytes.fromhex(request), timeout)

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _exchange)
        except TimeoutError:
            return None

    async def send_uds(
        self,
        service_pid: str,
        timeout: float | None = None,
        expected_sid: int | None = None,
        expected_echo: bytes | None = None,
        retries: int = 0,
    ) -> UdsResponse:
        async with self.transaction():
            attempt = 0
            while True:
                reply = await self._read(service_pid, timeout)
                resp = parse_uds_response(
                    "NO DATA" if reply is None else reply.hex().upper(),
                    expected_sid=expected_sid,
                    expected_echo=expected_echo,
                    protocol=self.session_policy.protocol(self._cur),
                    request=service_pid,
                )
                if resp.get("ok") or resp.get("nrc") is not None or attempt >= retries:
                    return resp
                attempt += 1

    async def send_command(self, cmd: str, timeout: float | None = None) -> str:
        if cmd.strip().upper().startswith("AT"):
            return "OK"
        async with self.transaction():
            reply = await self._read(cmd.replace(" ", ""), timeout)
        return "NO DATA" if reply is None else reply.hex().upper()

    async def send_functional(
        self, tx_id: int, request: str, window: float = 0.3
    ) -> FunctionalResult:
        raise NotImplementedError("a functional request goes through RawTerminal, not the monitor")

    async def send_frame(self, can_id: int, data: bytes) -> None:
        raise NotImplementedError("a frame injection goes through RawTerminal, not the monitor")

    async def close(self) -> None:
        """Nothing to close: the client belongs to the monitor."""
