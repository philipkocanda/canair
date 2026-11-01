"""Tests for canlib.dtc_describe — structural DTC interpretation."""

from typing import ClassVar

import pytest

from canlib.dtc.describe import describe_dtc, dtc_kind


class TestDtcKind:
    def test_body_manufacturer(self):
        assert dtc_kind("B", 2) == "manufacturer-specific"
        assert dtc_kind("C", 1) == "manufacturer-specific"

    def test_generic(self):
        assert dtc_kind("B", 0) == "generic (ISO/SAE)"
        assert dtc_kind("P", 0) == "generic (ISO/SAE)"
        assert dtc_kind("P", 2) == "generic (ISO/SAE)"

    def test_powertrain_manufacturer(self):
        assert dtc_kind("P", 1) == "manufacturer-specific"

    def test_reserved(self):
        assert dtc_kind("U", 3) == "reserved"


class TestDescribeDtc:
    """Pure structural interpretation (defs={} = no profile definitions)."""

    def test_body_mfr_no_subtype(self):
        d = describe_dtc("B2915-00", defs={})
        assert d["category"] == "Body"
        assert d["kind"] == "manufacturer-specific"
        assert d["failure_type"] == "0x00"
        assert d["failure_type_desc"] == "no sub-type information"
        assert d["description"] is None
        # FTB 0x00 is omitted from the compact meaning.
        assert d["meaning"] == "Body · manufacturer-specific"

    def test_chassis_mfr(self):
        d = describe_dtc("C182C-00", defs={})
        assert d["category"] == "Chassis"
        assert d["kind"] == "manufacturer-specific"

    def test_failure_type_decoded(self):
        d = describe_dtc("B1234-11", defs={})
        assert d["failure_type"] == "0x11"
        assert d["failure_type_desc"] == "circuit short to ground"
        assert "circuit short to ground" in d["meaning"]

    def test_generic_known_code(self):
        d = describe_dtc("P0420", defs={})
        assert d["category"] == "Powertrain"
        assert d["kind"] == "generic (ISO/SAE)"
        assert d["description"] == "Catalyst system efficiency below threshold (Bank 1)"

    def test_network_category(self):
        assert describe_dtc("U0100", defs={})["category"] == "Network"

    def test_kwp_two_byte_no_ftb(self):
        d = describe_dtc("B2915", defs={})
        assert d["failure_type"] is None
        assert d["category"] == "Body"

    def test_malformed_is_safe(self):
        d = describe_dtc("", defs={})
        assert d["category"] == "unknown"


class TestProfileDefinitions:
    """Manufacturer meanings + failure-type bytes come from the profile's per-ECU dtcs: + failure_types:."""

    DEFS: ClassVar = {
        "dtcs": {
            "C182C": {"description": "DC fast-charge (CCS) charging / PLC communication failure"},
            "B1285": {"description": "Direction Control Motor — AUTO Defog"},
        },
        "failure_types": {0xF3: "manufacturer-specific symptom"},
    }

    def test_profile_description_used(self):
        d = describe_dtc("C182C-00", defs=self.DEFS)
        assert d["description"] == "DC fast-charge (CCS) charging / PLC communication failure"
        assert d["meaning"].startswith("DC fast-charge")

    def test_profile_ftb_and_description_combined(self):
        d = describe_dtc("B1285-F3", defs=self.DEFS)
        assert d["description"] == "Direction Control Motor — AUTO Defog"
        assert d["failure_type"] == "0xF3"
        assert d["failure_type_desc"] == "manufacturer-specific symptom"
        # meaning folds in both the code meaning and the FTB note.
        assert "AUTO Defog" in d["meaning"]
        assert "FTB 0xF3" in d["meaning"]

    def test_standard_ftb_needs_no_profile_entry(self):
        # Hyundai/Kia use the SAE J2012 table unchanged (ES95486 Annex D).
        d = describe_dtc("B1285-77", defs=self.DEFS)
        assert d["failure_type_desc"] == "commanded position not reachable"

    def test_reserved_ftb_is_unnamed(self):
        assert describe_dtc("B1285-2A", defs={})["failure_type_desc"] is None

    def test_profile_missing_code_falls_back_to_structural(self):
        d = describe_dtc("B2915-00", defs=self.DEFS)
        assert d["description"] is None
        assert d["meaning"] == "Body · manufacturer-specific"

    def test_real_profile_dtc_yaml_loads(self):
        # The bundled hyundai-ioniq-ae-ev profile ships C182C + B1285 definitions.
        d = describe_dtc("C182C-00", ecu="CCM")  # defs=None -> loads active profile
        assert d["description"] and "charg" in d["description"].lower()
        b = describe_dtc("B1285-77", ecu="HVAC")
        assert "Defog" in (b["description"] or "")
        assert b["failure_type_desc"] == "commanded position not reachable"


class TestPerEcuMeaning:
    """A code means what the reporting ECU says: two units may define it differently."""

    ECUS: ClassVar = {
        "BSD-L": {"dtcs": {"C1616": "CAN communication line malfunction", "C1101": "Power high"}},
        "EPS": {"dtcs": {"C1616": {"description": "CAN bus off"}, "c1101": "Power high"}},
        "BMS": {"dtcs": {"P1AAA": "Cell imbalance"}},
    }

    @pytest.fixture(autouse=True)
    def _profile(self, monkeypatch):
        monkeypatch.setattr("canlib.pids.load_pids", lambda: {"ecus": self.ECUS})

    def test_the_reporting_ecus_meaning_wins(self):
        assert describe_dtc("C1616-00", ecu="EPS")["description"] == "CAN bus off"
        assert describe_dtc("C1616-00", ecu="eps")["description"] == "CAN bus off"
        assert describe_dtc("C1616-00", ecu="BSD-L")["description"] == (
            "CAN communication line malfunction"
        )

    def test_another_ecus_meaning_is_never_borrowed(self):
        d = describe_dtc("P1AAA", ecu="EPS")
        assert d["description"] is None
        assert d["meaning"] == "Powertrain · manufacturer-specific"

    def test_without_an_ecu_only_an_agreed_meaning_is_given(self):
        assert describe_dtc("C1101-00")["description"] == "Power high"
        assert describe_dtc("C1616-00")["description"] is None  # depends on the unit

    def test_a_generic_code_needs_no_ecu_entry(self):
        assert describe_dtc("U0100", ecu="EPS")["description"] == "Lost communication with ECM/PCM"

    def test_a_read_and_a_logged_scan_are_described_by_their_ecu(self, monkeypatch):
        from canlib.dtc import log as dtc_log
        from canlib.modes import dtc

        monkeypatch.setattr("canlib.ecus.ecu_name", lambda tx: {0x7D4: "EPS"}[tx])
        monkeypatch.setattr("canlib.ecus.load_ecus", lambda: {0x7D4: {"name": "EPS"}})
        rec = {"dtc": "C1616-00", "raw": "561600", "status": 0x09, "status_bits": []}
        assert dtc._jsonable([rec], 0x7D4)[0]["interpretation"]["description"] == "CAN bus off"
        # Logged under a since-renamed label: resolved by the TX id it recorded.
        scan = dtc_log.build_scan(
            "all", {"OLD (0x7D4)": {"tx": "0x7D4", "dtcs": ["C1616-00"]}}, timestamp="t"
        )
        assert "CAN bus off" in "\n".join(dtc_log.render_scan(scan))
