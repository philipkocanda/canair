"""Per-profile CAN bus segment vocabulary.

A profile declares the physical CAN bus segments its ECUs sit on in
``<profile>/can_buses.yaml``. Bus naming is **vendor-specific** — Hyundai/Kia
use single-letter domain codes (B/P/C/M/H), Ford uses speed codes (HS/MS), BMW
uses PT-CAN/K-CAN/F-CAN, VW uses German domain names — so the accepted codes
live per profile rather than in a global enum. The top-level ``can_bus:`` field
on each ECU (in ``ecus/``) is validated against this per-profile vocabulary.

File format — each bare code maps to a human ``name`` and ``description``::

    can_buses:
      All:
        name: All segments
        description: The gateway bridges every segment.
      B:
        name: Body CAN
        description: Comfort/body electronics.

The older list form (``can_buses: [All, B]``) is still accepted for
back-compatibility — those codes simply carry no name/description.

``allowed_can_buses`` returns the set of declared codes; when the file is absent
the vocabulary is empty (membership is then not enforced — a profile need not
declare buses at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from canlib import yaml_io


@dataclass(frozen=True)
class BusDef:
    """One declared CAN bus segment: a code plus optional human name/description."""

    code: str
    name: str = ""
    description: str = ""

    @property
    def label(self) -> str:
        """Human label for display — the ``name`` if set, else the bare code."""
        return self.name or self.code


def _can_buses_path(profile=None) -> Path:
    from .profile import active

    return (profile or active()).can_buses_file


def load_can_buses(profile=None) -> list[BusDef]:
    """Ordered list of declared buses. Empty when no can_buses.yaml.

    Accepts both the ``code: {name, description}`` mapping form and the legacy
    ``[code, ...]`` list form. Blank/duplicate codes are dropped, order preserved.
    """
    path = _can_buses_path(profile)
    if not path.exists():
        return []
    data = yaml_io.safe_load(path.read_text()) or {}
    raw = data.get("can_buses")
    out: list[BusDef] = []
    seen: set[str] = set()

    def _add(code: str, name: str = "", description: str = "") -> None:
        code = str(code).strip()
        if code and code not in seen:
            seen.add(code)
            out.append(
                BusDef(
                    code=code,
                    name=str(name or "").strip(),
                    description=str(description or "").strip(),
                )
            )

    if isinstance(raw, dict):
        for code, meta in raw.items():
            if isinstance(meta, dict):
                _add(code, meta.get("name", ""), meta.get("description", ""))
            elif isinstance(meta, str):
                _add(code, meta)
            else:
                _add(code)
    elif isinstance(raw, list):
        for entry in raw:
            _add(entry)
    return out


def load_can_bus_codes(profile=None) -> list[str]:
    """Ordered list of declared bus codes. Empty when no can_buses.yaml."""
    return [b.code for b in load_can_buses(profile)]


def allowed_can_buses(profile=None) -> set[str]:
    """The set of accepted CAN bus codes for a profile (empty when undeclared)."""
    return {b.code for b in load_can_buses(profile)}


def bus_names(profile=None) -> dict[str, str]:
    """Map each declared code to its human label (name, or the code itself)."""
    return {b.code: b.label for b in load_can_buses(profile)}
