"""Tests for the raw-CAN monitor backend helpers (pure, no device)."""

from canlib.modes.monitor import _raw_pid_result
from canlib.modes.raw_monitor import _keep_mode, query_ecu_addresses


class _Args:
    def __init__(self, **kw):
        self.keep_unique = self.keep_all = False
        self.keep = None
        self.__dict__.update(kw)


class TestKeepMode:
    def test_none(self):
        assert _keep_mode(_Args()) is None

    def test_unique(self):
        assert _keep_mode(_Args(keep_unique=True)) == "unique"

    def test_all(self):
        assert _keep_mode(_Args(keep_all=True)) == "all"

    def test_last(self):
        assert _keep_mode(_Args(keep=5)) == "last"


class TestQueryEcuAddresses:
    def test_maps_tx_rx(self):
        ecu_index = {"IGPM": {"tx_id": 0x770}, "BMS": {"tx_id": 0x7E4}}
        steps = [{"ecu": "igpm", "pids": []}, {"ecu": "BMS", "pids": ["2101"]}]
        out = query_ecu_addresses(steps, ecu_index)
        assert out == {"IGPM": (0x770, 0x778), "BMS": (0x7E4, 0x7EC)}

    def test_skips_unknown_ecu(self):
        out = query_ecu_addresses([{"ecu": "NOPE", "pids": []}], {"IGPM": {"tx_id": 0x770}})
        assert out == {}


class TestRawPidResult:
    def test_positive_mapped(self):
        r = _raw_pid_result("22BC03", {"parameters": {}}, False, bytes.fromhex("62BC03FDEE"), 1.0)
        assert r["raw_hex"] == "62BC03FDEE"
        assert r["acquired_at"] == 1.0
        assert "error" not in r

    def test_positive_unmapped(self):
        r = _raw_pid_result("22B003", None, True, bytes.fromhex("62B003AA"), 2.0)
        assert r["raw_hex"] == "62B003AA"
        assert r["unmapped"] is True

    def test_negative_response_nrc(self):
        r = _raw_pid_result("22B004", None, False, bytes.fromhex("7F2213"), 3.0)
        assert "NRC 0x13" in r["error"]

    def test_timeout_none(self):
        r = _raw_pid_result("2101", {"parameters": {}}, False, None, 4.0)
        assert r["error"] == "timeout"

    def test_exception_value(self):
        r = _raw_pid_result("2101", {"parameters": {}}, False, TimeoutError("x"), 5.0)
        assert r["error"] == "timeout"

    def test_other_exception(self):
        r = _raw_pid_result("2101", None, False, ValueError("boom"), 6.0)
        assert "boom" in r["error"]

    def test_empty_response(self):
        r = _raw_pid_result("2101", None, False, b"", 7.0)
        assert "empty" in r["error"]
