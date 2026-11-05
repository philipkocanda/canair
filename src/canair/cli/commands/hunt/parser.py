"""The ``canair hunt`` parser: the group and its two kinds."""

from __future__ import annotations

import argparse

from canair.analysis.align import (
    DEFAULT_SESSION_GAP_S,
)
from canair.cli.args.can import add_can_log_source_args
from canair.cli.args.group import group_help
from canair.cli.args.join import (
    add_join_args,
)
from canair.cli.args.notation import add_notation_arg
from canair.cli.args.scope import add_scope_args

from .can import run_can_log
from .uds import run

NAME = "hunt"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Identify which byte *is* a known signal: uds (a PID) | can (a frame ID)",
        description=(
            "Answer 'which byte carries a signal I already know?' Choose a domain kind:\n"
            "  uds   sweep a diagnostic PID's bytes vs a known signal (domain A).\n"
            "        A bare `canair hunt AAF 2181 --against …` is shorthand for this.\n"
            "  can   sweep a raw broadcast-CAN frame ID's bytes vs a reference frame\n"
            "        byte in the same log (domain B), bytes labelled 0xID:rN.\n\n"
            "Read-only: analyses captures/ only, never talks to the device."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="hunt_kind", metavar="<kind>")
    _add_uds_parser(kinds)
    _add_can_parser(kinds)
    parser.set_defaults(func=group_help("_hunt_group_parser"), _hunt_group_parser=parser)
    return parser


def _add_shared_hunt_args(parser) -> None:
    """Flags common to both the uds and can hunt kinds."""
    parser.add_argument(
        "--min-n", type=int, default=10, metavar="N", help="Min aligned points (default 10)"
    )
    parser.add_argument("--top", type=int, default=12, metavar="N", help="Max hits (default 12)")
    parser.add_argument(
        "--transform",
        choices=["raw", "delta", "abs", "cumsum", "normalize", "smooth"],
        default="raw",
        metavar="MODE",
        help="Transform the reference before aligning (e.g. delta to hunt the "
        "byte that tracks the reference's *rate* — torque vs acceleration)",
    )
    parser.add_argument(
        "--method",
        choices=["pearson", "spearman"],
        default="pearson",
        help="Ranking coefficient: pearson (linear, default) or spearman (rank)",
    )
    add_join_args(parser)
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument(
        "--all-interps",
        action="store_true",
        help="Show every interpretation per offset (u8/i16/u24/…); default "
        "collapses to the best interpretation per byte offset",
    )


