"""The monitor's two form dialogs: label a recording, edit a signal's definition.

Both are modals that dismiss with their fields on a valid submit, or ``None`` on cancel.
Their read-only overlays and the session-type picker are in
:mod:`canair.tui.apps.monitor.overlays`.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from canair.tui.keys import CANCEL_HINT, Token, arrows_hint, bind, hint
from canair.tui.modals import KitModal
from canair.tui.styles import dialog_css
from canair.tui.widgets import CheckBox


def _split_state_tokens(value: str) -> tuple[list[str], str]:
    """Split a comma-separated state string into (completed tokens, active token).

    The *active token* is the trailing fragment after the last comma — the one
    being typed and thus the one autocomplete filters against. Completed tokens
    are everything before it, stripped of surrounding whitespace. A trailing
    comma yields an empty active token (nothing being typed yet).
    """
    parts = value.split(",")
    active = parts[-1].strip()
    completed = [p.strip() for p in parts[:-1] if p.strip()]
    return completed, active


def _complete_state_token(value: str, choice: str) -> str:
    """Return ``value`` with its active (last) token replaced by ``choice``.

    Preserves the already-typed tokens and appends a ``", "`` so the caret is
    ready for the next state — e.g. ``("ready, pa", "parked") -> "ready, parked, "``.
    """
    completed, _active = _split_state_tokens(value)
    return ", ".join([*completed, choice]) + ", "


def _unknown_state_tokens(value: str, vocabulary: set[str]) -> list[str]:
    """Tokens in ``value`` (comma-separated) that aren't in ``vocabulary``.

    Case-insensitive; the trailing token still being typed is ignored while it
    remains a prefix of some known state (so the warning doesn't flicker mid-word).
    """
    completed, active = _split_state_tokens(value)
    unknown = [t for t in completed if t.lower() not in vocabulary]
    if active and active.lower() not in vocabulary:
        if not any(name.startswith(active.lower()) for name in vocabulary):
            unknown.append(active)
    return unknown


class SaveDialog(KitModal["tuple[str, str, str] | None"]):
    """Modal prompt for capture metadata (label / state / notes).

    The state field is a free-text ``Input`` (states are comma-separated and may
    be composite, e.g. ``ready, parked``) augmented with a filtering autocomplete
    dropdown of the profile's known states and a live warning for any token
    outside the vocabulary. The label is optional: an empty one uses the suggested
    label, which the placeholder names. Dismisses with ``(label, state, notes)`` on
    save, or ``None`` on cancel.
    """

    CSS = (
        dialog_css("SaveDialog", "save", width=84)
        + """
    #save-box { padding: 1 3; }
    #save-scroll { height: auto; }
    #save-footer { dock: bottom; height: auto; }
    #save-caption { color: $text-muted; margin-bottom: 1; height: auto; }
    #save-box Input { margin-bottom: 0; }
    #save-state-hint { color: $text-muted; height: 1; margin-bottom: 0; }
    #save-state-status { color: $text-muted; height: auto; margin-bottom: 0; }
    #save-state-warning { color: $warning; height: auto; margin-bottom: 1; display: none; }
    #save-state-warning.visible { display: block; }
    #save-state-options {
        height: auto; max-height: 8; margin-bottom: 1;
        border: round $panel; background: $panel; display: none;
    }
    #save-state-options.visible { display: block; }
    """
    )

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("back", "cancel", desc="cancel"),
        # The form has three Inputs, a dropdown and two Buttons, so what `enter`
        # does depends on what is focused. ctrl+s applies from anywhere.
        *bind("apply", "submit", priority=True),
    ]

    def __init__(
        self,
        suggested_label: str,
        suggested_state: str = "",
        state_options: list[tuple[str, str]] | None = None,
        title: str = "Save captures to profile",
        caption: str = "",
        save_button: str = "Save",
    ):
        super().__init__()
        self._suggested = suggested_label
        self._suggested_state = suggested_state
        self._title = title
        self._caption = caption
        self._save_button = save_button
        self._state_options = state_options or []
        self._vocabulary = {name for name, _desc in self._state_options}

    def compose(self) -> ComposeResult:
        label_hint = f'Label (empty uses "{self._suggested}")' if self._suggested else "Label"
        with Vertical(id="save-box"):
            yield Label(self._title, id="save-title")
            with VerticalScroll(id="save-scroll"):
                if self._caption:
                    yield Static(self._caption, id="save-caption", markup=True)
                yield Input(value=self._suggested, placeholder=label_hint, id="save-label")
                yield Input(
                    value=self._suggested_state,
                    placeholder="States (comma-separated, e.g. ready, parked)",
                    id="save-state",
                )
                yield Label(
                    hint(arrows_hint("select"), "accept", Token("type to filter")),
                    id="save-state-hint",
                )
                yield OptionList(id="save-state-options")
                yield Label("", id="save-state-status")
                yield Label("", id="save-state-warning")
                yield Input(placeholder="Notes (optional)", id="save-notes")
            # Docked, so the buttons and the hint stay in view when 24 rows make the form scroll.
            with Vertical(id="save-footer"):
                with Horizontal(id="save-buttons"):
                    yield Button(self._save_button, variant="primary", id="save")
                    yield Button("Cancel", id="cancel")
                yield Label(hint("apply", CANCEL_HINT), id="save-hint")

    def on_mount(self) -> None:
        self.query_one("#save-label", Input).focus()
        self._refresh_state_ui(self._suggested_state)

    def _option_for(self, name: str, desc: str) -> Option:
        prompt = name if not desc else f"{name} — {desc}"
        return Option(prompt, id=name)

    def _refresh_state_ui(self, value: str) -> None:
        """Repopulate the dropdown for the active token and update the warning."""
        _completed, active = _split_state_tokens(value)
        needle = active.lower()
        matches = [
            (name, desc)
            for name, desc in self._state_options
            if needle in name or needle in desc.lower()
        ]

        options = self.query_one("#save-state-options", OptionList)
        options.clear_options()
        for name, desc in matches:
            options.add_option(self._option_for(name, desc))
        options.set_class(bool(matches), "visible")

        warning = self.query_one("#save-state-warning", Label)
        unknown = _unknown_state_tokens(value, self._vocabulary)
        if unknown:
            warning.update("unknown state(s): " + ", ".join(unknown))
            warning.set_class(True, "visible")
        else:
            warning.update("")
            warning.set_class(False, "visible")

        # The state field is free-text, not a checkbox — nothing is ever
        # "selected". Make that explicit: when empty, say the state will be
        # auto-detected from data on save (the span-aware back-fill); when it
        # still holds the auto-suggested value, mark it as auto-detected so the
        # user knows it was pre-filled (and can edit/clear it).
        status = self.query_one("#save-state-status", Label)
        stripped = value.strip().rstrip(",").strip()
        if not stripped:
            status.update("no state set — will auto-detect from data on save")
        elif self._suggested_state and stripped == self._suggested_state.strip():
            status.update(f"state auto-detected: {stripped} (edit or clear)")
        else:
            status.update("")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "save-state":
            self._refresh_state_ui(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        state_input = self.query_one("#save-state", Input)
        state_input.value = _complete_state_token(state_input.value, event.option.id or "")
        state_input.cursor_position = len(state_input.value)
        state_input.focus()
        self._refresh_state_ui(state_input.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # In the state field, enter first drops focus into the dropdown so a
        # match can be picked; only submit from the other fields.
        if event.input.id == "save-state":
            options = self.query_one("#save-state-options", OptionList)
            if "visible" in options.classes and options.option_count:
                options.focus()
                if options.highlighted is None:
                    options.highlighted = 0
                return
        self._submit()

    def _submit(self) -> None:
        label = self.query_one("#save-label", Input).value.strip() or self._suggested
        state = self.query_one("#save-state", Input).value.strip().rstrip(",").strip()
        notes = self.query_one("#save-notes", Input).value.strip()
        self.dismiss((label, state, notes))

    def action_submit(self) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditParamDialog(KitModal["dict | None"]):
    """Modal editor for a single signal's definition.

    Prefilled from the selected signal's current fields; dismisses with a
    ``{expression, unit, min, max, notes, verified, ship}`` dict on save, or
    ``None`` on cancel. The expression is required: an empty one is refused inside
    the dialog. Writing is done by the caller (via the monitor editor).
    """

    # One line per field (compact widgets, label beside its input) so the whole
    # form fits a 24-row terminal. The footer is docked, so Save/Cancel and the
    # key hint stay visible when a small terminal makes the form scroll. The
    # monitor runs the ansi-dark theme, where $surface/$panel/$boost are all
    # transparent, so focus is shown on the label rather than a field background.
    CSS = (
        dialog_css("EditParamDialog", "edit", width=100)
        + """
    #edit-box { width: 90%; max-width: 100; }
    #edit-scroll { height: auto; }
    .edit-row { height: 1; margin-bottom: 1; }
    .edit-label { width: 12; color: $text-muted; }
    .edit-row:focus-within .edit-label { color: $accent; text-style: bold; }
    .edit-row Input { width: 1fr; }
    #edit-flags { height: 1; }
    #edit-flags CheckBox { margin-right: 4; }
    #edit-footer { dock: bottom; height: auto; margin-top: 1; }
    #edit-error { color: $error; height: auto; display: none; }
    #edit-error.visible { display: block; }
    #edit-buttons { height: 1; margin-top: 0; }
    #edit-buttons Button { min-width: 10; }
    """
    )

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("back", "cancel", desc="cancel"),
        # Enter submits only from an Input; the two Checkboxes and the Buttons
        # consume it themselves, so without this the dialog had no confirm key
        # once focus moved past the text fields.
        *bind("apply", "submit", priority=True),
    ]

    def __init__(self, target: dict):
        super().__init__()
        self._target = target

    _FIELDS = (
        ("expression", "Expression", "edit-expr"),
        ("unit", "Unit", "edit-unit"),
        ("min", "Min", "edit-min"),
        ("max", "Max", "edit-max"),
        ("notes", "Notes", "edit-notes"),
    )

    def compose(self) -> ComposeResult:
        t = self._target
        title = f"Edit {t.get('ecu', '')} {t.get('pid', '')} {t.get('name', '')}"
        with Vertical(id="edit-box"):
            yield Label(title.strip(), id="edit-title")
            with VerticalScroll(id="edit-scroll"):
                for key, label, field_id in self._FIELDS:
                    with Horizontal(classes="edit-row"):
                        yield Label(label, classes="edit-label")
                        # The cursor starts at the end of the current value: a
                        # select-all on focus made the first keystroke replace it.
                        yield Input(
                            value=t.get(key, ""),
                            placeholder="Required" if key == "expression" else "Not set",
                            id=field_id,
                            compact=True,
                            select_on_focus=False,
                        )
                with Horizontal(id="edit-flags"):
                    yield CheckBox(
                        "Verified", value=bool(t.get("verified")), id="edit-verified", compact=True
                    )
                    yield CheckBox(
                        "Ship", value=bool(t.get("ship", True)), id="edit-ship", compact=True
                    )
            with Vertical(id="edit-footer"):
                yield Label("", id="edit-error")
                with Horizontal(id="edit-buttons"):
                    yield Button("Save", variant="primary", id="edit-save", compact=True)
                    yield Button("Cancel", id="edit-cancel", compact=True)
                yield Label(hint("apply", CANCEL_HINT), id="edit-hint")

    def on_mount(self) -> None:
        self.query_one("#edit-expr", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-save":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._show_error("")

    def _show_error(self, message: str) -> None:
        error = self.query_one("#edit-error", Label)
        error.update(message)
        error.set_class(bool(message), "visible")

    def _submit(self) -> None:
        fields = {
            key: self.query_one(f"#{fid}", Input).value.strip() for key, _label, fid in self._FIELDS
        }
        if not fields["expression"]:
            self._show_error("The expression is required")
            self.query_one("#edit-expr", Input).focus()
            return
        self.dismiss(
            {
                **fields,
                "verified": self.query_one("#edit-verified", CheckBox).value,
                "ship": self.query_one("#edit-ship", CheckBox).value,
            }
        )

    def action_submit(self) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)
