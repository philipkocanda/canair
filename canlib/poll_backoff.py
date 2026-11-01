"""Per-ECU poll backoff for the live monitor: slow down an ECU that holds up the rest.

Every transport reads one ECU at a time somewhere: the ELM327 pipe is strictly serial,
and the raw poller's tick only ends when its slowest ECU's queue has drained. So an ECU
that times out (asleep, or not answering in this vehicle state) or answers very slowly
delays the next poll of every other ECU, not just its own rows.

:class:`EcuBackoff` watches each ECU's polls and stretches its poll periods by a
power-of-two *slowdown* while it is slow, then steps back once it answers promptly
again. A dependency-free leaf, like :mod:`canlib.poll_schedule`, which applies the
slowdown.
"""

from __future__ import annotations

from dataclasses import dataclass

# Mean seconds per read above which an ECU counts as slow on a poll. A healthy read is
# tens of milliseconds on the raw path and well under that on an ELM327 with a learned
# frame count, so this only trips on timeouts and pathologically slow responses.
SLOW_READ_S = 0.5
# An ECU that drops reads can cost seconds per poll, so the ceiling is high: at 32x a
# PID with a 5 s monitor period is read every 160 s.
MAX_SLOWDOWN = 32
# Consecutive slow polls before the first slowdown (one cold first read is not a
# pattern); once slowed, each further slow poll doubles it again. Consecutive healthy
# polls before each step back.
ESCALATE_AFTER = 2
RECOVER_AFTER = 3


@dataclass
class PollCost:
    """What polling one ECU cost on one tick.

    ``reads`` is the requests sent to it, ``seconds`` the time they took, and
    ``answered`` how many got a coherent reply — a payload or a negative response,
    since an NRC is a prompt answer, not a stall.
    """

    reads: int = 0
    seconds: float = 0.0
    answered: int = 0


@dataclass
class _EcuHealth:
    slowdown: int = 1
    slow_streak: int = 0
    ok_streak: int = 0
    reason: str = ""


class EcuBackoff:
    """Tracks which ECUs are slow and how much to stretch their poll periods."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._ecus: dict[str, _EcuHealth] = {}

    def observe(self, ecu: str, cost: PollCost) -> int | None:
        """Fold one poll of ``ecu`` in; return its new slowdown when it changed."""
        if not self.enabled or cost.reads <= 0:
            return None
        h = self._ecus.setdefault(ecu, _EcuHealth())
        per_read = cost.seconds / cost.reads
        if cost.answered * 2 < cost.reads:
            reason = "not answering"
        elif per_read > SLOW_READ_S:
            reason = f"{per_read:.1f}s per read"
        else:
            reason = ""
        if reason:
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
            h.ok_streak = 0
            h.slowdown //= 2
            return h.slowdown
        return None

    def slowdown(self, ecu: str) -> int:
        h = self._ecus.get(ecu)
        return h.slowdown if h is not None else 1

    def reason(self, ecu: str) -> str:
        """Why ``ecu`` is slowed down (empty when it is not)."""
        h = self._ecus.get(ecu)
        return h.reason if h is not None and h.slowdown > 1 else ""

    def slowed(self) -> dict[str, int]:
        """Every slowed-down ECU and its slowdown factor."""
        return {ecu: h.slowdown for ecu, h in self._ecus.items() if h.slowdown > 1}

    def reset(self) -> None:
        """Forget every ECU's history (after a reconnect, the old link's timings say nothing)."""
        self._ecus.clear()


def describe_slowdown(factor: int, reason: str) -> str:
    """Short note for an ECU's header, e.g. ``slow (not answering) · polled 4× less often``."""
    why = f" ({reason})" if reason else ""
    return f"slow{why} · polled {factor}× less often"
