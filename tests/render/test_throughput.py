"""Tests for canair.render.progress — the speed over the last minute and the time left (pure)."""

from __future__ import annotations

from canair.render.progress import MIN_SPAN_S, WINDOW_S, Throughput


def test_no_rate_before_there_is_enough_to_say():
    t = Throughput(0.0)
    assert t.rate() is None and t.eta(1000) is None
    t.observe(MIN_SPAN_S - 0.1, 500)
    assert t.rate() is None


def test_the_rate_is_bytes_over_time():
    t = Throughput(10.0)
    t.observe(20.0, 2000)
    assert t.rate() == 200.0
    assert t.eta(4000) == 20.0


def test_a_stalled_read_has_no_eta():
    t = Throughput(0.0)
    t.observe(30.0, 0)
    assert t.rate() == 0.0 and t.eta(100) is None


def test_the_rate_follows_the_last_window_not_the_whole_run():
    t = Throughput(0.0)
    for second in range(1, 301):  # 300 s at 100 B/s, then 300 s at 500 B/s
        t.observe(float(second), second * 100)
    done = 300 * 100
    for second in range(301, 601):
        done += 500
        t.observe(float(second), done)
    assert t.rate() == 500.0  # the whole-run average would be 300 B/s


def test_the_window_stays_bounded_over_a_long_run():
    t = Throughput(0.0)
    for second in range(1, 5000):
        t.observe(float(second), second)
    assert len(t._samples) <= WINDOW_S + 2


def test_nothing_left_is_zero_seconds():
    t = Throughput(0.0)
    t.observe(10.0, 1000)
    assert t.eta(0) == 0.0
