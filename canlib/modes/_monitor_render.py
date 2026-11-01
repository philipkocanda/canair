"""Live monitor — result rendering.

The pure presentation helper that turns a cycle's decoded query results into a
Rich :class:`~rich.text.Text` block. Shared by the Textual TUI
(:mod:`canlib.modes._monitor_tui`) and the non-interactive fallback, so both
paths render identically. Kept out of :mod:`canlib.modes.monitor` (which owns
the polling/recording controller) so the ~150-line renderer is its own concern.
"""

import time

from rich.text import Text

from canlib.uds.notation import ByteDisplay, ByteNotation, subfunction_bytes_for_pid

from ..formatting import (
    _render_hex_line,
    changed_param_highlights,
    render_byte_rulers,
    render_param_ranges,
    render_param_table,
)
from ._monitor_history import HistoryRun, PayloadHistory
from .multi_batch import EcuFrame, ResultEntry

# Ordered display view modes cycled by the TUI 'V' key. Increasing detail:
#   ecus    — just the responding ECUs (+ a PID/signal count)
#   ranges  — each signal's captured value span (min-max / distinct labels)
#   signals — the decoded signal table (no raw hex)
#   full    — signals + the raw byte payloads (the default)
VIEW_MODES = ("ecus", "ranges", "signals", "full")

# Cap how many history rows a single PID renders per cycle when the caller sets
# none. A noisy PID accrues thousands of runs over a long drive; rendering them all
# every cycle is O(cycles²·PIDs) and unbounded. The full history still lives in the
# journal (--save) — this only bounds the on-screen buffer to the newest rows.
_RENDER_MAX_ROWS = 200

# Default on-screen history depth (runs of identical payloads per PID). A noisy PID
# can amass hundreds of runs over a session; rendering them all every cycle both
# clutters the view and (on a big multi-PID sweep) can make the frame large enough
# to choke the TUI. Keep the live view compact — every poll is still in the journal
# when --save. `--keep N` overrides it.
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
    history: list[HistoryRun] | None,
    view_mode: str = "full",
    stat_sig: tuple | None = None,
    notation: ByteNotation = ByteNotation.WICAN,
    stale_label: str = "",
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
        stale_label,
        changed,
        verbose,
        show_rulers,
        is_selected,
        sel_name if is_selected else None,
        row_cap,
        prev_raw,
        tuple(sorted(prev_values.items())) if prev_values is not None else None,
        # The rendered tail plus the run count: the rows above the cap only show as
        # the "earlier entries hidden" count, so hashing them is wasted work.
        (len(history), tuple(history[-(row_cap + 1) :])) if history is not None else None,
        params,
        view_mode,
        stat_sig,
        notation,
    )


