"""``hunt can``: which byte of a raw frame ID tracks a reference frame byte."""

from __future__ import annotations

import json as _json
import sys

from canair.analysis import frame_series as frame_series
from canair.analysis.cross_signal import (
    transform_ref,
)
from canair.captures.can_logs import CanLogError
from canair.render import ansi, messages


def run_can_log(args) -> int:
    """Hunt on a raw broadcast-CAN frame log: which byte of --id tracks --against?

    ``--against`` is a frame byte ``0xID:rN`` in the same log (resolved from the
    log's frame series). Reuses the frame-domain sweep (frame_series.hunt_frame)
    and the shared ranking; hits are raw-CAN ``rN`` labels (no WiCAN expression,
    so --promote is rejected — frame signals are defined in signals/, Stage 4).
    """
    from pathlib import Path

    path = Path(args.file)
    if not path.is_file():
        messages.error(f"no such file: {path}")
        return 1
    try:
        target = int(args.id, 16)
        id_filter = (
            frame_series.parse_id_filter(args.against.split(":")[0])
            if ":" in args.against
            else None
        )
        # Reference: a frame byte 0xID:rN in the same log.
        ref_series_map = frame_series.build_frame_series(
            path, args.can_format, min_distinct=2, id_filter=id_filter
        )
    except (ValueError, CanLogError) as e:
        messages.error(str(e))
        return 1

    ref = ref_series_map.get(args.against)
    if ref is None:
        avail = ", ".join(sorted(ref_series_map)[:6])
        messages.error(
            f"--against {args.against!r}: not a varying byte in {path.name}. "
            f"Expected a frame label like 0x386:r0. Available e.g.: {avail} …"
        )
        return 1
    ref_label = args.against
    if args.transform and args.transform != "raw":
        ref = transform_ref(ref, args.transform)
        ref_label = f"{args.transform}({ref_label})"

    try:
        hits = frame_series.hunt_frame(
            path,
            args.can_format,
            target,
            ref,
            tol_s=args.join_tol,
            min_n=args.min_n,
            top=args.top,
            method=args.method,
            all_interps=args.all_interps,
        )
    except CanLogError as e:
        messages.error(str(e))
        return 1

    if args.json:
        _json.dump(
            {
                "can_log": path.name,
                "target": f"0x{target:X}",
                "reference": ref_label,
                "hits": [
                    {
                        "expr": h.expr,
                        "interp": h.interp,
                        "offset": h.offset,
                        "r": h.r,
                        "n": h.n,
                        "slope": h.slope,
                        "intercept": h.intercept,
                        "resid": h.resid,
                        "unit_guess": h.unit_guess,
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
        print(f"No byte of 0x{target:X} correlates with {ref_label} in {path.name}.")
        return 0
    print(
        f"\n  {ansi.BOLD}Hunt 0x{target:X} vs {ref_label}{ansi.RESET} "
        f"{ansi.DIM}({path.name}, nearest-join ≤{args.join_tol:g}s){ansi.RESET}"
    )
    for h in hits:
        color = ansi.GREEN if abs(h.r) >= 0.7 else ansi.YELLOW if abs(h.r) >= 0.3 else ansi.DIM
        unit = f"  {ansi.DIM}{h.unit_guess}{ansi.RESET}" if h.unit_guess else ""
        print(
            f"    {color}r={h.r:+.3f}{ansi.RESET}  {ansi.BOLD}{h.expr}{ansi.RESET} "
            f"{ansi.DIM}({h.interp}){ansi.RESET}  fit y={h.slope:.4f}·x{h.intercept:+.2f} "
            f"{ansi.DIM}resid={h.resid:.2f} n={h.n}{ansi.RESET}{unit}"
        )
    print()
    return 0
