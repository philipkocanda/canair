"""Selecting a variant: the global ``--variant`` flag and ``CANAIR_VARIANT``.

The profile names the model; the variant names *your* car within it. Selection is
explicit and checked: a value must be one of the profile's declared variants (or the
analysis scopes ``all``/``untagged``), and an unknown one is an error that lists the
valid ones — never a fallback. What a selection does (tagging recordings, scoping
analysis) is up to its readers; see :mod:`canlib.variants.tags`.
"""

from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING, Literal

from canlib.variants import VariantsError, load_variants, normalize_variant_id
from canlib.variants.tags import SCOPES

if TYPE_CHECKING:
    from canlib.profile import Profile

VariantSource = Literal["flag", "env"]

ENV_VAR = "CANAIR_VARIANT"


def require_named_car(profile: Profile, what: str) -> str | None:
    """The variant an import into ``profile`` must be tagged with; refuse when unnamed.

    Imported data comes from someone else's car, so on a profile with more than one
    variant it must say which on the command line (the global ``--variant``) — never
    by an environment default meant for the user's own car. Returns the variant, or
    None when the profile has at most one variant (nothing to tell apart).
    """
    from canlib.profile import ProfileError
    from canlib.variants.tags import selected_variant

    try:
        declared = load_variants(profile)
    except VariantsError as e:
        raise ProfileError(str(e)) from None
    if len(declared) < 2:
        return selected_variant(profile)
    variant = selected_variant(profile)
    if variant and profile.variant_source == "flag":
        return variant
    raise ProfileError(
        f"{what}: '{profile.name}' describes {len(declared)} variants "
        f"({', '.join(declared)}). Imported data comes from someone else's car, so name "
        f"it with the global flag: canair --variant ID {what} …"
    )


def apply_variant(profile: Profile, flag: str | None = None) -> Profile:
    """``profile`` with the selected variant (``--variant`` > ``CANAIR_VARIANT``) applied.

    Raises :class:`~canlib.profile.ProfileError` when the selection names no variant
    the profile declares.
    """
    from canlib.profile import ProfileError

    source: VariantSource
    if flag:
        chosen, source = flag, "flag"
    elif os.environ.get(ENV_VAR):
        chosen, source = os.environ[ENV_VAR], "env"
    else:
        return profile
    chosen = normalize_variant_id(chosen)
    if chosen not in SCOPES:
        try:
            declared = load_variants(profile)
        except VariantsError as e:
            raise ProfileError(str(e)) from None
        if not declared:
            raise ProfileError(
                f"--variant {chosen}: profile '{profile.name}' declares no variants "
                "(no variants.yaml)."
            )
        if chosen not in declared:
            raise ProfileError(
                f"--variant {chosen}: profile '{profile.name}' has no such variant. "
                f"Declared: {', '.join(declared)} (or {' / '.join(SCOPES)} to scope analysis)."
            )
    return dataclasses.replace(profile, variant=chosen, variant_source=source)
