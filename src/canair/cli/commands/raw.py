"""``canair raw`` — send a raw UDS request (hex in, hex out)."""

from __future__ import annotations

import argparse

from canair.cli.live import LiveOperation, add_connection_args, live_command
from canair.cli.live.handlers import reads
from canair.query.steps import parse_raw_spec

NAME = "raw"
_OPERATION = LiveOperation(reads.handle_raw, records=True)


def _raw_spec(value: str) -> str:
    try:
        parse_raw_spec(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return value


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Send a raw UDS request (e.g. 7E4:2101)",
        description="Send a raw UDS request (hex in, hex out).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair raw 7E4:2101
  canair raw 770:22BC03 --session
  canair raw 770:2FBC0103 --hold --wake     IOControl: hold low beams (Ctrl+C to release)
""",
    )
    parser.add_argument(
        "raw", metavar="TX:PID", type=_raw_spec, help="Raw UDS request (e.g. 7E4:2101)"
    )
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--hold", action="store_true", help="Keep session alive until Ctrl+C")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    parser.add_argument("--save", action="store_true", help="Save result to captures/")
    parser.add_argument(
        "--label",
        metavar="TEXT",
        default=None,
        help="Session label for --save (asked for when omitted; with no terminal the "
        "session is saved under a generated label)",
    )
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    add_connection_args(parser)
    parser.set_defaults(func=live_command(_OPERATION))
    return parser
