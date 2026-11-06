"""Tests for canair.live.identity.read — UDS/KWP2000 probe + mode orchestration."""

import json

import pytest

from canair.cli.profile_context import active
from canair.live.identity import decode as idec
from canair.live.identity import read as ident
from canair.live.identity.records import records_for
from canair.uds.parse import parse_uds_response
from tests.support.fakes import FakeTerminal, nrc
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
                "1A80": _ok("5A80" + "41"),
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
        return FakeTerminal(
            {"22F190": _ok("62F190414243"), "22F100": _ok("62F100313730"), "22F101": repeat}
        )

    @pytest.mark.asyncio
    async def test_it_is_not_printed_as_the_requested_field(self, monkeypatch, capsys):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "uds")
        await ident.mode_identity(
            self._terminal(), 0x7A1, session=False, wake=False, as_json=True, profile=active()
        )
        data = json.loads(capsys.readouterr().out)
        assert {r["did"]: r["decoded"] for r in data["results"]} == {"F190": "ABC", "F100": "170"}
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
        for record in ident.UDS_IDENTITY_DIDS:
            assert "(HK)" not in record.label


# The UDS records read on the active (Hyundai) profile.
UDS_READS = len(records_for("uds", "hyundai"))


async def _identity(term, *, as_json=True, session=False, protocol="uds", monkeypatch=None):
    monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: protocol)
    await ident.mode_identity(
        term, 0x7A1, session=session, wake=False, as_json=as_json, profile=active()
    )


class TestSilentEcu:
    """A known protocol runs no probe, so a silent ECU is found out by the first reads."""

    @pytest.mark.asyncio
    async def test_it_stops_after_three_silent_reads(self, monkeypatch, capsys):
        term = FakeTerminal({})
        await _identity(term, monkeypatch=monkeypatch)
        captured = capsys.readouterr()
        assert len(term.sent) == ident.SILENT_READS_BEFORE_STOP == 3
        summary = json.loads(captured.out)["summary"]
        assert summary["no_data"] == 3
        assert summary["not_asked"] == UDS_READS - 3
        assert "hint: try --session/--wake" in captured.err
        assert "hint" not in captured.out

    @pytest.mark.asyncio
    async def test_the_text_summary_counts_the_records_not_asked(self, monkeypatch, capsys):
        await _identity(FakeTerminal({}), as_json=False, monkeypatch=monkeypatch)
        out = capsys.readouterr().out
        assert "No response — ECU may be asleep or unpowered." in out
        assert f"3 no-response, {UDS_READS - 3} not asked (of {UDS_READS})." in out

    @pytest.mark.asyncio
    async def test_it_never_stops_once_a_record_answered(self, monkeypatch):
        term = FakeTerminal({"22F190": _ok("62F190414243")})
        await _identity(term, monkeypatch=monkeypatch)
        assert len(term.sent) == UDS_READS

    @pytest.mark.asyncio
    async def test_it_never_stops_once_a_record_drew_an_nrc(self, monkeypatch):
        # Two silent reads, then an NRC on the third: the ECU is awake.
        third = ident.UDS_IDENTITY_DIDS[2].id
        term = FakeTerminal({"22" + third: nrc(0x31)})
        await _identity(term, monkeypatch=monkeypatch)
        assert len(term.sent) == UDS_READS

    @pytest.mark.asyncio
    async def test_a_stale_reply_does_not_count_as_silence(self, monkeypatch):
        stale = {"ok": False, "error": "echo mismatch", "error_kind": "stale"}
        term = FakeTerminal({}, default=stale)
        await _identity(term, monkeypatch=monkeypatch)
        assert len(term.sent) == UDS_READS


class TestRefusalHints:
    @pytest.mark.asyncio
    async def test_session_refusals_point_at_session(self, monkeypatch, capsys):
        term = FakeTerminal({}, default=nrc(0x7F))
        await _identity(term, monkeypatch=monkeypatch)
        captured = capsys.readouterr()
        assert "hint: these records need a diagnostic session: try --session" in captured.err
        assert "hint" not in captured.out

    @pytest.mark.asyncio
    async def test_a_session_refusal_in_the_probe_skips_the_reads(self, monkeypatch, capsys):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "unknown")
        term = FakeTerminal({"1A90": nrc(0x11)}, default=nrc(0x7F))
        await ident.mode_identity(
            term, 0x7A1, session=False, wake=False, as_json=True, profile=active()
        )
        captured = capsys.readouterr()
        assert term.sent == ["22F190", "1A90"]
        assert captured.err.count("hint:") == 1
        summary = json.loads(captured.out)["summary"]
        assert summary["not_asked"] == UDS_READS
        assert "try --session" in captured.err

    @pytest.mark.asyncio
    async def test_with_session_the_reads_run_and_the_hint_names_the_session(
        self, monkeypatch, capsys
    ):
        term = FakeTerminal({}, default=nrc(0x7F))
        await _identity(term, session=True, monkeypatch=monkeypatch)
        err = capsys.readouterr().err
        assert "refused these records in the session canair opened (10 03)" in err
        assert "try --session" not in err
        assert sum(request.startswith("22") for request in term.sent) == UDS_READS

    @pytest.mark.asyncio
    async def test_kwp2000_notes_the_session_it_cannot_open(self, monkeypatch, capsys):
        term = FakeTerminal({}, default=nrc(0x80))
        await _identity(term, session=True, protocol="kwp", monkeypatch=monkeypatch)
        assert "10 81" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_security_access_says_nothing_about_keys(self, monkeypatch, capsys):
        term = FakeTerminal({}, default=nrc(0x33))
        await _identity(term, monkeypatch=monkeypatch)
        err = capsys.readouterr().err
        assert "need security access, which canair does not unlock" in err
        assert "key" not in err

    @pytest.mark.asyncio
    async def test_out_of_range_says_the_ecu_has_none_of_the_records(self, monkeypatch, capsys):
        term = FakeTerminal({}, default=nrc(0x31))
        await _identity(term, monkeypatch=monkeypatch)
        assert "answers none of the standard identity records" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_some_fields_and_some_session_refusals_suggest_session(self, monkeypatch, capsys):
        answers = {"22" + did: _ok("62" + did + "414243") for did in ("F190", "F188", "F187")}
        term = FakeTerminal(answers, default=nrc(0x7F))
        await _identity(term, monkeypatch=monkeypatch)
        assert "hint: --session may return more fields" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_the_json_counts_the_nrc_codes_and_stdout_stays_one_document(
        self, monkeypatch, capsys
    ):
        refused = {"22" + r.id: nrc(0x31) for r in ident.UDS_IDENTITY_DIDS[:2]}
        term = FakeTerminal(refused, default=nrc(0x7F))
        await _identity(term, monkeypatch=monkeypatch)
        summary = json.loads(capsys.readouterr().out)["summary"]
        assert summary["nrc_codes"] == {"0x31": 2, "0x7F": UDS_READS - 2}
        assert summary["nrc"] == UDS_READS
        assert summary["not_asked"] == 0
