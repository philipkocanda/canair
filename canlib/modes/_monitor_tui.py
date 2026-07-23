"""Textual TUI for the live monitor (``canair query --monitor``).

The latest values render into a single widget that updates *in place* inside a
scrollable container. The scroll position is independent of the data refresh, so
values keep updating wherever you are — the view never jumps or freezes, and
mouse wheel / scrollbar / keys all scroll natively.

Auto-follow uses the familiar "stick to the bottom only while already at the
bottom" rule (like ``tail -f`` in a pager): if you scroll up to read, new data
won't yank you back down; scroll to the bottom again to resume sticking. ``f``
disables sticking entirely, ``space`` pauses polling.

The monitor doubles as a lightweight PID editor: ``↑``/``↓`` move a selection
cursor over the decoded parameter rows, ``e`` opens an in-place edit dialog
(expression / unit / min / max / notes / verified / enabled), ``v`` and ``d``
quick-toggle the selected parameter's verified/enabled flags, and ``F`` cycles
a display filter (all / verified / unverified / enabled / disabled). Edits are
written through :mod:`canlib.pids_edit` and picked up on the next poll.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Static

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

    def __init__(self, suggested_label: str, suggested_state: str = ""):
        super().__init__()
        self._suggested = suggested_label
        self._suggested_state = suggested_state

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="dialog"):
            yield Label("Save captures to profile", id="dialog-title")
            yield Input(value=self._suggested, placeholder="Label (required)", id="f-label")
            yield Input(
                value=self._suggested_state,
                placeholder="States (comma-separated, e.g. ready, parked)",
                id="f-state",
            )
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


class EditParamDialog(ModalScreen[dict | None]):
    """Modal editor for a single PID parameter's definition.

    Prefilled from the selected parameter's current fields; dismisses with a
    ``{expression, unit, min, max, notes, verified, enabled}`` dict on save, or
    ``None`` on cancel. Writing is done by the caller (via the monitor editor).
    """

    CSS = """
    EditParamDialog { align: center middle; background: $background 60%; }
    #edit-dialog {
        width: 72; height: auto; padding: 1 2;
        border: round $accent; background: $surface;
    }
    #edit-title { text-style: bold; margin-bottom: 1; }
    #edit-dialog Input { margin-bottom: 1; }
    #edit-dialog Checkbox { height: 1; }
    #edit-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    #edit-buttons Button { margin-left: 2; }
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, target: dict):
        super().__init__()
        self._target = target

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        t = self._target
        title = f"Edit {t.get('ecu', '')} {t.get('pid', '')} {t.get('name', '')}"
        with Vertical(id="edit-dialog"):
            yield Label(title.strip(), id="edit-title")
            yield Input(value=t.get("expression", ""), placeholder="expression", id="e-expr")
            yield Input(value=t.get("unit", ""), placeholder="unit", id="e-unit")
            yield Input(value=t.get("min", ""), placeholder="min", id="e-min")
            yield Input(value=t.get("max", ""), placeholder="max", id="e-max")
            yield Input(value=t.get("notes", ""), placeholder="notes", id="e-notes")
            yield Checkbox("verified", value=bool(t.get("verified")), id="e-verified")
            yield Checkbox("enabled", value=bool(t.get("enabled", True)), id="e-enabled")
            with Horizontal(id="edit-buttons"):
                yield Button("Save", variant="primary", id="edit-save")
                yield Button("Cancel", id="edit-cancel")

    def on_mount(self) -> None:
        self.query_one("#e-expr", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-save":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        self.dismiss(
            {
                "expression": self.query_one("#e-expr", Input).value.strip(),
                "unit": self.query_one("#e-unit", Input).value.strip(),
                "min": self.query_one("#e-min", Input).value.strip(),
                "max": self.query_one("#e-max", Input).value.strip(),
                "notes": self.query_one("#e-notes", Input).value.strip(),
                "verified": self.query_one("#e-verified", Checkbox).value,
                "enabled": self.query_one("#e-enabled", Checkbox).value,
            }
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


class MonitorApp(App):
    """Scrollable, in-place live-value monitor."""

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #scroll { height: 1fr; scrollbar-gutter: stable; background: transparent; }
    #body { height: auto; padding: 0 1; background: transparent; }
    #status { dock: bottom; height: 2; padding: 0 1; background: transparent; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
        Binding("s", "save", "save"),
        Binding("f", "toggle_follow", "follow"),
        Binding("r", "toggle_rulers", "rulers"),
        Binding("space", "toggle_pause", "pause"),
        Binding("down", "select(1)", "select down", show=False, priority=True),
        Binding("up", "select(-1)", "select up", show=False, priority=True),
        Binding("e", "edit", "edit"),
        Binding("v", "verify", "verify"),
        Binding("d", "disable", "en/disable"),
        Binding("F", "cycle_filter", "filter"),
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
        # Let the controller repaint mid-cycle as each PID resolves, so a slow /
        # timing-out PID never freezes the whole view (only its own row lags).
        self.controller._on_partial = self._refresh_body
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
        # Live auto-suggested vehicle state (from decoded PID values), if any.
        state_txt = ""
        state_fn = getattr(c, "suggested_state", None)
        if callable(state_fn):
            try:
                s = state_fn()
            except Exception:
                s = None
            if s:
                state_txt = f"[dim]· state[/] [cyan]{s}[/] "
        status.update(
            f"[dim]cycle[/] {c.cycle} [dim]· every[/] {c.interval:.1f}[dim]s · last[/] "
            f"{c.elapsed:.1f}[dim]s ·[/] {metric} "
            f"{state_txt}{follow}{paused}"
            f"{flash}\n"
            f"{self._edit_status_line()}"
        )

    def _editor(self):
        """The controller's edit collaborator, or None (older/fake controllers)."""
        return getattr(self.controller, "editor", None)

    def _edit_status_line(self) -> str:
        """Second status line: current selection, active filter, and edit keys."""
        ed = self._editor()
        if ed is None:
            return (
                "[dim]↑↓/jk PgUp/PgDn g/G · f follow · space pause · r rulers · s save · q quit[/]"
            )
        filt = getattr(ed, "filter_mode", "all")
        filt_txt = f"[cyan]{filt}[/]" if filt != "all" else "[dim]all[/]"
        sel = ""
        label = ed.selection_label() if hasattr(ed, "selection_label") else ""
        if label:
            sel = f"[b]▶ {label}[/]  "
        return (
            f"{sel}[dim]filter[/] {filt_txt} "
            "[dim]· ↑↓ select · e edit · v verify · d en/disable · F filter · s save · q quit[/]"
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

    def action_toggle_rulers(self) -> None:
        self.controller.show_rulers = not self.controller.show_rulers
        self._refresh_body()

    # -- selection / in-place editing --------------------------------------
    def _last_queries(self):
        return getattr(self.controller, "last_queries", [])

    def _modal_active(self) -> bool:
        """True when a dialog owns the screen (don't act on the view beneath it)."""
        return len(self.screen_stack) > 1

    def action_select(self, delta: int) -> None:
        """Move the parameter-selection cursor and scroll it into view.

        Falls back to plain scrolling when there is no editor or nothing is
        selectable, so the arrow keys never feel dead.
        """
        if self._modal_active():
            return
        ed = self._editor()
        if ed is None or ed.move(self._last_queries(), delta) is None:
            self.action_scroll(delta)
            return
        self._render_no_follow()
        self._scroll_to_selection()
        self._update_status()

    def action_cycle_filter(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        mode = ed.cycle_filter(self._last_queries())
        self._render_no_follow()
        self._flash(f"Filter: {mode}")

    def action_verify(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        if getattr(ed, "selected", None) is None:
            self._flash("Select a parameter first (↑↓).")
            return
        self._flash(ed.toggle_verified())
        self._render_no_follow()

    def action_disable(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        if getattr(ed, "selected", None) is None:
            self._flash("Select a parameter first (↑↓).")
            return
        self._flash(ed.toggle_enabled())
        self._render_no_follow()

    def action_edit(self) -> None:
        """Open the edit dialog for the selected parameter (polling pauses)."""
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        target = ed.edit_target() if hasattr(ed, "edit_target") else None
        if not target:
            self._flash("Select a parameter first (↑↓).")
            return
        # Auto-pause polling while the modal is open; restore prior state after.
        was_paused = self.paused
        self.paused = True

        def _done(result: dict | None) -> None:
            self.paused = was_paused
            if result is None:
                self._flash("Edit cancelled.")
                return
            try:
                msg = ed.apply_edit(result)
            except Exception as exc:  # keep the TUI alive on any edit error
                msg = f"Edit failed: {exc}"
            self._flash(msg)
            self._render_no_follow()

        self.push_screen(EditParamDialog(target), _done)

    def _render_no_follow(self) -> None:
        """Repaint the body without the follow-tail snap (used during editing)."""
        try:
            body = self.query_one("#body", Static)
        except NoMatches:
            return
        body.update(self.controller.render())
        self._update_status()

    def _scroll_to_selection(self) -> None:
        """Scroll so the ``▶`` selection cursor is within the viewport."""
        try:
            scroll = self.query_one("#scroll", VerticalScroll)
            body = self.query_one("#body", Static)
        except NoMatches:
            return
        plain = body.render().plain
        line = next((i for i, ln in enumerate(plain.splitlines()) if "▶" in ln), None)
        if line is None:
            return
        top = int(scroll.scroll_offset.y)
        height = scroll.size.height or 1
        if line < top:
            scroll.scroll_to(y=line, animate=False)
        elif line >= top + height:
            scroll.scroll_to(y=max(0, line - height + 1), animate=False)

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

        suggested_state = ""
        state_fn = getattr(self.controller, "suggested_state", None)
        if callable(state_fn):
            try:
                suggested_state = state_fn() or ""
            except Exception:
                suggested_state = ""

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

        self.push_screen(SaveDialog(suggested, suggested_state), _done)


async def run_monitor_app(controller: MonitorController) -> None:
    """Run the monitor TUI to completion (returns when the user quits)."""
    await MonitorApp(controller).run_async()
