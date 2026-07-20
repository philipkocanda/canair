#!/usr/bin/env python3
"""Report open reverse-engineering work from the pids/ `research:` sections.

Each ECU file in pids/ may carry a `research:` list tracking untested scans,
undecoded captures, and unverified parameters (schema in pids/_schema.yaml).
This tool aggregates those entries across every ECU so you can answer
"what should I reverse-engineer next?" at a glance.

It complements the other audit tools:
  - pid-coverage.py  finds undecoded *bytes* in PIDs that already have captures
  - research.py      surfaces *planned* work (scans/decodes/verifies) still to do

Modes / filters (all combine with AND):
  --ecu ECU             Only this ECU
  --type TYPE           scan | decode | verify | iocontrol_scan
  --status STATUS       pending | captured | nrc | done
  --priority PRIO       P1 | P2 | P3
  --prerequisite STATE  sleep | plugged | acc | acc2 | ready | charging
  --summary             Counts by status / type / priority / ECU
  --all                 Include done items (hidden by default)
  --json                Machine-readable output
  --dir DIR             Override pids/ directory

Examples:
  python3 research.py                      # all open items, highest priority first
  python3 research.py --summary            # overview counts
  python3 research.py --ecu MCU            # just the MCU backlog
  python3 research.py --type decode        # captured-but-undecoded work
  python3 research.py --priority P1        # high-value items only
  python3 research.py --prerequisite acc   # what needs ACC power to test
  python3 research.py --json               # for further processing
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.pids import load_pids

NAME = "research"

# ANSI colors (match the sibling audit tools)
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"

VALID_TYPES = ("scan", "decode", "verify", "iocontrol_scan")
VALID_STATUSES = ("pending", "captured", "nrc", "done")
VALID_PRIORITIES = ("P1", "P2", "P3")
VALID_PREREQS = ("sleep", "plugged", "acc", "acc2", "ready", "charging")

# Rank for sorting: lower sorts first. Unknown/missing priority sorts last.
_PRIO_RANK = {"P1": 0, "P2": 1, "P3": 2}
# Open statuses roughly ordered by "closeness to done" for stable display.
_STATUS_RANK = {"pending": 0, "nrc": 1, "captured": 2, "done": 3}

# Priority colors.
_PRIO_COLOR = {"P1": _RED, "P2": _YELLOW, "P3": _DIM}
_STATUS_COLOR = {
    "pending": _YELLOW,
    "captured": _CYAN,
    "nrc": _DIM,
    "done": _GREEN,
}


def load_research(pids_dir: Path | None = None) -> list[dict]:
    """Flatten every ECU's `research:` list into records with ECU context.

    Each record is the raw research entry plus an ``ecu`` key (and ``tx_id``
    when the ECU defines one). Order follows file/definition order.
    """
    data = load_pids(pids_dir)
    records: list[dict] = []
    for ecu_name, ecu_def in data.get("ecus", {}).items():
        if not isinstance(ecu_def, dict):
            continue
        research = ecu_def.get("research")
        if not isinstance(research, list):
            continue
        tx_id = ecu_def.get("tx_id")
        for entry in research:
            if not isinstance(entry, dict):
                continue
            rec = dict(entry)
            rec["ecu"] = ecu_name
            if tx_id is not None:
                rec["tx_id"] = tx_id
            records.append(rec)
    return records


def filter_records(
    records: list[dict],
    *,
    ecu: str | None = None,
    rtype: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    prerequisite: str | None = None,
    include_done: bool = False,
) -> list[dict]:
    """Apply the AND-combined CLI filters to the flattened research records.

    ``done`` items are dropped unless ``include_done`` is set or the caller
    explicitly filters ``status='done'``.
    """
    out = []
    for r in records:
        if not include_done and status != "done" and r.get("status") == "done":
            continue
        if ecu and str(r.get("ecu", "")).upper() != ecu.upper():
            continue
        if rtype and r.get("type") != rtype:
            continue
        if status and r.get("status") != status:
            continue
        if priority and r.get("priority") != priority:
            continue
        if prerequisite:
            prereqs = r.get("prerequisite") or []
            if not isinstance(prereqs, list) or prerequisite not in prereqs:
                continue
        out.append(r)
    return out


def _sort_key(r: dict) -> tuple:
    """Sort by priority (P1 first), then status, then ECU, then target."""
    prio = _PRIO_RANK.get(r.get("priority"), 99)
    stat = _STATUS_RANK.get(r.get("status"), 99)
    return (prio, stat, str(r.get("ecu", "")), str(r.get("target", "")))


def _truncate(text: str, width: int = 140) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def cmd_summary(records: list[dict]) -> None:
    """Print aggregate counts across the (already filtered) records."""
    open_records = [r for r in records if r.get("status") != "done"]

    print(f"\n  {_BOLD}Research Summary{_RESET}")
    print(f"  Items: {len(records)} total ({len(open_records)} open, "
          f"{len(records) - len(open_records)} done)")

    def _dump(title: str, counter: Counter, order=None) -> None:
        if not counter:
            return
        print(f"\n  {_BOLD}{title}{_RESET}")
        keys = order if order else sorted(counter, key=lambda k: -counter[k])
        for k in keys:
            if counter.get(k):
                print(f"    {k!s:<14} {counter[k]:>4}")

    _dump("By status:", Counter(r.get("status", "?") for r in records), VALID_STATUSES)
    _dump("By type:", Counter(r.get("type", "?") for r in records), VALID_TYPES)
    _dump("By priority:", Counter(r.get("priority", "—") for r in open_records),
          [*VALID_PRIORITIES, "—"])
    _dump("By ECU (open):", Counter(r.get("ecu", "?") for r in open_records))
    print()


def cmd_list(records: list[dict]) -> None:
    """Print the filtered research items, highest priority first."""
    if not records:
        print("  No research items match the filter criteria.")
        return

    records = sorted(records, key=_sort_key)
    print(f"\n  {_BOLD}Research backlog{_RESET} — {len(records)} items\n")

    for r in records:
        ecu = str(r.get("ecu", "?"))
        rtype = str(r.get("type", "?"))
        target = str(r.get("target", "?"))
        status = str(r.get("status", "?"))
        prio = r.get("priority")

        prio_str = f"{_PRIO_COLOR.get(prio, _DIM)}[{prio}]{_RESET}" if prio else f"{_DIM}[--]{_RESET}"
        status_str = f"{_STATUS_COLOR.get(status, '')}{status}{_RESET}"

        prereqs = r.get("prerequisite") or []
        prereq_str = f"  {_DIM}prereq: {','.join(prereqs)}{_RESET}" if prereqs else ""
        date_str = f"  {_DIM}{r['date']}{_RESET}" if r.get("date") else ""

        print(f"  {prio_str} {_CYAN}{ecu:<10}{_RESET} {rtype:<14} {_BOLD}{target}{_RESET}"
              f"  {status_str}{prereq_str}{date_str}")

        if r.get("result"):
            print(f"       {_DIM}result:{_RESET} {_truncate(r['result'])}")
        if r.get("notes"):
            print(f"       {_DIM}notes:{_RESET}  {_truncate(r['notes'])}")
        wtt = r.get("what_to_test")
        if isinstance(wtt, list) and wtt:
            print(f"       {_DIM}what_to_test: {len(wtt)} item(s){_RESET}")
    print()


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Report open reverse-engineering work from pids/ research: sections",
        description="Report open reverse-engineering work from pids/ research: sections.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument(
        "--ecu", "-e", metavar="ECU", help="Filter by ECU name"
    ).completer = _ecu_completer
    parser.add_argument("--type", "-t", dest="rtype", choices=VALID_TYPES,
                        help="Filter by research type")
    parser.add_argument("--status", choices=VALID_STATUSES, help="Filter by status")
    parser.add_argument("--priority", "-p", choices=VALID_PRIORITIES,
                        help="Filter by priority")
    parser.add_argument("--prerequisite", "--prereq", choices=VALID_PREREQS,
                        help="Filter to items needing this car power state")
    parser.add_argument("--summary", "-s", action="store_true",
                        help="Show aggregate counts instead of the item list")
    parser.add_argument("--all", "-a", action="store_true",
                        help="Include done items (hidden by default)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--dir", type=Path, default=None,
                        help="pids/ directory (default: active profile)")
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    records = load_research(args.dir)
    if not records:
        print("  No research entries found in pids/.")
        return 1

    filtered = filter_records(
        records,
        ecu=args.ecu,
        rtype=args.rtype,
        status=args.status,
        priority=args.priority,
        prerequisite=args.prerequisite,
        include_done=args.all,
    )

    if args.json:
        json.dump(sorted(filtered, key=_sort_key), sys.stdout, indent=2, default=str)
        print()
        return 0

    if args.summary:
        cmd_summary(filtered)
    else:
        cmd_list(filtered)
    return 0
