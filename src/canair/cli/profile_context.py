"""The active profile: the one place that holds it.

Library code takes the profile as an argument. The CLI resolves the profile once, with the
variant and revision pins applied, and passes it down; commands read it through :func:`active`.
Selecting the car (variant and pins) needs the variants, which sit above ``profile/``, so the
CLI does it here.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from canair.profile import Profile, ProfileError, profile_for_path, resolve_profile
from canair.variants.selection import apply_variant

_active: Profile | None = None


def set_active(
    name: str | None = None,
    profiles_dir: str | os.PathLike | None = None,
    variant: str | None = None,
    revisions: Sequence[str] | None = None,
) -> Profile:
    """Resolve the active profile and memoize it (called by ``main``).

    ``variant`` is the ``--variant`` flag and ``revisions`` the ``--revision ECU=ID`` pins.
    """
    return replace_active(apply_variant(resolve_profile(name, profiles_dir), variant, revisions))


def replace_active(profile: Profile) -> Profile:
    """Make ``profile`` the active profile."""
    global _active
    _active = profile
    return _active


def active() -> Profile:
    """The active profile, resolved on first use when nothing set one."""
    return _active if _active is not None else replace_active(apply_variant(resolve_profile()))


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


def named_car(what: str) -> str | None:
    """The variant an import must be tagged with (see ``require_named_car``).

    The active profile then loses the revisions the user's config pinned or a detection
    remembered, since an import comes from someone else's car.
    """
    from canair.variants.selection import require_named_car

    variant, profile = require_named_car(active(), what)
    replace_active(profile)
    return variant
