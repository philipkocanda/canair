"""``canair profile list``."""

from __future__ import annotations

from canair.cli.profile_context import active
from canair.config.file import load_config
from canair.install.context import bundled_profiles_are_snapshot
from canair.profile.discovery import (
    alias_index,
    config_dir_hint,
    profile_layers,
    selectable_profiles,
)
from canair.profile.model import ProfileError
from canair.render import messages


def _warn_if_bundled_snapshot() -> None:
    """Warn contributors that a bare `canair` reads a frozen copy of the bundled
    profiles, so edits to the git checkout's `profiles/` aren't picked up."""
    if bundled_profiles_are_snapshot():
        messages.note(
            "running the `uv tool install` copy — its bundled profiles are a frozen snapshot, "
            "so edits to the repo's profiles/ won't appear here.\n"
            "Run `uv run canair` from the repo root (or reinstall) to pick them up."
        )


def cmd_list(args) -> int:
    profiles_dir = getattr(args, "profiles_dir", None)
    index = alias_index(profiles_dir)
    profiles = selectable_profiles(profiles_dir, index)
    if not profiles:
        print("No profiles found.")
        print('Create one with `canair profile create <name> --car-model "..."`,')
        print(f"or add a bundle under {config_dir_hint()}.")
        return 0
    try:
        active_name = active().name
    except ProfileError:
        active_name = None
    for name, root in profiles.items():
        marker = "*" if name == active_name else " "
        print(f"{marker} {name}\t{root}")
        old = index.aliases_of(name)
        layers = profile_layers(name, profiles_dir, old)
        if len(layers) > 1:
            for layer in layers[:-1]:
                if layer != root:
                    print(f"    \tcaptures also from {layer}")
            print(f"    \tlayered over {layers[-1]}")
        successor = index.target(name)
        if successor and successor in profiles:
            print(f"    \t(an old profile name: the bundled successor is {successor})")
    renamed = {a: t for a, t in index.targets.items() if t in profiles and a not in profiles}
    if renamed:
        print()
        print("Renamed (old names still resolve):")
        for alias, target in sorted(renamed.items()):
            print(f"  {alias} → {target}")

    if not load_config().get("default_profile"):
        print()
        if len(profiles) == 1:
            print("No default_profile set (the single discovered profile is used).")
        else:
            print("No default_profile set.")
        print("Set one with `canair profile use <name>`.")

    _warn_if_bundled_snapshot()
    return 0
