"""Capture-level views: the default list (``QUERY``) and ``--latest``.

Both render individual capture entries — timestamp, state, label, payload and a
decoded preview — as opposed to the metadata aggregates in :mod:`sessions` or the
byte-diff blocks in :mod:`diff`. The shared per-entry printers live here because
these two views are their only callers.
"""

import sys
from collections.abc import Sequence

from canair.captures.entries import entry_to_dict
from canair.captures.store import decoded_preview
from canair.captures.types import CaptureEntry
from canair.cli.args.query import parse_canonical_query
from canair.cli.profile_context import active
from canair.core.terminal import terminal_columns, truncate_text
from canair.render import ansi, glyphs, messages
from canair.states.tokens import join_states as _join_states

from .query import _dump_json


def _text_width(prefix: str) -> int:
    """The room left on a terminal line after ``prefix``, never under 40 characters."""
    return max(40, terminal_columns() - len(prefix))


def cmd_list(entries: Sequence[CaptureEntry], query, as_json: bool = False, limit: int = 0) -> None:
    """List captures matching ``query`` (canair.query selection).

    The default view: unlike --diff/--step (payload-only), this lists *every*
    matching entry — payloads, text responses and scan results alike — with
    timestamps, state, notes and a decoded preview where a PID definition exists.
    Selectors that matched nothing are reported (with the available ECUs).

    ``limit`` caps the human view to the most recent ``limit`` captures (0 = no
    cap). Truncation is reported loudly (in both text and JSON) so hidden history
    is never silent — the full set is still available with ``--limit 0`` or a
    tighter scope (``--since``/``--last-session``/…).
    """
    q = parse_canonical_query(query)
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
                "captures": [entry_to_dict(e, profile=active()) for e in shown],
            }
        )
        return

    if empty:
        known = {e["ecu"].upper() for e in entries}
        for sel in empty:
            hint = ""
            if not sel.pids and sel.ecu not in known and any(c.isdigit() for c in sel.ecu):
                hint = "  (did you mean to attach it as a PID, e.g. ECU:PID?)"
            warn = f"{glyphs.WARN} No captures matched selector '{sel}'"
            print(f"  {ansi.YELLOW}{warn}{ansi.RESET}{hint}")
        print(f"  {ansi.DIM}Available ECUs: {', '.join(sorted(known))}{ansi.RESET}")

    if not matched:
        return

    header = f"{total} captures"
    if truncated:
        header = f"latest {len(shown)} of {total} captures"
    print(f"\n  {ansi.BOLD}{q}{ansi.RESET} — {header}\n")

    # Show the ECU column only when the results span more than one ECU.
    show_ecu = len({e["ecu"] for e in shown}) > 1
    for e in shown:
        _print_entry(e, show_ecu=show_ecu)
    print()
    if truncated:
        # Loud, always-printed (not TTY-gated) so an agent sees hidden history.
        print(
            f"  {ansi.YELLOW}… {total - len(shown)} more not shown{ansi.RESET} "
            f"(showing latest {len(shown)} of {total}). "
            f"Use {ansi.BOLD}--limit 0{ansi.RESET} for all, or narrow with "
            f"--since/--last-session/--state.\n"
        )
    if sys.stdout.isatty():
        messages.hint("add --step to step through these captures one at a time")


# ---------------------------------------------------------------------------
# Latest mode
# ---------------------------------------------------------------------------


def cmd_latest(entries: Sequence[CaptureEntry], as_json: bool = False) -> None:
    """Show the most recent payload per ECU+PID.

    ECU/PID selection is the caller's job: ``uds.run`` narrows ``entries`` with the
    QUERY before calling, so ``BMS --latest`` and a bare ``--latest`` differ only in
    what they pass here.
    """
    # Only payloads (not scan_results or text responses)
    payload_entries = [e for e in entries if e["payload"]]

    if not payload_entries:
        if as_json:
            _dump_json([])
            return
        print("  No payload captures found.")
        return

    # Group by ECU+PID, keep latest (last in list = most recent date/position)
    latest: dict[tuple[str, str | int], CaptureEntry] = {}
    for e in payload_entries:
        key = (e["ecu"], e["pid"])
        latest[key] = e

    ordered = sorted(latest.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))

    if as_json:
        _dump_json([entry_to_dict(e, profile=active()) for _key, e in ordered])
        return

    print(f"\n  {ansi.BOLD}Latest payloads{ansi.RESET} — {len(latest)} PIDs\n")

    for (ecu, pid), e in ordered:
        payload = e["payload"] or ""
        date = e["date"]
        _st = _join_states(e.get("vehicle_states"))
        state = f"  ({_st})" if _st else ""
        trunc = truncate_text(payload, _text_width("  "))
        print(f"  {ansi.CYAN}{ecu:<10}{ansi.RESET} {pid!s:<10} {ansi.DIM}{date}{state}{ansi.RESET}")
        print(f"    {trunc}")
        decoded = decoded_preview(e, profile=active())
        if decoded:
            _print_decoded_preview(decoded, limit=5, ecu=str(ecu), pid=str(pid))
    print()


# ---------------------------------------------------------------------------
# Shared per-entry printers
# ---------------------------------------------------------------------------


def _print_decoded_preview(
    decoded: dict, *, limit: int, ecu: str = "", pid: str = "", indent: str = "    "
) -> None:
    """Print decoded signals capped at ``limit``, with a visible hint for any hidden.

    The capture views show only a preview of a PID's decoded parameters to stay
    compact; without a marker a busy PID silently drops signals (e.g. a mode/enum
    defined late in the file). Emit a "+N more" line so the cap is never silent,
    pointing at ``canair decode`` (which renders every param, enum labels and all).
    """
    items = list(decoded.items())
    for k, v in items[:limit]:
        print(f"{indent}{ansi.DIM}{k}: {v}{ansi.RESET}")
    hidden = len(items) - limit
    if hidden > 0:
        where = (
            f"canair decode {ecu} {pid}".strip() if (ecu and pid) else "canair decode <ECU> <PID>"
        )
        print(
            f"{indent}{ansi.DIM}… +{hidden} more signal(s) not shown — "
            f"`{where}` for all{ansi.RESET}"
        )


def _print_entry(e: dict, show_ecu: bool = False) -> None:
    """Print a single capture entry."""
    ecu_prefix = f"{ansi.CYAN}{e['ecu']:<10}{ansi.RESET} " if show_ecu else ""
    date = e["date"]
    time_str = e.get("time", "")
    ts = f"{date} {time_str}".strip()
    _st = _join_states(e.get("vehicle_states"))
    state = f"  ({_st})" if _st else ""
    label = f"  [{e['label']}]" if e.get("label") else ""

    print(f"  {ecu_prefix}{ansi.DIM}{ts}{state}{label}{ansi.RESET}")
    print(f"    PID: {e['pid']}")

    if e["payload"]:
        print(f"    Payload: {truncate_text(e['payload'], _text_width('    Payload: '))}")
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

    decoded = decoded_preview(e, profile=active())
    if decoded:
        _print_decoded_preview(
            decoded, limit=3, ecu=str(e.get("ecu", "")), pid=str(e.get("pid", ""))
        )

    if e.get("notes"):
        notes_str = truncate_text(str(e["notes"]).strip(), _text_width("    Notes: "))
        print(f"    {ansi.DIM}Notes: {notes_str}{ansi.RESET}")
