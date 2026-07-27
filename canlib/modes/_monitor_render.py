"""Live monitor — result rendering.

The pure presentation helper that turns a cycle's decoded query results into a
Rich :class:`~rich.text.Text` block. Shared by the Textual TUI
(:mod:`canlib.modes._monitor_tui`) and the non-interactive fallback, so both
paths render identically. Kept out of :mod:`canlib.modes.monitor` (which owns
the polling/recording controller) so the ~150-line renderer is its own concern.
"""

from rich.text import Text

from ..formatting import (
    _render_hex_line,
    render_byte_rulers,
    render_param_table,
)

# Cap how many history rows a single PID renders per cycle. With --keep-all a
# long drive accrues thousands of payloads per PID; rendering them all every
# cycle is O(cycles²·PIDs) and unbounded. The full history still lives in the
# journal (--save) — this only bounds the on-screen buffer to the newest rows.
_RENDER_MAX_ROWS = 200


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
    selected: tuple[str, str, str] | None = None,
) -> Text:
    """Render all ECU query results as a Rich Text object for display.

    ``footer`` appends the "Press Ctrl+C to stop" hint (kept for callers that
    render a single static block). The scrolling monitor passes ``footer=False``
    and draws its own fixed status line below the scroll viewport.

    ``selected`` is an ``(ecu_label, pid, param_name)`` triple naming the
    parameter row the monitor is targeting for in-place editing; that row is
    drawn with a ``▶`` cursor.
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
            stale = entry.get("stale", False)

            # Detect change from previous cycle
            hex_key = (ecu_label, pid)
            changed = cycle > 1 and raw_hex and hex_key in prev_hex and prev_hex[hex_key] != raw_hex

            # Render the whole entry into its own Text so a stale (timed-out) PID
            # can be dimmed as a unit — its last-good values stay on screen
            # (greyed) instead of collapsing to an error line and jolting layout.
            entry_text = Text()
            entry_text.append("    ")
            entry_text.append(pid, style="yellow")
            if changed and not stale:
                entry_text.append(" ●", style="bright_green")
            if stale:
                entry_text.append(" (stale)", style="dim")
            if unmapped:
                entry_text.append(" (unmapped)", style="dim")
            # Show history count when keeping history
            if hex_history and hex_key in hex_history:
                n_entries = len(hex_history[hex_key])
                if raw_hex and raw_hex not in [h for h, _ts in hex_history[hex_key]]:
                    n_entries += 1  # current not yet added
                if n_entries > 1:
                    entry_text.append(f"  ({n_entries} entries)", style="dim")
            if error:
                entry_text.append(f"  {error}\n", style="red")
                text.append_text(entry_text)
                continue
            entry_text.append("\n")

            if params:
                # With rulers on, annotate each param with the payload byte
                # index(es) it maps to (e.g. "16-17"), matching the diff view.
                n_bytes = len(raw_hex) // 2 if (show_rulers and raw_hex) else None
                sel_name = (
                    selected[2]
                    if selected is not None and selected[0] == ecu_label and selected[1] == pid
                    else None
                )
                entry_text.append_text(
                    render_param_table(
                        params, verbose=verbose, n_bytes=n_bytes, selected_name=sel_name
                    )
                )
            elif decode:
                entry_text.append(f"      {decode}\n")

            if raw_hex:
                # Byte-index ruler, once per PID, above the hex lines.
                if show_rulers:
                    ruler_pw = 16 if hex_history is not None else 6
                    entry_text.append_text(
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
                    # Bound the rendered rows to the newest _RENDER_MAX_ROWS so a
                    # long --keep-all run stays cheap to render (full data is in
                    # the journal). Older rows are summarized, not walked.
                    if len(all_entries) > _RENDER_MAX_ROWS:
                        omitted = len(all_entries) - _RENDER_MAX_ROWS
                        all_entries = all_entries[-_RENDER_MAX_ROWS:]
                        entry_text.append(
                            f"      … {omitted} earlier entries omitted (in journal)\n",
                            style="dim",
                        )
                    for i, (payload, ts) in enumerate(all_entries):
                        prev_raw = all_entries[i - 1][0] if i > 0 else ""
                        prefix = f"      {ts}  " if ts else "                "
                        entry_text.append_text(
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
                    entry_text.append_text(
                        _render_hex_line(raw_hex, params, unmapped, prev_raw=prev_raw)
                    )

            if stale:
                entry_text.stylize("dim")  # grey the whole PID block
            text.append_text(entry_text)

    if footer:
        text.append("\n  Press Ctrl+C to stop monitoring\n", style="dim")
    return text
