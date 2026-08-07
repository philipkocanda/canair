"""Tests for `canlib.counters` — monotonic counter detection.

Covers the evidence score, the three fingerprints (accumulator / cycle / timer),
and — most importantly — the rejection rules, since a brute-force width x
endianness sweep produces far more spurious windows than real ones.
"""

from __future__ import annotations

import math

from canlib.counters import (
    MAX_MSB_JUMP,
    cluster_counters,
    find_counters,
    mono_bits,
    nearest_tick,
    split_sessions,
)

SESSION_GAP = 300.0


def _columns(values: list[int], width: int, *, little: bool = False):
    """Split ``values`` into ``width`` byte columns in payload order, keyed 0..width-1.

    Payload position 0 is the most-significant byte for a big-endian layout and
    the least-significant for little-endian — the same convention
    :func:`find_counters` assumes of adjacent columns.
    """
    order = range(width) if little else range(width - 1, -1, -1)
    cols: list[tuple[object, list[int]]] = []
    for pos, shift in enumerate(order):
        cols.append((pos, [(v >> (8 * shift)) & 0xFF for v in values]))
    return cols


def _canonical(cands, width: int, *, little: bool | None = None):
    """The canonical candidates of a given width — what the tool actually reports."""
    return [
        c
        for c in cands
        if c.width == width and c.canonical and (little is None or c.little is little)
    ]


def _dense_ts(n: int, *, step: float = 5.0, t0: float = 0.0) -> list[float]:
    return [t0 + i * step for i in range(n)]


def _sessioned_ts(n_sessions: int, per_session: int, *, step: float = 5.0) -> list[float]:
    ts: list[float] = []
    t = 0.0
    for _ in range(n_sessions):
        for i in range(per_session):
            ts.append(t + i * step)
        t += 86400.0  # next day — well past SESSION_GAP
    return ts


class TestMonoBits:
    def test_clean_run_is_exactly_k_bits(self):
        assert mono_bits(10, 0) == 10.0
        assert mono_bits(1, 0) == 1.0

    def test_no_moving_steps_is_zero(self):
        assert mono_bits(0, 0) == 0.0

    def test_violation_costs_evidence(self):
        assert 0.0 < mono_bits(9, 1) < 9.0

    def test_all_down_is_zero_evidence(self):
        assert mono_bits(0, 10) < 1e-9

    def test_large_k_does_not_overflow(self):
        # A 4000-sample accumulator overflows a float binomial coefficient if the
        # tail is summed directly rather than in log space.
        assert mono_bits(4000, 0) == 4000.0
        v = mono_bits(4000, 5)
        assert math.isfinite(v) and v > 100.0

    def test_half_up_half_down_is_near_zero(self):
        assert mono_bits(50, 50) < 1.0


class TestSplitSessions:
    def test_single_run(self):
        assert split_sessions([0.0, 5.0, 10.0], gap_s=SESSION_GAP) == [(0, 3)]

    def test_gap_splits(self):
        ts = [0.0, 5.0, 100000.0, 100005.0]
        assert split_sessions(ts, gap_s=SESSION_GAP) == [(0, 2), (2, 4)]

    def test_empty(self):
        assert split_sessions([], gap_s=SESSION_GAP) == []


class TestNearestTick:
    def test_exact_one_second(self):
        got = nearest_tick(1.0)
        assert got is not None and got[0] == "1 s" and got[1] == 0.0

    def test_within_tolerance(self):
        got = nearest_tick(0.98)
        assert got is not None and got[0] == "1 s"

    def test_implausible_rate_rejected(self):
        assert nearest_tick(7.3) is None


