"""Textual TUI for the live monitor (``canair query --monitor``).

The latest values render into a single widget that updates *in place* inside a
scrollable container. The scroll position is independent of the data refresh, so
values keep updating wherever you are — the view never jumps or freezes, and
mouse wheel / scrollbar / keys all scroll natively.

Auto-follow uses the familiar "stick to the bottom only while already at the
bottom" rule (like ``tail -f`` in a pager): if you scroll up to read, new data
won't yank you back down; scroll to the bottom again to resume sticking. ``f``
disables sticking entirely, ``space`` pauses polling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

if TYPE_CHECKING:
    from .monitor import MonitorController

import asyncio
import time


class MonitorApp(App):
    """Scrollable, in-place live-value monitor."""

    CSS = """
    Screen { layout: vertical; }
    #scroll { height: 1fr; scrollbar-gutter: stable; }
    #body { height: auto; padding: 0 1; }
    #status {
        dock: bottom; height: 1; padding: 0 1;
        background: $panel; color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "quit", priority=True),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
        Binding("f", "toggle_follow", "follow"),
        Binding("space", "toggle_pause", "pause"),
        Binding("j", "scroll(1)", "down", show=False),
        Binding("k", "scroll(-1)", "up", show=False),
        Binding("g", "to_top", "top", show=False),
        Binding("G", "to_bottom", "bottom", show=False),
    ]

    def __init__(self, controller: MonitorController):
        super().__init__()
        self.controller = controller
        # Default: history modes tail the newest output; the plain dashboard
        # stays put (it fits, and users scroll to read). Either way the
        # stick-if-at-bottom rule keeps it non-annoying.
        self.follow_enabled = bool(controller.keep_mode)
        self.paused = False

    # -- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with VerticalScroll(id="scroll"):
            yield Static(self.controller.render(), id="body", markup=False)
        yield Static("", id="status", markup=True)

    def on_mount(self) -> None:
        self.query_one("#scroll", VerticalScroll).focus()
        self.run_worker(self._poll_loop(), name="poll", exclusive=True)
        self.set_interval(0.25, self._update_status)
        self._update_status()

    # -- polling -----------------------------------------------------------
    async def _poll_loop(self) -> None:
        while True:
            if not self.paused:
                t0 = time.monotonic()
                try:
                    await self.controller.poll_once()
                except Exception:
                    # Unexpected poll failure: stop cleanly rather than leaving a
                    # silently-frozen UI. Treated like a disconnect on exit.
                    self.controller.disconnected = True
                    self.exit()
                    return
                if self.controller.disconnected:
                    self.exit()
                    return
                self._refresh_body()
                remaining = self.controller.interval - (time.monotonic() - t0)
            else:
                remaining = 0.1
            # Chunked sleep so pause/quit stay responsive.
            deadline = time.monotonic() + max(remaining, 0.05)
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)

    def _refresh_body(self) -> None:
        scroll = self.query_one("#scroll", VerticalScroll)
        # Measure BEFORE swapping content: were we pinned to the bottom?
        at_bottom = scroll.scroll_offset.y >= scroll.max_scroll_y - 1
        self.query_one("#body", Static).update(self.controller.render())
        if self.follow_enabled and at_bottom:
            self.call_after_refresh(scroll.scroll_end, animate=False)
        self._update_status()

    def _update_status(self) -> None:
        c = self.controller
        follow = "[green]follow[/]" if self.follow_enabled else "[yellow]manual[/]"
        paused = " · [yellow reverse] PAUSED [/]" if self.paused else ""
        self.query_one("#status", Static).update(
            f"cycle {c.cycle} · every {c.interval:.1f}s · last {c.elapsed:.1f}s · {follow}{paused}"
            "    ↑↓/jk PgUp/PgDn g/G scroll · f follow · space pause · q quit"
        )

    # -- actions -----------------------------------------------------------
    def action_scroll(self, delta: int) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_relative(y=delta, animate=False)

    def action_to_top(self) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_home(animate=False)

    def action_to_bottom(self) -> None:
        self.query_one("#scroll", VerticalScroll).scroll_end(animate=False)

    def action_toggle_follow(self) -> None:
        self.follow_enabled = not self.follow_enabled
        if self.follow_enabled:
            self.query_one("#scroll", VerticalScroll).scroll_end(animate=False)
        self._update_status()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self._update_status()


async def run_monitor_app(controller: MonitorController) -> None:
    """Run the monitor TUI to completion (returns when the user quits)."""
    await MonitorApp(controller).run_async()
