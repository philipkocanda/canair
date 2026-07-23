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

import logging
import time

import can
import isotp

from ..timing import TimingRecorder

# Quiet can-isotp's transient recovered-timeout warnings (e.g. a cold ECU's first
# multi-frame response) — they're handled/retried and just add noise. Genuine
# errors surface as per-request Exceptions from poll()/read().
logging.getLogger("isotp").setLevel(logging.ERROR)

# Standard 11-bit UDS response offset for the Ioniq ECUs (0x770->0x778,
# 0x7E4->0x7EC). Overridable per-ECU when constructing the client.
RESPONSE_OFFSET = 0x08


def response_id(tx_id: int) -> int:
    return tx_id + RESPONSE_OFFSET


def is_response_pending(resp: bytes) -> bool:
    """True if ``resp`` is a UDS ResponsePending negative response (7F xx 78).

    The ECU acknowledges the request but needs more time; it will send the final
    response in a follow-up frame. Both raw-CAN clients must wait through this so
    they behave like the ELM327 firmware (which handles 0x78 automatically).
    """
    return len(resp) >= 3 and resp[0] == 0x7F and resp[2] == 0x78


# UDS ResponsePending (0x78) bounds — the ECU said "still working"; wait for the
# real answer, capped. Shared by both raw-CAN clients (RawUdsClient + RawTerminal).
PENDING_RECV_TIMEOUT = 5.0  # per follow-up wait after a 0x78
PENDING_TOTAL_TIMEOUT = 20.0  # overall cap while the ECU keeps saying "pending"


class RawUdsClient:
    """UDS reads over raw CAN with per-ECU ISO-TP stacks and pipelined polling."""

    def __init__(
        self,
        bus: can.BusABC,
        ecus: dict[str, tuple[int, int]],
        *,
        timeout: float = 1.0,
        tx_padding: int = 0xAA,
        ecu_timeouts: dict[str, float] | None = None,
    ):
        """``ecus``: name -> (tx_id, rx_id). ``timeout``: per-request seconds.

        ``ecu_timeouts``: optional ``{ECU_NAME(upper): seconds}`` per-ECU budget
        overriding ``timeout`` for that ECU (see :mod:`canlib.timeouts`).
        """
        self.bus = bus
        self.timeout = timeout
        self.ecu_timeouts = ecu_timeouts or {}
        # Per-(ECU, PID) round-trip timing (surfaced by `canair query --timings`).
        self.timings = TimingRecorder()
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
        t0 = time.monotonic()
        stack.send(request)
        t = timeout if timeout is not None else self.ecu_timeouts.get(ecu, self.timeout)
        resp = stack.recv(block=True, timeout=t)
        if resp is None:
            raise TimeoutError(f"no UDS response from {ecu}")
        resp = bytes(resp)
        # Wait through UDS ResponsePending (0x78) so slow services return their
        # final answer — parity with the ELM327 path + RawTerminal.
        pending_deadline = time.monotonic() + PENDING_TOTAL_TIMEOUT
        while is_response_pending(resp) and time.monotonic() < pending_deadline:
            nxt = stack.recv(block=True, timeout=PENDING_RECV_TIMEOUT)
            if nxt is None:
                break
            resp = bytes(nxt)
        self.timings.record(ecu, request.hex().upper(), time.monotonic() - t0)
        return resp

    def poll(
        self,
        requests: list[tuple[str, bytes]],
        timeout: float | None = None,
        on_result=None,
    ) -> dict[tuple[str, bytes], bytes | Exception]:
        """Round-based pipelined read.

        An ISO-TP stack allows only ONE outstanding request, so we pipeline
        *across* ECUs but stay sequential *within* an ECU: each round sends the
        next pending request for every ECU (concurrent on the bus), then collects
        that round's responses — overlapping the ECUs' think-time. Returns a map
        keyed by the input ``(ecu, request)`` tuple; values are response bytes or
        an ``Exception`` on failure.

        Collection is a **non-blocking multiplexed harvest**: each in-flight
        request gets its *own* deadline (``sent_at + t``) and we round-robin over
        all stacks, taking whichever completes first. This avoids the earlier
        shared-deadline bug where one slow/silent ECU consumed the whole budget
        and starved the ECUs collected after it (leaving them ~0.05s → spurious
        timeouts). Now a slow ECU only spends its own budget; the rest are
        unaffected.

        ``on_result``: optional ``callback((ecu, req), value)`` fired the instant
        each request resolves (bytes) or gives up (Exception). Lets the caller
        render results *incrementally* so one slow/timing-out request can't hold
        up displaying the others (the monitor uses this to stay live).
        """
        from collections import defaultdict, deque

        def _finish(ecu: str, req: bytes, value):
            out[(ecu, req)] = value
            if on_result is not None:
                try:
                    on_result((ecu, req), value)
                except Exception:
                    pass  # a rendering callback must never break polling

        explicit = timeout is not None
        t = timeout if explicit else self.timeout
        queues: dict[str, deque] = defaultdict(deque)
        for ecu, req in requests:
            queues[ecu].append(req)
        for ecu in queues:
            self._drain(ecu)

        out: dict[tuple[str, bytes], bytes | Exception] = {}
        while any(queues.values()):
            # Send one request per ECU (concurrent on the bus).
            pending: dict[str, dict] = {}  # ecu -> {req, sent_at, deadline, cap}
            now = time.monotonic()
            for ecu, q in queues.items():
                if q:
                    req = q.popleft()
                    self._stacks[ecu].send(req)
                    # Per-ECU budget applies unless the caller forced a timeout.
                    ecu_t = t if explicit else self.ecu_timeouts.get(ecu, t)
                    pending[ecu] = {
                        "req": req,
                        "sent_at": now,
                        "deadline": now + ecu_t,
                        "cap": None,
                    }
            # Harvest whichever completes first; each ECU only spends its own budget.
            while pending:
                progressed = False
                now = time.monotonic()
                for ecu in list(pending):
                    info = pending[ecu]
                    req = info["req"]
                    st = self._stacks[ecu]
                    try:
                        resp = st.recv(block=False) if st.available() else None
                    except Exception as e:  # surface per-request, keep polling the rest
                        _finish(ecu, req, e)
                        del pending[ecu]
                        progressed = True
                        continue
                    if resp is not None:
                        resp = bytes(resp)
                        if is_response_pending(resp):
                            # ECU said "still working" — wait for the follow-up,
                            # bounded (parity with read()/RawTerminal/ELM).
                            if info["cap"] is None:
                                info["cap"] = now + PENDING_TOTAL_TIMEOUT
                            info["deadline"] = min(now + PENDING_RECV_TIMEOUT, info["cap"])
                            progressed = True
                            continue
                        self.timings.record(
                            ecu, req.hex().upper(), time.monotonic() - info["sent_at"]
                        )
                        _finish(ecu, req, resp)
                        del pending[ecu]
                        progressed = True
                    elif now >= info["deadline"]:
                        _finish(ecu, req, TimeoutError("no response"))
                        del pending[ecu]
                        progressed = True
                if pending and not progressed:
                    time.sleep(0.002)  # yield to the notifier thread reassembling frames
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
