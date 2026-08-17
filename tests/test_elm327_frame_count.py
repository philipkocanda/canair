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

from canlib.transport.elm327_frame_count import (
    MAX_REQUESTABLE_FRAMES,
    CountVerdict,
    FrameCountCache,
    annotate_request,
    requestable,
)
from canlib.transport.elm327_terminal import Elm327Terminal
from canlib.uds_parse import CAT_BUS, CAT_DROP, CAT_NO_DATA

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
    async def test_a_short_reply_realigns_retries_plain_and_drops_the_digit(self):
        """The whole safety argument in one exchange.

        The digit-bearing read comes back a frame short, and the caller passed no
        ``retries`` — proof the recovery is not funded from the caller's retry
        budget, which would make the optimization able to consume a read. The plain
        retry then returns the *same* complete length, so this was a frame lost in
        transit, not a variable response: the digit is dropped for the session, but
        the durable count is **not** retired (that would delete a verified profile
        value over one dropped frame — the bug a flaky wican-ws link exposed).
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
        assert term._frame_counts.count_for(_KEY) is None, "digit dropped for the session"
        assert _KEY not in term._frame_counts.ledger.retired(), "count kept in the profile"

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
    async def test_an_adapter_that_rejects_the_nibble_drops_the_digit(self):
        """A clone answering `?` must degrade to plain, not fail every read.

        And must not pay for a drain doing it: `?` is a complete, prompt-
        terminated refusal, so nothing is left queued to realign. The plain retry
        returns the same complete length, so the count itself is fine — this
        adapter just can't use the hint — and the durable value stays put for a
        capable adapter, while the digit is dropped for this session.
        """
        ch = QueuedChannel(_block(_COMPLETE))
        term = _term(ch)
        await term.send_uds("22C00B", timeout=5.0)

        ch.feed(_block("?"), _block(_COMPLETE))
        resp = await term.send_uds("22C00B", timeout=5.0)

        assert resp["ok"] is True
        assert ch.drains == 0
        assert ch.sent == ["22C00B\r", "22C00B4\r", "22C00B\r"]
        assert term._frame_counts.count_for(_KEY) is None, "digit dropped for the session"
        assert _KEY not in term._frame_counts.ledger.retired(), "count kept in the profile"


class TestDecisionProcedure:
    """`CountAttempt` on its own — the retry policy with no channel or clock.

    `TestLearningFromTheWire` and `TestSelfHealing` above prove the same rules
    end-to-end through a terminal; these pin them directly, which is what makes the
    attribution logic readable as policy rather than as control flow.
    """

    @staticmethod
    def _ok(frames: int) -> dict:
        return {"raw": "", "ok": True, "isotp_frame_count": frames}

    @staticmethod
    def _short(frames: int) -> dict:
        return {
            "raw": "",
            "ok": False,
            "isotp_frame_count": frames,
            "error": "truncated ISO-TP",
            "error_kind": CAT_DROP,
        }

    @staticmethod
    def _nrc() -> dict:
        return {"raw": "", "ok": False, "nrc": 0x31, "isotp_frame_count": 1}

    @staticmethod
    def _silent() -> dict:
        return {"raw": "", "ok": False, "error": "no response", "error_kind": CAT_NO_DATA}

    def test_the_first_request_is_plain_and_teaches_the_count(self):
        cache = FrameCountCache()
        attempt = cache.attempt((0x7A0, "22C00B"))
        assert attempt.command("22C00B") == "22C00B"
        assert attempt.verdict(self._ok(4)) == CountVerdict()
        assert cache.count_for((0x7A0, "22C00B")) == 4

    def test_a_later_request_carries_the_digit_and_settles(self):
        cache = FrameCountCache()
        cache.learn((0x7A0, "22C00B"), 4)
        attempt = cache.attempt((0x7A0, "22C00B"))
        assert attempt.command("22C00B") == "22C00B4"
        assert not attempt.verdict(self._ok(4)).retry_plain

    def test_an_incomplete_plain_reply_teaches_nothing(self):
        cache = FrameCountCache()
        attempt = cache.attempt((0x7A0, "22C00B"))
        attempt.command("22C00B")
        attempt.verdict(self._short(2))
        assert cache.count_for((0x7A0, "22C00B")) is None

    def test_a_short_digit_reply_asks_to_realign_then_retry_plain(self):
        cache = FrameCountCache()
        cache.learn((0x7A0, "22C00B"), 4)
        attempt = cache.attempt((0x7A0, "22C00B"))
        attempt.command("22C00B")
        verdict = attempt.verdict(self._short(2))
        assert verdict.retry_plain and verdict.realign
        assert "asked 4 frame(s), got 2" in verdict.note

    def test_the_retry_drops_the_digit(self):
        cache = FrameCountCache()
        cache.learn((0x7A0, "22C00B"), 4)
        attempt = cache.attempt((0x7A0, "22C00B"))
        attempt.command("22C00B")
        attempt.verdict(self._short(2))
        assert attempt.command("22C00B") == "22C00B"

    def test_a_plain_retry_that_works_convicts_the_digit(self):
        cache = FrameCountCache()
        cache.learn((0x7A0, "22C00B"), 4)
        attempt = cache.attempt((0x7A0, "22C00B"))
        attempt.command("22C00B")
        attempt.verdict(self._short(2))
        attempt.command("22C00B")
        assert attempt.verdict(self._ok(6)) == CountVerdict()
        assert cache.count_for((0x7A0, "22C00B")) is None, "opted out"

    def test_a_plain_retry_of_a_different_length_retires_the_count(self):
        # A COMPLETE plain reply of a different length than the digit asked for is
        # the only thing that proves the stored count wrong, so it is the only
        # thing that retires the key and clears the profile value.
        key = (0x7A0, "22C00B")
        cache = FrameCountCache()
        cache.learn(key, 4)
        attempt = cache.attempt(key)
        attempt.command("22C00B")
        attempt.verdict(self._short(2))
        attempt.command("22C00B")
        attempt.verdict(self._ok(6))
        assert key in cache.ledger.retired()

    def test_a_transient_drop_disables_the_digit_but_keeps_the_count(self):
        # The regression: a digit-bearing read that merely loses a frame in transit
        # looks identical to an undercount at the moment of failure. The plain
        # retry returning the SAME length proves it was transient loss, not a
        # variable response — so the digit is dropped for the session but the
        # durable count is NOT retired. Deleting a verified profile value over a
        # dropped frame is exactly the bug a flaky wican-ws link exposed.
        key = (0x7A0, "22C00B")
        cache = FrameCountCache()
        cache.learn(key, 4)
        attempt = cache.attempt(key)
        assert attempt.command("22C00B") == "22C00B4"
        assert attempt.verdict(self._short(2)).retry_plain
        attempt.command("22C00B")
        assert attempt.verdict(self._ok(4)) == CountVerdict()
        assert cache.count_for(key) is None, "digit disabled for the session"
        assert key not in cache.ledger.retired(), "count NOT cleared from the profile"

    def test_a_transient_drop_does_not_defeat_a_seed_across_reconnect(self):
        # End-to-end BCM shape: a seeded count hits a dirty pipe, the plain retry
        # shows the same length, and the seed survives — so a reconnect (a fresh
        # link) re-applies it and the digit is tried again.
        key = (0x7A0, "22C00B")
        cache = FrameCountCache()
        cache.seed({key: 4})
        attempt = cache.attempt(key)
        assert attempt.command("22C00B") == "22C00B4"
        attempt.verdict(self._short(2))
        attempt.command("22C00B")
        attempt.verdict(self._ok(4))
        assert key not in cache.ledger.retired()
        cache.reset()
        assert cache.attempt(key).command("22C00B") == "22C00B4"

    def test_an_nrc_on_the_plain_retry_keeps_the_count(self):
        # An NRC is a complete answer that simply occupies fewer frames; it proves
        # nothing about the positive response's length, so it disables the digit
        # for the session but must not retire the stored count.
        key = (0x7A0, "22C00B")
        cache = FrameCountCache()
        cache.learn(key, 4)
        attempt = cache.attempt(key)
        attempt.command("22C00B")
        attempt.verdict(self._short(2))
        attempt.command("22C00B")
        assert attempt.verdict(self._nrc()) == CountVerdict()
        assert cache.count_for(key) is None, "digit disabled"
        assert key not in cache.ledger.retired(), "count kept"

    def test_a_still_silent_ecu_proves_nothing_and_keeps_the_optimization(self):
        cache = FrameCountCache()
        cache.learn((0x7A0, "22C00B"), 4)
        attempt = cache.attempt((0x7A0, "22C00B"))
        attempt.command("22C00B")
        attempt.verdict(self._silent())
        attempt.command("22C00B")
        attempt.verdict(self._silent())
        assert cache.count_for((0x7A0, "22C00B")) == 4

    def test_a_rejected_nibble_needs_no_realign(self):
        # `?` is a complete, prompt-terminated refusal: nothing is left queued, so
        # draining would cost a probe round trip for no reason.
        cache = FrameCountCache()
        cache.learn((0x7A0, "22C00B"), 4)
        attempt = cache.attempt((0x7A0, "22C00B"))
        attempt.command("22C00B")
        verdict = attempt.verdict(
            {"raw": "?", "ok": False, "error": "Unknown command", "error_kind": CAT_BUS}
        )
        assert verdict.retry_plain and not verdict.realign

    def test_an_nrc_is_a_complete_answer_and_keeps_the_count(self):
        cache = FrameCountCache()
        cache.learn((0x7A0, "22C00B"), 4)
        attempt = cache.attempt((0x7A0, "22C00B"))
        attempt.command("22C00B")
        assert attempt.verdict(self._nrc()) == CountVerdict()
        assert cache.count_for((0x7A0, "22C00B")) == 4

    def test_a_digit_reply_never_relearns_its_own_count(self):
        # It can only ever confirm the number it asked for, so learning from it
        # would launder a wrong count into a permanent one.
        cache = FrameCountCache()
        cache.learn((0x7A0, "22C00B"), 4)
        attempt = cache.attempt((0x7A0, "22C00B"))
        attempt.command("22C00B")
        attempt.verdict(self._ok(4))
        assert cache.count_for((0x7A0, "22C00B")) == 4


class TestWireRules:
    """``requestable``/``annotate_request`` — the rules the emit paths share."""

    def test_a_plausible_count_on_an_even_request_is_requestable(self):
        assert requestable("2101", 4) is True

    def test_no_count_is_not_requestable(self):
        assert requestable("2101", None) is False

    def test_a_count_below_one_is_not_requestable(self):
        assert requestable("2101", 0) is False

    def test_a_count_above_the_ceiling_is_refused_not_clamped(self):
        # Clamping would send a deliberate undercount, whose queued tail then
        # answers the next request. The Ioniq's 13-frame 21F2 read is the case.
        assert requestable("21F2", MAX_REQUESTABLE_FRAMES) is True
        assert requestable("21F2", MAX_REQUESTABLE_FRAMES + 1) is False
        assert requestable("21F2", 13) is False

    def test_an_odd_length_request_cannot_carry_a_digit(self):
        # The nibble is only distinguishable from data because it makes the
        # command odd-length; appending to an odd request changes the question.
        assert requestable("210", 4) is False

    def test_the_digit_is_uppercase_hex_appended(self):
        assert annotate_request("2101", 4) == "21014"
        assert annotate_request("22C00B", 9) == "22C00B9"

    def test_annotating_never_adds_a_data_byte(self):
        # One character, so the adapter's 7-byte request ceiling is unaffected.
        assert len(annotate_request("22C00B", 4)) == len("22C00B") + 1


class TestSeeding:
    """A count carried in from the profile is used before anything is learned."""

    def test_a_seeded_count_is_requested_on_the_very_first_read(self):
        cache = FrameCountCache()
        cache.seed({_KEY: _FRAMES})
        attempt = cache.attempt(_KEY)
        assert attempt.command("22C00B") == f"22C00B{_FRAMES}"

    def test_a_seeded_count_is_reported_as_seeded(self):
        cache = FrameCountCache()
        cache.seed({_KEY: _FRAMES})
        assert cache.is_seeded(_KEY) is True
        assert cache.is_seeded((None, "2101")) is False

    def test_a_seed_too_large_to_request_is_dropped(self):
        cache = FrameCountCache()
        cache.seed({_KEY: MAX_REQUESTABLE_FRAMES + 1})
        assert cache.attempt(_KEY).command("22C00B") == "22C00B"

    def test_a_seed_survives_a_reconnect(self):
        # reset() forgets what this link taught us, but a profile fact is not a
        # property of the link, so it is re-applied.
        cache = FrameCountCache()
        cache.seed({_KEY: _FRAMES})
        cache.reset()
        assert cache.attempt(_KEY).command("22C00B") == f"22C00B{_FRAMES}"

    def test_opting_out_defeats_the_seed_and_retires_it(self):
        # A seeded count that this session disproves must not come back on the
        # next reconnect, and must be cleared from the profile.
        cache = FrameCountCache()
        cache.seed({_KEY: _FRAMES})
        cache.opt_out(_KEY)
        cache.reset()
        assert cache.attempt(_KEY).command("22C00B") == "22C00B"
        assert _KEY in cache.ledger.retired()

    def test_a_disabled_cache_ignores_seeds(self):
        cache = FrameCountCache(enabled=False)
        cache.seed({_KEY: _FRAMES})
        assert cache.attempt(_KEY).command("22C00B") == "22C00B"


class TestLedgerFeeding:
    """What the decision procedure banks for the profile write-back."""

    def _resp(self, frames: int, ok: bool = True) -> dict:
        return {"ok": ok, "isotp_frame_count": frames}

    def test_a_plain_complete_reply_is_observed(self):
        cache = FrameCountCache()
        attempt = cache.attempt(_KEY)
        attempt.command("22C00B")
        attempt.verdict(self._resp(_FRAMES))
        assert cache.ledger.record(_KEY).observations == 1

    def test_a_held_digit_confirms(self):
        cache = FrameCountCache()
        cache.seed({_KEY: _FRAMES})
        attempt = cache.attempt(_KEY)
        attempt.command("22C00B")
        assert attempt.verdict(self._resp(_FRAMES)).retry_plain is False
        assert cache.ledger.confirmed() == {_KEY: _FRAMES}

    def test_an_nrc_to_a_digit_bearing_request_does_not_confirm(self):
        # digit_held accepts an NRC (nothing is left queued), but a refusal
        # occupies fewer frames than the positive response the count measures.
        cache = FrameCountCache()
        cache.seed({_KEY: _FRAMES})
        attempt = cache.attempt(_KEY)
        attempt.command("22C00B")
        attempt.verdict({"ok": False, "nrc": 0x7F, "isotp_frame_count": 1})
        assert cache.ledger.confirmed() == {}

    def test_a_count_too_large_to_request_is_still_observed(self):
        # learn() opts such a key out, but the true count is a profile fact worth
        # recording — and a later disagreement must still be able to retire it.
        cache = FrameCountCache()
        attempt = cache.attempt(_KEY)
        attempt.command("22C00B")
        attempt.verdict(self._resp(MAX_REQUESTABLE_FRAMES + 4))
        assert cache.ledger.record(_KEY).frames == MAX_REQUESTABLE_FRAMES + 4

    def test_a_convicted_digit_retires_the_count(self):
        cache = FrameCountCache()
        cache.seed({_KEY: _FRAMES})
        attempt = cache.attempt(_KEY)

        attempt.command("22C00B")
        verdict = attempt.verdict({"ok": False, "error_kind": CAT_DROP, "isotp_frame_count": 3})
        assert verdict.retry_plain is True

        # The plain form answers, which convicts the digit.
        assert attempt.command("22C00B") == "22C00B"
        attempt.verdict(self._resp(_FRAMES + 1))
        assert _KEY in cache.ledger.retired()
        assert cache.ledger.confirmed() == {}
