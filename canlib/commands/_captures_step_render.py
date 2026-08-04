#!/usr/bin/env python3
"""Rich ``Text`` renderers for the ``captures --step`` viewer.

Pure formatting: every function here takes plain capture data and returns a
:class:`rich.text.Text`, so the *same* renderer serves the interactive Textual
app (:mod:`_captures_step_tui`), the piped static output, and tests. Nothing
here prints, and nothing here knows about the view vocabulary — the caller
(:mod:`_captures_step_model`) decides *what* to show and passes explicit
switches (``show_hex``/``changed_only``).

Building ``Text`` directly rather than printing markup also means capture-owned
free text (labels, notes) can never be mis-parsed as Rich markup.
"""

from __future__ import annotations

from rich.text import Text

from canlib.commands._captures_query import PidDefs, _capture_key
from canlib.states import join_states as _join_states

# Width of the rule drawn between stacked capture blocks.
_SEPARATOR_WIDTH = 44


def _payload_hex(entry: dict | None) -> str:
    """Normalized (upper, space-free) payload hex for a capture, or ``""``."""
    if not entry:
        return ""
    return str(entry.get("payload") or "").upper().replace(" ", "")


def key_label(key: tuple[str, str]) -> str:
    """Display form of an (ECU, PID) key: ``HVAC:2201A0``."""
    return f"{key[0]}:{key[1]}"


def capture_block_text(
    captures: list[dict],
    i: int,
    defs: dict[tuple[str, str], PidDefs],
    prev_idx: list[int | None],
    ordinals: list[tuple[int, int]],
    *,
    rulers: bool = False,
    position: str = "",
    aliases: dict[str, str] | None = None,
    show_hex: bool = True,
    changed_only: bool = False,
    selected: bool = False,
    dt_label: str = "",
    show_per_pid: bool = True,
) -> Text:
    """Render one capture: header, decoded params, optional ruler, byte-diff hex.

    The byte-diff compares the payload against the previous capture of the *same*
    (ECU, PID) — via ``prev_idx`` — shown dimmed above, and parameters whose
    *decoded value* moved since then are highlighted with the same
    coverage-coloured background as the changed byte (as the live monitor does).

    ``position`` is the free-text locator shown next to the PID (e.g.
    ``capture 3/50``); ``show_per_pid`` appends the per-PID ordinal for a
    multi-PID selection. ``selected`` marks the block with a ``▶`` cursor (the
    stacked view's block focus) and ``dt_label`` shows its offset from the
    frame's anchor time. ``show_hex`` drops the hex/ruler lines (the params-only
    view); ``changed_only`` narrows the parameter table to rows whose decoded
    value changed, leaving byte colouring computed from the full set.
    """
    from canlib.decoding import decode_param_rows
    from canlib.formatting import (
        _render_hex_line,
        changed_param_highlights,
        render_byte_rulers,
        render_param_table,
    )

    e = captures[i]
    key = _capture_key(e)
    parameters, tx_id = defs.get(key, ({}, None))

    pj = prev_idx[i]
    prev = captures[pj] if pj is not None else None

    norm = _payload_hex(e)
    prev_norm = _payload_hex(prev)
    n_bytes = len(norm) // 2

    rows = decode_param_rows(e["payload"], parameters)
    unmapped = not rows

    prev_rows = decode_param_rows(prev["payload"], parameters) if prev else []
    prev_values: dict[str, object] | None = {r[0]: r[1] for r in prev_rows} if prev else None
    changed_styles = changed_param_highlights(rows, norm, prev_norm, prev_values)

    out = Text()

    # Header: ECU / PID + position, timestamp, state, label, file.
    cursor = "▶ " if selected else "  "
    tx_str = f" (0x{tx_id:03X})" if isinstance(tx_id, int) else ""
    out.append("\n")
    out.append(cursor, style="bold cyan")
    out.append(f"{e['ecu']}{tx_str}", style="bold cyan")
    alias = (aliases or {}).get(e["ecu"])
    if alias:
        out.append(f"  (alias {alias})", style="dim")
    if dt_label:
        out.append(f"   {dt_label}", style="dim")
    out.append("\n")

    ord_n, ord_m = ordinals[i]
    per_pid = f" · this PID {ord_n}/{ord_m}" if show_per_pid else ""
    pos = f"{position}{per_pid}".strip()
    out.append("    ")
    out.append(str(e["pid"]), style="yellow")
    if pos:
        out.append(f"  {pos}", style="dim")
    out.append("\n")

    ts = e.get("time") or e.get("date") or ""
    out.append(f"    {ts}", style="bold")
    meta = Text()
    st = _join_states(e.get("vehicle_states"))
    if st:
        meta.append(f"  states={st}")
    if e.get("label"):
        meta.append(f"  [{e['label']}]")
    if e.get("file"):
        meta.append(f"  ({e['file']})")
    meta.stylize("dim")
    out.append(meta)
    out.append("\n")

    note = (e.get("notes") or "").strip()
    if note:
        out.append("    note:", style="dim")
        out.append(f" {note}\n")

    # Decoded-parameter block (aligned columns, verification marks, byte indices).
    table_rows = [r for r in rows if r[0] in changed_styles] if changed_only else rows
    if table_rows:
        out.append(render_param_table(table_rows, n_bytes=n_bytes, changed_styles=changed_styles))
    elif changed_only and rows:
        out.append("      (no param changes)\n", style="dim")

    if not show_hex:
        return out

    # Byte-index ruler (opt-in), aligned with the hex byte columns below.
    prev_ts = (prev.get("time") or prev.get("date") or "") if prev else ""
    max_ts = max(len(ts), len(prev_ts))
    if rulers and n_bytes:
        out.append(render_byte_rulers(n_bytes, rows, prefix_width=8 + max_ts))

    # Previous same-PID capture (dimmed, no highlight) for visual reference, then
    # the current capture with per-byte change highlighting against it.
    if prev is not None:
        out.append(
            _render_hex_line(
                prev_norm, rows, unmapped, prefix=f"      {prev_ts:<{max_ts}}  ", prefix_style="dim"
            )
        )
    out.append(
        _render_hex_line(norm, rows, unmapped, prev_raw=prev_norm, prefix=f"    > {ts:<{max_ts}}  ")
    )
    return out


def missing_block_text(key: tuple[str, str], tol_s: float, *, selected: bool = False) -> Text:
    """Placeholder for a key with no capture within the join tolerance."""
    out = Text()
    out.append("\n")
    out.append("▶ " if selected else "  ", style="bold cyan")
    out.append(f"— no {key_label(key)} capture within {tol_s:g}s —\n", style="dim")
    return out


def separator_text() -> Text:
    """The rule drawn between two stacked capture blocks."""
    return Text(f"  {'─' * _SEPARATOR_WIDTH}\n", style="dim")


def frame_header_text(
    *,
    position: str,
    timestamp: str = "",
    tol_s: float | None = None,
    states: str = "",
    label: str = "",
) -> Text:
    """The stacked view's frame header: locator, anchor time, tolerance, session tags."""
    out = Text()
    out.append("\n  ")
    out.append(position, style="bold")
    detail = Text()
    if timestamp:
        detail.append(f"   {timestamp}")
    if tol_s is not None:
        detail.append(f"   tol={tol_s:g}s")
    if states:
        detail.append(f"   states={states}")
    if label:
        detail.append(f"   [{label}]")
    detail.stylize("dim")
    out.append(detail)
    out.append("\n")
    return out
