"""The refusal to edit the definitions of a layered (read-only base) profile."""

from __future__ import annotations

from canair.profile.model import Profile, ProfileError


def require_writable_definitions(profile: Profile) -> Profile:
    """Refuse a definition edit whose target is a layered profile's read-only base.

    An overlay carries recorded evidence only: definitions still resolve from the
    base bundle, which is normally the read-only shipped copy. Writing there would
    either fail or — worse, when the base is an install snapshot — appear to work
    and vanish at the next reinstall. Every authoring command calls this first, so
    the refusal names the fix instead of leaving a half-written file behind.
    """
    if not profile.layered:
        return profile
    raise ProfileError(
        f"'{profile.name}' is a layered profile: its definitions live in the "
        f"read-only base at {profile.root}, and {profile.write_root} only holds what "
        "you record. Definitions do not overlay yet. To edit them, either take a "
        f"full writable copy (`canair profile adopt {profile.name}`) or point canair "
        "at a checkout you can commit to (`canair config set profiles_dir "
        "<clone>/profiles`)."
    )
