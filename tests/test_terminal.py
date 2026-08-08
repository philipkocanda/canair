"""Tests for canlib.terminal.WiCANTerminal — header caching + instrumentation.

A fake WebSocket drives the *real* set_header/_send_command_locked logic so the
caching decisions under test are the production ones.
"""

import asyncio
import contextlib

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


class DrainWS:
    """WebSocket double for the stale-frame drain: the reply is only enqueued
    on send() when ``reply`` is set (None => the recv loop times out), and stale
    frames can be pre-loaded into the queue to simulate a dirty pipe."""

    def __init__(self, reply: str | None = "OK\r>"):
        self.sent: list[str] = []
        self.reply = reply
        self._q: asyncio.Queue[str] = asyncio.Queue()

    def preload(self, *items: str):
        for it in items:
            self._q.put_nowait(it)

    async def send(self, data: str):
        self.sent.append(data)
        if self.reply is not None:
            await self._q.put(self.reply)

    async def recv(self) -> str:
        return await self._q.get()

    async def close(self):
        pass


class TestStaleFrameDrain:
    """A command that times out before consuming the ELM `>` prompt leaves the
    pipe dirty; the next command must realign before sending so late/stale frames
    can't leak into (and corrupt) the next response."""

    @pytest.mark.asyncio
    async def test_timeout_marks_pipe_dirty(self):
        t = WiCANTerminal(host="test")
        t.ws = DrainWS(reply=None)  # no reply -> the recv loop times out
        assert t._pipe_dirty is False
        await t.send_command("2101", timeout=0.05)
        assert t._pipe_dirty is True

    @pytest.mark.asyncio
    async def test_clean_prompt_keeps_pipe_clean(self):
        t = WiCANTerminal(host="test")
        t.ws = DrainWS(reply="6101AA\r>")
        t._pipe_dirty = True  # pretend a prior command left it dirty
        await t.send_command("2101")
        assert t._pipe_dirty is False  # this command consumed its prompt

    @pytest.mark.asyncio
    async def test_stale_frames_drained_before_next_command(self):
        # Dirty pipe with two stale frames buffered; the next command must
        # discard them so its parsed response is only the real reply.
        t = WiCANTerminal(host="test", timeout=0.2)
        t.ws = DrainWS(reply="6101AA\r>")
        t._pipe_dirty = True
        t.ws.preload("6F BC 09 00\r", "STALE\r")  # late frames from a prior read
        resp = await t.send_command("2101")
        assert resp == "6101AA"  # stale frames drained, not concatenated
        assert t._pipe_dirty is False

    @pytest.mark.asyncio
    async def test_no_drain_when_pipe_clean(self):
        # A clean pipe does not drain: a frame arriving concurrently with the
        # send is this command's real response, not stale, and is preserved.
        # (Recovery from a stale frame that *does* arrive prompt-terminated on a
        # clean pipe is response-content-driven — see TestPipeResync.)
        t = WiCANTerminal(host="test")
        t.ws = DrainWS(reply="6101AA\r>")
        assert t._pipe_dirty is False
        resp = await t.send_command("2101")
        assert resp == "6101AA"


