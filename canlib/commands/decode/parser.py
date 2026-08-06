"""Argparse surface for ``canair decode``.

The flag definitions and the worked examples shown in ``--help``. Split out of
the package ``__init__`` because it is 165 lines of pure declaration with no
logic in it, and it is the part a contributor adding a flag has to read.
"""

from __future__ import annotations

import argparse

from canlib.capture_dates import add_scope_args
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.commands._join import add_join_args, add_mirror_args
from canlib.inspect_bytes import POST_TRANSFORMS
from canlib.notation import add_notation_arg
from canlib.stats import METHOD_CHEAT_SHEET as _METHOD_CHEAT_SHEET

from .entry import run

NAME = "decode"
ALIASES = ["dec"]

# The ``--help`` epilog: the worked examples, lifted verbatim from the package
# docstring this was extracted from (where it was reached as
# ``__doc__.split("Examples:")[1]``), so ``--help`` stays byte-identical. Held as
# a literal rather than re-derived from a docstring, so editing the package prose
# cannot silently reshape the help text.
EXAMPLES = '\n  canair decode BMS 2101              # Value range of every param across captures\n  canair decode BMS 2101 --param SOC_BMS SOC_DISP  # Only specific params\n  canair decode IGPM 22BC03           # Decode IGPM DID BC03\n  canair decode BMS 2101 --verified   # Only verified parameters\n  canair decode BMS 2101 --unverified # Only unverified parameters (validation focus)\n  canair decode BMS 2101 --compact    # One line per capture (value evolution)\n  canair decode ESC 22C101 --state \'MT->KW\' --compact --changes-only  # One drive, stationary runs collapsed\n  canair decode MCU 2102 --stats --group-by state  # Per-drive-segment statistics\n  canair decode VCU 2101 --date 2026-07-22 --last 20  # Last 20 captures of one day\n  canair decode BMS 2101 --json       # JSON (per-capture decoded values)\n  canair decode MCU 2102 --stats      # Descriptive stats per param (mean/median/stdev/distinct)\n  canair decode MCU 2102 --corr MCU_MOTOR_RPM   # Correlate every param vs a known signal\n  canair decode MCU 2102 --plot                      # sweep interpretations, find the signal\n  canair decode MCU 2102 --plot --corr MCU_MOTOR_RPM # overlay a known signal + live r\n  canair decode MCU 2102 --try "TORQUE:Nm=[S12:S13]/100"   # Test a candidate expression\n  canair decode MCU 2102 --try "T=[S17:S18]" --corr MCU_MOTOR_RPM  # Validate a candidate by correlation\n  canair decode MCU 21F2 --try "X=B9" --try "Y=[S10:S11]"  # Multiple candidates, undefined PID OK\n  canair decode BMS 2101 --dump-bytes         # timestamp x byte-offset matrix (CSV, PCI skipped)\n  canair decode BMS 2101 --dump-bytes --json  # same matrix as JSON (ad-hoc analysis escape hatch)\n'


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Decode captured UDS payloads using PID parameter definitions",
        description="Decode captured UDS payloads using PID parameter definitions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES + "\n" + _METHOD_CHEAT_SHEET,
    )
    parser.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="ECU/PID selection (mini-language, see canlib/query.py): 'BMS 2101', "
        "'BMS:2101', 'BMS:2101,2102', 'BMS' (all defined PIDs), or a quoted "
        "cross-ECU query 'MCU:2102 VCU:2101'. Multi-PID queries are supported for "
        "the default value-range, --compact and --json views; the analysis modes "
        "(--corr/--plot/--stats/--discriminate/--find-mirrors/--try/--dump-bytes) "
        "require the query to resolve to a single PID.",
    ).completer = _ecu_completer
    parser.add_argument(
        "--param",
        action="extend",
        nargs="+",
        metavar="NAME",
        help="Show only specific parameters (repeatable and/or space-separated: "
        "--param A B or --param A --param B)",
    )
    parser.add_argument("--verified", action="store_true", help="Show only verified parameters")
    parser.add_argument("--unverified", action="store_true", help="Show only unverified parameters")
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON (per-capture decoded values)"
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="One line per capture (chronological param=value pairs)",
    )
    parser.add_argument(
        "--changes-only",
        "-c",
        action="store_true",
        help="With --compact: skip rows where all shown params are "
        "unchanged from the previous row (collapses stationary runs)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Descriptive statistics per param (n, distinct, mean, median, stdev)",
    )
    parser.add_argument(
        "--group-by",
        choices=["state", "vehicle_states"],
        metavar="FIELD",
        help="With --stats: compute statistics per session FIELD "
        "(currently 'state') instead of pooling all captures",
    )
    parser.add_argument(
        "--discriminate",
        metavar="AXIS",
        help="Rank params/bytes by how cleanly they separate across AXIS groups "
        "(F = between/within variance; Cramér's V for typed params) — finds "
        "axis-dependent signals a driving correlation misses. AXIS is 'state' "
        "(the vehicle power state) or a cross-signal ECU:PID:PARAM to group by "
        "(e.g. HVAC:220102:HVAC_COMPRESSOR_ON — which byte separates on from off)",
    )
    parser.add_argument(
        "--find-mirrors",
        action="store_true",
        help="Report byte positions on this PID that mirror each other — redundant "
        "status mirrors and unit-variants. Add --bits for bit-level, --allow-offset "
        "to accept an offset/scale, --mirror-match to change the agreement required",
    )
    parser.add_argument(
        "--bits",
        action="store_true",
        help="With --find-mirrors: compare individual bits (Bn:k). "
        "With --discriminate: also rank individual toggling bits across the axis",
    )
    parser.add_argument(
        "--bytes",
        action="store_true",
        help="With --discriminate: also rank every varying raw byte (Bn), not "
        "just defined params — finds axis-dependent bytes without a --try",
    )
    parser.add_argument(
        "--first", type=int, metavar="N", help="Only the first N matching captures (chronological)"
    )
    parser.add_argument(
        "--last", type=int, metavar="N", help="Only the last N matching captures (chronological)"
    )
    parser.add_argument(
        "--corr",
        metavar="PARAM",
        help="Correlate every param (incl. --try) against PARAM (Pearson r). "
        "PARAM may be a local param name, or a cross-signal reference "
        "ECU:PID:PARAM or ECU:PID:EXPR (e.g. ESC:22C101:REAL_SPEED_KMH) which is "
        "time-aligned by nearest timestamp.",
    )
    add_join_args(parser)
    add_mirror_args(parser)
    parser.add_argument(
        "--corr-transform",
        choices=list(POST_TRANSFORMS),
        metavar="MODE",
        help="Transform the --corr reference before pairing "
        "(raw/delta/abs/cumsum/normalize/smooth) — e.g. --corr-transform delta "
        "to test whether a signal tracks a reference's RATE rather than its level",
    )
    parser.add_argument(
        "--method",
        choices=["pearson", "spearman", "cramers_v", "mutual_info"],
        default="pearson",
        help="Coefficient for --corr: pearson (linear, default) or spearman "
        "(rank — catches monotone-but-nonlinear/quantized/saturating links), or "
        "the categorical cramers_v / mutual_info (nominal association — for "
        "mode/flag/enum references where numeric spacing is meaningless)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Interactive signal explorer: sweep byte interpretations "
        "(u8/i16/f32/... and endianness) and params, plot across captures, "
        "apply transforms (delta/abs/normalize/...), zoom/pan the x-axis, "
        "overlay a --corr signal, and flag bytes already mapped by a param",
    )
    parser.add_argument(
        "--try",
        dest="try_expr",
        action="append",
        metavar="NAME[:unit]=EXPR",
        help="Evaluate a candidate expression against captures without editing "
        "YAML (repeatable; works even if the PID has no params defined yet)",
    )
    parser.add_argument(
        "--dump-bytes",
        dest="dump_bytes",
        action="store_true",
        help="Emit a timestamp × byte-offset matrix (one row per capture) instead "
        "of decoding params — the escape hatch for ad-hoc byte analysis. CSV by "
        "default; add --json for JSON. PCI framing bytes are skipped unless "
        "--include-pci. Honours --notation for column labels and all scope flags",
    )
    parser.add_argument(
        "--include-pci",
        dest="include_pci",
        action="store_true",
        help="With --dump-bytes: include ISO-TP PCI framing bytes (skipped by default)",
    )
    parser.add_argument(
        "--signed",
        dest="dump_signed",
        action="store_true",
        help="With --dump-bytes: render each data byte as a signed value (-128..127) "
        "with an Snn column header, instead of the default unsigned Bnn (0..255). "
        "Use when a byte is the high half of a signed value (a 0xFF near-zero baseline "
        "correlates poorly unsigned but cleanly signed)",
    )
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser
