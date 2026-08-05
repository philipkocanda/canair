#!/usr/bin/env python3
"""Query captured UDS payloads across all capture files.

QUERY selects the ECU(s) and PID(s) to show (see the mini-language below).
By default the matching captures are listed (most recent --limit, default 50);
add --diff or --step to change how they are rendered. --summary and --sessions
are aggregate modes that take no QUERY.

  QUERY                 List matching captures (default view; latest --limit)
  QUERY --diff          Monitor-style view (decoded params + colored byte-diff),
                        one block per ECU+PID (unique payloads only; --all = all)
  QUERY --step          Interactive: step through captures with arrow keys,
                        decoded params + byte-diff vs the previous capture of the
                        same PID; e adds/edits a note, d deletes a capture.
                        A QUERY selecting SEVERAL PIDs stacks them underneath
                        each other in one time-joined frame (--join-tol), so
                        they can be cross-compared; PIDs, tolerance and view are
                        all editable inside the TUI (a/t/V), s jumps between
                        sessions and noted captures, ? for help
  QUERY --latest        Most recent payload per PID for the QUERY selection
  --latest              Most recent payload per PID (all ECUs; no QUERY)
  QUERY --delete        Delete the captures matching QUERY (and scope filters);
                        --dry-run previews, confirms before deleting unless --yes
  --summary             Overview: captures per ECU, per date, total payloads
  --sessions            Session table of contents: date/time-span/state/label/
                        notes/ECUs per session (no payloads); --json for machine
                        output. Honors the scope filters.

Step views (--view; default auto — stacked for up to 6 PIDs, else interleaved):
  stacked               One block per PID per frame: params + byte-diff hex
  signals               Params only (no hex) — fits more PIDs on one screen
  changed               Only params whose decoded value moved, per block
  interleaved           One capture per frame, chronologically across the PIDs

Output size (default list view):
  --limit N             Show only the most recent N captures (default 50; 0 =
                        no cap). A loud footer reports any hidden history — use
                        --limit 0 or a tighter scope (--since/--last-session) to
                        see the rest.

QUERY mini-language (see canlib/query.py):
  ECU PID               one PID (bare ECU + PID)       e.g. BMS 2102
  ECU                   all PIDs for an ECU            e.g. VCU
  ECU:PID               one PID                        e.g. VCU:2101
  ECU:PID,PID           several PIDs                   e.g. VCU:2101,22BC03
  "ECU:PID ECU:PID"     cross-ECU (quote the space)    e.g. "VCU:2101 BMS:2101"
  ECU:22                prefix PID match (22xxxx)      e.g. BCM:22
  ECU:BC03              suffix PID match (->22BC03)    e.g. IGPM:BC03

Date scoping (inclusive, YYYY-MM-DD; combines with any mode):
  --since DATE          captures on or after DATE
  --until DATE          captures on or before DATE
  --date DATE           captures on DATE only (--since DATE --until DATE)

State/label scoping (case-insensitive substring; combines with any mode):
  --state SUBSTR        only sessions whose vehicle_states contain SUBSTR (e.g. driving)
  --label SUBSTR        only sessions/captures whose label contains SUBSTR

Examples (a bare `canair captures …` is shorthand for `canair captures uds …`):
  canair captures uds BMS 2102              # ECU + PID (most useful)
  canair captures uds BMS                   # All BMS captures
  canair captures uds "BMS:2102,2103"       # Several PIDs
  canair captures uds IGPM 22BC03 --diff    # Byte-diff for one ECU+PID
  canair captures uds "BMS:2102,2103" --diff  # Byte-diff, one block per PID
  canair captures uds BMS 2102 --step       # Step through one PID
  canair captures uds "BMS:2102,2103" --step  # Stack two PIDs, time-joined
  canair captures uds "HVAC:220100,2201A0,2201A2" --step  # Cross-compare three PIDs
  canair captures uds "VCU:2101 BMS:2101" --step  # Cross-ECU compare
  canair captures uds "VCU:2101 BMS:2101" --step --join-tol 1.0  # Tighter join
  canair captures uds "HVAC:220100,2201A0" --step --view signals # Params only
  canair captures uds BMS --step --view interleaved  # Browse every BMS PID
  canair captures uds "BMS:2102,2103" --step --json --limit 5  # Frames as data
  canair captures uds --diff VCU:2101 --all  # One PID, every payload
  canair captures uds --summary             # Overview stats
  canair captures uds --sessions            # Session table of contents
  canair captures uds --sessions --state driving # Index of every drive
  canair captures uds --sessions --json      # Machine-readable TOC
  canair captures uds BMS --latest          # Latest payload per BMS PID
  canair captures uds --latest              # Latest payload per PID (all ECUs)
  canair captures uds OBC 2101 --delete --dry-run  # Preview a delete
  canair captures uds OBC 2101 --delete --yes      # Delete (non-interactive)
  canair captures uds BMS 2102 --limit 200  # Widen the default 50-row cap
  canair captures uds BMS 2102 --limit 0    # Every matching capture (no cap)
  canair captures uds --summary --since 2026-04-19        # Stats since a date
  canair captures uds BMS 2101 --diff --date 2026-04-19   # One day only
  canair captures uds VCU --since 2026-04-14 --until 2026-04-21  # Range
  canair captures can                       # List imported raw broadcast-CAN frame logs
"""

