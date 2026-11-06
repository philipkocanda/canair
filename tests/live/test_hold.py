"""``live/hold.py``: a single-ECU command's session, and the wake ritual its ``--wake`` uses."""

from __future__ import annotations

import pytest

from canair.cli.profile_context import active
from canair.live.hold import held_session
from tests.support.fakes import FakeTerminal

PIDS = {
    "ecus": {
        "SKM": {
            "tx_id": 0x7A5,
            "wake": {"method": "rapid_read", "prime_pid": "22B003", "attempts": 3},
        },
        "BMS": {"tx_id": 0x7E4},
    }
}


@pytest.mark.asyncio
async def test_wake_uses_the_ecus_own_ritual():
    t = FakeTerminal(send_command_reply="62 B0 03 00")
    async with held_session(t, 0x7A5, profile=active(), wake=True, pids_data=PIDS) as outcome:
        assert outcome is not None and outcome.ok
    assert ("send_command", "22B003") in t.calls
    assert "1001" not in t.sent


@pytest.mark.asyncio
async def test_wake_without_a_ritual_sends_10_01():
    t = FakeTerminal()
    async with held_session(t, 0x7E4, profile=active(), wake=True, pids_data=PIDS):
        pass
    assert t.sent == ["1001", "1003"]


@pytest.mark.asyncio
async def test_disabled_sends_nothing():
    t = FakeTerminal()
    async with held_session(t, 0x7E4, profile=active(), enabled=False) as outcome:
        assert outcome is None
    assert t.calls == []
