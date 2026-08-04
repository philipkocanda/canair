#!/usr/bin/env python3
"""Textual TUI for ``canair captures --step`` — the capture stepper/comparator.

A thin shell over :class:`~canlib.commands._captures_step_model.StepModel`, which
owns all state and renders the frame. The app contributes only interaction:

- frame navigation, plus **scrolling** (a stacked multi-PID frame is routinely
  taller than the terminal — the reason this view is Textual at all);
- a **block cursor** (``tab``) so note/delete/drop act on a chosen stacked block;
- **live editing of the comparison**: ``a`` adds/removes PIDs, ``t``/``<``/``>``
  change the join tolerance, ``V`` cycles the view — no restart needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, SelectionList, Static

from canlib.commands._captures_step_render import key_label
from canlib.tui_help import HelpMixin
from canlib.tui_modals import ConfirmModal, TextPromptModal

if TYPE_CHECKING:
    from canlib.commands._captures_step_model import StepModel

Key = tuple[str, str]


class PidSelectModal(ModalScreen["list[Key] | None"]):
    """Add/remove the (ECU, PID) keys being compared.

    A filterable checklist of every PID with captures in scope. Filtering
    re-populates the list, so the checked set is tracked separately from the
    widget and merged back on every change — a PID checked before filtering
    stays checked when it scrolls out of view.
    """

    CSS = """
    PidSelectModal { align: center middle; background: $background 60%; }
    #pid-box { width: 76; height: auto; max-height: 90%; padding: 1 2;
               border: round $accent; background: $surface; }
    #pid-title { text-style: bold; }
    #pid-hint { color: $text-muted; margin-bottom: 1; }
    #pid-list { height: auto; max-height: 20; }
    #pid-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    #pid-buttons Button { margin-left: 2; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+s", "apply", "apply"),
        Binding("slash", "focus_filter", "filter"),
    ]

    def __init__(self, available: list[tuple[Key, int]], selected: list[Key]):
        super().__init__()
        self._available = available
        self._checked: set[Key] = set(selected)
        self._filter = ""
        self._syncing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="pid-box"):
            yield Label("Compare PIDs", id="pid-title")
            yield Label(
                "space toggle · / filter · ctrl+s apply · esc cancel",
                id="pid-hint",
            )
            yield Input(placeholder="filter (e.g. hvac or 2201)", id="pid-filter")
            yield SelectionList[Key](id="pid-list")
            with Horizontal(id="pid-buttons"):
                yield Button("Apply", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._repopulate()
        self.query_one("#pid-list", SelectionList).focus()

    def _visible(self) -> list[tuple[Key, int]]:
        if not self._filter:
            return self._available
        needle = self._filter.lower()
        return [(k, n) for k, n in self._available if needle in key_label(k).lower()]

    def _repopulate(self) -> None:
        lst = self.query_one("#pid-list", SelectionList)
        self._syncing = True
        try:
            lst.clear_options()
            for key, count in self._visible():
                lst.add_option(
                    (f"{key_label(key):<22} {count:>6} captures", key, key in self._checked)
                )
        finally:
            self._syncing = False

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "pid-filter":
            return
        self._filter = event.value.strip()
        self._repopulate()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.query_one("#pid-list", SelectionList).focus()

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged[Key]) -> None:
        if self._syncing:
            return
        visible = {k for k, _ in self._visible()}
        self._checked = (self._checked - visible) | set(event.selection_list.selected)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_apply() if event.button.id == "ok" else self.dismiss(None)

    def action_focus_filter(self) -> None:
        self.query_one("#pid-filter", Input).focus()

    def action_apply(self) -> None:
        self.dismiss(sorted(self._checked))

    def action_cancel(self) -> None:
        self.dismiss(None)


