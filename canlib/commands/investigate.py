#!/usr/bin/env python3
"""``canair investigate ECU PID`` — one-shot "tell me everything about this PID".

Bundles the manual reverse-engineering battery — coverage (mapped?),
state-discriminability, the best co-polled cross-signal anchor, and a physical
unit guess — into one ranked, per-byte report. The "point it at an unknown PID"
entry point that collapses a coverage → discriminate → correlate → hunt loop.

Read-only analysis over ``captures/``; talks to no device.
"""

from __future__ import annotations

import argparse
import json as _json
import sys
from dataclasses import dataclass

from canlib.align import DEFAULT_JOIN_TOL_S, join_nearest, load_signal_captures
from canlib.byteindex import extract_byte_indices
from canlib.capture_dates import add_scope_args, resolve_date_bounds
from canlib.xanalysis import (
    build_byte_series,
    build_param_series,
    correlation,
    linear_fit,
    sniff_unit,
)

NAME = "investigate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="One-shot per-byte report for a PID (mapped? / state F / best anchor / unit)",
        description=(
            "For every varying data byte of ECU PID: whether a param already maps "
            "it, how cleanly it separates by power state, its strongest co-polled "
            "cross-signal anchor (r + linear fit + unit guess). The 'explain this "
            "unknown PID' entry point."
        ),
    )
    parser.add_argument("ecu", help="Target ECU (e.g. MCU)")
    parser.add_argument("pid", help="Target PID (e.g. 2102)")
    parser.add_argument(
        "--min-r", type=float, default=0.6, metavar="R",
        help="Only report an anchor when |r| ≥ this (default 0.6)",
    )
    parser.add_argument(
        "--min-n", type=int, default=15, metavar="N", help="Min aligned points (default 15)"
    )
    parser.add_argument(
        "--join-tol", type=float, default=DEFAULT_JOIN_TOL_S, metavar="SECONDS",
        help=f"Nearest-timestamp join window (default {DEFAULT_JOIN_TOL_S}s)",
    )
    parser.add_argument(
        "--all", action="store_true", help="Include already-mapped bytes too (default: skip them)"
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


@dataclass
class _ByteReport:
    offset: int
    mapped_by: str | None
    state_f: float | None
    anchor: str | None
    anchor_r: float | None
    anchor_n: int
    slope: float | None
    intercept: float | None
    unit_guess: str | None


def _state_f(frames_by_state: dict[str, list[float]]):
    from canlib.commands.decode import _discriminability

    return _discriminability(frames_by_state)


def run(args) -> int:
    from canlib.commands.correlate import _discover_specs
    from canlib.commands.decode import _byte_state_buckets
    from canlib.ecus import canonical_ecu_name_safe
    from canlib.pids import build_ecu_index, load_pids

    since, until, err = resolve_date_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    ecu = canonical_ecu_name_safe(args.ecu)
    pid = args.pid.upper()
    scope = {"since": since, "until": until, "state": args.state, "label": args.label}

    loaded = load_signal_captures([(ecu, pid)], **scope)
    lp = loaded[(ecu.upper(), pid)]
    if not lp.captures:
        print(
            f"No timed captures for {ecu} {pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)." if lp.n_no_time else "."),
            file=sys.stderr,
        )
        return 1

    # Target byte series (min_distinct=2 so near-binary relay bytes count).
    target = build_byte_series(lp, min_distinct=2)

    # Which offsets are already mapped by a defined param.
    ecu_index = build_ecu_index(load_pids())
    params_def = ecu_index.get(ecu.upper(), {}).get("pids", {}).get(pid, {}).get("parameters", {})
    mapped: dict[int, str] = {}
    for pname, pdef in params_def.items():
        expr = pdef.get("expression") or ""
        for off in extract_byte_indices(expr):
            mapped.setdefault(off, pname)

    # State buckets per byte (F score) — reuse decode's bucketer over a lite
    # all_results (only needs r["capture"]).
    all_results = [{"capture": c} for c in lp.captures]
    state_buckets = _byte_state_buckets(all_results, "state")

    # Anchor signals: every param on the OTHER co-polled ECU/PIDs in scope.
    anchors: dict[str, list] = {}
    other_specs = [s for s in _discover_specs(None, since, until, args.state, args.label)
                   if s != (ecu.upper(), pid)]
    if other_specs:
        aloaded = load_signal_captures(other_specs, **scope)
        for (aecu, apid), alp in aloaded.items():
            if not alp.captures:
                continue
            aparams = ecu_index.get(aecu, {}).get("pids", {}).get(apid, {}).get("parameters", {})
            anchors.update(build_param_series(alp, aparams))

    reports: list[_ByteReport] = []
    for key, series in target.items():
        off = int(key.rsplit(":B", 1)[1])
        best = _best_anchor(series, anchors, args.join_tol, args.min_n)
        sb = state_buckets.get(f"B{off}")
        reports.append(
            _ByteReport(
                offset=off,
                mapped_by=mapped.get(off),
                state_f=_state_f(sb) if sb else None,
                anchor=best[0] if best else None,
                anchor_r=best[1] if best else None,
                anchor_n=best[2] if best else 0,
                slope=best[3] if best else None,
                intercept=best[4] if best else None,
                unit_guess=best[5] if best else None,
            )
        )

    if not args.all:
        reports = [r for r in reports if r.mapped_by is None]
    # Rank: strongest anchor first, then state separation.
    reports.sort(key=lambda r: (-(abs(r.anchor_r or 0)), -(r.state_f or 0)))

    if args.json:
        _json.dump(
            {"target": f"{ecu}:{pid}", "join_tol_s": args.join_tol, "bytes": [vars(r) for r in reports]},
            sys.stdout, indent=2, default=str,
        )
        print()
        return 0

    _print_report(ecu, pid, reports, args, lp)
    return 0


