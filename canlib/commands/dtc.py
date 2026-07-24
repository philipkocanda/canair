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
  canair dtc --all                     Scan every ECU (logs + shows changes since last scan)
  canair dtc --all --no-log            Scan every ECU without recording to the history log
  canair dtc BMS --label fixed         Read + log a single-ECU scan with a label
  canair dtc --all --state ready       Log a full scan tagged with the vehicle state
  canair dtc BMS --mask 08             Read confirmed DTCs only (mask 0x08)
  canair dtc IGPM --session --wake     Wake + read in extended session
  canair dtc BMS --json                Machine-readable output
  canair dtc BMS --clear               Clear all DTCs (asks to confirm)
  canair dtc BMS --clear --yes         Clear without the confirmation prompt
  canair dtc BMS --protocol kwp        Force KWP2000 readDTCByStatus (0x18)
  canair dtc --history                 Show the last logged full-sweep scan (offline, no device)
  canair dtc BMS --history             Show the last logged scan for one ECU (offline)
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
        "--history",
        dest="dtc_history",
        action="store_true",
        help="Show the most recent logged scan from dtc_log.yaml without touching "
        "the device (useful when the WiCAN is offline). Decodes each code and "
        "reports the change since the previous scan. Scope is --all by default, "
        "or a single ECU when one is named.",
    )
    parser.add_argument(
        "--no-log",
        dest="dtc_log",
        action="store_false",
        default=True,
        help="Don't record this scan to the profile's dtc_log.yaml (scans are "
        "logged by default, reporting what cleared/appeared since the last scan)",
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
        "(comma-separated, e.g. 'ready' or 'sleep, plugged'). Vocabulary from states.yaml.",
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
    if getattr(args, "dtc_history", False):
        return _run_history(args)
    if not args.dtc and not getattr(args, "dtc_all", False):
        from canlib.commands._hints import ecu_hint

        print("Specify an ECU (e.g. `canair dtc BMS`) or `canair dtc --all`.\n")
        print(ecu_hint())
        return 2
    return run_live(args)


def _run_history(args) -> int:
    """Offline: print the most recent logged scan for the requested scope."""
    from canlib.dtc_log import latest_matching, prior_matching, render_scan

    if args.dtc:
        from canlib.ecus import ecu_display, resolve_tx

        tx_id = resolve_tx(args.dtc)
        if tx_id is None:
            from canlib.commands._hints import ecu_hint

            print(f"Unknown ECU '{args.dtc}'.\n")
            print(ecu_hint())
            return 2
        scope = ecu_display(tx_id)
    else:
        scope = "all"

    entry = latest_matching(scope)
    if entry is None:
        print(
            f"No logged DTC scan for scope '{scope}'. "
            "Run `canair dtc "
            + (args.dtc if args.dtc else "--all")
            + "` while connected to record one."
        )
        return 1

    previous = prior_matching(scope, entry.get("timestamp", ""))
    for line in render_scan(entry, previous):
        print(line)
    return 0
