"""``canair io`` — IOControl (0x2F) actuator control: TUI or single command."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, ecu_completer, finalize_live_parser, run_live

NAME = "io"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=["iocontrol"],
        help="IOControl actuators: interactive TUI or single --did command",
        description="IOControl (0x2F): interactive TUI, or single actuator command with --did.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair io IGPM                 Interactive TUI (navigate + toggle)
  canair io IGPM --poll          TUI with background status polling enabled
  canair io IGPM --json          List all IGPM IOControl DIDs (offline JSON)
  canair io IGPM --did BC01      Turn on low beam (hold until Ctrl+C)
  canair io IGPM --did BC01 --off
""",
    )
    parser.add_argument(
        "iocontrol", metavar="ECU", nargs="?", help="ECU name (e.g. IGPM)"
    ).completer = ecu_completer
    parser.add_argument("--did", metavar="DID", help="DID to execute (e.g. BC01)")
    parser.add_argument("--off", action="store_true", help="Send OFF/returnControl instead of ON")
    parser.add_argument(
        "--poll",
        action="store_true",
        help="Enable background status polling in the TUI: sends 2F{DID}00 "
        "(returnControlToECU) to every DID every 3s. This can actuate "
        "relay/solenoid-backed DIDs (audible click) — off by default.",
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if not args.iocontrol:
        from canlib.commands._hints import ecu_hint

        print("Specify an ECU, e.g. `canair io IGPM`.\n")
        print(ecu_hint())
        return 2
    return run_live(args)
