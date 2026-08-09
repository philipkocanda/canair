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

A same-named user bundle that declares ``extends:`` **layers** onto the bundled
one instead of shadowing it (:func:`profile_layers`): definitions keep resolving
from the read-only base while captures read from every layer and are written to
the user's. That is how a shipped profile can accumulate your recordings without
you forking it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Literal

from canlib import yaml_io

from .config import config_dir, load_config
from .constants import BUNDLED_PROFILES_DIR


class ProfileError(Exception):
    """Raised when the active vehicle profile cannot be resolved."""


# ── the bundle, declared once ─────────────────────────────────────────────
# Every member of a profile directory is described here, and everything that
# needs to reason about "what is a profile made of" reads this table rather than
# repeating the names: Profile's path properties, `canair profile show`,
# `canair contribute` (what to copy / what may only grow), the blind-test strip,
# and profile discovery. A member added here is picked up by all of them at
# once — a member added to only *some* of those lists is exactly how
# `groups.yaml` came to be silently dropped from contributions.
#
# `role` decides how a contribution treats the member:
#   definition — curated, hand-authored: always contributed, and normally only
#                *grows* (so a diff that removes upstream lines is suspicious).
#   evidence   — recorded measurements: contributed opt-in (`--captures`),
#                append-only, unioned rather than replaced.
#   external   — third-party material of uncertain licence: never contributed.
#   local      — machine-local bookkeeping (gitignored): never contributed.
#   generated  — reproducible output: never contributed, regenerate instead.
MemberRole = Literal["definition", "evidence", "external", "local", "generated"]
MemberKind = Literal["file", "dir"]


@dataclass(frozen=True)
class BundleMember:
    """One member of a profile bundle (a file or directory under its root)."""

    name: str
    kind: MemberKind
    role: MemberRole
    # Profile property exposing this member's path (None for a legacy alias).
    attr: str | None = None
    # Column label in `canair profile show` (defaults to the name sans suffix).
    label: str = ""
    # A profile is recognisable by its required members (see looks_like_profile).
    required: bool = False
    # Withheld from the blind-rediscovery sandbox: it would leak the answers.
    blind_strip: bool = False
    # Superseded names still honoured when present (e.g. states.yaml).
    aliases: tuple[str, ...] = ()

    @property
    def contributable(self) -> bool:
        """Whether `canair contribute` ships this member (evidence: opt-in)."""
        return self.role in ("definition", "evidence")

    @property
    def display_label(self) -> str:
        return self.label or self.name.removesuffix(".yaml")


BUNDLE_MEMBERS: tuple[BundleMember, ...] = (
    BundleMember(
        "profile.yaml", "file", "definition", attr="meta_file", label="profile", required=True
    ),
    BundleMember("ecus", "dir", "definition", attr="ecus_dir", required=True),
    BundleMember(
        "vehicle_states.yaml",
        "file",
        "definition",
        attr="states_file",
        label="states",
        aliases=("states.yaml",),
    ),
    BundleMember("can_buses.yaml", "file", "definition", attr="can_buses_file"),
    BundleMember("groups.yaml", "file", "definition", attr="groups_file"),
    # A signal map names and scales broadcast fields — the answers a blind run
    # is meant to rediscover — so it ships upstream but never into the sandbox.
    BundleMember("signals", "dir", "definition", attr="signals_dir", blind_strip=True),
    BundleMember("captures", "dir", "evidence", attr="captures_dir"),
    BundleMember("references", "dir", "external", attr="references_dir", blind_strip=True),
    BundleMember("dtc_log.yaml", "file", "local", attr="dtc_log_file", blind_strip=True),
    BundleMember("out", "dir", "generated", attr="out_dir", blind_strip=True),
)


def members_by_role(*roles: MemberRole) -> tuple[BundleMember, ...]:
    """Bundle members with any of ``roles``, in declaration order."""
    return tuple(m for m in BUNDLE_MEMBERS if m.role in roles)


