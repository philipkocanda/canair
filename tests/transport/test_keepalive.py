"""A TesterPresent keepalive stops once the ECU answers NRC 0x11 (canair.transport.keepalive).

The Ioniq's BMS has no TesterPresent in any session, so a keepalive to it only costs bus time.
NRC 0x7F/0x80/0x22 say "not in this session" or "not now" and do not stop it.
"""

from __future__ import annotations

import pytest

from canair.transport import keepalive
from canair.transport.session_manager import SessionManager
from canair.uds.nrc import NRC_SERVICE_NOT_SUPPORTED
from tests.support.fakes import FakeTerminal, nrc, ok

BMS = 0x7E4


def test_only_service_not_supported_counts():
    assert keepalive.unsupported(nrc(NRC_SERVICE_NOT_SUPPORTED))
    for code in (0x7F, 0x80, 0x22, 0x78):
        assert not keepalive.unsupported(nrc(code))
    assert not keepalive.unsupported(ok("7E00"))
    assert keepalive.unsupported_bytes(bytes.fromhex("7F3E11"))
    assert not keepalive.unsupported_bytes(bytes.fromhex("7F3E7F"))
    assert not keepalive.unsupported_bytes(bytes.fromhex("7F2211"))  # another service's refusal
    assert not keepalive.unsupported_bytes(None)


class TestSessionManager:
    @staticmethod
    async def _sweeps(reply, ecus=(BMS,), n=3):
        term = FakeTerminal({"3E00": reply})
        sm = SessionManager(term)
        for tx_id in ecus:
            sm.table.track(tx_id, at=0.0)
        for _ in range(n):
            await sm.keepalive_stale(threshold=-1.0)  # everything is always stale
        return term, sm

    @pytest.mark.asyncio
    async def test_an_ecu_without_tester_present_is_asked_once(self):
        term, sm = await self._sweeps(nrc(NRC_SERVICE_NOT_SUPPORTED))
        assert term.sent == ["3E00"]
        assert sm.has_session(BMS)  # its session is still tracked

    @pytest.mark.asyncio
    async def test_keepalive_all_skips_it_too(self):
        term, sm = await self._sweeps(nrc(NRC_SERVICE_NOT_SUPPORTED), n=1)
        await sm.keepalive_all()
        assert term.sent == ["3E00"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [0x7F, 0x80, 0x22])
    async def test_a_not_in_this_session_refusal_keeps_asking(self, code):
        term, _ = await self._sweeps(nrc(code))
        assert term.sent == ["3E00"] * 3

    @pytest.mark.asyncio
    async def test_only_the_refusing_ecu_is_dropped(self):
        term = FakeTerminal({"3E00": ok("7E00")}, key_by_header=True)
        term.responses = {
            (BMS, "3E00"): nrc(NRC_SERVICE_NOT_SUPPORTED),
            (0x770, "3E00"): ok("7E00"),
        }
        sm = SessionManager(term)
        sm.table.track(BMS, at=0.0)
        sm.table.track(0x770, at=0.0)
        for _ in range(3):
            await sm.keepalive_stale(threshold=-1.0)
        assert term.sent.count("3E00") == 1 + 3
        assert [h for h in term.headers if h == BMS] == [BMS]

    @pytest.mark.asyncio
    async def test_it_is_logged_once(self, monkeypatch):
        seen = []
        monkeypatch.setattr(keepalive, "log_event", lambda *a, **k: seen.append((a, k)))
        await self._sweeps(nrc(NRC_SERVICE_NOT_SUPPORTED))
        assert len(seen) == 1 and "0x7E4" in seen[0][0][1]
