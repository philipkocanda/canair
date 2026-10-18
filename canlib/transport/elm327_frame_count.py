"""Learned ELM327 expected-response-count digits.

An ELM327 data request whose hex length is *odd* has its final nibble read as the
number of response frames to wait for, rather than as data. Supplying it lets the
adapter return the instant that many frames have arrived instead of sitting out
its ``ATST`` ECU-wait budget, which is the difference between ~206 ms and ~53 ms
per read on a WiCAN Pro (measurements in
``plans/2026-08-09-wican-ws-throughput-ceiling.md``).

The digit is only safe if it is *correct*. Ask for fewer frames than the ECU
sends and the adapter hands back a truncated reply and leaves the remainder in
the pipe, where it surfaces as the *next* request's answer — the desync
``Elm327Terminal._resync`` exists to repair. So the count is never guessed:

- It is learned from an ordinary digit-free read, and **only** from one. A
  digit-bearing reply contains exactly as many frames as we asked for, so
  learning from it would confirm its own premise and could freeze a truncation
  in place.
- It is learned only from a reply that passed SID/echo validation and the
  ISO-TP declared-length check, i.e. one already known to be complete.
- Every digit-bearing reply is checked against the count that was requested, and
  a PID whose response length turns out to vary is opted out for the rest of the
  session (see ``Elm327Terminal.send_uds``).

This module owns the policy and the bookkeeping; the terminal owns the I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..frame_counts import CountKey, FrameCountLedger
from ..uds_parse import CAT_DECODE, CAT_DROP, CAT_STALE, UdsResponse

# Highest count we are willing to emit. The digit is a single hex nibble, so the
# wire format allows 1..15, but only 1..9 is verified on the STN2120 in the WiCAN
# Pro; whether it reads A..F as 10..15 is untested, and the classic WiCAN
# firmware's own emulation caps at 9 (`wican-fw/main/elm327.c:783-785`). A PID
# whose response needs more frames than this simply keeps the unoptimized path.
MAX_REQUESTABLE_FRAMES = 9

# ``CountKey`` is defined with the ledger, in ``canlib/frame_counts.py``: the key
# identifies a *response length*, which both transport families observe, while only
# this module decides what to ask for. Re-exported because the terminals key their
# call sites off it.
__all__ = [
    "MAX_REQUESTABLE_FRAMES",
    "CountAttempt",
    "CountKey",
    "CountVerdict",
    "FrameCountCache",
    "annotate_request",
    "digit_held",
    "requestable",
]


# Response classifications that prove the pipe still holds bytes which are not
# this request's answer, so it must be realigned before anything is re-sent.
# ``drop`` is the undercount signature specifically: a short reassembly means the
# frames we did not wait for are still queued.
_DIRTY_PIPE = frozenset({CAT_DROP, CAT_STALE, CAT_DECODE})


def requestable(request: str, frames: int | None) -> bool:
    """Can ``frames`` be appended to ``request`` as an expected-response digit?

    The single home for the wire rules, so the live transport and the generated
    AutoPID profile cannot disagree about which counts are safe to ask for:

    - The request must be **even-length**. The digit is only distinguishable from
      data because it makes the command odd-length; appending to an already-odd
      request would have the adapter absorb the nibble as a data byte and ask the
      ECU something else entirely.
    - The count must fit ``1..MAX_REQUESTABLE_FRAMES``. Never clamped — a clamp is
      a deliberate undercount, and an undercount leaves the response's tail queued
      to answer the *next* request.
    """
    if frames is None or frames < 1 or frames > MAX_REQUESTABLE_FRAMES:
        return False
    return len(request) % 2 == 0


def annotate_request(request: str, frames: int) -> str:
    """``request`` with ``frames`` appended as the expected-response-count digit.

    Uppercase hex, to match the request casing the adapter echoes. Lengthens the
    command by a character without adding a data byte, so the 7-byte request
    ceiling is unaffected.
    """
    return f"{request}{frames:X}"


@dataclass
class FrameCountCache:
    """Per-connection record of how many frames each request's answer occupies.

    The *digit policy*: which requests get a count appended, and what to do when
    one turns out to be wrong. Connection-scoped on purpose, via ``reset()`` on
    connect: a count measures one link, and a reconnect may have re-homed the
    session onto a different device entirely. A monitor cycle re-learns within one
    poll, so the cost of forgetting is one plain read per request — and a profile
    that already knows the count skips even that, via ``seed()``.

    The durable *evidence* is kept separately, in ``ledger``, which survives being
    read at teardown and does not care which transport produced it.
    """

    enabled: bool = True
    ledger: FrameCountLedger = field(default_factory=FrameCountLedger)
    _counts: dict[CountKey, int] = field(default_factory=dict)
    _opted_out: set[CountKey] = field(default_factory=set)
    _seeded: dict[CountKey, int] = field(default_factory=dict)

    def seed(self, counts: dict[CountKey, int]) -> None:
        """Prime the cache from the profile's ``response_frames:`` values.

        Kept apart from the learned counts so a reconnect can re-apply them: the
        profile's claim outlives the link, unlike a value measured on it. A wrong
        seed is safe — it takes the same retry-plain/realign path as a wrong
        learned count, costing latency but never a reading, and is retired from the
        profile only once a plain reply proves a genuinely different length.
        """
        self._seeded = {k: v for k, v in counts.items() if requestable(k[1], v)}
        self._apply_seed()

    def reset(self) -> None:
        """Forget every learned count and opt-out, then re-apply the seed."""
        self._counts.clear()
        self._opted_out.clear()
        self._apply_seed()

    def _apply_seed(self) -> None:
        for key, frames in self._seeded.items():
            self._counts.setdefault(key, frames)

    def is_seeded(self, key: CountKey) -> bool:
        """Did ``key``'s current count come from the profile rather than the wire?"""
        return key in self._seeded

    def count_for(self, key: CountKey) -> int | None:
        """Frames to request for ``key``, or None to send a plain request."""
        if not self.enabled:
            return None
        return self._counts.get(key)

    def observe(self, key: CountKey, frames: int | None) -> None:
        """Record a complete digit-free response as evidence.

        Separate from :meth:`learn` because the two answer different questions.
        ``learn`` decides what to ask for and so stops caring once it has an answer
        (or has opted out); the ledger wants every reading, which is what lets a
        response too long to request — the Ioniq's 13-frame ``21F2`` — still earn
        its true count in the profile, and lets a later disagreement retire a key
        the digit policy had already settled.
        """
        self.ledger.observe(key, frames)

    def learn(self, key: CountKey, frames: int | None) -> None:
        """Record the frame count observed on a complete digit-free reply.

        A count we cannot express, or an implausible one, opts the request out
        rather than being clamped: clamping would send a deliberate undercount.
        """
        if not self.enabled or key in self._opted_out or key in self._counts:
            return
        if frames is None or frames < 1:
            return
        if not requestable(key[1], frames):
            self._opted_out.add(key)
            return
        self._counts[key] = frames

    def disable_digit(self, key: CountKey) -> None:
        """Stop appending a digit for ``key`` for the rest of *this session*.

        A purely session-local safety measure with no bearing on the profile: the
        digit is risky on a link that just desynced, but a dropped frame is not
        evidence that the response length varies. The seed is left intact, so a
        reconnect (a fresh link, via :meth:`reset`) re-applies it and gives the
        digit another chance; and plain reads keep feeding the ledger
        (:meth:`observe` ignores the opt-out), so a count that was merely unlucky
        can still earn its place back.
        """
        self._counts.pop(key, None)
        self._opted_out.add(key)

    def opt_out(self, key: CountKey) -> None:
        """Convict ``key``: disable the digit, drop the seed, and retire the count.

        Reserved for proof that the response length is not the fixed thing a stored
        count claims — a plain reply of a genuinely *different* length. Dropping the
        seed stops a reconnect resurrecting it, and the ledger conflict clears the
        stored value from the profile. A transient drop (which looks identical at
        the moment of failure) must never reach here; that path stops at
        :meth:`disable_digit`.
        """
        self.disable_digit(key)
        self._seeded.pop(key, None)
        self.ledger.mark_conflict(key)

    def confirm(self, key: CountKey, frames: int) -> None:
        """Record that a request carrying ``frames`` came back whole."""
        self.ledger.confirm(key, frames)

    def annotate(self, request: str, frames: int) -> str:
        """Append ``frames`` to ``request`` as the expected-response-count digit."""
        return annotate_request(request, frames)

    def attempt(self, key: CountKey) -> CountAttempt:
        """Start the decision procedure for one request on ``key``."""
        return CountAttempt(self, key)


