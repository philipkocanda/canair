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
import io
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
    transport: str | None = None,
    quality: dict | None = None,
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

    session = build_query_session(
        results,
        label,
        vehicle_states,
        notes,
        keep_mode=keep_mode,
        transport=transport,
        quality=quality,
    )
    return save_session(session, captures_dir)


def _open_journal(controller, label: str | None, vehicle_states, notes: str | None):
    """Open a write-ahead capture journal for a monitor --save run/segment.

    Shared by ``mode_monitor`` (run start) and ``MonitorRecorder.new_segment``
    (segment rotate) so their open args can't drift. The recording keep-mode
    (``changes``/``unique``) is carried through from the display keep-mode; other
    modes journal every row.
    The transport label (for saved-capture provenance) comes from the controller.
    """
    from ..capture_journal import CaptureJournal

    journal_label = label or controller.query_label() or "Monitor session"
    keep = controller.keep_mode if controller.keep_mode in ("changes", "unique") else None
    return CaptureJournal.open(
        controller.captures_dir,
        label=journal_label,
        vehicle_states=list(vehicle_states or []),
        notes=notes,
        source="monitor",
        keep_mode=keep,
        transport=getattr(controller, "transport_type", None),
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
        # Every distinct state auto-suggested from decoded values across the
        # *current segment's* lifetime, insertion-ordered. The end-of-run /
        # segment-rotate back-fill uses this union (not a single point-in-time
        # snapshot) so a segment that charged then went idle is still labelled
        # `charging` — the exit snapshot alone would miss it.
        self.observed_states: dict[str, None] = {}
        # Transport-diagnostics baseline for the current segment: a snapshot of the
        # active client's .diag counts taken when the segment starts, so each
        # segment's recorded `quality` reflects only its own span (diff at
        # reconcile). None = start-of-run (baseline is zero → whole run so far).
        self._diag_base: dict[str, int] | None = None
        # Session/segment timing + history, surfaced by the TUI session-info modal.
        # run_started_at is the whole monitor run's start; segment_started_at resets
        # on each 'n' rotate; segment_frames_base is total_frames at the current
        # segment's start (so per-segment frame count = total_frames - base).
        # segments holds a summary dict of each closed segment (oldest first).
        self.run_started_at = datetime.now()
        self.segment_started_at = self.run_started_at
        self.segment_frames_base = 0
        self.segments: list[dict] = []
        # Save banners produced while the TUI owned stdout (an on-demand 's' save
        # or an 'n' segment rotate). Textual redirects stdout for the app's whole
        # lifetime, so those lines would be silently dropped; mode_monitor drains
        # this after the app (and any modal) is gone, so a save always reports the
        # file it landed in.
        self.deferred_saves: list[str] = []

    def segment_quality(self) -> dict | None:
        """Data-quality footprint for the current segment, or None if unavailable.

        The active client's exchange/error counts since :attr:`_diag_base` (set at
        segment start). Recorded onto the reconciled session so a capture carries
        the transport health it was gathered under.
        """
        diag = self._diag_recorder()
        if diag is None:
            return None
        return diag.diff(self._diag_base or {}).quality()

    def _diag_recorder(self):
        """The controller's active-client diag recorder, or None (older/fake controllers)."""
        fn = getattr(self.c, "diag_recorder", None)
        return fn() if callable(fn) else None

    def _reset_diag_base(self) -> None:
        """Snapshot the active client's diag counts as the new segment's baseline."""
        diag = self._diag_recorder()
        self._diag_base = diag.snapshot() if diag is not None else None

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
        # Accumulate the state auto-suggested from this cycle's decoded values, so
        # the segment's back-fill reflects everything it saw (not just the state
        # active at reconcile time). Cheap; only consulted when no explicit state.
        self._observe_state()

    def _observe_state(self) -> None:
        """Fold this cycle's auto-suggested state into ``observed_states``."""
        with contextlib.suppress(Exception):
            suggested = self.c.suggested_state()
            if suggested:
                self.observed_states.setdefault(suggested, None)

    def _backfill_states(self) -> list[str] | None:
        """States to back-fill the closing segment with when none was set explicitly.

        The union of everything observed over the segment's span (insertion
        order), falling back to the instantaneous auto-suggest if the accumulator
        is empty (e.g. a segment too short to complete a decode cycle). ``None``
        when nothing can be inferred, leaving the segment unlabelled.
        """
        if self.observed_states:
            return list(self.observed_states)
        with contextlib.suppress(Exception):
            suggested = self.c.suggested_state()
            if suggested:
                return [suggested]
        return None

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

    def _defer_save_output(self, buf: io.StringIO) -> None:
        """Queue save output captured while the TUI owned stdout, for post-exit printing.

        The capture writer prints its own "Saved N capture(s) to <path>" banner;
        inside the TUI that goes to Textual's redirected stdout and is discarded.
        Collecting it here lets :func:`~canlib.modes.monitor.mode_monitor` replay
        it once the screen is handed back, so an in-run save still tells the user
        where it went (including a multi-day reconcile, which writes one file per
        day and so emits several banners).
        """
        for line in buf.getvalue().splitlines():
            if line.strip():
                self.deferred_saves.append(line.rstrip())

    def drain_deferred_saves(self) -> list[str]:
        """Pop the queued save banners (see :meth:`_defer_save_output`)."""
        lines = list(self.deferred_saves)
        self.deferred_saves.clear()
        return lines

    def save_now(self, label: str, vehicle_states=None, notes: str | None = None) -> str:
        """Save the payloads captured so far (on-demand save from the TUI).

        ``vehicle_states`` may be a comma-separated string (as typed in the TUI
        dialog) or a token list; it is normalized to a list. Uses the richest
        history available — the full ``--save`` history if enabled, else the
        display (``--keep``) history, else just the latest per-PID snapshot —
        merged with the current values. Returns a one-line summary for display.
        Never writes to stdout (the TUI owns the screen); the destination banner
        is deferred to :attr:`deferred_saves` and printed after the app exits.
        """
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
            state_txt = f", state={', '.join(states)}" if states else ""
            return (
                f"Label set: {label!r}{state_txt} — recording continues "
                "(auto-saved on quit / new session)."
            )

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
        # This non-journal path draws from the display history, which is
        # globally deduped for both "changes" and "unique" modes (see observe()).
        # Persist it honestly as "unique" (global) rather than the controller's
        # nominal "changes" — run-length is only applied on the journal path.
        save_keep = "unique" if self.c.keep_mode in ("changes", "unique") else self.c.keep_mode
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            path = _write_merged(
                new_merged,
                label,
                states,
                notes or "",
                captures_dir,
                keep_mode=save_keep,
                transport=getattr(self.c, "transport_type", None),
                quality=self.segment_quality(),
            )
        self._defer_save_output(buf)
        for key, entries in merged.items():
            self._saved_counts[key] = len(entries)
        return f"Saved {n_payloads} payload(s) across {n_pids} PID(s) → {path.name}"

    def new_segment(self, label: str, vehicle_states=None, notes: str | None = None) -> str:
        """Close the current --save segment and start a fresh one (journal rotate).

        Reconciles the current journal into a capture file, then opens a new empty
        journal carrying the provided label/states/notes. One monitor run can thus
        produce several independently-labelled sessions. A no-op (with a message)
        when --save is off, since there is no journal to rotate. Returns a one-line
        summary; never writes to stdout (the TUI owns the screen) — the destination
        banner is deferred to :attr:`deferred_saves` and printed after the app exits.
        """
        from ..states import parse_states

        if self.journal is None:
            return "Start a new session needs --save (nothing is being recorded)."

        states = parse_states(vehicle_states)

        # Give the closing segment its states when none were set explicitly: the
        # union of everything auto-suggested across the segment span (mirroring the
        # end-of-run reconcile in mode_monitor), not just the state active now.
        closing_states = list(self.session_states)
        if not self.state_explicit:
            backfill = self._backfill_states()
            if backfill:
                closing_states = backfill
                with contextlib.suppress(Exception):
                    self.journal.update_meta(vehicle_states=backfill)

        # Stamp the closing segment with its transport data-quality footprint.
        with contextlib.suppress(Exception):
            quality = self.segment_quality()
            if quality is not None:
                self.journal.update_meta(quality=quality)

        written = None
        buf = io.StringIO()
        with contextlib.suppress(Exception), contextlib.redirect_stdout(buf):
            written = self.journal.reconcile()
        self._defer_save_output(buf)

        # Record the just-closed segment's summary for the session-info modal
        # before the metadata is reset for the fresh segment.
        self._record_closed_segment(closing_states, written)

        self.journal = _open_journal(self.c, label, states, notes)
        self.state_explicit = bool(states)
        # Fresh segment: reset the observed-state accumulator so it doesn't carry
        # the previous segment's states into this one's back-fill, and rebase the
        # diag baseline so the new segment's quality counts only its own span.
        self.observed_states = {}
        self._reset_diag_base()
        # Reset per-segment timing/frame baseline for the new segment.
        self.segment_started_at = datetime.now()
        self.segment_frames_base = self.total_frames
        # Reset the display metadata to the new segment's (label always set here).
        self.session_label = label or ""
        self.session_states = list(states or [])
        self.session_notes = notes or ""

        if written is not None:
            return f"Session saved → {written.name}. Now recording {label!r}."
        return f"Now recording new session {label!r}."

    def _record_closed_segment(self, states: list[str], written) -> None:
        """Append a summary of the segment being closed to :attr:`segments`."""
        self.segments.append(
            {
                "label": self.session_label or self.c.query_label() or "Monitor",
                "states": list(states),
                "notes": self.session_notes,
                "started_at": self.segment_started_at,
                "ended_at": datetime.now(),
                "frames": self.total_frames - self.segment_frames_base,
                "written": written.name if written is not None else None,
            }
        )
