"""Live monitor mode — repeatedly polls a set of ECU PIDs and refreshes the display.

Uses Rich Live to render the latest values in-place without scrolling. Each
query cycle replaces the previous output, giving a real-time view of changing
parameter values (SOC, temps, voltages, etc.).

Usage (via --multi --monitor):
    canreq --multi "session BCM --wake" "query BCM C00B B00E" --monitor
    canreq --multi "query BMS 2101" --monitor 2.0
    canreq --multi "session IGPM --wake" "query IGPM BC03 BC06" --monitor

The --monitor flag applies to the last 'query' step in the pipeline. If
there are multiple query steps, all of them are repeated each cycle.

NOTE on terminal rendering: Rich Live with transient=False leaves artifacts on
terminal resize (duplicate renders). The clean solution would be to use the
alternate screen buffer — in Python that's Textual (https://github.com/Textualize/textual),
in Go that's Bubble Tea (https://github.com/charmbracelet/bubbletea, same approach
used by OpenCode's TUI). Refactoring this tool to Go + Bubble Tea would be a fun
exercise and would eliminate all the Rich Live quirks.
"""

import asyncio
import re
import signal
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.text import Text

from ..formatting import _build_byte_colors, format_value
from ..session_manager import SessionManager

_console = Console(highlight=False)


def _bytes_to_ascii(raw_hex: str) -> str:
    data = bytes.fromhex(raw_hex)
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


# Map base byte color → highlighted variant (changed byte)
_HIGHLIGHT_STYLE = {
    "green": "bold white on dark_green",
    "yellow": "bold white on dark_goldenrod",
    "bright_black": "bold white on grey37",
}


def _render_hex_line(
    raw_hex: str,
    params: list,
    unmapped: bool,
    *,
    prev_raw: str = "",
    prefix: str = "      ",
    prefix_style: str = "",
) -> Text:
    """Render a hex line with per-byte change highlighting.

    Changed bytes get a background color adapted from their base color.
    prefix is prepended before the hex bytes (default: 6 spaces of indent).
    """
    elm_bytes = [raw_hex[i : i + 2] for i in range(0, len(raw_hex), 2)]
    prev_bytes = [prev_raw[i : i + 2] for i in range(0, len(prev_raw), 2)] if prev_raw else []
    n_bytes = len(elm_bytes)
    t = Text()
    t.append(prefix, style=prefix_style)

    if unmapped or not params:
        for i, hb in enumerate(elm_bytes):
            if i > 0:
                t.append(" ")
            changed = i < len(prev_bytes) and prev_bytes[i] != hb
            style = _HIGHLIGHT_STYLE["bright_black"] if changed else "bright_black"
            t.append(hb, style=style)
        ascii_repr = _bytes_to_ascii(raw_hex)
        t.append(f"  {ascii_repr}  ({n_bytes} B)", style="bright_black")
    else:
        byte_color = _build_byte_colors(params, n_bytes)
        for i, hb in enumerate(elm_bytes):
            if i > 0:
                t.append(" ")
            base = byte_color[i]
            changed = i < len(prev_bytes) and prev_bytes[i] != hb
            style = _HIGHLIGHT_STYLE.get(base, base) if changed else base
            t.append(hb, style=style)
        t.append(f"  ({n_bytes} B)", style="bright_black")

    t.append("\n")
    return t


def _render_results(
    queries: list[tuple[str, list]],
    verbose: bool,
    cycle: int,
    elapsed: float,
    interval: float,
    prev_hex: dict[tuple[str, str], str] | None = None,
    hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = None,
) -> Text:
    """Render all ECU query results as a Rich Text object for Live display."""
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
                max_name = max(len(r[0]) for r in params)
                max_val = max(
                    len(
                        format_value(r[1], r[2], r[6] if len(r) > 6 else "")
                        if r[1] is not None
                        else "ERROR"
                    )
                    for r in params
                )
                for row in params:
                    name, value, unit, expression, perr, verified = row[:6]
                    display = row[6] if len(row) > 6 else ""
                    mark_style = "green" if verified else "yellow"
                    mark_char = "✓" if verified else "?"
                    if perr:
                        text.append(f"      {name:<{max_name}}  ")
                        text.append(f"ERROR: {perr}\n", style="red")
                    else:
                        val_str = format_value(value, unit, display)
                        text.append(f"      {name:<{max_name}}  ")
                        if verbose:
                            text.append(f"{val_str:<{max_val}}  ")
                            text.append(mark_char, style=mark_style)
                            text.append(f"  {expression}\n", style="dim")
                        else:
                            text.append(f"{val_str:<{max_val}}  ")
                            text.append(mark_char + "\n", style=mark_style)
            elif decode:
                text.append(f"      {decode}\n")

            if raw_hex:
                hex_key = (ecu_label, pid)
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

    text.append("\n  Press Ctrl+C to stop monitoring\n", style="dim")
    return text


