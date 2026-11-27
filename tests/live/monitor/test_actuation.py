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


class _SessionLoss(FakeTerminal):
    """An IGPM that refuses IOControl with NRC 0x7F while it is out of its session."""

    def __init__(self) -> None:
        super().__init__(_replies())
        self.in_session = False

    def _answer_uds(self, req, *args) -> dict:
        reply = super()._answer_uds(req, *args)
        if len(req) == 4 and req.startswith("10"):
            self.in_session = req != "1001"
        elif req.startswith("2F") and not self.in_session:
            return {"ok": False, "nrc": 0x7F, "hex": "7F2F7F", "raw": "7F 2F 7F"}
        return reply


def test_an_off_after_the_ecu_left_its_session_reopens_it_on_the_monitors_manager():
    terminal = _SessionLoss()
    steps = [{"type": "query", "ecu": "IGPM", "pids": ["BC03"]}]
    c = make_controller(terminal, steps, _pids(), actuate=("IGPM",))
    c._ecu_index = build_ecu_index(c.pids_data)
    assert c.actuation is not None
    relay = _io(c, "B108")

    async def run() -> None:
        await c.actuation.submit_toggle(relay)
        terminal.in_session = False  # its S3 timer ran out; canair still tracks 10 C0
        assert c.sm.session_mode(0x770) == 0xC0
        await c.actuation.submit_off(relay)
        await c.foreground.stop()

    asyncio.run(run())
    assert terminal.requests == [RELAY_ON, RELAY_OFF, RELAY_OFF]
    assert terminal.sessions == ["C0", "C0"]
    assert terminal.sent[-1] == "1001"  # nothing held: back to the default session
    assert c.actuation.on_count() == 0
    status = c.actuation.take_status()
    assert status is not None and status[1] == "warning"
    assert "B108: the ECU had left its session" in status[0]


def _readback(c, value: float) -> None:
    row = ("REAR_DEFOG", value, "", "B4:1", None, False, "")
    c.last_queries = [("IGPM (0x770)", [{"pid": "22BC03", "params": [row]}])]


class TestReadbackWarnings:
    def _on(self):
        c, _terminal = _controller()
        assert c.actuation is not None
        _readback(c, 0.0)

        async def run() -> None:
            await c.actuation.submit_toggle(_io(c))
            await c.foreground.stop()

        asyncio.run(run())
        return c, c.actuation

    def test_a_readback_back_at_its_value_from_before_the_on_warns_once(self):
        c, a = self._on()
        assert a.readback_warnings() == []
        _readback(c, 1.0)
        assert a.readback_warnings() == []
        _readback(c, 0.0)
        assert a.readback_warnings() == [
            "IGPM BC0C may be off: REAR_DEFOG is back at 0, as before the switch, so the ECU may "
            "have left its session"
        ]
        assert a.readback_warnings() == []

    def test_an_output_switched_off_is_not_watched(self):
        c, a = self._on()
        _readback(c, 1.0)
        a.readback_warnings()

        async def off() -> None:
            await a.submit_off(_io(c))
            await c.foreground.stop()

        asyncio.run(off())
        _readback(c, 0.0)
        assert a.readback_warnings() == []

    def test_an_output_with_a_request_on_the_way_is_left_alone(self):
        c, a = self._on()
        _readback(c, 1.0)
        a.readback_warnings()
        _readback(c, 0.0)
        a.pending.add(_io(c).key)
        assert a.readback_warnings() == []
