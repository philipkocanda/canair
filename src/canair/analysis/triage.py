#!/usr/bin/env python3
"""Byte triage — cheap first-pass classification of a payload's data bytes.

The "what *kind* of byte is this?" primitive: entropy, bit-flip rate, lag-1
autocorrelation, step size, and a coarse classification (constant / counter /
checksum / enum / continuous), plus multi-byte **word detection** (a near-constant
high byte next to a full-range low byte = a probable scaled 16-bit word — how the
AC input voltage hid across a byte boundary).

Kept **leaf and numpy-free** (imports only stdlib ``math``), like ``stats.py``:
it operates on plain value sequences and offset-keyed columns, and knows nothing
about WiCAN framing, PCI, or the capture model — the caller (``investigate``)
extracts the per-offset series and maps results back to byte expressions. It is
byte-space-agnostic, so the domain-B raw-CAN path can reuse it unchanged.

Caveats the caller should surface, not this module:
- Lag-1 autocorrelation here is **sample-lag**, not a time-domain ACF: on the
  irregularly-sampled diagnostic poll it measures "consecutive stored samples",
  not a fixed time offset. It still separates slow (thermal) from fast (load)
  bytes, but don't read a physical period off it.
- Flip rates assume the values are in **acquisition order** and that consecutive
  samples are real consecutive reads — a ``keep:unique`` scope (rising-edge-only
  storage) distorts them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise

__all__ = [
    "ByteTriage",
    "WordCandidate",
    "bit_flip_rates",
    "byte_entropy",
    "classify",
    "detect_words",
    "flip_rate",
    "lag1_autocorr",
    "mean_abs_step",
    "triage_byte",
]


def byte_entropy(values: list[int]) -> float:
    """Shannon entropy (bits) of a byte's observed value distribution.

    0.0 for a constant byte; approaches 8.0 for a uniformly-random byte (a
    counter or checksum). A quick separator of static/enum (low) from
    counter/checksum (high) bytes.
    """
    n = len(values)
    if n == 0:
        return 0.0
    counts: dict[int, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    h = 0.0
    for c in counts.values():
        p = c / n
        h -= p * math.log2(p)
    return h


def lag1_autocorr(values: Sequence[float]) -> float | None:
    """Lag-1 autocorrelation over the sample sequence (Pearson of x[t], x[t-1]).

    High (→1) for a slowly-drifting signal (a temperature); low/near-zero for
    noise, a fast counter, or a checksum. ``None`` when undefined (fewer than 3
    points or a constant series). **Sample-lag**, not time-lag — see module note.
    """
    n = len(values)
    if n < 3:
        return None
    a = values[:-1]
    b = values[1:]
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    va = math.fsum((x - ma) ** 2 for x in a)
    vb = math.fsum((x - mb) ** 2 for x in b)
    if va == 0 or vb == 0:
        return None
    cov = math.fsum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    return cov / (va**0.5 * vb**0.5)


def mean_abs_step(values: Sequence[float]) -> float:
    """Mean absolute change between consecutive samples (0.0 for <2 points).

    Small for a slow/thermal signal, ~1 for a ±1 counter, large/erratic for a
    checksum or a load-tracking value.
    """
    if len(values) < 2:
        return 0.0
    return sum(abs(b - a) for a, b in pairwise(values)) / (len(values) - 1)


def flip_rate(values: list[int]) -> float:
    """Fraction of consecutive samples in which the byte changed at all."""
    if len(values) < 2:
        return 0.0
    return sum(1 for a, b in pairwise(values) if a != b) / (len(values) - 1)


def bit_flip_rates(values: list[int]) -> list[float]:
    """Per-bit flip probability (bit 0 = LSB) between consecutive samples.

    A multi-byte little counter shows a monotonic gradient (LSB flips often, high
    bits rarely); a checksum flips every bit ~0.5. Length-8 list; all zeros for a
    constant byte or a single sample.
    """
    if len(values) < 2:
        return [0.0] * 8
    trans = len(values) - 1
    out = []
    for k in range(8):
        flips = sum(1 for a, b in pairwise(values) if ((a >> k) & 1) != ((b >> k) & 1))
        out.append(flips / trans)
    return out


def classify(values: list[int]) -> str:
    """Coarse kind of a data byte from its distribution and dynamics.

    Returns one of ``constant`` / ``counter`` / ``checksum`` / ``enum`` /
    ``continuous``. A heuristic first-pass label, not a verdict: a "counter" is a
    high-entropy byte stepping in small regular increments; a "checksum" is
    high-entropy but erratic; an "enum" has few distinct values; "continuous" is
    everything else (a real analog reading).
    """
    distinct = len(set(values))
    if distinct <= 1:
        return "constant"
    ent = byte_entropy(values)
    step = mean_abs_step(values)
    fr = flip_rate(values)
    high_entropy = ent >= 6.0  # ~64+ effective values — near byte-random
    if high_entropy and fr >= 0.9:
        # A ±1/±small counter steps regularly and small; a checksum jumps around.
        return "counter" if step <= 4.0 else "checksum"
    if distinct <= 12:
        return "enum"
    return "continuous"


@dataclass
class ByteTriage:
    """First-pass triage of one data byte's value sequence."""

    n: int
    distinct: int
    entropy: float
    flip_rate: float
    lag1: float | None
    step: float
    kind: str
    bit_flip: list[float] = field(default_factory=lambda: [0.0] * 8)


