"""The live monitor's slow-PID backoff (:mod:`canair.live.poll_backoff`)."""

from __future__ import annotations

import asyncio

from canair.live.poll_backoff import (
    ESCALATE_AFTER,
    MAX_SLOWDOWN,
    RECOVER_AFTER,
    PollBackoff,
    PollCost,
    SlowReason,
    describe_slowdown,
    slowdown_log_message,
    summarize_slowed,
)
from tests.support.fakes import FakeTerminal, nrc, ok

SILENT = PollCost(reads=4, seconds=4.0, answered=0)
SLUGGISH = PollCost(reads=2, seconds=1.6, answered=2)
HEALTHY = PollCost(reads=4, seconds=0.2, answered=4)


SKM = ("SKM", "22B002")


def _feed(b: PollBackoff, cost: PollCost, n: int, key=SKM) -> list[int | None]:
    return [b.observe(key, cost) for _ in range(n)]


class TestEscalation:
    def test_one_slow_poll_is_not_a_pattern(self):
        b = PollBackoff()
        assert b.observe(SKM, SILENT) is None
        assert b.slowdown(SKM) == 1

    def test_consecutive_silent_polls_double_the_slowdown(self):
        b = PollBackoff()
        changes = _feed(b, SILENT, ESCALATE_AFTER)
        assert changes[-1] == 2
        assert b.reason(SKM) == SlowReason("not answering", 1.0)

    def test_once_slowed_each_slow_poll_doubles_again(self):
        # A slowed ECU is polled rarely, so waiting for two slow polls per step would
        # take minutes to reach the ceiling.
        b = PollBackoff()
        _feed(b, SILENT, ESCALATE_AFTER)
        assert _feed(b, SILENT, 3) == [4, 8, 16]

    def test_slow_answers_count_too(self):
        b = PollBackoff()
        _feed(b, SLUGGISH, ESCALATE_AFTER)
        assert b.slowdown(SKM) == 2
        reason = b.reason(SKM)
        assert reason == SlowReason("slow", 0.8)
        assert reason.describe() == "0.8s per read"

    def test_capped(self):
        b = PollBackoff()
        _feed(b, SILENT, 100)
        assert b.slowdown(SKM) == MAX_SLOWDOWN

    def test_nrc_answers_are_healthy(self):
        # A prompt refusal is an answer, not a stall.
        b = PollBackoff()
        _feed(b, PollCost(reads=3, seconds=0.1, answered=3), 10)
        assert b.slowed() == {}

    def test_other_pids_are_unaffected(self):
        b = PollBackoff()
        _feed(b, SILENT, ESCALATE_AFTER)
        _feed(b, HEALTHY, ESCALATE_AFTER, key=("SKM", "22B003"))
        _feed(b, HEALTHY, ESCALATE_AFTER, key=("BMS", "2101"))
        assert b.slowed() == {SKM: 2}


class TestRecovery:
    def test_steps_back_after_healthy_polls(self):
        b = PollBackoff()
        _feed(b, SILENT, ESCALATE_AFTER + 1)
        assert b.slowdown(SKM) == 4
        assert _feed(b, HEALTHY, RECOVER_AFTER)[-1] == 2
        assert _feed(b, HEALTHY, 1) == [1]
        assert b.reason(SKM) is None

    def test_once_trusted_each_healthy_poll_halves(self):
        # At 32x a 5 s PID is read every 160 s: a streak per step took ~15 min back.
        b = PollBackoff()
        _feed(b, SILENT, 100)
        assert b.slowdown(SKM) == MAX_SLOWDOWN
        assert _feed(b, HEALTHY, RECOVER_AFTER + 4) == [None] * (RECOVER_AFTER - 1) + [
            16,
            8,
            4,
            2,
            1,
        ]

    def test_an_intermittent_ecu_keeps_climbing(self):
        # Fewer healthy polls in a row than RECOVER_AFTER never step it back.
        b = PollBackoff()
        _feed(b, SILENT, ESCALATE_AFTER)
        for _ in range(4):
            _feed(b, HEALTHY, RECOVER_AFTER - 1)
            b.observe(SKM, SILENT)
        assert b.slowdown(SKM) == MAX_SLOWDOWN

    def test_a_slow_poll_resets_the_recovery_streak(self):
        b = PollBackoff()
        _feed(b, SILENT, ESCALATE_AFTER)
        _feed(b, HEALTHY, RECOVER_AFTER - 1)
        b.observe(SKM, SILENT)  # slowed already, so this also doubles it
        _feed(b, HEALTHY, RECOVER_AFTER - 1)
        assert b.slowdown(SKM) == 4


