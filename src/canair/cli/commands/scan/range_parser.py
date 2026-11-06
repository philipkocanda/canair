"""The ``scan range`` kind: its flags."""

from __future__ import annotations

import argparse

from canair.cli.args.session import add_wake_arg
from canair.cli.live import add_connection_args, ecu_completer
from canair.live.scan.presets import presets_help

from .plan import slots
from .run import run_range


def add_range_parser(kinds) -> argparse.ArgumentParser:
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
    add_wake_arg(parser)
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
