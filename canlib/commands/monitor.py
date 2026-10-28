"""``canair monitor`` — live, continuously-refreshing view of ECU signals.

Promoted from the former ``canair query --monitor`` flag into its own top-level
command. Positional STEPs use the same multi mini-language as ``canair read``;
a bare selector (no leading verb) is treated as a ``query`` step, so
``canair monitor BMS:2101`` and
``canair monitor "session IGPM --wake" "query IGPM"`` both work. With no STEP at
all, a terminal gets an interactive picker (groups/ECUs, then PIDs —
:mod:`canlib.modes.monitor_select`) whose result becomes the STEPs.

On a terminal this opens the scrollable Textual monitor (the polling / decoding /
capture-saving logic lives in :mod:`canlib.modes.monitor`); piped/non-interactive
it polls silently until Ctrl+C. Recording (``--save`` + metadata) and keep-modes
apply here, not to the one-shot ``canair read``.
"""

from __future__ import annotations

import argparse
import shlex
import sys

from canlib.commands._live import (
    add_connection_args,
    expand_step_groups,
    finalize_live_parser,
    report_merged_selectors,
    run_live,
    step_completer,
    to_step,
)
from canlib.poll_schedule import DEFAULT_SCHEDULE, SCHEDULES
from canlib.uds.notation import add_notation_arg

NAME = "monitor"
ALIASES = ["mon"]


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Live, continuously-refreshing view of ECU signals (scrollable TUI)",
        description="Monitor ECUs/signals live in a scrollable, refreshing view. "
        "Positional STEPs use the multi mini-language (same as `canair read`). "
        "With no STEP on a terminal, an interactive picker offers saved groups and ECUs, "
        "then PIDs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair monitor                            Pick ECUs, then PIDs, interactively (TTY only)
  canair monitor BMS:2101                   Monitor BMS PID 2101 at its profile period
  canair monitor BMS:2101 --interval 2      PIDs without a profile period refresh every 2s
  canair monitor BMS:2101 --schedule fixed --interval 1   Every PID every 1s
  canair monitor "VCU:2101 BMS:2101"        Cross-ECU monitor
  canair monitor @charging                  Monitor a saved group (see `canair groups`)
  canair monitor @driving CLU:220B          A group plus an extra selector
  canair monitor "skm-wake acc" "query IGPM:BC03,BC06"
  canair monitor BMS:2101 --save --label "…" --state "READY, PARKED"  Record while monitoring

Polling: by default each PID is read at its own `period:` from the profile (the same
milliseconds the WiCAN AutoPID profile uses; set one with `canair pids set-pid-period`),
and a PID without one every --interval seconds. --schedule fixed reads every PID on every
cycle, --interval apart.

In the TUI: mouse wheel / scrollbar / arrows-jk / PgUp-PgDn / g-G scroll,
space pauses, =/- poll faster/slower (the interval on the fixed schedule, a
multiplier on every PID's period on the per-PID one), m switches schedule,
r toggles the byte-index ruler, E opens the errors/diagnostics log,
V cycles the view mode (ecus / ranges / signals / full),
i opens the session-info overlay (segment history + summary),
↑/↓ select a signal (esc deselects), e/v/x/F edit/verify/en-disable/filter,
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
        help="Poll interval in seconds for PIDs with no profile `period:` — or for every "
        "PID with --schedule fixed (default 5.0; scale it live in the TUI with =/-)",
    )
    parser.add_argument(
        "--schedule",
        choices=SCHEDULES,
        default=DEFAULT_SCHEDULE,
        help="dynamic (default): poll each PID at its own profile `period:`, falling back "
        "to --interval; fixed: poll every PID every cycle, --interval apart",
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
        "--rulers",
        action="store_true",
        help="Show a byte-index ruler above the hex (in the notation from "
        "--notation / display.byte_notation)",
    )
    add_notation_arg(parser)
    parser.add_argument(
        "--include-static",
        action="store_true",
        help="Include static and slow PIDs (identity/calibration blocks and per-cycle "
        "records such as 21F2) in a bare-ECU sweep. By default `canair monitor ECU` omits "
        "PIDs with status: static or slow; naming one explicitly (ECU:21F2) always "
        "polls it.",
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    parser.set_defaults(func=run)
    return parser


def _pick_steps(args) -> list[str] | None:
    """Bare ``canair monitor`` on a terminal: let the user pick ECUs, then PIDs.

    Returns the chosen selectors, or ``None`` when the picker was cancelled.
    Raises ``ValueError`` if there is nothing in the active profile to pick from.
    """
    from canlib.commands._live import load_pids
    from canlib.modes.monitor_select import pick_selectors

    return pick_selectors(load_pids(), include_static=args.include_static)


def run(args) -> int:
    if not args.steps:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(
                "Error: monitor needs at least one query step, e.g. `canair monitor BMS:2101` "
                "(run it on a terminal without steps for an interactive picker)",
                file=sys.stderr,
            )
            return 2
        try:
            picked = _pick_steps(args)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        if not picked:
            print("Cancelled — nothing selected.")
            return 0
        args.steps = picked
        print(f"Monitoring: canair monitor {shlex.join(picked)}")

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
    report_merged_selectors(query_steps)

    # Reject typo'd/unknown ECU names before connecting (mirrors `canair read`).
    from canlib.commands._live import load_pids
    from canlib.modes.monitor import query_ecu_error

    ecu_err = query_ecu_error(query_steps, load_pids())
    if ecu_err:
        print(f"Error: {ecu_err}", file=sys.stderr)
        return 2

    return run_live(args)
