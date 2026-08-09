"""Shared Textual help modal for canair's live TUIs.

Every full-screen Textual app in canair (the ``canair monitor`` monitor, the
``sniff`` table, and any future one) advertises its keybindings the same way: a
``?`` opens a modal cheat-sheet built **from the app's own ``BINDINGS``**, so the
list can never drift from what the keys actually do.

Mix :class:`HelpMixin` into an ``App`` and add a ``Binding("question_mark",
"help", "help")`` to its ``BINDINGS``; the modal is populated automatically.
Keys the app does *not* declare because the framework already handles them
(``Home``/``End``/``PgUp``/``PgDn`` on a focused scroll container) can be listed
via ``HELP_EXTRA_ROWS`` — otherwise they are real, working keys the cheat-sheet
would silently omit.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from .tui_keys import DISMISS_DESCRIPTION, bind

# Keys the focused scroll container handles for free, so there is no ``Binding``
# to derive a cheat-sheet row from — but they are real, working keys in every
# canair TUI (all four focus a ``VerticalScroll``), so every app advertises them
# by default rather than each remembering to.
SCROLL_HELP_ROWS: tuple[tuple[str, str], ...] = (
    ("home/end", "top / bottom"),
    ("pgup/pgdn", "page up / down"),
)

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
    """Human-friendly rendering of a Textual key name (``right_square_bracket`` -> ``]``).

    Delegates to Textual's own :func:`~textual.keys.format_key` — the function
    that renders keys in its Footer — rather than a hand-written table. A local
    table only covered the handful of names it happened to list, so every other
    symbolic binding leaked its raw identifier into the cheat-sheet (``]``, ``>``,
    ``:``, ``=``, ``,`` all showed as words). Deriving the label from Textual
    means a new symbolic binding is rendered correctly without touching this.
    """
    from textual.keys import format_key

    return format_key(key)


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
        *bind("back", "close", desc=DISMISS_DESCRIPTION),
        *bind("help", "close", desc=DISMISS_DESCRIPTION),
        *bind("quit", "close", desc=DISMISS_DESCRIPTION),
        *bind("move_down", "scroll_down", show=False),
        *bind("move_up", "scroll_up", show=False),
        *bind("axis_start", "to_top", show=False),
        *bind("axis_end", "to_bottom", show=False),
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
            yield Label("j/k g/G scroll · ? / esc / q to close", id="help-hint")

    def on_mount(self) -> None:
        # Focus the scroll container, or the cheat-sheet cannot be paged: a long
        # keymap overflows the 90%-height box and pgup/pgdn/arrows go nowhere
        # unless a scrollable widget has focus.
        self.query_one("#help-rows", VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def _rows_scroll(self) -> VerticalScroll:
        return self.query_one("#help-rows", VerticalScroll)

    def action_scroll_down(self) -> None:
        self._rows_scroll().scroll_relative(y=1, animate=False)

    def action_scroll_up(self) -> None:
        self._rows_scroll().scroll_relative(y=-1, animate=False)

    def action_to_top(self) -> None:
        self._rows_scroll().scroll_home(animate=False)

    def action_to_bottom(self) -> None:
        self._rows_scroll().scroll_end(animate=False)


class HelpMixin:
    """Adds a ``?`` help modal driven by the app's own ``BINDINGS``.

    Mix in *before* ``App`` in the base list and add
    ``Binding("question_mark", "help", "help")`` to the app's ``BINDINGS``.
    Set :attr:`HELP_EXTRA_ROWS` for keys the framework handles on the app's
    behalf, which therefore have no ``Binding`` to derive a row from.
    """

    HELP_TITLE: ClassVar[str] = "Keyboard shortcuts"
    #: ``(keys, description)`` rows appended after the derived ones. Defaults to
    #: the framework scroll keys; override to reword them for the app's axis.
    HELP_EXTRA_ROWS: ClassVar[tuple[tuple[str, str], ...]] = SCROLL_HELP_ROWS

    def action_help(self) -> None:
        assert isinstance(self, App)
        # Don't stack a second modal (or open over an existing dialog).
        if len(self.screen_stack) > 1:
            return
        rows = bindings_help_rows(self) + list(self.HELP_EXTRA_ROWS)
        self.push_screen(HelpModal(self.HELP_TITLE, rows))
