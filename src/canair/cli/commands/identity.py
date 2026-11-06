"""``canair identity`` — query standard UDS identity DIDs from an ECU."""

from __future__ import annotations

import argparse

from canair.cli.args.session import add_wake_arg
from canair.cli.live import LiveOperation, add_connection_args, live_command
from canair.cli.live.handlers import reads

NAME = "identity"
ALIASES = ["id"]
_OPERATION = LiveOperation(reads.handle_identity)


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Query ECU identity (part no., serial, VIN, ...) via UDS or KWP2000",
        description="Query ECU identity data and decode it. Supports UDS "
        "(22 F1xx) and KWP2000 (1A 8x/9x) ECUs; the protocol is auto-selected "
        "from the profile registry or an on-device probe (override with --protocol). When the "
        "profile records no protocol, both services are probed, and a reading that proves one "
        "offers to record it in identity.id_protocol, which routines and scans read to tell UDS "
        "from KWP2000. Nothing else is written.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair identity IGPM --session --wake\n"
        "  canair identity 770 --session\n"
        "  canair identity BMS --protocol kwp   # KWP2000 powertrain ECU\n"
        "  canair identity IGPM --set-protocol  # record a proven protocol without asking\n",
    )
    parser.add_argument(
        "tx",
        metavar="ECU",
        help="ECU name or TX ID (e.g. IGPM or 770)",
    )
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    add_wake_arg(parser)
    parser.add_argument(
        "--protocol",
        choices=("auto", "uds", "kwp"),
        default="auto",
        help="Identity protocol: uds (22 F1xx), kwp (1A 8x/9x KWP2000), or auto "
        "(registry hint, else on-device probe). Default: auto",
    )
    parser.add_argument(
        "--set-protocol",
        action="store_true",
        help="When the profile records no protocol for the ECU and the probe proves one, write "
        "it to identity.id_protocol without asking (asked on a terminal otherwise)",
    )
    add_connection_args(parser)
    parser.set_defaults(func=live_command(_OPERATION))
    return parser
