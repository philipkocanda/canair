#!/usr/bin/env python3
"""Monotonic counter detection — "which bytes here only ever go up?".

The complement to :mod:`canlib.triage`, whose ``counter`` class is
*direction-blind* (it scores entropy + flip rate + ``mean_abs_step``, all of
which treat a rise and a fall alike) and single-byte. That finds a fast rolling
"alive" counter's low byte; it cannot find an **odometer**, an operating-hours
tally, or a power-cycle count — a slow multi-byte accumulator polled every few
seconds looks *constant* to it.

Three fingerprints, distinguished by *where* the monotonicity lives:

``accumulator``
    Non-decreasing across the whole corpus **and** moving within recording
    sessions — an odometer, operating-seconds, cumulative Ah/Wh.
``cycle``
    Non-decreasing across the corpus but **flat within every session**, stepping
    only across session boundaries — an ignition/power-cycle or trip count.
``timer``
    Monotonic within each session but **resetting to ~0** between them, with a
    slope that tracks wall-clock — uptime / seconds-since-key-on.

Evidence is measured in **bits** (:func:`mono_bits`): under the null "this series
has no preferred direction", each of the k moving steps points up with p=0.5, so
a clean all-up run of k steps is worth exactly k bits. That single scale is what
lets a 4000-sample accumulator and a 6-sample odometer be ranked together instead
of behind a hand-tuned minimum-step threshold — the sparse, long-horizon counters
are precisely the ones a fixed threshold discards.

Kept **leaf and numpy-free** like :mod:`canlib.triage` and :mod:`canlib.stats`:
it operates on plain value columns plus a parallel timestamp vector, and knows
nothing about WiCAN framing, PCI, ISO-TP, or the capture model. Callers supply
byte columns in **adjacency order** (consecutive entries are byte-adjacent) and
map the resulting windows back to expressions, so the domain-B raw-CAN path can
reuse this unchanged.

Caveats for the caller to surface, not this module:

- Columns must be **row-aligned**: ``columns[i][1][k]`` and ``ts[k]`` must
  describe the same sample. A ragged matrix (payloads of differing length) must
  be reconciled *before* calling — and by common **prefix**, not by discarding
  every payload of a non-modal length, which throws away exactly the old
  captures a long-horizon search needs.
- Step direction assumes ``ts`` is sorted and consecutive samples are real
  consecutive reads. A ``keep:unique`` scope distorts run structure.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TypedDict

from .stats import linear_fit, median, pearson

__all__ = [
    "DEFAULT_MAX_WIDTH",
    "DEFAULT_MIN_BITS",
    "MAX_MSB_JUMP",
    "MAX_RESET_FRAC",
    "MAX_STEP_RATIO",
    "SCAN_FLOOR_BITS",
    "TICK_RATES",
    "CounterCandidate",
    "SessionFit",
    "boundary_gradient",
    "cluster_counters",
    "find_counters",
    "mono_bits",
    "nearest_tick",
    "split_sessions",
]

DEFAULT_MAX_WIDTH = 4
DEFAULT_MIN_BITS = 4.0
# Floor for a sweep whose caller filters afterwards: low enough that the best
# rejected candidate is still known (so an empty report can name the threshold
# that would surface something) while excluding pure single-step noise.
SCAN_FLOOR_BITS = 2.0
# Evidence at which a monotonic window is trusted enough to claim bytes away from
# the timer fingerprint. Deliberately NOT the caller's display threshold: whether
# a run-timer is really an accumulator's wrapping low byte is a property of the
# data, not of how much the user chose to be shown.
_ALIAS_MIN_BITS = 8.0

# A true counter advances its most-significant byte by at most ~1 per observed
# step. A window shifted off the real counter boundary swallows a NEIGHBOURING
# counter's byte, so when that neighbour ticks the window leaps by 256^(w-1) —
# hundreds of MSB units. This is the single most effective rejection rule for the
# spurious windows a brute-force width sweep produces.
MAX_MSB_JUMP = 4.0

# A counter that has been running since the factory holds a value vastly larger
# than one increment. A block of bytes flipping 0x000000 -> 0x010001 is monotone
# and looks like a 2-step counter, but its "increment" IS its whole value — that
# is a set of flags turning on, not an accumulator.
MAX_STEP_RATIO = 0.5

# A real run-timer starts each session near zero. A wide accumulator's low bytes
# wrap every 256 ticks and mimic a resetting timer, but they restart from whatever
# the accumulator happened to be, not from ~0.
MAX_RESET_FRAC = 0.2

# Plausible wall-clock tick rates for a run-timer, in ticks per second.
TICK_RATES: dict[str, float] = {
    "10 ms": 100.0,
    "100 ms": 10.0,
    "0.5 s": 2.0,
    "1 s": 1.0,
    "2 s": 0.5,
    "1 min": 1 / 60,
    "1 h": 1 / 3600,
}
# Fractional tolerance for snapping a fitted slope onto a TICK_RATES entry.
_TICK_TOL = 0.20
# A timer's per-session slopes must agree this closely (coefficient of variation).
_MAX_SLOPE_CV = 0.25
# Minimum |r| of value-vs-elapsed-time within a session for a timer fit.
_MIN_TIMER_R = 0.98
_MIN_TIMER_SAMPLES = 5
_MIN_TIMER_SESSIONS = 3
# Fraction of sessions that must be flat for the "cycle count" fingerprint.
_CYCLE_FLAT_FRAC = 0.9


def mono_bits(n_up: int, n_down: int) -> float:
    """Bits of evidence that a series prefers to rise.

    One-sided binomial tail at p=0.5 over the *moving* steps, expressed in bits.
    A clean run of k up-steps yields exactly k bits; each violation costs roughly
    a factor of k, so the score degrades gracefully instead of falling off a
    hand-picked cliff. Flat steps carry no directional information and are
    excluded, which is what keeps a slow counter (mostly flat, occasionally +1)
    scoring on its real evidence rather than being diluted by its own stillness.
    """
    k = n_up + n_down
    if k == 0:
        return 0.0
    if n_down == 0:
        return float(k)  # tail is exactly 2^-k
    # Log-space tail, accumulated relative to the largest term: a 4000-step
    # series overflows a float binomial coefficient outright.
    logs = [
        math.lgamma(k + 1) - math.lgamma(i + 1) - math.lgamma(k - i + 1) for i in range(n_up, k + 1)
    ]
    m = max(logs)
    log_tail = m + math.log(math.fsum(math.exp(x - m) for x in logs)) - k * math.log(2)
    return max(0.0, -log_tail / math.log(2))


def nearest_tick(slope: float) -> tuple[str, float] | None:
    """Closest plausible wall-clock tick rate to ``slope`` (ticks/second).

    Returns ``(label, relative_error)``, or ``None`` when nothing in
    :data:`TICK_RATES` is within :data:`_TICK_TOL`. A monotonic per-session ramp
    whose rate matches no sane clock division is not a timer.
    """
    best: str | None = None
    err = math.inf
    for label, rate in TICK_RATES.items():
        e = abs(slope - rate) / rate
        if e < err:
            best, err = label, e
    return (best, err) if best is not None and err <= _TICK_TOL else None


def split_sessions(ts: Sequence[float], *, gap_s: float) -> list[tuple[int, int]]:
    """Index spans ``[start, end)`` of runs separated by more than ``gap_s``.

    Recordings sit minutes-to-days apart, so a time gap is the session boundary.
    ``ts`` must be sorted ascending.
    """
    if not ts:
        return []
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(ts)):
        if ts[i] - ts[i - 1] > gap_s:
            out.append((start, i))
            start = i
    out.append((start, len(ts)))
    return out


@dataclass(frozen=True)
class SessionFit:
    """One session's value-vs-elapsed-time fit, for the ``timer`` fingerprint."""

    start_t: float
    n: int
    duration_s: float
    lo: float
    hi: float
    slope: float  # ticks per second
    r: float


