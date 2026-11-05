"""``canair profile use``: the default profile, its variant and revision pins."""

from __future__ import annotations

import sys

from canair.profile import (
    ProfileError,
    resolve_profile,
    selectable_profiles,
)
from canair.render import messages
from canair.yaml_edit.echo import echo_edit

from .common import current_name


def cmd_use(args) -> int:
    from canair.config.file import set_config_value

    profiles_dir = getattr(args, "profiles_dir", None)
    name = current_name(args.name.strip(), profiles_dir)
    profiles = selectable_profiles(profiles_dir)
    if name not in profiles:
        avail = ", ".join(profiles) or "none"
        messages.error(f"profile '{name}' not found. Available: {avail}.")
        return 1
    variant = choose_variant(name, profiles_dir, getattr(args, "use_variant", None))
    if variant == "":
        return 2
    # Checked before anything is written, so a bad pin leaves the config as it was.
    pins = _checked_pins(name, profiles_dir, variant, getattr(args, "pins", None) or [])
    if pins is None:
        return 2
    from canair.config.file import set_config_key

    path = set_config_value("default_profile", name)
    changes = []
    if variant:
        set_config_key(f"profile_settings.{name}.variant", variant)
        changes.append(f"profile_settings.{name}.variant = {variant}")
    for ecu, rid in pins:
        set_config_key(f"profile_settings.{name}.revisions.{ecu}", rid)
        changes.append(f"profile_settings.{name}.revisions.{ecu} = {rid}")
    echo_edit(f"default_profile = {name}", path)
    for change in changes:
        print(f"    {change}")
    return 0


def _checked_pins(
    name: str, profiles_dir, variant: str | None, texts: list[str]
) -> list[tuple[str, str]] | None:
    """``--pin ECU=ID`` values checked against the profile, or None after reporting one.

    Each must name an ECU that declares the revision, and one the chosen car (the variant
    being set, else the one remembered) can carry.
    """
    if not texts:
        return []
    from canair.ecus.pids import load_raw_pids
    from canair.variants import VariantsError, load_variants
    from canair.variants.revisions import RevisionError, canonical_ecu, parse_pin, resolve
    from canair.variants.selection import configured_variant

    try:
        prof = resolve_profile(name, profiles_dir)
        ecus = load_raw_pids(prof).get("ecus") or {}
        vid = variant or configured_variant(prof)
        car = load_variants(prof).get(vid) if vid else None
        pins = [parse_pin(t) for t in texts]
        resolve(ecus, car, [(e, r, "flag") for e, r in pins])
    except (ProfileError, VariantsError, RevisionError) as e:
        messages.error(f"--pin: {e}")
        return None
    return [(canonical_ecu(e, ecus) or e, r) for e, r in pins]


def choose_variant(name: str, profiles_dir, requested: str | None) -> str | None:
    """The variant to remember for ``name``: the one requested, else picked on a TTY.

    Returns None when there is nothing to choose (or the choice was skipped), and ""
    after reporting an invalid ``requested`` variant.
    """
    from canair.variants import VariantsError, describe, load_variants, normalize_variant_id
    from canair.variants.selection import configured_variant

    try:
        prof = resolve_profile(name, profiles_dir)
        variants = load_variants(prof)
        remembered = configured_variant(prof)
    except (ProfileError, VariantsError) as e:
        messages.error(str(e))
        return ""
    if requested:
        vid = normalize_variant_id(requested)
        if vid not in variants:
            known = ", ".join(variants) or "none (no variants.yaml)"
            messages.error(f"'{name}' has no variant {vid!r}. Declared: {known}.")
            return ""
        return vid
    if len(variants) < 2:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        messages.note(
            f"'{name}' describes {len(variants)} variants; name yours with "
            f"`canair profile use {name} --variant ID` ({', '.join(variants)})"
        )
        return None
    from canair.tui import select_from_list

    items = list(variants.items())

    def _render(item) -> str:
        current = "  [green](current)[/green]" if item[0] == remembered else ""
        return f"[bold]{item[0]}[/bold]{current}\n     [dim]{describe(item[1])}[/dim]"

    chosen = select_from_list(
        items,
        title=f"Which {name} is your car?",
        render=_render,
        initial=next((i for i, (vid, _) in enumerate(items) if vid == remembered), 0),
        footer="↑/↓ move · enter choose · q/esc skip",
    )
    return chosen[0] if chosen else None
