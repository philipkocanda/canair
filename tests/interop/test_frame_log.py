"""Tests for extracting UDS data-read replies from a recorded frame log."""

from __future__ import annotations

from datetime import datetime

import pytest

from canair.ecus.registry import load_ecus
from canair.interop.frame_log import ExtractedReply, extract_replies, stamp
from canair.profile import active
from tests.support.isotp_frames import flow_control, message_frames

BMS_TX, BMS_RX = 0x7E4, 0x7EC
BODY = bytes(range(1, 30))


@pytest.fixture(scope="module")
def ecus():
    return load_ecus(profile=active())


def at(frames, ts):
    for f in frames:
        f.timestamp = ts
    return frames


def exchange(request: str, reply: str, ts: float, *, tx: int = BMS_TX, rx: int = BMS_RX):
    """A request and its reply, the reply landing 10 ms later."""
    return [
        *at(message_frames(tx, bytes.fromhex(request)), ts),
        *at(message_frames(rx, bytes.fromhex(reply)), ts + 0.01),
    ]


class TestReplies:
    def test_filed_under_service_and_identifier_echoed(self, ecus):
        frames = [
            *exchange("220101", "620101" + BODY.hex(), 1.0),
            *exchange("2101", "6101" + BODY.hex(), 2.0),
        ]
        out = extract_replies(frames, ecus, profile=active())
        assert [(r.rx, r.pid, r.timestamp) for r in out.replies] == [
            ("0x7EC", "220101", 1.01),
            ("0x7EC", "2101", 2.01),
        ]
        assert out.replies[0].payload == "620101" + BODY.hex().upper()

    def test_reply_is_filed_by_its_echo_not_by_the_last_request(self, ecus):
        # A tool that pipelines requests (or interleaves a broadcast scan) has sent the
        # next request before the previous reply lands; the request must not name it.
        frames = [
            *at(message_frames(BMS_TX, bytes.fromhex("220101")), 1.0),
            *at(message_frames(BMS_TX, bytes.fromhex("220102")), 1.001),
            *at(message_frames(BMS_RX, bytes.fromhex("620101AABB")), 1.02),
            *at(message_frames(BMS_RX, bytes.fromhex("620102CCDD")), 1.03),
        ]
        out = extract_replies(frames, ecus, profile=active())
        assert [r.pid for r in out.replies] == ["220101", "220102"]

    def test_replies_without_any_request_in_the_log_are_kept(self, ecus):
        out = extract_replies(
            message_frames(BMS_RX, bytes.fromhex("620105AA")), ecus, profile=active()
        )
        assert [r.pid for r in out.replies] == ["220105"]

    def test_unrelated_services_are_counted_not_recorded(self, ecus):
        frames = [
            *message_frames(BMS_RX, bytes.fromhex("410D00")),  # OBD-II mode 01
            *message_frames(BMS_RX, bytes.fromhex("5903FF")),  # DTC report
        ]
        out = extract_replies(frames, ecus, profile=active())
        assert out.replies == []
        assert out.other_services == {0x41: 1, 0x59: 1}

    def test_ids_outside_the_registry_are_reported_when_they_answer_a_read(self, ecus):
        frames = [
            *message_frames(0x6AA, bytes.fromhex("620101AA")),
            *message_frames(0x6AA, bytes.fromhex("410D00")),  # broadcast noise: not mentioned
        ]
        out = extract_replies(frames, ecus, profile=active())
        assert out.replies == [] and out.unregistered == {0x6AA: 1}

    def test_request_traffic_is_not_mistaken_for_a_reply(self, ecus):
        out = extract_replies(
            message_frames(BMS_TX, bytes.fromhex("620101AA")), ecus, profile=active()
        )
        assert out.replies == []

    def test_each_bus_keeps_its_own_conversation(self, ecus):
        frames = exchange("220101", "620101AA", 1.0)
        for f in frames:
            f.channel = 1
        other_bus = at(message_frames(BMS_RX, bytes.fromhex("620102BB")), 1.5)
        for f in other_bus:
            f.channel = 0
        out = extract_replies([*frames, *other_bus], ecus, profile=active())
        assert sorted(r.pid for r in out.replies) == ["220101", "220102"]


