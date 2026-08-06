#!/usr/bin/env python3
"""Pairwise correlation ranking over time-aligned signals.

The ranking core behind ``canair correlate``'s default report ("show me every
strong relationship in this scope") — extracted from :mod:`canlib.xanalysis`,
which re-exports it.

**Why it is bucketed.** Ranking N signals means N²/2 pairs, and each pair needs a
nearest-timestamp join before a coefficient can be computed. But a join depends
only on the two series' *clocks*, never on their values — and nearly every signal
decoded from one ``(ECU, PID)`` shares an identical timestamp vector, because they
all come from the same list of captures. So the same join was being recomputed
thousands of times: on the bundled profile, 49,441 pairwise joins collapse to 741
joins over distinct timestamp-vector pairs (411,778 → 1,225 with ``--bytes``).

Two consequences shape the code below:

1. Signals are **bucketed by their exact timestamp vector** (not by ``(ECU, PID)``:
   a byte offset absent from a short frame yields a *shorter* vector for the same
   PID, and keying on the PID would silently mis-join it), and the join is
   computed once per bucket pair as a reusable index mapping
   (:func:`canlib.align.join_indices`).
2. Given a bucket pair, each signal's joined sub-vector is fixed — so its mean
   deviations, sum of squares, and **finiteness** are hoisted out of the pair loop
   (O(signals) instead of O(signals²)), leaving a single covariance pass per pair.

Semantics are identical to the naive sweep, including tie ordering; the subtle
part is finiteness, which must be tested on the **joined sub-vector**, not the
whole series — see :func:`_sub_stats`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NamedTuple

from .align import (
    DEFAULT_JOIN_TOL_S,
    TimePoint,
    join_indices,
    prepare_series,
    timestamps_disjoint,
)
from .stats import CATEGORICAL_METHODS, correlation, rank

__all__ = [
    "CLUSTER_THRESHOLD",
    "CorrHit",
    "colinear_clusters",
    "correlate_matrix",
    "signal_group_key",
]


@dataclass
class CorrHit:
    a: str
    b: str
    r: float
    n: int
    # How many of ``n`` rows came from carrying a run-length value forward rather
    # than from a sample of ``b`` near the reference instant (see canlib.fill).
    # Clock-only, so it is the same for every pair sharing a bucket pair.
    n_filled: int = 0


class _Clock(NamedTuple):
    """A series' join identity: its timestamp vector and its validity windows.

    The bucket key. The timestamp vector alone was enough while every join was
    strict, but forward fill makes the join depend on the *hold* vector too — two
    signals with identical timestamps but different validity windows join
    differently, so the hold must be part of the key or one would silently borrow
    the other's mapping.
    """

    ts: tuple[float, ...]
    hold: tuple[float, ...] | None


def signal_group_key(label: str) -> str:
    """The "same signal source" grouping key for a series label.

    Two label grammars flow through the correlation engine, and the key is the
    part identifying *where the bytes came from* — so a pair sharing it is an
    intra-source (not cross-signal) relationship:

    ==========================  =============================  ==========
    label                       grammar                        key
    ==========================  =============================  ==========
    ``BMS:2101:SOC``            domain A, named param          ``BMS:2101``
    ``IGPM:22BC03:B12``         domain A, raw byte             ``IGPM:22BC03``
    ``IGPM:22BC03:B12:3``       domain A, raw *bit*            ``IGPM:22BC03``
    ``0x220:r1``                domain B, frame byte           ``0x220``
    ``0x220:r1.3``              domain B, frame bit            ``0x220``
    ==========================  =============================  ==========

    Domain A always has at least an ECU and a PID, so a 3-or-more-field label
    keys on its first two fields; a domain-B frame label is a single
    arbitration ID plus one byte/bit field (the bit uses a ``.``, deliberately,
    see :mod:`canlib.frame_series`), so it keys on its first field.

    A plain ``rsplit(":", 1)[0]`` — which this replaced — silently broke on the
    4-field *bit* form: it keyed ``IGPM:22BC03:B12:3`` on ``IGPM:22BC03:B12``, so
    every ``--bits`` pair looked cross-PID and `correlate --bits` reported a
    param against its own backing bit (r=1.000) as a top "cross-signal" hit.
    """
    parts = label.split(":")
    return ":".join(parts[:2]) if len(parts) >= 3 else parts[0]


def _same_pid(a: str, b: str) -> bool:
    """True if two signal labels come from the same ECU+PID (or arbitration ID)."""
    return signal_group_key(a) == signal_group_key(b)


@dataclass(frozen=True)
class _SubStats:
    """A signal's joined sub-vector reduced to what a Pearson pass still needs."""

    dev: list[float]  # deviations from the sub-vector's own mean
    ss: float  # sum of squared deviations


