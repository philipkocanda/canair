"""canair.uds.subfunctions: every constant names the value the display table names."""

from __future__ import annotations

from enum import IntEnum

import pytest

from canair.uds.subfunctions import (
    SUBFUNCTION_NAMES,
    DtcReportType,
    IoControlParameter,
    KwpIoControlParameter,
    KwpSessionType,
    RoutineControlType,
    SessionType,
    format_session_type,
    parse_session_type,
    subfunction_name,
)

SERVICE_OF: dict[type[IntEnum], int] = {
    SessionType: 0x10,
    KwpSessionType: 0x10,
    DtcReportType: 0x19,
    IoControlParameter: 0x2F,
    KwpIoControlParameter: 0x30,
    RoutineControlType: 0x31,
}


def _words(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _members(enums) -> list[IntEnum]:
    return [member for enum in enums for member in enum]


@pytest.mark.parametrize("member", _members(SERVICE_OF), ids=repr)
def test_every_constant_names_its_table_entry(member):
    name = SUBFUNCTION_NAMES[SERVICE_OF[type(member)]].get(member)
    assert name is not None and _words(name) == _words(member.name)


def test_kwp_io_control_is_not_named_with_the_uds_values():
    # SSF 14230-3 0x01 is reportCurrentState; the UDS 0x2F 0x01 is resetToDefault.
    assert subfunction_name(0x30, KwpIoControlParameter.REPORT_CURRENT_STATE) == (
        "reportCurrentState"
    )
    assert subfunction_name(0x2F, IoControlParameter.RESET_TO_DEFAULT) == "resetToDefault"


def test_a_member_is_its_byte():
    assert RoutineControlType.REQUEST_ROUTINE_RESULTS == 0x03
    assert f"{IoControlParameter.RETURN_CONTROL_TO_ECU:02X}" == "00"
    assert bytes([KwpSessionType.EXTENDED_DIAGNOSTIC_SESSION]) == b"\x83"


class TestSessionTypeText:
    @pytest.mark.parametrize(
        ("text", "value"), [("03", 3), ("0x3", 3), (" 81 ", 0x81), (0xC0, 0xC0)]
    )
    def test_parse(self, text, value):
        assert parse_session_type(text) == value

    @pytest.mark.parametrize("text", ["", "zz", "0x", "100", -1])
    def test_parse_refuses_what_is_not_one_byte(self, text):
        with pytest.raises(ValueError):
            parse_session_type(text)

    def test_format_is_two_hex_digits(self):
        assert format_session_type(SessionType.EXTENDED_DIAGNOSTIC_SESSION) == "03"
        assert format_session_type(0xC0) == "C0"
