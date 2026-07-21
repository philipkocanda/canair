"""``canair iocontrol-scan`` — IOControl (0x2F) DID discovery scan (safe SF 0x00)."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, ecu_completer, finalize_live_parser

NAME = "iocontrol-scan"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="IOControl (0x2F) DID discovery scan across a DID range (safe SF 0x00)",
        description="Probe returnControlToECU (SF 0x00) across a DID range on one or more ECUs. "
        "Hits are written to pids/<ecu>.yaml under an iocontrol_discoveries: section.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n  canair iocontrol-scan IGPM\n  canair iocontrol-scan IGPM BCM --did-range B000-BFFF\n",
    )
    parser.add_argument(
        "iocontrol_scan", nargs="+", metavar="ECU", help="ECUs to scan (at least one required)"
    ).completer = ecu_completer
    parser.add_argument(
        "--did-range", metavar="START-END", default=None, help="DID range (per-ECU defaults if omitted)"
    )
    parser.add_argument(
        "--throttle-ms", type=int, default=150, help="Delay in ms between probes (default 150)"
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    return parser
