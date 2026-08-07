"""The ``--delete`` mode: remove the captures a QUERY selects.

A QUERY-driven mutating mode, like :mod:`backfill` and :mod:`set_state`: operate
on the scope-filtered entries, report first, confirm on a TTY unless ``--yes``,
write nothing on ``--dry-run``. Deletes are addressed by each entry's
``_session_idx``/``_capture_idx`` locators and applied in reverse order per file
so earlier indices stay valid.
"""

import sys
from collections.abc import Sequence
from pathlib import Path

from canlib import ansi
from canlib.capture_io import resolve_captures_dir
from canlib.capture_types import CaptureEntry

from .query import _parse_query


def cmd_delete(
    entries: Sequence[CaptureEntry],
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

    cdir = resolve_captures_dir(captures_dir)

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
            f"    {ansi.DIM}{e.get('file', '?')}{ansi.RESET} "
            f"{e.get('ecu', '?')} {e.get('pid', '?')} @ {e.get('date', '?')} "
            f"{e.get('time', '') or '(no time)'}  {ansi.DIM}{e.get('payload', '') or ''}{ansi.RESET}"
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