class TestFindAccumulator:
    def test_dense_three_byte_accumulator(self):
        # 0x010000 + k: constant non-zero MSB, moving LSB — a running total.
        values = [0x010000 + k for k in range(40)]
        cands = find_counters(
            _columns(values, 3), _dense_ts(40), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        accs = [c for c in cands if c.kind == "accumulator"]
        assert accs, "a dense rising 3-byte value must be found as an accumulator"
        # A constant middle+high byte makes the reversed reading monotonic too, but
        # only the true layout is canonical (the other's LSB never moves).
        best = _canonical(cands, 3, little=False)
        assert len(best) == 1
        assert best[0].keys == (0, 1, 2)
        assert best[0].n_down == 0
        assert best[0].bits == 39.0  # 39 clean up-steps
        assert best[0].first == 0x010000
        assert best[0].last == 0x010000 + 39

    def test_little_endian_window_detected(self):
        values = [0x010000 + k for k in range(40)]
        cands = find_counters(
            _columns(values, 3, little=True),
            _dense_ts(40),
            session_gap_s=SESSION_GAP,
            min_bits=4.0,
        )
        le = _canonical(cands, 3, little=True)
        assert le, "a little-endian counter must be found with little=True"
        assert le[0].last == 0x010000 + 39

    def test_decreasing_series_is_not_a_counter(self):
        values = [0x010000 - k for k in range(40)]
        cands = find_counters(
            _columns(values, 3), _dense_ts(40), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        assert not [c for c in cands if c.kind in ("accumulator", "cycle")]


class TestFindCycleCounter:
    def test_flat_within_sessions_is_a_cycle_count(self):
        # +1 between sessions, constant inside each — an ignition/trip count.
        per = 8
        values = [10000 + s for s in range(5) for _ in range(per)]
        cands = find_counters(
            _columns(values, 2),
            _sessioned_ts(5, per),
            session_gap_s=SESSION_GAP,
            min_bits=4.0,
        )
        cycles = [c for c in cands if c.kind == "cycle"]
        assert cycles
        best = max(cycles, key=lambda c: c.width)
        assert best.flat_sessions == 5
        assert best.n_sessions == 5
        assert best.boundary_steps == 4
        assert best.flat_frac == 1.0

    def test_moving_within_session_is_accumulator_not_cycle(self):
        per = 8
        values = [10000 + s * 100 + i for s in range(5) for i in range(per)]
        cands = find_counters(
            _columns(values, 2),
            _sessioned_ts(5, per),
            session_gap_s=SESSION_GAP,
            min_bits=4.0,
        )
        kinds = {c.kind for c in cands if c.width == 2}
        assert "accumulator" in kinds
        assert "cycle" not in kinds


class TestFindRunTimer:
    def test_resetting_seconds_counter(self):
        # 16-bit seconds-since-key-on, resetting to 0 each session.
        per, step = 40, 5.0
        values = [i * int(step) for _ in range(4) for i in range(per)]
        cands = find_counters(
            _columns(values, 2),
            _sessioned_ts(4, per, step=step),
            session_gap_s=SESSION_GAP,
            min_bits=4.0,
        )
        timers = [c for c in cands if c.kind == "timer"]
        assert timers, "a per-session ramp that resets to 0 must be a run timer"
        t = timers[0]
        assert t.tick == "1 s"
        assert t.tick_err is not None and t.tick_err < 0.05
        assert t.slope_cv is not None and t.slope_cv < 0.01
        assert t.reset_frac is not None and t.reset_frac < 0.05
        assert len(t.sessions) == 4

    def test_accumulator_low_byte_is_not_reported_as_a_timer(self):
        # The alias that matters: a wide accumulator's low byte wraps every 256
        # ticks, which looks exactly like a resetting timer viewed alone.
        n = 4000
        values = [500000 + k for k in range(n)]
        cands = find_counters(
            _columns(values, 3), _dense_ts(n), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        assert [c for c in cands if c.kind == "accumulator"]
        assert not [c for c in cands if c.kind == "timer"], (
            "the accumulator's wrapping low byte must not resurface as a run timer"
        )


class TestRejectionRules:
    def test_flags_turning_on_are_not_a_counter(self):
        # 0x000000 -> 0x010001: monotone, but the "increment" IS the whole value.
        values = [0x000000] * 20 + [0x010001] * 20
        cands = find_counters(
            _columns(values, 3), _dense_ts(40), session_gap_s=SESSION_GAP, min_bits=1.0
        )
        wide = [c for c in cands if c.width == 3]
        assert not wide, "a block of flags flipping 0->1 must be rejected (step_ratio)"

    def test_msb_jump_rejects_a_straddling_window(self):
        # A 1-byte counter with a dead neighbour: read as 2 bytes the MSB leaps by
        # 10 per step, which no real counter does.
        values = [k * 10 * 256 for k in range(20)]
        cands = find_counters(
            _columns(values, 2), _dense_ts(20), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        assert not [c for c in cands if c.width == 2 and c.msb_jump > MAX_MSB_JUMP]
        # ...while the genuine single-byte counter underneath is still found.
        assert [c for c in cands if c.width == 1]

    def test_constant_window_yields_nothing(self):
        values = [0x1234] * 30
        assert (
            find_counters(
                _columns(values, 2), _dense_ts(30), session_gap_s=SESSION_GAP, min_bits=1.0
            )
            == []
        )

    def test_below_min_bits_is_excluded(self):
        values = [100, 100, 101, 101]  # exactly 1 up-step = 1 bit
        cands = find_counters(
            _columns(values, 2), _dense_ts(4), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        assert cands == []

    def test_too_few_samples(self):
        assert find_counters(_columns([1], 1), [0.0], session_gap_s=SESSION_GAP) == []
        assert find_counters([], [], session_gap_s=SESSION_GAP) == []


class TestCanonicalWindow:
    def test_constant_zero_high_byte_is_padding(self):
        # 0x0000NN — the leading zero byte adds no magnitude, so the 2-byte window
        # is not canonical while the 1-byte one is.
        values = list(range(10, 200))
        cands = find_counters(
            _columns(values, 2), _dense_ts(len(values)), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        by_width = {c.width: c for c in cands if not c.little}
        assert by_width[2].canonical is False
        assert by_width[1].canonical is True
        assert _canonical(cands, 2, little=False) == []

    def test_constant_nonzero_high_byte_is_real_magnitude(self):
        values = [0x0100 + k for k in range(100)]
        cands = find_counters(
            _columns(values, 2), _dense_ts(100), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        wide = [c for c in cands if c.width == 2 and not c.little]
        assert wide and wide[0].canonical is True

    def test_constant_low_byte_makes_window_non_canonical(self):
        # 0xNN00 — a constant low byte means the window is over-extended right.
        values = [k * 256 for k in range(1, 60)]
        cands = find_counters(
            _columns(values, 2), _dense_ts(59), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        wide = [c for c in cands if c.width == 2 and not c.little]
        assert wide and wide[0].canonical is False


class TestClusterCounters:
    def test_nested_prefixes_collapse_to_widest_canonical(self):
        values = [0x010000 + k for k in range(60)]
        cands = find_counters(
            _columns(values, 3), _dense_ts(60), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        groups = cluster_counters(cands)
        monotonic = [(r, m) for r, m in groups if r.kind in ("accumulator", "cycle")]
        assert len(monotonic) == 1, "one physical counter must yield one representative"
        rep, members = monotonic[0]
        assert rep.width == 3 and rep.canonical
        assert members, "the narrower prefix windows are subsumed, not dropped"

    def test_timers_cluster_separately_from_monotonic(self):
        per, step = 40, 5.0
        ramp = [i * int(step) for _ in range(4) for i in range(per)]
        ts = _sessioned_ts(4, per, step=step)
        cands = find_counters(_columns(ramp, 2), ts, session_gap_s=SESSION_GAP, min_bits=4.0)
        groups = cluster_counters(cands)
        assert any(r.kind == "timer" for r, _m in groups)

    def test_empty_input(self):
        assert cluster_counters([]) == []


class TestBoundaryGradient:
    """The bit-flip boundary-gradient tie-break (Part F)."""

    def test_high_for_an_aligned_big_endian_counter(self):
        from canlib.counters import boundary_gradient
        from canlib.triage import bit_flip_rates

        values = list(range(0, 600))  # a 2-byte rising counter
        cols = _columns(values, 2)  # big-endian: key0=MSB, key1=LSB
        bf = {k: bit_flip_rates(v) for k, v in cols}
        keys = tuple(k for k, _ in cols)
        score = boundary_gradient(keys, little=False, bit_flip=bf)
        assert score is not None and score >= 0.9

    def test_lower_when_the_endianness_is_wrong(self):
        from canlib.counters import boundary_gradient
        from canlib.triage import bit_flip_rates

        values = list(range(0, 600))
        cols = _columns(values, 2)  # laid out big-endian
        bf = {k: bit_flip_rates(v) for k, v in cols}
        keys = tuple(k for k, _ in cols)
        good = boundary_gradient(keys, little=False, bit_flip=bf)
        bad = boundary_gradient(keys, little=True, bit_flip=bf)  # read LSB<->MSB flipped
        assert good is not None and bad is not None and good > bad

    def test_none_for_single_byte_or_missing_rates(self):
        from canlib.counters import boundary_gradient

        assert boundary_gradient((0,), little=False, bit_flip={0: [0.0] * 8}) is None
        assert boundary_gradient((0, 1), little=False, bit_flip={0: [0.0] * 8}) is None

    def test_tie_break_prefers_the_aligned_endianness(self):
        """Given two same-width/-evidence windows over the same bytes, the gradient
        picks the correctly-aligned endianness as the representative."""
        from canlib.counters import CounterCandidate, cluster_counters
        from canlib.triage import bit_flip_rates

        values = list(range(0, 600))
        cols = _columns(values, 2)  # big-endian layout
        bf = {k: bit_flip_rates(v) for k, v in cols}
        keys = tuple(k for k, _ in cols)

        def _cand(little: bool) -> CounterCandidate:
            return CounterCandidate(
                keys=keys,
                little=little,
                kind="accumulator",
                bits=20.0,
                n=600,
                n_distinct=600,
                n_up=599,
                n_down=0,
                n_varying=2,
                canonical=True,
                first=0.0,
                last=599.0,
                lo=0.0,
                hi=599.0,
                med_step=1.0,
                max_step=1.0,
                msb_jump=0.0,
                step_ratio=0.01,
                span_s=3000.0,
                n_sessions=1,
                flat_sessions=0,
                boundary_steps=0,
            )

        groups = cluster_counters([_cand(little=True), _cand(little=False)], bit_flip=bf)
        assert len(groups) == 1, "same bytes -> one physical counter"
        rep, _members = groups[0]
        assert rep.little is False, "the aligned big-endian window must win the tie-break"

    def test_no_bit_flip_is_byte_identical_ranking(self):
        # Without bit_flip the representative choice must not change (guards the 30
        # existing tests' behaviour).
        values = [0x010000 + k for k in range(60)]
        cands = find_counters(
            _columns(values, 3), _dense_ts(60), session_gap_s=SESSION_GAP, min_bits=4.0
        )
        assert cluster_counters(cands) == cluster_counters(cands, bit_flip=None)
