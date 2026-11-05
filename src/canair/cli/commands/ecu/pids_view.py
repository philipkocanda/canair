"""``canair ecu ECU pids``: every PID with its latest decoded state."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from canair.analysis.ecu_summary import pids_latest_records
from canair.core.terminal import terminal_columns
from canair.render import ansi


def _value_grid(values: Mapping[str, Any], width: int, indent: str, ncols: int = 2) -> list[str]:
    """Render ``name value`` pairs as an aligned multi-column grid.

    Names are left-aligned and values right-aligned within per-column widths so
    the pairs line up in tidy columns (rather than a flat wrapped blob). Each
    cell is ``name … value``; the number of columns is reduced automatically
    when a single row wouldn't fit ``width``.
    """
    items = [(str(k), str(v)) for k, v in values.items()]
    if not items:
        return []

    gap = "   "  # between columns
    # Shrink ncols until a row fits the target width (or we're down to 1 column).
    while ncols > 1:
        rows = [items[i : i + ncols] for i in range(0, len(items), ncols)]
        name_w = [max((len(r[c][0]) for r in rows if c < len(r)), default=0) for c in range(ncols)]
        val_w = [max((len(r[c][1]) for r in rows if c < len(r)), default=0) for c in range(ncols)]
        row_w = len(indent) + sum(name_w) + sum(val_w) + 2 * ncols + len(gap) * (ncols - 1)
        if row_w <= width:
            break
        ncols -= 1

    rows = [items[i : i + ncols] for i in range(0, len(items), ncols)]
    name_w = [
        max((len(rows[r][c][0]) for r in range(len(rows)) if c < len(rows[r])), default=0)
        for c in range(ncols)
    ]
    val_w = [
        max((len(rows[r][c][1]) for r in range(len(rows)) if c < len(rows[r])), default=0)
        for c in range(ncols)
    ]

    lines: list[str] = []
    for row in rows:
        cells = [
            f"{ansi.DIM}{n:<{name_w[c]}}{ansi.RESET} {ansi.BOLD}{v:>{val_w[c]}}{ansi.RESET}"
            for c, (n, v) in enumerate(row)
        ]
        lines.append(indent + gap.join(cells))
    return lines


def cmd_pids(info: Mapping[str, Any], tx_id: int, ecu_def: dict | None, as_json: bool) -> int:
    """Compact per-PID view: every defined PID + its latest decoded state.

    Shows the *decoded* parameter values (not raw hex) from the most recent
    capture of each PID — a quick "what does this ECU currently report?" glance.
    Points at `canair captures`/`canair decode` for full history and statistics.
    """
    name = info.get("name") or f"0x{tx_id:03X}"
    records = pids_latest_records(ecu_def, name)

    if as_json:
        json.dump(
            {"ecu": name, "tx": f"0x{tx_id:03X}", "pids": records},
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    title = f"{ansi.BOLD}{ansi.CYAN}{name}{ansi.RESET}"
    if info.get("alias"):
        title += f" {ansi.DIM}(alias: {info['alias']}){ansi.RESET}"
    print(f"\n  {title} {ansi.DIM}(0x{tx_id:03X}){ansi.RESET} — latest decoded state")

    if ecu_def is None:
        print(
            f"\n  {ansi.YELLOW}No PID definitions{ansi.RESET} {ansi.DIM}(identity-only module){ansi.RESET}\n"
        )
        return 0
    if not records:
        print(f"\n  {ansi.YELLOW}No PIDs defined for {name}.{ansi.RESET}\n")
        return 0

    n_with = sum(1 for r in records if r["values"])
    print(
        f"  {ansi.DIM}{len(records)} PIDs · {n_with} with a recent capture "
        f"(each PID's newest value + the vehicle state it was read in){ansi.RESET}\n"
    )

    from canair.states.tokens import join_states

    width = min(terminal_columns(), 120)
    for r in records:
        flags = []
        if r["status"] != "active":
            flags.append(f"{ansi.YELLOW}{r['status']}{ansi.RESET}")
        # Context (state/date) for the capture the values came from.
        ctx = ""
        if r["values"]:
            st = join_states(r["vehicle_states"])
            when = " ".join(x for x in [r.get("date") or "", r.get("time") or ""] if x).strip()
            bits = []
            if st:
                bits.append(f"{ansi.DIM}state{ansi.RESET} {ansi.CYAN}{st}{ansi.RESET}")
            if when:
                bits.append(f"{ansi.DIM}{when}{ansi.RESET}")
            ctx = (
                f"  {ansi.DIM}·{ansi.RESET} " + f" {ansi.DIM}·{ansi.RESET} ".join(bits)
                if bits
                else ""
            )
        flag_str = ("  " + " ".join(flags)) if flags else ""
        print(f"  {ansi.BOLD}{ansi.CYAN}{r['pid']}{ansi.RESET}{ctx}{flag_str}")

        if r["values"]:
            for line in _value_grid(r["values"], width, "      "):
                print(line)
        elif r["n_params"] == 0:
            print(f"      {ansi.DIM}(no signals defined){ansi.RESET}")
        else:
            print(
                f"      {ansi.YELLOW}no capture{ansi.RESET} {ansi.DIM}({r['n_params']} signals defined){ansi.RESET}"
            )

    print(
        f"\n  {ansi.DIM}Latest values only. Full history/diff: "
        f"`canair captures {name} <PID>` · stats: `canair decode {name} <PID> --stats`{ansi.RESET}\n"
    )
    return 0
