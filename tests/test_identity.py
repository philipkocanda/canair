"""Tests for canlib.modes.identity — UDS/KWP2000 probe + mode orchestration."""

import json

import pytest

from canlib.modes import identity as ident
from canlib.modes import identity_decode as idec


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
        # UDS not supported; KWP records respond. resolve_protocol_hint reads
        # the registry via identity_decode.ecu_id_protocol.
        monkeypatch.setattr(idec, "ecu_id_protocol", lambda tx: "KWP2000")
        t = FakeTerminal(
            {
                "1A90": _ok("5A90" + "4145455620424D53"),  # "AEEV BMS"
                "1A91": _ok("5A91" + "5645522E322E33"),  # "VER.2.3"
            }
        )
        await ident.mode_identity(t, 0x7E4, session=False, wake=False, as_json=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["protocol"] == "kwp"
        labels = {r["did"]: r["decoded"] for r in data["results"]}
        assert labels["90"] == "AEEV BMS"
        assert labels["91"] == "VER.2.3"
        # Must not have probed UDS (registry hint was decisive).
        assert not any(c.startswith("22") for c in t.sent)

    @pytest.mark.asyncio
    async def test_no_data_reports_clearly(self, monkeypatch, capsys):
        monkeypatch.setattr(idec, "ecu_id_protocol", lambda tx: None)
        t = FakeTerminal({})
        await ident.mode_identity(t, 0x7E2, session=False, wake=False, as_json=False)
        out = capsys.readouterr().out
        assert "No identity data" in out
        assert "asleep" in out


class TestReExports:
    def test_tables_reexported(self):
        # modes/__init__ imports these names from .identity; keep them available.
        assert ident.IDENTITY_DIDS is ident.UDS_IDENTITY_DIDS
        assert len(ident.UDS_IDENTITY_DIDS) > 0
        assert len(ident.KWP_IDENTITY_RECORDS) > 0
