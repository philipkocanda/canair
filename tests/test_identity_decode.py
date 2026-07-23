"""Tests for canlib.modes.identity_decode — pure decode + protocol selection."""

from canlib.modes import identity_decode as idec

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

    def test_other_nrc_means_supported(self):
        # requestOutOfRange -> service exists, this DID doesn't.
        assert idec.service_supported({"ok": False, "nrc": 0x31}) is True

    def test_no_data_no_signal(self):
        assert idec.service_supported({"ok": False, "error": "NO DATA"}) is None


# --- resolve_protocol_hint ---


class TestResolveProtocolHint:
    def test_explicit_uds(self):
        assert idec.resolve_protocol_hint(0x7E4, "uds") == "uds"

    def test_explicit_kwp(self):
        assert idec.resolve_protocol_hint(0x770, "kwp") == "kwp"

    def test_registry_uds(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_id_protocol", lambda tx: "UDS")
        assert idec.resolve_protocol_hint(0x7A0, "auto") == "uds"

    def test_registry_kwp(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_id_protocol", lambda tx: "KWP2000")
        assert idec.resolve_protocol_hint(0x7E4, "auto") == "kwp"

    def test_registry_none_triggers_probe(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_id_protocol", lambda tx: "none")
        assert idec.resolve_protocol_hint(0x783, "auto") is None

    def test_registry_missing_triggers_probe(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_id_protocol", lambda tx: None)
        assert idec.resolve_protocol_hint(0x999, "auto") is None
