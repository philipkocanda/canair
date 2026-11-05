"""canair.uds.requests: each builder sends the bytes the hand-built request did."""

from __future__ import annotations

import pytest

from canair.uds.requests import (
    clear_dtc_request,
    io_control_request,
    kwp_io_control_request,
    routine_control_request,
    session_request,
)
from canair.uds.subfunctions import IoControlParameter, KwpSessionType, RoutineControlType


class TestSessionRequest:
    def test_takes_the_mode_as_hex_text_or_a_byte(self):
        assert session_request("03") == "1003"
        assert session_request("c0") == "10C0"
        assert session_request(KwpSessionType.STANDARD_DIAGNOSTIC_SESSION) == "1081"

    @pytest.mark.parametrize("mode", ["XY", "100", 0x100, -1])
    def test_refuses_a_mode_that_is_not_one_byte(self, mode):
        with pytest.raises(ValueError):
            session_request(mode)


class TestIoControlRequest:
    def test_short_term_adjustment_carries_its_state(self):
        request = io_control_request(
            0xB108, IoControlParameter.SHORT_TERM_ADJUSTMENT, bytes.fromhex("0A0A05")
        )
        assert request == "2FB108030A0A05"

    def test_return_control_has_no_state(self):
        assert io_control_request(0xBC07, IoControlParameter.RETURN_CONTROL_TO_ECU) == "2FBC0700"

    def test_a_did_wider_than_two_bytes_is_refused(self):
        with pytest.raises(ValueError, match="DID"):
            io_control_request(0x10000, IoControlParameter.RETURN_CONTROL_TO_ECU)


def test_kwp_io_control_request_passes_the_option_through():
    assert kwp_io_control_request(0x0A, b"\x00") == "300A00"
    assert kwp_io_control_request(0x0A, b"\x01\x00") == "300A0100"
    with pytest.raises(ValueError, match="LID"):
        kwp_io_control_request(0x100, b"\x00")


def test_routine_control_request():
    assert routine_control_request(RoutineControlType.REQUEST_ROUTINE_RESULTS, 0xF000) == (
        "3103F000"
    )
    assert routine_control_request(RoutineControlType.START_ROUTINE, 0x0203) == "31010203"


class TestClearDtcRequest:
    def test_the_group_is_two_bytes_on_kwp2000_and_three_on_uds(self):
        assert clear_dtc_request(0xFFFFFF, kwp=True) == "14FFFF"
        assert clear_dtc_request(0xFFFFFF, kwp=False) == "14FFFFFF"

    def test_a_narrow_group_is_zero_padded(self):
        assert clear_dtc_request(0x1234, kwp=False) == "14001234"
