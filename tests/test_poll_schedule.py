"""The live monitor's per-PID poll scheduler (:mod:`canlib.poll_schedule`)."""

from __future__ import annotations

from canlib.poll_schedule import (
    DEFAULT_SCHEDULE,
    MAX_RATE_STEPS,
    PollScheduler,
    declared_period_ms,
)


def _plan(*pids):
    """A build_query_plan-shaped plan: (code, pid_info, unmapped) per PID.

    ``pids`` are ``(code, period_ms_or_None)`` pairs.
    """
    return [(code, ({"period": ms} if ms is not None else {}), False) for code, ms in pids]


class TestDeclaredPeriod:
    def test_reads_a_positive_int(self):
        assert declared_period_ms({"period": 10000}) == 10000

    def test_missing_is_none(self):
        assert declared_period_ms({}) is None
        assert declared_period_ms(None) is None

    def test_zero_and_negative_are_none(self):
        # period 0 is "unset" to the firmware, not "never poll".
        assert declared_period_ms({"period": 0}) is None
        assert declared_period_ms({"period": -5}) is None

    def test_bool_is_not_a_period(self):
        # A stray `period: true` is an int subclass; it must not read as 1 ms.
        assert declared_period_ms({"period": True}) is None


class TestFixedSchedule:
    def test_every_pid_is_due_every_tick(self):
        s = PollScheduler("fixed", interval=2.0)
        plan = _plan(("22BC03", 10000), ("2101", None))
        for t in (0.0, 2.0, 4.0):
            s.begin_tick(t)
            assert s.take_due("IGPM", plan) == plan
            s.end_tick()

    def test_next_tick_is_one_interval_away(self):
        s = PollScheduler("fixed", interval=2.0)
        s.begin_tick(0.0)
        s.take_due("IGPM", _plan(("2101", None)))
        s.end_tick()
        assert s.seconds_until_due(0.0) == 2.0
        assert s.seconds_until_due(1.5) == 0.5
        assert s.seconds_until_due(2.0) == 0.0

    def test_period_ignores_the_profile(self):
        s = PollScheduler("fixed", interval=3.0)
        s.begin_tick(0.0)
        s.take_due("IGPM", _plan(("22BC03", 10000)))
        assert s.period(("IGPM", "22BC03")) == 3.0


class TestDynamicSchedule:
    def test_default_is_dynamic(self):
        assert PollScheduler().schedule == DEFAULT_SCHEDULE == "dynamic"

    def test_first_tick_polls_everything(self):
        s = PollScheduler("dynamic", interval=5.0)
        plan = _plan(("22BC03", 2000), ("2101", None))
        s.begin_tick(0.0)
        assert s.take_due("IGPM", plan) == plan
        s.end_tick()

    def test_a_fast_pid_refreshes_while_a_slow_one_waits(self):
        s = PollScheduler("dynamic", interval=5.0)
        plan = _plan(("FAST", 1000), ("SLOW", 10000))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        # 1.0 s later: only the 1 s PID is due.
        s.begin_tick(1.0)
        due = s.take_due("E", plan)
        s.end_tick()
        assert [e[0] for e in due] == ["FAST"]
        # 10 s later: both.
        s.begin_tick(10.0)
        due = s.take_due("E", plan)
        s.end_tick()
        assert {e[0] for e in due} == {"FAST", "SLOW"}

    def test_a_pid_with_no_period_falls_back_to_the_interval(self):
        s = PollScheduler("dynamic", interval=4.0)
        plan = _plan(("2101", None))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        assert s.period(("E", "2101")) == 4.0
        assert s.seconds_until_due(0.0) == 4.0

    def test_periods_that_are_multiples_coincide(self):
        # A 2 s and a 10 s PID first polled together must fall due together again
        # at t=10 — that is what lets a multi_did ECU keep batching them.
        s = PollScheduler("dynamic", interval=5.0)
        plan = _plan(("A", 2000), ("B", 10000))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        s.begin_tick(10.0)
        due = s.take_due("E", plan)
        s.end_tick()
        assert {e[0] for e in due} == {"A", "B"}

    def test_slower_stretches_every_period(self):
        s = PollScheduler("dynamic", interval=5.0)
        s.slower()  # rate / 1.5
        plan = _plan(("A", 2000))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        assert s.period(("E", "A")) == 3.0  # 2 s x 1.5
        assert s.seconds_until_due(0.0) == 3.0

    def test_faster_shortens_every_period(self):
        # Regression: = must poll faster. The label used to show the period
        # multiplier (x0.67 after "faster"), which read as the opposite.
        s = PollScheduler("dynamic", interval=6.0)
        s.faster()
        plan = _plan(("A", 3000), ("B", None))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        assert s.period(("E", "A")) == 2.0
        assert s.period(("E", "B")) == 4.0
        assert s.describe() == "per-PID 1.5× faster"

    def test_faster_then_slower_returns_exactly_to_start(self):
        s = PollScheduler("dynamic")
        for _ in range(3):
            s.slower()
        for _ in range(3):
            s.faster()
        assert s.rate == 1.0
        assert s.describe() == "per-PID"

    def test_rate_is_clamped(self):
        s = PollScheduler("dynamic")
        for _ in range(100):
            s.slower()
        assert s.rate_steps == -MAX_RATE_STEPS
        for _ in range(200):
            s.faster()
        assert s.rate_steps == MAX_RATE_STEPS

    def test_period_speedup_applies_to_declared_periods_only(self):
        s = PollScheduler("dynamic", interval=5.0, period_speedup=2.0)
        plan = _plan(("A", 10000), ("B", None))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        assert s.period(("E", "A")) == 5.0
        assert s.period(("E", "B")) == 5.0  # --interval is already the caller's seconds

    def test_wake_makes_the_next_tick_due_now(self):
        s = PollScheduler("dynamic", interval=5.0)
        plan = _plan(("A", 10000))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        assert s.seconds_until_due(1.0) > 0  # not due for a while
        s.wake()
        assert s.seconds_until_due(1.0) == 0.0

    def test_a_late_tick_does_not_burst(self):
        # A tick that runs long skips the grid points it missed rather than
        # replaying them: it stays due, but only advances one period past now.
        s = PollScheduler("dynamic", interval=5.0)
        plan = _plan(("A", 1000))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        # Nothing polled for 10 s (10 missed grid points); it is due once...
        s.begin_tick(10.0)
        assert [e[0] for e in s.take_due("E", plan)] == ["A"]
        s.end_tick()
        # ...and the next due time is 11 s, not a backlog at 1,2,3…
        assert s.seconds_until_due(10.0) == 1.0


