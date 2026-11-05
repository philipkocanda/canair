"""Per-PID poll backoff for the live monitor: slow down a read that holds up the rest.

Every transport reads one ECU at a time somewhere: the ELM327 pipe is strictly serial,
and the raw poller's tick only ends when its slowest ECU's queue has drained. So a PID
that times out (its ECU asleep, or the PID not answered in this vehicle state) or is
answered very slowly delays the next poll of every other PID, not just its own row.

:class:`PollBackoff` watches each ``(ECU, PID)`` request's polls and stretches its poll
period by a power-of-two *slowdown* while it is slow, then steps back once it answers
promptly again. Per PID, not per ECU: one dead PID must not slow its healthy neighbours,
and an ECU that is asleep slows every one of its PIDs anyway. A dependency-free leaf,
like :mod:`canair.live.monitor.schedule`, which applies the slowdown.

The wording a slowdown is shown and logged with lives here too, so the monitor's PID row,
status line and diagnostics log cannot describe it differently.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from canair.live.monitor.schedule import Key

# Mean seconds per read above which a PID counts as slow on a poll. A healthy read is
# tens of milliseconds on the raw path and well under that on an ELM327 with a learned
# frame count, so this only trips on timeouts and pathologically slow responses.
SLOW_READ_S = 0.5
# A PID that drops reads can cost seconds per poll, so the ceiling is high: at 32x a
# PID with a 5 s monitor period is read every 160 s.
MAX_SLOWDOWN = 32
# Consecutive slow polls before the first slowdown (one cold first read is not a
# pattern); once slowed, each further slow poll doubles it again. Consecutive healthy
# polls before the first step back (a PID answered only now and then keeps climbing);
# from then on each healthy poll halves it again, since a slowed PID is polled so
# rarely that a streak per step would keep a woken ECU's PIDs slow for many minutes.
ESCALATE_AFTER = 2
RECOVER_AFTER = 3

SlowKind = Literal["not answering", "slow"]


@dataclass(slots=True)
class PollCost:
    """What polling one PID cost on one tick.

    ``reads`` is the requests sent for it, ``seconds`` the time they took, and
    ``answered`` how many got a coherent reply — a payload or a negative response,
    since an NRC is a prompt answer, not a stall. A multi-DID batch is one request
    for each PID it answers, timed as the whole batch.
    """

    reads: int = 0
    seconds: float = 0.0
    answered: int = 0


@dataclass(frozen=True, slots=True)
class SlowReason:
    """Why a PID is slowed down: its kind, and the mean seconds per read measured."""

    kind: SlowKind
    per_read_s: float

    def describe(self) -> str:
        """``not answering``, or the measured time, e.g. ``0.8s per read``."""
        if self.kind == "not answering":
            return "not answering"
        return f"{self.per_read_s:.1f}s per read"


@dataclass(slots=True)
class _Health:
    slowdown: int = 1
    slow_streak: int = 0
    ok_streak: int = 0
    reason: SlowReason | None = None


def _classify(cost: PollCost) -> SlowReason | None:
    per_read = cost.seconds / cost.reads
    if cost.answered * 2 < cost.reads:
        return SlowReason("not answering", per_read)
    if per_read > SLOW_READ_S:
        return SlowReason("slow", per_read)
    return None


class PollBackoff:
    """Tracks which PIDs are slow and how much to stretch their poll periods."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._keys: dict[Key, _Health] = {}

    def observe(self, key: Key, cost: PollCost) -> int | None:
        """Fold one poll of ``key`` in; return its new slowdown when it changed."""
        if not self.enabled or cost.reads <= 0:
            return None
        h = self._keys.setdefault(key, _Health())
        reason = _classify(cost)
        if reason is not None:
            h.ok_streak = 0
            h.slow_streak += 1
            h.reason = reason
            needed = ESCALATE_AFTER if h.slowdown == 1 else 1
            if h.slow_streak >= needed and h.slowdown < MAX_SLOWDOWN:
                h.slow_streak = 0
                h.slowdown *= 2
                return h.slowdown
            return None
        h.slow_streak = 0
        if h.slowdown == 1:
            return None
        h.ok_streak += 1
        if h.ok_streak >= RECOVER_AFTER:
            h.slowdown //= 2
            return h.slowdown
        return None

    def slowdown(self, key: Key) -> int:
        h = self._keys.get(key)
        return h.slowdown if h is not None else 1

    def reason(self, key: Key) -> SlowReason | None:
        """Why ``key`` is slowed down (None when it is not)."""
        h = self._keys.get(key)
        return h.reason if h is not None and h.slowdown > 1 else None

    def slowed(self) -> dict[Key, int]:
        """Every slowed-down PID and its slowdown factor."""
        return {key: h.slowdown for key, h in self._keys.items() if h.slowdown > 1}

    def reset(self) -> None:
        """Forget every PID's history (after a reconnect, the old link's timings say nothing).

        The scheduler keeps its own copy of each slowdown, so restore those too (the
        monitor's ``reset_backoff`` does both): a PID forgotten here is never reported
        as back to 1, and would otherwise stay slow for the whole session.
        """
        self._keys.clear()


def describe_slowdown(factor: int, reason: SlowReason | None) -> str:
    """Short note for a PID's row, e.g. ``slow (not answering) · polled 4× less often``."""
    why = f" ({reason.describe()})" if reason is not None else ""
    return f"slow{why} · polled {factor}× less often"


def slowdown_log_message(what: str, factor: int, reason: SlowReason | None) -> str:
    """The diagnostics-log line for ``what`` (``SKM 22B002``) changing to ``factor``."""
    if factor > 1:
        why = f" ({reason.describe()})" if reason is not None else ""
        return f"{what} is slow{why}: polled {factor}x less often"
    return f"{what} answers promptly again: back to its normal poll rate"


def summarize_slowed(slowed: Mapping[Key, int]) -> str:
    """The status line's list of slowed PIDs, e.g. ``SKM:22B002,22B003÷4 BMS:2101÷2``.

    PIDs of one ECU slowed by the same factor share one entry, so an ECU that went to
    sleep reads as one item rather than one per PID.
    """
    groups: dict[tuple[str, int], list[str]] = {}
    for (ecu, pid), factor in sorted(slowed.items()):
        groups.setdefault((ecu, factor), []).append(pid)
    return " ".join(f"{ecu}:{','.join(pids)}÷{factor}" for (ecu, factor), pids in groups.items())
