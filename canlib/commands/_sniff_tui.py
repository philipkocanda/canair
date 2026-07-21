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

if TYPE_CHECKING:
    from canlib.commands.sniff import SniffStats

import time


class SniffApp(App):
    """Live per-ID sniff table."""

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #scroll { height: 1fr; scrollbar-gutter: stable; background: transparent; }
    #body { height: auto; padding: 0 1; background: transparent; }
    #status { dock: bottom; height: 1; padding: 0 1; background: transparent; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "quit", priority=True),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
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
        yield Static("", id="status", markup=True)

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
            self.query_one("#status", Static).update(
                f"[dim]sniff[/] {self.host} [dim]·[/] {len(rows)} IDs [dim]·[/] "
                f"{self.stats.total_frames} frames [dim]·[/] {elapsed:.0f}s"
                "    [dim]c clear · q quit[/]"
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
