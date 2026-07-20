"""``canair routines-scan`` — RoutineControl (0x31) discovery scan (safe SF 0x03)."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, ecu_completer, finalize_live_parser

NAME = "routines-scan"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="RoutineControl (0x31) discovery scan across a RID range (safe SF 0x03)",
        description="Probe requestRoutineResults (SF 0x03) across a RID range on one or more ECUs. "
        "Hits are written to pids/<ecu>.yaml under a routines: section.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n  canair routines-scan\n  canair routines-scan IGPM BCM --rid-range F000-F0FF\n",
    )
    parser.add_argument(
        "routines_scan", nargs="*", metavar="ECU", help="ECUs to scan (default: IGPM BCM HVAC)"
    ).completer = ecu_completer
    parser.add_argument(
        "--rid-range", metavar="START-END", default="F000-F0FF", help="RID range (default F000-F0FF)"
    )
    parser.add_argument(
        "--throttle-ms", type=int, default=150, help="Delay in ms between probes (default 150)"
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    return parser
