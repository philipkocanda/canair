"""Regression tests for how parse_uds_response reads ELM327 adapter text.

Each case is a reply shape the parser once misread: a data line dropped as a flow
control echo, an interim ResponsePending frame glued onto the answer, a stale line
taken for the ISO-TP length, frame lines printed with spaces, and so on.
"""

import pytest

from canlib.uds.parse import ADAPTER_STATUS, parse_uds_response
from canlib.uds.response import (
    CAT_BUS,
    CAT_DECODE,
    CAT_DROP,
    CAT_NO_DATA,
    CAT_STALE,
    classify_error,
    classify_response,
)


class TestFlowControlEcho:
    @pytest.mark.parametrize(
        "reply",
        [
            "620F0012",  # 22 0F00 read: bytes 3-4 of the hex text spell "F0"
            "62BF0105",
            "410F05",  # OBD-II intake air temperature, data byte below 0x10
        ],
    )
    def test_a_short_reply_with_f0_inside_is_data_not_an_echo(self, reply):
        r = parse_uds_response(reply)
        assert r["ok"] is True
        assert r["hex"] == reply

    @pytest.mark.parametrize("echo", ["F00", "F0 00 00", "770 F00", "7E0 F0 00 00"])
    def test_a_flow_control_echo_is_still_dropped(self, echo):
        r = parse_uds_response(f"{echo}\n62C00BFF")
        assert r["ok"] is True
        assert r["hex"] == "62C00BFF"


class TestResponsePending:
    def test_an_interim_pending_line_ahead_of_the_answer_is_dropped(self):
        r = parse_uds_response("7F2278\n62F19041", expected_sid=0x22, expected_echo=b"\xf1\x90")
        assert r["ok"] is True
        assert r["hex"] == "62F19041"

    def test_pending_lines_ahead_of_a_multi_frame_answer_are_dropped(self):
        r = parse_uds_response("7F 21 78\n7F 21 78\n00A\n0:6101AABBCCDD\n1:EEFF0011")
        assert r["ok"] is True
        assert r["hex"] == "6101AABBCCDDEEFF0011"

    def test_a_pending_frame_alone_is_reported_as_its_nrc(self):
        r = parse_uds_response("7F2278", expected_sid=0x22)
        assert r["nrc"] == 0x78

    def test_a_pending_line_between_replies_is_not_a_competing_answer(self):
        # A late reply to an earlier read, then this read's pending frame and its answer.
        raw = "6201AABB\n7F2278\n620202CC\n"
        r = parse_uds_response(raw, expected_sid=0x22, expected_echo=b"\x02\x02", request="220202")
        assert r["ok"] is True
        assert r["hex"] == "620202CC"

    def test_a_reply_containing_the_pending_bytes_is_not_dropped(self):
        r = parse_uds_response("62017F2278", expected_sid=0x22, expected_echo=b"\x01\x7f")
        assert r["ok"] is True
        assert r["hex"] == "62017F2278"


class TestAdapterLines:
    def test_searching_is_a_progress_line(self):
        # What an adapter on automatic protocol detection (ATSP0) prints first.
        r = parse_uds_response("SEARCHING...\n410C1AF8")
        assert r["ok"] is True
        assert r["hex"] == "410C1AF8"

    @pytest.mark.parametrize("line", sorted(ADAPTER_STATUS))
    def test_every_status_line_classifies_the_same_from_its_message(self, line):
        r = parse_uds_response(line)
        kind, message = ADAPTER_STATUS[line]
        assert r["error_kind"] == kind
        assert classify_error(message) == kind
        assert classify_response({"raw": line, "ok": False, "error": message}) == kind

    def test_an_adapter_refusal_is_a_bus_failure(self):
        # A complete, prompt-terminated refusal: nothing is left queued, so it must not
        # read as a dirty pipe (decode), which would trigger a resync.
        assert parse_uds_response("?")["error_kind"] == CAT_BUS
        assert classify_error("Unknown command") == CAT_BUS

    def test_bus_busy_is_a_bus_failure(self):
        assert parse_uds_response("BUS BUSY")["error_kind"] == CAT_BUS


