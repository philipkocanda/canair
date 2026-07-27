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
from .multi_batch import EcuFrame, ResultEntry

# Cap how many history rows a single PID renders per cycle. With --keep-all a
# long drive accrues thousands of payloads per PID; rendering them all every
# cycle is O(cycles²·PIDs) and unbounded. The full history still lives in the
# journal (--save) — this only bounds the on-screen buffer to the newest rows.
_RENDER_MAX_ROWS = 200

# Default on-screen history depth for the implicit --keep-unique mode (now the
# monitor default). Unique-dedup accrues one row per *distinct* payload, so a
# noisy PID can amass hundreds over a session; rendering them all every cycle
# both clutters the view and (on a big multi-PID sweep) can make the frame large
# enough to choke the TUI. Keep the live view compact — the full set is still in
# hex_history (and the journal when --save). Overridable per keep-mode.
_RENDER_DEFAULT_ROWS = 4


class RenderCache:
    """Per-PID rendered-block cache for the live monitor.

    A monitor repaints its whole body on every poll cycle *and* on every
    mid-cycle partial resolve, but between paints most PIDs are unchanged. Each
    PID entry's rendered :class:`~rich.text.Text` is deterministic in its
    inputs, so we key the block on a signature of everything that affects its
    output and reuse the cached block on a hit — skipping the per-byte
    ``Text.append`` churn and the expression byte-index re-parsing that
    dominated render cost.

    A cached ``Text`` is only ever appended into a parent via
    ``Text.append_text`` (which copies, not mutates), so sharing one cached
    instance across many renders is safe. Held by the monitor controller so it
    persists across paints; a fresh cache renders everything once.
    """

    def __init__(self) -> None:
        self._blocks: dict[tuple[str, str], tuple[tuple, Text]] = {}

    def get(self, key: tuple[str, str], sig: tuple) -> Text | None:
        hit = self._blocks.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
        return None

    def put(self, key: tuple[str, str], sig: tuple, block: Text) -> None:
        self._blocks[key] = (sig, block)

    def prune(self, live_keys: set[tuple[str, str]]) -> None:
        """Drop cached blocks for PIDs no longer polled (e.g. after a filter change)."""
        for key in [k for k in self._blocks if k not in live_keys]:
            del self._blocks[key]


def _entry_signature(
    entry: ResultEntry,
    *,
    changed: bool,
    verbose: bool,
    show_rulers: bool,
    is_selected: bool,
    sel_name: str | None,
    row_cap: int,
    prev_raw: str,
    history: list[tuple[str, str]] | None,
) -> tuple:
    """A hashable signature of everything that affects one PID entry's render.

    Two renders with equal signatures produce byte-identical output, so a match
    lets :class:`RenderCache` reuse the prior block. ``params`` are flattened to
    a tuple (name/value/unit/expr/error/verified/display) because their values
    and verification state drive both the table and the per-byte colours.
    """
    params = tuple(tuple(row) for row in entry.get("params", []))
    return (
        entry.get("pid", ""),
        entry.get("raw_hex", ""),
        entry.get("error"),
        entry.get("decode"),
        bool(entry.get("unmapped", False)),
        bool(entry.get("stale", False)),
        changed,
        verbose,
        show_rulers,
        is_selected,
        sel_name if is_selected else None,
        row_cap,
        prev_raw,
        tuple(history) if history is not None else None,
        params,
    )


