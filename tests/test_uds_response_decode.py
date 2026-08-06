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

import inspect
import re

import pytest

from canlib.formatting import decode_uds_response
from canlib.uds_layout import (
    ROLE_CTRL,
    ROLE_DID,
    ROLE_LID,
    ROLE_RID,
    ROLE_SF,
    SUBFUNCTION_NAMES,
    response_layout,
    subfunction_name,
)
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

    @pytest.mark.parametrize("resp_sid", list(range(0x40, 0x100)))
    def test_no_response_sid_raises_on_any_short_payload(self, resp_sid):
        # Exhaustive: every response SID, at every truncation, must degrade
        # cleanly. A branch whose length guard is one too small would IndexError
        # on a real truncated frame.
        for n in range(0, 6):
            decode_uds_response(bytes([resp_sid]) + bytes(n))


class TestCrossCheckedAgainstUdsLayout:
    """``uds_layout`` and ``decode_uds_response`` are two independent models of the
    same response headers. Any disagreement means one of them is wrong, so pin
    them to each other rather than trusting each in isolation.
    """

    # Sentinel identifier bytes, chosen so a value read from the wrong offset
    # cannot coincidentally match the right one.
    _SENTINELS = bytes([0xA1, 0xB2, 0xC3, 0xD4, 0xE5])

    def _payload(self, resp_sid: int, layout) -> bytes:
        """A payload whose header bytes are distinctive, per ``layout``."""
        out = bytearray([resp_sid])
        for field in layout.fields:
            if field.role == ROLE_SF:
                # A sub-function must be a *valid* value or the decode reports hex.
                out.append(0x01)
            elif field.role == ROLE_CTRL:
                out.append(0x00)  # returnControlToECU
            else:
                out.extend(self._SENTINELS[: field.width])
        out.extend(b"\x11\x22")  # trailing data
        return bytes(out)

    @pytest.mark.parametrize("resp_sid", [0x62, 0x61, 0x6E, 0x6F, 0x71, 0x6C])
    def test_identifier_is_read_from_the_offset_uds_layout_declares(self, resp_sid):
        layout = response_layout(resp_sid)
        assert layout is not None
        id_field = next(
            (f for f in layout.fields if f.role in (ROLE_DID, ROLE_LID, ROLE_RID)), None
        )
        assert id_field is not None, f"0x{resp_sid:02X} has no identifier field to check"
        payload = self._payload(resp_sid, layout)
        decoded = _decode(payload.hex())
        assert decoded is not None

        # Where uds_layout says the identifier sits, and what it should read as.
        offset = 1 + sum(f.width for f in layout.fields[: layout.fields.index(id_field)])
        expected = payload[offset : offset + id_field.width]
        want = f"0x{int.from_bytes(expected, 'big'):0{id_field.width * 2}X}"
        assert want in decoded, (
            f"0x{resp_sid:02X}: uds_layout puts {id_field.role} at payload byte "
            f"{offset} (={want}) but decode_uds_response said {decoded!r}"
        )

    @pytest.mark.parametrize("payload,request_sid", _NAMED_RESPONSES)
    def test_decoded_services_have_a_layout(self, payload, request_sid):
        # If formatting.py can name a response, uds_layout should be able to lay it
        # out — otherwise `bix --annotate` silently falls back to generic labels
        # for a service the rest of the tool understands.
        resp_sid = int(payload[:2], 16)
        assert response_layout(resp_sid) is not None, (
            f"0x{resp_sid:02X} is decoded by formatting.py but has no uds_layout entry"
        )

    def test_every_decoded_response_sid_has_a_layout(self):
        """Total, self-maintaining version of the check above.

        The set of response SIDs is read out of ``decode_uds_response`` itself, so
        adding a branch there without a matching ``uds_layout`` entry fails here —
        no hand-maintained list to forget. The exemptions are services whose header
        is genuinely variable-length or absent (documented in ``_RESPONSE_FIELDS``).
        """
        variable_or_headerless = {0x63, 0x74, 0x75, 0x76, 0x77}
        src = inspect.getsource(decode_uds_response)
        decoded = {int(m, 16) for m in re.findall(r"sid == (0x[0-9A-Fa-f]{2})", src)}
        assert decoded, "could not extract any response SIDs — did the source change?"
        missing = sorted(
            sid for sid in decoded - variable_or_headerless if response_layout(sid) is None
        )
        assert not missing, (
            "decode_uds_response handles these response SIDs but uds_layout has no "
            f"entry: {[hex(s) for s in missing]}"
        )

    def test_subfunction_names_are_shared_not_copied(self):
        # The session table lives in one place now; a private copy is what let
        # formatting.py's drift out of step (missing the KWP2000 0x8x range).
        from canlib.modes.sessions_scan import SESSION_NAMES

        assert SESSION_NAMES is SUBFUNCTION_NAMES[0x10]


