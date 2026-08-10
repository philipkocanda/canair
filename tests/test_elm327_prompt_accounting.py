"""Prompt accounting in the shared ELM327 engine — correlating replies to commands.

UDS carries no transaction id. A SID+DID echo (`uds_parse.request_echo`) identifies
the request's *content*, so it catches a desync only while the poll cycle happens to
be walking different PIDs — the exact circumstance that made the original bug report
legible. It is structurally blind to an offset of exactly one cycle, and to any
single-PID poll, because the stale reply echoes the very PID that was just asked for.

The one per-exchange framing signal the ELM327 does offer is its `>` prompt: exactly
one per response it sends. Counting the prompts the adapter still owes turns "which
command does this reply belong to?" from a timing guess into arithmetic, so these
tests pin the ledger rather than any tuned constant.

Device-free: a chunk-driven fake channel stands in for the wire.
"""

from __future__ import annotations

import asyncio

import pytest

from canlib.transport.elm327_pipe import MAX_OWED_PROMPTS
from canlib.transport.elm327_terminal import Elm327Terminal

from ._fakes import QueuedChannel


def _compact(text: str) -> str:
    return text.replace(" ", "").replace("\n", "")


class TestNormalExchangeIsUnaffected:
    """The ledger must cost nothing when nothing is wrong."""

    @pytest.mark.asyncio
    async def test_a_reply_is_returned_as_soon_as_its_prompt_lands(self):
        """One owed prompt, one block: return immediately, don't wait for a second.

        Waiting to see whether *more* was queued would add a full timeout to every
        command on the happy path, which on the slow links this feature targets
        would be a worse bug than the one being fixed.
        """
        ch = QueuedChannel("61 01 AA\r>")
        term = Elm327Terminal(ch, timeout=5.0)
        resp = await asyncio.wait_for(term.send_command("2101", timeout=5.0), timeout=1.0)
        assert _compact(resp) == "6101AA"
        assert ch.drains == 0
        assert term._pipe.dirty is False
        assert term.diag.stale == 0


class TestLateReplyIsAttributed:
    """A reply that arrives after its command was abandoned belongs to that command."""

    @pytest.mark.asyncio
    async def test_the_newest_block_is_returned_and_the_late_one_discarded(self):
        ch = QueuedChannel()
        term = Elm327Terminal(ch, timeout=5.0)

        # The link stalls: nothing arrives, so this command is abandoned owing a
        # prompt. (This is the "Empty response" that opened the reported cascade.)
        await term.send_command("2102", timeout=0.05)
        assert term._pipe.dirty is True
        assert term._pipe.owed == 1

        # Now both replies land: the late one for 2102, then this command's.
        ch.feed("61 02 AA\r>", "61 03 BB\r>")
        resp = await asyncio.wait_for(term.send_command("2103", timeout=5.0), timeout=1.0)
        assert _compact(resp) == "6103BB"
        assert term.diag.stale == 1
        assert term._pipe.dirty is False
        assert term._pipe.owed == 0

    @pytest.mark.asyncio
    async def test_recovery_needs_no_drain_and_no_timing_guess(self):
        """The point of counting prompts: recovery is positive, not heuristic."""
        ch = QueuedChannel()
        term = Elm327Terminal(ch, timeout=5.0)
        await term.send_command("2102", timeout=0.05)
        ch.feed("61 02 AA\r>", "61 03 BB\r>")
        await asyncio.wait_for(term.send_command("2103", timeout=5.0), timeout=1.0)
        assert ch.drains == 0

    @pytest.mark.asyncio
    async def test_the_same_pid_twice_still_recovers(self):
        """The regression echo validation cannot catch, and this can.

        Both blocks are answers to `2101`, so their SID+DID echoes are identical
        and `parse_uds_response` has nothing to compare — a single-PID monitor
        would have shown the previous cycle's value forever. Only the prompt count
        distinguishes them, and it must pick the fresh one.
        """
        ch = QueuedChannel()
        term = Elm327Terminal(ch, timeout=5.0)
        await term.send_command("2101", timeout=0.05)

        ch.feed("61 01 AA\r>", "61 01 BB\r>")
        resp = await asyncio.wait_for(
            term.send_uds("2101", timeout=5.0, expected_sid=0x21, expected_echo=b"\x01"),
            timeout=1.0,
        )
        assert resp["ok"] is True
        assert resp["hex"] == "6101BB"  # the fresh reading, not the stale one
        assert term.diag.stale == 1

    @pytest.mark.asyncio
    async def test_two_abandoned_commands_are_both_discarded(self):
        ch = QueuedChannel()
        term = Elm327Terminal(ch, timeout=5.0)
        await term.send_command("2102", timeout=0.05)
        await term.send_command("2103", timeout=0.05)
        assert term._pipe.owed == 2

        ch.feed("61 02 AA\r>", "61 03 BB\r>", "61 04 CC\r>")
        resp = await asyncio.wait_for(term.send_command("2104", timeout=5.0), timeout=1.0)
        assert _compact(resp) == "6104CC"
        assert term.diag.stale == 2


