"""``canair monitor`` — live, continuously-refreshing view of ECU signals.

Promoted from the former ``canair query --monitor`` flag into its own top-level
command. Positional STEPs use the same multi mini-language as ``canair read``;
a bare selector (no leading verb) is treated as a ``query`` step, so
``canair monitor BMS:2101`` and
``canair monitor "session IGPM --wake" "query IGPM"`` both work. With no STEP at
all, a terminal gets an interactive picker (groups/ECUs, then PIDs —
:mod:`canair.live.monitor.select`) whose result becomes the STEPs.

On a terminal this opens the scrollable Textual monitor (the polling / decoding /
capture-saving logic lives in :mod:`canair.live.monitor.controller`); piped/non-interactive
it polls silently until Ctrl+C. Recording (``--save`` + metadata) and keep-modes
apply here, not to the one-shot ``canair read``.
"""

from __future__ import annotations

import argparse
import math
import shlex
import sys

from canair.cli.args.notation import add_notation_arg
from canair.cli.live import (
    add_connection_args,
    expand_step_groups,
    report_merged_selectors,
    run_live,
    step_completer,
    to_step,
)
from canair.cli.live.handlers.multi import monitor_operation, monitor_options
from canair.live.monitor.schedule import DEFAULT_SCHEDULE, MIN_INTERVAL, SCHEDULES
from canair.profile import active
from canair.render import messages

NAME = "monitor"
ALIASES = ["mon"]


def _interval(value: str) -> float:
    """``--interval``: seconds, no shorter than the TUI's own =/- floor.

    Zero or negative would make every PID without a profile period due on every tick
    (the monitor never pauses between polls).
    """
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a number of seconds, got {value!r}") from None
    if not math.isfinite(seconds) or seconds < MIN_INTERVAL:
        raise argparse.ArgumentTypeError(f"must be at least {MIN_INTERVAL} seconds, got {value}")
    return seconds


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
  canair monitor BMS:2101                   Monitor BMS PID 2101 at twice its profile rate
  canair monitor BMS:2101 --interval 2      PIDs without a profile period refresh every 2s
  canair monitor BMS:2101 --schedule fixed --interval 1   Every PID every 1s
  canair monitor "VCU:2101 BMS:2101"        Cross-ECU monitor
  canair monitor @charging                  Monitor a saved group (see `canair groups`)
  canair monitor @driving CLU:220B          A group plus an extra selector
  canair monitor "skm-wake acc" "query IGPM:BC03,BC06"
  canair monitor BMS:2101 --save --label "…" --state "READY, PARKED"  Record while monitoring

Polling: by default each PID is read every half of its own `period:` from the profile
(the period is the WiCAN AutoPID cadence; set one with `canair pids set-pid-period`), and a
PID without one every --interval seconds. --schedule fixed reads every PID on every cycle,
--interval apart.

A PID that stops answering or is answered very slowly holds up every other PID's next
poll, so it is polled 2x, 4x, then up to 32x less often until it answers promptly again
(its ECU's other PIDs keep their rate); its row and the status line say so. --no-backoff
keeps polling it at the normal rate.

In the TUI: mouse wheel / scrollbar / arrows-jk / PgUp-PgDn / g-G scroll,
space pauses, =/- poll faster/slower (the interval on the fixed schedule, every
PID's rate by 1.5x on the per-PID one), m switches schedule,
r toggles the byte-index ruler, E opens this session's errors/diagnostics log,
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
        type=_interval,
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
    parser.add_argument(
        "--no-backoff",
        action="store_true",
        help="Keep polling a PID that times out or is answered slowly at its normal rate "
        "(default: poll it less often until it recovers, so it can't hold up the rest)",
    )
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    keep = parser.add_mutually_exclusive_group()
    keep.add_argument(
        "--keep-all",
        action="store_true",
        help="Record every polled payload (full time-series) — the default. Repeated "
        "payloads are folded on disk, so the capture file stays compact",
    )
    keep.add_argument(
        "--keep-changes",
        action="store_true",
        help="Record only value-transitions per PID (run-length; drops immediate "
        "repeats, so the polling cadence is not recorded)",
    )
    keep.add_argument(
        "--keep",
        type=int,
        metavar="N",
        help="Show only the last N history rows per PID in the live view",
    )
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
    add_connection_args(parser, json=False)
    parser.set_defaults(func=run)
    return parser


def _pick_steps(args) -> list[str] | None:
    """Bare ``canair monitor`` on a terminal: let the user pick ECUs, then PIDs.

    Returns the chosen selectors, or ``None`` when the picker was cancelled.
    Raises ``ValueError`` if there is nothing in the active profile to pick from.
    """
    from canair.cli.live import load_pids
    from canair.tui.apps.monitor_select.app import pick_selectors

    return pick_selectors(
        load_pids(profile=active()), include_static=args.include_static, profile=active()
    )


def run(args) -> int:
    if not args.steps:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            messages.error(
                "monitor needs at least one query step, e.g. `canair monitor BMS:2101` "
                "(run it on a terminal without steps for an interactive picker)"
            )
            return 2
        try:
            picked = _pick_steps(args)
        except ValueError as e:
            messages.error(str(e))
            return 2
        if not picked:
            print("Cancelled — nothing selected.", file=sys.stderr)
            return 1
        args.steps = picked
        print(f"Monitoring: canair monitor {shlex.join(picked)}", file=sys.stderr)

    # Expand any @group references into their member selectors before the
    # mini-language parser sees them (composes with ad-hoc selectors).
    from canair.ecus.ecu_groups import GroupError

    try:
        args.steps = expand_step_groups(args.steps)
    except GroupError as e:
        messages.error(str(e))
        return 2

    args.multi = [to_step(s) for s in args.steps]

    from canair.live.read.multi import parse_sub_commands

    # Validate the mini-language up front so ambiguous/malformed steps fail
    # loudly *before* we acquire the device lock and open a connection.
    try:
        commands = parse_sub_commands(args.multi, profile=active())
    except ValueError as e:
        messages.error(str(e))
        return 2

    query_steps = [c for c in commands if c["type"] == "query"]
    if not query_steps:
        messages.error("monitor requires at least one 'query' step")
        return 2
    report_merged_selectors(query_steps)

    # Reject typo'd/unknown ECU names before connecting (mirrors `canair read`).
    from canair.cli.live import load_pids
    from canair.live.monitor.controller import query_ecu_error

    ecu_err = query_ecu_error(query_steps, load_pids(profile=active()), profile=active())
    if ecu_err:
        messages.error(ecu_err)
        return 2

    return run_live(args, monitor_operation(monitor_options(args)))
