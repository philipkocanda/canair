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
    def test_body_mfr_no_subtype(self):
        d = describe_dtc("B2915-00")
        assert d["category"] == "Body"
        assert d["kind"] == "manufacturer-specific"
        assert d["failure_type"] == "0x00"
        assert d["failure_type_desc"] == "no sub-type information"
        assert d["description"] is None
        # FTB 0x00 is omitted from the compact meaning.
        assert d["meaning"] == "Body · manufacturer-specific"

    def test_chassis_mfr(self):
        d = describe_dtc("C182C-00")
        assert d["category"] == "Chassis"
        assert d["kind"] == "manufacturer-specific"

    def test_failure_type_decoded(self):
        d = describe_dtc("B1234-11")
        assert d["failure_type"] == "0x11"
        assert d["failure_type_desc"] == "circuit short to ground"
        assert "circuit short to ground" in d["meaning"]

    def test_generic_known_code(self):
        d = describe_dtc("P0420")
        assert d["category"] == "Powertrain"
        assert d["kind"] == "generic (ISO/SAE)"
        assert d["description"] == "Catalyst system efficiency below threshold (Bank 1)"

    def test_network_category(self):
        assert describe_dtc("U0100")["category"] == "Network"

    def test_kwp_two_byte_no_ftb(self):
        d = describe_dtc("B2915")
        assert d["failure_type"] is None
        assert d["category"] == "Body"

    def test_malformed_is_safe(self):
        d = describe_dtc("")
        assert d["category"] == "unknown"
