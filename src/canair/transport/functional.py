"""One functional (broadcast) request, and every reply it draws, for any transport.

A request on the functional id (``0x7DF``, or ``0x18DB33F1`` in a 29-bit mode)
reaches every ECU the gateway routes it to, and each answers on its own response
id within a few tens of milliseconds, so the replies overlap. This module is the
transport-neutral part of collecting them: which frames count, ISO-TP reassembly
per responder (the shared :class:`~canair.uds.isotp_passive.Reassembler`), and
when to stop listening. It is pure; the raw and ELM327 terminals feed it frames
(``Terminal.send_functional``), and only the raw path, which runs ISO-TP itself,
acts on :meth:`FunctionalCollector.feed` asking for flow control.

Stopping: after the window (default :data:`DEFAULT_WINDOW_S`), unless a
responder is mid-message (wait up to ISO-TP's N_Cr for *its* next frame) or has
asked for more time with ResponsePending (``7F xx 78``), which the spec follows
with a final reply within 5 s. A message idle for longer than N_Cr is abandoned
(counted lost), and nothing keeps the collector listening past
:data:`ABSOLUTE_CAP_S` beyond the window, however much other traffic arrives. See
``plans/2026-10-30-functional-addressing-followups.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from canair.uds.isotp_passive import Reassembler, ReassemblyStats, is_first_frame
from canair.uds.nrc import is_response_pending
from canair.uds.response import CAT_DROP
from canair.uds.services import NEGATIVE_RESPONSE_SID, positive_response_sid

if TYPE_CHECKING:
    from .stats import TransportStats

# How long to listen after a functional request. An ECU has 50 ms to start its answer, but
# the Ioniq's AMP, on the slow MM-CAN behind the gateway, missed a 300 ms window in one of two
# sweeps and answered every round at 1 s, the only value tried that caught every responder.
DEFAULT_WINDOW_S: Final = 1.0

# ISO-TP N_Cr: the longest gap between two frames of one message (1000 ms on
# Hyundai/Kia, the ISO 15765-2 default).
CONSECUTIVE_FRAME_TIMEOUT_S: Final = 1.0

# After a ResponsePending, the final reply is due within P2* (5000 ms); a run of
# them is capped as on the physical raw path (`uds_raw.PENDING_TOTAL_TIMEOUT`).
PENDING_EXTENSION_S: Final = 5.0
PENDING_TOTAL_S: Final = 20.0

# The longest a collector listens past its window, whatever arrives: every
# ResponsePending extension, then one message's last gap.
ABSOLUTE_CAP_S: Final = PENDING_TOTAL_S + CONSECUTIVE_FRAME_TIMEOUT_S

# ISO-TP frame types (the PCI byte's high nibble).
_CONSECUTIVE_FRAME: Final = 2
_FLOW_CONTROL: Final = 3

CAN_FD_REFUSAL: Final = (
    "a functional request is sent and reassembled as classic CAN only, and the "
    "profile's isotp: block selects CAN-FD (can_fd, or a tx_data_length other than 8)"
)

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
            (:func:`canair.safety.blocklist.check_functional_request`).
        ValueError: ``tx_id`` is not a functional id.
    """
    from canair.safety.blocklist import SafetyBlocked, check_functional_request
    from canair.uds.addressing import FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID

    if tx_id not in (FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID):
        raise ValueError(f"0x{tx_id:X} is not a functional request id")
    blocked = check_functional_request(request)
    if blocked is not None:
        raise SafetyBlocked(blocked)
    return bytes.fromhex(request.replace(" ", ""))


def classic_can(isotp_params: Mapping[str, object]) -> bool:
    """Whether resolved ISO-TP params (``build_isotp_params``) mean 8-byte classic frames."""
    return not isotp_params.get("can_fd") and isotp_params.get("tx_data_length", 8) == 8


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
    # What the transport could not do for this request, for the report to say
    # (an adapter that refused a command, so some replies may be missing).
    notes: list[str] = field(default_factory=list)


