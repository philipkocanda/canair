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

# Highest count we are willing to emit. The digit is a single hex nibble, so the
# wire format allows 1..15, but only 1..9 is verified on the STN2120 in the WiCAN
# Pro; whether it reads A..F as 10..15 is untested, and the classic WiCAN
# firmware's own emulation caps at 9 (`wican-fw/main/elm327.c:783-785`). A PID
# whose response needs more frames than this simply keeps the unoptimized path.
MAX_REQUESTABLE_FRAMES = 9

# Learned counts are per response address *and* request, because the same DID on
# two ECUs need not return the same number of frames.
CountKey = tuple[int | None, str]


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
