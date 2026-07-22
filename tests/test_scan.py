"""Tests for scan service presets and smart per-ECU defaults (canlib.scan_presets)."""

from types import SimpleNamespace

import pytest

from canlib import scan_presets as sp
from canlib.scan_presets import (
    ServiceError,
    is_wide_service,
    plan_scan,
    preset_by_service,
    presets_help,
    resolve_service,
    service_label,
)


class TestResolveService:
    def test_preset_names(self):
        assert resolve_service("live-data") == (0x21, "live-data")
        assert resolve_service("read-did") == (0x22, "read-did")
        assert resolve_service("iocontrol") == (0x2F, "iocontrol")
        assert resolve_service("routine") == (0x31, "routine")

    def test_case_and_underscore_insensitive(self):
        assert resolve_service("READ_DID") == (0x22, "read-did")
        assert resolve_service("Live-Data") == (0x21, "live-data")

    def test_aliases(self):
        assert resolve_service("io")[0] == 0x2F
        assert resolve_service("did")[0] == 0x22
        assert resolve_service("live")[0] == 0x21
        assert resolve_service("routines")[0] == 0x31

    def test_hex_with_and_without_prefix(self):
        assert resolve_service("22") == (0x22, "read-did")
        assert resolve_service("0x2F") == (0x2F, "iocontrol")

    def test_hex_without_preset_name(self):
        # A valid hex byte that isn't a known preset resolves with name None.
        assert resolve_service("18") == (0x18, None)

    def test_invalid_raises(self):
        with pytest.raises(ServiceError):
            resolve_service("zz")

    def test_out_of_range_raises(self):
        with pytest.raises(ServiceError):
            resolve_service("100")  # 0x100 > one byte


class TestServiceHelpers:
    def test_is_wide_service(self):
        assert is_wide_service(0x22)
        assert is_wide_service(0x2F)
        assert is_wide_service(0x31)
        assert not is_wide_service(0x21)

    def test_preset_by_service(self):
        assert preset_by_service(0x21).name == "live-data"
        assert preset_by_service(0xAB) is None

    def test_service_label(self):
        assert service_label(0x22, "read-did") == "read-did (0x22)"
        assert service_label(0x21) == "live-data (0x21)"
        # Not a scan preset, but named via the uds_services registry.
        assert service_label(0x18) == "ReadDTCByStatus (KWP2000) (0x18)"
        assert service_label(0x1A) == "ReadEcuIdentification (0x1A)"
        # 0x30 is a scan preset now → friendly preset name.
        assert service_label(0x30) == "iocontrol-kwp (0x30)"
        # Genuinely unknown SID falls back to bare hex.
        assert service_label(0xAB) == "0xAB"

    def test_presets_help_lists_all(self):
        text = presets_help()
        for name in ("live-data", "read-did", "iocontrol", "routine"):
            assert name in text


class TestInferFromPids:
    def test_narrow_service_21(self):
        # BMS-style paged PIDs → service 0x21, full paged range.
        svc, rng = sp._infer_from_pids(["2101", "2102", "2105"])
        assert svc == 0x21
        assert rng == (0x01, 0xFF)

    def test_wide_service_22_single_high_byte(self):
        svc, rng = sp._infer_from_pids(["22BC03", "22BC06", "22BC07"])
        assert svc == 0x22
        assert rng == (0xBC00, 0xBCFF)

    def test_wide_service_spans_high_bytes(self):
        svc, rng = sp._infer_from_pids(["22B003", "22C00B"])
        assert svc == 0x22
        assert rng == (0xB000, 0xC0FF)

    def test_dominant_service_wins(self):
        svc, _ = sp._infer_from_pids(["2101", "2102", "22F190"])
        assert svc == 0x21

    def test_empty_returns_none(self):
        assert sp._infer_from_pids([]) is None
        assert sp._infer_from_pids(["zz", "1"]) is None


class TestPlanScan:
    def _pids(self):
        return {
            "ecus": {
                "BMS": {"tx_id": 0x7E4, "pids": {"2101": {}, "2105": {}}},
                "IGPM": {"tx_id": 0x770, "pids": {"22BC03": {}, "22BC06": {}}},
                "MYSTERY_UDS": {"tx_id": 0x7C0, "pids": {}},
            }
        }

    def _ecus(self):
        return {
            0x7E4: {"name": "BMS", "id_protocol": "KWP2000"},
            0x770: {"name": "IGPM", "id_protocol": "UDS"},
            0x7C0: {"name": "MYSTERY_UDS", "id_protocol": "UDS"},
            0x7B7: {"name": "NOPIDS_KWP", "id_protocol": "KWP2000"},
        }

    def test_kwp_ecu_from_pids(self):
        p = plan_scan("BMS", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.service == 0x21
        assert p.service_name == "live-data"
        assert p.pid_range == (0x01, 0xFF)
        assert "known PID" in p.reason

    def test_uds_ecu_from_pids(self):
        p = plan_scan("IGPM", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.service == 0x22
        assert p.pid_range == (0xBC00, 0xBCFF)

    def test_uds_ecu_no_pids_falls_back_to_identity(self):
        p = plan_scan("MYSTERY_UDS", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.service == 0x22
        assert p.pid_range == (0xF100, 0xF1FF)
        assert "identity" in p.reason.lower()

    def test_kwp_ecu_no_pids_falls_back_to_live_data(self):
        p = plan_scan("NOPIDS_KWP", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.service == 0x21
        assert p.pid_range == (0x01, 0xFF)

    def test_resolve_by_hex_tx(self):
        p = plan_scan("7E4", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.ecu == "BMS"
        assert p.tx_id == 0x7E4

    def test_unresolvable_returns_none(self):
        assert plan_scan("NOPE", pids_data=self._pids(), ecus_data=self._ecus()) is None


class TestCommandHelpers:
    def test_fmt_range(self):
        from canlib.commands.scan import _fmt_range

        assert _fmt_range((0x01, 0xFF), wide=False) == "01-FF"
        assert _fmt_range((0xBC00, 0xBCFF), wide=True) == "BC00-BCFF"

    def test_equiv_command(self):
        from canlib.commands.scan import _equiv_command

        args = SimpleNamespace(
            tx="BMS", service="21", range="01-FF", append=None, session=False, wake=False
        )
        assert _equiv_command(args) == "canair scan range BMS --service 21 --range 01-FF"

    def test_equiv_command_with_flags(self):
        from canlib.commands.scan import _equiv_command

        args = SimpleNamespace(
            tx="IGPM", service="2F", range="E000-E0FF", append="03", session=True, wake=True
        )
        cmd = _equiv_command(args)
        assert "--append 03" in cmd
        assert "--session" in cmd
        assert "--wake" in cmd

    def test_resolve_ecu_selection_by_number(self):
        from canlib.commands.scan import _resolve_ecu_selection

        choices = [("BMS", 0x7E4, "batt"), ("IGPM", 0x770, "gw")]
        assert _resolve_ecu_selection("2", choices) == "IGPM"
        assert _resolve_ecu_selection("BMS", choices) == "BMS"
        assert _resolve_ecu_selection("7A0", choices) == "7A0"
