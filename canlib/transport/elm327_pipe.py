"""Attributing ``>``-terminated ELM327 replies to the commands that earned them.

An ELM327 emits exactly one ``>`` prompt per response it sends, and UDS carries no
transaction id — a SID+DID echo identifies a request's *content*, so a reply that
is offset by exactly one poll cycle, or any single-PID poll, is invisible to
validation. Counting prompts is therefore the primary defence against a
desynchronised pipe, and this module is that counter: a small state machine that
turns a stream of adapter bytes into "the block that answers *this* command" plus
"blocks that answer commands nobody is waiting for any more".

It deliberately performs no I/O. :class:`~canlib.transport.elm327_terminal.Elm327Terminal`
owns the channel and the clock; this owns only the ledger, which is what makes the
accounting testable without a socket, a timeout, or an event loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# UDS ResponsePending negative response: `7F <sid> 78` ("request received,
# response pending"). Matched against the whitespace-stripped ELM327 text, so it
# works with `ATS1` (spaces on) and `ATS0` alike. Anchoring to the full 3-byte
# shape — rather than testing for `7F` and `78` separately — keeps a positive
# response that merely *contains* those bytes from being mistaken for the NRC.
# The raw-CAN counterpart is `uds_raw.is_response_pending`.
PENDING_RE = re.compile(r"7F[0-9A-Fa-f]{2}78")

# The ELM327's reply delimiter, and the only per-exchange framing signal the
# protocol offers (see the module docstring).
PROMPT = ">"

# Ceiling on how many unanswered prompts we keep tracking. Reached only when the
# adapter owes replies it will never send, at which point the count is no longer
# evidence of anything and a drain-and-probe resync is the honest fallback.
MAX_OWED_PROMPTS = 4


def compact(text: str) -> str:
    """Strip whitespace/line breaks, so hex tests work with ``ATS1`` and ``ATS0``."""
    return text.replace(" ", "").replace("\r", "").replace("\n", "")


def split_prompt_blocks(text: str) -> tuple[list[str], str]:
    """Split adapter output into completed ``>``-terminated blocks and a remainder.

    Each returned block is one complete adapter response with its prompt removed;
    the remainder is whatever trailed the last prompt (an incomplete block, kept
    for the next read rather than discarded — see :attr:`ResponsePipe.carry`).
    """
    parts = text.split(PROMPT)
    return parts[:-1], parts[-1]


@dataclass(frozen=True)
class PipeRead:
    """The outcome of collecting one command's reply."""

    # The block to treat as this command's answer. When nothing completed, this is
    # the incomplete text — the best available candidate, with `send_uds`'s echo
    # validation as the backstop.
    block: str
    # Blocks belonging to earlier, abandoned commands. The caller reports these as
    # discarded stale replies; they are already removed from the ledger.
    stale: list[str]
    # True when a block was consumed prompt-and-all, so nothing is half-read.
    clean: bool


