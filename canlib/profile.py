"""Vehicle profile resolution.

A *profile* is a self-contained directory bundling one vehicle's data:

    <profile>/
      profile.yaml   profile-wide settings (car_model, init, failure_types, ...)
      ecus/          per-ECU definitions — each file is the single source of
                     truth for one ECU (identity, scan_log, dtcs, pids, ...)
      captures/      raw UDS capture files (per date)
      references/    external reference material (spreadsheets, other-vehicle logs)
      out/           generated WiCAN JSON profiles (optional)
      logs/          command/response logs (optional)

Profiles are discovered from several roots (user config dir shadows the
repo-bundled ones). The active profile is chosen by ``--profile`` /
``CANAIR_PROFILE`` / ``default_profile`` in config, or auto-selected when only
one profile exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

from .config import config_dir, load_config
from .constants import BUNDLED_PROFILES_DIR


class ProfileError(Exception):
    """Raised when the active vehicle profile cannot be resolved."""


@dataclass(frozen=True)
class Profile:
    """A resolved vehicle profile rooted at a directory."""

    name: str
    root: Path

    @property
    def ecus_dir(self) -> Path:
        return self.root / "ecus"

    @property
    def captures_dir(self) -> Path:
        return self.root / "captures"

    @property
    def states_file(self) -> Path:
        return self.root / "states.yaml"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @cached_property
    def meta(self) -> dict:
        """Contents of <profile>/profile.yaml (car_model, init, ...), or {}."""
        meta_path = self.root / "profile.yaml"
        if meta_path.exists():
            with open(meta_path) as f:
                return yaml.safe_load(f) or {}
        return {}


def _looks_like_profile(path: Path) -> bool:
    return path.is_dir() and (
        (path / "ecus").is_dir() or (path / "profile.yaml").exists()
    )


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
    roots.append(BUNDLED_PROFILES_DIR)  # repo-bundled (e.g. ioniq-2017)
    return roots


def discover_profiles(profiles_dir: str | os.PathLike | None = None) -> dict[str, Path]:
    """Discover available profiles by name. Earlier roots shadow later ones."""
    found: dict[str, Path] = {}
    for root in profiles_roots(profiles_dir):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.name not in found and _looks_like_profile(child):
                found[child.name] = child
    return found


def resolve_profile(
    name: str | None = None, profiles_dir: str | os.PathLike | None = None
) -> Profile:
    """Resolve a :class:`Profile` from an explicit name/path, env, or config."""
    name = name or os.environ.get("CANAIR_PROFILE") or load_config().get("default_profile")

    # A path-like name is used directly.
    if name and (os.sep in str(name) or (Path(name).expanduser().is_absolute())):
        root = Path(name).expanduser()
        if not _looks_like_profile(root):
            raise ProfileError(f"Profile path {root} does not look like a profile directory.")
        return Profile(root.name, root)

    profiles = discover_profiles(profiles_dir)

    if name:
        if name in profiles:
            return Profile(name, profiles[name])
        avail = ", ".join(profiles) or "none"
        raise ProfileError(f"Profile '{name}' not found. Available: {avail}.")

    if len(profiles) == 1:
        only = next(iter(profiles))
        return Profile(only, profiles[only])
    if not profiles:
        raise ProfileError(
            "No vehicle profiles found. Create one with "
            "`canair profile create <name> --car-model \"...\"`, or add a bundle "
            f"under {config_dir() / 'profiles'} or {BUNDLED_PROFILES_DIR}."
        )
    raise ProfileError(
        f"Multiple profiles found ({', '.join(profiles)}). "
        "Set `default_profile` in config or pass --profile."
    )


_active: Profile | None = None


def set_active(name: str | None = None, profiles_dir: str | os.PathLike | None = None) -> Profile:
    """Resolve and memoize the active profile (called by the CLI)."""
    global _active
    _active = resolve_profile(name, profiles_dir)
    return _active


def active() -> Profile:
    """Return the active profile, resolving it lazily on first use."""
    global _active
    if _active is None:
        _active = resolve_profile()
    return _active


def config_dir_hint() -> Path:
    """User profiles directory (for help/hint messages)."""
    return config_dir() / "profiles"
