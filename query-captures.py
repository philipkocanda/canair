#!/usr/bin/env python3
"""Query captured UDS payloads across all capture files.

Modes:
  --ecu ECU --pid PID   Captures for a specific ECU+PID combination (recommended)
  --ecu ECU             All captures for an ECU
  --pid PID             All captures for a PID (across all ECUs)
  --summary             Overview: captures per ECU, per date, total payloads
  --latest [ECU]        Most recent payload per PID (optionally filtered by ECU)
  --diff ECU PID        canreq monitor-style view: decoded params + colored
                        byte-diff hex (unique payloads only; --all for every one)
  --step ECU PID        Interactive: step through captures one at a time with
                        arrow keys, decoded params + byte-diff vs previous
                        capture; e adds/edits a note, d deletes a capture

Examples:
  python3 query-captures.py --ecu IGPM --pid 22BC03   # ECU+PID (most useful)
  python3 query-captures.py --ecu BMS                 # All BMS captures
  python3 query-captures.py --summary                 # Overview stats
  python3 query-captures.py --latest BMS              # Latest payload per BMS PID
  python3 query-captures.py --diff VCU 2101           # Params + colored byte-diff
  python3 query-captures.py --diff VCU 2101 --all     # ...show every payload
  python3 query-captures.py --step VCU 2101           # Interactive step-through
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

    Plus internal locator keys (``_session_idx``, ``_capture_idx``) that address
    the capture within its source file, for in-place edits/deletes.
    """
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
            state = session.get("state", "")
            for c_idx, cap in enumerate(session.get("captures", [])):
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
                    "_session_idx": s_idx,
                    "_capture_idx": c_idx,
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

    for (ecu, pid), e in sorted(latest.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        payload = e["payload"]
        date = e["date"]
        state = f"  ({e['state']})" if e["state"] else ""
        trunc = payload[:80] + "..." if len(payload) > 80 else payload
        print(f"  {_CYAN}{ecu:<10}{_RESET} {str(pid):<10} {_DIM}{date}{state}{_RESET}")
        print(f"    {trunc}")
        decoded = _decoded_preview(e)
        if decoded:
            for k, v in list(decoded.items())[:5]:
                print(f"    {_DIM}{k}: {v}{_RESET}")
    print()


# ---------------------------------------------------------------------------
# Diff mode
# ---------------------------------------------------------------------------

def _gather_payloads(
    entries: list[dict], ecu_filter: str, pid_filter: str
) -> tuple[list[dict], dict, int | None]:
    """Collect + chronologically sort payloads for an ECU+PID, plus PID defs.

    Returns ``(payloads, parameters, tx_id)``. ``payloads`` is an empty list when
    nothing matches (callers report the error). Exact ECU+PID match is preferred
    (needed for correct PID-definition lookup); falls back to a substring PID
    match so nothing silently disappears (those render without params).
    """
    ecu_upper = ecu_filter.upper()
    pid_upper = pid_filter.upper()

    payloads = [
        e for e in entries
        if e["ecu"].upper() == ecu_upper and str(e["pid"]).upper() == pid_upper and e["payload"]
    ]
    if not payloads:
        payloads = [
            e for e in entries
            if e["ecu"].upper() == ecu_upper and pid_upper in str(e["pid"]).upper() and e["payload"]
        ]

    # Chronological order (date, then time within a session).
    payloads.sort(key=lambda e: (str(e.get("date", "")), str(e.get("time", ""))))

    # Look up PID definitions for decoding + byte colouring.
    parameters: dict = {}
    tx_id = None
    try:
        from canlib.pids import build_ecu_index, load_pids

        idx = build_ecu_index(load_pids(PIDS_DIR))
        if ecu_upper in idx:
            tx_id = idx[ecu_upper].get("tx_id")
            pid_info = idx[ecu_upper]["pids"].get(pid_upper)
            if pid_info:
                parameters = pid_info.get("parameters", {}) or {}
    except Exception:
        pass

    return payloads, parameters, tx_id


def _dedupe_payloads(payloads: list[dict]) -> list[dict]:
    """Return payloads with duplicate hex removed (case/space-insensitive, first-seen)."""
    seen: set[str] = set()
    unique: list[dict] = []
    for e in payloads:
        norm = e["payload"].upper().replace(" ", "")
        if norm not in seen:
            seen.add(norm)
            unique.append(e)
    return unique


def cmd_diff(entries: list[dict], ecu_filter: str, pid_filter: str, show_all: bool = False) -> None:
    """Show payloads for an ECU+PID in canreq monitor style.

    Renders an ``ECU (0xTXID)`` / ``PID (N entries)`` header, a decoded-parameter
    block (from the most recent payload) with verification marks, then the payload
    hex lines with per-byte change highlighting (bytes differing from the previous
    line get a background colour) plus base colouring by parameter coverage.

    By default only *unique* payloads are shown (deduped, first-seen timestamp);
    pass ``show_all=True`` to render every capture.
    """
    from rich.console import Console

    from canlib.decoding import decode_param_rows
    from canlib.formatting import _render_hex_line, render_byte_rulers, render_param_table

    console = Console(highlight=False)

    pid_upper = pid_filter.upper()

    payloads, parameters, tx_id = _gather_payloads(entries, ecu_filter, pid_filter)
    if not payloads:
        print(f"  No payloads found for {ecu_filter} {pid_filter}.")
        return

    # Decode the most recent payload into param rows (drives the table + colours).
    rows = decode_param_rows(payloads[-1]["payload"], parameters)
    unmapped = not rows
    n_bytes = len(payloads[-1]["payload"].replace(" ", "")) // 2

    # Dedupe payloads (case/space-insensitive), keeping first-seen order.
    unique = _dedupe_payloads(payloads)

    total = len(payloads)
    n_unique = len(unique)
    if total == n_unique or show_all:
        count_str = f"({total} entries)"
    else:
        count_str = f"({total} entries, {n_unique} unique)"

    # ECU + PID headers.
    ecu_display = payloads[0]["ecu"] or ecu_filter
    tx_str = f" (0x{tx_id:03X})" if isinstance(tx_id, int) else ""
    console.print(f"\n  [bold cyan]{ecu_display}{tx_str}[/bold cyan]")
    console.print(f"    [yellow]{pid_upper}[/yellow]  [dim]{count_str}[/dim]")

    # Decoded-parameter block (aligned columns, verification marks, byte indices).
    if rows:
        console.print(render_param_table(rows, n_bytes=n_bytes), end="")

    # Payload hex lines with per-byte change highlighting, under a byte-index ruler.
    render_list = payloads if show_all else unique
    max_ts = max((len(e.get("time") or e.get("date") or "") for e in render_list), default=0)

    # Byte-index ruler (once), aligned with the hex byte columns below.
    # Two rows: "idx" = payload byte position, "wican" = WiCAN Bnn (skips PCI).
    if n_bytes:
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

    console.print()


# ---------------------------------------------------------------------------
# Step mode (interactive)
# ---------------------------------------------------------------------------

def _read_key(fd: int) -> str:
    """Read a single keypress (or escape sequence) from a raw/cbreak stdin."""
    import os

    ch = os.read(fd, 16).decode("utf-8", errors="ignore")
    return ch


def _render_step_frame(
    console,
    payloads: list[dict],
    i: int,
    parameters: dict,
    tx_id: int | None,
    pid_upper: str,
    status: str = "",
    prompt: str | None = None,
) -> None:
    """Render one capture full-screen: header, decoded params, ruler, diff hex.

    Shows the *current* capture's decoded parameter table and, underneath, the
    previous capture's payload hex (dimmed, for reference) followed by the current
    payload with per-byte change highlighting against that previous capture — the
    "diff, current capture only" view.

    ``prompt`` (when set) replaces the status line with a bold input prompt, used
    by the note-edit and delete-confirm sub-loops.
    """
    from rich.markup import escape

    from canlib.decoding import decode_param_rows
    from canlib.formatting import _render_hex_line, render_byte_rulers, render_param_table

    e = payloads[i]
    prev = payloads[i - 1] if i > 0 else None

    norm = e["payload"].upper().replace(" ", "")
    prev_norm = prev["payload"].upper().replace(" ", "") if prev else ""
    n_bytes = len(norm) // 2

    # Decode the *current* capture (drives the table + byte colours for this frame).
    rows = decode_param_rows(e["payload"], parameters)
    unmapped = not rows

    # Header: ECU / PID + position, timestamp, state, label, file.
    ecu_display = escape(e["ecu"])
    tx_str = f" (0x{tx_id:03X})" if isinstance(tx_id, int) else ""
    ts = e.get("time") or e.get("date") or ""
    state = f"  state={escape(e['state'])}" if e.get("state") else ""
    label = f"  [{escape(e['label'])}]" if e.get("label") else ""
    file_str = f"  ({escape(e['file'])})" if e.get("file") else ""

    console.print(f"\n  [bold cyan]{ecu_display}{tx_str}[/bold cyan]")
    console.print(
        f"    [yellow]{escape(pid_upper)}[/yellow]  "
        f"[dim]capture {i + 1}/{len(payloads)}[/dim]"
    )
    console.print(
        f"    [bold]{escape(ts)}[/bold][dim]{state}{label}{file_str}[/dim]"
    )

    # Capture note (if any).
    note = (e.get("notes") or "").strip()
    if note:
        console.print(f"    [dim]note:[/dim] {escape(note)}")

    # Decoded-parameter block (aligned columns, verification marks, byte indices).
    if rows:
        console.print(render_param_table(rows, n_bytes=n_bytes), end="")

    # Byte-index ruler, aligned with the hex byte columns below.
    max_ts = len(ts)
    if n_bytes:
        console.print(
            render_byte_rulers(n_bytes, rows, prefix_width=8 + max_ts), end="", soft_wrap=True
        )

    # Previous capture (dimmed, no highlight) for visual reference, then the
    # current capture with per-byte change highlighting against it.
    if prev is not None:
        prev_ts = prev.get("time") or prev.get("date") or ""
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
        "\n  [dim]←/h/p prev   →/l/n/space next   g/G first/last   "
        "e note   d delete   q quit[/dim]"
    )
    if prompt is not None:
        console.print(f"  [bold yellow]{escape(prompt)}[/bold yellow]")
    elif status:
        console.print(f"  [yellow]{escape(status)}[/yellow]")


