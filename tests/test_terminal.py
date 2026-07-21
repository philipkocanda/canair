"""Tests for canlib.terminal.WiCANTerminal — header caching + instrumentation.

A fake WebSocket drives the *real* set_header/_send_command_locked logic so the
caching decisions under test are the production ones.
"""

import asyncio

import pytest

from canlib.terminal import WiCANTerminal


class FakeWS:
    """Minimal async WebSocket: every send() gets a canned ELM `OK>` reply."""

    def __init__(self):
        self.sent: list[str] = []
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, data: str):
        self.sent.append(data)
        await self._q.put("OK\r>")

    async def recv(self) -> str:
        return await self._q.get()

    async def close(self):
        pass


def _term() -> WiCANTerminal:
    t = WiCANTerminal(host="test")
    t.ws = FakeWS()
    return t


def _cmds(t: WiCANTerminal) -> list[str]:
    """ELM commands actually put on the wire (CR stripped)."""
    return [s.rstrip("\r") for s in t.ws.sent]


class TestHeaderCaching:
    @pytest.mark.asyncio
    async def test_first_set_header_sends_pair(self):
        t = _term()
        await t.set_header(0x7A0)
        assert _cmds(t) == ["ATSH7A0", "ATFCSH7A0"]

    @pytest.mark.asyncio
    async def test_same_ecu_is_cached(self):
        t = _term()
        await t.set_header(0x7A0)
        t.ws.sent.clear()
        await t.set_header(0x7A0)  # unchanged -> no commands
        assert _cmds(t) == []

    @pytest.mark.asyncio
    async def test_switch_ecu_resends(self):
        t = _term()
        await t.set_header(0x7A0)
        t.ws.sent.clear()
        await t.set_header(0x770)
        assert _cmds(t) == ["ATSH770", "ATFCSH770"]

    @pytest.mark.asyncio
    async def test_switch_back_resends(self):
        t = _term()
        await t.set_header(0x7A0)
        await t.set_header(0x770)
        t.ws.sent.clear()
        await t.set_header(0x7A0)  # cache now holds 0x770 -> must resend
        assert _cmds(t) == ["ATSH7A0", "ATFCSH7A0"]

    @pytest.mark.asyncio
    async def test_many_pids_one_ecu_one_header(self):
        # Simulate a per-PID loop: set_header before each of 5 UDS reads.
        t = _term()
        for _ in range(5):
            await t.set_header(0x7E4)
            await t.send_uds("2101")
        header_cmds = [c for c in _cmds(t) if c.startswith(("ATSH", "ATFCSH"))]
        assert header_cmds == ["ATSH7E4", "ATFCSH7E4"]  # only once, not 5x

    @pytest.mark.asyncio
    async def test_atz_resets_cache(self):
        t = _term()
        await t.set_header(0x7A0)
        await t.send_command("ATZ")  # resets ELM defaults -> header cleared
        t.ws.sent.clear()
        await t.set_header(0x7A0)  # must resend after reset
        assert _cmds(t) == ["ATSH7A0", "ATFCSH7A0"]

    @pytest.mark.asyncio
    async def test_direct_atsh_updates_cache(self):
        # A caller sending ATSH directly must keep the cache coherent, so a
        # later set_header for the same ECU is a no-op.
        t = _term()
        await t.send_command("ATSH7A0")
        await t.send_command("ATFCSH7A0")
        t.ws.sent.clear()
        await t.set_header(0x7A0)
        assert _cmds(t) == []

    @pytest.mark.asyncio
    async def test_atdp_does_not_reset_cache(self):
        # ATDP (describe protocol) must NOT be treated like ATD (set defaults).
        t = _term()
        await t.set_header(0x7A0)
        await t.send_command("ATDP")
        t.ws.sent.clear()
        await t.set_header(0x7A0)
        assert _cmds(t) == []


class TestInstrumentation:
    @pytest.mark.asyncio
    async def test_cmd_count_and_time_accumulate(self):
        t = _term()
        assert t.cmd_count == 0
        await t.set_header(0x7A0)  # 2 commands
        await t.send_uds("2101")  # 1 command
        assert t.cmd_count == 3
        assert t.cmd_time >= 0.0
