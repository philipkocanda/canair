"""Passive ISO-TP (ISO 15765-2) reassembly of a recorded CAN frame stream.

The live clients (:mod:`canlib.transport`) are one side of an ISO-TP conversation:
they send the flow control and know which message they are waiting for. A *log*
holds both directions interleaved, with no flow control to answer and no request
to pair against, so this is a separate, much smaller state machine: it only
follows each arbitration ID's First/Consecutive frames and hands back the
messages that completed.

Every lost frame is counted, never repaired. A sequence gap, a new message that
starts before the previous one finished, and a consecutive frame with no First
Frame each cost exactly one message, tallied once in :class:`ReassemblyStats`
(a lost message's trailing consecutive frames are not counted again). A truncated
payload is therefore never handed to the caller, which keeps a half-captured
multi-frame reply from becoming a believable-looking capture.

Scope: classic CAN (8-byte frames) with *normal* addressing, where the PCI byte is
the first data byte. Extended addressing (the PCI byte follows a target-address
byte) and the CAN-FD / 32-bit-length escapes are not understood and are counted as
``malformed`` rather than guessed at.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
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


@dataclass
class _Partial:
    length: int
    buf: bytearray
    next_seq: int = 1


def reassemble(frames: Iterable[can.Message], stats: ReassemblyStats) -> Iterator[IsoTpMessage]:
    """Yield every ISO-TP message in ``frames``, in order of completion.

    ``stats`` is updated as the generator is consumed and is final once it is
    exhausted (a message still open at the end of the stream is then counted as
    truncated). Flow-control frames (always tester to ECU) carry no payload and
    are ignored.
    """
    partial: dict[_Key, _Partial] = {}
    # Keys whose message was lost part-way: the rest of that same message's
    # consecutive frames are expected, and must not be counted as an orphan run.
    lost_run: set[_Key] = set()

    for frame in frames:
        data = bytes(frame.data)
        if not data:
            continue
        key: _Key = (
            getattr(frame, "channel", None),
            frame.arbitration_id,
            bool(frame.is_extended_id),
        )
        ts = float(frame.timestamp or 0.0)
        pci = data[0] >> 4

        if pci == _FLOW_CONTROL:
            continue

        if pci == _CONSECUTIVE:
            open_msg = partial.get(key)
            if open_msg is None:
                if key not in lost_run:
                    stats.orphan_runs += 1
                    lost_run.add(key)
                continue
            if data[0] & 0x0F != open_msg.next_seq & 0x0F:
                del partial[key]
                stats.truncated += 1
                lost_run.add(key)
                continue
            open_msg.buf += data[1:]
            open_msg.next_seq += 1
            if len(open_msg.buf) >= open_msg.length:
                del partial[key]
                stats.messages += 1
                yield IsoTpMessage(ts, *key, bytes(open_msg.buf[: open_msg.length]))
            continue

        # A single or first frame on a key still mid-message: the earlier
        # message's remaining frames never arrived.
        if partial.pop(key, None) is not None:
            stats.truncated += 1
        lost_run.discard(key)

        if pci == _SINGLE:
            size = data[0] & 0x0F
            if size == 0 or size > len(data) - 1:
                stats.malformed += 1
                continue
            stats.messages += 1
            yield IsoTpMessage(ts, *key, data[1 : 1 + size])
        elif pci == _FIRST:
            length = ((data[0] & 0x0F) << 8) | data[1] if len(data) > 1 else 0
            if length <= 7:
                stats.malformed += 1  # 0 is the 32-bit escape; 1-7 belongs in a single frame
                continue
            partial[key] = _Partial(length, bytearray(data[2:]))
        else:
            stats.malformed += 1

    stats.truncated += len(partial)
