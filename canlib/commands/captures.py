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
  python3 query-captures.py BMS 2102                  # ECU + PID (most useful)
  python3 query-captures.py BMS                       # All BMS captures
  python3 query-captures.py "BMS:2102,2103"           # Several PIDs
  python3 query-captures.py IGPM 22BC03 --diff        # Byte-diff for one ECU+PID
  python3 query-captures.py "BMS:2102,2103" --diff    # Byte-diff, one block per PID
  python3 query-captures.py BMS 2102 --step           # Step through one PID
  python3 query-captures.py "BMS:2102,2103" --step    # Step two PIDs interleaved
  python3 query-captures.py "VCU:2101 BMS:2101" --step  # Cross-ECU step-through
  python3 query-captures.py --diff VCU:2101 --all     # One PID, every payload
  python3 query-captures.py --summary                 # Overview stats
  python3 query-captures.py --sessions                # Session table of contents
  python3 query-captures.py --sessions --state driving # Index of every drive
  python3 query-captures.py --sessions --json          # Machine-readable TOC
  python3 query-captures.py --latest BMS              # Latest payload per BMS PID
  python3 query-captures.py --summary --since 2026-04-19            # Stats since a date
  python3 query-captures.py BMS 2101 --diff --date 2026-04-19       # One day only
  python3 query-captures.py VCU --since 2026-04-14 --until 2026-04-21  # Range
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from canlib.capture_dates import (
    add_scope_args,
    filter_by_date_range,
    filter_by_text,
    resolve_date_bounds,
)
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.states import join_states as _join_states

NAME = "captures"

# ANSI color helpers
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


# ---------------------------------------------------------------------------
# On-the-fly decoding
# ---------------------------------------------------------------------------
#
# Decoded parameter values are NOT stored in capture files (they are derived
# data). We regenerate them on demand from the payload + PID definitions when
# displaying previews. The PID index is built once and cached.

_ecu_index = None
_decode_fn = None


def _decoded_preview(entry: dict) -> dict | None:
    """Regenerate decoded parameter values for a capture entry, or None.

    Lazily loads PID definitions on first use. Returns a dict of
    ``param_name -> "value unit (formatted)"`` strings, matching the format
    previously stored in the (now removed) ``decoded`` field.
    """
    global _ecu_index, _decode_fn

    payload = entry.get("payload")
    ecu = entry.get("ecu")
    pid = entry.get("pid")
    if not payload or not ecu or not pid:
        return None

    if _decode_fn is None:
        try:
            from canlib.captures import _decode_payload
            from canlib.pids import build_ecu_index, load_pids

            _decode_fn = _decode_payload
            _ecu_index = build_ecu_index(load_pids())
        except Exception:
            _decode_fn = False  # sentinel: decoding unavailable
            return None
    if _decode_fn is False:
        return None

    try:
        return _decode_fn(ecu, str(pid), payload, {}, ecu_index=_ecu_index)
    except Exception:
        return None


def _dump_json(obj) -> None:
    """Write ``obj`` to stdout as pretty JSON (dates/other objects via str())."""
    import json

    json.dump(obj, sys.stdout, indent=2, default=str)
    print()


