"""Evidence for how many CAN frames a PID's response occupies.

An ELM327 waits out its whole ``ATST`` budget (~614 ms at canair's and the WiCAN
firmware's shared ``ATST96``) on every request, because it cannot know whether
another response frame is still coming. Told the count up front it returns the
instant that many frames have arrived. The count is therefore worth *persisting*
into the profile — ``response_frames:`` on a PID — so a session starts fast
instead of re-learning it one slow read at a time, and so the generated WiCAN
AutoPID profile can carry it too.

Persisting it also raises the stakes, which is what this module is for. An
undercount leaves the response's tail queued, where it surfaces as the *next*
request's answer; canair repairs that (``Elm327Terminal._resync``) but the WiCAN
firmware does not (it accumulates into one static buffer it clears only *after* a
parse). So a count is written only once the wire has *proved* it, and this ledger
is the bookkeeping for that proof. Two kinds of proof exist, because the two
transport families differ in what they can offer:

- **A held digit** (ELM327 transports): a request that actually carried the count
  came back whole. That is a direct positive test of the exact claim being made,
  so one suffices.
- **Repetition** (the raw ``slcan-tcp`` path): canair runs ISO-TP itself, so there
  is no adapter to hint and nothing to test against. The bar is instead several
  agreeing complete responses with no disagreement.

Either way a single *disagreement* is permanently disqualifying. A response whose
length varies has no count to persist, and the direction that matters is removal:
a stale count is the one that corrupts readings, so a contradicted key is retired
and its stored value cleared.

Deliberately transport-neutral and dependency-free — the ELM327 digit policy
(what to *ask for*) lives in ``canlib/transport/elm327_frame_count.py``, while
this records only what the wire *showed*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Keyed by the arbitration id the request was sent to *and* the request itself:
# the same DID on two ECUs need not answer in the same number of frames.
CountKey = tuple[int | None, str]

# One held digit is a direct test of the count, so it is enough on its own.
CONFIRMATIONS_REQUIRED = 1

# Absent any way to test the count, only agreement across reads supports it. Three
# is a compromise: a genuinely variable response usually varies within a single
# monitor session, and demanding more would stop most PIDs ever being persisted
# from the default transport.
OBSERVATIONS_REQUIRED = 3


def frames_for_payload(length: int) -> int:
    """CAN frames a classic-CAN ISO-TP message of ``length`` data bytes occupies.

    A Single Frame spends one byte on PCI, so up to 7 bytes fit. Beyond that a
    First Frame spends two and carries 6, and each Consecutive Frame spends one and
    carries 7. Classic CAN only — a CAN-FD frame carries up to 64 bytes, so a
    CAN-FD ECU's count cannot be derived this way and must be observed instead.
    """
    if length <= 7:
        return 1
    return 1 + -(-(length - 6) // 7)


@dataclass
class FrameCountRecord:
    """What is known about one request's response length."""

    frames: int
    observations: int = 0
    confirmations: int = 0
    conflict: bool = False

    @property
    def confirmed(self) -> bool:
        if self.conflict:
            return False
        return (
            self.confirmations >= CONFIRMATIONS_REQUIRED
            or self.observations >= OBSERVATIONS_REQUIRED
        )


@dataclass
class FrameCountLedger:
    """Per-session evidence about response frame counts, per request.

    Session-scoped: the ledger accumulates what *this* session saw, and the
    command layer decides at teardown what has earned a place in the profile. A
    count seeded from the profile is not evidence — it becomes so only when this
    session confirms it, which is why seeding does not pre-populate a record.
    """

    _records: dict[CountKey, FrameCountRecord] = field(default_factory=dict)

    def observe(self, key: CountKey, frames: int | None) -> None:
        """Record a complete response of ``frames`` frames, digit-free."""
        rec = self._entry(key, frames)
        if rec is None or rec.conflict:
            return
        rec.observations += 1

    def confirm(self, key: CountKey, frames: int) -> None:
        """Record that a request carrying a count of ``frames`` came back whole."""
        rec = self._entry(key, frames)
        if rec is None or rec.conflict:
            return
        rec.confirmations += 1

    def mark_conflict(self, key: CountKey) -> None:
        """Permanently disqualify ``key`` — its response length is not fixed."""
        rec = self._records.get(key)
        if rec is None:
            # Nothing was observed, but the disqualification still has to be
            # reportable, so any count already stored in the profile gets cleared.
            self._records[key] = FrameCountRecord(frames=0, conflict=True)
            return
        rec.conflict = True

    def clear(self) -> None:
        """Forget everything (a reconnect re-homed the session onto a new link)."""
        self._records.clear()

    def confirmed(self) -> dict[CountKey, int]:
        """Counts this session proved, ready to persist."""
        return {k: r.frames for k, r in self._records.items() if r.confirmed}

    def retired(self) -> set[CountKey]:
        """Keys this session disqualified, whose stored count must be cleared."""
        return {k for k, r in self._records.items() if r.conflict}

    def record(self, key: CountKey) -> FrameCountRecord | None:
        """The raw record for ``key`` — for reporting and tests."""
        return self._records.get(key)

    def _entry(self, key: CountKey, frames: int | None) -> FrameCountRecord | None:
        """The record for ``key``, creating it, or flagging a disagreement."""
        if frames is None or frames < 1:
            return None
        rec = self._records.get(key)
        if rec is None:
            rec = self._records[key] = FrameCountRecord(frames=frames)
        elif rec.frames != frames:
            rec.conflict = True
        return rec
