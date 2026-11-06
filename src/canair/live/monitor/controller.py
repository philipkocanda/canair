"""Live monitor mode — repeatedly polls a set of ECU PIDs and refreshes the display.

On a TTY this runs a Textual app (:mod:`canair.tui.apps.monitor.app`): the latest
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
own profile ``period:`` (:mod:`canair.live.monitor.schedule`), or every PID on
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

from canair.expression.results import EcuFrame, ResultEntry
from canair.profile.model import Profile
from canair.transport.session_manager import SessionManager

from ...captures.link_events import is_rx
from .backoff import PollBackoff
from .health import MonitorHealth, answered, is_nrc, label_ecu
from .link_events import LinkEventTracker
from .options import MonitorOptions
from .poll_elm import MonitorElmPoller
from .raw_poller import MonitorRawPoller
from .record import MonitorRecorder
from .render import MonitorRenderer
from .schedule import MONITOR_PERIOD_SPEEDUP, PollScheduler
from .selectors import MonitorSelectors
from .sessions import MonitorSessions
from .state import MonitorStateSuggester
from .stats import ParamStats
from .view import MonitorView

if TYPE_CHECKING:
    from ...captures.link_events import DisconnectCause
    from .reconnect import Reconnector


def query_ecu_error(query_steps: list[dict], pids_data: dict, *, profile: Profile) -> str | None:
    """Return an error message if any query step names an unknown ECU, else None.

    Guards against typos like ``query ESC ECS`` (the second selector is a
    non-existent ECU) that would otherwise be silently skipped every poll cycle.
    ECU names are matched case-insensitively against the active profile.
    """
    from ...ecus.names import build_canonical_name_index, canonical_ecu_name_safe
    from ...ecus.pids import build_ecu_index

    ecu_index = build_ecu_index(pids_data)
    try:
        name_index = build_canonical_name_index(profile=profile)
    except FileNotFoundError:
        name_index = None
    seen: set[str] = set()
    unknown: list[str] = []
    for step in query_steps:
        key = canonical_ecu_name_safe(step["ecu"], name_index, profile=profile).upper()
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
    :meth:`seconds_until_due` and ``renderer.render()``, so the two share identical
    behaviour. Which PIDs a tick reads is the :class:`PollScheduler`'s call.
    """

    def __init__(
        self,
        terminal,
        options: MonitorOptions,
        pids_data: dict,
        raw_client=None,
        *,
        profile: Profile,
    ):
        self.terminal = terminal
        self.raw_client = raw_client  # transport.RawUdsClient when using the raw backend
        self.raw = raw_client is not None
        self.options = options
        self.query_steps = options.query_steps
        self.pids_data = pids_data
        self.profile = profile
        self.verbose = options.verbose
        self.schedule = PollScheduler(
            options.schedule, options.interval, period_speedup=MONITOR_PERIOD_SPEEDUP
        )
        # Slow-PID backoff: a slow or silent PID holds up every other PID's next poll, so it
        # is polled less often until it recovers. Fed from health.poll_costs.
        self.backoff = PollBackoff(enabled=options.backoff)
        self.keep_mode = options.keep_mode
        self.keep_n = options.keep_n
        self.save = options.save
        self.show_rulers = options.show_rulers
        # Byte-index notation for the rulers and byte-reference column (display.byte_notation).
        self.notation = options.notation
        self.include_static = options.include_static
        # Live display view mode (cycled with 'V' in the TUI; see VIEW_MODES). Views change
        # presentation only, never recording.
        self.view_mode = "full"

        self.sm = SessionManager(terminal, verbose=self.verbose) if not self.raw else None
        self._ecu_index: dict | None = None
        # Diagnostic session types (active modes, the safety check, known-good suggestions,
        # live switching) live in MonitorSessions, which also holds the raw backend's modes.
        self.sessions = MonitorSessions(self)
        # Live PID-selector management (the TUI 'p' picker) lives in MonitorSelectors.
        self.selectors = MonitorSelectors(self)
        # Raw-backend poll cycle + multi-DID batching state lives in the poller.
        self.raw_poller = MonitorRawPoller(self)
        # ELM327 session setup + poll cycle (the non-raw backends).
        self.elm_poller = MonitorElmPoller(self)
        # Hook the TUI sets to refresh the body mid-cycle, so a slow PID doesn't freeze the
        # view. None for the non-interactive path (rendered once, on exit).
        self.on_partial = None
        # Last *good* entry per (ecu_label, pid): a PID that times out reuses it (marked
        # stale, dimmed) so the layout doesn't jolt.
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
        # Echo/SID validation failures this cycle, apart from last_drops (drop + stale): a
        # desync and a lost frame call for opposite responses, so the status line splits them.
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
        # Constructed after keep_mode/save are set, since it initialises its
        # history maps from them.
        self.recorder = MonitorRecorder(self)
        # When the recording could not see the car (disconnects, resyncs), journaled
        # into the --save session's link_events. Looks the journal up per write, since
        # a segment rotation replaces it.
        self.link_events = LinkEventTracker(lambda: self.recorder.journal, rx_of=self._event_rx)
        self._name_index: dict | None = None
        # What each tick's reads cost, which PIDs to slow down, and whether the link
        # is alive at all (see health.py).
        self.health = MonitorHealth(
            self.schedule,
            self.backoff,
            self.link_events,
            mark_disconnected=self.mark_disconnected,
            transport=lambda: self.transport_type,
        )
        # Vehicle-state auto-suggestion (decoded-value accumulation, per-signal
        # clocks + expiry, vehicle_states.yaml evaluation) lives in the
        # MonitorStateSuggester collaborator.
        self.state = MonitorStateSuggester(self)
        # In-place editing / filtering of PID definitions from the TUI. The
        # editor owns the selection cursor + display filter and writes edits
        # through canair.pids_edit; this controller reloads on its request.
        from .edit import MonitorEditor

        self.pids_dir = None  # None -> the profile's ecus/ (tests override)
        self.editor = MonitorEditor(self)
        # The display: what it shows (view) and how it is drawn (renderer, which keeps the
        # per-PID render cache across paints).
        self.view = MonitorView(self)
        self.renderer = MonitorRenderer(self)

    def seconds_until_due(self) -> float:
        """How long the poll loop may sleep before the next tick (0 = poll now)."""
        return self.schedule.seconds_until_due(time.monotonic())

    def diag(self):
        """The active transport's :class:`~canair.transport.stats.TransportStats`.

        Reads it off the raw pipelined client (raw backend) or the ELM terminal —
        whichever is driving this run — so the TUI status line and capture
        provenance both source drops/error counts from one place. ``None`` when
        the transport doesn't expose one (older/fake terminals).
        """
        src = self.raw_client if self.raw else self.terminal
        return getattr(src, "diag", None)

    def link(self):
        """The active transport's :class:`~canair.transport.link_latency.LinkLatency`, or None.

        Surfaced so the TUI can show the measured round trip: on a remote link it
        is the difference between "the car is not answering" and "the network is
        slow", which otherwise look identical from the driver's seat.
        """
        src = self.raw_client if self.raw else self.terminal
        return getattr(src, "link", None)

    def reload_pids(self) -> None:
        """Re-read PID definitions after an in-place edit and rebuild the index.

        ``canair.pids_edit`` clears the memoized load on every write, so this
        picks up the just-edited expression/flags; the rebuilt ECU index makes
        the next poll decode with the new definition.
        """
        from ...ecus.pids import build_ecu_index, load_pids, load_pids_dir

        self.pids_data = load_pids_dir(self.pids_dir) if self.pids_dir else load_pids(self.profile)
        self._ecu_index = build_ecu_index(self.pids_data)
        self.sessions.invalidate_index()  # rebuilt lazily on next access

    async def setup(self, session_steps: list[dict] | None) -> None:
        """Build the ECU index, open the sessions, prime each ECU and start keepalives."""
        from canair.ecus.pids import build_ecu_index

        self._ecu_index = build_ecu_index(self.pids_data)
        if self.raw:
            await self.raw_poller.setup(session_steps)
        else:
            await self.elm_poller.setup(session_steps)

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
        self.health.begin_tick()
        self.fresh_queries = []
        t0 = time.monotonic()
        self.schedule.begin_tick(t0)
        try:
            if self.raw:
                await self.raw_poller.poll()
            else:
                await self.elm_poller.poll()
        finally:
            self.schedule.end_tick()
        self.elapsed = time.monotonic() - t0
        if diag is not None and base is not None:
            delta = diag.diff(base)
            self.last_drops = delta.drops
            self.last_errors = delta.errors
            self.last_stale = delta.stale
            self.link_events.resynced(delta.resyncs)
        self._record(self.fresh_queries)
        self.health.observe_ecu_answers()
        self.health.apply_backoff()
        self.health.check_liveness(
            {label_ecu(label) for label, rows in self.fresh_queries if rows},
            {label_ecu(label) for label, _rows in self.last_queries},
        )

    def mark_disconnected(self, cause: "DisconnectCause") -> None:
        """Hand the session to the reconnector, recording the gap this opens.

        Every link failure goes through here rather than setting ``disconnected``
        directly, so a ``--save`` session's ``link_events`` says why its data stops.
        """
        self.disconnected = True
        self.link_events.disconnected(cause)

    def diag_recorder(self):
        """The active client's transport-diagnostics recorder, or None.

        The monitor talks through the raw pipelined client (``raw_client``) on
        the ``slcan-tcp`` path and the ELM terminal otherwise; both carry a
        :class:`~canair.transport.stats.TransportStats` as ``.diag``. Returns
        None for a fake/older client without one (the status line then omits the
        drops indicator).
        """
        client = self.raw_client if self.raw else self.terminal
        return getattr(client, "diag", None)

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
        if answered(entry):
            self.link_events.answered(entry.get("acquired_at"), ecu=label_ecu(key[0]))
        return shown

    def _decide_display(self, key: tuple[str, str], entry: ResultEntry) -> ResultEntry:
        """The display decision itself — see :meth:`_displayify`."""
        if entry.get("raw_hex"):
            self._last_good[key] = entry
            self.health.cycle_answered = True
            return entry
        if is_nrc(entry):
            self.health.cycle_answered = True
            return entry
        last = self._last_good.get(key)
        if last is not None:
            return {**last, "stale": True}
        return entry

    def _event_rx(self, ecu: str) -> str | None:
        """``ecu``'s response address for a link event, or None when it has none."""
        try:
            ref = self._ecu_ref(ecu)
        except Exception:
            return None
        return ref if is_rx(ref) else None

    def _ecu_ref(self, ecu_label: str) -> str:
        """Resolve a monitor ECU label (e.g. "BMS") to its CAN response address.

        Falls back to the label's leading token when it isn't a known ECU name.
        Caches the name→TX index for repeated calls during polling.
        """
        from ...ecus.names import build_name_tx_index, rx_from_name

        if self._name_index is None:
            self._name_index = build_name_tx_index(profile=self.profile)
        m = re.match(r"(\w+)", ecu_label)
        assert m is not None  # ecu_label is an ECU name/label, always starts with a word char
        ecu_short = m.group(1)
        return rx_from_name(ecu_short, self._name_index, profile=self.profile) or ecu_short

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

        Every ECU's poll rate is restored too: a desynchronised pipe makes every ECU
        look unanswered, so by the time the liveness check reconnects they are all
        backed off, and the old link's timings say nothing about the new one.

        The old client's transport counts move to the new client, so the session's
        recorded ``quality`` still covers the exchanges made before the reconnect.
        """
        previous = self.diag_recorder()
        if self.raw:
            self.raw_client = new_client
            self.raw_poller = MonitorRawPoller(self)
        else:
            self.terminal = new_client
            self.sm = SessionManager(new_client, verbose=self.verbose)
        current = self.diag_recorder()
        if previous is not None and current is not None and current is not previous:
            current.absorb(previous)
        self.health.reset_backoff()
        self.disconnected = False
