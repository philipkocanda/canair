"""Live monitor — capture recording / journaling backend.

:class:`MonitorRecorder` is the collaborator that owns the monitor's *durability*
concern: per-cycle payload recording (frame accounting + display/`--save`
history), the write-ahead capture journal, on-demand (`s`) saves, segment rotation
(`n`), and the current segment's label/state/notes metadata.

Factored out of :class:`canlib.modes.monitor.MonitorController` (mirrors
:class:`canlib.modes.monitor_raw.MonitorRawPoller` and
:class:`canlib.modes.monitor_edit.MonitorEditor`) so the recording/journaling
logic is a self-contained, independently testable unit rather than another arm of
the controller god object. The controller keeps thin delegating
properties/methods for the tested public surface and holds the poll/display state
(``prev_hex``/``decoded_values``) the recorder reads via its ``self.c`` back-ref.
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .monitor import MonitorController
    from .multi_batch import EcuFrame


def _merge_history(
    hex_history: dict[tuple[str, str], list[tuple[str, str]]],
    prev_hex: dict[tuple[str, str], str],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Merge the latest ``prev_hex`` snapshot into the payload history.

    Returns a ``{(ecu_label, pid): [(hex, timestamp), ...]}`` map. A PID whose
    current payload isn't already the last history entry gets it appended with a
    fresh timestamp, so a bare snapshot (no history kept) still yields one row
    per PID.
    """
    all_keys = set(hex_history.keys()) | set(prev_hex.keys())
    merged: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key in all_keys:
        entries = list(hex_history.get(key, []))
        cur = prev_hex.get(key, "")
        if cur and cur not in [h for h, _ts in entries]:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            entries.append((cur, ts))
        if entries:
            merged[key] = entries
    return merged


def _write_merged(
    merged: dict[tuple[str, str], list[tuple[str, str]]],
    label: str,
    vehicle_states,
    notes: str,
    captures_dir: Path,
    keep_mode: str | None = None,
) -> Path:
    """Build a query-capture session from merged payloads and save it to disk.

    The ECU label (e.g. "BMS") is resolved to its CAN response address; an
    unknown label falls back to its leading token verbatim.
    """
    from ..captures import build_query_session, save_session
    from ..ecus import build_name_tx_index, rx_from_name

    name_index = build_name_tx_index()
    results: list[tuple[str, str, str, str]] = []
    for (ecu_label, pid), entries in sorted(merged.items()):
        m = re.match(r"(\w+)", ecu_label)
        assert m is not None  # ecu_label is an ECU name/label, always starts with a word char
        ecu_short = m.group(1)
        ecu_ref = rx_from_name(ecu_short, name_index) or ecu_short
        for hex_val, ts in entries:
            results.append((ecu_ref, pid, hex_val, ts))

    session = build_query_session(results, label, vehicle_states, notes, keep_mode=keep_mode)
    return save_session(session, captures_dir)


def _open_journal(controller, label: str | None, vehicle_states, notes: str | None):
    """Open a write-ahead capture journal for a monitor --save run/segment.

    Shared by ``mode_monitor`` (run start) and ``MonitorRecorder.new_segment``
    (segment rotate) so their open args can't drift. ``keep_mode="unique"`` is
    carried through from the display keep-mode; other modes journal every row.
    """
    from ..capture_journal import CaptureJournal

    journal_label = label or controller.query_label() or "Monitor session"
    keep = "unique" if controller.keep_mode == "unique" else None
    return CaptureJournal.open(
        controller.captures_dir,
        label=journal_label,
        vehicle_states=list(vehicle_states or []),
        notes=notes,
        source="monitor",
        keep_mode=keep,
    )


