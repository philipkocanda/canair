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

    def test_iocontrol_default_range_is_make_neutral(self):
        # The iocontrol preset must not default to the old HK body-DID zone.
        assert preset_by_service(0x2F).default_range == "0000-FFFF"

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


class TestDominantService:
    def test_narrow_service_21(self):
        assert sp._dominant_service(["2101", "2102", "2105"]) == 0x21

    def test_wide_service_22(self):
        assert sp._dominant_service(["22BC03", "22BC06", "22BC07"]) == 0x22

    def test_dominant_service_wins(self):
        assert sp._dominant_service(["2101", "2102", "22F190"]) == 0x21

    def test_empty_returns_none(self):
        assert sp._dominant_service([]) is None
        assert sp._dominant_service(["zz", "1"]) is None


class TestDidFamilies:
    def test_pids_and_iocontrol_are_both_evidence(self):
        ecu = {
            "pids": {"22B002": {}, "22B00B": {}, "2101": {}},
            "iocontrol": {"B101": {}, "B130": {}},
            "iocontrol_discoveries": {"B108": {}, "C001": {}},
        }
        assert sp.did_families(ecu) == {0xB0: "PIDs", 0xB1: "IOControl", 0xC0: "IOControl"}

    def test_a_family_seen_in_both_names_both(self):
        ecu = {"pids": {"22BC03": {}}, "iocontrol": {"BC01": {}}}
        assert sp.did_families(ecu) == {0xBC: "PIDs+IOControl"}

    def test_kwp_local_ids_never_form_a_family(self):
        assert sp.did_families({"pids": {"2101": {}}, "iocontrol": {"01": {}}}) == {}

    def test_iocontrol_can_be_excluded(self):
        ecu = {"pids": {"22B002": {}}, "iocontrol": {"B401": {}}}
        assert sp.did_families(ecu, include_iocontrol=False) == {0xB0: "PIDs"}

    def test_family_ranges_whole_or_first_slots(self):
        fam = {0xB0: "PIDs", 0xC0: "PIDs"}
        assert sp.family_ranges(fam) == ((0xB000, 0xB0FF), (0xC000, 0xC0FF))
        assert sp.family_ranges(fam, 0x20) == ((0xB000, 0xB01F), (0xC000, 0xC01F))
        with pytest.raises(ValueError):
            sp.family_ranges(fam, 0)


class TestPlanScan:
    def _pids(self):
        return {
            "ecus": {
                "BMS": {"tx_id": 0x7E4, "pids": {"2101": {}, "2105": {}}},
                "IGPM": {"tx_id": 0x770, "pids": {"22BC03": {}, "22BC06": {}}},
                "BCM": {"tx_id": 0x7A0, "pids": {"22B003": {}, "22C00B": {}}},
                "PSM": {"tx_id": 0x7A3, "pids": {}, "iocontrol": {"B401": {}, "B408": {}}},
                "MYSTERY_UDS": {"tx_id": 0x7C0, "pids": {}},
            }
        }

    def _ecus(self):
        return {
            0x7E4: {"name": "BMS", "id_protocol": "KWP2000"},
            0x770: {"name": "IGPM", "id_protocol": "UDS"},
            0x7A0: {"name": "BCM", "id_protocol": "UDS"},
            0x7A3: {"name": "PSM", "id_protocol": "UDS"},
            0x7C0: {"name": "MYSTERY_UDS", "id_protocol": "UDS"},
            0x7B7: {"name": "NOPIDS_KWP", "id_protocol": "KWP2000"},
        }

    def test_kwp_ecu_from_pids(self):
        p = plan_scan("BMS", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.service == 0x21
        assert p.service_name == "live-data"
        assert p.ranges == ((0x01, 0xFF),)
        assert "known PID" in p.reason

    def test_uds_ecu_from_pids(self):
        p = plan_scan("IGPM", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.service == 0x22
        assert p.ranges == ((0xBC00, 0xBCFF),)

    def test_separate_families_are_swept_not_the_span_between(self):
        p = plan_scan("BCM", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.ranges == ((0xB000, 0xB0FF), (0xC000, 0xC0FF))

    def test_an_ecu_known_only_by_its_actuators_reads_their_family(self):
        p = plan_scan("PSM", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.service == 0x22
        assert p.ranges == ((0xB400, 0xB4FF),)
        assert "B4xx (IOControl)" in p.reason

    def test_slots_narrow_each_family(self):
        p = plan_scan("BCM", pids_data=self._pids(), ecus_data=self._ecus(), slots=0x20)
        assert p.ranges == ((0xB000, 0xB01F), (0xC000, 0xC01F))
        assert "first 32" in p.reason

    def test_uds_ecu_no_pids_falls_back_to_identity(self):
        p = plan_scan("MYSTERY_UDS", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.service == 0x22
        assert p.ranges == ((0xF100, 0xF1FF),)
        assert "identity" in p.reason.lower()

    def test_kwp_ecu_no_pids_falls_back_to_live_data(self):
        p = plan_scan("NOPIDS_KWP", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.service == 0x21
        assert p.ranges == ((0x01, 0xFF),)

    def test_resolve_by_hex_tx(self):
        p = plan_scan("7E4", pids_data=self._pids(), ecus_data=self._ecus())
        assert p.ecu == "BMS"
        assert p.tx_id == 0x7E4

    def test_unresolvable_returns_none(self):
        assert plan_scan("NOPE", pids_data=self._pids(), ecus_data=self._ecus()) is None


class TestCommandHelpers:
    def test_fmt_ranges(self):
        from canlib.commands.scan import _fmt_ranges

        assert _fmt_ranges(((0x01, 0xFF),), wide=False) == "01-FF"
        assert _fmt_ranges(((0xB000, 0xB0FF), (0xC000, 0xC0FF)), wide=True) == "B000-B0FF,C000-C0FF"

    def test_slots_needs_the_smart_default(self, capsys):
        from canlib.commands.scan import _resolve_plan

        args = SimpleNamespace(
            tx="BCM",
            service=None,
            range="B000-B0FF",
            slots=32,
            append=None,
            session=False,
            wake=False,
        )
        with pytest.raises(SystemExit):
            _resolve_plan(args)
        assert "--slots" in capsys.readouterr().err

    def test_a_malformed_range_list_is_refused_up_front(self, capsys):
        from canlib.commands.scan import _resolve_plan

        args = SimpleNamespace(
            tx="7A0",
            service="22",
            range="B000-B0FF,nope",
            slots=None,
            append=None,
            session=False,
            wake=False,
        )
        with pytest.raises(SystemExit):
            _resolve_plan(args)
        assert "nope" in capsys.readouterr().err

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
