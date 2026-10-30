"""Support-map detection: constant leading bytes that mark which data bits/bytes are signals.

Several Hyundai body and climate DIDs start their data with a few constant bytes that say
which of the following bits (or bytes) are real signals — the layout is documented in
``plans/2026-10-29-support-bitmap-coverage.md``. It comes in two granularities:

* ``bit``  — map bit 7 of byte *j* marks bit 0 of the *j*-th covered data byte, bit 6 marks
  bit 1, and so on (MSB first). The map covers as many bytes as it has. A marked bit may be the
  *start* of a multi-bit field, so a set bit directly above a mark is a **continuation**, not a
  violation (see :data:`MAX_FIELD_WIDTH`).
* ``byte`` — map bit *i* (MSB first across the map) marks the *i*-th covered data byte.

This module is the **leaf**: rows of data bytes in (SID and DID echo already stripped), scored
fits out. It knows nothing of WiCAN indices, ISO-TP framing or captures, so any bridge can feed it.

The evidence is combinatorial, not statistical folklore. If a byte has *m* marked bits and *s*
bits ever set, the chance that all *s* land on marked bits for an unrelated map is
``C(m, s) / C(8, s)``; the per-byte layout is the same with bytes in place of bits. Those
probabilities multiply across the covered bytes (:attr:`MapFit.chance_log10`). A single DID with
few set bits cannot be significant alone, so callers may add the evidence of sibling DIDs that
share a placement (:func:`family_chance_log10`).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

Unit = Literal["bit", "byte"]

# A mark is the start of a field at most this many bits wide (the mark plus continuations).
# Wide enough for the IGPM's 2-bit turn-signal field, narrow enough that BCM 22B005's
# light-stalk bits (a gap of unmarked, never-set bits above the mark) stay violations.
MAX_FIELD_WIDTH = 3
# A byte is "live" (a real moving value, not a binary flag or +-1 jitter) from this many values.
LIVE_DISTINCT = 3
MIN_ROWS = 20
# Fraction of rows that must carry the modal map; the rest are dropped as a stale variant.
MODAL_FRAC = 0.98
MAX_MAP_LEN = 4
# Every Ioniq map starts right after the DID echo, so only offset 0 is searched by default.
DEFAULT_MAX_AT = 0
DEFAULT_MAX_CHANCE = 0.01
MIN_EXPLAINED = 3
# A witness must beat a random map of the same density by 10x on its own (before the placement
# penalty); a map that marks nearly everything is satisfied by anything and proves nothing.
MIN_OWN_LOG10 = -1.0


def reverse_bits(value: int) -> int:
    """``value`` with its 8 bits mirrored (map bit 7 becomes data bit 0)."""
    return int(f"{value & 0xFF:08b}"[::-1], 2)


def marked_bits(map_byte: int) -> frozenset[int]:
    """Data bit positions (0 = LSB) marked by one map byte, MSB first."""
    return frozenset(bit for bit in range(8) if (map_byte >> (7 - bit)) & 1)


def marked_byte_indices(mask: bytes, covered_len: int) -> frozenset[int]:
    """Covered-byte indices marked by a per-byte map, MSB first across the map."""
    return frozenset(
        i for i in range(min(covered_len, 8 * len(mask))) if (mask[i // 8] >> (7 - i % 8)) & 1
    )


def continuation_bits(marked: Iterable[int], active: Iterable[int]) -> frozenset[int]:
    """The ``active`` unmarked bits that extend a marked bit upward as one field.

    ``active`` are the unmarked bits that need explaining (ever set, or read by a signal). A
    bit extends a field when the bit below it is marked or already a continuation and the field
    would not exceed :data:`MAX_FIELD_WIDTH`.
    """
    marks = set(marked)
    wanted = set(active) - marks
    out: set[int] = set()
    start = -1
    for bit in range(8):
        if bit in marks:
            start = bit
        elif (
            bit in wanted and (bit - 1 in marks or bit - 1 in out) and bit - start < MAX_FIELD_WIDTH
        ):
            out.add(bit)
    return frozenset(out)


@dataclass(frozen=True)
class Slot:
    """One marked (or violating) position and how it behaved over the rows."""

    offset: int  # data offset of the byte, SID and DID echo excluded
    bit: int | None  # bit within the byte; None for a per-byte slot
    n_set: int  # rows where the bit is 1 / the byte is non-zero
    distinct: int  # distinct values of the byte over the rows
    n: int

    @property
    def state(self) -> str:
        """``moving``/``always``/``never`` for a bit; ``live``/``toggling``/``constant``/``zero``."""
        if self.bit is not None:
            return "never" if self.n_set == 0 else "always" if self.n_set == self.n else "moving"
        if self.n_set == 0:
            return "zero"
        if self.distinct >= LIVE_DISTINCT:
            return "live"
        return "toggling" if self.distinct == 2 else "constant"


@dataclass(frozen=True)
class MapFit:
    """One candidate placement of a map, scored against the rows."""

    unit: Unit
    at: int  # data offset of the first map byte
    length: int  # map bytes
    mask: bytes
    n_rows: int
    covered_start: int
    covered_len: int
    marked: tuple[Slot, ...]
    continuations: tuple[Slot, ...]
    violations: tuple[Slot, ...]
    inside: int  # moving bits (or live bytes) explained by a mark
    chance_log10: float
    placements: int  # placements tried, for the multiple-comparisons penalty

    @property
    def penalised_log10(self) -> float:
        """:attr:`chance_log10` plus the cost of having tried :attr:`placements` placements."""
        return self.chance_log10 + math.log10(max(self.placements, 1))

    @property
    def key(self) -> tuple[Unit, int, int]:
        """The placement, for grouping sibling DIDs that share one."""
        return (self.unit, self.at, self.length)

    def verdict(
        self, *, max_chance: float = DEFAULT_MAX_CHANCE, max_violations: int = 0
    ) -> Literal["strong", "weak", "rejected", "none"]:
        """How far to trust this fit.

        ``none``: nothing moves inside the map, so there is nothing to test. ``rejected``: more
        set bits or live bytes outside the map than allowed. ``strong``: consistent and
        significant alone. ``weak``: consistent, but with too few moving bits to be significant
        without siblings (:func:`corroborated` can promote it).
        """
        if self.inside < 1:
            return "none"
        if len(self.violations) > max_violations:
            return "rejected"
        if self.inside >= MIN_EXPLAINED and self.penalised_log10 <= math.log10(max_chance):
            return "strong"
        return "weak"


def _log10_comb(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)) / math.log(10)


def _chance_log10(groups: Iterable[tuple[int, int, int]]) -> float:
    """Sum of ``log10 C(m, s) / C(n, s)`` over ``(marked, set, universe)`` groups."""
    total = 0.0
    for m, s, n in groups:
        if s:
            total += _log10_comb(m, s) - _log10_comb(n, s)
    return total


def _columns(rows: Sequence[bytes], start: int, count: int) -> list[Counter[int]]:
    return [Counter(r[start + j] for r in rows) for j in range(count)]


def _fit_bits(rows: Sequence[bytes], at: int, mask: bytes, placements: int) -> MapFit | None:
    length = len(mask)
    start = at + length
    covered = min(length, len(rows[0]) - start)
    if covered < 1:
        return None
    n = len(rows)
    marked: list[Slot] = []
    conts: list[Slot] = []
    viols: list[Slot] = []
    groups: list[tuple[int, int, int]] = []
    inside = 0
    for j, counts in enumerate(_columns(rows, start, covered)):
        ever = 0
        for value in counts:
            ever |= value
        set_bits = {b for b in range(8) if (ever >> b) & 1}
        # Only a bit that actually changes is evidence: a constant byte (a second copy of the
        # map, say) would otherwise "fit" any map that happens to cover it.
        moving = {b for b in set_bits if 0 < sum(c for v, c in counts.items() if (v >> b) & 1) < n}
        marks = marked_bits(mask[j])
        cont = continuation_bits(marks, set_bits - marks)
        distinct = len(counts)

        def slot(bit: int, offset: int = start + j, counts=counts, distinct=distinct) -> Slot:
            return Slot(
                offset, bit, sum(c for v, c in counts.items() if (v >> bit) & 1), distinct, n
            )

        marked += [slot(b) for b in sorted(marks)]
        conts += [slot(b) for b in sorted(cont)]
        viols += [slot(b) for b in sorted(set_bits - marks - cont)]
        inside += len(moving & marks)
        groups.append((len(marks), len(moving & marks), 8))
    return MapFit(
        "bit", at, length, mask, n, start, covered, tuple(marked), tuple(conts), tuple(viols),
        inside, _chance_log10(groups), placements,
    )  # fmt: skip


def _fit_bytes(rows: Sequence[bytes], at: int, mask: bytes, placements: int) -> MapFit | None:
    length = len(mask)
    start = at + length
    covered = min(8 * length, len(rows[0]) - start)
    if covered < 1:
        return None
    n = len(rows)
    marks = marked_byte_indices(mask, covered)
    marked: list[Slot] = []
    viols: list[Slot] = []
    live_inside = 0
    for i, counts in enumerate(_columns(rows, start, covered)):
        slot = Slot(start + i, None, n - counts.get(0, 0), len(counts), n)
        live = len(counts) >= LIVE_DISTINCT
        if i in marks:
            marked.append(slot)
            live_inside += live
        elif live:
            viols.append(slot)
    chance = _chance_log10([(len(marks), live_inside, covered)])
    return MapFit(
        "byte", at, length, mask, n, start, covered, tuple(marked), (), tuple(viols),
        live_inside, chance, placements,
    )  # fmt: skip


def detect_support_maps(
    rows: Sequence[bytes], *, max_at: int = DEFAULT_MAX_AT, max_len: int = MAX_MAP_LEN
) -> list[MapFit]:
    """Every placement whose leading bytes are a constant, non-trivial map, best first.

    ``rows`` are data bytes with the SID and DID echo already stripped. Rejected placements are
    returned too (their :attr:`MapFit.violations` say why), so a caller asked about one PID can
    explain a refusal. Ordered by evidence; choose with :func:`best_fit`.
    """
    if len(rows) < MIN_ROWS:
        return []
    width = min(len(r) for r in rows)
    shapes = [(at, k, unit) for at in range(max_at + 1) for k in range(1, max_len + 1)
              for unit in ("bit", "byte")]  # fmt: skip
    fits: list[MapFit] = []
    for at, k, unit in shapes:
        if at + k >= width:
            continue
        modal, count = Counter(r[at : at + k] for r in rows).most_common(1)[0]
        if count < MODAL_FRAC * len(rows) or not any(modal) or all(b == 0xFF for b in modal):
            continue
        use = [r for r in rows if r[at : at + k] == modal]
        if len(use) < MIN_ROWS:
            continue
        fit = (_fit_bits if unit == "bit" else _fit_bytes)(use, at, bytes(modal), len(shapes))
        if fit is not None:
            fits.append(fit)
    fits.sort(key=lambda f: (f.chance_log10, f.unit != "bit"))
    return fits


def best_fit(fits: Iterable[MapFit]) -> MapFit | None:
    """The fit to report: the strongest evidence among those with something moving inside.

    Violations do not demote a fit here, so a PID whose real map is broken by one stray byte is
    reported as that map, rejected, rather than as a shorter stub that fits trivially. The caller
    reads the verdict off the result.
    """
    candidates = [f for f in fits if f.inside >= 1]
    if not candidates:
        return None
    return min(candidates, key=lambda f: (f.penalised_log10, f.unit != "bit"))


def _witness(fit: MapFit) -> bool:
    """A fit that can testify for its siblings: consistent, with real evidence of its own."""
    return not fit.violations and fit.inside >= MIN_EXPLAINED and fit.chance_log10 <= MIN_OWN_LOG10


def family_chance_log10(fits: Iterable[MapFit]) -> float:
    """Combined evidence of sibling DIDs sharing one placement, with the penalty applied once.

    Only witnesses contribute: a DID that breaks the layout is a counter-example, and one that
    a near-total map satisfies by construction proves nothing.
    """
    members = [f for f in fits if _witness(f)]
    if len(members) < 2:
        return 0.0
    return sum(f.chance_log10 for f in members) + math.log10(max(members[0].placements, 1))


def corroborated(
    fits_by_pid: Mapping[str, MapFit], *, max_chance: float = DEFAULT_MAX_CHANCE
) -> set[str]:
    """PIDs whose best fit is weak alone but passes together with siblings of the same placement.

    ``fits_by_pid`` holds each PID's best fit. Returns the PIDs to promote from ``weak``; only
    a witness can be promoted, so a near-empty map cannot ride on its neighbours.
    """
    groups: dict[tuple, list[str]] = {}
    for pid, fit in fits_by_pid.items():
        groups.setdefault(fit.key, []).append(pid)
    out: set[str] = set()
    for pids in groups.values():
        if family_chance_log10(fits_by_pid[p] for p in pids) <= math.log10(max_chance):
            out.update(
                p
                for p in pids
                if _witness(fits_by_pid[p])
                and fits_by_pid[p].verdict(max_chance=max_chance) == "weak"
            )
    return out
