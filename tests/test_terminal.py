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

    @pytest.mark.asyncio
    async def test_timing_records_uds_not_at_or_keepalive(self):
        t = _term()
        await t.set_header(0x7E4)  # ATSH/ATFCSH -> not recorded
        await t.send_command("3E00")  # keepalive -> not recorded
        await t.send_uds("2101")  # recorded under 0x7E4
        rows = t.timings.snapshot()
        assert [(r["ecu"], r["pid"]) for r in rows] == [("0x7E4", "2101")]


class ProgrammableWS:
    """FakeWS returning a queued reply per send (defaults to NO DATA)."""

    def __init__(self, replies):
        self.sent: list[str] = []
        self._replies = list(replies)
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, data: str):
        self.sent.append(data)
        reply = self._replies.pop(0) if self._replies else "NO DATA\r>"
        await self._q.put(reply)

    async def recv(self) -> str:
        return await self._q.get()

    async def close(self):
        pass


def _term_prog(replies) -> WiCANTerminal:
    t = WiCANTerminal(host="test")
    t.ws = ProgrammableWS(replies)
    return t


def _reads(t: WiCANTerminal, pid: str) -> int:
    return len([s for s in t.ws.sent if s.startswith(pid)])


class TestPerEcuTimeout:
    """send_uds resolves the current header's per-ECU budget when no explicit
    timeout is given; an explicit timeout still wins."""

    @staticmethod
    def _spy(t):
        seen = []
        orig = t.send_command

        async def spy(cmd, timeout=None):
            seen.append(timeout)
            return await orig(cmd, timeout=timeout)

        t.send_command = spy
        return seen

    @pytest.mark.asyncio
    async def test_per_ecu_budget_used_for_current_header(self):
        t = _term()
        t.ecu_timeouts = {0x7E2: 5.0}
        seen = self._spy(t)
        await t.set_header(0x7E2)
        seen.clear()  # ignore header ATSH/ATFCSH sends
        await t.send_uds("2101")
        assert seen == [5.0]

    @pytest.mark.asyncio
    async def test_explicit_timeout_overrides_per_ecu(self):
        t = _term()
        t.ecu_timeouts = {0x7E2: 5.0}
        seen = self._spy(t)
        await t.set_header(0x7E2)
        seen.clear()
        await t.send_uds("2101", timeout=1.25)
        assert seen == [1.25]

    @pytest.mark.asyncio
    async def test_falls_back_to_client_default(self):
        t = _term()  # WiCANTerminal(host="test") -> self.timeout == 3.0
        t.ecu_timeouts = {0x770: 5.0}  # different ECU
        seen = self._spy(t)
        await t.set_header(0x7E2)  # no per-ECU entry -> default
        seen.clear()
        await t.send_uds("2101")
        assert seen == [3.0]


class TestRetryOnTimeout:
    @pytest.mark.asyncio
    async def test_retries_on_no_data_then_succeeds(self):
        t = _term_prog(["NO DATA\r>", "6101AA\r>"])
        r = await t.send_uds("2101", retries=1)
        assert r["ok"] is True
        assert r["hex"] == "6101AA"
        assert _reads(t, "2101") == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_nrc(self):
        # A definitive negative (NRC) is returned immediately — not silence.
        t = _term_prog(["7F2112\r>", "6101AA\r>"])
        r = await t.send_uds("2101", retries=1)
        assert r["ok"] is False
        assert r["nrc"] == 0x12
        assert _reads(t, "2101") == 1

    @pytest.mark.asyncio
    async def test_retries_exhausted_returns_last(self):
        t = _term_prog(["NO DATA\r>", "NO DATA\r>"])
        r = await t.send_uds("2101", retries=1)
        assert r["ok"] is False
        assert "NO DATA" in r["error"]
        assert _reads(t, "2101") == 2

    @pytest.mark.asyncio
    async def test_default_is_no_retry(self):
        t = _term_prog(["NO DATA\r>", "6101AA\r>"])
        r = await t.send_uds("2101")  # retries=0
        assert r["ok"] is False
        assert _reads(t, "2101") == 1
