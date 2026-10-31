"""Recognising an ECU's revision from what the ECU reports (no I/O).

A revision's ``fingerprints:`` are evidence, not rules (see
:mod:`canlib.variants.revisions`):

* ``{responds: "1A90"}`` — a safe identity read only this revision answers. A protocol
  change shows here without knowing a single part number: a KWP2000 ECU answers ``1A90``
  and refuses a UDS ``22F187``, and the other way round.
* ``{identity: {part_number: "36601-0E1*"}}`` — identity fields matching glob patterns
  (case-sensitive, surrounding blanks ignored); every field listed must match.

A revision matches when any one of its fingerprints does. :func:`match` returns every
matching revision: exactly one is a detection, none or several settle nothing — the ECU
stays undetermined, never guessed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from typing import Any

from canlib.variants.revisions import declared, revision_entry


def _fingerprints(ecu_def: Mapping, rid: str) -> list[Mapping]:
    fps = revision_entry(ecu_def, rid).get("fingerprints")
    return [fp for fp in fps if isinstance(fp, Mapping)] if isinstance(fps, list) else []


def _normalize_request(request: Any) -> str:
    return str(request).replace(" ", "").strip().upper()


def probes(ecu_def: Mapping, possible: Iterable[str]) -> list[str]:
    """The ``responds:`` requests that can tell ``possible`` revisions apart, cheapest first.

    One per revision at most (its first), deduplicated, in declaration order — so a
    two-revision protocol change costs one or two reads, never one per PID.
    """
    out: list[str] = []
    for rid in possible:
        for fp in _fingerprints(ecu_def, rid):
            if "responds" in fp:
                req = _normalize_request(fp["responds"])
                if req not in out:
                    out.append(req)
                break
    return out


def _identity_matches(pattern: Mapping, readings: Mapping[str, Any]) -> bool:
    for field, glob in pattern.items():
        value = readings.get(field)
        if value is None or not fnmatchcase(str(value).strip(), str(glob).strip()):
            return False
    return bool(pattern)


def match(
    ecu_def: Mapping,
    *,
    possible: Iterable[str] | None = None,
    identity: Mapping[str, Any] | None = None,
    responded: Mapping[str, bool] | None = None,
) -> list[str]:
    """The revisions (of ``possible``, default all declared) whose fingerprints match.

    ``identity`` is the ECU's identity readings by field name; ``responded`` maps a
    request (hex) to whether the ECU answered it positively. A request that was not
    sent is no evidence either way.
    """
    candidates = list(possible) if possible is not None else declared(ecu_def)
    answered = {_normalize_request(k): v for k, v in (responded or {}).items()}
    out = []
    for rid in candidates:
        for fp in _fingerprints(ecu_def, rid):
            if "responds" in fp and answered.get(_normalize_request(fp["responds"])) is True:
                out.append(rid)
                break
            if "identity" in fp and identity and isinstance(fp["identity"], Mapping):
                if _identity_matches(fp["identity"], identity):
                    out.append(rid)
                    break
    return out


def detected(matches: list[str]) -> str | None:
    """The revision a match settles on: the only one, or None."""
    return matches[0] if len(matches) == 1 else None
