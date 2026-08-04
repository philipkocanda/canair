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
                        all editable inside the TUI (a/t/V, ? for help)
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
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TypedDict

from canlib.capture_dates import (
    add_scope_args,
    filter_by_date_range,
    filter_by_text,
    resolve_scope_bounds,
)
from canlib.commands._captures_query import (
    _BOLD,
    _CYAN,
    _DIM,
    _RED,
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
from canlib.commands._captures_step import cmd_step
from canlib.commands._captures_step_model import (
    AUTO_STACK_MAX_KEYS,
    DEFAULT_STEP_JOIN_TOL_S,
    VIEW_AUTO,
    VIEW_CHOICES,
)
from canlib.commands._group import group_help
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.states import join_states as _join_states

NAME = "captures"
ALIASES = ["cap"]


# ---------------------------------------------------------------------------
# Summary mode
# ---------------------------------------------------------------------------


def cmd_can_logs(as_json: bool = False) -> int:
    """List imported raw broadcast-CAN frame logs from captures/can/index.yaml."""
    import json as _json

    from canlib import can_logs
    from canlib.profile import active

    logs = can_logs.list_logs(active())
    if as_json:
        print(_json.dumps(logs, indent=2))
        return 0
    if not logs:
        print(
            "  No imported CAN frame logs (captures/can/ is empty). Add one with "
            "`canair import can <FILE>`."
        )
        return 0
    print(f"\n  {_BOLD}Raw-CAN frame logs{_RESET} {_DIM}({len(logs)} in captures/can/){_RESET}")
    for e in logs:
        ids = e.get("id_set", [])
        meta = []
        if e.get("date"):
            meta.append(e["date"])
        if e.get("vehicle_states"):
            meta.append(",".join(e["vehicle_states"]))
        if e.get("bitrate"):
            meta.append(f"{e['bitrate']}bps")
        meta_str = f"  {_DIM}[{' · '.join(meta)}]{_RESET}" if meta else ""
        label = f"  {e['label']}" if e.get("label") else ""
        print(
            f"    {_CYAN}{e.get('file', '?')}{_RESET} {_DIM}({e.get('format', '?')}){_RESET}"
            f"{meta_str}{label}"
        )
        print(
            f"      {_DIM}{e.get('frame_count', 0)} frames, {len(ids)} IDs"
            + (f": {', '.join(ids[:10])}{', …' if len(ids) > 10 else ''}" if ids else "")
            + _RESET
        )
    print()
    return 0


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
    version: str
    vehicle_states: list
    notes: str
    keep_mode: str
    transport: str
    quality: dict | None
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
                "version": e.get("session_version", ""),
                "vehicle_states": e.get("vehicle_states") or [],
                "notes": e.get("session_notes", ""),
                "keep_mode": e.get("keep_mode", ""),
                "transport": e.get("transport", ""),
                "quality": e.get("quality") or None,
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


def _quality_tag(quality: dict | None) -> str:
    """A colored one-liner flagging a session's recorded drops/errors, or ''.

    Clean sessions (no drops/errors) return an empty string so the TOC stays
    uncluttered; only a session that recorded transport trouble gets a line —
    drops in red (the ISO-TP integrity signal), other errors in yellow, with the
    exchange total for context.
    """
    if not quality:
        return ""
    drops = (quality.get("drop", 0) or 0) + (quality.get("stale", 0) or 0)
    errs = sum(quality.get(k, 0) or 0 for k in ("no_data", "bus", "decode", "other"))
    parts: list[str] = []
    if drops:
        parts.append(f"{_RED}drops {drops}{_RESET}")
    if errs:
        parts.append(f"{_YELLOW}errors {errs}{_RESET}")
    if not parts:
        return ""
    ex = quality.get("exchanges")
    ex_txt = f" {_DIM}/ {ex} exchanges{_RESET}" if ex else ""
    return f"{_DIM}⚠ quality:{_RESET} " + " ".join(parts) + ex_txt


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
                "version": s["version"],
                "vehicle_states": s["vehicle_states"],
                "notes": s["notes"],
                "keep_mode": s["keep_mode"],
                "transport": s["transport"],
                "quality": s["quality"],
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
        keep = s.get("keep_mode")
        keep_tag = f" · {_CYAN}keep:{keep}{_RESET}{_DIM}" if keep else ""
        transport = s.get("transport")
        transport_tag = f" · {transport}" if transport else ""
        version = s.get("version")
        version_tag = f" · v{version}" if version else ""
        print(
            f"    {_DIM}{s['n']} captures · {ecus}{keep_tag}{transport_tag}{version_tag} · {s['file']}{_RESET}"
        )
        # Data-quality footprint: flag any drops/errors recorded for the session
        # (the transport-health provenance) so a suspect capture stands out.
        qtag = _quality_tag(s.get("quality"))
        if qtag:
            print(f"    {qtag}")
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


def cmd_list(entries: list[dict], query, as_json: bool = False, limit: int = 0) -> None:
    """List captures matching ``query`` (canlib.query selection).

    The default view: unlike --diff/--step (payload-only), this lists *every*
    matching entry — payloads, text responses and scan results alike — with
    timestamps, state, notes and a decoded preview where a PID definition exists.
    Selectors that matched nothing are reported (with the available ECUs).

    ``limit`` caps the human view to the most recent ``limit`` captures (0 = no
    cap). Truncation is reported loudly (in both text and JSON) so hidden history
    is never silent — the full set is still available with ``--limit 0`` or a
    tighter scope (``--since``/``--last-session``/…).
    """
    q = _parse_query(query)
    matched, empty = q.filter(entries, ecu_of=lambda e: e["ecu"], pid_of=lambda e: str(e["pid"]))

    total = len(matched)
    truncated = limit > 0 and total > limit
    # matched is chronological (date-sorted files) — keep the most recent `limit`.
    shown = matched[-limit:] if truncated else matched

    if as_json:
        _dump_json(
            {
                "query": str(q),
                "matched": total,
                "shown": len(shown),
                "truncated": truncated,
                "limit": limit,
                "unmatched": [str(sel) for sel in empty],
                "captures": [_entry_to_dict(e) for e in shown],
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

    header = f"{total} captures"
    if truncated:
        header = f"latest {len(shown)} of {total} captures"
    print(f"\n  {_BOLD}{q}{_RESET} — {header}\n")

    # Show the ECU column only when the results span more than one ECU.
    show_ecu = len({e["ecu"] for e in shown}) > 1
    for e in shown:
        _print_entry(e, show_ecu=show_ecu)
    print()
    if truncated:
        # Loud, always-printed (not TTY-gated) so an agent sees hidden history.
        print(
            f"  {_YELLOW}… {total - len(shown)} more not shown{_RESET} "
            f"(showing latest {len(shown)} of {total}). "
            f"Use {_BOLD}--limit 0{_RESET} for all, or narrow with "
            f"--since/--last-session/--state.\n"
        )
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
            _print_decoded_preview(decoded, limit=5, ecu=str(ecu), pid=str(pid))
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


def _print_decoded_preview(
    decoded: dict, *, limit: int, ecu: str = "", pid: str = "", indent: str = "    "
) -> None:
    """Print decoded params capped at ``limit``, with a visible hint for any hidden.

    The capture views show only a preview of a PID's decoded parameters to stay
    compact; without a marker a busy PID silently drops params (e.g. a mode/enum
    defined late in the file). Emit a "+N more" line so the cap is never silent,
    pointing at ``canair decode`` (which renders every param, enum labels and all).
    """
    items = list(decoded.items())
    for k, v in items[:limit]:
        print(f"{indent}{_DIM}{k}: {v}{_RESET}")
    hidden = len(items) - limit
    if hidden > 0:
        where = (
            f"canair decode {ecu} {pid}".strip() if (ecu and pid) else "canair decode <ECU> <PID>"
        )
        print(f"{indent}{_DIM}… +{hidden} more param(s) not shown — `{where}` for all{_RESET}")


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
        _print_decoded_preview(
            decoded, limit=3, ecu=str(e.get("ecu", "")), pid=str(e.get("pid", ""))
        )

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


def cmd_delete(
    entries: list[dict],
    query: str,
    *,
    captures_dir: Path | None = None,
    dry_run: bool = False,
    assume_yes: bool = False,
    as_json: bool = False,
) -> int:
    """Delete the captures matching QUERY (already scoped by date/state/label).

    ``entries`` is the scope-filtered capture list from ``run`` (so --since/--state
    already applied); the QUERY narrows it to specific ECU/PID selectors. Deletes
    are addressed by each entry's ``_session_idx``/``_capture_idx`` locators and
    applied in reverse order per file so earlier indices stay valid. Requires
    confirmation unless ``assume_yes``; ``dry_run`` deletes nothing.
    """
    import json as _json

    from canlib.captures import delete_capture
    from canlib.query import QueryError

    cdir = _resolve_captures_dir(captures_dir)

    q = _parse_query(query)
    try:
        matched, _empty = q.filter(
            entries, ecu_of=lambda e: e["ecu"], pid_of=lambda e: str(e["pid"])
        )
    except QueryError as ex:
        print(f"error: invalid query: {ex}", file=sys.stderr)
        return 2

    if not matched:
        if as_json:
            print("[]")
            return 0
        print(f"  No captures match {query!r} in scope — nothing to delete.")
        return 1

    def _row(e: dict) -> dict:
        return {
            "file": e.get("file"),
            "date": e.get("date"),
            "time": e.get("time"),
            "ecu": e.get("ecu"),
            "ecu_addr": e.get("ecu_addr"),
            "pid": e.get("pid"),
            "payload": e.get("payload"),
        }

    if as_json and dry_run:
        print(_json.dumps([_row(e) for e in matched], indent=2))
        return 0

    verb = "Would delete" if dry_run else "Deleting"
    print(f"  {verb} {len(matched)} capture(s) matching {query!r}:")
    for e in matched:
        print(
            f"    {_DIM}{e.get('file', '?')}{_RESET} "
            f"{e.get('ecu', '?')} {e.get('pid', '?')} @ {e.get('date', '?')} "
            f"{e.get('time', '') or '(no time)'}  {_DIM}{e.get('payload', '') or ''}{_RESET}"
        )

    if dry_run:
        print("  (--dry-run: nothing deleted)")
        return 0

    if not assume_yes:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(
                "  error: refusing to delete without confirmation "
                "(pass --yes for non-interactive use, or --dry-run to preview).",
                file=sys.stderr,
            )
            return 2
        resp = input(f"  Delete these {len(matched)} capture(s)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("  Cancelled — nothing deleted.")
            return 1

    # Delete in reverse (file, session_idx, capture_idx) order so earlier indices
    # remain valid as we remove entries from each file.
    to_delete = sorted(
        matched,
        key=lambda e: (e["file"], e["_session_idx"], e["_capture_idx"]),
        reverse=True,
    )
    deleted = 0
    for e in to_delete:
        try:
            delete_capture(cdir / e["file"], e["_session_idx"], e["_capture_idx"])
            deleted += 1
        except Exception as ex:  # keep going; report the failure
            print(f"    ! {e.get('file', '?')} {e.get('ecu', '?')} {e.get('pid', '?')}: {ex}")

    print(f"  Deleted {deleted} capture(s).")
    return 0 if deleted == len(matched) else 1


def cmd_migrate(captures_dir: Path | None, *, dry_run: bool = False, as_json: bool = False) -> int:
    """Convert legacy captures/*.yaml → *.json for the active profile (or --dir)."""
    import json as _json

    from canlib.capture_migrate import MigrationError, migrate_dir

    cdir = _resolve_captures_dir(captures_dir)
    try:
        results = migrate_dir(cdir, dry_run=dry_run)
    except MigrationError as e:
        if as_json:
            _json.dump({"error": str(e)}, sys.stdout)
            print()
        else:
            print(f"  {_YELLOW}migration aborted:{_RESET} {e}", file=sys.stderr)
        return 1

    if as_json:
        _json.dump(
            {
                "dry_run": dry_run,
                "captures_dir": str(cdir),
                "migrated": [
                    {"from": r.yaml_path.name, "to": r.json_path.name, "captures": r.captures}
                    for r in results
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not results:
        print(f"  No legacy .yaml capture files in {cdir} (already JSON).")
        return 0
    caps = sum(r.captures for r in results)
    verb = "Would convert" if dry_run else "Converted"
    print(f"  {verb} {len(results)} file(s), {caps} capture(s):")
    for r in results:
        print(
            f"    {r.yaml_path.name} \u2192 {r.json_path.name}  {_DIM}({r.captures} caps){_RESET}"
        )
    if dry_run:
        print("  Re-run without --dry-run to write.")
    return 0


def cmd_migrate_rx(
    captures_dir: Path | None, *, dry_run: bool = False, as_json: bool = False
) -> int:
    """Rename the capture ``ecu`` field → ``rx`` for the active profile (or --dir)."""
    import json as _json

    from canlib.capture_field_migrate import migrate_dir

    cdir = _resolve_captures_dir(captures_dir)
    results = migrate_dir(cdir, dry_run=dry_run)
    touched = [r for r in results if r.renamed]
    total = sum(r.renamed for r in results)

    if as_json:
        _json.dump(
            {
                "dry_run": dry_run,
                "captures_dir": str(cdir),
                "renamed_total": total,
                "files": [{"file": r.path.name, "renamed": r.renamed} for r in touched],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not touched:
        print(f"  No `ecu` fields to rename in {cdir} (already `rx`).")
        return 0
    verb = "Would rename" if dry_run else "Renamed"
    print(f"  {verb} {total} `ecu` field(s) \u2192 `rx` across {len(touched)} file(s):")
    for r in touched:
        print(f"    {r.path.name}  {_DIM}({r.renamed} field(s)){_RESET}")
    if dry_run:
        print("  Re-run without --dry-run to write.")
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
            "session \u2014 run `canair captures uds --recover` to save (or --discard)."
        )


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Query captured data: uds (diagnostic payloads) | can (raw frame logs)",
        description="Query captured data. Choose a kind:\n"
        "  uds   diagnostic UDS payloads (captures/*.json) — the QUERY/diff/step/\n"
        "        summary/sessions/latest/recover surface (domain A)\n"
        "  can   imported raw broadcast-CAN frame logs (captures/can/index.yaml,\n"
        "        domain B)\n\n"
        "A bare `canair captures BMS 2102` (or any of the --summary/--sessions/… "
        "flags) is shorthand for `canair captures uds …`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="captures_kind", metavar="<kind>")
    _add_uds_parser(kinds)
    _add_can_parser(kinds)
    _add_migrate_parser(kinds)
    _add_migrate_rx_parser(kinds)
    from . import captures_merge_driver

    captures_merge_driver.add_parser(kinds)
    parser.set_defaults(func=group_help("_captures_group_parser"), _captures_group_parser=parser)
    return parser


def _add_migrate_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "migrate",
        help="Convert legacy captures/*.yaml to JSON (captures/*.json)",
        description="Convert the active profile's legacy per-day capture files "
        "(captures/YYYY-MM-DD.yaml) to JSON (captures/YYYY-MM-DD.json).\n\n"
        "Capture data is stored as JSON (parses ~60x faster than YAML); this is "
        "the supported one-time migration for a profile created before the "
        "cutover. Each file is round-trip verified before the YAML is replaced. "
        "Performs the migration by default; pass --dry-run to preview.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview conversions without writing"
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(
        func=lambda args: cmd_migrate(args.dir, dry_run=args.dry_run, as_json=args.json)
    )
    return parser


def _add_migrate_rx_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "migrate-rx",
        help="Rename the legacy capture `ecu` field to `rx` (captures/*.json)",
        description="Rename the persisted capture field `ecu` \u2192 `rx` in the active "
        "profile's capture files.\n\n"
        "The field holds the ECU CAN *response* address (RX = request TX + 8), not "
        "an ECU name, so it was renamed to `rx` to stop it being confused with the "
        "resolved short name. Renames at the capture level and inside "
        "scan_results.responding[]; idempotent (a file already on `rx` is left "
        "untouched). Readers tolerate the legacy `ecu` key, so this migration is "
        "safe to defer. Performs the rename by default; pass --dry-run to preview.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview renames without writing")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(
        func=lambda args: cmd_migrate_rx(args.dir, dry_run=args.dry_run, as_json=args.json)
    )
    return parser


def _add_can_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "can",
        help="List imported raw broadcast-CAN frame logs (captures/can/index.yaml)",
        description="List imported raw broadcast-CAN frame logs (domain B) — "
        "file/format/frames/IDs per log. Import them with `canair import can`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.set_defaults(func=lambda args: cmd_can_logs(as_json=args.json))
    return parser


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

    parser.add_argument(
        "--discard",
        action="store_true",
        help="With --recover: delete orphaned journals without saving them",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --delete: list the captures that would be deleted, delete nothing",
    )

    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="With --delete: skip the confirmation prompt (for scripting)",
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


def run(args) -> int:
    if args.recover:
        return cmd_recover(args.dir, discard=args.discard)

    query = build_query(args.query)
    standalone_mode = args.summary or args.sessions

    if args.delete and not query:
        print(
            "error: --delete requires a QUERY selecting what to delete "
            "(e.g. `canair captures uds OBC 2101 --delete`). Refusing to delete "
            "everything. Narrow with the QUERY and/or scope flags (--since/--state/…).",
            file=sys.stderr,
        )
        return 2

    if args.limit < 0:
        print("error: --limit must be >= 0 (0 = no cap)", file=sys.stderr)
        return 2

    # --summary/--sessions are aggregate modes that take no QUERY. --latest is a
    # dedup-per-PID *view* that reads its ECU/PID selection from the QUERY (like
    # the default list view), so it's handled with the QUERY path below.
    if standalone_mode:
        if query:
            print("error: --summary/--sessions do not take a QUERY argument", file=sys.stderr)
            return 2
        if args.latest:
            print(
                "error: --latest cannot be combined with --summary/--sessions",
                file=sys.stderr,
            )
            return 2
        if args.diff or args.step:
            print(
                "error: --diff/--step cannot be combined with --summary/--sessions",
                file=sys.stderr,
            )
            return 2
    elif args.latest and (args.diff or args.step):
        print("error: --latest cannot be combined with --diff/--step", file=sys.stderr)
        return 2
    elif not query and not args.latest:
        from canlib.commands._hints import ecu_hint

        print(
            "Specify a QUERY to look up captures, e.g. `canair captures BMS 2102` "
            "(or use --summary / --sessions / --latest).\n"
        )
        print(ecu_hint())
        return 2

    # Resolve date scoping (--date is shorthand for an equal since/until pair).
    since, until, err = resolve_scope_bounds(args)
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
        elif args.delete:
            return cmd_delete(
                entries,
                query,
                captures_dir=args.dir,
                dry_run=args.dry_run,
                assume_yes=args.yes,
                as_json=args.json,
            )
        elif args.latest:
            # ECU/PID selection comes from the QUERY (e.g. `BMS --latest`,
            # `BMS:2102 --latest`); a bare `--latest` shows every PID's latest.
            if query:
                q = _parse_query(query)
                entries, _empty = q.filter(
                    entries, ecu_of=lambda e: e["ecu"], pid_of=lambda e: str(e["pid"])
                )
            cmd_latest(entries, None, as_json=args.json)
        elif args.diff:
            cmd_diff(entries, query, show_all=args.all, rulers=args.rulers, as_json=args.json)
        elif args.step:
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
    except QueryError as ex:
        print(f"error: invalid query: {ex}", file=sys.stderr)
        return 2

    return 0