def _sub_stats(values: list[float], idx: list[int], *, ranked: bool) -> _SubStats | None:
    """Reduce one signal's joined sub-vector to ``(deviations, sum-of-squares)``.

    ``None`` when the sub-vector cannot yield a defined coefficient — the same
    three rejections :func:`canlib.stats.pearson` makes: a non-finite value, an
    overflowing sum of squared deviations, or zero variance.

    **Finiteness is deliberately tested here, on the joined sub-vector — not on
    the whole series.** Hoisting it a level further (one flag per series) looks
    equivalent and is not: a series carrying an ``inf``/``nan`` *outside* every
    join window is perfectly usable today, and a whole-series flag would discard
    it. ``f64``/``f32`` byte reinterpretations really do produce such values, so
    the distinction is load-bearing.

    With ``ranked`` the sub-vector is rank-transformed first, which is what makes
    Spearman (Pearson of ranks) fall out of the same hoist — and, as in
    :func:`canlib.stats.spearman`, means finiteness is judged on the *ranks*.
    """
    sub = [values[k] for k in idx]
    if ranked:
        sub = rank(sub)
    if not all(math.isfinite(v) for v in sub):
        return None
    mean = sum(sub) / len(sub)
    dev = [v - mean for v in sub]
    try:
        # `d ** 2`, not `d * d`: libm's pow is not bit-identical to a multiply for
        # every input, and this must reproduce stats.pearson exactly.
        ss = math.fsum(d**2 for d in dev)
    except (OverflowError, ValueError):
        return None
    if ss == 0:
        return None
    return _SubStats(dev, ss)


