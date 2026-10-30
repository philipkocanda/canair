"""Live monitor mode — repeatedly polls a set of ECU PIDs and refreshes the display.

On a TTY this runs a Textual app (:mod:`canlib.modes._monitor_tui`): the latest
values render into a widget that updates *in place* inside a scrollable
container, so the scroll position stays put while values refresh — mouse wheel,
scrollbar and keys all scroll natively and nothing ever freezes. When stdout is
not a TTY (piped/scripted) it polls silently until Ctrl+C and prints the final
values.

Usage (via canair monitor):
    canair monitor "session BCM --wake" "query BCM:C00B,B00E"
    canair monitor "query BMS:2101" --interval 2.0
    canair monitor "session IGPM --wake" "query IGPM:BC03,BC06"

Every 'query' step in the pipeline is polled repeatedly: by default each PID at its
own profile ``period:`` (:mod:`canlib.poll_schedule`), or every PID on
every cycle with ``--schedule fixed``.

The polling / decoding / capture-saving logic lives in :class:`MonitorController`
(reused by both the TUI and the non-interactive path); only the presentation
layer differs.
"""

import asyncio
import contextlib
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text

from canlib.uds.notation import ByteNotation

from .. import config
from ..formatting import (
    _HIGHLIGHT_STYLE,
    _bytes_to_ascii,
    _render_hex_line,
)
from ..keepmode import KEEP_LAST, KeepMode
from ..log import log_event
from ..poll_schedule import DEFAULT_SCHEDULE, PollScheduler, Schedule
from ..session_manager import SessionManager
from ._monitor_record import MonitorRecorder, _merge_history, _open_journal, _write_merged
from ._monitor_render import (
    _RENDER_DEFAULT_ROWS,
    _RENDER_MAX_ROWS,
    VIEW_MODES,
    RenderCache,
    _render_results,
)
from ._monitor_selectors import MonitorSelectors
from ._monitor_sessions import MonitorSessions
from ._monitor_state import STATE_INPUT_STALE_CYCLES, MonitorStateSuggester
from ._monitor_stats import ParamStats
from .monitor_raw import MonitorRawPoller, _raw_pid_result
from .multi_batch import EcuFrame, ResultEntry

if TYPE_CHECKING:
    from .monitor_reconnect import Reconnector

# _HIGHLIGHT_STYLE, _bytes_to_ascii and _render_hex_line moved to canlib.formatting;
# _render_results/_RENDER_MAX_ROWS/RenderCache to _monitor_render; _raw_pid_result to
# monitor_raw; _merge_history/_write_merged/_open_journal + the recording/journaling
# logic to _monitor_record; STATE_INPUT_STALE_CYCLES + the state-suggestion logic to
# _monitor_state; mode_monitor + the poll loops to _monitor_run. All re-exported here
# for backward-compatible imports (e.g. tests/test_monitor.py).
__all__ = [
    "STATE_INPUT_STALE_CYCLES",
    "_HIGHLIGHT_STYLE",
    "_RENDER_MAX_ROWS",
    "MonitorController",
    "MonitorRecorder",
    "RenderCache",
    "_bytes_to_ascii",
    "_merge_history",
    "_open_journal",
    "_raw_pid_result",
    "_render_hex_line",
    "_render_results",
    "_write_merged",
    "query_ecu_error",
]


def _label_ecu(label: str) -> str:
    """The ECU name a monitor row label carries (``"BMS (0x7E4)"`` -> ``"BMS"``)."""
    return label.rsplit(" (", 1)[0].upper()


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


