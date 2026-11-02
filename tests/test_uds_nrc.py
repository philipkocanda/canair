"""Tests for canlib.uds.nrc — negative response code names per diagnostic protocol."""

import pytest

from canlib.uds.nrc import NRC_ABBREV, NRC_CODES, nrc_abbrev, nrc_description
from canlib.uds.parse import parse_uds_response
from canlib.uds.protocol import KWP, UDS, UNKNOWN


class TestNrcNames:
    def test_condition_refinements_are_named(self):
        assert nrc_description(0x88) == "vehicleSpeedTooHigh"
        assert nrc_description(0x90) == "shifterLeverNotInPark"
        assert nrc_abbrev(0x93) == "VTL"

    def test_ranges_get_their_iso_name(self):
        assert nrc_description(0x8E) == "unknown (0x8E)"  # ISO/SAE reserved
        assert nrc_description(0xA0) == "reservedForSpecificConditionsNotCorrect"
        assert nrc_description(0xF3) == "vehicleManufacturerSpecificConditionsNotCorrect"
        assert nrc_abbrev(0xF3) == "VMSCNC"
        assert nrc_abbrev(0xFF) == "?"

    def test_kwp_codes_without_a_uds_meaning_are_named_and_tagged(self):
        # Protocol unknown: ISO 14229-1 has no 0x23, so the KWP2000 name is offered, tagged.
        assert nrc_description(0x23) == "routineNotCompleteOrServiceInProgress (KWP2000)"
        assert nrc_description(0x77) == "blockTransferDataChecksumError (KWP2000)"
        assert nrc_description(0x80) == "serviceNotSupportedInActiveDiagnosticSession (KWP2000)"
        assert nrc_abbrev(0x23) == "RNC"
        assert nrc_abbrev(0x77) == "BTDCE"
        assert nrc_abbrev(0x80) == "SNSIADS"

    def test_kwp_protocol_prefers_iso_14230_names(self):
        assert nrc_description(0x23, "kwp") == "routineNotCompleteOrServiceInProgress"
        assert nrc_description(0x72, "kwp") == "transferAborted"  # UDS: generalProgrammingFailure
        assert nrc_description(0x22, "kwp") == "conditionsNotCorrectOrRequestSequenceError"
        assert nrc_description(0x12, "kwp") == "subFunctionNotSupportedOrInvalidFormat"

    def test_kwp_shared_codes_fall_through_to_the_common_names(self):
        assert nrc_description(0x31, "kwp") == "requestOutOfRange"
        assert nrc_description(0x21, "kwp") == "busyRepeatRequest"
        assert nrc_description(0x78, "kwp") == "requestCorrectlyReceivedResponsePending"

    def test_kwp_high_range_is_not_read_as_iso_14229_refinements(self):
        # 0x88 is vehicleSpeedTooHigh in ISO 14229-1 but reserved in SSF 14230-3, and
        # 0x90 is shifterLeverNotInPark there but vehicle-manufacturer-specific here.
        assert nrc_description(0x88) == "vehicleSpeedTooHigh"
        assert nrc_description(0x88, "kwp") == "reservedByDocument"
        assert nrc_description(0x90) == "shifterLeverNotInPark"
        assert nrc_description(0x90, "kwp") == "vehicleManufacturerSpecific"
        assert nrc_description(0xF3, "kwp") == "vehicleManufacturerSpecific"
        assert nrc_description(0xFB, "kwp") == "systemSupplierSpecific"
        assert nrc_description(0x80, "kwp") == "serviceNotSupportedInActiveDiagnosticSession"

    def test_uds_protocol_does_not_borrow_kwp_names(self):
        assert nrc_description(0x23, "uds") == "unknown (0x23)"
        assert nrc_description(0x72, "uds") == "generalProgrammingFailure"
        assert nrc_description(0x88, "uds") == "vehicleSpeedTooHigh"

    def test_parse_uses_the_target_protocol_for_nrc_desc(self):
        kwp = parse_uds_response("7F3323", expected_sid=0x33, protocol="kwp")
        assert kwp["nrc"] == 0x23 and kwp["nrc_desc"] == "routineNotCompleteOrServiceInProgress"
        unknown = parse_uds_response("7F3323", expected_sid=0x33)
        assert unknown["nrc_desc"] == "routineNotCompleteOrServiceInProgress (KWP2000)"

    def test_parsed_negative_response_uses_the_range_name(self):
        r = parse_uds_response("7F31F4", expected_sid=0x31)
        assert r["nrc"] == 0xF4
        assert r["nrc_desc"] == "vehicleManufacturerSpecificConditionsNotCorrect"


class TestNameAndMnemonicAgree:
    """`nrc_description` and `nrc_abbrev` share one resolver, so they cannot drift."""

    def test_every_named_code_has_a_mnemonic(self):
        assert set(NRC_CODES) == set(NRC_ABBREV)

    @pytest.mark.parametrize("protocol", [UDS, KWP, UNKNOWN, None])
    def test_a_code_has_a_mnemonic_exactly_when_it_has_a_name(self, protocol):
        for nrc in range(0x100):
            named = not nrc_description(nrc, protocol).startswith("unknown")
            assert (nrc_abbrev(nrc, protocol) != "?") == named, hex(nrc)

    def test_kwp_mnemonics_follow_the_kwp_meaning(self):
        # 0x72 is transferAborted on KWP2000, not UDS generalProgrammingFailure (GPF).
        assert nrc_abbrev(0x72, KWP) == "TA"
        assert nrc_abbrev(0x72) == "GPF"
        # 0x90 is manufacturer-specific on KWP2000, not shifterLeverNotInPark.
        assert nrc_abbrev(0x90, KWP) == "VMS"
        assert nrc_abbrev(0x90) == "SLNIP"
        assert nrc_abbrev(0x88, KWP) == "RBD"
        assert nrc_abbrev(0xFB, KWP) == "SSS"

    def test_uds_does_not_borrow_kwp_mnemonics(self):
        assert nrc_abbrev(0x23, UDS) == "?"
        assert nrc_abbrev(0x23) == "RNC"
