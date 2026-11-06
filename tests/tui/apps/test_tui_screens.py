"""How ``esc``, ``q`` and ``?`` behave on each kind of screen, and what the apps flash.

One table of behaviour (the cli-tui-standards skill's "Keys" section):

- ``esc`` backs out one level and never quits; at a top level it does nothing.
- ``q`` quits at a top level and closes a read-only overlay; in a text field it is text.
- ``?`` opens help on every screen; in a text field it is text.
"""

from __future__ import annotations

import pytest
from textual.widgets import Input, Label

from canair.analysis.sniff_stats import SniffStats
from canair.cli.profile_context import active
from canair.tui.apps.captures_step.app import CapturesStepApp
from canair.tui.apps.decode_plot.app import PlotApp
from canair.tui.apps.monitor.app import MonitorApp
from canair.tui.apps.monitor.dialogs import EditParamDialog, SaveDialog
from canair.tui.apps.monitor.overlays import EventLogModal, SessionInfoModal
from canair.tui.apps.monitor_select.app import LiveMonitorSelectScreen
from canair.tui.apps.sniff.app import SniffApp
from canair.tui.help import HelpModal
from tests.support.monitor_picker import monitor_app as _monitor
from tests.support.monitor_tui import FakeController, FakeEditor
from tests.support.plot_model import plot_model as _plot_model
from tests.support.step_model import model as _step_model


def _plain(renderable) -> str:
    return renderable.plain if hasattr(renderable, "plain") else str(renderable)


def _top_level_apps():
    return {
        "monitor": lambda: MonitorApp(FakeController()),
        "stepper": lambda: CapturesStepApp(_step_model()),
        "plot": lambda: PlotApp(_plot_model(), profile=active()),
        "sniff": lambda: SniffApp(SniffStats(), host="10.0.0.1"),
    }


class TestEscapeAtTheTopLevel:
    @pytest.mark.parametrize("name", ["monitor", "stepper", "plot", "sniff"])
    @pytest.mark.asyncio
    async def test_escape_never_quits(self, name):
        app = _top_level_apps()[name]()
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert app.is_running
            await pilot.press("q")

    @pytest.mark.parametrize("name", ["monitor", "stepper", "plot", "sniff"])
    @pytest.mark.asyncio
    async def test_q_quits(self, name):
        app = _top_level_apps()[name]()
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("q")
            await pilot.pause(0.1)
            assert not app.is_running

    @pytest.mark.parametrize("name", ["monitor", "stepper", "plot", "sniff"])
    @pytest.mark.asyncio
    async def test_ctrl_q_is_the_declared_force_quit(self, name):
        app = _top_level_apps()[name]()
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("ctrl+q")
            await pilot.pause(0.1)
            assert not app.is_running

    @pytest.mark.asyncio
    async def test_q_in_help_closes_help_not_the_sniff_app(self):
        """sniff bound `q` with priority, which reached past the help overlay."""
        app = SniffApp(SniffStats(), host="10.0.0.1")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            await pilot.press("q")
            await pilot.pause(0.1)
            assert app.is_running
            assert not isinstance(app.screen, HelpModal)
            await pilot.press("q")


class TestPlotInfoView:
    @pytest.mark.asyncio
    async def test_q_closes_the_info_view_before_it_quits(self):
        app = PlotApp(_plot_model(), profile=active())
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("i")
            await pilot.pause(0.1)
            assert app.model.show_info
            await pilot.press("q")
            await pilot.pause(0.1)
            assert app.is_running
            assert not app.model.show_info
            await pilot.press("q")
            await pilot.pause(0.1)
            assert not app.is_running

    @pytest.mark.asyncio
    async def test_the_status_bar_names_the_keys_that_close_the_view(self):
        app = PlotApp(_plot_model(), profile=active())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("i")
            await pilot.pause(0.1)
            status = _plain(app.query_one("#status").render())
            assert "esc close" in status
            await pilot.press("q")
            await pilot.press("q")


class TestPlotHints:
    @pytest.mark.asyncio
    async def test_byte_order_is_b_and_annotate_is_e(self):
        app = PlotApp(_plot_model(), profile=active())
        async with app.run_test(size=(200, 30)) as pilot:
            await pilot.pause(0.1)
            status = _plain(app.query_one("#status").render())
            assert "b byte order" in status
            assert "e endian" not in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_comma_and_full_stop_pan_and_plus_minus_zoom(self):
        app = PlotApp(_plot_model(), profile=active())
        async with app.run_test(size=(240, 30)) as pilot:
            await pilot.pause(0.1)
            status = _plain(app.query_one("#status").render())
            assert ", pan left" in status and ". pan right" in status
            assert "+ zoom in" in status and "- zoom out" in status
            await pilot.press("q")


