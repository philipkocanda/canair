"""``canair io`` — IOControl (UDS 0x2F / KWP2000 0x30) actuator control: TUI or single command."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, ecu_completer, finalize_live_parser, run_live

NAME = "io"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=["iocontrol"],
        help="IOControl actuators: interactive TUI or single --did command",
        description="IOControl (UDS 0x2F, or an entry's own KWP2000 0x30 commands): interactive "
        "TUI, or single actuator command with --did. Every actuator switched on is released "
        "on exit and on Ctrl+C with its release request: returnControlToECU (2F{DID}00) on "
        "UDS, the entry's declared release: on KWP2000, where the 0x30 option byte has no "
        "standard release. A 0x30 ON without a declared release is refused (set one with "
        "`canair pids set-iocontrol ECU KEY --release HEX`). The TUI's value keys (v/+/-) "
        "build UDS 0x2F requests, so they are refused for a KWP2000 ECU.",
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
    parser.add_argument(
        "--off", action="store_true", help="Send the entry's OFF command instead of ON"
    )
    parser.add_argument(
        "--poll",
        action="store_true",
        help="Enable background status polling in the TUI: sends each DID's release "
        "(2F{DID}00, returnControlToECU, on UDS) every 3s, inside each entry's own session. "
        "This can actuate relay/solenoid-backed DIDs (audible click) — off by default. "
        "A KWP2000 entry is polled only when it declares a release.",
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
    if args.json and not args.did:
        # Listing reads only the profile, so it must not need (or lock) the device.
        from canlib import load_pids
        from canlib.modes.iocontrol import mode_iocontrol_list

        return 0 if mode_iocontrol_list(load_pids(), args.iocontrol, as_json=True) else 1
    return run_live(args)