def _best_anchor(series, anchors, tol, min_n):
    """The strongest-correlating anchor for one byte series → (label,r,n,m,c,unit)."""
    best = None
    for label, asig in anchors.items():
        xs, ys, n = join_nearest(asig, series, tol_s=tol)
        if n < min_n:
            continue
        r = correlation(xs, ys)
        if r is None:
            continue
        if best is None or abs(r) > abs(best[1]):
            fit = linear_fit(xs, ys)
            m, c = (fit[0], fit[1]) if fit else (None, None)
            best = (label, r, n, m, c, sniff_unit(xs, ys))
    return best


def _print_report(ecu, pid, reports, args, lp) -> None:
    print(
        f"\n  {_BOLD}Investigate {ecu} {pid}{_RESET} "
        f"{_DIM}({len(lp.captures)} timed captures, ≤{args.join_tol:g}s join){_RESET}"
    )
    if not reports:
        print(f"    {_DIM}no {'varying ' if not args.all else ''}bytes to report{_RESET}\n")
        return
    for r in reports:
        tag = f"{_DIM}[{r.mapped_by}]{_RESET}" if r.mapped_by else f"{_YELLOW}unmapped{_RESET}"
        f_str = ""
        if r.state_f is not None:
            fc = _GREEN if r.state_f >= 10 else _YELLOW if r.state_f >= 2 else _DIM
            f_val = "∞" if r.state_f == float("inf") else f"{r.state_f:.1f}"
            f_str = f"  {fc}stateF={f_val}{_RESET}"
        anchor = ""
        if r.anchor and r.anchor_r is not None and abs(r.anchor_r) >= args.min_r:
            rc = _GREEN if abs(r.anchor_r) >= 0.7 else _YELLOW
            fit = f" fit y={r.slope:.4f}·x{r.intercept:+.2f}" if r.slope is not None else ""
            unit = f" {_CYAN}{r.unit_guess}{_RESET}" if r.unit_guess else ""
            anchor = f"  {rc}r={r.anchor_r:+.3f}{_RESET} vs {r.anchor} {_DIM}n={r.anchor_n}{fit}{_RESET}{unit}"
        print(f"    {_BOLD}B{r.offset}{_RESET} {tag}{f_str}{anchor}")
    print()
