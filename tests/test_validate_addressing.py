"""Tests for the ELM327/AutoPID addressing warnings (canlib.commands.validate.addressing)."""

from pathlib import Path

import pytest
import yaml

from canlib.commands.validate.addressing import (
    _init_commands,
    _init_protocol,
    addressing_warnings,
)
from canlib.commands.validate.pids import _addressing_warnings, _run_pids


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

    def test_functional_warning_describes_the_auto_filter_correctly(self):
        (warning,) = addressing_warnings(_data(OBD={"tx_id": 0x7DF}))
        assert "accept any reply" not in warning
        assert "0x7E8-0x7EF" in warning

    def test_ecus_sharing_a_request_id(self):
        evc = {"tx_id": 0x18DB33F1, "rx_id": 0x18DAF1DB, "addressing": {"fc_id": 0x18DADBF1}}
        bms = {"tx_id": 0x18DB33F1, "rx_id": 0x18DAF1DA, "addressing": {"fc_id": 0x18DADAF1}}
        data = {**_data("ATSP7;", EVC=evc, BMS=bms), "addressing": {"mode": "normal_29bit"}}
        (warning,) = addressing_warnings(data)
        assert warning.startswith("EVC, BMS: share tx_id 0x18DB33F1")
        assert "broadcast" in warning

    def test_extended_addressing_ecus_share_a_request_id_by_design(self):
        mode = {"mode": "normal_extended_11bit"}
        ecus = {
            "DME": {"tx_id": 0x6F1, "addressing": {**mode, "target_address": 0x12}},
            "EGS": {"tx_id": 0x6F1, "addressing": {**mode, "target_address": 0x18}},
        }
        assert addressing_warnings(_data(**ecus)) == []

    def test_a_malformed_id_is_left_to_the_per_file_checks(self):
        ecus = {"BAD": {"tx_id": "0x7E4"}, "RX": {"tx_id": 0x7E4, "rx_id": "x"}}
        assert addressing_warnings(_data(**ecus)) == []


class TestInitProtocol:
    @pytest.mark.parametrize(
        ("init", "protocol"),
        [
            ("ATSPA6;", "6"),
            ("AT SP 0;", "0"),
            ("ATSP6;ATS0;ATSP7;", "7"),  # the last one wins
            ("ATS0;ATST96;", None),
        ],
    )
    def test_protocol(self, init, protocol):
        assert _init_protocol(_init_commands(init)) == protocol

    def test_automatic_protocol_is_not_a_mismatch(self):
        ecu = {"tx_id": 0x18DA10F1, "addressing": {"mode": "normal_fixed_29bit"}}
        assert addressing_warnings(_data("ATSP0;", ENG=ecu)) == []


class TestThroughValidatePids:
    """The warnings reach `validate pids` via `_addressing_warnings`."""

    def _profile(self, tmp_path: Path, init: str, ecus: dict) -> Path:
        (tmp_path / "profile.yaml").write_text(yaml.safe_dump({"car_model": "Car", "init": init}))
        ecus_dir = tmp_path / "ecus"
        ecus_dir.mkdir()
        for name, body in ecus.items():
            (ecus_dir / f"{name.lower()}.yaml").write_text(yaml.safe_dump({name: body}))
        return ecus_dir

    def test_warnings_come_from_the_profile_on_disk(self, tmp_path):
        ecus_dir = self._profile(tmp_path, "ATSP6;ATFCSM1;", {"OBD": {"tx_id": 0x7DF}})
        warnings = _addressing_warnings(ecus_dir)
        assert len(warnings) == 2 and "ATFCSM1" in warnings[0]

    def test_an_unparseable_file_yields_no_addressing_warnings(self, tmp_path):
        ecus_dir = self._profile(tmp_path, "ATSP6;", {})
        (ecus_dir / "bad.yaml").write_text("BMS: [unclosed\n")
        assert _addressing_warnings(ecus_dir) == []

    def test_run_pids_prints_them(self, tmp_path, monkeypatch, capsys):
        from canlib.profile import profile_for_path

        ecus_dir = self._profile(tmp_path, "ATSP6;", {"OBD": {"tx_id": 0x7DF}})
        monkeypatch.setattr("canlib.profile.active", lambda: profile_for_path(ecus_dir))
        _run_pids(None, stats=False)
        assert "WARN: OBD: tx_id 0x7DF is a functional broadcast id" in capsys.readouterr().out
