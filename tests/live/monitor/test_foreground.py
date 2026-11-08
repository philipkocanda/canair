"""The monitor's foreground lane: user jobs in order, one at a time, beside the poll."""

from __future__ import annotations

import asyncio

import pytest

from canair.live.monitor.foreground import ForegroundLane
from canair.live.read.batch import BatchState
from tests.support.fakes import FakeTerminal, ok
from tests.support.monitor_controller import make_controller


def test_jobs_run_in_order_one_at_a_time():
    async def run() -> list[str]:
        lane = ForegroundLane()
        log: list[str] = []

        def job(name: str):
            async def go() -> str:
                log.append(f"start {name}")
                await asyncio.sleep(0.01)
                log.append(f"end {name}")
                return name

            return go

        first = lane.submit("a", job("a"))
        second = lane.submit("b", job("b"))
        assert await first == "a" and await second == "b"
        await lane.stop()
        return log

    assert asyncio.run(run()) == ["start a", "end a", "start b", "end b"]


def test_a_job_waits_until_the_link_is_back():
    async def run() -> tuple[bool, str]:
        up = {"v": False}
        lane = ForegroundLane(ready=lambda: up["v"])
        done = lane.submit("x", lambda: asyncio.sleep(0, result="sent"))
        await asyncio.sleep(0.1)
        waited = not done.done()
        up["v"] = True
        result = await done
        await lane.stop()
        return waited, result

    assert asyncio.run(run()) == (True, "sent")


def test_a_failing_job_reaches_its_future_and_the_next_job_still_runs():
    async def boom():
        raise ConnectionError("link dropped")

    async def run() -> str:
        lane = ForegroundLane()
        failed = lane.submit("boom", boom)
        after = lane.submit("next", lambda: asyncio.sleep(0, result="ok"))
        with pytest.raises(ConnectionError):
            await failed
        result = await after
        await lane.stop()
        return result

    assert asyncio.run(run()) == "ok"


def test_stop_cancels_what_is_queued():
    async def run() -> bool:
        lane = ForegroundLane(ready=lambda: False)
        done = lane.submit("never", lambda: asyncio.sleep(0))
        await asyncio.sleep(0)
        await lane.stop()
        return done.cancelled()

    assert asyncio.run(run())


class _SlowElm(FakeTerminal):
    """An ELM327-like terminal whose every exchange takes a little while."""

    cmd_count = 0
    cmd_time = 0.0

    async def send_uds(self, req: str, *args, **kwargs):
        await asyncio.sleep(0.02)
        return await super().send_uds(req, *args, **kwargs)


def test_a_user_request_goes_out_between_two_poll_requests():
    replies = {code: ok(f"62{code[2:]}AA") for code in ("22BC03", "22BC04", "22BC06")}
    replies["2FBC0C03"] = ok("6FBC0C03")
    terminal = _SlowElm(replies, serialize=True)
    steps = [{"type": "query", "ecu": "IGPM", "pids": []}]
    c = make_controller(terminal, steps, {}, verbose=False, schedule="fixed")
    c._ecu_index = {
        "IGPM": {
            "tx_id": 0x770,
            "pids": {code: {"parameters": {}} for code in ("22BC03", "22BC04", "22BC06")},
        }
    }
    c.elm_poller.batch_state = BatchState()

    async def user_request():
        async with terminal.transaction():
            await terminal.set_header(0x770)
            return await terminal.send_uds("2FBC0C03")

    async def run() -> None:
        c.schedule.begin_tick(0.0)
        poll = asyncio.create_task(c.elm_poller.poll())
        await asyncio.sleep(0.01)  # the first poll request is on the pipe
        await c.foreground.submit("ON", user_request)
        await poll
        await c.foreground.stop()

    asyncio.run(run())
    assert terminal.requests == ["22BC03", "2FBC0C03", "22BC04", "22BC06"]
