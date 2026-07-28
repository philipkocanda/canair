"""Shared Textual help modal for canair's live TUIs.

Every full-screen Textual app in canair (the ``canair monitor`` monitor, the
``sniff`` table, and any future one) advertises its keybindings the same way: a
``?`` opens a modal cheat-sheet built **from the app's own ``BINDINGS``**, so the
list can never drift from what the keys actually do.

Mix :class:`HelpMixin` into an ``App`` and add a ``Binding("question_mark",
"help", "help")`` to its ``BINDINGS``; the modal is populated automatically.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

# Keys we never advertise (framework/no-op or redundant with a shown alias).
_HIDDEN_KEYS = frozenset({"ctrl+c"})


def bindings_help_rows(app) -> list[tuple[str, str]]:
    """Build ``(keys, description)`` rows from an app's ``BINDINGS``.

    Bindings without a description, and a small set of framework keys, are
    dropped. Bindings that share a description (aliases like ``j``/``down``) are
    collapsed onto one row with their keys joined, preserving first-seen order.
    """
    order: list[str] = []
    by_desc: dict[str, list[str]] = {}
    for b in app.BINDINGS:
        key = getattr(b, "key", None) if isinstance(b, Binding) else None
        desc = getattr(b, "description", "") if isinstance(b, Binding) else ""
        if not key or not desc or key in _HIDDEN_KEYS:
            continue
        display = _display_key(key)
        if desc not in by_desc:
            by_desc[desc] = []
            order.append(desc)
        if display not in by_desc[desc]:
            by_desc[desc].append(display)
    return [("/".join(by_desc[desc]), desc) for desc in order]


def _display_key(key: str) -> str:
    """Human-friendly rendering of a Textual key name (``question_mark`` -> ``?``)."""
    return {
        "question_mark": "?",
        "space": "space",
        "up": "↑",
        "down": "↓",
        "left": "←",
        "right": "→",
    }.get(key, key)


class HelpModal(ModalScreen[None]):
    """A dismissable keyboard cheat-sheet overlay."""

    CSS = """
    HelpModal { align: center middle; background: $background 60%; }
    #help-box {
        width: auto; max-width: 72; height: auto; max-height: 90%;
        padding: 1 2; border: round $accent; background: $surface;
    }
    #help-title { text-style: bold; margin-bottom: 1; }
    #help-rows { height: auto; }
    #help-hint { color: $text-muted; margin-top: 1; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "close"),
        Binding("question_mark", "close", "close"),
        Binding("q", "close", "close"),
    ]

    def __init__(self, title: str, rows: list[tuple[str, str]]):
        super().__init__()
        self._title = title
        self._rows = rows

    def compose(self) -> ComposeResult:
        width = max((len(k) for k, _ in self._rows), default=4)
        lines = "\n".join(f"{k:>{width}}  {desc}" for k, desc in self._rows)
        with Vertical(id="help-box"):
            yield Label(self._title, id="help-title")
            with VerticalScroll(id="help-rows"):
                yield Static(lines or "(no shortcuts)", markup=False)
            yield Label("? / esc / q to close", id="help-hint")

    def action_close(self) -> None:
        self.dismiss(None)


class HelpMixin:
    """Adds a ``?`` help modal driven by the app's own ``BINDINGS``.

    Mix in *before* ``App`` in the base list and add
    ``Binding("question_mark", "help", "help")`` to the app's ``BINDINGS``.
    """

    HELP_TITLE: ClassVar[str] = "Keyboard shortcuts"

    def action_help(self) -> None:
        assert isinstance(self, App)
        # Don't stack a second modal (or open over an existing dialog).
        if len(self.screen_stack) > 1:
            return
        self.push_screen(HelpModal(self.HELP_TITLE, bindings_help_rows(self)))
