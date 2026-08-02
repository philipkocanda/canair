"""Tests for canlib.modes.identity — UDS/KWP2000 probe + mode orchestration."""

import json

import pytest

from canlib.modes import identity as ident
from canlib.modes import identity_decode as idec
from tests._fakes import FakeTerminal
from tests._fakes import ok as _ok


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


class TestHkDidGating:
    @pytest.mark.asyncio
    async def test_f187_skipped_without_quirk(self, monkeypatch):
        from canlib.modes import identity_records as recs

        monkeypatch.setattr(idec, "ecu_id_protocol", lambda tx: "UDS")
        t = FakeTerminal({"22F190": _ok("62F190" + "0102030405")})
        await ident.mode_identity(
            t, 0x770, session=False, wake=False, as_json=True, quirks=frozenset()
        )
        # Make-neutral profile must never probe the HK-only F187 DID.
        assert not any(c.upper() == "22F187" for c in t.sent)
        assert "F187" in recs.QUIRK_GATED_DIDS

    @pytest.mark.asyncio
    async def test_f187_probed_with_hk_quirk(self, monkeypatch):
        from canlib.quirks import HK_F1XX_MINUS_ONE

        monkeypatch.setattr(idec, "ecu_id_protocol", lambda tx: "UDS")
        t = FakeTerminal({"22F190": _ok("62F190" + "0102030405")})
        await ident.mode_identity(
            t,
            0x770,
            session=False,
            wake=False,
            as_json=True,
            quirks=frozenset({HK_F1XX_MINUS_ONE}),
        )
        assert any(c.upper() == "22F187" for c in t.sent)

    def test_no_hk_label_in_tables(self):
        for _did, label, _fmt in ident.UDS_IDENTITY_DIDS:
            assert "(HK)" not in label
