"""One functional (broadcast) request, and every reply it draws, for any transport.

A request on the functional id (``0x7DF``, or ``0x18DB33F1`` in a 29-bit mode)
reaches every ECU the gateway routes it to, and each answers on its own response
id within a few tens of milliseconds, so the replies overlap. This module is the
transport-neutral part of collecting them: which frames count, ISO-TP reassembly
per responder (the shared :class:`~canlib.uds.isotp_passive.Reassembler`), and
when to stop listening. It is pure; the raw and ELM327 terminals feed it frames
(``Terminal.send_functional``), and only the raw path, which runs ISO-TP itself,
acts on :meth:`FunctionalCollector.feed` asking for flow control.

Stopping: after the window (default :data:`DEFAULT_WINDOW_S`), unless a
responder is mid-message (wait up to ISO-TP's N_Cr for its next frame) or has
asked for more time with ResponsePending (``7F xx 78``), which the spec follows
with a final reply within 5 s. See ``plans/2026-10-30-functional-addressing-followups.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from canlib.uds.isotp_passive import Reassembler, ReassemblyStats, is_first_frame

# How long to listen after a functional request: the 50 ms an ECU has to start its
# answer, plus gateway delay and the 100 kbit/s buses, with room to spare.
DEFAULT_WINDOW_S: Final = 0.3

# ISO-TP N_Cr: the longest gap between two frames of one message (1000 ms on
# Hyundai/Kia, the ISO 15765-2 default).
CONSECUTIVE_FRAME_TIMEOUT_S: Final = 1.0

# After a ResponsePending, the final reply is due within P2* (5000 ms); a run of
# them is capped as on the physical raw path (`uds_raw.PENDING_TOTAL_TIMEOUT`).
PENDING_EXTENSION_S: Final = 5.0
PENDING_TOTAL_S: Final = 20.0

# Lowest 11-bit diagnostic id a response may arrive on (ES95486: 0x700-0x7FF).
_DIAG_11BIT_LOW: Final = 0x700
_DIAG_11BIT_HIGH: Final = 0x7FF
# 29-bit physical responses to the tester address F1: 0x18DA F1 {ecu}.
_DIAG_29BIT_RESPONSE: Final = 0x18DAF100


def accepts_response_id(can_id: int, extended: bool, functional_id: int) -> bool:
    """Whether a frame on ``can_id`` could answer a request on ``functional_id``.

    11-bit: the diagnostic block ``0x700``-``0x7FF``, minus the functional id
    itself. 29-bit: physical responses addressed to tester ``0xF1``. A frame that
    passes still has to *answer* the request, which the caller checks on the
    reassembled message (an in-car tester's own requests also fall in the block).
    """
    if extended != (functional_id > 0x7FF):
        return False
    if extended:
        return can_id & 0xFFFFFF00 == _DIAG_29BIT_RESPONSE
    return _DIAG_11BIT_LOW <= can_id <= _DIAG_11BIT_HIGH and can_id != functional_id


def functional_request_bytes(tx_id: int, request: str) -> bytes:
    """Check a functional request and return its bytes; every transport calls this first.

    Raises:
        SafetyBlocked: ``request`` is not one of the allowed reads
            (:func:`canlib.safety.check_functional_request`).
        ValueError: ``tx_id`` is not a functional id.
    """
    from canlib.addressing import FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID
    from canlib.safety import SafetyBlocked, check_functional_request

    if tx_id not in (FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID):
        raise ValueError(f"0x{tx_id:X} is not a functional request id")
    blocked = check_functional_request(request)
    if blocked is not None:
        raise SafetyBlocked(blocked)
    return bytes.fromhex(request.replace(" ", ""))


def single_frame(request: bytes, padding: int) -> bytes:
    """``request`` as one classic-CAN single frame, DLC 8 (ECUs ignore a shorter one)."""
    if not 1 <= len(request) <= 7:
        raise ValueError(
            f"a functional request must fit one CAN frame (1-7 bytes), got {len(request)}"
        )
    return (bytes([len(request)]) + request).ljust(8, bytes([padding]))


def flow_control_frame(stmin: int, padding: int) -> bytes:
    """ClearToSend, block size 0 (everything after one frame), DLC 8."""
    return bytes([0x30, 0x00, max(0, min(0xFF, stmin))]).ljust(8, bytes([padding]))


@dataclass(frozen=True)
class FunctionalMessage:
    """One complete message from one responder.

    ``elapsed_ms`` is measured from the request on the raw path; None on an
    ELM327, which hands back every reply in one block.
    """

    rx_id: int
    payload: bytes
    elapsed_ms: int | None


@dataclass
class FunctionalResult:
    """Everything one functional request drew, in order of arrival."""

    tx_id: int
    request: bytes
    messages: list[FunctionalMessage] = field(default_factory=list)
    # Messages known to have lost frames (never handed back truncated).
    lost: int = 0


def is_pending(payload: bytes, request_sid: int) -> bool:
    """A ResponsePending (``7F {sid} 78``) to this request."""
    return (
        len(payload) >= 3
        and payload[0] == 0x7F
        and payload[1] == request_sid
        and (payload[2] == 0x78)
    )


class FunctionalCollector:
    """Collect the replies to one functional request, frame by frame.

    Times are the caller's monotonic clock, in seconds; ``started`` is when the
    request went out.
    """

    def __init__(
        self,
        tx_id: int,
        request: bytes,
        *,
        started: float,
        window: float = DEFAULT_WINDOW_S,
    ) -> None:
        self._result = FunctionalResult(tx_id, request)
        self._extended = tx_id > 0x7FF
        self._stats = ReassemblyStats()
        self._reassembler = Reassembler(self._stats)
        self._started = started
        self._deadline = started + window
        self._pending_cap = started + PENDING_TOTAL_S
        self._last_frame = started

    def feed(self, can_id: int, data: bytes, now: float) -> bool:
        """Take one received frame; True when it is a First Frame to flow-control."""
        if not accepts_response_id(can_id, self._extended, self._result.tx_id):
            return False
        self._last_frame = now
        message = self._reassembler.feed((None, can_id, self._extended), data, now)
        if message is not None:
            self._result.messages.append(
                FunctionalMessage(can_id, message.payload, round((now - self._started) * 1000))
            )
            if is_pending(message.payload, self._result.request[0]):
                self._deadline = max(
                    self._deadline, min(now + PENDING_EXTENSION_S, self._pending_cap)
                )
        return is_first_frame(data)

    def done(self, now: float) -> bool:
        """Whether to stop listening (see the module docstring)."""
        if now < self._deadline:
            return False
        return not self._reassembler.open or now >= self._last_frame + CONSECUTIVE_FRAME_TIMEOUT_S

    def remaining(self, now: float) -> float:
        """Seconds until :meth:`done` could next become true (at least a tick)."""
        until = self._deadline - now
        if until <= 0 and self._reassembler.open:
            until = self._last_frame + CONSECUTIVE_FRAME_TIMEOUT_S - now
        return max(until, 0.005)

    def result(self) -> FunctionalResult:
        """The collected messages; a message still open at this point is lost."""
        self._reassembler.finish()
        self._result.lost = self._stats.lost
        return self._result
