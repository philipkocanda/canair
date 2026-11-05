"""Every modal must be fully operable from the keyboard alone.

Regression tests for the class of defect where a modal shipped with a mouse-only
confirm button, an unfocused scroll container, or a long list with no way to
filter it. Each test drives real keys through Textual's headless pilot; none of
them touch a device.
"""

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Input, OptionList

from canair.cli.commands.captures.step_tui import CapturesStepApp
from canair.tui.apps.monitor import (
    EditParamDialog,
    EventLogModal,
    MonitorApp,
    SaveDialog,
    SessionInfoModal,
    SessionPickerScreen,
)
from canair.tui.apps.monitor_select import LiveMonitorSelectScreen
from canair.tui.help import HelpModal
from tests.support.monitor_picker import monitor_app as _monitor
from tests.support.monitor_tui import FakeController, FakeEditor
from tests.support.step_model import model as _model


def _focused_is_scroll(app) -> bool:
    return isinstance(app.focused, VerticalScroll)


class TestScrollableModalsFocusTheirScroll:
    """A scroll container that is never focused eats pgup/pgdn and the arrows."""

    @pytest.mark.asyncio
    async def test_help_modal(self):
        app = _monitor()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("?")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            assert _focused_is_scroll(app)
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_event_log_modal(self):
        app = _monitor()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("E")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EventLogModal)
            assert _focused_is_scroll(app)
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_session_info_modal(self):
        app = _monitor()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("i")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SessionInfoModal)
            assert _focused_is_scroll(app)
            await pilot.press("escape")
            await pilot.press("q")

    def test_help_modal_binds_scroll_keys(self):
        """A focused container is useless if no key is bound to move it."""
        from canair.tui.keys import keys_for

        bound = {k for b in HelpModal.BINDINGS for k in (b.key,)}
        for role in ("move_down", "move_up", "axis_start", "axis_end"):
            assert set(keys_for(role)) & bound, role


class TestDialogsHaveAKeyboardConfirm:
    """Enter only submits from an Input, so a dialog needs an explicit key."""

    @pytest.mark.asyncio
    async def test_save_dialog_ctrl_s_submits_from_a_checkbox(self):
        ctrl = FakeController(editor=FakeEditor())
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            app.screen.query_one("#f-label", Input).value = "kbd"
            # Tab away from the inputs: enter would now be dead.
            for _ in range(6):
                await pilot.press("tab")
            await pilot.press("ctrl+s")
            await pilot.pause(0.1)
            assert ctrl.saved is not None
            assert ctrl.saved[0] == "kbd"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_edit_param_dialog_ctrl_s_submits(self):
        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down")
            await pilot.press("e")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EditParamDialog)
            for _ in range(4):
                await pilot.press("tab")
            await pilot.press("ctrl+s")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, EditParamDialog)
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_text_prompt_ctrl_s_submits(self):
        app = CapturesStepApp(_model())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("J")
            await pilot.pause()
            app.screen.query_one("#prompt-input", Input).value = "0.5"
            await pilot.press("tab")  # leave the Input: enter is now dead
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert app.model.tol_s == 0.5


class TestPickersAreFilterable:
    """A picker over every ECU is unusable with arrow keys alone."""

    @pytest.mark.asyncio
    async def test_monitor_pid_picker_slash_focuses_filter(self):
        app = _monitor()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("p")
            await pilot.pause(0.1)
            assert isinstance(app.screen, LiveMonitorSelectScreen)
            await pilot.press("slash")
            await pilot.pause(0.1)
            assert isinstance(app.focused, Input)
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_session_picker_filter_narrows_the_ecu_list(self):
        """It used to be arrow-key-only over the whole ECU registry."""
        app = _monitor()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("t")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SessionPickerScreen)
            options = app.screen.query_one("#sess-pick-list", OptionList)
            assert options.option_count == 3
            assert isinstance(app.focused, Input)
            for ch in "hva":
                await pilot.press(ch)
            await pilot.pause(0.1)
            assert options.option_count == 1
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_plot_pid_picker_filters(self):
        from canair.tui.apps.decode_plot import PidPickerModal, PlotApp
        from tests.support.plot_model import plot_model as _plot_model

        options_in = [("BMS", "2101"), ("HVAC", "220100"), ("IGPM", "22BC07")]
        app = PlotApp(_plot_model(), reload_pid=lambda *a: None, pid_options=options_in)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("p")
            await pilot.pause(0.1)
            assert isinstance(app.screen, PidPickerModal)
            assert isinstance(app.focused, Input)
            options = app.screen.query_one(OptionList)
            assert options.option_count == 3
            for ch in "hva":
                await pilot.press(ch)
            await pilot.pause(0.1)
            assert options.option_count == 1
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, PidPickerModal)
