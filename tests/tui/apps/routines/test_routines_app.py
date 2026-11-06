"""The routines Textual app (canair.tui.apps.routines.app) over a real controller."""

from __future__ import annotations

import pytest
from textual.widgets import DataTable

from canair.tui.apps.routines.app import RoutinesApp
from canair.tui.help import HelpModal
from canair.tui.modals import ConfirmModal
from tests.support.iocontrol import routines_controller


def _cell(app: RoutinesApp, rid: str, column: str) -> str:
    cell = app.query_one(DataTable).get_cell(rid, column)
    return cell.plain if hasattr(cell, "plain") else str(cell)


async def _settle(pilot) -> None:
    await pilot.pause(0.1)
    await pilot.app.workers.wait_for_complete()
    await pilot.pause(0.05)


class TestRoutinesApp:
    @pytest.mark.asyncio
    async def test_enter_requests_the_results(self):
        c = routines_controller()
        app = RoutinesApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("enter")
            await _settle(pilot)
            assert c.terminal.sent == ["310312A1"]
            assert _cell(app, "12A1", "result") == "OK"
            assert _cell(app, "12A1", "response") == "710312A1"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_S_stops_the_routine_under_the_cursor(self):
        c = routines_controller()
        app = RoutinesApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("j", "S")
            await _settle(pilot)
            assert c.terminal.sent == ["310212A2"]
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_a_start_asks_first_and_cancel_sends_nothing(self):
        c = routines_controller()
        app = RoutinesApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("exclamation_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, ConfirmModal)
            # Focus starts on Cancel, so a stray enter does not start the routine.
            await pilot.press("enter")
            await _settle(pilot)
            assert not isinstance(app.screen, ConfirmModal)
            assert c.terminal.sent == []
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_y_in_the_confirmation_starts_the_routine(self):
        c = routines_controller()
        app = RoutinesApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("exclamation_mark", "y")
            await _settle(pilot)
            assert c.terminal.sent == ["310112A1"]
            assert _cell(app, "12A1", "subfn") == "startRoutine"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_v_toggles_verified_through_the_controller(self, monkeypatch):
        c = routines_controller()
        edits = []
        monkeypatch.setattr(c, "apply_edit", lambda *a: edits.append(a))
        app = RoutinesApp(c)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("v", "j", "v")
            await pilot.pause(0.1)
            assert edits == [("12A1", "verified", False), ("12A2", "verified", True)]
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_help_lists_the_routine_keys(self):
        app = RoutinesApp(routines_controller())
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            rows = {d: k for k, d in app.screen._rows}
            assert rows["request results"] == "enter"
            assert rows["stop routine"] == "S"
            assert rows["start routine"] == "!"
            await pilot.press("escape", "q")
