"""Prompt accounting as a pure state machine.

`tests/test_elm327_prompt_accounting.py` covers the same ledger *through* the
terminal, where every case needs a scripted channel, a timeout and an event loop.
These tests drive `ResponsePipe` directly, so the accounting rules are pinned
without any I/O — which is the point of it being a separate module.
"""

from canlib.transport.elm327_pipe import (
    MAX_OWED_PROMPTS,
    PipeRead,
    ResponsePipe,
    compact,
    split_prompt_blocks,
)

# A ResponsePending interim frame (7F <sid> 78) and an ordinary positive reply.
PENDING = "7F2278"
REPLY = "62C00BFFFF"
OTHER = "62BC0711"


def collect(pipe: ResponsePipe, *messages: str) -> PipeRead:
    """Run one command's collection to completion over `messages`."""
    pipe.begin()
    for msg in messages:
        pipe.feed(msg)
    return pipe.finish()


class TestTextHelpers:
    def test_compact_strips_the_formatting_ats1_adds(self):
        assert compact("62 C0 0B\r\nFF") == "62C00BFF"

    def test_split_returns_completed_blocks_and_the_remainder(self):
        blocks, rest = split_prompt_blocks("first>second>third")
        assert blocks == ["first", "second"]
        assert rest == "third"

    def test_text_with_no_prompt_is_all_remainder(self):
        assert split_prompt_blocks("partial") == ([], "partial")


class TestOneCommandOneReply:
    def test_a_prompt_terminated_reply_is_this_commands_answer(self):
        read = collect(ResponsePipe(), f"{REPLY}>")
        assert read == PipeRead(block=REPLY, stale=[], clean=True)

    def test_a_satisfied_ledger_settles_to_zero(self):
        pipe = ResponsePipe()
        collect(pipe, f"{REPLY}>")
        assert (pipe.owed, pipe.dirty, pipe.carry) == (0, False, "")

    def test_the_reply_is_available_the_moment_its_prompt_lands(self):
        pipe = ResponsePipe()
        pipe.begin()
        assert not pipe.satisfied
        pipe.feed(f"{REPLY}>")
        assert pipe.satisfied

    def test_a_reply_split_across_reads_is_reassembled(self):
        read = collect(ResponsePipe(), "62C0", "0BFF", "FF>")
        assert read.block == REPLY
        assert read.clean

    def test_bytes_after_the_prompt_are_carried_not_torn(self):
        pipe = ResponsePipe()
        read = collect(pipe, f"{REPLY}>{OTHER[:4]}")
        assert read.block == REPLY
        assert pipe.carry == OTHER[:4]

    def test_a_reply_already_in_the_carry_needs_no_channel_read(self):
        pipe = ResponsePipe()
        pipe.carry = f"{REPLY}>"
        pipe.begin()
        assert pipe.satisfied
        assert pipe.finish().block == REPLY


class TestResponsePending:
    def test_an_interim_pending_frame_is_reported_so_the_caller_can_wait_longer(self):
        pipe = ResponsePipe()
        pipe.begin()
        assert pipe.feed(f"{PENDING}>") is True

    def test_a_pending_frame_is_not_the_answer(self):
        pipe = ResponsePipe()
        pipe.begin()
        pipe.feed(f"{PENDING}>")
        assert not pipe.satisfied
        pipe.feed(f"{REPLY}>")
        assert pipe.finish().block == REPLY

    def test_a_pending_frame_leaves_the_ledger_alone(self):
        # It consumed a prompt but promises another for the same answer, so the
        # number of real blocks still expected has not changed.
        pipe = ResponsePipe()
        pipe.begin()
        pipe.feed(f"{PENDING}>")
        assert pipe.owed == 1

    def test_pending_is_recognised_with_spaces_on(self):
        pipe = ResponsePipe()
        pipe.begin()
        assert pipe.feed("7F 22 78\r\n>") is True

    def test_bytes_that_are_not_the_nrcs_three_byte_shape_are_not_pending(self):
        # `7F` and `78` both present, but not as `7F <sid> 78`.
        pipe = ResponsePipe()
        pipe.begin()
        assert pipe.feed("627F78AA>") is False


