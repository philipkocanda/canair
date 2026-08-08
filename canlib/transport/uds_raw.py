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
import threading
import time
from collections import defaultdict, deque

import can
import isotp

from ..addressing import DEFAULT_RX_OFFSET, EcuAddress, build_isotp_address
from ..timing import TimingRecorder
from ..transport_stats import TransportStats
from ..uds_parse import CAT_STALE, payload_echo_mismatch
from .isotp_stack import build_isotp_stack

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

# How many unpaid responses one ECU may owe before the ledger stops being evidence.
#
# An abandoned request leaves the ECU owing an answer, and an ISO-TP stack delivers
# one reassembled message per exchange — so "how many queued messages belong to
# earlier requests?" is arithmetic, exactly as the `>` prompt count is on the
# ELM327 path (see Elm327Terminal._send_command_locked). It is the only mechanism
# that catches a one-cycle offset when the same PID is polled repeatedly, because
# then the stale reply echoes the requested identifier and validation has nothing
# to compare.
#
# Past this many, the responses were genuinely lost rather than queued (a dropped
# frame owes forever), so the count is abandoned instead of discarding good replies
# for the rest of the session. Echo validation remains as the backstop.
_MAX_OWED_RESPONSES = 4

# Settling time for the notifier thread + ISO-TP stacks before the first request.
# Without it the opening frames are lost to warmup (observed: the whole first poll
# cycle dropped). Named so tests can zero it instead of patching `time.sleep`.
STACK_WARMUP_S = 0.2


