"""Reusable Textual modal screens shared by canair's TUIs.

Small, self-contained dialogs that more than one full-screen app needs. Each is
a :class:`~textual.screen.ModalScreen` carrying its own CSS and ``escape``
binding, dismissed with its result — opened by the host app with
``push_screen(Modal(...), callback)``. A cancelled dialog dismisses ``None``
(:class:`ConfirmModal` answers a ``bool``).

Every dialog has one anatomy: a bold title, the body, the buttons on the right with the
primary one first and Cancel last, and a hint line at the bottom built from the key roles.
:class:`KitModal` is the base that gives every dialog ``?``: Textual cuts a modal off from
the app's own bindings, so help has to be bound on the dialog. In a text field ``?`` is
text, so a dialog with an input opens help from anywhere outside the field.

App-specific modals (a save dialog with domain validation) stay with their app; only
genuinely generic dialogs belong here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList
from textual.widgets.option_list import Option

from canair.tui.help import HelpModal, LegendRow, binding_rows
from canair.tui.keys import CANCEL_HINT, bind, hint
from canair.tui.styles import dialog_css


class KitModal[T](ModalScreen[T]):
    """A modal that opens its own key help on ``?``."""

    #: What the help sheet over this dialog is called.
    HELP_TITLE: ClassVar[str] = "Keyboard shortcuts"
    #: ``(keys, description)`` rows for keys the widgets handle and no binding declares.
    HELP_EXTRA_ROWS: ClassVar[tuple[tuple[str, str], ...]] = ()
    #: ``(sample, style, meaning)`` rows when the dialog uses colour to mean something.
    HELP_LEGEND: ClassVar[tuple[LegendRow, ...]] = ()

    BINDINGS: ClassVar[list[BindingType]] = [*bind("help", "help", show=False)]

    def action_help(self) -> None:
        if isinstance(self.app.screen, HelpModal):
            return
        bindings = [b for group in self._bindings.key_to_bindings.values() for b in group]
        rows = [*binding_rows(bindings), *self.HELP_EXTRA_ROWS]
        self.app.push_screen(HelpModal(self.HELP_TITLE, rows, self.HELP_LEGEND))


class TextPromptModal(KitModal["str | None"]):
    """A single-line text prompt (a note, a name, a numeric setting).

    Dismisses with the stripped input value, or ``None`` when cancelled. With a ``validate``
    function, a value it rejects stays in the dialog with its message shown there.
    """

    CSS = (
        dialog_css("TextPromptModal", "prompt", width=70)
        + """
    #prompt-error { color: $error; height: auto; display: none; }
    #prompt-error.visible { display: block; }
    """
    )

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("back", "cancel", desc="cancel"),
        # ctrl+s confirms from anywhere, including once focus has tabbed onto a
        # Button, where enter presses that button instead of submitting the form.
        # A printable key could not serve here — the Input would swallow it.
        *bind("apply", "submit", priority=True),
    ]

    def __init__(
        self,
        title: str,
        placeholder: str = "",
        value: str = "",
        *,
        validate: Callable[[str], str | None] | None = None,
    ):
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._value = value
        self._validate = validate

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-box"):
            yield Label(self._title, id="prompt-title")
            yield Input(value=self._value, placeholder=self._placeholder, id="prompt-input")
            yield Label("", id="prompt-error")
            with Horizontal(id="prompt-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")
            yield Label(hint("accept", "apply", CANCEL_HINT), id="prompt-hint")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._show_error("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def action_submit(self) -> None:
        self._submit()

    def _show_error(self, message: str) -> None:
        error = self.query_one("#prompt-error", Label)
        error.update(message)
        error.set_class(bool(message), "visible")

    def _submit(self) -> None:
        value = self.query_one("#prompt-input", Input).value.strip()
        problem = self._validate(value) if self._validate else None
        if problem:
            self._show_error(problem)
            self.query_one("#prompt-input", Input).focus()
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(KitModal["bool"]):
    """A yes/no confirmation for a destructive action (defaults to *no*)."""

    CSS = (
        dialog_css("ConfirmModal", "confirm", width=64, border="$error")
        + "#confirm-detail { margin-bottom: 1; }"
    )

    #: Only the confirm side is styled as an error; ``escape``/``n`` are the safe
    #: exits and focus starts on Cancel, so a stray ``enter`` cannot destroy data.
    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("back", "cancel", desc="cancel"),
        *bind("confirm_no", "cancel"),
        *bind("confirm_yes", "confirm"),
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
                yield Label(self._detail, id="confirm-detail", markup=False)
            with Horizontal(id="confirm-buttons"):
                yield Button(self._confirm_label, variant="error", id="ok")
                yield Button("Cancel", id="cancel")
            yield Label(hint("confirm_yes", "confirm_no", CANCEL_HINT), id="confirm-hint")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ListPickerModal[T](KitModal[T | None]):
    """A filter box over a list: type to narrow, ``enter`` moves to the list, ``enter`` picks.

    The one filter-and-list picker. A subclass supplies :meth:`options` (the rows for the
    current filter) and :meth:`chosen` (what a picked row does), and may retitle itself or
    change its hint and footer as it moves between steps. Focus starts in the filter, so
    typing narrows at once.
    """

    PLACEHOLDER: ClassVar[str] = "type to filter…"
    BOX_WIDTH: ClassVar[int] = 60

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("back", "cancel", desc="cancel"),
        *bind("filter", "focus_filter", show=False),
    ]

    def __init__(self, title: str = ""):
        super().__init__()
        self._title = title

    @classmethod
    def picker_css(cls, screen: str, width: int, *, rows: int = 20) -> str:
        return (
            dialog_css(screen, "picker", width=width)
            + f"""
        #picker-list {{ height: auto; max-height: {rows}; }}
        #picker-filter {{ margin-bottom: 1; }}
        #picker-footer {{ color: $text-muted; margin-top: 1; }}
        """
        )

    def options(self, needle: str) -> list[Option]:
        raise NotImplementedError

    def chosen(self, option: Option) -> None:
        raise NotImplementedError

    def hint_text(self) -> str:
        return hint("accept", "filter", CANCEL_HINT)

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Label(self._title, id="picker-title")
            yield Input(placeholder=self.PLACEHOLDER, id="picker-filter")
            yield OptionList(id="picker-list")
            yield Label("", id="picker-footer")
            yield Label(self.hint_text(), id="picker-hint")

    def start(self) -> None:
        """Fill the list for the first time; a multi-step picker overrides this."""
        self.refill()
        self.query_one("#picker-filter", Input).focus()

    def on_mount(self) -> None:
        self.start()

    def needle(self) -> str:
        return self.query_one("#picker-filter", Input).value.strip().lower()

    def refill(self) -> None:
        listing = self.query_one("#picker-list", OptionList)
        listing.clear_options()
        listing.add_options(self.options(self.needle()))
        listing.highlighted = next(
            (n for n in range(listing.option_count) if not listing.get_option_at_index(n).disabled),
            None,
        )

    def set_title(self, title: str) -> None:
        self.query_one("#picker-title", Label).update(title)

    def set_hint(self, text: str) -> None:
        self.query_one("#picker-hint", Label).update(text)

    def set_footer(self, text: str) -> None:
        self.query_one("#picker-footer", Label).update(text)

    def clear_filter(self) -> None:
        field = self.query_one("#picker-filter", Input)
        field.value = ""
        field.focus()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self.refill()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        listing = self.query_one("#picker-list", OptionList)
        if listing.option_count:
            listing.focus()
            if listing.highlighted is None:
                listing.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.chosen(event.option)

    def action_focus_filter(self) -> None:
        self.query_one("#picker-filter", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)
