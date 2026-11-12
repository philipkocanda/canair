"""The monitor's signal cursor and in-place editing: select, filter, verify, ship, edit.

:class:`EditingMixin` is mixed into :class:`~canair.tui.apps.monitor.app.MonitorApp`. The keys
that reach it belong to the values pane (:class:`~canair.tui.apps.monitor.panes.ValuesPane`),
and the Signal and View menus run the same actions. The selection and the filter are the
controller's editor (:class:`~canair.live.monitor.edit.MonitorEditor`), which writes edits
through :mod:`canair.yaml_edit.pids`; an edit shows on the next poll.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static

from canair.live.monitor.controller import MonitorController
from canair.tui.apps.monitor.dialogs import EditParamDialog
from canair.tui.apps.monitor.status import SELECT_FIRST
from canair.tui.flash import Severity
from canair.tui.scroll import reveal_marker


class EditingMixin:
    """The signal cursor, the filter and the edit keys of the monitor app."""

    controller: MonitorController
    paused: bool

    @property
    def _app(self) -> App:
        assert isinstance(self, App)
        return self

    if TYPE_CHECKING:

        def _modal_active(self) -> bool: ...
        def _refresh_body(self) -> None: ...
        def action_scroll(self, delta: int) -> None: ...
        def _flash(
            self, msg: str, severity: Severity = "info", secs: float | None = None
        ) -> None: ...
        def _flash_result(self, msg: str) -> None: ...

    def _editor(self):
        """The controller's edit collaborator, or None (older/fake controllers)."""
        return getattr(self.controller, "editor", None)

    def _last_queries(self):
        return getattr(self.controller, "last_queries", [])

    def action_select(self, delta: int) -> None:
        """Move the signal-selection cursor and scroll it into view.

        Falls back to plain scrolling when there is no editor or nothing is selectable, so
        the arrow keys never feel dead.
        """
        if self._modal_active():
            return
        ed = self._editor()
        if ed is None or ed.move(self._last_queries(), delta) is None:
            self.action_scroll(delta)
            return
        self._refresh_body()
        self._scroll_to_selection()

    def action_clear_selection(self) -> None:
        """Drop the ▶ signal cursor so ↑/↓ resume plain scrolling.

        No-ops (letting escape fall through) when a modal owns the screen or nothing is
        selected, so it never steals escape from a dialog.
        """
        if self._modal_active():
            return
        ed = self._editor()
        if ed is None or not getattr(ed, "clear_selection", None):
            return
        if not ed.clear_selection():
            return
        self._refresh_body()
        self._flash("Selection cleared")

    def action_cycle_filter(self) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        self.action_set_filter(ed.cycle_filter(self._last_queries()))

    def action_set_filter(self, mode: str) -> None:
        """Show only the signals of one filter (``all``, ``verified``, ``ship``, …)."""
        ed = self._editor()
        if ed is None:
            return
        ed.set_filter(mode, self._last_queries())
        self._refresh_body()
        self._flash(f"Filter: {mode}")

    def _toggle_flag(self, toggle: str) -> None:
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        if getattr(ed, "selected", None) is None:
            self._flash(SELECT_FIRST, "warning")
            return
        self._flash_result(getattr(ed, toggle)())
        self._refresh_body()

    def action_verify(self) -> None:
        self._toggle_flag("toggle_verified")

    def action_toggle_ship(self) -> None:
        self._toggle_flag("toggle_ship")

    def action_edit(self) -> None:
        """Open the edit dialog for the selected signal (polling pauses)."""
        ed = self._editor()
        if ed is None or self._modal_active():
            return
        target = ed.edit_target() if hasattr(ed, "edit_target") else None
        if not target:
            self._flash(SELECT_FIRST, "warning")
            return
        # Auto-pause polling while the modal is open; restore prior state after.
        was_paused = self.paused
        self.paused = True

        def _done(result: dict | None) -> None:
            self.paused = was_paused
            if result is None:
                self._flash("Edit cancelled")
                return
            try:
                msg = ed.apply_edit(result)
            except Exception as exc:  # keep the TUI alive on any edit error
                msg = f"Edit failed: {exc}"
            self._flash_result(msg)
            self._refresh_body()

        self._app.push_screen(EditParamDialog(target), _done)

    def _scroll_to_selection(self) -> None:
        """Scroll so the ``▶`` selection cursor is within the viewport."""
        try:
            scroll = self._app.query_one("#scroll", VerticalScroll)
            body = self._app.query_one("#body", Static)
        except NoMatches:
            return
        reveal_marker(scroll, body)
