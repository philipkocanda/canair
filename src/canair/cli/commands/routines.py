"""``canair routines`` — RoutineControl (0x31): TUI or single command."""

from __future__ import annotations

import argparse
import sys

from canair.cli.live import LiveOperation, add_connection_args, ecu_completer, run_live
from canair.cli.live.handlers import actuators
from canair.profile import active
from canair.render import messages

NAME = "routines"
_OPERATION = LiveOperation(actuators.handle_routines)


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
  canair routines BCM --rid 12A1 --sf start -y   Skip the question (scripts)
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
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="With --sf start: send without asking first (required when stdin is not a terminal)",
    )
    add_connection_args(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if not args.routines:
        from canair.cli.commands._hints import ecu_hint

        messages.error("specify an ECU, e.g. `canair routines BCM`")
        print(ecu_hint(), file=sys.stderr)
        return 2
    if args.json and not args.rid:
        # Listing reads only the profile, so it must not need (or lock) the device.
        from canair.ecus.pids import load_pids
        from canair.live.actuate.routines_list import mode_routines_list

        return (
            0 if mode_routines_list(load_pids(profile=active()), args.routines, as_json=True) else 1
        )
    return run_live(args, _OPERATION)
