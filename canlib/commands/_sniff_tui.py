"""Textual live view for ``canair sniff`` — a refreshing per-ID CAN table.

The python-can Notifier feeds :class:`~canlib.commands.sniff.SniffStats` from a
background thread; this app just renders a snapshot on an interval. Uses the
terminal's own colors/background (ansi-dark) like the monitor TUI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from canlib.tui_help import HelpMixin
from canlib.tui_status import P_ESSENTIAL, P_HIGH, P_LOW, P_NORMAL, StatusBar, StatusItem

if TYPE_CHECKING:
    from canlib.commands.sniff import SniffStats

import time


class SniffApp(HelpMixin, App):
    """Live per-ID sniff table."""

    HELP_TITLE = "canair sniff — keyboard shortcuts"

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #scroll { height: 1fr; scrollbar-gutter: stable; scrollbar-size-vertical: 1; background: transparent; }
    #body { height: auto; padding: 0 1; background: transparent; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "quit", priority=True),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
        Binding("question_mark", "help", "help"),
        Binding("c", "clear", "clear"),
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
        from canlib.commands.sniff import render_sniff_table

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
                    StatusItem("[dim]c clear[/]", P_NORMAL),
                    StatusItem("[dim]? help[/]", P_ESSENTIAL),
                    StatusItem("[dim]q quit[/]", P_ESSENTIAL),
                ]
            )
        except Exception:  # transient teardown query misses are harmless
            return
        if self.duration and (time.monotonic() - self._start) >= self.duration:
            self.exit()

    def action_clear(self) -> None:
        self.stats.clear()
        self._refresh()


def run_sniff_app(stats: SniffStats, host: str, duration: float | None = None) -> None:
    SniffApp(stats, host, duration=duration).run()
