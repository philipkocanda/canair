"""Shared Textual help modal for canair's live TUIs.

Every full-screen Textual app in canair (the monitor, the stepper, the plot explorer and
the ``sniff`` table) advertises its keybindings the same way: a ``?`` opens a modal
cheat-sheet built **from the app's own ``BINDINGS``**, so the list can never drift from
what the keys actually do. A modal dialog does the same from its own bindings
(:class:`canair.tui.modals.KitModal`).

Mix :class:`HelpMixin` into an ``App`` and bind the ``help`` role with
``bind("help", "help")`` in its ``BINDINGS``; the modal is populated automatically.
Keys the app does *not* declare because the framework already handles them
(``Home``/``End``/``PgUp``/``PgDn`` on a focused scroll container) can be listed
via ``HELP_EXTRA_ROWS`` — otherwise they are real, working keys the cheat-sheet
would silently omit. An app whose screen uses colour to carry meaning sets
``HELP_LEGEND`` to draw a colour legend under the keys.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from canair.tui.keys import (
    CLOSE_HINT,
    DISMISS_DESCRIPTION,
    PAGE_HINT,
    arrows_hint,
    bind,
    display_key,
    hint,
)
from canair.tui.scroll import OVERLAY_SCROLL_BINDINGS, OverlayScroll
from canair.tui.styles import dialog_css

# One colour-legend row: a sample glyph, the Rich style it is drawn in, and what it means.
LegendRow = tuple[str, str, str]

# Keys the focused scroll container handles for free, so there is no ``Binding``
# to derive a cheat-sheet row from — but they are real, working keys in every
# canair TUI (each focuses a ``VerticalScroll``), so every app advertises them
# by default rather than each remembering to.
SCROLL_HELP_ROWS: tuple[tuple[str, str], ...] = (
    ("home/end", "top / bottom"),
    ("pgup/pgdn", "page up / down"),
)

#: The same keys in a view whose scroll container holds one frame of a longer sequence.
FRAME_SCROLL_HELP_ROWS: tuple[tuple[str, str], ...] = (
    ("home/end", "top / bottom of this frame"),
    ("pgup/pgdn", "page within this frame"),
)

# Keys we never advertise (framework/no-op or redundant with a shown alias).
_HIDDEN_KEYS = frozenset({"ctrl+c", "ctrl+q"})


def bindings_help_rows(app) -> list[tuple[str, str]]:
    """Build ``(keys, description)`` rows from an app's ``BINDINGS``."""
    return binding_rows(app.BINDINGS)


def binding_rows(bindings: Iterable[object]) -> list[tuple[str, str]]:
    """Build ``(keys, description)`` rows from bindings.

    Bindings without a description, and a small set of framework keys, are
    dropped. Bindings that share a description (aliases like ``j``/``down``) are
    collapsed onto one row with their keys joined, preserving first-seen order.
    """
    order: list[str] = []
    by_desc: dict[str, list[str]] = {}
    for b in bindings:
        key = getattr(b, "key", None) if isinstance(b, Binding) else None
        desc = getattr(b, "description", "") if isinstance(b, Binding) else ""
        if not key or not desc or key in _HIDDEN_KEYS:
            continue
        display = display_key(key)
        if desc not in by_desc:
            by_desc[desc] = []
            order.append(desc)
        if display not in by_desc[desc]:
            by_desc[desc].append(display)
    return [("/".join(by_desc[desc]), desc) for desc in order]


def legend_text(legend: Sequence[LegendRow], width: int) -> Text:
    """A ``Colours`` block: each sample drawn in its own style, right-aligned like the keys."""
    out = Text("Colours\n", style="bold")
    for i, (sample, style, meaning) in enumerate(legend):
        out.append(" " * max(0, width - len(sample)))
        out.append(sample, style=style)
        out.append(f"  {meaning}")
        if i < len(legend) - 1:
            out.append("\n")
    return out


class HelpModal(OverlayScroll, ModalScreen[None]):
    """A dismissable keyboard cheat-sheet overlay, with an optional colour legend."""

    SCROLL_ID: ClassVar[str] = "help-rows"

    CSS = (
        dialog_css("HelpModal", "help", width=72)
        + """
    #help-box { width: auto; max-width: 72; }
    #help-rows { height: auto; }
    #help-hint { dock: bottom; }
    #help-legend { margin-top: 1; }
    """
    )

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("back", "close", desc=DISMISS_DESCRIPTION),
        *bind("help", "close", desc=DISMISS_DESCRIPTION),
        *bind("quit", "close", desc=DISMISS_DESCRIPTION),
        *OVERLAY_SCROLL_BINDINGS,
    ]

    def __init__(
        self,
        title: str,
        rows: list[tuple[str, str]],
        legend: Sequence[LegendRow] = (),
    ):
        super().__init__()
        self._title = title
        self._rows = rows
        self._legend = tuple(legend)

    def compose(self) -> ComposeResult:
        width = max((len(k) for k, _ in self._rows), default=4)
        lines = "\n".join(f"{k:>{width}}  {desc}" for k, desc in self._rows)
        with Vertical(id="help-box"):
            yield Label(self._title, id="help-title")
            with VerticalScroll(id="help-rows"):
                yield Static(lines or "(no shortcuts)", markup=False)
                if self._legend:
                    yield Static(legend_text(self._legend, width), id="help-legend")
            yield Label(hint(arrows_hint("scroll"), PAGE_HINT, CLOSE_HINT), id="help-hint")

    def action_close(self) -> None:
        self.dismiss(None)


class HelpMixin:
    """Adds a ``?`` help modal driven by the app's own ``BINDINGS``.

    Mix in *before* ``App`` in the base list and add ``bind("help", "help")`` to the app's
    ``BINDINGS``.
    Set :attr:`HELP_EXTRA_ROWS` for keys the framework handles on the app's
    behalf, which therefore have no ``Binding`` to derive a row from.
    """

    HELP_TITLE: ClassVar[str] = "Keyboard shortcuts"
    #: ``(keys, description)`` rows appended after the derived ones. Defaults to
    #: the framework scroll keys; override to reword them for the app's axis.
    HELP_EXTRA_ROWS: ClassVar[tuple[tuple[str, str], ...]] = SCROLL_HELP_ROWS
    #: ``(sample, style, meaning)`` rows drawn as a colour legend under the keys.
    HELP_LEGEND: ClassVar[tuple[LegendRow, ...]] = ()

    def action_help(self) -> None:
        assert isinstance(self, App)
        # Don't stack a second modal (or open over an existing dialog).
        if len(self.screen_stack) > 1:
            return
        rows = bindings_help_rows(self) + list(self.HELP_EXTRA_ROWS)
        self.push_screen(HelpModal(self.HELP_TITLE, rows, self.HELP_LEGEND))