class TestBatchedReads:
    def test_reply_to_a_batched_request_is_skipped(self, ecus):
        frames = exchange("2201010102", "620101AABB0102CCDD", 1.0)
        out = extract_replies(frames, ecus, profile=active())
        assert out.replies == [] and out.batched_skipped == 1

    def test_a_later_single_read_is_not_skipped(self, ecus):
        frames = [
            *exchange("2201010102", "620101AABB0102CCDD", 1.0),
            *exchange("220101", "620101AABB", 2.0),
        ]
        out = extract_replies(frames, ecus, profile=active())
        assert [r.pid for r in out.replies] == ["220101"]
        assert out.batched_skipped == 1

    def test_a_batch_reply_is_skipped_when_later_requests_were_pipelined(self, ecus):
        # The batch is no longer the last request when its reply lands.
        frames = [
            *at(message_frames(BMS_TX, bytes.fromhex("2201010102")), 1.0),
            *at(message_frames(BMS_TX, bytes.fromhex("220105")), 1.001),
            *at(message_frames(BMS_RX, bytes.fromhex("62010111010222")), 1.02),
            *at(message_frames(BMS_RX, bytes.fromhex("62010533")), 1.03),
        ]
        out = extract_replies(frames, ecus, profile=active())
        assert [(r.pid, r.payload) for r in out.replies] == [("220105", "62010533")]
        assert out.batched_skipped == 1

    def test_a_refused_batch_does_not_hold_up_the_single_reads_after_it(self, ecus):
        frames = [
            *exchange("2201010102", "7F2213", 1.0),
            *exchange("220101", "620101AABB", 1.1),
        ]
        out = extract_replies(frames, ecus, profile=active())
        assert [r.pid for r in out.replies] == ["220101"] and out.batched_skipped == 0

    def test_an_unanswered_batch_expires(self, ecus):
        frames = [
            *at(message_frames(BMS_TX, bytes.fromhex("2201010102")), 1.0),
            *exchange("220101", "620101AABB", 10.0),
        ]
        out = extract_replies(frames, ecus, profile=active())
        assert [r.pid for r in out.replies] == ["220101"]

    def test_a_batch_reply_whose_request_was_lost_splits_on_learned_lengths(self, ecus):
        frames = [
            *exchange("220101", "620101AABB", 1.0),
            *exchange("220102", "620102CCDD", 1.1),
            *at(message_frames(BMS_RX, bytes.fromhex("620101AABB0102CCDD")), 2.0),
        ]
        out = extract_replies(frames, ecus, profile=active())
        assert [r.pid for r in out.replies] == ["220101", "220102"]
        assert out.batched_skipped == 1


class TestPipelinedNegatives:
    def test_an_nrc_answers_the_oldest_outstanding_request(self, ecus):
        frames = [
            *at(message_frames(BMS_TX, bytes.fromhex("22C101")), 1.0),
            *at(message_frames(BMS_TX, bytes.fromhex("22C1010102")), 1.001),
            *at(message_frames(BMS_RX, bytes.fromhex("7F2231")), 1.02),
        ]
        out = extract_replies(frames, ecus, keep_negative=True, profile=active())
        assert [r.pid for r in out.replies] == ["22C101"]


