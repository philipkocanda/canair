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
    changed_param_highlights,
    render_byte_rulers,
    render_param_ranges,
    render_param_table,
)
from .multi_batch import EcuFrame, ResultEntry

# Ordered display view modes cycled by the TUI 'V' key. Increasing detail:
#   ecus    — just the responding ECUs (+ a PID/signal count)
#   ranges  — each signal's captured value span (min-max / distinct labels)
#   signals — the decoded parameter table (no raw hex)
#   full    — signals + the raw byte payloads (the default)
VIEW_MODES = ("ecus", "ranges", "signals", "full")

# Cap how many history rows a single PID renders per cycle. With --keep-all a
# long drive accrues thousands of payloads per PID; rendering them all every
# cycle is O(cycles²·PIDs) and unbounded. The full history still lives in the
# journal (--save) — this only bounds the on-screen buffer to the newest rows.
_RENDER_MAX_ROWS = 200

# Default on-screen history depth for the implicit dedup display (the monitor
# default keep-mode). The live view dedups payloads globally for display, so a
# noisy PID can amass hundreds of distinct rows over a session; rendering them all
# every cycle both clutters the view and (on a big multi-PID sweep) can make the
# frame large enough to choke the TUI. Keep the live view compact — the full set
# is still in hex_history (and the journal when --save). Overridable per keep-mode.
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
    prev_values: dict[str, object] | None,
    history: list[tuple[str, str]] | None,
    view_mode: str = "full",
    stat_sig: tuple | None = None,
) -> tuple:
    """A hashable signature of everything that affects one PID entry's render.

    Two renders with equal signatures produce byte-identical output, so a match
    lets :class:`RenderCache` reuse the prior block. ``params`` are flattened to
    a tuple (name/value/unit/expr/error/verified/display) because their values
    and verification state drive both the table and the per-byte colours.
    ``view_mode`` and ``stat_sig`` (the ranges-view accumulated span) are part of
    the signature so a view switch or a moving range invalidates the cache.
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
        tuple(sorted(prev_values.items())) if prev_values is not None else None,
        tuple(history) if history is not None else None,
        params,
        view_mode,
        stat_sig,
    )


def _stat_signature(stat: dict) -> tuple:
    """A hashable signature of a signal's accumulated range (for the ranges view)."""
    return (
        stat.get("unit", ""),
        bool(stat.get("verified")),
        stat.get("n", 0),
        stat.get("min"),
        stat.get("max"),
        tuple(stat.get("values") or ()),
        int(stat.get("overflow", 0)),
    )


