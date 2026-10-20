"""Tests for the ELM327/AutoPID addressing warnings (canlib.commands.validate.addressing)."""

from canlib.commands.validate.addressing import addressing_warnings


def _data(init: str = "ATSP6;ATS0;", **ecus) -> dict:
    return {"init": init, "ecus": ecus}


class TestAddressingWarnings:
    def test_a_conventional_profile_is_clean(self):
        assert addressing_warnings(_data(BMS={"tx_id": 0x7E4})) == []

    def test_init_commands_canair_sets_per_ecu(self):
        warnings = addressing_warnings(_data("ATSP6;ATFCSM1;at fcsh 7bb;ATSTFF;"))
        assert len(warnings) == 2
        assert "ATFCSM1" in warnings[0] and "ATFCSH7BB" in warnings[1]

    def test_an_init_receive_filter_is_a_legitimate_baseline(self):
        assert addressing_warnings(_data("ATSP6;ATCRA7E8;ATCFC1;")) == []

    def test_functional_request_ecu_without_flow_control_id(self):
        evc = {"tx_id": 0x18DB33F1, "rx_id": 0x18DAF1DB, "addressing": {"mode": "normal_29bit"}}
        (warning,) = addressing_warnings(_data("ATSP7;", EVC=evc))
        assert warning.startswith("EVC:") and "fc_id" in warning

    def test_functional_request_ecu_with_flow_control_id_is_clean(self):
        evc = {
            "tx_id": 0x18DB33F1,
            "rx_id": 0x18DAF1DB,
            "addressing": {"mode": "normal_29bit", "fc_id": 0x18DADBF1},
        }
        assert addressing_warnings(_data("ATSP7;", EVC=evc)) == []

    def test_29bit_ecu_with_an_11bit_protocol(self):
        ecu = {"tx_id": 0x18DA10F1, "addressing": {"mode": "normal_fixed_29bit"}}
        (warning,) = addressing_warnings(_data("ATSP6;", ENG=ecu))
        assert "ATSP6" in warning and "ATSP7/ATSP9" in warning

    def test_11bit_ecu_with_a_29bit_protocol(self):
        (warning,) = addressing_warnings(_data("ATSPA7;", BMS={"tx_id": 0x7E4}))
        assert "ATSP6/ATSP8" in warning

    def test_no_protocol_in_init_is_not_a_mismatch(self):
        assert addressing_warnings(_data("ATS0;", BMS={"tx_id": 0x7E4})) == []
