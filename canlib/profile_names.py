"""Profile names: the naming convention, and the aliases that keep old names working.

A bundled profile is named ``make-model-code-drivetrain`` (``hyundai-ioniq-ae-ev``,
``xpeng-g6-f30-ev``): every part always present, never a year. A name that leaves out
the model code or the drivetrain has to change the moment the model gets a successor or
a new drivetrain, and a year was already wrong for most cars it named.

When a profile is renamed, its ``profile.yaml`` keeps the old name under ``aliases:``
(a mapping, so each entry can carry more than the bare name later), and resolving the
old name lands on the new profile. The index here is pure: it is handed the bundles to
read and knows nothing about discovery roots, which ``canlib.profile`` owns.

Separately, ``also_known_as:`` (in ``profile.yaml`` for the whole car, in ``variants.yaml``
for one variant) lists the other names owners call the car ("Classic Ioniq", "Ioniq 28").
They are labels for people and the docs site to find the profile by; nothing resolves them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from canlib import yaml_io

DRIVETRAINS: tuple[str, ...] = ("ev", "erev", "phev", "hev", "ice", "fcev")

_TOKEN = r"[a-z0-9]+"
# make, model, code, drivetrain — a model may itself be several tokens (ioniq-5).
_CONVENTION = re.compile(rf"^{_TOKEN}(?:-{_TOKEN}){{2,}}-(?:{'|'.join(DRIVETRAINS)})$")


def convention_problem(name: str) -> str | None:
    """Why ``name`` breaks the naming convention, or ``None`` when it follows it."""
    if _CONVENTION.match(name):
        return None
    return (
        f"'{name}' does not follow the make-model-code-drivetrain naming convention "
        f"(e.g. hyundai-ioniq-ae-ev; drivetrain one of {', '.join(DRIVETRAINS)}; "
        "never a year)"
    )


def declared_aliases(root: Path) -> dict[str, dict]:
    """The ``aliases:`` a bundle's ``profile.yaml`` declares (old name → entry)."""
    meta_file = root / "profile.yaml"
    if not meta_file.exists():
        return {}
    with open(meta_file) as f:
        meta = yaml_io.safe_load(f) or {}
    aliases = meta.get("aliases") if isinstance(meta, dict) else None
    if not isinstance(aliases, dict):
        return {}
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in aliases.items()}


@dataclass(frozen=True)
class VariantNames:
    """The other names owners give one variant of the car."""

    id: str
    name: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class AlsoKnownAs:
    """The other names a car goes by, for people looking for it.

    Labels only: unlike ``aliases:`` (old profile names), none of these resolve to the
    profile on the command line. ``profile`` names fit every variant of the car; each
    variant lists its own.
    """

    profile: tuple[str, ...] = ()
    variants: tuple[VariantNames, ...] = ()

    def all(self) -> list[str]:
        """Every name once (case-insensitively), the profile's first."""
        seen: set[str] = set()
        out: list[str] = []
        for name in (*self.profile, *(n for v in self.variants for n in v.names)):
            if name.casefold() not in seen:
                seen.add(name.casefold())
                out.append(name)
        return out


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v.strip() for v in value if isinstance(v, str) and v.strip())


def _load_mapping(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml_io.safe_load(f)
    return data if isinstance(data, dict) else {}


def also_known_as(root: Path) -> AlsoKnownAs:
    """The ``also_known_as:`` names a bundle declares in ``profile.yaml`` and ``variants.yaml``.

    Reads the files raw and tolerates malformed entries (``validate`` reports those), so a
    docs build never fails on a name.
    """
    profile = _texts(_load_mapping(root / "profile.yaml").get("also_known_as"))
    declared = _load_mapping(root / "variants.yaml").get("variants")
    variants = []
    for vid, entry in (declared if isinstance(declared, dict) else {}).items():
        if isinstance(entry, dict) and (names := _texts(entry.get("also_known_as"))):
            variants.append(VariantNames(str(vid), str(entry.get("name") or vid), names))
    return AlsoKnownAs(profile, tuple(variants))


def duplicate_names(names: object) -> list[str]:
    """The entries of a name list that repeat an earlier one, ignoring case."""
    seen: set[str] = set()
    dupes: list[str] = []
    for name in _texts(names):
        if name.casefold() in seen:
            dupes.append(name)
        seen.add(name.casefold())
    return dupes


@dataclass(frozen=True)
class AliasIndex:
    """Old profile names → the profile that now carries them."""

    targets: dict[str, str] = field(default_factory=dict)
    # An alias claimed by more than one profile: every claimant, so the error can name them.
    conflicts: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def target(self, alias: str) -> str | None:
        return self.targets.get(alias)

    def aliases_of(self, profile: str) -> list[str]:
        return sorted(a for a, t in self.targets.items() if t == profile)


def build_alias_index(bases: Mapping[str, Path]) -> AliasIndex:
    """Index the aliases declared by ``bases`` (profile name → definitions root)."""
    claims: dict[str, list[str]] = {}
    for name, root in bases.items():
        for alias in declared_aliases(root):
            if alias != name:
                claims.setdefault(alias, []).append(name)
    targets = {a: c[0] for a, c in claims.items() if len(c) == 1}
    conflicts = {a: tuple(sorted(c)) for a, c in claims.items() if len(c) > 1}
    return AliasIndex(targets, conflicts)
