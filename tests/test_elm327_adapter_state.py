"""Tests for AdapterState: how each command moves the cached ELM327 addressing state.

The cache decides what ``set_header`` sends, so a value it records wrongly sends a
request astray. These pin the cases a review found: a context-dependent rejection
(``ATFCSM1`` before its prerequisites) must not be treated as "unsupported", a
rejected header must never be skipped, a command whose reply never arrived is not
applied, and commands canair never plans still invalidate what they touch.
"""

import pytest

from canlib.addressing import EcuAddress
from canlib.transport.elm327_addressing import (
    AUTO,
    FC_MODE_ADAPTER,
    FC_MODE_USER,
    UNKNOWN,
    AdapterState,
    functional_target,
    target_for,
)

LBC = EcuAddress(0x79B, 0x7BB)
BMS = EcuAddress(0x7E4, 0x7EC)
ENGINE = EcuAddress(0x7E0, 0x7E8)


def _at(target) -> AdapterState:
    state = AdapterState()
    for cmd in state.plan(target):
        state.observe(cmd, "OK")
    return state


class TestRejections:
    def test_fcsm1_rejected_before_its_prerequisites_is_retried(self):
        # Upstream-style init order: ATFCSM1 before ATFCSH, which the adapter
        # rejects by design. That says nothing about a correctly ordered attempt.
        state = AdapterState()
        state.observe("ATZ", "ELM327 v2.3")
        state.observe("ATFCSM1", "?")
        state.observe("ATFCSD300000", "OK")
        assert "ATFCSM1" in state.plan(target_for(LBC))

    def test_fcsm1_rejected_with_its_prerequisites_is_not_retried(self):
        state = AdapterState()
        state.observe("ATFCSH79B", "OK")
        state.observe("ATFCSD300000", "OK")
        state.observe("ATFCSM1", "?")
        assert "ATFCSM1" not in state.plan(target_for(LBC))

    def test_a_rejected_header_is_never_skipped(self):
        state = AdapterState()
        state.observe("ATSH18DB33F1", "?")
        assert state.header is None
        plan = state.plan(target_for(EcuAddress(0x18DB33F1, 0x18DAF1DB, fc_id=0x18DADBF1)))
        assert plan[0] == "ATSH18DB33F1"

    def test_a_rejected_flow_control_header_is_skipped_only_while_mode_0(self):
        state = AdapterState()
        state.observe("ATFCSH7E4", "?")
        assert "ATFCSH7E4" not in state.plan(target_for(BMS))
        state.observe("ATFCSM1", "OK")
        assert "ATFCSH7E4" in state.plan(target_for(BMS))

    def test_required(self):
        state = AdapterState()
        assert state.required("AT SH 7E4", target_for(BMS))
        assert not state.required("ATFCSH7E4", target_for(BMS))
        assert state.required("ATFCSH79B", target_for(LBC))
        assert not state.required("ATFCSH7DF", functional_target(0x7DF))
        assert not state.required("ATCRA7BB", target_for(LBC))
        state.observe("ATFCSM1", "OK")
        # Mode 1 sends flow control to ATFCSH, so a conventional ECU needs it too.
        assert state.required("ATFCSH7E4", target_for(BMS))

    def test_explicit_target_after_a_rejected_fcsh_still_plans_it(self):
        state = AdapterState()
        state.observe("ATFCSH79B", "?")
        assert state.plan(target_for(LBC))[:2] == ["ATSH79B", "ATFCSH79B"]

    @pytest.mark.parametrize("cmd", ["ATZ", "ATWS", "ATD"])
    def test_a_rejected_reset_forgets_everything(self, cmd):
        state = _at(target_for(LBC))
        epoch = state.epoch
        state.observe(cmd, "?")
        assert (state.header, state.rx_filter, state.fc_mode) == (None, UNKNOWN, UNKNOWN)
        assert state.epoch == epoch + 1

    @pytest.mark.parametrize("cmd", ["ATWS", "ATD", "at d"])
    def test_reset_commands_restore_the_defaults(self, cmd):
        state = _at(target_for(LBC))
        state.observe(cmd, "OK")
        assert (state.header, state.fc_header, state.rx_filter, state.fc_mode) == (
            None,
            None,
            AUTO,
            FC_MODE_ADAPTER,
        )


