"""Textual TUI for ``canair decode --plot`` — the interactive signal explorer.

Wraps a :class:`~canlib.commands.decode.plot.PlotModel` (which owns all state
and produces the ANSI-colored frame) in a Textual app, so it shares the look and
feel of the query monitor / sniff TUIs and inherits the shared ``?`` help modal.

Beyond the byte/param sweep the model provides, the app adds:
- ``p`` — switch to another captured PID without leaving the explorer (B2).
- ``e`` — annotate the current param/byte (writes through ``canair pids``) (B4).
- ``N`` — rename the current PID (``pids rename-pid``) (B4).

Keys come from the shared keymap (:mod:`canlib.tui_keys`), never from a literal
key string, so the explorer cannot drift from the monitor and the stepper.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from canlib.tui_help import HelpMixin
from canlib.tui_keys import bind
from canlib.tui_modals import TextPromptModal
from canlib.tui_status import P_ESSENTIAL, P_LOW, P_NORMAL, StatusBar, StatusItem

if TYPE_CHECKING:
    from .plot import PlotModel

# Callback: (ecu, pid) -> a fresh PlotModel, or None if it can't be built.
ReloadFn = Callable[[str, str], "PlotModel | None"]


class PidPickerModal(ModalScreen["tuple[str, str] | None"]):
    """Pick an ``(ECU, PID)`` from the captured set to re-plot.

    Filterable: a profile with a hundred captured PIDs is unusable as an
    arrow-key-only list, and ``/`` is the filter key in every canair picker.
    """

    CSS = """
    PidPickerModal { align: center middle; background: $background 60%; }
    #pick-box { width: 60; height: auto; max-height: 80%; padding: 1 2;
                border: round $accent; background: $surface; }
    #pick-title { text-style: bold; margin-bottom: 1; }
    #pick-filter { margin-bottom: 1; }
    #pick-list { height: auto; max-height: 20; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("back", "cancel", desc="cancel"),
        *bind("filter", "focus_filter", show=False),
    ]

    def __init__(self, options: list[tuple[str, str]]):
        super().__init__()
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="pick-box"):
            yield Label("Switch PID (↑↓ + enter · / filter · esc cancel)", id="pick-title")
            yield Input(placeholder="type to filter…", id="pick-filter")
            yield OptionList(id="pick-list")

    def on_mount(self) -> None:
        self._repopulate()
        # Focus the filter, not the list: typing narrows immediately and enter
        # hands focus to the list. Same contract as the monitor's PidPickerScreen.
        self.query_one("#pick-filter", Input).focus()

    def _repopulate(self) -> None:
        needle = self.query_one("#pick-filter", Input).value.strip().lower()
        lst = self.query_one("#pick-list", OptionList)
        lst.clear_options()
        for ecu, pid in self._options:
            if needle and needle not in f"{ecu} {pid}".lower():
                continue
            lst.add_option(Option(f"{ecu}  {pid}", id=f"{ecu}:{pid}"))
        if lst.option_count:
            lst.highlighted = 0

    def action_focus_filter(self) -> None:
        self.query_one("#pick-filter", Input).focus()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._repopulate()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        lst = self.query_one("#pick-list", OptionList)
        if lst.option_count:
            lst.focus()
            if lst.highlighted is None:
                lst.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        ecu, pid = (event.option.id or ":").split(":", 1)
        self.dismiss((ecu, pid))

    def action_cancel(self) -> None:
        self.dismiss(None)


