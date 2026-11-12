#!/usr/bin/env python3
"""Rich ``Text`` renderers for the ``captures --step`` viewer.

Pure formatting: every function here takes plain capture data and returns a
:class:`rich.text.Text`, so the *same* renderer serves the interactive Textual
app (:mod:`step_tui`), the piped static output, and tests. Nothing
here prints, and nothing here knows about the view vocabulary — the caller
(:mod:`step_model`) decides *what* to show and passes explicit
switches (``show_hex``/``changed_only``).

Building ``Text`` directly rather than printing markup also means capture-owned
free text (labels, notes) can never be mis-parsed as Rich markup.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rich.text import Text

from canair.captures.entries import capture_key
from canair.captures.store import NO_DEFS, PidDefs
from canair.captures.types import CaptureEntry
from canair.expression.decoding import decode_param_rows
from canair.expression.notation import ByteDisplay, ByteNotation
from canair.render.formatting import changed_param_highlights, render_byte_rulers, render_hex_line
from canair.render.glyphs import CURSOR, WARN
from canair.render.signal_table import render_param_table
from canair.states.tokens import join_states as _join_states
from canair.tui import styles
from canair.uds.layout import subfunction_bytes_for_pid

# Width of the rule drawn between stacked capture blocks.
_SEPARATOR_WIDTH = 44


def _payload_hex(entry: Mapping[str, Any] | None) -> str:
    """Normalized (upper, space-free) payload hex for a capture, or ``""``."""
    if not entry:
        return ""
    return str(entry.get("payload") or "").upper().replace(" ", "")


def key_label(key: tuple[str, str]) -> str:
    """Display form of an (ECU, PID) key: ``HVAC:2201A0``."""
    return f"{key[0]}:{key[1]}"


def capture_block_text(
    captures: Sequence[CaptureEntry],
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
    notation: ByteNotation = ByteNotation.WICAN,
) -> Text:
    """Render one capture: header, decoded signals, optional ruler, byte-diff hex.

    The byte-diff compares the payload against the previous capture of the *same*
    (ECU, PID) — via ``prev_idx`` — shown dimmed above, and parameters whose
    *decoded value* moved since then are highlighted with the same
    coverage-coloured background as the changed byte (as the live monitor does).

    ``position`` is the free-text locator shown next to the PID (e.g.
    ``capture 3/50``); ``show_per_pid`` appends the per-PID ordinal for a
    multi-PID selection. ``selected`` marks the block with a ``▶`` cursor (the
    stacked view's block focus) and ``dt_label`` shows its offset from the
    frame's anchor time. ``show_hex`` drops the hex/ruler lines (the params-only
    view); ``changed_only`` narrows the signal table to rows whose decoded
    value changed, leaving byte colouring computed from the full set.
    ``notation`` is the byte-index notation the ruler and each signal's
    byte-reference column are drawn in.
    """

    e = captures[i]
    key = capture_key(e)
    parameters, tx_id, map_bytes = PidDefs(*defs.get(key, NO_DEFS))

    pj = prev_idx[i]
    prev = captures[pj] if pj is not None else None

    norm = _payload_hex(e)
    prev_norm = _payload_hex(prev)
    n_bytes = len(norm) // 2
    # One byte-notation view for both the ruler and the per-signal byte column.
    display = ByteDisplay(
        notation, payload_len=n_bytes, sub_bytes=subfunction_bytes_for_pid(str(e["pid"]))
    ).aligned()

    rows = decode_param_rows(e["payload"] or "", parameters)
    unmapped = not rows

    prev_rows = decode_param_rows(prev["payload"] or "", parameters) if prev else []
    prev_values: dict[str, object] | None = {r[0]: r[1] for r in prev_rows} if prev else None
    changed_styles = changed_param_highlights(rows, norm, prev_norm, prev_values)

    out = Text()

    # Header: ECU / PID + position, timestamp, state, label, file.
    cursor = f"{CURSOR} " if selected else "  "
    tx_str = f" (0x{tx_id:03X})" if isinstance(tx_id, int) else ""
    out.append("\n")
    out.append(cursor, style=styles.HEADING_NAME)
    out.append(f"{e['ecu']}{tx_str}", style=styles.HEADING_NAME)
    alias = (aliases or {}).get(e["ecu"])
    if alias:
        out.append(f"  (alias {alias})", style=styles.MUTED)
    if dt_label:
        out.append(f"   {dt_label}", style=styles.MUTED)
    out.append("\n")

    ord_n, ord_m = ordinals[i]
    per_pid = f" · this PID {ord_n}/{ord_m}" if show_per_pid else ""
    pos = f"{position}{per_pid}".strip()
    out.append("    ")
    out.append(str(e["pid"]), style=styles.NAME)
    if pos:
        out.append(f"  {pos}", style=styles.MUTED)
    out.append("\n")

    ts = e.get("time") or e.get("date") or ""
    out.append(f"    {ts}", style=styles.HEADING)
    meta = Text()
    st = _join_states(e.get("vehicle_states"))
    if st:
        meta.append(f"  states={st}")
    if e.get("label"):
        meta.append(f"  [{e['label']}]")
    if e.get("file"):
        meta.append(f"  ({e['file']})")
    meta.stylize(styles.MUTED)
    out.append(meta)
    out.append("\n")

    note = (e.get("notes") or "").strip()
    if note:
        out.append("    note:", style=styles.MUTED)
        out.append(f" {note}\n")

    # Decoded-signal block (aligned columns, verification marks, byte references).
    table_rows = [r for r in rows if r[0] in changed_styles] if changed_only else rows
    if table_rows:
        out.append(render_param_table(table_rows, display=display, changed_styles=changed_styles))
    elif changed_only and rows:
        out.append("      (no signal changes)\n", style=styles.MUTED)

    if not show_hex:
        return out

    # Byte-index ruler (opt-in), aligned with the hex byte columns below.
    prev_ts = (prev.get("time") or prev.get("date") or "") if prev else ""
    max_ts = max(len(ts), len(prev_ts))
    if rulers and n_bytes:
        out.append(render_byte_rulers(display, rows, prefix_width=8 + max_ts, map_bytes=map_bytes))

    # Previous same-PID capture (dimmed, no highlight) for visual reference, then
    # the current capture with per-byte change highlighting against it.
    if prev is not None:
        out.append(
            render_hex_line(
                prev_norm,
                rows,
                unmapped,
                prefix=f"      {prev_ts:<{max_ts}}  ",
                prefix_style=styles.MUTED,
                map_bytes=map_bytes,
            )
        )
    out.append(
        render_hex_line(
            norm,
            rows,
            unmapped,
            prev_raw=prev_norm,
            prefix=f"    > {ts:<{max_ts}}  ",
            map_bytes=map_bytes,
        )
    )
    return out


def missing_block_text(key: tuple[str, str], tol_s: float, *, selected: bool = False) -> Text:
    """Placeholder for a key with no capture within the join tolerance."""
    out = Text()
    out.append("\n")
    out.append(f"{CURSOR} " if selected else "  ", style=styles.HEADING_NAME)
    out.append(f"— no {key_label(key)} capture within {tol_s:g}s —\n", style=styles.MUTED)
    return out


def separator_text() -> Text:
    """The rule drawn between two stacked capture blocks."""
    return Text(f"  {'─' * _SEPARATOR_WIDTH}\n", style=styles.MUTED)


def frame_header_text(
    *,
    position: str,
    timestamp: str = "",
    tol_s: float | None = None,
    states: str = "",
    label: str = "",
    gaps: str = "",
    actuated: str = "",
) -> Text:
    """The stacked view's frame header: locator, anchor time, tolerance, session tags.

    ``gaps`` names the recorded gaps since the previous frame, and ``actuated`` what the
    recording switched since then, each on a line of its own.
    """
    out = Text()
    out.append("\n  ")
    out.append(position, style=styles.HEADING)
    detail = Text()
    if timestamp:
        detail.append(f"   {timestamp}")
    if tol_s is not None:
        detail.append(f"   tol={tol_s:g}s")
    if states:
        detail.append(f"   states={states}")
    if label:
        detail.append(f"   [{label}]")
    detail.stylize(styles.MUTED)
    out.append(detail)
    out.append("\n")
    if gaps:
        out.append(f"  {WARN} since the previous frame: {gaps}\n", style=styles.WARNING)
    if actuated:
        out.append("  switched since the previous frame: ", style=styles.HEADING)
        out.append(f"{actuated}\n")
    return out