def stale_age(entry: ResultEntry, now: float) -> str:
    """How long a stale entry's last-good value has been held, e.g. ``12s``, ``3m``.

    Coarse on purpose: the label is part of the render-cache signature, so it should
    change no more often than a reader can use.
    """
    acquired = entry.get("acquired_at")
    if not entry.get("stale") or not acquired:
        return ""
    secs = max(0, int(now - acquired))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{secs % 3600 // 60:02d}m"


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
    history: list[HistoryRun] | None,
    view_mode: str = "full",
    pid_stats: dict | None = None,
    notation: ByteNotation = ByteNotation.WICAN,
    stale_label: str = "",
) -> Text:
    """Render one PID entry (mark line, param table, hex/history) to its own Text.

    Rendered as a self-contained block so a stale (timed-out) PID can be dimmed
    as a unit — its last-good values stay on screen (greyed) instead of
    collapsing to an error line and jolting layout.

    ``view_mode`` selects how much of the entry is drawn: ``"ranges"`` shows each
    signal's accumulated value span (from ``pid_stats``) instead of the live
    table; ``"signals"`` shows the decoded table but omits the raw hex payload;
    ``"full"`` (the default) shows the table and the hex/history lines.

    ``notation`` is the byte-index notation the ruler and each signal's
    byte-reference column are drawn in (the user's ``display.byte_notation``).

    ``stale_label`` (see :func:`stale_age`) says how long a stale value has been held.
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
    entry_text.append(" ")
    # Freshness indicator: a small hollow circle, colored green while the value
    # came from this cycle's own answer and grey once it's a carried-forward
    # last-good reuse (see _decide_display). Deliberately a *different* glyph
    # from the solid "●" change flash below (bright_green, only for one cycle)
    # so the two never look like the same dot doubling up, and grepping for the
    # plain "●" (as existing "changed"/"unchanged" tests do) is unaffected by
    # this being present on every row.
    entry_text.append("○", style="grey58" if stale else "green")
    if changed and not stale:
        entry_text.append(" ●", style="bright_green")
    if stale:
        held = f" {stale_label}" if stale_label else ""
        entry_text.append(f" (stale{held})", style="dim")
    if unmapped:
        entry_text.append(" (unmapped)", style="dim")
    # Show history count when keeping history (full view only — the count refers
    # to the raw-payload history the other views don't draw).
    if history is not None and show_hex:
        n_entries = len(history)
        if raw_hex and (not history or history[-1].payload != raw_hex):
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

    # One byte-notation view per entry, shared by the ruler and the per-signal
    # byte-reference column so the two can never name bytes in different spaces.
    byte_display = (
        ByteDisplay(
            notation,
            payload_len=len(raw_hex) // 2,
            sub_bytes=subfunction_bytes_for_pid(pid),
        ).aligned()
        if (show_rulers and raw_hex and show_hex)
        else None
    )

    if params:
        # Highlight the signal(s) whose *decoded value* just changed, with the
        # same background the changed byte gets in the hex line (skipped when
        # stale — a timed-out reuse of last-good data is not a live change). The
        # per-byte hex highlight still tracks raw byte changes; only the signal
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
                display=byte_display,
                selected_name=sel_name,
                changed_styles=changed_styles,
            )
        )
    elif decode:
        entry_text.append(f"      {decode}\n")

    if raw_hex and show_hex:
        # Byte-index ruler, once per PID, above the hex lines.
        if byte_display is not None:
            ruler_pw = 16 if history is not None else 6
            entry_text.append_text(render_byte_rulers(byte_display, params, prefix_width=ruler_pw))
        if history is not None:
            # One row per run of identical payloads, oldest first, each diffed
            # against its predecessor. Include the current payload when it is not
            # yet the newest run (first cycle edge case).
            current = (
                [HistoryRun(raw_hex, "")]
                if raw_hex and (not history or history[-1].payload != raw_hex)
                else []
            )
            # Bound the rendered rows to the newest ``row_cap`` so a long, noisy
            # run stays cheap to render (every poll is in the journal).
            shown = row_cap - len(current)
            hidden = max(0, len(history) - shown)
            all_entries = [*history[hidden:], *current]
            if hidden:
                entry_text.append(f"      … {hidden} earlier entries hidden\n", style="dim")
            prev_row = history[hidden - 1].payload if hidden else ""
            for run in all_entries:
                # Display seconds resolution only (sub-second precision is kept in
                # the stored capture `time` for cross-signal alignment, not shown).
                ts_disp = run.time.split(".")[0] if run.time else ""
                prefix = f"      {ts_disp}  " if ts_disp else "                "
                entry_text.append_text(
                    _render_hex_line(
                        run.payload,
                        params,
                        unmapped,
                        prev_raw=prev_row,
                        prefix=prefix,
                        prefix_style="dim" if run.time else "",
                        suffix=f"  ×{run.polls}" if run.polls > 1 else "",
                    )
                )
                prev_row = run.payload
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
    interval: float | str,
    prev_hex: dict[tuple[str, str], str] | None = None,
    prev_params: dict[tuple[str, str], dict[str, object]] | None = None,
    hex_history: PayloadHistory | None = None,
    show_rulers: bool = False,
    footer: bool = True,
    selected: tuple[str, str, str] | None = None,
    max_history_rows: int | None = None,
    cache: RenderCache | None = None,
    view_mode: str = "full",
    param_stats=None,
    notation: ByteNotation = ByteNotation.WICAN,
    ecu_notes: dict[str, str] | None = None,
    now: float | None = None,
) -> Text:
    """Render all ECU query results as a Rich Text object for display.

    ``interval`` is the poll cadence for the header: seconds, or a ready-made
    label such as the per-PID schedule's ``per-PID``.

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

    ``notation`` selects the byte-index notation used by the rulers and the
    per-signal byte-reference column (see :class:`~canlib.uds.notation.ByteDisplay`).

    ``ecu_notes`` adds a note after an ECU's header, keyed by its label (the slow-ECU
    backoff's). ``now`` is the wall clock a stale value's age is measured against.

    ``view_mode`` (see :data:`VIEW_MODES`) selects the presentation: ``"ecus"``
    lists only the responding ECUs (+ a PID/signal count); ``"ranges"`` shows
    each signal's accumulated value span (from ``param_stats``, a
    :class:`~canlib.modes._monitor_stats.ParamStats`); ``"signals"`` shows the
    decoded table without the raw hex; ``"full"`` (default) shows table + hex.
    """
    row_cap = _RENDER_MAX_ROWS if max_history_rows is None else max(1, max_history_rows)
    now = time.time() if now is None else now
    text = Text()

    view_note = "" if view_mode == "full" else f"  ·  view: {view_mode}"
    rate = interval if isinstance(interval, str) else f"{interval:.1f}s"
    text.append(
        f"  Monitor — cycle {cycle}  (last: {elapsed:.1f}s, interval: {rate}){view_note}\n",
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
        note = (ecu_notes or {}).get(ecu_label)
        if note:
            text.append(f"  {note}", style="yellow")
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
            stale_label = stale_age(entry, now)

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
                    notation=notation,
                    stale_label=stale_label,
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
                        notation=notation,
                        stale_label=stale_label,
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
                    notation=notation,
                    stale_label=stale_label,
                )
            text.append_text(block)

    if cache is not None:
        cache.prune(live_keys)

    if footer:
        text.append("\n  Press Ctrl+C to stop monitoring\n", style="dim")
    return text
