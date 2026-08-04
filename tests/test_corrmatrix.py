"""Tests for the pairwise correlation ranking engine (canlib.corrmatrix).

The engine buckets signals by timestamp vector, joins once per bucket pair, and
hoists per-signal statistics out of the pair loop
(``plans/2026-08-04-join-and-correlation-performance.md``). That is a pure
performance change, so the load-bearing test here is **equivalence**: the bucketed
sweep must agree with a naive pair-at-a-time sweep to full float precision,
including hit ordering.

:func:`_naive_correlate_matrix` is the oracle — a deliberately dumb transcription
of "join every pair, then correlate it". Keep it dumb; it earns its keep by being
obviously correct rather than fast.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from canlib.align import (
    TimePoint,
    join_prepared,
    prepare_series,
    series_time_ranges_disjoint,
)
from canlib.corrmatrix import (
    CorrHit,
    _same_pid,
    colinear_clusters,
    correlate_matrix,
    signal_group_key,
)
from canlib.stats import correlation


def _tp(sec: float, val: float) -> TimePoint:
    return TimePoint(datetime(2026, 7, 22, 9, 0, 0) + timedelta(seconds=sec), val)


# ---------------------------------------------------------------------------
# The oracle: one join per signal pair, no bucketing, no hoisting.
# ---------------------------------------------------------------------------
def _naive_correlate_matrix(
    series: dict[str, list[TimePoint]],
    *,
    tol_s: float,
    min_r: float,
    min_n: int,
    include_intra: bool,
    method: str,
) -> list[CorrHit]:
    names = list(series)
    prepared = {name: prepare_series(series[name]) for name in names}
    hits: list[CorrHit] = []
    for i in range(len(names)):
        a = names[i]
        pa = prepared[a]
        if len(pa.ts) < min_n:
            continue
        for j in range(i + 1, len(names)):
            b = names[j]
            if not include_intra and _same_pid(a, b):
                continue
            pb = prepared[b]
            if len(pb.ts) < min_n or series_time_ranges_disjoint(pa, pb, tol_s):
                continue
            xs, ys, n = join_prepared(pa, pb, tol_s=tol_s)
            if n < min_n:
                continue
            r = correlation(xs, ys, method)
            if r is None or abs(r) < min_r:
                continue
            hits.append(CorrHit(a, b, r, n))
    hits.sort(key=lambda h: -abs(h.r))
    return hits


def _exact(hits: list[CorrHit]) -> list[tuple[str, str, str, int]]:
    """Hits reduced to a fully-precise, order-sensitive comparison key.

    ``repr`` of the float, not ``pytest.approx``: the plan's requirement is a
    byte-identical ``r``, because a last-bit drift is exactly how a silent
    behaviour change would hide.
    """
    return [(h.a, h.b, repr(h.r), h.n) for h in hits]


# A corpus deliberately built to exercise the shapes the bucketed sweep has to
# get right, not just a happy path: shared and distinct timestamp vectors, a
# same-PID family, cross-PID and cross-ECU pairs, a constant series, a bit-shaped
# 0/1 series, negative correlation, duplicate timestamps, and interleaved name
# ordering (so an ordering bug can't hide behind a sorted corpus).
def _mixed_corpus() -> dict[str, list[TimePoint]]:
    ramp = [_tp(i, float(i)) for i in range(40)]
    skewed = [_tp(i + 0.4, float(i)) for i in range(40)]
    sparse = [_tp(i * 2, float(i)) for i in range(25)]
    return {
        "MCU:2102:RPM": ramp,
        "BMS:2101:SOC": [_tp(i, float(i) * 2.0 + 3.0) for i in range(40)],
        "BMS:2101:TEMP": [_tp(i, float((i * 7) % 11)) for i in range(40)],
        "ESC:22C101:SPEED": skewed,
        "ESC:22C101:B12": [_tp(i + 0.4, float(-i)) for i in range(40)],
        "IGPM:22BC03:B10:5": [_tp(i, float(i % 2)) for i in range(40)],
        "IGPM:22BC03:FLAT": [_tp(i, 7.0) for i in range(40)],
        "OBC:2101:AC_V": sparse,
        "AAF:2181:DUPTIME": [_tp(i // 2, float(i)) for i in range(40)],  # repeated stamps
        "MCU:2102:TORQUE": [_tp(i, float(i * i)) for i in range(40)],
    }


class TestEquivalenceWithNaiveSweep:
    """The gate: bucketed == naive, to full precision and identical ordering."""

    @pytest.mark.parametrize("method", ["pearson", "spearman", "cramers_v", "mutual_info"])
    @pytest.mark.parametrize("include_intra", [False, True])
    def test_mixed_corpus(self, method, include_intra):
        series = _mixed_corpus()
        kw = {
            "tol_s": 5.0,
            "min_r": 0.3,
            "min_n": 5,
            "include_intra": include_intra,
            "method": method,
        }
        assert _exact(correlate_matrix(series, **kw)) == _exact(
            _naive_correlate_matrix(series, **kw)
        )

    @pytest.mark.parametrize("tol_s", [0.1, 0.5, 1.0, 5.0])
    def test_across_join_tolerances(self, tol_s):
        """The join window changes which pairs overlap — and by how much."""
        series = _mixed_corpus()
        kw = {"tol_s": tol_s, "min_r": 0.0, "min_n": 2, "include_intra": True, "method": "pearson"}
        assert _exact(correlate_matrix(series, **kw)) == _exact(
            _naive_correlate_matrix(series, **kw)
        )

    def test_ties_keep_the_naive_orderings(self):
        """Equal |r| must break ties in the naive sweep's order.

        The bucketed sweep visits pairs grouped by timestamp vector, *not* in
        ``i < j`` order, so it has to carry each pair's original position — this is
        the test that catches losing it. Two interleaved clocks (even/odd signals
        skewed 0.2 s apart) are what make the two enumeration orders differ: the
        naive sweep emits ``(0,1) (0,2) (0,3) (1,2) (1,3) (2,3)`` while the buckets
        emit the same-clock pairs first and one pair in reversed orientation. Every
        series is the same ramp, so all six pairs correlate at exactly 1.0 and
        ordering is decided purely by the tie-break.
        """
        series = {
            f"E{k}:P{k}:X": [_tp(i + (0.2 if k % 2 else 0.0), float(i)) for i in range(20)]
            for k in range(4)
        }
        kw = {"tol_s": 1.0, "min_r": 0.5, "min_n": 5, "include_intra": False, "method": "pearson"}
        got = correlate_matrix(series, **kw)
        assert len(got) == 6
        assert {h.r for h in got} == {1.0}  # all ties — nothing but order to get wrong
        assert _exact(got) == _exact(_naive_correlate_matrix(series, **kw))


class TestFinitenessIsPerJoinedSubVector:
    """The plan's step-3 caveat, pinned.

    Finiteness must be judged on the **joined sub-vector**, never on the whole
    series. Hoisting it one level further (a per-series flag) looks equivalent and
    silently discards a usable series — and the bundled corpus has no non-finite
    values, so nothing else in the suite would catch it. ``f64``/``f32`` byte
    reinterpretations in the hunters really do produce ``inf``/``nan``.
    """

    def _pair(self, poison_at: float | None, poison: float):
        """A clean 0..19s ramp pair, plus one candidate sample at ``poison_at``."""
        ref = [_tp(i, float(i)) for i in range(20)]
        cand = [_tp(i, float(i)) for i in range(20)]
        if poison_at is not None:
            cand.append(_tp(poison_at, poison))
        return {"A:P1:REF": ref, "B:P2:CAND": cand}

    @pytest.mark.parametrize("poison", [math.inf, -math.inf, math.nan])
    def test_non_finite_outside_the_join_window_still_hits(self, poison):
        # The poisoned sample sits 1000s away: no reference point joins to it, so
        # it never enters a coefficient and the pair must still rank.
        series = self._pair(1000.0, poison)
        kw = {"tol_s": 1.0, "min_r": 0.9, "min_n": 10, "include_intra": False, "method": "pearson"}
        hits = correlate_matrix(series, **kw)
        assert len(hits) == 1
        assert hits[0].r == pytest.approx(1.0)
        assert _exact(hits) == _exact(_naive_correlate_matrix(series, **kw))

    @pytest.mark.parametrize("poison", [math.inf, -math.inf, math.nan])
    def test_non_finite_inside_the_join_window_yields_no_hit(self, poison):
        # Replace an in-window sample: the coefficient is undefined, so no hit.
        series = self._pair(None, 0.0)
        series["B:P2:CAND"][5] = _tp(5, poison)
        kw = {"tol_s": 1.0, "min_r": 0.0, "min_n": 10, "include_intra": False, "method": "pearson"}
        assert correlate_matrix(series, **kw) == []
        assert _naive_correlate_matrix(series, **kw) == []

    def test_spearman_ranks_a_non_finite_series_like_stats_spearman(self):
        # stats.spearman ranks first, and ranks are always finite — so a non-finite
        # value inside the window does NOT kill a spearman hit. The hoist must
        # reproduce that, not "improve" on it.
        series = self._pair(None, 0.0)
        series["B:P2:CAND"][5] = _tp(5, math.inf)
        kw = {"tol_s": 1.0, "min_r": 0.0, "min_n": 10, "include_intra": False, "method": "spearman"}
        assert _exact(correlate_matrix(series, **kw)) == _exact(
            _naive_correlate_matrix(series, **kw)
        )


class TestTimestampVectorBuckets:
    def test_same_pid_with_two_distinct_timestamp_vectors(self):
        """Two signals from one PID can have *different* timestamp vectors.

        ``build_byte_series`` drops a byte offset on frames too short to contain it
        (``if bn < len(fr)``), so a PID whose final frame is short yields a shorter
        series for the tail bytes — the real ``HVAC:220102`` shape (632 vs 633
        samples). This is why the bucket key must be the timestamp vector itself:
        keying on ``(ECU, PID)`` would join the two vectors as if they were one.
        """
        full = [_tp(i, float(i)) for i in range(30)]
        short = [_tp(i, float(i)) for i in range(29)]  # last frame too short for this byte
        series = {
            "HVAC:220102:B10": full,
            "HVAC:220102:B20": short,
            "MCU:2102:RPM": [_tp(i + 0.3, float(i)) for i in range(30)],
            "BMS:2101:SOC": [_tp(i + 0.6, float(-i)) for i in range(30)],
        }
        kw = {"tol_s": 1.0, "min_r": 0.5, "min_n": 10, "include_intra": True, "method": "pearson"}
        assert _exact(correlate_matrix(series, **kw)) == _exact(
            _naive_correlate_matrix(series, **kw)
        )
        # And the two same-PID vectors really are distinct (else this proves nothing).
        assert len({tuple(prepare_series(s).ts) for s in (full, short)}) == 2

    def test_signals_sharing_one_vector_across_pids_still_pair(self):
        # Two different PIDs polled at identical instants land in ONE bucket; the
        # cross-PID pairs inside a bucket must still be ranked (the self-pair path).
        ts = [_tp(i, float(i)) for i in range(20)]
        series = {
            "A:P1:X": ts,
            "B:P2:Y": [_tp(i, float(i) * 3.0) for i in range(20)],
        }
        hits = correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10)
        assert len(hits) == 1
        assert hits[0].r == pytest.approx(1.0)

    def test_duplicate_timestamps_join_like_the_naive_sweep(self):
        """A self-bucket join is not the identity when timestamps repeat.

        ``bisect_left`` lands on the *first* of a duplicated stamp, so a repeated
        timestamp maps to the earlier sample. Two signals sharing such a vector
        therefore pair on shifted indices — the reason the sub-vector cache is
        per-side rather than per-signal.
        """
        dup = [_tp(i // 2, float(i)) for i in range(24)]
        series = {"A:P1:X": dup, "B:P2:Y": [_tp(i // 2, float(i) * -2.0) for i in range(24)]}
        kw = {"tol_s": 1.0, "min_r": 0.0, "min_n": 5, "include_intra": True, "method": "pearson"}
        assert _exact(correlate_matrix(series, **kw)) == _exact(
            _naive_correlate_matrix(series, **kw)
        )


class TestDegenerateSeries:
    def test_zero_variance_series_yields_no_hit(self):
        series = {
            "A:P1:FLAT": [_tp(i, 5.0) for i in range(20)],
            "B:P2:RAMP": [_tp(i, float(i)) for i in range(20)],
        }
        assert correlate_matrix(series, tol_s=1.0, min_r=0.0, min_n=5) == []

    def test_overflowing_series_does_not_raise(self):
        # A float reinterpretation of raw bytes can land near the float max, where
        # the squared deviations overflow. That must be reported as "no hit", never
        # abort a sweep mid-run.
        huge = [_tp(i, 1e308 * (1 if i % 2 else -1)) for i in range(20)]
        series = {"A:P1:HUGE": huge, "B:P2:RAMP": [_tp(i, float(i)) for i in range(20)]}
        assert correlate_matrix(series, tol_s=1.0, min_r=0.0, min_n=5) == []

    def test_empty_and_single_sample_series(self):
        series = {
            "A:P1:EMPTY": [],
            "B:P2:ONE": [_tp(0, 1.0)],
            "C:P3:RAMP": [_tp(i, float(i)) for i in range(20)],
        }
        # min_n=1 lets the degenerate series through the length prune; they must
        # still produce nothing rather than raising.
        assert correlate_matrix(series, tol_s=1.0, min_r=0.0, min_n=1) == []

    def test_no_series_at_all(self):
        assert correlate_matrix({}, tol_s=1.0, min_r=0.0, min_n=1) == []


# ---------------------------------------------------------------------------
# Behaviour that predates the bucketing (moved here with the engine)
# ---------------------------------------------------------------------------
class TestCorrelateMatrix:
    def test_cross_pid_pair_surfaces(self):
        # A on ECU1 and B on ECU2 are the same ramp; C is noise
        ramp = [_tp(i, i) for i in range(20)]
        series = {
            "E1:P:A": ramp,
            "E2:P:B": [_tp(i + 0.2, i) for i in range(20)],
            "E2:P:C": [_tp(i + 0.2, (i * 7) % 5) for i in range(20)],
        }
        hits = correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10)
        assert hits
        top = hits[0]
        assert {top.a, top.b} == {"E1:P:A", "E2:P:B"}
        assert top.r == pytest.approx(1.0)

    def test_intra_pid_excluded_by_default(self):
        ramp = [_tp(i, i) for i in range(20)]
        series = {"E1:P:A": ramp, "E1:P:B": [_tp(i, i) for i in range(20)]}
        assert correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10) == []
        # ...but included on request
        hits = correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10, include_intra=True)
        assert len(hits) == 1

    def test_min_n_threshold(self):
        series = {"E1:P:A": [_tp(i, i) for i in range(5)], "E2:P:B": [_tp(i, i) for i in range(5)]}
        assert correlate_matrix(series, tol_s=1.0, min_r=0.5, min_n=15) == []

    def test_intra_pid_bit_labels_excluded_by_default(self):
        """Bit labels (`ECU:PID:Bn:k`) must group by ECU+PID like every other label.

        Regression: the grouping key was `rsplit(":", 1)[0]`, which on the 4-field
        bit form yielded `ECU:PID:Bn` — so every `--bits` pair looked cross-PID.
        `correlate --bits` then ranked a param against its own backing bit
        (r=1.000) as the top "cross-signal" hit, flooding out real findings.
        """
        ramp = [_tp(i, i) for i in range(20)]
        series = {
            "IGPM:22BC03:DOOR_DRV_OPEN": ramp,
            "IGPM:22BC03:B10:5": [_tp(i, i) for i in range(20)],
        }
        assert correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10) == []
        hits = correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10, include_intra=True)
        assert len(hits) == 1

    def test_cross_pid_bit_labels_still_surface(self):
        # The fix must not over-group: a bit on a *different* PID is a real hit.
        series = {
            "IGPM:22BC03:B10:5": [_tp(i, i) for i in range(20)],
            "IGPM:22BC06:B10:0": [_tp(i + 0.2, i) for i in range(20)],
        }
        hits = correlate_matrix(series, tol_s=1.0, min_r=0.9, min_n=10)
        assert len(hits) == 1
        assert hits[0].r == pytest.approx(1.0)


class TestSignalGroupKey:
    """The cross-domain 'same signal source' key (see corrmatrix.signal_group_key)."""

    @pytest.mark.parametrize(
        "label,expected",
        [
            # domain A — diagnostics: key on ECU+PID
            ("BMS:2101:SOC", "BMS:2101"),
            ("IGPM:22BC03:B12", "IGPM:22BC03"),
            ("IGPM:22BC03:B12:3", "IGPM:22BC03"),  # 4-field bit form
            # domain B — raw broadcast frames: key on the arbitration ID
            ("0x220:r1", "0x220"),
            ("0x220:r1.3", "0x220"),  # bit uses '.', not ':'
        ],
    )
    def test_group_key(self, label, expected):
        assert signal_group_key(label) == expected

    def test_reexported_from_xanalysis(self):
        # correlate/investigate and a lot of muscle memory reach for these through
        # xanalysis; the module split must not break that.
        from canlib import xanalysis

        assert xanalysis.signal_group_key is signal_group_key
        assert xanalysis.correlate_matrix is correlate_matrix
        assert xanalysis.colinear_clusters is colinear_clusters
        assert xanalysis.CorrHit is CorrHit

    @pytest.mark.parametrize(
        "a,b,same",
        [
            ("IGPM:22BC03:B12", "IGPM:22BC03:B13", True),
            ("IGPM:22BC03:B12:3", "IGPM:22BC03:B13:4", True),
            ("IGPM:22BC03:SOC", "IGPM:22BC03:B13:4", True),
            ("IGPM:22BC03:B12:3", "IGPM:22BC04:B13:4", False),  # different PID
            ("IGPM:22BC03:B12:3", "BCM:22BC03:B13:4", False),  # different ECU
            ("0x220:r1.3", "0x220:r2.4", True),
            ("0x220:r1", "0x386:r2", False),
        ],
    )
    def test_same_pid(self, a, b, same):
        assert _same_pid(a, b) is same


class TestColinearClusters:
    def test_groups_mutual(self):
        # A,B,C mutually ~1.0 -> one cluster of 3; D unrelated
        hits = [
            CorrHit("A", "B", 0.999, 30),
            CorrHit("B", "C", 0.998, 30),
            CorrHit("A", "C", 0.997, 30),
            CorrHit("A", "D", 0.5, 30),
        ]
        clusters = colinear_clusters(hits)
        assert len(clusters) == 1
        assert clusters[0] == {"A", "B", "C"}

    def test_threshold_is_the_rendered_one(self):
        from canlib.corrmatrix import CLUSTER_THRESHOLD

        assert CLUSTER_THRESHOLD == 0.995