def _pairs_between(
    group_a: list[str],
    group_b: list[str],
    *,
    same_bucket: bool,
    order: dict[str, int],
    include_intra: bool,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Signal pairs spanning two timestamp buckets, split by join orientation.

    Returns ``(forward, reverse)`` lists of ``(ref, cand)`` pairs where ``ref`` is
    always the *lower-indexed* signal — the orientation the naive ``i < j`` sweep
    used, and one a nearest join is not symmetric under. ``forward`` pairs anchor
    on ``group_a``'s clock, ``reverse`` on ``group_b``'s.
    """
    forward: list[tuple[str, str]] = []
    reverse: list[tuple[str, str]] = []
    if same_bucket:
        for i, a in enumerate(group_a):
            for b in group_a[i + 1 :]:
                if include_intra or not _same_pid(a, b):
                    forward.append((a, b))
        return forward, reverse
    for a in group_a:
        for b in group_b:
            if not include_intra and _same_pid(a, b):
                continue
            if order[a] < order[b]:
                forward.append((a, b))
            else:
                reverse.append((b, a))
    return forward, reverse


def correlate_matrix(
    series: dict[str, list[TimePoint]],
    *,
    tol_s: float = DEFAULT_JOIN_TOL_S,
    min_r: float = 0.6,
    min_n: int = 15,
    include_intra: bool = False,
    method: str = "pearson",
) -> list[CorrHit]:
    """Pairwise correlation across all series, time-aligned by nearest timestamp.

    Returns hits with ``|r| >= min_r`` and ``n >= min_n``, strongest first.
    ``method`` selects Pearson (linear), Spearman (monotone/rank), or a
    categorical association. Same-(ECU, PID) pairs are dropped unless
    ``include_intra`` (they're already covered by ``decode --corr`` and dominate
    the ranking).

    The sweep is bucketed by timestamp vector and hoists per-signal statistics out
    of the pair loop (see the module docstring); results — values, ordering, and
    tie-breaking — are identical to a pair-at-a-time sweep. The categorical
    methods are contingency-table statistics over the *pair*, so they share the
    bucketed join but keep the per-pair coefficient path.
    """
    names = list(series)
    # Prepare each series once (sort + flatten to float epoch arrays), then bucket
    # by the exact timestamp vector: the join is a property of the clock alone, so
    # every signal in a bucket shares one join with every other bucket.
    prepared = {name: prepare_series(series[name]) for name in names}
    order = {name: i for i, name in enumerate(names)}
    buckets: dict[_Clock, list[str]] = {}
    for name in names:
        ps = prepared[name]
        clock = _Clock(tuple(ps.ts), tuple(ps.hold_ts) if ps.hold_ts is not None else None)
        buckets.setdefault(clock, []).append(name)
    # Every clock stays in the sweep. The old ``len(ts) >= min_n`` prune here read
    # like a mechanical impossibility and was not one: a join emits one row per
    # *reference* sample, and several reference rows may map to the same candidate
    # sample, so a short series can still supply a min_n overlap as the candidate —
    # and with forward fill a run-length signal of a handful of transitions covers a
    # whole window. Dropping it silently discarded exactly those signals. The bound
    # is applied per ordered pair below, where it belongs (on the reference side).
    keys = list(buckets)

    numeric = method not in CATEGORICAL_METHODS
    ranked = method == "spearman"
    n_names = len(names)
    # (pair position in the naive i<j enumeration, hit) — carried so equal-|r|
    # hits keep the unbucketed sweep's ordering.
    found: list[tuple[int, CorrHit]] = []

    for x, ka in enumerate(keys):
        for y in range(x, len(keys)):
            kb = keys[y]
            if x != y and timestamps_disjoint(ka.ts, kb.ts, tol_s):
                continue
            forward, reverse = _pairs_between(
                buckets[ka],
                buckets[kb],
                same_bucket=x == y,
                order=order,
                include_intra=include_intra,
            )
            for ref_clock, cand_clock, pairs in ((ka, kb, forward), (kb, ka, reverse)):
                if not pairs or len(ref_clock.ts) < min_n:
                    continue  # a join can never exceed the reference's own row count
                found.extend(
                    _rank_bucket_pair(
                        pairs,
                        ref_clock,
                        cand_clock,
                        prepared=prepared,
                        order=order,
                        n_names=n_names,
                        tol_s=tol_s,
                        min_r=min_r,
                        min_n=min_n,
                        numeric=numeric,
                        ranked=ranked,
                        method=method,
                    )
                )

    found.sort(key=lambda t: (-abs(t[1].r), t[0]))
    return [hit for _pos, hit in found]


def _rank_bucket_pair(
    pairs: list[tuple[str, str]],
    ref_clock: _Clock,
    cand_clock: _Clock,
    *,
    prepared: dict,
    order: dict[str, int],
    n_names: int,
    tol_s: float,
    min_r: float,
    min_n: int,
    numeric: bool,
    ranked: bool,
    method: str,
) -> list[tuple[int, CorrHit]]:
    """Correlate every signal pair spanning one *ordered* timestamp-bucket pair.

    The join is computed **once** here and reused for every pair; each signal's
    joined sub-vector statistics are memoised per side, so the per-pair work is a
    single covariance pass. A signal can sit on both sides within one bucket
    (a self-pair, where the two clocks are the same vector but the join need not be
    the identity when timestamps repeat), hence one cache per side.
    """
    ref_idx, cand_idx = join_indices(ref_clock.ts, cand_clock.ts, tol_s, cand_clock.hold)
    n = len(ref_idx)
    if n < min_n or n < 2:
        return []
    # Clock-only, like the join itself: one extra strict join per *bucket* pair
    # (not per signal pair) tells every hit here how much of its overlap was
    # reconstructed by forward fill rather than measured.
    n_filled = 0
    if cand_clock.hold is not None:
        n_filled = n - len(join_indices(ref_clock.ts, cand_clock.ts, tol_s)[0])

    ref_stats: dict[str, _SubStats | None] = {}
    cand_stats: dict[str, _SubStats | None] = {}
    ref_vals: dict[str, list[float]] = {}
    cand_vals: dict[str, list[float]] = {}
    out: list[tuple[int, CorrHit]] = []

    for a, b in pairs:
        if numeric:
            if a not in ref_stats:
                ref_stats[a] = _sub_stats(prepared[a].values, ref_idx, ranked=ranked)
            sa = ref_stats[a]
            if sa is None:
                continue
            if b not in cand_stats:
                cand_stats[b] = _sub_stats(prepared[b].values, cand_idx, ranked=ranked)
            sb = cand_stats[b]
            if sb is None:
                continue
            try:
                cov = math.fsum(da * db for da, db in zip(sa.dev, sb.dev, strict=True))
                r = cov / (sa.ss**0.5 * sb.ss**0.5)
            except (OverflowError, ValueError):
                continue
            if not math.isfinite(r):
                continue
        else:
            if a not in ref_vals:
                ref_vals[a] = [prepared[a].values[k] for k in ref_idx]
            if b not in cand_vals:
                cand_vals[b] = [prepared[b].values[k] for k in cand_idx]
            coeff = correlation(ref_vals[a], cand_vals[b], method)
            if coeff is None:
                continue
            r = coeff
        if abs(r) < min_r:
            continue
        out.append((order[a] * n_names + order[b], CorrHit(a, b, r, n, n_filled)))
    return out


# Above this |r| two signals are treated as the same line: co-linear bundles are
# collapsed into one summary row instead of flooding the ranked pair list. The
# render layer prints it, so it is part of this module's surface.
CLUSTER_THRESHOLD = 0.995


def colinear_clusters(hits, threshold: float = CLUSTER_THRESHOLD):
    """Union-find signals joined by ``|r| >= threshold`` into co-linear groups.

    Returns the list of clusters (sets of signal labels) with ≥3 members — the
    near-perfectly-correlated bundles (e.g. every balanced cell voltage during
    charging) that otherwise flood the ranked pair list with redundant rows.
    ``hits`` are :class:`CorrHit`-shaped (``.a``/``.b``/``.r``).
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for h in hits:
        if abs(h.r) >= threshold:
            ra, rb = find(h.a), find(h.b)
            if ra != rb:
                parent[ra] = rb
    groups: dict[str, set] = {}
    for sig in parent:
        groups.setdefault(find(sig), set()).add(sig)
    return [g for g in groups.values() if len(g) >= 3]
