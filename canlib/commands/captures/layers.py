"""Which capture layer a mutation is allowed to touch.

A layered profile reads captures from a read-only base bundle *and* from the
user's overlay (``canlib/profile.py`` → :func:`profile_layers`). Reads merge the
two; writes must not. Editing a base session would either fail on a read-only
directory or — when the base is an install snapshot — appear to work and be
erased by the next reinstall.

Deliberately rejected alternatives (both in
``plans/2026-08-05-profile-write-targets-and-workspace-hygiene.md``): *tombstones*
in the overlay, which push the policy into every reader, and *copy-on-write*,
which needs synthetic session ids — today a session's identity **is** its content,
which is what makes the git merge driver and the contribution overlay correct.

Only a layered profile has a read-only layer, so a single-store profile keeps its
unrestricted behaviour. An explicit ``--dir`` is judged by the profile that owns
it (:func:`owning_profile`), so naming a layered profile's base store directly
cannot sidestep the refusal.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from canlib import capture_io

if TYPE_CHECKING:
    from canlib.profile import Profile


def _under(path: Path, directory: Path) -> bool:
    try:
        return path.resolve().is_relative_to(directory.resolve())
    except OSError:  # pragma: no cover - unreadable path
        return str(path).startswith(str(directory))


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:  # pragma: no cover - unreadable path
        return a == b


def owning_profile(captures_dir: Path | None) -> Profile:
    """The profile — layered when it is one — whose captures ``captures_dir`` holds.

    ``None`` is the active profile. An explicit directory resolves to the active
    profile when it is one of its capture layers, else to the discovered profile
    of the same name whose layers include it, else to the plain bundle around it
    (:func:`canlib.profile.profile_for_path`) — never silently to the active one.
    """
    from canlib.profile import ProfileError, active, profile_for_path, resolve_profile

    if captures_dir is None:
        return active()
    candidates = []
    try:
        candidates.append(active())
    except ProfileError:
        pass
    plain = profile_for_path(captures_dir)
    if plain.name:
        try:
            candidates.append(resolve_profile(plain.name))
        except ProfileError:
            pass
    for prof in candidates:
        if any(_same(captures_dir, layer) for layer in prof.capture_layers):
            return prof
    return plain


def read_only_files(
    paths: Iterable[Path], captures_dir: Path | None = None, *, profile: Profile | None = None
) -> list[Path]:
    """Those of ``paths`` that live outside the layer writes go to.

    ``profile`` judges against that profile's layers instead of the ones
    ``captures_dir`` resolves to.
    """
    if profile is not None:
        layers, write_layer = profile.capture_layers, profile.captures_dir
    else:
        layers = capture_io.resolve_capture_layers(captures_dir)
        write_layer = capture_io.resolve_captures_dir(captures_dir)
    if len(layers) < 2:
        return []
    return sorted({p for p in paths if not _under(p, write_layer)})


def refusal(
    paths: Iterable[Path],
    action: str,
    captures_dir: Path | None = None,
    *,
    profile: Profile | None = None,
) -> str | None:
    """A ready-to-print refusal when ``paths`` reach the base layer, else None."""
    blocked = read_only_files(paths, captures_dir, profile=profile)
    if not blocked:
        return None

    from canlib.profile import active

    profile = profile or active()
    shown = "\n".join(f"  {p}" for p in blocked[:5])
    if len(blocked) > 5:
        shown += f"\n  … and {len(blocked) - 5} more"
    return (
        f"error: {len(blocked)} capture file(s) belong to the read-only base of "
        f"'{profile.name}' and cannot be {action}:\n"
        f"{shown}\n"
        f"Your layer at {profile.captures_dir} only holds what you recorded. To edit "
        f"the base's history, take a full writable copy: "
        f"`canair profile adopt {profile.name}`."
    )