def _render_entry(
    entry: ResultEntry,
    ecu_label: str,
    *,
    changed: bool,
    verbose: bool,
    show_rulers: bool,
    sel_name: str | None,
    row_cap: int,
    prev_raw: str,
    history: list[tuple[str, str]] | None,
) -> Text:
    """Render one PID entry (mark line, param table, hex/history) to its own Text.

    Rendered as a self-contained block so a stale (timed-out) PID can be dimmed
    as a unit — its last-good values stay on screen (greyed) instead of
    collapsing to an error line and jolting layout.
    """
    pid = entry["pid"]
    error = entry.get("error")
    params = entry.get("params", [])
    raw_hex = entry.get("raw_hex", "")
    decode = entry.get("decode")
    unmapped = entry.get("unmapped", False)
    stale = entry.get("stale", False)

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
    if history is not None:
        n_entries = len(history)
        if raw_hex and raw_hex not in [h for h, _ts in history]:
            n_entries += 1  # current not yet added
        if n_entries > 1:
            entry_text.append(f"  ({n_entries} entries)", style="dim")
    if error:
        entry_text.append(f"  {error}\n", style="red")
        return entry_text
    entry_text.append("\n")

    if params:
        # With rulers on, annotate each param with the payload byte index(es) it
        # maps to (e.g. "16-17"), matching the diff view.
        n_bytes = len(raw_hex) // 2 if (show_rulers and raw_hex) else None
        entry_text.append_text(
            render_param_table(params, verbose=verbose, n_bytes=n_bytes, selected_name=sel_name)
        )
    elif decode:
        entry_text.append(f"      {decode}\n")

    if raw_hex:
        # Byte-index ruler, once per PID, above the hex lines.
        if show_rulers:
            ruler_pw = 16 if history is not None else 6
            entry_text.append_text(
                render_byte_rulers(len(raw_hex) // 2, params, prefix_width=ruler_pw)
            )
        if history is not None:
            # Show all unique payloads chronologically, each diffed against predecessor
            history_hexes = [h for h, _ts in history]
            # Include current if not yet in history (first cycle edge case)
            if raw_hex not in history_hexes:
                all_entries = [*history, (raw_hex, "")]
            else:
                all_entries = list(history)
            # Bound the rendered rows to the newest ``row_cap`` so a long
            # --keep-all (or noisy --keep-unique) run stays cheap to render (full
            # data is retained in memory / the journal).
            if len(all_entries) > row_cap:
                omitted = len(all_entries) - row_cap
                all_entries = all_entries[-row_cap:]
                entry_text.append(f"      … {omitted} earlier entries hidden\n", style="dim")
            for i, (payload, ts) in enumerate(all_entries):
                prev_row = all_entries[i - 1][0] if i > 0 else ""
                prefix = f"      {ts}  " if ts else "                "
                entry_text.append_text(
                    _render_hex_line(
                        payload,
                        params,
                        unmapped,
                        prev_raw=prev_row,
                        prefix=prefix,
                        prefix_style="dim" if ts else "",
                    )
                )
        else:
            entry_text.append_text(_render_hex_line(raw_hex, params, unmapped, prev_raw=prev_raw))

    if stale:
        entry_text.stylize("dim")  # grey the whole PID block
    return entry_text


def _render_results(
    queries: list[EcuFrame],
    verbose: bool,
    cycle: int,
    elapsed: float,
    interval: float,
    prev_hex: dict[tuple[str, str], str] | None = None,
    hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = None,
    show_rulers: bool = False,
    footer: bool = True,
    selected: tuple[str, str, str] | None = None,
    max_history_rows: int | None = None,
    cache: RenderCache | None = None,
) -> Text:
    """Render all ECU query results as a Rich Text object for display.

    ``footer`` appends the "Press Ctrl+C to stop" hint (kept for callers that
    render a single static block). The scrolling monitor passes ``footer=False``
    and draws its own fixed status line below the scroll viewport.

    ``selected`` is an ``(ecu_label, pid, param_name)`` triple naming the
    parameter row the monitor is targeting for in-place editing; that row is
    drawn with a ``▶`` cursor.

    ``max_history_rows`` bounds how many history rows each PID renders (newest
    kept); ``None`` falls back to :data:`_RENDER_MAX_ROWS`.

    ``cache`` (a :class:`RenderCache`) reuses per-PID blocks whose inputs are
    unchanged since the last render, avoiding the per-byte Text churn and
    expression re-parsing for static PIDs. ``None`` renders everything fresh.
    """
    row_cap = _RENDER_MAX_ROWS if max_history_rows is None else max(1, max_history_rows)
    text = Text()

    text.append(
        f"  Monitor — cycle {cycle}  (last: {elapsed:.1f}s, interval: {interval:.1f}s)\n",
        style="dim",
    )

    if prev_hex is None:
        prev_hex = {}

    live_keys: set[tuple[str, str]] = set()
    for ecu_label, pid_results in queries:
        if not pid_results:
            continue

        text.append("\n  ")
        text.append(ecu_label, style="bold cyan")
        text.append("\n")

        for entry in pid_results:
            pid = entry["pid"]
            raw_hex = entry.get("raw_hex", "")
            hex_key = (ecu_label, pid)
            live_keys.add(hex_key)

            # Change from the previous cycle (drives the "●" fresh-data marker).
            changed = cycle > 1 and raw_hex and hex_key in prev_hex and prev_hex[hex_key] != raw_hex
            is_selected = selected is not None and selected[0] == ecu_label and selected[1] == pid
            sel_name = selected[2] if is_selected else None
            prev_raw = prev_hex.get(hex_key, "") if cycle > 1 else ""
            history = hex_history[hex_key] if hex_history and hex_key in hex_history else None

            if cache is not None:
                sig = _entry_signature(
                    entry,
                    changed=bool(changed),
                    verbose=verbose,
                    show_rulers=show_rulers,
                    is_selected=is_selected,
                    sel_name=sel_name,
                    row_cap=row_cap,
                    prev_raw=prev_raw,
                    history=history,
                )
                block = cache.get(hex_key, sig)
                if block is None:
                    block = _render_entry(
                        entry,
                        ecu_label,
                        changed=bool(changed),
                        verbose=verbose,
                        show_rulers=show_rulers,
                        sel_name=sel_name,
                        row_cap=row_cap,
                        prev_raw=prev_raw,
                        history=history,
                    )
                    cache.put(hex_key, sig, block)
            else:
                block = _render_entry(
                    entry,
                    ecu_label,
                    changed=bool(changed),
                    verbose=verbose,
                    show_rulers=show_rulers,
                    sel_name=sel_name,
                    row_cap=row_cap,
                    prev_raw=prev_raw,
                    history=history,
                )
            text.append_text(block)

    if cache is not None:
        cache.prune(live_keys)

    if footer:
        text.append("\n  Press Ctrl+C to stop monitoring\n", style="dim")
    return text
