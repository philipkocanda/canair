"""Tests for canlib.modes.identity — UDS/KWP2000 identity querying."""

import pytest

from canlib.modes import identity as ident


# --- _decode_identity_payload ---


class TestDecodePayload:
    def test_ascii(self):
        assert ident._decode_identity_payload(b"95400G7470", "ascii") == "95400G7470"

    def test_ascii_strips_trailing_padding(self):
        assert ident._decode_identity_payload(b"ABC\x00\x00\xff", "ascii") == "ABC"

    def test_empty(self):
        assert ident._decode_identity_payload(b"\x00\x00", "ascii") == "(empty)"

    def test_date_8_hex(self):
        assert ident._decode_identity_payload(bytes.fromhex("20170531"), "date") == "2017-05-31"

    def test_date_6_hex(self):
        assert ident._decode_identity_payload(bytes.fromhex("170606"), "date") == "2017-06-06"

    def test_hex_fallback_for_binary(self):
        # Mostly non-printable -> hex.
        assert ident._decode_identity_payload(b"\x01\x02\x03\x04", "auto") == "01020304"

    def test_auto_ascii(self):
        assert ident._decode_identity_payload(b"AEEV__ BMS", "auto") == "AEEV__ BMS"


# --- _service_supported ---


class TestServiceSupported:
    def test_positive(self):
        assert ident._service_supported({"ok": True}) is True

    def test_service_not_supported(self):
        assert ident._service_supported({"ok": False, "nrc": 0x11}) is False

    def test_other_nrc_means_supported(self):
        # requestOutOfRange -> service exists, this DID doesn't.
        assert ident._service_supported({"ok": False, "nrc": 0x31}) is True

    def test_no_data_no_signal(self):
        assert ident._service_supported({"ok": False, "error": "NO DATA"}) is None


# --- _resolve_protocol_hint ---


class TestResolveProtocolHint:
    def test_explicit_uds(self):
        assert ident._resolve_protocol_hint(0x7E4, "uds") == "uds"

    def test_explicit_kwp(self):
        assert ident._resolve_protocol_hint(0x770, "kwp") == "kwp"

    def test_registry_uds(self, monkeypatch):
        monkeypatch.setattr(ident, "ecu_id_protocol", lambda tx: "UDS")
        assert ident._resolve_protocol_hint(0x7A0, "auto") == "uds"

    def test_registry_kwp(self, monkeypatch):
        monkeypatch.setattr(ident, "ecu_id_protocol", lambda tx: "KWP2000")
        assert ident._resolve_protocol_hint(0x7E4, "auto") == "kwp"

    def test_registry_none_triggers_probe(self, monkeypatch):
        monkeypatch.setattr(ident, "ecu_id_protocol", lambda tx: "none")
        assert ident._resolve_protocol_hint(0x783, "auto") is None

    def test_registry_missing_triggers_probe(self, monkeypatch):
        monkeypatch.setattr(ident, "ecu_id_protocol", lambda tx: None)
        assert ident._resolve_protocol_hint(0x999, "auto") is None


# --- fake terminal + probe/mode integration ---


class FakeTerminal:
    """Minimal terminal returning canned send_uds responses by request string."""

    def __init__(self, responses):
        self._responses = responses
        self.sent = []

    async def set_header(self, tx_id):
        pass

    async def send_uds(self, cmd, timeout=None):
        self.sent.append(cmd)
        resp = self._responses.get(cmd, {"ok": False, "error": "NO DATA", "raw": "NO DATA"})
        return dict(resp)


def _ok(hex_str):
    b = bytes.fromhex(hex_str)
    return {"ok": True, "bytes": b, "hex": hex_str.upper(), "raw": hex_str}


class TestProbeProtocol:
    @pytest.mark.asyncio
    async def test_uds_detected(self):
        t = FakeTerminal({"22F190": _ok("62F190" + "414243")})
        proto, _ = await ident._probe_protocol(t)
        assert proto == "uds"

    @pytest.mark.asyncio
    async def test_kwp_detected_when_uds_not_supported(self):
        t = FakeTerminal(
            {
                "22F190": {"ok": False, "nrc": 0x11},
                "1A90": _ok("5A90" + "4145455620424D53"),
            }
        )
        proto, reason = await ident._probe_protocol(t)
        assert proto == "kwp"
        assert "1A" in reason

    @pytest.mark.asyncio
    async def test_no_response_reports_asleep(self):
        t = FakeTerminal({})  # everything NO DATA
        proto, reason = await ident._probe_protocol(t)
        assert proto is None
        assert "asleep" in reason


class TestModeIdentity:
    @pytest.mark.asyncio
    async def test_kwp_ecu_json(self, monkeypatch, capsys):
        # UDS not supported; KWP records respond.
        monkeypatch.setattr(ident, "ecu_id_protocol", lambda tx: "KWP2000")
        t = FakeTerminal(
            {
                "1A90": _ok("5A90" + "4145455620424D53"),  # "AEEV BMS"
                "1A91": _ok("5A91" + "5645522E322E33"),  # "VER.2.3"
            }
        )
        await ident.mode_identity(t, 0x7E4, session=False, wake=False, as_json=True)
        out = capsys.readouterr().out
        import json

        data = json.loads(out)
        assert data["protocol"] == "kwp"
        labels = {r["did"]: r["decoded"] for r in data["results"]}
        assert labels["90"] == "AEEV BMS"
        assert labels["91"] == "VER.2.3"
        # Must not have probed UDS (registry hint was decisive).
        assert not any(c.startswith("22") for c in t.sent)

    @pytest.mark.asyncio
    async def test_no_data_reports_clearly(self, monkeypatch, capsys):
        monkeypatch.setattr(ident, "ecu_id_protocol", lambda tx: None)
        t = FakeTerminal({})
        await ident.mode_identity(t, 0x7E2, session=False, wake=False, as_json=False)
        out = capsys.readouterr().out
        assert "No identity data" in out
        assert "asleep" in out
