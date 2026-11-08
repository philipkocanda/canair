"""The monitor's --actuate actuators: requests on the lane, sessions kept and restored."""

from __future__ import annotations

import asyncio

from canair.cli.profile_context import active
from canair.ecus.pids import build_ecu_index
from canair.live.monitor.actuation import actuate_ecus, actuate_problem
from canair.live.monitor.run import _release_actuators
from tests.support.fakes import FakeTerminal, ok
from tests.support.monitor_controller import make_controller

ON, OFF = "2FBC0C03", "2FBC0C00"
RELAY_ON, RELAY_OFF = "2FB10803", "2FB10800"


def _pids() -> dict:
    return {
        "ecus": {
            "IGPM": {
                "tx_id": 0x770,
                "identity": {"id_protocol": "UDS"},
                "iocontrol": {
                    "BC0C": {
                        "label": "Rear defogger",
                        "on": ON,
                        "off": OFF,
                        "session": False,
                        "status_param": "REAR_DEFOG",
                    },
                    "B108": {
                        "label": "ACC relay",
                        "on": RELAY_ON,
                        "off": RELAY_OFF,
                        "session_mode": "C0",
                    },
                },
                "pids": {"22BC03": {"parameters": {"REAR_DEFOG": {"expression": "B4:1"}}}},
            },
            "BCM": {
                "tx_id": 0x7A0,
                "identity": {"id_protocol": "UDS"},
                "routines": {"12A1": {"label": "Test routine"}},
            },
            "OBC": {
                "tx_id": 0x7E5,
                "identity": {"id_protocol": "KWP2000"},
                "routines": {"01": {}},
            },
            "VCU": {"tx_id": 0x7E2, "pids": {"2101": {"parameters": {}}}},
        }
    }


def _replies() -> dict:
    return {
        ON: ok("6FBC0C03"),
        OFF: ok("6FBC0C00"),
        RELAY_ON: ok("6FB10803"),
        RELAY_OFF: ok("6FB10800"),
        "310112A1": ok("710112A1"),
        "310212A1": ok("710212A1"),
    }


def _controller(actuate=("IGPM", "BCM")):
    terminal = FakeTerminal(_replies())
    steps = [{"type": "query", "ecu": "IGPM", "pids": ["BC03"]}]
    c = make_controller(terminal, steps, _pids(), actuate=tuple(actuate))
    c._ecu_index = build_ecu_index(c.pids_data)
    return c, terminal


def _io(c, did="BC0C"):
    assert c.actuation is not None
    return c.actuation.actuator(f"IGPM/iocontrol/{did}")


def test_the_flag_takes_repeats_and_commas():
    assert actuate_ecus(["igpm,BCM", "IGPM", " skm "]) == ["IGPM", "BCM", "SKM"]


class TestActuateProblem:
    def test_an_unknown_ecu_is_refused(self):
        assert "no such ECU" in (actuate_problem(_pids(), ["NOPE"], profile=active()) or "")

    def test_an_ecu_without_actuators_is_refused(self):
        assert "no iocontrol" in (actuate_problem(_pids(), ["VCU"], profile=active()) or "")

    def test_a_kwp_ecu_with_only_routines_is_refused(self):
        assert actuate_problem(_pids(), ["OBC"], profile=active()) is not None

    def test_ecus_with_actuators_or_routines_pass(self):
        assert actuate_problem(_pids(), ["IGPM", "BCM"], profile=active()) is None


def test_the_rows_are_io_then_routines_ecu_by_ecu():
    c, _ = _controller()
    assert c.actuation is not None
    keys = [a.key for a in c.actuation.actuators()]
    assert keys == ["IGPM/iocontrol/BC0C", "IGPM/iocontrol/B108", "BCM/routine/12A1"]
    assert _io(c).status_param == "REAR_DEFOG"


def test_without_the_flag_there_is_nothing_to_actuate():
    c, _ = _controller(actuate=())
    assert c.actuation is None


def test_a_toggle_goes_out_on_the_lane_and_counts_as_on():
    c, terminal = _controller()
    assert c.actuation is not None

    async def run() -> None:
        await c.actuation.submit_toggle(_io(c))
        await c.foreground.stop()

    asyncio.run(run())
    assert terminal.requests == [ON]
    assert terminal.addressed[-1] == (0x770, ON)
    assert c.actuation.on_count() == 1
    assert c.actuation.pending == set()


def test_the_session_switch_refuses_an_ecu_that_holds_an_output():
    c, terminal = _controller()
    assert c.actuation is not None

    async def run() -> str:
        await c.actuation.submit_toggle(_io(c))
        msg = await c.sessions.switch("IGPM", "03")
        await c.foreground.stop()
        return msg

    msg = asyncio.run(run())
    assert "IGPM holds BC0C" in msg
    assert terminal.sessions == []


def test_the_previous_session_comes_back_after_the_release():
    c, terminal = _controller()
    assert c.actuation is not None

    async def run() -> None:
        await c.sm.open_session(0x770, mode="03")
        await c.actuation.submit_toggle(_io(c, "B108"))
        assert c.sm.session_mode(0x770) == 0xC0
        await c.actuation.submit_off(_io(c, "B108"))
        await c.foreground.stop()

    asyncio.run(run())
    assert terminal.sessions == ["03", "C0", "03"]
    assert c.sm.session_mode(0x770) == 0x03


def test_an_ecu_with_no_session_before_is_returned_to_the_default_session():
    c, terminal = _controller()
    assert c.actuation is not None

    async def run() -> None:
        await c.actuation.submit_toggle(_io(c, "B108"))
        await c.actuation.submit_off(_io(c, "B108"))
        await c.foreground.stop()

    asyncio.run(run())
    assert terminal.sessions == ["C0"]
    assert terminal.requests[-1] == RELAY_OFF
    assert terminal.sent[-1] == "1001"  # closed
    assert c.sm.session_mode(0x770) is None


def test_a_started_routine_holds_its_ecus_session_until_it_is_stopped():
    c, _terminal = _controller()
    assert c.actuation is not None
    routine = c.actuation.actuator("BCM/routine/12A1")
    assert routine is not None

    async def run() -> None:
        await c.actuation.submit_routine(routine, 0x01)
        assert c.actuation.held("BCM") == ["12A1"]
        assert c.sm.session_mode(0x7A0) == 0x03
        await c.actuation.submit_routine(routine, 0x02)
        await c.foreground.stop()

    asyncio.run(run())
    assert c.actuation.held("BCM") == []
    assert c.sm.session_mode(0x7A0) is None


def test_exit_releases_every_output_still_on(capsys):
    c, terminal = _controller()
    assert c.actuation is not None

    async def run() -> None:
        await c.actuation.submit_toggle(_io(c))
        await _release_actuators(c)

    asyncio.run(run())
    assert terminal.requests == [ON, OFF]
    assert "released IGPM BC0C" in capsys.readouterr().err


def test_a_reconnect_hands_back_what_was_on():
    c, terminal = _controller()
    assert c.actuation is not None

    async def run() -> None:
        await c.actuation.submit_toggle(_io(c))
        await c.foreground.stop()
        await c.actuation.after_reconnect()

    asyncio.run(run())
    assert terminal.requests == [ON, OFF]
    assert c.actuation.on_count() == 0
    assert c.actuation.take_status() == ("Released after the reconnect: IGPM BC0C", "warning")