def record_functional_losses(diag: TransportStats, result: FunctionalResult) -> None:
    """Tally each reply that lost frames as a drop on the terminal's diagnostics."""
    for _ in range(result.lost):
        diag.record(
            CAT_DROP,
            ecu=f"0x{result.tx_id:X}",
            pid=result.request.hex().upper(),
            detail="a functional reply lost frames",
        )


class FunctionalCollector:
    """Collect the replies to one functional request, frame by frame.

    Times are the caller's monotonic clock, in seconds; ``started`` is when the
    request went out.

    Only a First Frame that opens an answer to the request (its first byte the
    request SID + 0x40, or 0x7F) asks for flow control and holds the window open:
    another tester's multi-frame request falls in the same id block, and flow
    control for it would land on some ECU's request id. Each held message is
    timed on its own last frame, so a responder that stalls mid-message is given
    up on after N_Cr however busy the others are.
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
        self._hard_stop = started + window + ABSOLUTE_CAP_S
        # Responders mid-answer -> when their last frame arrived.
        self._open: dict[int, float] = {}
        # Responders whose answer stalled past N_Cr: the rest of that message is
        # ignored, and the reassembler counts it lost when it ends.
        self._abandoned: set[int] = set()

    def _answers(self, first_byte: bytes) -> bool:
        return first_byte in (
            bytes([positive_response_sid(self._result.request[0]) & 0xFF]),
            bytes([NEGATIVE_RESPONSE_SID]),
        )

    def _expire(self, now: float) -> None:
        for can_id, last in list(self._open.items()):
            if now >= last + CONSECUTIVE_FRAME_TIMEOUT_S:
                del self._open[can_id]
                self._abandoned.add(can_id)

    def feed(self, can_id: int, data: bytes, now: float, *, extended: bool | None = None) -> bool:
        """Take one received frame; True when it is a First Frame to flow-control.

        ``extended`` is the frame's own format (default: from the id's value).
        """
        if extended is None:
            extended = can_id > 0x7FF
        if not data or not accepts_response_id(can_id, extended, self._result.tx_id):
            return False
        self._expire(now)
        pci = data[0] >> 4
        if pci == _FLOW_CONTROL:
            return False
        if can_id in self._abandoned:
            if pci == _CONSECUTIVE_FRAME:
                return False
            self._abandoned.discard(can_id)
        malformed, truncated = self._stats.malformed, self._stats.truncated
        message = self._reassembler.feed((None, can_id, extended), data, now)
        if message is not None:
            self._open.pop(can_id, None)
            self._result.messages.append(
                FunctionalMessage(can_id, message.payload, round((now - self._started) * 1000))
            )
            if is_response_pending(message.payload, self._result.request[0]):
                self._deadline = max(
                    self._deadline, min(now + PENDING_EXTENSION_S, self._pending_cap)
                )
            return False
        if pci == _CONSECUTIVE_FRAME:
            if can_id in self._open:
                if self._stats.truncated == truncated:
                    self._open[can_id] = now
                else:
                    del self._open[can_id]
            return False
        # A single or first frame ends whatever this responder had open.
        self._open.pop(can_id, None)
        opened = is_first_frame(data) and self._stats.malformed == malformed
        if not opened or not self._answers(data[2:3]):
            return False
        self._open[can_id] = now
        return True

    def done(self, now: float) -> bool:
        """Whether to stop listening (see the module docstring)."""
        if now >= self._hard_stop:
            return True
        self._expire(now)
        return now >= self._deadline and not self._open

    def remaining(self, now: float) -> float:
        """Seconds until :meth:`done` could next become true (at least a tick)."""
        until = self._deadline - now
        if until <= 0 and self._open:
            until = min(self._open.values()) + CONSECUTIVE_FRAME_TIMEOUT_S - now
        return max(min(until, self._hard_stop - now), 0.005)

    def result(self) -> FunctionalResult:
        """The collected messages; a message still open at this point is lost."""
        self._reassembler.finish()
        self._result.lost = self._stats.lost
        return self._result
