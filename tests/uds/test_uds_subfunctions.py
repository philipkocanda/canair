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
    subfunction_name,
)

SERVICE_OF: dict[type[IntEnum], int] = {
    SessionType: 0x10,
    KwpSessionType: 0x10,
    DtcReportType: 0x19,
    IoControlParameter: 0x2F,
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


@pytest.mark.xfail(
    strict=True,
    reason="0x30 is named with the UDS 0x2F values; SSF 14230-3 differs from 0x01 up "
    "(plans/2026-11-05-named-protocol-constants.md, phase 3)",
)
def test_kwp_io_control_parameters_match_their_names():
    for member in KwpIoControlParameter:
        name = subfunction_name(0x30, member)
        assert name is not None and _words(name) == _words(member.name)


def test_a_member_is_its_byte():
    assert RoutineControlType.REQUEST_ROUTINE_RESULTS == 0x03
    assert f"{IoControlParameter.RETURN_CONTROL_TO_ECU:02X}" == "00"
    assert bytes([KwpSessionType.EXTENDED_DIAGNOSTIC_SESSION]) == b"\x83"
