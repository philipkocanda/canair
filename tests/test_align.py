"""Tests for the time-aligned cross-signal analysis primitives (canlib.align)."""

import json
from datetime import datetime, timedelta

import pytest

from canlib.align import (
    DEFAULT_JOIN_TOL_S,
    SignalRef,
    TimePoint,
    align_many,
    detrend_by_session,
    extract_series,
    join_fill_stats,
    join_indices,
    join_nearest,
    join_nearest_presorted,
    join_prepared,
    load_reference_file,
    load_signal_captures,
    prepare_series,
    series_time_ranges_disjoint,
    timestamps_disjoint,
)
from canlib.fill import FillPolicy


# ---------------------------------------------------------------------------
# SignalRef parsing
# ---------------------------------------------------------------------------
class TestSignalRefParse:
    def test_param_ref(self):
        r = SignalRef.parse("ESC:22C101:REAL_SPEED_KMH")
        assert (r.ecu, r.pid, r.name_or_expr) == ("ESC", "22C101", "REAL_SPEED_KMH")

    def test_expr_with_colons_kept_intact(self):
        r = SignalRef.parse("MCU:2102:[S10:S11]")
        assert r.ecu == "MCU"
        assert r.pid == "2102"
        assert r.name_or_expr == "[S10:S11]"

    def test_label_roundtrips(self):
        assert SignalRef.parse("A:B:C").label == "A:B:C"

    @pytest.mark.parametrize("bad", ["", "ECU", "ECU:PID", "ECU::EXPR", ":PID:EXPR", "ECU:PID:"])
    def test_invalid_raises(self, bad):
        with pytest.raises(ValueError):
            SignalRef.parse(bad)


# ---------------------------------------------------------------------------
# join_nearest
# ---------------------------------------------------------------------------
def _tp(sec: float, val: float) -> TimePoint:
    return TimePoint(datetime(2026, 7, 22, 9, 0, 0) + _sec(sec), val)


def _sec(s: float):
    from datetime import timedelta

    return timedelta(seconds=s)


class TestJoinNearest:
    def test_skewed_within_tol_joins(self):
        ref = [_tp(0.0, 10.0), _tp(1.0, 20.0)]
        cand = [_tp(0.3, 100.0), _tp(1.2, 200.0)]
        xs, ys, n = join_nearest(ref, cand, tol_s=1.0)
        assert n == 2
        assert xs == [10.0, 20.0]
        assert ys == [100.0, 200.0]

    def test_tight_tol_drops_out_of_range(self):
        ref = [_tp(0.0, 10.0), _tp(5.0, 20.0)]
        cand = [_tp(0.02, 100.0)]  # only near the first ref point
        xs, ys, n = join_nearest(ref, cand, tol_s=0.05)
        assert n == 1
        assert xs == [10.0] and ys == [100.0]

    def test_picks_closest_candidate(self):
        ref = [_tp(1.0, 1.0)]
        cand = [_tp(0.4, 1.0), _tp(1.1, 2.0), _tp(2.0, 3.0)]
        _, ys, n = join_nearest(ref, cand, tol_s=2.5)
        assert n == 1 and ys == [2.0]  # 1.1s is nearest to 1.0s

    def test_empty_inputs(self):
        assert join_nearest([], [_tp(0, 1)]) == ([], [], 0)
        assert join_nearest([_tp(0, 1)], []) == ([], [], 0)

    def test_default_tol_covers_round_robin_skew(self):
        # An 8-ECU round-robin poll skews adjacent-in-cycle ECUs ~3.4s; the
        # default window must join them (the old 2.5s silently dropped the pair).
        ref = [_tp(0.0, 10.0)]
        cand = [_tp(3.4, 100.0)]
        assert join_nearest(ref, cand, tol_s=DEFAULT_JOIN_TOL_S)[2] == 1
        assert join_nearest(ref, cand, tol_s=2.5)[2] == 0  # regression guard