@dataclass
class CounterCandidate:
    """One byte window that behaves like a counter, with the evidence for it."""

    keys: tuple[object, ...]  # the column keys spanned, in adjacency order
    little: bool  # False = big-endian combination of ``keys``
    kind: str  # "accumulator" | "cycle" | "timer"
    bits: float  # monotonic evidence (see mono_bits)
    n: int
    n_distinct: int
    n_up: int
    n_down: int
    n_varying: int  # bytes in the window that actually move
    canonical: bool  # window is not padded at either end (see find_counters)
    first: float
    last: float
    lo: float
    hi: float
    med_step: float
    max_step: float
    msb_jump: float
    step_ratio: float
    span_s: float
    n_sessions: int
    flat_sessions: int  # sessions in which the value never moved
    boundary_steps: int  # session boundaries the value stepped across
    tick: str | None = None  # timer only: matched TICK_RATES label
    tick_err: float | None = None
    slope_cv: float | None = None
    reset_frac: float | None = None
    sessions: list[SessionFit] = field(default_factory=list, repr=False)

    @property
    def width(self) -> int:
        return len(self.keys)

    @property
    def total_delta(self) -> float:
        return self.last - self.first

    @property
    def per_year(self) -> float | None:
        """Increase per year over the observed span, or None if too short."""
        years = self.span_s / (365.25 * 86400)
        return self.total_delta / years if years > 0.02 else None

    @property
    def flat_frac(self) -> float:
        return self.flat_sessions / self.n_sessions if self.n_sessions else 0.0