class CapturesStepApp(HelpMixin, App):
    """Interactive stepper/comparator for saved captures."""

    HELP_TITLE = "canair captures --step — keyboard shortcuts"

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #header { dock: top; height: 1; padding: 0 1; background: transparent; }
    #scroll { height: 1fr; scrollbar-gutter: stable; scrollbar-size-vertical: 1; background: transparent; }
    #body { height: auto; padding: 0 1; background: transparent; }
    #status { dock: bottom; height: 1; padding: 0 1; background: transparent; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "quit"),
        Binding("escape", "quit", "quit", show=False),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
        Binding("question_mark", "help", "help"),
        # Frame navigation.
        Binding("right", "advance(1)", "next frame"),
        Binding("l", "advance(1)", "next frame", show=False),
        Binding("n", "advance(1)", "next frame", show=False),
        Binding("space", "advance(1)", "next frame", show=False),
        Binding("left", "advance(-1)", "prev frame"),
        Binding("h", "advance(-1)", "prev frame", show=False),
        Binding("p", "advance(-1)", "prev frame", show=False),
        Binding("right_square_bracket", "page(1)", "+100 frames"),
        Binding("left_square_bracket", "page(-1)", "-100 frames"),
        Binding("g", "first", "first frame"),
        Binding("G", "last", "last frame"),
        Binding("colon", "goto", "goto frame"),
        # Scrolling within a (tall) frame.
        Binding("down", "scroll(1)", "scroll down", show=False),
        Binding("up", "scroll(-1)", "scroll up", show=False),
        Binding("j", "scroll(1)", "scroll down", show=False),
        Binding("k", "scroll(-1)", "scroll up", show=False),
        # The comparison itself.
        # priority: Textual would otherwise consume tab for focus movement.
        Binding("tab", "block(1)", "next block", show=False, priority=True),
        Binding("shift+tab", "block(-1)", "prev block", show=False, priority=True),
        Binding("a", "pick_pids", "add/remove PIDs"),
        Binding("x", "drop_pid", "drop this PID"),
        Binding("t", "set_tol", "join tolerance"),
        Binding("greater_than_sign", "nudge_tol(1)", "wider tolerance", show=False),
        Binding("less_than_sign", "nudge_tol(-1)", "tighter tolerance", show=False),
        Binding("V", "cycle_view", "view mode"),
        Binding("r", "toggle_rulers", "rulers"),
        Binding("u", "toggle_all", "unique/all payloads"),
        # Per-capture edits (act on the focused block).
        Binding("e", "edit_note", "edit note"),
        Binding("d", "delete", "delete capture"),
    ]

    _gated_cache: ClassVar[frozenset[str] | None] = None

    def __init__(self, model: StepModel):
        super().__init__()
        self.model = model
        self._flash_msg = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="header", markup=True)
        with VerticalScroll(id="scroll"):
            yield Static("", id="body", markup=False)
        yield Static("", id="status", markup=True)

    def on_mount(self) -> None:
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        self.query_one("#scroll", VerticalScroll).focus()
        self._refresh()

    # -- rendering ---------------------------------------------------------

    def _refresh(self, *, to_top: bool = True) -> None:
        try:
            body = self.query_one("#body", Static)
            header = self.query_one("#header", Static)
            status = self.query_one("#status", Static)
            scroll = self.query_one("#scroll", VerticalScroll)
        except NoMatches:  # transient teardown query misses are harmless
            return
        body.update(self.model.render())
        header.update(f"[b]captures step[/] [dim]·[/] {self.model.keys_label()}")
        flash = f"    [b green]{self._flash_msg}[/]" if self._flash_msg else ""
        # Deliberately terse: `?` is the cheat-sheet, so the status bar does not
        # have to be one (and stays readable on a narrow terminal).
        status.update(
            f"[dim]{self.model.status_line()}[/]{flash}"
            "    [dim]←/→ frame · a PIDs · t tol · ? help · q quit[/]"
        )
        if to_top:
            # A new frame starts at its top; scrolling is for reading *within* one.
            scroll.scroll_home(animate=False)

    def _flash(self, msg: str) -> None:
        self._flash_msg = msg
        self._refresh(to_top=False)

    def _modal_active(self) -> bool:
        return len(self.screen_stack) > 1

    @classmethod
    def _gated_actions(cls) -> frozenset[str]:
        """Action names this app declares, which a modal must suppress.

        Derived from ``BINDINGS`` so it cannot drift. ``quit`` stays live (ctrl-c
        must always work), and framework actions the app never declares — notably
        ``app.focus_next`` behind ``tab`` — are deliberately absent, so a modal
        keeps its own focus/movement keys.
        """
        cached = cls.__dict__.get("_gated_cache")
        if cached is None:
            cached = frozenset(
                b.action.partition("(")[0] for b in cls.BINDINGS if isinstance(b, Binding)
            ) - {"quit"}
            cls._gated_cache = cached
        return cached

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Disable this app's own keys while a modal is open.

        One choke point instead of a guard in every action. A ``False`` leaves the
        key *unhandled*, so keys the app claims with ``priority=True`` (``tab``)
        still fall through to the modal that needs them.
        """
        if self._modal_active() and action in self._gated_actions():
            return False
        return True

    # -- navigation --------------------------------------------------------

    def action_advance(self, delta: int) -> None:
        self._flash_msg = self.model.advance(delta)
        self._refresh()

    def action_page(self, direction: int) -> None:
        from canlib.commands._captures_step_model import PAGE_JUMP

        self.action_advance(PAGE_JUMP * direction)

    def action_first(self) -> None:
        self.model.first()
        self._flash_msg = ""
        self._refresh()

    def action_last(self) -> None:
        self.model.last()
        self._flash_msg = ""
        self._refresh()

    def action_scroll(self, delta: int) -> None:
        try:
            scroll = self.query_one("#scroll", VerticalScroll)
        except NoMatches:
            return
        scroll.scroll_down(animate=False) if delta > 0 else scroll.scroll_up(animate=False)

    def action_block(self, delta: int) -> None:
        self.model.move_block(delta)
        self._flash_msg = ""
        self._refresh(to_top=False)

    def action_goto(self) -> None:
        total = self.model.frame_count()

        def _done(value: str | None) -> None:
            if not value:
                return
            try:
                self._flash_msg = self.model.goto(int(value))
            except ValueError:
                self._flash_msg = f"Not a frame number: {value}"
            self._refresh()

        self.push_screen(TextPromptModal(f"Go to frame # (1-{total})", placeholder="1"), _done)

    # -- the comparison ----------------------------------------------------

    def action_pick_pids(self) -> None:

        def _done(keys: list[Key] | None) -> None:
            if keys is None:
                return
            if not keys:
                self._flash("Keep at least one PID selected.")
                return
            self.model.set_keys(keys)
            self._flash(f"Comparing {len(keys)} PID{'s' if len(keys) != 1 else ''}")

        self.push_screen(PidSelectModal(self.model.available_keys(), list(self.model.keys)), _done)

    def action_drop_pid(self) -> None:
        key = self.model.focused_key()
        if key is None:
            return
        if self.model.remove_key(key):
            self._flash(f"Dropped {key_label(key)}")
        else:
            self._flash("Cannot drop the last PID.")

    def action_set_tol(self) -> None:

        def _done(value: str | None) -> None:
            if not value:
                return
            try:
                self.model.set_tol(float(value))
            except ValueError:
                self._flash(f"Not a number of seconds: {value}")
                return
            self._flash(f"Join tolerance {self.model.tol_s:g}s")

        self.push_screen(
            TextPromptModal(
                "Join tolerance in seconds", placeholder="5", value=f"{self.model.tol_s:g}"
            ),
            _done,
        )

    def action_nudge_tol(self, direction: int) -> None:
        self.model.nudge_tol(direction)
        self._flash(f"Join tolerance {self.model.tol_s:g}s")

    def action_cycle_view(self) -> None:
        self._flash(f"View: {self.model.cycle_view()}")

    def action_toggle_rulers(self) -> None:
        self._flash(f"Rulers {'on' if self.model.toggle_rulers() else 'off'}")

    def action_toggle_all(self) -> None:
        self._flash("All payloads" if self.model.toggle_show_all() else "Unique payloads")

    # -- per-capture edits -------------------------------------------------

    def action_edit_note(self) -> None:
        cap = self.model.focused_capture()
        if cap is None:
            self._flash("No capture in the focused block.")
            return
        existing = (cap.get("notes") or "").replace("\n", " ").strip()

        def _done(value: str | None) -> None:
            if value is None:
                return
            self._write_note(cap, value)

        self.push_screen(
            TextPromptModal(
                f"Note for {cap['ecu']} {cap['pid']} @ {cap.get('time', '')}",
                placeholder="notes",
                value=existing,
            ),
            _done,
        )

    def _write_note(self, cap: dict, note: str) -> None:
        from canlib.captures import set_capture_note

        try:
            assert self.model.captures_dir is not None
            set_capture_note(
                self.model.captures_dir / cap["file"],
                cap["_session_idx"],
                cap["_capture_idx"],
                note,
            )
        except Exception as ex:
            self._flash(f"Note save failed: {ex}")
            return
        saved = "Note saved" if note.strip() else "Note cleared"
        if not self.model.reload_from_disk():
            self.exit(message=f"  {saved} — no captures left")
            return
        self._flash(saved)

    def action_delete(self) -> None:
        cap = self.model.focused_capture()
        if cap is None:
            self._flash("No capture in the focused block.")
            return

        def _done(confirmed: bool | None) -> None:
            if confirmed:
                self._delete(cap)
            else:
                self._flash("Delete cancelled")

        self.push_screen(
            ConfirmModal(
                "Delete this capture?",
                detail=f"{cap['ecu']} {cap['pid']} @ {cap.get('time', '')} ({cap.get('file', '')})",
            ),
            _done,
        )

    def _delete(self, cap: dict) -> None:
        from canlib.captures import delete_capture

        try:
            assert self.model.captures_dir is not None
            delete_capture(
                self.model.captures_dir / cap["file"],
                cap["_session_idx"],
                cap["_capture_idx"],
            )
        except Exception as ex:
            self._flash(f"Delete failed: {ex}")
            return
        if not self.model.reload_from_disk():
            self.exit(message="  Capture deleted — no captures left")
            return
        self._flash("Capture deleted")


def run_step_app(model: StepModel) -> None:
    """Run the stepper (Textual prints any exit message, e.g. the list went empty)."""
    CapturesStepApp(model).run()
