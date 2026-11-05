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

from canair.captures.store import entry_path
from canair.captures.types import CaptureEntry
from canair.core.prompt import confirm
from canair.render import ansi, messages
from canair.render.paint import Painter

from .echo import echo_write
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

    from canair.captures import delete_capture, layers
    from canair.query import QueryError

    q = _parse_query(query)
    try:
        matched, _empty = q.filter(
            entries, ecu_of=lambda e: e["ecu"], pid_of=lambda e: str(e["pid"])
        )
    except QueryError as ex:
        messages.error(f"invalid query: {ex}")
        return 2

    if not matched:
        if as_json:
            print("[]")
            return 0
        messages.error(f"no captures match {query!r} in scope; nothing to delete")
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

    out = sys.stderr if as_json else sys.stdout
    paint = Painter(out)
    verb = "Would delete" if dry_run else "Deleting"
    print(f"  {verb} {len(matched)} capture(s) matching {query!r}:", file=out)
    for e in matched:
        print(
            f"    {paint(str(e.get('file', '?')), ansi.raw.DIM)} "
            f"{e.get('ecu', '?')} {e.get('pid', '?')} @ {e.get('date', '?')} "
            f"{e.get('time', '') or '(no time)'}  "
            f"{paint(str(e.get('payload', '') or ''), ansi.raw.DIM)}",
            file=out,
        )

    if dry_run:
        print("  (--dry-run: nothing deleted)", file=out)
        return 0

    if blocked := layers.refusal((entry_path(e, captures_dir) for e in matched), "deleted"):
        print(blocked, file=sys.stderr)
        return 1

    if not confirm(f"Delete these {len(matched)} capture(s)?", yes=assume_yes):
        return 1

    # Delete in reverse (file, session_idx, capture_idx) order so earlier indices
    # remain valid as we remove entries from each file.
    to_delete = sorted(
        matched,
        key=lambda e: (e["_path"], e["_session_idx"], e["_capture_idx"]),
        reverse=True,
    )
    deleted = 0
    for e in to_delete:
        try:
            target = entry_path(e, captures_dir)
            delete_capture(target, e["_session_idx"], e["_capture_idx"])
            deleted += 1
        except Exception as ex:  # keep going; report the failure
            messages.error(f"{e.get('file', '?')} {e.get('ecu', '?')} {e.get('pid', '?')}: {ex}")

    echo_write(f"Deleted {deleted} capture(s)", (entry_path(e, captures_dir) for e in matched), out)
    if as_json:
        print(_json.dumps([_row(e) for e in matched], indent=2))
    return 0 if deleted == len(matched) else 1
