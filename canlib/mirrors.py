#!/usr/bin/env python3
"""Mirror detection: one physical quantity exposed by two different signals.

A *mirror* is the same value reachable two ways — a status bit an ECU publishes
that another repeats, a temperature a second module reports at a different offset,
a raw byte and the scaled parameter derived from it. Finding them is load-bearing
for reverse engineering: it identifies redundant signals (don't decode a byte
twice), and it *anchors an unknown byte to a known one* — the fastest possible
identification, because it needs no correlation reasoning at all.

**Why this module replaced an exact-equality test.** The original detector reported
a pair only if it was equal on *every* aligned row, with no offset or scale
allowed. Both limits produced false negatives on real data
(``plans/2026-08-05-run-length-forward-fill-joins.md``):

- **Poll skew breaks unanimity.** canair polls one connection round-robin, so two
  ECUs read a *drifting* signal seconds apart and disagree by ±1. The AAF/OBC
  LDC-temperature mirror is exactly equal in 94.9 % of 1729 rows and ±1 in the
  rest — obviously one signal, reported as no mirror. Hence
  :data:`DEFAULT_MIRROR_MATCH`: agreement on a *fraction* of rows, not all.
- **Real mirrors are frequently offset or scaled.** ``AAF:2181:B19 - 100`` is the
  OBC's LDC temperature; ``BCM:22C011:B11`` is ``12.8 x`` the decoded 12 V rail.
  Hence ``allow_offset``, which searches ``a = scale * b + offset``.

**How the search stays cheap.** A fraction test cannot bail on the first
disagreement the way exact equality could, and the sweeps here are O(signals²) —
tens of thousands of pairs over thousands of rows. Two things keep it affordable,
and both matter:

1. **Propose from a sample, verify exactly.** A candidate offset/scale is the modal
   ``a - b`` (or ``a / b``) over the first :data:`_SAMPLE` aligned rows; a relation
   holding on ≥90 % of rows is overwhelmingly the mode of any sample of it. The
   candidate is then *verified* against every row, so the sample only bounds the
   search — it never decides the answer.
2. **Bail once the disagreement budget is spent.** ``n - ceil(fraction * n)``
   disagreements are permitted, so a pair that is not a mirror is abandoned after a
   few rows rather than fully scanned.

Pure math over prepared series; no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .align import DEFAULT_JOIN_TOL_S, PreparedSeries, join_indices, timestamps_disjoint

__all__ = [
    "DEFAULT_MIRROR_MATCH",
    "MIN_KAPPA",
    "MirrorHit",
    "MirrorRelation",
    "byte_owns_bit",
    "find_column_mirrors",
    "find_series_mirrors",
    "frame_columns",
    "match_pair",
]

# Fraction of aligned rows that must agree for a pair to be reported.
#
# 0.9 admits the round-robin poll skew described above (a drifting signal read by
# two ECUs seconds apart disagrees on a minority of rows) while still demanding the
# overwhelming majority agree. Unanimity — the old behaviour — is available as
# ``--mirror-match 1``.
DEFAULT_MIRROR_MATCH = 0.9

# Chance-corrected agreement (Cohen's kappa) a pair must also clear.
#
# Raw agreement alone is not evidence, and on bit series it is actively misleading:
# two flags that are both zero in 99 % of rows agree in 99 % of rows *by
# construction*, with no relationship whatsoever. On the bundled profile, thresholding
# raw agreement at 0.9 turned 3 real mirrors into 73 reported pairs, nearly all of
# them independent rare bits. Kappa divides out that baseline — (observed - expected)
# / (1 - expected) — so it stays ~1 for a genuine mirror of any signal, and collapses
# toward 0 for a coincidence between two sparse flags.
#
# Not a user knob: it is a validity floor, not a preference. ``--mirror-match``
# remains the tunable, intuitive one ("agree on this fraction of rows").
MIN_KAPPA = 0.8

# Aligned rows used to *propose* an offset/scale. Only ever a search bound: every
# proposal is verified against the full series.
_SAMPLE = 32

# Values this close count as agreeing. Deliberately tight — essentially "equal up
# to float representation", so a scale of 12.8 recovered from division still
# matches exactly. Genuine ±1 drift is absorbed by the match *fraction*, not by a
# loose value tolerance, which would otherwise call two merely-similar signals a
# mirror.
_EPS = 1e-6


@dataclass(frozen=True)
class MirrorRelation:
    """``a == scale * b + offset``, and how well it held."""

    scale: float
    offset: float
    n: int  # rows compared
    n_match: int  # rows that agreed
    kappa: float  # agreement corrected for chance (see MIN_KAPPA)

    @property
    def fraction(self) -> float:
        return self.n_match / self.n if self.n else 0.0

    @property
    def exact(self) -> bool:
        return self.scale == 1.0 and self.offset == 0.0

    @property
    def unanimous(self) -> bool:
        return self.n_match == self.n

    def quality(self) -> str:
        """``n=310`` when every row agreed, else the exact count and kappa.

        Deliberately not a rounded percentage: 309 of 310 rows renders as "100%",
        which reads as unanimous and hides precisely the disagreement the reader
        needs to weigh. Kappa is shown alongside because it, not the raw count, is
        what qualified the pair (see :data:`MIN_KAPPA`).
        """
        if self.unanimous:
            return f"n={self.n}"
        return f"n={self.n}, {self.n_match} agree, \u03ba={self.kappa:.2f}"

    def describe(self, b_label: str) -> str:
        """``b_label`` transformed into ``a`` — e.g. ``B19 - 100``, ``SOC x 12.8``."""
        out = b_label
        if self.scale != 1.0:
            out = f"{out} × {_fmt(self.scale)}"
        if self.offset:
            out = f"{out} {'+' if self.offset > 0 else '-'} {_fmt(abs(self.offset))}"
        return out


@dataclass(frozen=True)
class MirrorHit:
    """One reported mirror pair, with the relation that made it one."""

    a: str
    b: str
    relation: MirrorRelation

    @property
    def n(self) -> int:
        return self.relation.n

    def as_json(self) -> dict:
        rel = self.relation
        return {
            "a": self.a,
            "b": self.b,
            "n": rel.n,
            "n_match": rel.n_match,
            "fraction": round(rel.fraction, 4),
            "kappa": round(rel.kappa, 4),
            "scale": rel.scale,
            "offset": rel.offset,
        }


def _fmt(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def _verify(
    a: Sequence[float],
    b: Sequence[float],
    scale: float,
    offset: float,
    budget: int,
) -> int | None:
    """Rows where ``a == scale*b + offset``, or ``None`` once ``budget`` is blown.

    The early exit is what makes an O(signals²) sweep affordable: a pair that is
    not a mirror is dropped after ``budget + 1`` rows instead of being fully
    scanned.
    """
    misses = 0
    matched = 0
    for av, bv in zip(a, b, strict=True):
        pred = bv * scale + offset
        if av == pred or abs(av - pred) <= _EPS * max(1.0, abs(av)):
            matched += 1
        else:
            misses += 1
            if misses > budget:
                return None
    return matched


def _kappa(
    a: Sequence[float],
    b: Sequence[float],
    scale: float,
    offset: float,
    n_match: int,
) -> float:
    """Cohen's kappa for ``a`` vs the relation's prediction from ``b``.

    ``(observed - expected) / (1 - expected)``, where *expected* is the agreement
    two independent signals with these value distributions would reach by chance.
    That baseline is what makes a mirror claim meaningful: see :data:`MIN_KAPPA`.
    """
    n = len(a)
    if n == 0:
        return 0.0
    observed = n_match / n
    counts_a: dict[float, int] = {}
    counts_pred: dict[float, int] = {}
    for av, bv in zip(a, b, strict=True):
        key_a = round(av, 6)
        counts_a[key_a] = counts_a.get(key_a, 0) + 1
        key_p = round(bv * scale + offset, 6)
        counts_pred[key_p] = counts_pred.get(key_p, 0) + 1
    expected = sum(c * counts_pred.get(v, 0) for v, c in counts_a.items()) / (n * n)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def _modes(values: list[float], top: int = 2) -> list[float]:
    """The most common values in ``values`` (rounded to 6 dp), most frequent first."""
    counts: dict[float, int] = {}
    for v in values:
        key = round(v, 6)
        counts[key] = counts.get(key, 0) + 1
    return [v for v, _c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]


def _candidates(
    a: Sequence[float], b: Sequence[float], allow_offset: bool
) -> list[tuple[float, float]]:
    """``(scale, offset)`` pairs worth verifying for this pair of series."""
    out: list[tuple[float, float]] = [(1.0, 0.0)]  # identity — always tried first
    if not allow_offset:
        return out
    sample = min(len(a), _SAMPLE)
    for off in _modes([a[i] - b[i] for i in range(sample)]):
        if off and (1.0, off) not in out:
            out.append((1.0, off))
    ratios = [a[i] / b[i] for i in range(sample) if abs(b[i]) > _EPS]
    for scale in _modes(ratios):
        if scale not in (0.0, 1.0) and (scale, 0.0) not in out:
            out.append((scale, 0.0))
    return out


def match_pair(
    a: Sequence[float],
    b: Sequence[float],
    *,
    min_fraction: float = DEFAULT_MIRROR_MATCH,
    allow_offset: bool = False,
) -> MirrorRelation | None:
    """The best relation making ``b`` a mirror of ``a``, or ``None``.

    Prefers the *simplest* relation that clears ``min_fraction``: identity over an
    offset, an offset over a scale — so a genuinely equal pair is never reported as
    a coincidentally-fitting scaled one.

    A pair is rejected unless **both** sides vary over the compared rows: two
    signals that are simply constant in this scope agree perfectly while carrying no
    evidence whatsoever that they are the same quantity. The byte/bit column
    builders already drop constant positions, but a *defined parameter* can be
    constant over a window, and without this guard every such parameter mirrored
    every other one. Near-constant pairs are then filtered by :data:`MIN_KAPPA`,
    which is the same argument applied by degree rather than absolutely.
    """
    n = len(a)
    if n == 0 or n != len(b):
        return None
    if len(set(a)) < 2 or len(set(b)) < 2:
        return None
    # `- _EPS` keeps a float product like 0.9*10 == 9.000000000000002 from demanding
    # all 10 rows when the user asked for 90%.
    needed = min(n, max(1, math.ceil(min_fraction * n - _EPS)))
    budget = n - needed
    for scale, offset in _candidates(a, b, allow_offset):
        matched = _verify(a, b, scale, offset, budget)
        if matched is None or matched < needed:
            continue
        kappa = _kappa(a, b, scale, offset, matched)
        if kappa < MIN_KAPPA:
            continue  # agrees no better than coincidence — see MIN_KAPPA
        return MirrorRelation(scale=scale, offset=offset, n=n, n_match=matched, kappa=kappa)
    return None


def find_series_mirrors(
    series: dict[str, PreparedSeries],
    *,
    tol_s: float = DEFAULT_JOIN_TOL_S,
    min_n: int = 15,
    min_fraction: float = DEFAULT_MIRROR_MATCH,
    allow_offset: bool = False,
    same_source: Callable[[str, str], bool] | None = None,
) -> list[MirrorHit]:
    """Time-aligned mirror sweep across ``series`` (cross-signal, cross-ECU).

    ``same_source`` marks pairs to skip (same PID, same arbitration ID) — those are
    the *intra*-source case :func:`find_column_mirrors` covers, and reporting them
    here would bury the cross-source finds that are the point.

    Signals are bucketed by their join clock and the join computed **once per
    bucket pair**, reusing :func:`canlib.align.join_indices`' index mapping across
    every signal pair that shares it. Nearly every signal decoded from one PID
    shares one clock, so this turns tens of thousands of joins into a few hundred —
    without it the sweep spends nearly all its time re-deriving the same alignment.
    """
    names = sorted(series)
    order = {name: i for i, name in enumerate(names)}
    buckets: dict[tuple, list[str]] = {}
    for name in names:
        ps = series[name]
        key = (tuple(ps.ts), tuple(ps.hold_ts) if ps.hold_ts is not None else None)
        buckets.setdefault(key, []).append(name)
    keys = list(buckets)

    hits: list[MirrorHit] = []
    for x, ka in enumerate(keys):
        for y in range(x, len(keys)):
            kb = keys[y]
            if x != y and timestamps_disjoint(ka[0], kb[0], tol_s):
                continue
            # Mirroring is symmetric, so the *denser* clock is used as the
            # reference. ``min_n`` bounds the reference's row count and nothing
            # else: a join emits one row per reference sample, and a run-length
            # candidate of a few transitions can cover every one of them once
            # carried forward. Pruning short series outright — or fixing the
            # direction by sort order — would hide exactly the sparse-vs-dense
            # mirrors this sweep exists to find.
            ref_key, cand_key = (ka, kb) if len(ka[0]) >= len(kb[0]) else (kb, ka)
            if len(ref_key[0]) < min_n:
                continue
            ref_idx, cand_idx = join_indices(ref_key[0], cand_key[0], tol_s, cand_key[1])
            if len(ref_idx) < min_n:
                continue
            # Hoisted per side: the joined sub-vector of a signal is fixed for this
            # bucket pair, so it is built once instead of once per pair it appears in.
            left: dict[str, list[float]] = {}
            right: dict[str, list[float]] = {}
            for a in buckets[ref_key]:
                for b in buckets[cand_key]:
                    if a == b or (same_source is not None and same_source(a, b)):
                        continue
                    if x == y and order[a] > order[b]:
                        continue  # one direction only within a bucket
                    if a not in left:
                        left[a] = [series[a].values[k] for k in ref_idx]
                    if b not in right:
                        right[b] = [series[b].values[j] for j in cand_idx]
                    rel = match_pair(
                        left[a],
                        right[b],
                        min_fraction=min_fraction,
                        allow_offset=allow_offset,
                    )
                    if rel is not None:
                        hits.append(MirrorHit(a, b, rel))
    hits.sort(key=lambda h: (-h.relation.kappa, -h.relation.n, h.a, h.b))
    return hits


def find_column_mirrors(
    columns: dict[str, list[float]],
    *,
    min_n: int = 1,
    min_fraction: float = DEFAULT_MIRROR_MATCH,
    allow_offset: bool = False,
    same_source: Callable[[str, str], bool] | None = None,
) -> list[MirrorHit]:
    """Positional mirror sweep over equal-length value columns (one PID's bytes/bits).

    No time join: the columns come from the *same* captures, so row *i* of each is
    the same capture — the intra-PID case (``decode --find-mirrors``), where a byte
    and the parameter derived from it, or two redundant status bits, sit in one
    response.
    """
    names = sorted(columns)
    hits: list[MirrorHit] = []
    for i, a in enumerate(names):
        va = columns[a]
        if len(va) < min_n:
            continue
        for b in names[i + 1 :]:
            vb = columns[b]
            if len(vb) != len(va):
                continue
            if same_source is not None and same_source(a, b):
                continue
            rel = match_pair(va, vb, min_fraction=min_fraction, allow_offset=allow_offset)
            if rel is not None:
                hits.append(MirrorHit(a, b, rel))
    hits.sort(key=lambda h: (-h.relation.kappa, -h.relation.n, h.a, h.b))
    return hits


def frame_columns(frames: Sequence[bytes], *, bits: bool = False) -> dict[str, list[float]]:
    """One value column per *varying* byte (and optionally bit) position in ``frames``.

    The input shape :func:`find_column_mirrors` wants for the single-PID case: all
    the frames sit in one offset space and row *i* of every column is the same
    capture, so no time join is needed. Only positions present in **every** frame are
    considered (a shorter response has no value at a tail offset), and only those
    with ≥2 distinct values — all-constant padding would otherwise "mirror"
    everything.
    """
    if len(frames) < 2:
        return {}
    max_len = min(len(f) for f in frames)
    cols: dict[str, list[float]] = {}
    for i in range(max_len):
        col = [float(f[i]) for f in frames]
        if len(set(col)) >= 2:
            cols[f"B{i}"] = col
    if bits:
        for i in range(max_len):
            for k in range(8):
                col = [float((f[i] >> k) & 1) for f in frames]
                if len(set(col)) >= 2:
                    cols[f"B{i}:{k}"] = col
    return cols


def byte_owns_bit(a: str, b: str) -> bool:
    """True when one of ``a``/``b`` is a bit *of* the other's byte (``B12`` vs ``B12:3``).

    Such a pair mirrors trivially whenever the byte only ever takes two values that
    differ in that bit — a restatement of the same datum, not a second signal, so
    :func:`find_column_mirrors` is asked to skip it.
    """
    return a.split(":")[0] == b.split(":")[0] and (":" in a) != (":" in b)