def member_names(members: tuple[BundleMember, ...]) -> tuple[str, ...]:
    """Flatten members to on-disk names, each followed by its legacy aliases."""
    return tuple(n for m in members for n in (m.name, *m.aliases))


@dataclass(frozen=True)
class Profile:
    """A resolved vehicle profile rooted at a directory.

    ``root`` holds the definitions. ``overlays`` are same-named bundles from
    *higher*-precedence roots that declared ``extends:`` and so **layer** onto it
    instead of shadowing it (least specific first) — see :func:`profile_layers`.
    Recorded evidence then reads from every layer and writes to the most specific
    one, which is what lets a read-only bundled profile accumulate your captures.
    """

    name: str
    root: Path
    overlays: tuple[Path, ...] = ()

    @property
    def layered(self) -> bool:
        """Whether an overlay sits on top of this profile's definitions."""
        return bool(self.overlays)

    @property
    def write_root(self) -> Path:
        """Where this profile's *recorded* members are written.

        The most specific overlay when layered, else the root. Definitions stay
        on :attr:`root` — they do not overlay in this slice, and an edit against a
        layered profile is refused by :func:`require_writable_definitions`.
        """
        return self.overlays[-1] if self.overlays else self.root

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
        return self.write_root / "dtc_log.yaml"

    @property
    def captures_dir(self) -> Path:
        """Where a new capture is written — the overlay's store when layered.

        Reads must go through :attr:`capture_layers` instead, so history recorded
        in the base is not lost the moment a layer appears.
        """
        return self.write_root / "captures"

    @property
    def capture_layers(self) -> list[Path]:
        """Every captures directory to read, least specific (the base) first."""
        return [self.root / "captures", *(o / "captures" for o in self.overlays)]

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
        return self.write_root / "out"

    @property
    def cache_key(self) -> str:
        """Identity of this profile's definitions, for per-process memoization.

        Everything a profile resolves is rooted here, so the root identifies the
        definitions a cache entry was built from. Callers key on this rather than
        on one member's path, so a cache cannot be shared between two profiles
        that happen to agree on the member it was keyed by.
        """
        return str(self.root)

    @cached_property
    def meta(self) -> dict:
        """Contents of <profile>/profile.yaml (car_model, init, ...), or {}."""
        if self.meta_file.exists():
            with open(self.meta_file) as f:
                return yaml_io.safe_load(f) or {}
        return {}

    def member_path(self, member: BundleMember) -> Path:
        """Resolve a bundle member to this profile's path for it.

        Goes through the member's declared property (so ``states_file``'s legacy
        fallback still applies) and falls back to the plain name.
        """
        if member.attr:
            return getattr(self, member.attr)
        return self.root / member.name

    def member_exists(self, member: BundleMember) -> bool:
        path = self.member_path(member)
        return path.is_dir() if member.kind == "dir" else path.exists()


