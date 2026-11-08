"""The safe discovery kinds of ``canair scan``: iocontrol, routines and sessions."""

from __future__ import annotations

import argparse

from canair.cli.args.session import add_wake_arg
from canair.cli.live import add_connection_args, ecu_completer, live_command
from canair.cli.live.handlers import scans

# ---------------------------------------------------------------------------
# scan iocontrol — IOControl discovery (UDS 0x2F / KWP2000 0x30, auto)
# ---------------------------------------------------------------------------


def add_iocontrol_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "iocontrol",
        help="IOControl discovery (UDS 0x2F; KWP2000 0x30 asks first)",
        description="Probe returnControlToECU across an id range on one or more ECUs. "
        "The service is auto-selected per ECU from its id_protocol: UDS ECUs use "
        "InputOutputControlByIdentifier (0x2F, 16-bit DID); KWP2000 ECUs (BMS, VCU, "
        "MCU, AAF) use InputOutputControlByLocalIdentifier (0x30, 8-bit LID). "
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
    add_wake_arg(parser)
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


# ---------------------------------------------------------------------------
# scan routines — SAFE RoutineControl (0x31) discovery
# ---------------------------------------------------------------------------


def add_routines_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "routines",
        help="SAFE RoutineControl discovery (UDS 0x31 SF03 / KWP2000 0x33, auto)",
        description="Probe routine results across a range on one or more ECUs. The service "
        "is auto-selected per ECU from its id_protocol: UDS ECUs use RoutineControl "
        "(0x31, requestRoutineResults SF 0x03); KWP2000 ECUs (BMS, VCU, MCU, AAF) use "
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
    add_wake_arg(parser)
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
        help="Skip the confirmations: the one a KWP2000 sweep asks for where 0x33 is not known "
        "to be side-effect-free (a nissan_c0_session profile), and the one for an ECU whose "
        "protocol the profile does not record (run `canair identity ECU` to record it)",
    )
    add_connection_args(parser)
    parser.set_defaults(func=live_command(scans.ROUTINES))
    return parser


# ---------------------------------------------------------------------------
# scan sessions — SAFE diagnostic session-type discovery (service 0x10)
# ---------------------------------------------------------------------------


def add_sessions_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "sessions",
        help="SAFE diagnostic session-type discovery (0x10, auto UDS/KWP2000)",
        description="Probe which DiagnosticSessionControl (service 0x10) session types an "
        "ECU supports. The session-mode set is auto-selected per ECU from its id_protocol: "
        "UDS ECUs are probed with 01 (default) + 03 (extended); KWP2000 ECUs (BMS, VCU, MCU, "
        "AAF) with 81 (standard) + 82 + 83 (extended), plus any OEM session a profile "
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
