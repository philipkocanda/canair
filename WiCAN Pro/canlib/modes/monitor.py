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
import time
from datetime import datetime

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
            # Show unique count when keeping history
            if hex_history and hex_key in hex_history:
                history_hexes = [h for h, _ts in hex_history[hex_key]]
                n_unique = len(history_hexes)
                if raw_hex and raw_hex not in history_hexes:
                    n_unique += 1  # current not yet added
                if n_unique > 1:
                    text.append(f"  ({n_unique} unique)", style="dim")
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
                                prefix_style="grey30" if ts else "",
                            )
                        )
                else:
                    prev_raw = prev_hex.get(hex_key, "") if prev_hex and cycle > 1 else ""
                    text.append_text(_render_hex_line(raw_hex, params, unmapped, prev_raw=prev_raw))

    text.append("\n  Press Ctrl+C to stop monitoring\n", style="dim")
    return text


async def mode_monitor(
    terminal,
    query_steps: list[dict],
    pids_data: dict,
    verbose: bool,
    interval: float = 5.0,
    session_steps: list[dict] | None = None,
    keep: bool = False,
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
    """
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
        hex_history: dict[tuple[str, str], list[tuple[str, str]]] = {} if keep else None

        with Live(
            _render_results([], verbose, 0, 0.0, interval),
            console=_console,
            refresh_per_second=4,
            transient=False,
        ) as live:
            try:
                while True:
                    cycle += 1
                    t0 = time.monotonic()

                    new_queries = []
                    for step in query_steps:
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
                        if result is not None:
                            new_queries.append(result)

                    last_queries = new_queries
                    elapsed = time.monotonic() - t0

                    # Record new payloads into history before rendering
                    for ecu_label, pid_results in new_queries:
                        for entry in pid_results:
                            raw = entry.get("raw_hex", "")
                            if raw:
                                key = (ecu_label, entry["pid"])
                                prev_hex[key] = raw
                                if hex_history is not None:
                                    existing = [h for h, _ts in hex_history.get(key, [])]
                                    if raw not in existing:
                                        ts = datetime.now().strftime("%H:%M:%S")
                                        hex_history.setdefault(key, []).append((raw, ts))

                    render = _render_results(
                        last_queries, verbose, cycle, elapsed, interval, prev_hex, hex_history
                    )
                    live.update(render)

                    remaining = interval - elapsed
                    if remaining > 0:
                        await asyncio.sleep(remaining)

            except ConnectionError:
                # Let Live exit cleanly, then print error outside
                pass
            except KeyboardInterrupt:
                print("\n  Monitoring stopped.")
                return

        # If we got here, it was a ConnectionError — print after Live has exited
        _console.print("\n  [bold red]✖ WebSocket disconnected[/bold red]")
        _console.print(f"  [red]Stopped after {cycle} cycles.[/red]\n")
        raise

    finally:
        sm.stop_background_keepalive()
        print("  Closing sessions...")
        try:
            await asyncio.wait_for(sm.close_all(), timeout=3.0)
        except (TimeoutError, Exception):
            pass
