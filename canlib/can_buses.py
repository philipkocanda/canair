"""Per-profile CAN bus segment vocabulary.

A profile declares the physical CAN bus segments its ECUs sit on in
``<profile>/can_buses.yaml``. Bus naming is **vendor-specific** — Hyundai/Kia
use domain codes (B-CAN/P-CAN/C-CAN/MM-CAN/H-CAN), Ford uses speed codes (HS/MS),
BMW uses PT-CAN/K-CAN/F-CAN, VW uses German domain names — so the accepted codes
live per profile rather than in a global enum. The top-level ``can_bus:`` field
on each ECU (in ``ecus/``) is validated against this per-profile vocabulary.

File format — each bare code maps to a human ``name``, ``description``, and an
optional ``bitrate`` (the segment's bus speed in bit/s)::

    can_buses:
      ALL:
        name: All segments
        description: The gateway bridges every segment.
      B-CAN:
        name: Body CAN
        description: Comfort/body electronics.
        bitrate: 100000

The older list form (``can_buses: [ALL, B-CAN]``) is still accepted for
back-compatibility — those codes simply carry no name/description/bitrate.

``allowed_can_buses`` returns the set of declared codes; when the file is absent
the vocabulary is empty (membership is then not enforced — a profile need not
declare buses at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from canlib import yaml_io

if TYPE_CHECKING:
    from collections.abc import Iterable

    from canlib.profile import Profile


@dataclass(frozen=True)
class BusDef:
    """One declared CAN bus segment: a code plus optional human name/description.

    ``bitrate`` is the segment's bus speed in bit/s (e.g. ``500000`` for a
    500 kbit/s high-speed CAN, ``100000`` for a 100 kbit/s low-speed body CAN);
    ``None`` when the profile hasn't recorded it.
    """

    code: str
    name: str = ""
    description: str = ""
    bitrate: int | None = None

    @property
    def label(self) -> str:
        """Human label for display — the ``name`` if set, else the bare code."""
        return self.name or self.code


def _can_buses_path(profile: Profile | None = None) -> Path:
    from .profile import active

    return (profile or active()).can_buses_file


def load_can_buses(profile: Profile | None = None) -> list[BusDef]:
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

    def _add(code: str, name: str = "", description: str = "", bitrate: object = None) -> None:
        code = str(code).strip()
        if code and code not in seen:
            seen.add(code)
            rate: int | None = None
            if bitrate is not None:
                try:
                    rate = int(bitrate)
                except (TypeError, ValueError):
                    rate = None
            out.append(
                BusDef(
                    code=code,
                    name=str(name or "").strip(),
                    description=str(description or "").strip(),
                    bitrate=rate,
                )
            )

    if isinstance(raw, dict):
        for code, meta in raw.items():
            if isinstance(meta, dict):
                _add(
                    code,
                    meta.get("name", ""),
                    meta.get("description", ""),
                    meta.get("bitrate"),
                )
            elif isinstance(meta, str):
                _add(code, meta)
            else:
                _add(code)
    elif isinstance(raw, list):
        for entry in raw:
            _add(entry)
    return out


def load_can_bus_codes(profile: Profile | None = None) -> list[str]:
    """Ordered list of declared bus codes. Empty when no can_buses.yaml."""
    return [b.code for b in load_can_buses(profile)]


def allowed_can_buses(profile: Profile | None = None) -> set[str]:
    """The set of accepted CAN bus codes for a profile (empty when undeclared)."""
    return {b.code for b in load_can_buses(profile)}


def bus_names(profile: Profile | None = None) -> dict[str, str]:
    """Map each declared code to its human label (name, or the code itself)."""
    return {b.code: b.label for b in load_can_buses(profile)}


# The conventional code for a gateway that bridges every segment. An ECU tagged
# with it is a member of every declared bus (see ``expand_bus_membership``).
ALL_CODE = "ALL"


def expand_bus_membership(codes: Iterable[str], declared: Iterable[str]) -> set[str]:
    """Resolve an ECU's ``can_bus`` codes to concrete segment membership.

    The conventional gateway code (``ALL``, case-insensitive) means the ECU sits
    on / bridges *every* declared segment, so it expands to the full declared
    vocabulary; all other codes pass through unchanged. Used by ``canair bus`` so
    an ``ALL``-tagged gateway is counted on each segment (including the diagnostic
    bus) rather than only on a standalone ``ALL`` row.
    """
    declared_list = list(declared)
    out: set[str] = set()
    for code in codes:
        code = str(code).strip()
        if not code:
            continue
        if code.upper() == ALL_CODE:
            # Gateway: member of every declared segment (the canonical ALL code
            # is itself in the vocabulary, so it's included when declared).
            out.update(declared_list)
        else:
            out.add(code)
    return out
