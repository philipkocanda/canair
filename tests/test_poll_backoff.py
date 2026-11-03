"""The live monitor's slow-ECU backoff (:mod:`canlib.poll_backoff`)."""

from __future__ import annotations

import asyncio

from canlib.poll_backoff import (
    ESCALATE_AFTER,
    MAX_SLOWDOWN,
    RECOVER_AFTER,
    EcuBackoff,
    PollCost,
    describe_slowdown,
)
from tests._fakes import FakeTerminal, nrc, ok

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
        assert _feed(b, HEALTHY, 1) == [1]
        assert b.reason("SKM") == ""

    def test_once_trusted_each_healthy_poll_halves(self):
        # At 32x a 5 s PID is read every 160 s: a streak per step took ~15 min back.
        b = EcuBackoff()
        _feed(b, SILENT, 100)
        assert b.slowdown("SKM") == MAX_SLOWDOWN
        assert _feed(b, HEALTHY, RECOVER_AFTER + 4) == [None] * (RECOVER_AFTER - 1) + [
            16,
            8,
            4,
            2,
            1,
        ]

    def test_an_intermittent_ecu_keeps_climbing(self):
        # Fewer healthy polls in a row than RECOVER_AFTER never step it back.
        b = EcuBackoff()
        _feed(b, SILENT, ESCALATE_AFTER)
        for _ in range(4):
            _feed(b, HEALTHY, RECOVER_AFTER - 1)
            b.observe("SKM", SILENT)
        assert b.slowdown("SKM") == MAX_SLOWDOWN

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
                {"pid": "2101", "raw_hex": "6101AA", "elapsed_ms": 20},
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


class _ElmTerminal(FakeTerminal):
    """A FakeTerminal with the ELM327 counters the monitor reads, and a slow 3E00."""

    cmd_count = 0
    cmd_time = 0.0
    keepalive_delay = 0.0

    async def send_uds(self, req: str, *args, **kwargs):
        if req == "3E00":
            await asyncio.sleep(self.keepalive_delay)
            return ok("7E00")
        return await super().send_uds(req, *args, **kwargs)


def _igpm_controller(terminal: _ElmTerminal, **index_extra):
    """A real controller polling IGPM's three DIDs through the real executor."""
    from canlib.modes.monitor import MonitorController
    from canlib.modes.multi_batch import BatchState

    steps = [{"type": "query", "ecu": "IGPM", "pids": []}]
    c = MonitorController(terminal, steps, {}, verbose=False, schedule="fixed")
    c._ecu_index = {
        "IGPM": {
            "tx_id": 0x770,
            "pids": {code: {"parameters": {}} for code in ("22BC03", "22BC04", "22BC06")},
            **index_extra,
        }
    }
    c._batch_state = BatchState()
    return c


def _poll(c) -> None:
    c.schedule.begin_tick(0.0)
    asyncio.run(c._poll_elm())


class TestElmCostAttribution:
    def test_a_batch_counts_as_one_request(self):
        # Counting its three DIDs as three reads divided one slow request by three, so
        # it rarely crossed SLOW_READ_S.
        multi = "62BC03FDEE3C730A000000BC04B53FF4EA01000042BC06B480000000000000AAAA"
        c = _igpm_controller(_ElmTerminal({"22BC03BC04BC06": ok(multi)}), multi_did=True)
        assert c._batch_state is not None
        for d in ("BC03", "BC04", "BC06"):
            c._batch_state.lengths[(0x770, d)] = 8
        _poll(c)
        assert c.terminal.sent == ["22BC03BC04BC06"]
        cost = c.ecu_costs["IGPM"]
        assert (cost.reads, cost.answered) == (1, 1)

    def test_single_reads_count_one_each(self):
        responses = {
            "22BC03": ok("62BC03FDEE3C730A000000AAAA"),
            "22BC06": nrc(0x31, "requestOutOfRange"),
        }  # 22BC04 times out (the fake's NO DATA default)
        c = _igpm_controller(_ElmTerminal(responses))
        _poll(c)
        cost = c.ecu_costs["IGPM"]
        assert (cost.reads, cost.answered) == (3, 2)

    def test_keepalives_for_other_ecus_are_not_its_cost(self):
        # The executor refreshes every stale session before each read; on the serial
        # pipe that is time spent for other ECUs, not time IGPM held everyone up.
        import time

        term = _ElmTerminal({"22BC03": ok("62BC03FDEE3C730A000000AAAA")})
        term.keepalive_delay = 0.2
        c = _igpm_controller(term)
        assert c.sm is not None
        c.sm._sessions[0x7A5] = time.monotonic() - 10.0  # SKM's session is stale
        _poll(c)
        assert "3E00" not in term.sent  # answered by the subclass, never recorded
        assert c.sm.keepalive_seconds >= 0.2
        assert c.ecu_costs["IGPM"].seconds < 0.1