def _entry_to_dict(e: dict, *, decoded: bool = True) -> dict:
    """Serialize a capture entry to a clean, JSON-ready dict.

    Includes the regenerated ``decoded`` preview (param -> formatted value) when
    ``decoded`` is set and a PID definition exists.
    """
    d = {
        "ecu": e.get("ecu"),
        "ecu_addr": e.get("ecu_addr"),
        "pid": str(e["pid"]) if e.get("pid") is not None else None,
        "date": e.get("date"),
        "time": e.get("time") or None,
        "vehicle_states": e.get("vehicle_states") or None,
        "label": e.get("label") or e.get("session_label") or None,
        "notes": (str(e["notes"]).strip() or None) if e.get("notes") else None,
        "payload": e.get("payload") or None,
        "response": e.get("response") or None,
        "scan_results": e.get("scan_results") or None,
    }
    if decoded:
        d["decoded"] = _decoded_preview(e)
    return d


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_all_captures(captures_dir: Path | None = None) -> list[dict]:
    """Load all capture files and return a flat list of (session, capture) tuples.

    Each entry is a dict with keys:
        file, date, label, state, ecu, ecu_addr, pid, payload, response,
        scan_results, notes, time

    The capture ``ecu`` field stores the ECU CAN response address (e.g.
    ``"0x7EC"``); it is resolved to the canonical short name in ``ecu`` for
    display/joins, with the raw address preserved in ``ecu_addr``.

    Plus internal locator keys (``_session_idx``, ``_capture_idx``) that address
    the capture within its source file, for in-place edits/deletes.
    """
    from canlib.ecus import build_rx_index, ecu_name_from_ref

    if captures_dir is None:
        from canlib.profile import active

        captures_dir = active().captures_dir

    try:
        rx_index = build_rx_index()
    except Exception:
        rx_index = {}

    entries = []
    for fpath in sorted(captures_dir.glob("*.yaml")):
        if fpath.name.startswith(("SCHEMA", "_")):
            continue
        with open(fpath) as f:
            data = yaml.safe_load(f)
        if not data or "sessions" not in data:
            continue
        for s_idx, session in enumerate(data["sessions"]):
            date = session.get("date", "")
            label = session.get("label", "")
            vehicle_states = session.get("vehicle_states") or []
            session_notes = session.get("notes", "")
            for c_idx, cap in enumerate(session.get("captures", [])):
                raw_ecu = cap.get("ecu", "")
                entry = {
                    "file": fpath.name,
                    "date": date,
                    "session_label": label,
                    "vehicle_states": list(vehicle_states),
                    "session_notes": session_notes,
                    "ecu": ecu_name_from_ref(raw_ecu, rx_index) if raw_ecu else "",
                    "ecu_addr": raw_ecu,
                    "pid": cap.get("pid", ""),
                    "payload": cap.get("payload"),
                    "response": cap.get("response"),
                    "scan_results": cap.get("scan_results"),
                    "notes": cap.get("notes", ""),
                    "time": cap.get("time", ""),
                    "label": cap.get("label", ""),
                    "_session_idx": s_idx,
                    "_capture_idx": c_idx,
                }
                entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Query parsing (alias-aware)
# ---------------------------------------------------------------------------


def _parse_query(query):
    """Parse a QUERY and canonicalize selector ECUs (aliases -> primary name).

    So `canair captures SMK` resolves to the SKM module. Falls back to the raw
    parse if the ECU registry is unavailable; :class:`EcuNameCollision` from an
    ambiguous registry is allowed to propagate.
    """
    from canlib.query import parse_query

    q = parse_query(query)
    try:
        from canlib.ecus import build_canonical_name_index

        name_index = build_canonical_name_index()
    except FileNotFoundError:
        return q
    return q.canonicalize_ecus(lambda ecu: name_index.get(ecu, ecu).upper())


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


def _group_sessions(entries: list[dict]) -> list[dict]:
    """Reconstruct per-session metadata from flat capture entries.

    Groups by ``(file, _session_idx)`` — the true session identity — and rolls
    up each session's date, label, state, session-level notes, capture count,
    the distinct ECUs touched, the time span, and any distinct capture-level
    notes. Sessions are returned in chronological order (date, then first time).
    """
    groups: dict[tuple[str, int], dict] = {}
    for e in entries:
        key = (e["file"], e.get("_session_idx", 0))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
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
# Query gathering (shared by --diff and --step)
# ---------------------------------------------------------------------------

# A resolved PID definition: (parameters, tx_id) for one (ECU, PID) pair.
PidDefs = tuple[dict, "int | None"]


def _load_ecu_index() -> dict:
    """Load + build the ECU/PID definition index once (empty dict on failure)."""
    try:
        from canlib.pids import build_ecu_index, load_pids

        return build_ecu_index(load_pids())
    except Exception:
        return {}