@dataclass
class ResponsePipe:
    """The prompt ledger for one ELM327 connection.

    ``owed`` is how many ``>`` prompts the adapter still owes: one per command
    sent, plus one more for every interim ResponsePending frame, minus those
    consumed. It normally sits at 1 for the duration of a command, so a reply is
    returned the instant its prompt lands (no added latency). It only exceeds 1
    after a command was abandoned mid-flight, and *that* is what lets the next
    read positively identify the late reply as somebody else's: wait for the 2nd
    prompt, discard the 1st.
    """

    # How many prompts the adapter still owes (see the class docstring).
    owed: int = 0
    # Bytes that trailed the returned block's prompt, so a block is never torn
    # across two reads.
    carry: str = ""
    # Set when a collection ended WITHOUT consuming a prompt (a timeout
    # mid-response): the adapter may still emit trailing frames plus a late
    # prompt, which would otherwise leak into — and corrupt — the next command's
    # response.
    dirty: bool = False
    # Collection-scoped: the unparsed tail and the completed blocks harvested so
    # far. Reset by `begin()`, consumed by `finish()`.
    _buf: str = ""
    _done: list[str] = field(default_factory=list)

    def reset(self) -> None:
        """Forget everything — a fresh connection owes nothing and carries nothing."""
        self.owed = 0
        self.carry = ""
        self.dirty = False
        self._buf = ""
        self._done = []

    def needs_resync(self) -> bool:
        """Is a drain-and-probe resync the only way to trust the pipe again?

        A dirty pipe is normally resolved by the accounting itself: the abandoned
        command's prompt is still owed, so the next command waits for *its* prompt
        and discards the late reply positively, with no timing guess. Fall back to
        a resync only when accounting has nothing to work with — no owed prompt to
        count (so a stray banner or garbage is the likely content), or a backlog
        that has stopped shrinking and is therefore no longer evidence of anything.
        """
        return self.dirty and not 0 < self.owed < MAX_OWED_PROMPTS

    def clear_backlog(self) -> None:
        """Zero the ledger after the channel has been drained.

        The drain discarded whatever the adapter still owed, so the ledger must be
        zeroed with it — otherwise the following probe would wait for prompts that
        were just thrown away.
        """
        self.dirty = False
        self.owed = 0
        self.carry = ""

    def begin(self) -> bool:
        """Open a collection for a command just sent; True if a pending frame arrived.

        The carry may already hold this command's whole reply, so this harvests
        before the caller waits on the channel at all.
        """
        self.owed += 1
        self._buf = self.carry
        self.carry = ""
        self._done = []
        return self._harvest()

    def feed(self, msg: str) -> bool:
        """Add received text; True if an interim ResponsePending frame arrived.

        A True return means the ECU asked for more time, and the caller should
        extend its deadline rather than counting the wait against the request.
        """
        self._buf += msg
        return self._harvest()

    @property
    def satisfied(self) -> bool:
        """Have as many blocks completed as there are prompts owed?"""
        return len(self._done) >= self.owed

    def finish(self) -> PipeRead:
        """Close the collection, selecting this command's answer.

        Every block but the last belongs to an earlier, abandoned command, so the
        newest is the answer. That holds both ways: when the owed count is
        satisfied the last block is *provably* ours, and when the deadline expired
        first (an owed reply the adapter will never send — a frame lost on the
        link) it is still the best candidate. Either beats returning the *oldest*
        buffered block, which is how a single late reply used to become a permanent
        one-command offset that served every PID's value under the next PID's name.
        """
        clean = bool(self._done)
        # A block was consumed prompt-and-all, so nothing is half-read; the carry
        # keeps any bytes that trailed it rather than tearing the next block.
        self.dirty = not clean
        self.carry = self._buf
        if clean:
            # Whatever else was owed has now either been consumed or written off
            # (see the block-selection note above); starting the next command from
            # a clean ledger is what keeps one lost frame from inflating the count
            # forever and turning it into a dead session.
            self.owed = 0
        # Otherwise the ledger stands: this command's prompt is still outstanding,
        # so the *next* command waits for two and can name the late reply as
        # somebody else's instead of guessing. `MAX_OWED_PROMPTS` bounds the
        # optimism — past that `needs_resync()` gives up on accounting.
        return PipeRead(
            block=self._done[-1] if self._done else self._buf,
            stale=self._done[:-1],
            clean=clean,
        )

    def _harvest(self) -> bool:
        """Move completed blocks out of the buffer; True if a pending frame arrived."""
        blocks, self._buf = split_prompt_blocks(self._buf)
        saw_pending = False
        for block in blocks:
            if PENDING_RE.search(compact(block)):
                # UDS ResponsePending (7F xx 78) — an interim "still working" ack
                # which the adapter terminates with its own prompt, NOT the answer.
                # It leaves the ledger alone on purpose: it consumed a prompt but
                # promises another for the *same* answer, so the number of real
                # blocks still expected has not changed. All it buys the ECU is
                # more time. (The raw path does the same in
                # `uds_raw.is_response_pending`.)
                saw_pending = True
                continue
            self._done.append(block)
        return saw_pending
