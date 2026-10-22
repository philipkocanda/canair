"""``captures uds --set-notes TEXT`` / ``--set-transport NAME`` — correct session metadata.

Replaces one free-text session field on the scope-selected sessions, for a value
that turned out wrong after recording:

- ``notes`` — a note the session's own data contradicts. The scriptable
  counterpart to the ``--step`` TUI's note editor, which is TTY-only.
- ``transport`` — the acquisition provenance, which the one-shot
  discover/scan/raw saves once recorded from configuration rather than from the
  live client, mislabelling sessions run with ``--transport``/``--wican`` or after
  a fallback. Only a known transport label is accepted.

An empty value clears the field. Mirrors ``--set-state``: operate on the already
scope-filtered entries, preview before writing, confirm on a TTY unless
``--yes``, and never write on ``--dry-run``. The caller (``run``) refuses a bare
invocation with no scope filter, so this can never rewrite every session at once.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from collections.abc import Sequence

    from canlib.capture_types import CaptureEntry

_PREVIEW_WIDTH = 60


@dataclass(frozen=True)
class SessionField:
    """One editable session field: its key, its flag, and how to write it."""

    key: str
    flag: str
    write: Callable[[Path, int, str], None]
    allowed: Callable[[], Sequence[str]] | None = None  # None = free text


def _notes_field() -> SessionField:
    from canlib.captures import set_session_note

    return SessionField("notes", "--set-notes", set_session_note)


def known_transports() -> list[str]:
    """Every label a session's ``transport`` can legitimately carry."""
    from canlib.captures import IMPORT_TRANSPORT
    from canlib.transport.config import TRANSPORTS

    return [*TRANSPORTS, IMPORT_TRANSPORT]


def _transport_field() -> SessionField:
    from canlib.captures import set_session_transport

    return SessionField("transport", "--set-transport", set_session_transport, known_transports)


def cmd_set_notes(entries: Sequence[CaptureEntry], value: str, **kw) -> int:
    """Set the session ``notes`` on every scope-selected session to ``value``."""
    return cmd_set_field(entries, _notes_field(), value, **kw)


def cmd_set_transport(entries: Sequence[CaptureEntry], value: str, **kw) -> int:
    """Set the session ``transport`` on every scope-selected session to ``value``."""
    return cmd_set_field(entries, _transport_field(), value, **kw)


def cmd_set_field(
    entries: Sequence[CaptureEntry],
    field: SessionField,
    value: str,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
    as_json: bool = False,
) -> int:
    """Set ``field`` on every scope-selected session to ``value`` ('' clears it)."""
    from .query import group_sessions
    from .session_gate import refuse_write

    value = value.strip()
    if value and field.allowed is not None and value not in (allowed := field.allowed()):
        print(
            f"  error: {field.flag} {value!r} is not a known {field.key} "
            f"(one of: {', '.join(allowed)}; '' clears it).",
            file=sys.stderr,
        )
        return 2

    rows: list[dict] = [
        {
            "file": g["file"],
            "path": g["path"],
            "session_idx": g["session_idx"],
            "date": g["date"],
            "label": g["label"],
            "times": g["times"],
            "recorded": g.get(field.key) or "",
            "new": value,
            "will_write": (g.get(field.key) or "") != value,
        }
        for g in group_sessions(entries)
    ]
    to_write = [r for r in rows if r["will_write"]]

    if as_json and dry_run:
        print(json.dumps(rows, indent=2))
        return 0

    # Under --json stdout carries only the JSON; the human report goes to stderr.
    out = sys.stderr if as_json else sys.stdout
    _print_report(rows, field.key, value, out)

    if not to_write:
        if as_json:
            print(json.dumps(rows, indent=2))
        return 0

    if dry_run:
        print("  (--dry-run: nothing written)", file=out)
        return 0

    action = f"Set {field.key} on" if value else f"Clear the {field.key} of"
    stop = refuse_write(
        (Path(r["path"]) for r in to_write),
        verb="edited",
        prompt=f"{action} these {len(to_write)} session(s)?",
        what=f"set {field.key}",
        assume_yes=assume_yes,
        out=out,
    )
    if stop is not None:
        return stop

    written = 0
    for r in to_write:
        try:
            field.write(Path(r["path"]), r["session_idx"], value)
            written += 1
            print(
                f"    \u2192 {r['file']} [{r['session_idx']}] {r['date']}  {r['label']}", file=out
            )
        except Exception as ex:  # keep going; report the failure
            print(f"    ! {r['file']} [{r['session_idx']}]: {ex}", file=out)

    print(f"  {'Set' if value else 'Cleared'} {field.key} on {written} session(s).", file=out)
    if as_json:
        print(json.dumps(rows, indent=2))
    return 0 if written == len(to_write) else 1


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _PREVIEW_WIDTH else text[: _PREVIEW_WIDTH - 1] + "\u2026"


def _print_report(rows: list[dict], key: str, value: str, out: TextIO) -> None:
    n_write = sum(1 for r in rows if r["will_write"])
    target = f"{key} = {_clip(value)!r}" if value else f"cleared {key}"
    print(f"  Setting {target} on {n_write} of {len(rows)} matched session(s):\n", file=out)
    for r in rows:
        mark = "*" if r["will_write"] else " "
        span = r["times"][0][:8] if r["times"] else "--:--:--"
        label = (r["label"] or "")[:40]
        note = "" if r["will_write"] else "  (already)"
        print(f"  {mark} {r['date']} {span}  {label}{note}", file=out)
        if r["will_write"]:
            print(f"      was: {_clip(r['recorded']) or '(none)'}", file=out)
    print(file=out)
