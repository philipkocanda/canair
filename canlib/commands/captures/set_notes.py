"""``canair captures uds --set-notes TEXT`` — correct a recorded session's notes.

Replaces the session-level ``notes`` on the scope-selected sessions — for a note
that turned out wrong or incomplete after recording, e.g. a claim the session's
own data contradicts. An empty ``TEXT`` clears the field. The scriptable
counterpart to the ``--step`` TUI's note editor, which is TTY-only.

Mirrors ``--set-state``: operate on the already scope-filtered entries, preview
before writing, confirm on a TTY unless ``--yes``, and never write on
``--dry-run``. The caller (``run``) refuses a bare invocation with no scope
filter, so this can never overwrite every session's notes at once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from canlib.capture_types import CaptureEntry

_PREVIEW_WIDTH = 60


def cmd_set_notes(
    entries: Sequence[CaptureEntry],
    notes_arg: str,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
    as_json: bool = False,
) -> int:
    """Set the session ``notes`` on every scope-selected session to ``notes_arg``."""
    from canlib.captures import set_session_note

    from .query import group_sessions
    from .session_gate import refuse_write

    notes = notes_arg.strip()
    rows: list[dict] = [
        {
            "file": g["file"],
            "path": g["path"],
            "session_idx": g["session_idx"],
            "date": g["date"],
            "label": g["label"],
            "times": g["times"],
            "recorded": g.get("notes") or "",
            "new_notes": notes,
            "will_write": (g.get("notes") or "") != notes,
        }
        for g in group_sessions(entries)
    ]
    to_write = [r for r in rows if r["will_write"]]

    if as_json and dry_run:
        print(json.dumps(rows, indent=2))
        return 0

    _print_report(rows, notes)

    if not to_write:
        if as_json:
            print(json.dumps(rows, indent=2))
        return 0

    if dry_run:
        print("  (--dry-run: nothing written)")
        return 0

    action = "Set notes on" if notes else "Clear the notes of"
    stop = refuse_write(
        (Path(r["path"]) for r in to_write),
        verb="re-noted",
        prompt=f"{action} these {len(to_write)} session(s)?",
        what="set notes",
        assume_yes=assume_yes,
    )
    if stop is not None:
        return stop

    written = 0
    for r in to_write:
        try:
            set_session_note(Path(r["path"]), r["session_idx"], notes)
            written += 1
            print(f"    \u2192 {r['file']} [{r['session_idx']}] {r['date']}  {r['label']}")
        except Exception as ex:  # keep going; report the failure
            print(f"    ! {r['file']} [{r['session_idx']}]: {ex}")

    print(f"  {'Set' if notes else 'Cleared'} notes on {written} session(s).")
    if as_json:
        print(json.dumps(rows, indent=2))
    return 0 if written == len(to_write) else 1


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _PREVIEW_WIDTH else text[: _PREVIEW_WIDTH - 1] + "\u2026"


def _print_report(rows: list[dict], notes: str) -> None:
    n_write = sum(1 for r in rows if r["will_write"])
    target = f"notes = {_clip(notes)!r}" if notes else "cleared notes"
    print(f"  Setting {target} on {n_write} of {len(rows)} matched session(s):\n")
    for r in rows:
        mark = "*" if r["will_write"] else " "
        span = r["times"][0][:8] if r["times"] else "--:--:--"
        label = (r["label"] or "")[:40]
        note = "" if r["will_write"] else "  (already)"
        print(f"  {mark} {r['date']} {span}  {label}{note}")
        if r["will_write"]:
            print(f"      was: {_clip(r['recorded']) or '(none)'}")
    print()
