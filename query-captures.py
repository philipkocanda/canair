#!/usr/bin/env python3
"""Query captured UDS payloads across all capture files.

Modes:
  --ecu ECU --pid PID   Captures for a specific ECU+PID combination (recommended)
  --ecu ECU             All captures for an ECU
  --pid PID             All captures for a PID (across all ECUs)
  --summary             Overview: captures per ECU, per date, total payloads
  --latest [ECU]        Most recent payload per PID (optionally filtered by ECU)
  --diff ECU PID        Show all payloads for an ECU+PID with byte-level diff colorization

Examples:
  python3 query-captures.py --ecu IGPM --pid 22BC03   # ECU+PID (most useful)
  python3 query-captures.py --ecu BMS                 # All BMS captures
  python3 query-captures.py --summary                 # Overview stats
  python3 query-captures.py --latest BMS              # Latest payload per BMS PID
  python3 query-captures.py --diff IGPM 22BC03        # Byte-level diff
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

CAPTURES_DIR = Path(__file__).parent / "captures"
PIDS_DIR = Path(__file__).parent / "pids"

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
            _ecu_index = build_ecu_index(load_pids(PIDS_DIR))
        except Exception:
            _decode_fn = False  # sentinel: decoding unavailable
            return None
    if _decode_fn is False:
        return None

    try:
        return _decode_fn(ecu, str(pid), payload, {}, ecu_index=_ecu_index)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_all_captures(captures_dir: Path = CAPTURES_DIR) -> list[dict]:
    """Load all capture files and return a flat list of (session, capture) tuples.

    Each entry is a dict with keys:
        file, date, label, state, ecu, pid, payload, response, scan_results,
        notes, time
    """
    entries = []
    for fpath in sorted(captures_dir.glob("*.yaml")):
        if fpath.name.startswith(("SCHEMA", "_")):
            continue
        with open(fpath) as f:
            data = yaml.safe_load(f)
        if not data or "sessions" not in data:
            continue
        for session in data["sessions"]:
            date = session.get("date", "")
            label = session.get("label", "")
            state = session.get("state", "")
            for cap in session.get("captures", []):
                entry = {
                    "file": fpath.name,
                    "date": date,
                    "session_label": label,
                    "state": state,
                    "ecu": cap.get("ecu", ""),
                    "pid": cap.get("pid", ""),
                    "payload": cap.get("payload"),
                    "response": cap.get("response"),
                    "scan_results": cap.get("scan_results"),
                    "notes": cap.get("notes", ""),
                    "time": cap.get("time", ""),
                    "label": cap.get("label", ""),
                }
                entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Summary mode
# ---------------------------------------------------------------------------

def cmd_summary(entries: list[dict]) -> None:
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

    print(f"\n  {_BOLD}Capture Summary{_RESET}")
    print(f"  Files:    {len(set(e['file'] for e in entries))}")
    print(f"  Sessions: {len(set((e['file'], e['session_label']) for e in entries))}")
    print(f"  Entries:  {len(entries)} ({payloads} payloads, {scans} scans, {responses} responses)")

    print(f"\n  {_BOLD}By ECU:{_RESET}")
    for ecu, count in sorted(by_ecu.items(), key=lambda x: -x[1]):
        print(f"    {ecu:<12} {count:>4}")

    print(f"\n  {_BOLD}By Date:{_RESET}")
    for date, count in sorted(by_date.items()):
        print(f"    {date}  {count:>4}")
    print()


# ---------------------------------------------------------------------------
# Filter mode (--ecu, --pid, or both)
# ---------------------------------------------------------------------------

def cmd_filter(entries: list[dict], ecu: str | None = None, pid: str | None = None) -> None:
    """Show captures filtered by ECU, PID, or both."""
    filtered = entries

    if ecu:
        ecu_upper = ecu.upper()
        filtered = [e for e in filtered if e["ecu"].upper() == ecu_upper]
        if not filtered:
            # Try partial match
            filtered = [e for e in entries if ecu_upper in e["ecu"].upper()]
        if not filtered:
            print(f"  No captures found for ECU '{ecu}'.")
            ecus = sorted(set(e["ecu"] for e in entries))
            print(f"  Available: {', '.join(ecus)}")
            return

    if pid:
        pid_upper = pid.upper()
        filtered = [e for e in filtered if pid_upper in str(e["pid"]).upper()]
        if not filtered:
            print(f"  No captures found for PID '{pid}'" + (f" on ECU '{ecu}'" if ecu else "") + ".")
            return

    # Title
    parts = []
    if ecu:
        parts.append(ecu)
    if pid:
        parts.append(f"PID {pid.upper()}")
    title = " ".join(parts) if parts else "All"

    print(f"\n  {_BOLD}{title}{_RESET} — {len(filtered)} captures\n")

    show_ecu = not ecu  # Show ECU column when not filtering by ECU
    for e in filtered:
        _print_entry(e, show_ecu=show_ecu)
    print()


# ---------------------------------------------------------------------------
# ECU mode (kept for backward compat, delegates to cmd_filter)
# ---------------------------------------------------------------------------

def cmd_ecu(entries: list[dict], ecu_filter: str) -> None:
    """Show all captures for an ECU."""
    cmd_filter(entries, ecu=ecu_filter)


# ---------------------------------------------------------------------------
# PID mode (kept for backward compat, delegates to cmd_filter)
# ---------------------------------------------------------------------------

def cmd_pid(entries: list[dict], pid_filter: str) -> None:
    """Show all captures for a PID."""
    cmd_filter(entries, pid=pid_filter)


# ---------------------------------------------------------------------------
# Latest mode
# ---------------------------------------------------------------------------

def cmd_latest(entries: list[dict], ecu_filter: str | None) -> None:
    """Show latest payload per ECU+PID."""
    if ecu_filter:
        ecu_upper = ecu_filter.upper()
        filtered = [e for e in entries if e["ecu"].upper() == ecu_upper]
    else:
        filtered = entries

    # Only payloads (not scan_results or text responses)
    payload_entries = [e for e in filtered if e["payload"]]

    if not payload_entries:
        print("  No payload captures found.")
        return

    # Group by ECU+PID, keep latest (last in list = most recent date/position)
    latest: dict[tuple[str, str], dict] = {}
    for e in payload_entries:
        key = (e["ecu"], e["pid"])
        latest[key] = e

    title = f"Latest payloads" + (f" for {ecu_filter}" if ecu_filter else "")
    print(f"\n  {_BOLD}{title}{_RESET} — {len(latest)} PIDs\n")

    for (ecu, pid), e in sorted(latest.items()):
        payload = e["payload"]
        date = e["date"]
        state = f"  ({e['state']})" if e["state"] else ""
        trunc = payload[:80] + "..." if len(payload) > 80 else payload
        print(f"  {_CYAN}{ecu:<10}{_RESET} {pid:<10} {_DIM}{date}{state}{_RESET}")
        print(f"    {trunc}")
        decoded = _decoded_preview(e)
        if decoded:
            for k, v in list(decoded.items())[:5]:
                print(f"    {_DIM}{k}: {v}{_RESET}")
    print()


# ---------------------------------------------------------------------------
# Diff mode
# ---------------------------------------------------------------------------

def cmd_diff(entries: list[dict], ecu_filter: str, pid_filter: str) -> None:
    """Show all payloads for ECU+PID with byte-level diff colorization."""
    ecu_upper = ecu_filter.upper()
    pid_upper = pid_filter.upper()

    filtered = [
        e for e in entries
        if e["ecu"].upper() == ecu_upper
        and pid_upper in e["pid"].upper()
        and e["payload"]
    ]

    if not filtered:
        print(f"  No payloads found for {ecu_filter} {pid_filter}.")
        return

    print(f"\n  {_BOLD}Diff: {ecu_filter} {pid_filter}{_RESET} — {len(filtered)} payloads\n")

    prev_hex = None
    for e in filtered:
        payload = e["payload"].upper()
        date = e["date"]
        time_str = e.get("time", "")
        state = f"  ({e['state']})" if e["state"] else ""
        ts = f"{date} {time_str}".strip()

        print(f"  {_DIM}{ts}{state}{_RESET}")

        if prev_hex is None:
            # First payload — print normally
            print(f"    {_format_hex_spaced(payload)}")
        else:
            # Colorize changed bytes
            print(f"    {_diff_hex(prev_hex, payload)}")

        decoded = _decoded_preview(e)
        if decoded:
            for k, v in list(decoded.items())[:5]:
                print(f"    {_DIM}{k}: {v}{_RESET}")

        prev_hex = payload

    print()


def _format_hex_spaced(hex_str: str) -> str:
    """Format hex string with spaces between bytes."""
    return " ".join(hex_str[i:i+2] for i in range(0, len(hex_str), 2))


def _diff_hex(prev: str, curr: str) -> str:
    """Colorize hex diff — green for unchanged, red for changed bytes."""
    prev = prev.upper()
    curr = curr.upper()

    parts = []
    max_len = max(len(prev), len(curr))
    for i in range(0, max_len, 2):
        prev_byte = prev[i:i+2] if i + 1 < len(prev) else "  "
        curr_byte = curr[i:i+2] if i + 1 < len(curr) else "  "

        if i >= len(prev):
            # New byte (payload grew)
            parts.append(f"{_YELLOW}{curr_byte}{_RESET}")
        elif i >= len(curr):
            # Missing byte (payload shrank)
            parts.append(f"{_RED}--{_RESET}")
        elif prev_byte != curr_byte:
            parts.append(f"{_RED}{curr_byte}{_RESET}")
        else:
            parts.append(f"{_DIM}{curr_byte}{_RESET}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_entry(e: dict, show_ecu: bool = False) -> None:
    """Print a single capture entry."""
    ecu_prefix = f"{_CYAN}{e['ecu']:<10}{_RESET} " if show_ecu else ""
    date = e["date"]
    time_str = e.get("time", "")
    ts = f"{date} {time_str}".strip()
    state = f"  ({e['state']})" if e["state"] else ""
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

def main():
    parser = argparse.ArgumentParser(
        description="Query captured UDS payloads.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # --ecu and --pid can be used independently or together
    parser.add_argument("--ecu", "-e", metavar="ECU", help="Filter by ECU name")
    parser.add_argument("--pid", "-p", metavar="PID", help="Filter by PID/DID")

    # These are mutually exclusive with each other (and with --ecu/--pid)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--summary", "-s", action="store_true", help="Overview statistics")
    group.add_argument(
        "--latest", "-l", nargs="?", const="", metavar="ECU",
        help="Latest payload per PID (optionally filtered by ECU)",
    )
    group.add_argument(
        "--diff", "-d", nargs=2, metavar=("ECU", "PID"),
        help="Byte-level diff for ECU+PID payloads",
    )

    parser.add_argument(
        "--dir", type=Path, default=CAPTURES_DIR,
        help=f"Captures directory (default: {CAPTURES_DIR})",
    )

    args = parser.parse_args()

    # Require at least one mode
    if not any([args.ecu, args.pid, args.summary, args.latest is not None, args.diff]):
        parser.error("at least one of --ecu, --pid, --summary, --latest, --diff is required")

    # --summary/--latest/--diff conflict with --ecu/--pid
    if (args.summary or args.diff) and (args.ecu or args.pid):
        parser.error("--summary and --diff cannot be combined with --ecu/--pid")

    entries = load_all_captures(args.dir)

    if not entries:
        print("  No capture files found.")
        sys.exit(1)

    if args.summary:
        cmd_summary(entries)
    elif args.diff:
        cmd_diff(entries, args.diff[0], args.diff[1])
    elif args.latest is not None:
        cmd_latest(entries, args.latest or args.ecu or None)
    elif args.ecu or args.pid:
        cmd_filter(entries, ecu=args.ecu, pid=args.pid)


if __name__ == "__main__":
    main()
