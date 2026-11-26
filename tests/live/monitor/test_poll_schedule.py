"""The live monitor's per-PID poll scheduler (:mod:`canair.live.monitor.schedule`)."""

from __future__ import annotations

import pytest

from canair.live.monitor.schedule import (
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


class TestMonitorOnce:
    @pytest.mark.parametrize("mode", ["dynamic", "fixed"])
    def test_retries_until_success_then_stops_even_after_wake_and_toggle(self, mode):
        scheduler = PollScheduler(mode, interval=1.0)
        plan = [("21F2", {"monitor_once": True, "period": 1000}, False)]
        for now in (0.0, 1.0):
            scheduler.begin_tick(now)
            assert scheduler.take_due("BMS", plan) == plan
            scheduler.end_tick()
        assert scheduler.note_success("BMS", "21F2")
        assert not scheduler.note_success("BMS", "21F2")
        scheduler.wake()
        scheduler.toggle()
        scheduler.begin_tick(1000.0)
        assert scheduler.take_due("BMS", plan) == []
        scheduler.end_tick()
        assert scheduler.seconds_until_due(1000.0) > 0

    def test_a_pending_one_shot_retries_at_the_interval_not_its_period(self):
        scheduler = PollScheduler("dynamic", interval=2.0, period_speedup=2.0)
        plan = [("21F2", {"monitor_once": True, "period": 300000}, False)]
        scheduler.begin_tick(0.0)
        assert scheduler.take_due("BMS", plan) == plan
        scheduler.end_tick()
        assert scheduler.seconds_until_due(0.0) == pytest.approx(2.0)

    def test_forget_and_rearm_read_a_retired_pid_again(self):
        scheduler = PollScheduler("fixed", interval=1.0)
        plan = [("21F2", {"monitor_once": True}, False)]
        for forget in (True, False):
            scheduler.begin_tick(0.0)
            scheduler.take_due("BMS", plan)
            scheduler.note_success("BMS", "21F2")
            scheduler.end_tick()
            assert scheduler.is_retired("BMS", "21F2")
            if forget:
                scheduler.forget("BMS", "21F2")
            else:
                scheduler.rearm()
            assert not scheduler.is_retired("BMS", "21F2")
            scheduler.begin_tick(1.0)
            assert scheduler.take_due("BMS", plan) == plan
            scheduler.end_tick()

    def test_rearm_with_keys_only_touches_those(self):
        scheduler = PollScheduler("fixed", interval=1.0)
        plan = [("21F2", {"monitor_once": True}, False)]
        scheduler.begin_tick(0.0)
        for ecu in ("BMS", "VCU"):
            scheduler.take_due(ecu, plan)
            scheduler.note_success(ecu, "21F2")
        scheduler.end_tick()
        scheduler.rearm([("VCU", "21F2")])
        assert scheduler.is_retired("BMS", "21F2")
        assert not scheduler.is_retired("VCU", "21F2")

    def test_polling_ecus_leaves_out_ecus_with_only_retired_pids(self):
        scheduler = PollScheduler("fixed", interval=1.0)
        scheduler.begin_tick(0.0)
        scheduler.take_due("BMS", [("21F2", {"monitor_once": True}, False)])
        scheduler.take_due("VCU", [("2101", {}, False)])
        scheduler.note_success("BMS", "21F2")
        scheduler.end_tick()
        assert scheduler.polling_ecus() == {"VCU"}

    def test_retirement_is_per_ecu_and_a_new_run_starts_fresh(self):
        scheduler = PollScheduler("fixed", interval=1.0)
        plan = [("21F2", {"monitor_once": True}, False)]
        scheduler.begin_tick(0.0)
        assert scheduler.take_due("BMS", plan) == plan
        assert scheduler.take_due("VCU", plan) == plan
        scheduler.note_success("BMS", "21F2")
        scheduler.end_tick()
        scheduler.begin_tick(1.0)
        assert not scheduler.take_due("BMS", plan)
        assert scheduler.take_due("VCU", plan) == plan
        fresh = PollScheduler("fixed")
        fresh.begin_tick(0.0)
        assert fresh.take_due("BMS", plan) == plan

    def test_a_cleared_policy_restores_polling(self):
        scheduler = PollScheduler("dynamic", interval=1.0)
        plan = [("21F2", {"monitor_once": True}, False)]
        scheduler.begin_tick(0.0)
        scheduler.take_due("BMS", plan)
        scheduler.note_success("BMS", "21F2")
        scheduler.end_tick()
        scheduler.begin_tick(1.0)
        assert scheduler.take_due("BMS", [("21F2", {}, False)])


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

    @pytest.mark.parametrize("interval", [0.0, -1.0, float("nan")])
    def test_a_non_positive_interval_is_refused(self, interval):
        # Every PID without a period would be due on every tick: a busy poll loop.
        with pytest.raises(ValueError, match="interval"):
            PollScheduler("dynamic", interval=interval)

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
    def test_dynamic_slowdown_stretches_only_that_pid(self):
        s = PollScheduler("dynamic", interval=5.0)
        plan = _plan(("A", 1000), ("B", 1000))
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        s.set_slowdown(("E", "A"), 4)
        due = []
        for t in (1.0, 2.0, 3.0, 4.0):
            s.begin_tick(t)
            due += [code for code, _i, _u in s.take_due("E", plan)]
            s.end_tick()
        assert due.count("B") == 4
        assert due.count("A") == 1  # only at t=4

    def test_fixed_slowdown_sits_out_rounds(self):
        s = PollScheduler("fixed", interval=1.0)
        plan = _plan(("A", None), ("B", None))
        polled = []
        for t in range(7):
            s.begin_tick(float(t))
            due = [code for code, _i, _u in s.take_due("E", plan)]
            polled.append("A" in due)
            assert "B" in due
            s.end_tick()
            if t == 0:
                # Applied straight after a poll, as the monitor's backoff does.
                s.set_slowdown(("E", "A"), 3)
        assert polled == [True, False, False, True, False, False, True]

    def test_a_sitting_out_pid_still_counts_its_ecu_as_polled(self):
        # The per-ECU clock ticks on any of its PIDs being due, not all of them.
        s = PollScheduler("fixed", interval=1.0)
        plan = _plan(("A", None), ("B", None))
        s.set_slowdown(("E", "A"), 4)
        for t in range(3):
            s.begin_tick(float(t))
            s.take_due("E", plan)
            s.end_tick()
        assert s.ecu_ticks("E") == 3
        assert s.due_count("E", "B") == 3

    def test_clearing_the_slowdown_restores_the_rate(self):
        s = PollScheduler("fixed", interval=1.0)
        plan = _plan(("A", None))
        s.set_slowdown(("E", "A"), 4)
        s.begin_tick(0.0)
        s.take_due("E", plan)
        s.end_tick()
        s.set_slowdown(("E", "A"), 1)
        s.begin_tick(1.0)
        assert s.take_due("E", plan) == plan
        assert s.slowdown(("E", "A")) == 1

    def test_slowed_lists_only_slowed_pids(self):
        s = PollScheduler("dynamic", interval=1.0)
        s.set_slowdown(("E", "A"), 4)
        s.set_slowdown(("E", "B"), 2)
        s.set_slowdown(("E", "B"), 1)
        assert s.slowed() == {("E", "A"): 4}


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
