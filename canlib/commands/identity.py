"""``canair identity`` — query standard UDS identity DIDs from an ECU."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, finalize_live_parser

NAME = "identity"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Query standard UDS identity DIDs (part no., serial, VIN, ...) from an ECU",
        description="Query standard UDS identity DIDs (F100, F18x, F190, F19x) and decode them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n  canair identity IGPM --session --wake\n  canair identity 770 --session\n",
    )
    parser.add_argument(
        "tx",
        metavar="ECU",
        help="ECU name or TX ID (e.g. IGPM or 770)",
    )
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    add_connection_args(parser)
    finalize_live_parser(parser, identity=True)
    return parser