class TestUnanswered:
    @pytest.mark.parametrize(
        ("cmd", "attr", "unknown"),
        [
            ("ATSH7E4", "header", None),
            ("ATFCSH7E4", "fc_header", None),
            ("ATFCSD300000", "fc_data", UNKNOWN),
            ("ATFCSM1", "fc_mode", UNKNOWN),
            ("ATCRA7EC", "rx_filter", UNKNOWN),
            ("ATCRA", "rx_filter", UNKNOWN),
        ],
    )
    def test_a_command_with_no_reply_is_not_recorded_as_applied(self, cmd, attr, unknown):
        state = AdapterState()
        state.observe(cmd, "", answered=False)
        assert getattr(state, attr) is unknown

    def test_an_unanswered_command_is_not_blacklisted(self):
        state = AdapterState()
        state.observe("ATCRA7BB", "", answered=False)
        assert "ATCRA7BB" in state.plan(target_for(LBC))

    def test_an_unanswered_reset_forgets_everything(self):
        state = AdapterState()
        state.observe("ATZ", "", answered=False)
        assert state.rx_filter is UNKNOWN


class TestSideEffects:
    @pytest.mark.parametrize("cmd", ["ATCP18", "ATSP7", "ATSPA6", "ATTP6"])
    def test_header_commands_make_the_header_unknown(self, cmd):
        state = _at(target_for(BMS))
        state.observe(cmd, "OK")
        assert state.header is None and state.fc_header is None
        assert state.plan(target_for(BMS))[:2] == ["ATSH7E4", "ATFCSH7E4"]

    def test_tester_address_moves_header_and_filter(self):
        state = _at(target_for(LBC))
        state.observe("AT TA F2", "OK")
        assert (state.header, state.rx_filter) == (None, UNKNOWN)

    @pytest.mark.parametrize(
        "cmd",
        [
            "ATCF7E8",
            "ATCM7FF",
            "AT CM 7F0",
            "ATSR E8",
            "ATCEA04",
            "ATCEA",
            "STFAC",
            "STFPA 7E8,7FF",
        ],
    )
    def test_filter_commands_make_the_filter_unknown(self, cmd):
        state = _at(target_for(LBC))
        assert state.rx_filter == 0x7BB
        state.observe(cmd, "OK")
        assert state.rx_filter is UNKNOWN
        assert state.plan(target_for(LBC)) == ["ATCRA7BB"]

    @pytest.mark.parametrize(
        "cmd", ["STFFCA 7E8,7FF", "STFFCC", "STFAFC 7E8,7FF", "STCFCPA 7E0,7E8"]
    )
    def test_stn_flow_control_commands_make_flow_control_unknown(self, cmd):
        state = _at(target_for(LBC))
        state.observe(cmd, "OK")
        assert state.fc_header is None and state.fc_mode is UNKNOWN
        assert state.rx_filter == 0x7BB

    def test_can_flow_control_switch_is_not_a_filter(self):
        state = _at(target_for(LBC))
        state.observe("ATCFC1", "OK")
        assert state.rx_filter == 0x7BB

    def test_an_invalid_flow_control_mode_is_unknown(self):
        state = AdapterState()
        state.observe("ATFCSM3", "OK")
        assert state.fc_mode is UNKNOWN

    def test_flow_control_data_with_spaces_and_case(self):
        state = AdapterState()
        state.observe("at fc sd 30 00 00", "OK")
        assert state.fc_data == "300000"
        state.observe("\tATFCSM 1 ", "OK")
        assert state.fc_mode == FC_MODE_USER

    def test_unrelated_commands_change_nothing(self):
        state = _at(target_for(LBC))
        for cmd in ("ATST96", "ATS0", "ATAL", "ATSS", "ATCSM1", "ATCAF1", "STI", "ATRV"):
            state.observe(cmd, "OK")
        assert state.plan(target_for(LBC)) == []


class TestPlan:
    def test_conventional_target_with_flow_control_mode_unknown(self):
        state = AdapterState()
        state.observe("ATFCSM1", "", answered=False)
        # A conventional ECU works in either mode, because ATFCSH follows the header.
        assert state.plan(target_for(BMS)) == ["ATSH7E4", "ATFCSH7E4"]

    def test_an_atcra_baseline_on_another_ecu_is_kept_for_a_conventional_ecu(self):
        # The init pins 0x7E8 (the engine); a read of BMS (0x7EC) keeps that
        # baseline, exactly as before addressing was managed per ECU.
        state = AdapterState()
        state.observe("ATCRA7E8", "OK")
        state.mark_baseline()
        assert state.plan(target_for(BMS)) == ["ATSH7E4", "ATFCSH7E4"]

    def test_epoch_moves_on_reset_and_forget(self):
        state = AdapterState()
        state.assume_defaults()
        state.forget()
        assert state.epoch == 2
