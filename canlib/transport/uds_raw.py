"""Pipelined UDS-over-raw-CAN client (ISO-TP via can-isotp).

Raw CAN modes (SLCAN/SavvyCAN/...) move single frames only, so multi-frame UDS
responses need client-side ISO-TP. This wraps one :class:`isotp` stack per ECU
over a shared python-can bus/``Notifier`` and adds **request pipelining**: fire
requests to several ECUs back-to-back, then collect the responses as they arrive
— overlapping the ECUs' processing time instead of the strictly sequential
request→wait→response of the ELM327 path.

Requests/responses are raw UDS payloads (e.g. ``bytes.fromhex("22BC03")`` in,
``62 BC 03 …`` out); ISO-TP PCI/flow-control/reassembly is handled here.
"""

from __future__ import annotations

import time

import can
import isotp

# Standard 11-bit UDS response offset for the Ioniq ECUs (0x770->0x778,
# 0x7E4->0x7EC). Overridable per-ECU when constructing the client.
RESPONSE_OFFSET = 0x08


def response_id(tx_id: int) -> int:
    return tx_id + RESPONSE_OFFSET


class RawUdsClient:
    """UDS reads over raw CAN with per-ECU ISO-TP stacks and pipelined polling."""

    def __init__(
        self,
        bus: can.BusABC,
        ecus: dict[str, tuple[int, int]],
        *,
        timeout: float = 1.0,
        tx_padding: int = 0xAA,
    ):
        """``ecus``: name -> (tx_id, rx_id). ``timeout``: per-request seconds."""
        self.bus = bus
        self.timeout = timeout
        self.notifier = can.Notifier(bus, [], timeout=0.1)
        self._stacks: dict[str, isotp.NotifierBasedCanStack] = {}
        params = {
            "tx_padding": tx_padding,
            "blocksize": 0,
            "stmin": 0,
            "rx_flowcontrol_timeout": 1000,
            "rx_consecutive_frame_timeout": 1000,
            "can_fd": False,
            "tx_data_length": 8,
        }
        for name, (tx, rx) in ecus.items():
            addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=tx, rxid=rx)
            stack = isotp.NotifierBasedCanStack(bus, self.notifier, address=addr, params=params)
            stack.start()
            self._stacks[name] = stack
        # Let the notifier thread + stacks settle before the first request so the
        # opening frames aren't lost to warmup (observed: first poll cycle drops).
        time.sleep(0.2)

    def _drain(self, ecu: str) -> None:
        stack = self._stacks[ecu]
        while stack.available():
            stack.recv()

    def read(self, ecu: str, request: bytes, timeout: float | None = None) -> bytes:
        """Send one UDS request to ``ecu`` and return the reassembled response."""
        stack = self._stacks[ecu]
        self._drain(ecu)
        stack.send(request)
        resp = stack.recv(block=True, timeout=timeout if timeout is not None else self.timeout)
        if resp is None:
            raise TimeoutError(f"no UDS response from {ecu}")
        return bytes(resp)

    def poll(
        self, requests: list[tuple[str, bytes]], timeout: float | None = None
    ) -> dict[tuple[str, bytes], bytes | Exception]:
        """Round-based pipelined read.

        An ISO-TP stack allows only ONE outstanding request, so we pipeline
        *across* ECUs but stay sequential *within* an ECU: each round sends the
        next pending request for every ECU (concurrent on the bus), then collects
        that round's responses — overlapping the ECUs' think-time. Returns a map
        keyed by the input ``(ecu, request)`` tuple; values are response bytes or
        an ``Exception`` on failure.
        """
        from collections import defaultdict, deque

        t = timeout if timeout is not None else self.timeout
        queues: dict[str, deque] = defaultdict(deque)
        for ecu, req in requests:
            queues[ecu].append(req)
        for ecu in queues:
            self._drain(ecu)

        out: dict[tuple[str, bytes], bytes | Exception] = {}
        while any(queues.values()):
            inflight = []
            for ecu, q in queues.items():
                if q:
                    req = q.popleft()
                    self._stacks[ecu].send(req)
                    inflight.append((ecu, req))
            deadline = time.monotonic() + t
            for ecu, req in inflight:
                remaining = max(0.05, deadline - time.monotonic())
                try:
                    resp = self._stacks[ecu].recv(block=True, timeout=remaining)
                    out[(ecu, req)] = (
                        bytes(resp) if resp is not None else TimeoutError("no response")
                    )
                except Exception as e:  # surface per-request, keep polling the rest
                    out[(ecu, req)] = e
        return out

    def close(self) -> None:
        for stack in self._stacks.values():
            try:
                stack.stop()
            except Exception:
                pass
        try:
            self.notifier.stop()
        except Exception:
            pass
        try:
            self.bus.shutdown()
        except Exception:
            pass