class TestPickerOverTheMonitor:
    """A plain `Screen` pushed over an app left the app's keys live."""

    @pytest.mark.asyncio
    async def test_s_does_not_open_the_save_dialog(self):
        app = _monitor()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("p")
            await pilot.pause(0.1)
            assert isinstance(app.screen, LiveMonitorSelectScreen)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, LiveMonitorSelectScreen)
            assert not any(isinstance(s, SaveDialog) for s in app.screen_stack)

    @pytest.mark.asyncio
    async def test_the_monitors_other_keys_stay_out_too(self):
        ctrl_app = _monitor(journal=object())
        async with ctrl_app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("p")
            await pilot.pause(0.1)
            before = ctrl_app.controller.show_rulers
            paused = ctrl_app.paused
            await pilot.press("n", "r", "m", "V", "E", "i", "t")
            await pilot.pause(0.1)
            assert isinstance(ctrl_app.screen, LiveMonitorSelectScreen)
            assert len(ctrl_app.screen_stack) == 2
            assert ctrl_app.controller.show_rulers == before
            assert ctrl_app.paused == paused

    @pytest.mark.asyncio
    async def test_help_opens_from_the_picker(self):
        app = _monitor()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("p")
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            await pilot.press("escape")
            assert isinstance(app.screen, LiveMonitorSelectScreen)


class TestMonitorOverlays:
    @pytest.mark.asyncio
    async def test_q_closes_the_event_log_and_the_monitor_survives(self):
        app = MonitorApp(FakeController())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("E")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EventLogModal)
            await pilot.press("q")
            await pilot.pause(0.1)
            assert app.is_running
            assert not isinstance(app.screen, EventLogModal)
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_question_mark_opens_help_over_an_overlay(self):
        app = MonitorApp(FakeController())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("i")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SessionInfoModal)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SessionInfoModal)
            await pilot.press("escape", "q")

    @pytest.mark.asyncio
    async def test_session_info_hint_advertises_only_keys_that_work_there(self):
        app = MonitorApp(FakeController())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("i")
            await pilot.pause(0.1)
            hint = _plain(app.screen.query_one("#sess-hint", Label).render())
            assert hint == "↑/↓ scroll · pgup/pgdn page · esc close"
            await pilot.press("escape", "q")

    @pytest.mark.asyncio
    async def test_s_inside_the_session_info_does_nothing(self):
        app = MonitorApp(FakeController())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("i")
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SessionInfoModal)
            await pilot.press("escape", "q")


class TestDialogsCheckTheirInput:
    @pytest.mark.asyncio
    async def test_the_label_is_optional_and_the_placeholder_says_what_it_uses(self):
        ctrl = FakeController(editor=FakeEditor(), query_label="BMS:2101")
        app = MonitorApp(ctrl)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            label = app.screen.query_one("#save-label", Input)
            assert label.placeholder == 'Label (empty uses "BMS:2101")'
            label.value = ""
            await pilot.press("ctrl+s")
            await pilot.pause(0.1)
            assert ctrl.recorder.saved is not None and ctrl.recorder.saved[0] == "BMS:2101"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_the_expression_is_required_inside_the_edit_dialog(self):
        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down", "e")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EditParamDialog)
            app.screen.query_one("#edit-expr", Input).value = ""
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EditParamDialog)
            error = app.screen.query_one("#edit-error", Label)
            assert _plain(error.render()) == "The expression is required"
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_the_stepper_goto_prompt_refuses_a_bad_number_in_place(self):
        from canair.tui.modals import TextPromptModal

        app = CapturesStepApp(_step_model())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("colon")
            await pilot.pause()
            app.screen.query_one("#prompt-input", Input).value = "x"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, TextPromptModal)
            assert "Not a frame number" in _plain(app.screen.query_one("#prompt-error").render())
            assert app.flash.message == ""


class TestFailuresLookLikeFailures:
    @pytest.mark.asyncio
    async def test_a_failed_save_is_red_with_a_cross(self):
        ctrl = FakeController(editor=FakeEditor())

        def boom(*_a, **_k):
            raise OSError("disk full")

        ctrl.recorder.save_now = boom
        app = MonitorApp(ctrl)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            await pilot.press("ctrl+s")
            await pilot.pause(0.1)
            assert app.flash.severity == "error"
            markup = app.flash.item().markup
            assert "red" in markup and "✗" in markup and "green" not in markup
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_a_result_the_controller_returns_takes_the_severity_of_its_words(self):
        ed = FakeEditor()
        ctrl = FakeController(editor=ed)
        app = MonitorApp(ctrl)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("v")  # nothing selected
            await pilot.pause(0.1)
            assert app.flash.severity == "warning"
            assert not app.flash.message.endswith(".")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_the_stepper_flashes_a_refused_drop_as_a_warning(self):
        app = CapturesStepApp(_step_model(keys=[("HVAC", "220100")]))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()
            assert app.flash.severity == "warning"
            assert app.flash.message == "Cannot drop the last PID"
            await pilot.press("q")


class TestSniffColours:
    @pytest.mark.asyncio
    async def test_help_has_a_legend_for_the_changed_byte_colour(self):
        app = SniffApp(SniffStats(), host="10.0.0.1")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            meanings = [m for _s, _st, m in app.screen._legend]
            assert any("changed" in m for m in meanings)
            await pilot.press("escape", "q")
