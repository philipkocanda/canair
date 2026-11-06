"""Tests for canair.uds.services — the diagnostic service (SID) registry."""

import re

import pytest

from canair.uds import services
from canair.uds.services import (
    BOTH,
    CONTROL_DTC_SETTING,
    DEFAULT_SESSION_REQUEST,
    DIAGNOSTIC_SESSION_CONTROL,
    EXTENDED_SESSION_REQUEST,
    KWP2000,
    OBD2,
    READ_DATA_BY_IDENTIFIER,
    RESPONSE_SID_OFFSET,
    SERVICES,
    TESTER_PRESENT,
    TESTER_PRESENT_REQUEST,
    identifier_width,
    positive_response_sid,
    request_sid_of,
    service_info,
    service_name,
    service_response_name,
)
from canair.uds.subfunctions import SessionType


class TestServiceName:
    def test_known_uds_service(self):
        assert service_name(0x2F) == "InputOutputControlByIdentifier"
        assert service_name(0x22) == "ReadDataByIdentifier"

    def test_known_kwp_service(self):
        assert service_name(0x30) == "InputOutputControlByLocalIdentifier"
        assert service_name(0x21) == "ReadDataByLocalIdentifier"
        assert service_name(0x1A) == "ReadEcuIdentification"

    def test_unknown_service(self):
        assert service_name(0xAB) is None


class TestKwpServices:
    """The ISO 14230-3 services that were missing from, or mislabelled in, the registry."""

    def test_by_address_routine_trio_uses_kwp_names(self):
        assert "StartRoutineByAddress" in service_name(0x38)
        assert "RequestFileTransfer" in service_name(0x38)  # the UDS meaning of the same byte
        assert service_name(0x39) == "StopRoutineByAddress (KWP2000)"
        assert service_name(0x3A) == "RequestRoutineResultsByAddress (KWP2000)"

    def test_by_address_services_that_run_or_stop_code_actuate(self):
        assert service_info(0x38).actuates is True
        assert service_info(0x39).actuates is True
        assert service_info(0x3A).actuates is False  # results are a read

    def test_by_address_services_carry_no_identifier_width(self):
        # Their address is 3 bytes, not an id, so a layout must not read it as a LID/DID.
        for sid in (0x38, 0x39, 0x3A):
            assert service_info(sid).id_width == 0

    @pytest.mark.parametrize(
        "sid,name",
        [
            (0x12, "ReadFreezeFrameData (KWP2000)"),
            (0x13, "ReadDTCs (KWP2000)"),
            (0x17, "ReadStatusOfDTCs (KWP2000)"),
            (0x20, "StopDiagnosticSession (KWP2000)"),
            (0x25, "StopRepeatedDataTransmission (KWP2000)"),
            (0x26, "SetDataRates (KWP2000)"),
            (0x80, "EscapeCode (KWP2000)"),
        ],
    )
    def test_kwp_only_services_are_registered(self, sid, name):
        assert service_name(sid) == name
        assert service_info(sid).family == KWP2000
        assert service_response_name(sid + RESPONSE_SID_OFFSET) == f"{name} (response)"

    def test_write_memory_by_address_is_registered_and_actuates(self):
        info = service_info(0x3D)
        assert info.name == "WriteMemoryByAddress"
        assert info.actuates is True

    @pytest.mark.parametrize("sid", [0x22, 0x2E, 0x2F])
    def test_identifier_services_shared_with_kwp_common_identifier(self, sid):
        # Same 2-byte-id request on both protocols (KWP2000 calls them ...ByCommonIdentifier).
        assert service_info(sid).family == BOTH
        assert service_info(sid).id_width == 2

    def test_every_blocked_service_is_a_registered_actuating_or_memory_service(self):
        # The blocklist and the registry must not drift: a blocked SID without a registry
        # entry would print as an unnamed service in scan output.
        from canair.safety.blocklist import BLOCKED_UDS_SERVICES

        for sid in BLOCKED_UDS_SERVICES:
            assert service_info(sid) is not None, f"0x{sid:02X} blocked but unregistered"


