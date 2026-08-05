#!/usr/bin/env python3
"""Textual TUI for ``canair captures --step`` — the capture stepper/comparator.

A thin shell over :class:`~canlib.commands.captures.step_model.StepModel`, which
owns all state and renders the frame. The app contributes only interaction:

- frame navigation, plus **scrolling** (a stacked multi-PID frame is routinely
  taller than the terminal — the reason this view is Textual at all);
- a **block cursor** (``tab``) so note/delete/drop act on a chosen stacked block;
- **live editing of the comparison**: ``a`` adds/removes PIDs, ``t``/``<``/``>``
  change the join tolerance, ``V`` cycles the view — no restart needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, SelectionList, Static
from textual.widgets.option_list import Option

from canlib.capture_types import CaptureEntry
from canlib.tui_help import HelpMixin
from canlib.tui_modals import ConfirmModal, TextPromptModal

from .step_render import key_label

if TYPE_CHECKING:
    from .step_model import JumpList, JumpTarget, StepModel

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


# Visible width of a jump row: the #jump-box width (96) less its horizontal
# padding (2+2), the OptionList's own padding/border and the scrollbar gutter.
# Rows are truncated to it rather than wrapped — a wrapped row would break the
# indentation tying a note to its session heading.
_JUMP_ROW_WIDTH = 84


class JumpModal(ModalScreen["JumpTarget | None"]):
    """Jump to a session, or to a capture carrying a note.

    Sessions are listed newest first, each followed by its noted captures. Rows
    the stepper cannot reach are shown **disabled with the reason** rather than
    hidden — a note on a non-payload or untimed capture is still something the
    user wrote and should be able to find.
    """

    CSS = """
    JumpModal { align: center middle; background: $background 60%; }
    #jump-box { width: 96; height: auto; max-height: 90%; padding: 1 2;
                border: round $accent; background: $surface; }
    #jump-title { text-style: bold; }
    #jump-hint { color: $text-muted; margin-bottom: 1; }
    #jump-list { height: auto; max-height: 22; }
    #jump-footer { color: $text-muted; margin-top: 1; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "cancel"),
        Binding("slash", "focus_filter", "filter"),
        Binding("n", "toggle_notes", "notes only"),
    ]

    def __init__(self, targets: JumpList, *, notes_only: bool = False):
        super().__init__()
        self._targets = targets.rows
        self._hidden_sessions = targets.hidden_sessions
        self._hidden_notes = targets.hidden_notes
        self._notes_only = notes_only
        self._filter = ""
        self._shown: list[JumpTarget] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="jump-box"):
            yield Label("Jump to session / note", id="jump-title")
            yield Label("enter jump · / filter · n notes-only · esc cancel", id="jump-hint")
            yield Input(placeholder="filter (date, label, state, note text)", id="jump-filter")
            yield OptionList(id="jump-list")
            yield Label("", id="jump-footer")

    def on_mount(self) -> None:
        self._repopulate()
        self.query_one("#jump-list", OptionList).focus()

    def _visible(self) -> list[JumpTarget]:
        rows = self._targets
        if self._notes_only:
            # Keep a session header only when it still has a note under it.
            with_notes = {t.session for t in rows if t.is_note}
            rows = [t for t in rows if t.is_note or t.session in with_notes]
        if self._filter:
            needle = self._filter.lower()
            hit = {t.session for t in rows if needle in t.searchable}
            rows = [
                t for t in rows if needle in t.searchable or (not t.is_note and t.session in hit)
            ]
        return rows

    def _repopulate(self) -> None:
        lst = self.query_one("#jump-list", OptionList)
        lst.clear_options()
        self._shown = self._visible()
        for n, t in enumerate(self._shown):
            lst.add_option(Option(self._row(t), id=str(n), disabled=bool(t.blocked)))
        blocked = sum(1 for t in self._shown if t.blocked and t.is_note)
        total_notes = sum(1 for t in self._targets if t.is_note)
        # Terse on purpose: the footer gets one line inside a 96-wide box.
        parts = [f"{len(self._shown)} rows", f"{total_notes} notes"]
        if blocked:
            parts.append(f"{blocked} unreachable")
        if self._hidden_sessions:
            parts.append(f"{self._hidden_sessions} sessions hidden")
        if self._hidden_notes:
            # Never silently drop a note the user wrote — say where to read it.
            parts.append(f"{self._hidden_notes} untimed notes hidden — captures --sessions")
        self.query_one("#jump-footer", Label).update(" · ".join(parts))
        if self._shown:
            lst.highlighted = next(
                (n for n, t in enumerate(self._shown) if not t.blocked),
                0,
            )

    def _row(self, t: JumpTarget) -> Text:
        """One list row.

        Built as a ``Text``, never a markup string: session labels and note text
        are user-owned free text, and a ``[ready]`` state or a bracketed note
        would otherwise be parsed away as a Rich style tag. ``no_wrap`` +
        ellipsis keeps one row on one line so the indented note rows stay
        readable under their session.
        """
        # Only a note explains itself: a blocked *session* row is just the
        # heading its notes hang under, and an inline "not in this selection"
        # there reads as noise rather than information.
        reason = f"   ({t.blocked})" if t.blocked and t.is_note else ""
        row = Text(no_wrap=True, overflow="ellipsis")
        if t.is_note:
            row.append("    ▸ ")
            row.append(t.detail, style="dim")
            row.append("   ")
            row.append(t.label)
        else:
            row.append(t.label)
            row.append(f"   {t.detail}", style="dim")
        # Hard-truncate: a wrapped row would break the indentation that ties a
        # note to the session above it (notes are arbitrarily long free text).
        # The reason is budgeted out first so it is never the part cut off.
        row.truncate(_JUMP_ROW_WIDTH - len(reason), overflow="ellipsis")
        if reason:
            row.append(reason, style="dim italic")
        return row

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "jump-filter":
            return
        self._filter = event.value.strip()
        self._repopulate()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.query_one("#jump-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        self.dismiss(self._shown[int(event.option.id)])

    def action_focus_filter(self) -> None:
        self.query_one("#jump-filter", Input).focus()

    def action_toggle_notes(self) -> None:
        self._notes_only = not self._notes_only
        self._repopulate()

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
        Binding("s", "jump", "sessions & notes"),
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
        from .step_model import PAGE_JUMP

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

    def action_jump(self) -> None:
        def _done(target: JumpTarget | None) -> None:
            if target is None:
                return
            if target.is_note and target.ref is not None:
                self._flash_msg = self.model.seek_capture(target.ref, target.key)
            else:
                self._flash_msg = self.model.seek_session(target.session)
            self._refresh()  # a jump lands on a new frame, so start at its top

        self.push_screen(JumpModal(self.model.jump_targets()), _done)

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

    def _write_note(self, cap: CaptureEntry, note: str) -> None:
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

    def _delete(self, cap: CaptureEntry) -> None:
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
