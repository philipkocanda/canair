"""Load the rows ``canair search`` scans: payload bytes, free text, and ECU identity fields.

Capture entries repeat enormously — a session's label rides on every one of its
captures, and a polled PID stores the same payload thousands of times — so every
loader collapses to *distinct* values with a weight, before any pattern runs. That is
what keeps a corpus-wide scan at tens of milliseconds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .findings import FIELD_PAYLOAD, PayloadRow, TextRow

FIELD_LABEL = "label"
FIELD_NOTES = "notes"
FIELD_RESPONSE = "response"
FIELD_SESSION_LABEL = "session label"
FIELD_SESSION_NOTES = "session notes"
FIELD_SCAN_NOTES = "scan notes"

# A scan's per-responder notes carry the response as ``Raw: <hex>``: in full for a
# discover sweep, cut to 80 digits plus ``...`` for a DID range scan. Those are bytes,
# so they are searched as a payload (by the text, --hex and --vin matchers alike),
# not as prose; only a cut-short one is lossy.
_RAW_PREFIX = "Raw:"
_TRUNCATED = "..."


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
    """Distinct ``(ecu, pid, payload)`` rows of every hex payload capture.

    Followed by the responses recovered from scan notes (:func:`_raw_responses`),
    each tagged with the ``scan notes`` field so it is never mistaken for a capture.
    """
    tally = _Tally()
    for e in entries:
        date = str(e.get("date") or "")
        data = hex_payload(e.get("payload"))
        if data is not None:
            key = (str(e.get("ecu") or ""), str(e.get("pid") or ""), data, FIELD_PAYLOAD, False)
            tally.add(key, date)
        for ecu, pid, raw, lossy in _raw_responses(e):
            tally.add((ecu, pid, raw, FIELD_SCAN_NOTES, lossy), date)
    return [
        PayloadRow(ecu, pid, data, weight, first, last, field, lossy)
        for (ecu, pid, data, field, lossy), weight, first, last in tally.items()
    ]


def _raw_hex(note: str) -> tuple[bytes, bool] | None:
    """``(bytes, truncated)`` of a ``Raw: <hex>`` note, or ``None`` if it holds no hex.

    A discover sweep stores an NRC's description there (``Raw: serviceNotSupported``),
    which stays free text.
    """
    if not note.startswith(_RAW_PREFIX):
        return None
    body = note[len(_RAW_PREFIX) :].strip()
    truncated = body.endswith(_TRUNCATED)
    if truncated:
        body = body[: -len(_TRUNCATED)].replace(" ", "")
        body = body[: len(body) - len(body) % 2]  # a cut can split a byte
    data = hex_payload(body)
    return None if data is None else (data, truncated)


def _scan_service(pid: str) -> str:
    """The service byte of a scan capture's ``scan SS …`` pid, or ``""``."""
    parts = pid.split()
    return parts[1] if len(parts) > 1 and parts[0] == "scan" else ""


def _raw_responses(e: Mapping[str, Any]) -> Iterable[tuple[str, str, bytes, bool]]:
    """``(ecu, pid, bytes, truncated)`` for each scan responder whose note holds hex.

    A DID range scan's responder names its ``did``, so the row is filed under the PID
    a capture of it would use (service + DID). A discover responder has no PID; it is
    filed under its response address and the sweep's pid.
    """
    scan = e.get("scan_results")
    if not isinstance(scan, Mapping):
        return
    pid = str(e.get("pid") or "")
    service = _scan_service(pid)
    for responder in scan.get("responding") or []:
        if not isinstance(responder, Mapping) or not isinstance(responder.get("notes"), str):
            continue
        raw = _raw_hex(responder["notes"])
        if raw is None:
            continue
        did = responder.get("did")
        ecu = str(e.get("ecu") or "") if did else str(responder.get("rx") or e.get("ecu") or "")
        yield ecu, f"{service}{did}" if did else pid, raw[0], raw[1]


def _scan_notes(scan_results: Mapping[str, Any] | None) -> Iterable[str]:
    """The free-text notes of a scan: everything but a responder's ``Raw:`` hex."""
    if not isinstance(scan_results, Mapping):
        return
    for key in ("notes", "rejected"):
        value = scan_results.get(key)
        if isinstance(value, str):
            yield value
    for responder in scan_results.get("responding") or []:
        if isinstance(responder, Mapping) and isinstance(responder.get("notes"), str):
            if _raw_hex(responder["notes"]) is None:
                yield responder["notes"]


def text_rows(entries: Iterable[Mapping[str, Any]]) -> list[TextRow]:
    """Distinct free-text values: capture labels/notes/responses and session text.

    Session-level text is counted once per session, not once per capture in it. A
    session is identified by the file it lives in (its full path: a layered profile's
    base and overlay both hold a ``2026-07-24.json``) and its index there.
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
                tally.add((field, ecu, pid, value), date)
        for note in _scan_notes(e.get("scan_results")):
            if note.strip():
                tally.add((FIELD_SCAN_NOTES, ecu, pid, note), date)
        session = (str(e.get("_path") or e.get("file") or ""), int(e.get("_session_idx") or 0))
        if session in seen_sessions:
            continue
        seen_sessions.add(session)
        for field, value in (
            (FIELD_SESSION_LABEL, e.get("session_label")),
            (FIELD_SESSION_NOTES, e.get("session_notes")),
        ):
            if isinstance(value, str) and value.strip():
                tally.add((field, "", "", value), date)
    return [
        TextRow(field, ecu, pid, text, weight, first, last)
        for (field, ecu, pid, text), weight, first, last in tally.items()
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
