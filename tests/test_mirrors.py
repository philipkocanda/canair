"""Tests for mirror detection (canlib.mirrors).

Two limits of the exact-equality detector this replaced are what these tests pin:
poll skew breaking unanimity, and real mirrors sitting at an offset or scale. Both
produced false negatives on the bundled profile — see
``plans/2026-08-05-run-length-forward-fill-joins.md``.
"""

from datetime import datetime, timedelta

import pytest

from canlib.align import TimePoint, prepare_series
from canlib.mirrors import (
    DEFAULT_MIRROR_MATCH,
    byte_owns_bit,
    find_column_mirrors,
    find_series_mirrors,
    frame_columns,
    match_pair,
)

BASE = datetime(2026, 8, 5, 12, 0, 0)


def _series(values, *, step=1.0, offset=0.0):
    return [
        TimePoint(BASE + timedelta(seconds=offset + i * step), float(v))
        for i, v in enumerate(values)
    ]


class TestMatchPair:
    def test_identical_series(self):
        rel = match_pair([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        assert rel is not None
        assert rel.exact and rel.fraction == 1.0

    def test_unrelated_series(self):
        assert match_pair([1.0, 2.0, 3.0], [9.0, 4.0, 7.0]) is None

    def test_one_disagreement_in_twenty_still_a_mirror(self):
        """Round-robin poll skew makes two ECUs disagree by ±1 on a drifting signal."""
        a = [float(v) for v in range(20)]
        b = list(a)
        b[7] += 1
        rel = match_pair(a, b)
        assert rel is not None and rel.n_match == 19

    def test_unanimity_can_be_demanded(self):
        a = [float(v) for v in range(20)]
        b = list(a)
        b[7] += 1
        assert match_pair(a, b, min_fraction=1.0) is None

    def test_offset_requires_allow_offset(self):
        a = [float(v) for v in range(20)]
        b = [v - 100 for v in a]
        assert match_pair(a, b) is None
        rel = match_pair(a, b, allow_offset=True)
        assert rel is not None and (rel.scale, rel.offset) == (1.0, 100.0)

    def test_scale_recovered_exactly(self):
        """The 12.8 divisor of a raw 12 V byte must come back as 12.8, not 12.799…"""
        a = [float(v) for v in range(150, 170)]
        b = [v / 12.8 for v in a]
        rel = match_pair(a, b, allow_offset=True)
        assert rel is not None and rel.scale == 12.8 and rel.offset == 0.0

    def test_prefers_the_simplest_relation(self):
        """An equal pair is reported as equal, never as a coincidentally-fitting scale."""
        a = [float(v) for v in range(20)]
        rel = match_pair(a, list(a), allow_offset=True)
        assert rel is not None and rel.exact

    def test_length_mismatch_is_not_a_mirror(self):
        assert match_pair([1.0, 2.0], [1.0]) is None

    def test_empty(self):
        assert match_pair([], []) is None

    def test_two_constant_signals_are_not_a_mirror(self):
        """Perfect agreement with no variation is no evidence at all."""
        assert match_pair([1.0] * 20, [1.0] * 20) is None
        assert match_pair([1.0] * 20, [7.0] * 20, allow_offset=True) is None

    def test_a_sampled_candidate_is_still_verified_on_every_row(self):
        """The sample only *proposes* an offset; a late divergence must still reject."""
        a = [float(v) for v in range(200)]
        b = [v - 5 for v in a[:100]] + [v + 900 for v in a[100:]]
        assert match_pair(a, b, allow_offset=True) is None

    def test_describe_renders_the_relation(self):
        a = [float(v) for v in range(20)]
        off = match_pair(a, [v - 100 for v in a], allow_offset=True)
        scaled = match_pair(a, [v / 12.8 for v in a], allow_offset=True)
        assert off is not None and scaled is not None
        assert off.describe("B19") == "B19 + 100"
        assert scaled.describe("VOLTS") == "VOLTS × 12.8"
        assert match_pair(a, list(a)).describe("B4") == "B4"


class TestFrameColumns:
    def test_only_varying_positions(self):
        frames = [bytes([0, 0, 0, 0, 0x00, 0xAA]), bytes([0, 0, 0, 0, 0x00, 0xBB])]
        assert list(frame_columns(frames)) == ["B5"]

    def test_bits(self):
        frames = [bytes([0x00]), bytes([0x05]), bytes([0x00]), bytes([0x05])]
        cols = frame_columns(frames, bits=True)
        assert {"B0", "B0:0", "B0:2"} <= set(cols)

    def test_only_offsets_present_in_every_frame(self):
        frames = [bytes([1, 2, 3]), bytes([4, 5])]
        assert set(frame_columns(frames)) == {"B0", "B1"}

    def test_too_few_frames(self):
        assert frame_columns([bytes([1, 2, 3])]) == {}


class TestByteOwnsBit:
    def test_a_bit_of_its_own_byte(self):
        assert byte_owns_bit("B12", "B12:3")
        assert byte_owns_bit("B12:3", "B12")

    def test_distinct_positions(self):
        assert not byte_owns_bit("B12", "B13")
        assert not byte_owns_bit("B12:3", "B12:4")


class TestFindColumnMirrors:
    def test_finds_and_skips_byte_own_bit(self):
        cols = {
            "B4": [0.0, 5.0, 0.0, 5.0],
            "B4:0": [0.0, 1.0, 0.0, 1.0],
            "B4:2": [0.0, 1.0, 0.0, 1.0],
        }
        pairs = {(h.a, h.b) for h in find_column_mirrors(cols, same_source=byte_owns_bit)}
        assert ("B4:0", "B4:2") in pairs
        assert ("B4", "B4:0") not in pairs

    def test_unequal_lengths_skipped(self):
        cols = {"A": [1.0, 2.0], "B": [1.0, 2.0, 3.0]}
        assert find_column_mirrors(cols) == []


class TestFindSeriesMirrors:
    def _prepared(self, mapping):
        return {k: prepare_series(v) for k, v in mapping.items()}

    def test_finds_a_cross_pid_mirror(self):
        vals = [float(v) for v in range(20)]
        series = self._prepared(
            {
                "A:1:B4": _series(vals),
                "B:2:B4": _series(vals, offset=0.3),  # co-polled, small skew
            }
        )

        def same_pid(a, b):
            return a.split(":")[:2] == b.split(":")[:2]

        hits = find_series_mirrors(series, tol_s=5.0, min_n=10, same_source=same_pid)
        assert [(h.a, h.b) for h in hits] == [("A:1:B4", "B:2:B4")]

    def test_same_source_pairs_excluded(self):
        vals = [float(v) for v in range(20)]
        series = self._prepared({"A:1:B4": _series(vals), "A:1:B5": _series(vals)})

        def same_pid(a, b):
            return a.split(":")[:2] == b.split(":")[:2]

        assert find_series_mirrors(series, min_n=10, same_source=same_pid) == []

    def test_min_n_drops_thin_overlap(self):
        vals = [float(v) for v in range(5)]
        series = self._prepared({"A:1:B4": _series(vals), "B:2:B4": _series(vals)})
        assert find_series_mirrors(series, min_n=10) == []

    def test_disjoint_scopes_never_pair(self):
        vals = [float(v) for v in range(20)]
        series = self._prepared({"A:1:B4": _series(vals), "B:2:B4": _series(vals, offset=100_000)})
        assert find_series_mirrors(series, min_n=10) == []

    def test_offset_mirror_across_pids(self):
        vals = [float(v) for v in range(20)]
        series = self._prepared(
            {"A:1:LDC_TEMP": _series(vals), "B:2:B19": _series([v + 100 for v in vals])}
        )
        assert find_series_mirrors(series, min_n=10) == []
        hits = find_series_mirrors(series, min_n=10, allow_offset=True)
        assert len(hits) == 1
        assert hits[0].relation.offset == -100.0

    @pytest.mark.parametrize("sparse_first", [False, True])
    def test_forward_fill_lets_a_run_length_signal_mirror(self, sparse_first):
        """A run-length signal has no sample at most instants — without the carry
        forward it can only mirror on the few rows it happens to be re-polled."""
        # Both sides must vary (a constant pair is no evidence), so the run-length
        # side has two transitions and the dense side samples through both.
        vals = [1.0] * 15 + [2.0] * 15
        dense = _series(vals, step=5.0)
        sparse = [
            TimePoint(BASE, 1.0, BASE + timedelta(seconds=75)),
            TimePoint(BASE + timedelta(seconds=75), 2.0, BASE + timedelta(seconds=150)),
        ]
        # The sparse side must be found whichever way the labels sort: the sweep
        # picks the denser clock as the reference, not the alphabetically first.
        dense_key, sparse_key = ("B:2:B4", "A:1:B4") if sparse_first else ("A:1:B4", "B:2:B4")
        filled = find_series_mirrors(
            {dense_key: prepare_series(dense), sparse_key: prepare_series(sparse)},
            tol_s=5.0,
            min_n=10,
        )
        strict = find_series_mirrors(
            {
                dense_key: prepare_series(dense),
                sparse_key: prepare_series([TimePoint(tp.dt, tp.value) for tp in sparse]),
            },
            tol_s=5.0,
            min_n=10,
        )
        assert len(filled) == 1 and filled[0].n == 30
        assert strict == []

    def test_ranked_best_match_first(self):
        vals = [float(v) for v in range(20)]
        near = list(vals)
        near[3] += 1
        series = self._prepared(
            {"A:1:B4": _series(vals), "B:2:B4": _series(vals), "C:3:B4": _series(near)}
        )
        hits = find_series_mirrors(series, min_n=10)
        assert hits[0].relation.fraction == 1.0
        assert hits[-1].relation.fraction < 1.0

    def test_empty(self):
        assert find_series_mirrors({}) == []


class TestDefaults:
    def test_default_match_admits_poll_skew_but_not_noise(self):
        assert 0.5 < DEFAULT_MIRROR_MATCH < 1.0

    @pytest.mark.parametrize("fraction", [0.9, 1.0])
    def test_fraction_never_demands_more_rows_than_exist(self, fraction):
        assert match_pair([1.0, 2.0], [1.0, 2.0], min_fraction=fraction) is not None
