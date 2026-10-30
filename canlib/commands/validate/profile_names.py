"""Profile-name checks: the naming convention and the ``aliases:`` block.

Run with ``validate pids`` alongside ``profile.yaml``'s own checks. The convention is
enforced only for bundled profiles (CI runs this on each); a user's own profile may be
named anything, and ``profile create`` merely warns.
"""

from __future__ import annotations

from canlib.constants import BUNDLED_PROFILES_DIR
from canlib.profile_names import convention_problem


def _is_bundled(prof) -> bool:
    try:
        return prof.root.resolve().is_relative_to(BUNDLED_PROFILES_DIR.resolve())
    except OSError:  # pragma: no cover - unreadable path
        return False


def profile_name_findings(prof) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for ``prof``'s name and the aliases it declares."""
    from canlib.profile import alias_index, selectable_profiles

    errors: list[str] = []
    warnings: list[str] = []

    if _is_bundled(prof):
        problem = convention_problem(prof.name)
        if problem:
            errors.append(f"profile name: {problem}")

    aliases = prof.meta.get("aliases")
    if aliases is None:
        return errors, warnings
    if not isinstance(aliases, dict):
        errors.append("profile.yaml: 'aliases' must be a mapping of old profile name → {}")
        return errors, warnings

    index = alias_index()
    profiles = selectable_profiles(index=index)
    for alias, entry in aliases.items():
        label = f"profile.yaml: alias '{alias}'"
        if not isinstance(entry, dict):
            errors.append(f"{label} must map to a mapping ({{}} when it carries nothing)")
        if alias == prof.name:
            errors.append(f"{label} is the profile's own name")
        claimants = index.conflicts.get(str(alias))
        if claimants:
            others = ", ".join(c for c in claimants if c != prof.name)
            errors.append(f"{label} is also declared by {others}; an alias must be unique")
        if alias in profiles and profiles[alias].resolve() != prof.root.resolve():
            # Not the bundle's fault (typically an adopted copy kept under the old
            # name), but worth knowing: the real directory wins, so the alias is shadowed.
            warnings.append(
                f"{label} is shadowed by a profile directory of that name ({profiles[alias]}), "
                "which takes precedence"
            )
    return errors, warnings
