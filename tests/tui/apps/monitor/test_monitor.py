"""Integration tests for the Textual monitor app (canair.tui.apps.monitor.app).

Uses Textual's headless ``run_test`` pilot with a lightweight fake controller —
no CAN connection or TTY required.
"""

import asyncio

import pytest
from textual.containers import VerticalScroll

from canair.tui.apps.monitor.app import MonitorApp
from tests.support.monitor_tui import FakeController, FakeEditor


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
    async def test_a_pause_is_recorded_when_polling_stops_and_resumes(self):
        calls: list[bool] = []
        ctrl = FakeController()
        ctrl.link_events = type("T", (), {"paused": lambda _self, on: calls.append(on)})()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause(0.15)
            await pilot.press("space")
            await pilot.pause(0.2)
            await pilot.press("space")
            await pilot.pause(0.2)
            assert calls == [True, False]
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
            assert "r byte ruler" in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_scroll_position_never_moved_by_a_poll(self):
        """The monitor never follows the tail — a repaint must not move the view.

        Regression for the removed follow/manual mode: reading a byte mid-buffer
        was interrupted whenever fresh data arrived while pinned to the bottom.
        """
        for keep_mode in (None, "all"):
            ctrl = FakeController(keep_mode=keep_mode, n_lines=60)
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
    async def test_no_follow_binding(self):
        assert "f" not in {b.key for b in MonitorApp.BINDINGS}

    @pytest.mark.asyncio
    async def test_status_bar_survives_a_phone_width(self):
        """A 45-column terminal must still show the way out and to the help."""
        ctrl = FakeController(journal=object())
        app = MonitorApp(ctrl)
        async with app.run_test(size=(45, 16)) as pilot:
            await pilot.pause(0.2)
            status = _plain(app.query_one("#status").render())
            assert "? help" in status and "q quit" in status
            assert "REC" in status  # an active recording is never shed
            # Two composed lines, neither wrapped out of the docked region.
            assert len(status.splitlines()) == 2
            assert all(len(ln) <= 45 for ln in status.splitlines())
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

        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            # Suggested label is pre-filled with the active query.
            assert app.screen.query_one("#save-label", Input).value == "BMS:2101"
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
    async def test_empty_state_shows_autodetect_hint(self):
        # The state field is free-text (not a checkbox): an empty field must say
        # the state will be auto-detected from data on save, so a user who leaves
        # it blank knows the span-aware back-fill is the safety net.
        from textual.widgets import Label

        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController()  # no suggested_states -> state field opens empty
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            status = app.screen.query_one("#save-state-status", Label)
            assert "auto-detect" in _plain(status.render())
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_prefilled_state_shows_autodetected_caption(self):
        from textual.widgets import Input, Label

        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController()
        # auto-suggest pre-fills the field (plural API)
        ctrl.suggested_states = lambda: ["charging"]
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            assert app.screen.query_one("#save-state", Input).value == "charging"
            status = app.screen.query_one("#save-state-status", Label)
            plain = _plain(status.render())
            assert "auto-detected" in plain and "charging" in plain
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_save_cancelled_with_escape(self):
        from canair.tui.apps.monitor.app import SaveDialog

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
        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController(has_captures=False)
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, SaveDialog)
            assert ctrl.saved is None
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_new_segment_opens_dialog_and_rotates(self):
        from textual.widgets import Input

        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController(journal=object())  # journal present ⇒ recording
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("n")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            app.screen.query_one("#save-label", Input).value = "seg2"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert ctrl.new_seg is not None
            assert ctrl.new_seg[0] == "seg2"
            assert ctrl.saved is None  # 'n' is distinct from 's'
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_new_segment_without_save_flashes_no_dialog(self):
        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController(journal=None)  # not recording
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("n")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, SaveDialog)
            assert ctrl.new_seg is None
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_save_dialog_labels_when_recording(self):
        """With --save active, 's' opens a 'Label recording' dialog (metadata only)."""
        from textual.widgets import Button, Label

        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController(journal=object())  # recording
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            title = _plain(app.screen.query_one("#save-title", Label).render())
            assert "Label recording" in title
            btn = app.screen.query_one("#save", Button)
            assert "label" in str(btn.label).lower()
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_save_dialog_saves_when_not_recording(self):
        """Without --save, 's' opens a 'Save captures now' dialog (writes a file)."""
        from textual.widgets import Button, Label

        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController(journal=None)  # not recording
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            title = _plain(app.screen.query_one("#save-title", Label).render())
            assert "Save captures now" in title
            assert str(app.screen.query_one("#save", Button).label) == "Save"
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_rec_indicator_shown_when_recording(self):
        ctrl = FakeController(journal=object())
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            plain = _plain(app.query_one("#status").render())
            assert "REC" in plain
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_no_rec_indicator_when_not_recording(self):
        ctrl = FakeController(journal=None)
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            plain = _plain(app.query_one("#status").render())
            assert "REC" not in plain
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_help_modal_opens_with_question_mark(self):
        from canair.tui.help import HelpModal

        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            # A known monitor binding is listed.
            from textual.widgets import Static

            text = "\n".join(_plain(s.render()) for s in app.screen.query(Static))
            assert "save" in text and "byte ruler" in text
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, HelpModal)
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_status_line_advertises_help(self):
        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            assert "? help" in _plain(app.query_one("#status").render())
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_status_line_shows_frame_counter(self):
        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.2)
            status = _plain(app.query_one("#status").render())
            assert "captured" in status and "uniq" in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_header_shows_title_states_and_note(self):
        ctrl = FakeController()
        ctrl.session_label = "test drive"
        ctrl.session_states = ["ready", "parked"]
        ctrl.session_notes = "heater on high"
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            header = _plain(app.query_one("#header").render())
            assert "test drive" in header
            assert "ready, parked" in header
            assert "heater on high" in header
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_header_falls_back_to_query_label(self):
        # No explicit label -> the polled query is the title; no note line.
        ctrl = FakeController(query_label="BCM VCU:2101")
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            header = _plain(app.query_one("#header").render())
            assert "BCM VCU:2101" in header
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_polling_rate_adjustable_live(self):
        ctrl = FakeController()
        ctrl.interval = 4.0
        app = MonitorApp(ctrl)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("minus")  # slower
            await pilot.pause(0.05)
            assert ctrl.interval == pytest.approx(6.0)
            await pilot.press("equals_sign")  # faster
            await pilot.pause(0.05)
            assert ctrl.interval == pytest.approx(4.0)
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_polling_rate_clamped(self):
        ctrl = FakeController()
        ctrl.interval = 0.1
        app = MonitorApp(ctrl)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            for _ in range(5):
                await pilot.press("equals_sign")  # faster, but clamped
                await pilot.pause(0.02)
            assert ctrl.interval >= 0.1
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_view_toggle_cycles_modes(self):
        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            assert ctrl.view_mode == "full"
            await pilot.press("V")  # full -> ecus (wraps)
            await pilot.pause(0.05)
            assert ctrl.view_mode == "ecus"
            status = _plain(app.query_one("#status").render())
            assert "ecus" in status
            await pilot.press("V")  # ecus -> ranges
            await pilot.pause(0.05)
            assert ctrl.view_mode == "ranges"
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_session_info_modal_opens_and_closes(self):
        from canair.tui.apps.monitor.app import SessionInfoModal

        ctrl = FakeController(journal=object())
        ctrl.session_label = "test drive"
        app = MonitorApp(ctrl)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.15)
            await pilot.press("i")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SessionInfoModal)
            from textual.widgets import Static

            text = "\n".join(_plain(s.render()) for s in app.screen.query(Static))
            assert "test drive" in text
            assert "retain mode" in text
            assert "Finished segments" in text
            # Where the write-ahead journal is streaming to (what --recover reads).
            assert "journal (WAL)" in text
            assert "20260806T101010-42.jsonl" in text
            assert "captures uds --recover" in text
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, SessionInfoModal)
            await pilot.press("q")