class PlotApp(HelpMixin, App):
    """Interactive signal explorer for ``decode --plot``."""

    HELP_TITLE = "canair decode --plot — keyboard shortcuts"

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #scroll { height: 1fr; scrollbar-gutter: stable; scrollbar-size-vertical: 1; background: transparent; }
    #body { height: auto; padding: 0 1; background: transparent; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "quit"),
        *bind("quit_force", "quit", show=False, priority=True),
        *bind("help", "help"),
        *bind("back", "escape", show=False),
        *bind("prev", "move(-1)", desc="prev offset/param"),
        *bind("next", "move(1)", desc="next offset/param"),
        *bind("move_down", "scroll(1)", desc="scroll down", show=False),
        *bind("move_up", "scroll(-1)", desc="scroll up", show=False),
        *bind("axis_start", "to_top", desc="top", show=False),
        *bind("axis_end", "to_bottom", desc="bottom", show=False),
        *bind("value_type", "type_next", desc="type"),
        *bind("value_type_prev", "type_prev", show=False),
        *bind("byte_order", "endian"),
        *bind("mode", "mode"),
        *bind("transform", "transform"),
        *bind("overlay", "overlay"),
        *bind("increase", "zoom_in", desc="zoom in"),
        *bind("decrease", "zoom_out", desc="zoom out"),
        *bind("nudge_down", "pan(-1)", desc="pan left"),
        *bind("nudge_up", "pan(1)", desc="pan right"),
        *bind("reset", "reset_x", desc="reset"),
        *bind("info", "info", desc="captures in view"),
        *bind("pick", "pick_pid", desc="switch PID"),
        *bind("edit", "annotate", desc="annotate"),
        *bind("rename", "rename_pid", desc="rename"),
    ]

    def __init__(
        self,
        model: PlotModel,
        reload_pid: ReloadFn | None = None,
        pid_options: list[tuple[str, str]] | None = None,
    ):
        super().__init__()
        self.model = model
        self._reload_pid = reload_pid
        self._pid_options = pid_options or []
        self._flash_msg = ""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="scroll"):
            yield Static("", id="body", markup=False)
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        self.query_one("#scroll", VerticalScroll).focus()
        self._refresh()

    # -- rendering ---------------------------------------------------------
    def _refresh(self) -> None:
        try:
            body = self.query_one("#body", Static)
            status = self.query_one("#status", StatusBar)
        except NoMatches:
            return
        body.update(Text.from_ansi("\n".join(self.model.render_lines())))
        # Mode-specific keys (the ones that move the plot) outrank the rest; the
        # bar sheds the tail on a narrow terminal instead of clipping it.
        items = [
            StatusItem(f"[dim]{bit}[/]", P_NORMAL if i < 2 else P_LOW)
            for i, bit in enumerate(self.model.hint_bits())
        ]
        if self._flash_msg:
            items.append(StatusItem(f"[b green]{self._flash_msg}[/]", P_ESSENTIAL))
        items += [
            StatusItem("[dim]? help[/]", P_ESSENTIAL),
            StatusItem("[dim]q quit[/]", P_ESSENTIAL),
        ]
        status.set_lines(items)

    def _flash(self, msg: str) -> None:
        self._flash_msg = msg
        self._refresh()

    def _modal_active(self) -> bool:
        return len(self.screen_stack) > 1

    # -- navigation actions ------------------------------------------------
    def action_move(self, delta: int) -> None:
        self.model.move_right() if delta > 0 else self.model.move_left()
        self._flash_msg = ""
        self._refresh()

    def action_type_next(self) -> None:
        self.model.type_next()
        self._refresh()

    def action_type_prev(self) -> None:
        self.model.type_prev()
        self._refresh()

    def action_endian(self) -> None:
        self.model.toggle_endian()
        self._refresh()

    def action_mode(self) -> None:
        self.model.toggle_mode()
        self._refresh()

    def action_transform(self) -> None:
        self.model.cycle_transform()
        self._refresh()

    def action_overlay(self) -> None:
        self._flash(self.model.cycle_overlay())

    def action_zoom_in(self) -> None:
        self.model.zoom_in()
        self._refresh()

    def action_zoom_out(self) -> None:
        self.model.zoom_out()
        self._refresh()

    def action_pan(self, delta: int) -> None:
        self.model.pan_right() if delta > 0 else self.model.pan_left()
        self._refresh()

    def action_reset_x(self) -> None:
        self.model.reset_x()
        self._refresh()

    def action_info(self) -> None:
        self.model.toggle_info()
        self._refresh()

    def action_escape(self) -> None:
        """Back out one level; never quit (``q`` is the only exit).

        An escape that sometimes exits the program is the one key a user cannot
        safely press to find out where they are, so at the top level this is a
        deliberate no-op — the same rule the monitor and stepper now follow.
        """
        if self._modal_active():
            return
        if self.model.show_info:
            self.model.toggle_info()
            self._refresh()

    def action_scroll(self, delta: int) -> None:
        try:
            scroll = self.query_one("#scroll", VerticalScroll)
        except NoMatches:
            return
        scroll.scroll_relative(y=delta, animate=False)

    def action_to_top(self) -> None:
        try:
            self.query_one("#scroll", VerticalScroll).scroll_home(animate=False)
        except NoMatches:
            return

    def action_to_bottom(self) -> None:
        try:
            self.query_one("#scroll", VerticalScroll).scroll_end(animate=False)
        except NoMatches:
            return

    # -- PID switch (B2) ---------------------------------------------------
    def action_pick_pid(self) -> None:
        if self._modal_active():
            return
        reload_fn = self._reload_pid
        if not (reload_fn and self._pid_options):
            self._flash("PID switching unavailable.")
            return

        def _done(choice: tuple[str, str] | None) -> None:
            if choice is None:
                return
            ecu, pid = choice
            try:
                new_model = reload_fn(ecu, pid)
            except Exception as exc:  # keep the TUI alive
                self._flash(f"Load failed: {exc}")
                return
            if new_model is None:
                self._flash(f"No plottable data for {ecu} {pid}.")
                return
            self.model = new_model
            self._flash(f"Now plotting {ecu} {pid}")

        self.push_screen(PidPickerModal(self._pid_options), _done)

    # -- annotation / rename (B4) ------------------------------------------
    def action_annotate(self) -> None:
        if self._modal_active():
            return
        m = self.model
        if m.mode == "param":
            name = m.current_param_name()
            if not name:
                self._flash("No parameter to annotate.")
                return
            self._prompt_param_note(name)
        else:
            expr = m.current_expr()
            if not expr:
                self._flash("This interpretation has no WiCAN expression to define.")
                return
            self._prompt_new_candidate(expr)

    def _prompt_param_note(self, name: str) -> None:
        existing = (self.model.parameters.get(name) or {}).get("notes", "")

        def _done(note: str | None) -> None:
            if note is None:
                return
            self._write_param(name, self._param_expr(name), notes=note)

        self.push_screen(
            TextPromptModal(f"Note for {name}", placeholder="notes", value=existing), _done
        )

    def _prompt_new_candidate(self, expr: str) -> None:
        def _got_name(name: str | None) -> None:
            if not name:
                return

            def _got_note(note: str | None) -> None:
                self._write_param(name, expr, notes=note or "", verified=False)

            self.push_screen(
                TextPromptModal(f"Note for {name} (optional)", placeholder="notes"), _got_note
            )

        self.push_screen(
            TextPromptModal(f"New candidate param for {expr}", placeholder="PARAM_NAME"),
            _got_name,
        )

    def _param_expr(self, name: str) -> str:
        return (self.model.parameters.get(name) or {}).get("expression", "")

    def _write_param(
        self, name: str, expr: str, *, notes: str = "", verified: bool | None = None
    ) -> None:
        from canlib.pids_edit import upsert_parameter

        try:
            upsert_parameter(
                self.model.ecu_key,
                self.model.pid_key,
                name,
                expr,
                notes=notes or None,
                verified=verified,
            )
        except Exception as exc:
            self._flash(f"Write failed: {exc}")
            return
        self._flash(f"Saved {name}")

    def action_rename_pid(self) -> None:
        if self._modal_active():
            return
        old = self.model.pid_key

        def _done(new: str | None) -> None:
            if not new or new == old:
                return
            from canlib.pids_edit import rename_pid

            try:
                rename_pid(self.model.ecu_key, old, new)
            except Exception as exc:
                self._flash(f"Rename failed: {exc}")
                return
            self.model.pid_key = new
            self._flash(f"Renamed PID {old} → {new}")

        self.push_screen(
            TextPromptModal(f"Rename PID {old} on {self.model.ecu_key}", value=old), _done
        )


def run_plot_app(
    model: PlotModel,
    reload_pid: ReloadFn | None = None,
    pid_options: list[tuple[str, str]] | None = None,
) -> None:
    """Run the plot explorer TUI to completion."""
    PlotApp(model, reload_pid=reload_pid, pid_options=pid_options).run()