def cmd_step(
    entries: list[dict],
    ecu_filter: str,
    pid_filter: str,
    show_all: bool = False,
    captures_dir: Path = CAPTURES_DIR,
) -> None:
    """Interactively step through captures for an ECU+PID, one at a time.

    Arrow keys (or vim ``h``/``l``) move between captures. Each frame shows the
    decoded parameter values for the current capture plus a byte-diff hex view
    (current payload highlighted against the previous capture) — the same
    rendering as ``--diff`` but focused on a single capture at a time.

    ``e`` edits/adds the current capture's note; ``d`` deletes the current
    capture (after a y/N confirmation). Both mutate the source YAML file and
    reload in place.

    Steps through *unique* payloads by default; ``show_all=True`` walks every
    capture (including duplicates). Falls back to ``cmd_diff`` when stdin/stdout
    is not an interactive terminal.
    """
    import sys

    from rich.console import Console

    from canlib.captures import delete_capture, set_capture_note

    pid_upper = pid_filter.upper()

    def build_list(src: list[dict]):
        pls, params, tx = _gather_payloads(src, ecu_filter, pid_filter)
        if not show_all:
            pls = _dedupe_payloads(pls)
        return pls, params, tx

    payloads, parameters, tx_id = build_list(entries)
    if not payloads:
        print(f"  No payloads found for {ecu_filter} {pid_filter}.")
        return

    # Non-interactive (piped) — fall back to the static diff view.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("  (not a TTY — falling back to --diff view)")
        cmd_diff(entries, ecu_filter, pid_filter, show_all=show_all)
        return

    import termios
    import tty

    console = Console(highlight=False)
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    i = 0
    status = ""
    final_msg = ""

    def redraw(prompt: str | None = None) -> None:
        sys.stdout.write("\033[2J\033[H")  # clear + home
        _render_step_frame(
            console, payloads, i, parameters, tx_id, pid_upper, status=status, prompt=prompt
        )

    def reload() -> bool:
        """Re-read captures from disk and rebuild the payload list. False if empty."""
        nonlocal payloads, parameters, tx_id, i
        fresh = load_all_captures(captures_dir)
        payloads, parameters, tx_id = build_list(fresh)
        if not payloads:
            return False
        i = min(i, len(payloads) - 1)
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
                if i < len(payloads) - 1:
                    i += 1
                else:
                    status = "At last capture"
            elif key in ("\x1b[H", "g"):  # Home / g — first
                i = 0
            elif key in ("\x1b[F", "G"):  # End / G — last
                i = len(payloads) - 1
            elif key in ("e", "E"):  # edit / add note
                cap = payloads[i]
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
                            cap["_session_idx"], cap["_capture_idx"], buf,
                        )
                        saved = "Note saved" if buf.strip() else "Note cleared"
                        if not reload():
                            final_msg = saved + " — no captures left"
                            break
                        status = saved
                    except Exception as ex:
                        status = f"Note save failed: {ex}"
            elif key in ("d", "D"):  # delete current capture (confirmed)
                cap = payloads[i]
                redraw(prompt="Delete this capture? (y/N)")
                if _read_key(fd) in ("y", "Y"):
                    try:
                        delete_capture(
                            captures_dir / cap["file"],
                            cap["_session_idx"], cap["_capture_idx"],
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
        help="canreq monitor-style view for ECU+PID: decoded params + colored byte-diff",
    )
    group.add_argument(
        "--step", "-S", nargs=2, metavar=("ECU", "PID"),
        help="Interactively step through captures for ECU+PID (arrow keys; e=note, d=delete)",
    )

    parser.add_argument(
        "--all", "-a", action="store_true",
        help="For --diff/--step: use every payload instead of unique-only",
    )

    parser.add_argument(
        "--dir", type=Path, default=CAPTURES_DIR,
        help=f"Captures directory (default: {CAPTURES_DIR})",
    )

    args = parser.parse_args()

    # Require at least one mode
    if not any([args.ecu, args.pid, args.summary, args.latest is not None, args.diff, args.step]):
        parser.error("at least one of --ecu, --pid, --summary, --latest, --diff, --step is required")

    # --summary/--latest/--diff/--step conflict with --ecu/--pid
    if (args.summary or args.diff or args.step) and (args.ecu or args.pid):
        parser.error("--summary, --diff and --step cannot be combined with --ecu/--pid")

    entries = load_all_captures(args.dir)

    if not entries:
        print("  No capture files found.")
        sys.exit(1)

    if args.summary:
        cmd_summary(entries)
    elif args.diff:
        cmd_diff(entries, args.diff[0], args.diff[1], show_all=args.all)
    elif args.step:
        cmd_step(entries, args.step[0], args.step[1], show_all=args.all, captures_dir=args.dir)
    elif args.latest is not None:
        cmd_latest(entries, args.latest or args.ecu or None)
    elif args.ecu or args.pid:
        cmd_filter(entries, ecu=args.ecu, pid=args.pid)


if __name__ == "__main__":
    main()
