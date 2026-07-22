"""``RawTerminal`` — a WiCANTerminal-compatible adapter over raw CAN (SLCAN + ISO-TP).

Presents the small surface the live modes use on a ``WiCANTerminal``
(``set_header`` / ``send_uds`` / ``send_command`` / ``enter_extended_session`` /
``close``) but drives the bus with python-can + client-side ISO-TP. This lets the
existing ELM-path modes (scan, discover, identity, iocontrol, routines, and the
*-scan probers) run unchanged over the ``slcan-tcp`` transport.

One ISO-TP stack is created lazily per target ECU (rx = tx + 8) over a shared
Notifier. Responses are formatted back through :func:`parse_uds_response` so the
returned dict is byte-for-byte the same shape the modes already expect (ok / hex
/ bytes / nrc / nrc_desc / error), including SID/DID echo validation.
"""

from __future__ import annotations

import asyncio
import logging
import time

import can
import isotp

from ..log import log_response
from ..safety import enforce_command_safety
from ..uds_parse import parse_uds_response
from .uds_raw import RESPONSE_OFFSET, is_response_pending

logging.getLogger("isotp").setLevel(logging.ERROR)

# UDS ResponsePending (NRC 0x78) handling: after a 0x78 the ECU sends its real
# response in a follow-up frame within P2* (server) time. Wait for it, bounded.
_PENDING_RECV_TIMEOUT = 5.0   # per follow-up recv after a 0x78
_PENDING_TOTAL_TIMEOUT = 20.0  # overall cap while the ECU keeps saying "pending"


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
        tx_padding: int = 0xAA,
    ):
        from .slcan_tcp import SlcanTcpBus

        self.host = host
        self.verbose = verbose
        self.unsafe = unsafe
        self.timeout = timeout
        # Parity attributes some callers read.
        self.cmd_count = 0
        self.cmd_time = 0.0
        self.elm_timeout_cmd = ""

        self.bus = SlcanTcpBus(host, port=port, bitrate=bitrate)
        self.notifier = can.Notifier(self.bus, [], timeout=0.1)
        self._params = {
            "tx_padding": tx_padding,
            "blocksize": 0,
            "stmin": 0,
            "rx_flowcontrol_timeout": 1000,
            "rx_consecutive_frame_timeout": 1000,
            "can_fd": False,
            "tx_data_length": 8,
        }
        self._stacks: dict[int, isotp.NotifierBasedCanStack] = {}
        self._cur: int | None = None

    # -- WiCANTerminal-compatible surface -----------------------------------
    async def connect(self) -> None:  # bus already opened in __init__
        return None

    async def init_elm(self, *_a, **_k) -> None:  # no ELM to initialise
        return None

    async def set_header(self, tx_id: int) -> None:
        self._cur = tx_id
        self._stack(tx_id)

    async def send_uds(
        self,
        service_pid: str,
        timeout: float | None = None,
        expected_sid: int | None = None,
        expected_did: int | None = None,
    ) -> dict:
        await enforce_command_safety(service_pid, self.unsafe)
        try:
            req = bytes.fromhex(service_pid.replace(" ", ""))
        except ValueError:
            return parse_uds_response("?")
        resp = await self._exchange(req, timeout)
        raw = "NO DATA" if resp is None else resp.hex().upper()
        log_response(service_pid, raw)
        return parse_uds_response(raw, expected_sid=expected_sid, expected_did=expected_did)

    async def send_command(self, cmd: str, timeout: float | None = None) -> str:
        """AT commands are a no-op ('OK'); UDS hex is sent and returned as hex."""
        await enforce_command_safety(cmd, self.unsafe)
        c = cmd.strip()
        if c.upper().startswith("AT"):
            return "OK"
        try:
            req = bytes.fromhex(c.replace(" ", ""))
        except ValueError:
            return "?"
        resp = await self._exchange(req, timeout)
        return "NO DATA" if resp is None else resp.hex().upper()

    async def enter_extended_session(self, wake: bool = False) -> tuple[bool, asyncio.Task | None]:
        """Enter extended session (10 03) on the current ECU + start keepalive.

        Mirrors WiCANTerminal.enter_extended_session; the TesterPresent loop
        targets the ECU that was current at entry.
        """
        tx = self._cur
        if wake:
            await self.send_uds("1001", timeout=3.0)
            await asyncio.sleep(0.3)
        resp = await self.send_uds("1003", timeout=3.0)
        if resp.get("ok"):
            print("  Extended session (10 03) established.")
        elif resp.get("nrc") is not None:
            print(f"  WARNING: session NRC 0x{resp['nrc']:02X} ({resp['nrc_desc']}) — continuing.")

        async def _tester_loop():
            try:
                while True:
                    await asyncio.sleep(2.0)
                    with _suppress():
                        await self._exchange_tx(tx, bytes.fromhex("3E00"), 1.5)
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
            addr = isotp.Address(
                isotp.AddressingMode.Normal_11bits, txid=tx_id, rxid=tx_id + RESPONSE_OFFSET
            )
            st = isotp.NotifierBasedCanStack(
                self.bus, self.notifier, address=addr, params=self._params
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
        st = self._stack(tx_id)
        t = timeout if timeout is not None else self.timeout

        def _io():
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
            pending_deadline = time.monotonic() + _PENDING_TOTAL_TIMEOUT
            while is_response_pending(r) and time.monotonic() < pending_deadline:
                nxt = st.recv(block=True, timeout=_PENDING_RECV_TIMEOUT)
                if nxt is None:
                    break
                r = bytes(nxt)
            return r

        self.cmd_count += 1
        t0 = time.monotonic()
        try:
            return await asyncio.get_event_loop().run_in_executor(None, _io)
        finally:
            self.cmd_time += time.monotonic() - t0


class _suppress:
    """contextlib.suppress(Exception) without importing contextlib per-call."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True