class TestDefaultJoinTol:
    def test_default_is_five_seconds(self):
        # Shared by align/correlate/hunt/investigate/discriminate; widened
        # 2.5->5.0 for multi-ECU round-robin monitor sessions.
        assert DEFAULT_JOIN_TOL_S == 5.0

    def test_align_parser_defaults_to_shared_constant(self):
        import argparse

        from canlib.commands import align as align_cmd

        sub = argparse.ArgumentParser().add_subparsers()
        align_cmd.add_parser(sub)
        ns = sub.choices[align_cmd.NAME].parse_args(["A:B:C", "D:E:F"])
        assert ns.join_tol == DEFAULT_JOIN_TOL_S

    def test_presorted_matches_join_nearest(self):
        ref = [_tp(0.0, 10.0), _tp(1.0, 20.0)]
        cand = [_tp(1.2, 200.0), _tp(0.3, 100.0)]  # unsorted
        cand_sorted = sorted(cand, key=lambda tp: tp.dt)
        assert join_nearest_presorted(ref, cand_sorted, tol_s=1.0) == join_nearest(
            ref, cand, tol_s=1.0
        )


class TestJoinIndices:
    """The single join implementation, expressed as reusable index lists.

    ``join_prepared`` is a thin wrapper over it, so these two must never diverge —
    the bucketed correlation sweep reuses one mapping across many signals, and a
    tie-breaking difference between the "fast" and "slow" paths would be invisible
    in the ranked output.
    """

    def _pair(self, ref_offsets, cand_offsets):
        ref = prepare_series([_tp(o, o * 10) for o in ref_offsets])
        cand = prepare_series([_tp(o, o * 100) for o in cand_offsets])
        return ref, cand

    @pytest.mark.parametrize(
        "ref_offsets,cand_offsets,tol",
        [
            ([0.0, 1.0], [0.3, 1.2], 1.0),  # both in range
            ([0.0, 5.0], [0.02], 0.05),  # one out of range
            ([1.0], [0.4, 1.1, 2.0], 2.5),  # nearest of three wins
            ([0.0, 1.0, 2.0], [], 1.0),  # empty candidate
            ([], [0.0], 1.0),  # empty reference
            ([0.0, 0.0, 1.0], [0.0, 1.0], 1.0),  # duplicate reference stamps
            ([0.0, 1.0], [0.5, 0.5], 1.0),  # duplicate candidate stamps
            ([2.0], [1.0, 3.0], 1.0),  # exact tie either side
            ([1.0], [0.0, 2.0], 1.0),  # tie at exactly ±tol
            ([0.0, 1.0, 2.0], [0.0, 1.0, 2.0], 0.0),  # zero tolerance
        ],
    )
    def test_agrees_with_join_prepared(self, ref_offsets, cand_offsets, tol):
        ref, cand = self._pair(ref_offsets, cand_offsets)
        ri, ci = join_indices(ref.ts, cand.ts, tol)
        xs, ys, n = join_prepared(ref, cand, tol_s=tol)
        assert len(ri) == len(ci) == n
        assert [ref.values[k] for k in ri] == xs
        assert [cand.values[j] for j in ci] == ys

    def test_exact_tie_prefers_the_earlier_candidate(self):
        # Two candidates exactly `tol` away on either side: the sweep tests j-1
        # before j and keeps a strictly-smaller delta, so the EARLIER one wins.
        # Pinned because the bucketed sweep depends on this being stable.
        ref, cand = self._pair([2.0], [1.0, 3.0])
        ri, ci = join_indices(ref.ts, cand.ts, 1.0)
        assert (ri, ci) == ([0], [0])

    def test_drops_reference_points_with_no_candidate_in_range(self):
        ref, cand = self._pair([0.0, 10.0, 20.0], [0.1, 20.2])
        ri, ci = join_indices(ref.ts, cand.ts, 0.5)
        assert ri == [0, 2]  # the t=10 reference point has nothing within 0.5s
        assert ci == [0, 1]

    def test_indices_are_reusable_across_signals_sharing_a_clock(self):
        """The whole point: one mapping, many signals.

        Two signals decoded from the same captures share a timestamp vector, so the
        join computed from that vector must apply verbatim to either one's values.
        """
        clock = [0.0, 1.0, 2.0, 3.0]
        a = prepare_series([_tp(o, o) for o in clock])
        b = prepare_series([_tp(o, o * -5.0) for o in clock])  # same stamps, other values
        other = prepare_series([_tp(o + 0.4, o) for o in clock])
        ri, ci = join_indices(a.ts, other.ts, 1.0)
        for signal in (a, b):
            xs, ys, _n = join_prepared(signal, other, tol_s=1.0)
            assert [signal.values[k] for k in ri] == xs
            assert [other.values[j] for j in ci] == ys


