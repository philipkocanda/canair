"""Reusable Textual modal screens shared by canair's TUIs.

Small, self-contained dialogs that more than one full-screen app needs. Each is
a :class:`~textual.screen.ModalScreen` carrying its own CSS and ``escape``
binding, dismissed with its result — opened by the host app with
``push_screen(Modal(...), callback)``.

App-specific modals (a PID picker built from one app's data, a save dialog with
domain validation) stay with their app; only genuinely generic dialogs belong
here.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class TextPromptModal(ModalScreen["str | None"]):
    """A single-line text prompt (a note, a name, a numeric setting).

    Dismisses with the stripped input value, or ``None`` when cancelled.
    """

    CSS = """
    TextPromptModal { align: center middle; background: $background 60%; }
    #prompt-box { width: 70; height: auto; padding: 1 2;
                  border: round $accent; background: $surface; }
    #prompt-title { text-style: bold; margin-bottom: 1; }
    #prompt-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    #prompt-buttons Button { margin-left: 2; }
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, title: str, placeholder: str = "", value: str = ""):
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-box"):
            yield Label(self._title, id="prompt-title")
            yield Input(value=self._value, placeholder=self._placeholder, id="prompt-input")
            with Horizontal(id="prompt-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        self.dismiss(self.query_one("#prompt-input", Input).value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen["bool"]):
    """A yes/no confirmation for a destructive action (defaults to *no*)."""

    CSS = """
    ConfirmModal { align: center middle; background: $background 60%; }
    #confirm-box { width: 64; height: auto; padding: 1 2;
                   border: round $error; background: $surface; }
    #confirm-title { text-style: bold; margin-bottom: 1; }
    #confirm-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    #confirm-buttons Button { margin-left: 2; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "cancel"),
        Binding("n", "cancel", "no"),
        Binding("y", "confirm", "yes"),
    ]

    def __init__(self, title: str, detail: str = "", confirm_label: str = "Delete"):
        super().__init__()
        self._title = title
        self._detail = detail
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._title, id="confirm-title")
            if self._detail:
                yield Label(self._detail, id="confirm-detail")
            with Horizontal(id="confirm-buttons"):
                yield Button(self._confirm_label, variant="error", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
