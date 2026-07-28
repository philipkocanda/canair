"""``canair monitor`` — live, continuously-refreshing view of ECU parameters.

Promoted from the former ``canair query --monitor`` flag into its own top-level
command. Positional STEPs use the same multi mini-language as ``canair query``;
a bare selector (no leading verb) is treated as a ``query`` step, so
``canair monitor BMS:2101`` and
``canair monitor "session IGPM --wake" "query IGPM"`` both work.

On a terminal this opens the scrollable Textual monitor (the polling / decoding /
capture-saving logic lives in :mod:`canlib.modes.monitor`); piped/non-interactive
it polls silently until Ctrl+C. Recording (``--save`` + metadata) and keep-modes
apply here, not to the one-shot ``canair query``.
"""

from __future__ import annotations

import argparse
import sys

from canlib.commands._live import (
    add_connection_args,
    finalize_live_parser,
    param_completer,
    run_live,
    to_step,
)

NAME = "monitor"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Live, continuously-refreshing view of ECU parameters (scrollable TUI)",
        description="Monitor ECUs/parameters live in a scrollable, refreshing view. "
        "Positional STEPs use the multi mini-language (same as `canair query`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair monitor BMS:2101                   Monitor BMS PID 2101 (default 5s interval)
  canair monitor BMS:2101 --interval 2      Refresh every 2s
  canair monitor "VCU:2101 BMS:2101"        Cross-ECU monitor
  canair monitor "skm-wake acc" "query IGPM:BC03,BC06"
  canair monitor BMS:2101 --save --label "…" --state "ready, parked"  Record while monitoring

In the TUI: mouse wheel / scrollbar / arrows-jk / PgUp-PgDn / g-G scroll,
f toggles follow-tail, space pauses, =/- change the poll interval live,
s edits the save label/state/notes, n starts a fresh recording segment, q quits.

For a single one-shot read (no live refresh) use `canair query` instead.
""",
    )
    parser.add_argument(
        "steps",
        nargs="*",
        metavar="STEP",
        help="Query selector(s) or multi mini-language step(s)",
    ).completer = param_completer
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="Poll interval in seconds (default 5.0; change it live in the TUI with =/-)",
    )
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    keep = parser.add_mutually_exclusive_group()
    keep.add_argument(
        "--keep-unique",
        action="store_true",
        help="Retain only unique payloads (rising-edge) — the default",
    )
    keep.add_argument(
        "--keep-all",
        action="store_true",
        help="Retain every polled payload (full time-series; larger capture files)",
    )
    keep.add_argument("--keep", type=int, metavar="N", help="Keep the last N payloads per PID")
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument("--label", metavar="TEXT", default=None, help="Session label for --save")
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    parser.add_argument(
        "--rulers", action="store_true", help="Show byte-index rulers above the hex"
    )
    parser.add_argument(
        "--include-static",
        action="store_true",
        help="Include static config/identity PIDs (e.g. 21F2) in a bare-ECU sweep. "
        "By default `canair monitor ECU` omits PIDs flagged static:true; naming one "
        "explicitly (ECU:21F2) always polls it.",
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if not args.steps:
        print(
            "Error: monitor needs at least one query step, e.g. `canair monitor BMS:2101`",
            file=sys.stderr,
        )
        return 2

    args.multi = [to_step(s) for s in args.steps]
    # dispatch_mode routes to the monitor when both args.multi and args.monitor
    # are set; the interval flag is the poll period.
    args.monitor = args.interval

    from canlib.modes.multi import parse_sub_commands

    # Validate the mini-language up front so ambiguous/malformed steps fail
    # loudly *before* we acquire the device lock and open a connection.
    try:
        commands = parse_sub_commands(args.multi)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    query_steps = [c for c in commands if c["type"] == "query"]
    if not query_steps:
        print("Error: monitor requires at least one 'query' step", file=sys.stderr)
        return 2

    # Reject typo'd/unknown ECU names before connecting (mirrors `canair query`).
    from canlib.commands._live import load_pids
    from canlib.modes.monitor import query_ecu_error

    ecu_err = query_ecu_error(query_steps, load_pids())
    if ecu_err:
        print(f"Error: {ecu_err}", file=sys.stderr)
        return 2

    return run_live(args)