def _add_can_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "can",
        help="Hunt a raw broadcast-CAN frame ID's bytes vs a reference (domain B)",
        description=(
            "Hunt on a raw broadcast-CAN frame log: sweep every byte/interpretation "
            "of --id's frames vs --against (a frame byte 0xID:rN in the same log). "
            "Hits are raw-CAN rN labels (no WiCAN expr); --promote is not supported "
            "for frames yet (frame signals are defined in signals/)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_can_log_source_args(parser)
    parser.add_argument(
        "--id",
        required=True,
        metavar="ID",
        help="The arbitration ID to hunt on (e.g. 0x220)",
    )
    parser.add_argument(
        "--against",
        required=True,
        metavar="0xID:rN",
        help="Reference frame byte in the same log (e.g. 0x386:r0)",
    )
    _add_shared_hunt_args(parser)
    add_notation_arg(parser)
    parser.set_defaults(func=run_can_log)
    return parser


def _add_uds_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "uds",
        help="Hunt a diagnostic PID's bytes vs a known signal (domain A)",
        description=(
            "Sweeps every byte offset of the target PID under every interpretation\n"
            "(u8/i16/u24/f32/... x endianness), time-aligns each candidate against a\n"
            "known reference signal on another ECU/PID (--against), and ranks them by\n"
            "|Pearson r|. Each top hit reports a linear fit (y=m·x+c), the residual,\n"
            "and a physical-unit guess — so you learn not just which byte, but its\n"
            "scale and offset.\n\n"
            "This automates the 'which byte tracks vehicle speed / motor RPM?' scratch\n"
            "work. Use --transform delta to hunt the byte tracking the reference's\n"
            "*rate* (e.g. torque vs acceleration), and --promote to write the top hit\n"
            "straight into ecus/ as an unverified candidate parameter.\n\n"
            "Read-only: analyses captures/ only, never talks to the device."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # which byte on AAF 2181 is vehicle speed (known on ESC 22C101)?
  canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH

  # restrict to drive captures and only strong linear fits
  canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH --state driving --min-n 30

  # hunt the byte that tracks the reference's *rate* (acceleration, not speed)
  canair hunt MCU 2102 --against ESC:22C101:REAL_SPEED_KMH --transform delta

  # rank by rank-correlation (catches quantized / saturating links)
  canair hunt MCU 2102 --against ESC:22C101:REAL_SPEED_KMH --method spearman

  # write the winning byte into ecus/ as an unverified candidate param
  canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH --promote WHEEL_SPEED_KMH

  # reference an EXTERNAL log (calibrated meter / GPS / grid-voltage export),
  # joined by nearest timestamp on the captures' absolute clock
  canair hunt OBC 2101 --against-file grid_voltage.csv --state charging

  # confounder control: which byte tracks the grid once the IR-drop current is
  # regressed out? (partial correlation — surfaces links a dominant driver hides)
  canair hunt OBC 2101 --against-file grid_voltage.csv --control OBC:2101:OBC_DC_A

  # no reference at all: flag bytes whose scaled value lands in a physical band
  # (mains RMS/peak, line freq, 12V rail, HV pack) — finds an anchorless signal
  canair hunt OBC 2101 --physical --state charging

tip: --against takes a known signal ECU:PID:PARAM (or a raw ECU:PID:EXPR). Use
     `canair correlate --overlap` first to find a reference that actually shares
     time-aligned samples with your target.""",
    )
    parser.add_argument("ecu", nargs="?", help="Target ECU to hunt on (e.g. AAF)")
    parser.add_argument("pid", nargs="?", help="Target PID to hunt on (e.g. 2181)")
    ref_group = parser.add_mutually_exclusive_group(required=True)
    ref_group.add_argument(
        "--against",
        metavar="ECU:PID:PARAM",
        help="Reference signal: a diagnostic ECU:PID:PARAM (or ECU:PID:EXPR)",
    )
    ref_group.add_argument(
        "--against-file",
        dest="against_file",
        metavar="FILE",
        help="Reference from an external CSV (timestamp,value) instead of a bus "
        "signal — a calibrated meter log, GPS track, grid-voltage export. Joined "
        "by nearest timestamp; the file must be on the same absolute clock as the "
        "captures (relative/zero-based logs won't align)",
    )
    ref_group.add_argument(
        "--physical",
        action="store_true",
        help="No reference: flag bytes whose (scaled) value lands in a named "
        "physical band (mains RMS/peak, line freq, 12V rail, HV pack) at some "
        "scaling (/1 /10 /100 ×2 ×√2). Finds anchorless signals by plausibility",
    )
    _add_shared_hunt_args(parser)
    parser.add_argument(
        "--control",
        metavar="ECU:PID:PARAM",
        help="Confounder control: regress out this nuisance signal and rank by the "
        "PARTIAL correlation (what remains after removing the control's linear "
        "influence). Surfaces a byte whose link to --against only shows once the "
        "dominant driver is removed (e.g. AC voltage behind the IR-drop current)",
    )
    parser.add_argument(
        "--control-file",
        dest="control_file",
        metavar="FILE",
        help="Like --control, but the nuisance signal is an external "
        "timestamp,value CSV (mutually exclusive with --control)",
    )
    parser.add_argument(
        "--promote",
        metavar="NAME",
        help="Write the top hit's expression to ecus/ as an unverified "
        "candidate param NAME (via pids upsert-param), with the correlation "
        "evidence auto-filled into notes",
    )
    parser.add_argument(
        "--per-session",
        dest="per_session",
        action="store_true",
        help="Remove each recording session's DC baseline before ranking — makes a "
        "slowly-varying absolute-level reference/byte (pack/12V/mains voltage, a "
        "held temperature) rankable by --against instead of being dominated by "
        "cross-session offsets. Ranks the in-session *variation*, not the level",
    )
    parser.add_argument(
        "--session-gap",
        dest="session_gap",
        type=float,
        default=DEFAULT_SESSION_GAP_S,
        metavar="SECONDS",
        help=f"With --per-session: time gap that starts a new session (default {DEFAULT_SESSION_GAP_S}s)",
    )
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser
