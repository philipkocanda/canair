"""The ``canair scan`` parser: the group and its four kinds."""

from __future__ import annotations

import argparse

from canair.cli.args.group import group_help
from canair.cli.live import add_connection_args, ecu_completer, live_command
from canair.cli.live.handlers import scans
from canair.live.scan.presets import (
    presets_help,
)

from .plan import slots
from .run import run_range

NAME = "scan"


# Subcommand names under ``canair scan``. Kept in sync with the ``scan`` entry in
# ``cli._GROUP_DEFAULTS`` (which injects "range" when the token after ``scan``
# isn't one of these).
SCAN_KINDS = ("range", "iocontrol", "routines", "sessions")


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Scan an ECU: range | iocontrol | routines | sessions",
        description="Scan an ECU. Choose a kind:\n"
        "  range      sweep a PID/DID range (general purpose)\n"
        "  iocontrol  IOControl discovery (UDS 0x2F; KWP2000 0x30 asks first)\n"
        "  routines   SAFE RoutineControl discovery (UDS 0x31 SF03 / KWP2000 0x33, auto)\n"
        "  sessions   SAFE diagnostic session-type discovery (0x10, auto UDS/KWP2000)\n\n"
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

Run `canair scan <kind> --help` for the flags of each kind.""",
    )
    kinds = parser.add_subparsers(dest="scan_kind", metavar="<kind>")
    _add_range_parser(kinds)
    _add_iocontrol_parser(kinds)
    _add_routines_parser(kinds)
    _add_sessions_parser(kinds)
    parser.set_defaults(func=group_help("_scan_group_parser"), _scan_group_parser=parser)
    return parser


def _add_range_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "range",
        help="Sweep a range of PIDs/DIDs on an ECU",
        description="Scan one or more ranges of PIDs/DIDs on an ECU. One scan at a time only. "
        "Without --range, a UDS ECU's DID families are swept: every high byte the profile "
        "shows it using (its 22/2F PIDs and its IOControl DIDs), the whole family by default "
        "(--slots narrows it).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
getting started:
  canair scan range                 # interactive wizard — pick ECU/service/range
  canair scan BMS                   # smart defaults for that ECU (bare = range)
  canair scan range IGPM            # UDS ECU → read-did over its known DID families
  canair scan range PSM --slots 32  # first 32 DIDs of each family (xx00-xx1F)

"""
        + presets_help()
        + """

examples:
  canair scan range BMS --service live-data --range 01-FF
  canair scan range 7E4 --service read-did --range BC01-BC0B
  canair scan range SKM --service read-did --range B000-B01F,B100-B11F
  canair scan range IGPM --service iocontrol --range E000-E0FF --append 03 --session

tips:
  * Run ONE scan at a time — parallel scans lock up the WiCAN.
  * Start with a small --range to gauge ECU response time, then widen.
  * Add --save --label "..." to record results to captures/.
  * For SAFE actuator/routine discovery use `canair scan iocontrol`/`routines`.
""",
    )
    ecu_arg = parser.add_argument(
        "tx",
        metavar="ECU",
        nargs="?",
        default=None,
        help="ECU name or TX ID (e.g. BMS or 7E4). Omit for the interactive wizard.",
    )
    try:
        ecu_arg.completer = ecu_completer  # type: ignore[attr-defined]
    except Exception:
        pass
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Force the interactive wizard even when an ECU is given",
    )
    parser.add_argument(
        "--service",
        metavar="SVC",
        default=None,
        help="UDS service: a preset name (live-data, read-did, iocontrol, routine) "
        "or a hex byte (default: smart per-ECU)",
    )
    parser.add_argument(
        "--range",
        metavar="START-END[,...]",
        default=None,
        help="PID/DID range in hex, or a comma list of ranges (default: smart per-ECU)",
    )
    parser.add_argument(
        "--slots",
        metavar="N",
        type=slots,
        default=None,
        help="With the smart default: sweep only the first N DIDs of each DID family "
        "(1-256, default 256 = the whole family). Can't be combined with --range.",
    )
    parser.add_argument("--append", metavar="HEX", help="Hex bytes to append after each DID")
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument(
        "--label",
        metavar="TEXT",
        default=None,
        help="Session label for --save (asked for when omitted; with no terminal the "
        "session is saved under a generated label)",
    )
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    add_connection_args(parser)
    # The wizard and plan resolution run before the shared live runtime.
    parser.set_defaults(func=run_range)
    return parser