class TestSubfunctionCoverage:
    @pytest.mark.parametrize("session", [0x01, 0x02, 0x03, 0x04, 0x81, 0x82, 0x83, 0x85])
    def test_every_probed_session_type_is_named(self, session):
        # A KWP2000 ECU answers 0x81/0x82/0x83; those printed as bare hex before.
        decoded = _decode(f"50{session:02X}")
        assert decoded is not None
        assert f"0x{session:02X}" not in decoded, f"session 0x{session:02X} rendered as hex"

    def test_scanned_session_modes_are_all_named(self):
        from canlib.modes.sessions_scan import KWP_SESSION_MODES, UDS_SESSION_MODES

        for mode in (*UDS_SESSION_MODES, *KWP_SESSION_MODES):
            assert subfunction_name(0x10, mode) is not None

    @pytest.mark.parametrize("reset", [0x01, 0x02, 0x03, 0x04, 0x05])
    def test_ecu_reset_types_are_named(self, reset):
        decoded = _decode(f"51{reset:02X}")
        assert decoded is not None
        assert f"0x{reset:02X}" not in decoded

    def test_iocontrol_params_match_the_scanner_constants(self):
        # formatting.py and the two IOControl scanners must agree on these values.
        from canlib.modes.iocontrol_scan import (
            SF_FREEZE,
            SF_RESET_TO_DEFAULT,
            SF_RETURN_CONTROL,
            SF_SHORT_TERM_ADJ,
        )

        assert subfunction_name(0x2F, SF_RETURN_CONTROL) == "returnControlToECU"
        assert subfunction_name(0x2F, SF_RESET_TO_DEFAULT) == "resetToDefault"
        assert subfunction_name(0x2F, SF_FREEZE) == "freezeCurrentState"
        assert subfunction_name(0x2F, SF_SHORT_TERM_ADJ) == "shortTermAdjustment"

    def test_kwp_iocontrol_shares_the_uds_control_values(self):
        from canlib.modes.kwp_iocontrol_scan import IOCP_RETURN_CONTROL

        assert subfunction_name(0x30, IOCP_RETURN_CONTROL) == "returnControlToECU"

    def test_routine_subfunctions_match_the_routines_module(self):
        from canlib.modes.routines import SF_RESULTS, SF_START, SF_STOP

        assert subfunction_name(0x31, SF_START) == "startRoutine"
        assert subfunction_name(0x31, SF_STOP) == "stopRoutine"
        assert subfunction_name(0x31, SF_RESULTS) == "requestRoutineResults"

    def test_unnamed_value_returns_none_rather_than_inventing_a_label(self):
        assert subfunction_name(0x10, 0xEE) is None
        assert subfunction_name(0x99, 0x01) is None


class TestFirmwareTransferBlockSize:
    """0x74/0x75 previously reported the response's RESERVED low nibble as an
    "addrLen" — request semantics (addressAndLengthFormatIdentifier) read into a
    response, which carries a lengthFormatIdentifier instead.
    """

    def test_block_size_is_decoded_from_the_length_format_identifier(self):
        # 0x20 -> high nibble 2 = a 2-byte maxNumberOfBlockLength (0x02F0 = 752).
        decoded = _decode("742002F0")
        assert decoded is not None
        assert "max block 752 bytes" in decoded

    def test_reserved_nibble_is_not_reported_as_an_address_length(self):
        for payload in ("742002F0", "752002F0"):
            decoded = _decode(payload)
            assert decoded is not None
            assert "addrLen" not in decoded
            assert "memLen" not in decoded

    def test_single_byte_block_length(self):
        decoded = _decode("7410FF")
        assert decoded is not None
        assert "max block 255 bytes" in decoded

    def test_truncated_block_length_is_reported_not_guessed(self):
        # Declared 4 bytes but only 1 present: say so rather than print a wrong size.
        decoded = _decode("7440FF")
        assert decoded is not None
        assert "truncated" in decoded

    def test_both_transfer_directions_keep_their_warning(self):
        for payload, word in (("742002F0", "RequestDownload"), ("752002F0", "RequestUpload")):
            decoded = _decode(payload)
            assert decoded is not None
            assert decoded.startswith("WARNING:")
            assert word in decoded
