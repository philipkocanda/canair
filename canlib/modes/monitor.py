"""Live monitor mode — repeatedly polls a set of ECU PIDs and refreshes the display.

Renders into the alternate screen buffer with a scrollable viewport: a
background task polls all ``query`` steps while the foreground loop redraws the
latest values and handles keys. Content taller than the terminal can be scrolled
(arrows / j-k / PgUp-PgDn / g-G / Home-End); the view follows the tail by
default and detaches when you scroll up. Each poll cycle updates the values in
place, giving a real-time view of changing parameters (SOC, temps, voltages …).

Usage (via --multi --monitor):
    canreq --multi "session BCM --wake" "query BCM C00B B00E" --monitor
    canreq --multi "query BMS 2101" --monitor 2.0
    canreq --multi "session IGPM --wake" "query IGPM BC03 BC06" --monitor

The --monitor flag applies to the last 'query' step in the pipeline. If
there are multiple query steps, all of them are repeated each cycle.

Terminal plumbing (cbreak input, alternate screen, scroll maths, paging) lives
in :mod:`canlib.tui` and is shared with the other interactive modes.
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
    render_byte_rulers,
    render_param_table,
)
from ..session_manager import SessionManager
from ..tui import (
    ScrollView,
    compose_frame,
    page_output,
    raw_screen,
    segments_to_str,
    terminal_columns,
    terminal_lines,
    wrap_text_lines,
)

# _HIGHLIGHT_STYLE, _bytes_to_ascii and _render_hex_line moved to canlib.formatting;
# re-exported here for backward-compatible imports (e.g. tests/test_monitor.py).
__all__ = [
    "_HIGHLIGHT_STYLE",
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
    """Live-refresh ECU parameter monitor with a scrollable viewport.

    Executes the given query_steps repeatedly in a background task while the
    foreground loop redraws the latest values into the alternate screen and
    handles scroll keys. Sessions are opened once (from session_steps) and kept
    alive with background keepalives.

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

    Keys (interactive/TTY): ↑/↓ or j/k scroll, PgUp/PgDn page, g/Home top,
    G/End bottom, f toggle follow-tail, q or Ctrl+C stop. When stdout is not a
    TTY (piped/scripted), it polls silently until Ctrl+C and prints the final
    values. On stop, output taller than the terminal is opened in a pager.
    """
    from ..pids import build_ecu_index
    from ..profile import active
    from .multi import _exec_query, _exec_session, _exec_skm_wake

    captures_dir = active().captures_dir

    ecu_index = build_ecu_index(pids_data)
    sm = SessionManager(terminal, verbose=verbose)

    # Shared monitor state — written by the poll cycle, read by the renderer.
    cycle = 0
    elapsed = 0.0
    last_queries: list[tuple[str, list]] = []
    prev_hex: dict[tuple[str, str], str] = {}
    hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = {} if keep_mode else None
    save_history: dict[tuple[str, str], list[tuple[str, str]]] | None = {} if save else None
    disconnected = False

    def _record(new_queries: list[tuple[str, list]]) -> None:
        """Record freshly-polled payloads into prev_hex / display / save history."""
        for ecu_label, pid_results in new_queries:
            for entry in pid_results:
                raw = entry.get("raw_hex", "")
                if not raw:
                    continue
                key = (ecu_label, entry["pid"])
                prev_hex[key] = raw
                # Per-PID acquisition timestamp (moment the response arrived),
                # millisecond precision, so sequentially-polled PIDs keep skew.
                acq = entry.get("acquired_at")
                ts = (
                    datetime.fromtimestamp(acq).strftime("%H:%M:%S.%f")[:-3]
                    if acq
                    else datetime.now().strftime("%H:%M:%S.%f")[:-3]
                )
                if save_history is not None:  # --save: always keep everything
                    save_history.setdefault(key, []).append((raw, ts))
                if hex_history is not None:  # --keep display history
                    if keep_mode in ("all", "last"):
                        hex_history.setdefault(key, []).append((raw, ts))
                        if keep_mode == "last" and keep_n and len(hex_history[key]) > keep_n:
                            hex_history[key] = hex_history[key][-keep_n:]
                    else:  # "unique": store only if not seen before
                        existing = [h for h, _ts in hex_history.get(key, [])]
                        if raw not in existing:
                            hex_history.setdefault(key, []).append((raw, ts))

    async def _poll_once() -> None:
        """Run every query step once, updating shared state."""
        nonlocal cycle, elapsed, last_queries, disconnected
        cycle += 1
        t0 = time.monotonic()
        new_queries: list[tuple[str, list]] = []
        for step in query_steps:
            try:
                result = await _exec_query(
                    sm,
                    step["ecu"],
                    step.get("pids", []),
                    ecu_index,
                    pids_data,
                    verbose,
                    return_results=True,
                    quiet=True,
                )
            except ConnectionError:
                disconnected = True
                return
            if result is not None:
                new_queries.append(result)
        last_queries = new_queries
        elapsed = time.monotonic() - t0
        _record(new_queries)

    async def _poll_loop(should_stop) -> None:
        """Poll on ``interval`` until stopped or disconnected."""
        while not should_stop() and not disconnected:
            t0 = time.monotonic()
            await _poll_once()
            if disconnected:
                return
            remaining = interval - (time.monotonic() - t0)
            while remaining > 0 and not should_stop() and not disconnected:
                await asyncio.sleep(min(remaining, 0.1))
                remaining = interval - (time.monotonic() - t0)

    def _body(footer: bool = False) -> Text:
        return _render_results(
            last_queries,
            verbose,
            cycle,
            elapsed,
            interval,
            prev_hex,
            hex_history,
            show_rulers=show_rulers,
            footer=footer,
        )

    def _footer(scroll: ScrollView) -> Text:
        t = Text("  ")
        if scroll.total > scroll.viewport:
            t.append(f"lines {scroll.top + 1}-{scroll.bottom}/{scroll.total}", style="dim")
        else:
            t.append(f"{scroll.total} lines", style="dim")
        t.append("  ")
        t.append(
            "● following" if scroll.follow else "❚❚ paused",
            style="green" if scroll.follow else "yellow",
        )
        t.append("   ↑↓ jk · PgUp/PgDn · g/G · f follow · q quit", style="dim")
        return t

    def _full_frame_str() -> str:
        """The complete (uncropped) view as an ANSI string, for paging/printing."""
        lines = wrap_text_lines(_body(footer=False), terminal_columns(), _console)
        return segments_to_str(_console, lines)

    async def _monitor_interactive() -> None:
        scroll = ScrollView(follow=True)
        stopped = False

        poll_task = asyncio.ensure_future(_poll_loop(lambda: stopped))
        try:
            async with raw_screen(alt_screen=True, hide_cursor=True) as get_key:
                while not stopped and not disconnected:
                    frame = compose_frame(
                        _console,
                        _body(footer=False),
                        scroll,
                        width=terminal_columns(),
                        height=terminal_lines(),
                        footer=_footer,
                    )
                    sys.stdout.write(frame)
                    sys.stdout.flush()

                    key = await get_key(timeout=0.2)
                    if key is None:
                        continue  # periodic redraw (fresh values / elapsed)
                    if key in ("CTRL_C", "q", "Q"):
                        stopped = True
                    elif key in ("UP", "k"):
                        scroll.scroll(-1)
                    elif key in ("DOWN", "j"):
                        scroll.scroll(1)
                    elif key == "PGUP":
                        scroll.page(-1)
                    elif key == "PGDN":
                        scroll.page(1)
                    elif key in ("HOME", "g"):
                        scroll.home()
                    elif key in ("END", "G"):
                        scroll.end()
                    elif key in ("f", "F"):
                        scroll.toggle_follow()
        finally:
            stopped = True
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task

    async def _monitor_noninteractive() -> None:
        """No TTY: poll silently until SIGINT/disconnect (piped/scripted runs)."""
        stop_flag = {"v": False}

        def _handle_sigint(_sig, _frame):
            stop_flag["v"] = True

        old_handler = signal.signal(signal.SIGINT, _handle_sigint)
        try:
            await _poll_loop(lambda: stop_flag["v"])
        finally:
            signal.signal(signal.SIGINT, old_handler)

    try:
        # One-shot setup: open sessions
        if session_steps:
            for step in session_steps:
                stype = step["type"]
                if stype == "skm-wake":
                    print(f"  SKM wakeup ({step['level']})...")
                    await _exec_skm_wake(sm, step["level"], verbose)
                elif stype == "session":
                    print(f"  Opening session on {step['target']}...")
                    await _exec_session(sm, step["target"], step.get("wake", False), ecu_index)

        # Start background keepalives
        sm.start_background_keepalive(interval=2.0)

        if sys.stdout.isatty():
            await _monitor_interactive()
        else:
            await _monitor_noninteractive()

        if disconnected:
            _console.print("\n  [bold red]✖ WebSocket disconnected[/bold red]")
            _console.print(f"  [red]Stopped after {cycle} cycles.[/red]\n")
            raise ConnectionError("WebSocket disconnected")

        # User-requested stop: surface the full view, then optionally save.
        frame = _full_frame_str()
        if sys.stdout.isatty() and frame.count("\n") + 1 > terminal_lines():
            page_output(frame)  # scroll the whole history in a pager
        else:
            sys.stdout.write(frame if frame.endswith("\n") else frame + "\n")
            sys.stdout.flush()
        print("  Monitoring stopped.")
        if save and save_history is not None:
            _prompt_and_save(save_history, prev_hex, captures_dir, label, state, notes)

    finally:
        sm.stop_background_keepalive()
        print("  Closing sessions...")
        try:
            await asyncio.wait_for(sm.close_all(), timeout=3.0)
        except (TimeoutError, Exception):
            pass