def _add_iocontrol_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "iocontrol",
        help="IOControl discovery (UDS 0x2F; KWP2000 0x30 asks first)",
        description="Probe returnControlToECU across an id range on one or more ECUs. "
        "The service is auto-selected per ECU from its id_protocol: UDS ECUs use "
        "InputOutputControlByIdentifier (0x2F, 16-bit DID); KWP2000 ECUs (BMS, VCU, "
        "MCU, LDC, AAF) use InputOutputControlByLocalIdentifier (0x30, 8-bit LID). "
        "Only returnControlToECU (00) is ever sent, never an actuating option. On UDS "
        "that is the standard release. ISO 14230-3 leaves the 0x30 option byte "
        "manufacturer-defined, so a KWP2000 sweep may actuate outputs: it asks for "
        "confirmation first (--yes to "
        "skip; it refuses non-interactively without it). On a profile with the "
        "nissan_c0_session quirk, KWP2000 ECUs get Nissan's layout instead, with no "
        "prompt: 30 {LID} 01 00 (is value 00 supported?) inside the Nissan session "
        "10 C0, because there 30 {LID} 00 begins a one-shot command. "
        "Hits are written to pids/<ecu>.yaml under an iocontrol_discoveries: "
        "section.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair scan iocontrol IGPM              # UDS 0x2F DID scan\n"
        "  canair scan iocontrol BMS               # KWP2000 0x30 LID scan (asks first)\n"
        "  canair scan iocontrol BMS --session --mode 81   # sweep inside KWP2000 session\n"
        "  canair scan iocontrol IGPM BCM --did-range B000-BFFF\n",
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
    parser.add_argument(
        "--session", action="store_true", help="Open a diagnostic session before scanning"
    )
    parser.add_argument("--wake", action="store_true", help="Wake ECU from deep sleep first")
    parser.add_argument(
        "--mode",
        dest="session_mode",
        metavar="HEX",
        default=None,
        help="Session mode for --session (default 03 = UDS extended; 81 = KWP2000 standard, "
        "e.g. BMS; on a profile with the nissan_c0_session quirk KWP2000 ECUs default to C0 "
        "and always open a session)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation a KWP2000 0x30 sweep asks for, since 30 {LID} 00 is "
        "not known to drive nothing (every profile without the nissan_c0_session quirk)",
    )
    add_connection_args(parser)
    parser.set_defaults(func=live_command(scans.IOCONTROL))
    return parser


def _add_routines_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "routines",
        help="SAFE RoutineControl discovery (UDS 0x31 SF03 / KWP2000 0x33, auto)",
        description="Probe routine results across a range on one or more ECUs. The service "
        "is auto-selected per ECU from its id_protocol: UDS ECUs use RoutineControl "
        "(0x31, requestRoutineResults SF 0x03); KWP2000 ECUs (BMS, VCU, MCU, LDC, AAF) use "
        "RequestRoutineResultsByLocalIdentifier (0x33). 0x31 (StartRoutine on KWP2000) is "
        "NEVER sent to a KWP2000 ECU — only the read-only results service. Hits are written "
        "to pids/<ecu>.yaml under a routines: section.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair scan routines IGPM               # UDS 0x31 SF03\n"
        "  canair scan routines BMS                # KWP2000 0x33 LID scan (auto)\n"
        "  canair scan routines BMS --session --mode 81    # sweep inside KWP2000 session\n"
        "  canair scan routines IGPM BCM --rid-range F000-F0FF\n",
    )
    parser.add_argument(
        "routines_scan", nargs="+", metavar="ECU", help="ECUs to scan (at least one required)"
    ).completer = ecu_completer
    parser.add_argument(
        "--rid-range",
        metavar="START-END",
        default="F000-F0FF",
        help="RID range for UDS (default F000-F0FF); KWP2000 ECUs use LID 00-FF",
    )
    parser.add_argument(
        "--throttle-ms", type=int, default=150, help="Delay in ms between probes (default 150)"
    )
    parser.add_argument(
        "--session", action="store_true", help="Open a diagnostic session before scanning"
    )
    parser.add_argument("--wake", action="store_true", help="Wake ECU from deep sleep first")
    parser.add_argument(
        "--mode",
        dest="session_mode",
        metavar="HEX",
        default="03",
        help="Session mode for --session (default 03 = UDS extended; 81 = KWP2000 standard, "
        "e.g. BMS)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation a KWP2000 sweep asks for where 0x33 is not known to be "
        "side-effect-free (a nissan_c0_session profile)",
    )
    add_connection_args(parser)
    parser.set_defaults(func=live_command(scans.ROUTINES))
    return parser


def _add_sessions_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "sessions",
        help="SAFE diagnostic session-type discovery (0x10, auto UDS/KWP2000)",
        description="Probe which DiagnosticSessionControl (service 0x10) session types an "
        "ECU supports. The session-mode set is auto-selected per ECU from its id_protocol: "
        "UDS ECUs are probed with 01 (default) + 03 (extended); KWP2000 ECUs (BMS, VCU, MCU, "
        "LDC, AAF) with 81 (standard) + 82 + 83 (extended), plus any OEM session a profile "
        "quirk permits on KWP2000 (C0 with nissan_c0_session). Every mode is checked against "
        "the safety policy for the ECU's protocol first and a refused one is skipped — the "
        "programming sessions (UDS 0x02, also 0x82 on a UDS ECU; KWP2000 0x85) are NEVER "
        "probed. "
        "Results are written to ecus/<ecu>.yaml under a sessions: section.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair scan sessions IGPM             # UDS: probe 10 01 / 10 03\n"
        "  canair scan sessions BMS              # KWP2000: probe 10 81 / 82 / 83 (auto)\n"
        "  canair scan sessions BMS VCU MCU      # several ECUs in one run\n"
        "  canair scan sessions IGPM --modes 01,03,10   # override the probed modes\n",
    )
    parser.add_argument(
        "sessions_scan", nargs="+", metavar="ECU", help="ECUs to scan (at least one required)"
    ).completer = ecu_completer
    parser.add_argument(
        "--modes",
        metavar="HEX[,HEX...]",
        default=None,
        help="Comma-separated 0x10 sub-functions to probe, overriding the per-ECU "
        "protocol default (e.g. 01,03 or 81,82,83). Modes the safety policy refuses for "
        "the ECU's protocol (02/85, 82 on UDS, the OEM band) are skipped.",
    )
    parser.add_argument(
        "--throttle-ms", type=int, default=200, help="Delay in ms between probes (default 200)"
    )
    add_connection_args(parser)
    parser.set_defaults(func=live_command(scans.SESSIONS))
    return parser
