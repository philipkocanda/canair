"""Tests for canlib.modes.multi — parse_sub_commands, resolve_tx_id."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from canlib.modes.multi import _exec_query, parse_sub_commands, resolve_tx_id

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

    def test_resolve_ecus_alias(self):
        # ecus.yaml alias 'LDC' canonicalises to the pids key 'OBC' (0x7E5).
        index = {"OBC": {"tx_id": 0x7E5, "pids": {}}}
        assert resolve_tx_id("LDC", index) == 0x7E5
        assert resolve_tx_id("ldc", index) == 0x7E5


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
        assert result[0] == {"type": "session", "target": "IGPM", "wake": False, "mode": "03"}

    def test_session_with_mode(self):
        result = parse_sub_commands(["session BMS --mode 81"])
        assert result[0]["target"] == "BMS"
        assert result[0]["mode"] == "81"

    def test_session_with_wake(self):
        result = parse_sub_commands(["session SKM --wake"])
        assert result[0]["wake"] is True
        assert result[0]["target"] == "SKM"

    def test_session_missing_target(self):
        with pytest.raises(ValueError, match="requires"):
            parse_sub_commands(["session"])

    def test_query_with_pids(self):
        result = parse_sub_commands(["query IGPM:BC03,BC06"])
        assert result[0]["type"] == "query"
        assert result[0]["ecu"] == "IGPM"
        assert result[0]["pids"] == ["BC03", "BC06"]

    def test_query_no_pids(self):
        result = parse_sub_commands(["query BMS"])
        assert result[0]["pids"] == []

    def test_query_missing_ecu(self):
        with pytest.raises(ValueError, match="requires"):
            parse_sub_commands(["query"])

    def test_query_cross_ecu_fans_out(self):
        result = parse_sub_commands(["query VCU:2101 BMS:2101"])
        assert [(c["type"], c["ecu"], c["pids"]) for c in result] == [
            ("query", "VCU", ["2101"]),
            ("query", "BMS", ["2101"]),
        ]

    def test_query_uppercases(self):
        result = parse_sub_commands(["query vcu:2101"])
        assert result[0]["ecu"] == "VCU"
        assert result[0]["pids"] == ["2101"]

    def test_query_dedups_identical_selectors(self):
        result = parse_sub_commands(["query VCU:2101 VCU:2101"])
        assert len(result) == 1

    def test_query_all_pids_across_ecus(self):
        result = parse_sub_commands(["query VCU BMS"])
        assert [(c["ecu"], c["pids"]) for c in result] == [("VCU", []), ("BMS", [])]

    def test_query_malformed_double_colon_raises(self):
        with pytest.raises(ValueError):
            parse_sub_commands(["query VCU::2101"])

    def test_query_malformed_empty_ecu_raises(self):
        with pytest.raises(ValueError):
            parse_sub_commands(["query :2101"])

    # --- space-vs-colon guard rail (a bare PID/DID in the ECU slot) ---

    def test_query_space_form_pid_after_ecu_raises(self):
        # "query IGPM 22BC07" is the classic mistake for "query IGPM:22BC07":
        # the space makes 22BC07 an independent (bogus) ECU selector.
        with pytest.raises(ValueError, match="looks like a PID/DID"):
            parse_sub_commands(["query IGPM 22BC07"])

    def test_query_space_form_suggests_colon_form(self):
        with pytest.raises(ValueError, match=r"IGPM:22BC07"):
            parse_sub_commands(["query IGPM 22BC07"])

    def test_query_space_form_short_did_raises(self):
        with pytest.raises(ValueError, match="looks like a PID/DID"):
            parse_sub_commands(["query BCM C00B B00E"])

    def test_query_lone_pid_raises(self):
        with pytest.raises(ValueError, match="looks like a PID/DID"):
            parse_sub_commands(["query 2101"])

    def test_query_colon_form_ok(self):
        # The correct form must still parse cleanly.
        result = parse_sub_commands(["query IGPM:22BC07"])
        assert result[0] == {"type": "query", "ecu": "IGPM", "pids": ["22BC07"]}

    def test_query_two_alpha_ecus_not_flagged(self):
        # Two real (alphabetic) ECU names are a legitimate cross-ECU query.
        result = parse_sub_commands(["query VCU BMS"])
        assert [(c["ecu"], c["pids"]) for c in result] == [("VCU", []), ("BMS", [])]

    def test_query_hex_only_ecu_name_not_flagged(self):
        # A bare hex-letters-only token (no digit) is not treated as a PID, so a
        # hypothetical all-letter ECU name still works.
        result = parse_sub_commands(["query BMS ABC"])
        assert [(c["ecu"], c["pids"]) for c in result] == [("BMS", []), ("ABC", [])]

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
            "type": "scan",
            "tx": "770",
            "service": "22",
            "range": "BC00-BCFF",
            "append": "",
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
        result = parse_sub_commands(
            [
                "skm-wake acc",
                "session IGPM --wake",
                "query IGPM:BC03",
                "sleep 1",
            ]
        )
        assert len(result) == 4
        assert [r["type"] for r in result] == ["skm-wake", "session", "query", "sleep"]

    def test_underscore_alias(self):
        """skm_wake (underscore) should work same as skm-wake (hyphen)."""
        result = parse_sub_commands(["skm_wake ign2"])
        assert result[0] == {"type": "skm-wake", "level": "ign2"}


# --- _exec_query acquisition timestamps ---


class TestExecQueryTimestamp:
    """Each queried PID must carry its own acquisition timestamp (moment the
    response arrived), so sequentially-polled PIDs keep their true skew rather
    than sharing one per-cycle time."""

    def _make_sm(self, latency: float):
        sm = MagicMock()
        sm.keepalive_stale = AsyncMock()
        sm.has_session = MagicMock(return_value=True)
        sm.terminal = MagicMock()
        sm.terminal.set_header = AsyncMock()

        async def fake_send_uds(pid_code, *a, **k):
            await asyncio.sleep(latency)  # simulate round-trip so each PID lands at a distinct time
            return {"ok": True, "hex": "6101F8F8", "bytes": bytes.fromhex("6101F8F8")}

        sm.terminal.send_uds = fake_send_uds
        return sm

    def test_acquired_at_attached_per_pid(self):
        ecu_index = {
            "MCU": {"tx_id": 0x7E3, "pids": {"2101": {"parameters": {}}, "2102": {"parameters": {}}}}
        }
        sm = self._make_sm(latency=0.02)
        _label, results = asyncio.run(
            _exec_query(sm, "MCU", [], ecu_index, {}, verbose=False, return_results=True)
        )
        assert len(results) == 2
        for r in results:
            assert isinstance(r.get("acquired_at"), float)
        # Sequential PIDs must have distinct, increasing timestamps reflecting real skew.
        assert results[1]["acquired_at"] > results[0]["acquired_at"]
        assert results[1]["acquired_at"] - results[0]["acquired_at"] >= 0.01

    def test_error_result_also_timestamped(self):
        ecu_index = {"MCU": {"tx_id": 0x7E3, "pids": {"2101": {"parameters": {}}}}}
        sm = self._make_sm(latency=0.0)

        async def fail_send_uds(pid_code, *a, **k):
            return {"ok": False, "nrc": 0x12, "nrc_desc": "subFunctionNotSupported"}

        sm.terminal.send_uds = fail_send_uds
        _label, results = asyncio.run(
            _exec_query(sm, "MCU", [], ecu_index, {}, verbose=False, return_results=True)
        )
        assert len(results) == 1
        assert "error" in results[0]
        assert isinstance(results[0].get("acquired_at"), float)
