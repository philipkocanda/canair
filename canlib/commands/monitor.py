"""``canair monitor`` — live, continuously-refreshing view of ECU parameters.

Promoted from the former ``canair query --monitor`` flag into its own top-level
command. Positional STEPs use the same multi mini-language as ``canair read``;
a bare selector (no leading verb) is treated as a ``query`` step, so
``canair monitor BMS:2101`` and
``canair monitor "session IGPM --wake" "query IGPM"`` both work.

On a terminal this opens the scrollable Textual monitor (the polling / decoding /
capture-saving logic lives in :mod:`canlib.modes.monitor`); piped/non-interactive
it polls silently until Ctrl+C. Recording (``--save`` + metadata) and keep-modes
apply here, not to the one-shot ``canair read``.
"""

from __future__ import annotations

import argparse
import sys

from canlib.commands._live import (
    add_connection_args,
    expand_step_groups,
    finalize_live_parser,
    run_live,
    step_completer,
    to_step,
)

NAME = "monitor"
ALIASES = ["mon"]


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Live, continuously-refreshing view of ECU parameters (scrollable TUI)",
        description="Monitor ECUs/parameters live in a scrollable, refreshing view. "
        "Positional STEPs use the multi mini-language (same as `canair read`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair monitor BMS:2101                   Monitor BMS PID 2101 (default 5s interval)
  canair monitor BMS:2101 --interval 2      Refresh every 2s
  canair monitor "VCU:2101 BMS:2101"        Cross-ECU monitor
  canair monitor @charging                  Monitor a saved group (see `canair groups`)
  canair monitor @driving CLU:220B          A group plus an extra selector
  canair monitor "skm-wake acc" "query IGPM:BC03,BC06"
  canair monitor BMS:2101 --save --label "…" --state "READY, PARKED"  Record while monitoring

In the TUI: mouse wheel / scrollbar / arrows-jk / PgUp-PgDn / g-G scroll,
f toggles follow-tail, space pauses, =/- change the poll interval live,
r toggles byte-index rulers, l opens the errors/diagnostics log,
V cycles the view mode (ecus / ranges / signals / full),
i opens the session-info overlay (segment history + summary),
↑/↓ select a parameter (esc deselects), e/v/d/F edit/verify/en-disable/filter,
s labels the recording (or saves now when not using --save),
n finishes the current --save session and starts a fresh one,
? shows all shortcuts, q quits.

For a single one-shot read (no live refresh) use `canair read` instead.
""",
    )
    parser.add_argument(
        "steps",
        nargs="*",
        metavar="STEP",
        help="Query selector(s), @group(s), or multi mini-language step(s)",
    ).completer = step_completer
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="Poll interval in seconds (default 5.0; change it live in the TUI with =/-)",
    )
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    keep = parser.add_mutually_exclusive_group()
    keep.add_argument(
        "--keep-changes",
        action="store_true",
        help="Retain value-transitions per PID (run-length; collapses only "
        "immediate repeats) — the default",
    )
    keep.add_argument(
        "--keep-unique",
        action="store_true",
        help="Retain only globally-distinct payloads per PID (legacy global dedup; "
        "return-to-previous transitions are dropped)",
    )
    keep.add_argument(
        "--keep-all",
        action="store_true",
        help="Retain every polled payload (full time-series; larger capture files)",
    )
    keep.add_argument("--keep", type=int, metavar="N", help="Keep the last N payloads per PID")
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument("--label", metavar="TEXT", default=None, help="Session label for --save")
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    parser.add_argument(
        "--rulers", action="store_true", help="Show byte-index rulers above the hex"
    )
    parser.add_argument(
        "--include-static",
        action="store_true",
        help="Include static config/identity PIDs (e.g. 21F2) in a bare-ECU sweep. "
        "By default `canair monitor ECU` omits PIDs flagged static:true; naming one "
        "explicitly (ECU:21F2) always polls it.",
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if not args.steps:
        print(
            "Error: monitor needs at least one query step, e.g. `canair monitor BMS:2101`",
            file=sys.stderr,
        )
        return 2

    # Expand any @group references into their member selectors before the
    # mini-language parser sees them (composes with ad-hoc selectors).
    from canlib.ecu_groups import GroupError

    try:
        args.steps = expand_step_groups(args.steps)
    except GroupError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    args.multi = [to_step(s) for s in args.steps]
    # dispatch_mode routes to the monitor when both args.multi and args.monitor
    # are set; the interval flag is the poll period.
    args.monitor = args.interval

    from canlib.modes.multi import parse_sub_commands

    # Validate the mini-language up front so ambiguous/malformed steps fail
    # loudly *before* we acquire the device lock and open a connection.
    try:
        commands = parse_sub_commands(args.multi)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    query_steps = [c for c in commands if c["type"] == "query"]
    if not query_steps:
        print("Error: monitor requires at least one 'query' step", file=sys.stderr)
        return 2

    # Reject typo'd/unknown ECU names before connecting (mirrors `canair read`).
    from canlib.commands._live import load_pids
    from canlib.modes.monitor import query_ecu_error

    ecu_err = query_ecu_error(query_steps, load_pids())
    if ecu_err:
        print(f"Error: {ecu_err}", file=sys.stderr)
        return 2

    return run_live(args)
