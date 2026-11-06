"""Profiles for tests that call library code, which takes the profile as an argument."""

from __future__ import annotations

from canair.profile.discovery import resolve_profile
from canair.profile.model import Profile
from canair.variants.selection import apply_variant

BUNDLED = "hyundai-ioniq-ae-ev"


def bundled_profile(name: str = BUNDLED) -> Profile:
    """A bundled profile with its car selected the way the suite's config selects it."""
    return apply_variant(resolve_profile(name))
