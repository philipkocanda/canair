"""A memory reply repeats nothing of its request, so its length is its check."""

import pytest

from canair.uds.echo import ReplyCheck, reply_check, request_echo
from canair.uds.layout import layout_of
from canair.uds.parse import parse_uds_response
from canair.uds.payload import payload_echo_mismatch
from canair.uds.protocol import KWP
from canair.uds.response import CAT_STALE

REQUEST = "2324FEDE00000004"  # 4 bytes at 0xFEDE0000, ALFID 24


class TestReplyCheck:
    def test_memory_read_fixes_the_length(self):
        assert reply_check(REQUEST) == ReplyCheck(0x23, b"", 5)
        assert request_echo(REQUEST) == (0x23, b"")

    def test_kwp_form_with_the_protocol(self):
        assert reply_check("2300010004", KWP) == ReplyCheck(0x23, b"", 5)

    def test_other_reads_keep_their_echo_and_no_length(self):
        assert reply_check("22F190") == ReplyCheck(0x22, bytes.fromhex("F190"))
        assert reply_check("2101") == ReplyCheck(0x21, b"\x01")
        assert reply_check("3E00") is None


class TestParse:
    def test_exact_length_passes(self):
        resp = parse_uds_response("6300112233", expected_sid=0x23, request=REQUEST)
        assert resp["ok"] and resp["hex"] == "6300112233"

    @pytest.mark.parametrize("reply", ["63001122", "630011223344", "63"])
    def test_wrong_length_is_stale(self, reply):
        resp = parse_uds_response(reply, expected_sid=0x23, request=REQUEST)
        assert not resp["ok"] and resp["error_kind"] == CAT_STALE
        assert "Length mismatch" in resp["error"]

    def test_frame_count_digit_is_not_part_of_the_request(self):
        resp = parse_uds_response("6300112233", expected_sid=0x23, request=REQUEST + "1")
        assert resp["ok"]

    def test_nrc_is_still_reported(self):
        resp = parse_uds_response("7F2331", expected_sid=0x23, request=REQUEST)
        assert resp["nrc"] == 0x31

    def test_other_requests_unchanged(self):
        assert parse_uds_response("62F19041", expected_sid=0x22, request="22F190")["ok"]


class TestStoredPayload:
    def test_length_mismatch_is_reported(self):
        assert "memory read" in payload_echo_mismatch(REQUEST, "6300")

    def test_exact_length_is_fine(self):
        assert payload_echo_mismatch(REQUEST, "6300112233") is None


def test_bix_layout_has_no_header():
    layout = layout_of(bytes.fromhex("6300112233"))
    assert layout is not None and layout.header_bytes == 1
    assert layout.role_at(1) is None  # memory, not a header byte
