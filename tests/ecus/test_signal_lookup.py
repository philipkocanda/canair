"""Tests for the signal-name lookup behind ``canair signal``."""

import pytest

from canair.ecus.signal_lookup import find_signals, parse_signal_ref, similar_signal_names


def _entry(**pids):
    return {"pids": {pid: {"parameters": params} for pid, params in pids.items()}}


INDEX = {
    "BMS": _entry(
        **{
            "2101": {"SOC_BMS": {"unit": "%", "verified": True}, "BATTERY_CURRENT": {}},
            "220101": {"SOC_BMS": {"unit": "%"}},
        }
    ),
    "VCU": _entry(**{"2101": {"VEHICLE_SPEED": {"unit": "km/h", "verified": True}}}),
}


class TestParseSignalRef:
    def test_bare_name(self):
        assert parse_signal_ref("SOC_BMS") == (None, None, "SOC_BMS")

    def test_ecu_and_name(self):
        assert parse_signal_ref("BMS:SOC_BMS") == ("BMS", None, "SOC_BMS")

    def test_ecu_pid_and_name(self):
        assert parse_signal_ref("BMS:2101:SOC_BMS") == ("BMS", "2101", "SOC_BMS")

    @pytest.mark.parametrize("text", ["A:B:C:D", "BMS::SOC_BMS", ":SOC_BMS", ""])
    def test_malformed(self, text):
        with pytest.raises(ValueError, match="invalid signal"):
            parse_signal_ref(text)


class TestFindSignals:
    def test_a_repeated_name_returns_every_definition(self):
        found = find_signals(INDEX, "SOC_BMS")
        assert [s.selector for s in found] == ["BMS:2101:SOC_BMS", "BMS:220101:SOC_BMS"]

    def test_the_match_is_case_insensitive(self):
        assert [s.selector for s in find_signals(INDEX, "vehicle_speed")] == [
            "VCU:2101:VEHICLE_SPEED"
        ]

    def test_the_pid_narrows_the_match(self):
        found = find_signals(INDEX, "SOC_BMS", ecu="bms", pid="220101")
        assert [s.selector for s in found] == ["BMS:220101:SOC_BMS"]

    def test_the_ecu_narrows_the_match(self):
        assert find_signals(INDEX, "SOC_BMS", ecu="VCU") == []

    def test_unit_and_verified_come_from_the_definition(self):
        verified, unverified = find_signals(INDEX, "SOC_BMS")
        assert (verified.unit, verified.verified) == ("%", True)
        assert unverified.verified is False

    def test_a_null_parameters_block_is_skipped(self):
        index = {"X": {"pids": {"2101": {"parameters": None}}}}
        assert find_signals(index, "ANY") == []


class TestSimilarSignalNames:
    def test_a_substring_is_suggested(self):
        assert similar_signal_names(INDEX, "SPEED") == ["VEHICLE_SPEED"]

    def test_a_typo_is_suggested(self):
        assert similar_signal_names(INDEX, "SOC_BMZ") == ["SOC_BMS"]

    def test_nothing_close_gives_nothing(self):
        assert similar_signal_names(INDEX, "ZZZZZZZZ") == []
