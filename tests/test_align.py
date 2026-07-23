"""Tests for the time-aligned cross-signal analysis primitives (canlib.align)."""

from datetime import datetime

import pytest
import yaml

from canlib.align import (
    SignalRef,
    TimePoint,
    align_many,
    extract_series,
    join_nearest,
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
        loaded = load_signal_captures(
            [("ESC", "22C101"), ("AAF", "2181")], captures_dir=tmp_path
        )
        esc = loaded[("ESC", "22C101")]
        aaf = loaded[("AAF", "2181")]
        assert len(esc.captures) == 2
        assert len(aaf.captures) == 2  # 2 timed; untimed excluded, scan ignored
        assert aaf.n_no_time == 1

    def test_scope_state_filter(self, tmp_path):
        _write_captures(tmp_path)
        loaded = load_signal_captures(
            [("ESC", "22C101")], state="charging", captures_dir=tmp_path
        )
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
        loaded = load_signal_captures(
            [("ESC", "22C101"), ("AAF", "2181")], captures_dir=tmp_path
        )
        esc = extract_series(loaded[("ESC", "22C101")], "B5")
        aaf = extract_series(loaded[("AAF", "2181")], "B4")
        xs, ys, n = join_nearest(esc, aaf, tol_s=1.0)
        assert n == 2
        assert xs == [10.0, 20.0]
        assert ys == [10.0, 20.0]
