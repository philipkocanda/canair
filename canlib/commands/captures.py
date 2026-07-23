#!/usr/bin/env python3
"""Query captured UDS payloads across all capture files.

A QUERY selects the ECU(s) and PID(s) to show (see the mini-language below).
By default the matching captures are listed; add --diff or --step to change how
they are rendered. --summary and --latest are standalone modes that take no
QUERY.

  QUERY                 List matching captures (default view)
  QUERY --diff          Monitor-style view (decoded params + colored byte-diff),
                        one block per ECU+PID (unique payloads only; --all = all)
  QUERY --step          Interactive: step through captures one at a time with
                        arrow keys, decoded params + byte-diff vs previous
                        capture; e adds/edits a note, d deletes a capture
  QUERY --step --pair   Interactive: compare two ECU:PID selections side by
                        side, joining captures by nearest timestamp within
                        --join-tol (query must resolve to exactly two keys)
  --summary             Overview: captures per ECU, per date, total payloads
  --sessions            Session table of contents: date/time-span/state/label/
                        notes/ECUs per session (no payloads); --json for machine
                        output. Honors the scope filters.
  --latest [ECU]        Most recent payload per PID (optionally filtered by ECU)

QUERY mini-language (see canlib/query.py):
  ECU PID               one PID (bare ECU + PID)       e.g. BMS 2102
  ECU                   all PIDs for an ECU            e.g. VCU
  ECU:PID               one PID                        e.g. VCU:2101
  ECU:PID,PID           several PIDs                   e.g. VCU:2101,22BC03
  "ECU:PID ECU:PID"     cross-ECU (quote the space)    e.g. "VCU:2101 BMS:2101"
  ECU:22                substring PID match (22xxxx)   e.g. BCM:22

Date scoping (inclusive, YYYY-MM-DD; combines with any mode):
  --since DATE          captures on or after DATE
  --until DATE          captures on or before DATE
  --date DATE           captures on DATE only (--since DATE --until DATE)

State/label scoping (case-insensitive substring; combines with any mode):
  --state SUBSTR        only sessions whose vehicle_states contain SUBSTR (e.g. driving)
  --label SUBSTR        only sessions/captures whose label contains SUBSTR

Examples:
  canair captures BMS 2102                  # ECU + PID (most useful)
  canair captures BMS                       # All BMS captures
  canair captures "BMS:2102,2103"           # Several PIDs
  canair captures IGPM 22BC03 --diff        # Byte-diff for one ECU+PID
  canair captures "BMS:2102,2103" --diff    # Byte-diff, one block per PID
  canair captures BMS 2102 --step           # Step through one PID
  canair captures "BMS:2102,2103" --step    # Step two PIDs interleaved
  canair captures "VCU:2101 BMS:2101" --step  # Cross-ECU step-through
  canair captures "VCU:2101 BMS:2101" --step --pair  # Compare two ECUs side by side
  canair captures "VCU:2101 BMS:2101" --step --pair --join-tol 1.0  # Tighter pairing
  canair captures --diff VCU:2101 --all     # One PID, every payload
  canair captures --summary                 # Overview stats
  canair captures --sessions                # Session table of contents
  canair captures --sessions --state driving # Index of every drive
  canair captures --sessions --json          # Machine-readable TOC
  canair captures --latest BMS              # Latest payload per BMS PID
  canair captures --summary --since 2026-04-19            # Stats since a date
  canair captures BMS 2101 --diff --date 2026-04-19       # One day only
  canair captures VCU --since 2026-04-14 --until 2026-04-21  # Range
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TypedDict

from canlib.align import DEFAULT_JOIN_TOL_S
from canlib.capture_dates import (
    add_scope_args,
    filter_by_date_range,
    filter_by_text,
    resolve_date_bounds,
)
from canlib.commands._captures_query import (
    _BOLD,
    _CYAN,
    _DIM,
    _RESET,
    _YELLOW,
    _decoded_preview,
    _dedupe_payloads,
    _dump_json,
    _entry_to_dict,
    _gather_query,
    _group_by_key,
    _parse_query,
    load_all_captures,
)
from canlib.commands._captures_step import cmd_step, cmd_step_pair
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.states import join_states as _join_states

NAME = "captures"


# ---------------------------------------------------------------------------
# Summary mode
# ---------------------------------------------------------------------------


def cmd_summary(entries: list[dict], as_json: bool = False) -> None:
    """Print overview statistics."""
    by_ecu = defaultdict(int)
    by_date = defaultdict(int)
    payloads = 0
    scans = 0
    responses = 0

    for e in entries:
        by_ecu[e["ecu"]] += 1
        by_date[e["date"]] += 1
        if e["payload"]:
            payloads += 1
        elif e["scan_results"]:
            scans += 1
        elif e["response"]:
            responses += 1

    if as_json:
        _dump_json(
            {
                "files": len({e["file"] for e in entries}),
                "sessions": len({(e["file"], e["session_label"]) for e in entries}),
                "entries": len(entries),
                "payloads": payloads,
                "scans": scans,
                "responses": responses,
                "by_ecu": dict(sorted(by_ecu.items(), key=lambda x: -x[1])),
                "by_date": dict(sorted(by_date.items())),
            }
        )
        return

    print(f"\n  {_BOLD}Capture Summary{_RESET}")
    print(f"  Files:    {len({e['file'] for e in entries})}")
    print(f"  Sessions: {len({(e['file'], e['session_label']) for e in entries})}")
    print(f"  Entries:  {len(entries)} ({payloads} payloads, {scans} scans, {responses} responses)")

    print(f"\n  {_BOLD}By ECU:{_RESET}")
    for ecu, count in sorted(by_ecu.items(), key=lambda x: -x[1]):
        print(f"    {ecu:<12} {count:>4}")

    print(f"\n  {_BOLD}By Date:{_RESET}")
    for day, count in sorted(by_date.items()):
        print(f"    {day}  {count:>4}")
    print()


# ---------------------------------------------------------------------------
# Sessions mode (metadata table of contents)
# ---------------------------------------------------------------------------

# Strip ANSI/CSI escape sequences and other control chars so a note that
# accidentally captured raw keystrokes (e.g. arrow-key \x1b[D from interactive
# entry) can't corrupt the terminal when listed.
_CTRL_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f]")


def _clean(text) -> str:
    """Sanitize a metadata string for terminal display (drop control sequences).

    Also collapses any whitespace run (incl. newlines from YAML block scalars)
    into single spaces so each field renders on one tidy line.
    """
    return " ".join(_CTRL_RE.sub("", str(text)).split())


class _SessionGroup(TypedDict):
    """Rolled-up per-session accumulator built by ``_group_sessions``."""

    file: str
    date: str
    label: str
    vehicle_states: list
    notes: str
    n: int
    ecus: dict  # ordered set (dict) of ECU names
    times: list
    cap_notes: list  # distinct capture-level notes, first-seen order


def _group_sessions(entries: list[dict]) -> list[_SessionGroup]:
    """Reconstruct per-session metadata from flat capture entries.

    Groups by ``(file, _session_idx)`` — the true session identity — and rolls
    up each session's date, label, state, session-level notes, capture count,
    the distinct ECUs touched, the time span, and any distinct capture-level
    notes. Sessions are returned in chronological order (date, then first time).
    """
    groups: dict[tuple[str, int], _SessionGroup] = {}
    for e in entries:
        key = (e["file"], e.get("_session_idx", 0))
        g: _SessionGroup | None = groups.get(key)
        if g is None:
            g = {
                "file": e["file"],
                "date": e.get("date", ""),
                "label": e.get("session_label", ""),
                "vehicle_states": e.get("vehicle_states") or [],
                "notes": e.get("session_notes", ""),
                "n": 0,
                "ecus": {},  # ordered set (dict) of ECU names
                "times": [],
                "cap_notes": [],  # distinct capture-level notes, first-seen order
            }
            groups[key] = g
        g["n"] += 1
        ecu = e.get("ecu") or e.get("ecu_addr") or ""
        if ecu:
            g["ecus"].setdefault(ecu, None)
        t = str(e.get("time", "")).strip()
        if t:
            g["times"].append(t)
        cn = str(e.get("notes", "")).strip()
        if cn and cn not in g["cap_notes"]:
            g["cap_notes"].append(cn)

    sessions = list(groups.values())
    sessions.sort(key=lambda g: (str(g["date"]), min(g["times"]) if g["times"] else ""))
    return sessions


def cmd_sessions(entries: list[dict], as_json: bool = False, max_notes: int = 6) -> None:
    """List capture *sessions* with their metadata — a searchable table of contents.

    Answers "what's in the captures?" without dumping payloads: one block per
    session showing date, time span, state, label, session notes, capture count,
    the ECUs touched, and distinct capture-level notes. Honors the shared scope
    filters (``--since``/``--until``/``--date``/``--state``/``--label``), so e.g.
    ``--sessions --state driving`` is a quick index of every drive.
    """
    sessions = _group_sessions(entries)

    if as_json:
        import json

        out = [
            {
                "file": s["file"],
                "date": s["date"],
                "label": s["label"],
                "vehicle_states": s["vehicle_states"],
                "notes": s["notes"],
                "captures": s["n"],
                "ecus": list(s["ecus"]),
                "time_start": min(s["times"]) if s["times"] else None,
                "time_end": max(s["times"]) if s["times"] else None,
                "capture_notes": s["cap_notes"],
            }
            for s in sessions
        ]
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return

    if not sessions:
        print("  No sessions found.")
        return

    print(f"\n  {_BOLD}Sessions{_RESET} — {len(sessions)} total\n")
    for s in sessions:
        span = ""
        if s["times"]:
            lo, hi = min(s["times"]), max(s["times"])
            lo, hi = lo.split(".")[0], hi.split(".")[0]
            span = lo if lo == hi else f"{lo}-{hi}"
        state_str = _join_states(s["vehicle_states"])
        state = f"  {_CYAN}{_clean(state_str)}{_RESET}" if state_str else ""
        print(f"  {_BOLD}{s['date']}{_RESET}{('  ' + _DIM + span + _RESET) if span else ''}{state}")
        if s["label"]:
            print(f"    {_clean(s['label'])}")
        if s["notes"]:
            print(f"    {_DIM}{_clean(s['notes'])}{_RESET}")
        ecus = ", ".join(s["ecus"]) or "—"
        print(f"    {_DIM}{s['n']} captures · {ecus} · {s['file']}{_RESET}")
        # Distinct capture-level notes (RE annotations) — the other place notes live.
        for cn in s["cap_notes"][:max_notes]:
            clean = _clean(cn)
            trunc = clean if len(clean) <= 100 else clean[:97] + "..."
            print(f"      {_DIM}▸ {trunc}{_RESET}")
        if len(s["cap_notes"]) > max_notes:
            print(f"      {_DIM}… +{len(s['cap_notes']) - max_notes} more capture-notes{_RESET}")
        print()


# ---------------------------------------------------------------------------
# List mode (default view for a QUERY)
# ---------------------------------------------------------------------------


def cmd_list(entries: list[dict], query, as_json: bool = False) -> None:
    """List captures matching ``query`` (canlib.query selection).

    The default view: unlike --diff/--step (payload-only), this lists *every*
    matching entry — payloads, text responses and scan results alike — with
    timestamps, state, notes and a decoded preview where a PID definition exists.
    Selectors that matched nothing are reported (with the available ECUs).
    """
    q = _parse_query(query)
    matched, empty = q.filter(entries, ecu_of=lambda e: e["ecu"], pid_of=lambda e: str(e["pid"]))

    if as_json:
        _dump_json(
            {
                "query": str(q),
                "matched": len(matched),
                "unmatched": [str(sel) for sel in empty],
                "captures": [_entry_to_dict(e) for e in matched],
            }
        )
        return

    if empty:
        known = {e["ecu"].upper() for e in entries}
        for sel in empty:
            hint = ""
            if not sel.pids and sel.ecu not in known and any(c.isdigit() for c in sel.ecu):
                hint = "  (did you mean to attach it as a PID, e.g. ECU:PID?)"
            print(f"  {_YELLOW}No captures matched selector '{sel}'{_RESET}{hint}")
        print(f"  {_DIM}Available ECUs: {', '.join(sorted(known))}{_RESET}")

    if not matched:
        return

    print(f"\n  {_BOLD}{q}{_RESET} — {len(matched)} captures\n")

    # Show the ECU column only when the results span more than one ECU.
    show_ecu = len({e["ecu"] for e in matched}) > 1
    for e in matched:
        _print_entry(e, show_ecu=show_ecu)
    print()
    if sys.stdout.isatty():
        print(
            f"  {_DIM}Tip: add --step to interactively step through these captures "
            f"one at a time.{_RESET}\n"
        )


# ---------------------------------------------------------------------------
# Latest mode
# ---------------------------------------------------------------------------


def cmd_latest(entries: list[dict], ecu_filter: str | None, as_json: bool = False) -> None:
    """Show latest payload per ECU+PID."""
    if ecu_filter:
        from canlib.ecus import canonical_ecu_name

        ecu_upper = canonical_ecu_name(ecu_filter).upper()
        filtered = [e for e in entries if e["ecu"].upper() == ecu_upper]
    else:
        filtered = entries

    # Only payloads (not scan_results or text responses)
    payload_entries = [e for e in filtered if e["payload"]]

    if not payload_entries:
        if as_json:
            _dump_json([])
            return
        print("  No payload captures found.")
        return

    # Group by ECU+PID, keep latest (last in list = most recent date/position)
    latest: dict[tuple[str, str], dict] = {}
    for e in payload_entries:
        key = (e["ecu"], e["pid"])
        latest[key] = e

    ordered = sorted(latest.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))

    if as_json:
        _dump_json([_entry_to_dict(e) for _key, e in ordered])
        return

    title = "Latest payloads" + (f" for {ecu_filter}" if ecu_filter else "")
    print(f"\n  {_BOLD}{title}{_RESET} — {len(latest)} PIDs\n")

    for (ecu, pid), e in ordered:
        payload = e["payload"]
        date = e["date"]
        _st = _join_states(e.get("vehicle_states"))
        state = f"  ({_st})" if _st else ""
        trunc = payload[:80] + "..." if len(payload) > 80 else payload
        print(f"  {_CYAN}{ecu:<10}{_RESET} {pid!s:<10} {_DIM}{date}{state}{_RESET}")
        print(f"    {trunc}")
        decoded = _decoded_preview(e)
        if decoded:
            for k, v in list(decoded.items())[:5]:
                print(f"    {_DIM}{k}: {v}{_RESET}")
    print()


# ---------------------------------------------------------------------------
# Diff mode (monitor-style byte-diff, one block per ECU+PID)
# ---------------------------------------------------------------------------


def _render_diff_group(
    console,
    payloads: list[dict],
    parameters: dict,
    tx_id: int | None,
    show_all: bool,
    rulers: bool = False,
) -> None:
    """Render one ECU+PID block: header, decoded params, optional ruler, byte-diff hex."""
    from rich.markup import escape

    from canlib.decoding import decode_param_rows
    from canlib.formatting import _render_hex_line, render_byte_rulers, render_param_table

    # Decode the most recent payload into param rows (drives the table + colours).
    rows = decode_param_rows(payloads[-1]["payload"], parameters)
    unmapped = not rows
    n_bytes = len(payloads[-1]["payload"].replace(" ", "")) // 2

    unique = _dedupe_payloads(payloads)
    total = len(payloads)
    n_unique = len(unique)
    if total == n_unique or show_all:
        count_str = f"({total} entries)"
    else:
        count_str = f"({total} entries, {n_unique} unique)"

    # ECU + PID headers.
    ecu_display = escape(payloads[0]["ecu"])
    pid_display = escape(str(payloads[0]["pid"]))
    tx_str = f" (0x{tx_id:03X})" if isinstance(tx_id, int) else ""
    console.print(f"\n  [bold cyan]{ecu_display}{tx_str}[/bold cyan]")
    console.print(f"    [yellow]{pid_display}[/yellow]  [dim]{count_str}[/dim]")

    # Decoded-parameter block (aligned columns, verification marks, byte indices).
    if rows:
        console.print(render_param_table(rows, n_bytes=n_bytes), end="")

    # Payload hex lines with per-byte change highlighting, under a byte-index ruler.
    render_list = payloads if show_all else unique
    max_ts = max((len(e.get("time") or e.get("date") or "") for e in render_list), default=0)

    # Byte-index ruler (opt-in via --rulers), aligned with the hex byte columns
    # below. Two rows: "idx" = payload byte position, "wican" = WiCAN Bnn (skips PCI).
    if rulers and n_bytes:
        console.print(
            render_byte_rulers(n_bytes, rows, prefix_width=8 + max_ts), end="", soft_wrap=True
        )

    prev_norm = ""
    for e in render_list:
        norm = e["payload"].upper().replace(" ", "")
        ts = e.get("time") or e.get("date") or ""
        prefix = f"      {ts:<{max_ts}}  "
        line = _render_hex_line(
            norm, rows, unmapped, prev_raw=prev_norm, prefix=prefix, prefix_style="dim"
        )
        # soft_wrap keeps long hex lines on one row (let the terminal wrap, not rich)
        console.print(line, end="", soft_wrap=True)
        prev_norm = norm


def cmd_diff(
    entries: list[dict], query, show_all: bool = False, rulers: bool = False, as_json: bool = False
) -> None:
    """Show payloads matching ``query`` in monitor style, per ECU+PID.

    ``query`` is a canlib.query selection (``"VCU"``, ``"VCU:2101,2102"``,
    ``"VCU:2101 BMS:2101"`` — see canlib.query). One block is rendered per
    distinct (ECU, PID): an ``ECU (0xTXID)`` / ``PID (N entries)`` header, a
    decoded-parameter block (from the most recent payload), then the payload hex
    lines with per-byte change highlighting.

    By default only *unique* payloads per PID are shown; ``show_all=True`` renders
    every capture.
    """
    captures, defs = _gather_query(entries, query, warn=not as_json)
    if not captures:
        if as_json:
            _dump_json([])
        return

    groups = _group_by_key(captures)

    if as_json:
        out = []
        for key, group in sorted(groups.items()):
            parameters, tx_id = defs.get(key, ({}, None))
            unique = _dedupe_payloads(group)
            render_list = group if show_all else unique
            out.append(
                {
                    "ecu": group[0]["ecu"],
                    "pid": str(group[0]["pid"]),
                    "tx_id": f"0x{tx_id:03X}" if isinstance(tx_id, int) else None,
                    "total": len(group),
                    "unique": len(unique),
                    "payloads": [e["payload"].upper().replace(" ", "") for e in render_list],
                    "decoded": _decoded_preview(group[-1]),
                }
            )
        _dump_json(out)
        return

    from rich.console import Console

    console = Console(highlight=False)

    for key, group in sorted(groups.items()):
        parameters, tx_id = defs.get(key, ({}, None))
        _render_diff_group(console, group, parameters, tx_id, show_all, rulers)

    console.print()


def _print_entry(e: dict, show_ecu: bool = False) -> None:
    """Print a single capture entry."""
    ecu_prefix = f"{_CYAN}{e['ecu']:<10}{_RESET} " if show_ecu else ""
    date = e["date"]
    time_str = e.get("time", "")
    ts = f"{date} {time_str}".strip()
    _st = _join_states(e.get("vehicle_states"))
    state = f"  ({_st})" if _st else ""
    label = f"  [{e['label']}]" if e.get("label") else ""

    print(f"  {ecu_prefix}{_DIM}{ts}{state}{label}{_RESET}")
    print(f"    PID: {e['pid']}")

    if e["payload"]:
        trunc = e["payload"][:80] + "..." if len(e["payload"]) > 80 else e["payload"]
        print(f"    Payload: {trunc}")
    elif e["response"]:
        print(f"    Response: {e['response']}")
    elif e["scan_results"]:
        sr = e["scan_results"]
        responding = sr.get("responding", [])
        rejected = sr.get("rejected", "")
        print(f"    Scan: {len(responding)} responding", end="")
        if rejected:
            print(f", {rejected}", end="")
        print()

    decoded = _decoded_preview(e)
    if decoded:
        for k, v in list(decoded.items())[:3]:
            print(f"    {_DIM}{k}: {v}{_RESET}")

    if e.get("notes"):
        notes_str = str(e["notes"]).strip()
        if len(notes_str) > 80:
            notes_str = notes_str[:77] + "..."
        print(f"    {_DIM}Notes: {notes_str}{_RESET}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_query(tokens: list[str]) -> str:
    """Turn positional CLI tokens into a query string for ``canlib.query``.

    Two bare tokens (neither containing ``:``) collapse to the decode.py-style
    ``ECU PID`` form, i.e. ``ECU:PID`` — so ``BMS 2102`` becomes ``BMS:2102``.
    Everything else is space-joined and handed to the mini-language unchanged, so
    ``BMS:2102,2103`` and a quoted ``"VCU:2101 BMS:2101"`` pass straight through.
    """
    if not tokens:
        return ""
    if len(tokens) == 2 and ":" not in tokens[0] and ":" not in tokens[1]:
        return f"{tokens[0]}:{tokens[1]}"
    return " ".join(tokens)


def _resolve_captures_dir(explicit: Path | None) -> Path:
    """Captures dir from --dir, else the active profile's captures/."""
    if explicit is not None:
        return explicit
    from canlib.profile import active

    return active().captures_dir