class TestSaveDialogStateHelpers:
    def test_split_state_tokens(self):
        from canair.tui.apps.monitor.dialogs import _split_state_tokens

        assert _split_state_tokens("") == ([], "")
        assert _split_state_tokens("ready") == ([], "ready")
        assert _split_state_tokens("ready, pa") == (["ready"], "pa")
        assert _split_state_tokens("ready, parked, ") == (["ready", "parked"], "")

    def test_complete_state_token(self):
        from canair.tui.apps.monitor.dialogs import _complete_state_token

        assert _complete_state_token("", "ready") == "ready, "
        assert _complete_state_token("re", "ready") == "ready, "
        assert _complete_state_token("ready, pa", "parked") == "ready, parked, "

    def test_unknown_state_tokens(self):
        from canair.tui.apps.monitor.dialogs import _unknown_state_tokens

        vocab = {"ready", "parked", "charging"}
        # A completed token outside the vocabulary is flagged.
        assert _unknown_state_tokens("bogus, ready", vocab) == ["bogus"]
        # The active token is not flagged while it's still a prefix of a state.
        assert _unknown_state_tokens("ready, pa", vocab) == []
        # ...but is flagged once it can't complete to any known state.
        assert _unknown_state_tokens("ready, zzz", vocab) == ["zzz"]
        # All known → no warning.
        assert _unknown_state_tokens("ready, parked", vocab) == []


