"""Tests for passive ISO-TP reassembly of a recorded frame stream."""

from __future__ import annotations

import can

from canlib.uds.isotp_passive import ReassemblyStats, reassemble
from tests._isotp_frames import flow_control, frame, message_frames

ECU = 0x7EC


def run(frames):
    stats = ReassemblyStats()
    return list(reassemble(frames, stats)), stats


class TestMessages:
    def test_single_frame_ignores_padding(self):
        msgs, stats = run(message_frames(ECU, bytes.fromhex("6101FF"), ts=1.5))
        assert [(m.payload.hex(), m.timestamp, m.arbitration_id) for m in msgs] == [
            ("6101ff", 1.5, ECU)
        ]
        assert stats.messages == 1 and stats.lost == 0

    def test_multi_frame_is_trimmed_to_declared_length(self):
        payload = bytes(range(20))
        msgs, stats = run(message_frames(ECU, payload))
        assert msgs[0].payload == payload  # the last frame's padding is not payload
        assert stats.messages == 1

    def test_timestamp_is_that_of_the_last_frame(self):
        frames = message_frames(ECU, bytes(range(20)))
        for i, f in enumerate(frames):
            f.timestamp = 10.0 + i
        msgs, _ = run(frames)
        assert msgs[0].timestamp == 10.0 + len(frames) - 1

    def test_sequence_number_wraps_after_fifteen(self):
        payload = bytes(i % 251 for i in range(130))
        msgs, stats = run(message_frames(ECU, payload))
        assert msgs[0].payload == payload and stats.lost == 0

    def test_flow_control_is_ignored(self):
        frames = [*message_frames(0x7E4, b"\x22\x01\x01"), flow_control(0x7E4)]
        msgs, stats = run(frames)
        assert len(msgs) == 1 and stats.malformed == 0

    def test_conversations_are_kept_apart_by_id_and_channel(self):
        a = message_frames(0x7EC, bytes(range(20)), channel=1)
        b = message_frames(0x7EA, bytes(range(40, 60)), channel=1)
        c = message_frames(0x7EC, bytes(range(80, 100)), channel=0)
        interleaved = [f for trio in zip(a, b, c, strict=True) for f in trio]
        msgs, stats = run(interleaved)
        assert sorted(m.payload[0] for m in msgs) == [0, 40, 80]
        assert stats.lost == 0

    def test_extended_and_standard_ids_are_distinct(self):
        std = frame(0x123, bytes([2, 0x62, 0x01]))
        ext = frame(0x123, bytes([2, 0x62, 0x02]), extended=True)
        msgs, _ = run([std, ext])
        assert {(m.is_extended_id, m.payload) for m in msgs} == {
            (False, b"\x62\x01"),
            (True, b"\x62\x02"),
        }


class TestLostFrames:
    def test_sequence_gap_drops_the_message_once(self):
        frames = message_frames(ECU, bytes(range(40)))
        del frames[2]  # the trailing consecutive frames then follow a gap
        msgs, stats = run(frames)
        assert msgs == []
        assert (stats.truncated, stats.orphan_runs) == (1, 0)

    def test_lost_first_frame_counts_one_orphan_run(self):
        msgs, stats = run(message_frames(ECU, bytes(range(40)))[1:])
        assert msgs == [] and (stats.truncated, stats.orphan_runs) == (0, 1)

    def test_a_new_message_abandons_an_unfinished_one(self):
        unfinished = message_frames(ECU, bytes(range(40)))[:2]
        msgs, stats = run([*unfinished, *message_frames(ECU, b"\x62\x01\x01")])
        assert [m.payload for m in msgs] == [b"\x62\x01\x01"]
        assert stats.truncated == 1

    def test_message_still_open_at_the_end_is_truncated(self):
        msgs, stats = run(message_frames(ECU, bytes(range(40)))[:3])
        assert msgs == [] and stats.truncated == 1

    def test_next_message_after_a_loss_is_unaffected(self):
        broken = message_frames(ECU, bytes(range(40)))
        del broken[1]
        msgs, stats = run([*broken, *message_frames(ECU, bytes(range(20)))])
        assert [len(m.payload) for m in msgs] == [20]
        assert stats.lost == 1

    def test_unreadable_headers_are_counted_not_guessed(self):
        zero_length_single = frame(ECU, bytes([0x00, 0x62]))
        claims_more_than_it_carries = can.Message(
            arbitration_id=ECU, data=bytes([0x05, 0x62]), is_extended_id=False
        )
        first_frame_that_fits_in_a_single = frame(ECU, bytes([0x10, 0x05, 1, 2, 3, 4, 5]))
        unknown_pci = frame(ECU, bytes([0x50]))
        msgs, stats = run(
            [
                zero_length_single,
                claims_more_than_it_carries,
                first_frame_that_fits_in_a_single,
                unknown_pci,
            ]
        )
        assert msgs == [] and stats.malformed == 4


class TestTrackedIds:
    BROADCAST = 0x4E2

    def test_broadcast_noise_is_not_counted_as_lost_replies(self):
        # A P-CAN log mixes diagnostic replies with broadcast frames whose first byte is
        # often an ISO-TP-looking PCI; only the tracked ids' losses may be tallied.
        noise = [
            frame(self.BROADCAST, bytes([0x25, 0x4A])),  # a "consecutive frame"
            frame(self.BROADCAST, bytes([0x10, 0x20, 1, 2])),  # a "first frame", never finished
            frame(self.BROADCAST, bytes([0xF0])),  # an unknown PCI
        ]
        stats = ReassemblyStats()
        reply = message_frames(ECU, bytes(range(20)))
        msgs = list(reassemble([*noise, *reply], stats, tracked={ECU}))
        assert [m.arbitration_id for m in msgs] == [ECU]
        assert (stats.lost, stats.malformed, stats.messages) == (0, 0, 1)

    def test_untracked_messages_are_still_yielded(self):
        stats = ReassemblyStats()
        msgs = list(reassemble(message_frames(self.BROADCAST, b"\x62\x01"), stats, tracked={ECU}))
        assert [m.payload for m in msgs] == [b"\x62\x01"]
        assert stats.messages == 0
