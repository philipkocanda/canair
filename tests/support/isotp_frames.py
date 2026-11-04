"""Build synthetic ISO-TP frame streams for the passive-reassembly tests."""

from __future__ import annotations

import can


def frame(
    arb_id: int, data: bytes, ts: float = 0.0, channel: int | None = None, *, extended: bool = False
) -> can.Message:
    return can.Message(
        arbitration_id=arb_id,
        data=data.ljust(8, b"\xaa"),  # the padding a real ECU sends; PCI says how much counts
        timestamp=ts,
        channel=channel,
        is_extended_id=extended,
    )


def message_frames(
    arb_id: int, payload: bytes, ts: float = 0.0, channel: int | None = None
) -> list[can.Message]:
    """The frames of one ISO-TP message: a single frame, or First + Consecutive frames."""
    if len(payload) <= 7:
        return [frame(arb_id, bytes([len(payload)]) + payload, ts, channel)]
    frames = [
        frame(arb_id, bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6], ts)
    ]
    for n, start in enumerate(range(6, len(payload), 7), start=1):
        frames.append(frame(arb_id, bytes([0x20 | (n & 0x0F)]) + payload[start : start + 7], ts))
    for f in frames:
        f.channel = channel
    return frames


def flow_control(arb_id: int, ts: float = 0.0, channel: int | None = None) -> can.Message:
    return frame(arb_id, bytes([0x30, 0x00, 0x00]), ts, channel)
