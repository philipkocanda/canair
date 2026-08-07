#!/usr/bin/env python3
"""Report open reverse-engineering work from the ecus/ `research:` sections.

Each ECU file in ecus/ may carry a `research:` list tracking untested scans,
undecoded captures, and unverified parameters (schema in pids_schema.yaml).
This tool aggregates those entries across every ECU so you can answer
"what should I reverse-engineer next?" at a glance.

It complements the other audit tools:
  - `canair coverage`  finds undecoded *bytes* in PIDs that already have captures
  - `canair research`  surfaces *planned* work (scans/decodes/verifies) still to do

Modes / filters (all combine with AND):
  --ecu ECU             Only this ECU
  --type TYPE           scan | decode | verify | iocontrol_scan
  --status STATUS       pending | captured | nrc | done
  --priority PRIO       P1 | P2 | P3
  --states STATE        a token from the profile's vehicle-state vocabulary
                        (base SLEEP/ACC/RUN/CRANK + ALL + any the profile declares)
                        (aliases: --vehicle-states / --prerequisite / --prereq;
                        case-insensitive)
  --summary             Counts by status / type / priority / ECU
  --all                 Include done items (hidden by default)
  --verbose             Show full notes/results (default caps long prose)
  --json                Machine-readable output
  --dir DIR             Override ecus/ directory

Examples:
  canair research                      # all open items, highest priority first
  canair research --summary            # overview counts
  canair research --ecu MCU            # just the MCU backlog
  canair research --type decode        # captured-but-undecoded work
  canair research --priority P1        # high-value items only
  canair research --states acc         # what needs ACC power to test
  canair research --json               # for further processing
"""

import argparse
import json
import shutil
import sys
import textwrap
from collections import Counter
from pathlib import Path

from canlib import ansi
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.pids import load_pids
from canlib.states import allowed_states, parse_states

NAME = "research"

# ANSI colors (match the sibling audit tools)
VALID_TYPES = ("scan", "decode", "verify", "iocontrol_scan")
VALID_STATUSES = ("pending", "captured", "nrc", "done")
VALID_PRIORITIES = ("P1", "P2", "P3")

# Rank for sorting: lower sorts first. Unknown/missing priority sorts last.
_PRIO_RANK = {"P1": 0, "P2": 1, "P3": 2}
# Open statuses roughly ordered by "closeness to done" for stable display.
_STATUS_RANK = {"pending": 0, "nrc": 1, "captured": 2, "done": 3}


# Priority + status colour lookups. Defined as functions (not module-level
# dicts) because the values come from :mod:`canlib.ansi`'s gated attributes,
# which resolve to the escape code or ``""`` based on whether stdout is a TTY;
# a dict evaluated at import time would freeze the decision the process started
# with and misgate every subsequent print. Callers use ``_prio_color(prio)``.
def _prio_color(prio: str) -> str:
    return {"P1": ansi.RED, "P2": ansi.YELLOW, "P3": ansi.DIM}.get(prio, ansi.DIM)


def _status_color(status: str) -> str:
    return {
        "pending": ansi.YELLOW,
        "captured": ansi.CYAN,
        "nrc": ansi.DIM,
        "done": ansi.GREEN,
    }.get(status, "")


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
    state: str | None = None,
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
        if state:
            entry_states = r.get("vehicle_states") or []
            if not isinstance(entry_states, list) or state not in entry_states:
                continue
        out.append(r)
    return out


def _sort_key(r: dict) -> tuple:
    """Sort by priority (P1 first), then status, then ECU, then target."""
    prio = _PRIO_RANK.get(r.get("priority"), 99)
    stat = _STATUS_RANK.get(r.get("status"), 99)
    return (prio, stat, str(r.get("ecu", "")), str(r.get("target", "")))


def _term_width() -> int:
    """Usable text width, clamped to a sane range for wrapping prose."""
    cols = shutil.get_terminal_size(fallback=(100, 24)).columns
    return max(60, min(cols, 120))