def _resolve_defs(ecu_index: dict, ecu: str, pid: str) -> PidDefs:
    """Look up ``(parameters, tx_id)`` for one ECU+PID from the index.

    Parameters come from an *exact* PID key match (substring-matched captures
    with no exact definition render as raw hex, i.e. empty parameters).
    """
    info = ecu_index.get(str(ecu).upper())
    if not info:
        return {}, None
    tx_id = info.get("tx_id")
    pid_info = info.get("pids", {}).get(str(pid).upper())
    parameters = (pid_info or {}).get("parameters", {}) or {}
    return parameters, tx_id


def _gather_query(
    entries: list[dict], query, *, warn: bool = True
) -> tuple[list[dict], dict[tuple[str, str], PidDefs]]:
    """Select payload captures matching ``query`` (a canlib.query string/Query).

    Returns ``(captures, defs)``:
      - ``captures`` — payload-bearing entries matching any selector, sorted
        chronologically ``(date, time)``.
      - ``defs`` — cache mapping ``(ECU_UPPER, PID_UPPER)`` to ``(parameters,
        tx_id)`` for every distinct pair present in ``captures``.

    When ``warn`` is set, prints a note for any selector that matched nothing
    (with an ``ECU:PID`` hint when a bare selector looks like a DID).
    """
    q = _parse_query(query)
    payloads = [e for e in entries if _is_hex_payload(e.get("payload"))]
    matched, empty = q.filter(payloads, ecu_of=lambda e: e["ecu"], pid_of=lambda e: e["pid"])

    # Chronological order (date, then time within a session).
    matched.sort(key=lambda e: (str(e.get("date", "")), str(e.get("time", ""))))

    ecu_index = _load_ecu_index()
    defs: dict[tuple[str, str], PidDefs] = {}
    for e in matched:
        key = (e["ecu"].upper(), str(e["pid"]).upper())
        if key not in defs:
            defs[key] = _resolve_defs(ecu_index, *key)

    if warn and empty:
        known_ecus = {e["ecu"].upper() for e in payloads}
        for sel in empty:
            hint = ""
            # Bare selector whose "ECU" isn't a real ECU but looks like a DID —
            # likely the old `ECU PID` space form; nudge toward `ECU:PID`.
            if not sel.pids and sel.ecu not in known_ecus and any(c.isdigit() for c in sel.ecu):
                hint = "  (did you mean to attach it as a PID, e.g. ECU:PID?)"
            print(f"  {_YELLOW}No captures matched selector '{sel}'{_RESET}{hint}")
        avail = ", ".join(sorted(known_ecus))
        print(f"  {_DIM}Available ECUs: {avail}{_RESET}")

    return matched, defs


def _is_hex_payload(payload) -> bool:
    """True if ``payload`` is a byte-diffable hex string.

    The byte-level views (``--diff``/``--step``) render payloads as hex. Some
    legacy captures store a human outcome (e.g. ``"NO DATA"``) under ``payload``
    instead of ``response``; those aren't hex and must be excluded here so the
    hex renderer never chokes on them. Spaces are tolerated (payloads are
    normally stored space-free, uppercase).
    """
    if not payload:
        return False
    s = str(payload).replace(" ", "")
    if not s or len(s) % 2 != 0:
        return False
    try:
        bytes.fromhex(s)
    except ValueError:
        return False
    return True


def _capture_key(e: dict) -> tuple[str, str]:
    """The (ECU, PID) grouping/diff key for a capture (upper-cased)."""
    return e["ecu"].upper(), str(e["pid"]).upper()


def _dedupe_payloads(payloads: list[dict]) -> list[dict]:
    """Drop duplicate payloads per (ECU, PID), keeping first-seen order.

    Deduping is scoped to each ECU+PID so identical hex under different PIDs is
    never collapsed together.
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict] = []
    for e in payloads:
        ecu, pid = _capture_key(e)
        norm = e["payload"].upper().replace(" ", "")
        key = (ecu, pid, norm)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _prev_same_index(captures: list[dict]) -> list[int | None]:
    """Per position, the nearest earlier index sharing the same (ECU, PID).

    Used by the interleaved step view so byte-diffing compares a capture against
    the previous capture *of the same PID*, not merely the adjacent frame.
    """
    last: dict[tuple[str, str], int] = {}
    out: list[int | None] = []
    for idx, e in enumerate(captures):
        key = _capture_key(e)
        out.append(last.get(key))
        last[key] = idx
    return out


def _key_ordinals(captures: list[dict]) -> list[tuple[int, int]]:
    """Per position, its 1-based ordinal within its (ECU, PID) and that group's total."""
    totals: dict[tuple[str, str], int] = {}
    for e in captures:
        totals[_capture_key(e)] = totals.get(_capture_key(e), 0) + 1
    seen: dict[tuple[str, str], int] = {}
    out: list[tuple[int, int]] = []
    for e in captures:
        key = _capture_key(e)
        seen[key] = seen.get(key, 0) + 1
        out.append((seen[key], totals[key]))
    return out


