"""The kinds of revision fingerprint, and the shape check each one gets (no I/O).

A fingerprint is a one-key mapping naming its kind. Matching a car against one is
:mod:`canair.variants.fingerprints`; this leaf only knows what a well-formed one is, so
both the definitions check (:mod:`canair.variants.revisions`) and the editor
(:mod:`canair.yaml_edit.revisions`) use the same vocabulary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: How a revision is recognised: identity fields matching globs, a safe read only this
#: revision answers, or the ECU's software version matching a glob.
FINGERPRINT_KINDS: tuple[str, ...] = ("identity", "responds", "software")

_HEX_RE = re.compile(r"^(?:[0-9A-Fa-f]{2})+$")


def fingerprint_problem(fp: Any, identity_fields: set[str]) -> str | None:
    """Why ``fp`` is not a well-formed fingerprint, or ``None``."""
    if not isinstance(fp, Mapping) or len(fp) != 1:
        return f"each fingerprint is one of {', '.join(FINGERPRINT_KINDS)}"
    kind, value = next(iter(fp.items()))
    if kind == "responds":
        if not isinstance(value, str) or not _HEX_RE.match(value.strip()):
            return 'responds: must be a quoted hex request (whole bytes, e.g. "1A90")'
        return None
    if kind == "identity":
        if not isinstance(value, Mapping) or not value:
            return "identity: must map identity fields to glob patterns"
        for field_name, pattern in value.items():
            if identity_fields and field_name not in identity_fields:
                return f"identity: unknown identity field '{field_name}'"
            if not isinstance(pattern, str) or not pattern.strip():
                return f"identity: '{field_name}' must be a text pattern"
        return None
    if kind == "software":
        if not isinstance(value, str) or not value.strip():
            return 'software: must be a text pattern for the software version ("F195:1.04*")'
        return None
    return f"unknown fingerprint kind '{kind}' (known: {', '.join(FINGERPRINT_KINDS)})"


def new_fingerprint(
    responds: str | None = None,
    identity: Mapping[str, str] | None = None,
    software: str | None = None,
) -> dict[str, Any]:
    """The one fingerprint the arguments name, normalized.

    Raises :class:`ValueError` when they name none or several: a fingerprint is one kind.
    """
    given = [
        k for k, v in (("responds", responds), ("identity", identity), ("software", software)) if v
    ]
    if len(given) > 1:
        raise ValueError("one fingerprint at a time: --responds, --identity or --software")
    if responds:
        return {"responds": responds.strip().upper()}
    if software:
        return {"software": software.strip()}
    if identity:
        return {"identity": {str(k): str(v) for k, v in identity.items()}}
    raise ValueError(f"give a fingerprint ({' or '.join(FINGERPRINT_KINDS)}), or --clear")
