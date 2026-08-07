"""Argparse surface for ``canair investigate`` — the group and its uds/can kinds."""

from __future__ import annotations

import argparse

from canlib.capture_dates import add_scope_args
from canlib.commands._group import group_help
from canlib.commands._join import (
    add_join_args,
)
from canlib.counters import DEFAULT_MAX_WIDTH, DEFAULT_MIN_BITS
from canlib.notation import (
    add_notation_arg,
)

from .can import _run_can
from .uds import run

NAME = "investigate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Explain an unknown signal: uds (a PID) | can (an arbitration ID in a frame log)",
        description=(
            "Point this at an unknown signal and get one ranked table telling you\n"
            "everything worth knowing about each of its bytes. Choose a domain kind:\n"
            "  uds   a diagnostic PID (default) — per byte: mapped? / state F /\n"
            "        best co-polled anchor / unit guess (domain A). A bare\n"
            "        `canair investigate MCU 2102` is shorthand for this.\n"
            "  can   an arbitration ID in a raw broadcast-CAN frame log (domain B) —\n"
            "        per byte: best cross-ID anchor + linear fit + unit guess.\n\n"
            "Read-only: analyses captures/ only, never talks to the device."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="investigate_kind", metavar="<kind>")
    _add_uds_parser(kinds)
    _add_can_parser(kinds)
    parser.set_defaults(
        func=group_help("_investigate_group_parser"), _investigate_group_parser=parser
    )
    return parser


