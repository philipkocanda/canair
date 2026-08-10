"""Expected-response-count digits on ELM327 requests — learning, use, self-healing.

An ELM327 data request whose hex length is *odd* has its final nibble read as the
number of response frames to wait for. Supplying it lets the adapter return the
instant the reply is whole instead of sitting out its ``ATST`` ECU-wait budget:
measured ~206 ms → ~53 ms per read on a WiCAN Pro
(``plans/2026-08-09-wican-ws-throughput-ceiling.md``).

The optimization is only safe because it is never *guessed*, and these tests pin
the three properties that make it safe rather than the speedup itself:

- a count is learned only from a digit-free reply already proven complete, so a
  truncation can never be learned and then frozen in place by self-confirmation;
- a digit-bearing reply is checked against what was asked for, and a mismatch
  realigns the pipe — an undercount leaves the tail of the response queued, where
  it would surface as the *next* request's answer;
- a mismatch then retries in the plain form and opts the request out for the rest
  of the session, so the feature can cost latency but can never lose a read.

Device-free: the shared chunk-driven fake channel drives the real engine, so the
request text on the wire and the resync are the ones production would produce.
"""

from __future__ import annotations

import pytest

from canlib.transport.elm327_frame_count import MAX_REQUESTABLE_FRAMES, FrameCountCache
from canlib.transport.elm327_terminal import Elm327Terminal

from ._fakes import QueuedChannel

# Real wire format of a live BCM 22C00B read: declared 0x017 = 23 bytes across
# four ISO-TP frames (the body shared with tests/test_uds_parse.py).
_FRAMES = 4
_COMPLETE = "017\r0:62C00BFFFF00\r1:00C84F0100C84E\r2:0100C84D0100C6\r3:4D0100AAAAAAAA"
# The same read one frame short — what an undercounted digit actually produces.
_SHORT = "017\r0:62C00BFFFF00\r1:00C84F0100C84E\r2:0100C84D0100C6"

_KEY = (None, "22C00B")


def _block(body: str) -> str:
    """A prompt-terminated adapter response block."""
    return f"{body}\r>"


def _term(ch: QueuedChannel, **kw) -> Elm327Terminal:
    return Elm327Terminal(ch, timeout=5.0, **kw)


class TestCachePolicy:
    """The bookkeeping, isolated from any I/O."""

    def test_nothing_is_requested_until_something_is_learned(self):
        assert FrameCountCache().count_for(_KEY) is None

    def test_a_learned_count_is_returned(self):
        cache = FrameCountCache()
        cache.learn(_KEY, 4)
        assert cache.count_for(_KEY) == 4

    def test_an_unknown_count_is_not_invented(self):
        """A parse that yielded no frame count must not become a guess."""
        cache = FrameCountCache()
        cache.learn(_KEY, None)
        cache.learn(_KEY, 0)
        assert cache.count_for(_KEY) is None

    def test_an_unrequestable_count_opts_out_instead_of_clamping(self):
        """Clamping would be a deliberate undercount — the one truly unsafe act.

        A response needing more frames than the digit can express (the Ioniq's
        ``0x7EA:21F2`` needs 13) must simply keep the unoptimized path.
        """
        cache = FrameCountCache()
        cache.learn(_KEY, MAX_REQUESTABLE_FRAMES + 1)
        assert cache.count_for(_KEY) is None
        # And permanently: a later attempt must not resurrect it.
        cache.learn(_KEY, 4)
        assert cache.count_for(_KEY) is None

    def test_opting_out_is_permanent_for_the_session(self):
        cache = FrameCountCache()
        cache.learn(_KEY, 4)
        cache.opt_out(_KEY)
        cache.learn(_KEY, 4)
        assert cache.count_for(_KEY) is None

    def test_an_odd_length_request_is_refused(self):
        """The nibble is only distinguishable from data by making the request odd.

        Appending to an already-odd request would be read as a data byte, asking
        the ECU something else entirely. Unreachable for whole-byte UDS requests;
        refused rather than trusted.
        """
        cache = FrameCountCache()
        cache.learn((None, "22C00"), 2)
        assert cache.count_for((None, "22C00")) is None

    def test_disabled_learns_nothing_and_requests_nothing(self):
        cache = FrameCountCache(enabled=False)
        cache.learn(_KEY, 4)
        assert cache.count_for(_KEY) is None

    def test_counts_are_per_ecu(self):
        """The same DID on two ECUs need not answer in the same number of frames."""
        cache = FrameCountCache()
        cache.learn((0x7A0, "22C00B"), 4)
        assert cache.count_for((0x7E4, "22C00B")) is None

    def test_the_digit_is_uppercase_hex(self):
        cache = FrameCountCache()
        assert cache.annotate("22C00B", 4) == "22C00B4"
        assert cache.annotate("2101", 12) == "2101C"


