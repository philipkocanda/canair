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
                 query_label="BMS:2101", editor=None):
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
        self.editor = editor
        self.last_queries = []

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

    def save_now(self, label, vehicle_states=None, notes=None) -> str:
        self.saved = (label, vehicle_states, notes)
        return "Saved 1 payload → foo.yaml"


def _plain(renderable) -> str:
    return renderable.plain if hasattr(renderable, "plain") else str(renderable)


class FakeEditor:
    """Records calls from the TUI without touching disk or the CAN bus."""

    _ITEMS = [("BMS (0x7E4)", "2101", "SOC"), ("BMS (0x7E4)", "2101", "TEMP")]
    _FILTERS = ("all", "verified", "unverified", "enabled", "disabled")

    def __init__(self):
        self.selected = None
        self.filter_mode = "all"
        self.applied = None
        self.verified_toggles = 0
        self.enabled_toggles = 0

    def move(self, _last_queries, delta):
        if self.selected is None:
            self.selected = self._ITEMS[0] if delta >= 0 else self._ITEMS[-1]
        else:
            i = self._ITEMS.index(self.selected)
            self.selected = self._ITEMS[max(0, min(len(self._ITEMS) - 1, i + delta))]
        return self.selected

    def cycle_filter(self, _last_queries=None):
        self.filter_mode = self._FILTERS[
            (self._FILTERS.index(self.filter_mode) + 1) % len(self._FILTERS)
        ]
        return self.filter_mode

    def ensure_valid(self, _last_queries):
        pass

    def selection_label(self):
        if self.selected is None:
            return ""
        ecu, pid, name = self.selected
        return f"{ecu.split()[0]} {pid} {name}"

    def edit_target(self):
        if self.selected is None:
            return None
        return {"ecu": "BMS", "pid": "2101", "name": self.selected[2],
                "expression": "B4", "unit": "%", "min": "", "max": "",
                "notes": "", "verified": False, "enabled": True}

    def apply_edit(self, fields):
        self.applied = fields
        return "Saved BMS 2101 SOC"

    def toggle_verified(self):
        self.verified_toggles += 1
        return "SOC verified=true"

    def toggle_enabled(self):
        self.enabled_toggles += 1
        return "SOC enabled=false"


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


class TestMonitorEditing:
    @pytest.mark.asyncio
    async def test_select_moves_cursor_and_shows_in_status(self):
        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down")
            await pilot.pause(0.05)
            assert ed.selected == ("BMS (0x7E4)", "2101", "SOC")
            await pilot.press("down")
            await pilot.pause(0.05)
            assert ed.selected == ("BMS (0x7E4)", "2101", "TEMP")
            status = _plain(app.query_one("#status").render())
            assert "BMS 2101 TEMP" in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_cycle_filter(self):
        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("F")
            await pilot.pause(0.05)
            assert ed.filter_mode == "verified"
            status = _plain(app.query_one("#status").render())
            assert "verified" in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_verify_and_disable_toggles(self):
        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down")  # select SOC
            await pilot.press("v")
            await pilot.pause(0.05)
            assert ed.verified_toggles == 1
            await pilot.press("d")
            await pilot.pause(0.05)
            assert ed.enabled_toggles == 1
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_verify_without_selection_flashes(self):
        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("v")  # nothing selected
            await pilot.pause(0.05)
            assert ed.verified_toggles == 0
            status = _plain(app.query_one("#status").render())
            assert "Select a parameter" in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_edit_dialog_pauses_and_applies(self):
        from canlib.modes._monitor_tui import EditParamDialog

        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down")  # select SOC
            await pilot.press("e")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EditParamDialog)
            assert app.paused is True  # polling auto-paused during edit
            # Change the expression and save.
            expr = app.screen.query_one("#e-expr")
            expr.value = "B4/4"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, EditParamDialog)
            assert ed.applied is not None
            assert ed.applied["expression"] == "B4/4"
            assert app.paused is False  # restored
            status = _plain(app.query_one("#status").render())
            assert "Saved" in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_edit_without_selection_flashes(self):
        from canlib.modes._monitor_tui import EditParamDialog

        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("e")  # nothing selected
            await pilot.pause(0.1)
            assert not isinstance(app.screen, EditParamDialog)
            assert ed.applied is None
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_edit_cancel_restores_pause_state(self):
        from canlib.modes._monitor_tui import EditParamDialog

        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down")
            await pilot.press("e")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EditParamDialog)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, EditParamDialog)
            assert ed.applied is None
            assert app.paused is False
            await pilot.press("q")
