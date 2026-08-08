"""ResponsePending (UDS NRC 0x78) handling in the shared ELM327 engine.

A slow service — a DTC read, a routine, a long identity DID — answers
`7F <sid> 78` ("request received, response pending"), possibly several times,
before its real reply. The ELM327 chip emits each of those as a *complete*
exchange terminated by its own `>` prompt, so the engine must recognise the NRC
and keep waiting instead of returning the pending frame as the answer.

This was covered on the raw path (`uds_raw.is_response_pending`, tests in
`test_uds_raw.py`) but **not** on either ELM transport, even though the engine
implements it twice — once on the prompt-received branch and once on the
receive-timeout branch. A break here makes every slow service spuriously report
`7F..78`/no-data on `wican-ws` and `elm327-tcp` only.

Device-free: a scripted channel supplies the staged replies.
"""

from __future__ import annotations

import asyncio

import pytest

from canlib.transport.channel import Channel
from canlib.transport.elm327_terminal import Elm327Terminal


class ScriptedChannel:
    """A channel whose single send() is answered by a *sequence* of messages.

    Each element of ``chunks`` is delivered by one recv(); a ``None`` element
    simulates a quiet line (a receive timeout) between messages, which is what
    drives the engine's timeout branch.
    """

    transport_name = "wican-ws"

    def __init__(self, chunks: list[str | None]):
        self.sent: list[str] = []
        self._chunks: list[str | None] = list(chunks)
        self.drains = 0
        self.closed = False
        # Messages the line delivers *after* a drain has swept it. Lets a test
        # stage the post-recovery exchange, which a plain queue can't express
        # because drain() discards whatever is pending.
        self.after_drain: list[str | None] = []

    async def connect(self) -> None:  # pragma: no cover - not used here
        pass

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def recv(self, timeout: float) -> str | None:
        if not self._chunks:
            await asyncio.sleep(min(timeout, 0.001))
            return None
        chunk = self._chunks.pop(0)
        if chunk is None:
            await asyncio.sleep(min(timeout, 0.001))
            return None
        return chunk

    async def drain(self, per_recv_timeout: float = 0.2, max_seconds: float = 1.0) -> None:
        self.drains += 1
        self._chunks = list(self.after_drain)
        self.after_drain = []

    async def close(self) -> None:
        self.closed = True


def test_scripted_channel_satisfies_the_protocol():
    assert isinstance(ScriptedChannel([]), Channel)


class TestResponsePendingPromptBranch:
    """Each `7F xx 78` arrives with its own `>` prompt; keep waiting."""

    @pytest.mark.asyncio
    async def test_single_pending_then_the_real_reply(self):
        ch = ScriptedChannel(["7F 19 78\r>", "59 02 FF 01 23 45 67\r>"])
        term = Elm327Terminal(ch)
        result = await term.send_command("1902", timeout=2.0)
        # The pending frame must not be the returned answer.
        assert "5902" in result.replace(" ", "").replace("\n", "")

    @pytest.mark.asyncio
    async def test_repeated_pending_frames_are_all_absorbed(self):
        ch = ScriptedChannel(["7F 19 78\r>", "7F 19 78\r>", "7F 19 78\r>", "59 02 AA\r>"])
        term = Elm327Terminal(ch)
        result = await term.send_command("1902", timeout=2.0)
        assert "5902AA" in result.replace(" ", "").replace("\n", "")

    @pytest.mark.asyncio
    async def test_pending_is_recognised_without_spaces(self):
        # ELM327 `ATS0` (spaces off) is a normal configuration.
        ch = ScriptedChannel(["7F1978\r>", "5902AA\r>"])
        term = Elm327Terminal(ch)
        result = await term.send_command("1902", timeout=2.0)
        assert "5902AA" in result.replace(" ", "").replace("\n", "")

    @pytest.mark.asyncio
    async def test_send_uds_reports_the_final_positive_response(self):
        ch = ScriptedChannel(["7F 19 78\r>", "59 02 AA\r>"])
        term = Elm327Terminal(ch)
        resp = await term.send_uds("1902", timeout=2.0)
        assert resp["ok"] is True
        assert resp["hex"] == "5902AA"

    @pytest.mark.asyncio
    async def test_a_real_nrc_is_not_treated_as_pending(self):
        """Only 0x78 means "keep waiting" — 0x31 etc. must return immediately."""
        ch = ScriptedChannel(["7F 19 31\r>"])
        term = Elm327Terminal(ch)
        resp = await term.send_uds("1902", timeout=2.0)
        assert resp["ok"] is False
        assert resp["nrc"] == 0x31

    @pytest.mark.asyncio
    async def test_pending_then_a_real_nrc_returns_that_nrc(self):
        ch = ScriptedChannel(["7F 19 78\r>", "7F 19 31\r>"])
        term = Elm327Terminal(ch)
        resp = await term.send_uds("1902", timeout=2.0)
        assert resp["ok"] is False
        assert resp["nrc"] == 0x31

    @pytest.mark.asyncio
    async def test_pending_only_does_not_hang_past_the_timeout(self):
        """A device that never finishes must still return once the budget is spent.

        The deadline is *extended* per pending frame; with no further data the
        exchange has to end rather than block forever.
        """
        ch = ScriptedChannel(["7F 19 78\r>"])
        term = Elm327Terminal(ch)
        started = asyncio.get_running_loop().time()
        await term.send_command("1902", timeout=0.2)
        assert asyncio.get_running_loop().time() - started < 5.0

    @pytest.mark.asyncio
    async def test_a_78_that_is_not_an_nrc_does_not_extend_the_exchange(self):
        """`78` as ordinary payload data must not be mistaken for the NRC.

        The check is anchored to the `7F <sid> 78` shape, so a positive response
        whose bytes merely contain 0x7F and 0x78 must return immediately.
        """
        ch = ScriptedChannel(["61 01 7F 78 AA\r>"])
        term = Elm327Terminal(ch)
        resp = await term.send_uds("2101", timeout=0.5)
        assert resp["ok"] is True
        assert resp["hex"] == "61017F78AA"


