"""``canair reset`` — restart one ECU with ECUReset (0x11)."""

from __future__ import annotations

import argparse
import sys

from canair.cli.args.session import add_wake_arg
from canair.cli.commands._hints import ecu_hint
from canair.cli.live import LiveOperation, add_connection_args, ecu_completer, run_live
from canair.cli.live.handlers import actuators
from canair.config.file import ALLOW_RESET_KEY, reset_allowed
from canair.render import messages
from canair.safety.reset import RESET_TYPES

NAME = "reset"
_OPERATION = LiveOperation(actuators.handle_reset)


def add_parser(subparsers) -> argparse.ArgumentParser:
    types = ", ".join(f"{word} (11 {value:02X})" for word, value in RESET_TYPES.items())
    parser = subparsers.add_parser(
        NAME,
        help="Restart one ECU with ECUReset (0x11): hard | key-off-on | soft",
        description="Restart one ECU with ECUReset (0x11), then wait until it answers\n"
        "TesterPresent again. A restarting ECU stops working, so reset it only with the car\n"
        "parked and not in use.\n\n"
        f"The command works only after you set {ALLOW_RESET_KEY} to true at a terminal\n"
        f"(canair config set {ALLOW_RESET_KEY} true). It then asks you to type the ECU's name\n"
        "before it sends the reset: --yes skips that question, not the config key.\n\n"
        f"Reset types: {types}.\n"
        "A KWP2000 ECU, or one whose protocol the profile does not record, takes only hard\n"
        "(powerOn on KWP2000). A reset does not clear DTCs, and other ECUs may store\n"
        "lost-communication DTCs while this one restarts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair reset BMS soft                   Restart the BMS application (asks first)
  canair reset IGPM hard --session        Enter the extended session first, then hard reset
  canair reset 7E4 hard --yes --json      Skip the question, machine-readable result
  canair reset BCM hard --restart-timeout 0   Send the reset, don't wait for the ECU
""",
    )
    parser.add_argument(
        "reset", metavar="ECU", nargs="?", help="ECU name or TX ID (e.g. BMS or 7E4)"
    ).completer = ecu_completer
    parser.add_argument(
        "reset_type",
        metavar="TYPE",
        nargs="?",
        choices=tuple(RESET_TYPES),
        help="Reset type: hard (hardReset 01), key-off-on (keyOffOnReset 02) or soft "
        "(softReset 03)",
    )
    parser.add_argument(
        "--restart-timeout",
        metavar="SECONDS",
        type=float,
        default=10.0,
        help="How long to wait for the ECU to answer TesterPresent after the reset "
        "(default: 10; 0 sends the reset and does not wait)",
    )
    parser.add_argument(
        "--session", action="store_true", help="Enter extended session (10 03) first"
    )
    add_wake_arg(parser)
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help=f"Skip the typed confirmation (required when stdin is not a terminal). "
        f"{ALLOW_RESET_KEY} must still be true",
    )
    add_connection_args(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if not args.reset or not args.reset_type:
        messages.error("specify an ECU and a reset type, e.g. `canair reset BMS soft`")
        print(ecu_hint(), file=sys.stderr)
        return 2
    if args.restart_timeout < 0:
        messages.error("--restart-timeout must be 0 or more seconds")
        return 2
    if not reset_allowed():
        messages.error(f"canair reset is off: {ALLOW_RESET_KEY} is not true in your config")
        messages.hint(
            f"to turn it on, run `canair config set {ALLOW_RESET_KEY} true` at a terminal "
            "(it asks you to type YES)"
        )
        return 2
    return run_live(args, _OPERATION)