def _add_uds_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "uds",
        help="Explain a diagnostic PID (domain A)",
        description=(
            "Point this at an unknown PID and get one ranked table telling you\n"
            "everything worth knowing about each of its bytes — the fastest way to\n"
            "start decoding.\n\n"
            "For every varying data byte of ECU PID it reports, in one pass:\n"
            "  - mapped?   whether a defined parameter already decodes this byte\n"
            "              (a verified param hides the byte by default; an\n"
            "              unverified [param?] mapping is shown as still-open work)\n"
            "  - stateF    how cleanly the byte separates across power states\n"
            "              (sleep/acc/ready/charging) — high F = a mode/relay/thermal\n"
            "              signal a driving correlation would miss\n"
            "  - anchor    the strongest-correlating known signal on another\n"
            "              co-polled ECU/PID (Pearson r + linear fit y=m·x+c)\n"
            "  - unit      a physical-unit guess for that fit (e.g. raw-40 degC,\n"
            "              x1.609 mph->km/h)\n\n"
            "Bytes are ranked strongest-anchor-first, then by state separation, so\n"
            "the most decodable bytes float to the top. This bundles the manual\n"
            "coverage -> discriminate -> correlate -> hunt loop into a single call.\n\n"
            "ECU and PID are optional: give both for the full per-byte deep-dive,\n"
            "an ECU (or a QUERY) alone to sweep its PIDs, or nothing to sweep the\n"
            "whole profile. A sweep prints a ranked SUMMARY per PID (--top caps it)\n"
            "rather than N full reports.\n\n"
            "--counters switches to a different question — 'which bytes here only\n"
            "ever go UP?' — sweeping multi-byte windows for odometers, operating-hour\n"
            "tallies, power-cycle counts and uptime timers. Those are invisible to\n"
            "the default view (a slow counter looks constant within one session),\n"
            "so they need the whole capture history rather than a scoped window.\n\n"
            "Read-only: analyses captures/ only, never talks to the device. Once a\n"
            "byte looks promising, confirm the exact expression with `canair hunt\n"
            "ECU PID --against ...` and write it with `canair pids upsert-param`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair investigate MCU 2102              # rank unmapped + unverified-mapped bytes of MCU 2102
  canair investigate MCU 2102 --all        # include bytes a verified param already maps
  canair investigate BMS                   # sweep every captured BMS PID (ranked summary)
  canair investigate                       # sweep the whole profile (ranked summary)
  canair investigate --counters            # find every monotonic counter in the car
  canair investigate BMS --counters        # every counter across BMS's PIDs
  canair investigate IGPM 22BC03 --bits    # rank toggling bits (body/status-ECU work)
  canair investigate IGPM 22BC03 --events  # bit/byte edges aligned to the event timeline
  canair investigate CLU 22B002 --counters # hunt monotonic counters (odometer / cycle count)
  canair investigate BMS 2101 --counters --unmapped-only   # only counters not settled yet
  canair investigate BMS 2101 --state driving   # only consider drive captures
  canair investigate ESC 22C101 --min-r 0.8      # only show strong anchors (|r| >= 0.8)
  canair investigate AAF 2181 --json       # machine-readable output

  # active-but-independent: rank bytes that separate by state yet DON'T track a
  # named driver — the fingerprint of AC voltage vs charge current
  canair investigate OBC 2101 --independent-of OBC:2101:OBC_DC_A --state charging

tip: no anchors found? widen scope (drop --state), lower --min-r, or grow the
     capture set — an anchor needs another co-polled signal it can align to. For
     a body/comfort PID with no co-polled partner, use --bits / --events (the
     signals are toggling status bits, ranked by state separation + edge time).""",
    )
    parser.add_argument(
        "ecu",
        nargs="?",
        help='Target ECU (e.g. MCU), or a QUERY (e.g. "BMS:2101,2102"). Omit ECU '
        "and PID to sweep the whole profile; give an ECU alone to sweep its PIDs.",
    )
    parser.add_argument("pid", nargs="?", help="Target PID (e.g. 2102). Omit to sweep the ECU.")
    parser.add_argument(
        "--min-r",
        type=float,
        default=0.6,
        metavar="R",
        help="Only report an anchor when |r| ≥ this (default 0.6)",
    )
    parser.add_argument(
        "--min-n", type=int, default=15, metavar="N", help="Min aligned points (default 15)"
    )
    add_join_args(parser)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include bytes a verified param already maps (default: hide only verified-mapped)",
    )
    parser.add_argument(
        "--bits",
        action="store_true",
        help="Also analyse individual toggling bits (Bn:k) — the body/status-ECU finder",
    )
    parser.add_argument(
        "--events",
        action="store_true",
        help="Report each bit/byte rising/falling edge with its timestamp, aligned to "
        "the nearest capture note (the narrated event timeline)",
    )
    parser.add_argument(
        "--dwell",
        action="store_true",
        help="Summarise each event bit/byte by how long it stays ON — median "
        "on-duration + a momentary|sustained class. Separates a briefly-pulsed "
        "bit (a door flicked open) from one held for minutes (a hood left up), so "
        "body/event signals are identifiable without capture-note narration. "
        "Needs --keep-all/--keep-changes data (keep:unique drops falling edges)",
    )
    parser.add_argument(
        "--field",
        metavar="NAME",
        help="With --events: track ONE defined param (a typed enum/bitmask/struct "
        "date field) as a single logical signal — emit one transition per change of "
        "its DECODED value (e.g. {Mon 08:00}->{Tue 07:30}), not scattered per-byte "
        "edges. NAME is a parameter of the target ECU:PID.",
    )
    parser.add_argument(
        "--counters",
        action="store_true",
        help="Hunt MONOTONIC COUNTERS instead: sweep every 1-4-byte window x "
        "endianness for a value that only ever rises across the capture corpus "
        "(odometer, operating-hours, ignition/power-cycle count) or ramps and "
        "resets per session (uptime). Ranks by bits of monotonic evidence",
    )
    parser.add_argument(
        "--min-bits",
        type=float,
        default=DEFAULT_MIN_BITS,
        metavar="BITS",
        help=f"With --counters: minimum bits of monotonic evidence (default "
        f"{DEFAULT_MIN_BITS:g}). Each clean up-step with no down-step is 1 bit, so "
        f"8 bits ≈ 8 rises and no falls (1-in-256 by chance). Lower it to surface "
        f"sparse long-horizon counters read only a handful of times",
    )
    parser.add_argument(
        "--counter-width",
        type=int,
        default=DEFAULT_MAX_WIDTH,
        metavar="N",
        help=f"With --counters: widest byte window to test (default {DEFAULT_MAX_WIDTH})",
    )
    parser.add_argument(
        "--unmapped-only",
        action="store_true",
        help="With --counters: hide windows a VERIFIED parameter already decodes. A "
        "window mapped only by an unverified guess is kept (tagged [NAME?]) — "
        "monotonicity is often the evidence that refutes such a guess",
    )
    parser.add_argument(
        "--independent-of",
        dest="independent_of",
        metavar="ECU:PID:PARAM",
        help="Rank bytes that separate by state yet DON'T track this driver signal "
        "— the 'active-but-independent' finder (e.g. AC voltage: varies while "
        "charging but is uncorrelated with charge current). Adds a driver-r column "
        "and re-ranks by state separation weighted by independence from the driver",
    )
    parser.add_argument(
        "--independent-of-file",
        dest="independent_of_file",
        metavar="FILE",
        help="Like --independent-of, but the driver is an external timestamp,value "
        "CSV (mutually exclusive with --independent-of)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument(
        "--top",
        type=int,
        default=40,
        metavar="N",
        help="In a corpus/ECU sweep (ECU or PID omitted): cap the ranked summary to "
        "the top N rows (default 40; 0 = no cap). Ignored for a single PID.",
    )
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def _add_can_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "can",
        help="Explain an arbitration ID in a raw broadcast-CAN frame log (domain B)",
        description=(
            "Explain one arbitration ID in a raw broadcast-CAN frame log: for every\n"
            "varying data byte, report its strongest cross-ID anchor (Pearson r +\n"
            "linear fit y=m·x+c) and a physical-unit guess, ranked strongest first.\n\n"
            "The domain-B analogue of `investigate uds`: frames have no defined-param\n"
            "mapping (signals live in signals/, decoded via Stage-4 tooling) and no\n"
            "power-state metadata, so the report is anchor-centric. Bytes are\n"
            "labelled 0xID:rN (raw-CAN space, no PCI). Read-only.\n\n"
            "To pin the exact byte against a known reference use `canair hunt can`;\n"
            "to see every relationship at once use `canair correlate can`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair investigate can drive.blf --id 0x386        # rank each byte of 0x386 by best cross-ID anchor
  canair investigate can drive.csv --id 0x220 --bits # include toggling bits
  canair investigate can drive.asc --id 0x386 --json # machine-readable""",
    )
    parser.add_argument(
        "file",
        metavar="FILE",
        help="Path to a raw broadcast-CAN frame log (.asc/.blf/candump .log/.trc/GVRET .csv)",
    )
    parser.add_argument(
        "--id",
        required=True,
        metavar="ID",
        help="Arbitration ID to explain (e.g. 0x386)",
    )
    parser.add_argument(
        "--can-format",
        choices=["auto", "asc", "blf", "csv", "log", "gvret"],
        default="auto",
        help="Log format (default: auto-detect by extension)",
    )
    parser.add_argument(
        "--min-r",
        type=float,
        default=0.6,
        metavar="R",
        help="Only report an anchor when |r| ≥ this (default 0.6)",
    )
    parser.add_argument(
        "--min-n", type=int, default=15, metavar="N", help="Min aligned points (default 15)"
    )
    add_join_args(parser)
    parser.add_argument(
        "--bits",
        action="store_true",
        help="Also analyse individual toggling bits (rN:k)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.set_defaults(func=_run_can)
    return parser
