"""Vehicle profile resolution.

A *profile* is a self-contained directory bundling one vehicle's data:

    <profile>/
      profile.yaml         profile-wide settings (car_model, init, addressing, ...)
      ecus/                per-ECU definitions — each file is the single source of
                           truth for one ECU (identity, scan_log, dtcs, pids, ...)
      vehicle_states.yaml  vehicle operating-state vocabulary
      can_buses.yaml       physical CAN bus segment vocabulary
      groups.yaml          named capture/monitor selector groups
      signals/             broadcast signal maps, one <bus>.yaml per CAN bus
      captures/            raw UDS capture files (per date) + can/ frame logs
      references/          external reference material (spreadsheets, other logs)
      dtc_log.yaml         DTC scan history (gitignored)
      out/                 generated WiCAN JSON profiles (optional)

Only ``profile.yaml`` and ``ecus/`` are required; every other member is optional
and absent in a freshly-created profile.

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

from canlib import yaml_io

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
    def meta_file(self) -> Path:
        """Profile-wide settings (car_model, init, addressing, quirks, ...)."""
        return self.root / "profile.yaml"

    @property
    def references_dir(self) -> Path:
        """External reference material (other-vehicle logs, spreadsheets)."""
        return self.root / "references"

    @property
    def dtc_log_file(self) -> Path:
        """Per-profile DTC scan history (gitignored — recorded by `canair dtc`)."""
        return self.root / "dtc_log.yaml"

    @property
    def captures_dir(self) -> Path:
        return self.root / "captures"

    @property
    def can_dir(self) -> Path:
        """Native raw-CAN frame-log store (imported .blf/.asc/.csv/... logs)."""
        return self.captures_dir / "can"

    @property
    def can_index_file(self) -> Path:
        """Per-file metadata index for the raw-CAN log store."""
        return self.can_dir / "index.yaml"

    @property
    def signals_dir(self) -> Path:
        """Broadcast signal-definition sidecar (one <bus>.yaml per CAN bus)."""
        return self.root / "signals"

    @property
    def states_file(self) -> Path:
        """Path to the profile's vehicle-state vocabulary.

        The canonical name is ``vehicle_states.yaml``. For back-compatibility a
        legacy ``states.yaml`` is still honoured: when the canonical file is
        absent but a legacy one exists, the legacy path is returned (so old
        profiles keep working). New profiles are scaffolded with the canonical
        name, and writes/messages use it.
        """
        canonical = self.root / "vehicle_states.yaml"
        if canonical.exists():
            return canonical
        legacy = self.root / "states.yaml"
        if legacy.exists():
            return legacy
        return canonical

    @property
    def can_buses_file(self) -> Path:
        """Per-profile CAN bus segment vocabulary (codes for ECU can_bus:)."""
        return self.root / "can_buses.yaml"

    @property
    def groups_file(self) -> Path:
        """Per-profile capture/monitor selector groups (named saved queries)."""
        return self.root / "groups.yaml"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @cached_property
    def meta(self) -> dict:
        """Contents of <profile>/profile.yaml (car_model, init, ...), or {}."""
        if self.meta_file.exists():
            with open(self.meta_file) as f:
                return yaml_io.safe_load(f) or {}
        return {}


def _looks_like_profile(path: Path) -> bool:
    return path.is_dir() and ((path / "ecus").is_dir() or (path / "profile.yaml").exists())


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
            '`canair profile create <name> --car-model "..."`, or add a bundle '
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
