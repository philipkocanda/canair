"""Tests for canlib.formatting.decode_uds_response — the one-line response decode.

The function is a hand-rolled ``if sid == …`` chain that duplicates the service
names in :mod:`canlib.uds_services`, and it had no tests at all — which is how two
crossed SIDs survived: ``0x68`` (CommunicationControl's response) was labelled
ControlDTCSetting, ``0x6C`` (DynamicallyDefineDataIdentifier's) carried
CommunicationControl's sub-function names, and ``0xC5`` — the *real* ControlDTCSetting
response — was unrecognised.

:func:`test_service_name_matches_the_registry` is the guard against a recurrence:
it asserts each branch names the service the response SID actually belongs to.
"""

import pytest

from canlib.formatting import decode_uds_response
from canlib.uds_services import RESPONSE_SID_OFFSET, SERVICES, service_name


def _decode(hex_: str) -> str | None:
    return decode_uds_response(bytes.fromhex(hex_))


# (payload, request SID the response belongs to) — the response SID is
# request + 0x40, so this table also pins each branch to the right service.
_NAMED_RESPONSES = [
    ("5001", 0x10),  # DiagnosticSessionControl
    ("5101", 0x11),  # ECUReset
    ("6101FF", 0x21),  # ReadDataByLocalIdentifier
    ("62B004FF", 0x22),  # ReadDataByIdentifier
    ("6703AABB", 0x27),  # SecurityAccess
    ("680100", 0x28),  # CommunicationControl
    ("6C010203", 0x2C),  # DynamicallyDefineDataIdentifier
    ("6EB004", 0x2E),  # WriteDataByIdentifier
    ("6FB00300", 0x2F),  # InputOutputControlByIdentifier
    ("710312A1", 0x31),  # RoutineControl
    ("5902", 0x19),  # ReadDTCInformation
    ("C501", 0x85),  # ControlDTCSetting
]


@pytest.mark.parametrize("payload,request_sid", _NAMED_RESPONSES)
def test_response_sid_is_request_plus_offset(payload, request_sid):
    # Sanity-check the table itself, so a typo in it can't mask a real mismatch.
    assert int(payload[:2], 16) == (request_sid + RESPONSE_SID_OFFSET) & 0xFF


# Intentional display shorthands: the registry name is either too long for a
# one-liner or carries a dual UDS/KWP2000 label that can't be echoed verbatim.
_DISPLAY_ALIASES = {
    0x2F: "IOControl",  # InputOutputControlByIdentifier
    0x30: "IOControl",  # InputOutputControlByLocalIdentifier
    0x31: "RoutineControl",  # ...(UDS) / StartRoutineByLocalIdentifier (KWP2000)
}


def _accepted_names(request_sid: int) -> list[str]:
    name = service_name(request_sid)
    assert name is not None
    names = [name]
    if request_sid in _DISPLAY_ALIASES:
        names.append(_DISPLAY_ALIASES[request_sid])
    return names


@pytest.mark.parametrize("payload,request_sid", _NAMED_RESPONSES)
def test_service_name_matches_the_registry(payload, request_sid):
    """Each branch must name the service its response SID belongs to."""
    decoded = _decode(payload)
    assert decoded is not None, f"{payload} produced no decode"
    accepted = _accepted_names(request_sid)
    assert any(n in decoded for n in accepted), (
        f"{payload}: response 0x{int(payload[:2], 16):02X} belongs to "
        f"request 0x{request_sid:02X} ({accepted[0]}) but decoded as {decoded!r}"
    )


@pytest.mark.parametrize("payload,request_sid", _NAMED_RESPONSES)
def test_decode_never_names_a_different_service(payload, request_sid):
    """The actual bug class: a branch labelled with *another* service's name.

    Naming 0x68 "ControlDTCSetting" (0x85's service) and 0x6C
    "CommunicationControl" (0x28's) both passed silently because nothing checked
    that a decode doesn't advertise a service it cannot be.
    """
    decoded = _decode(payload)
    assert decoded is not None
    mine = _accepted_names(request_sid)
    for other in SERVICES:
        if other.sid == request_sid:
            continue
        # First token only: registry names like "ReadDTCByStatus (KWP2000)" carry
        # a parenthetical, and a dual name carries a "/" alternative.
        token = other.name.split()[0].split("/")[0].strip()
        # Skip tokens that overlap our own accepted names — an overlap would make
        # this assertion fire on a correct decode rather than on a crossed one.
        if any(token in n or n in token for n in mine):
            continue
        assert token not in decoded, (
            f"{payload} (request 0x{request_sid:02X}) decoded as {decoded!r}, "
            f"which names a different service: 0x{other.sid:02X} {other.name}"
        )