class TestTimestampsDisjoint:
    def test_matches_the_prepared_series_guard(self):
        a = prepare_series([_tp(i, i) for i in range(5)])
        b = prepare_series([_tp(i + 100, i) for i in range(5)])
        assert timestamps_disjoint(a.ts, b.ts, 1.0) is True
        assert series_time_ranges_disjoint(a, b, 1.0) is True
        assert timestamps_disjoint(a.ts, a.ts, 1.0) is False
        assert series_time_ranges_disjoint(a, a, 1.0) is False

    def test_empty_is_disjoint(self):
        assert timestamps_disjoint([], [1.0], 1.0) is True
        assert timestamps_disjoint([1.0], [], 1.0) is True

    def test_tolerance_bridges_a_gap(self):
        a = prepare_series([_tp(0.0, 1.0)])
        b = prepare_series([_tp(4.0, 1.0)])
        assert timestamps_disjoint(a.ts, b.ts, 1.0) is True
        assert timestamps_disjoint(a.ts, b.ts, 5.0) is False


# ---------------------------------------------------------------------------
# Forward fill — a run-length sample is a segment, not a point
# ---------------------------------------------------------------------------
def _held(sec: float, val: float, until: float) -> TimePoint:
    """A sample at ``sec`` whose value is known to hold until ``until``."""
    base = datetime(2026, 7, 22, 9, 0, 0)
    return TimePoint(base + _sec(sec), val, base + _sec(until))


class TestForwardFill:
    def test_carries_a_value_onto_rows_beyond_the_join_window(self):
        """The measured case: a value known for a whole window, sampled twice."""
        ref = [_tp(float(i), float(i)) for i in range(0, 200, 5)]
        cand = [_held(0.0, 1.0, 195.0)]
        _xs, ys, n = join_prepared(prepare_series(ref), prepare_series(cand), 5.0)
        assert n == len(ref)
        assert set(ys) == {1.0}

    def test_a_real_nearby_sample_always_wins(self):
        """Filling is a fallback: it must never displace a measured value."""
        ref = [_tp(10.0, 0.0)]
        cand = [_held(0.0, 1.0, 100.0), _tp(11.0, 2.0)]
        _xs, ys, _n = join_prepared(prepare_series(ref), prepare_series(cand), 5.0)
        assert ys == [2.0]

    def test_never_fills_past_the_hold(self):
        ref = [_tp(50.0, 0.0)]
        cand = [_held(0.0, 1.0, 20.0)]
        assert join_prepared(prepare_series(ref), prepare_series(cand), 5.0)[2] == 0

    def test_never_fills_backwards(self):
        """A value is known *after* it was read, never before."""
        ref = [_tp(0.0, 0.0)]
        cand = [_held(100.0, 1.0, 200.0)]
        assert join_prepared(prepare_series(ref), prepare_series(cand), 5.0)[2] == 0

    def test_a_series_without_holds_joins_identically(self):
        """No hold vector must be bit-identical to the pre-fill strict join."""
        ref = [_tp(float(i), float(i)) for i in range(0, 100, 5)]
        cand = [_tp(2.0, 1.0), _tp(60.0, 2.0)]
        assert prepare_series(cand).hold_ts is None
        strict = join_indices([tp.dt.timestamp() for tp in ref], [tp.dt.timestamp() for tp in cand])
        assert strict == join_indices(
            [tp.dt.timestamp() for tp in ref],
            [tp.dt.timestamp() for tp in cand],
            DEFAULT_JOIN_TOL_S,
            None,
        )

    def test_align_many_fills_its_columns(self):
        ref = [_tp(float(i), float(i)) for i in range(0, 40, 5)]
        _vals, cols = align_many(ref, {"B": [_held(0.0, 7.0, 35.0)]}, tol_s=1.0)
        assert cols["B"] == [7.0] * len(ref)


