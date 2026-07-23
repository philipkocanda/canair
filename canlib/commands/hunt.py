#!/usr/bin/env python3
"""``canair hunt`` — which byte on ECU:PID *is* this known signal?

Sweeps every byte offset × interpretation (u8/i16/f32/… × endianness) on a
target PID, time-aligns each against a reference signal from another ECU/PID,
and ranks by |Pearson r| — reporting the best linear fit and a physical-unit
guess for each top hit. Automates the "which byte tracks vehicle speed?"
question that previously needed a scratch script.

Read-only analysis over ``captures/``; talks to no device.
"""

from __future__ import annotations

import argparse
import json as _json
import sys

from canlib.align import DEFAULT_JOIN_TOL_S, load_signal_captures
from canlib.capture_dates import add_scope_args, resolve_date_bounds
from canlib.xanalysis import hunt_byte, load_ref

NAME = "hunt"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Find which byte/interpretation on ECU:PID matches a known signal",
        description=(
            "Sweep every byte offset × interpretation on ECU PID, time-align "
            "each against --against (a known signal on another ECU/PID), and "
            "rank by |r| with a linear fit + unit guess."
        ),
    )
    parser.add_argument("ecu", help="Target ECU to hunt on (e.g. AAF)")
    parser.add_argument("pid", help="Target PID to hunt on (e.g. 2181)")
    parser.add_argument(
        "--against",
        required=True,
        metavar="ECU:PID:PARAM",
        help="Reference signal, e.g. ESC:22C101:REAL_SPEED_KMH (param or EXPR)",
    )
    parser.add_argument(
        "--min-n", type=int, default=10, metavar="N", help="Min aligned points (default 10)"
    )
    parser.add_argument("--top", type=int, default=12, metavar="N", help="Max hits (default 12)")
    parser.add_argument(
        "--join-tol",
        type=float,
        default=DEFAULT_JOIN_TOL_S,
        metavar="SECONDS",
        help=f"Nearest-timestamp join window (default {DEFAULT_JOIN_TOL_S}s)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument(
        "--promote",
        metavar="NAME",
        help="Write the top hit's expression to ecus/ as an enabled, unverified "
        "candidate param NAME (via pids upsert-param), with the correlation "
        "evidence auto-filled into notes",
    )
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    from canlib.ecus import canonical_ecu_name_safe

    since, until, err = resolve_date_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    ecu = canonical_ecu_name_safe(args.ecu)
    pid = args.pid.upper()

    try:
        ref_series, ref_label = load_ref(
            args.against, since=since, until=until, state=args.state, label=args.label
        )
    except ValueError as e:
        print(f"--against error: {e}", file=sys.stderr)
        return 1

    loaded = load_signal_captures(
        [(ecu, pid)], since=since, until=until, state=args.state, label=args.label
    )
    lp = loaded[(ecu.upper(), pid)]
    if not lp.captures:
        print(
            f"No timed captures for {ecu} {pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)." if lp.n_no_time else "."),
            file=sys.stderr,
        )
        return 1

    hits = hunt_byte(
        lp, ref_series, tol_s=args.join_tol, min_n=args.min_n, top=args.top
    )

    if args.promote:
        return _promote(args.promote, ecu, pid, hits, ref_label)

    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "reference": ref_label,
                "join_tol_s": args.join_tol,
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
        print(f"No byte on {ecu} {pid} correlates with {ref_label} in scope.")
        return 0
    print(
        f"\n  {_BOLD}Hunt {ecu} {pid} vs {ref_label}{_RESET} "
        f"{_DIM}(nearest-join ≤{args.join_tol:g}s){_RESET}"
    )
    for h in hits:
        color = _GREEN if abs(h.r) >= 0.7 else _YELLOW if abs(h.r) >= 0.3 else _DIM
        unit = f"  {_CYAN}{h.unit_guess}{_RESET}" if h.unit_guess else ""
        print(
            f"    {color}r={h.r:+.3f}{_RESET}  {_BOLD}{h.expr}{_RESET} "
            f"{_DIM}({h.interp}){_RESET}  fit y={h.slope:.4f}·x{h.intercept:+.2f} "
            f"{_DIM}resid={h.resid:.2f} n={h.n}{_RESET}{unit}"
        )
    print()
    return 0


def _promote(name: str, ecu: str, pid: str, hits, ref_label: str) -> int:
    """Write the top hit as an enabled, unverified candidate param.

    Routes through the same snapshot → edit → schema-validate → auto-revert gate
    as ``canair pids upsert-param`` (via ``pids._guarded``), so a promoted
    expression that fails schema validation (e.g. a PCI-crossing multibyte read)
    is rejected and rolled back rather than committed.
    """
    from canlib.commands.pids import _guarded
    from canlib.pids_edit import PidsEditError, upsert_parameter

    if not hits:
        print("Nothing to promote — no correlating byte found.", file=sys.stderr)
        return 1
    top = hits[0]
    if top.expr == "<no-expr>":
        print(
            f"Top hit ({top.interp} @ B{top.offset}) has no WiCAN expression "
            "(float/LE-signed) — cannot promote. Try a byte-expressible interpretation.",
            file=sys.stderr,
        )
        return 1
    unit_note = f" {top.unit_guess}" if top.unit_guess else ""
    notes = (
        f"Candidate from `canair hunt` vs {ref_label}: r={top.r:+.3f} (n={top.n}), "
        f"fit y={top.slope:.4f}·x{top.intercept:+.2f}, resid={top.resid:.2f}.{unit_note} "
        "Enabled unverified — confirm scale/sign against reality."
    )

    def do():
        upsert_parameter(
            ecu,
            pid,
            name,
            top.expr,
            source=f"canair hunt vs {ref_label}",
            notes=notes,
            verified=False,
            enabled=True,
        )

    try:
        fpath = _guarded(ecu, None, do, validate=True)
    except (PidsEditError, SystemExit) as e:
        print(f"promote failed: {e}", file=sys.stderr)
        return 1
    print(
        f"{_GREEN}✓ promoted{_RESET} {ecu} {pid} {name} = {_BOLD}{top.expr}{_RESET} "
        f"{_DIM}(r={top.r:+.3f}, {fpath.name}){_RESET}"
    )
    print(f"  {_DIM}Review + verify, then: canair pids upsert-param {ecu} {pid} {name} "
          f'"{top.expr}" --verified{_RESET}')
    return 0