class TestDisabled:
    def test_disabled_never_slows_down(self):
        b = PollBackoff(enabled=False)
        assert _feed(b, SILENT, 10) == [None] * 10
        assert b.slowed() == {}

    def test_a_poll_with_no_reads_is_ignored(self):
        b = PollBackoff()
        _feed(b, PollCost(), 10)
        assert b.slowed() == {}


class TestWording:
    def test_describe_slowdown(self):
        reason = SlowReason("not answering", 1.2)
        assert describe_slowdown(4, reason) == "slow (not answering) · polled 4× less often"
        assert describe_slowdown(2, SlowReason("slow", 0.84)) == (
            "slow (0.8s per read) · polled 2× less often"
        )

    def test_log_message(self):
        reason = SlowReason("slow", 0.8)
        assert slowdown_log_message("SKM 22B002", 4, reason) == (
            "SKM 22B002 is slow (0.8s per read): polled 4x less often"
        )
        assert "back to its normal poll rate" in slowdown_log_message("SKM 22B002", 1, None)

    def test_summarize_groups_an_ecus_pids_by_factor(self):
        slowed = {("SKM", "22B003"): 4, ("SKM", "22B002"): 4, ("BMS", "2101"): 2}
        assert summarize_slowed(slowed) == "BMS:2101÷2 SKM:22B002,22B003÷4"


class TestElmPollCost:
    """Each PID is charged its own request's time, as the executor stamps it."""

    def test_each_pid_gets_its_own_cost(self, monkeypatch):
        from canair.live.monitor import MonitorController

        async def fake_exec_query(sm, ecu, pids, index, pids_data, verbose, **kw):
            plan = [(p, None, False) for p in pids]
            due = kw["plan_filter"](ecu.upper(), plan)
            if ecu == "SKM":
                return "SKM (0x7A5)", [
                    {"pid": p, "error": "NO DATA", "request_s": 1.2} for p, _i, _u in due
                ]
            return "BMS (0x7E4)", [
                {"pid": "2101", "raw_hex": "6101AA", "elapsed_ms": 20, "request_s": 0.02},
                {"pid": "2102", "error": "NRC 0x31 (ROOR)", "nrc": 0x31, "request_s": 0.03},
            ]

        monkeypatch.setattr("canair.live.multi._exec_query", fake_exec_query)

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
        assert c.poll_costs == {
            ("SKM", "22B002"): PollCost(reads=1, seconds=1.2, answered=0),
            ("SKM", "22B003"): PollCost(reads=1, seconds=1.2, answered=0),
            ("BMS", "2101"): PollCost(reads=1, seconds=0.02, answered=1),
            ("BMS", "2102"): PollCost(reads=1, seconds=0.03, answered=1),  # an NRC answers
        }


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
    from canair.live.monitor import MonitorController
    from canair.live.multi_batch import BatchState

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
    def test_a_batch_is_one_request_for_each_member(self):
        # Dividing one slow request among its three DIDs hid it from SLOW_READ_S; each
        # member was held up for the whole of it.
        multi = "62BC03FDEE3C730A000000BC04B53FF4EA01000042BC06B480000000000000AAAA"
        c = _igpm_controller(_ElmTerminal({"22BC03BC04BC06": ok(multi)}), multi_did=True)
        assert c._batch_state is not None
        for d in ("BC03", "BC04", "BC06"):
            c._batch_state.lengths[(0x770, d)] = 8
        _poll(c)
        assert c.terminal.sent == ["22BC03BC04BC06"]
        costs = [c.poll_costs[("IGPM", code)] for code in ("22BC03", "22BC04", "22BC06")]
        assert {(cost.reads, cost.answered) for cost in costs} == {(1, 1)}
        assert len({cost.seconds for cost in costs}) == 1

    def test_single_reads_count_one_each(self):
        responses = {
            "22BC03": ok("62BC03FDEE3C730A000000AAAA"),
            "22BC06": nrc(0x31, "requestOutOfRange"),
        }  # 22BC04 times out (the fake's NO DATA default)
        c = _igpm_controller(_ElmTerminal(responses))
        _poll(c)
        answered = {pid: cost.answered for (_ecu, pid), cost in c.poll_costs.items()}
        assert answered == {"22BC03": 1, "22BC04": 0, "22BC06": 1}

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
        assert all(cost.seconds < 0.1 for cost in c.poll_costs.values())

    def test_a_background_keepalive_queued_ahead_is_not_its_cost(self):
        # The monitor's background keepalive takes the same transaction, so a read can
        # wait behind it on the serial pipe; that wait is session upkeep, not the read.
        from contextlib import asynccontextmanager

        from canair.live.multi_exec import _read_single
        from canair.transport.session_manager import SessionManager

        class SerialTerminal(_ElmTerminal):
            """Serialises transactions like the real ELM327 engine."""

            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.lock = asyncio.Lock()

            @asynccontextmanager
            async def transaction(self):
                async with self.lock:
                    yield

        term = SerialTerminal({"22BC03": ok("62BC03FDEE3C730A000000AAAA")})
        term.keepalive_delay = 0.2
        sm = SessionManager(term)

        async def run():
            keepalive = asyncio.create_task(sm.send_keepalive(0x7A5))
            await asyncio.sleep(0)  # the keepalive now holds the transaction
            entry = await _read_single(sm, 0x770, "22BC03", {"parameters": {}}, False, None)
            await keepalive
            return entry

        entry = asyncio.run(run())
        assert entry["elapsed_ms"] >= 200  # the persisted round trip is unchanged
        assert entry["request_s"] < 0.1