class TestLearningFromTheWire:
    """What canair puts on the wire, and when it starts optimizing."""

    @pytest.mark.asyncio
    async def test_the_first_read_is_plain_and_teaches_the_count(self):
        ch = QueuedChannel(_block(_COMPLETE))
        term = _term(ch)
        resp = await term.send_uds("22C00B", timeout=5.0)
        assert resp["ok"] is True
        assert ch.sent == ["22C00B\r"]
        assert term._frame_counts.count_for(_KEY) == _FRAMES

    @pytest.mark.asyncio
    async def test_the_second_read_carries_the_digit(self):
        ch = QueuedChannel(_block(_COMPLETE))
        term = _term(ch)
        await term.send_uds("22C00B", timeout=5.0)
        ch.feed(_block(_COMPLETE))
        resp = await term.send_uds("22C00B", timeout=5.0)
        assert resp["ok"] is True
        assert ch.sent == ["22C00B\r", "22C00B4\r"]
        # Still trusted: the reply held exactly the four frames requested.
        assert term._frame_counts.count_for(_KEY) == _FRAMES

    @pytest.mark.asyncio
    async def test_a_single_frame_read_is_optimized_too(self):
        """The common case. One frame is still a full ATST wait without the digit."""
        ch = QueuedChannel(_block("62BC070841FBCC00"))
        term = _term(ch)
        await term.send_uds("22BC07", timeout=5.0)
        ch.feed(_block("62BC070841FBCC00"))
        await term.send_uds("22BC07", timeout=5.0)
        assert ch.sent == ["22BC07\r", "22BC071\r"]

    @pytest.mark.asyncio
    async def test_an_incomplete_reply_teaches_nothing(self):
        """Learning from a truncated read would bake the truncation in forever."""
        ch = QueuedChannel(_block(_SHORT))
        term = _term(ch)
        ch.after_drain = [_block("ELM327 v2.3")]
        resp = await term.send_uds("22C00B", timeout=5.0)
        assert resp["ok"] is False
        assert term._frame_counts.count_for(_KEY) is None

    @pytest.mark.asyncio
    async def test_disabling_keeps_every_request_plain(self):
        ch = QueuedChannel(_block(_COMPLETE))
        term = _term(ch, expected_responses=False)
        await term.send_uds("22C00B", timeout=5.0)
        ch.feed(_block(_COMPLETE))
        await term.send_uds("22C00B", timeout=5.0)
        assert ch.sent == ["22C00B\r", "22C00B\r"]

    @pytest.mark.asyncio
    async def test_reconnecting_forgets_what_was_learned(self):
        """A count measures one link, so it must not cross a reconnect.

        `monitor` re-homes a dropped session onto whichever device is reachable,
        possibly a different one on a different transport — carrying a count over
        that boundary would be an unmeasured guess.
        """
        ch = QueuedChannel(_block(_COMPLETE))
        term = _term(ch)
        await term.send_uds("22C00B", timeout=5.0)
        assert term._frame_counts.count_for(_KEY) == _FRAMES

        await term.connect()
        assert term._frame_counts.count_for(_KEY) is None

    def test_reset_keeps_the_configured_setting(self):
        """Clearing must not silently re-enable a feature the user turned off."""
        cache = FrameCountCache(enabled=False)
        cache.reset()
        assert cache.enabled is False