class TestAbandonedCommands:
    def test_a_collection_that_consumed_no_prompt_is_dirty(self):
        pipe = ResponsePipe()
        read = collect(pipe, "62C00B")
        assert not read.clean
        assert pipe.dirty

    def test_an_incomplete_block_is_still_offered_as_the_best_candidate(self):
        # `send_uds`'s echo validation is the backstop; returning nothing would
        # discard a reply that merely lost its prompt.
        assert collect(ResponsePipe(), "62C00B").block == "62C00B"

    def test_an_abandoned_command_leaves_its_prompt_owed(self):
        pipe = ResponsePipe()
        collect(pipe, "62C00B")
        assert pipe.owed == 1

    def test_the_next_command_waits_for_two_prompts_and_names_the_late_reply(self):
        pipe = ResponsePipe()
        collect(pipe)  # a command that timed out having received nothing
        pipe.begin()
        assert pipe.owed == 2
        pipe.feed(f"{OTHER}>")
        assert not pipe.satisfied, "one prompt cannot settle a two-prompt debt"
        pipe.feed(f"{REPLY}>")
        read = pipe.finish()
        assert read.block == REPLY, "the newest block answers the current command"
        assert read.stale == [OTHER]

    def test_the_newest_block_wins_not_the_oldest(self):
        # The regression that motivated the ledger: returning the oldest buffered
        # block turned one late reply into a permanent one-command offset, serving
        # every PID's value under the next PID's name.
        pipe = ResponsePipe()
        collect(pipe)
        read = collect(pipe, f"{OTHER}>{REPLY}>")
        assert read.block == REPLY
        assert read.stale == [OTHER]

    def test_a_half_read_block_is_completed_by_the_next_read_not_torn(self):
        # The carry deliberately prefixes the next collection: a response whose
        # prompt merely arrived late must be reassembled whole, because splitting
        # it at the read boundary would corrupt both halves into garbage.
        pipe = ResponsePipe()
        collect(pipe, REPLY[:4])
        read = collect(pipe, f"{REPLY[4:]}>")
        assert read.block == REPLY
        assert read.clean

    def test_a_write_off_clears_the_debt_rather_than_inflating_it(self):
        # A frame lost on the link means a prompt that will never arrive. Carrying
        # it forever would push `owed` past the ceiling and kill the session.
        pipe = ResponsePipe()
        collect(pipe)
        collect(pipe, f"{REPLY}>")
        assert pipe.owed == 0
        assert not pipe.dirty


class TestResyncFallback:
    def test_a_clean_pipe_never_needs_a_resync(self):
        pipe = ResponsePipe()
        collect(pipe, f"{REPLY}>")
        assert not pipe.needs_resync()

    def test_accounting_handles_a_dirty_pipe_without_draining(self):
        pipe = ResponsePipe()
        collect(pipe, "partial")
        assert pipe.dirty and pipe.owed == 1
        assert not pipe.needs_resync()

    def test_a_dirty_pipe_with_nothing_owed_has_no_accounting_to_fall_back_on(self):
        # A stray banner or line noise: no owed prompt to count it against.
        pipe = ResponsePipe(dirty=True, owed=0)
        assert pipe.needs_resync()

    def test_a_backlog_that_stopped_shrinking_gives_up_on_accounting(self):
        pipe = ResponsePipe(dirty=True, owed=MAX_OWED_PROMPTS)
        assert pipe.needs_resync()

    def test_clearing_the_backlog_matches_a_drained_channel(self):
        pipe = ResponsePipe(dirty=True, owed=3, carry="leftover")
        pipe.clear_backlog()
        assert (pipe.dirty, pipe.owed, pipe.carry) == (False, 0, "")


class TestReset:
    def test_a_fresh_connection_owes_nothing_and_carries_nothing(self):
        pipe = ResponsePipe(dirty=True, owed=2, carry="leftover")
        pipe.begin()
        pipe.feed("half")
        pipe.reset()
        assert (pipe.owed, pipe.carry, pipe.dirty) == (0, "", False)
        assert collect(pipe, f"{REPLY}>") == PipeRead(block=REPLY, stale=[], clean=True)
