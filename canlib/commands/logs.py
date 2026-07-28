"""``canair logs`` — view the central rotating diagnostics event log.

canair records transport-level faults (dropped/stale ISO-TP frames, timeouts,
bus errors, decode failures) and unexpected internal errors to a single collated,
size-rotated log so they can be inspected after the fact — the "why did that
capture look off?" trail. The file rotates automatically (a small ``maxBytes``
with a few backups), so it never grows unbounded and needs no manual cleanup.

Read-only by default; ``--clear`` deletes the log and its rotated backups.
"""

from __future__ import annotations

import argparse

NAME = "logs"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="View the central event log (internal transport errors/drops)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair logs                 # last 50 event-log lines
  canair logs -n 200          # last 200 lines
  canair logs --path          # print the log file path
  canair logs --json          # machine-readable (parsed fields)
  canair logs --clear         # delete the log + rotated backups
""",
    )
    parser.add_argument(
        "-n", "--lines", type=int, default=50, help="Number of recent lines to show (default: 50)"
    )
    parser.add_argument("--path", action="store_true", help="Print the log file path and exit")
    parser.add_argument(
        "--clear", action="store_true", help="Delete the event log and its rotated backups"
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Skip the --clear confirmation")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    import json

    from ..log import clear_event_log, event_log_path, parse_event_line, read_event_log

    path = event_log_path()

    if args.path:
        if args.json:
            print(json.dumps({"path": str(path)}))
        else:
            print(path)
        return 0

    if args.clear:
        if not args.yes:
            try:
                reply = input(f"  Delete event log at {path}? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Cancelled.")
                return 1
            if reply not in ("y", "yes"):
                print("  Cancelled.")
                return 1
        removed = clear_event_log()
        print(f"  Event log cleared ({removed} file(s) removed).")
        return 0

    limit = None if args.lines is not None and args.lines <= 0 else args.lines
    lines = read_event_log(lines=limit)
    if args.json:
        print(json.dumps({"path": str(path), "events": [parse_event_line(ln) for ln in lines]}))
        return 0
    if not lines:
        print("  No events logged yet.")
        return 0
    for line in lines:
        print(line)
    return 0
