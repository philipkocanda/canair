"""Tests for canlib.pids — PID loading and index building."""

import pytest

from canlib.pids import (
    DEFAULT_PID_STATUS,
    PID_STATUSES,
    build_ecu_index,
    build_param_index,
    load_pids,
    pid_status,
)


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


class TestPidStatus:
    def test_default_is_active(self):
        assert pid_status({}) == "active"
        assert pid_status({"period": 5000}) == "active"
        assert DEFAULT_PID_STATUS == "active"

    def test_reads_explicit_status(self):
        for s in PID_STATUSES:
            assert pid_status({"status": s}) == s

    def test_unknown_status_degrades_to_active(self):
        assert pid_status({"status": "bogus"}) == "active"

    def test_case_insensitive(self):
        assert pid_status({"status": "DRAFT"}) == "draft"


class TestStatusVisibility:
    """build_*_index apply the lifecycle rules derived from `status:`."""

    def _data(self):
        return {
            "ecus": {
                "TEST": {
                    "tx_id": 0x7E0,
                    "pids": {
                        "2101": {"status": "active", "parameters": {"A": {"expression": "B0"}}},
                        "2102": {"status": "draft", "parameters": {"B": {"expression": "B1"}}},
                        "21F2": {"status": "static", "parameters": {"C": {"expression": "B2"}}},
                        "22DEAD": {"status": "ignored", "parameters": {"D": {"expression": "B3"}}},
                    },
                }
            }
        }

    def test_ignored_excluded_from_ecu_index(self):
        idx = build_ecu_index(self._data())
        pids = idx["TEST"]["pids"]
        assert set(pids) == {"2101", "2102", "21F2"}  # ignored dropped

    def test_ignored_excluded_from_param_index(self):
        idx = build_param_index(self._data())
        assert "D" not in idx  # param of an ignored PID
        assert {"A", "B", "C"} <= set(idx)

    def test_derived_flags(self):
        pids = build_ecu_index(self._data())["TEST"]["pids"]
        assert pids["2101"]["shipped"] and pids["2101"]["swept"]
        assert not pids["2102"]["shipped"] and pids["2102"]["swept"]  # draft: swept, unshipped
        assert not pids["21F2"]["shipped"] and not pids["21F2"]["swept"]  # static: neither
