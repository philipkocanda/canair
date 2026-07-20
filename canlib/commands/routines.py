"""``canair routines`` — RoutineControl (0x31): TUI or single command."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, ecu_completer, finalize_live_parser, run_live

NAME = "routines"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="RoutineControl (0x31): interactive TUI or single --rid command",
        description="RoutineControl (0x31): interactive TUI, or single command with --rid.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair routines BCM                     Interactive TUI
  canair routines BCM --json              List all BCM routines (offline JSON)
  canair routines BCM --rid 12A1          Request results (SF 0x03, safe)
  canair routines BCM --rid 12A1 --sf stop
""",
    )
    parser.add_argument(
        "routines", metavar="ECU", nargs="?", help="ECU name (e.g. BCM)"
    ).completer = ecu_completer
    parser.add_argument("--rid", metavar="RID", help="Routine ID to execute (e.g. 12A1)")
    parser.add_argument(
        "--sf",
        metavar="SF",
        default="results",
        choices=["results", "start", "stop"],
        help="Sub-function: results (0x03, safe default), start (0x01, actuates), stop (0x02)",
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if not args.routines:
        from canlib.commands._hints import ecu_hint

        print("Specify an ECU, e.g. `canair routines BCM`.\n")
        print(ecu_hint())
        return 2
    return run_live(args)
