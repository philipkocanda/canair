"""Textual TUI for ``canair decode --plot`` — the interactive signal explorer.

Wraps a :class:`~canair.tui.apps.decode_plot.model.PlotModel` (which owns all state
and produces the ANSI-colored frame) in a Textual app, so it shares the look and
feel of the query monitor / sniff TUIs and inherits the shared ``?`` help modal.

Beyond the byte/signal sweep the model provides, the app adds:
- ``p`` — switch to another captured PID without leaving the explorer (B2).
- ``e`` — annotate the current signal/byte (writes through ``canair pids``) (B4).
- ``N`` — rename the current PID (``pids rename-pid``) (B4).

Keys come from the shared keymap (:mod:`canair.tui.keys`), never from a literal
key string, so the explorer cannot drift from the monitor and the stepper.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static
from textual.widgets.option_list import Option

from canair.profile.model import Profile
from canair.render.glyphs import ARROW
from canair.tui import styles
from canair.tui.flash import Flash, Severity, severity_of
from canair.tui.help import HelpMixin, LegendRow
from canair.tui.keys import CLOSE_HINT, DISMISS_DESCRIPTION, bind, key_hint, side_hint
from canair.tui.modals import ListPickerModal, TextPromptModal
from canair.tui.scroll import APP_BASE_CSS
from canair.tui.status import P_ESSENTIAL, P_LOW, P_NORMAL, StatusBar, hint_item

if TYPE_CHECKING:
    from canair.tui.apps.decode_plot.model import PlotModel

# Callback: (ecu, pid) -> a fresh PlotModel, or None if it can't be built.
ReloadFn = Callable[[str, str], "PlotModel | None"]


class PidPickerModal(ListPickerModal["tuple[str, str]"]):
    """Pick an ``(ECU, PID)`` from the captured set to re-plot.

    Filterable: a profile with a hundred captured PIDs is unusable as an
    arrow-key-only list, and ``/`` is the filter key in every canair picker.
    """

    CSS = ListPickerModal.picker_css("PidPickerModal", 60)

    def __init__(self, options: list[tuple[str, str]]):
        super().__init__("Switch PID")
        self._options = options

    def options(self, needle: str) -> list[Option]:
        return [
            Option(f"{ecu}  {pid}", id=f"{ecu}:{pid}")
            for ecu, pid in self._options
            if not needle or needle in f"{ecu} {pid}".lower()
        ]

    def chosen(self, option: Option) -> None:
        ecu, pid = (option.id or ":").split(":", 1)
        self.dismiss((ecu, pid))


class PlotApp(HelpMixin, App):
    """Interactive signal explorer for ``decode --plot``."""

    HELP_TITLE = "canair decode --plot — keyboard shortcuts"
    HELP_LEGEND: ClassVar[tuple[LegendRow, ...]] = (
        ("⠿", styles.OK, "the plotted series"),
        ("⠿", styles.MUTED, "the overlay reference, normalized"),
        ("✓", styles.OK, "byte mapped by a verified signal"),
        ("?", styles.WARNING, "byte mapped by an unverified signal"),
        ("~", styles.WARNING, "byte a defined signal also reads"),
        ("⚠", styles.WARNING, "the interpretation crosses an ISO-TP framing byte"),
    )

    # canair uses no command-palette commands; disable Textual's ctrl+p palette.
    ENABLE_COMMAND_PALETTE = False

    CSS = APP_BASE_CSS

    BINDINGS: ClassVar[list[Binding]] = [
        *bind("quit", "quit_view"),
        *bind("quit_force", "quit", show=False, priority=True),
        *bind("help", "help"),
        *bind("back", "escape", show=False),
        *bind("prev", "move(-1)", desc="prev offset/signal"),
        *bind("next", "move(1)", desc="next offset/signal"),
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
        *,
        profile: Profile,
    ):
        super().__init__()
        self.model = model
        self.profile = profile
        self._reload_pid = reload_pid
        self._pid_options = pid_options or []
        self.flash = Flash()

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
            hint_item(bit, P_NORMAL if i < 2 else P_LOW) for i, bit in enumerate(self._hints())
        ]
        flash = self.flash.item()
        if flash is not None:
            items.append(flash)
        items += [
            hint_item(key_hint("help"), P_ESSENTIAL),
            hint_item(key_hint("quit"), P_ESSENTIAL),
        ]
        status.set_lines(items)

    def _hints(self) -> list[str]:
        """The key hints for the current mode, most useful first."""
        if self.model.show_info:
            return [CLOSE_HINT, key_hint("info", DISMISS_DESCRIPTION)]
        if self.model.mode == "bytes":
            mode = [
                side_hint("offset/signal"),
                key_hint("value_type"),
                key_hint("byte_order"),
                key_hint("mode", "bytes/signal mode"),
            ]
        else:
            mode = [side_hint("offset/signal"), key_hint("mode", "bytes/signal mode")]
        return [
            *mode,
            key_hint("transform"),
            key_hint("overlay"),
            key_hint("info", "captures in view"),
            key_hint("increase", "zoom in", key="plus"),
            key_hint("decrease", "zoom out", key="minus"),
            key_hint("nudge_down", "pan left"),
            key_hint("nudge_up", "pan right"),
            key_hint("reset"),
        ]

    def _flash(self, msg: str, severity: Severity = "info") -> None:
        self.flash.show(msg, severity)
        self._refresh()

    def action_quit_view(self) -> None:
        """``q`` closes the in-body info view first; from the plot itself it quits."""
        if self.model.show_info:
            self.model.toggle_info()
            self._refresh()
            return
        self.exit()

    def _modal_active(self) -> bool:
        return len(self.screen_stack) > 1

    # -- navigation actions ------------------------------------------------
    def action_move(self, delta: int) -> None:
        self.model.move_right() if delta > 0 else self.model.move_left()
        self.flash.clear()
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
        msg = self.model.cycle_overlay()
        self._flash(msg, severity_of(msg, ok="info"))

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
            self._flash("PID switching unavailable", "warning")
            return

        def _done(choice: tuple[str, str] | None) -> None:
            if choice is None:
                return
            ecu, pid = choice
            try:
                new_model = reload_fn(ecu, pid)
            except Exception as exc:  # keep the TUI alive
                self._flash(f"Load failed: {exc}", "error")
                return
            if new_model is None:
                self._flash(f"No plottable data for {ecu} {pid}", "warning")
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
                self._flash("No signal to annotate", "warning")
                return
            self._prompt_param_note(name)
        else:
            expr = m.current_expr()
            if not expr:
                self._flash("This interpretation has no WiCAN expression to define", "warning")
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
            TextPromptModal(f"New candidate signal for {expr}", placeholder="SIGNAL_NAME"),
            _got_name,
        )

    def _param_expr(self, name: str) -> str:
        return (self.model.parameters.get(name) or {}).get("expression", "")

    def _write_param(
        self, name: str, expr: str, *, notes: str = "", verified: bool | None = None
    ) -> None:
        from canair.yaml_edit.pids import upsert_parameter

        try:
            upsert_parameter(
                self.model.ecu_key,
                self.model.pid_key,
                name,
                expr,
                notes=notes or None,
                verified=verified,
                profile=self.profile,
            )
        except Exception as exc:
            self._flash(f"Write failed: {exc}", "error")
            return
        self._flash(f"Saved {name}", "success")

    def action_rename_pid(self) -> None:
        if self._modal_active():
            return
        old = self.model.pid_key

        def _done(new: str | None) -> None:
            if not new or new == old:
                return
            from canair.yaml_edit.pids import rename_pid

            try:
                rename_pid(self.model.ecu_key, old, new, profile=self.profile)
            except Exception as exc:
                self._flash(f"Rename failed: {exc}", "error")
                return
            self.model.pid_key = new
            self._flash(f"Renamed PID {old} {ARROW} {new}", "success")

        self.push_screen(
            TextPromptModal(f"Rename PID {old} on {self.model.ecu_key}", value=old), _done
        )


def run_plot_app(
    model: PlotModel,
    reload_pid: ReloadFn | None = None,
    pid_options: list[tuple[str, str]] | None = None,
    *,
    profile: Profile,
) -> None:
    """Run the plot explorer TUI to completion."""
    PlotApp(model, reload_pid=reload_pid, pid_options=pid_options, profile=profile).run()