class MonitorController:
    """Polls a set of ECU PIDs on a schedule and renders/records the results.

    Holds all monitor state and the CAN-facing logic (session setup, polling,
    history bookkeeping, capture saving). The presentation layer — the Textual
    TUI or the non-interactive fallback — drives it via :meth:`poll_once`,
    :meth:`seconds_until_due` and :meth:`render`, so the two share identical
    behaviour. Which PIDs a tick reads is the :class:`PollScheduler`'s call.
    """

    def __init__(
        self,
        terminal,
        query_steps: list[dict],
        pids_data: dict,
        verbose: bool,
        interval: float = 5.0,
        keep_mode: KeepMode | None = None,
        keep_n: int | None = None,
        save: bool = False,
        show_rulers: bool = False,
        notation: ByteNotation = ByteNotation.WICAN,
        raw_client=None,
        include_static: bool = False,
        schedule: Schedule = DEFAULT_SCHEDULE,
    ):
        self.terminal = terminal
        self.raw_client = raw_client  # transport.RawUdsClient when using the raw backend
        self.raw = raw_client is not None
        self.query_steps = query_steps
        self.pids_data = pids_data
        self.verbose = verbose
        self.schedule = PollScheduler(schedule, interval)
        self.keep_mode = keep_mode
        self.keep_n = keep_n
        self.save = save
        self.show_rulers = show_rulers
        # Byte-index notation for the rulers + per-signal byte-reference column
        # (the user's display.byte_notation, or --notation).
        self.notation = notation
        self.include_static = include_static
        # Live display view mode (cycled with 'V' in the TUI). See VIEW_MODES:
        # "full" (signals + hex payloads, the default), "signals" (decoded params
        # only), "ranges" (each signal's captured value span), "ecus" (just the
        # responding ECUs). Named views only change presentation, not recording.
        self.view_mode = "full"

        self.sm = SessionManager(terminal, verbose=verbose) if not self.raw else None
        self._ecu_index: dict | None = None
        # Diagnostic session-type concern (active modes, the safety check, the
        # picker's known-good suggestions, live switching) lives in the
        # MonitorSessions collaborator; it also holds the raw backend's
        # opened-session modes (which has no SessionManager to track them).
        self.sessions = MonitorSessions(self)
        # Live PID-selector management (the TUI 'p' picker: enumerate ECUs/PIDs,
        # report/toggle selectors) lives in the MonitorSelectors collaborator.
        self.selectors = MonitorSelectors(self)
        self._batch_state = None  # multi.BatchState, created in setup()
        # Raw-backend poll cycle + multi-DID batching state lives in the poller.
        self.raw_poller = MonitorRawPoller(self)
        # Incremental rendering: a hook the TUI sets to refresh the body mid-cycle
        # so a slow/timing-out PID doesn't freeze the whole view. Left None for
        # the non-interactive path (results are only rendered once, on exit).
        self._on_partial = None
        # Last *good* entry seen per (ecu_label, pid). A PID that times out reuses
        # this (marked stale → dimmed) so its values stay on screen instead of
        # collapsing to an error line and jolting the layout.
        self._last_good: dict[tuple[str, str], ResultEntry] = {}
        # Last entry *shown* per (ecu_label, pid) — whatever _displayify last
        # returned, including an honest `NRC …` line. A pending PID (raw,
        # mid-cycle) re-shows this until its own result lands. It must be a
        # superset of _last_good: keying the mid-cycle fallback on good data alone
        # made a PID that only ever answers NRC vanish at every cycle start and
        # reappear on resolve, shifting every row below it once per cycle.
        self._last_shown: dict[tuple[str, str], ResultEntry] = {}

        # Live state (read by the renderer).
        self.cycle = 0
        self.elapsed = 0.0
        self.last_cmds = 0  # ELM commands issued during the last poll cycle
        self.last_elm_time = 0.0  # seconds spent in ELM commands last cycle
        # Per-cycle transport-health deltas (from the active client's .diag),
        # surfaced in the TUI status line to raise awareness of connection/latency
        # issues: dropped/stale ISO-TP frames + all non-answer errors this cycle.
        self.last_drops = 0
        self.last_errors = 0
        # Echo/SID validation failures this cycle, tracked apart from last_drops
        # (which sums drop + stale for capture back-compat): a desync and a lost
        # frame call for opposite responses, so the status line names them apart.
        self.last_stale = 0
        # Resolved transport label (e.g. "slcan-tcp"/"wican-ws"), recorded into
        # saved-capture provenance. Set by mode_monitor.
        self.transport_type: str | None = None
        self.last_queries: list[EcuFrame] = []
        # The PIDs actually read on the last tick (as displayed). Under the per-PID
        # schedule a tick reads only what is due, while last_queries keeps showing
        # every PID — so this, not last_queries, is what gets recorded.
        self.fresh_queries: list[EcuFrame] = []
        self.prev_hex: dict[tuple[str, str], str] = {}
        # Payloads as of the *previous* poll cycle, snapshotted before prev_hex is
        # overwritten each cycle. Rendering diffs against this so byte-level change
        # highlighting works in the single-frame view too (prev_hex already holds
        # the freshly-recorded current payload by render time).
        self.prev_snapshot: dict[tuple[str, str], str] = {}
        # Decoded param values as of the *previous* poll cycle, per PID key:
        # {(ecu_label, pid): {param_name: value}}. Snapshotted alongside
        # prev_snapshot so the renderer highlights a param only when its
        # *interpreted* value changed (not merely a raw byte it reads).
        self.prev_params_snapshot: dict[tuple[str, str], dict[str, object]] = {}
        self._cur_params: dict[tuple[str, str], dict[str, object]] = {}
        # Per-signal value-range accumulator (min/max + distinct labels across the
        # whole run), backing the "ranges" view mode. Independent of the on-screen
        # keep-history, which the default keep-mode trims.
        self.param_stats = ParamStats()
        # Where on-demand ('s' key in the TUI) / end-of-run captures are written.
        # Set by mode_monitor; resolved lazily if left None.
        self.captures_dir: Path | None = None
        self.disconnected = False
        # Poll cycles in which not one PID answered coherently (no payload and no
        # negative response — every row carried forward as stale). A
        # desynced-but-open pipe raises nothing, so this count is the only thing
        # that can tell a live-but-useless link from a healthy one; at
        # `config.stale_cycles_before_reconnect()` it sets `disconnected` and lets
        # the reconnector rebuild the client. An NRC counts as answered: a negative
        # response proves the request reached the ECU and its reply came back in the
        # right slot, which is a healthy pipe refusing, not a broken one.
        #
        # Counted per ECU (`_unanswered`: polls of that ECU since anything last
        # answered) and reported as the minimum over the polled ECUs, so a fast PID
        # on a sleeping ECU cannot escalate on its own while a slower ECU is still
        # answering between its ticks. On the fixed schedule every ECU is polled
        # every cycle, so this is simply consecutive dead cycles.
        self.dead_cycles = 0
        self._unanswered: dict[str, int] = {}
        self._cycle_answered = False
        # Mid-session reconnect / auto-failover (set by mode_monitor). When a
        # reconnector is present the poll loop re-homes a dropped session instead
        # of exiting; session_steps are replayed on each reconnect to re-open
        # sessions. `reconnecting` flags an in-flight attempt (shown in the TUI).
        self.reconnect: Reconnector | None = None
        self.session_steps: list[dict] | None = None
        self.reconnecting = False
        # Latest reconnect status line (set from the reconnector, possibly off the
        # main thread — a plain string write, read by the TUI status renderer).
        self.reconnect_note = ""
        # Capture recording / journaling / on-demand-save / segment-rotate logic
        # lives in the MonitorRecorder collaborator (frame counters, display &
        # --save history, the write-ahead journal, segment label/state/notes).
        # The controller exposes its tested surface via the delegating
        # properties/methods below. Constructed after keep_mode/save are set,
        # since it initialises its history maps from them.
        self.recorder = MonitorRecorder(self)
        self._name_index: dict | None = None
        # Vehicle-state auto-suggestion (decoded-value accumulation, per-signal
        # clocks + expiry, vehicle_states.yaml evaluation) lives in the
        # MonitorStateSuggester collaborator; the controller exposes its tested
        # surface via the delegating properties/methods below.
        self.state = MonitorStateSuggester(self)
        # In-place editing / filtering of PID definitions from the TUI. The
        # editor owns the selection cursor + display filter and writes edits
        # through canlib.pids_edit; this controller reloads on its request.
        from .monitor_edit import MonitorEditor

        self.pids_dir = None  # None -> active profile's ecus/ (tests override)
        self.editor = MonitorEditor(self)
        # Per-PID rendered-block cache: the body repaints every poll cycle and
        # every mid-cycle partial, but most PIDs are unchanged between paints, so
        # reuse their rendered Text instead of rebuilding it each time.
        self._render_cache = RenderCache()

    # --- Recording / journaling: delegated to the MonitorRecorder collaborator.
    # These facades keep the tested public surface (and the TUI/renderer reads)
    # stable while the durability logic lives in _monitor_record.py.
    @property
    def total_frames(self) -> int:
        return self.recorder.total_frames

    @total_frames.setter
    def total_frames(self, v: int) -> None:
        self.recorder.total_frames = v

    @property
    def unique_frames(self) -> int:
        return self.recorder.unique_frames

    @unique_frames.setter
    def unique_frames(self, v: int) -> None:
        self.recorder.unique_frames = v

    @property
    def hex_history(self):
        return self.recorder.hex_history

    @hex_history.setter
    def hex_history(self, v) -> None:
        self.recorder.hex_history = v

    @property
    def save_history(self):
        return self.recorder.save_history

    @save_history.setter
    def save_history(self, v) -> None:
        self.recorder.save_history = v

    @property
    def journal(self):
        return self.recorder.journal

    @journal.setter
    def journal(self, v) -> None:
        self.recorder.journal = v

    @property
    def session_label(self) -> str:
        return self.recorder.session_label

    @session_label.setter
    def session_label(self, v: str) -> None:
        self.recorder.session_label = v

    @property
    def session_states(self) -> list[str]:
        return self.recorder.session_states

    @session_states.setter
    def session_states(self, v: list[str]) -> None:
        self.recorder.session_states = v

    @property
    def session_notes(self) -> str:
        return self.recorder.session_notes

    @session_notes.setter
    def session_notes(self, v: str) -> None:
        self.recorder.session_notes = v

    @property
    def _state_explicit(self) -> bool:
        return self.recorder.state_explicit

    @_state_explicit.setter
    def _state_explicit(self, v: bool) -> None:
        self.recorder.state_explicit = v

    def has_captures(self) -> bool:
        """True when there's at least one payload available to save."""
        return self.recorder.has_captures()

    # --- State auto-suggestion: delegated to the MonitorStateSuggester
    # (_monitor_state.py). These facades keep the tested public surface
    # (decoded_values/responded/suggested_state) and the TUI reads stable.

    @property
    def decoded_values(self) -> dict[str, float]:
        return self.state.decoded_values

    @property
    def responded(self) -> set[str]:
        return self.state.responded

    def suggested_state(self) -> str | None:
        """Auto-suggest the single, most *specific* vehicle state.

        See :meth:`MonitorStateSuggester.suggested_state`.
        """
        return self.state.suggested_state()

    def suggested_states(self) -> list[str]:
        """Auto-suggest the vehicle state(s) from the latest decoded values.

        See :meth:`MonitorStateSuggester.suggested_states`.
        """
        return self.state.suggested_states()

    def state_options(self) -> list[tuple[str, str]]:
        """The profile's ordered ``(state, description)`` vocabulary for the save dialog."""
        return self.state.state_options()

    def offer_states(self, *, next_segment: bool = False) -> list[str]:
        """The save dialog's state pre-fill. See :meth:`MonitorRecorder.offer_states`."""
        return self.recorder.offer_states(next_segment=next_segment)

    # --- Poll scheduling: delegated to the PollScheduler (poll_schedule.py).

    @property
    def interval(self) -> float:
        """The fixed schedule's period, and the per-PID schedule's fallback (s)."""
        return self.schedule.interval

    @interval.setter
    def interval(self, v: float) -> None:
        self.schedule.interval = v

    def seconds_until_due(self) -> float:
        """How long the poll loop may sleep before the next tick (0 = poll now)."""
        return self.schedule.seconds_until_due(time.monotonic())

    def _set_segment_meta(
        self, label: str | None, states: list[str] | None, notes: str | None
    ) -> None:
        """Mirror the journal's last-wins metadata onto the display fields."""
        self.recorder.set_segment_meta(label, states, notes)

    def save_now(self, label: str, vehicle_states=None, notes: str | None = None) -> str:
        """On-demand save (TUI 's'). See :meth:`MonitorRecorder.save_now`."""
        return self.recorder.save_now(label, vehicle_states, notes)

    def new_segment(self, label: str, vehicle_states=None, notes: str | None = None) -> str:
        """Close the current --save segment and start a fresh one (TUI 'n').

        See :meth:`MonitorRecorder.new_segment`.
        """
        return self.recorder.new_segment(label, vehicle_states, notes)

    def diag(self):
        """The active transport's :class:`~canlib.transport.stats.TransportStats`.

        Reads it off the raw pipelined client (raw backend) or the ELM terminal —
        whichever is driving this run — so the TUI status line and capture
        provenance both source drops/error counts from one place. ``None`` when
        the transport doesn't expose one (older/fake terminals).
        """
        src = self.raw_client if self.raw else self.terminal
        return getattr(src, "diag", None)

    def link(self):
        """The active transport's :class:`~canlib.transport.link_latency.LinkLatency`, or None.

        Surfaced so the TUI can show the measured round trip: on a remote link it
        is the difference between "the car is not answering" and "the network is
        slow", which otherwise look identical from the driver's seat.
        """
        src = self.raw_client if self.raw else self.terminal
        return getattr(src, "link", None)

    def reload_pids(self) -> None:
        """Re-read PID definitions after an in-place edit and rebuild the index.

        ``canlib.pids_edit`` clears the memoized load on every write, so this
        picks up the just-edited expression/flags; the rebuilt ECU index makes
        the next poll decode with the new definition.
        """
        from ..pids import build_ecu_index, load_pids

        self.pids_data = load_pids(self.pids_dir)
        self._ecu_index = build_ecu_index(self.pids_data)
        self.sessions.invalidate_index()  # rebuilt lazily on next access

    # --- Live PID selection: delegated to the MonitorSelectors collaborator
    # (_monitor_selectors.py). Mutating self.query_steps mid-poll is safe —
    # _poll_elm/_poll_raw iterate a fresh `for step in self.query_steps` each
    # cycle, so an add/remove takes effect on the very next cycle. These facades
    # keep the TUI 'p' picker's surface stable.

    def available_ecus(self) -> list[str]:
        """Every ECU known in the active profile, for the PID-picker."""
        return self.selectors.available_ecus()

    def available_pids(self, ecu_name: str) -> list[tuple[str, str]]:
        """(pid_code, description) pairs for one ECU's defined PIDs.

        See :meth:`MonitorSelectors.available_pids`.
        """
        return self.selectors.available_pids(ecu_name)

    def active_selectors(self) -> set[tuple[str, str]]:
        """(ECU, PID) pairs currently polled with an explicit PID filter."""
        return self.selectors.active_selectors()

    def swept_ecus(self) -> set[str]:
        """ECUs currently polled with NO PID filter (a bare whole-ECU sweep).

        See :meth:`MonitorSelectors.swept_ecus`.
        """
        return self.selectors.swept_ecus()

    def add_pid_selector(self, ecu_name: str, pid_code: str) -> None:
        """Start polling one PID on ``ecu_name`` (TUI PID-picker toggle-on).

        See :meth:`MonitorSelectors.add`.
        """
        self.selectors.add(ecu_name, pid_code)

    def remove_pid_selector(self, ecu_name: str, pid_code: str) -> None:
        """Stop polling one PID on ``ecu_name`` (TUI PID-picker toggle-off).

        See :meth:`MonitorSelectors.remove`.
        """
        self.selectors.remove(ecu_name, pid_code)

    # --- Diagnostic session type: delegated to the MonitorSessions collaborator
    # (_monitor_sessions.py). Session *opening* on setup() forwards a
    # mini-language `--mode` (see setup()); these facades keep the TUI 't' picker's
    # surface (active modes, the safety check, known types, a live switch) stable.

    def active_session_modes(self) -> dict[str, str]:
        """ECU_NAME -> currently-open "10 xx" sub-function (2-hex-digit str).

        See :meth:`MonitorSessions.active_modes`.
        """
        return self.sessions.active_modes()

    def _session_check(self, ecu_name: str, mode: str) -> str | None:
        """The command blocklist's verdict on ``10 <mode>`` for one ECU (None = safe).

        See :meth:`MonitorSessions.check`.
        """
        return self.sessions.check(ecu_name, mode)

    def known_session_types(self, ecu_name: str) -> list[tuple[str, str, bool | None]]:
        """(mode_hex, label, supported) options for the session-type picker.

        See :meth:`MonitorSessions.known_types`.
        """
        return self.sessions.known_types(ecu_name)

    async def switch_session(self, ecu_name: str, mode: str) -> str:
        """Force-switch one ECU to a new diagnostic session type, live.

        See :meth:`MonitorSessions.switch`.
        """
        return await self.sessions.switch(ecu_name, mode)

    async def setup(self, session_steps: list[dict] | None) -> None:
        """Build the ECU index, run one-shot session setup, start keepalives."""
        from ..pids import build_ecu_index

        self._ecu_index = build_ecu_index(self.pids_data)

        if self.raw:
            # Raw backend: no ELM sessions/keepalive. Sessions (10 03) for ECUs
            # that need them are opened best-effort before polling — but a mode
            # the safety policy refuses aborts setup rather than being swallowed
            # with the transport errors (the ELM path raises the same way).
            from ..safety import SafetyBlocked
            from .multi import build_query_plan

            assert self.raw_client is not None  # self.raw is True ⟺ raw_client was provided
            for step in session_steps or []:
                if step["type"] == "session":
                    tgt = step["target"].upper()
                    if tgt in self._ecu_index:
                        mode = str(step.get("mode", "03")).upper().removeprefix("0X").zfill(2)
                        blocked = self._session_check(tgt, mode)
                        if blocked:
                            raise SafetyBlocked(f"session {tgt} --mode {mode}: {blocked}")
                        with contextlib.suppress(Exception):
                            self.raw_client.read(tgt, bytes.fromhex(f"10{mode}"), timeout=1.0)
                        self.sessions.record_opened(tgt, mode)
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
        from ..transport.isotp_params import resolve_tx_padding

        self._batch_state = BatchState(resolve_tx_padding(self.pids_data))
        for step in session_steps or []:
            stype = step["type"]
            if stype == "skm-wake":
                print(f"  SKM wakeup ({step['level']})...")
                await _exec_skm_wake(self.sm, step["level"], self.verbose)
            elif stype == "session":
                print(f"  Opening session on {step['target']}...")
                await _exec_session(
                    self.sm,
                    step["target"],
                    step.get("wake", False),
                    self._ecu_index,
                    mode=step.get("mode", "03"),
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

    def _record(self, new_queries: list[EcuFrame]) -> None:
        """Record freshly-polled payloads into prev_hex / display / save history."""
        # Refresh the decoded-value snapshot used for state auto-suggestion.
        self.state.observe(new_queries)
        # Snapshot the prior cycle's payloads before the recorder overwrites them,
        # so the renderer can diff current-vs-previous (prev_hex is about to become
        # the current values).
        self.prev_snapshot = dict(self.prev_hex)
        # Snapshot the prior cycle's decoded values (the accumulated per-PID map)
        # so the renderer can gate the param highlight on the interpreted value
        # changing; then fold this cycle's decoded values into the current map.
        self.prev_params_snapshot = {k: dict(v) for k, v in self._cur_params.items()}
        for ecu_label, pid_results in new_queries:
            for entry in pid_results:
                if entry.get("stale"):
                    continue
                key = (ecu_label, entry["pid"])
                params = entry.get("params", [])
                self._cur_params[key] = {row[0]: row[1] for row in params if not row[4]}
                # Fold this cycle's decoded values into the run-long value ranges
                # (backs the "ranges" view mode).
                self.param_stats.observe(key, params)
        # Durability (prev_hex update + frame accounting + display/--save history +
        # journal) is the recorder's job.
        self.recorder.observe(new_queries)

    async def poll_once(self) -> None:
        """Run one tick: read every PID that is due, updating live state.

        On the fixed schedule that is every PID; on the per-PID schedule only the
        ones whose period has elapsed (the rest keep showing their last value).
        Sets ``disconnected``.
        """
        self.cycle += 1
        diag = self.diag_recorder()
        base = diag.snapshot() if diag is not None else None
        self._cycle_answered = False
        self.fresh_queries = []
        t0 = time.monotonic()
        self.schedule.begin_tick(t0)
        try:
            if self.raw:
                await self._poll_raw()
            else:
                await self._poll_elm()
        finally:
            self.schedule.end_tick()
        self.elapsed = time.monotonic() - t0
        if diag is not None and base is not None:
            delta = diag.diff(base)
            self.last_drops = delta.drops
            self.last_errors = delta.errors
            self.last_stale = delta.stale
        self._record(self.fresh_queries)
        self._check_liveness(
            {_label_ecu(label) for label, rows in self.fresh_queries if rows},
            {_label_ecu(label) for label, _rows in self.last_queries},
        )

    def _check_liveness(self, polled: set[str], live: set[str]) -> None:
        """Escalate an all-stale run to a reconnect.

        A transport error raises and is caught by the poll loop, but a pipe that
        is *open and useless* — every reply landing one slot behind its request —
        raises nothing, so ``disconnected`` would never be set and the session
        would sit there showing carried-forward values forever. That is the bug
        this guards: count polls where nothing answered, and hand the session to
        the reconnector once it is clearly not recovering on its own.

        ``polled`` are the ECUs read this tick, ``live`` every ECU on screen. It
        escalates once *every* live ECU has gone unanswered for the threshold, so
        a tick that only reached a sleeping ECU is not held against a slower one
        that is still answering between its own ticks.
        """
        if self._cycle_answered or not polled:
            self._unanswered.clear()
            self.dead_cycles = 0
            return
        for ecu in polled:
            self._unanswered[ecu] = self._unanswered.get(ecu, 0) + 1
        self.dead_cycles = min(self._unanswered.get(ecu, 0) for ecu in live | polled)
        limit = config.stale_cycles_before_reconnect()
        if limit <= 0 or self.dead_cycles < limit:
            return
        log_event(
            "stale",
            f"no coherent response for {self.dead_cycles} poll cycles - reconnecting",
            transport=self.transport_type,
        )
        self._unanswered.clear()
        self.dead_cycles = 0
        self.disconnected = True

    def diag_recorder(self):
        """The active client's transport-diagnostics recorder, or None.

        The monitor talks through the raw pipelined client (``raw_client``) on
        the ``slcan-tcp`` path and the ELM terminal otherwise; both carry a
        :class:`~canlib.transport.stats.TransportStats` as ``.diag``. Returns
        None for a fake/older client without one (the status line then omits the
        drops indicator).
        """
        client = self.raw_client if self.raw else self.terminal
        return getattr(client, "diag", None)

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
        polled: list[str] = []
        last_render = 0.0
        for step in self.query_steps:
            full_codes: list[str] = []

            def _take_due(ecu: str, plan: list, codes: list[str] = full_codes) -> list:
                codes.extend(entry[0] for entry in plan)
                return self.schedule.take_due(ecu, plan)

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
                    plan_filter=_take_due,
                )
            except ConnectionError:
                self.disconnected = True
                return
            if result is not None:
                label, pids = result
                # Keep a timed-out PID's last-good values (stale/dimmed) instead
                # of collapsing to an error line and jolting the layout.
                pids = [self._displayify((label, e["pid"]), e) for e in pids]
                if pids:
                    self.fresh_queries.append((label, pids))
                if label not in frame:
                    order.append(label)
                if label not in polled:
                    polled.append(label)
                # Every PID of the plan, in plan order: the ones read this tick
                # with their new result, the rest as they were last shown.
                frame[label] = [
                    self._last_shown[(label, code)]
                    for code in full_codes
                    if (label, code) in self._last_shown
                ]
                self.last_queries = [(lb, frame[lb]) for lb in order if lb in frame]
                now = _t.monotonic()
                if self._on_partial is not None and (now - last_render) >= 0.12:
                    last_render = now
                    with contextlib.suppress(Exception):
                        self._on_partial()
        self.last_queries = [(lb, frame[lb]) for lb in polled]
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

    def _apply_raw_submission(
        self, s: dict, val, acquired: float, by_pid: dict[tuple[str, str], ResultEntry]
    ) -> None:
        self.raw_poller.apply_submission(s, val, acquired, by_pid)

    def _displayify(self, key: tuple[str, str], entry: ResultEntry) -> ResultEntry:
        """Map a freshly-resolved entry to what should be shown for that PID.

        - good (has ``raw_hex``): remembered as the last-good, shown as-is.
        - a real negative response (``NRC …``): shown as-is (honest).
        - a timeout / no-data / transport error: reuse the last-good entry marked
          ``stale`` (dimmed on screen) so its parameters stay put and the layout
          doesn't jump; if we never had good data, show the error.

        Whatever it settles on is also remembered as the last-*shown* entry, so the
        mid-cycle pending fallback can hold the row's place even for a PID that
        never returns data.
        """
        shown = self._decide_display(key, entry)
        self._last_shown[key] = shown
        return shown

    def _decide_display(self, key: tuple[str, str], entry: ResultEntry) -> ResultEntry:
        """The display decision itself — see :meth:`_displayify`."""
        if entry.get("raw_hex"):
            self._last_good[key] = entry
            self._cycle_answered = True
            return entry
        if str(entry.get("error", "")).startswith("NRC"):
            self._cycle_answered = True
            return entry
        last = self._last_good.get(key)
        if last is not None:
            return {**last, "stale": True}
        return entry

    def _raw_build_queries(
        self, plan_by_ecu, by_pid: dict[tuple[str, str], ResultEntry]
    ) -> list[EcuFrame]:
        return self.raw_poller.build_queries(plan_by_ecu, by_pid)

    def render(self) -> Text:
        """The current view as a Rich Text (rendered by the TUI / printed on exit)."""
        self.editor.ensure_valid(self.last_queries)
        return _render_results(
            self.editor.visible_queries(self.last_queries),
            self.verbose,
            self.cycle,
            self.elapsed,
            self.schedule.describe(),
            self.prev_snapshot,
            hex_history=self.hex_history,
            show_rulers=self.show_rulers,
            footer=False,
            selected=self.editor.selected,
            max_history_rows=self._history_render_limit(),
            cache=self._render_cache,
            prev_params=self.prev_params_snapshot,
            view_mode=self.view_mode,
            param_stats=self.param_stats,
            notation=self.notation,
        )

    def cycle_view(self) -> str:
        """Advance to the next display view mode (TUI 'V'); returns the new mode."""
        idx = VIEW_MODES.index(self.view_mode) if self.view_mode in VIEW_MODES else 0
        self.view_mode = VIEW_MODES[(idx + 1) % len(VIEW_MODES)]
        return self.view_mode

    def _history_render_limit(self) -> int:
        """How many history rows (runs of identical payloads) to render per PID.

        --keep N shows the requested N; every other keep-mode stays compact at a
        handful of newest runs so a noisy/long session doesn't flood (or choke)
        the live view — every poll is still in the --save journal.
        """
        if self.keep_mode == KEEP_LAST and self.keep_n:
            return self.keep_n
        return _RENDER_DEFAULT_ROWS

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

    def segment_title(self) -> str:
        """Display title for the current session/segment.

        The user-set label when present, else the polled-selectors summary, so
        the TUI header is meaningful even before (or without) a --save label.
        """
        return self.session_label or self.query_label() or "Monitor"

    def session_summary(self) -> dict:
        """A snapshot of the current run/segment for the session-info modal.

        Aggregates the run-level counters (frames, cycles, transport) and the
        active segment's metadata (label/states/notes, start time, frame count).
        Pure read — safe to call any time.
        """
        r = self.recorder
        return {
            "recording": self.journal is not None,
            "label": self.segment_title(),
            "states": list(self.session_states),
            "notes": self.session_notes,
            "query": self.query_label(),
            "keep_mode": self.keep_mode,
            "keep_n": self.keep_n,
            "view_mode": self.view_mode,
            "interval": self.interval,
            "schedule": self.schedule.describe(),
            "cycle": self.cycle,
            "total_frames": self.total_frames,
            "unique_frames": self.unique_frames,
            "transport": self.transport_type,
            "captures_dir": str(self.captures_dir) if self.captures_dir else None,
            "journal_path": str(self.journal.path) if self.journal is not None else None,
            "run_started_at": r.run_started_at,
            "segment_started_at": r.segment_started_at,
            "segment_frames": self.total_frames - r.segment_frames_base,
            "completed_segments": len(r.segments),
        }

    def segment_history(self) -> list[dict]:
        """Summaries of the --save segments already closed this run (oldest first)."""
        return list(self.recorder.segments)

    def interrupt(self) -> None:
        """Abort an in-flight raw poll ASAP so Ctrl-C / SIGTERM exit is prompt.

        Only the raw backend can stall shutdown (its pipelined poll runs in an
        executor thread that asyncio joins on the way out); the ELM path's polls
        are plain awaits that cancel promptly, so this is a no-op there.
        """
        if self.raw and self.raw_client is not None:
            with contextlib.suppress(Exception):
                self.raw_client.interrupt()

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

    async def close_client(self) -> None:
        """Best-effort close of just the transport client, keeping session state.

        Used by the reconnector to free a dead socket before re-probing; unlike
        :meth:`close` it does not reconcile the journal or tear down recording —
        the controller lives on to resume once a new client is rebound.
        """
        if self.raw:
            if self.raw_client is not None:
                with contextlib.suppress(Exception):
                    self.raw_client.close()
            return
        if self.sm is not None:
            with contextlib.suppress(Exception):
                self.sm.stop_background_keepalive()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.terminal.close(), timeout=2.0)

    def rebind(self, new_client) -> None:
        """Swap in a freshly-reconnected transport client, preserving session state.

        Journal, display/save history, counters, and value ranges stay on the
        controller — only the transport-facing client (and its dependent
        session-manager / raw poller) is replaced — so a reconnect continues the
        same ``--save`` session. Clears :attr:`disconnected` so the poll loop
        resumes. The new client must match the controller's transport kind (the
        reconnector filters candidates to the same transport).
        """
        if self.raw:
            self.raw_client = new_client
            self.raw_poller = MonitorRawPoller(self)
        else:
            self.terminal = new_client
            self.sm = SessionManager(new_client, verbose=self.verbose)
        self.disconnected = False


# The run driver (mode_monitor + the poll loops) lives in _monitor_run.py, which
# imports MonitorController from here. To keep `canlib.modes.monitor.mode_monitor`
# working for back-compat imports without an eager circular import, resolve it
# lazily on attribute access.
def __getattr__(name: str):
    if name == "mode_monitor":
        from ._monitor_run import mode_monitor

        return mode_monitor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
