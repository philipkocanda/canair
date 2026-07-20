"""``canair discover`` — sweep TX addresses to find responding ECUs."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, finalize_live_parser

NAME = "discover"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Sweep a range of TX addresses to find responding ECUs",
        description="Sweep a range of TX addresses (sends 10 01 to each) to find ECUs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair discover                     Discover ECUs in 0x700-0x7EF (default)
  canair discover --range 600-6FF     Custom range
  canair discover --delay 0.5         Slower pacing
  canair discover --register          Auto-add newly-found ECUs to ecus.yaml
  canair discover --register --dry-run  Preview what would be registered
""",
    )
    parser.add_argument(
        "--range", metavar="START-END", default="01-FF", help="Address range (default 700-7EF)"
    )
    parser.add_argument(
        "--delay", type=float, default=0.2, help="Delay between probes in seconds (default 0.2)"
    )
    parser.add_argument(
        "--register", action="store_true",
        help="Register newly-discovered ECUs into the profile's ecus.yaml",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --register: show what would be added without writing",
    )
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument("--label", metavar="TEXT", default=None, help="Session label for --save")
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    add_connection_args(parser)
    finalize_live_parser(parser, discover=True)
    return parser