def triage_byte(values: list[int]) -> ByteTriage:
    """Compute the full :class:`ByteTriage` for one byte's ordered value list."""
    fvals = [float(v) for v in values]
    return ByteTriage(
        n=len(values),
        distinct=len(set(values)),
        entropy=byte_entropy(values),
        flip_rate=flip_rate(values),
        lag1=lag1_autocorr(fvals),
        step=mean_abs_step(fvals),
        kind=classify(values),
        bit_flip=bit_flip_rates(values),
    )


@dataclass
class WordCandidate:
    """A probable multi-byte scaled word: a near-constant hi byte + wide lo byte."""

    hi_key: object  # opaque offset/label of the high (most-significant) byte
    lo_key: object  # opaque offset/label of the low byte
    score: float  # 0..1 — higher is a more confident word


def detect_words(
    columns: Sequence[tuple[object, Sequence[int]]],
    *,
    hi_max_range: int = 32,
    lo_min_range: int = 160,
    min_score: float = 0.3,
) -> list[WordCandidate]:
    """Flag adjacent (hi, lo) byte pairs that look like one scaled 16-bit word.

    ``columns`` is the payload's **data** bytes in left-to-right order (PCI
    already excluded), each ``(key, values)`` with ``values`` in sample order.
    Consecutive list entries are treated as byte-adjacent (so a PCI-straddling
    pair is still caught — the caller renders the correct shift expression).

    The fingerprint of a big-endian scaled word (e.g. a centivolt voltage): the
    **high** byte barely moves (small range, it's the integer part over a narrow
    span) while the **low** byte sweeps most of 0–255 (the fractional part). This
    is exactly how a real value hides as a "constant" byte next to a "garbage"
    byte. Ranks by how cleanly the pair fits that shape; returns strongest first.
    """
    out: list[WordCandidate] = []
    for (hi_key, hi), (lo_key, lo) in pairwise(columns):
        if len(hi) < 2 or len(lo) < 2:
            continue
        hi_range = max(hi) - min(hi)
        lo_range = max(lo) - min(lo)
        if hi_range == 0 and lo_range == 0:
            continue  # both constant — not a varying word
        if hi_range > hi_max_range or lo_range < lo_min_range:
            continue
        # Score: reward a wide low byte and a narrow (but not necessarily flat)
        # high byte. Both terms in 0..1; product keeps it conservative.
        score = (lo_range / 255.0) * (1.0 - hi_range / 255.0)
        if score >= min_score:
            out.append(WordCandidate(hi_key=hi_key, lo_key=lo_key, score=score))
    out.sort(key=lambda w: -w.score)
    return out
