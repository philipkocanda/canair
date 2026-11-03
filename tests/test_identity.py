"""Tests for canlib.modes.identity — UDS/KWP2000 probe + mode orchestration."""

import json

import pytest

from canlib.modes import identity as ident
from canlib.modes import identity_decode as idec
from canlib.uds.parse import parse_uds_response
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
        # the registry via identity_decode.ecu_protocol.
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx: "kwp")
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
    @pytest.mark.parametrize("protocol", ["kwp", "uds"])
    async def test_every_identity_read_is_echo_validated(self, monkeypatch, protocol):
        # Unvalidated, a late reply to the previous record would be printed under
        # this record's label.
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx: protocol)
        t = FakeTerminal({})
        await ident.mode_identity(t, 0x7E4, session=False, wake=False, as_json=True)
        assert t.sent
        for request, kwargs in zip(t.sent, t.uds_kwargs, strict=True):
            assert kwargs["expected_sid"] == int(request[:2], 16), request
            assert kwargs["expected_echo"] == bytes.fromhex(request[2:]), request

    @pytest.mark.asyncio
    async def test_no_data_reports_clearly(self, monkeypatch, capsys):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx: "unknown")
        t = FakeTerminal({})
        await ident.mode_identity(t, 0x7E2, session=False, wake=False, as_json=False)
        out = capsys.readouterr().out
        assert "No identity data" in out
        assert "asleep" in out


class TestHkOffsetWithoutQuirk:
    """A Hyundai/Kia ECU on a profile without the hk_f1xx_minus_one quirk."""

    @staticmethod
    def _offset_reply(did: str, data: str) -> dict:
        lower = f"{int(did, 16) - 1:04X}"
        return parse_uds_response(
            f"62{lower}{data}", expected_sid=0x22, expected_echo=bytes.fromhex(did)
        )

    def _terminal(self) -> FakeTerminal:
        return FakeTerminal(
            {
                "22F190": self._offset_reply("F190", "4B4D48"),
                "22F188": self._offset_reply("F188", "3931323334"),
            }
        )

    @pytest.mark.asyncio
    async def test_the_offset_is_reported_with_the_quirk_hint(self, monkeypatch, capsys):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx: "uds")
        await ident.mode_identity(
            self._terminal(), 0x7A0, session=False, wake=False, as_json=False, quirks=frozenset()
        )
        out = capsys.readouterr().out
        assert "answered as F187" in out
        assert "hk_f1xx_minus_one" in out
        assert "asleep" not in out
        # Still refused: the reply is never printed as the requested DID's value.
        assert "91234" not in out

    @pytest.mark.asyncio
    async def test_json_lists_the_offset_answers(self, monkeypatch, capsys):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx: "uds")
        await ident.mode_identity(
            self._terminal(), 0x7A0, session=False, wake=False, as_json=True, quirks=frozenset()
        )
        data = json.loads(capsys.readouterr().out)
        assert data["results"] == []
        assert data["summary"]["offset"] == 2
        assert {a["did"]: a["answered_as"] for a in data["offset_answers"]} == {
            "F190": "F18F",
            "F188": "F187",
        }

    @pytest.mark.asyncio
    async def test_the_probe_takes_an_offset_answer_as_uds(self):
        proto, _ = await ident._probe_protocol(self._terminal())
        assert proto == "uds"

    def test_another_identifier_is_not_taken_for_the_offset(self):
        r = parse_uds_response("62F18A41", expected_sid=0x22, expected_echo=b"\xf1\x88")
        assert ident._offset_answer("22F188", r) is None
        assert r["error_kind"] == "stale"


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

        monkeypatch.setattr(idec, "ecu_protocol", lambda tx: "uds")
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

        monkeypatch.setattr(idec, "ecu_protocol", lambda tx: "uds")
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

    def test_c0de_is_gated_like_f187(self):
        from canlib.modes import identity_records as recs
        from canlib.quirks import HK_F1XX_MINUS_ONE

        def dids(quirks):
            return {d for d, _, _ in recs.records_for("uds", quirks)}

        assert "C0DE" not in dids(frozenset())
        assert "C0DE" in dids(frozenset({HK_F1XX_MINUS_ONE}))
        assert "F186" in dids(frozenset())  # ISO standard, never gated
