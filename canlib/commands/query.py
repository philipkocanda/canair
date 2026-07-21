"""``canair query`` — the primary live query command (multi-pipeline default).

Absorbs the old ``--multi``/``--param``/``--ecu``/``--monitor`` modes. Positional
arguments are steps in the multi mini-language; a bare selector (no leading verb)
is treated as a ``query`` step, so ``canair query BMS:2101`` and
``canair query "session IGPM --wake" "query IGPM"`` both work.
"""

from __future__ import annotations

import argparse
import sys

from canlib.commands._live import (
    add_connection_args,
    finalize_live_parser,
    param_completer,
    run_live,
)

NAME = "query"

_VERBS = ("skm-wake", "session", "query", "raw", "scan", "security", "iocontrol", "sleep", "repl")


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Query ECUs/parameters over the WiCAN terminal (multi-pipeline default)",
        description="Query ECUs/parameters live. Positional STEPs use the multi mini-language.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair query BMS:2101                     Query BMS PID 2101
  canair query "VCU:2101 BMS:2101"          Cross-ECU query
  canair query "skm-wake acc" "query IGPM:BC03,BC06"
  canair query BMS:2101 --monitor 2         Live monitor, refresh every 2s
  canair query --param SOC_BMS SOC_DISP     Query named parameters
""",
    )
    parser.add_argument(
        "steps",
        nargs="*",
        metavar="STEP",
        help="Query selector(s) or multi mini-language step(s)",
    )
    parser.add_argument(
        "--param", nargs="+", metavar="NAME", help="Query named parameters instead of selectors"
    ).completer = param_completer
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    parser.add_argument("--repl", action="store_true", help="Drop into REPL after the pipeline")
    parser.add_argument(
        "--monitor",
        nargs="?",
        const=5.0,
        default=None,
        type=float,
        metavar="INTERVAL",
        help="Repeatedly poll query steps and refresh a live view (default interval 5.0s)",
    )
    keep = parser.add_mutually_exclusive_group()
    keep.add_argument("--keep-unique", action="store_true", help="Monitor: retain unique payloads")
    keep.add_argument("--keep-all", action="store_true", help="Monitor: retain every payload")
    keep.add_argument("--keep", type=int, metavar="N", help="Monitor: keep last N payloads per PID")
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument("--label", metavar="TEXT", default=None, help="Session label for --save")
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    parser.add_argument("--rulers", action="store_true", help="Monitor: show byte-index rulers")
    add_connection_args(parser)
    finalize_live_parser(parser)
    parser.set_defaults(func=run)
    return parser


def _to_step(selector: str) -> str:
    """Prefix a bare selector with the ``query`` verb unless it already has one."""
    first = selector.strip().split(maxsplit=1)
    if first and first[0].lower() in _VERBS:
        return selector
    return f"query {selector}"


def run(args) -> int:
    if args.steps:
        args.multi = [_to_step(s) for s in args.steps]
        # Validate the mini-language up front so ambiguous/malformed steps fail
        # loudly *before* we acquire the device lock and open a connection.
        from canlib.modes.multi import parse_sub_commands

        try:
            parse_sub_commands(args.multi)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
    # else: --param / interactive fall through to async_main's dispatch
    return run_live(args)
