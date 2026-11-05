"""The decoded range of a signal over a PID's saved captures."""

from __future__ import annotations

from canair.analysis import signal_range


def _captures(monkeypatch, payloads):
    monkeypatch.setattr(
        signal_range, "load_pid_captures", lambda ecu, pid: [{"payload": p} for p in payloads]
    )


def test_no_captures(monkeypatch):
    _captures(monkeypatch, [])
    found = signal_range.capture_range("BMS", "2101", "X", {"expression": "B4"})
    assert found == signal_range.CaptureRange(0, [], [], None)


def test_values_across_captures(monkeypatch):
    _captures(monkeypatch, ["6101" + "0A", "6101" + "14"])
    found = signal_range.capture_range("BMS", "2101", "X", {"expression": "B3"})
    assert found.captures == 2
    assert found.values == [10.0, 20.0] and found.error is None
