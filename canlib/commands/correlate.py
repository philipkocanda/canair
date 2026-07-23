#!/usr/bin/env python3
"""``canair correlate`` — cross-signal correlation across a drive/session.

Builds every decoded param + every varying raw byte across all co-polled
ECU/PIDs in scope, time-aligns them by nearest timestamp, and ranks the
strongest cross-signal relationships. The "show me every strong relationship in
this drive" entry point — how the AAF-speed and MCU-temp finds were made by hand.

Read-only analysis over ``captures/``; talks to no device.
"""

from __future__ import annotations

import argparse
import json as _json
import sys

from canlib.align import DEFAULT_JOIN_TOL_S, join_nearest, load_signal_captures
from canlib.capture_dates import add_scope_args, resolve_date_bounds
from canlib.xanalysis import (
    build_byte_series,
    build_param_series,
    correlate_matrix,
    load_ref,
    pearson,
)

NAME = "correlate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Cross-signal correlation matrix across co-polled ECUs in a drive",
        description=(
            "Time-align every decoded param + varying raw byte across the "
            "co-polled ECU/PIDs in scope and rank the strongest cross-signal "
            "correlations. Use --against to hunt one reference (ECU:PID:PARAM)."
        ),
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Optional ECU[:PID] selector(s) to restrict the signals "
        "(e.g. 'MCU VCU' or 'ESC:22C101'); default = all co-polled in scope",
    )
    parser.add_argument(
        "--against",
        metavar="ECU:PID:PARAM",
        help="Correlate every signal against this one reference "
        "(e.g. ESC:22C101:REAL_SPEED_KMH) instead of the full matrix",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Print a labelled r-matrix instead of a ranked pair list",
    )
    parser.add_argument(
        "--include-intra",
        action="store_true",
        help="Include same-ECU+PID pairs (default: cross-PID/ECU only)",
    )
    parser.add_argument(
        "--min-r", type=float, default=0.6, metavar="R", help="Min |r| to report (default 0.6)"
    )
    parser.add_argument(
        "--min-n", type=int, default=15, metavar="N", help="Min aligned points (default 15)"
    )
    parser.add_argument("--top", type=int, default=40, metavar="N", help="Max hits (default 40)")
    parser.add_argument(
        "--join-tol",
        type=float,
        default=DEFAULT_JOIN_TOL_S,
        metavar="SECONDS",
        help=f"Nearest-timestamp join window (default {DEFAULT_JOIN_TOL_S}s)",
    )
    parser.add_argument("--bytes", action="store_true", help="Include raw varying bytes (Bn)")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def _discover_specs(query, since, until, state, label):
    """Which (ECU, PID) pairs have timed payload captures in scope."""
    from canlib.capture_dates import (
        entry_datetime,
        filter_by_date_range,
        filter_by_text,
    )
    from canlib.commands.captures import load_all_captures

    entries = load_all_captures()
    entries = filter_by_date_range(entries, since, until)
    entries = filter_by_text(entries, state=state, label=label)
    q = None
    if query:
        from canlib.query import parse_query

        q = parse_query(query)
    specs: set[tuple[str, str]] = set()
    for e in entries:
        ecu = str(e.get("ecu", "")).upper()
        pid = str(e.get("pid", "")).upper()
        if not e.get("payload") or entry_datetime(e) is None:
            continue
        if q is not None and not q.matches(ecu, pid):
            continue
        specs.add((ecu, pid))
    return sorted(specs)


def _gather_series(specs, since, until, state, label, want_bytes):
    """Build all signal series (params + optionally varying bytes) for specs."""
    from canlib.pids import build_ecu_index, load_pids

    loaded = load_signal_captures(
        specs, since=since, until=until, state=state, label=label
    )
    ecu_index = build_ecu_index(load_pids())
    series: dict = {}
    for (ecu, pid), lp in loaded.items():
        if not lp.captures:
            continue
        params = ecu_index.get(ecu, {}).get("pids", {}).get(pid, {}).get("parameters", {})
        series.update(build_param_series(lp, params))
        if want_bytes:
            series.update(build_byte_series(lp))
    return series


def _color_r(r: float) -> str:
    c = _GREEN if abs(r) >= 0.7 else _YELLOW if abs(r) >= 0.3 else _DIM
    return f"{c}r={r:+.3f}{_RESET}"


def run(args) -> int:
    since, until, err = resolve_date_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    series = _gather_series(
        _discover_specs(args.query, since, until, args.state, args.label),
        since,
        until,
        args.state,
        args.label,
        args.bytes,
    )
    if not series:
        print("No time-aligned signals found in scope.", file=sys.stderr)
        return 1

    # --against: rank every signal vs one reference.
    if args.against:
        try:
            ref_series, ref_label = load_ref(
                args.against, since=since, until=until, state=args.state, label=args.label
            )
        except ValueError as e:
            print(f"--against error: {e}", file=sys.stderr)
            return 1
        rows = []
        for name, s in series.items():
            xs, ys, n = join_nearest(ref_series, s, tol_s=args.join_tol)
            if n < args.min_n:
                continue
            r = pearson(xs, ys)
            if r is None or abs(r) < args.min_r:
                continue
            rows.append((name, r, n))
        rows.sort(key=lambda t: -abs(t[1]))
        rows = rows[: args.top]
        if args.json:
            _json.dump(
                {
                    "reference": ref_label,
                    "join_tol_s": args.join_tol,
                    "hits": [{"signal": n, "r": r, "n": nn} for n, r, nn in rows],
                },
                sys.stdout,
                indent=2,
            )
            print()
            return 0
        print(f"\n  {_BOLD}vs {ref_label}{_RESET} {_DIM}(nearest-join ≤{args.join_tol:g}s){_RESET}")
        for name, r, n in rows:
            print(f"    {_color_r(r)}  {name}  {_DIM}n={n}{_RESET}")
        print()
        return 0

    hits = correlate_matrix(
        series,
        tol_s=args.join_tol,
        min_r=args.min_r,
        min_n=args.min_n,
        include_intra=args.include_intra,
    )
    hits = hits[: args.top]
    if args.json:
        _json.dump(
            {
                "join_tol_s": args.join_tol,
                "hits": [{"a": h.a, "b": h.b, "r": h.r, "n": h.n} for h in hits],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not hits:
        print(f"No cross-signal correlations with |r| ≥ {args.min_r} (n ≥ {args.min_n}).")
        return 0
    print(
        f"\n  {_BOLD}Cross-signal correlations{_RESET} "
        f"{_DIM}({len(series)} signals, |r|≥{args.min_r}, n≥{args.min_n}, "
        f"≤{args.join_tol:g}s){_RESET}"
    )
    for h in hits:
        print(f"    {_color_r(h.r)}  {h.a}  {_DIM}⟷{_RESET}  {h.b}  {_DIM}n={h.n}{_RESET}")
    print()
    return 0
