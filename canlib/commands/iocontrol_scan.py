"""``canair iocontrol-scan`` — IOControl (0x2F) DID discovery scan (safe SF 0x00)."""

from __future__ import annotations

import argparse

from canlib.commands._live import add_connection_args, ecu_completer, finalize_live_parser

NAME = "iocontrol-scan"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="IOControl discovery scan across an id range (safe: UDS 0x2F SF00 / KWP2000 0x30 IOCP00)",
        description="Probe returnControlToECU across an id range on one or more ECUs. "
        "The service is auto-selected per ECU from its id_protocol: UDS ECUs use "
        "InputOutputControlByIdentifier (0x2F, 16-bit DID); KWP2000 ECUs (BMS, VCU, "
        "MCU, LDC, AAF) use InputOutputControlByLocalIdentifier (0x30, 8-bit LID). "
        "Only the side-effect-free sub-function is ever sent — the scanner never "
        "actuates. Hits are written to pids/<ecu>.yaml under an iocontrol_discoveries: "
        "section.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair iocontrol-scan IGPM              # UDS 0x2F DID scan\n"
        "  canair iocontrol-scan BMS               # KWP2000 0x30 LID scan (auto)\n"
        "  canair iocontrol-scan BMS --did-range 00-FF\n"
        "  canair iocontrol-scan IGPM BCM --did-range B000-BFFF\n",
    )
    parser.add_argument(
        "iocontrol_scan", nargs="+", metavar="ECU", help="ECUs to scan (at least one required)"
    ).completer = ecu_completer
    parser.add_argument(
        "--did-range",
        metavar="START-END",
        default=None,
        help="Id range: DID for UDS (per-ECU defaults), LID 00-FF for KWP2000 (default 00-FF)",
    )
    parser.add_argument(
        "--throttle-ms", type=int, default=150, help="Delay in ms between probes (default 150)"
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    return parser
