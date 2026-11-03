"""Passive ISO-TP (ISO 15765-2) reassembly of a recorded CAN frame stream.

The live clients (:mod:`canlib.transport`) are one side of an ISO-TP conversation:
they send the flow control and know which message they are waiting for. A *log*
holds both directions interleaved, with no flow control to answer and no request
to pair against, so this is a separate, much smaller state machine: it only
follows each arbitration ID's First/Consecutive frames and hands back the
messages that completed. :class:`Reassembler` is the same rules fed one frame at
a time, for the functional responder sweep, which listens to many ECUs at once.

Every lost frame is counted, never repaired. A sequence gap, a new message that
starts before the previous one finished, a consecutive frame with no First Frame
and a First Frame this reader cannot use each cost exactly one message, tallied
once in :class:`ReassemblyStats` (a lost message's trailing consecutive frames are
not counted again). Those trailing frames are told from the next message's by their
sequence number: every message's first consecutive frame is numbered 1, so a 1 that
does not follow a 0 starts another message. A truncated payload is therefore never
handed to the caller, which keeps a half-captured multi-frame reply from becoming a
believable-looking capture.

Scope: *normal* addressing, where the PCI byte is the first data byte, including the
32-bit First Frame length of a message over 4095 bytes. Extended addressing (the PCI
byte follows a target-address byte) and the CAN-FD single-frame length escape are
not understood and are counted as ``malformed`` rather than guessed at.
"""

from __future__ import annotations

from collections.abc import Container, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import can

_SINGLE, _FIRST, _CONSECUTIVE, _FLOW_CONTROL = 0, 1, 2, 3

# (channel, arbitration id, is_extended): the same number on another bus, or in
# the other frame format, is a different conversation.
_Key = tuple[int | str | None, int, bool]


@dataclass(frozen=True)
class IsoTpMessage:
    """One reassembled ISO-TP message and when its last frame arrived."""

    timestamp: float
    channel: int | str | None
    arbitration_id: int
    is_extended_id: bool
    payload: bytes


@dataclass
class ReassemblyStats:
    """What :func:`reassemble` saw, filled in as the generator is consumed."""

    messages: int = 0
    truncated: int = 0  # started, then abandoned (sequence gap, restart, or end of stream)
    orphan_runs: int = 0  # consecutive frames whose First Frame was never seen
    malformed: int = 0  # a PCI this reader does not understand

    @property
    def lost(self) -> int:
        """Messages known to be missing frames."""
        return self.truncated + self.orphan_runs


@dataclass(slots=True)
class _Partial:
    length: int
    buf: bytearray
    next_seq: int = 1


def is_first_frame(data: bytes) -> bool:
    """Whether ``data`` is an ISO-TP First Frame (the one a receiver must flow-control)."""
    return bool(data) and data[0] >> 4 == _FIRST


class Reassembler:
    """The incremental form of :func:`reassemble`, for a live receiver.

    Fed one frame at a time, so a caller that must act on a frame as it arrives
    (send flow control after a First Frame) can share the reassembly rules with
    the log reader. :meth:`finish` closes the stream: a message still open is
    then counted as truncated.
    """

    def __init__(self, stats: ReassemblyStats) -> None:
        self.stats = stats
        self._partial: dict[_Key, _Partial] = {}
        # Keys whose message was lost part-way, with the sequence number of the last
        # frame seen (0 for a First Frame): the rest of that same message's consecutive
        # frames are expected, and must not be counted as another orphan run.
        self._lost_run: dict[_Key, int] = {}

    @property
    def open(self) -> int:
        """Messages started but not yet complete."""
        return len(self._partial)

    def feed(self, key: _Key, data: bytes, ts: float) -> IsoTpMessage | None:
        """Take one frame; return the message it completes, if any."""
        if not data:
            return None
        stats, partial, lost_run = self.stats, self._partial, self._lost_run
        pci = data[0] >> 4

        if pci == _FLOW_CONTROL:
            return None

        if pci == _CONSECUTIVE:
            seq = data[0] & 0x0F
            open_msg = partial.get(key)
            if open_msg is not None and seq == open_msg.next_seq & 0x0F:
                open_msg.buf += data[1:]
                open_msg.next_seq += 1
                if len(open_msg.buf) >= open_msg.length:
                    del partial[key]
                    stats.messages += 1
                    return IsoTpMessage(ts, *key, bytes(open_msg.buf[: open_msg.length]))
                return None
            if open_msg is not None:
                del partial[key]
                stats.truncated += 1
                last: int | None = (open_msg.next_seq - 1) & 0x0F
            else:
                last = lost_run.get(key)
            if last is None or _starts_message(last, seq):
                stats.orphan_runs += 1
            lost_run[key] = seq
            return None

        # A single or first frame on a key still mid-message: the earlier
        # message's remaining frames never arrived.
        if partial.pop(key, None) is not None:
            stats.truncated += 1
        lost_run.pop(key, None)

        if pci == _SINGLE:
            size = data[0] & 0x0F
            if size == 0 or size > len(data) - 1:
                stats.malformed += 1
                return None
            stats.messages += 1
            return IsoTpMessage(ts, *key, data[1 : 1 + size])
        if pci == _FIRST:
            length, start = _first_frame_length(data)
            if length is None:
                stats.malformed += 1
                lost_run[key] = 0  # its consecutive frames are this one message, not another
                return None
            partial[key] = _Partial(length, bytearray(data[start:]))
            return None
        stats.malformed += 1
        return None

    def finish(self) -> None:
        """End of stream: every message still open lost its remaining frames."""
        self.stats.truncated += len(self._partial)
        self._partial.clear()


def _starts_message(last_seq: int, seq: int) -> bool:
    """Is a consecutive frame numbered ``seq`` the first of a new message?

    Every message numbers its first consecutive frame 1 (its First Frame counting as
    0), and within one message a 1 only ever follows a 0, at a wrap.
    """
    return seq == 1 and last_seq != 0


def _first_frame_length(data: bytes) -> tuple[int | None, int]:
    """``(message length, offset of the first payload byte)`` of a First Frame.

    The length is None when the frame cannot start a multi-frame message: a 12-bit
    length of 1-7 belongs in a single frame, and the 32-bit escape (a 12-bit length
    of 0) is only valid for a message too long for 12 bits.
    """
    if len(data) < 2:
        return None, 2
    length = ((data[0] & 0x0F) << 8) | data[1]
    if length:
        return (length if length > 7 else None), 2
    if len(data) < 6:
        return None, 6
    length = int.from_bytes(data[2:6], "big")
    return (length if length > 0xFFF else None), 6


def reassemble(
    frames: Iterable[can.Message],
    stats: ReassemblyStats,
    *,
    tracked: Container[int] | None = None,
) -> Iterator[IsoTpMessage]:
    """Yield every ISO-TP message in ``frames``, in order of completion.

    ``stats`` is updated as the generator is consumed and is final once it is
    exhausted (a message still open at the end of the stream is then counted as
    truncated). Flow-control frames (always tester to ECU) carry no payload and
    are ignored.

    ``tracked`` limits ``stats`` to those arbitration ids. Frames on other ids are
    still reassembled and yielded, but their losses are not tallied: on a bus that
    also carries broadcast traffic, most broadcast frames read as broken ISO-TP and
    would otherwise drown the real diagnostic losses.
    """
    ours = Reassembler(stats)
    others = ours if tracked is None else Reassembler(ReassemblyStats())
    for frame in frames:
        key: _Key = (
            getattr(frame, "channel", None),
            frame.arbitration_id,
            bool(frame.is_extended_id),
        )
        reassembler = ours if tracked is None or frame.arbitration_id in tracked else others
        message = reassembler.feed(key, bytes(frame.data), float(frame.timestamp or 0.0))
        if message is not None:
            yield message
    ours.finish()
    others.finish()
