"""The ``--diff`` view: monitor-style decoded signals + coloured byte-diff.

One block per (ECU, PID): header, decoded-signal table, an optional byte-index
ruler, then the payload hex lines with per-byte change highlighting. The
interactive counterpart is the stepper (:mod:`step`), which renders the same
information one time-joined frame at a time.
"""

from collections.abc import Sequence

from canair.captures.entries import _dedupe_payloads, _group_by_key
from canair.captures.store import NO_DEFS, PidDefs, decoded_preview
from canair.captures.types import CaptureEntry
from canair.expression.notation import ByteDisplay, ByteNotation
from canair.render.console import console as make_console
from canair.render.console import escape
from canair.uds.layout import subfunction_bytes_for_pid

from .query import _dump_json, _gather_query


def _render_diff_group(
    console,
    payloads: list[dict],
    pdefs: PidDefs,
    show_all: bool,
    rulers: bool = False,
    notation: ByteNotation = ByteNotation.WICAN,
) -> None:
    """Render one ECU+PID block: header, decoded signals, optional ruler, byte-diff hex."""
    parameters, tx_id, map_bytes = pdefs
    from canair.expression.decoding import decode_param_rows
    from canair.render.formatting import _render_hex_line, render_byte_rulers, render_param_table

    # Decode the most recent payload into param rows (drives the table + colours).
    rows = decode_param_rows(payloads[-1]["payload"], parameters)
    unmapped = not rows
    n_bytes = len(payloads[-1]["payload"].replace(" ", "")) // 2
    # One byte-notation view for both the ruler and the per-signal byte column.
    display = ByteDisplay(
        notation,
        payload_len=n_bytes,
        sub_bytes=subfunction_bytes_for_pid(str(payloads[0]["pid"])),
    ).aligned()

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
    console.print(f"    [cyan]{pid_display}[/cyan]  [dim]{count_str}[/dim]")

    # Decoded-signal block (aligned columns, verification marks, byte references).
    if rows:
        console.print(render_param_table(rows, display=display), end="")

    # Payload hex lines with per-byte change highlighting, under a byte-index ruler.
    render_list = payloads if show_all else unique
    max_ts = max((len(e.get("time") or e.get("date") or "") for e in render_list), default=0)

    # Byte-index ruler (opt-in via --rulers), aligned with the hex byte columns
    # below, in the user's preferred byte notation (default WiCAN Bnn).
    if rulers and n_bytes:
        console.print(
            render_byte_rulers(display, rows, prefix_width=8 + max_ts, map_bytes=map_bytes),
            end="",
            soft_wrap=True,
        )

    prev_norm = ""
    for e in render_list:
        norm = e["payload"].upper().replace(" ", "")
        ts = e.get("time") or e.get("date") or ""
        prefix = f"      {ts:<{max_ts}}  "
        line = _render_hex_line(
            norm,
            rows,
            unmapped,
            prev_raw=prev_norm,
            prefix=prefix,
            prefix_style="dim",
            map_bytes=map_bytes,
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
    notation: ByteNotation = ByteNotation.WICAN,
) -> None:
    """Show payloads matching ``query`` in monitor style, per ECU+PID.

    ``query`` is a canair.query selection (``"VCU"``, ``"VCU:2101,2102"``,
    ``"VCU:2101 BMS:2101"`` — see canair.query). One block is rendered per
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
            tx_id = defs.get(key, NO_DEFS).tx_id
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
                    "decoded": decoded_preview(group[-1]),
                }
            )
        _dump_json(out)
        return

    console = make_console()

    for key, group in sorted(groups.items()):
        pdefs = defs.get(key, NO_DEFS)
        _render_diff_group(console, group, pdefs, show_all, rulers, notation)

    console.print()