import argparse
import sys
from pathlib import Path

from canlib.capture_dates import (
    add_scope_args,
    filter_by_date_range,
    filter_by_text,
    resolve_scope_bounds,
)
from canlib.capture_store import load_all_captures
from canlib.capture_types import CaptureEntry
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.state_infer import DEFAULT_CYCLE_TOL_S

from .backfill import cmd_backfill_states
from .delete import cmd_delete
from .diff import cmd_diff
from .listing import cmd_latest, cmd_list
from .maint import cmd_recover
from .mode_select import Mode, ModeError, resolve_mode
from .query import _DIM, _RESET, _parse_query, build_query
from .sessions import cmd_sessions, cmd_summary
from .set_state import cmd_set_state
from .step import cmd_step
from .step_model import (
    AUTO_STACK_MAX_KEYS,
    DEFAULT_STEP_JOIN_TOL_S,
    VIEW_AUTO,
    VIEW_CHOICES,
)


def _add_uds_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "uds",
        help="Query captured diagnostic UDS payloads across all capture files",
        description="Query captured UDS payloads.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="ECU/PID selection: 'BMS 2102', 'BMS:2102,2103', 'BMS' (all PIDs), "
        "or a quoted cross-ECU query 'VCU:2101 BMS:2101'",
    ).completer = _ecu_completer

    # View modifiers for a QUERY (default is the list view).
    view = parser.add_mutually_exclusive_group()
    view.add_argument(
        "--diff",
        "-d",
        action="store_true",
        help="Monitor-style view (decoded params + colored byte-diff), one block per ECU+PID",
    )
    view.add_argument(
        "--step",
        "-S",
        action="store_true",
        help="Interactively step through matching captures (arrow keys; a several-PID "
        "QUERY stacks them time-joined for cross-comparison; e=note, d=delete, ?=help)",
    )

    # Standalone modes that take no QUERY.
    standalone = parser.add_mutually_exclusive_group()
    standalone.add_argument("--summary", "-s", action="store_true", help="Overview statistics")
    standalone.add_argument(
        "--sessions",
        "-n",
        action="store_true",
        help="List sessions with their metadata (date/state/label/notes/ECUs) — a "
        "searchable table of contents; no payloads. Honors the scope filters.",
    )
    standalone.add_argument(
        "--latest",
        "-l",
        action="store_true",
        help="Latest payload per PID (ECU/PID taken from the QUERY, e.g. `BMS --latest`)",
    )
    standalone.add_argument(
        "--recover",
        action="store_true",
        help="Reconcile orphaned capture journals (from a killed/crashed session) "
        "into capture files. Add --discard to delete them without saving.",
    )
    standalone.add_argument(
        "--delete",
        action="store_true",
        help="Delete the captures matching QUERY (and any scope filters). "
        "Previews with --dry-run; confirms before deleting unless --yes.",
    )
    standalone.add_argument(
        "--backfill-states",
        action="store_true",
        dest="backfill_states",
        help="Infer each session's vehicle_states from its decoded captures and "
        "fill sessions that have none. Reports conflicts (never writes them "
        "unless --overwrite). Previews with --dry-run; confirms unless --yes. "
        "Honors the scope filters.",
    )
    standalone.add_argument(
        "--set-state",
        metavar="STATES",
        default=None,
        dest="set_state",
        help="Manually set vehicle_states (comma-separated) on the scope-selected "
        "sessions — for a state known from the label but not inferable from the "
        "data (e.g. --set-state ACC --label 'ACC only'). Requires a scope filter "
        "(--label/--date/--since/…); previews with --dry-run, confirms unless --yes.",
    )

    parser.add_argument(
        "--discard",
        action="store_true",
        help="With --recover: delete orphaned journals without saving them",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="With --backfill-states: also rewrite sessions whose recorded states "
        "conflict with / differ from the inferred states (default: fill empty only)",
    )

    parser.add_argument(
        "--cycle-tol",
        type=float,
        default=DEFAULT_CYCLE_TOL_S,
        metavar="SECONDS",
        help="With --backfill-states: max timestamp gap grouping captures into one "
        f"pseudo-cycle for cross-ECU predicates (default {DEFAULT_CYCLE_TOL_S:g}s)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --delete/--backfill-states/--set-state: preview the changes, write nothing",
    )

    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="With --delete/--backfill-states/--set-state: skip the confirmation prompt (scripting)",
    )

    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="For --diff/--step: use every payload instead of unique-only",
    )

    parser.add_argument(
        "--limit",
        "-L",
        type=int,
        default=50,
        metavar="N",
        help="Default list view: show only the most recent N captures (default 50; "
        "0 = no cap). A loud footer reports any hidden history. Also caps the "
        "frames rendered by a piped/--json --step.",
    )

    parser.add_argument(
        "--rulers",
        "-r",
        action="store_true",
        help="For --diff/--step: show the byte-index ruler (idx/wican) above the hex",
    )

    parser.add_argument(
        "--view",
        choices=VIEW_CHOICES,
        default=VIEW_AUTO,
        help="For --step: how a frame is rendered — stacked (one block per PID), "
        "signals (params only), changed (only params that moved), interleaved "
        "(one capture per frame). Default auto: stacked for up to "
        f"{AUTO_STACK_MAX_KEYS} PIDs, else interleaved. Cycle it live with V.",
    )

    parser.add_argument(
        "--join-tol",
        type=float,
        default=DEFAULT_STEP_JOIN_TOL_S,
        metavar="SECONDS",
        help=f"For --step: max timestamp difference when joining captures of "
        f"different PIDs into one stacked frame (default {DEFAULT_STEP_JOIN_TOL_S:g}s, "
        f"sized for a full round-robin monitor cycle; adjustable live with t / < / >)",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output (summary/sessions/latest/diff/step and "
        "the default QUERY list)",
    )

    add_scope_args(parser)

    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Captures directory (default: active profile)",
    )

    parser.set_defaults(func=run)
    return parser


