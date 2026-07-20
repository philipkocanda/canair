"""``canair repl`` — interactive live terminal (the old no-mode default)."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, finalize_live_parser

NAME = "repl"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=["interactive"],
        help="Interactive live terminal (REPL) over the WiCAN connection",
        description="Drop into the interactive live terminal (REPL).",
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    return parser
