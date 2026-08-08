"""``canair read`` — the primary live read command (multi-pipeline default).

Named for the UDS ReadDataByIdentifier service (0x22) it fronts. Absorbs the old
``--multi``/``--param``/``--ecu`` modes. Positional arguments are steps in the
multi mini-language; a bare selector (no leading verb) is treated as a ``query``
step (the mini-language verb keeps its name), so ``canair read BMS:2101`` and
``canair read "session IGPM --wake" "query IGPM"`` both work. ``canair query``
remains as an alias.

The live *monitor* (a scrollable, continuously-refreshing TUI) is its own
top-level command — see ``canair monitor``.
"""

from __future__ import annotations

import argparse
import sys

from canlib.commands._live import (
    add_connection_args,
    expand_step_groups,
    finalize_live_parser,
    param_completer,
    report_merged_selectors,
    run_live,
    step_completer,
    to_step,
)

NAME = "read"
ALIASES = ["query"]


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Read ECUs/parameters over the WiCAN terminal (multi-pipeline default)",
        description="Read ECUs/parameters live. Positional STEPs use the multi mini-language.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair read BMS:2101                      Read BMS PID 2101
  canair read "VCU:2101 BMS:2101"           Cross-ECU read
  canair read @charging                     Read a saved group (see `canair groups`)
  canair read @driving CLU:220B             A group plus an extra selector
  canair read "skm-wake acc" "query IGPM:BC03,BC06"
  canair read --param SOC_BMS SOC_DISP      Read named parameters

`canair query` is a kept alias. For a live, continuously-refreshing view use
`canair monitor` instead.
""",
    )
    parser.add_argument(
        "steps",
        nargs="*",
        metavar="STEP",
        help="Read selector(s), @group(s), or multi mini-language step(s)",
    ).completer = step_completer
    parser.add_argument(
        "--param", nargs="+", metavar="NAME", help="Read named parameters instead of selectors"
    ).completer = param_completer
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    parser.add_argument("--repl", action="store_true", help="Drop into REPL after the pipeline")
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument("--label", metavar="TEXT", default=None, help="Session label for --save")
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    parser.add_argument(
        "--include-static",
        action="store_true",
        help="Include static config/identity PIDs (e.g. 21F2) in a bare-ECU sweep. "
        "By default `canair read ECU` omits PIDs flagged static:true; naming one "
        "explicitly (ECU:21F2) always queries it.",
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if args.steps:
        # Expand any @group references into their member selectors before the
        # mini-language parser sees them (composes with ad-hoc selectors).
        from canlib.ecu_groups import GroupError

        try:
            args.steps = expand_step_groups(args.steps)
        except GroupError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        args.multi = [to_step(s) for s in args.steps]
        # Validate the mini-language up front so ambiguous/malformed steps fail
        # loudly *before* we acquire the device lock and open a connection.
        from canlib.modes.multi import parse_sub_commands

        try:
            commands = parse_sub_commands(args.multi)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2

        # Reject typo'd/unknown ECU names in query steps before connecting, so a
        # mistake like "query ESC ECS" fails loudly instead of silently polling
        # only the valid ECU(s).
        query_steps = [c for c in commands if c["type"] == "query"]
        if query_steps:
            from canlib.commands._live import load_pids
            from canlib.modes.monitor import query_ecu_error

            ecu_err = query_ecu_error(query_steps, load_pids())
            if ecu_err:
                print(f"Error: {ecu_err}", file=sys.stderr)
                return 2
            report_merged_selectors(query_steps)
    # else: --param / interactive fall through to async_main's dispatch

    # On an interactive terminal, nudge toward the live monitor — `read` is a
    # one-shot read, while `canair monitor` gives a continuously-refreshing view
    # of the same steps. Skip when piped or emitting JSON so machine output stays
    # clean.
    if sys.stdout.isatty() and not args.json:
        print(
            "hint: for a live, continuously-refreshing view use `canair monitor` "
            "(same query steps).",
            file=sys.stderr,
        )
    return run_live(args)
