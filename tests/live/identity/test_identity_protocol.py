"""The identity probe proves an ECU's protocol only when one service answers and the other
refuses."""

from __future__ import annotations

import json

import pytest

from canair.cli.profile_context import active
from canair.live.identity import decode as idec
from canair.live.identity import read as ident
from canair.live.identity.decode import decide_protocol
from tests.support.fakes import FakeTerminal, nrc, ok

NOT_SUPPORTED = nrc(0x11)
OUT_OF_RANGE = nrc(0x31)


@pytest.mark.parametrize(
    ("uds", "kwp", "expected"),
    [
        ("positive", "refused", "uds"),
        ("supported", "refused", "uds"),
        ("refused", "positive", "kwp"),
        ("refused", "supported", "kwp"),
        # A KWP2000 ECU with ReadDataByCommonIdentifier answers 22 F190 with an NRC.
        ("supported", "positive", "kwp"),
        # 0x1A exists only in KWP2000.
        ("positive", "positive", "kwp"),
        ("supported", "supported", "uds"),
        ("refused", "refused", None),
        ("silent", "silent", None),
        ("refused", "silent", None),
        ("session", "refused", None),
        ("refused", "session", None),
    ],
)
def test_the_decision(uds, kwp, expected):
    assert decide_protocol(uds, kwp)[0] == expected


@pytest.mark.asyncio
async def test_both_services_are_probed_even_when_uds_answers():
    term = FakeTerminal({"22F190": ok("62F190414243"), "1A90": NOT_SUPPORTED})
    probe = await ident._probe_protocol(term)
    assert (probe.protocol, probe.uds, probe.kwp) == ("uds", "positive", "refused")
    assert term.sent[:2] == ["22F190", "1A90"]


@pytest.mark.asyncio
async def test_an_nrc_to_22_does_not_outweigh_a_kwp2000_record():
    term = FakeTerminal({"22F190": OUT_OF_RANGE, "1A90": ok("5A904142")})
    assert (await ident._probe_protocol(term)).protocol == "kwp"


def _unknown(monkeypatch):
    monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "unknown")


async def _identity(term, **kw):
    return await ident.mode_identity(
        term, 0x7A1, session=False, wake=False, as_json=True, profile=active(), **kw
    )


@pytest.mark.asyncio
async def test_a_reading_proves_uds_when_1a_is_refused(monkeypatch, capsys):
    _unknown(monkeypatch)
    term = FakeTerminal({"22F190": ok("62F190414243"), "1A90": NOT_SUPPORTED})
    finding = await _identity(term)
    assert finding is not None and finding.protocol == "uds" and finding.tx_id == 0x7A1
    assert "KWP2000 1A refused" in finding.evidence
    assert '"probe"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_reading_proves_kwp2000_when_22_is_refused(monkeypatch):
    _unknown(monkeypatch)
    term = FakeTerminal({"22F190": NOT_SUPPORTED, "1A90": ok("5A904142")})
    finding = await _identity(term)
    assert finding is not None and finding.protocol == "kwp"


@pytest.mark.asyncio
async def test_no_finding_without_a_refusal_of_the_other_service(monkeypatch):
    _unknown(monkeypatch)
    # 1A never answers: an asleep ECU, or a UDS ECU that ignores it. Not proof.
    assert await _identity(FakeTerminal({"22F190": ok("62F190414243")})) is None


@pytest.mark.asyncio
async def test_no_finding_when_the_profile_names_the_protocol(monkeypatch):
    monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "uds")
    term = FakeTerminal({"22F190": ok("62F190414243"), "1A90": NOT_SUPPORTED})
    assert await _identity(term) is None
    assert "1A90" not in term.sent


@pytest.mark.asyncio
async def test_no_finding_for_an_explicit_protocol(monkeypatch):
    _unknown(monkeypatch)
    term = FakeTerminal({"22F190": ok("62F190414243")})
    assert await _identity(term, protocol="uds") is None