def _wrapped(label: str, text: str, indent: int = 7, max_lines: int | None = None) -> list[str]:
    """Render a labelled prose block, wrapped to the terminal width.

    The first line carries the dim ``label:``; continuation lines hang-indent
    to align under the text (not the label). When ``max_lines`` is set, the
    block is capped and a dim ``… (+N lines, -v for full)`` marker is appended.
    """
    text = " ".join(str(text).split())
    pad = " " * indent
    lead = f"{pad}{ansi.DIM}{label}:{ansi.RESET} "
    cont = pad + " " * (len(label) + 2)
    body_width = _term_width() - len(cont)
    lines = textwrap.wrap(text, width=max(20, body_width)) or [""]

    hidden = 0
    if max_lines is not None and len(lines) > max_lines:
        hidden = len(lines) - max_lines
        lines = lines[:max_lines]

    out = [lead + lines[0]]
    out.extend(cont + ln for ln in lines[1:])
    if hidden:
        out.append(f"{cont}{ansi.DIM}… (+{hidden} lines, -v for full){ansi.RESET}")
    return out


def _test_preview(items: list, limit: int = 2) -> str:
    """One-line preview of the first ``what_to_test`` items, '+N more' tail."""
    heads = [" ".join(str(i).split()) for i in items[:limit]]
    # Trim each head so the preview stays to a single readable line.
    heads = [h if len(h) <= 60 else h[:59] + "…" for h in heads]
    extra = len(items) - len(heads)
    joined = "; ".join(heads)
    return f"{joined}  {ansi.DIM}(+{extra} more){ansi.RESET}" if extra > 0 else joined


def cmd_summary(records: list[dict]) -> None:
    """Print aggregate counts across the (already filtered) records."""
    open_records = [r for r in records if r.get("status") != "done"]

    print(f"\n  {ansi.BOLD}Research Summary{ansi.RESET}")
    print(
        f"  Items: {len(records)} total ({len(open_records)} open, "
        f"{len(records) - len(open_records)} done)"
    )

    def _dump(title: str, counter: Counter, order=None) -> None:
        if not counter:
            return
        print(f"\n  {ansi.BOLD}{title}{ansi.RESET}")
        keys = order if order else sorted(counter, key=lambda k: -counter[k])
        for k in keys:
            if counter.get(k):
                print(f"    {k!s:<14} {counter[k]:>4}")

    _dump("By status:", Counter(r.get("status", "?") for r in records), VALID_STATUSES)
    _dump("By type:", Counter(r.get("type", "?") for r in records), VALID_TYPES)
    _dump(
        "By priority:",
        Counter(r.get("priority", "—") for r in open_records),
        [*VALID_PRIORITIES, "—"],
    )
    _dump("By ECU (open):", Counter(r.get("ecu", "?") for r in open_records))
    print()


