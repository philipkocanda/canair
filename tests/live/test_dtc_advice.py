"""``live/dtc/advice.py``: what to try when an ECU does not answer or refuses a clear."""

from __future__ import annotations

from canair.cli.profile_context import active
from canair.live.dtc.advice import clear_nrc_hints, no_response_hints, power_state_hint

PIDS = {
    "ecus": {
        "BMS": {"tx_id": 0x7E4, "vehicle_states": ["READY", "CHARGING"]},
        "BCM": {"tx_id": 0x7A0, "vehicle_states": ["ALL"]},
        "VCU": {"tx_id": 0x7E2},
    }
}
NO_DATA = {"ok": False, "error": "NO DATA"}


def test_the_hint_names_the_states_the_ecu_answers_in():
    hint = power_state_hint(0x7E4, profile=active(), pids_data=PIDS)
    assert hint is not None
    assert "BMS answers only in CHARGING, READY" in hint


def test_an_ecu_that_always_answers_gets_no_power_state_hint():
    assert power_state_hint(0x7A0, profile=active(), pids_data=PIDS) is None


def test_an_ecu_with_no_states_gets_the_generic_hint():
    hint = power_state_hint(0x7E2, profile=active(), pids_data=PIDS)
    assert hint is not None and "switch the ignition on" in hint


def test_no_response_suggests_the_power_state_then_a_wake():
    hints = no_response_hints(0x7E4, NO_DATA, profile=active(), pids_data=PIDS)
    assert "CHARGING, READY" in hints[0]
    assert "--wake" in hints[1]
    assert len(no_response_hints(0x7E4, NO_DATA, profile=active(), pids_data=PIDS, wake=True)) == 1


def test_an_answer_is_not_a_missing_response():
    nrc = {"ok": False, "nrc": 0x22, "nrc_desc": "conditionsNotCorrect"}
    stale = {"ok": False, "error": "response SID mismatch"}
    for result in (nrc, stale, {"ok": True}):
        assert no_response_hints(0x7E4, result, profile=active(), pids_data=PIDS) == []


def test_conditions_not_correct_points_at_the_power_state():
    hints = clear_nrc_hints(0x22, 0x7E4, profile=active(), pids_data=PIDS)
    assert "power state" in hints[0]
    assert "CHARGING, READY" in hints[1]


def test_a_session_refusal_suggests_session_only_when_none_was_held():
    assert "--session" in clear_nrc_hints(0x7F, 0x7E4, profile=active(), pids_data=PIDS)[0]
    assert "--session" in clear_nrc_hints(0x80, 0x7E4, profile=active(), pids_data=PIDS)[0]
    assert clear_nrc_hints(0x7F, 0x7E4, profile=active(), pids_data=PIDS, session=True) == []
