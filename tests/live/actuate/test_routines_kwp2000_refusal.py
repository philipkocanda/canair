"""``canair routines`` refuses a KWP2000 ECU, which reads UDS RoutineControl as a routine start."""

from __future__ import annotations

import asyncio

import pytest

from canair.live.actuate.routines import mode_routines_tui
from canair.live.actuate.routines_execute import mode_routines_execute, routines_refusal
from canair.safety.blocklist import SafetyBlocked
from canair.uds.subfunctions import RoutineControlType
from tests.support.fakes import FakeTerminal

KWP_ECU = {
    "tx_id": 0x7E4,
    "identity": {"id_protocol": "KWP2000"},
    "routines": {"05": {"label": "Routine 05"}},
}
UNSET_ECU_WITH_LID = {"tx_id": 0x7E2, "routines": {"0A": {}, "1234": {}}}
UDS_ECU = {
    "tx_id": 0x745,
    "identity": {"id_protocol": "UDS"},
    "routines": {"12A1": {"label": "Self test"}},
}
DATA = {"ecus": {"BMS": KWP_ECU, "VCU": UNSET_ECU_WITH_LID, "BCM": UDS_ECU}}


def test_a_kwp2000_ecu_is_refused():
    reason = routines_refusal(DATA, "bms")
    assert reason is not None
    assert reason.startswith("BMS speaks KWP2000\n")
    assert "StartRoutineByLocalIdentifier" in reason
    assert "canair scan routines BMS --rid-range" in reason


def test_a_two_digit_routine_key_is_a_kwp2000_local_identifier():
    reason = routines_refusal(DATA, "VCU")
    assert reason is not None
    assert reason.startswith("VCU lists KWP2000 local identifiers under routines: (0A)")


def test_a_uds_ecu_and_an_unknown_one_are_not_refused():
    assert routines_refusal(DATA, "BCM") is None
    assert routines_refusal(DATA, "NOPE") is None


@pytest.mark.parametrize("sub_function", list(RoutineControlType))
def test_execute_sends_nothing_to_a_kwp2000_ecu(sub_function):
    term = FakeTerminal()
    with pytest.raises(SafetyBlocked, match="BMS speaks KWP2000"):
        asyncio.run(mode_routines_execute(term, DATA, "BMS", "05", sub_function=sub_function))
    assert term.sent == []
    assert term.headers == []


def test_the_tui_does_not_open_on_a_kwp2000_ecu(tmp_path):
    term = FakeTerminal()
    with pytest.raises(SafetyBlocked, match="BMS speaks KWP2000"):
        asyncio.run(mode_routines_tui(term, DATA, "BMS", profile=None))  # type: ignore[arg-type]
    assert term.sent == []


def test_execute_on_a_uds_ecu_still_sends():
    term = FakeTerminal(session_ok=True)
    asyncio.run(mode_routines_execute(term, DATA, "BCM", "12A1"))
    assert "3103" + "12A1" in term.sent
