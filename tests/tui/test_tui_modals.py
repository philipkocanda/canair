"""The shared dialogs: help on ``?``, inputs checked inside, one filter-and-list picker."""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Button, Input, Label, OptionList
from textual.widgets.option_list import Option

from canair.tui.help import HelpModal
from canair.tui.modals import ConfirmModal, ListPickerModal, TextPromptModal


class _Host(App):
    """An empty app that opens one dialog and records what it answered."""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.answer: object = "unanswered"

    def on_mount(self) -> None:
        self.push_screen(self.dialog, self._got)

    def _got(self, value) -> None:
        self.answer = value


def _text(label) -> str:
    return str(label.render())


class TestHelpOnQuestionMark:
    @pytest.mark.asyncio
    async def test_confirm_modal_opens_help(self):
        app = _Host(ConfirmModal("Delete this capture?"))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpModal)
            rows = dict(app.screen._rows)
            assert rows["y"] == "yes"
            assert rows["esc"] == "cancel"
            await pilot.press("question_mark")  # closes the help, not the dialog
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)

    @pytest.mark.asyncio
    async def test_text_prompt_types_the_question_mark(self):
        app = _Host(TextPromptModal("Note"))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, TextPromptModal)
            assert app.screen.query_one("#prompt-input", Input).value == "?"

    @pytest.mark.asyncio
    async def test_text_prompt_opens_help_once_focus_leaves_the_field(self):
        app = _Host(TextPromptModal("Note"))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("tab")  # onto a button
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpModal)
            assert dict(app.screen._rows)["ctrl+s"] == "apply"

    @pytest.mark.asyncio
    async def test_help_modal_closes_on_escape(self):
        app = _Host(ConfirmModal("Delete?"))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("question_mark", "escape")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)


class TestConfirmModal:
    @pytest.mark.asyncio
    async def test_starts_on_cancel_and_answers_a_bool(self):
        app = _Host(ConfirmModal("Delete?", detail="a capture"))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert app.focused is not None and app.focused.id == "cancel"
            await pilot.press("enter")  # a stray enter must not destroy anything
            await pilot.pause()
            assert app.answer is False

    @pytest.mark.asyncio
    async def test_yes_confirms(self):
        app = _Host(ConfirmModal("Delete?"))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert app.answer is True

    @pytest.mark.asyncio
    async def test_the_hint_is_built_from_the_roles(self):
        app = _Host(ConfirmModal("Delete?"))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert _text(app.screen.query_one("#confirm-hint", Label)) == (
                "y yes · n no · esc cancel"
            )


class TestTextPromptValidation:
    @pytest.mark.asyncio
    async def test_a_rejected_value_stays_in_the_dialog_with_its_reason(self):
        app = _Host(TextPromptModal("Join tolerance", validate=_positive))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            field = app.screen.query_one("#prompt-input", Input)
            field.value = "abc"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, TextPromptModal)
            assert _text(app.screen.query_one("#prompt-error", Label)) == "Not a number: abc"
            assert app.screen.query_one("#prompt-error", Label).has_class("visible")
            assert app.answer == "unanswered"

    @pytest.mark.asyncio
    async def test_the_reason_clears_when_the_value_changes(self):
        app = _Host(TextPromptModal("Join tolerance", validate=_positive))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.screen.query_one("#prompt-input", Input).value = "abc"
            await pilot.press("enter")
            await pilot.press("backspace")
            await pilot.pause()
            assert not app.screen.query_one("#prompt-error", Label).has_class("visible")

    @pytest.mark.asyncio
    async def test_a_valid_value_closes_the_dialog(self):
        app = _Host(TextPromptModal("Join tolerance", validate=_positive))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.screen.query_one("#prompt-input", Input).value = " 2.5 "
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert app.answer == "2.5"

    @pytest.mark.asyncio
    async def test_q_is_text_and_escape_cancels(self):
        app = _Host(TextPromptModal("Note"))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app.screen, TextPromptModal)
            assert app.screen.query_one("#prompt-input", Input).value == "q"
            await pilot.press("escape")
            await pilot.pause()
            assert app.answer is None

    @pytest.mark.asyncio
    async def test_without_a_validator_anything_goes(self):
        app = _Host(TextPromptModal("Note"))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.screen.query_one("#prompt-input", Input).value = ""
            await pilot.press("enter")
            await pilot.pause()
            assert app.answer == ""

    @pytest.mark.asyncio
    async def test_the_ok_button_validates_too(self):
        app = _Host(TextPromptModal("Join tolerance", validate=_positive))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.screen.query_one("#prompt-input", Input).value = "x"
            app.screen.query_one("#ok", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, TextPromptModal)


def _positive(value: str) -> str | None:
    try:
        float(value)
    except ValueError:
        return f"Not a number: {value}"
    return None


class _Pick(ListPickerModal[str]):
    CSS = ListPickerModal.picker_css("_Pick", 50)

    def __init__(self, names: list[str]):
        super().__init__("Pick one")
        self._names = names

    def options(self, needle: str) -> list[Option]:
        return [Option(n, id=n) for n in self._names if needle in n.lower()]

    def chosen(self, option: Option) -> None:
        self.dismiss(option.id)


class TestListPicker:
    @pytest.mark.asyncio
    async def test_starts_in_the_filter_and_enter_moves_to_the_list(self):
        app = _Host(_Pick(["alpha", "beta", "gamma"]))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert isinstance(app.focused, Input)
            assert app.focused.placeholder == "type to filter…"
            await pilot.press("b")
            await pilot.pause()
            assert app.screen.query_one("#picker-list", OptionList).option_count == 1
            await pilot.press("enter")
            assert isinstance(app.focused, OptionList)
            await pilot.press("enter")
            await pilot.pause()
            assert app.answer == "beta"

    @pytest.mark.asyncio
    async def test_escape_cancels_with_none(self):
        app = _Host(_Pick(["alpha"]))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.answer is None

    @pytest.mark.asyncio
    async def test_q_is_typed_into_the_filter(self):
        app = _Host(_Pick(["quiet", "loud"]))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert app.screen.query_one("#picker-list", OptionList).option_count == 1
            assert app.answer == "unanswered"

    @pytest.mark.asyncio
    async def test_hint_is_at_the_bottom_and_built_from_the_roles(self):
        app = _Host(_Pick(["alpha"]))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            hint = app.screen.query_one("#picker-hint", Label)
            assert _text(hint) == "enter accept · / filter · esc cancel"
            title = app.screen.query_one("#picker-title", Label)
            assert title.region.y < hint.region.y

    @pytest.mark.asyncio
    async def test_question_mark_is_text_in_the_filter_and_help_from_the_list(self):
        app = _Host(_Pick(["alpha"]))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("enter")  # to the list
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpModal)
