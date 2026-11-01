"""The live monitor's slow-ECU backoff (:mod:`canlib.poll_backoff`)."""

from __future__ import annotations

from canlib.poll_backoff import (
    ESCALATE_AFTER,
    MAX_SLOWDOWN,
    RECOVER_AFTER,
    EcuBackoff,
    PollCost,
    describe_slowdown,
)

SILENT = PollCost(reads=4, seconds=4.0, answered=0)
SLUGGISH = PollCost(reads=2, seconds=1.6, answered=2)
HEALTHY = PollCost(reads=4, seconds=0.2, answered=4)


def _feed(b: EcuBackoff, cost: PollCost, n: int, ecu: str = "SKM") -> list[int | None]:
    return [b.observe(ecu, cost) for _ in range(n)]


class TestEscalation:
    def test_one_slow_poll_is_not_a_pattern(self):
        b = EcuBackoff()
        assert b.observe("SKM", SILENT) is None
        assert b.slowdown("SKM") == 1

    def test_consecutive_silent_polls_double_the_slowdown(self):
        b = EcuBackoff()
        changes = _feed(b, SILENT, ESCALATE_AFTER)
        assert changes[-1] == 2
        assert b.reason("SKM") == "not answering"

    def test_once_slowed_each_slow_poll_doubles_again(self):
        # A slowed ECU is polled rarely, so waiting for two slow polls per step would
        # take minutes to reach the ceiling.
        b = EcuBackoff()
        _feed(b, SILENT, ESCALATE_AFTER)
        assert _feed(b, SILENT, 3) == [4, 8, 16]

    def test_slow_answers_count_too(self):
        b = EcuBackoff()
        _feed(b, SLUGGISH, ESCALATE_AFTER)
        assert b.slowdown("SKM") == 2
        assert b.reason("SKM") == "0.8s per read"

    def test_capped(self):
        b = EcuBackoff()
        _feed(b, SILENT, 100)
        assert b.slowdown("SKM") == MAX_SLOWDOWN

    def test_nrc_answers_are_healthy(self):
        # A prompt refusal is an answer, not a stall.
        b = EcuBackoff()
        _feed(b, PollCost(reads=3, seconds=0.1, answered=3), 10)
        assert b.slowed() == {}

    def test_other_ecus_are_unaffected(self):
        b = EcuBackoff()
        _feed(b, SILENT, ESCALATE_AFTER, ecu="SKM")
        _feed(b, HEALTHY, ESCALATE_AFTER, ecu="BMS")
        assert b.slowed() == {"SKM": 2}


class TestRecovery:
    def test_steps_back_after_healthy_polls(self):
        b = EcuBackoff()
        _feed(b, SILENT, ESCALATE_AFTER + 1)
        assert b.slowdown("SKM") == 4
        assert _feed(b, HEALTHY, RECOVER_AFTER)[-1] == 2
        assert _feed(b, HEALTHY, RECOVER_AFTER)[-1] == 1
        assert b.reason("SKM") == ""

    def test_a_slow_poll_resets_the_recovery_streak(self):
        b = EcuBackoff()
        _feed(b, SILENT, ESCALATE_AFTER)
        _feed(b, HEALTHY, RECOVER_AFTER - 1)
        b.observe("SKM", SILENT)  # slowed already, so this also doubles it
        _feed(b, HEALTHY, RECOVER_AFTER - 1)
        assert b.slowdown("SKM") == 4


class TestDisabled:
    def test_disabled_never_slows_down(self):
        b = EcuBackoff(enabled=False)
        assert _feed(b, SILENT, 10) == [None] * 10
        assert b.slowed() == {}

    def test_a_poll_with_no_reads_is_ignored(self):
        b = EcuBackoff()
        _feed(b, PollCost(), 10)
        assert b.slowed() == {}


def test_describe_slowdown():
    assert describe_slowdown(4, "not answering") == "slow (not answering) · polled 4× less often"


class TestElmPollCost:
    """The ELM327 pipe is serial: an ECU's step time is what it cost everyone else."""

    def test_step_cost_is_recorded_per_ecu(self, monkeypatch):
        import asyncio
        import time

        from canlib.modes.monitor import MonitorController

        async def fake_exec_query(sm, ecu, pids, index, pids_data, verbose, **kw):
            plan = [(p, None, False) for p in pids]
            due = kw["plan_filter"](ecu.upper(), plan)
            if ecu == "SKM":
                time.sleep(0.01)
                return "SKM (0x7A5)", [{"pid": p, "error": "NO DATA"} for p, _i, _u in due]
            return "BMS (0x7E4)", [
                {"pid": "2101", "raw_hex": "6101AA"},
                {"pid": "2102", "error": "NRC 0x31 (ROOR)"},
            ]

        monkeypatch.setattr("canlib.modes.multi._exec_query", fake_exec_query)

        class FakeTerminal:
            cmd_count = 0
            cmd_time = 0.0

        steps = [
            {"ecu": "SKM", "pids": ["22B002", "22B003"]},
            {"ecu": "BMS", "pids": ["2101", "2102"]},
        ]
        c = MonitorController(FakeTerminal(), steps, {}, verbose=False, schedule="fixed")
        c._ecu_index = {}
        c.schedule.begin_tick(0.0)
        asyncio.run(c._poll_elm())
        skm, bms = c.ecu_costs["SKM"], c.ecu_costs["BMS"]
        assert (skm.reads, skm.answered) == (2, 0) and skm.seconds >= 0.01
        assert (bms.reads, bms.answered) == (2, 2)  # an NRC is an answer