class TestServiceInfo:
    def test_iocontrol_metadata(self):
        uds = service_info(0x2F)
        assert uds.id_width == 2
        assert uds.safe_discovery_sf == 0x00
        assert uds.actuates is True

        kwp = service_info(0x30)
        assert kwp.id_width == 1  # local identifier is a single byte
        # No standard release: 30 {LID} 00 actuates on a Kia Soul EV charger.
        assert kwp.safe_discovery_sf is None
        assert kwp.actuates is True

    def test_routine_metadata(self):
        r = service_info(0x31)
        assert r.id_width == 2
        assert r.safe_discovery_sf == 0x03

    def test_unknown_returns_none(self):
        assert service_info(0xAB) is None


class TestServiceResponseName:
    def test_positive_response_echo(self):
        # 0x6F is the positive response to 0x2F.
        assert service_response_name(0x2F + RESPONSE_SID_OFFSET) == (
            "InputOutputControlByIdentifier (response)"
        )
        assert service_response_name(0x70) == ("InputOutputControlByLocalIdentifier (response)")

    def test_negative_response_marker(self):
        assert service_response_name(0x7F) == "NegativeResponse"

    def test_unknown(self):
        # A byte that is neither a registered request SID nor request+0x40.
        assert service_response_name(0x0B) is None
        assert service_response_name(0xFE) is None

    def test_obd2_mode_responses_are_named(self):
        # OBD-II modes share the request-SID byte and the +0x40 response rule, so
        # a mode-01/09 response names its mode rather than reading as unknown.
        assert service_response_name(0x41) == "ShowCurrentData (response)"
        assert service_response_name(0x49) == "RequestVehicleInformation (response)"

    def test_obd2_monitor_modes_stay_unregistered(self):
        # 0x05/0x06/0x08 are excluded on purpose: their identifier is an
        # O2-monitor id / MID / TID, not a PID, so registering an id_width would
        # let a caller mislabel that byte.
        for sid in (0x05, 0x06, 0x08):
            assert service_info(sid) is None


def test_registry_has_no_duplicate_sids():
    sids = [s.sid for s in SERVICES]
    assert len(sids) == len(set(sids))


def _words(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _sid_constants() -> dict[str, int]:
    skip = {"NEGATIVE_RESPONSE_SID", "RESPONSE_SID_OFFSET"}
    return {
        name: value
        for name, value in vars(services).items()
        if name.isupper() and type(value) is int and name not in skip
    }


def _registry_names(info) -> set[str]:
    """Each alternative of a row's name, without its protocol tag: ``A (UDS) / B`` -> a, b."""
    return {_words(re.sub(r"\(.*?\)", "", part)) for part in info.name.split(" / ")}


@pytest.mark.parametrize(("name", "sid"), sorted(_sid_constants().items()))
def test_every_sid_constant_names_its_registry_row(name, sid):
    info = service_info(sid)
    assert info is not None, f"{name} = 0x{sid:02X} is not in SERVICES"
    bare = name.removeprefix("KWP_").removeprefix("OBD_")
    assert _words(bare) in _registry_names(info), f"{name} does not name {info.name!r}"
    if name.startswith("KWP_"):
        assert info.family in (KWP2000, BOTH)
    if name.startswith("OBD_"):
        assert info.family == OBD2


def test_every_registry_row_has_a_constant():
    named = set(_sid_constants().values())
    assert [f"0x{s.sid:02X}" for s in SERVICES if s.sid not in named] == []


def test_positive_response_sid_round_trips():
    assert positive_response_sid(READ_DATA_BY_IDENTIFIER) == 0x62
    assert request_sid_of(0x62) == READ_DATA_BY_IDENTIFIER
    assert positive_response_sid(CONTROL_DTC_SETTING) == 0xC5


def test_tester_present_request_is_the_tester_present_service():
    assert bytes.fromhex(TESTER_PRESENT_REQUEST) == bytes([TESTER_PRESENT, 0x00])


def test_identifier_width_comes_from_the_registry():
    assert identifier_width(READ_DATA_BY_IDENTIFIER) == 2
    assert identifier_width(0x21) == 1
    assert identifier_width(0x10) == 0
    assert identifier_width(0x99) == 0


def test_the_session_requests_are_diagnostic_session_control():
    assert bytes.fromhex(DEFAULT_SESSION_REQUEST) == bytes(
        [DIAGNOSTIC_SESSION_CONTROL, SessionType.DEFAULT_SESSION]
    )
    assert bytes.fromhex(EXTENDED_SESSION_REQUEST) == bytes(
        [DIAGNOSTIC_SESSION_CONTROL, SessionType.EXTENDED_DIAGNOSTIC_SESSION]
    )
