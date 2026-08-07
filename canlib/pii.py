"""Personally-identifiable-information (PII) pre-flight scan for contributions.

The canair tree is public: profiles, captures, and git history are meant to be
shared upstream. Before a profile leaves the user's machine (``canair
contribute``), this module flags data that could **identify or locate** the car
owner so a human can review/redact it — see the "No PII or location data" policy
in the contributing-profiles skill.

It is a *heuristic* net, not a guarantee. It scans the classes that most often
leak:

* **VIN identity DIDs** — capture payloads answering a VIN identifier (UDS
  ``F190``, KWP2000 record ``90``). These embed the vehicle's unique identity in
  the raw bytes even though the capture "is just hex".
* **VIN-shaped ASCII** — any capture payload whose bytes decode to a 17-char
  VIN, regardless of which identifier it was filed under.
* **The curated VIN** — ``ecus/<ecu>.yaml``'s ``identity.vin``, which
  ``canair identity`` writes from a live read. Unlike the rest of ``identity:``
  this is *the* vehicle's unique number, and it reaches a PR through the
  definitions, not the captures — so a definitions-only contribution can leak it.
* **PII-looking free text** — emails, long digit runs (phone-ish), and
  VIN-shaped tokens in capture labels/notes, session notes, and ``car_model``;
  emails and VIN tokens (only) in an ECU's identity free text, whose technical
  prose makes the digit-run heuristic useless there.

Deliberately **not** flagged: per-unit **ECU hardware serials**, in either place
they appear — a capture of the serial DID (UDS ``F18C``/``F18B``) and the curated
``identity.serial``/``ecu_id``/``part_number``. They identify a *module*, not a
person or a location; the project treats them as shareable diagnostic data; and
several are long opaque alphanumerics that collide with the VIN charset, so
scanning them is false positives without a privacy gain. A serial response that
happens to decode to something VIN-shaped is still caught by the payload check
above, which is keyed on the *value*, not the identifier.

A value carrying an obvious redaction mask is never flagged (see
:func:`looks_redacted`) — a report that fires on data already scrubbed teaches
the reviewer to skip it.

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

# UDS ReadDataByIdentifier DIDs (and KWP2000 records) whose response identifies
# the *vehicle*. Only the VIN qualifies: part numbers and SW versions are the same
# across every car of a model, and a per-unit ECU serial (``F18C``/``F18B``) names
# a module rather than a person — the project treats those as shareable diagnostic
# data, so flagging them was noise. Suffixes are matched after stripping the
# service prefix (``22``/``1A``) so ``22F190``, ``1AF190`` and a bare ``F190`` all
# match.
_SENSITIVE_UDS_DIDS = {"F190"}  # VIN
_SENSITIVE_KWP_RECORDS = {"90"}  # VIN

# ``ecus/<ecu>.yaml`` identity fields worth scanning, and how.
#
# ``vin`` is *the* vehicle's unique number — always flagged when it holds a real
# value. The free-text fields get the two *specific* patterns (email, VIN token)
# but NOT the long-digit-run heuristic: identity prose is technical, and DID
# ranges like ``220100-0103`` read as phone numbers to it. Every other identity
# field (``serial``, ``ecu_id``, ``part_number``, versions, …) is deliberately
# unscanned: those identify a module rather than a person, and long opaque serials
# collide with the VIN charset.
_IDENTITY_VIN_FIELD = "vin"
_IDENTITY_FREE_TEXT_FIELDS = ("notes", "description", "alias", "supplier")

# A VIN is 17 chars, upper alphanumeric excluding I/O/Q (ISO 3779).
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# A run of 10+ digits (phone number, long serial) allowing spaces/dashes.
_PHONE_RE = re.compile(r"(?:\d[\s-]?){10,}")
# 4+ identical mask characters in a row — the signature of a redacted value.
_MASK_RUN_RE = re.compile(r"([Xx*#?])\1{3,}")


def _has_vin_token(text: str) -> bool:
    """True when ``text`` contains a token that could be a VIN.

    Shape alone is not enough. A VIN's charset is a *superset* of digits, so a
    17-digit ECU serial — the exact format the BSD modules report — matches
    ``_VIN_RE`` and used to be reported as a VIN. Requiring at least one letter
    separates the two: every real-world WMI carries letters, while an all-numeric
    17-char run is a serial, which this scanner deliberately does not flag.
    """
    return any(any(c.isalpha() for c in m.group()) for m in _VIN_RE.finditer(text))


def looks_redacted(value: str) -> bool:
    """True when ``value`` carries a run of 4+ identical mask characters.

    An already-scrubbed value (``KMHCXXXXXXXXXXXXX``) is not PII, and re-flagging
    it on every run trains the reviewer to skip the report — the failure mode that
    makes a security check useless. Four identical ``X``/``*``/``#``/``?`` in a row
    does not occur in a real VIN or serial, so this is specific without having to
    know which redaction convention was used.
    """
    return bool(_MASK_RUN_RE.search(value))


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
    """Return a human reason if ``pid`` addresses a VIN identifier, else None."""
    ident = _strip_service_prefix(pid)
    if ident in _SENSITIVE_UDS_DIDS:
        return "VIN"
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
    if _has_vin_token(text) and not looks_redacted(text):
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
    if isinstance(payload, str) and not looks_redacted(payload):
        # `payload` is response hex; `response` is a free-form summary that may
        # already hold *decoded* text (an identity read's ASCII), so test both the
        # hex-decoded bytes and the string as written.
        if _has_vin_token(_payload_ascii(payload)) or _has_vin_token(payload):
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


def _scan_identity(identity: object, loc: str) -> list[Finding]:
    """Flag the curated VIN and any PII in one ECU's ``identity:`` free text.

    ``identity.vin`` is the one field here that is *the* vehicle's unique number,
    so a real value is always flagged. The free-text fields are swept for the two
    specific patterns only (email, VIN token) — not the digit-run heuristic, which
    reads a DID range as a phone number. Serials/part numbers/versions are
    intentionally skipped — see the module docstring.
    """
    findings: list[Finding] = []
    if not isinstance(identity, dict):
        return findings
    vin = str(identity.get(_IDENTITY_VIN_FIELD) or "").strip()
    if vin and not looks_redacted(vin):
        findings.append(
            Finding(
                f"{loc} identity.{_IDENTITY_VIN_FIELD}",
                "identity-vin",
                f"holds an unredacted VIN ({len(vin)} chars, starts {vin[:4]!r})",
            )
        )
    for field in _IDENTITY_FREE_TEXT_FIELDS:
        text = str(identity.get(field) or "")
        where = f"{loc} identity.{field}"
        if _EMAIL_RE.search(text):
            findings.append(Finding(where, "email", "contains an email address"))
        if _has_vin_token(text) and not looks_redacted(text):
            findings.append(Finding(where, "vin-text", "contains a VIN-shaped token"))
    return findings


def _scan_ecus(profile: Profile) -> list[Finding]:
    """Scan every ECU's ``identity:`` block in the profile's ``ecus/``.

    This is the *definitions* leak path: ``canair identity`` writes a live VIN read
    straight into ``ecus/<ecu>.yaml``, so it reaches a PR even from a
    ``--no-captures`` contribution — which is exactly the case the capture-only
    scan could never see.
    """
    from .pids import load_pids

    if not profile.ecus_dir.is_dir():
        return []
    try:
        data = load_pids(profile.ecus_dir)
    except (OSError, ValueError, KeyError, TypeError):
        return []  # a malformed profile is `canair validate`'s job to report
    findings: list[Finding] = []
    for name, ecu in (data.get("ecus") or {}).items():
        if isinstance(ecu, dict):
            findings += _scan_identity(ecu.get("identity"), f"ecus/ {name}")
    return findings


def scan_profile(profile: Profile, *, include_captures: bool = True) -> list[Finding]:
    """Scan a **whole** profile for likely-PII data. Returns a flat list.

    ``include_captures=False`` skips the capture store (definitions-only
    contributions), still scanning ``profile.yaml``'s ``car_model``.

    Scope is deliberately the **high-risk** free text: capture session/capture
    labels and notes (auto-suggested from real drives, so the most likely to name
    a place or person), ``car_model``, and each ECU's ``identity:`` VIN + free
    text. The rest of the curated definitions — an ECU's ``notes``/``research``,
    and the state/bus/group vocabularies' descriptions — is *not* scanned; keep it
    technical.

    ``ecus/`` is scanned regardless of ``include_captures``: the VIN lives in the
    definitions, so a definitions-only contribution can leak it.

    Within that scope this scans everything, including data already committed
    upstream. For a *contribution* review — where re-flagging already-shared
    history is just noise — prefer :func:`scan_contribution`, which is scoped to
    what the PR adds.
    """
    findings: list[Finding] = []
    findings += _scan_free_text(str(profile.meta.get("car_model") or ""), "profile.yaml car_model")
    findings += _scan_ecus(profile)
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
    re-flags already-shared captures. ``car_model`` and the ``ecus/`` identity
    blocks are always scanned (cheap, and the VIN ships with the definitions).
    """
    from . import capture_io

    findings: list[Finding] = []
    findings += _scan_free_text(str(profile.meta.get("car_model") or ""), "profile.yaml car_model")
    findings += _scan_ecus(profile)
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
