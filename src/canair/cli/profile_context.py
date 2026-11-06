"""The active profile's context: what the CLI wires around the profile package.

Selecting the car (variant and revision pins) needs the variants, which sit above ``profile/``,
so the CLI installs it once at start-up and the profile package applies it when it resolves
the active profile.
"""

from __future__ import annotations

from canair.profile import Profile, ProfileError, active, profile_for_path, use_variant_applier
from canair.variants.selection import apply_variant


def install() -> None:
    """Make the active profile resolve with its variant and revision pins applied."""
    use_variant_applier(apply_variant)


def target_profile(args) -> Profile:
    """The profile an edit command works on: the one that owns ``--dir``, else the active one.

    An explicit ``--dir`` points at definitions that need no resolvable active profile (they
    validate against their own), so it must not fail when the active profile is ambiguous.
    """
    directory = getattr(args, "dir", None)
    return profile_for_path(directory) if directory else active()


def dir_profile(directory) -> Profile:
    """The profile for a command that may be pointed at an explicit captures ``--dir``.

    The active profile when one resolves, as ever; else (several profiles and no default) the
    profile that owns ``directory``, so an offline ``--dir`` read keeps working.
    """
    if directory is None:
        return active()
    try:
        return active()
    except ProfileError:
        return profile_for_path(directory)
