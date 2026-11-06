"""Tests for canair.live.identity.decode — pure decode + protocol selection."""

import pytest

from canair.cli.profile_context import active
from canair.live.identity import decode as idec

# --- decode_identity_payload ---


class TestDecodePayload:
    def test_ascii(self):
        assert idec.decode_identity_payload(b"95400G7470", "ascii") == "95400G7470"

    def test_ascii_strips_trailing_padding(self):
        assert idec.decode_identity_payload(b"ABC\x00\x00\xff", "ascii") == "ABC"

    def test_empty(self):
        assert idec.decode_identity_payload(b"\x00\x00", "ascii") == "(empty)"

    def test_date_8_hex(self):
        assert idec.decode_identity_payload(bytes.fromhex("20170531"), "date") == "2017-05-31"

    def test_date_6_hex(self):
        assert idec.decode_identity_payload(bytes.fromhex("170606"), "date") == "2017-06-06"

    def test_date_binary_uint16_year(self):
        # AAF 1A99: 0x07E0 = 2016, month 3, day 2.
        assert idec.decode_identity_payload(bytes.fromhex("07E00302"), "date") == "2016-03-02"

    def test_non_date_falls_back_to_hex(self):
        # VCU/MCU 1A8B version-ish code — not a plausible calendar date.
        assert idec.decode_identity_payload(bytes.fromhex("1E090D14"), "date") == "1E090D14"

    def test_date_zeros_is_empty(self):
        assert idec.decode_identity_payload(bytes.fromhex("00000000"), "date") == "(empty)"

    def test_hex_fallback_for_binary(self):
        # Mostly non-printable -> hex.
        assert idec.decode_identity_payload(b"\x01\x02\x03\x04", "auto") == "01020304"

    def test_auto_ascii(self):
        assert idec.decode_identity_payload(b"AEEV__ BMS", "auto") == "AEEV__ BMS"

    def test_hex_keeps_every_byte(self):
        # Printable bytes and zero padding stay hex: a functional-unit map is not text.
        assert idec.decode_identity_payload(b"\x00AB\x00", "hex") == "00414200"

    @pytest.mark.parametrize(
        ("payload", "decoded"),
        [("03", "extendedDiagnosticSession (03)"), ("01", "defaultSession (01)"), ("60", "60")],
    )
    def test_session(self, payload, decoded):
        assert idec.decode_identity_payload(bytes.fromhex(payload), "session") == decoded

    def test_count_is_the_first_byte(self):
        assert idec.decode_identity_payload(b"\x00", "count") == "0"
        assert idec.decode_identity_payload(b"\x03", "count") == "3"

    def test_modules_names_the_count_then_shows_hex(self):
        assert idec.decode_identity_payload(b"\x02AB", "modules") == "2 modules: 4142"
        assert idec.decode_identity_payload(b"\x01", "modules") == "1 module"

    @pytest.mark.parametrize("fmt", ["hex", "session", "count", "modules"])
    def test_structured_empty(self, fmt):
        assert idec.decode_identity_payload(b"", fmt) == "(empty)"


# --- decode_date ---


class TestDecodeDate:
    def test_bcd_4_byte(self):
        assert idec.decode_date(bytes.fromhex("20170606")) == "2017-06-06"

    def test_bcd_3_byte(self):
        assert idec.decode_date(bytes.fromhex("170606")) == "2017-06-06"

    def test_binary_uint16_year(self):
        assert idec.decode_date(bytes.fromhex("07E00302")) == "2016-03-02"

    def test_implausible_year_is_none(self):
        assert idec.decode_date(bytes.fromhex("1E090D14")) is None

    def test_bad_month_is_none(self):
        # BCD year ok (2017) but month 0x99 -> 99, invalid.
        assert idec.decode_date(bytes.fromhex("20179906")) is None

    def test_wrong_length_is_none(self):
        assert idec.decode_date(bytes.fromhex("2017")) is None


# --- service_supported ---


class TestServiceSupported:
    def test_positive(self):
        assert idec.service_supported({"ok": True}) is True

    def test_service_not_supported(self):
        assert idec.service_supported({"ok": False, "nrc": 0x11}) is False

    @pytest.mark.parametrize("nrc", [0x7F, 0x80])
    def test_not_in_the_active_session_is_not_supported(self, nrc):
        # 0x7F is UDS serviceNotSupportedInActiveSession; KWP2000 spells it 0x80.
        assert idec.service_supported({"ok": False, "nrc": nrc}) is False

    def test_other_nrc_means_supported(self):
        # requestOutOfRange -> service exists, this DID doesn't.
        assert idec.service_supported({"ok": False, "nrc": 0x31}) is True

    def test_no_data_no_signal(self):
        assert idec.service_supported({"ok": False, "error": "NO DATA"}) is None


# --- resolve_protocol_hint ---


class TestResolveProtocolHint:
    def test_explicit_uds(self):
        assert idec.resolve_protocol_hint(0x7E4, "uds", profile=active()) == "uds"

    def test_explicit_kwp(self):
        assert idec.resolve_protocol_hint(0x770, "kwp", profile=active()) == "kwp"

    def test_registry_uds(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "uds")
        assert idec.resolve_protocol_hint(0x7A0, "auto", profile=active()) == "uds"

    def test_registry_kwp(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "kwp")
        assert idec.resolve_protocol_hint(0x7E4, "auto", profile=active()) == "kwp"

    def test_registry_none_triggers_probe(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "unknown")
        assert idec.resolve_protocol_hint(0x783, "auto", profile=active()) is None

    def test_registry_missing_triggers_probe(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "unknown")
        assert idec.resolve_protocol_hint(0x999, "auto", profile=active()) is None