class TestJoinFillStats:
    def test_splits_measured_from_reconstructed(self):
        ref = [_tp(float(i), float(i)) for i in range(0, 40, 5)]
        stats = join_fill_stats(prepare_series(ref), prepare_series([_held(0.0, 7.0, 35.0)]), 1.0)
        assert stats.n_direct == 1  # only the t=0 row was actually sampled
        assert stats.n_filled == len(ref) - 1
        assert stats.n == len(ref)
        assert stats.max_hold_s == pytest.approx(35.0)

    def test_reports_which_rows_were_filled(self):
        ref = [_tp(0.0, 0.0), _tp(20.0, 1.0)]
        stats = join_fill_stats(prepare_series(ref), prepare_series([_held(0.0, 7.0, 30.0)]), 1.0)
        assert stats.filled_rows == frozenset({1})

    def test_nothing_filled_without_holds(self):
        ref = [_tp(0.0, 0.0), _tp(20.0, 1.0)]
        stats = join_fill_stats(prepare_series(ref), prepare_series([_tp(0.0, 7.0)]), 1.0)
        assert (stats.n_direct, stats.n_filled, stats.max_hold_s) == (1, 0, 0.0)


# ---------------------------------------------------------------------------
# align_many
# ---------------------------------------------------------------------------
class TestAlignMany:
    def test_keeps_every_reference_row_padding_none(self):
        ref = [_tp(0.0, 1.0), _tp(10.0, 2.0)]
        others = {"B": [_tp(0.2, 100.0)]}  # only near first ref
        ref_vals, cols = align_many(ref, others, tol_s=1.0)
        assert ref_vals == [1.0, 2.0]
        assert cols["B"] == [100.0, None]

    def test_multiple_series(self):
        ref = [_tp(0.0, 1.0), _tp(1.0, 2.0)]
        others = {
            "B": [_tp(0.1, 10.0), _tp(1.1, 20.0)],
            "C": [_tp(0.1, 30.0), _tp(1.1, 40.0)],
        }
        _, cols = align_many(ref, others, tol_s=1.0)
        assert cols["B"] == [10.0, 20.0]
        assert cols["C"] == [30.0, 40.0]


# ---------------------------------------------------------------------------
# load_signal_captures + extract_series (fixture capture files)
# ---------------------------------------------------------------------------
def _write_captures(tmp_path):
    """Two co-polled ECUs on one date; ECU short names used directly as the
    ``ecu`` field (ecu_name_from_ref passes unresolved refs through unchanged),
    plus one untimed scan capture and one untimed payload capture."""
    doc = {
        "sessions": [
            {
                "date": "2026-07-22",
                "label": "drive",
                "vehicle_states": ["driving"],
                "captures": [
                    # ESC speed: 61 01 .. B2 = data byte 0 ; keep payloads simple
                    {"ecu": "ESC", "pid": "22C101", "payload": "62C101000A", "time": "09:00:00"},
                    {"ecu": "ESC", "pid": "22C101", "payload": "62C1010014", "time": "09:00:02"},
                    {"ecu": "AAF", "pid": "2181", "payload": "6181000A", "time": "09:00:00.3"},
                    {"ecu": "AAF", "pid": "2181", "payload": "61810014", "time": "09:00:02.2"},
                    # untimed payload capture (grandfathered, dropped from joins)
                    {"ecu": "AAF", "pid": "2181", "payload": "61810099"},
                    # scan capture (no payload) — never a time series
                    {
                        "ecu": "AAF",
                        "pid": "scan 22 0100-010F",
                        "scan_results": {"responding": []},
                    },
                ],
            }
        ]
    }
    (tmp_path / "2026-07-22.json").write_text(json.dumps(doc))
    return tmp_path


