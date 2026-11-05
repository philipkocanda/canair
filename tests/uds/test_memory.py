"""Tests for canair.uds.memory — the ReadMemoryByAddress request, reply and NRC meanings."""

import pytest

from canair.uds.memory import (
    KWP_FORMAT,
    MEMORY_SIGNAL_MAX_BYTES,
    MemoryFormat,
    MemoryRequestError,
    RegionResult,
    classify_memory_reply,
    exceeds_signal_cap,
    format_from_alfid,
    is_memory_request,
    memory_read,
    memory_request,
    parse_format,
    parse_memory_request,
    parse_memory_token,
    token_names,
)
from canair.uds.protocol import KWP, UDS


class TestFormat:
    @pytest.mark.parametrize("address_bytes", range(1, 9))
    @pytest.mark.parametrize("size_bytes", range(1, 5))
    def test_alfid_round_trip(self, address_bytes, size_bytes):
        fmt = MemoryFormat(address_bytes, size_bytes)
        assert fmt.alfid == (size_bytes << 4) | address_bytes
        assert format_from_alfid(fmt.alfid) == fmt
        assert parse_format(fmt.name) == fmt

    @pytest.mark.parametrize("alfid", [0x00, 0x10, 0x04, 0x51, 0x19])
    def test_unusable_alfid_refused(self, alfid):
        with pytest.raises(MemoryRequestError):
            format_from_alfid(alfid)

    def test_kwp_form(self):
        assert parse_format("kwp") == KWP_FORMAT
        assert KWP_FORMAT.alfid is None and KWP_FORMAT.name == "kwp"
        assert KWP_FORMAT.request_length == 5

    @pytest.mark.parametrize("text", ["2", "244", "ZZ", "", "uds"])
    def test_malformed_format_refused(self, text):
        with pytest.raises(MemoryRequestError):
            parse_format(text)


class TestRequest:
    def test_uds_request(self):
        assert memory_request(parse_format("24"), 0xFEDE0000, 16) == "2324FEDE00000010"
        assert memory_request(parse_format("13"), 0x100, 4) == "231300010004"

    def test_kwp_request(self):
        assert memory_request(KWP_FORMAT, 0x100, 4) == "2300010004"

    def test_address_too_wide_for_the_format(self):
        with pytest.raises(MemoryRequestError, match="does not fit"):
            memory_request(parse_format("13"), 0x1000000, 1)

    @pytest.mark.parametrize("size", [0, 256])
    def test_size_outside_the_format(self, size):
        with pytest.raises(MemoryRequestError):
            memory_request(parse_format("13"), 0, size)

    def test_read_past_the_address_space(self):
        with pytest.raises(MemoryRequestError, match="past"):
            memory_request(parse_format("11"), 0xFF, 2)

    @pytest.mark.parametrize(
        ("fmt", "address", "size"),
        [("24", 0xFEDE0000, 0x80), ("13", 0, 1), ("44", 0x40000000, 0xFFE), ("kwp", 0x100, 4)],
    )
    def test_round_trip(self, fmt, address, size):
        read = memory_read(parse_format(fmt), address, size)
        proto = KWP if fmt == "kwp" else UDS
        assert parse_memory_request(read.request, proto) == read
        assert read.reply_length == 1 + size

    def test_token(self):
        assert memory_read(parse_format("24"), 0x1A2B0, 4).token == "23/0001A2B0+04"
        assert memory_read(KWP_FORMAT, 0x100, 4).token == "23/000100+04"


class TestParse:
    def test_not_a_memory_read(self):
        assert parse_memory_request("22F190") is None
        assert not is_memory_request("2101")
        assert is_memory_request("2324FEDE00000010")

    def test_kwp_needs_five_bytes(self):
        assert parse_memory_request("23000100", KWP) is None

    def test_uds_length_must_match_its_alfid(self):
        assert parse_memory_request("2324FEDE0000", UDS) is None

    def test_zero_size_is_no_read(self):
        assert parse_memory_request("2324FEDE00000000", UDS) is None

    def test_unknown_protocol_takes_the_only_reading(self):
        read = parse_memory_request("2324FEDE00000010")
        assert read is not None and read.size == 16

    def test_unknown_protocol_refuses_an_ambiguous_size(self):
        # 23 21 00 00 FF: UDS ALFID 21 reads 0x00FF bytes at 0x00; KWP2000 reads 0xFF
        # bytes at 0x210000. Both are 255 bytes, so the size is still known.
        assert parse_memory_request("23210000FF").size == 255
        # 23 21 00 01 00: UDS reads 0x0100 bytes, KWP2000 0x00 (no read): UDS only.
        assert parse_memory_request("2321000100").size == 0x100
        # 23 21 00 00 10 and 23 21 00 10 01 differ: 16 vs 16, then 4097 vs 1.
        assert parse_memory_request("2321001001") is None


class TestToken:
    def test_parse(self):
        assert parse_memory_token("23/FEDE0000+10") == (0xFEDE0000, 0x10)
        assert parse_memory_token("22/F190") is None

    @pytest.mark.parametrize("token", ["23/FEDE0000", "23/+10", "23/XY+10", "23/00+00"])
    def test_malformed(self, token):
        with pytest.raises(MemoryRequestError):
            parse_memory_token(token)

    def test_names_a_defined_read_in_any_format(self):
        assert token_names("23/FEDE0000+10", "2324FEDE00000010")
        assert token_names("23/100+4", "2300010004")
        assert not token_names("23/FEDE0000+10", "2324FEDE00000020")
        assert not token_names("23/FEDE0000+10", "22F190")


class TestSignalCap:
    def test_cap(self):
        fmt = parse_format("24")
        assert not exceeds_signal_cap(memory_request(fmt, 0, MEMORY_SIGNAL_MAX_BYTES))
        assert exceeds_signal_cap(memory_request(fmt, 0, MEMORY_SIGNAL_MAX_BYTES + 1))
        assert exceeds_signal_cap(memory_request(KWP_FORMAT, 0, 0x80))
        assert not exceeds_signal_cap("22F190")


class TestClassify:
    @pytest.mark.parametrize(
        ("response", "result"),
        [
            ({"ok": True}, RegionResult.READABLE),
            ({"ok": False}, RegionResult.NO_ANSWER),
            ({"ok": False, "nrc": 0x31}, RegionResult.OUT_OF_RANGE),
            ({"ok": False, "nrc": 0x33}, RegionResult.LOCKED),
            ({"ok": False, "nrc": 0x22}, RegionResult.CONDITIONS),
            ({"ok": False, "nrc": 0x7F}, RegionResult.WRONG_SESSION),
            ({"ok": False, "nrc": 0x80}, RegionResult.WRONG_SESSION),
            ({"ok": False, "nrc": 0x11}, RegionResult.UNSUPPORTED),
            ({"ok": False, "nrc": 0x13}, RegionResult.BAD_FORMAT),
            ({"ok": False, "nrc": 0x79}, RegionResult.BAD_FORMAT),
            ({"ok": False, "nrc": 0x14}, RegionResult.TOO_LARGE),
            ({"ok": False, "nrc": 0x53}, RegionResult.TOO_LARGE),
            ({"ok": False, "nrc": 0x99}, RegionResult.REFUSED),
        ],
    )
    def test_nrc_meanings(self, response, result):
        assert classify_memory_reply(response) is result
