"""The monitor's save and new-recording keys: ``s`` labels or saves, ``n`` starts a recording.

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
from canair.tui.keys import display_key, keys_for


class RecordingMixin:
    """``action_save`` and ``action_new_segment`` of the monitor app."""

    controller: MonitorController

    @property
    def _app(self) -> App:
        assert isinstance(self, App)
        return self

    if TYPE_CHECKING:

        def _modal_active(self) -> bool: ...
        def _flash(
            self, msg: str, severity: Severity = "info", secs: float | None = None
        ) -> None: ...
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
        """Edit the current session (--save) or write a one-off snapshot (no --save).

        With --save active every payload is already journaled and written to a
        capture file automatically on exit, so ``s`` only *edits* the current
        recording (label / state / notes). Without --save nothing is recording: ``s``
        writes the payloads captured since the previous snapshot to a new capture
        file, once, and the dialog says so.
        """
        if self._modal_active():
            return
        if not self.controller.recorder.has_captures():
            self._flash("No payloads captured yet — nothing to save", "warning")
            return
        suggested, suggested_state, state_options = self._suggested_metadata()
        recorder = self.controller.recorder
        recording = recorder.journal is not None
        current_notes = ""
        new_key = display_key(keys_for("new")[0])
        if recording:
            title = "Edit current recording"
            caption = (
                "This changes the label, state and notes of the [b]current recording[/] — "
                "everything recorded in it, before and after this moment.\n"
                "[b]It does not start a new recording.[/] "
                f"[dim]Press [b]{new_key}[/b] to finish this recording and start the next.[/]"
            )
            save_button = "Update recording"
            suggested = recorder.session_label or suggested
            current_notes = recorder.session_notes
        else:
            title = "Save a one-off snapshot"
            caption = (
                "[b]This monitor is not recording[/] (no --save). This writes a single "
                "[b]snapshot[/]: the payloads captured since the last snapshot, or since "
                "the monitor started, go to a [b]new capture file[/] now.\n"
                "[b]Nothing is saved after this[/], and what you do not snapshot is lost "
                f"on quit. [dim]Press [b]{new_key}[/b] to start recording continuously.[/]"
            )
            save_button = "Save snapshot"

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
                current_notes=current_notes,
            ),
            _done,
        )

    def action_new_segment(self) -> None:
        """Finish the current recording and start the next; with none running, start one."""
        if self._modal_active():
            return
        recording = self.controller.recorder.journal is not None
        suggested, suggested_state, state_options = self._suggested_metadata(next_segment=True)

        def _done(result: tuple[str, str, str] | None) -> None:
            if result is None:
                self._flash(
                    "Cancelled — the current recording goes on"
                    if recording
                    else "Cancelled — not recording"
                )
                return
            label, state, notes = result
            try:
                msg = self.controller.recorder.new_segment(label, state, notes)
            except Exception as exc:  # keep the TUI alive on any error
                msg = f"Could not start a new recording: {exc}"
            self._flash_result(msg)
            self._update_header()

        if recording:
            title = "Finish recording & start the next"
            caption = (
                "[b]Writes the current recording to a capture file now[/], then "
                "starts the next one with the label below.\n"
                "[dim]One monitor run can produce several separately-labelled "
                "recordings this way; each is a capture session.[/]"
            )
            save_button = "Finish & start new"
        else:
            title = "Start recording"
            caption = (
                "[b]Records every poll from now on[/] under the label below. It is written "
                "to a capture file when you quit or finish it.\n"
                "[b]Payloads captured before this moment are not in it.[/] "
                "[dim]Take a snapshot first if you want them.[/]"
            )
            save_button = "Start recording"
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
