"""Per-profile CAN bus segment vocabulary.

A profile declares the physical CAN bus segments its ECUs sit on in
``<profile>/can_buses.yaml``. Bus naming is **vendor-specific** — Hyundai/Kia
use single-letter domain codes (B/P/C/M/H), Ford uses speed codes (HS/MS), BMW
uses PT-CAN/K-CAN/F-CAN, VW uses German domain names — so the accepted codes
live per profile rather than in a global enum. The top-level ``can_bus:`` field
on each ECU (in ``ecus/``) is validated against this per-profile vocabulary.

File format (commit-1 shape — a list of codes)::

    can_buses:
      - All        # the gateway bridges every segment
      - B          # e.g. Body CAN

``allowed_can_buses`` returns the set of declared codes; when the file is absent
the vocabulary is empty (membership is then not enforced — a profile need not
declare buses at all).
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _can_buses_path(profile=None) -> Path:
    from .profile import active

    return (profile or active()).can_buses_file


def load_can_bus_codes(profile=None) -> list[str]:
    """Ordered list of declared bus codes. Empty when no can_buses.yaml.

    Blank/duplicate entries are dropped, order otherwise preserved.
    """
    path = _can_buses_path(profile)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    raw = data.get("can_buses") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        code = str(entry).strip()
        if code and code not in out:
            out.append(code)
    return out


def allowed_can_buses(profile=None) -> set[str]:
    """The set of accepted CAN bus codes for a profile (empty when undeclared)."""
    return set(load_can_bus_codes(profile))
