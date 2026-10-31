"""Selecting a variant: which of the profile's cars is yours.

The profile names the model; the variant names *your* car within it. Selection is
explicit and checked, in this order:

1. the global ``--variant`` flag;
2. ``CANAIR_VARIANT``;
3. ``profile_settings.<profile>.variant`` in the user config (``canair profile use
   NAME --variant ID``);
4. the variant an old profile name implies (``aliases:`` in ``profile.yaml``), when
   the profile was named by it;
5. the only variant, when the profile declares exactly one.

There is **no default variant**. A value must be one of the profile's declared variants
(or, from the flag or the env var, the analysis scopes ``all``/``untagged``); anything
else is an error that lists the valid ones. Commands that need a car call
:func:`require_variant`, which refuses — and says how to pick — when none is selected.
"""

from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING, Literal

from canlib.variants import Variant, VariantsError, describe, load_variants, normalize_variant_id
from canlib.variants.tags import SCOPES, selected_variant

if TYPE_CHECKING:
    from canlib.profile import Profile

VariantSource = Literal["flag", "env", "config", "alias", "only"]

ENV_VAR = "CANAIR_VARIANT"


def _declared(profile: Profile) -> dict[str, Variant]:
    from canlib.profile import ProfileError

    try:
        return load_variants(profile)
    except VariantsError as e:
        raise ProfileError(str(e)) from None


def configured_variant(profile: Profile) -> str | None:
    """The variant remembered for ``profile`` in the user config, if any."""
    from canlib.config import load_config

    settings = load_config().get("profile_settings")
    entry = settings.get(profile.name) if isinstance(settings, dict) else None
    value = entry.get("variant") if isinstance(entry, dict) else None
    return str(value) if value else None


def alias_variant(profile: Profile) -> str | None:
    """The variant the old name ``profile`` was resolved through implies, if any."""
    from canlib.profile_names import declared_aliases

    alias = getattr(profile, "alias", None)
    if not alias:
        return None
    value = declared_aliases(profile.root).get(alias, {}).get("variant")
    return str(value) if value else None


def apply_variant(profile: Profile, flag: str | None = None) -> Profile:
    """``profile`` with its selected variant applied (see the module docstring).

    Raises :class:`~canlib.profile.ProfileError` when a selection names a variant the
    profile does not declare.
    """
    from canlib.profile import ProfileError

    candidates: list[tuple[str | None, VariantSource]] = [
        (flag, "flag"),
        (os.environ.get(ENV_VAR), "env"),
        (configured_variant(profile), "config"),
        (alias_variant(profile), "alias"),
    ]
    picked = next(((v, s) for v, s in candidates if v), None)
    if picked is None:
        declared = _declared(profile)
        if len(declared) != 1:
            return profile
        picked = (next(iter(declared)), "only")
    chosen, source = picked
    chosen = normalize_variant_id(chosen)
    if chosen in SCOPES and source in ("flag", "env"):
        return dataclasses.replace(profile, variant=chosen, variant_source=source)
    declared = _declared(profile)
    origin = {
        "flag": "--variant",
        "env": ENV_VAR,
        "config": f"profile_settings.{profile.name}.variant in the config",
        "alias": f"the old profile name {getattr(profile, 'alias', '')!r}",
        "only": "the only variant",
    }[source]
    if not declared:
        raise ProfileError(
            f"{origin} names variant {chosen!r}, but profile '{profile.name}' declares no "
            "variants (no variants.yaml)."
        )
    if chosen not in declared:
        raise ProfileError(
            f"{origin} names variant {chosen!r}, which profile '{profile.name}' does not "
            f"declare. Declared: {', '.join(declared)}."
        )
    return dataclasses.replace(profile, variant=chosen, variant_source=source)


def require_variant(profile: Profile, what: str) -> None:
    """Refuse ``what`` unless the profile's car is named (when there is a choice to make).

    Everything that talks to the car, records a capture or generates device config
    needs to know which car it is; canair never guesses. The error lists the profile's
    variants and how to pick one.
    """
    from canlib.profile import ProfileError

    declared = _declared(profile)
    if len(declared) < 2 or selected_variant(profile) is not None:
        return
    if profile.variant in SCOPES:
        raise ProfileError(
            f"--variant {profile.variant} scopes analysis to a set of sessions; {what} needs "
            "your car. Name it with --variant ID."
        )
    width = max(len(v) for v in declared)
    rows = "\n".join(f"  {vid.ljust(width)}   {describe(v)}" for vid, v in declared.items())
    example = next(iter(declared))
    raise ProfileError(
        f"{profile.name} describes {len(declared)} variants and none is selected, so canair "
        f"won't {what}.\n\n{rows}\n\n"
        f"  Pick yours once:     canair profile use {profile.name} --variant {example}\n"
        f"  Or for one command:  canair --variant {example} <command> …"
    )


def require_named_car(profile: Profile, what: str) -> str | None:
    """The variant an import into ``profile`` must be tagged with; refuse when unnamed.

    Imported data comes from someone else's car, so on a profile with more than one
    variant it must say which on the command line (the global ``--variant``) — never
    by a default meant for the user's own car (``CANAIR_VARIANT``, the config, an
    alias). Returns the variant, or the selection (possibly None) when the profile has
    at most one variant.
    """
    from canlib.profile import ProfileError

    declared = _declared(profile)
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
