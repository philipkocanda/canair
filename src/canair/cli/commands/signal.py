"""``canair signal NAME`` — one signal's values over time, found by name.

``decode`` needs the ECU and PID. This command looks the signal up by name in the active profile
and hands the one ECU, PID and signal it found to ``decode``, so the table, ``--plot``, ``--stats``,
``--json`` and the scope flags are ``decode``'s own. A name defined more than once is refused with
the candidates listed, unless ``--all`` shows every one or ``ECU:PID:NAME`` names one.
"""

from __future__ import annotations

import argparse

from canair.cli.args.scope import add_scope_args
from canair.cli.commands import decode
from canair.cli.profile_context import active
from canair.ecus.pids import build_ecu_index, load_pids
from canair.ecus.signal_lookup import (
    SignalRef,
    find_signals,
    parse_signal_ref,
    similar_signal_names,
)
from canair.render import messages

NAME = "signal"

EXAMPLES = """
  canair signal SOC_BMS                    # Every capture of SOC_BMS, in time order
  canair signal SOC_BMS --last-session     # Only the latest recorded session
  canair signal SOC_BMS --state driving -c # One state, unchanged rows collapsed
  canair signal BMS:SOC_BMS --stats        # Name the ECU when two ECUs define the name
  canair signal BMS:220101:SOC_BMS --plot  # Name the PID too
  canair signal SOC_BMS --all              # Show every definition of a repeated name
  canair signal SOC_BMS --json             # Per-capture values as JSON
"""


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Show one signal's values over time, found by name",
        description=(
            "Show one diagnostic signal's captured values over time. The signal is found "
            "by name, so you need not know its ECU or PID. A name defined more than once is "
            "listed so you can pick one. For broadcast-signal definitions see `signals`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES,
    )
    parser.add_argument(
        "name",
        metavar="NAME",
        help="Signal name (case-insensitive). Add the ECU (ECU:NAME) or the ECU and PID "
        "(ECU:PID:NAME) to pick one definition when the name is defined more than once",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="When the name is defined more than once, show every definition",
    )
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--stats", action="store_true", help="Descriptive statistics, not a table")
    view.add_argument("--plot", action="store_true", help="Open the interactive signal explorer")
    view.add_argument("--json", action="store_true", help="Output per-capture values as JSON")
    parser.add_argument(
        "--changes-only",
        "-c",
        action="store_true",
        help="Skip rows whose value is unchanged from the previous row (collapses stationary runs)",
    )
    parser.add_argument(
        "--first", type=int, metavar="N", help="Only the first N matching captures (chronological)"
    )
    parser.add_argument(
        "--last", type=int, metavar="N", help="Only the last N matching captures (chronological)"
    )
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def _decode_defaults() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    decode.add_arguments(parser)
    return parser.parse_args([])


def _decode_args(args, matches: list[SignalRef]) -> argparse.Namespace:
    """``decode``'s arguments for ``matches``: its defaults, then every flag both commands share."""
    out = _decode_defaults()
    for dest in vars(out):
        if hasattr(args, dest):
            setattr(out, dest, getattr(args, dest))
    out.query = sorted({f"{m.ecu}:{m.pid}" for m in matches})
    out.param = sorted({m.name for m in matches})
    out.compact = not (args.stats or args.plot)
    return out


def _ambiguity_message(name: str, matches: list[SignalRef]) -> str:
    width = max(len(m.selector) for m in matches)
    rows = [
        f"{m.selector.ljust(width)}  {m.unit or '-':<6} {'verified' if m.verified else 'unverified'}"
        for m in matches
    ]
    return f"{name} is defined {len(matches)} times. Name one, or pass --all:\n" + "\n".join(rows)


def run(args) -> int:
    try:
        ecu, pid, name = parse_signal_ref(args.name)
    except ValueError as e:
        messages.error(str(e))
        return 2
    if args.changes_only and (args.stats or args.plot):
        messages.error("--changes-only applies to the table view, not --stats or --plot")
        return 2

    index = build_ecu_index(load_pids(profile=active()))
    matches = find_signals(index, name, ecu=ecu, pid=pid)
    if not matches:
        messages.error(f"no signal named {args.name!r} in the active profile")
        if similar := similar_signal_names(index, name):
            messages.hint(f"did you mean: {', '.join(similar)}")
        return 2
    if len(matches) > 1 and not args.all:
        messages.error(_ambiguity_message(name, matches))
        return 2
    return decode.run(_decode_args(args, matches))
