"""What the kinds of ``canair profile`` share."""

from __future__ import annotations

from canair.profile.discovery import alias_index, selectable_profiles
from canair.render import messages


def current_name(name: str, profiles_dir) -> str:
    """``name``, or the current name of the profile it is an old name of."""
    index = alias_index(profiles_dir)
    profiles = selectable_profiles(profiles_dir, index)
    target = index.target(name)
    if name not in profiles and target in profiles:
        messages.note(f"'{name}' was renamed; using its current name, '{target}'")
        return target
    return name