class RawUdsClient:
    """UDS reads over raw CAN with per-ECU ISO-TP stacks and pipelined polling."""

    def __init__(
        self,
        bus: can.BusABC,
        addresses: dict[str, EcuAddress],
        *,
        timeout: float = 1.0,
        isotp_config: dict | None = None,
        ecu_timeouts: dict[str, float] | None = None,
        hk_f1xx_offset: bool = False,
    ):
        """``addresses``: name -> resolved :class:`EcuAddress`. ``timeout``: per-request seconds.

        ``ecu_timeouts``: optional ``{ECU_NAME(upper): seconds}`` per-ECU budget
        overriding ``timeout`` for that ECU (see :mod:`canlib.timeouts`).
        ``isotp_config``: optional profile ``isotp:`` block (flow-control/padding/
        CAN-FD). Each ECU's :class:`EcuAddress` shapes its ISO-TP stack (11-bit vs
        29-bit normal/fixed/extended, plus any flow-control-address override).
        ``hk_f1xx_offset``: profile opts into the Hyundai/Kia F1xx identity-DID -1
        quirk, so echo validation tolerates that off-by-one (see
        :func:`canlib.uds_parse.payload_echo_mismatch`).
        """
        from .isotp_params import build_isotp_params

        self.bus = bus
        self.timeout = timeout
        self.ecu_timeouts = ecu_timeouts or {}
        self.hk_f1xx_offset = hk_f1xx_offset
        # Per-(ECU, PID) round-trip timing (surfaced by `canair read --timings`).
        self.timings = TimingRecorder()
        # Per-exchange outcome tally (drops/errors/decode), same as the terminals.
        self.diag = TransportStats(transport="slcan-tcp")
        # Responses each ECU still owes us, from requests abandoned at their
        # deadline. See _MAX_OWED_RESPONSES: the count is what lets a late reply be
        # recognised as an earlier request's even when it echoes the right PID.
        self._owed: dict[str, int] = defaultdict(int)
        self.notifier = can.Notifier(bus, [], timeout=0.1)
        self._stacks: dict[str, isotp.NotifierBasedCanStack] = {}
        # Set to abort an in-flight poll() promptly (Ctrl-C / SIGTERM), so the
        # executor thread running poll() returns at once instead of finishing the
        # whole cycle's per-ECU timeouts — otherwise asyncio joins it at shutdown
        # and the process hangs for seconds. See canair monitor's interrupt path.
        self._interrupt = threading.Event()
        params = build_isotp_params(isotp_config)
        for name, address in addresses.items():
            stack = build_isotp_stack(
                bus,
                self.notifier,
                build_isotp_address(address),
                params,
                fc_id=address.fc_id,
            )
            stack.start()
            self._stacks[name] = stack
        # Let the notifier thread + stacks settle before the first request so the
        # opening frames aren't lost to warmup (observed: first poll cycle drops).
        time.sleep(STACK_WARMUP_S)

    def _stale(self, ecu: str, resp: bytes, reason: str, pid: str | None = None) -> None:
        """Tally one response discarded as belonging to an earlier request."""
        self.diag.record(
            CAT_STALE,
            ecu=ecu,
            pid=pid,
            detail=f"discarded {resp[:8].hex().upper()} — {reason}",
        )

    def _stale_reason(self, ecu: str, req: bytes, resp: bytes, owed: int) -> str | None:
        """Why ``resp`` cannot be ``req``'s answer, or None if it can be.

        Two independent tests, in order of strength. The ledger is decisive: while
        the ECU owes earlier answers, the next ones off its stack *are* those
        answers, whatever they contain — which is the only way to catch a repeated
        single-PID poll drifting one cycle behind. Echo validation then covers the
        case the ledger cannot see, a reply for a PID we never abandoned.
        """
        if owed > 0:
            return f"{ecu} still owed {owed} earlier response(s)"
        return payload_echo_mismatch(req.hex().upper(), resp.hex().upper(), self.hk_f1xx_offset)

    def _abandon(self, ecu: str) -> None:
        """Record that ``ecu`` never answered, so its reply may still be in flight."""
        owed = self._owed[ecu] + 1
        if owed >= _MAX_OWED_RESPONSES:
            # Nothing is coming: these responses were lost, not delayed. Holding
            # the debt open would discard every genuine reply from here on.
            owed = 0
        self._owed[ecu] = owed

    def _drain(self, ecu: str) -> int:
        """Discard messages already queued on ``ecu``'s stack; return how many.

        Anything sitting here when a request is about to go out answers an
        *earlier* request, so serving it would report a stale value as fresh. Each
        one settles part of what the ECU owed.
        """
        stack = self._stacks[ecu]
        discarded = 0
        while stack.available():
            msg = stack.recv()
            discarded += 1
            if msg is not None:
                self._stale(ecu, bytes(msg), "queued before the request was sent")
        if discarded:
            self._owed[ecu] = max(0, self._owed[ecu] - discarded)
        return discarded

    def read(self, ecu: str, request: bytes, timeout: float | None = None) -> bytes:
        """Send one UDS request to ``ecu`` and return the reassembled response."""
        stack = self._stacks[ecu]
        self._drain(ecu)
        pid = request.hex().upper()
        t = timeout if timeout is not None else self.ecu_timeouts.get(ecu, self.timeout)
        t0 = time.monotonic()
        stack.send(request)
        deadline = t0 + t
        owed = self._owed[ecu]
        pending_cap: float | None = None
        last_pending: bytes | None = None
        while True:
            remaining = deadline - time.monotonic()
            resp_raw = stack.recv(block=True, timeout=max(0.0, remaining))
            if resp_raw is None:
                if last_pending is not None:
                    # The ECU acknowledged but never delivered. Surface its own
                    # "pending" answer, as before, rather than a bare timeout.
                    resp = last_pending
                    break
                self.diag.record("no_data", ecu=ecu, pid=pid)
                self._abandon(ecu)
                raise TimeoutError(f"no UDS response from {ecu}")
            resp = bytes(resp_raw)
            # Wait through UDS ResponsePending (0x78) so slow services return their
            # final answer — parity with the ELM327 path + RawTerminal.
            if is_response_pending(resp):
                last_pending = resp
                if pending_cap is None:
                    pending_cap = time.monotonic() + PENDING_TOTAL_TIMEOUT
                deadline = min(time.monotonic() + PENDING_RECV_TIMEOUT, pending_cap)
                continue
            reason = self._stale_reason(ecu, request, resp, owed)
            if reason is None:
                break
            # A late answer to an earlier request. Discard it and keep waiting
            # within this request's own budget rather than returning it as ours.
            self._stale(ecu, resp, reason, pid=pid)
            owed = max(0, owed - 1)
            self._owed[ecu] = owed
        self._owed[ecu] = 0
        self.timings.record(ecu, pid, time.monotonic() - t0)
        self.diag.record_raw(resp, ecu=ecu, pid=pid)
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

        Every harvested message is checked against the request it is claimed to
        answer (see :meth:`_stale_reason`); a late reply to an earlier request is
        discarded and waiting continues, so a stalled link can't leave this client
        reporting values one cycle behind for the rest of the session.
        """

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

        out: dict[tuple[str, bytes], bytes | Exception] = {}
        while any(queues.values()):
            if self._interrupt.is_set():
                break
            # Send one request per ECU (concurrent on the bus).
            pending: dict[str, dict] = {}  # ecu -> {req, sent_at, deadline, cap, owed}
            for ecu, q in queues.items():
                if not q:
                    continue
                req = q.popleft()
                # Drain per *round*, not once per poll(): a reply that landed while
                # the previous round was being harvested is stale for this one.
                self._drain(ecu)
                now = time.monotonic()
                self._stacks[ecu].send(req)
                # Per-ECU budget applies unless the caller forced a timeout.
                ecu_t = t if explicit else self.ecu_timeouts.get(ecu, t)
                pending[ecu] = {
                    "req": req,
                    "sent_at": now,
                    "deadline": now + ecu_t,
                    "cap": None,
                    "owed": self._owed[ecu],
                }
            # Harvest whichever completes first; each ECU only spends its own budget.
            while pending:
                if self._interrupt.is_set():
                    break
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
                        reason = self._stale_reason(ecu, req, resp, info["owed"])
                        if reason is not None:
                            # An earlier request's answer. Drop it and keep waiting
                            # on this one's own deadline.
                            self._stale(ecu, resp, reason, pid=req.hex().upper())
                            info["owed"] = max(0, info["owed"] - 1)
                            self._owed[ecu] = info["owed"]
                            progressed = True
                            continue
                        self._owed[ecu] = 0
                        self.timings.record(
                            ecu, req.hex().upper(), time.monotonic() - info["sent_at"]
                        )
                        _finish(ecu, req, resp)
                        del pending[ecu]
                        progressed = True
                    elif now >= info["deadline"]:
                        self._abandon(ecu)
                        _finish(ecu, req, TimeoutError("no response"))
                        del pending[ecu]
                        progressed = True
                if pending and not progressed:
                    time.sleep(0.002)  # yield to the notifier thread reassembling frames
        return out

    def interrupt(self) -> None:
        """Abort an in-flight :meth:`poll` ASAP (thread-safe).

        Set from the main thread on Ctrl-C / SIGTERM so the pipelined poll loop —
        which runs in an executor thread — returns immediately instead of waiting
        out every pending ECU's timeout. Terminal: the client is being torn down,
        so it is never cleared.
        """
        self._interrupt.set()

    def close(self) -> None:
        self._interrupt.set()  # abort any in-flight poll() so its thread returns
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
