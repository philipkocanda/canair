"""Live monitor mode — repeatedly polls a set of ECU PIDs and refreshes the display.

On a TTY this runs a Textual app (:mod:`canlib.modes._monitor_tui`): the latest
values render into a widget that updates *in place* inside a scrollable
container, so the scroll position stays put while values refresh — mouse wheel,
scrollbar and keys all scroll natively and nothing ever freezes. When stdout is
not a TTY (piped/scripted) it polls silently until Ctrl+C and prints the final
values.

Usage (via canair query --monitor):
    canair query "session BCM --wake" "query BCM:C00B,B00E" --monitor
    canair query "query BMS:2101" --monitor 2.0
    canair query "session IGPM --wake" "query IGPM:BC03,BC06" --monitor

The --monitor flag applies to the last 'query' step in the pipeline. If
there are multiple query steps, all of them are repeated each cycle.

The polling / decoding / capture-saving logic lives in :class:`MonitorController`
(reused by both the TUI and the non-interactive path); only the presentation
layer differs.
"""

import asyncio
import contextlib
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.text import Text

from ..formatting import (
    _HIGHLIGHT_STYLE,
    _bytes_to_ascii,
    _render_hex_line,
)
from ..session_manager import SessionManager
from ._monitor_render import _RENDER_MAX_ROWS, _render_results
from .monitor_raw import MonitorRawPoller, _raw_pid_result

# _HIGHLIGHT_STYLE, _bytes_to_ascii and _render_hex_line moved to canlib.formatting;
# _render_results/_RENDER_MAX_ROWS to _monitor_render; _raw_pid_result to monitor_raw.
# All re-exported here for backward-compatible imports (e.g. tests/test_monitor.py).
__all__ = [
    "_HIGHLIGHT_STYLE",
    "_RENDER_MAX_ROWS",
    "MonitorController",
    "_bytes_to_ascii",
    "_raw_pid_result",
    "_render_hex_line",
    "_render_results",
    "mode_monitor",
    "query_ecu_error",
]

_console = Console(highlight=False)


def query_ecu_error(query_steps: list[dict], pids_data: dict) -> str | None:
    """Return an error message if any query step names an unknown ECU, else None.

    Guards against typos like ``query ESC ECS`` (the second selector is a
    non-existent ECU) that would otherwise be silently skipped every poll cycle.
    ECU names are matched case-insensitively against the active profile.
    """
    from ..ecus import build_canonical_name_index, canonical_ecu_name_safe
    from ..pids import build_ecu_index

    ecu_index = build_ecu_index(pids_data)
    try:
        name_index = build_canonical_name_index()
    except FileNotFoundError:
        name_index = None
    seen: set[str] = set()
    unknown: list[str] = []
    for step in query_steps:
        key = canonical_ecu_name_safe(step["ecu"], name_index).upper()
        if key not in ecu_index and key not in seen:
            seen.add(key)
            unknown.append(step["ecu"])
    if not unknown:
        return None
    available = ", ".join(sorted(ecu_index.keys()))
    return f"unknown ECU(s) in query: {', '.join(unknown)}.\n  Available ECUs: {available}"


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

    Shared by ``mode_monitor`` (run start) and ``MonitorController.new_segment``
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