# One reply per failure message the parser can produce, with the category it stamps.
_EVERY_FAILURE = [
    ("", {}, CAT_NO_DATA),  # Empty response
    ("22F190", {"request": "22F190"}, CAT_NO_DATA),  # only the request echo
    ("62F19041\n6101AA", {}, CAT_STALE),  # separate replies, no request
    ("62F19041\n62F19042", {"expected_sid": 0x22, "expected_echo": b"\xf1\x90"}, CAT_STALE),
    ("2101\n6102AA", {"request": "2102"}, CAT_STALE),  # another request's echo
    ("5001\n6101AA", {"request": "2102"}, CAT_STALE),  # no line answers
    ("00A\n0:6101AABBCCDD\n1:EEFFGG11", {}, CAT_DECODE),  # non-hex frame line
    ("00A\n1:6101AABBCCDD\n0:EEFF0011", {}, CAT_DROP),  # non-contiguous counters
    ("GARBAGE", {}, CAT_DECODE),  # non-hex response
    ("6", {}, CAT_DECODE),  # too short
    ("62F1904", {}, CAT_DECODE),  # odd hex length
    ("014\n0:6101AABBCCDD\n1:EEFF0011", {}, CAT_DROP),  # truncated
    ("7F22", {}, CAT_DECODE),  # negative response too short
    ("7F2131", {"expected_sid": 0x22}, CAT_STALE),  # NRC echo mismatch
    ("6101AA", {"expected_sid": 0x22}, CAT_STALE),  # SID mismatch
    ("6201", {"expected_sid": 0x22, "expected_echo": b"\xf1\x90"}, CAT_DECODE),  # too short
    ("62F18F41", {"expected_sid": 0x22, "expected_echo": b"\xf1\x90"}, CAT_STALE),  # echo
]


@pytest.mark.parametrize(("raw", "kwargs", "kind"), _EVERY_FAILURE)
def test_every_failure_message_classifies_as_the_kind_it_is_stamped(raw, kwargs, kind):
    # The recorder falls back to classifying the message when no kind was stamped, so
    # the two must never disagree.
    r = parse_uds_response(raw, **kwargs)
    assert r["ok"] is False
    assert r["error_kind"] == kind
    assert classify_error(r["error"]) == kind


class TestMultiFrameText:
    def test_frame_lines_printed_with_spaces_reassemble(self):
        # ATS1 (spaces on) prints `0: 61 01 …`.
        r = parse_uds_response("00A\n0: 61 01 AA BB CC DD\n1: EE FF 00 11")
        assert r["ok"] is True
        assert r["hex"] == "6101AABBCCDDEEFF0011"
        assert r["isotp_declared_len"] == 10

    def test_a_stale_short_line_is_not_taken_for_the_length(self):
        # `5001` (a late 10 01 reply) is bare hex too; only the last one before the
        # frames is the First Frame length.
        r = parse_uds_response("5001\n00A\n0:6101AABBCCDD\n1:EEFF0011")
        assert r["ok"] is True
        assert r["isotp_declared_len"] == 10

    def test_a_malformed_frame_line_is_a_decode_failure(self):
        r = parse_uds_response("00A\n0:6101AABBCCDD\n1:EEFFGG11")
        assert r["ok"] is False
        assert r["error_kind"] == CAT_DECODE

    def test_trailing_padding_is_cut_to_the_declared_length(self):
        r = parse_uds_response("009\n0:6101AABBCCDD\n1:EEFF00AAAAAA")
        assert r["hex"] == "6101AABBCCDDEEFF00"

    @pytest.mark.parametrize("token", ["0", "000", "007"])
    def test_a_length_below_a_first_frame_is_not_a_length(self, token):
        # Regression: a declared length of 0 cut the payload to nothing and raised
        # IndexError. A First Frame always declares at least 8 bytes.
        r = parse_uds_response(f"{token}\n0:620101\n")
        assert r["ok"] is True
        assert r["hex"] == "620101"
        assert "isotp_declared_len" not in r