class TestOnlyTheDeadPidIsSlowed:
    """One always-timing-out PID next to two healthy ones on the same ECU."""

    def test_elm327(self):
        responses = {
            "22BC03": ok("62BC03FDEE3C730A000000AAAA"),
            "22BC06": ok("62BC06B480000000000000AAAA"),
        }  # 22BC04 never answers
        c = _igpm_controller(_ElmTerminal(responses))
        for _ in range(ESCALATE_AFTER):
            asyncio.run(c.poll_once())
        assert c.schedule.slowed() == {("IGPM", "22BC04"): 2}
        assert c.backoff.slowed() == {("IGPM", "22BC04"): 2}
        # Its neighbours are read on the next tick; it sits that one out.
        asyncio.run(c.poll_once())
        assert set(c.poll_costs) == {("IGPM", "22BC03"), ("IGPM", "22BC06")}

    def test_raw(self):
        from canair.live.monitor import MonitorController
        from tests.support.raw_monitor import TimedRawClient as _TimedRawClient

        table = {
            ("IGPM", bytes.fromhex("22BC03")): bytes.fromhex("62BC03FDEE3C730A000000"),
            ("IGPM", bytes.fromhex("22BC06")): bytes.fromhex("62BC06B480000000000000"),
        }  # 22BC04 times out, costing a second each time
        steps = [{"ecu": "IGPM", "pids": []}]
        c = MonitorController(
            None, steps, {}, verbose=False, raw_client=_TimedRawClient(table), schedule="fixed"
        )
        c._ecu_index = {
            "IGPM": {
                "tx_id": 0x770,
                "pids": {code: {"parameters": {}} for code in ("22BC03", "22BC04", "22BC06")},
            }
        }
        for _ in range(ESCALATE_AFTER):
            asyncio.run(c.poll_once())
        assert c.schedule.slowed() == {("IGPM", "22BC04"): 2}
        assert c.poll_costs[("IGPM", "22BC03")].seconds == 0.02
        asyncio.run(c.poll_once())
        assert set(c.poll_costs) == {("IGPM", "22BC03"), ("IGPM", "22BC06")}
        rows = c.render().plain.splitlines()
        dead = next(line for line in rows if "22BC04" in line)
        assert "slow (not answering) · polled 2× less often" in dead
        assert not any("polled" in line for line in rows if "22BC03" in line)
