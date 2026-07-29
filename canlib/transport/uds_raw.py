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

from ..addressing import DEFAULT_MODE, DEFAULT_RX_OFFSET, AddressingMode, build_isotp_address
from ..timing import TimingRecorder
from ..transport_stats import TransportStats

# Quiet can-isotp's transient recovered-timeout warnings (e.g. a cold ECU's first
# multi-frame response) — they're handled/retried and just add noise. Genuine
# errors surface as per-request Exceptions from poll()/read().
logging.getLogger("isotp").setLevel(logging.ERROR)

# Conventional 11-bit UDS response offset (0x770->0x778, 0x7E4->0x7EC). The
# canonical source is canlib.addressing; kept here as the default for callers
# that pass explicit (tx, rx) pairs. Per-ECU / per-profile overrides are resolved
# upstream (see canlib.addressing.resolve_rx) and fed in as explicit rx addresses.
RESPONSE_OFFSET = DEFAULT_RX_OFFSET


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
        isotp_config: dict | None = None,
        ecu_timeouts: dict[str, float] | None = None,
        modes: dict[str, AddressingMode] | None = None,
        mode: AddressingMode = DEFAULT_MODE,
    ):
        """``ecus``: name -> (tx_id, rx_id). ``timeout``: per-request seconds.

        ``ecu_timeouts``: optional ``{ECU_NAME(upper): seconds}`` per-ECU budget
        overriding ``timeout`` for that ECU (see :mod:`canlib.timeouts`).
        ``isotp_config``: optional profile ``isotp:`` block (flow-control/padding/
        CAN-FD). ``modes``/``mode``: per-ECU addressing mode (name → mode) falling
        back to the profile default ``mode`` — shapes each ECU's ISO-TP stack
        (11-bit vs 29-bit normal/fixed).
        """
        from .isotp_params import build_isotp_params

        self.bus = bus
        self.timeout = timeout
        self.ecu_timeouts = ecu_timeouts or {}
        # Per-(ECU, PID) round-trip timing (surfaced by `canair query --timings`).
        self.timings = TimingRecorder()
        # Per-exchange outcome tally (drops/errors/decode), same as the terminals.
        self.diag = TransportStats(transport="slcan-tcp")
        self.notifier = can.Notifier(bus, [], timeout=0.1)
        self._stacks: dict[str, isotp.NotifierBasedCanStack] = {}
        modes = modes or {}
        params = build_isotp_params(isotp_config)
        for name, (tx, rx) in ecus.items():
            addr = build_isotp_address(tx, rx, modes.get(name, mode))
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
            self.diag.record("no_data", ecu=ecu, pid=request.hex().upper())
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
        self.diag.record_raw(resp, ecu=ecu, pid=request.hex().upper())
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
            self.diag.record_raw(value, ecu=ecu, pid=req.hex().upper())
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
