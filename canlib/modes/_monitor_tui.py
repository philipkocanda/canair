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
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

if TYPE_CHECKING:
    from .monitor import MonitorController

import asyncio
import time
from datetime import datetime


class SaveDialog(ModalScreen[tuple[str, str, str] | None]):
    """Modal prompt for capture metadata (label / state / notes).

    Dismisses with ``(label, state, notes)`` on save, or ``None`` on cancel.
    """

    CSS = """
    SaveDialog { align: center middle; background: $background 60%; }
    #dialog {
        width: 60; height: auto; padding: 1 2;
        border: round $accent; background: $surface;
    }
    #dialog-title { text-style: bold; margin-bottom: 1; }
    #dialog Input { margin-bottom: 1; }
    #dialog-buttons { height: auto; align-horizontal: right; }
    #dialog-buttons Button { margin-left: 2; }
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, suggested_label: str):
        super().__init__()
        self._suggested = suggested_label

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="dialog"):
            yield Label("Save captures to profile", id="dialog-title")
            yield Input(value=self._suggested, placeholder="Label (required)", id="f-label")
            yield Input(placeholder="State (e.g. ready, parked)", id="f-state")
            yield Input(placeholder="Notes (optional)", id="f-notes")
            with Horizontal(id="dialog-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#f-label", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        label = self.query_one("#f-label", Input).value.strip() or self._suggested
        state = self.query_one("#f-state", Input).value.strip()
        notes = self.query_one("#f-notes", Input).value.strip()
        self.dismiss((label, state, notes))

    def action_cancel(self) -> None:
        self.dismiss(None)


class MonitorApp(App):
    """Scrollable, in-place live-value monitor."""

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #scroll { height: 1fr; scrollbar-gutter: stable; background: transparent; }
    #body { height: auto; padding: 0 1; background: transparent; }
    #status { dock: bottom; height: 1; padding: 0 1; background: transparent; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
        Binding("s", "save", "save"),
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
        # Transient status-line message (e.g. save confirmation) + its expiry.
        self._flash_msg = ""
        self._flash_expires = 0.0


    # -- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with VerticalScroll(id="scroll"):
            yield Static(self.controller.render(), id="body", markup=False)
        yield Static("", id="status", markup=True)

    def on_mount(self) -> None:
        # Use the terminal's own palette + default background (ansi_default)
        # rather than Textual's remapped truecolor theme + grey surface. Keeps
        # byte colours matching the plain output and readable on any themed
        # terminal (iTerm2, Termius, …). CSS `background: transparent` covers the
        # background regardless; the theme fixes the colour mapping.
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
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
        # The poll worker can fire mid-teardown (after quit); ignore if the DOM
        # is already gone.
        try:
            scroll = self.query_one("#scroll", VerticalScroll)
            body = self.query_one("#body", Static)
        except NoMatches:
            return
        # Measure BEFORE swapping content: were we pinned to the bottom?
        at_bottom = scroll.scroll_offset.y >= scroll.max_scroll_y - 1
        body.update(self.controller.render())
        if self.follow_enabled and at_bottom:
            self.call_after_refresh(scroll.scroll_end, animate=False)
        self._update_status()

    def _update_status(self) -> None:
        try:
            status = self.query_one("#status", Static)
        except NoMatches:
            return
        c = self.controller
        follow = "[green]follow[/]" if self.follow_enabled else "[yellow]manual[/]"
        paused = " · [reverse] PAUSED [/]" if self.paused else ""
        # ELM path reports commands + time spent in the ELM327; the raw path
        # reports UDS requests (no ELM involved).
        if getattr(c, "raw", False):
            metric = f"{c.last_cmds}[dim] reqs ·[/]"
        else:
            metric = f"{c.last_cmds}[dim] cmds/[/]{c.last_elm_time:.1f}[dim]s ELM ·[/]"
        flash = ""
        if self._flash_msg:
            if time.monotonic() < self._flash_expires:
                flash = f"    [b green]{self._flash_msg}[/]"
            else:
                self._flash_msg = ""
        status.update(
            f"[dim]cycle[/] {c.cycle} [dim]· every[/] {c.interval:.1f}[dim]s · last[/] "
            f"{c.elapsed:.1f}[dim]s ·[/] {metric} "
            f"{follow}{paused}"
            "    [dim]↑↓/jk PgUp/PgDn g/G · f follow · space pause · s save · q quit[/]"
            f"{flash}"
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

    def _flash(self, msg: str, secs: float = 5.0) -> None:
        """Show a transient message in the status line for ``secs`` seconds."""
        self._flash_msg = msg
        self._flash_expires = time.monotonic() + secs
        self._update_status()

    def action_save(self) -> None:
        """Prompt for metadata and save the captured payloads to the profile."""
        if not self.controller.has_captures():
            self._flash("No payloads captured yet — nothing to save.")
            return
        # Pre-fill the label with the active query (e.g. "BCM VCU:2101"),
        # falling back to a timestamp when it can't be derived.
        suggested = ""
        label_fn = getattr(self.controller, "query_label", None)
        if callable(label_fn):
            suggested = label_fn()
        if not suggested:
            suggested = f"Monitor {datetime.now():%H:%M}"

        def _done(result: tuple[str, str, str] | None) -> None:
            if result is None:
                self._flash("Save cancelled.")
                return
            label, state, notes = result
            try:
                msg = self.controller.save_now(label, state, notes)
            except Exception as exc:  # keep the TUI alive on any save error
                msg = f"Save failed: {exc}"
            self._flash(msg)

        self.push_screen(SaveDialog(suggested), _done)


async def run_monitor_app(controller: MonitorController) -> None:
    """Run the monitor TUI to completion (returns when the user quits)."""
    await MonitorApp(controller).run_async()
