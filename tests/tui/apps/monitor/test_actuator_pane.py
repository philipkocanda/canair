"""The monitor's actuator pane, over a real controller on a fake terminal."""

from __future__ import annotations

import pytest
from textual.widgets import Static

from canair.ecus.pids import build_ecu_index
from canair.tui.apps.monitor.actuators import ActuatorPane, ActuatorTable
from canair.tui.apps.monitor.app import MonitorApp
from canair.tui.apps.monitor.panes import ValuesPane
from canair.tui.help import HelpModal
from canair.tui.modals import ConfirmModal, TextPromptModal
from tests.support.fakes import FakeTerminal, ok
from tests.support.monitor_controller import make_controller

ON, OFF = "2FBC0C03", "2FBC0C00"
PIDS = {
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
                "F001": {"label": "Blend door", "on": "", "off": "2FF00100", "session": False},
            },
            "pids": {"22BC03": {"parameters": {"REAR_DEFOG": {"expression": "B4"}}}},
        },
        "BCM": {
            "tx_id": 0x7A0,
            "identity": {"id_protocol": "UDS"},
            "routines": {"12A1": {"label": "Test routine"}},
        },
    }
}


def _controller(actuate=("IGPM", "BCM")):
    replies = {
        ON: ok("6FBC0C03"),
        OFF: ok("6FBC0C00"),
        "22BC03": ok("62BC0301"),
        "2FF0010300": ok("6FF00103"),
        "310112A1": ok("710112A1"),
    }
    terminal = FakeTerminal(replies)
    terminal.cmd_count = 0  # type: ignore[attr-defined]
    terminal.cmd_time = 0.0  # type: ignore[attr-defined]
    steps = [{"type": "query", "ecu": "IGPM", "pids": ["BC03"]}]
    c = make_controller(terminal, steps, PIDS, actuate=tuple(actuate), schedule="fixed")
    return c, terminal


async def _start(c) -> None:
    await c.setup([])
    c._ecu_index = build_ecu_index(c.pids_data)


async def _settle(pilot, seconds: float = 0.15) -> None:
    await pilot.pause(seconds)


def _cell(app: MonitorApp, key: str, column: str) -> str:
    table = app.query_one(ActuatorTable)
    return table.get_cell(key, column).plain


@pytest.mark.asyncio
async def test_a_toggles_the_pane_and_takes_focus():
    c, _ = _controller()
    await _start(c)
    app = MonitorApp(c)
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(pilot)
        pane = app.query_one(ActuatorPane)
        assert not pane.has_class("shown")
        await pilot.press("A")
        await _settle(pilot)
        assert pane.has_class("shown")
        assert isinstance(app.focused, ActuatorTable)
        await pilot.press("escape")
        assert isinstance(app.focused, ValuesPane)
        await pilot.press("tab")
        assert isinstance(app.focused, ActuatorTable)
        await pilot.press("A")
        assert not pane.has_class("shown") and isinstance(app.focused, ValuesPane)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_enter_switches_an_output_on_while_polling_and_the_readback_shows():
    c, terminal = _controller()
    await _start(c)
    app = MonitorApp(c)
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(pilot, 0.3)
        await pilot.press("A")
        await _settle(pilot)
        await pilot.press("enter")
        await _settle(pilot, 0.3)
        assert ON in terminal.requests
        assert _cell(app, "IGPM/iocontrol/BC0C", "state") == "ON"
        assert _cell(app, "IGPM/iocontrol/BC0C", "readback") == "1"
        header = "\n".join(str(s.render()) for s in app.query(Static))
        assert "1 ON" in header
        await pilot.press("O")
        await _settle(pilot, 0.3)
        assert _cell(app, "IGPM/iocontrol/BC0C", "state") == "OFF"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_an_output_with_no_on_asks_for_a_value():
    c, terminal = _controller()
    await _start(c)
    app = MonitorApp(c)
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(pilot)
        await pilot.press("A", "j")
        await pilot.press("enter")
        await _settle(pilot)
        assert isinstance(app.screen, TextPromptModal)
        assert app.screen.query_one("Input").value == "00"  # type: ignore[union-attr]
        await pilot.press("enter")
        await _settle(pilot, 0.3)
        assert "2FF0010300" in terminal.requests
        await pilot.press("q")


@pytest.mark.asyncio
async def test_starting_a_routine_asks_first():
    c, terminal = _controller()
    await _start(c)
    app = MonitorApp(c)
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(pilot)
        await pilot.press("A", "j", "j")
        await pilot.press("exclamation_mark")
        await _settle(pilot)
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("escape")
        await _settle(pilot)
        assert "310112A1" not in terminal.requests
        await pilot.press("exclamation_mark")
        await _settle(pilot)
        await pilot.press("y")
        await _settle(pilot, 0.3)
        assert "310112A1" in terminal.requests
        assert _cell(app, "BCM/routine/12A1", "state") == "started"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_without_actuate_the_key_says_which_flag():
    c, _ = _controller(actuate=())
    await _start(c)
    app = MonitorApp(c)
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(pilot)
        await pilot.press("A")
        await _settle(pilot)
        assert "--actuate" in app.shell_messages.current
        assert not app.query(ActuatorPane)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_a_wide_terminal_puts_the_pane_beside_the_values():
    c, _ = _controller()
    await _start(c)
    app = MonitorApp(c)
    async with app.run_test(size=(180, 40)) as pilot:
        await _settle(pilot)
        assert app.query_one("#panes").has_class("wide")
        await pilot.press("A")
        await _settle(pilot)
        pane = app.query_one(ActuatorPane)
        assert pane.region.x > 0 and pane.region.y == app.query_one(ValuesPane).region.y
        await pilot.press("q")
    app = MonitorApp(c)
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        assert not app.query_one("#panes").has_class("wide")
        await pilot.press("q")


@pytest.mark.asyncio
async def test_help_lists_the_actuator_keys():
    c, _ = _controller()
    await _start(c)
    app = MonitorApp(c)
    async with app.run_test(size=(100, 50)) as pilot:
        await _settle(pilot)
        await pilot.press("question_mark")
        await _settle(pilot)
        assert isinstance(app.screen, HelpModal)
        rows = {d: k for k, d in app.screen.all_rows}
        assert rows["send OFF"] == "O" and rows["start routine"] == "!"
        assert rows["actuators"] == "A"
        await pilot.press("escape", "q")
