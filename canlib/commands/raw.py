"""``canair raw`` — send a raw UDS request (hex in, hex out)."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, finalize_live_parser

NAME = "raw"


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
    parser.add_argument("raw", metavar="TX:PID", help="Raw UDS request (e.g. 7E4:2101)")
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--hold", action="store_true", help="Keep session alive until Ctrl+C")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    parser.add_argument("--save", action="store_true", help="Save result to captures/")
    parser.add_argument("--label", metavar="TEXT", default=None, help="Session label for --save")
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    add_connection_args(parser)
    finalize_live_parser(parser)
    return parser
