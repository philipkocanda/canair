"""Argparse surface for ``canair correlate`` — the group and its uds/can kinds."""

from __future__ import annotations

import argparse

from canlib.align import (
    DEFAULT_SESSION_GAP_S,
)
from canlib.capture_dates import add_scope_args
from canlib.commands._can_args import add_can_log_source_args
from canlib.commands._group import group_help
from canlib.commands._join import (
    add_join_args,
    add_mirror_args,
)
from canlib.notation import add_notation_arg
from canlib.stats import METHOD_CHEAT_SHEET as _METHOD_CHEAT_SHEET

from .can import _run_can_log
from .uds import run

NAME = "correlate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Find every strong cross-signal relationship: uds (captures) | can (frame log)",
        description=(
            "Find every strong cross-signal relationship across a drive/session.\n"
            "Choose a domain kind:\n"
            "  uds   diagnostic captures (default) — co-polled ECU/PID params/bytes\n"
            "        (domain A). A bare `canair correlate …` is shorthand for this.\n"
            "  can   a raw broadcast-CAN frame log's per-byte series (domain B),\n"
            "        bytes labelled 0xID:rN.\n\n"
            "Read-only: analyses captures/ only, never talks to the device. To pin\n"
            "down *which byte* a relationship lives in, follow up with `canair hunt`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="correlate_kind", metavar="<kind>")
    _add_uds_parser(kinds)
    _add_can_parser(kinds)
    parser.set_defaults(func=group_help("_correlate_group_parser"), _correlate_group_parser=parser)
    return parser


def _add_can_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "can",
        help="Correlate a raw broadcast-CAN frame log's per-byte series (domain B)",
        description=(
            "Correlate the per-byte series of a raw broadcast-CAN frame log "
            "(.asc/.blf/candump .log/.trc/GVRET .csv) — bytes are labelled 0xID:rN. "
            "--against/--bits/--id/--min-r/--top/--find-mirrors all apply."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_METHOD_CHEAT_SHEET,
    )
    add_can_log_source_args(parser)
    parser.add_argument(
        "--id",
        metavar="IDS",
        help="Restrict to comma-separated arbitration IDs (e.g. 0x220,0x386)",
    )
    parser.add_argument(
        "--include-intra",
        action="store_true",
        help="Include same-arbitration-ID pairs (default: cross-ID only)",
    )
    parser.add_argument(
        "--find-mirrors",
        action="store_true",
        help="Instead of ranking correlations, report byte/bit positions mirrored "
        "ACROSS arbitration IDs (time-aligned) — a signal broadcast on two IDs (e.g. "
        "wheel speed on 0x386 and 0x331). Use with --bits for bit-level and "
        "--allow-offset for offset/scale mirrors",
    )
    add_mirror_args(parser)
    parser.add_argument(
        "--no-cluster",
        action="store_true",
        help="Don't collapse near-perfectly-correlated (|r|≥0.995) byte groups into one line",
    )
    _add_shared_analysis_args(parser)
    add_notation_arg(parser)
    parser.set_defaults(func=_run_can_log)
    return parser


def _add_shared_analysis_args(parser) -> None:
    """Flags common to both the uds and can correlate kinds."""
    parser.add_argument(
        "--against",
        metavar="ECU:PID:PARAM",
        help="Correlate every signal against this one reference "
        "(e.g. ESC:22C101:REAL_SPEED_KMH) instead of the full matrix",
    )
    parser.add_argument(
        "--min-r", type=float, default=0.6, metavar="R", help="Min |r| to report (default 0.6)"
    )
    parser.add_argument(
        "--min-n", type=int, default=15, metavar="N", help="Min aligned points (default 15)"
    )
    parser.add_argument("--top", type=int, default=40, metavar="N", help="Max hits (default 40)")
    parser.add_argument(
        "--method",
        choices=["pearson", "spearman", "cramers_v", "mutual_info"],
        default="pearson",
        help="Association coefficient: pearson (linear, default) or spearman "
        "(rank — catches monotone-but-nonlinear/quantized/saturating links), or "
        "the categorical cramers_v / mutual_info (treat each value as a nominal "
        "category — for mode/flag/enum bytes where numeric spacing is meaningless)",
    )
    add_join_args(parser)
    parser.add_argument(
        "--bits",
        action="store_true",
        help="Include individual toggling bits (rN:k / Bn:k)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")


def _add_uds_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "uds",
        help="Correlate co-polled diagnostic captures (domain A)",
        description=(
            "Show me every strong relationship across a whole drive.\n\n"
            "Builds every decoded parameter (and, with --bytes, every varying raw\n"
            "byte; --bits for toggling bits) across all co-polled ECU/PIDs in scope,\n"
            "time-aligns them by nearest timestamp, and ranks the strongest\n"
            "cross-signal correlations. This is how the AAF-speed and MCU-temp links\n"
            "were originally found by hand.\n\n"
            "Three ways to use it:\n"
            "  (default)     ranked list of the strongest cross-ECU/PID pairs\n"
            "  --against R   rank every signal against one reference R=ECU:PID:PARAM\n"
            "  --matrix      a labelled correlation r-matrix\n\n"
            "Use --overlap first to see which ECU:PID pairs actually share aligned\n"
            "samples (so you pick a viable --against). --gate isolates a regime\n"
            "(e.g. 'while moving'), --lag-scan reveals command->response ordering,\n"
            "and --promote writes the top raw-byte hit into ecus/.\n\n"
            "Read-only: analyses captures/ only, never talks to the device. To pin\n"
            "down *which byte* a relationship lives in, follow up with `canair hunt`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
examples:
  # every strong relationship in the most recent drive
  canair correlate --state driving

  # which ECU:PID pairs even share aligned samples? (pick an --against target)
  canair correlate --overlap --state driving

  # rank every signal against a known speed reference
  canair correlate --against ESC:22C101:REAL_SPEED_KMH --state driving

  # include raw bytes + bits (finds undecoded status/relay signals)
  canair correlate --against ESC:22C101:REAL_SPEED_KMH --bytes --bits

  # only while moving (isolate a regime whole-history correlation dilutes)
  canair correlate --against ESC:22C101:REAL_SPEED_KMH --gate '> 0'

  # restrict to a couple of ECUs and show the full r-matrix
  canair correlate "MCU VCU" --matrix

  # rank every byte against an EXTERNAL log (meter/GPS/grid), nearest-timestamp join
  canair correlate --against-file grid_voltage.csv --bytes --state charging

  # partial correlation: rank every byte vs the grid with the charge current
  # regressed out — surfaces a signal only visible once the driver is removed
  canair correlate --against-file grid_voltage.csv --control OBC:2101:OBC_DC_A --bytes

  # spearman ranks catch monotone-but-nonlinear links
  canair correlate --against ESC:22C101:REAL_SPEED_KMH --method spearman

{_METHOD_CHEAT_SHEET}""",
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Optional ECU[:PID] selector(s) to restrict the signals "
        "(e.g. 'MCU VCU' or 'ESC:22C101'); default = all co-polled in scope",
    )
    parser.add_argument(
        "--transform",
        choices=["raw", "delta", "abs", "cumsum", "normalize", "smooth"],
        default="raw",
        metavar="MODE",
        help="With --against: transform the reference before aligning (e.g. "
        "delta to rank signals against the reference's *rate*)",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Print a labelled r-matrix instead of a ranked pair list",
    )
    parser.add_argument(
        "--against-file",
        dest="against_file",
        metavar="FILE",
        help="Rank every signal against an external CSV (timestamp,value) reference "
        "instead of a bus signal — a calibrated meter log, GPS track, grid-voltage "
        "export. Joined by nearest timestamp; the file must be on the same absolute "
        "clock as the captures (relative/zero-based logs won't align)",
    )
    parser.add_argument(
        "--include-intra",
        action="store_true",
        help="Include same-ECU+PID pairs (default: cross-PID/ECU only)",
    )
    parser.add_argument(
        "--include-self",
        action="store_true",
        help="With --against: keep the reference's own signal (trivial r=1.0; dropped by default)",
    )
    _add_shared_analysis_args(parser)
    parser.add_argument(
        "--per-session",
        dest="per_session",
        action="store_true",
        help="Remove each recording session's DC baseline before correlating — makes "
        "slowly-varying absolute-level signals (pack/12V/mains voltage, a held "
        "temperature) rankable instead of dominated by cross-session offsets. "
        "Ranks in-session variation, not the level (so absolute scale is lost)",
    )
    parser.add_argument(
        "--session-gap",
        dest="session_gap",
        type=float,
        default=DEFAULT_SESSION_GAP_S,
        metavar="SECONDS",
        help=f"With --per-session: time gap that starts a new session (default {DEFAULT_SESSION_GAP_S}s)",
    )
    parser.add_argument(
        "--no-cluster",
        action="store_true",
        help="Don't collapse near-perfectly-correlated (|r|≥0.995) signal groups "
        "into a single summary line (e.g. balanced cell voltages while charging)",
    )
    parser.add_argument("--bytes", action="store_true", help="Include raw varying bytes (Bn)")
    parser.add_argument(
        "--lag-scan",
        type=int,
        default=0,
        metavar="N",
        help="With --against: shift each signal by ±N sample-intervals and report "
        "the lag maximising |r| (apparent lag incl. poll offset — not proven "
        "causality). Reveals command→response ordering across ECUs",
    )
    parser.add_argument(
        "--gate",
        metavar="'[SIGNAL] OP VALUE'",
        help="With --against: only count points where a predicate holds, e.g. "
        "'> 0' (reference itself — 'while moving') or 'MCU:2102:MCU_MOTOR_RPM > 0' "
        "(a named signal). Isolates a regime whole-history correlation dilutes",
    )
    parser.add_argument(
        "--control",
        metavar="ECU:PID:PARAM",
        help="With --against: regress out this nuisance signal and rank by the "
        "PARTIAL correlation (what remains after removing the control's linear "
        "influence) — surfaces signals visible only once the dominant driver is "
        "removed. --control-file takes an external timestamp,value CSV instead",
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
        help="With --against: write the top raw-byte hit to ecus/ as an enabled, "
        "unverified candidate param NAME (via pids upsert-param), with the "
        "correlation evidence auto-filled into notes",
    )
    parser.add_argument(
        "--overlap",
        action="store_true",
        help="Instead of correlating, report which ECU:PID pairs share "
        "time-aligned samples (and how many) in scope — pick a viable "
        "--against reference without trial and error",
    )
    parser.add_argument(
        "--find-mirrors",
        action="store_true",
        help="Instead of ranking correlations, report byte/bit positions mirrored "
        "ACROSS co-polled ECU/PIDs (time-aligned) — e.g. a door bit in IGPM also "
        "present in BCM, or a temperature another ECU reports at a different offset. "
        "Use with --bits for bit-level and --allow-offset for offset/scale mirrors. "
        "Cross-ECU companion to `decode --find-mirrors` (which is single-PID)",
    )
    add_mirror_args(parser)
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser
