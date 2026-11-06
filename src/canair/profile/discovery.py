"""Finding profiles: the roots, layers, aliases and ``resolve_profile``."""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

from canair.config.file import load_config
from canair.core import yaml_io
from canair.core.paths import BUNDLED_PROFILES_DIR, config_dir
from canair.profile.model import Profile, ProfileError, looks_like_profile
from canair.profile.names import AliasIndex, build_alias_index


def profiles_roots(profiles_dir: str | os.PathLike | None = None) -> list[Path]:
    """Return the profile search roots, highest precedence first."""
    roots: list[Path] = []
    if profiles_dir:
        roots.append(Path(profiles_dir))
    env = os.environ.get("CANAIR_PROFILES_DIR")
    if env:
        roots.append(Path(env))
    cfg = load_config().get("profiles_dir")
    if cfg:
        roots.append(Path(cfg))
    roots.append(config_dir() / "profiles")  # user profiles (uncommitted)
    roots.append(BUNDLED_PROFILES_DIR)  # repo-bundled (e.g. hyundai-ioniq-ae-ev)
    return roots


def discover_profiles(profiles_dir: str | os.PathLike | None = None) -> dict[str, Path]:
    """Discover available profiles by name. Earlier roots shadow later ones."""
    found: dict[str, Path] = {}
    for root in profiles_roots(profiles_dir):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.name not in found and looks_like_profile(child):
                found[child.name] = child
    return found


def extends_target(root: Path) -> str | None:
    """The profile name ``root`` layers onto, from its ``extends:`` marker.

    ``extends:`` marks a bundle as a *layer* rather than a whole vehicle. It is
    the key reserved by ``plans/2026-07-30-profile-variant-inheritance.md``; this
    slice honours only the same-name case (an overlay for recorded evidence).
    """
    meta_file = root / "profile.yaml"
    if not meta_file.exists():
        return None
    with open(meta_file) as f:
        meta = yaml_io.safe_load(f) or {}
    target = meta.get("extends")
    return str(target) if target else None


def profile_layers(
    name: str,
    profiles_dir: str | os.PathLike | None = None,
    old_names: tuple[str, ...] | list[str] = (),
) -> list[Path]:
    """Bundles contributing to ``name``, most specific first.

    Discovery normally stops at the first root holding ``name`` — a user profile
    *shadows* a bundled one. A bundle that declares ``extends:`` opts out of that:
    it is a layer, so the walk continues to the bundle it layers onto. This is how
    ``--profile hyundai-ioniq-ae-ev`` keeps decoding against the read-only bundled
    definitions while transparently including the captures you recorded.

    ``old_names`` are the profile's aliases. An overlay still filed under one of
    them (``extends: <old name>``) is a layer of this profile too, so a renamed
    profile keeps its users' recordings before their directory is migrated. An
    old-named bundle *without* ``extends:`` is a profile in its own right, never a
    layer.
    """
    layers: list[Path] = []
    for root in profiles_roots(profiles_dir):
        if not root.is_dir():
            continue
        reached_base = False
        for candidate_name in (name, *old_names):
            candidate = root / candidate_name
            if not looks_like_profile(candidate):
                continue
            target = extends_target(candidate)
            if candidate_name != name and target not in (candidate_name, name):
                continue
            layers.append(candidate)
            if target is None:
                reached_base = True
                break
        if reached_base:
            break
    return layers


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:  # pragma: no cover - unreadable path
        return a == b


def _from_layers(
    name: str, layers: list[Path], old_names: tuple[str, ...] | list[str] = ()
) -> Profile:
    """Assemble a :class:`Profile` from :func:`profile_layers` output."""
    base = layers[-1]
    dangling = extends_target(base)
    if dangling is not None:
        raise ProfileError(
            f"Profile '{name}' at {base} declares `extends: {dangling}`, but no "
            f"'{dangling}' bundle was found below it to layer onto. Either remove "
            "the marker to make it a profile in its own right, or make the base "
            f"discoverable (it is normally the bundled copy under {BUNDLED_PROFILES_DIR})."
        )
    for overlay in layers[:-1]:
        target = extends_target(overlay)
        if target != name and target not in old_names:
            raise ProfileError(
                f"Profile '{name}' at {overlay} declares `extends: {target}`. "
                "Layering onto a differently-named profile is not supported yet "
                "(see plans/2026-07-30-profile-variant-inheritance.md) — an overlay "
                "must extend its own name."
            )
    return Profile(name, base, tuple(reversed(layers[:-1])))


def alias_index(profiles_dir: str | os.PathLike | None = None) -> AliasIndex:
    """Every old profile name a discoverable profile declares under ``aliases:``.

    Read from each profile's definitions root (its base layer), since that is the
    bundle that was renamed; an overlay's own ``profile.yaml`` carries no aliases.
    """
    bases: dict[str, Path] = {}
    for name in discover_profiles(profiles_dir):
        layers = profile_layers(name, profiles_dir)
        if layers and extends_target(layers[-1]) is None:
            bases[name] = layers[-1]
    return build_alias_index(bases)