class TestCommunicationControl:
    def test_control_type_zero_is_valid(self):
        # enableRxAndTx is controlType 0x00. The old map started at 0x01, so every
        # value was named as its predecessor and 0x00 fell through to raw hex.
        assert _decode("6800") == "CommunicationControl: enableRxAndTx"

    @pytest.mark.parametrize(
        "raw,name",
        [
            ("6800", "enableRxAndTx"),
            ("6801", "enableRxAndDisableTx"),
            ("6802", "disableRxAndEnableTx"),
            ("6803", "disableRxAndTx"),
        ],
    )
    def test_iso14229_control_types(self, raw, name):
        assert _decode(raw) == f"CommunicationControl: {name}"

    def test_unknown_control_type_shows_hex(self):
        assert _decode("68AA") == "CommunicationControl: 0xAA"

    def test_is_not_reported_as_a_did_read(self):
        # The old branch read data[1:3] as a DID, inventing an identifier that a
        # CommunicationControl response does not carry.
        assert "DID" not in (_decode("680102") or "")


class TestDynamicallyDefineDataIdentifier:
    @pytest.mark.parametrize(
        "raw,name",
        [
            ("6C01", "defineByIdentifier"),
            ("6C02", "defineByMemoryAddress"),
            ("6C03", "clearDynamicallyDefinedDataIdentifier"),
        ],
    )
    def test_subfunctions(self, raw, name):
        assert _decode(raw) == f"DynamicallyDefineDataIdentifier: {name}"

    def test_echoed_dynamic_did_is_reported(self):
        assert _decode("6C01F200") == (
            "DynamicallyDefineDataIdentifier: defineByIdentifier, dynamic DID 0xF200"
        )

    def test_no_longer_claims_to_be_communication_control(self):
        assert "CommunicationControl" not in (_decode("6C0102") or "")


class TestControlDTCSetting:
    def test_response_sid_is_recognised(self):
        # 0xC5 (0x85 + 0x40) previously returned None — the service was decoded
        # under 0x68 instead, so the real response went unnamed.
        assert _decode("C501") is not None

    @pytest.mark.parametrize("raw,setting", [("C501", "on"), ("C502", "off")])
    def test_setting_type(self, raw, setting):
        decoded = _decode(raw)
        assert decoded is not None
        assert f"DTC setting {setting}" in decoded

    def test_keeps_the_dtc_logging_warning(self):
        # The warning belonged to ControlDTCSetting all along; it must survive the
        # move to the correct SID.
        decoded = _decode("C501")
        assert decoded is not None
        assert decoded.startswith("WARNING:")
        assert "DTC logging may be altered" in decoded

    def test_does_not_invent_a_did(self):
        assert "DID" not in (_decode("C501") or "")


class TestLocalIdentifierNaming:
    def test_kwp_read_names_the_local_identifier(self):
        # 0x21's identifier is a 1-byte LID, matching the registry name and the
        # role canair reports for the same byte elsewhere.
        assert _decode("6101FFEE") == "ReadDataByLocalIdentifier: LID 0x01, 2 data bytes"

    def test_uds_read_still_names_a_did(self):
        assert _decode("62B004FFEE") == "ReadDataByIdentifier: DID 0xB004, 2 data bytes"


class TestGracefulHandling:
    def test_empty_payload(self):
        assert decode_uds_response(b"") is None

    def test_unknown_sid(self):
        assert _decode("9901") is None

    @pytest.mark.parametrize("payload", ["68", "6C", "C5", "61", "62B0", "71", "6F"])
    def test_truncated_payloads_do_not_raise(self, payload):
        # A short/garbled frame must degrade to None, never IndexError.
        decode_uds_response(bytes.fromhex(payload))
