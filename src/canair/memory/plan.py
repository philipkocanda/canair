"""Planning a memory image: flash size, aliasing, the survey's areas and the time it takes.

Pure: each function takes what the reads found and returns a decision, so the live run
(:mod:`canair.live.memory.image`) holds no policy and a fake ECU can drive it all offline.

- **Size.** The readable span is matched against common flash sizes (:data:`FLASH_SIZES`).
- **Aliasing.** An ECU that ignores its high address bits repeats one image across its
  space. For a candidate period ``S``, samples at ``A`` and ``A + S`` (and at the farthest
  ``A + k·S`` in the span) are compared. Identical samples at every point mean the ECU
  ignores the bits above ``S``. A sample erased on both sides is no evidence.
- **Survey.** A sample every stride is sorted into erased (all ``FF`` or all ``00``) or data,
  and each change between two samples is bisected to the block (:func:`bisect_mid`), since
  a stride alone misses where an area really ends.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from .job import DATA, ERASED, FULL, THOROUGH, Area, JobPlan

_KIB: Final = 1024
_MIB: Final = 1024 * _KIB

#: Common flash sizes: powers of two from 256 bytes (an alias of a small window) to 16 MiB,
#: and the 1.5, 3 and 6 MiB parts.
FLASH_SIZES: Final = tuple(
    sorted({1 << n for n in range(8, 25)} | {3 * _MIB // 2, 3 * _MIB, 6 * _MIB})
)
#: Sample points per alias candidate, and how many must carry data for a verdict.
ALIAS_POINTS: Final = 4
ALIAS_EVIDENCE: Final = 2
#: The survey's stride when the user names none.
DEFAULT_STRIDE: Final = 0x1000


def is_erased(data: bytes) -> bool:
    """All ``FF`` or all ``00``: what blank flash (or an unmapped address) reads as."""
    return bool(data) and (data.count(0xFF) == len(data) or data.count(0) == len(data))


def flash_size(span: int) -> int | None:
    """The smallest common flash size that holds ``span`` bytes."""
    return next((s for s in FLASH_SIZES if s >= span), None)


def alias_candidates(start: int, end: int, sample: int) -> list[int]:
    """The periods worth testing in ``start``-``end``: there is room for ``A + S + sample``."""
    span = end - start + 1
    return [s for s in FLASH_SIZES if s + sample <= span]


def alias_pairs(start: int, end: int, period: int, sample: int) -> list[tuple[int, int]]:
    """``(a, b)`` sample addresses whose bytes must match if the space repeats every ``period``."""
    pairs: list[tuple[int, int]] = []
    for i in range(ALIAS_POINTS):
        a = start + (i * period) // ALIAS_POINTS
        a = min(a, start + period - sample)
        far = a + ((end - sample + 1 - a) // period) * period
        for b in dict.fromkeys((a + period, far)):
            if b + sample - 1 <= end and b != a:
                pairs.append((a, b))
    return pairs


def aliased(samples: Iterable[tuple[bytes, bytes]]) -> bool:
    """Do the sample pairs prove the space repeats: all equal, and enough of them data?"""
    evidence = 0
    for a, b in samples:
        if a != b:
            return False
        if not is_erased(a):
            evidence += 1
    return evidence >= ALIAS_EVIDENCE


def survey_points(start: int, end: int, stride: int) -> list[int]:
    """One sample address every ``stride`` bytes from ``start``."""
    return list(range(start, end + 1, stride))


def changes(points: dict[int, bool]) -> list[tuple[int, int]]:
    """Each pair of neighbouring samples where erased (``True``) and data (``False``) change."""
    ordered = sorted(points)
    return [(a, b) for a, b in itertools.pairwise(ordered) if points[a] != points[b]]


def bisect_mid(lo: int, hi: int, block: int) -> int | None:
    """The block-aligned address halfway between two samples, or ``None`` once adjacent."""
    if hi - lo <= block:
        return None
    mid = lo + ((hi - lo) // 2 // block) * block
    return mid if lo < mid < hi else None


def areas(points: dict[int, bool], start: int, end: int) -> list[Area]:
    """Join the samples into areas: each sample stands for the addresses up to the next one."""
    ordered = sorted(p for p in points if start <= p <= end)
    out: list[Area] = []
    for i, a in enumerate(ordered):
        last = ordered[i + 1] - 1 if i + 1 < len(ordered) else end
        kind = ERASED if points[a] else DATA
        first = start if i == 0 else a
        if out and out[-1].kind == kind:
            out[-1] = Area(out[-1].start, last, kind)
        else:
            out.append(Area(first, last, kind))
    return out


@dataclass(frozen=True)
class Estimate:
    """How many reads a plan takes and how long, at the measured rate."""

    reads: int
    seconds: float
    full_bytes: int
    sampled_bytes: int


def estimate(plan: JobPlan, block: int, seconds_per_read: float) -> Estimate:
    """Reads and time for ``plan`` at ``block`` bytes a read (``N``/``N-1``) and the rate."""
    per_read = max(1.0, block - 0.5)
    reads = 0.0
    full = sampled = 0
    for extent in plan.extents():
        for lo, hi in plan.spans(extent):
            n = hi - lo + 1
            reads += -(-n // per_read)
            if extent.kind == FULL:
                full += n
            else:
                sampled += n
    reads *= 2 if plan.mode == THOROUGH else 1.1
    return Estimate(int(reads), reads * seconds_per_read, full, sampled)
