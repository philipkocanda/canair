"""Remove stored replies to sensitive PIDs from capture files.

Nothing writes one (:mod:`canair.privacy.sensitive`), but a PID can be marked sensitive after
it was recorded, and an older checkout or a hand-copied file can carry one in. This is
the one rewrite that takes them out, shared by ``canair captures purge-sensitive``,
``contribute`` (which strips its staged copy whatever the source holds) and the
blind-test strip.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from canair.captures import io as capture_io
from canair.privacy.sensitive import SensitiveSet


@dataclass(frozen=True)
class PurgeResult:
    """What a purge removed (or would remove) from one capture file."""

    path: Path
    removed: int  # capture rows
    sessions_dropped: int  # sessions left with no capture at all


def strip_doc(doc: Any, sset: SensitiveSet) -> tuple[Any, int, int]:
    """``doc`` without its sensitive rows: ``(doc, rows removed, sessions dropped)``.

    A session whose every capture was sensitive is dropped with them: it would be
    an empty recording. The document is returned unchanged (the same object) when
    nothing matched, so a caller can tell a no-op from a rewrite.
    """
    if not sset or not isinstance(doc, dict) or not isinstance(doc.get("sessions"), list):
        return doc, 0, 0
    removed = dropped = 0
    sessions = []
    for session in doc["sessions"]:
        caps = session.get("captures") if isinstance(session, dict) else None
        if not isinstance(caps, list):
            sessions.append(session)
            continue
        kept = [c for c in caps if not (isinstance(c, dict) and sset.has_capture(c))]
        if len(kept) == len(caps):
            sessions.append(session)
            continue
        removed += len(caps) - len(kept)
        if kept:
            sessions.append({**session, "captures": kept})
        else:
            dropped += 1
    if not removed:
        return doc, 0, 0
    return {**doc, "sessions": sessions}, removed, dropped


def purge_file(path: Path, sset: SensitiveSet, *, dry_run: bool = False) -> PurgeResult:
    doc = capture_io.load_capture_file(path)
    new, removed, dropped = strip_doc(doc, sset)
    if removed and not dry_run:
        capture_io.dump_capture_file(path, new)
    return PurgeResult(path, removed, dropped)


def purge_dir(
    captures_dir: Path, sset: SensitiveSet, *, dry_run: bool = False
) -> list[PurgeResult]:
    """Purge every dated capture file in ``captures_dir``; only files that held one."""
    if not sset or not captures_dir.is_dir():
        return []
    results = [
        purge_file(p, sset, dry_run=dry_run) for p in capture_io.iter_capture_files(captures_dir)
    ]
    return [r for r in results if r.removed]


def sensitive_findings(path: Path, sset: SensitiveSet) -> list[str]:
    """One ``validate captures`` error per stored reply to a sensitive PID in ``path``."""
    if not sset:
        return []
    try:
        doc = capture_io.load_capture_file(path)
    except (OSError, ValueError):
        return []  # the file's own errors are reported by the schema check
    found: list[str] = []
    for si, session in enumerate((doc or {}).get("sessions") or []):
        caps = session.get("captures") if isinstance(session, dict) else None
        for ci, cap in enumerate(caps or []):
            if isinstance(cap, dict) and sset.has_capture(cap):
                found.append(
                    f"sessions[{si}].captures[{ci}]: a reply to sensitive PID {sset.label(cap)} "
                    "— remove it with `canair captures purge-sensitive`"
                )
    return found