class TestNegativeResponses:
    NRC = "7F2231"

    def test_skipped_by_default(self, ecus):
        out = extract_replies(exchange("22C101", self.NRC, 1.0), ecus, profile=active())
        assert out.replies == [] and out.negatives_skipped == 1

    def test_kept_on_request_against_the_single_identifier_asked(self, ecus):
        out = extract_replies(
            exchange("22C101", self.NRC, 1.0), ecus, keep_negative=True, profile=active()
        )
        (reply,) = out.replies
        assert (reply.pid, reply.payload) == ("22C101", "7F2231")
        assert reply.notes == "NRC 0x31 requestOutOfRange"

    def test_a_kept_nrc_is_named_for_the_ecus_protocol(self, ecus):
        # 0x72 is transferAborted on KWP2000 and generalProgrammingFailure on UDS.
        for protocol, name in (
            ("KWP2000", "transferAborted"),
            ("UDS", "generalProgrammingFailure"),
        ):
            registry = {BMS_TX: {**ecus[BMS_TX], "id_protocol": protocol}}
            out = extract_replies(
                exchange("22C101", "7F2272", 1.0), registry, keep_negative=True, profile=active()
            )
            assert out.replies[0].notes == f"NRC 0x72 {name}"

    def test_not_kept_when_the_request_was_a_batch_or_a_different_service(self, ecus):
        batch = extract_replies(
            exchange("22C1010102", self.NRC, 1.0), ecus, keep_negative=True, profile=active()
        )
        other = extract_replies(
            exchange("2101", self.NRC, 1.0), ecus, keep_negative=True, profile=active()
        )
        assert batch.replies == [] and other.replies == []

    def test_response_pending_is_never_a_result(self, ecus):
        frames = [
            *exchange("220101", "7F2278", 1.0),
            *at(message_frames(BMS_RX, bytes.fromhex("620101AA")), 1.5),
        ]
        out = extract_replies(frames, ecus, keep_negative=True, profile=active())
        assert [r.pid for r in out.replies] == ["220101"]
        assert out.negatives_skipped == 0


class TestLostFrames:
    def test_a_truncated_multi_frame_reply_is_never_recorded(self, ecus):
        reply = message_frames(BMS_RX, bytes.fromhex("620101") + BODY)
        del reply[1]
        out = extract_replies([*reply, flow_control(BMS_TX)], ecus, profile=active())
        assert out.replies == [] and out.lost == 1

    def test_losses_on_request_ids_are_not_counted(self, ecus):
        # A lost multi-frame request is not a capture that went missing.
        request = message_frames(BMS_TX, bytes.fromhex("22") + bytes(range(1, 21)))
        del request[1]
        out = extract_replies(
            [*request, *exchange("220101", "620101AA", 1.0)], ecus, profile=active()
        )
        assert [r.pid for r in out.replies] == ["220101"]
        assert out.lost == 0 and out.reassembly.truncated == 0


class TestStamp:
    def test_times_follow_the_log_clock_from_the_first_reply(self):
        replies = [
            ExtractedReply(100.0, "0x7EC", "220101", "6201"),
            ExtractedReply(100.2504, "0x7EC", "220101", "6201", "n"),
            ExtractedReply(161.0, "0x7EC", "220102", "6202"),
        ]
        out = stamp(replies, datetime(2023, 1, 12, 9, 30, 0))
        assert [r["time"] for r in out] == ["09:30:00.000", "09:30:00.250", "09:31:01.000"]
        assert out[1]["notes"] == "n" and "notes" not in out[0]

    def test_running_past_midnight_is_refused(self):
        replies = [
            ExtractedReply(0.0, "0x7EC", "220101", "6201"),
            ExtractedReply(7200.0, "0x7EC", "220101", "6201"),
        ]
        with pytest.raises(ValueError, match="past midnight"):
            stamp(replies, datetime(2023, 1, 12, 23, 0, 0))


def test_broadcast_frames_do_not_inflate_the_loss_count(ecus):
    # A P-CAN log where the car's own telematics polls the BMS among broadcast traffic.
    noise = [*message_frames(0x4E2, bytes(range(30)))[1:], *message_frames(0x5EC, b"\x25")]
    out = extract_replies(
        [*noise, *exchange("220101", "620101" + BODY.hex(), 1.0)], ecus, profile=active()
    )
    assert [r.pid for r in out.replies] == ["220101"]
    assert out.lost == 0 and out.reassembly.malformed == 0
