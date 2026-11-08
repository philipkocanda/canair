"""The IOControl Textual app (canair.tui.apps.iocontrol.app) over a real controller."""

from __future__ import annotations

import pytest
from textual.widgets import DataTable

from canair.tui.apps.iocontrol.app import IOControlApp
from canair.tui.help import HelpModal
from canair.tui.modals import TextPromptModal
from canair.tui.row_view import hex_problem
from tests.support.iocontrol import io_controller


def _plain(renderable) -> str:
    return renderable.plain if hasattr(renderable, "plain") else str(renderable)


def _cell(app: IOControlApp, did: str, column: str) -> str:
    return _plain(app.query_one(DataTable).get_cell(did, column))


async def _settle(pilot) -> None:
    await pilot.pause(0.1)
    await pilot.app.workers.wait_for_complete()
    await pilot.pause(0.05)


class TestIOControlApp:
    @pytest.mark.asyncio
    async def test_enter_switches_the_row_on_and_again_off(self):
        c = io_controller()
        app = IOControlApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("enter")
            await _settle(pilot)
            assert c.terminal.sent == ["2FBC0103"]
            assert _cell(app, "BC01", "cmd") == "ON"
            assert _cell(app, "BC01", "response") == "6FBC0103"
            await pilot.press("enter")
            await _settle(pilot)
            assert c.terminal.sent[-1] == "2FBC0100"
            assert _cell(app, "BC01", "cmd") == "OFF"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_j_moves_down_and_O_sends_off(self):
        c = io_controller()
        app = IOControlApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("j", "O")
            await _settle(pilot)
            assert c.terminal.sent == ["2FBC0200"]
            await pilot.press("k", "space")
            await _settle(pilot)
            assert c.terminal.sent[-1] == "2FBC0103"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_a_did_without_on_asks_for_a_value_and_sends_it(self):
        c = io_controller()
        app = IOControlApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("G", "enter")
            await _settle(pilot)
            assert isinstance(app.screen, TextPromptModal)
            assert c.terminal.sent == []
            # The prompt is seeded with 00; enter submits it instead of toggling again.
            await pilot.press("backspace", "backspace", "4", "2", "enter")
            await _settle(pilot)
            assert not isinstance(app.screen, TextPromptModal)
            assert c.terminal.sent == ["2FBC030342"]
            assert c.last_value["BC03"] == b"\x42"
            await pilot.press("plus")
            await _settle(pilot)
            assert c.terminal.sent[-1] == "2FBC030343"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_V_cycles_the_view_to_the_discoveries(self):
        c = io_controller()
        app = IOControlApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            assert app.query_one(DataTable).row_count == 3
            await pilot.press("V")
            await pilot.pause(0.1)
            assert c.view_mode == "all"
            assert app.query_one(DataTable).row_count == 4
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_v_toggles_verified_through_the_controller(self, monkeypatch):
        c = io_controller()
        edits = []
        monkeypatch.setattr(c, "apply_edit", lambda *a: edits.append(a))
        app = IOControlApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("v", "j", "v")
            await pilot.pause(0.1)
            assert edits == [("BC01", "verified", False), ("BC02", "verified", True)]
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_a_refusal_is_flashed_as_an_error(self):
        c = io_controller()
        c.protocol = "kwp"
        app = IOControlApp(c)
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("w")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, TextPromptModal)
            assert app.flash.severity == "error" and "UDS-only" in app.flash.message
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_help_lists_the_actuator_keys(self):
        app = IOControlApp(io_controller())
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            rows = {d: k for k, d in app.screen._rows}
            assert rows["toggle"] == "enter/space"
            assert rows["send OFF"] == "O"
            assert rows["set value"] == "w"
            await pilot.press("escape", "q")

    @pytest.mark.asyncio
    async def test_q_waits_for_the_request_in_flight(self):
        c = io_controller()
        app = IOControlApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            app._inflight = True
            await pilot.press("q")
            await pilot.pause(0.1)
            assert app.is_running and c.stopping
            app._inflight = False
            await pilot.press("q")
            await pilot.pause(0.1)
            assert not app.is_running


@pytest.mark.parametrize(
    ("value", "ok"), [("01", True), ("0a 0b", True), ("", False), ("1", False), ("zz", False)]
)
def test_hex_problem(value, ok):
    assert (hex_problem(value) is None) is ok