@dataclass(frozen=True)
class CountVerdict:
    """What the caller should do with the reply to a request it just sent.

    ``retry_plain`` asks for one more exchange without the digit; it never
    consumes the caller's own retry budget, because the extra round trip is the
    optimization's cost to bear, not the reader's. ``realign`` means the reply
    proved bytes are still queued, so the pipe must be repaired *before* the
    retry — a plain re-send into an offset pipe just draws the next stale reply.
    """

    retry_plain: bool = False
    realign: bool = False
    note: str = ""


@dataclass
class CountAttempt:
    """The expected-response-count decision procedure for one ``send_uds`` call.

    Holds the retry state that makes the optimization safe, so the terminal is
    left with the I/O. Call :meth:`command` to form each request and
    :meth:`verdict` with the parsed reply; when the verdict does not ask for a
    retry the attempt has settled and the cache has been updated.

    The invariant worth protecting is *attribution*: a digit-bearing failure is
    only ever blamed on the digit when the plain form of the same request then
    works. Opting out on the failure alone would deoptimize every PID that
    happened to be read while its ECU was briefly silent.
    """

    cache: FrameCountCache
    key: CountKey
    requested: int | None = field(default=None, init=False)
    _force_plain: bool = field(default=False, init=False)
    _diagnosing: bool = field(default=False, init=False)
    _suspect: int | None = field(default=None, init=False)

    def command(self, request: str) -> str:
        """The request to send now — annotated with a digit, or plain."""
        self.requested = None if self._force_plain else self.cache.count_for(self.key)
        if self.requested is None:
            return request
        return self.cache.annotate(request, self.requested)

    @property
    def was_seeded(self) -> bool:
        """Did the digit this attempt used come from the profile, not this session?

        Only for reporting. A seeded count that fails is worth naming separately,
        because it means a stored definition is wrong — not merely that a fresh
        measurement was unlucky.
        """
        return self.cache.is_seeded(self.key)

    def verdict(self, resp: UdsResponse) -> CountVerdict:
        """Judge ``resp``, settling the cache unless a plain retry is needed."""
        requested = self.requested
        if requested is not None and not digit_held(resp, requested):
            # The digit is implicated: either the reply is short of what we asked
            # for, or asking changed the outcome. Retry plain before concluding
            # anything — it costs one exchange and guarantees the optimization
            # can never lose a read.
            self._force_plain = True
            self._diagnosing = True
            self._suspect = requested
            return CountVerdict(
                retry_plain=True,
                realign=resp.get("error_kind") in _DIRTY_PIPE,
                note=(
                    f"asked {requested} frame(s), got {resp.get('isotp_frame_count')}"
                    f" ({resp.get('error', 'complete')}) — retrying plain"
                ),
            )

        if self._diagnosing:
            self._diagnosing = False
            plain = resp.get("isotp_frame_count")
            if resp.get("ok") and plain is not None and plain != self._suspect:
                # The plain retry returned a COMPLETE response of a *different*
                # length than the digit asked for. That — and only that — proves
                # the stored count wrong (a genuinely variable-length response, or
                # a clone that answers a shorter form when the nibble is rejected),
                # so it is retired and the profile value cleared.
                self.cache.opt_out(self.key)
            elif resp.get("ok") or resp.get("nrc") is not None:
                # The ECU answered, but not with a different length. A plain reply
                # of the SAME length means the digit-bearing read merely lost a
                # frame in transit — a dirty pipe the resync already repaired, not
                # a variable response — and an NRC proves nothing about the
                # positive response's length. Stop the digit for this session as a
                # precaution, but keep the durable count: deleting a verified
                # profile value over a transient drop is the bug this guards.
                # Continued silence falls through untouched — it proves nothing, so
                # the digit stays for the next poll.
                self.cache.disable_digit(self.key)
        elif requested is not None:
            # A request that *carried* the count came back whole. This is the only
            # direct test of the count there is, and passing it is what promotes the
            # count to something worth writing into the profile. An NRC does not
            # count: `digit_held` accepts one because nothing is left in the pipe,
            # but a refusal occupies fewer frames and so proves nothing about the
            # positive response's length.
            if resp.get("ok"):
                self.cache.confirm(self.key, requested)
        elif resp.get("ok"):
            # Learn only here. A digit-bearing reply carries exactly the count we
            # requested, so it can only ever confirm itself; and ``ok`` means
            # SID/echo validation and the ISO-TP declared-length check already
            # proved this one complete.
            frames = resp.get("isotp_frame_count")
            self.cache.observe(self.key, frames)
            self.cache.learn(self.key, frames)

        return CountVerdict()


def digit_held(resp: UdsResponse, requested: int) -> bool:
    """Did a request carrying an expected-response-count digit come back whole?

    A negative response counts as held: the ECU gave a complete, valid answer that
    simply occupies fewer frames than the positive one the count was measured from,
    so the digit did not fail — nothing is left in the pipe. Treating an NRC as a
    failure would opt out every PID an ECU refuses while a session is closed, which
    on most cars is most of them.

    Anything else (a short reassembly, a reply the adapter never produced, a clone
    answering ``?`` to the unexpected nibble) is treated as the digit's fault until
    a plain retry proves otherwise.
    """
    if resp.get("nrc") is not None:
        return True
    return bool(resp.get("ok")) and resp.get("isotp_frame_count") == requested
