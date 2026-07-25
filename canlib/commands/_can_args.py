"""Shared argparse scaffolding for raw broadcast-CAN log inputs (domain B).

The ``can`` kind of ``correlate`` and ``hunt`` both read a raw frame log from a
positional ``FILE`` and a ``--can-format`` selector with the same choices/help.
Centralize that pair so the two commands can't drift (the ``--id`` argument
differs per command — optional comma-list vs required single ID — so it stays
with each command).
"""

from __future__ import annotations

import argparse

CAN_LOG_EXTS = ".asc/.blf/candump .log/.trc/GVRET .csv"
CAN_LOG_FORMATS = ["auto", "asc", "blf", "csv", "log", "gvret"]


def add_can_log_source_args(parser: argparse.ArgumentParser) -> None:
    """Add the ``FILE`` positional + ``--can-format`` selector for a raw-CAN log."""
    parser.add_argument(
        "file",
        metavar="FILE",
        help=f"Path to a raw broadcast-CAN frame log ({CAN_LOG_EXTS})",
    )
    parser.add_argument(
        "--can-format",
        choices=CAN_LOG_FORMATS,
        default="auto",
        help="Log format (default: auto-detect by extension)",
    )