class MonitorRecorder:
    """Owns the monitor's capture recording, journaling, and save/segment logic.

    Constructed with a back-reference to the :class:`MonitorController`
    (``self.c``), which it reads for poll/display state (``prev_hex``,
    ``keep_mode``/``keep_n``, ``captures_dir``, ``query_label``,
    ``suggested_state``, ``_ecu_ref``) and updates in place while recording.
    """

    def __init__(self, controller: MonitorController) -> None:
        self.c = controller
        # Frame accounting (surfaced in the status line). total_frames counts every
        # fresh (non-stale) payload received across all cycles — the "captured"
        # figure; unique_frames counts distinct (ecu, pid, payload) values seen,
        # which is what a keep:unique session actually stores/displays.
        self.total_frames = 0
        self.unique_frames = 0
        self._seen_payloads: set[tuple[tuple[str, str], str]] = set()
        self.hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = (
            {} if controller.keep_mode else None
        )
        self.save_history: dict[tuple[str, str], list[tuple[str, str]]] | None = (
            {} if controller.save else None
        )
        # High-water mark of payloads already written by a non-journal on-demand
        # save (the fallback --save-off path), per PID key. Repeated 's' presses
        # only write rows beyond this, so a payload is never saved twice in one run.
        self._saved_counts: dict[tuple[str, str], int] = {}
        # Current --save segment metadata (label/states/notes), surfaced by the
        # TUI header. Kept in sync as the initial journal is opened and whenever
        # the user edits (`s`) or rotates a segment (`n`); the journal itself only
        # keeps these in its write-ahead log, so we mirror them here for display.
        self.session_label = ""
        self.session_notes = ""
        self.session_states: list[str] = []
        # Write-ahead journal (durability): when --save is on, every polled payload
        # is appended here as it arrives and reconciled into a capture file on exit.
        # Set by mode_monitor. A dropped connection or crash leaves the journal on
        # disk for `canair captures uds --recover`.
        self.journal = None
        # True once the user sets a non-empty state via the TUI save dialog, so the
        # end-of-run auto-suggest fallback doesn't clobber their choice.
        self.state_explicit = False

    def observe(self, new_queries: list[EcuFrame]) -> None:
        """Record freshly-polled payloads: update ``prev_hex`` + histories/journal.

        The durability half of the controller's ``_record`` (the controller keeps
        the ``decoded_values``/``prev_snapshot`` display half). Stale re-shown
        values and empty payloads are skipped.
        """
        c = self.c
        for ecu_label, pid_results in new_queries:
            for entry in pid_results:
                if entry.get("stale"):
                    continue  # a re-shown last-good value on timeout — not fresh data
                raw = entry.get("raw_hex", "")
                if not raw:
                    continue
                key = (ecu_label, entry["pid"])
                c.prev_hex[key] = raw
                # Frame accounting: every fresh payload is a captured frame; track
                # distinct (key, payload) so the status line can show captured vs
                # unique (which is what keep:unique stores).
                self.total_frames += 1
                sig = (key, raw)
                if sig not in self._seen_payloads:
                    self._seen_payloads.add(sig)
                    self.unique_frames += 1
                # Per-PID acquisition timestamp (moment the response arrived),
                # millisecond precision, so sequentially-polled PIDs keep skew.
                # Also carry the acquisition date so a monitor session crossing
                # midnight reconciles into the correct per-day capture files.
                acq = entry.get("acquired_at")
                dt = datetime.fromtimestamp(acq) if acq else datetime.now()
                ts = dt.strftime("%H:%M:%S.%f")[:-3]
                ts_date = dt.strftime("%Y-%m-%d")
                if self.save_history is not None:  # --save
                    if self.journal is not None:
                        # Journaled: the write-ahead log is the source of truth on
                        # exit, so skip the redundant in-memory save_history growth.
                        with contextlib.suppress(Exception):
                            self.journal.append(
                                c._ecu_ref(ecu_label), entry["pid"], raw, ts, ts_date
                            )
                    else:
                        self.save_history.setdefault(key, []).append((raw, ts))
                if self.hex_history is not None:  # --keep display history
                    if c.keep_mode in ("all", "last"):
                        self.hex_history.setdefault(key, []).append((raw, ts))
                        if (
                            c.keep_mode == "last"
                            and c.keep_n
                            and len(self.hex_history[key]) > c.keep_n
                        ):
                            self.hex_history[key] = self.hex_history[key][-c.keep_n :]
                    else:  # "unique": store only if not seen before
                        existing = [h for h, _ts in self.hex_history.get(key, [])]
                        if raw not in existing:
                            self.hex_history.setdefault(key, []).append((raw, ts))
        # One durable flush per cycle instead of an fsync per payload — keeps the
        # poll loop (and TUI) off N serial fsync syscalls when saving many PIDs.
        if self.journal is not None:
            with contextlib.suppress(Exception):
                self.journal.flush()

    def open_journal(self, label: str | None, vehicle_states, notes: str | None):
        """Open (and store) the write-ahead journal for the current run/segment."""
        self.journal = _open_journal(self.c, label, vehicle_states, notes)
        return self.journal

    def has_captures(self) -> bool:
        """True when there's at least one payload available to save."""
        history = self.save_history if self.save_history is not None else (self.hex_history or {})
        return bool(history) or bool(self.c.prev_hex)

    def set_segment_meta(
        self, label: str | None, states: list[str] | None, notes: str | None
    ) -> None:
        """Mirror the journal's last-wins metadata onto the display fields."""
        if label:
            self.session_label = label
        if states:
            self.session_states = list(states)
        if notes is not None:
            self.session_notes = notes

    def save_now(self, label: str, vehicle_states=None, notes: str | None = None) -> str:
        """Save the payloads captured so far (on-demand save from the TUI).

        ``vehicle_states`` may be a comma-separated string (as typed in the TUI
        dialog) or a token list; it is normalized to a list. Uses the richest
        history available — the full ``--save`` history if enabled, else the
        display (``--keep``) history, else just the latest per-PID snapshot —
        merged with the current values. Returns a one-line summary for display.
        Never writes to stdout (the TUI owns the screen).
        """
        import io

        from ..states import parse_states

        states = parse_states(vehicle_states)

        # Journal active (--save): payloads are already durably journaled and
        # reconciled on exit. The on-demand save just updates the metadata that
        # the reconciled session will carry (label/states/notes), applied live.
        if self.journal is not None:
            with contextlib.suppress(Exception):
                self.journal.update_meta(label, states, notes)
            self.set_segment_meta(label, states, notes)
            if states:
                self.state_explicit = True
            return f"Metadata set (label={label!r}); session auto-saves on exit."

        history = self.save_history if self.save_history is not None else (self.hex_history or {})
        merged = _merge_history(history, self.c.prev_hex)
        if not merged:
            return "No payloads captured yet — nothing to save."

        # Only write rows that appeared since the last on-demand save. Repeated
        # 's' presses re-merge the full history, so without this a payload would
        # be written once per press. The high-water mark is per PID; advance it
        # by the number of rows written so the next press starts after them.
        new_merged: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for key, entries in merged.items():
            already = self._saved_counts.get(key, 0)
            fresh = entries[already:]
            if fresh:
                new_merged[key] = fresh
        if not new_merged:
            return "Nothing new since last save."

        captures_dir = self.c.captures_dir
        if captures_dir is None:
            from ..profile import active

            captures_dir = active().captures_dir

        n_pids = len(new_merged)
        n_payloads = sum(len(v) for v in new_merged.values())
        with contextlib.redirect_stdout(io.StringIO()):
            path = _write_merged(
                new_merged, label, states, notes or "", captures_dir, keep_mode=self.c.keep_mode
            )
        for key, entries in merged.items():
            self._saved_counts[key] = len(entries)
        return f"Saved {n_payloads} payload(s) across {n_pids} PID(s) → {path.name}"

    def new_segment(self, label: str, vehicle_states=None, notes: str | None = None) -> str:
        """Close the current --save segment and start a fresh one (journal rotate).

        Reconciles the current journal into a capture file, then opens a new empty
        journal carrying the provided label/states/notes. One monitor run can thus
        produce several independently-labelled sessions. A no-op (with a message)
        when --save is off, since there is no journal to rotate. Returns a one-line
        summary; never writes to stdout (the TUI owns the screen).
        """
        from ..states import parse_states

        if self.journal is None:
            return "New segment requires --save (nothing is being recorded)."

        states = parse_states(vehicle_states)

        # Give the closing segment its auto-suggested state when none was set
        # explicitly, mirroring the end-of-run reconcile in mode_monitor.
        if not self.state_explicit:
            with contextlib.suppress(Exception):
                suggested = self.c.suggested_state()
                if suggested:
                    self.journal.update_meta(vehicle_states=[suggested])

        written = None
        with contextlib.suppress(Exception):
            written = self.journal.reconcile()

        self.journal = _open_journal(self.c, label, states, notes)
        self.state_explicit = bool(states)
        # Reset the display metadata to the new segment's (label always set here).
        self.session_label = label or ""
        self.session_states = list(states or [])
        self.session_notes = notes or ""

        if written is not None:
            return f"Segment saved → {written.name}; recording new segment ({label!r})."
        return f"Recording new segment ({label!r})."