class TestPipeResync:
    """Recovering the request/response alignment of an ELM327 pipe.

    The failure this covers: a transient link stall makes a read time out, the
    adapter's reply lands afterwards *with* its `>` prompt, and from then on every
    reply answers the previous request. Nothing raises — the socket is open and
    every read "succeeds" — so the only evidence is that the response doesn't echo
    the request that was sent. The fix reacts to that evidence.
    """

    @staticmethod
    def _drain_spy(t):
        calls: list[dict] = []

        async def drain(per_recv_timeout: float = 0.2, max_seconds: float = 1.0) -> None:
            calls.append({"per_recv_timeout": per_recv_timeout, "max_seconds": max_seconds})

        t._channel.drain = drain
        return calls

    @pytest.mark.asyncio
    async def test_resync_probes_the_adapter_after_draining(self):
        t = _term_prog(["ELM327 v1.5\r>", "6101AA\r>"])
        t._pipe_dirty = True
        resp = await t.send_command("2101")
        # ATI goes first: draining alone proves nothing, since the drain can't
        # distinguish "line was quiet" from "line was dead".
        assert t.ws.sent[0].startswith("ATI")
        assert t.ws.sent[1].startswith("2101")
        assert resp == "6101AA"
        assert t._pipe_dirty is False

    @pytest.mark.asyncio
    async def test_quiet_window_covers_the_adapters_own_ecu_wait(self):
        # Draining for less than the adapter's ATST budget is the bug, not the
        # fix: the late reply arrives after the drain gives up and survives it.
        t = _term_prog(["ELM327 v1.5\r>", "6101AA\r>"])
        calls = self._drain_spy(t)
        t.elm_timeout_cmd = "ATST96"  # 0x96 * 4.096ms = 614ms
        t.timeout = 30.0  # not the binding constraint here
        t._pipe_dirty = True
        await t.send_command("2101")
        assert calls[0]["per_recv_timeout"] > 0.614
        assert calls[0]["max_seconds"] >= 3.0

    @pytest.mark.asyncio
    async def test_quiet_window_never_exceeds_the_command_timeout(self):
        t = _term_prog(["ELM327 v1.5\r>", "6101AA\r>"])
        calls = self._drain_spy(t)
        t.elm_timeout_cmd = "ATSTFF"
        t.timeout = 0.3
        t._pipe_dirty = True
        await t.send_command("2101")
        assert calls[0]["per_recv_timeout"] == 0.3

    @pytest.mark.asyncio
    async def test_unparseable_st_falls_back_to_the_default_budget(self):
        t = _term_prog(["ELM327 v1.5\r>", "6101AA\r>"])
        calls = self._drain_spy(t)
        t.elm_timeout_cmd = "ATSTZZ"
        t.timeout = 30.0
        t._pipe_dirty = True
        await t.send_command("2101")
        assert calls[0]["per_recv_timeout"] > 0.614

    @pytest.mark.asyncio
    async def test_silent_probe_raises_connection_error(self):
        # ATI is answered by the adapter itself without touching the CAN bus, so
        # its silence cannot be blamed on the car: the link is gone. Raising is
        # what lets the monitor's reconnector take over.
        t = WiCANTerminal(host="test", timeout=0.1)
        t.ws = DrainWS(reply=None)
        t._pipe_dirty = True
        with pytest.raises(ConnectionError, match="resync failed"):
            await t.send_command("2101")

    @pytest.mark.asyncio
    async def test_stale_echo_triggers_a_resync_then_succeeds(self):
        # The reported bug, replayed: the reply to 2102 answers 2101. Both share
        # response SID 0x61, so only the echo byte reveals the offset.
        t = _term_prog(["6101AA\r>", "ELM327 v1.5\r>", "6102BB\r>"])
        t.timeout = 0.2
        r = await t.send_uds("2102", retries=1, expected_sid=0x21, expected_echo=b"\x02")
        assert r["ok"] is True
        assert r["hex"] == "6102BB"
        assert [s[:4] for s in t.ws.sent] == ["2102", "ATI\r", "2102"]

    @pytest.mark.asyncio
    async def test_stale_echo_resyncs_even_with_no_retries(self):
        # retries=0 must still realign. The validated TesterPresent keepalive runs
        # with no retries, and it is the exchange most likely to notice the offset
        # first — if it returned the stale reply without resyncing, the desync
        # would survive every keepalive tick.
        t = _term_prog(["6101AA\r>", "ELM327 v1.5\r>"])
        t.timeout = 0.2
        r = await t.send_uds("2102", expected_sid=0x21, expected_echo=b"\x02")
        assert r["ok"] is False
        assert "Echo mismatch" in r["error"]
        assert [s[:4] for s in t.ws.sent] == ["2102", "ATI\r"]  # realigned for next time

    @pytest.mark.asyncio
    async def test_good_response_never_resyncs(self):
        t = _term_prog(["6102BB\r>"])
        r = await t.send_uds("2102", expected_sid=0x21, expected_echo=b"\x02")
        assert r["ok"] is True
        assert not any(s.startswith("ATI") for s in t.ws.sent)

    @pytest.mark.asyncio
    async def test_nrc_never_resyncs(self):
        # A negative response is a healthy pipe: the request reached the ECU and
        # its refusal came back in the right slot.
        t = _term_prog(["7F2112\r>"])
        r = await t.send_uds("2102", expected_sid=0x21, expected_echo=b"\x02")
        assert r["nrc"] == 0x12
        assert not any(s.startswith("ATI") for s in t.ws.sent)

    @pytest.mark.asyncio
    async def test_resync_is_tallied_separately_from_the_fault(self):
        t = _term_prog(["6101AA\r>", "ELM327 v1.5\r>", "6102BB\r>"])
        t.timeout = 0.2
        await t.send_uds("2102", retries=1, expected_sid=0x21, expected_echo=b"\x02")
        assert t.diag.resyncs == 1
        assert t.diag.stale == 1  # the fault, counted once and not as a resync

    @pytest.mark.asyncio
    async def test_resync_does_not_recurse(self):
        # The probe goes through the same send path that triggers a resync, so a
        # dirty probe must not start another one.
        t = _term_prog(["ELM327 v1.5\r>", "6101AA\r>"])
        t.timeout = 0.2
        t._pipe_dirty = True
        await t.send_command("2101")
        assert len([s for s in t.ws.sent if s.startswith("ATI")]) == 1


class TestTransaction:
    """`set_header` + the request it applies to must not be interleaved.

    ATSH is sticky adapter state, so a keepalive that retargets the header
    between them sends the request to the wrong ECU — and the reply that comes
    back is then a perfectly-formed answer attributed to the wrong PID.
    """

    @pytest.mark.asyncio
    async def test_transaction_serialises_against_a_concurrent_command(self):
        t = _term_prog(["OK\r>", "6101AA\r>", "OK\r>"])
        order: list[str] = []

        async def owner():
            async with t.transaction():
                await t.send_command("ATSH7E4")
                order.append("header")
                await asyncio.sleep(0.05)  # a window an unlocked rival would use
                await t.send_command("2101")
                order.append("request")

        async def rival():
            await asyncio.sleep(0.01)
            await t.send_command("ATSH770")
            order.append("rival")

        await asyncio.gather(owner(), rival())
        assert order == ["header", "request", "rival"]

    @pytest.mark.asyncio
    async def test_transaction_is_reentrant_for_its_owner(self):
        # send_uds opens a transaction of its own; nesting it inside a caller's
        # must not deadlock on the same lock.
        # set_header issues two commands (ATSH + ATFCSH) before the request.
        t = _term_prog(["OK\r>", "OK\r>", "6101AA\r>"])
        async with t.transaction():
            await t.set_header(0x7E4)
            r = await t.send_uds("2101", expected_sid=0x21)
        assert r["ok"] is True

    @pytest.mark.asyncio
    async def test_lock_owner_is_cleared_after_a_failure(self):
        t = WiCANTerminal(host="test", timeout=0.05)
        t.ws = DrainWS(reply=None)
        with contextlib.suppress(ConnectionError):
            async with t.transaction():
                await t.send_command("2101")
        assert t._lock_owner is None
        assert not t._cmd_lock.locked()