class TestResponsePendingTimeoutBranch:
    """A pending frame followed by a quiet line, then the reply.

    Exercises the other implementation of the rule: the engine has the prompt but
    hit a receive timeout, and must keep waiting because the accumulated text is
    an unresolved `7F xx 78`.
    """

    @pytest.mark.asyncio
    async def test_quiet_gap_after_pending_still_waits_for_the_reply(self):
        ch = ScriptedChannel(["7F 19 78\r>", None, None, "59 02 AA\r>"])
        term = Elm327Terminal(ch)
        result = await term.send_command("1902", timeout=2.0)
        assert "5902AA" in result.replace(" ", "").replace("\n", "")

    @pytest.mark.asyncio
    async def test_quiet_gap_after_a_complete_reply_returns_it(self):
        # Control: with no pending NRC, a prompt + quiet line ends the exchange.
        ch = ScriptedChannel(["59 02 AA\r>", None])
        term = Elm327Terminal(ch)
        resp = await term.send_uds("1902", timeout=2.0)
        assert resp["ok"] is True
        assert resp["hex"] == "5902AA"


class TestPipeHygieneAroundPending:
    @pytest.mark.asyncio
    async def test_a_resolved_pending_exchange_leaves_the_pipe_clean(self):
        """A clean exit must not force the next command to drain.

        If a resolved ResponsePending left the pipe marked dirty, every
        subsequent command would pay a drain and could discard a valid early
        frame.
        """
        ch = ScriptedChannel(["7F 19 78\r>", "59 02 AA\r>"])
        term = Elm327Terminal(ch)
        await term.send_command("1902", timeout=2.0)
        assert term._pipe_dirty is False

    @pytest.mark.asyncio
    async def test_an_unresolved_pending_marks_the_pipe_dirty(self):
        """Timing out mid-exchange must dirty the pipe.

        The device may still deliver the late reply; it has to be drained so it
        can't be returned as the *next* command's response.
        """
        ch = ScriptedChannel(["7F 19 78\r>"])
        term = Elm327Terminal(ch)
        await term.send_command("1902", timeout=0.2)
        assert term._pipe_dirty is True

    @pytest.mark.asyncio
    async def test_the_next_command_drains_a_dirty_pipe(self):
        ch = ScriptedChannel(["7F 19 78\r>"])
        term = Elm327Terminal(ch)
        await term.send_command("1902", timeout=0.2)
        assert term._pipe_dirty is True
        # The resync drains, then confirms alignment with an ATI probe before the
        # real command goes out — so the line must answer both, *after* the drain.
        ch.after_drain = ["ELM327 v1.5\r>", "59 02 AA\r>"]
        resp = await term.send_command("1902", timeout=0.5)
        assert ch.drains == 1
        assert "5902AA" in resp.replace(" ", "")
        assert term._pipe_dirty is False
