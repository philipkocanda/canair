"""Naming the car: the checks a command makes before it talks to, or records from, one."""

from __future__ import annotations

from canair.ecus.pids import load_raw_pids
from canair.profile import Profile, active
from canair.variants.revisions import declared, revision_entry
from canair.variants.selection import require_variant


def identity_hint(profile: Profile) -> str:
    """The "not sure?" line, naming an ECU whose revision tells the variants apart."""
    try:
        ecus = load_raw_pids(profile).get("ecus") or {}
    except Exception:
        return ""
    for name, ecu_def in ecus.items():
        revs = declared(ecu_def)
        if len(revs) > 1 and any(revision_entry(ecu_def, r).get("fingerprints") for r in revs):
            return f"\n  Not sure which you have?  canair identity {name}   (names the match)"
    return ""


def require_car(what: str) -> None:
    """Refuse ``what`` until the active profile's car is named (see ``require_variant``)."""
    require_variant(active(), what, identity_hint)
