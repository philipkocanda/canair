"""The monitor's panes: each focusable region of the screen owns the keys that act on it.

Textual resolves a key from the focused widget outwards: the widget's own bindings, then its
ancestors', then the screen's and the app's. So a binding declared on a pane means "this key, in
this pane", and the app keeps only the keys that mean the same wherever focus is (quit, help,
save, pause, the poll rate, the pickers). The help overlay lists the app's keys, then one
section per pane (:data:`PANE_SECTIONS` on the app).

* :class:`ValuesPane` — the live values: the signal cursor and the keys that edit the selected
  signal. Its arrow keys replace the scroll container's own, which is why they need no
  ``priority``: a focused pane's own binding wins over the bindings it inherits.

The actions stay on the app (``app.select(1)``): they repaint the body, open dialogs and flash
results, which is the app's business, and a pane only says which of them its keys reach.
"""

from __future__ import annotations

from typing import ClassVar

from textual.binding import BindingType
from textual.containers import VerticalScroll

from canair.tui.keys import bind


class ValuesPane(VerticalScroll):
    """The scrollable live values, with the signal cursor and the signal edit keys."""

    HELP_SECTION: ClassVar[str] = "Values"

    BINDINGS: ClassVar[list[BindingType]] = [
        # Down/up and j/k are the same movement: the signal cursor, which falls back to
        # plain scrolling when nothing is selectable.
        *bind("move_down", "app.select(1)", desc="select down", show=False),
        *bind("move_up", "app.select(-1)", desc="select up", show=False),
        *bind("axis_start", "app.to_top", desc="top", show=False),
        *bind("axis_end", "app.to_bottom", desc="bottom", show=False),
        *bind("back", "app.clear_selection", desc="deselect", show=False),
        *bind("edit", "app.edit"),
        *bind("verify", "app.verify"),
        *bind("exclude", "app.toggle_ship", desc="toggle ship"),
        *bind("filter_cycle", "app.cycle_filter"),
    ]
