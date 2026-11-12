"""The monitor on the application shell: its menus, its zone clicks and its messages."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.widgets import OptionList

from canair.tui.apps.monitor.app import MonitorApp
from canair.tui.apps.monitor.overlays import EventLogModal, SessionInfoModal, SessionPickerScreen
from canair.tui.shell.menubar import MenuScreen
from canair.tui.shell.statusline import StatusLine
from tests.support.monitor_tui import FakeController, FakeEditor


def _plain(widget) -> str:
    rendered = widget.render()
    return rendered.plain if hasattr(rendered, "plain") else str(rendered)


def _options(app) -> list[str]:
    options = app.screen.query_one(OptionList)
    return [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]


def _zone_x(app, zone: str) -> int:
    span = next(s for s in app.query_one(StatusLine).spans[0] if s.zone == zone)
    return span.start + 2  # inside the zone, past the line's left padding


class TestMenus:
    @pytest.mark.asyncio
    async def test_f10_opens_the_record_menu_and_a_poll_item_runs(self):
        app = MonitorApp(FakeController(journal=object()))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("f10")
            await pilot.pause(0.1)
            assert isinstance(app.screen, MenuScreen) and app.screen.menu.title == "Record"
            assert any("Label recording…" in o for o in _options(app))
            await pilot.press("right")  # Poll; its first item is "Pause polling"
            await pilot.pause(0.1)
            assert app.screen.menu.title == "Poll"
            assert _options(app)[0].startswith("[ ] Pause polling")
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.paused is True
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_a_view_choice_shows_which_mode_is_on_and_sets_it(self):
        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("alt+v")
            await pilot.pause(0.1)
            options = _options(app)
            assert options[3].startswith("(•) Signals and bytes")  # full, the default
            await pilot.press("down", "enter")  # "Value ranges"
            await pilot.pause(0.1)
            assert ctrl.view_mode == "ranges"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_a_signal_command_needs_a_selection(self):
        app = MonitorApp(FakeController(editor=FakeEditor()))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await app.run_command("edit")
            await pilot.pause(0.1)
            assert "Select a signal first" in _plain(app.query_one("#context"))
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_a_new_recording_needs_save(self):
        app = MonitorApp(FakeController(journal=None))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("alt+r")
            await pilot.pause(0.1)
            options = app.screen.query_one(OptionList)
            assert options.get_option("new").disabled
            await pilot.press("escape")
            await pilot.press("q")


class TestZoneClicks:
    @pytest.mark.asyncio
    async def test_the_link_zone_opens_the_event_log_with_this_runs_messages(self):
        app = MonitorApp(FakeController())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            app.message("Poll rate: 2.0s")
            await pilot.click(StatusLine, offset=(_zone_x(app, "link"), 0))
            await pilot.pause(0.2)
            assert isinstance(app.screen, EventLogModal)
            assert "Poll rate: 2.0s" in _plain(app.screen.query_one("#evlog-messages"))
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_the_poll_zone_opens_the_poll_menu_above_the_status_line(self):
        app = MonitorApp(FakeController())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.click(StatusLine, offset=(_zone_x(app, "poll"), 0))
            await pilot.pause(0.2)
            assert isinstance(app.screen, MenuScreen) and app.screen.menu.title == "Poll"
            menu = app.screen.query_one(OptionList)
            assert menu.region.bottom <= app.query_one(StatusLine).region.y
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_the_recording_and_car_zones_open_their_overlays(self):
        ctrl = FakeController(journal=object())
        ctrl.state.suggested_state = lambda: "CHARGING"
        ctrl.selectors = SimpleNamespace(available_ecus=lambda: ["BMS"])
        app = MonitorApp(ctrl)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.click(StatusLine, offset=(_zone_x(app, "record"), 0))
            await pilot.pause(0.2)
            assert isinstance(app.screen, SessionInfoModal)
            await pilot.press("escape")
            await pilot.pause(0.1)
            car = next(s for s in app.query_one(StatusLine).spans[0] if s.zone == "car")
            await pilot.click(StatusLine, offset=(car.start + 2, 0))
            await pilot.pause(0.2)
            assert isinstance(app.screen, SessionPickerScreen)
            await pilot.press("escape")
            await pilot.press("q")


class TestMessages:
    @pytest.mark.asyncio
    async def test_an_error_stays_until_the_next_key(self):
        app = MonitorApp(FakeController())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            app._flash("Session switch failed: BMS answered 7F 10 22", "error")
            await pilot.pause(0.6)
            assert "✗ Session switch failed" in _plain(app.query_one("#context"))
            await pilot.press("r")
            await pilot.pause(0.1)
            assert "Session switch failed" not in _plain(app.query_one("#context"))
            assert app.shell_messages.history[-1].severity == "error"
            await pilot.press("q")