def _group_by_key(captures: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Group captures by (ECU, PID), preserving first-appearance order of keys."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in captures:
        groups.setdefault(_capture_key(e), []).append(e)
    return groups


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


# ---------------------------------------------------------------------------
# Step mode (interactive)
# ---------------------------------------------------------------------------


def _read_key(fd: int) -> str:
    """Read a single keypress (or escape sequence) from a raw/cbreak stdin."""
    from canlib.tui import read_key_raw

    return read_key_raw(fd)


def _render_step_frame(
    console,
    captures: list[dict],
    i: int,
    defs: dict[tuple[str, str], PidDefs],
    prev_idx: list[int | None],
    ordinals: list[tuple[int, int]],
    status: str = "",
    prompt: str | None = None,
    rulers: bool = False,
) -> None:
    """Render one capture full-screen: header, decoded params, optional ruler, diff hex.

    PID definitions (``parameters``/``tx_id``) are resolved per-capture from
    ``defs``, so a single interleaved list can span multiple PIDs/ECUs. The
    byte-diff compares the current payload against the previous capture of the
    *same* (ECU, PID) — via ``prev_idx`` — rendered dimmed above for reference.

    ``prompt`` (when set) replaces the status line with a bold input prompt, used
    by the note-edit and delete-confirm sub-loops.
    """
    from rich.markup import escape

    from canlib.decoding import decode_param_rows
    from canlib.formatting import _render_hex_line, render_byte_rulers, render_param_table

    e = captures[i]
    key = _capture_key(e)
    parameters, tx_id = defs.get(key, ({}, None))
    multi = len(defs) > 1

    pj = prev_idx[i]
    prev = captures[pj] if pj is not None else None

    norm = e["payload"].upper().replace(" ", "")
    prev_norm = prev["payload"].upper().replace(" ", "") if prev else ""
    n_bytes = len(norm) // 2

    # Decode the *current* capture (drives the table + byte colours for this frame).
    rows = decode_param_rows(e["payload"], parameters)
    unmapped = not rows

    # Header: ECU / PID + position, timestamp, state, label, file.
    ecu_display = escape(e["ecu"])
    pid_display = escape(str(e["pid"]))
    tx_str = f" (0x{tx_id:03X})" if isinstance(tx_id, int) else ""
    ts = e.get("time") or e.get("date") or ""
    _st = _join_states(e.get("vehicle_states"))
    state = f"  states={escape(_st)}" if _st else ""
    label = f"  [{escape(e['label'])}]" if e.get("label") else ""
    file_str = f"  ({escape(e['file'])})" if e.get("file") else ""

    ord_n, ord_m = ordinals[i]
    per_pid = f" · this PID {ord_n}/{ord_m}" if multi else ""

    console.print(f"\n  [bold cyan]{ecu_display}{tx_str}[/bold cyan]")
    console.print(
        f"    [yellow]{pid_display}[/yellow]  [dim]capture {i + 1}/{len(captures)}{per_pid}[/dim]"
    )
    console.print(f"    [bold]{escape(ts)}[/bold][dim]{state}{label}{file_str}[/dim]")

    # Capture note (if any).
    note = (e.get("notes") or "").strip()
    if note:
        console.print(f"    [dim]note:[/dim] {escape(note)}")

    # Decoded-parameter block (aligned columns, verification marks, byte indices).
    if rows:
        console.print(render_param_table(rows, n_bytes=n_bytes), end="")

    # Byte-index ruler (opt-in via --rulers), aligned with the hex byte columns below.
    prev_ts = (prev.get("time") or prev.get("date") or "") if prev else ""
    max_ts = max(len(ts), len(prev_ts))
    if rulers and n_bytes:
        console.print(
            render_byte_rulers(n_bytes, rows, prefix_width=8 + max_ts), end="", soft_wrap=True
        )

    # Previous same-PID capture (dimmed, no highlight) for visual reference, then
    # the current capture with per-byte change highlighting against it.
    if prev is not None:
        prev_prefix = f"      {prev_ts:<{max_ts}}  "
        console.print(
            _render_hex_line(prev_norm, rows, unmapped, prefix=prev_prefix, prefix_style="dim"),
            end="",
            soft_wrap=True,
        )
    prefix = f"    > {ts:<{max_ts}}  "
    console.print(
        _render_hex_line(norm, rows, unmapped, prev_raw=prev_norm, prefix=prefix),
        end="",
        soft_wrap=True,
    )

    # Footer: key hints, then either an input prompt or a transient status.
    console.print(
        "\n  [dim]←/h/p prev   →/l/n/space next   PgUp/PgDn ±100   g/G first/last   "
        ": goto   e note   d delete   q quit[/dim]"
    )
    if prompt is not None:
        console.print(f"  [bold yellow]{escape(prompt)}[/bold yellow]")
    elif status:
        console.print(f"  [yellow]{escape(status)}[/yellow]")


def cmd_step(
    entries: list[dict],
    query,
    show_all: bool = False,
    captures_dir: Path | None = None,
    rulers: bool = False,
) -> None:
    """Interactively step through captures matching ``query``, one at a time.

    ``query`` is a canlib.query selection (``"VCU"``, ``"VCU:2101,2102"``,
    ``"VCU:2101 BMS:2101"``). Captures are interleaved chronologically across the
    selected PIDs; the byte-diff for each frame is computed against the previous
    capture of the same (ECU, PID).

    Arrow keys (or vim ``h``/``l``) move between captures; PgUp/PgDn skip ±100;
    ``:`` jumps to a capture number; ``g``/``G`` go to first/last. ``e``
    edits/adds the current capture's note; ``d`` deletes it (y/N confirm). Both
    mutate the source YAML and reload in place.

    Steps through *unique* payloads (per PID) by default; ``show_all=True`` walks
    every capture. Falls back to ``cmd_diff`` when stdin/stdout is not a TTY.
    """
    import sys

    from rich.console import Console

    from canlib.captures import delete_capture, set_capture_note

    if captures_dir is None:
        from canlib.profile import active

        captures_dir = active().captures_dir

    def build_list(src: list[dict], warn: bool):
        caps, defs = _gather_query(src, query, warn=warn)
        if not show_all:
            caps = _dedupe_payloads(caps)
        prev_idx = _prev_same_index(caps)
        ordinals = _key_ordinals(caps)
        return caps, defs, prev_idx, ordinals

    captures, defs, prev_idx, ordinals = build_list(entries, warn=True)
    if not captures:
        return

    # Non-interactive (piped) — fall back to the static diff view.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("  (not a TTY — falling back to --diff view)")
        cmd_diff(entries, query, show_all=show_all, rulers=rulers)
        return

    import termios
    import tty

    console = Console(highlight=False)
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    i = len(captures) - 1  # start at the most recent capture
    status = ""
    final_msg = ""

    def redraw(prompt: str | None = None) -> None:
        sys.stdout.write("\033[2J\033[H")  # clear + home
        _render_step_frame(
            console,
            captures,
            i,
            defs,
            prev_idx,
            ordinals,
            status=status,
            prompt=prompt,
            rulers=rulers,
        )

    def reload() -> bool:
        """Re-read captures from disk and rebuild the list. False if empty."""
        nonlocal captures, defs, prev_idx, ordinals, i
        fresh = load_all_captures(captures_dir)
        captures, defs, prev_idx, ordinals = build_list(fresh, warn=False)
        if not captures:
            return False
        i = min(i, len(captures) - 1)
        return True

    # Alternate screen buffer + hidden cursor for clean redraws.
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    try:
        tty.setcbreak(fd)
        while True:
            redraw()
            status = ""

            key = _read_key(fd)
            if key in ("q", "Q", "\x1b\x1b", "\x1b", "\x03"):  # q / Esc / Ctrl-C
                break
            elif key in ("\x1b[D", "h", "p"):  # left / prev
                if i > 0:
                    i -= 1
                else:
                    status = "At first capture"
            elif key in ("\x1b[C", "l", "n", " "):  # right / next
                if i < len(captures) - 1:
                    i += 1
                else:
                    status = "At last capture"
            elif key in ("\x1b[H", "g"):  # Home / g — first
                i = 0
            elif key in ("\x1b[F", "G"):  # End / G — last
                i = len(captures) - 1
            elif key in ("\x1b[6~", "]"):  # PageDown / ] — forward 100
                i = min(i + 100, len(captures) - 1)
            elif key in ("\x1b[5~", "["):  # PageUp / [ — back 100
                i = max(i - 100, 0)
            elif key in (":", "#"):  # jump to a specific capture number
                buf = ""
                cancelled = False
                while True:
                    redraw(
                        prompt=f"go to capture # (1-{len(captures)}, Enter=go · Esc=cancel): {buf}\u2588"
                    )
                    k = _read_key(fd)
                    if k in ("\r", "\n"):
                        break
                    if k in ("\x1b", "\x03"):
                        cancelled = True
                        break
                    if k in ("\x7f", "\x08"):  # backspace
                        buf = buf[:-1]
                    elif k.isdigit():
                        buf += k
                if cancelled or not buf:
                    status = "Jump cancelled"
                else:
                    n = int(buf)
                    if 1 <= n <= len(captures):
                        i = n - 1
                    else:
                        i = max(0, min(n - 1, len(captures) - 1))
                        status = f"Clamped to {i + 1} (valid: 1-{len(captures)})"
            elif key in ("e", "E"):  # edit / add note
                cap = captures[i]
                buf = (cap.get("notes") or "").replace("\n", " ").strip()
                cancelled = False
                while True:
                    redraw(prompt=f"note (Enter=save · Esc=cancel): {buf}\u2588")
                    k = _read_key(fd)
                    if k in ("\r", "\n"):
                        break
                    if k in ("\x1b", "\x03"):
                        cancelled = True
                        break
                    if k in ("\x7f", "\x08"):  # backspace
                        buf = buf[:-1]
                    elif len(k) == 1 and k.isprintable():
                        buf += k
                if cancelled:
                    status = "Note edit cancelled"
                else:
                    try:
                        set_capture_note(
                            captures_dir / cap["file"],
                            cap["_session_idx"],
                            cap["_capture_idx"],
                            buf,
                        )
                        saved = "Note saved" if buf.strip() else "Note cleared"
                        if not reload():
                            final_msg = saved + " — no captures left"
                            break
                        status = saved
                    except Exception as ex:
                        status = f"Note save failed: {ex}"
            elif key in ("d", "D"):  # delete current capture (confirmed)
                cap = captures[i]
                redraw(prompt="Delete this capture? (y/N)")
                if _read_key(fd) in ("y", "Y"):
                    try:
                        delete_capture(
                            captures_dir / cap["file"],
                            cap["_session_idx"],
                            cap["_capture_idx"],
                        )
                        if not reload():
                            final_msg = "Capture deleted — no captures left"
                            break
                        status = "Capture deleted"
                    except Exception as ex:
                        status = f"Delete failed: {ex}"
                else:
                    status = "Delete cancelled"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?25h\033[?1049l")  # show cursor, leave alt screen
        sys.stdout.flush()

    if final_msg:
        print(f"  {final_msg}")


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
            cmd_step(entries, query, show_all=args.all, captures_dir=args.dir, rulers=args.rulers)
        else:
            cmd_list(entries, query, as_json=args.json)
    except QueryError as ex:
        print(f"error: invalid query: {ex}", file=sys.stderr)
        return 2

    return 0