@pytest.mark.asyncio
async def test_a_refusal_for_this_session_only_proves_nothing(monkeypatch):
    """A KWP2000 ECU that answers 22 F1xx and wants a session for 1A must not read as UDS."""
    _unknown(monkeypatch)
    term = FakeTerminal({"22F190": ok("62F190414243"), "1A90": nrc(0x80)})
    assert await _identity(term) is None


@pytest.mark.parametrize(
    ("code", "state"),
    [
        (0x11, "refused"),
        (0x7F, "session"),
        (0x80, "session"),
        (0x7E, "session"),
        (0x31, "supported"),
    ],
)
def test_probe_states(code, state):
    assert idec.probe_state(nrc(code)) == state


@pytest.mark.parametrize(
    ("uds", "kwp", "uds_nrc", "kwp_nrc", "expected"),
    [
        ("session", "refused", 0x7F, 0x11, "uds"),
        ("session", "silent", 0x7E, None, "uds"),
        ("refused", "session", 0x11, 0x80, "kwp"),
        ("silent", "session", None, 0x80, "kwp"),
        # 0x80 on 22 is a KWP2000 ECU with common identifiers; 1A answers.
        ("session", "positive", 0x80, None, "kwp"),
        # One code per protocol, each from the other protocol's spelling: no decision.
        ("session", "session", 0x7F, 0x80, None),
        ("session", "refused", 0x80, 0x11, None),
        ("refused", "session", 0x11, 0x7F, None),
    ],
)
def test_a_session_refusal_names_the_protocol_that_has_the_service(
    uds, kwp, uds_nrc, kwp_nrc, expected
):
    assert decide_protocol(uds, kwp, uds_nrc, kwp_nrc)[0] == expected


@pytest.mark.asyncio
async def test_22_refused_in_session_and_1a_refused_is_uds_without_a_finding(monkeypatch):
    _unknown(monkeypatch)
    term = FakeTerminal({"22F190": nrc(0x7F), "1A90": NOT_SUPPORTED}, default=nrc(0x7F))
    probe = await ident._probe_protocol(term)
    assert probe.protocol == "uds"
    assert "refused in this session (0x7F)" in probe.reason
    assert (probe.uds_nrc, probe.kwp_nrc) == (0x7F, 0x11)
    assert await _identity(term) is None


@pytest.mark.asyncio
async def test_1a_refused_in_session_and_22_refused_is_kwp_without_a_finding(monkeypatch):
    _unknown(monkeypatch)
    term = FakeTerminal({"22F190": NOT_SUPPORTED, "1A90": nrc(0x80)}, default=nrc(0x80))
    probe = await ident._probe_protocol(term)
    assert probe.protocol == "kwp"
    assert "refused in this session (0x80)" in probe.reason
    assert await _identity(term) is None


@pytest.mark.parametrize(
    ("uds", "kwp", "uds_nrc", "kwp_nrc", "named"),
    [
        ("refused", "silent", 0x11, None, "22F190 refused 0x11, 1A90 silent"),
        ("session", "refused", 0x80, 0x11, "22F190 session 0x80, 1A90 refused 0x11"),
    ],
)
def test_an_undecided_reason_names_both_replies(uds, kwp, uds_nrc, kwp_nrc, named):
    protocol, reason = decide_protocol(uds, kwp, uds_nrc, kwp_nrc)
    assert protocol is None
    assert named in reason
    assert "asleep" not in reason


@pytest.mark.asyncio
async def test_a_kwp2000_ecu_with_common_identifiers_is_still_kwp():
    term = FakeTerminal({"22F190": nrc(0x80), "1A90": ok("5A904142")})
    assert (await ident._probe_protocol(term)).protocol == "kwp"


@pytest.mark.asyncio
async def test_the_json_probe_carries_the_nrc_codes(monkeypatch, capsys):
    _unknown(monkeypatch)
    term = FakeTerminal({"22F190": nrc(0x7F), "1A90": NOT_SUPPORTED}, default=nrc(0x7F))
    await _identity(term)
    probe = json.loads(capsys.readouterr().out)["probe"]
    assert probe == {"uds": "session", "uds_nrc": "0x7F", "kwp": "refused", "kwp_nrc": "0x11"}