class MonitorController:
    """Polls a set of ECU PIDs on an interval and renders/records the results.

    Holds all monitor state and the CAN-facing logic (session setup, polling,
    history bookkeeping, capture saving). The presentation layer — the Textual
    TUI or the non-interactive fallback — drives it via :meth:`poll_once` and
    :meth:`render`, so the two share identical behaviour.
    """

    def __init__(
        self,
        terminal,
        query_steps: list[dict],
        pids_data: dict,
        verbose: bool,
        interval: float = 5.0,
        keep_mode: str | None = None,
        keep_n: int | None = None,
        save: bool = False,
        show_rulers: bool = False,
        raw_client=None,
        include_static: bool = False,
    ):
        self.terminal = terminal
        self.raw_client = raw_client  # transport.RawUdsClient when using the raw backend
        self.raw = raw_client is not None
        self.query_steps = query_steps
        self.pids_data = pids_data
        self.verbose = verbose
        self.interval = interval
        self.keep_mode = keep_mode
        self.keep_n = keep_n
        self.save = save
        self.show_rulers = show_rulers
        self.include_static = include_static

        self.sm = SessionManager(terminal, verbose=verbose) if not self.raw else None
        self._ecu_index: dict | None = None
        self._batch_state = None  # multi.BatchState, created in setup()
        # Raw-backend poll cycle + multi-DID batching state lives in the poller.
        self.raw_poller = MonitorRawPoller(self)
        # Incremental rendering: a hook the TUI sets to refresh the body mid-cycle
        # so a slow/timing-out PID doesn't freeze the whole view. Left None for
        # the non-interactive path (results are only rendered once, on exit).
        self._on_partial = None
        # Last *good* entry seen per (ecu_label, pid). A PID that times out reuses
        # this (marked stale → dimmed) so its values stay on screen instead of
        # collapsing to an error line and jolting the layout; a pending PID (raw,
        # mid-cycle) shows it too until its own result lands.
        self._last_good: dict[tuple[str, str], dict] = {}

        # Live state (read by the renderer).
        self.cycle = 0
        self.elapsed = 0.0
        self.last_cmds = 0  # ELM commands issued during the last poll cycle
        self.last_elm_time = 0.0  # seconds spent in ELM commands last cycle
        self.last_queries: list[tuple[str, list]] = []
        self.prev_hex: dict[tuple[str, str], str] = {}
        # Payloads as of the *previous* poll cycle, snapshotted before prev_hex is
        # overwritten each cycle. Rendering diffs against this so byte-level change
        # highlighting works in the single-frame view too (prev_hex already holds
        # the freshly-recorded current payload by render time).
        self.prev_snapshot: dict[tuple[str, str], str] = {}
        # Where on-demand ('s' key in the TUI) / end-of-run captures are written.
        # Set by mode_monitor; resolved lazily if left None.
        self.captures_dir: Path | None = None
        self.hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = (
            {} if keep_mode else None
        )
        self.save_history: dict[tuple[str, str], list[tuple[str, str]]] | None = (
            {} if save else None
        )
        # High-water mark of payloads already written by a non-journal on-demand
        # save (the fallback --save-off path), per PID key. Repeated 's' presses
        # only write rows beyond this, so a payload is never saved twice in one run.
        self._saved_counts: dict[tuple[str, str], int] = {}
        self.disconnected = False
        # Write-ahead journal (durability): when --save is on, every polled
        # payload is appended here as it arrives and reconciled into a capture
        # file on exit. Set by mode_monitor. A dropped connection or crash leaves
        # the journal on disk for `canair captures uds --recover`.
        self.journal = None
        self._name_index: dict | None = None
        # Auto-suggest state: latest decoded {ECU.PARAM: value} + responded ECUs,
        # evaluated against the profile's states.yaml rules (lazy-loaded).
        self.decoded_values: dict[str, float] = {}
        self.responded: set[str] = set()
        self._state_rules: list | None = None
        # True once the user sets a non-empty state via the TUI save dialog, so
        # the end-of-run auto-suggest fallback doesn't clobber their choice.
        self._state_explicit = False
        # In-place editing / filtering of PID definitions from the TUI. The
        # editor owns the selection cursor + display filter and writes edits
        # through canlib.pids_edit; this controller reloads on its request.
        from .monitor_edit import MonitorEditor

        self.pids_dir = None  # None -> active profile's ecus/ (tests override)
        self.editor = MonitorEditor(self)

    def reload_pids(self) -> None:
        """Re-read PID definitions after an in-place edit and rebuild the index.

        ``canlib.pids_edit`` clears the memoized load on every write, so this
        picks up the just-edited expression/flags; the rebuilt ECU index makes
        the next poll decode with the new definition.
        """
        from ..pids import build_ecu_index, load_pids

        self.pids_data = load_pids(self.pids_dir)
        self._ecu_index = build_ecu_index(self.pids_data)

    async def setup(self, session_steps: list[dict] | None) -> None:
        """Build the ECU index, run one-shot session setup, start keepalives."""
        from ..pids import build_ecu_index

        self._ecu_index = build_ecu_index(self.pids_data)

        if self.raw:
            # Raw backend: no ELM sessions/keepalive. Sessions (10 03) for ECUs
            # that need them are opened best-effort before polling.
            from .multi import build_query_plan

            assert self.raw_client is not None  # self.raw is True ⟺ raw_client was provided
            for step in session_steps or []:
                if step["type"] == "session":
                    tgt = step["target"].upper()
                    if tgt in self._ecu_index:
                        with contextlib.suppress(Exception):
                            self.raw_client.read(tgt, bytes.fromhex("1003"), timeout=1.0)
            # Warm each ECU up: the first diagnostic request after idle is slow
            # (the ECU/gateway has to wake). Prime with one throwaway read per ECU
            # on a longer timeout so the first *monitored* cycle is already warm.
            for step in self.query_steps:
                info = self._ecu_index.get(step["ecu"].upper())
                if not info:
                    continue
                plan = (
                    build_query_plan(
                        info, step.get("pids", []), quiet=True, include_static=self.include_static
                    )
                    or []
                )
                if plan:
                    with contextlib.suppress(Exception):
                        self.raw_client.read(
                            step["ecu"].upper(), bytes.fromhex(plan[0][0]), timeout=3.0
                        )
            return

        from .multi import _exec_session, _exec_skm_wake, build_query_plan
        from .multi_batch import BatchState

        assert self.sm is not None  # not self.raw ⟺ sm was constructed
        self._batch_state = BatchState()
        for step in session_steps or []:
            stype = step["type"]
            if stype == "skm-wake":
                print(f"  SKM wakeup ({step['level']})...")
                await _exec_skm_wake(self.sm, step["level"], self.verbose)
            elif stype == "session":
                print(f"  Opening session on {step['target']}...")
                await _exec_session(
                    self.sm, step["target"], step.get("wake", False), self._ecu_index
                )
        self.sm.start_background_keepalive(interval=2.0)
        # Prime each ECU once (parity with the raw path): the first request after
        # idle is slow, so warm the path before the first *displayed* cycle. The
        # retry (retries=1) also rides out a cold NO-DATA on this throwaway read.
        for step in self.query_steps:
            info = self._ecu_index.get(step["ecu"].upper())
            if not info:
                continue
            plan = (
                build_query_plan(
                    info, step.get("pids", []), quiet=True, include_static=self.include_static
                )
                or []
            )
            if plan:
                with contextlib.suppress(Exception):
                    await self.sm.terminal.set_header(info["tx_id"])
                    await self.sm.terminal.send_uds(plan[0][0], retries=1)

    def _record(self, new_queries: list[tuple[str, list]]) -> None:
        """Record freshly-polled payloads into prev_hex / display / save history."""
        # Refresh the decoded-value snapshot used for state auto-suggestion.
        from ..states import collect_values

        values, responded = collect_values(new_queries)
        self.decoded_values.update(values)
        self.responded |= responded
        # Snapshot the prior cycle's payloads before overwriting them, so the
        # renderer can diff current-vs-previous (prev_hex is about to become the
        # current values).
        self.prev_snapshot = dict(self.prev_hex)
        for ecu_label, pid_results in new_queries:
            for entry in pid_results:
                if entry.get("stale"):
                    continue  # a re-shown last-good value on timeout — not fresh data
                raw = entry.get("raw_hex", "")
                if not raw:
                    continue
                key = (ecu_label, entry["pid"])
                self.prev_hex[key] = raw
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
                                self._ecu_ref(ecu_label), entry["pid"], raw, ts, ts_date
                            )
                    else:
                        self.save_history.setdefault(key, []).append((raw, ts))
                if self.hex_history is not None:  # --keep display history
                    if self.keep_mode in ("all", "last"):
                        self.hex_history.setdefault(key, []).append((raw, ts))
                        if (
                            self.keep_mode == "last"
                            and self.keep_n
                            and len(self.hex_history[key]) > self.keep_n
                        ):
                            self.hex_history[key] = self.hex_history[key][-self.keep_n :]
                    else:  # "unique": store only if not seen before
                        existing = [h for h, _ts in self.hex_history.get(key, [])]
                        if raw not in existing:
                            self.hex_history.setdefault(key, []).append((raw, ts))
        # One durable flush per cycle instead of an fsync per payload — keeps the
        # poll loop (and TUI) off N serial fsync syscalls when saving many PIDs.
        if self.journal is not None:
            with contextlib.suppress(Exception):
                self.journal.flush()

    async def poll_once(self) -> None:
        """Run every query step once, updating live state. Sets ``disconnected``."""
        self.cycle += 1
        t0 = time.monotonic()
        if self.raw:
            await self._poll_raw()
        else:
            await self._poll_elm()
        self.elapsed = time.monotonic() - t0
        self._record(self.last_queries)

    async def _poll_elm(self) -> None:
        import time as _t

        from .multi import _exec_query

        # The ELM poll path only runs on the non-raw backend, where setup() has
        # constructed sm and built the ECU index.
        assert self.sm is not None
        assert self._ecu_index is not None
        cmds0 = self.terminal.cmd_count
        elm0 = self.terminal.cmd_time
        # Seed the frame from last cycle so ECUs not yet re-polled keep showing
        # their values (no flicker); overwrite each as it completes this cycle.
        frame = dict(self.last_queries)
        order = [label for label, _ in self.last_queries]
        new_queries: list[tuple[str, list]] = []
        last_render = 0.0
        for step in self.query_steps:
            try:
                result = await _exec_query(
                    self.sm,
                    step["ecu"],
                    step.get("pids", []),
                    self._ecu_index,
                    self.pids_data,
                    self.verbose,
                    return_results=True,
                    quiet=True,
                    batch_state=self._batch_state,
                    include_static=self.include_static,
                )
            except ConnectionError:
                self.disconnected = True
                return
            if result is not None:
                label, pids = result
                # Keep a timed-out PID's last-good values (stale/dimmed) instead
                # of collapsing to an error line and jolting the layout.
                pids = [self._displayify((label, e["pid"]), e) for e in pids]
                result = (label, pids)
                new_queries.append(result)
                if label not in frame:
                    order.append(label)
                frame[label] = pids
                self.last_queries = [(lb, frame[lb]) for lb in order if lb in frame]
                now = _t.monotonic()
                if self._on_partial is not None and (now - last_render) >= 0.12:
                    last_render = now
                    with contextlib.suppress(Exception):
                        self._on_partial()
        self.last_queries = new_queries
        self.last_cmds = self.terminal.cmd_count - cmds0
        self.last_elm_time = self.terminal.cmd_time - elm0

    # --- Raw-CAN backend: delegated to the MonitorRawPoller collaborator. The
    # per-session batch state (_raw_lengths / _raw_nobatch) lives on the poller;
    # these facades keep the tested public surface stable. See monitor_raw.py.

    @property
    def _raw_lengths(self) -> dict[tuple[str, str], int]:
        return self.raw_poller.lengths

    @property
    def _raw_nobatch(self) -> set[str]:
        return self.raw_poller.nobatch

    def _build_raw_submissions(self):
        return self.raw_poller.build_submissions()

    async def _poll_raw(self) -> None:
        await self.raw_poller.poll()

    def _apply_raw_submission(self, s: dict, val, acquired: float, by_pid: dict) -> None:
        self.raw_poller.apply_submission(s, val, acquired, by_pid)

    def _displayify(self, key: tuple[str, str], entry: dict) -> dict:
        """Map a freshly-resolved entry to what should be shown for that PID.

        - good (has ``raw_hex``): remembered as the last-good, shown as-is.
        - a real negative response (``NRC …``): shown as-is (honest).
        - a timeout / no-data / transport error: reuse the last-good entry marked
          ``stale`` (dimmed on screen) so its parameters stay put and the layout
          doesn't jump; if we never had good data, show the error.
        """
        if entry.get("raw_hex"):
            self._last_good[key] = entry
            return entry
        if str(entry.get("error", "")).startswith("NRC"):
            return entry
        last = self._last_good.get(key)
        if last is not None:
            return {**last, "stale": True}
        return entry

    def _raw_build_queries(self, plan_by_ecu, by_pid: dict) -> list[tuple[str, list]]:
        return self.raw_poller.build_queries(plan_by_ecu, by_pid)

    def render(self) -> Text:
        """The current view as a Rich Text (rendered by the TUI / printed on exit)."""
        self.editor.ensure_valid(self.last_queries)
        return _render_results(
            self.editor.visible_queries(self.last_queries),
            self.verbose,
            self.cycle,
            self.elapsed,
            self.interval,
            self.prev_snapshot,
            self.hex_history,
            show_rulers=self.show_rulers,
            footer=False,
            selected=self.editor.selected,
        )

    def has_captures(self) -> bool:
        """True when there's at least one payload available to save."""
        history = self.save_history if self.save_history is not None else (self.hex_history or {})
        return bool(history) or bool(self.prev_hex)

    def _ecu_ref(self, ecu_label: str) -> str:
        """Resolve a monitor ECU label (e.g. "BMS") to its CAN response address.

        Falls back to the label's leading token when it isn't a known ECU name.
        Caches the name→TX index for repeated calls during polling.
        """
        from ..ecus import build_name_tx_index, rx_from_name

        if self._name_index is None:
            self._name_index = build_name_tx_index()
        m = re.match(r"(\w+)", ecu_label)
        assert m is not None  # ecu_label is an ECU name/label, always starts with a word char
        ecu_short = m.group(1)
        return rx_from_name(ecu_short, self._name_index) or ecu_short

    def query_label(self) -> str:
        """Short summary of the polled selectors, e.g. ``BCM VCU:2101``.

        Reconstructs the query mini-language from the active steps (ECU name,
        with its PID list attached by a colon when filtered). Used to pre-fill
        the on-demand save dialog's label.
        """
        parts: list[str] = []
        for step in self.query_steps:
            ecu = step["ecu"].upper()
            pids = step.get("pids") or []
            parts.append(f"{ecu}:{','.join(pids)}" if pids else ecu)
        return " ".join(parts)

    def suggested_state(self) -> str | None:
        """Auto-suggest the vehicle state from the latest decoded values.

        Evaluates the active profile's states.yaml rules against the accumulated
        ``decoded_values``/``responded`` snapshot. Returns None when no rule
        matches or the profile declares no states.
        """
        from ..states import StatePredicateError, load_states, suggest_state

        if self._state_rules is None:
            try:
                self._state_rules = load_states()
            except StatePredicateError:
                self._state_rules = []
        if not self._state_rules:
            return None
        return suggest_state(self._state_rules, self.decoded_values, self.responded)

    def state_options(self) -> list[tuple[str, str]]:
        """The profile's ordered ``(state, description)`` vocabulary for the save dialog."""
        from ..states import state_options

        try:
            return state_options()
        except Exception:
            return []

    def save_now(self, label: str, vehicle_states=None, notes: str | None = None) -> str:
        """Save the payloads captured so far (on-demand save from the TUI).

        ``vehicle_states`` may be a comma-separated string (as typed in the TUI
        dialog) or a token list; it is normalized to a list. Uses the richest
        history available — the full ``--save`` history if enabled, else the
        display (``--keep``) history, else just the latest per-PID snapshot —
        merged with the current values. Returns a one-line summary for display.
        Never writes to stdout (the TUI owns the screen).
        """
        import contextlib
        import io

        from ..states import parse_states

        states = parse_states(vehicle_states)

        # Journal active (--save): payloads are already durably journaled and
        # reconciled on exit. The on-demand save just updates the metadata that
        # the reconciled session will carry (label/states/notes), applied live.
        if self.journal is not None:
            with contextlib.suppress(Exception):
                self.journal.update_meta(label, states, notes)
            if states:
                self._state_explicit = True
            return f"Metadata set (label={label!r}); session auto-saves on exit."

        history = self.save_history if self.save_history is not None else (self.hex_history or {})
        merged = _merge_history(history, self.prev_hex)
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

        captures_dir = self.captures_dir
        if captures_dir is None:
            from ..profile import active

            captures_dir = active().captures_dir

        n_pids = len(new_merged)
        n_payloads = sum(len(v) for v in new_merged.values())
        with contextlib.redirect_stdout(io.StringIO()):
            path = _write_merged(
                new_merged, label, states, notes or "", captures_dir, keep_mode=self.keep_mode
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
        import contextlib

        from ..states import parse_states

        if self.journal is None:
            return "New segment requires --save (nothing is being recorded)."

        states = parse_states(vehicle_states)

        # Give the closing segment its auto-suggested state when none was set
        # explicitly, mirroring the end-of-run reconcile in mode_monitor.
        if not self._state_explicit:
            with contextlib.suppress(Exception):
                suggested = self.suggested_state()
                if suggested:
                    self.journal.update_meta(vehicle_states=[suggested])

        written = None
        with contextlib.suppress(Exception):
            written = self.journal.reconcile()

        self.journal = _open_journal(self, label, states, notes)
        self._state_explicit = bool(states)

        if written is not None:
            return f"Segment saved → {written.name}; recording new segment ({label!r})."
        return f"Recording new segment ({label!r})."

    async def close(self) -> None:
        """Stop keepalives and close all open sessions / the raw client (best-effort)."""
        if self.raw:
            print("  Closing raw CAN client...")
            assert self.raw_client is not None  # self.raw is True ⟺ raw_client was provided
            with contextlib.suppress(Exception):
                self.raw_client.close()
            return
        assert self.sm is not None  # not self.raw ⟺ sm was constructed
        self.sm.stop_background_keepalive()
        print("  Closing sessions...")
        try:
            await asyncio.wait_for(self.sm.close_all(), timeout=3.0)
        except (TimeoutError, Exception):
            pass


async def _monitor_noninteractive(controller: MonitorController) -> None:
    """No TTY: poll silently until SIGINT/disconnect (piped/scripted runs)."""
    stop_flag = {"v": False}

    def _handle_sigint(_sig, _frame):
        stop_flag["v"] = True

    old_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        while not stop_flag["v"] and not controller.disconnected:
            t0 = time.monotonic()
            await controller.poll_once()
            if controller.disconnected:
                return
            remaining = controller.interval - (time.monotonic() - t0)
            while remaining > 0 and not stop_flag["v"] and not controller.disconnected:
                await asyncio.sleep(min(remaining, 0.1))
                remaining = controller.interval - (time.monotonic() - t0)
    finally:
        signal.signal(signal.SIGINT, old_handler)


async def mode_monitor(
    terminal,
    query_steps: list[dict],
    pids_data: dict,
    verbose: bool,
    interval: float = 5.0,
    session_steps: list[dict] | None = None,
    keep_mode: str | None = None,
    keep_n: int | None = None,
    save: bool = False,
    show_rulers: bool = False,
    label: str | None = None,
    vehicle_states=None,
    notes: str | None = None,
    raw_client=None,
    include_static: bool = False,
):
    """Live-refresh ECU parameter monitor.

    On a TTY this launches the Textual monitor app (scrollable, in-place value
    updates, mouse + keyboard). Otherwise it polls silently until Ctrl+C and
    prints the final values. Sessions are opened once (from session_steps) and
    kept alive with background keepalives.

    Args:
        terminal:       Connected WiCANTerminal.
        query_steps:    list of {'type': 'query', 'ecu': ..., 'pids': [...]} dicts.
        pids_data:      Loaded PID definitions.
        verbose:        Show expressions.
        interval:       Seconds between poll cycles (default: 5.0).
        session_steps:  Optional list of session/skm-wake steps to run once before
                        the first poll cycle.
        keep_mode:      None = no history, "unique" = deduped unique payloads,
                        "all" = every payload from every cycle,
                        "last" = sliding window of last N payloads (see keep_n).
        keep_n:         For keep_mode="last": number of recent payloads to display.
        save:           Journal every polled payload as it arrives and reconcile
                        into captures/ on stop (crash/disconnect leaves a
                        recoverable journal). Metadata comes from label/state/
                        notes (auto-suggested when omitted) and the TUI 's' key;
                        'n' rotates to a fresh segment mid-run.
        show_rulers:    Show byte-index rulers (idx/wican) once per PID.

    TUI keys: ↑/↓ move the parameter-selection cursor, j/k scroll, PgUp/PgDn
    page, g/Home top, G/End bottom, f toggle follow-tail, space pause/resume
    polling, e edit the selected parameter, v toggle its verified flag, d
    toggle enabled/disabled, F cycle the display filter (all/verified/
    unverified/enabled/disabled), s save/label the current session, n close the
    current --save segment and start a new one, q or Ctrl+C stop. A blinking
    ``● REC`` in the status line marks an active --save recording.
    """
    from ..profile import active
    from ..states import parse_states

    captures_dir = active().captures_dir
    states = parse_states(vehicle_states)
    controller = MonitorController(
        terminal,
        query_steps,
        pids_data,
        verbose,
        interval=interval,
        keep_mode=keep_mode,
        keep_n=keep_n,
        save=save,
        show_rulers=show_rulers,
        raw_client=raw_client,
        include_static=include_static,
    )
    controller.captures_dir = captures_dir

    # --save: open the write-ahead journal up front so every polled payload is
    # durably recorded as it arrives. On a clean stop we reconcile it into a
    # capture file; a disconnect/crash leaves it on disk for `--recover`.
    if save:
        controller.journal = _open_journal(controller, label, states, notes)
        journal_label = label or controller.query_label() or "Monitor session"
        print(
            f"  --save: journaling to {controller.journal.path.name} "
            f"(label: {journal_label!r}); auto-saves on stop. "
            "Press 's' to edit label/states/notes, 'n' to start a new segment."
        )

    try:
        await controller.setup(session_steps)

        if sys.stdout.isatty():
            from ._monitor_tui import run_monitor_app

            await run_monitor_app(controller)
        else:
            await _monitor_noninteractive(controller)

        if controller.disconnected:
            link = "raw CAN bus" if controller.raw else "WebSocket"
            _console.print(f"\n  [bold red]✖ {link} disconnected[/bold red]")
            _console.print(f"  [red]Stopped after {controller.cycle} cycles.[/red]\n")
            raise ConnectionError(f"{link} disconnected")

        # Print the final values so a stopped session leaves them in scrollback.
        _console.print(controller.render())
        print("  Monitoring stopped.")

    finally:
        # Reconcile the journal even on disconnect/exception (this is the fix for
        # the old bug where a dropped connection lost the whole --save session).
        if controller.journal is not None:
            with contextlib.suppress(Exception):
                # If no state was set explicitly (flag or the TUI dialog), fall
                # back to the auto-suggested state from decoded PID values.
                if not states and not controller._state_explicit:
                    suggested = controller.suggested_state()
                    if suggested:
                        controller.journal.update_meta(vehicle_states=[suggested])
                written = controller.journal.reconcile()
                if written is not None:
                    _console.print(f"  → Saved journaled captures to {written.name}")
        await controller.close()