class _WindowStats(TypedDict):
    """The window-shape facts every candidate carries, independent of fingerprint.

    A ``TypedDict`` rather than a bare dict so unpacking it into
    :class:`CounterCandidate` stays type-checked — the field list is long enough
    that a silent name/type slip would otherwise be easy.
    """

    keys: tuple[object, ...]
    little: bool
    n: int
    n_distinct: int
    n_up: int
    n_down: int
    n_varying: int
    canonical: bool
    first: float
    last: float
    lo: float
    hi: float
    med_step: float
    max_step: float
    msb_jump: float
    step_ratio: float
    span_s: float
    n_sessions: int


def _window_values(cols: Sequence[Sequence[int]], n_rows: int, *, little: bool) -> list[float]:
    """Combine ``cols`` (adjacent bytes, payload order) into one value per row."""
    order = list(reversed(range(len(cols)))) if little else list(range(len(cols)))
    out: list[float] = []
    for row in range(n_rows):
        v = 0
        for ci in order:
            v = (v << 8) | cols[ci][row]
        out.append(float(v))
    return out


def _steps(values: Sequence[float]) -> tuple[int, int, list[float]]:
    """``(n_up, n_down, up_step_sizes)`` over consecutive samples."""
    n_up = n_down = 0
    ups: list[float] = []
    for a, b in pairwise(values):
        d = b - a
        if d > 0:
            n_up += 1
            ups.append(d)
        elif d < 0:
            n_down += 1
    ups.sort()
    return n_up, n_down, ups


def _timer_fits(
    values: Sequence[float], ts: Sequence[float], spans: Sequence[tuple[int, int]]
) -> tuple[list[SessionFit], float] | None:
    """Per-session monotonic ramps and the median session-start fraction.

    ``None`` unless enough sessions each ramp cleanly upward against wall-clock.
    """
    fits: list[SessionFit] = []
    resets: list[float] = []
    for start, end in spans:
        if end - start < _MIN_TIMER_SAMPLES:
            continue
        sv = list(values[start:end])
        st = list(ts[start:end])
        if len(set(sv)) < 3 or any(b < a for a, b in pairwise(sv)):
            continue
        elapsed = [t - st[0] for t in st]
        fit = linear_fit(elapsed, sv)
        r = pearson(elapsed, sv)
        if fit is None or r is None or r < _MIN_TIMER_R or fit[0] <= 0:
            continue
        fits.append(
            SessionFit(
                start_t=st[0],
                n=len(sv),
                duration_s=elapsed[-1],
                lo=sv[0],
                hi=sv[-1],
                slope=fit[0],
                r=r,
            )
        )
        resets.append(sv[0] / sv[-1] if sv[-1] else 1.0)
    if len(fits) < _MIN_TIMER_SESSIONS:
        return None
    return fits, median(resets)


