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
