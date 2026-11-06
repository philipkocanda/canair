"""Tests for canair.live.identity.read — UDS/KWP2000 probe + mode orchestration."""

import json

import pytest

from canair.cli.profile_context import active
from canair.live.identity import decode as idec
from canair.live.identity import read as ident
from canair.uds.parse import parse_uds_response
from tests.support.fakes import FakeTerminal
from tests.support.fakes import ok as _ok


class TestProbeProtocol:
    @pytest.mark.asyncio
    async def test_uds_detected(self):
        t = FakeTerminal({"22F190": _ok("62F190" + "414243")})
        probe = await ident._probe_protocol(t)
        assert probe.protocol == "uds"
        assert probe.uds == "positive"

    @pytest.mark.asyncio
    async def test_kwp_detected_when_uds_not_supported(self):
        t = FakeTerminal(
            {
                "22F190": {"ok": False, "nrc": 0x11},
                "1A90": _ok("5A90" + "4145455620424D53"),
            }
        )
        probe = await ident._probe_protocol(t)
        assert probe.protocol == "kwp"
        assert "1A" in probe.reason

    @pytest.mark.asyncio
    async def test_no_response_reports_asleep(self):
        t = FakeTerminal({})  # everything NO DATA
        probe = await ident._probe_protocol(t)
        assert probe.protocol is None
        assert "asleep" in probe.reason


class TestModeIdentity:
    @pytest.mark.asyncio
    async def test_kwp_ecu_json(self, monkeypatch, capsys):
        # UDS not supported; KWP records respond. resolve_protocol_hint reads
        # the registry via identity.decode.ecu_protocol.
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "kwp")
        t = FakeTerminal(
            {
                "1A90": _ok("5A90" + "4145455620424D53"),  # "AEEV BMS"
                "1A91": _ok("5A91" + "5645522E322E33"),  # "VER.2.3"
            }
        )
        await ident.mode_identity(
            t, 0x7E4, session=False, wake=False, as_json=True, profile=active()
        )
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
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: protocol)
        t = FakeTerminal({})
        await ident.mode_identity(
            t, 0x7E4, session=False, wake=False, as_json=True, profile=active()
        )
        assert t.sent
        for request, kwargs in zip(t.sent, t.uds_kwargs, strict=True):
            assert kwargs["expected_sid"] == int(request[:2], 16), request
            assert kwargs["expected_echo"] == bytes.fromhex(request[2:]), request

    @pytest.mark.asyncio
    async def test_no_data_reports_clearly(self, monkeypatch, capsys):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "unknown")
        t = FakeTerminal({})
        await ident.mode_identity(
            t, 0x7E2, session=False, wake=False, as_json=False, profile=active()
        )
        out = capsys.readouterr().out
        assert "No identity data" in out
        assert "asleep" in out


class TestRepeatedPreviousReply:
    """DDM, ADM and PSM answer a DID they do not support by repeating their previous reply.

    The repeat often names the DID just below the one asked for, which is how it once
    passed for a Hyundai/Kia "one lower" answer. It is another DID's reply: never printed
    as the requested field, and no hint.
    """

    def _terminal(self) -> FakeTerminal:
        repeat = parse_uds_response("62F100313730", expected_sid=0x22, expected_echo=b"\xf1\x01")
        return FakeTerminal({"22F100": _ok("62F100313730"), "22F101": repeat})

    @pytest.mark.asyncio
    async def test_it_is_not_printed_as_the_requested_field(self, monkeypatch, capsys):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "uds")
        await ident.mode_identity(
            self._terminal(), 0x7A1, session=False, wake=False, as_json=True, profile=active()
        )
        data = json.loads(capsys.readouterr().out)
        assert {r["did"]: r["decoded"] for r in data["results"]} == {"F100": "170"}
        assert "offset_answers" not in data

    @pytest.mark.asyncio
    async def test_the_probe_does_not_take_another_dids_reply_as_uds(self):
        repeat = parse_uds_response("62F18F41", expected_sid=0x22, expected_echo=b"\xf1\x90")
        probe = await ident._probe_protocol(FakeTerminal({"22F190": repeat}))
        assert probe.protocol is None


class TestReExports:
    def test_tables_reexported(self):
        # modes/__init__ imports these names from .identity; keep them available.
        assert ident.IDENTITY_DIDS is ident.UDS_IDENTITY_DIDS
        assert len(ident.UDS_IDENTITY_DIDS) > 0
        assert len(ident.KWP_IDENTITY_RECORDS) > 0


class TestProbedDids:
    @pytest.mark.asyncio
    async def test_f187_is_probed_on_every_profile(self, monkeypatch):
        # ISO 14229's vehicleManufacturerSparePartNumber, answered directly on the Ioniq.
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "uds")
        t = FakeTerminal({"22F190": _ok("62F190" + "0102030405")})
        await ident.mode_identity(
            t, 0x770, session=False, wake=False, as_json=True, profile=active()
        )
        assert "22F187" in t.sent
        assert "22C0DE" not in t.sent

    def test_no_hk_label_in_tables(self):
        for _did, label, _fmt in ident.UDS_IDENTITY_DIDS:
            assert "(HK)" not in label
