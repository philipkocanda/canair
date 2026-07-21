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
    render_byte_rulers,
    render_param_table,
)
from ..session_manager import SessionManager

# _HIGHLIGHT_STYLE, _bytes_to_ascii and _render_hex_line moved to canlib.formatting;
# re-exported here for backward-compatible imports (e.g. tests/test_monitor.py).
__all__ = [
    "_HIGHLIGHT_STYLE",
    "MonitorController",
    "_bytes_to_ascii",
    "_render_hex_line",
    "_render_results",
    "mode_monitor",
]

_console = Console(highlight=False)


def _render_results(
    queries: list[tuple[str, list]],
    verbose: bool,
    cycle: int,
    elapsed: float,
    interval: float,
    prev_hex: dict[tuple[str, str], str] | None = None,
    hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = None,
    show_rulers: bool = False,
    footer: bool = True,
) -> Text:
    """Render all ECU query results as a Rich Text object for display.

    ``footer`` appends the "Press Ctrl+C to stop" hint (kept for callers that
    render a single static block). The scrolling monitor passes ``footer=False``
    and draws its own fixed status line below the scroll viewport.
    """
    text = Text()

    text.append(
        f"  Monitor — cycle {cycle}  (last: {elapsed:.1f}s, interval: {interval:.1f}s)\n",
        style="dim",
    )

    if prev_hex is None:
        prev_hex = {}

    for ecu_label, pid_results in queries:
        if not pid_results:
            continue

        text.append("\n  ")
        text.append(ecu_label, style="bold cyan")
        text.append("\n")

        for entry in pid_results:
            pid = entry["pid"]
            error = entry.get("error")
            params = entry.get("params", [])
            raw_hex = entry.get("raw_hex", "")
            decode = entry.get("decode")
            unmapped = entry.get("unmapped", False)

            # Detect change from previous cycle
            hex_key = (ecu_label, pid)
            changed = cycle > 1 and raw_hex and hex_key in prev_hex and prev_hex[hex_key] != raw_hex

            text.append("    ")
            text.append(pid, style="yellow")
            if changed:
                text.append(" ●", style="bright_green")
            if unmapped:
                text.append(" (unmapped)", style="dim")
            # Show history count when keeping history
            if hex_history and hex_key in hex_history:
                n_entries = len(hex_history[hex_key])
                if raw_hex and raw_hex not in [h for h, _ts in hex_history[hex_key]]:
                    n_entries += 1  # current not yet added
                if n_entries > 1:
                    text.append(f"  ({n_entries} entries)", style="dim")
            if error:
                text.append(f"  {error}\n", style="red")
                continue
            text.append("\n")

            if params:
                text.append_text(render_param_table(params, verbose=verbose))
            elif decode:
                text.append(f"      {decode}\n")

            if raw_hex:
                hex_key = (ecu_label, pid)
                # Byte-index ruler, once per PID, above the hex lines.
                if show_rulers:
                    ruler_pw = 16 if hex_history is not None else 6
                    text.append_text(
                        render_byte_rulers(len(raw_hex) // 2, params, prefix_width=ruler_pw)
                    )
                if hex_history and hex_key in hex_history:
                    # Show all unique payloads chronologically, each diffed against predecessor
                    history = hex_history[hex_key]  # list of (hex, timestamp)
                    history_hexes = [h for h, _ts in history]
                    # Include current if not yet in history (first cycle edge case)
                    if raw_hex not in history_hexes:
                        all_entries = [*history, (raw_hex, "")]
                    else:
                        all_entries = list(history)
                    for i, (payload, ts) in enumerate(all_entries):
                        prev_raw = all_entries[i - 1][0] if i > 0 else ""
                        prefix = f"      {ts}  " if ts else "                "
                        text.append_text(
                            _render_hex_line(
                                payload,
                                params,
                                unmapped,
                                prev_raw=prev_raw,
                                prefix=prefix,
                                prefix_style="dim" if ts else "",
                            )
                        )
                else:
                    prev_raw = prev_hex.get(hex_key, "") if prev_hex and cycle > 1 else ""
                    text.append_text(_render_hex_line(raw_hex, params, unmapped, prev_raw=prev_raw))

    if footer:
        text.append("\n  Press Ctrl+C to stop monitoring\n", style="dim")
    return text


def _prompt_and_save(
    hex_history: dict[tuple[str, str], list[tuple[str, str]]],
    prev_hex: dict[tuple[str, str], str],
    captures_dir: Path,
    label: str | None = None,
    state: str | None = None,
    notes: str | None = None,
) -> None:
    """Prompt (or use provided metadata) and write captures to YAML file.

    Collects label, state, and notes via stdin prompts (or uses the provided
    values non-interactively), then appends a new session with all unique
    payloads to captures/YYYY-MM-DD.yaml. Decoded parameter values are not
    stored — they are regenerated on demand from the payload + PID definitions
    (see decode.py / query-captures.py).
    """
    from ..captures import build_query_session, resolve_metadata, save_session

    if not hex_history and not prev_hex:
        print("  No payloads captured — nothing to save.")
        return

    # Merge current values into history for PIDs not yet in history
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

    if not merged:
        print("  No payloads captured — nothing to save.")
        return

    # Count what we'll save
    n_pids = len(merged)
    n_payloads = sum(len(v) for v in merged.values())
    print(f"\n  Saving {n_payloads} payload(s) across {n_pids} PID(s).")

    # Resolve metadata (non-interactive when label provided)
    meta = resolve_metadata(label, state, notes, suggested_label="Monitor session")
    if meta is None:
        return
    label, state, notes = meta

    # Flatten to (ecu_ref, pid, hex, time) rows, grouped by ECU then PID.
    # The ECU label (e.g. "BMS") is resolved to its CAN response address; an
    # unknown label falls back to its leading token verbatim.
    from ..ecus import build_name_tx_index, rx_from_name

    name_index = build_name_tx_index()
    results: list[tuple[str, str, str, str]] = []
    for (ecu_label, pid), entries in sorted(merged.items()):
        ecu_short = re.match(r"(\w+)", ecu_label).group(1)
        ecu_ref = rx_from_name(ecu_short, name_index) or ecu_short
        for hex_val, ts in entries:
            results.append((ecu_ref, pid, hex_val, ts))

    session = build_query_session(results, label, state, notes)
    save_session(session, captures_dir)


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
    ):
        self.terminal = terminal
        self.query_steps = query_steps
        self.pids_data = pids_data
        self.verbose = verbose
        self.interval = interval
        self.keep_mode = keep_mode
        self.keep_n = keep_n
        self.save = save
        self.show_rulers = show_rulers

        self.sm = SessionManager(terminal, verbose=verbose)
        self._ecu_index: dict | None = None
        self._batch_state = None  # multi.BatchState, created in setup()

        # Live state (read by the renderer).
        self.cycle = 0
        self.elapsed = 0.0
        self.last_cmds = 0  # ELM commands issued during the last poll cycle
        self.last_elm_time = 0.0  # seconds spent in ELM commands last cycle
        self.last_queries: list[tuple[str, list]] = []
        self.prev_hex: dict[tuple[str, str], str] = {}
        self.hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = (
            {} if keep_mode else None
        )
        self.save_history: dict[tuple[str, str], list[tuple[str, str]]] | None = (
            {} if save else None
        )
        self.disconnected = False

    async def setup(self, session_steps: list[dict] | None) -> None:
        """Build the ECU index, run one-shot session setup, start keepalives."""
        from ..pids import build_ecu_index
        from .multi import BatchState, _exec_session, _exec_skm_wake

        self._ecu_index = build_ecu_index(self.pids_data)
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

    def _record(self, new_queries: list[tuple[str, list]]) -> None:
        """Record freshly-polled payloads into prev_hex / display / save history."""
        for ecu_label, pid_results in new_queries:
            for entry in pid_results:
                raw = entry.get("raw_hex", "")
                if not raw:
                    continue
                key = (ecu_label, entry["pid"])
                self.prev_hex[key] = raw
                # Per-PID acquisition timestamp (moment the response arrived),
                # millisecond precision, so sequentially-polled PIDs keep skew.
                acq = entry.get("acquired_at")
                ts = (
                    datetime.fromtimestamp(acq).strftime("%H:%M:%S.%f")[:-3]
                    if acq
                    else datetime.now().strftime("%H:%M:%S.%f")[:-3]
                )
                if self.save_history is not None:  # --save: always keep everything
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

    async def poll_once(self) -> None:
        """Run every query step once, updating live state. Sets ``disconnected``."""
        from .multi import _exec_query

        self.cycle += 1
        t0 = time.monotonic()
        cmds0 = self.terminal.cmd_count
        elm0 = self.terminal.cmd_time
        new_queries: list[tuple[str, list]] = []
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
                )
            except ConnectionError:
                self.disconnected = True
                return
            if result is not None:
                new_queries.append(result)
        self.last_queries = new_queries
        self.elapsed = time.monotonic() - t0
        self.last_cmds = self.terminal.cmd_count - cmds0
        self.last_elm_time = self.terminal.cmd_time - elm0
        self._record(new_queries)

    def render(self) -> Text:
        """The current view as a Rich Text (rendered by the TUI / printed on exit)."""
        return _render_results(
            self.last_queries,
            self.verbose,
            self.cycle,
            self.elapsed,
            self.interval,
            self.prev_hex,
            self.hex_history,
            show_rulers=self.show_rulers,
            footer=False,
        )

    def save_captures(
        self,
        captures_dir: Path,
        label: str | None,
        state: str | None,
        notes: str | None,
    ) -> None:
        if self.save and self.save_history is not None:
            _prompt_and_save(self.save_history, self.prev_hex, captures_dir, label, state, notes)

    async def close(self) -> None:
        """Stop keepalives and close all open sessions (best-effort)."""
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
    state: str | None = None,
    notes: str | None = None,
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
        save:           On stop, prompt for metadata and save to captures/.
        show_rulers:    Show byte-index rulers (idx/wican) once per PID.

    TUI keys: ↑/↓ or j/k scroll, PgUp/PgDn page, g/Home top, G/End bottom,
    f toggle follow-tail, space pause/resume polling, q or Ctrl+C stop.
    """
    from ..profile import active

    captures_dir = active().captures_dir
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
    )

    try:
        await controller.setup(session_steps)

        if sys.stdout.isatty():
            from ._monitor_tui import run_monitor_app

            await run_monitor_app(controller)
        else:
            await _monitor_noninteractive(controller)

        if controller.disconnected:
            _console.print("\n  [bold red]✖ WebSocket disconnected[/bold red]")
            _console.print(f"  [red]Stopped after {controller.cycle} cycles.[/red]\n")
            raise ConnectionError("WebSocket disconnected")

        # Print the final values so a stopped session leaves them in scrollback.
        _console.print(controller.render())
        print("  Monitoring stopped.")
        controller.save_captures(captures_dir, label, state, notes)

    finally:
        await controller.close()
