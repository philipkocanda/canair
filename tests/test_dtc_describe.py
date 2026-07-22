"""Tests for canlib.dtc_describe — structural DTC interpretation."""

from canlib.dtc_describe import describe_dtc, dtc_kind


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

    DEFS = {
        "dtcs": {
            "C182C": {"description": "DC fast-charge (CCS) charging / PLC communication failure"},
            "B1285": {"description": "Direction Control Motor — AUTO Defog"},
        },
        "failure_types": {0x77: "Hyundai-specific — meaning not yet determined"},
    }

    def test_profile_description_used(self):
        d = describe_dtc("C182C-00", defs=self.DEFS)
        assert d["description"] == "DC fast-charge (CCS) charging / PLC communication failure"
        assert d["meaning"].startswith("DC fast-charge")

    def test_profile_ftb_and_description_combined(self):
        d = describe_dtc("B1285-77", defs=self.DEFS)
        assert d["description"] == "Direction Control Motor — AUTO Defog"
        assert d["failure_type"] == "0x77"
        assert d["failure_type_desc"] == "Hyundai-specific — meaning not yet determined"
        # meaning folds in both the code meaning and the FTB note.
        assert "AUTO Defog" in d["meaning"]
        assert "FTB 0x77" in d["meaning"]

    def test_profile_missing_code_falls_back_to_structural(self):
        d = describe_dtc("B2915-00", defs=self.DEFS)
        assert d["description"] is None
        assert d["meaning"] == "Body · manufacturer-specific"

    def test_real_profile_dtc_yaml_loads(self):
        # The bundled ioniq-2017 profile ships C182C + B1285 definitions.
        d = describe_dtc("C182C-00")  # defs=None -> loads active profile
        assert d["description"] and "charg" in d["description"].lower()
        b = describe_dtc("B1285-77")
        assert "Defog" in (b["description"] or "")
        assert b["failure_type_desc"] and "Hyundai" in b["failure_type_desc"]
