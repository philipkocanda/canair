"""Live monitor — what the display shows, as plain values.

:class:`MonitorView` (``controller.view``) answers the display's questions about the run:
the polled-selector label, the title of the current segment, the session summary the info
overlay lists, how many history rows to draw and which view mode comes next. It returns
strings, numbers and dicts. Turning the run into Rich text is
:class:`~canair.live.monitor.render.MonitorRenderer`, the only monitor module that imports
``rich``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from canair.captures.keepmode import KEEP_LAST
from canair.ecus.pids import ecu_index_key

if TYPE_CHECKING:
    from .controller import MonitorController

# Ordered display view modes cycled by the TUI 'V' key. Increasing detail:
#   ecus    — just the responding ECUs (+ a PID/signal count)
#   ranges  — each signal's captured value span (min-max / distinct labels)
#   signals — the decoded signal table (no raw hex)
#   full    — signals + the raw byte payloads (the default)
VIEW_MODES = ("ecus", "ranges", "signals", "full")

# Default on-screen history depth (runs of identical payloads per PID). A noisy PID
# can amass hundreds of runs over a session; rendering them all every cycle both
# clutters the view and (on a big multi-PID sweep) can make the frame large enough
# to choke the TUI. Keep the live view compact — every poll is still in the journal
# when --save. `--keep N` overrides it.
DEFAULT_HISTORY_ROWS = 4


class MonitorView:
    """The display model of one monitor run, read from the controller's state."""

    def __init__(self, controller: MonitorController):
        self.c = controller

    def cycle(self) -> str:
        """Advance to the next display view mode (TUI 'V'); returns the new mode."""
        c = self.c
        idx = VIEW_MODES.index(c.view_mode) if c.view_mode in VIEW_MODES else 0
        c.view_mode = VIEW_MODES[(idx + 1) % len(VIEW_MODES)]
        return c.view_mode

    def history_limit(self) -> int:
        """How many history rows (runs of identical payloads) to render per PID.

        --keep N shows the requested N; every other keep-mode stays compact at a
        handful of newest runs so a noisy/long session doesn't flood (or choke)
        the live view — every poll is still in the --save journal.
        """
        c = self.c
        if c.keep_mode == KEEP_LAST and c.keep_n:
            return c.keep_n
        return DEFAULT_HISTORY_ROWS

    def query_label(self) -> str:
        """Short summary of the polled selectors, e.g. ``BCM VCU:2101``.

        Reconstructs the query mini-language from the active steps (ECU name,
        with its PID list attached by a colon when filtered). Used to pre-fill
        the on-demand save dialog's label.
        """
        c = self.c
        parts: list[str] = []
        for step in c.query_steps:
            ecu = (
                ecu_index_key(step["ecu"], c._ecu_index or {}, profile=self.c.profile)
                or step["ecu"].upper()
            )
            pids = step.get("pids") or []
            parts.append(f"{ecu}:{','.join(pids)}" if pids else ecu)
        return " ".join(parts)

    def segment_title(self) -> str:
        """Display title for the current session/segment.

        The user-set label when present, else the polled-selectors summary, so
        the TUI header is meaningful even before (or without) a --save label.
        """
        return self.c.recorder.session_label or self.query_label() or "Monitor"

    def session_summary(self) -> dict:
        """A snapshot of the current run/segment for the session-info modal.

        Aggregates the run-level counters (frames, cycles, transport) and the
        active segment's metadata (label/states/notes, start time, frame count).
        Pure read — safe to call any time.
        """
        c = self.c
        r = c.recorder
        return {
            "recording": r.journal is not None,
            "label": self.segment_title(),
            "states": list(r.session_states),
            "notes": r.session_notes,
            "query": self.query_label(),
            "keep_mode": c.keep_mode,
            "keep_n": c.keep_n,
            "view_mode": c.view_mode,
            "interval": c.schedule.interval,
            "schedule": c.schedule.describe(),
            "cycle": c.cycle,
            "total_frames": r.total_frames,
            "unique_frames": r.unique_frames,
            "transport": c.transport_type,
            "captures_dir": str(c.captures_dir) if c.captures_dir else None,
            "journal_path": str(r.journal.path) if r.journal is not None else None,
            "run_started_at": r.run_started_at,
            "segment_started_at": r.segment_started_at,
            "segment_frames": r.total_frames - r.segment_frames_base,
            "completed_segments": len(r.segments),
        }
