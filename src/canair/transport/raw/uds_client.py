"""Pipelined UDS-over-raw-CAN client (ISO-TP via can-isotp).

Raw CAN modes (SLCAN/SavvyCAN/...) move single frames only, so multi-frame UDS
responses need client-side ISO-TP. This wraps one :class:`isotp` stack per ECU
over a shared python-can bus/``Notifier`` and adds **request pipelining**: fire
requests to several ECUs back-to-back, then collect the responses as they arrive
— overlapping the ECUs' processing time instead of the strictly sequential
request→wait→response of the ELM327 path.

Requests/responses are raw UDS payloads (e.g. ``bytes.fromhex("22BC03")`` in,
``62 BC 03 …`` out); ISO-TP PCI/flow-control/reassembly is handled here.

One exchange per ECU at a time, enforced by a per-ECU lock: a blocking :meth:`RawUdsClient.read`
from another thread (a session switch, an actuation) interleaves with a running
:meth:`RawUdsClient.poll` request by request, and holds up only its own ECU. An ECU that joins
after construction gets its stack from :meth:`RawUdsClient.ensure_ecu`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any

import can
import isotp

from canair.safety.blocklist import SafetyBlocked, SessionPolicy, check_command_safety
from canair.transport.isotp_params import build_isotp_params
from canair.transport.link_latency import LinkLatency
from canair.transport.raw.isotp_stack import (
    EcuStacks,
    ReceptionFaults,
    build_isotp_stack,
    hint_transport_choice,
)
from canair.transport.stats import TransportStats
from canair.transport.timeouts import PENDING_RECV_TIMEOUT, pending_deadline
from canair.transport.timing import TimingRecorder
from canair.uds.addressing import DEFAULT_RX_OFFSET, EcuAddress, build_isotp_address
from canair.uds.nrc import is_negative_response, is_response_pending
from canair.uds.payload import payload_echo_mismatch
from canair.uds.response import CAT_STALE

# Quiet can-isotp's transient recovered-timeout warnings (e.g. a cold ECU's first
# multi-frame response) — they're handled/retried and just add noise. Genuine
# errors surface as per-request Exceptions from poll()/read().
logging.getLogger("isotp").setLevel(logging.ERROR)

# Conventional 11-bit UDS response offset (0x770->0x778, 0x7E4->0x7EC). The
# canonical source is canair.uds.addressing; kept here as the default for callers
# that pass explicit (tx, rx) pairs. Per-ECU / per-profile overrides are resolved
# upstream (see canair.uds.addressing.resolve_rx) and fed in as explicit rx addresses.
RESPONSE_OFFSET = DEFAULT_RX_OFFSET


def response_id(tx_id: int) -> int:
    return tx_id + RESPONSE_OFFSET


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
        ecu_isotp: Mapping[int, Mapping[str, Any]] | None = None,
        ecu_timeouts: dict[str, float] | None = None,
        session_policy: SessionPolicy | None = None,
    ):
        """``addresses``: name -> resolved :class:`EcuAddress`. ``timeout``: per-request seconds.

        ``ecu_timeouts``: optional ``{ECU_NAME(upper): seconds}`` per-ECU budget
        overriding ``timeout`` for that ECU (see :mod:`canair.transport.timeouts`).
        ``isotp_config``: optional profile ``isotp:`` block (flow-control/padding/
        CAN-FD); ``ecu_isotp``: ``{tx_id: block}`` for the ECUs with their own
        (:func:`~canair.transport.isotp_params.ecu_isotp_blocks`), laid over it. Each
        ECU's :class:`EcuAddress` shapes its ISO-TP stack (11-bit vs 29-bit
        normal/fixed/extended, plus any flow-control-address override).
        ``session_policy``: the profile's per-ECU session context for the command
        blocklist (:mod:`canair.safety.blocklist`). Every request is checked against it, so
        this client can never send what the terminals would refuse; there is no
        ``--unsafe`` prompt on this pipelined path, so a blocked request raises.
        """
        self.bus = bus
        self.timeout = timeout
        self.ecu_timeouts = ecu_timeouts or {}
        self.session_policy = session_policy or SessionPolicy()
        # Per-(ECU, PID) round-trip timing (surfaced by `canair read --timings`).
        self.timings = TimingRecorder()
        # Each request's send-to-resolve time from the last poll(), keyed like its result.
        self.last_latency: dict[tuple[str, bytes], float] = {}
        # Per-exchange outcome tally (drops/errors/decode), same as the terminals.
        self.diag = TransportStats(transport="slcan-tcp")
        # Responses each ECU still owes us, from requests abandoned at their
        # deadline. See _MAX_OWED_RESPONSES: the count is what lets a late reply be
        # recognised as an earlier request's even when it echoes the right PID.
        self._owed: dict[str, int] = defaultdict(int)
        self.notifier = can.Notifier(bus, [], timeout=0.1)
        # Whether a reply that never arrived had started (its first frame came).
        self._rx_faults = ReceptionFaults()
        # One exchange per ECU at a time (the module docstring): stacks, ids and locks.
        self._ecus = EcuStacks(self._build_stack, lambda: time.sleep(STACK_WARMUP_S))
        self._stacks = self._ecus.stacks
        self._ecu_locks = self._ecus.locks
        self._tx_ids = self._ecus.tx_ids
        # Set to abort an in-flight poll() promptly (Ctrl-C / SIGTERM), so the
        # executor thread running poll() returns at once instead of finishing the
        # whole cycle's per-ECU timeouts — otherwise asyncio joins it at shutdown
        # and the process hangs for seconds. See canair monitor's interrupt path.
        self._interrupt = threading.Event()
        # The bus measures the link at connect (TCP handshake ≈ one round trip);
        # a bus that doesn't offer an estimate leaves the LAN defaults in place.
        self.link: LinkLatency = getattr(bus, "link", None) or LinkLatency()
        self._isotp_config = isotp_config
        self._ecu_isotp = ecu_isotp or {}
        self._params = build_isotp_params(isotp_config, self.link.budget)
        hint_transport_choice(self.link)
        for name, address in addresses.items():
            self._ecus.add(name, address)
        # Let the notifier thread + stacks settle before the first request so the
        # opening frames aren't lost to warmup (observed: first poll cycle drops).
        time.sleep(STACK_WARMUP_S)

    def _build_stack(self, address: EcuAddress) -> isotp.NotifierBasedCanStack:
        own = self._ecu_isotp.get(address.tx_id)
        params = build_isotp_params(self._isotp_config, self.link.budget, own) if own else None
        stack = build_isotp_stack(
            self.bus,
            self.notifier,
            build_isotp_address(address),
            params or self._params,
            fc_id=address.fc_id,
        )
        self._rx_faults.watch(stack, address.tx_id)
        return stack

    def _lost(self, ecu: str) -> TimeoutError:
        """The error for a request ``ecu`` did not answer by its deadline (a ledger entry)."""
        error, owed = self._rx_faults.lost(self._stacks[ecu], self._tx_ids[ecu], ecu)
        if owed:
            self._abandon(ecu)
        return error

    def ensure_ecu(self, name: str, address: EcuAddress) -> None:
        """Give ``name`` an ISO-TP stack if it has none yet (thread-safe; call off the loop)."""
        self._ecus.ensure(name, address)

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
        case the ledger cannot see, a reply for a PID we never abandoned, and a
        negative response must name this request's service.
        """
        if owed > 0:
            return f"{ecu} still owed {owed} earlier response(s)"
        if is_negative_response(resp) and not is_negative_response(resp, req[0]):
            return f"a negative response to service 0x{resp[1]:02X}, not 0x{req[0]:02X}"
        return payload_echo_mismatch(req.hex().upper(), resp.hex().upper())

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

    def _budget(self, ecu: str, forced: float | None) -> float:
        """Per-request deadline: the ECU's think time plus the network's share.

        A caller-forced ``timeout`` is returned untouched — that is an explicit
        instruction, not a budget to be adjusted. Otherwise the profile's per-ECU
        value (or this client's default) states how long the *car* may take, and
        the measured link allowance is added on top; without that a value tuned
        against a LAN expires while the answer is still crossing the network, and
        the answer then arrives during the next request.
        """
        if forced is not None:
            return forced
        return self.ecu_timeouts.get(ecu, self.timeout) + (self.link.budget or 0.0)

    def _check_safety(self, ecu: str, request: bytes) -> None:
        """Refuse ``request`` if the shared command blocklist would."""
        blocked = check_command_safety(request.hex(), self.session_policy, self._tx_ids.get(ecu))
        if blocked:
            raise SafetyBlocked(blocked)

    def read(self, ecu: str, request: bytes, timeout: float | None = None) -> bytes:
        """Send one UDS request to ``ecu`` and return the reassembled response.

        Waits for the request ``poll()`` has in flight to ``ecu``, if any, and never for
        the other ECUs: safe to call from another thread while a poll runs.
        """
        self._check_safety(ecu, request)
        self._ecus.require(ecu)
        with self._ecu_locks[ecu]:
            return self._read_locked(ecu, request, timeout)

    def _read_locked(self, ecu: str, request: bytes, timeout: float | None) -> bytes:
        stack = self._stacks[ecu]
        self._drain(ecu)
        pid = request.hex().upper()
        t = self._budget(ecu, timeout)
        t0 = time.monotonic()
        self._rx_faults.clear(self._tx_ids[ecu])
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
                error = self._lost(ecu)
                self.diag.record_raw(error, ecu=ecu, pid=pid)
                raise error
            resp = bytes(resp_raw)
            # Wait through UDS ResponsePending (0x78) so slow services return their
            # final answer — parity with the ELM327 path + RawTerminal.
            if is_response_pending(resp, request[0]):
                last_pending = resp
                deadline, pending_cap = pending_deadline(
                    time.monotonic(), PENDING_RECV_TIMEOUT, pending_cap
                )
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
        request gets its *own* deadline (``sent_at + its budget``) and we round-robin
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

        Each request's own send-to-resolve time is left in :attr:`last_latency`
        (a timeout counts its whole budget), so a caller can tell which ECU is
        holding the rounds up.

        A request goes out only while this poll holds its ECU's lock, taken without
        waiting: an ECU that a :meth:`read` from another thread holds keeps its request
        for a later round, so that read waits for at most one of this poll's requests.
        A request to an ECU with no stack resolves to a ``LookupError``.
        """

        def _finish(ecu: str, req: bytes, value, sent_at: float):
            out[(ecu, req)] = value
            self.last_latency[(ecu, req)] = time.monotonic() - sent_at
            self.diag.record_raw(value, ecu=ecu, pid=req.hex().upper())
            if on_result is not None:
                try:
                    on_result((ecu, req), value)
                except Exception:
                    pass  # a rendering callback must never break polling

        queues: dict[str, deque] = defaultdict(deque)
        out: dict[tuple[str, bytes], bytes | Exception] = {}
        self.last_latency = {}
        for ecu, req in requests:
            # Checked up front, before anything is sent: a refused request is a
            # caller bug, not a per-request outcome to render alongside the rest.
            self._check_safety(ecu, req)
            if ecu not in self._stacks:
                _finish(ecu, req, LookupError(f"no ISO-TP stack for {ecu}"), time.monotonic())
                continue
            queues[ecu].append(req)

        # The ECUs whose lock this poll holds: one per request in flight.
        held: set[str] = set()

        def _resolve(ecu: str, info: dict, value) -> None:
            _finish(ecu, info["req"], value, info["sent_at"])
            del pending[ecu]
            held.discard(ecu)
            self._ecu_locks[ecu].release()

        pending: dict[str, dict] = {}  # ecu -> {req, sent_at, deadline, cap, owed}
        try:
            while any(queues.values()):
                if self._interrupt.is_set():
                    break
                # Send one request per ECU (concurrent on the bus). An ECU that read()
                # holds keeps its request for a later round; the others go on.
                for ecu, q in queues.items():
                    if not q or not self._ecu_locks[ecu].acquire(blocking=False):
                        continue
                    held.add(ecu)
                    req = q.popleft()
                    # Drain per *round*, not once per poll(): a reply that landed while
                    # the previous round was being harvested is stale for this one.
                    self._drain(ecu)
                    now = time.monotonic()
                    self._rx_faults.clear(self._tx_ids[ecu])
                    self._stacks[ecu].send(req)
                    # Per-ECU budget applies unless the caller forced a timeout.
                    ecu_t = timeout if timeout is not None else self._budget(ecu, None)
                    pending[ecu] = {
                        "req": req,
                        "sent_at": now,
                        "deadline": now + ecu_t,
                        "cap": None,
                        "owed": self._owed[ecu],
                    }
                if not pending:
                    time.sleep(0.002)  # every ECU left is held by a read(): let it finish
                    continue
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
                            _resolve(ecu, info, e)
                            progressed = True
                            continue
                        if resp is not None:
                            resp = bytes(resp)
                            if is_response_pending(resp, req[0]):
                                # ECU said "still working" — wait for the follow-up,
                                # bounded (parity with read()/RawTerminal/ELM).
                                info["deadline"], info["cap"] = pending_deadline(
                                    now, PENDING_RECV_TIMEOUT, info["cap"]
                                )
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
                            _resolve(ecu, info, resp)
                            progressed = True
                        elif now >= info["deadline"]:
                            _resolve(ecu, info, self._lost(ecu))
                            progressed = True
                    if pending and not progressed:
                        time.sleep(0.002)  # yield to the notifier thread reassembling frames
        finally:
            for ecu in held:
                self._ecu_locks[ecu].release()
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
