"""Tests for the time-aligned cross-signal analysis primitives (canlib.align)."""

from datetime import datetime

import pytest
import yaml

from canlib.align import (
    SignalRef,
    TimePoint,
    align_many,
    aligned_all_equal,
    extract_series,
    join_nearest,
    join_nearest_presorted,
    load_reference_file,
    load_signal_captures,
)


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

    def test_presorted_matches_join_nearest(self):
        ref = [_tp(0.0, 10.0), _tp(1.0, 20.0)]
        cand = [_tp(1.2, 200.0), _tp(0.3, 100.0)]  # unsorted
        cand_sorted = sorted(cand, key=lambda tp: tp.dt)
        assert join_nearest_presorted(ref, cand_sorted, tol_s=1.0) == join_nearest(
            ref, cand, tol_s=1.0
        )


class TestAlignedAllEqual:
    def test_equal_series_returns_overlap(self):
        a = [_tp(i, i % 4) for i in range(20)]
        b = [_tp(i + 0.1, i % 4) for i in range(20)]  # same values, small skew
        assert aligned_all_equal(a, sorted(b, key=lambda tp: tp.dt), tol_s=0.5, min_n=10) == 20

    def test_first_mismatch_returns_zero(self):
        a = [_tp(i, i % 4) for i in range(20)]
        b = [_tp(i + 0.1, (i % 4) + 1) for i in range(20)]  # off by one everywhere
        assert aligned_all_equal(a, sorted(b, key=lambda tp: tp.dt), tol_s=0.5, min_n=10) == 0

    def test_below_min_n_returns_zero(self):
        a = [_tp(i, i % 4) for i in range(5)]
        b = [_tp(i + 0.1, i % 4) for i in range(5)]
        assert aligned_all_equal(a, sorted(b, key=lambda tp: tp.dt), tol_s=0.5, min_n=10) == 0


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
    (tmp_path / "2026-07-22.yaml").write_text(yaml.safe_dump(doc))
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
