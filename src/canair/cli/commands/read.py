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

from canair.cli.args.session import add_wake_arg
from canair.cli.live import (
    LiveOperation,
    add_connection_args,
    expand_step_groups,
    load_pids,
    param_completer,
    report_merged_selectors,
    run_live,
    step_completer,
    to_step,
)
from canair.cli.live.handlers import elm_only, multi, reads
from canair.cli.profile_context import active
from canair.ecus.ecu_groups import GroupError
from canair.live.monitor.controller import query_ecu_error
from canair.live.read.multi import parse_sub_commands
from canair.privacy.sensitive import set_reveal
from canair.render import messages

NAME = "read"
ALIASES = ["query"]


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Read ECUs/signals over the WiCAN terminal (multi-pipeline default)",
        description="Read ECUs/signals live. Positional STEPs use the multi mini-language.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair read BMS:2101                      Read BMS PID 2101
  canair read BMS:01/0D                     OBD-II mode 01 PID 0D (an undefined bare
                                            010D would read DID 0x010D)
  canair read "VCU:2101 BMS:2101"           Cross-ECU read
  canair read @charging                     Read a saved group (see `canair groups`)
  canair read @driving CLU:220B             A group plus an extra selector
  canair read "skm-wake acc" "query IGPM:BC03,BC06"
  canair read --param SOC_BMS SOC_DISP      Read named signals

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
        "--param", nargs="+", metavar="NAME", help="Read named signals instead of selectors"
    ).completer = param_completer
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    add_wake_arg(parser)
    parser.add_argument("--repl", action="store_true", help="Drop into REPL after the pipeline")
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
    parser.add_argument(
        "--reveal",
        action="store_true",
        help="Show sensitive PIDs and signals (`sensitive: true`, e.g. the CCM's EVCCID) "
        "in clear instead of *****. Display only: cannot be combined with --save, and "
        "a sensitive reply is never saved either way.",
    )
    parser.add_argument(
        "--include-static",
        action="store_true",
        help="Include static and slow PIDs (identity/calibration blocks and per-cycle "
        "records such as 21F2) in a bare-ECU sweep. By default `canair read ECU` omits "
        "PIDs with status: static or slow; naming one explicitly (ECU:21F2) always "
        "queries it.",
    )
    add_connection_args(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if args.reveal:
        if args.save:
            messages.error("--reveal cannot be combined with --save")
            return 2

        set_reveal(True)
    if args.json and not (args.steps or args.param):
        messages.error("--json needs a STEP or --param: the interactive REPL has no JSON output")
        return 2
    if args.json and args.repl:
        messages.error("--json cannot be combined with --repl")
        return 2
    operation = LiveOperation(elm_only.handle_interactive)
    if args.steps:
        # Expand any @group references into their member selectors before the
        # mini-language parser sees them (composes with ad-hoc selectors).
        try:
            args.steps = expand_step_groups(args.steps)
        except GroupError as e:
            messages.error(str(e))
            return 2
        args.multi = [to_step(s) for s in args.steps]
        # Validate the mini-language up front so ambiguous/malformed steps fail
        # loudly *before* we acquire the device lock and open a connection.
        try:
            commands = parse_sub_commands(args.multi, profile=active())
        except ValueError as e:
            messages.error(str(e))
            return 2

        # Reject typo'd/unknown ECU names in query steps before connecting, so a
        # mistake like "query ESC ECS" fails loudly instead of silently polling
        # only the valid ECU(s).
        query_steps = [c for c in commands if c["type"] == "query"]
        if query_steps:
            ecu_err = query_ecu_error(query_steps, load_pids(profile=active()), profile=active())
            if ecu_err:
                messages.error(ecu_err)
                return 2
            report_merged_selectors(query_steps)
        operation = LiveOperation(
            multi.handle_multi, records=any(c["type"] in ("query", "raw") for c in commands)
        )
    elif args.param:
        operation = LiveOperation(reads.handle_param)
    # else: no steps and no --param: the interactive REPL

    # On an interactive terminal, nudge toward the live monitor — `read` is a
    # one-shot read, while `canair monitor` gives a continuously-refreshing view
    # of the same steps. Skip when piped or emitting JSON so machine output stays
    # clean.
    if sys.stdout.isatty() and not args.json:
        messages.hint(
            "for a live, continuously-refreshing view use `canair monitor` (same query steps)."
        )
    return run_live(args, operation)
