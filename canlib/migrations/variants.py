"""Stamp a variant tag onto existing, untagged capture sessions.

History recorded before a profile declared its variants carries no ``variant``, so
it is attributed to no car. ``canair captures migrate-variants ID`` stamps the
sessions a person knows came from one car — scoped by date and label, so history
from several cars is stamped in several runs — and records ``attribution: migrated``
so the tag's source stays visible. A session that already carries a variant is never
touched: a migration only adds information, it never overrides it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from canlib.captures import io as capture_io


@dataclass
class StampResult:
    """What a migration did (or would do) to one capture file."""

    path: Path
    # (session index, date, label) of every session stamped.
    sessions: list[tuple[int, str, str]] = field(default_factory=list)


def _in_scope(session: dict, since: date | None, until: date | None, label: str | None) -> bool:
    raw = str(session.get("date") or "")[:10]
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        day = None
    if (since or until) and day is None:
        return False
    if since and day and day < since:
        return False
    if until and day and day > until:
        return False
    if label and label.lower() not in str(session.get("label") or "").lower():
        return False
    return True


def stamp_untagged(
    files: list[Path],
    variant: str,
    *,
    since: date | None = None,
    until: date | None = None,
    label: str | None = None,
    dry_run: bool = False,
) -> list[StampResult]:
    """Tag every untagged session in scope with ``variant``; one result per changed file."""
    from canlib.captures import _set_session_field

    results: list[StampResult] = []
    for path in files:
        data = capture_io.load_capture_file(path)
        sessions = (data or {}).get("sessions") or []
        result = StampResult(path)
        for idx, session in enumerate(sessions):
            if session.get("variant") or not _in_scope(dict(session), since, until, label):
                continue
            _set_session_field(session, "variant", variant)
            attribution = dict(session.get("attribution") or {})
            attribution["variant"] = "migrated"
            _set_session_field(session, "attribution", attribution)
            result.sessions.append(
                (idx, str(session.get("date") or ""), str(session.get("label") or ""))
            )
        if result.sessions:
            if not dry_run:
                capture_io.dump_capture_file(path, data)
            results.append(result)
    return results
