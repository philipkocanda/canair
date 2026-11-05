"""Textual live view for ``canair sniff`` — a refreshing per-ID CAN table.

The python-can Notifier feeds :class:`~canair.analysis.sniff_stats.SniffStats` from a
background thread; this app just renders a snapshot on an interval. Uses the
terminal's own colors/background (ansi-dark) like the monitor TUI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from canair.render.sniff import SNIFF_LEGEND, render_sniff_table
from canair.tui.help import HelpMixin
from canair.tui.keys import bind, key_hint
from canair.tui.scroll import APP_BASE_CSS
from canair.tui.status import (
    P_ESSENTIAL,
    P_HIGH,
    P_LOW,
    P_NORMAL,
    StatusBar,
    StatusItem,
    hint_item,
)

if TYPE_CHECKING:
    from canair.analysis.sniff_stats import SniffStats

import time


class SniffApp(HelpMixin, App):
    """Live per-ID sniff table."""

    HELP_TITLE = "canair sniff — keyboard shortcuts"
    HELP_LEGEND = SNIFF_LEGEND

    # canair uses no command-palette commands; disable Textual's ctrl+p palette.
    ENABLE_COMMAND_PALETTE = False

    CSS = APP_BASE_CSS

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "quit"),
        *bind("quit_force", "quit", show=False, priority=True),
        *bind("help", "help"),
        # The top level has nothing to back out of: escape stays inert rather than quitting.
        *bind("back", "stay", show=False),
        *bind("clear", "clear"),
        *bind("move_down", "scroll(1)", desc="scroll down", show=False),
        *bind("move_up", "scroll(-1)", desc="scroll up", show=False),
        *bind("axis_start", "to_top", desc="top", show=False),
        *bind("axis_end", "to_bottom", desc="bottom", show=False),
    ]

    def __init__(self, stats: SniffStats, host: str, duration: float | None = None):
        super().__init__()
        self.stats = stats
        self.host = host
        self.duration = duration
        self._start = time.monotonic()

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="scroll"):
            yield Static("", id="body", markup=False)
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        self.query_one("#scroll", VerticalScroll).focus()
        self.set_interval(0.25, self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        rows = self.stats.snapshot()
        try:
            self.query_one("#body", Static).update(render_sniff_table(rows))
            elapsed = time.monotonic() - self._start
            self.query_one("#status", StatusBar).set_lines(
                [
                    StatusItem(f"[dim]sniff[/] {self.host}", P_LOW),
                    StatusItem(f"{len(rows)} [dim]IDs[/]", P_HIGH),
                    StatusItem(f"{self.stats.total_frames} [dim]frames[/]", P_NORMAL),
                    StatusItem(f"{elapsed:.0f}[dim]s[/]", P_LOW),
                    hint_item(key_hint("clear"), P_NORMAL),
                    hint_item(key_hint("help"), P_ESSENTIAL),
                    hint_item(key_hint("quit"), P_ESSENTIAL),
                ]
            )
        except Exception:  # transient teardown query misses are harmless
            return
        if self.duration and (time.monotonic() - self._start) >= self.duration:
            self.exit()

    def action_stay(self) -> None:
        """Escape: the table has no level to back out of, so it does nothing."""

    def action_clear(self) -> None:
        self.stats.clear()
        self._refresh()

    def action_scroll(self, delta: int) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_relative(y=delta, animate=False)

    def action_to_top(self) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_home(animate=False)

    def action_to_bottom(self) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_end(animate=False)


def run_sniff_app(stats: SniffStats, host: str, duration: float | None = None) -> None:
    SniffApp(stats, host, duration=duration).run()