class TestBlockFraming:
    """Blocks must never be torn across two reads."""

    @pytest.mark.asyncio
    async def test_bytes_trailing_the_prompt_are_carried_to_the_next_command(self):
        """A single chunk can hold one whole block plus the head of the next.

        Dropping the tail would corrupt the following reply; the carry keeps it.
        """
        ch = QueuedChannel("61 01 AA\r>61 0")
        term = Elm327Terminal(ch, timeout=5.0)
        resp = await asyncio.wait_for(term.send_command("2101", timeout=5.0), timeout=1.0)
        assert _compact(resp) == "6101AA"
        assert term._pipe.carry == "61 0"

        ch.feed("2 BB\r>")
        resp = await asyncio.wait_for(term.send_command("2102", timeout=5.0), timeout=1.0)
        assert _compact(resp) == "6102BB"

    @pytest.mark.asyncio
    async def test_a_reply_split_across_chunks_is_reassembled(self):
        ch = QueuedChannel("61 01 ", None, "AA\r>")
        term = Elm327Terminal(ch, timeout=5.0)
        resp = await asyncio.wait_for(term.send_command("2101", timeout=5.0), timeout=1.0)
        assert _compact(resp) == "6101AA"


class TestLedgerBounds:
    """The count is only evidence while the adapter is paying its debts."""

    @pytest.mark.asyncio
    async def test_an_unpaid_backlog_falls_back_to_a_drain_and_probe(self):
        """Past `MAX_OWED_PROMPTS` the arithmetic has stopped explaining anything.

        A silent adapter would otherwise make every later command wait for prompts
        that are never coming, so the engine reverts to the drain-and-probe resync
        — which either realigns or raises, escalating to a reconnect.
        """
        ch = QueuedChannel()
        term = Elm327Terminal(ch, timeout=5.0)
        for _ in range(MAX_OWED_PROMPTS):
            await term.send_command("2101", timeout=0.05)
        assert term._pipe.owed == MAX_OWED_PROMPTS
        assert ch.drains == 0

        with pytest.raises(ConnectionError, match="resync failed"):
            await term.send_command("2101", timeout=0.05)
        assert ch.drains == 1

    @pytest.mark.asyncio
    async def test_a_resync_zeroes_the_ledger(self):
        """The drain threw the owed replies away, so the count must go with them."""
        ch = QueuedChannel()
        term = Elm327Terminal(ch, timeout=5.0)
        for _ in range(MAX_OWED_PROMPTS):
            await term.send_command("2101", timeout=0.05)

        # The probe is answered, so the resync succeeds and leaves a clean ledger.
        ch.after_drain = ["OBDLink MX\r>", "61 01 AA\r>"]
        resp = await asyncio.wait_for(term.send_command("2101", timeout=5.0), timeout=2.0)
        assert _compact(resp) == "6101AA"
        assert term._pipe.owed == 0
        assert term.diag.resyncs == 1
