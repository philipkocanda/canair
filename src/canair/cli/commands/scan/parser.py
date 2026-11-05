"""The ``canair scan`` parser: the group and its five kinds."""

from __future__ import annotations

import argparse

from canair.cli.args.group import group_help

from .memory_kind import add_memory_parser
from .probes import add_iocontrol_parser, add_routines_parser, add_sessions_parser
from .range_parser import add_range_parser

NAME = "scan"

# Subcommand names under ``canair scan``. Kept in sync with the ``scan`` entry in
# ``cli._GROUP_DEFAULTS`` (which injects "range" when the token after ``scan``
# isn't one of these).
SCAN_KINDS = ("range", "iocontrol", "routines", "sessions", "memory")


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Scan an ECU: range | iocontrol | routines | sessions | memory",
        description="Scan an ECU. Choose a kind:\n"
        "  range      sweep a PID/DID range (general purpose)\n"
        "  iocontrol  IOControl discovery (UDS 0x2F; KWP2000 0x30 asks first)\n"
        "  routines   SAFE RoutineControl discovery (UDS 0x31 SF03 / KWP2000 0x33, auto)\n"
        "  sessions   SAFE diagnostic session-type discovery (0x10, auto UDS/KWP2000)\n"
        "  memory     map which address ranges answer ReadMemoryByAddress (0x23; asks first)\n\n"
        "A bare `canair scan BMS` (or `canair scan` alone) is shorthand for "
        "`canair scan range …`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair scan BMS                         # sweep a PID range on BMS (bare = range)
  canair scan range                       # interactive wizard (pick ECU/service/range)
  canair scan range IGPM --range BC01-BC0B  # scan a specific DID range
  canair scan iocontrol IGPM              # SAFE actuator (IOControl) discovery
  canair scan routines BCM                # SAFE routine discovery
  canair scan sessions BMS                # SAFE diagnostic session-type discovery
  canair scan memory BCM --range FEDE0000-FEDFFFFF  # map readable memory (asks first)

Run `canair scan <kind> --help` for the flags of each kind.""",
    )
    kinds = parser.add_subparsers(dest="scan_kind", metavar="<kind>")
    add_range_parser(kinds)
    add_iocontrol_parser(kinds)
    add_routines_parser(kinds)
    add_sessions_parser(kinds)
    add_memory_parser(kinds)
    parser.set_defaults(func=group_help("_scan_group_parser"), _scan_group_parser=parser)
    return parser
