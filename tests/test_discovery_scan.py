"""Tests for the discovery-scan engine's session handling."""

from __future__ import annotations

import pytest

from canlib.modes.discovery_scan import DiscoveryProbe, scan_ecu


class _FakeTerminal:
    def __init__(self, responses=None):
        self.headers: list[int] = []
        self.sessions: list[tuple[bool, str]] = []
        self.reqs: list[str] = []
        self._responses = responses or {}

    async def set_header(self, tx_id):
        self.headers.append(tx_id)

    async def enter_extended_session(self, wake=False, mode="03"):
        self.sessions.append((wake, mode))
        return True, None

    async def send_uds(self, req, **kw):
        self.reqs.append(req)
        # Return a canned response keyed by request, else "absent" (NRC 0x31).
        return self._responses.get(req, {"ok": False, "nrc": 0x31})


def _classify(resp):
    if resp.get("ok"):
        return "positive", None
    nrc = resp.get("nrc")
    if nrc == 0x7F:
        return "wrong-session", nrc
    if nrc == 0x31:
        return "absent", nrc
    return "error", None


def _probe(service=0x30):
    return DiscoveryProbe(
        name="Test",
        scan_type="test",
        id_label="LID",
        id_width=1,
        service=service,
        probe=lambda t, i: t.send_uds(f"{service:02X}{i:02X}00"),
        classify=_classify,
        make_hit=lambda *a: a,
        request_display=lambda i: f"{service:02X} {i:02X} 00",
        write_hit=None,
    )


@pytest.mark.asyncio
async def test_session_opened_upfront_with_mode():
    term = _FakeTerminal()
    await scan_ecu(
        term,
        _probe(),
        "BMS",
        0x7E4,
        (0x00, 0x02),
        throttle_ms=0,
        session=True,
        session_mode="81",
    )
    # Session opened exactly once, up front, with the requested KWP mode.
    assert term.sessions == [(False, "81")]


@pytest.mark.asyncio
async def test_no_session_when_not_requested():
    term = _FakeTerminal()
    await scan_ecu(term, _probe(), "BMS", 0x7E4, (0x00, 0x02), throttle_ms=0)
    # No pre-opened session and no lazy escalation (responses were plain "absent").
    assert term.sessions == []


@pytest.mark.asyncio
async def test_lazy_escalation_uses_session_mode():
    # First probe returns NRC 0x7F → engine should escalate using session_mode.
    term = _FakeTerminal(responses={"300000": {"ok": False, "nrc": 0x7F}})
    await scan_ecu(
        term,
        _probe(),
        "BMS",
        0x7E4,
        (0x00, 0x01),
        throttle_ms=0,
        session_mode="81",
    )
    assert term.sessions == [(False, "81")]
