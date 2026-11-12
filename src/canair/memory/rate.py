"""The rate controller of ``canair memory image``: the next block size and throttle.

A pure function of the events of the last reads (:func:`advance`). The rules:

- **The ceiling is the user's.** The block never exceeds the smaller of the ECU's recorded
  ``max_block`` and ``--block-limit`` (:func:`ceiling`). Raising ``max_block`` stays the
  scan's block search.
- **Prefer the digit.** On an ELM327 transport the preferred block is the largest whose
  reply fits ``MAX_REQUESTABLE_FRAMES`` CAN frames, since a larger reply cannot carry the
  expected-frame-count digit and every read then waits out the adapter's ``ATST`` budget.
  Elsewhere the ceiling is preferred (:func:`preferred_block`).
- **Back off fast, recover slow.** Two errors within :data:`ERROR_WINDOW` reads halve the
  block (to :data:`FLOOR`) and add :data:`THROTTLE_STEP_MS` of throttle;
  :data:`RECOVER_AFTER` clean reads take one step back up.
- **Silence is never a rate signal.** A silent read goes to the liveness check, and the
  controller never hears of it. A ``retry`` (a lost reply of a size the ECU is known to
  deliver, read again) is neither an error nor a clean read.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from canair.transport.elm327.frame_count import MAX_REQUESTABLE_FRAMES
from canair.uds.frame_counts import frames_for_payload

FLOOR: Final = 16
ERROR_WINDOW: Final = 50
RECOVER_AFTER: Final = 500
THROTTLE_STEP_MS: Final = 20

#: The read outcomes that count as errors (the reader's ``reread``, ``halving``, ``drop``).
ERRORS: Final = frozenset({"reread", "halving", "drop"})
#: The reader's ``retry``: counted as a read, and nothing else.
NEUTRAL: Final = frozenset({"retry"})


def ceiling(max_block: int | None, block_limit: int | None, format_max: int) -> int:
    """The largest block the run may ever ask for."""
    limits = [n for n in (max_block, block_limit) if n]
    return max(1, min([format_max, *limits]))


def digit_block(top: int) -> int:
    """The largest block up to ``top`` whose reply (``63`` and the bytes) fits the digit."""
    size = top
    while size > 1 and frames_for_payload(1 + size) > MAX_REQUESTABLE_FRAMES:
        size -= 1
    return size


def preferred_block(top: int, *, digit: bool) -> int:
    """The block a clean run reads with under ``top``: the digit's, or ``top`` itself."""
    return digit_block(top) if digit else top


@dataclass(frozen=True)
class Rate:
    """The block and throttle now, and the recent history they were chosen from."""

    block: int
    preferred: int
    throttle_ms: int = 0
    clean: int = 0  # reads since the last change, with no error
    errors: tuple[int, ...] = ()  # the read counter at each recent error
    count: int = 0  # reads seen

    @property
    def floor(self) -> int:
        return min(FLOOR, self.preferred)


def start_rate(preferred: int, *, block: int | None = None, throttle_ms: int = 0) -> Rate:
    """A run's first rate; a resume passes the block it had reached, never more."""
    first = preferred if block is None else max(1, min(block, preferred))
    return Rate(first, preferred, throttle_ms)


def advance(rate: Rate, event: str) -> Rate:
    """The rate after one read whose outcome is ``event``."""
    count = rate.count + 1
    if event in NEUTRAL:
        return replace(rate, count=count)
    if event in ERRORS:
        recent = (*tuple(n for n in rate.errors if count - n < ERROR_WINDOW), count)
        if len(recent) >= 2:
            return replace(
                rate,
                block=min(rate.block, max(rate.floor, rate.block // 2)),
                throttle_ms=rate.throttle_ms + THROTTLE_STEP_MS,
                errors=(),
                clean=0,
                count=count,
            )
        return replace(rate, errors=recent, clean=0, count=count)
    clean = rate.clean + 1
    if clean >= RECOVER_AFTER and (rate.block < rate.preferred or rate.throttle_ms):
        return replace(
            rate,
            block=min(rate.preferred, rate.block * 2),
            throttle_ms=max(0, rate.throttle_ms - THROTTLE_STEP_MS),
            clean=0,
            count=count,
        )
    return replace(rate, clean=clean, count=count)
