"""``canair tester-present`` — send TesterPresent (3E00) at intervals."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, finalize_live_parser

NAME = "tester-present"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Send TesterPresent (3E00) at regular intervals (Ctrl+C to stop)",
        description="Send TesterPresent (3E00) at regular intervals to keep a session alive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n  canair tester-present\n  canair tester-present --target 7A5\n",
    )
    parser.add_argument(
        "--target", metavar="TX_ID", help="ECU TX ID (hex, e.g. 7A5). Default: broadcast 7DF"
    )
    parser.add_argument(
        "--interval", type=float, default=1.0, help="Interval in seconds (default: 1.0)"
    )
    add_connection_args(parser)
    finalize_live_parser(parser, tester_present=True)
    return parser