def is_old_name_overlay(name: str, profiles_dir, index: AliasIndex) -> bool:
    """``name`` is an old profile name whose only directory is a capture overlay.

    Such an overlay (``extends: <old name>``) layers onto the renamed profile, so
    it is not listed or selected as a profile of its own. It is deliberately never
    renamed for the user: older canair checkouts sharing the same config still
    know only the old name.
    """
    if index.target(name) is None:
        return False
    layers = profile_layers(name, profiles_dir)
    return bool(layers) and extends_target(layers[-1]) is not None


def selectable_profiles(
    profiles_dir: str | os.PathLike | None = None, index: AliasIndex | None = None
) -> dict[str, Path]:
    """Discovered profiles a user can select, by name.

    :func:`discover_profiles` minus capture overlays still filed under an old
    profile name: those are layers of the renamed profile, not profiles.
    """
    index = index if index is not None else alias_index(profiles_dir)
    return {
        n: p
        for n, p in discover_profiles(profiles_dir).items()
        if not is_old_name_overlay(n, profiles_dir, index)
    }


def _resolve_named(name: str, profiles_dir, index: AliasIndex) -> Profile:
    old = index.aliases_of(name)
    return _from_layers(name, profile_layers(name, profiles_dir, old), old)


_noted_renames: set[str] = set()


def _note_renamed(alias: str, prof: Profile) -> None:
    """Say once per process that an old profile name was used, and what to use instead."""
    from canair.profile.names import declared_aliases

    if alias in _noted_renames:
        return
    _noted_renames.add(alias)
    variant = declared_aliases(prof.root).get(alias, {}).get("variant")
    implied = f" (variant {variant})" if variant else ""
    switch = f"canair profile use {prof.name}" + (f" --variant {variant}" if variant else "")
    print(
        f"note: profile '{alias}' is now '{prof.name}'{implied}. The old name keeps working; "
        f"switch with `{switch}`.",
        file=sys.stderr,
    )


def _alias_target(name: str, index: AliasIndex) -> str | None:
    claimants = index.conflicts.get(name)
    if claimants:
        raise ProfileError(
            f"'{name}' is declared as an alias by more than one profile "
            f"({', '.join(claimants)}). Pass the profile's current name instead."
        )
    return index.target(name)


def resolve_profile(
    name: str | None = None, profiles_dir: str | os.PathLike | None = None
) -> Profile:
    """Resolve a :class:`Profile` from an explicit name/path, env, or config.

    A real profile directory always wins over an alias of the same name, so an
    adopted copy still called by an old name keeps resolving to itself. An old name
    is noted only when typed (``--profile``/``CANAIR_PROFILE``): one kept in
    ``default_profile`` keeps working silently, since a config shared by checkouts
    of different ages cannot be rewritten to a name the older ones don't know.
    """
    typed = name or os.environ.get("CANAIR_PROFILE")
    name = typed or load_config().get("default_profile")
    index = alias_index(profiles_dir)

    # A path-like name is used directly.
    if name and (os.sep in str(name) or (Path(name).expanduser().is_absolute())):
        root = Path(name).expanduser()
        if not looks_like_profile(root):
            raise ProfileError(f"Profile path {root} does not look like a profile directory.")
        if extends_target(root) is None:
            return Profile(root.name, root)
        # An explicit path to an overlay still needs its base, or nothing decodes.
        base_name = index.target(root.name) or root.name
        old = index.aliases_of(base_name)
        below = [p for p in profile_layers(base_name, profiles_dir, old) if not _same_path(p, root)]
        return _from_layers(base_name, [root, *below], old)

    profiles = selectable_profiles(profiles_dir, index)

    if name:
        if name in profiles:
            return _resolve_named(name, profiles_dir, index)
        target = _alias_target(name, index)
        if target in profiles:
            prof = dataclasses.replace(_resolve_named(target, profiles_dir, index), alias=name)
            if typed:
                _note_renamed(name, prof)
            return prof
        avail = ", ".join(profiles) or "none"
        raise ProfileError(f"Profile '{name}' not found. Available: {avail}.")

    if len(profiles) == 1:
        only = next(iter(profiles))
        return _resolve_named(only, profiles_dir, index)
    if not profiles:
        raise ProfileError(
            "No vehicle profiles found. Create one with "
            '`canair profile create <name> --car-model "..."`, or add a bundle '
            f"under {config_dir() / 'profiles'} or {BUNDLED_PROFILES_DIR}."
        )
    raise ProfileError(
        f"Multiple profiles found ({', '.join(profiles)}). "
        "Set `default_profile` in config or pass --profile."
    )


def config_dir_hint() -> Path:
    """User profiles directory (for help/hint messages)."""
    return config_dir() / "profiles"
