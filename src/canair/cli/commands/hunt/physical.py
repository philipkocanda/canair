"""``hunt --physical``: the reference-free physical-value band scan."""

from __future__ import annotations

import json as _json
import sys

from canair.analysis.align import (
    load_signal_captures,
    longest_payload_len,
)
from canair.analysis.cross_signal import physical_scan
from canair.analysis.physical.bands import resolve_physical_bands
from canair.cli.args.notation import resolve_notation
from canair.cli.grid_prompt import resolve_grid_region
from canair.cli.profile_context import active
from canair.expression.notation import ByteNotation, ByteRef
from canair.render import ansi, messages
from canair.uds.layout import subfunction_bytes_for_pid
from canair.variants import effective_meta


def run_physical(args, ecu: str, pid: str, since, until) -> int:
    """Reference-free physical-value band scan on one PID."""
    loaded = load_signal_captures(
        [(ecu, pid)],
        since=since,
        until=until,
        state=args.state,
        label=args.label,
        one_revision="hunt",
        profile=active(),
    )
    lp = loaded[(ecu.upper(), pid)]
    if not lp.captures:
        messages.error(
            f"no timed captures for {ecu} {pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)" if lp.n_no_time else "")
        )
        return 1

    bands = resolve_physical_bands(effective_meta(active()), grid_region=resolve_grid_region())
    hits = physical_scan(lp, min_n=args.min_n, top=args.top, bands=bands)

    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "mode": "physical",
                "hits": [
                    {
                        "expr": h.expr,
                        "interp": h.interp,
                        "offset": h.offset,
                        "scaling": h.scaling,
                        "band": h.band,
                        "frac": h.frac,
                        "median": h.median,
                        "n": h.n,
                    }
                    for h in hits
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not hits:
        print(f"No byte on {ecu} {pid} lands in a known physical band in scope.")
        return 0
    notation = resolve_notation(args.notation)
    sub_bytes = subfunction_bytes_for_pid(pid)
    print(
        f"\n  {ansi.BOLD}Physical-band scan {ecu} {pid}{ansi.RESET} "
        f"{ansi.DIM}({len(lp.captures)} timed captures){ansi.RESET}"
    )
    for h in hits:
        color = ansi.GREEN if h.frac >= 0.9 else ansi.YELLOW if h.frac >= 0.7 else ansi.DIM
        if notation is ByteNotation.WICAN:
            label = h.expr
        else:
            label = ByteRef.from_wican(
                h.offset,
                width=h.width,
                payload_len=longest_payload_len(getattr(lp, "captures", None)),
            ).render(notation, sub_bytes=sub_bytes)
        print(
            f"    {color}{h.frac * 100:3.0f}% in-band{ansi.RESET}  {ansi.BOLD}{h.scaling}·{label}{ansi.RESET} "
            f"{ansi.DIM}({h.interp}){ansi.RESET}  {ansi.DIM}{h.band}{ansi.RESET} "
            f"{ansi.DIM}median≈{h.median:.1f} n={h.n}{ansi.RESET}"
        )
    print()
    return 0