def cmd_list(records: list[dict], hidden_done: int = 0, verbose: bool = False) -> None:
    """Print the filtered research items, highest priority first."""
    if not records:
        print("  No research items match the filter criteria.")
        return

    # Cap prose blocks in the default view; -v shows the full text.
    cap = None if verbose else 4
    records = sorted(records, key=_sort_key)
    hint = f"  {ansi.DIM}({hidden_done} done hidden — use --all){ansi.RESET}" if hidden_done else ""
    print(f"\n  {ansi.BOLD}Research backlog{ansi.RESET} — {len(records)} items{hint}")

    prio = None
    for r in records:
        # Blank-line divider between priority bands for scannability.
        if r.get("priority") != prio:
            prio = r.get("priority")
            print()

        ecu = str(r.get("ecu", "?"))
        rtype = str(r.get("type", "?"))
        target = str(r.get("target", "?"))
        status = str(r.get("status", "?"))

        prio_str = (
            f"{_prio_color(prio)}[{prio}]{ansi.RESET}" if prio else f"{ansi.DIM}[--]{ansi.RESET}"
        )
        status_str = f"{_status_color(status)}{status:<8}{ansi.RESET}"

        # Aligned, scannable header row; target (variable width) ends the line.
        print(
            f"  {prio_str} {status_str} {rtype:<14} {ansi.CYAN}{ecu:<11}{ansi.RESET} "
            f"{ansi.BOLD}{target}{ansi.RESET}"
        )

        # Dim meta line: where it can be tested + when it was last touched.
        meta = []
        prereqs = r.get("vehicle_states") or []
        if prereqs:
            meta.append(f"states: {','.join(prereqs)}")
        when = r.get("updated") or r.get("date")
        if when:
            meta.append(f"updated {when}")
        if meta:
            print(f"       {ansi.DIM}{'  ·  '.join(meta)}{ansi.RESET}")

        if r.get("result"):
            for line in _wrapped("result", r["result"], max_lines=cap):
                print(line)
        if r.get("notes"):
            for line in _wrapped("notes", r["notes"], max_lines=cap):
                print(line)
        wtt = r.get("what_to_test")
        if isinstance(wtt, list) and wtt:
            if verbose:
                print(f"       {ansi.DIM}to test:{ansi.RESET}")
                width = _term_width() - 11
                for item in wtt:
                    body = textwrap.wrap(" ".join(str(item).split()), width=max(20, width))
                    for i, ln in enumerate(body or [""]):
                        print(f"         {'-' if i == 0 else ' '} {ln}")
            else:
                for line in _wrapped("to test", _test_preview(wtt)):
                    print(line)
        if r.get("capture_protocol"):
            for line in _wrapped("capture", r["capture_protocol"], max_lines=cap):
                print(line)
    print()


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Report open reverse-engineering work from ecus/ research: sections",
        description="Report open reverse-engineering work from ecus/ research: sections.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument(
        "--ecu", "-e", metavar="ECU", help="Filter by ECU name"
    ).completer = _ecu_completer
    parser.add_argument(
        "--type", "-t", dest="rtype", choices=VALID_TYPES, help="Filter by research type"
    )
    parser.add_argument("--status", choices=VALID_STATUSES, help="Filter by status")
    parser.add_argument("--priority", "-p", choices=VALID_PRIORITIES, help="Filter by priority")
    parser.add_argument(
        "--states",
        "--vehicle-states",
        "--prerequisite",
        "--prereq",
        dest="state",
        type=str.upper,
        help="Filter to items needing this car power state (from the profile's "
        "vehicle-state vocabulary)",
    )
    parser.add_argument(
        "--summary",
        "-s",
        action="store_true",
        help="Show aggregate counts instead of the item list",
    )
    parser.add_argument(
        "--all", "-a", action="store_true", help="Include done items (hidden by default)"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show full notes/results (default caps long prose)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--dir", type=Path, default=None, help="ecus/ directory (default: active profile)"
    )
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    records = load_research(args.dir)
    if not records:
        print("  No research entries found in ecus/.")
        return 1

    # Friendly (non-fatal) note when filtering on a state outside the profile's
    # vocabulary — the filter still runs (yields nothing) but the user learns why.
    if args.state:
        allowed = allowed_states()
        if not set(parse_states(args.state)) <= allowed:
            print(
                f"  note: '{args.state}' is not in this profile's state vocabulary "
                f"({', '.join(sorted(allowed))}).",
                file=sys.stderr,
            )

    filtered = filter_records(
        records,
        ecu=args.ecu,
        rtype=args.rtype,
        status=args.status,
        priority=args.priority,
        state=args.state,
        include_done=args.all,
    )

    if args.json:
        json.dump(sorted(filtered, key=_sort_key), sys.stdout, indent=2, default=str)
        print()
        return 0

    if args.summary:
        cmd_summary(filtered)
    else:
        # Count done items suppressed by the default view (not when the user
        # explicitly asked for --all or filtered on a specific status).
        hidden_done = 0
        if not args.all and args.status is None:
            hidden_done = sum(1 for r in records if r.get("status") == "done")
        cmd_list(filtered, hidden_done=hidden_done, verbose=args.verbose)
    return 0
