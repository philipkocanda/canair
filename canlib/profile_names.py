"""Profile names: the naming convention, and the aliases that keep old names working.

A bundled profile is named ``make-model-code-drivetrain`` (``hyundai-ioniq-ae-ev``,
``xpeng-g6-f30-ev``): every part always present, never a year. A name that leaves out
the model code or the drivetrain has to change the moment the model gets a successor or
a new drivetrain, and a year was already wrong for most cars it named.

When a profile is renamed, its ``profile.yaml`` keeps the old name under ``aliases:``
(a mapping, so each entry can carry more than the bare name later), and resolving the
old name lands on the new profile. The index here is pure: it is handed the bundles to
read and knows nothing about discovery roots, which ``canlib.profile`` owns.
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
