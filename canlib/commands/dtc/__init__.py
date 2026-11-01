"""``canair dtc`` — read and clear Diagnostic Trouble Codes, and review their log.

Two kinds live under one command:

  * ``canair dtc read [ECU]`` — read (UDS ``0x19`` / KWP2000 ``0x18``) or clear
    (``0x14``) an ECU's fault memory, or sweep every ECU with ``--all``. Each read is
    recorded to the DTC log. A bare ``canair dtc BMS`` (or ``canair dtc --all``) is
    shorthand for it — see ``_inject_default_subcommand`` in ``canlib/cli.py``.
  * ``canair dtc log [ECU]`` — the last logged scan, offline (:mod:`.log`).
"""

from __future__ import annotations

import argparse
import sys

from canlib.commands._group import group_help
from canlib.commands._live import (
    add_connection_args,
    ecu_completer,
    finalize_live_parser,
    run_live,
)

NAME = "dtc"

# Kept in sync with the ``dtc`` entry in ``cli._GROUP_DEFAULTS``.
DTC_KINDS = ("read", "log")


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Read or clear DTCs (ReadDTCInformation 0x19 / ClearDiagnosticInformation 0x14), "
        "or review the DTC log: read | log",
        description="Read, clear or review Diagnostic Trouble Codes. Choose a kind:\n"
        "  read  read stored DTCs (UDS 0x19 / KWP2000 0x18) or clear them (0x14)\n"
        "  log   show the last logged scan, offline (no device)\n\n"
        "A bare `canair dtc BMS` (or `canair dtc --all`, or `canair dtc` alone) is "
        "shorthand for `canair dtc read …`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair dtc BMS                       Read all stored DTCs (bare = read)
  canair dtc --all                     Scan every ECU (logs + shows changes since last scan)
  canair dtc log                       Show the last logged full-sweep scan (offline)
  canair dtc log BMS                   Show the last logged scan for one ECU (offline)

Run `canair dtc <kind> --help` for the flags of each kind.""",
    )
    kinds = parser.add_subparsers(dest="dtc_kind", metavar="<kind>")
    _add_read_parser(kinds)
    from canlib.commands.dtc.log import add_parser as _add_log_parser

    _add_log_parser(kinds)
    parser.set_defaults(func=group_help("_dtc_group_parser"), _dtc_group_parser=parser)
    return parser


def _add_read_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "read",
        help="Read or clear DTCs on one ECU, or scan every ECU (--all)",
        description="Read stored Diagnostic Trouble Codes with UDS 0x19 "
        "(reportDTCByStatusMask) or KWP2000 0x18, or clear them with 0x14. Clearing "
        "mutates ECU fault memory and prompts for confirmation unless --yes is given. "
        "Every read is recorded to the DTC log (`canair dtc log`), which lives in user "
        "state, not in the profile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair dtc BMS                       Read all stored DTCs (status mask FF)
  canair dtc --all                     Scan every ECU (logs + shows changes since last scan)
  canair dtc --all --no-log            Scan every ECU without recording to the DTC log
  canair dtc BMS --label fixed         Read + log a single-ECU scan with a label
  canair dtc --all --state ready       Log a full scan tagged with the vehicle state
  canair dtc BMS --mask 08             Read confirmed DTCs only (mask 0x08)
  canair dtc IGPM --session --wake     Wake + read in extended session
  canair dtc BMS --json                Machine-readable output
  canair dtc BMS --clear               Clear all DTCs (asks to confirm)
  canair dtc BMS --clear --yes         Clear without the confirmation prompt
  canair dtc BMS --protocol kwp        Force KWP2000 readDTCByStatus (0x18)
  canair dtc BMS --supported           List every DTC a KWP2000 ECU supports (not a fault list)
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
    # Superseded by `canair dtc log`; kept working so old scripts don't break.
    parser.add_argument(
        "--history", dest="dtc_history", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--no-log",
        dest="dtc_log",
        action="store_false",
        default=True,
        help="Don't record this scan to the DTC log (scans are logged by default, "
        "reporting what cleared/appeared since the last scan)",
    )
    parser.add_argument(
        "--no-retry",
        dest="dtc_retry",
        action="store_false",
        default=True,
        help="With --all, don't retry unresponsive ECUs (by default a no-response "
        "ECU is retried once with a wake + longer timeout so it isn't skipped)",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Optional label for the logged scan entry (e.g. 'before clearing')",
    )
    parser.add_argument(
        "--state",
        "--vehicle-states",
        dest="state",
        metavar="STATES",
        default=None,
        help="Vehicle power state(s) during the scan, recorded on the log entry "
        "(comma-separated, e.g. 'READY' or 'SLEEP, PLUGGED'; case-insensitive). "
        "Vocabulary from vehicle_states.yaml (`canair states`).",
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
        "--supported",
        dest="dtc_supported",
        action="store_true",
        help="KWP2000 only: list every DTC the ECU supports (18 03 FF 00) instead of its "
        "stored ones. This is a catalogue, not a fault list, so it is shown as raw hex and "
        "never logged. Not combinable with --all or --clear",
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
    if getattr(args, "dtc_history", False):
        from canlib.commands.dtc.log import run as run_log

        print("note: `--history` is now `canair dtc log`.", file=sys.stderr)
        return run_log(args)
    if getattr(args, "dtc_supported", False) and (
        getattr(args, "dtc_all", False) or getattr(args, "clear", False)
    ):
        print("--supported reads one KWP2000 ECU; it can't be combined with --all or --clear.")
        return 2
    if not args.dtc and not getattr(args, "dtc_all", False):
        from canlib.commands._hints import ecu_hint
        from canlib.commands.dtc.log import log_hint

        print("Specify an ECU (e.g. `canair dtc BMS`) or `canair dtc --all`.\n")
        print(ecu_hint())
        hint = log_hint()
        if hint:
            print(f"\n{hint}")
        return 2
    return run_live(args)