def cmd_recover(captures_dir: Path | None, discard: bool = False) -> int:
    """Reconcile (or discard) orphaned capture journals left by a killed session."""
    from canlib.capture_journal import list_orphans
    from canlib.capture_journal import recover as _recover

    cdir = _resolve_captures_dir(captures_dir)
    orphans = list_orphans(cdir)
    if not orphans:
        print("  No orphaned capture journals found.")
        return 0

    verb = "Discarding" if discard else "Recovering"
    print(f"  {verb} {len(orphans)} orphaned journal(s) in {cdir}/.journal/:")
    recovered = 0
    for path in orphans:
        try:
            written = _recover(path, discard=discard)
        except Exception as ex:  # keep going; report the failure
            print(f"    ! {path.name}: {ex}")
            continue
        if discard:
            print(f"    - {path.name} (discarded)")
        elif written is not None:
            print(f"    \u2192 {path.name} \u2192 {written.name}")
            recovered += 1
        else:
            print(f"    - {path.name} (empty; removed)")
    if not discard:
        print(f"  Recovered {recovered} session(s).")
    return 0


def orphan_notice(captures_dir: Path | None = None) -> None:
    """Print a one-line notice if orphaned journals exist (best-effort, silent on error)."""
    try:
        from canlib.capture_journal import list_orphans

        cdir = _resolve_captures_dir(captures_dir)
        orphans = list_orphans(cdir)
    except Exception:
        return
    if orphans:
        print(
            f"  Note: {len(orphans)} orphaned capture journal(s) from a previous "
            "session \u2014 run `canair captures --recover` to save (or --discard)."
        )


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Query captured UDS payloads across all capture files",
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
        help="Interactively step through matching captures (arrow keys; e=note, d=delete)",
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
        nargs="?",
        const="",
        metavar="ECU",
        help="Latest payload per PID (optionally filtered by ECU)",
    )
    standalone.add_argument(
        "--recover",
        action="store_true",
        help="Reconcile orphaned capture journals (from a killed/crashed session) "
        "into capture files. Add --discard to delete them without saving.",
    )

    parser.add_argument(
        "--discard",
        action="store_true",
        help="With --recover: delete orphaned journals without saving them",
    )

    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="For --diff/--step: use every payload instead of unique-only",
    )

    parser.add_argument(
        "--rulers",
        "-r",
        action="store_true",
        help="For --diff/--step: show the byte-index ruler (idx/wican) above the hex",
    )

    parser.add_argument(
        "--pair",
        "-P",
        action="store_true",
        help="For --step: compare two ECU:PID selections side by side, joining "
        "captures by nearest timestamp within --join-tol (query must resolve "
        'to exactly two keys, e.g. "VCU:2101 BMS:2101")',
    )

    parser.add_argument(
        "--join-tol",
        type=float,
        default=DEFAULT_JOIN_TOL_S,
        metavar="SECONDS",
        help=f"For --step --pair: max timestamp difference to pair two captures "
        f"(default {DEFAULT_JOIN_TOL_S:g}s)",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output (summary/sessions/latest/diff and the "
        "default QUERY list; not --step, which is interactive)",
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


def run(args) -> int:
    if args.recover:
        return cmd_recover(args.dir, discard=args.discard)

    query = build_query(args.query)
    standalone_mode = args.summary or args.sessions or args.latest is not None

    if args.json and args.step:
        print("error: --json cannot be combined with --step (interactive mode)", file=sys.stderr)
        return 2

    if args.pair and not args.step:
        print("error: --pair requires --step", file=sys.stderr)
        return 2

    # A QUERY and the standalone modes are mutually exclusive; --diff/--step are
    # view modifiers that require a QUERY.
    if standalone_mode:
        if query:
            print(
                "error: --summary/--sessions/--latest do not take a QUERY argument", file=sys.stderr
            )
            return 2
        if args.diff or args.step:
            print(
                "error: --diff/--step cannot be combined with --summary/--sessions/--latest",
                file=sys.stderr,
            )
            return 2
    elif not query:
        from canlib.commands._hints import ecu_hint

        print(
            "Specify a QUERY to look up captures, e.g. `canair captures BMS 2102` "
            "(or use --summary / --sessions / --latest).\n"
        )
        print(ecu_hint())
        return 2

    # Resolve date scoping (--date is shorthand for an equal since/until pair).
    since, until, err = resolve_date_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

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

    from canlib.query import QueryError

    try:
        if args.summary:
            cmd_summary(entries, as_json=args.json)
        elif args.sessions:
            cmd_sessions(entries, as_json=args.json)
        elif args.latest is not None:
            cmd_latest(entries, args.latest or None, as_json=args.json)
        elif args.diff:
            cmd_diff(entries, query, show_all=args.all, rulers=args.rulers, as_json=args.json)
        elif args.step:
            if args.pair:
                cmd_step_pair(
                    entries,
                    query,
                    show_all=args.all,
                    captures_dir=args.dir,
                    rulers=args.rulers,
                    tol_s=args.join_tol,
                )
            else:
                cmd_step(
                    entries, query, show_all=args.all, captures_dir=args.dir, rulers=args.rulers
                )
        else:
            cmd_list(entries, query, as_json=args.json)
    except QueryError as ex:
        print(f"error: invalid query: {ex}", file=sys.stderr)
        return 2

    return 0
