"""A resolved profile: the ``Profile`` paths, ``ProfileError`` and reading a bundle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from canair.core import yaml_io
from canair.profile.bundle import BUNDLE_MEMBERS, BundleMember


class ProfileError(Exception):
    """Raised when the active vehicle profile cannot be resolved."""


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
    # The selected variant (a variants.yaml ID, or the analysis scope all/untagged)
    # and where it came from — see canair.variants.selection.
    variant: str | None = None
    variant_source: Literal["flag", "env", "config", "alias", "only"] | None = None
    # The old profile name this profile was resolved through (an ``aliases:`` entry).
    alias: str | None = None
    # ECU revision pins as (ECU, revision, source): the global --revision flag ("flag")
    # and profile_settings.<profile>.revisions ("config"). Checked when resolved.
    revision_pins: tuple[tuple[str, str, Literal["flag", "config"]], ...] = ()
    # ECU revisions detected from the car (ECU, revision): remembered from an earlier
    # session, or settled by this one's probe. Outranked by a pin and by "only one".
    revision_detections: tuple[tuple[str, str], ...] = ()

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
    def actions_file(self) -> Path:
        """Profile-defined, guarded actions (``canair action``)."""
        return self.root / "actions.yaml"

    @property
    def variants_file(self) -> Path:
        """The variants of this car (battery, facelift, market) and their metadata."""
        return self.root / "variants.yaml"

    @property
    def out_dir(self) -> Path:
        return self.write_root / "out"

    @property
    def cache_key(self) -> str:
        """Identity of this profile's definitions, for per-process memoization.

        Everything a profile resolves is rooted here, and the definitions are resolved for
        the selected variant and revision pins, so all three identify what a cache entry
        was built from. Callers key on this rather than on one member's path, so a cache
        cannot be shared between two profiles (or two cars of one profile) that happen to
        agree on the member it was keyed by.
        """
        key = str(self.root)
        if self.variant:
            key += f"|variant={self.variant}"
        if self.revision_pins:
            key += "|pins=" + ",".join(f"{e}={r}" for e, r, _ in sorted(self.revision_pins))
        if self.revision_detections:
            key += "|detected=" + ",".join(f"{e}={r}" for e, r in sorted(self.revision_detections))
        return key

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


def read_meta(meta_file: Path) -> dict:
    """A ``profile.yaml`` as written, or {} when absent."""
    if meta_file.exists():
        with open(meta_file) as f:
            return yaml_io.safe_load(f) or {}
    return {}


def looks_like_profile(path: Path) -> bool:
    """True when ``path`` holds any of the bundle's required members."""
    if not path.is_dir():
        return False
    return any(
        (path / m.name).is_dir() if m.kind == "dir" else (path / m.name).exists()
        for m in BUNDLE_MEMBERS
        if m.required
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