class TestLoadSignalCaptures:
    def test_groups_and_counts_no_time(self, tmp_path):
        _write_captures(tmp_path)
        loaded = load_signal_captures([("ESC", "22C101"), ("AAF", "2181")], captures_dir=tmp_path)
        esc = loaded[("ESC", "22C101")]
        aaf = loaded[("AAF", "2181")]
        assert len(esc.captures) == 2
        assert len(aaf.captures) == 2  # 2 timed; untimed excluded, scan ignored
        assert aaf.n_no_time == 1

    def test_scope_state_filter(self, tmp_path):
        _write_captures(tmp_path)
        loaded = load_signal_captures([("ESC", "22C101")], state="charging", captures_dir=tmp_path)
        assert len(loaded[("ESC", "22C101")].captures) == 0


class TestExtractSeries:
    def test_raw_expression(self, tmp_path):
        _write_captures(tmp_path)
        loaded = load_signal_captures([("AAF", "2181")], captures_dir=tmp_path)
        # AAF payload 6181 00 0A -> WiCAN B3=0x00, B4=0x0A ; raw B4
        series = extract_series(loaded[("AAF", "2181")], "B4")
        assert [tp.value for tp in series] == [10.0, 20.0]

    def test_named_param_resolves_expression(self, tmp_path):
        _write_captures(tmp_path)
        loaded = load_signal_captures([("ESC", "22C101")], captures_dir=tmp_path)
        params = {"REAL_SPEED_KMH": {"expression": "B5"}}
        # ESC payload 62 C1 01 00 0A -> B5 = 0x0A
        series = extract_series(loaded[("ESC", "22C101")], "REAL_SPEED_KMH", parameters=params)
        assert [tp.value for tp in series] == [10.0, 20.0]

    def test_cross_ecu_join_end_to_end(self, tmp_path):
        _write_captures(tmp_path)
        loaded = load_signal_captures([("ESC", "22C101"), ("AAF", "2181")], captures_dir=tmp_path)
        esc = extract_series(loaded[("ESC", "22C101")], "B5")
        aaf = extract_series(loaded[("AAF", "2181")], "B4")
        xs, ys, n = join_nearest(esc, aaf, tol_s=1.0)
        assert n == 2
        assert xs == [10.0, 20.0]
        assert ys == [10.0, 20.0]


