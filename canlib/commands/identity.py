"""``canair identity`` — query standard UDS identity DIDs from an ECU."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, finalize_live_parser

NAME = "identity"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Query ECU identity (part no., serial, VIN, ...) via UDS or KWP2000",
        description="Query ECU identity data and decode it. Supports UDS "
        "(22 F1xx) and KWP2000 (1A 8x/9x) ECUs; the protocol is auto-selected "
        "from the profile registry or an on-device probe (override with --protocol).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair identity IGPM --session --wake\n"
        "  canair identity 770 --session\n"
        "  canair identity BMS --protocol kwp   # KWP2000 powertrain ECU\n",
    )
    parser.add_argument(
        "tx",
        metavar="ECU",
        help="ECU name or TX ID (e.g. IGPM or 770)",
    )
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    parser.add_argument(
        "--protocol",
        choices=("auto", "uds", "kwp"),
        default="auto",
        help="Identity protocol: uds (22 F1xx), kwp (1A 8x/9x KWP2000), or auto "
        "(registry hint, else on-device probe). Default: auto",
    )
    add_connection_args(parser)
    finalize_live_parser(parser, identity=True)
    return parser