def _prompt_and_save(
    hex_history: dict[tuple[str, str], list[tuple[str, str]]],
    prev_hex: dict[tuple[str, str], str],
    captures_dir: Path,
    pids_data: dict | None = None,
) -> None:
    """Prompt for session metadata and write captures to YAML file.

    Collects label, state, and notes via stdin prompts, then appends a new
    session with all unique payloads to captures/YYYY-MM-DD.yaml.
    If pids_data is provided, decoded parameter values are included per capture.
    """
    from ..captures import prompt_metadata, save_session, _decode_payload

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
            ts = datetime.now().strftime("%H:%M:%S")
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

    # Prompt for metadata
    meta = prompt_metadata(suggested_label="Monitor session")
    if meta is None:
        return
    label, state, notes = meta

    # Build captures list, grouped by ECU then PID
    captures = []
    for (ecu_label, pid), entries in sorted(merged.items()):
        ecu_short = re.match(r"(\w+)", ecu_label).group(1)

        for hex_val, ts in entries:
            capture: dict = {
                "ecu": ecu_short,
                "pid": pid,
                "payload": hex_val.upper(),
            }
            if ts:
                capture["time"] = ts

            # Decode parameters from payload
            if pids_data:
                decoded = _decode_payload(ecu_short, pid, hex_val, pids_data)
                if decoded:
                    capture["decoded"] = decoded

            captures.append(capture)

    # Build session entry
    today = datetime.now().strftime("%Y-%m-%d")
    session: dict = {"date": today, "label": label}
    if state:
        session["state"] = state
    if notes:
        session["notes"] = notes + "\n"
    session["captures"] = captures

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
):
    """Live-refresh ECU parameter monitor.

    Executes the given query_steps repeatedly, refreshing the display
    in-place via Rich Live. Sessions are opened once (from session_steps)
    and kept alive with background keepalives.

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
        save:           On Ctrl+C, prompt for metadata and save to captures/.
    """
    from ..captures import CAPTURES_DIR
    from ..pids import build_ecu_index
    from .multi import _exec_query, _exec_session, _exec_skm_wake

    ecu_index = build_ecu_index(pids_data)
    sm = SessionManager(terminal, verbose=verbose)

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

        cycle = 0
        last_queries: list[tuple[str, list]] = []
        prev_hex: dict[tuple[str, str], str] = {}
        hex_history: dict[tuple[str, str], list[tuple[str, str]]] = {} if keep_mode else None
        save_history: dict[tuple[str, str], list[tuple[str, str]]] = {} if save else None
        stop_requested = False

        def _handle_sigint(_sig, _frame):
            nonlocal stop_requested
            stop_requested = True

        old_handler = signal.signal(signal.SIGINT, _handle_sigint)

        try:
            with Live(
                _render_results([], verbose, 0, 0.0, interval),
                console=_console,
                refresh_per_second=4,
                transient=False,
            ) as live:
                disconnected = False
                while not stop_requested:
                    cycle += 1
                    t0 = time.monotonic()

                    new_queries = []
                    for step in query_steps:
                        if stop_requested:
                            break
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
                            break
                        if result is not None:
                            new_queries.append(result)

                    if disconnected:
                        break

                    last_queries = new_queries
                    elapsed = time.monotonic() - t0

                    # Record new payloads into history before rendering
                    for ecu_label, pid_results in new_queries:
                        for entry in pid_results:
                            raw = entry.get("raw_hex", "")
                            if raw:
                                key = (ecu_label, entry["pid"])
                                prev_hex[key] = raw
                                ts = datetime.now().strftime("%H:%M:%S")
                                # Save history (for --save): always keep all
                                if save_history is not None:
                                    save_history.setdefault(key, []).append((raw, ts))
                                # Display history (for --keep flags)
                                if hex_history is not None:
                                    if keep_mode == "all" or keep_mode == "last":
                                        hex_history.setdefault(key, []).append((raw, ts))
                                        if keep_mode == "last" and keep_n and len(hex_history[key]) > keep_n:
                                            hex_history[key] = hex_history[key][-keep_n:]
                                    else:
                                        # "unique" mode: only store if not seen before
                                        existing = [h for h, _ts in hex_history.get(key, [])]
                                        if raw not in existing:
                                            hex_history.setdefault(key, []).append((raw, ts))

                    render = _render_results(
                        last_queries, verbose, cycle, elapsed, interval, prev_hex, hex_history
                    )
                    live.update(render)

                    # Sleep in small increments so we can check stop_requested
                    remaining = interval - (time.monotonic() - t0)
                    while remaining > 0 and not stop_requested:
                        await asyncio.sleep(min(remaining, 0.2))
                        remaining = interval - (time.monotonic() - t0)

        finally:
            signal.signal(signal.SIGINT, old_handler)

        if stop_requested:
            print("\n  Monitoring stopped.")
            if save and save_history is not None:
                captures_dir = CAPTURES_DIR
                _prompt_and_save(save_history, prev_hex, captures_dir, pids_data)
            return

        # If we got here, it was a ConnectionError
        _console.print("\n  [bold red]✖ WebSocket disconnected[/bold red]")
        _console.print(f"  [red]Stopped after {cycle} cycles.[/red]\n")
        raise ConnectionError("WebSocket disconnected")

    finally:
        sm.stop_background_keepalive()
        print("  Closing sessions...")
        try:
            await asyncio.wait_for(sm.close_all(), timeout=3.0)
        except (TimeoutError, Exception):
            pass
