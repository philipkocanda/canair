"""Tests for canlib.pids — PID loading and index building."""

import pytest

from canlib.pids import build_ecu_index, build_param_index, load_pids


@pytest.fixture(scope="module")
def pids_data():
    """Load the real YAML PID definitions once."""
    return load_pids()


class TestLoadPids:
    def test_loads_successfully(self, pids_data):
        assert "ecus" in pids_data
        assert len(pids_data["ecus"]) > 0

    def test_bms_ecu_exists(self, pids_data):
        assert "BMS" in pids_data["ecus"]
        assert str(pids_data["ecus"]["BMS"]["tx_id"]) in ("7E4", "2020")

    def test_bms_has_pids(self, pids_data):
        bms = pids_data["ecus"]["BMS"]
        assert "pids" in bms
        assert "2101" in bms["pids"] or 2101 in bms["pids"]


class TestBuildParamIndex:
    def test_returns_populated_index(self, pids_data):
        idx = build_param_index(pids_data)
        assert len(idx) > 100  # should have 200+ params

    def test_soc_bms_present(self, pids_data):
        idx = build_param_index(pids_data)
        assert "SOC_BMS" in idx
        p = idx["SOC_BMS"]
        assert p["ecu"] == "BMS"
        assert str(p["tx_id"]) in ("7E4", "2020")
        assert p["expression"]  # non-empty

    def test_keys_uppercased(self, pids_data):
        idx = build_param_index(pids_data)
        for key in idx:
            assert key == key.upper()

    def test_param_has_required_fields(self, pids_data):
        idx = build_param_index(pids_data)
        for _name, p in list(idx.items())[:5]:
            assert "ecu" in p
            assert "tx_id" in p
            assert "pid" in p
            assert "expression" in p


class TestBuildEcuIndex:
    def test_returns_populated_index(self, pids_data):
        idx = build_ecu_index(pids_data)
        assert len(idx) >= 5  # BMS, VCU, IGPM, BCM, etc.

    def test_bms_has_pids(self, pids_data):
        idx = build_ecu_index(pids_data)
        assert "BMS" in idx
        assert str(idx["BMS"]["tx_id"]) in ("7E4", "2020")
        assert len(idx["BMS"]["pids"]) > 0

    def test_keys_uppercased(self, pids_data):
        idx = build_ecu_index(pids_data)
        for key in idx:
            assert key == key.upper()
