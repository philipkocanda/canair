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

from ..uds_parse import CAT_DECODE, CAT_DROP, CAT_STALE, UdsResponse

# Highest count we are willing to emit. The digit is a single hex nibble, so the
# wire format allows 1..15, but only 1..9 is verified on the STN2120 in the WiCAN
# Pro; whether it reads A..F as 10..15 is untested, and the classic WiCAN
# firmware's own emulation caps at 9 (`wican-fw/main/elm327.c:783-785`). A PID
# whose response needs more frames than this simply keeps the unoptimized path.
MAX_REQUESTABLE_FRAMES = 9

# Learned counts are per response address *and* request, because the same DID on
# two ECUs need not return the same number of frames.
CountKey = tuple[int | None, str]

# Response classifications that prove the pipe still holds bytes which are not
# this request's answer, so it must be realigned before anything is re-sent.
# ``drop`` is the undercount signature specifically: a short reassembly means the
# frames we did not wait for are still queued.
_DIRTY_PIPE = frozenset({CAT_DROP, CAT_STALE, CAT_DECODE})


@dataclass
class FrameCountCache:
    """Per-connection record of how many frames each request's answer occupies.

    Connection-scoped on purpose, via ``reset()`` on connect: a count measures one
    link, and a reconnect may have re-homed the session onto a different device
    entirely. A monitor cycle re-learns within one poll, so the cost of forgetting
    is one plain read per request.
    """

    enabled: bool = True
    _counts: dict[CountKey, int] = field(default_factory=dict)
    _opted_out: set[CountKey] = field(default_factory=set)

    def reset(self) -> None:
        """Forget every learned count and opt-out, keeping the enabled setting."""
        self._counts.clear()
        self._opted_out.clear()

    def count_for(self, key: CountKey) -> int | None:
        """Frames to request for ``key``, or None to send a plain request."""
        if not self.enabled:
            return None
        return self._counts.get(key)

    def learn(self, key: CountKey, frames: int | None) -> None:
        """Record the frame count observed on a complete digit-free reply.

        A count we cannot express, or an implausible one, opts the request out
        rather than being clamped: clamping would send a deliberate undercount.
        """
        if not self.enabled or key in self._opted_out or key in self._counts:
            return
        if frames is None or frames < 1:
            return
        if len(key[1]) % 2 != 0:
            # The digit is only distinguishable from data because it makes the
            # request odd-length. A request that is *already* odd would absorb
            # the nibble as a data byte and ask the ECU something else entirely.
            # UDS requests are whole bytes, so this should be unreachable —
            # refuse rather than trust that.
            self._opted_out.add(key)
            return
        if frames > MAX_REQUESTABLE_FRAMES:
            self._opted_out.add(key)
            return
        self._counts[key] = frames

    def opt_out(self, key: CountKey) -> None:
        """Stop using a digit for ``key`` for the rest of the session."""
        self._counts.pop(key, None)
        self._opted_out.add(key)

    def annotate(self, request: str, frames: int) -> str:
        """Append ``frames`` to ``request`` as the expected-response-count digit.

        The nibble is uppercase hex to match the request casing the adapter
        echoes, and lengthens the command by a character without adding a data
        byte, so the 7-byte request ceiling is unaffected.
        """
        return f"{request}{frames:X}"

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

    def command(self, request: str) -> str:
        """The request to send now — annotated with a digit, or plain."""
        self.requested = None if self._force_plain else self.cache.count_for(self.key)
        if self.requested is None:
            return request
        return self.cache.annotate(request, self.requested)

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
            if resp.get("ok") or resp.get("nrc") is not None:
                # The plain form answered where the digit did not, which convicts
                # the digit: a genuinely variable-length response, or a clone that
                # rejects the nibble outright. A still-silent ECU proves nothing
                # and keeps the optimization.
                self.cache.opt_out(self.key)
        elif requested is None and resp.get("ok"):
            # Learn only here. A digit-bearing reply carries exactly the count we
            # requested, so it can only ever confirm itself; and ``ok`` means
            # SID/echo validation and the ISO-TP declared-length check already
            # proved this one complete.
            self.cache.learn(self.key, resp.get("isotp_frame_count"))

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
