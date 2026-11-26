"""``monitor_once`` PIDs through a real controller: retirement and what reads them again."""

from __future__ import annotations

import asyncio

import pytest

from canair.ecus.pids import build_ecu_index
from tests.support.fakes import FakeTerminal, ok
from tests.support.monitor_controller import make_controller

NO_DATA = {"ok": False, "error": "NO DATA"}
ROW = ("IGPM (0x770)", "22BC03")
KEY = ("IGPM", "22BC03")


class _Terminal(FakeTerminal):
    cmd_count = 0
    cmd_time = 0.0


def _pids_data(once: bool = True) -> dict:
    return {
        "ecus": {
            "IGPM": {
                "tx_id": 0x770,
                "pids": {"22BC03": {"monitor_once": once}, "22BC04": {}},
            },
            "BCM": {"tx_id": 0x7A0, "pids": {"22C00B": {}}},
        }
    }


def _controller(responses: dict, steps: list[dict] | None = None, *, once: bool = True):
    terminal = _Terminal(responses)
    steps = steps or [{"type": "query", "ecu": "IGPM", "pids": ["BC03", "BC04"]}]
    c = make_controller(terminal, steps, {}, verbose=False, schedule="fixed")
    c._ecu_index = build_ecu_index(_pids_data(once))
    from canair.live.read.batch import BatchState

    c.elm_poller.batch_state = BatchState()
    return c, terminal


def _poll(c, n: int = 1) -> None:
    for _ in range(n):
        asyncio.run(c.poll_once())


def _rows(c) -> list[str]:
    return [e["pid"] for _label, entries in c.last_queries for e in entries]


def test_removing_and_re_adding_a_retired_pid_reads_it_again():
    c, terminal = _controller({"22BC03": ok("62BC0301"), "22BC04": ok("62BC0400")})
    _poll(c)
    c.selectors.apply({"IGPM": ["22BC04"]})
    _poll(c)
    c.selectors.apply({"IGPM": ["22BC03", "22BC04"]})
    _poll(c, 3)
    assert terminal.sent.count("22BC03") == 2
    assert "22BC03" in _rows(c)


def test_a_slowdown_does_not_outlive_retirement():
    c, terminal = _controller({"22BC03": NO_DATA, "22BC04": ok("62BC0400")})
    _poll(c, 2)
    assert c.schedule.slowdown(KEY) == 2
    terminal.responses["22BC03"] = ok("62BC0301")
    _poll(c, 3)
    assert c.schedule.is_retired(*KEY)
    assert c.schedule.slowdown(KEY) == 1
    assert c.backoff.slowdown(KEY) == 1
    assert ROW not in c.health.pid_notes(c.last_queries)


def test_an_ecu_of_retired_pids_does_not_keep_a_dead_link_alive():
    steps = [
        {"type": "query", "ecu": "IGPM", "pids": ["BC03"]},
        {"type": "query", "ecu": "BCM", "pids": ["C00B"]},
    ]
    c, terminal = _controller({"22BC03": ok("62BC0301"), "22C00B": ok("62C00B00")}, steps)
    _poll(c)
    terminal.responses["22C00B"] = NO_DATA
    _poll(c, 10)
    assert c.disconnected


@pytest.mark.parametrize("backoff", [True, False])
def test_a_dead_link_reconnects_on_the_fixed_schedule(backoff):
    steps = [{"type": "query", "ecu": "IGPM", "pids": ["BC04"]}]
    c, terminal = _controller({"22BC04": ok("62BC0400")}, steps)
    c.backoff.enabled = backoff
    _poll(c)
    terminal.responses["22BC04"] = NO_DATA
    _poll(c, 6)
    assert c.disconnected


def test_a_changed_definition_reads_a_retired_pid_again(monkeypatch):
    c, terminal = _controller({"22BC03": ok("62BC0301"), "22BC04": ok("62BC0400")})
    _poll(c)
    edited = _pids_data()
    edited["ecus"]["IGPM"]["pids"]["22BC03"]["parameters"] = {"X": {"expression": "B4"}}
    monkeypatch.setattr("canair.live.monitor.controller.load_pids", lambda _profile: edited)
    c.reload_pids()
    _poll(c, 2)
    assert terminal.sent.count("22BC03") == 2
    assert [row[0] for row in c._last_shown[ROW]["params"]] == ["X"]


def test_an_unchanged_reload_does_not_read_it_again(monkeypatch):
    c, terminal = _controller({"22BC03": ok("62BC0301"), "22BC04": ok("62BC0400")})
    _poll(c)
    monkeypatch.setattr("canair.live.monitor.controller.load_pids", lambda _p: _pids_data())
    c.reload_pids()
    _poll(c, 2)
    assert terminal.sent.count("22BC03") == 1


def test_a_new_segment_reads_it_again(tmp_path):
    c, terminal = _controller({"22BC03": ok("62BC0301"), "22BC04": ok("62BC0400")})
    c.captures_dir = tmp_path
    _poll(c, 2)
    c.recorder.new_segment("next")
    _poll(c, 2)
    assert terminal.sent.count("22BC03") == 2


def test_a_reconnect_reads_it_again():
    c, terminal = _controller({"22BC03": ok("62BC0301"), "22BC04": ok("62BC0400")})
    _poll(c, 2)
    fresh = _Terminal(dict(terminal.responses))
    c.rebind(fresh)
    _poll(c, 2)
    assert fresh.sent.count("22BC03") == 1


def test_a_retired_record_stops_vouching_for_a_state():
    c, _terminal = _controller({"22BC03": ok("62BC0301"), "22BC04": ok("62BC0400")})
    _poll(c)
    assert c.schedule.is_retired(*KEY)
    c.state._value_cycle["IGPM.X"] = ("IGPM", "22BC03", c.state._state_clock(*KEY))
    c.state.decoded_values["IGPM.X"] = 1.0
    _poll(c, 4)
    assert "IGPM.X" not in c.state.decoded_values
