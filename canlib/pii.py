"""Personally-identifiable-information (PII) pre-flight scan for contributions.

The canair tree is public: profiles, captures, and git history are meant to be
shared upstream. Before a profile leaves the user's machine (``canair
contribute``), this module flags data that could **identify or locate** the car
owner so a human can review/redact it — see the "No PII or location data" policy
in the contributing-profiles skill.

It is a *heuristic* net, not a guarantee. It scans the classes that most often
leak:

* **Identity DIDs** — capture payloads answering a VIN / ECU-serial identifier
  (UDS ``F190``/``F18C``, KWP2000 ``90``). These embed the vehicle's unique
  identity in the raw bytes even though the capture "is just hex".
* **VIN-shaped ASCII** — any capture payload whose bytes decode to a 17-char
  VIN, regardless of which identifier it was filed under.
* **PII-looking free text** — emails, long digit runs (phone-ish), and
  VIN-shaped tokens in capture labels/notes, session notes, and ``car_model``.

The reviewer remains the backstop; the scan only forces the look.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from .profile import Profile

# ---------------------------------------------------------------------------
# Sensitive identity identifiers
# ---------------------------------------------------------------------------

# UDS ReadDataByIdentifier DIDs (and KWP2000 records) that return *identifying*
# data (a VIN or an ECU serial). Part numbers / SW versions are the same across
# every car of a model, so they are deliberately NOT here — only fields that pin
# down one specific vehicle. Suffixes are matched after stripping the service
# prefix (``22``/``1A``) so ``22F190``, ``F190`` and a bare ``F190`` all match.
_SENSITIVE_UDS_DIDS = {"F190", "F18C"}  # VIN, ECU serial / calibration ID
_SENSITIVE_KWP_RECORDS = {"90"}  # ECU name / VIN

# A VIN is 17 chars, upper alphanumeric excluding I/O/Q (ISO 3779).
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# A run of 10+ digits (phone number, long serial) allowing spaces/dashes.
_PHONE_RE = re.compile(r"(?:\d[\s-]?){10,}")


@dataclass(frozen=True)
class Finding:
    """One flagged item. ``kind`` groups the heuristic that fired."""

    location: str
    kind: str
    detail: str


def _strip_service_prefix(pid: str) -> str:
    """Return the identifier of a capture ``pid``, minus its UDS/KWP prefix.

    ``22F190`` → ``F190``; ``1A90`` → ``90``; ``F190`` → ``F190``. Ranges/other
    shapes pass through unchanged (they won't match the sensitive sets).
    """
    up = pid.strip().upper()
    if up.startswith("22") and len(up) > 2:
        return up[2:]
    if up.startswith("1A") and len(up) > 2:
        return up[2:]
    return up


def _did_is_sensitive(pid: str) -> str | None:
    """Return a human reason if ``pid`` addresses a VIN/serial identifier, else None."""
    ident = _strip_service_prefix(pid)
    if ident in _SENSITIVE_UDS_DIDS:
        return "VIN" if ident == "F190" else "ECU serial"
    if ident in _SENSITIVE_KWP_RECORDS:
        return "VIN (KWP2000)"
    return None


def _payload_ascii(payload: str) -> str:
    """Best-effort ASCII rendering of a hex payload (non-printables dropped)."""
    hexstr = re.sub(r"[^0-9a-fA-F]", "", payload)
    if len(hexstr) % 2:
        hexstr = hexstr[:-1]
    try:
        raw = bytes.fromhex(hexstr)
    except ValueError:
        return ""
    return "".join(chr(b) if 32 <= b < 127 else " " for b in raw)


def _scan_free_text(text: str, location: str) -> list[Finding]:
    """Flag emails / phone-ish digit runs / VIN-shaped tokens in free text."""
    findings: list[Finding] = []
    if not text:
        return findings
    if _EMAIL_RE.search(text):
        findings.append(Finding(location, "email", "contains an email address"))
    if _VIN_RE.search(text):
        findings.append(Finding(location, "vin-text", "contains a VIN-shaped token"))
    if _PHONE_RE.search(text):
        findings.append(Finding(location, "digits", "contains a long digit run (phone/serial?)"))
    return findings


def _scan_capture(cap: object, loc: str) -> list[Finding]:
    """Flag identity DIDs, VIN-shaped payloads, and free-text PII in one capture."""
    findings: list[Finding] = []
    if not isinstance(cap, dict):
        return findings
    pid = str(cap.get("pid") or "")
    reason = _did_is_sensitive(pid)
    if reason:
        findings.append(Finding(f"{loc} ({pid})", "identity-did", reason))
    payload = cap.get("payload") or cap.get("response")
    if isinstance(payload, str) and _VIN_RE.search(_payload_ascii(payload)):
        findings.append(Finding(f"{loc} ({pid})", "vin-payload", "payload decodes to a VIN"))
    for field in ("label", "notes"):
        findings += _scan_free_text(str(cap.get(field) or ""), f"{loc}.{field}")
    return findings


def _scan_session(session: object, loc: str) -> list[Finding]:
    """Flag PII in one capture session (its label/notes and every capture)."""
    findings: list[Finding] = []
    if not isinstance(session, dict):
        return findings
    for field in ("label", "notes"):
        findings += _scan_free_text(str(session.get(field) or ""), f"{loc}.{field}")
    captures = session.get("captures")
    if isinstance(captures, list):
        for ci, cap in enumerate(captures):
            findings += _scan_capture(cap, f"{loc}.captures[{ci}]")
    return findings


def _scan_captures(profile: Profile) -> list[Finding]:
    from . import capture_io

    findings: list[Finding] = []
    if not profile.captures_dir.is_dir():
        return findings
    for path in capture_io.iter_capture_files(profile.captures_dir):
        try:
            data = capture_io.load_capture_file(path)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        rel = f"captures/{path.name}"
        for si, session in enumerate(data.get("sessions", []) or []):
            findings += _scan_session(session, f"{rel} sessions[{si}]")
    return findings


def scan_profile(profile: Profile, *, include_captures: bool = True) -> list[Finding]:
    """Scan a **whole** profile for likely-PII data. Returns a flat list.

    ``include_captures=False`` skips the capture store (definitions-only
    contributions), still scanning ``profile.yaml``'s ``car_model``.

    Scope is deliberately the **high-risk** free text: capture session/capture
    labels and notes (auto-suggested from real drives, so the most likely to name
    a place or person) plus ``car_model``. Curated free text — an ECU's
    ``notes``/``research``, and the state/bus/group vocabularies' descriptions —
    is *not* scanned; keep it technical.

    Within that scope this scans everything, including data already committed
    upstream. For a *contribution* review — where re-flagging already-shared
    history is just noise — prefer :func:`scan_contribution`, which is scoped to
    what the PR adds.
    """
    findings: list[Finding] = []
    findings += _scan_free_text(str(profile.meta.get("car_model") or ""), "profile.yaml car_model")
    if include_captures:
        findings += _scan_captures(profile)
    return findings


def _base_session_keys(base_text: str | None) -> set[str]:
    """Serialized identities of the sessions already present in the base file.

    A capture file is an append-only session log, so a session that appears
    verbatim in the committed (base) version is *already upstream* and must not
    be re-flagged. We key each session by its canonical JSON so an unchanged
    session matches exactly while an appended/edited one does not.
    """
    if not base_text:
        return set()
    try:
        data = json.loads(base_text)
    except ValueError:
        return set()
    if not isinstance(data, dict):
        return set()
    return {
        json.dumps(s, sort_keys=True) for s in data.get("sessions", []) or [] if isinstance(s, dict)
    }


def scan_contribution(
    profile: Profile,
    captures_dir,
    *,
    include_captures: bool,
    base_reader: Callable[[str], str | None],
) -> list[Finding]:
    """Scan only what a contribution *adds or changes* vs the upstream base.

    ``captures_dir`` is the prepared contribution's capture directory (inside the
    workspace clone). ``base_reader(relpath)`` returns the committed content of a
    file at ``profiles/<name>/captures/<file>`` (or ``None`` when the file is new
    upstream). Sessions byte-identical to the base are skipped, so a
    definitions-only PR — or one that only appends new sessions — no longer
    re-flags already-shared captures. ``car_model`` is always scanned (cheap).
    """
    from . import capture_io

    findings: list[Finding] = []
    findings += _scan_free_text(str(profile.meta.get("car_model") or ""), "profile.yaml car_model")
    if not include_captures or not captures_dir.is_dir():
        return findings

    for path in capture_io.iter_capture_files(captures_dir):
        try:
            data = capture_io.load_capture_file(path)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        base_keys = _base_session_keys(base_reader(f"profiles/{profile.name}/captures/{path.name}"))
        rel = f"captures/{path.name}"
        for si, session in enumerate(data.get("sessions", []) or []):
            if not isinstance(session, dict):
                continue
            if json.dumps(session, sort_keys=True) in base_keys:
                continue  # already upstream — not part of this contribution
            findings += _scan_session(session, f"{rel} sessions[{si}]")
    return findings