def find_counters(
    columns: Sequence[tuple[object, Sequence[int]]],
    ts: Sequence[float],
    *,
    session_gap_s: float,
    max_width: int = DEFAULT_MAX_WIDTH,
    min_bits: float = DEFAULT_MIN_BITS,
    max_step_ratio: float = MAX_STEP_RATIO,
) -> list[CounterCandidate]:
    """Sweep every 1..``max_width``-byte window x endianness for counter behaviour.

    ``columns`` is the payload's data bytes in **adjacency order**, each
    ``(key, values)`` with ``values`` in sample order and row-aligned with ``ts``
    (epoch seconds, ascending). Windows are taken over consecutive list entries,
    so adjacency is the caller's contract — see the module note.

    A window is *canonical* when it is not padded at either end: its
    least-significant byte must actually move (otherwise the window is
    over-extended to the right, e.g. a 3-byte odometer read as 4 bytes = the
    value x 256), and its most-significant byte must either move or be a non-zero
    constant (a constant ZERO high byte adds no magnitude, so including it is
    padding, whereas a constant 0x01 is real magnitude). Non-canonical windows are
    still returned — they are how a counter whose high bytes never moved in scope
    is found at all — but :func:`cluster_counters` prefers canonical ones.
    """
    n_rows = len(ts)
    if n_rows < 2 or not columns:
        return []
    spans = split_sessions(ts, gap_s=session_gap_s)
    span_s = ts[-1] - ts[0]
    col_values = [vals for _k, vals in columns]
    const = [len(set(v)) <= 1 for v in col_values]
    first_row = [v[0] for v in col_values]

    found: list[CounterCandidate] = []
    monotonic: list[tuple[CounterCandidate, range]] = []
    timer_pending: list[tuple[CounterCandidate, range]] = []

    for width in range(1, max_width + 1):
        for off in range(len(columns) - width + 1):
            cols = col_values[off : off + width]
            for little in (False,) if width == 1 else (False, True):
                values = _window_values(cols, n_rows, little=little)
                n_distinct = len(set(values))
                if n_distinct < 2:
                    continue
                n_up, n_down, ups = _steps(values)
                max_step = ups[-1] if ups else 0.0
                msb_jump = max_step / (256.0 ** (width - 1)) if width > 1 else 0.0
                hi = max(values)
                step_ratio = max_step / max(abs(hi), 1.0)
                lsb = off if little else off + width - 1
                msb = off + width - 1 if little else off
                canonical = (not const[lsb]) and (not const[msb] or first_row[msb] != 0)
                keys = tuple(k for k, _v in columns[off : off + width])
                window = range(off, off + width)

                base: _WindowStats = {
                    "keys": keys,
                    "little": little,
                    "n": n_rows,
                    "n_distinct": n_distinct,
                    "n_up": n_up,
                    "n_down": n_down,
                    "n_varying": sum(1 for i in window if not const[i]),
                    "canonical": canonical,
                    "first": values[0],
                    "last": values[-1],
                    "lo": min(values),
                    "hi": hi,
                    "med_step": ups[len(ups) // 2] if ups else 0.0,
                    "max_step": max_step,
                    "msb_jump": msb_jump,
                    "step_ratio": step_ratio,
                    "span_s": span_s,
                    "n_sessions": len(spans),
                }

                bits = mono_bits(n_up, n_down)
                if (
                    n_down == 0
                    and bits >= min_bits
                    and msb_jump <= MAX_MSB_JUMP
                    and step_ratio <= max_step_ratio
                ):
                    flat = boundary = 0
                    prev_last: float | None = None
                    for start, end in spans:
                        seg = values[start:end]
                        if len(set(seg)) == 1:
                            flat += 1
                        if prev_last is not None and seg[0] != prev_last:
                            boundary += 1
                        prev_last = seg[-1]
                    flat_frac = flat / len(spans) if spans else 0.0
                    monotonic.append(
                        (
                            CounterCandidate(
                                **base,
                                kind="cycle" if flat_frac >= _CYCLE_FLAT_FRAC else "accumulator",
                                bits=bits,
                                flat_sessions=flat,
                                boundary_steps=boundary,
                            ),
                            window,
                        )
                    )
                    continue

                # A window sweeping most of its full scale is straddling unrelated
                # bytes, not ramping.
                if hi - min(values) > 0.5 * 256.0**width:
                    continue
                fitted = _timer_fits(values, ts, spans)
                if fitted is None:
                    continue
                fits, reset_frac = fitted
                slopes = [f.slope for f in fits]
                mean_slope = math.fsum(slopes) / len(slopes)
                if mean_slope <= 0:
                    continue
                cv = (
                    math.fsum((s - mean_slope) ** 2 for s in slopes) / len(slopes)
                ) ** 0.5 / mean_slope
                tick = nearest_tick(median(slopes))
                if cv > _MAX_SLOPE_CV or tick is None or reset_frac > MAX_RESET_FRAC:
                    continue
                cand = CounterCandidate(
                    **base,
                    kind="timer",
                    bits=mono_bits(sum(f.n - 1 for f in fits), 0),
                    flat_sessions=0,
                    boundary_steps=0,
                    tick=tick[0],
                    tick_err=tick[1],
                    slope_cv=cv,
                    reset_frac=reset_frac,
                )
                cand.sessions = fits
                timer_pending.append((cand, window))

    # A wide accumulator's LOW bytes wrap every 256 ticks, which is
    # indistinguishable from a resetting run-timer when viewed alone. If the same
    # bytes already participate in a well-evidenced accumulator, that is what they
    # are — drop the alias rather than report the same counter twice under two
    # fingerprints.
    found = [c for c, _w in monotonic]
    strong = [w for c, w in monotonic if c.bits >= _ALIAS_MIN_BITS]
    found.extend(
        cand for cand, window in timer_pending if not any(set(window) & set(w) for w in strong)
    )
    return found


def cluster_counters(
    candidates: Sequence[CounterCandidate],
    *,
    bit_flip: Mapping[object, Sequence[float]] | None = None,
) -> list[tuple[CounterCandidate, list[CounterCandidate]]]:
    """Collapse overlapping windows of one counter to a representative + members.

    A real N-byte counter makes every *prefix* window monotonic too (dropping the
    low byte just divides by 256), so the sweep necessarily reports nested hits
    for a single physical counter. The representative is the widest **canonical**
    window, because width is what recovers the counter's real magnitude — and the
    magnitude is the diagnostic: ``[B12:B14] = 72982`` reads instantly as an
    odometer where its low byte alone (``B14 = 88``) says nothing.

    Accumulator and cycle candidates cluster **together** (both are corpus-wide
    monotonic, so a counter's high half must not resurface as a separate "cycle
    count"); timers cluster separately.

    ``bit_flip`` (per-column bit-flip rates, keyed like ``CounterCandidate.keys``)
    is an optional **tie-break**: among equally-wide, equally-evidenced windows it
    prefers the one whose per-bit flip rates decrease most cleanly from LSB to MSB
    (:func:`boundary_gradient`) — the fingerprint of a correctly-aligned counter,
    which pins the byte boundary better than ``msb_jump`` alone. Omitting it leaves
    the ranking byte-identical to the pre-gradient behaviour.
    """
    groups: dict[str, list[CounterCandidate]] = {}
    for c in candidates:
        groups.setdefault("timer" if c.kind == "timer" else "monotonic", []).append(c)

    def rank_key(c: CounterCandidate):
        if bit_flip is None:
            return (not c.canonical, -c.width, -c.bits, c.msb_jump)
        grad = boundary_gradient(c.keys, c.little, bit_flip)
        # Higher gradient is better; a missing/undefined gradient sorts neutral-last
        # among ties (0.0) without disturbing the stronger canonical/width/bits keys.
        return (not c.canonical, -c.width, -c.bits, -(grad or 0.0), c.msb_jump)

    out: list[tuple[CounterCandidate, list[CounterCandidate]]] = []
    for group in groups.values():
        ranked = sorted(group, key=rank_key)
        reps: list[tuple[CounterCandidate, list[CounterCandidate], set[object]]] = []
        for cand in ranked:
            keyset = set(cand.keys)
            for _rep, members, seen in reps:
                if keyset & seen:
                    members.append(cand)
                    break
            else:
                reps.append((cand, [], keyset))
        out.extend((rep, members) for rep, members, _seen in reps)
    return out


def boundary_gradient(
    keys: Sequence[object], little: bool, bit_flip: Mapping[object, Sequence[float]]
) -> float | None:
    """How cleanly a window's per-bit flip rates fall from LSB to MSB (0..1).

    A true multi-byte counter flips its low bits far more often than its high
    bits, and monotonically so: laid out from the whole word's least- to
    most-significant bit, the flip-rate gradient of a *correctly aligned* window
    slopes smoothly down, while a window shifted onto a neighbouring byte breaks
    the slope. Returns the fraction of adjacent bit-pairs (ordered LSB->MSB across
    the whole window) that are non-increasing — 1.0 is a perfect gradient — or
    ``None`` when a key's rates are missing or the window is a single byte (no
    boundary to locate).

    ``bit_flip`` maps each column key to that byte's 8 per-bit flip rates (bit 0 =
    that byte's LSB), as produced by :func:`canlib.triage.bit_flip_rates`. Keys are
    in adjacency order (ascending offset); for a big-endian window the LSB sits at
    the highest offset, so the byte order is reversed before concatenating.
    """
    if len(keys) < 2:
        return None
    rates: list[Sequence[float]] = []
    for k in keys:
        r = bit_flip.get(k)
        if r is None:
            return None
        rates.append(r)
    # Bytes LSB->MSB: little-endian keys are already LSB-first; big-endian has the
    # LSB at the highest offset, so reverse. Within each byte, bit 0 (its LSB) is
    # the less-significant end, and byte N's bit 0 is more significant than byte
    # N-1's bit 7 — so concatenating (LSB byte first, bits 0..7 each) yields the
    # whole word's bits in ascending significance.
    ordered = rates if little else list(reversed(rates))
    gradient: list[float] = []
    for byte_rates in ordered:
        gradient.extend(byte_rates)
    pairs = list(pairwise(gradient))
    if not pairs:
        return None
    non_increasing = sum(1 for a, b in pairs if b <= a + 1e-9)
    return non_increasing / len(pairs)