class TestFixedRateControl:
    def test_faster_shortens_the_interval(self):
        s = PollScheduler("fixed", interval=3.0)
        assert s.faster() == "2.0s"
        assert s.slower() == "3.0s"


class TestSlowdown:
    def test_dynamic_slowdown_stretches_only_that_ecu(self):
        s = PollScheduler("dynamic", interval=5.0)
        plan = _plan(("A", 1000))
        s.begin_tick(0.0)
        s.take_due("SLOW", plan)
        s.take_due("FAST", plan)
        s.end_tick()
        s.set_slowdown("SLOW", 4)
        due_slow = []
        due_fast = []
        for t in (1.0, 2.0, 3.0, 4.0):
            s.begin_tick(t)
            due_slow += s.take_due("SLOW", plan)
            due_fast += s.take_due("FAST", plan)
            s.end_tick()
        assert len(due_fast) == 4
        assert len(due_slow) == 1  # only at t=4

    def test_fixed_slowdown_sits_out_rounds(self):
        s = PollScheduler("fixed", interval=1.0)
        plan = _plan(("A", None))
        polled = []
        for t in range(7):
            s.begin_tick(float(t))
            polled.append(bool(s.take_due("SLOW", plan)))
            assert s.take_due("FAST", plan) == plan
            s.end_tick()
            if t == 0:
                # Applied straight after a poll, as the monitor's backoff does.
                s.set_slowdown("SLOW", 3)
        assert polled == [True, False, False, True, False, False, True]

    def test_clearing_the_slowdown_restores_the_rate(self):
        s = PollScheduler("fixed", interval=1.0)
        plan = _plan(("A", None))
        s.set_slowdown("E", 4)
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        s.set_slowdown("E", 1)
        s.begin_tick(1.0)
        assert s.take_due("E", plan) == plan
        assert s.slowdown("E") == 1


class TestToggle:
    def test_toggle_flips_and_re_wakes(self):
        s = PollScheduler("dynamic", interval=2.0)
        plan = _plan(("A", 10000))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        assert s.toggle() == "2.0s"  # now fixed
        assert s.schedule == "fixed"
        assert s.seconds_until_due(1.0) == 0.0  # woken by the toggle
        assert s.toggle() == "per-PID"


class TestClocks:
    def test_due_count_tracks_each_pid(self):
        s = PollScheduler("dynamic", interval=5.0)
        plan = _plan(("FAST", 1000), ("SLOW", 10000))
        for t in (0.0, 1.0, 2.0, 3.0):
            s.begin_tick(t)
            s.take_due("E", plan)
            s.end_tick()
        assert s.due_count("E", "FAST") == 4  # due every second
        assert s.due_count("E", "SLOW") == 1  # only the first tick
        assert s.due_count("E", "MISSING") is None

    def test_ecu_ticks_counts_ticks_that_touched_the_ecu(self):
        s = PollScheduler("dynamic", interval=5.0)
        plan = _plan(("A", 1000))
        for t in (0.0, 1.0):
            s.begin_tick(t)
            s.take_due("E", plan)
            s.end_tick()
        assert s.ecu_ticks("E") == 2
        assert s.ecu_ticks("OTHER") is None


class TestDescribe:
    def test_fixed_shows_seconds(self):
        assert PollScheduler("fixed", 5.0).describe() == "5.0s"

    def test_dynamic_shows_per_pid(self):
        s = PollScheduler("dynamic")
        assert s.describe() == "per-PID"
        s.slower()
        assert s.describe() == "per-PID 1.5× slower"