class TestExtractSeriesFill:
    """``extract_series`` is where a capture's validity window becomes a sample's."""

    def _run_length(self, tmp_path):
        doc = {
            "sessions": [
                {
                    "date": "2026-07-22",
                    "label": "charge",
                    "keep_mode": "changes",
                    "captures": [
                        {"ecu": "AAF", "pid": "2181", "payload": "6181000A", "time": "09:00:00"},
                        # nothing stored for 10 minutes: the value did not change
                        {"ecu": "AAF", "pid": "2181", "payload": "61810014", "time": "09:10:00"},
                        {"ecu": "AAF", "pid": "2181", "payload": "6181001E", "time": "09:20:00"},
                    ],
                }
            ]
        }
        (tmp_path / "2026-07-22.json").write_text(json.dumps(doc))
        return load_signal_captures([("AAF", "2181")], captures_dir=tmp_path)[("AAF", "2181")]

    def test_no_policy_means_point_samples(self, tmp_path):
        series = extract_series(self._run_length(tmp_path), "B4")
        assert all(tp.hold_until is None for tp in series)

    def test_auto_holds_a_run_length_session(self, tmp_path):
        series = extract_series(self._run_length(tmp_path), "B4", fill=FillPolicy())
        assert series[0].hold_until == series[1].dt
        assert series[-1].hold_until is None  # last row of the session — nothing to carry

    def test_max_hold_caps_the_segment(self, tmp_path):
        series = extract_series(self._run_length(tmp_path), "B4", fill=FillPolicy(max_hold_s=60))
        assert (series[0].hold_until - series[0].dt).total_seconds() == 60

    def test_holds_come_from_the_capture_timeline_not_the_decoded_series(self, tmp_path):
        """A capture that fails to decode still closes the previous run: a stored row
        means the payload changed, whether or not this expression can read it."""
        lp = self._run_length(tmp_path)
        # B40 is past the end of every frame, so only some captures decode — the
        # hold vector must still be derived from all of them.
        holds = lp.timed_holds(FillPolicy())
        assert holds[0] is not None and len(holds) == 3


# ---------------------------------------------------------------------------
# load_reference_file — external timestamp,value series
# ---------------------------------------------------------------------------
class TestLoadReferenceFile:
    def test_iso_timestamps_with_header(self, tmp_path):
        p = tmp_path / "grid.csv"
        p.write_text(
            "timestamp,value\n"
            "2026-07-22T09:00:00,225.0\n"
            "2026-07-22 09:00:01.500,231.0\n"
            "2026-07-22T09:00:03,222.0\n"
        )
        series, label = load_reference_file(p)
        assert label == "grid.csv"
        assert [tp.value for tp in series] == [225.0, 231.0, 222.0]
        # header row skipped, sorted by time
        assert series[0].dt < series[1].dt < series[2].dt
        assert series[0].dt == datetime(2026, 7, 22, 9, 0, 0)

    def test_epoch_seconds(self, tmp_path):
        p = tmp_path / "epoch.csv"
        base = datetime(2026, 7, 22, 9, 0, 0)
        epoch = base.timestamp()
        p.write_text(f"{epoch},1.0\n{epoch + 2},2.0\n")
        series, _ = load_reference_file(p)
        assert [tp.value for tp in series] == [1.0, 2.0]
        assert series[0].dt == base

    def test_naive_datetimes_join_with_captures(self, tmp_path):
        # External ref timestamps must be naive so they compare with capture
        # datetimes (which are naive local); a tz-aware ISO string is coerced.
        p = tmp_path / "tz.csv"
        p.write_text("2026-07-22T09:00:00+00:00,5.0\n")
        series, _ = load_reference_file(p)
        assert series[0].dt.tzinfo is None

    def test_malformed_rows_skipped(self, tmp_path):
        p = tmp_path / "messy.csv"
        p.write_text("not,a,timestamp\n2026-07-22T09:00:00,3.0\nbad,line\n,\n")
        series, _ = load_reference_file(p)
        assert [tp.value for tp in series] == [3.0]

    def test_no_usable_rows_raises(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("timestamp,value\nfoo,bar\n")
        with pytest.raises(ValueError, match="no usable"):
            load_reference_file(p)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="cannot read"):
            load_reference_file(tmp_path / "nope.csv")

    def test_relative_timestamps_warn(self, tmp_path, capsys):
        # A zero-based / relative log (all timestamps pre-2000) should warn that
        # it won't align to the captures' absolute clock.
        p = tmp_path / "relative.csv"
        p.write_text("0.0,1.0\n0.07,2.0\n0.14,3.0\n")
        series, _ = load_reference_file(p)
        assert len(series) == 3
        assert "relative" in capsys.readouterr().err.lower()

    def test_absolute_timestamps_no_warn(self, tmp_path, capsys):
        p = tmp_path / "abs.csv"
        p.write_text("2026-07-22T09:00:00,1.0\n2026-07-22T09:00:01,2.0\n")
        load_reference_file(p)
        assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# join_nearest_triple — three-way alignment for partial correlation
