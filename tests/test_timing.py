"""Tests for canlib.timing — per-(ECU, PID) round-trip timing instrumentation."""

from __future__ import annotations

import json

import canlib.timing as timing_mod
from canlib.timing import Stat, TimingRecorder, print_timings, render_timings


def test_stat_aggregation():
    st = Stat()
    for v in (0.1, 0.3, 0.2):
        st.add(v)
    assert st.n == 3
    assert st.max == 0.3
    assert st.last == 0.2
    assert abs(st.mean - 0.2) < 1e-9


def test_recorder_keys_and_snapshot_sorted_slowest_first():
    rec = TimingRecorder()
    rec.record("0x7E4", "2101", 0.05)
    rec.record("0x7E4", "2101", 0.15)  # same key -> aggregates
    rec.record("0x7E2", "2101", 0.40)  # slower ECU

    rows = rec.snapshot()
    assert [(r["ecu"], r["pid"]) for r in rows] == [("0x7E2", "2101"), ("0x7E4", "2101")]

    slow, fast = rows
    assert slow["max_ms"] == 400.0
    assert fast["n"] == 2
    assert fast["max_ms"] == 150.0
    assert fast["mean_ms"] == 100.0
    assert fast["last_ms"] == 150.0


def test_recorder_bool():
    rec = TimingRecorder()
    assert not rec
    rec.record("BMS", "2101", 0.01)
    assert rec


def test_resolve_ecu_hex_label(monkeypatch):
    # Hex request-id labels are resolved to names at render time.
    import canlib.ecus as ecus_mod

    monkeypatch.setattr(
        ecus_mod, "ecu_name", lambda tx, e=None: "BMS" if tx == 0x7E4 else f"0x{tx:03X}"
    )
    assert timing_mod._resolve_ecu("0x7E4") == "BMS"
    assert timing_mod._resolve_ecu("VCU") == "VCU"  # non-hex passes through


def test_render_timings_empty_is_none():
    assert render_timings(TimingRecorder()) is None


def test_render_timings_table(monkeypatch):
    import canlib.ecus as ecus_mod

    monkeypatch.setattr(
        ecus_mod, "ecu_name", lambda tx, e=None: "BMS" if tx == 0x7E4 else f"0x{tx:03X}"
    )
    rec = TimingRecorder()
    rec.record("0x7E4", "2101", 0.05)
    table = render_timings(rec)
    assert table is not None
    assert table.row_count == 1


def test_print_timings_json_to_stderr(capsys, monkeypatch):
    import canlib.ecus as ecus_mod

    monkeypatch.setattr(
        ecus_mod, "ecu_name", lambda tx, e=None: "VCU" if tx == 0x7E2 else f"0x{tx:03X}"
    )
    rec = TimingRecorder()
    rec.record("0x7E2", "2101", 0.4)
    print_timings(rec, as_json=True)
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout stays clean for real JSON results
    payload = json.loads(captured.err)
    assert payload["timings"][0]["ecu"] == "VCU"
    assert payload["timings"][0]["max_ms"] == 400.0


def test_print_timings_none_and_empty_noop(capsys):
    print_timings(None, as_json=False)
    print_timings(TimingRecorder(), as_json=False)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
