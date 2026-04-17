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
"""

import asyncio
import time

from rich.console import Console
from rich.live import Live
from rich.text import Text

from ..session_manager import SessionManager
from ..formatting import format_value, _build_byte_colors

_console = Console(highlight=False)


def _bytes_to_ascii(raw_hex: str) -> str:
    data = bytes.fromhex(raw_hex)
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _render_hex_line(raw_hex: str, params: list, unmapped: bool) -> Text:
    """Render a hex line: green=verified, yellow=unverified, bright_black=uncovered/unmapped."""
    elm_bytes = [raw_hex[i : i + 2] for i in range(0, len(raw_hex), 2)]
    n_bytes = len(elm_bytes)
    t = Text()
    t.append("      ")

    if unmapped or not params:
        spaced = " ".join(elm_bytes)
        ascii_repr = _bytes_to_ascii(raw_hex)
        t.append(f"{spaced}  {ascii_repr}  ({n_bytes} B)", style="bright_black")
    else:
        byte_color = _build_byte_colors(params, n_bytes)
        for i, hb in enumerate(elm_bytes):
            if i > 0:
                t.append(" ")
            t.append(hb, style=byte_color[i])
        t.append(f"  ({n_bytes} B)", style="bright_black")

    t.append("\n")
    return t


def _render_results(
    queries: list[tuple[str, list]],
    verbose: bool,
    cycle: int,
    elapsed: float,
    interval: float,
) -> Text:
    """Render all ECU query results as a Rich Text object for Live display."""
    text = Text()

    text.append(
        f"  Monitor — cycle {cycle}  (last: {elapsed:.1f}s, interval: {interval:.1f}s)\n",
        style="dim",
    )

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

            text.append("    ")
            text.append(pid, style="yellow")
            if unmapped:
                text.append(" (unmapped)", style="dim")
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
                text.append_text(_render_hex_line(raw_hex, params, unmapped))

    text.append("\n  Press Ctrl+C to stop monitoring\n", style="dim")
    return text


async def mode_monitor(
    terminal,
    query_steps: list[dict],
    pids_data: dict,
    verbose: bool,
    interval: float = 5.0,
    session_steps: list[dict] | None = None,
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
                    await _exec_session(
                        sm, step["target"], step.get("wake", False), ecu_index
                    )

        # Start background keepalives
        sm.start_background_keepalive(interval=2.0)

        cycle = 0
        last_queries: list[tuple[str, list]] = []

        with Live(
            _render_results([], verbose, 0, 0.0, interval),
            console=_console,
            refresh_per_second=4,
            transient=False,
        ) as live:
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
                live.update(
                    _render_results(last_queries, verbose, cycle, elapsed, interval)
                )

                remaining = interval - elapsed
                if remaining > 0:
                    await asyncio.sleep(remaining)

    except KeyboardInterrupt:
        print("\n  Monitoring stopped.")
    finally:
        sm.stop_background_keepalive()
        print("  Closing sessions...")
        try:
            await asyncio.wait_for(sm.close_all(), timeout=3.0)
        except (asyncio.TimeoutError, Exception):
            pass