def _pid_stat_signature(pid_stats: dict) -> tuple:
    """Signature over all signals' ranges for one PID (order-stable)."""
    return tuple((name, _stat_signature(s)) for name, s in pid_stats.items())


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
    prev_values: dict[str, object] | None,
    history: list[tuple[str, str]] | None,
    view_mode: str = "full",
    pid_stats: dict | None = None,
) -> Text:
    """Render one PID entry (mark line, param table, hex/history) to its own Text.

    Rendered as a self-contained block so a stale (timed-out) PID can be dimmed
    as a unit — its last-good values stay on screen (greyed) instead of
    collapsing to an error line and jolting layout.

    ``view_mode`` selects how much of the entry is drawn: ``"ranges"`` shows each
    signal's accumulated value span (from ``pid_stats``) instead of the live
    table; ``"signals"`` shows the decoded table but omits the raw hex payload;
    ``"full"`` (the default) shows the table and the hex/history lines.
    """
    pid = entry["pid"]
    error = entry.get("error")
    params = entry.get("params", [])
    raw_hex = entry.get("raw_hex", "")
    decode = entry.get("decode")
    unmapped = entry.get("unmapped", False)
    stale = entry.get("stale", False)
    show_hex = view_mode == "full"

    entry_text = Text()
    entry_text.append("    ")
    entry_text.append(pid, style="yellow")
    if changed and not stale:
        entry_text.append(" ●", style="bright_green")
    if stale:
        entry_text.append(" (stale)", style="dim")
    if unmapped:
        entry_text.append(" (unmapped)", style="dim")
    # Show history count when keeping history (full view only — the count refers
    # to the raw-payload history the other views don't draw).
    if history is not None and show_hex:
        n_entries = len(history)
        if raw_hex and raw_hex not in [h for h, _ts in history]:
            n_entries += 1  # current not yet added
        if n_entries > 1:
            entry_text.append(f"  ({n_entries} entries)", style="dim")
    if error:
        entry_text.append(f"  {error}\n", style="red")
        return entry_text
    entry_text.append("\n")

    # "ranges" view: the accumulated value span per signal, not the live table.
    if view_mode == "ranges":
        if pid_stats:
            entry_text.append_text(render_param_ranges(pid_stats, selected_name=sel_name))
        elif params:
            # No accumulated stats yet (first cycle / error-only) — fall back to
            # the live table so the row isn't blank.
            entry_text.append_text(
                render_param_table(params, verbose=verbose, selected_name=sel_name)
            )
        elif decode:
            entry_text.append(f"      {decode}\n")
        if stale:
            entry_text.stylize("dim")
        return entry_text

    if params:
        # With rulers on, annotate each param with the payload byte index(es) it
        # maps to (e.g. "16-17"), matching the diff view.
        n_bytes = len(raw_hex) // 2 if (show_rulers and raw_hex and show_hex) else None
        # Highlight the param(s) whose *decoded value* just changed, with the
        # same background the changed byte gets in the hex line (skipped when
        # stale — a timed-out reuse of last-good data is not a live change). The
        # per-byte hex highlight still tracks raw byte changes; only the param
        # name/value cells are gated on the interpreted value moving.
        changed_styles = (
            changed_param_highlights(params, raw_hex, prev_raw, prev_values)
            if raw_hex and not stale
            else {}
        )
        entry_text.append_text(
            render_param_table(
                params,
                verbose=verbose,
                n_bytes=n_bytes,
                selected_name=sel_name,
                changed_styles=changed_styles,
            )
        )
    elif decode:
        entry_text.append(f"      {decode}\n")

    if raw_hex and show_hex:
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
            # --keep-all (or noisy dedup) run stays cheap to render (full
            # data is retained in memory / the journal).
            if len(all_entries) > row_cap:
                omitted = len(all_entries) - row_cap
                all_entries = all_entries[-row_cap:]
                entry_text.append(f"      … {omitted} earlier entries hidden\n", style="dim")
            for i, (payload, ts) in enumerate(all_entries):
                prev_row = all_entries[i - 1][0] if i > 0 else ""
                # Display seconds resolution only (sub-second precision is kept in
                # the stored capture `time` for cross-signal alignment, not shown).
                ts_disp = ts.split(".")[0] if ts else ""
                prefix = f"      {ts_disp}  " if ts_disp else "                "
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
    prev_params: dict[tuple[str, str], dict[str, object]] | None = None,
    hex_history: dict[tuple[str, str], list[tuple[str, str]]] | None = None,
    show_rulers: bool = False,
    footer: bool = True,
    selected: tuple[str, str, str] | None = None,
    max_history_rows: int | None = None,
    cache: RenderCache | None = None,
    view_mode: str = "full",
    param_stats=None,
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

    ``prev_params`` is the previous cycle's decoded ``{param_name: value}`` per
    PID key; it gates the param name/value change-highlight on the *interpreted*
    value moving (the per-byte hex highlight still tracks raw byte changes).
    ``None`` falls back to the raw-byte-change heuristic.

    ``cache`` (a :class:`RenderCache`) reuses per-PID blocks whose inputs are
    unchanged since the last render, avoiding the per-byte Text churn and
    expression re-parsing for static PIDs. ``None`` renders everything fresh.

    ``view_mode`` (see :data:`VIEW_MODES`) selects the presentation: ``"ecus"``
    lists only the responding ECUs (+ a PID/signal count); ``"ranges"`` shows
    each signal's accumulated value span (from ``param_stats``, a
    :class:`~canlib.modes._monitor_stats.ParamStats`); ``"signals"`` shows the
    decoded table without the raw hex; ``"full"`` (default) shows table + hex.
    """
    row_cap = _RENDER_MAX_ROWS if max_history_rows is None else max(1, max_history_rows)
    text = Text()

    view_note = "" if view_mode == "full" else f"  ·  view: {view_mode}"
    text.append(
        f"  Monitor — cycle {cycle}  (last: {elapsed:.1f}s, interval: {interval:.1f}s)"
        f"{view_note}\n",
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

        # "ecus" view: just the responding ECU + a compact count, no per-PID rows.
        if view_mode == "ecus":
            for entry in pid_results:
                live_keys.add((ecu_label, entry["pid"]))
            n_pids = len(pid_results)
            n_signals = sum(len(e.get("params", [])) for e in pid_results)
            fresh = sum(
                1
                for e in pid_results
                if cycle > 1
                and e.get("raw_hex")
                and prev_hex.get((ecu_label, e["pid"])) not in (None, e.get("raw_hex"))
            )
            fresh_note = f" · {fresh} changed" if fresh else ""
            text.append(f"    {n_pids} PID(s) · {n_signals} signal(s){fresh_note}\n", style="dim")
            continue

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
            prev_values = (
                prev_params.get(hex_key) if prev_params is not None and cycle > 1 else None
            )
            history = hex_history[hex_key] if hex_history and hex_key in hex_history else None
            pid_stats = param_stats.for_pid(hex_key) if param_stats is not None else None
            stat_sig = (
                _pid_stat_signature(pid_stats) if (view_mode == "ranges" and pid_stats) else None
            )

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
                    prev_values=prev_values,
                    history=history,
                    view_mode=view_mode,
                    stat_sig=stat_sig,
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
                        prev_values=prev_values,
                        history=history,
                        view_mode=view_mode,
                        pid_stats=pid_stats,
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
                    prev_values=prev_values,
                    history=history,
                    view_mode=view_mode,
                    pid_stats=pid_stats,
                )
            text.append_text(block)

    if cache is not None:
        cache.prune(live_keys)

    if footer:
        text.append("\n  Press Ctrl+C to stop monitoring\n", style="dim")
    return text
