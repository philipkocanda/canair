"""``canair dtc`` — read and clear Diagnostic Trouble Codes (UDS 0x19 / 0x14)."""

from __future__ import annotations

import argparse

from canlib.commands._live import (
    add_connection_args,
    ecu_completer,
    finalize_live_parser,
    run_live,
)

NAME = "dtc"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Read or clear DTCs (ReadDTCInformation 0x19 / ClearDiagnosticInformation 0x14)",
        description="Read stored Diagnostic Trouble Codes with UDS 0x19 "
        "(reportDTCByStatusMask), or clear them with UDS 0x14. Clearing mutates "
        "ECU fault memory and prompts for confirmation unless --yes is given.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair dtc BMS                       Read all stored DTCs (status mask FF)
  canair dtc --all                     Scan every ECU for DTCs
  canair dtc BMS --mask 08             Read confirmed DTCs only (mask 0x08)
  canair dtc IGPM --session --wake     Wake + read in extended session
  canair dtc BMS --json                Machine-readable output
  canair dtc BMS --clear               Clear all DTCs (asks to confirm)
  canair dtc BMS --clear --yes         Clear without the confirmation prompt
  canair dtc BMS --protocol kwp        Force KWP2000 readDTCByStatus (0x18)
""",
    )
    parser.add_argument(
        "dtc", metavar="ECU", nargs="?", help="ECU name or TX ID (e.g. BMS or 7E4)"
    ).completer = ecu_completer
    parser.add_argument(
        "--all",
        dest="dtc_all",
        action="store_true",
        help="Scan every ECU in the profile for DTCs (protocol auto-selected per ECU)",
    )
    parser.add_argument(
        "--mask",
        metavar="HEX",
        default="FF",
        help="statusOfDTC mask for the UDS read (hex, default FF = all; falls "
        "back to 08 if the ECU rejects FF with requestOutOfRange)",
    )
    parser.add_argument(
        "--protocol",
        choices=("auto", "uds", "kwp"),
        default="auto",
        help="DTC protocol: uds (0x19/0x14), kwp (KWP2000 0x18/0x14), or auto "
        "(from the profile's id_protocol). Default: auto",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear DTCs (ClearDiagnosticInformation 0x14) instead of reading",
    )
    parser.add_argument(
        "--group",
        metavar="HEX",
        default="FFFFFF",
        help="groupOfDTC to clear (3-byte hex, default FFFFFF = all groups)",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip the clear confirmation prompt"
    )
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECU from deep sleep (10 01)")
    add_connection_args(parser)
    finalize_live_parser(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if not args.dtc and not getattr(args, "dtc_all", False):
        from canlib.commands._hints import ecu_hint

        print("Specify an ECU (e.g. `canair dtc BMS`) or `canair dtc --all`.\n")
        print(ecu_hint())
        return 2
    return run_live(args)