class TestMalformedPayloads:
    def test_a_truncated_negative_response_is_not_a_positive(self):
        r = parse_uds_response("7F22")
        assert r["ok"] is False
        assert r["error_kind"] == CAT_DECODE
        assert "nrc" not in r

    def test_odd_hex_length_is_a_decode_failure(self):
        r = parse_uds_response("62F1904")
        assert r["ok"] is False
        assert r["error_kind"] == CAT_DECODE
        assert classify_error(r["error"]) == CAT_DECODE

    def test_only_the_request_echo_is_no_data(self):
        r = parse_uds_response("22F190", request="22F190")
        assert r["error_kind"] == CAT_NO_DATA


class TestSeveralSingleFrameLines:
    """Several unprefixed lines are separate messages and are never joined."""

    def test_a_late_reply_ahead_of_the_answer_is_set_aside(self):
        # A late 6101… from the previous request, then this request's own answer.
        r = parse_uds_response("6101AABB\n6102CCDD", expected_sid=0x21, expected_echo=b"\x02")
        assert r["ok"] is True
        assert r["hex"] == "6102CCDD"
        assert r["isotp_frame_count"] == 1

    def test_the_answer_is_picked_by_the_request_alone(self):
        r = parse_uds_response("2102\n5003\n6102CCDD", request="2102")
        assert r["ok"] is True
        assert r["hex"] == "6102CCDD"

    def test_the_answer_is_picked_by_the_requests_identifier(self):
        # Regression: with only `request` given, both 62 replies answered "service 22"
        # and were refused as two ECUs; the DID tells them apart.
        r = parse_uds_response("620101AA\n620202BB\n", request="220202")
        assert r["ok"] is True
        assert r["hex"] == "620202BB"

    def test_the_frame_count_digit_is_not_part_of_the_identifier(self):
        r = parse_uds_response("2202021\n620101AA\n620202BB", request="2202021")
        assert r["ok"] is True
        assert r["hex"] == "620202BB"

    def test_no_line_answering_the_request_alone_is_refused(self):
        # Without an expected SID nothing re-checks the picked line, so a block with
        # no answer in it must not pass as the first line.
        r = parse_uds_response("5001\n6101AA", request="2102")
        assert r["ok"] is False
        assert r["error_kind"] == CAT_STALE
        assert classify_error(r["error"]) == CAT_STALE

    def test_two_ecus_answering_is_refused(self):
        r = parse_uds_response("62F19041\n62F19042", expected_sid=0x22, expected_echo=b"\xf1\x90")
        assert r["ok"] is False
        assert r["error_kind"] == CAT_STALE
        assert "more than one ECU" in r["error"]

    def test_the_same_reply_twice_is_one_answer(self):
        r = parse_uds_response("62F19041\n62F19041", expected_sid=0x22, expected_echo=b"\xf1\x90")
        assert r["ok"] is True
        assert r["hex"] == "62F19041"

    def test_no_line_answering_reports_the_first_mismatch(self):
        r = parse_uds_response("6101AA\n6103BB", expected_sid=0x21, expected_echo=b"\x02")
        assert r["ok"] is False
        assert "Echo mismatch" in r["error"]

    def test_an_nrc_for_this_service_is_the_answer(self):
        r = parse_uds_response("6101AA\n7F2131", expected_sid=0x21, expected_echo=b"\x02")
        assert r["nrc"] == 0x31

    def test_lines_without_any_request_to_judge_by_are_refused(self):
        r = parse_uds_response("62F19041\n6101AA")
        assert r["ok"] is False
        assert r["error_kind"] == CAT_STALE

    def test_a_non_hex_line_is_still_a_decode_failure(self):
        r = parse_uds_response("6101AA\nGARBAGE", expected_sid=0x21)
        assert r["error_kind"] == CAT_DECODE