def looks_like_profile(path: Path) -> bool:
    """True when ``path`` holds any of the bundle's required members."""
    if not path.is_dir():
        return False
    return any(
        (path / m.name).is_dir() if m.kind == "dir" else (path / m.name).exists()
        for m in BUNDLE_MEMBERS
        if m.required
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


def profile_layers(name: str, profiles_dir: str | os.PathLike | None = None) -> list[Path]:
    """Bundles contributing to ``name``, most specific first.

    Discovery normally stops at the first root holding ``name`` — a user profile
    *shadows* a bundled one. A bundle that declares ``extends:`` opts out of that:
    it is a layer, so the walk continues to the bundle it layers onto. This is how
    ``--profile ioniq-2017`` keeps decoding against the read-only bundled
    definitions while transparently including the captures you recorded.
    """
    layers: list[Path] = []
    for root in profiles_roots(profiles_dir):
        if not root.is_dir():
            continue
        candidate = root / name
        if not looks_like_profile(candidate):
            continue
        layers.append(candidate)
        if extends_target(candidate) is None:
            break
    return layers


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:  # pragma: no cover - unreadable path
        return a == b


def _from_layers(name: str, layers: list[Path]) -> Profile:
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
        if target != name:
            raise ProfileError(
                f"Profile '{name}' at {overlay} declares `extends: {target}`. "
                "Layering onto a differently-named profile is not supported yet "
                "(see plans/2026-07-30-profile-variant-inheritance.md) — an overlay "
                "must extend its own name."
            )
    return Profile(name, base, tuple(reversed(layers[:-1])))


def resolve_profile(
    name: str | None = None, profiles_dir: str | os.PathLike | None = None
) -> Profile:
    """Resolve a :class:`Profile` from an explicit name/path, env, or config."""
    name = name or os.environ.get("CANAIR_PROFILE") or load_config().get("default_profile")

    # A path-like name is used directly.
    if name and (os.sep in str(name) or (Path(name).expanduser().is_absolute())):
        root = Path(name).expanduser()
        if not looks_like_profile(root):
            raise ProfileError(f"Profile path {root} does not look like a profile directory.")
        if extends_target(root) is None:
            return Profile(root.name, root)
        # An explicit path to an overlay still needs its base, or nothing decodes.
        below = [p for p in profile_layers(root.name, profiles_dir) if not _same_path(p, root)]
        return _from_layers(root.name, [root, *below])

    profiles = discover_profiles(profiles_dir)

    if name:
        if name in profiles:
            return _from_layers(name, profile_layers(name, profiles_dir))
        avail = ", ".join(profiles) or "none"
        raise ProfileError(f"Profile '{name}' not found. Available: {avail}.")

    if len(profiles) == 1:
        only = next(iter(profiles))
        return _from_layers(only, profile_layers(only, profiles_dir))
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


def profile_for_path(path: Path | str) -> Profile:
    """The profile that owns ``path`` — its root, its ``ecus/`` dir, or a file inside.

    Walks up to the nearest directory that looks like a profile bundle, so a
    caller holding only a file path can resolve that file's *own* profile rather
    than whichever one happens to be active. When nothing on the way up looks
    like a bundle (a scratch file outside any profile), the nearest plausible
    root is used anyway: its vocabulary files are simply absent, which is what
    the callers want — never a silent fall back to the active profile.
    """
    path = Path(path).resolve()
    candidates = [path, *path.parents] if path.is_dir() else list(path.parents)
    for candidate in candidates:
        if looks_like_profile(candidate):
            return Profile(candidate.name, candidate)
    root = path.parent if path.is_dir() else path.parent.parent
    return Profile(root.name, root)


_active: Profile | None = None


def set_active(name: str | None = None, profiles_dir: str | os.PathLike | None = None) -> Profile:
    """Resolve and memoize the active profile (called by the CLI).

    Drops any state cached from the previous profile's definitions, so switching
    profiles in one process can't decode against the wrong vehicle.
    """
    global _active
    previous = _active
    _active = resolve_profile(name, profiles_dir)
    if previous is not None and previous != _active:
        from .pids import clear_cache

        clear_cache()
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


def require_writable_definitions(profile: Profile | None = None) -> Profile:
    """Refuse a definition edit whose target is a layered profile's read-only base.

    An overlay carries recorded evidence only: definitions still resolve from the
    base bundle, which is normally the read-only shipped copy. Writing there would
    either fail or — worse, when the base is an install snapshot — appear to work
    and vanish at the next reinstall. Every authoring command calls this first, so
    the refusal names the fix instead of leaving a half-written file behind.
    """
    profile = profile or active()
    if not profile.layered:
        return profile
    raise ProfileError(
        f"'{profile.name}' is a layered profile: its definitions live in the "
        f"read-only base at {profile.root}, and {profile.write_root} only holds what "
        "you record. Definitions do not overlay yet. To edit them, either take a "
        f"full writable copy (`canair profile adopt {profile.name}`) or point canair "
        "at a checkout you can commit to (`canair config set profiles_dir "
        "<clone>/profiles`)."
    )