def _scope(args, since, until) -> list[CaptureEntry] | int:
    """Load the captures and apply the date/state/label scope, or an exit code.

    Returns the surviving entries, or the exit code to return when the scope
    selected nothing (an empty scope is reported once here rather than by each
    mode). ``--json`` callers get ``[]`` instead of a human banner.
    """
    entries = load_all_captures(args.dir)
    if not entries:
        if args.json:
            print("[]")
            return 0
        print("  No capture files found.")
        return 1

    if since or until:
        entries = filter_by_date_range(entries, since, until)
        lo = since.isoformat() if since else "earliest"
        hi = until.isoformat() if until else "latest"
        if not entries:
            if args.json:
                print("[]")
                return 0
            print(f"  No captures in date range {lo} .. {hi}.")
            return 1
        # Keep JSON output clean (no human banner) when scoping --sessions --json.
        if not args.json:
            print(f"  {_DIM}Date range: {lo} .. {hi}  ({len(entries)} entries){_RESET}")

    if args.state or args.label:
        entries = filter_by_text(entries, state=args.state, label=args.label)
        if not entries:
            if args.json:
                print("[]")
                return 0
            crit = ", ".join(
                x
                for x in [
                    f"state~'{args.state}'" if args.state else "",
                    f"label~'{args.label}'" if args.label else "",
                ]
                if x
            )
            print(f"  No captures matching {crit}.")
            return 1

    return entries


def _dispatch(mode: Mode, args, query: str, entries: list[CaptureEntry]) -> int:
    """Hand the scoped entries to the resolved mode's handler."""
    if mode == "summary":
        cmd_summary(entries, as_json=args.json)
    elif mode == "sessions":
        cmd_sessions(entries, as_json=args.json)
    elif mode == "backfill_states":
        return cmd_backfill_states(
            entries,
            captures_dir=args.dir,
            overwrite=args.overwrite,
            cycle_tol=args.cycle_tol,
            dry_run=args.dry_run,
            assume_yes=args.yes,
            as_json=args.json,
        )
    elif mode == "set_state":
        return cmd_set_state(
            entries,
            args.set_state,
            captures_dir=args.dir,
            dry_run=args.dry_run,
            assume_yes=args.yes,
            as_json=args.json,
        )
    elif mode == "delete":
        return cmd_delete(
            entries,
            query,
            captures_dir=args.dir,
            dry_run=args.dry_run,
            assume_yes=args.yes,
            as_json=args.json,
        )
    elif mode == "latest":
        # ECU/PID selection comes from the QUERY (e.g. `BMS --latest`,
        # `BMS:2102 --latest`); a bare `--latest` shows every PID's latest.
        if query:
            q = _parse_query(query)
            entries, _empty = q.filter(
                entries, ecu_of=lambda e: e["ecu"], pid_of=lambda e: str(e["pid"])
            )
        cmd_latest(entries, as_json=args.json)
    elif mode == "diff":
        cmd_diff(entries, query, show_all=args.all, rulers=args.rulers, as_json=args.json)
    elif mode == "step":
        cmd_step(
            entries,
            query,
            show_all=args.all,
            captures_dir=args.dir,
            rulers=args.rulers,
            view=args.view,
            tol_s=args.join_tol,
            as_json=args.json,
            limit=args.limit,
        )
    else:
        cmd_list(entries, query, as_json=args.json, limit=args.limit)
    return 0


def run(args) -> int:
    from canlib.query import QueryError

    query = build_query(args.query)
    mode = resolve_mode(args, query)
    if isinstance(mode, ModeError):
        return mode.report()

    # --recover reconciles orphaned journals; it reads no captures, so it runs
    # before the store is loaded and ignores every scope flag.
    if mode == "recover":
        return cmd_recover(args.dir, discard=args.discard)

    # Resolve date scoping (--date is shorthand for an equal since/until pair).
    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    scoped = _scope(args, since, until)
    if isinstance(scoped, int):
        return scoped

    try:
        return _dispatch(mode, args, query, scoped)
    except QueryError as ex:
        print(f"error: invalid query: {ex}", file=sys.stderr)
        return 2
