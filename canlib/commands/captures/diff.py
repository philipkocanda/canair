"""The ``--diff`` view: monitor-style decoded params + coloured byte-diff.

One block per (ECU, PID): header, decoded-parameter table, an optional byte-index
ruler, then the payload hex lines with per-byte change highlighting. The
interactive counterpart is the stepper (:mod:`step`), which renders the same
information one time-joined frame at a time.
"""

from collections.abc import Sequence

from canlib.capture_types import CaptureEntry

from .query import (
    _decoded_preview,
    _dedupe_payloads,
    _dump_json,
    _gather_query,
    _group_by_key,
)


def _render_diff_group(
    console,
    payloads: list[dict],
    parameters: dict,
    tx_id: int | None,
    show_all: bool,
    rulers: bool = False,
) -> None:
    """Render one ECU+PID block: header, decoded params, optional ruler, byte-diff hex."""
    from rich.markup import escape

    from canlib.decoding import decode_param_rows
    from canlib.formatting import _render_hex_line, render_byte_rulers, render_param_table

    # Decode the most recent payload into param rows (drives the table + colours).
    rows = decode_param_rows(payloads[-1]["payload"], parameters)
    unmapped = not rows
    n_bytes = len(payloads[-1]["payload"].replace(" ", "")) // 2

    unique = _dedupe_payloads(payloads)
    total = len(payloads)
    n_unique = len(unique)
    if total == n_unique or show_all:
        count_str = f"({total} entries)"
    else:
        count_str = f"({total} entries, {n_unique} unique)"

    # ECU + PID headers.
    ecu_display = escape(payloads[0]["ecu"])
    pid_display = escape(str(payloads[0]["pid"]))
    tx_str = f" (0x{tx_id:03X})" if isinstance(tx_id, int) else ""
    console.print(f"\n  [bold cyan]{ecu_display}{tx_str}[/bold cyan]")
    console.print(f"    [yellow]{pid_display}[/yellow]  [dim]{count_str}[/dim]")

    # Decoded-parameter block (aligned columns, verification marks, byte indices).
    if rows:
        console.print(render_param_table(rows, n_bytes=n_bytes), end="")

    # Payload hex lines with per-byte change highlighting, under a byte-index ruler.
    render_list = payloads if show_all else unique
    max_ts = max((len(e.get("time") or e.get("date") or "") for e in render_list), default=0)

    # Byte-index ruler (opt-in via --rulers), aligned with the hex byte columns
    # below. Two rows: "idx" = payload byte position, "wican" = WiCAN Bnn (skips PCI).
    if rulers and n_bytes:
        console.print(
            render_byte_rulers(n_bytes, rows, prefix_width=8 + max_ts), end="", soft_wrap=True
        )

    prev_norm = ""
    for e in render_list:
        norm = e["payload"].upper().replace(" ", "")
        ts = e.get("time") or e.get("date") or ""
        prefix = f"      {ts:<{max_ts}}  "
        line = _render_hex_line(
            norm, rows, unmapped, prev_raw=prev_norm, prefix=prefix, prefix_style="dim"
        )
        # soft_wrap keeps long hex lines on one row (let the terminal wrap, not rich)
        console.print(line, end="", soft_wrap=True)
        prev_norm = norm


def cmd_diff(
    entries: Sequence[CaptureEntry],
    query,
    show_all: bool = False,
    rulers: bool = False,
    as_json: bool = False,
) -> None:
    """Show payloads matching ``query`` in monitor style, per ECU+PID.

    ``query`` is a canlib.query selection (``"VCU"``, ``"VCU:2101,2102"``,
    ``"VCU:2101 BMS:2101"`` — see canlib.query). One block is rendered per
    distinct (ECU, PID): an ``ECU (0xTXID)`` / ``PID (N entries)`` header, a
    decoded-parameter block (from the most recent payload), then the payload hex
    lines with per-byte change highlighting.

    By default only *unique* payloads per PID are shown; ``show_all=True`` renders
    every capture.
    """
    captures, defs = _gather_query(entries, query, warn=not as_json)
    if not captures:
        if as_json:
            _dump_json([])
        return

    groups = _group_by_key(captures)

    if as_json:
        out = []
        for key, group in sorted(groups.items()):
            parameters, tx_id = defs.get(key, ({}, None))
            unique = _dedupe_payloads(group)
            render_list = group if show_all else unique
            out.append(
                {
                    "ecu": group[0]["ecu"],
                    "pid": str(group[0]["pid"]),
                    "tx_id": f"0x{tx_id:03X}" if isinstance(tx_id, int) else None,
                    "total": len(group),
                    "unique": len(unique),
                    "payloads": [e["payload"].upper().replace(" ", "") for e in render_list],
                    "decoded": _decoded_preview(group[-1]),
                }
            )
        _dump_json(out)
        return

    from rich.console import Console

    console = Console(highlight=False)

    for key, group in sorted(groups.items()):
        parameters, tx_id = defs.get(key, ({}, None))
        _render_diff_group(console, group, parameters, tx_id, show_all, rulers)

    console.print()