class TestSaveDialogStateUI:
    @pytest.mark.asyncio
    async def test_dropdown_filters_and_completes(self):
        from textual.widgets import Input, OptionList

        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            state = app.screen.query_one("#save-state", Input)
            options = app.screen.query_one("#save-state-options", OptionList)
            # Typing a prefix filters the dropdown to matching states.
            state.value = "cha"
            await pilot.pause(0.05)
            assert "visible" in options.classes
            assert options.option_count == 1
            assert options.get_option_at_index(0).id == "charging"
            # Selecting the highlighted option completes the token.
            options.focus()
            options.highlighted = 0
            await pilot.press("enter")
            await pilot.pause(0.05)
            assert state.value == "charging, "
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_live_warning_for_unknown_state(self):
        from textual.widgets import Input, Label

        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            state = app.screen.query_one("#save-state", Input)
            warning = app.screen.query_one("#save-state-warning", Label)
            state.value = "bogus, ready"
            await pilot.pause(0.05)
            assert "visible" in warning.classes
            assert "bogus" in _plain(warning.render())
            # Clearing the unknown token hides the warning.
            state.value = "ready"
            await pilot.pause(0.05)
            assert "visible" not in warning.classes
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_state_trailing_comma_stripped_on_save(self):
        from textual.widgets import Input

        from canair.tui.apps.monitor.app import SaveDialog

        ctrl = FakeController()
        app = MonitorApp(ctrl)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert isinstance(app.screen, SaveDialog)
            app.screen.query_one("#save-state", Input).value = "ready, parked, "
            # Submit from the label field so enter doesn't hop into the dropdown.
            app.screen.query_one("#save-label", Input).focus()
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert ctrl.saved is not None
            assert ctrl.saved[1] == "ready, parked"
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
    async def test_verify_and_ship_toggles(self):
        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down")  # select SOC
            await pilot.press("v")
            await pilot.pause(0.05)
            assert ed.verified_toggles == 1
            await pilot.press("x")
            await pilot.pause(0.05)
            assert ed.ship_toggles == 1
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
            assert "Select a signal" in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_edit_dialog_pauses_and_applies(self):
        from canair.tui.apps.monitor.app import EditParamDialog

        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down")  # select SOC
            await pilot.press("e")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EditParamDialog)
            assert app.paused is True  # polling auto-paused during edit
            # The checkbox state reads by shape, not colour (see tui.widgets).
            from canair.tui import widgets
            from canair.tui.widgets import CheckBox

            verified = app.screen.query_one("#edit-verified", CheckBox).render().plain
            ship = app.screen.query_one("#edit-ship", CheckBox).render().plain
            assert widgets._UNCHECKED in verified
            assert widgets._CHECKED in ship
            # Change the expression and save.
            expr = app.screen.query_one("#edit-expr")
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
    async def test_edit_dialog_fits_a_small_terminal(self):
        """Every field, both checkboxes and Save/Cancel are on screen at 80x24, each
        widget is tall enough to show its text, and the value is not pre-selected."""
        from textual.widgets import Button, Input

        from canair.tui.apps.monitor.app import EditParamDialog
        from canair.tui.widgets import CheckBox

        app = MonitorApp(FakeController(editor=FakeEditor()))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down")
            await pilot.press("e")
            await pilot.pause(0.1)
            dialog = app.screen
            assert isinstance(dialog, EditParamDialog)
            box = dialog.query_one("#edit-box").region
            for widget in [*dialog.query(Input), *dialog.query(CheckBox), *dialog.query(Button)]:
                region = widget.region
                assert widget.content_region.height >= 1, widget
                assert box.contains_region(region), widget
            expr = dialog.query_one("#edit-expr", Input)
            assert expr.has_focus
            assert expr.selected_text == ""
            await pilot.press("escape")
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_edit_without_selection_flashes(self):
        from canair.tui.apps.monitor.app import EditParamDialog

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
        from canair.tui.apps.monitor.app import EditParamDialog

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

    @pytest.mark.asyncio
    async def test_escape_clears_selection(self):
        ed = FakeEditor()
        app = MonitorApp(FakeController(editor=ed))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("down")  # select SOC
            await pilot.pause(0.05)
            assert ed.selected is not None
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert ed.selected is None
            status = _plain(app.query_one("#status").render())
            assert "Selection cleared" in status
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_event_log_modal_opens_and_closes(self):
        from canair.tui.apps.monitor.app import EventLogModal

        app = MonitorApp(FakeController(editor=FakeEditor()))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("E")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EventLogModal)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, EventLogModal)
            await pilot.press("q")