class TestSelfHealing:
    """A wrong count must cost latency, never a lost or corrupted read."""

    @pytest.mark.asyncio
    async def test_a_short_reply_realigns_retries_plain_and_opts_out(self):
        """The whole safety argument in one exchange.

        The response shrinks after the count was learned, so the digit now asks
        for more frames than arrive. The reply is short, and the caller passed no
        ``retries`` — proof the recovery is not funded from the caller's retry
        budget, which would make the optimization able to consume a read.
        """
        ch = QueuedChannel(_block(_COMPLETE))
        term = _term(ch)
        await term.send_uds("22C00B", timeout=5.0)
        assert term._frame_counts.count_for(_KEY) == _FRAMES

        ch.feed(_block(_SHORT))
        ch.after_drain = [_block("ELM327 v2.3"), _block(_COMPLETE)]
        resp = await term.send_uds("22C00B", timeout=5.0)

        assert resp["ok"] is True, "the caller must still get the reading"
        assert ch.drains == 1, "the queued tail must be swept, not left to alias"
        assert ch.sent == ["22C00B\r", "22C00B4\r", "ATI\r", "22C00B\r"]
        assert term._frame_counts.count_for(_KEY) is None

    @pytest.mark.asyncio
    async def test_a_negative_response_is_not_the_digit_failing(self):
        """An NRC is a complete, valid answer that simply occupies fewer frames.

        Treating it as a digit failure would opt out every PID an ECU refuses
        while a session is closed — most of them, on most cars.
        """
        ch = QueuedChannel(_block(_COMPLETE))
        term = _term(ch)
        await term.send_uds("22C00B", timeout=5.0)

        ch.feed(_block("7F2231"))
        resp = await term.send_uds("22C00B", timeout=5.0)

        assert resp["nrc"] == 0x31
        assert ch.drains == 0
        assert ch.sent == ["22C00B\r", "22C00B4\r"]
        assert term._frame_counts.count_for(_KEY) == _FRAMES

    @pytest.mark.asyncio
    async def test_a_silent_ecu_keeps_the_optimization(self):
        """Attribution by controlled comparison, not by blame.

        If the plain retry is silent too, the ECU was the problem — opting out
        would let one missed read permanently deoptimize a healthy PID.
        """
        ch = QueuedChannel(_block(_COMPLETE))
        term = _term(ch)
        await term.send_uds("22C00B", timeout=5.0)

        ch.feed(_block("NO DATA"), _block("NO DATA"))
        resp = await term.send_uds("22C00B", timeout=5.0)

        assert resp["ok"] is False
        assert ch.sent == ["22C00B\r", "22C00B4\r", "22C00B\r"]
        assert term._frame_counts.count_for(_KEY) == _FRAMES

    @pytest.mark.asyncio
    async def test_an_adapter_that_rejects_the_nibble_opts_out(self):
        """A clone answering `?` must degrade to plain, not fail every read.

        And must not pay for a drain doing it: `?` is a complete, prompt-
        terminated refusal, so nothing is left queued to realign.
        """
        ch = QueuedChannel(_block(_COMPLETE))
        term = _term(ch)
        await term.send_uds("22C00B", timeout=5.0)

        ch.feed(_block("?"), _block(_COMPLETE))
        resp = await term.send_uds("22C00B", timeout=5.0)

        assert resp["ok"] is True
        assert ch.drains == 0
        assert ch.sent == ["22C00B\r", "22C00B4\r", "22C00B\r"]
        assert term._frame_counts.count_for(_KEY) is None
