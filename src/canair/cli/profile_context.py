"""The active profile's context: what the CLI wires around the profile package.

Selecting the car (variant and revision pins) needs the variants, which sit above ``profile/``,
so the CLI installs it once at start-up and the profile package applies it when it resolves
the active profile.
"""

from __future__ import annotations

from canair.profile import use_variant_applier
from canair.variants.selection import apply_variant


def install() -> None:
    """Make the active profile resolve with its variant and revision pins applied."""
    use_variant_applier(apply_variant)