# ---------------------------------------------------------------------------
class TestJoinNearestTriple:
    def test_keeps_only_full_triples(self):
        from canlib.align import join_nearest_triple

        ref = [_tp(0.0, 1.0), _tp(1.0, 2.0), _tp(2.0, 3.0)]
        cand = [_tp(0.1, 10.0), _tp(1.1, 20.0)]  # no candidate near t=2
        ctrl = [_tp(0.2, 100.0), _tp(1.2, 200.0), _tp(2.1, 300.0)]
        xs, ys, zs, n = join_nearest_triple(ref, cand, ctrl, tol_s=0.5)
        assert n == 2  # t=2 dropped (no candidate)
        assert xs == [1.0, 2.0]
        assert ys == [10.0, 20.0]
        assert zs == [100.0, 200.0]

    def test_empty_when_control_missing(self):
        from canlib.align import join_nearest_triple

        ref = [_tp(0.0, 1.0)]
        cand = [_tp(0.1, 10.0)]
        xs, ys, zs, n = join_nearest_triple(ref, cand, [], tol_s=0.5)
        assert n == 0
        assert (xs, ys, zs) == ([], [], [])


class TestDetrendBySession:
    def test_removes_per_session_baseline(self):
        # Two sessions (a day apart) share the same in-session ramp but sit on
        # very different DC baselines — the case Pearson misranks across sessions.
        base = datetime(2026, 7, 22, 9, 0, 0)
        s1 = [TimePoint(base + _sec(i), 100.0 + i) for i in range(5)]
        s2 = [TimePoint(base + timedelta(days=1) + _sec(i), 400.0 + i) for i in range(5)]
        out = detrend_by_session(s1 + s2, gap_s=300.0)
        vals = [tp.value for tp in out]
        # Each session de-meaned to the same zero-centred ramp (mean of 0..4 = 2).
        assert vals[:5] == [-2.0, -1.0, 0.0, 1.0, 2.0]
        assert vals[5:] == [-2.0, -1.0, 0.0, 1.0, 2.0]

    def test_makes_level_signal_correlate(self):
        from canlib.stats import pearson

        base = datetime(2026, 7, 22, 9, 0, 0)
        # Reference ramps identically in both sessions; candidate tracks it but on
        # a different per-session offset. Raw pearson is wrecked by the offset.
        ref = [TimePoint(base + _sec(i), float(i)) for i in range(5)] + [
            TimePoint(base + timedelta(days=1) + _sec(i), float(i)) for i in range(5)
        ]
        cand = [TimePoint(base + _sec(i), 100.0 + i) for i in range(5)] + [
            TimePoint(base + timedelta(days=1) + _sec(i), 400.0 + i) for i in range(5)
        ]
        raw_r = pearson([t.value for t in ref], [t.value for t in cand])
        dref = detrend_by_session(ref)
        dcand = detrend_by_session(cand)
        det_r = pearson([t.value for t in dref], [t.value for t in dcand])
        assert raw_r is not None and det_r is not None
        assert abs(det_r) > abs(raw_r)
        assert det_r > 0.999  # in-session variation matches perfectly

    def test_timestamps_preserved(self):
        s = [_tp(0.0, 10.0), _tp(1.0, 20.0)]
        out = detrend_by_session(s)
        assert [tp.dt for tp in out] == [tp.dt for tp in s]

    def test_single_point_segment_unchanged(self):
        out = detrend_by_session([_tp(0.0, 42.0)])
        assert out[0].value == 42.0

    def test_empty(self):
        assert detrend_by_session([]) == []
