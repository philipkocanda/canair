"""Tests for canlib.modes.multi — parse_sub_commands, resolve_tx_id."""

import pytest
from canlib.modes.multi import parse_sub_commands, resolve_tx_id


# --- resolve_tx_id ---

class TestResolveTxId:
    """Tests for ECU name / hex ID resolution."""

    def _ecu_index(self):
        return {
            "BMS": {"tx_id": 0x7E4, "pids": {}},
            "IGPM": {"tx_id": 0x770, "pids": {}},
            "SKM": {"tx_id": 0x7A5, "pids": {}},
        }

    def test_resolve_name_upper(self):
        assert resolve_tx_id("IGPM", self._ecu_index()) == 0x770

    def test_resolve_name_lower(self):
        assert resolve_tx_id("igpm", self._ecu_index()) == 0x770

    def test_resolve_name_mixed(self):
        assert resolve_tx_id("Bms", self._ecu_index()) == 0x7E4

    def test_resolve_hex_no_prefix(self):
        assert resolve_tx_id("770", self._ecu_index()) == 0x770

    def test_resolve_hex_with_prefix(self):
        assert resolve_tx_id("0x7A5", self._ecu_index()) == 0x7A5

    def test_resolve_hex_lowercase(self):
        assert resolve_tx_id("7e4", self._ecu_index()) == 0x7E4

    def test_resolve_unknown_returns_none(self):
        assert resolve_tx_id("UNKNOWN", self._ecu_index()) is None

    def test_resolve_invalid_hex_returns_none(self):
        assert resolve_tx_id("ZZZ", self._ecu_index()) is None


# --- parse_sub_commands ---

class TestParseSubCommands:
    def test_skm_wake_default(self):
        result = parse_sub_commands(["skm-wake"])
        assert len(result) == 1
        assert result[0] == {"type": "skm-wake", "level": "acc"}

    def test_skm_wake_with_level(self):
        result = parse_sub_commands(["skm-wake ign1"])
        assert result[0]["level"] == "ign1"

    def test_session_basic(self):
        result = parse_sub_commands(["session IGPM"])
        assert result[0] == {"type": "session", "target": "IGPM", "wake": False}

    def test_session_with_wake(self):
        result = parse_sub_commands(["session SKM --wake"])
        assert result[0]["wake"] is True
        assert result[0]["target"] == "SKM"

    def test_session_missing_target(self):
        with pytest.raises(ValueError, match="requires"):
            parse_sub_commands(["session"])

    def test_query_with_pids(self):
        result = parse_sub_commands(["query IGPM BC03 BC06"])
        assert result[0]["type"] == "query"
        assert result[0]["ecu"] == "IGPM"
        assert result[0]["pids"] == ["BC03", "BC06"]

    def test_query_no_pids(self):
        result = parse_sub_commands(["query BMS"])
        assert result[0]["pids"] == []

    def test_query_missing_ecu(self):
        with pytest.raises(ValueError, match="requires"):
            parse_sub_commands(["query"])

    def test_raw_basic(self):
        result = parse_sub_commands(["raw 770:22BC03"])
        assert result[0] == {"type": "raw", "spec": "770:22BC03", "hold": False}

    def test_raw_with_hold(self):
        result = parse_sub_commands(["raw 770:2FBC1003 --hold"])
        assert result[0]["hold"] is True

    def test_raw_missing_spec(self):
        with pytest.raises(ValueError, match="requires"):
            parse_sub_commands(["raw"])

    def test_scan(self):
        result = parse_sub_commands(["scan 770 22 BC00-BCFF"])
        assert result[0] == {
            "type": "scan", "tx": "770", "service": "22",
            "range": "BC00-BCFF", "append": "",
        }

    def test_scan_with_append(self):
        result = parse_sub_commands(["scan 7A0 2F B000-B0FF 00"])
        assert result[0]["append"] == "00"

    def test_scan_missing_args(self):
        with pytest.raises(ValueError, match="requires"):
            parse_sub_commands(["scan 770 22"])

    def test_sleep_default(self):
        result = parse_sub_commands(["sleep"])
        assert result[0] == {"type": "sleep", "seconds": 1.0}

    def test_sleep_custom(self):
        result = parse_sub_commands(["sleep 2.5"])
        assert result[0]["seconds"] == 2.5

    def test_repl(self):
        result = parse_sub_commands(["repl"])
        assert result[0] == {"type": "repl"}

    def test_unknown_command(self):
        with pytest.raises(ValueError, match="Unknown sub-command"):
            parse_sub_commands(["bogus"])

    def test_empty_args_skipped(self):
        result = parse_sub_commands(["", "repl"])
        assert len(result) == 1

    def test_multi_command_pipeline(self):
        result = parse_sub_commands([
            "skm-wake acc",
            "session IGPM --wake",
            "query IGPM BC03",
            "sleep 1",
        ])
        assert len(result) == 4
        assert [r["type"] for r in result] == ["skm-wake", "session", "query", "sleep"]

    def test_underscore_alias(self):
        """skm_wake (underscore) should work same as skm-wake (hyphen)."""
        result = parse_sub_commands(["skm_wake ign2"])
        assert result[0] == {"type": "skm-wake", "level": "ign2"}
