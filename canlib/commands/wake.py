"""``canair wake`` — wake sleeping ECUs via the Smart Key Module relay."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, finalize_live_parser

NAME = "wake"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Wake sleeping ECUs via SKM relay control (requires active CAN bus)",
        description="Wake sleeping ECUs via Smart Key Module relay control.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n  canair wake\n  canair wake --level ign1\n",
    )
    parser.add_argument(
        "--level",
        default="acc",
        choices=["acc", "ign1", "ign2", "start"],
        help="Relay level (default: acc)",
    )
    add_connection_args(parser)
    finalize_live_parser(parser, skm_wakeup=True)
    return parser
