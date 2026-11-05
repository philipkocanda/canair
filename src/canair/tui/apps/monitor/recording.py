"""The monitor's save and new-session keys: ``s`` labels or saves, ``n`` rotates the segment.

:class:`RecordingMixin` is mixed into :class:`~canair.tui.apps.monitor.app.MonitorApp`. It
opens the :class:`~canair.tui.apps.monitor.dialogs.SaveDialog` pre-filled from the controller
and hands the answer to ``controller.recorder``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from textual.app import App

from canair.live.monitor.controller import MonitorController
from canair.states.tokens import join_states
from canair.tui.apps.monitor.dialogs import SaveDialog
from canair.tui.flash import Severity


class RecordingMixin:
    """``action_save`` and ``action_new_segment`` of the monitor app."""

    controller: MonitorController

    @property
    def _app(self) -> App:
        assert isinstance(self, App)
        return self

    if TYPE_CHECKING:

        def _modal_active(self) -> bool: ...
        def _flash(self, msg: str, severity: Severity = "info", secs: float = 5.0) -> None: ...
        def _flash_result(self, msg: str) -> None: ...
        def _update_header(self) -> None: ...

    def _suggested_metadata(
        self, *, next_segment: bool = False
    ) -> tuple[str, str, list[tuple[str, str]] | None]:
        """Gather (suggested_label, suggested_state, state_options) for a dialog.

        Label pre-fills from the active query (e.g. "BCM VCU:2101"), falling back
        to a timestamp. State comes from ``offer_states``: the union observed over
        the span being saved, or — for ``next_segment`` (the ``n`` dialog, which
        labels the segment about to start) — the state active now.
        """
        suggested = self.controller.view.query_label()
        if not suggested:
            suggested = f"Monitor {datetime.now():%H:%M}"

        suggested_state = ""
        try:
            suggested_state = join_states(
                self.controller.recorder.offer_states(next_segment=next_segment)
            )
        except Exception:
            suggested_state = ""

        state_options = None
        try:
            state_options = self.controller.state.state_options()
        except Exception:
            state_options = None
        return suggested, suggested_state, state_options

    def action_save(self) -> None:
        """Label the recording (--save) or write captures now (no --save).

        With --save active every payload is already journaled and written to a
        capture file automatically on exit, so ``s`` only *labels* the current
        recording (label / state / notes). Without --save it performs an on-demand
        write of the payloads captured so far to a new capture file.
        """
        if self._modal_active():
            return
        if not self.controller.recorder.has_captures():
            self._flash("No payloads captured yet — nothing to save", "warning")
            return
        suggested, suggested_state, state_options = self._suggested_metadata()
        recording = self.controller.recorder.journal is not None
        if recording:
            title = "Label recording"
            caption = (
                "This recording is [b]already being saved[/] — every payload is "
                "written automatically when you quit or start a new session.\n"
                "[dim]Here you only set its label / state / notes. "
                "Use [b]n[/b] to finish this session and start a fresh one.[/]"
            )
            save_button = "Set label"
        else:
            title = "Save captures now"
            caption = (
                "Writes the payloads captured so far to a [b]new capture file[/].\n"
                "[dim](Not recording with --save — this is a one-off snapshot save.)[/]"
            )
            save_button = "Save"

        def _done(result: tuple[str, str, str] | None) -> None:
            if result is None:
                self._flash("Cancelled — nothing saved")
                return
            label, state, notes = result
            try:
                msg = self.controller.recorder.save_now(label, state, notes)
            except Exception as exc:  # keep the TUI alive on any save error
                msg = f"Save failed: {exc}"
            self._flash_result(msg)
            self._update_header()

        self._app.push_screen(
            SaveDialog(
                suggested,
                suggested_state,
                state_options,
                title=title,
                caption=caption,
                save_button=save_button,
            ),
            _done,
        )

    def action_new_segment(self) -> None:
        """Finish the current --save session (write it now) and start a fresh one."""
        if self._modal_active():
            return
        if self.controller.recorder.journal is None:
            self._flash("Start a new session needs --save (nothing is being recorded)", "warning")
            return
        suggested, suggested_state, state_options = self._suggested_metadata(next_segment=True)

        def _done(result: tuple[str, str, str] | None) -> None:
            if result is None:
                self._flash("Cancelled — current session kept recording")
                return
            label, state, notes = result
            try:
                msg = self.controller.recorder.new_segment(label, state, notes)
            except Exception as exc:  # keep the TUI alive on any error
                msg = f"Could not start new session: {exc}"
            self._flash_result(msg)
            self._update_header()

        self._app.push_screen(
            SaveDialog(
                suggested,
                suggested_state,
                state_options,
                title="Finish session & start a new one",
                caption=(
                    "[b]Writes the current session to a capture file now[/], then "
                    "starts a fresh recording with the label below.\n"
                    "[dim]One monitor run can produce several separately-labelled "
                    "sessions this way.[/]"
                ),
                save_button="Finish & start new",
            ),
            _done,
        )
