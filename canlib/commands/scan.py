"""``canair scan`` — scan a range of PIDs/DIDs on one ECU."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, finalize_live_parser

NAME = "scan"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Scan a range of PIDs/DIDs on an ECU (requires --tx)",
        description="Scan a range of PIDs/DIDs on an ECU. One scan at a time only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair scan --tx 7E4 --service 21 --range 01-FF
  canair scan --tx 7E4 --service 22 --range BC01-BC0B
  canair scan --tx 7E4 --service 2F --range E000-E0FF --append 03 --session
""",
    )
    parser.add_argument("--tx", metavar="ID", required=True, help="ECU TX ID (hex, e.g. 7E4)")
    parser.add_argument("--service", metavar="SVC", default="21", help="UDS service (hex, default 21)")
    parser.add_argument("--range", metavar="START-END", default="01-FF", help="PID range (hex)")
    parser.add_argument("--append", metavar="HEX", help="Hex bytes to append after each DID")
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument("--label", metavar="TEXT", default=None, help="Session label for --save")
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    add_connection_args(parser)
    finalize_live_parser(parser, scan=True)
    return parser
