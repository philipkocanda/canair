"""Integration tests for the Textual monitor app (canlib.modes._monitor_tui).

Uses Textual's headless ``run_test`` pilot with a lightweight fake controller —
no CAN connection or TTY required.
"""

import asyncio

import pytest
from rich.text import Text
from textual.containers import VerticalScroll

from canlib.modes._monitor_tui import MonitorApp


class FakeController:
    """Stand-in for MonitorController: canned render + counting poll."""

    def __init__(self, *, keep_mode=None, n_lines=50, disconnect_after=None, has_captures=True,
                 query_label="BMS:2101"):
        self.cycle = 0
        self.elapsed = 0.0
        self.interval = 0.05
        self.last_cmds = 0
        self.last_elm_time = 0.0
        self.keep_mode = keep_mode
        self.disconnected = False
        self._n_lines = n_lines
        self._disconnect_after = disconnect_after
        self._has_captures = has_captures
        self._query_label = query_label
        self.saved = None
        self.show_rulers = False

    async def poll_once(self):
        self.cycle += 1
        self.elapsed = 0.01
        self.last_cmds = 3
        self.last_elm_time = 0.02
        if self._disconnect_after and self.cycle >= self._disconnect_after:
            self.disconnected = True

    def render(self) -> Text:
        t = Text()
        t.append(f"  Monitor cycle {self.cycle}\n", style="dim")
        for i in range(self._n_lines):
            t.append(f"  BMS 21{i:02X}: value={self.cycle * i}\n")
        return t

    def has_captures(self) -> bool:
        return self._has_captures

    def query_label(self) -> str:
        return self._query_label

    def save_now(self, label, state=None, notes=None) -> str:
        self.saved = (label, state, notes)
        return "Saved 1 payload → foo.yaml"


def _plain(renderable) -> str:
    return renderable.plain if hasattr(renderable, "plain") else str(renderable)


class TestMonitorApp:
    @pytest.mark.asyncio
    async def test_renders_and_polls(self):
        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause(0.25)
            body = _plain(app.query_one("#body").render())
            status = _plain(app.query_one("#status").render())
            assert "Monitor cycle" in body
            assert "BMS 2100" in body
            assert "cycle" in status and "quit" in status
            assert ctrl.cycle >= 1
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_pause_and_resume(self):
        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause(0.15)
            await pilot.press("space")  # pause
            assert app.paused is True
            frozen = ctrl.cycle
            await pilot.pause(0.2)
            assert ctrl.cycle == frozen  # no polling while paused
            await pilot.press("space")  # resume
            await pilot.pause(0.2)
            assert ctrl.cycle > frozen
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_toggle_rulers(self):
        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause(0.15)
            assert ctrl.show_rulers is False
            await pilot.press("r")  # enable rulers
            await pilot.pause(0.1)
            assert ctrl.show_rulers is True
            await pilot.press("r")  # disable again
            await pilot.pause(0.1)
            assert ctrl.show_rulers is False
            # The status line advertises the shortcut.
            status = _plain(app.query_one("#status").render())
            assert "r rulers" in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_follow_default_depends_on_keep_mode(self):
        assert MonitorApp(FakeController(keep_mode=None)).follow_enabled is False
        assert MonitorApp(FakeController(keep_mode="all")).follow_enabled is True

    @pytest.mark.asyncio
    async def test_scroll_position_independent_of_updates(self):
        # Dashboard (follow off): scrolling to top must NOT be undone by a poll.
        ctrl = FakeController(keep_mode=None, n_lines=60)
        app = MonitorApp(ctrl)
        async with app.run_test(size=(80, 12)) as pilot:
            await pilot.pause(0.15)
            scroll = app.query_one("#scroll", VerticalScroll)
            assert scroll.max_scroll_y > 0  # content overflows
            await pilot.press("g")  # jump to top
            await pilot.pause(0.2)  # let several polls update the body
            assert scroll.scroll_offset.y == 0  # stayed at top
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_follow_sticks_to_bottom(self):
        ctrl = FakeController(keep_mode="all", n_lines=60)
        app = MonitorApp(ctrl)
        async with app.run_test(size=(80, 12)) as pilot:
            await pilot.pause(0.1)
            scroll = app.query_one("#scroll", VerticalScroll)
            await pilot.press("G")  # bottom + follow on
            await pilot.pause(0.2)
            assert scroll.scroll_offset.y >= scroll.max_scroll_y - 1
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_disconnect_exits(self):
        ctrl = FakeController(disconnect_after=1)
        app = MonitorApp(ctrl)
        async with app.run_test(size=(80, 20)):
            for _ in range(40):
                if ctrl.disconnected:
                    break
                await asyncio.sleep(0.02)
            assert ctrl.disconnected is True

    @pytest.mark.asyncio
    async def test_save_opens_dialog_and_saves(self):
        from textual.widgets import Input

        from canlib.modes._monitor_tui import SaveDialog

        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            # Suggested label is pre-filled with the active query.
            assert app.screen.query_one("#f-label", Input).value == "BMS:2101"
            # 'q' must not quit while the modal owns the keyboard.
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert ctrl.saved is not None
            assert ctrl.saved[0] == "BMS:2101"
            status = app.query_one("#status").render()
            plain = status.plain if hasattr(status, "plain") else str(status)
            assert "Saved" in plain
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_save_cancelled_with_escape(self):
        from canlib.modes._monitor_tui import SaveDialog

        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, SaveDialog)
            assert ctrl.saved is None
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_save_no_captures_flashes_no_dialog(self):
        from canlib.modes._monitor_tui import SaveDialog

        ctrl = FakeController(has_captures=False)
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, SaveDialog)
            assert ctrl.saved is None
            await pilot.press("q")
