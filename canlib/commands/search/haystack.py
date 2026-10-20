"""Load the rows ``canair search`` scans: payload bytes, free text, and ECU identity fields.

Capture entries repeat enormously — a session's label rides on every one of its
captures, and a polled PID stores the same payload thousands of times — so every
loader collapses to *distinct* values with a weight, before any pattern runs. That is
what keeps a corpus-wide scan at tens of milliseconds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .findings import PayloadRow, TextRow

FIELD_LABEL = "label"
FIELD_NOTES = "notes"
FIELD_RESPONSE = "response"
FIELD_SESSION_LABEL = "session label"
FIELD_SESSION_NOTES = "session notes"
FIELD_SCAN_NOTES = "scan notes"

# A scan's per-responder notes embed ``Raw: <hex…>`` truncated upstream, so bytes
# can never be reconstructed from them and a hit there is reported as lossy.
_LOSSY_PREFIX = "Raw:"


def hex_payload(payload: object) -> bytes | None:
    """Bytes of a hex ``payload`` string, or ``None`` for anything that is not hex.

    Legacy captures stored a human outcome (``"NO DATA"``) under ``payload``.
    """
    if not isinstance(payload, str):
        return None
    cleaned = payload.replace(" ", "")
    if not cleaned or len(cleaned) % 2:
        return None
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return None


class _Tally:
    """Weight and first/last-seen date for each distinct key."""

    def __init__(self) -> None:
        self._rows: dict[Any, list] = {}

    def add(self, key: Any, date: str, weight: int = 1) -> None:
        row = self._rows.get(key)
        if row is None:
            self._rows[key] = [weight, date, date]
            return
        row[0] += weight
        if date and (not row[1] or date < row[1]):
            row[1] = date
        if date and date > row[2]:
            row[2] = date

    def items(self) -> Iterable[tuple[Any, int, str, str]]:
        for key, (weight, first, last) in self._rows.items():
            yield key, weight, first, last


def payload_rows(entries: Iterable[Mapping[str, Any]]) -> list[PayloadRow]:
    """Distinct ``(ecu, pid, payload)`` rows of every hex payload capture."""
    tally = _Tally()
    for e in entries:
        data = hex_payload(e.get("payload"))
        if data is None:
            continue
        key = (str(e.get("ecu") or ""), str(e.get("pid") or ""), data)
        tally.add(key, str(e.get("date") or ""))
    return [
        PayloadRow(ecu, pid, data, weight, first, last)
        for (ecu, pid, data), weight, first, last in tally.items()
    ]


def _scan_notes(scan_results: Mapping[str, Any] | None) -> Iterable[str]:
    if not isinstance(scan_results, Mapping):
        return
    for key in ("notes", "rejected"):
        value = scan_results.get(key)
        if isinstance(value, str):
            yield value
    for responder in scan_results.get("responding") or []:
        if isinstance(responder, Mapping) and isinstance(responder.get("notes"), str):
            yield responder["notes"]


def text_rows(entries: Iterable[Mapping[str, Any]]) -> list[TextRow]:
    """Distinct free-text values: capture labels/notes/responses and session text.

    Session-level text is counted once per session, not once per capture in it.
    """
    tally = _Tally()
    seen_sessions: set[tuple[str, int]] = set()
    for e in entries:
        date = str(e.get("date") or "")
        ecu, pid = str(e.get("ecu") or ""), str(e.get("pid") or "")
        for field, value in (
            (FIELD_LABEL, e.get("label")),
            (FIELD_NOTES, e.get("notes")),
            (FIELD_RESPONSE, e.get("response")),
        ):
            if isinstance(value, str) and value.strip():
                tally.add((field, ecu, pid, value, False), date)
        for note in _scan_notes(e.get("scan_results")):
            if note.strip():
                lossy = note.startswith(_LOSSY_PREFIX)
                tally.add((FIELD_SCAN_NOTES, ecu, pid, note, lossy), date)
        session = (str(e.get("file") or ""), int(e.get("_session_idx") or 0))
        if session in seen_sessions:
            continue
        seen_sessions.add(session)
        for field, value in (
            (FIELD_SESSION_LABEL, e.get("session_label")),
            (FIELD_SESSION_NOTES, e.get("session_notes")),
        ):
            if isinstance(value, str) and value.strip():
                tally.add((field, "", "", value, False), date)
    return [
        TextRow(field, ecu, pid, text, weight, first, last, lossy)
        for (field, ecu, pid, text, lossy), weight, first, last in tally.items()
    ]


def identity_rows(
    ecus: Mapping[str, Any], *, only: Iterable[str] | None = None
) -> list[tuple[str, str, str]]:
    """``(ecu, field, value)`` for every scalar ``identity:`` field in the definitions."""
    wanted = {name.upper() for name in only} if only is not None else None
    out: list[tuple[str, str, str]] = []
    for name, ecu in ecus.items():
        if wanted is not None and str(name).upper() not in wanted:
            continue
        identity = ecu.get("identity") if isinstance(ecu, Mapping) else None
        if not isinstance(identity, Mapping):
            continue
        for field, value in identity.items():
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
                out.append((str(name), str(field), str(value)))
    return out
