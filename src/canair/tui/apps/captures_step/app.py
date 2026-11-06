#!/usr/bin/env python3
"""Textual TUI for ``canair captures --step`` — the capture stepper/comparator.

A thin shell over :class:`~canair.tui.apps.captures_step.model.StepModel`, which
owns all state and renders the frame. The app contributes only interaction:

- frame navigation, plus **scrolling** (a stacked multi-PID frame is routinely
  taller than the terminal — the reason this view is Textual at all). Moving
  between frames keeps the scroll position, so one byte deep in a stacked frame
  can be watched *across* frames instead of being scrolled back to every step;
- a **block cursor** (``tab``) so note/delete/drop act on a chosen stacked block;
- **live editing of the comparison**: ``p`` picks the PIDs compared, ``J``/``,``/``.``
  change the join tolerance, ``V`` cycles the view — no restart needed.

Keys come from the shared keymap (:mod:`canair.tui.keys`), never from a literal
key string, so the stepper cannot drift from the monitor and plot explorer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Label, SelectionList, Static
from textual.widgets.option_list import Option

from canair.captures.types import CaptureEntry
from canair.render.formatting import BYTE_COLOUR_LEGEND
from canair.render.glyphs import CURSOR, EMPTY, SEP, WARN
from canair.tui import styles
from canair.tui.flash import Flash, Severity, severity_of
from canair.tui.help import FRAME_SCROLL_HELP_ROWS, HelpMixin, LegendRow
from canair.tui.keys import CANCEL_HINT, bind, hint, key_hint, side_hint
from canair.tui.modals import ConfirmModal, KitModal, ListPickerModal, TextPromptModal
from canair.tui.scroll import APP_BASE_CSS, reveal_marker
from canair.tui.status import (
    P_ESSENTIAL,
    P_HIGH,
    P_LOW,
    P_NORMAL,
    StatusBar,
    StatusItem,
    hint_item,
)
from canair.tui.styles import dialog_css
from canair.tui.widgets import CheckSelectionList

from .render import key_label

if TYPE_CHECKING:
    from .model import JumpList, JumpTarget, StepModel

Key = tuple[str, str]


class PidSelectModal(KitModal["list[Key] | None"]):
    """Choose the (ECU, PID) keys being compared.

    A filterable checklist of every PID with captures in scope. Filtering
    re-populates the list, so the checked set is tracked separately from the
    widget and merged back on every change — a PID checked before filtering
    stays checked when it scrolls out of view.
    """

    CSS = (
        dialog_css("PidSelectModal", "pid", width=76)
        + "#pid-list { height: auto; max-height: 20; }\n#pid-filter { margin-bottom: 1; }"
    )

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("back", "cancel", desc="cancel"),
        *bind("apply", "apply", priority=True),
        *bind("filter", "focus_filter", show=False),
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
            yield Input(placeholder="type to filter…", id="pid-filter")
            yield CheckSelectionList(id="pid-list")
            with Horizontal(id="pid-buttons"):
                yield Button("Apply", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")
            yield Label(
                hint(key_hint("pause", "toggle"), "filter", "apply", CANCEL_HINT), id="pid-hint"
            )

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


class JumpModal(ListPickerModal["JumpTarget"]):
    """Jump to a session, or to a capture carrying a note.

    Sessions are listed newest first, each followed by its noted captures. Rows
    the stepper cannot reach are shown **disabled with the reason** rather than
    hidden — a note on a non-payload or untimed capture is still something the
    user wrote and should be able to find.
    """

    CSS = ListPickerModal.picker_css("JumpModal", 96, rows=22)

    BINDINGS: ClassVar[list[Binding]] = [
        # ctrl+n, not `n`: the stepper beneath uses `n` for "next" and a modal
        # that redefines a host key to something unrelated is exactly the
        # inconsistency this keymap exists to prevent.
        *bind("notes_only", "toggle_notes"),
    ]

    def __init__(self, targets: JumpList, *, notes_only: bool = False):
        super().__init__("Jump to session / note")
        self._targets = targets.rows
        self._hidden_sessions = targets.hidden_sessions
        self._hidden_notes = targets.hidden_notes
        self._notes_only = notes_only
        self._shown: list[JumpTarget] = []

    def hint_text(self) -> str:
        return hint("accept", "filter", "notes_only", CANCEL_HINT)

    def _visible(self, needle: str) -> list[JumpTarget]:
        rows = self._targets
        if self._notes_only:
            # Keep a session header only when it still has a note under it.
            with_notes = {t.session for t in rows if t.is_note}
            rows = [t for t in rows if t.is_note or t.session in with_notes]
        if needle:
            hit = {t.session for t in rows if needle in t.searchable}
            rows = [
                t for t in rows if needle in t.searchable or (not t.is_note and t.session in hit)
            ]
        return rows

    def options(self, needle: str) -> list[Option]:
        self._shown = self._visible(needle)
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
        self.set_footer(f" {SEP} ".join(parts))
        return [
            Option(self._row(t), id=str(n), disabled=bool(t.blocked))
            for n, t in enumerate(self._shown)
        ]

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
            row.append(f"    {SEP} ")
            row.append(t.detail, style=styles.MUTED)
            row.append("   ")
            row.append(t.label)
        else:
            row.append(t.label)
            row.append(f"   {t.detail}", style=styles.MUTED)
        # Hard-truncate: a wrapped row would break the indentation that ties a
        # note to the session above it (notes are arbitrarily long free text).
        # The reason is budgeted out first so it is never the part cut off.
        row.truncate(_JUMP_ROW_WIDTH - len(reason), overflow="ellipsis")
        if reason:
            row.append(reason, style=f"{styles.MUTED} italic")
        return row

    def chosen(self, option: Option) -> None:
        if option.id is None:
            return
        self.dismiss(self._shown[int(option.id)])

    def action_toggle_notes(self) -> None:
        self._notes_only = not self._notes_only
        self.refill()


class CapturesStepApp(HelpMixin, App):
    """Interactive stepper/comparator for saved captures."""

    HELP_TITLE = "canair captures --step — keyboard shortcuts"

    # canair uses no command-palette commands; disable Textual's ctrl+p palette.
    ENABLE_COMMAND_PALETTE = False

    # Handled by the focused scroll container itself, so there is no Binding to
    # derive these from — but they are the way back to a tall frame's header,
    # which matters now that a frame move keeps the scroll position.
    HELP_EXTRA_ROWS: ClassVar[tuple[tuple[str, str], ...]] = FRAME_SCROLL_HELP_ROWS
    HELP_LEGEND: ClassVar[tuple[LegendRow, ...]] = (
        *BYTE_COLOUR_LEGEND,
        (CURSOR, styles.HEADING_NAME, "the focused block"),
        (WARN, styles.WARNING, "a gap in the recording since the previous frame"),
        ("dim", styles.MUTED, "the previous capture of the PID, for reference"),
    )

    CSS = (
        APP_BASE_CSS
        + """
    #header { dock: top; height: 1; padding: 0 1; background: transparent; }
    """
    )

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "quit"),
        *bind("quit_force", "quit", show=False, priority=True),
        *bind("help", "help"),
        # The top level has nothing to back out of: escape stays inert rather than quitting.
        *bind("back", "stay", show=False),
        # Frame navigation (the primary axis, so g/G address it).
        *bind("next", "advance(1)", desc="next frame"),
        *bind("prev", "advance(-1)", desc="prev frame"),
        *bind("page_next", "page(1)", desc="+100 frames"),
        *bind("page_prev", "page(-1)", desc="-100 frames"),
        *bind("axis_start", "first", desc="first frame"),
        *bind("axis_end", "last", desc="last frame"),
        *bind("goto", "goto", desc="goto frame"),
        # Scrolling within a (tall) frame.
        *bind("move_down", "scroll(1)", desc="scroll down", show=False),
        *bind("move_up", "scroll(-1)", desc="scroll up", show=False),
        # The comparison itself.
        # priority: Textual would otherwise consume tab for focus movement.
        *bind("block_next", "block(1)", show=False, priority=True),
        *bind("block_prev", "block(-1)", show=False, priority=True),
        *bind("session", "jump", desc="sessions & notes"),
        *bind("pick", "pick_pids", desc="compare PIDs"),
        *bind("exclude", "drop_pid", desc="drop this PID"),
        *bind("join_tol", "set_tol"),
        *bind("nudge_up", "nudge_tol(1)", desc="wider tolerance", show=False),
        *bind("nudge_down", "nudge_tol(-1)", desc="tighter tolerance", show=False),
        *bind("view", "cycle_view"),
        *bind("rulers", "toggle_rulers"),
        *bind("unique", "toggle_all"),
        # Per-capture edits (act on the focused block).
        *bind("edit", "edit_note", desc="edit note"),
        *bind("delete", "delete", desc="delete capture"),
    ]

    _gated_cache: ClassVar[frozenset[str] | None] = None

    def __init__(self, model: StepModel):
        super().__init__()
        self.model = model
        self.flash = Flash()

    def compose(self) -> ComposeResult:
        yield Static("", id="header", markup=True)
        with VerticalScroll(id="scroll"):
            yield Static("", id="body", markup=False)
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        self.query_one("#scroll", VerticalScroll).focus()
        self._refresh()

    # -- rendering ---------------------------------------------------------

    def _refresh(self, *, reveal: bool = False) -> None:
        """Repaint the current frame in place, never moving the scroll position.

        A stacked frame is routinely taller than the terminal, and stepping is
        how a byte is watched *across* frames — so the viewport stays exactly
        where the reader put it and only the content underneath changes (the
        same rule as the live monitor). ``reveal`` additionally brings the
        focused ``▶`` block on screen, for the actions whose whole point is to
        show a particular block.
        """
        try:
            body = self.query_one("#body", Static)
            header = self.query_one("#header", Static)
            status = self.query_one("#status", StatusBar)
        except NoMatches:  # transient teardown query misses are harmless
            return
        body.update(self.model.render())
        header.update(f"[b]Captures step[/] [dim]{SEP}[/] {self.model.keys_label()}")
        # Deliberately terse: `?` is the cheat-sheet, so the status bar does not
        # have to be one — and it drops its least essential parts rather than
        # clipping its tail on a narrow terminal.
        bits = self.model.status_bits()
        # The frame position is the one segment always worth the space.
        items = [hint_item(bit, P_HIGH if i == 0 else P_LOW) for i, bit in enumerate(bits)]
        # The frame's own timestamp lives in the body header, which scrolls out
        # of view in a tall frame — so keep it in the always-visible bar too, or
        # stepping while scrolled down says nothing about *when* you are.
        items[1:1] = self._time_items()
        flash = self.flash.item()
        if flash is not None:
            items.append(flash)
        items += [
            hint_item(side_hint("frame"), P_NORMAL),
            hint_item(key_hint("pick", "compare PIDs"), P_NORMAL),
            hint_item(key_hint("join_tol"), P_LOW),
            hint_item(key_hint("help"), P_ESSENTIAL),
            hint_item(key_hint("quit"), P_ESSENTIAL),
        ]
        status.set_lines(items)
        if reveal:
            # After the repaint has been laid out, so the scroll clamp sees the
            # new frame's height rather than the previous one's.
            self.call_after_refresh(self._reveal_focused_block)

    def _time_items(self) -> list[StatusItem]:
        """The current frame's timestamp, date droppable before the clock."""
        when = self.model.current_time()
        if when is None:
            return []
        return [
            StatusItem(f"[dim]{when:%Y-%m-%d}[/]", P_LOW),
            StatusItem(f"[dim]{when:%H:%M:%S}.{when.microsecond // 1000:03d}[/]", P_HIGH),
        ]

    def _reveal_focused_block(self) -> None:
        try:
            scroll = self.query_one("#scroll", VerticalScroll)
            body = self.query_one("#body", Static)
        except NoMatches:
            return
        reveal_marker(scroll, body)

    def _flash(self, msg: str, severity: Severity = "info") -> None:
        self.flash.show(msg, severity)
        self._refresh()

    def _note(self, msg: str) -> None:
        """Keep a model's status note (``""`` clears) until the next move; no repaint."""
        if msg:
            self.flash.show(msg, severity_of(msg, ok="info"))
        else:
            self.flash.clear()

    def action_stay(self) -> None:
        """Escape: the stepper has no level to back out of, so it does nothing."""

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
        self._note(self.model.advance(delta))
        self._refresh()

    def action_page(self, direction: int) -> None:
        from .model import PAGE_JUMP

        self.action_advance(PAGE_JUMP * direction)

    def action_first(self) -> None:
        self.model.first()
        self.flash.clear()
        self._refresh()

    def action_last(self) -> None:
        self.model.last()
        self.flash.clear()
        self._refresh()

    def action_scroll(self, delta: int) -> None:
        try:
            scroll = self.query_one("#scroll", VerticalScroll)
        except NoMatches:
            return
        scroll.scroll_down(animate=False) if delta > 0 else scroll.scroll_up(animate=False)

    def action_block(self, delta: int) -> None:
        self.model.move_block(delta)
        self.flash.clear()
        self._refresh(reveal=True)

    def action_goto(self) -> None:
        total = self.model.frame_count()

        def _done(value: str | None) -> None:
            if not value:
                return
            self._note(self.model.goto(int(value)))
            self._refresh()

        def _check(value: str) -> str | None:
            try:
                int(value)
            except ValueError:
                return f"Not a frame number: {value or 'empty'}"
            return None

        self.push_screen(
            TextPromptModal(f"Go to frame # (1-{total})", placeholder="1", validate=_check), _done
        )

    # -- the comparison ----------------------------------------------------

    def action_pick_pids(self) -> None:

        def _done(keys: list[Key] | None) -> None:
            if keys is None:
                return
            if not keys:
                self._flash("Keep at least one PID selected", "warning")
                return
            self.model.set_keys(keys)
            self._flash(f"Comparing {len(keys)} PID{'s' if len(keys) != 1 else ''}")

        self.push_screen(PidSelectModal(self.model.available_keys(), list(self.model.keys)), _done)

    def action_jump(self) -> None:
        def _done(target: JumpTarget | None) -> None:
            if target is None:
                return
            if target.is_note and target.ref is not None:
                self._note(self.model.seek_capture(target.ref, target.key))
                # Jumping to a note is a request to *see* that capture, so its
                # block is brought on screen even when it sits below the fold.
                self._refresh(reveal=True)
                return
            self._note(self.model.seek_session(target.session))
            self._refresh()

        self.push_screen(JumpModal(self.model.jump_targets()), _done)

    def action_drop_pid(self) -> None:
        key = self.model.focused_key()
        if key is None:
            return
        if self.model.remove_key(key):
            self._flash(f"Dropped {key_label(key)}")
        else:
            self._flash("Cannot drop the last PID", "warning")

    def action_set_tol(self) -> None:

        def _done(value: str | None) -> None:
            if not value:
                return
            self.model.set_tol(float(value))
            self._flash(f"Join tolerance {self.model.tol_s:g}s")

        def _check(value: str) -> str | None:
            try:
                float(value)
            except ValueError:
                return f"Not a number of seconds: {value or 'empty'}"
            return None

        self.push_screen(
            TextPromptModal(
                "Join tolerance in seconds",
                placeholder="5",
                value=f"{self.model.tol_s:g}",
                validate=_check,
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
            self._flash("No capture in the focused block", "warning")
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
        from canair.captures.session_edit import set_capture_note

        target = self._writable_target(cap)
        if target is None:
            return
        try:
            set_capture_note(target, cap["_session_idx"], cap["_capture_idx"], note)
        except Exception as ex:
            self._flash(f"Note save failed: {ex}", "error")
            return
        saved = "Note saved" if note.strip() else "Note cleared"
        if not self.model.reload_from_disk():
            self.exit(message=f"  {saved} {EMPTY} no captures left")
            return
        self._flash(saved, "success")

    def action_delete(self) -> None:
        cap = self.model.focused_capture()
        if cap is None:
            self._flash("No capture in the focused block", "warning")
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

    def _writable_target(self, cap: CaptureEntry) -> Path | None:
        """The file to edit for ``cap``, or None (flashing why) if it is read-only.

        Goes through the row's own locator: joining ``file`` onto the captures dir
        picks the wrong directory as soon as reads span two layers.
        """
        from canair.captures import layers
        from canair.captures.store import entry_path

        target = entry_path(cap, self.model.captures_dir, profile=self.model.profile)
        if layers.read_only_files([target], self.model.captures_dir, profile=self.model.profile):
            self._flash("Read-only: this capture belongs to the profile's base layer", "error")
            return None
        return target

    def _delete(self, cap: CaptureEntry) -> None:
        from canair.captures.session_edit import delete_capture

        target = self._writable_target(cap)
        if target is None:
            return
        try:
            delete_capture(target, cap["_session_idx"], cap["_capture_idx"])
        except Exception as ex:
            self._flash(f"Delete failed: {ex}", "error")
            return
        if not self.model.reload_from_disk():
            self.exit(message=f"  Capture deleted {EMPTY} no captures left")
            return
        self._flash("Capture deleted", "success")


def run_step_app(model: StepModel) -> None:
    """Run the stepper (Textual prints any exit message, e.g. the list went empty)."""
    CapturesStepApp(model).run()
