"""Tests for canlib.uds_services — the diagnostic service (SID) registry."""

from canlib.uds_services import (
    RESPONSE_SID_OFFSET,
    SERVICES,
    service_info,
    service_name,
    service_response_name,
)


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


class TestServiceInfo:
    def test_iocontrol_metadata(self):
        uds = service_info(0x2F)
        assert uds.id_width == 2
        assert uds.safe_discovery_sf == 0x00
        assert uds.actuates is True

        kwp = service_info(0x30)
        assert kwp.id_width == 1  # local identifier is a single byte
        assert kwp.safe_discovery_sf == 0x00
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
        assert service_response_name(0x01) is None


def test_registry_has_no_duplicate_sids():
    sids = [s.sid for s in SERVICES]
    assert len(sids) == len(set(sids))
