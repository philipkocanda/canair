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

Only a layered profile has a read-only layer, so a single-store profile (and any
explicit ``--dir``) keeps its unrestricted behaviour.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from canlib import capture_io


def _under(path: Path, directory: Path) -> bool:
    try:
        return path.resolve().is_relative_to(directory.resolve())
    except OSError:  # pragma: no cover - unreadable path
        return str(path).startswith(str(directory))


def read_only_files(paths: Iterable[Path], captures_dir: Path | None = None) -> list[Path]:
    """Those of ``paths`` that live outside the layer writes go to."""
    if len(capture_io.resolve_capture_layers(captures_dir)) < 2:
        return []
    write_layer = capture_io.resolve_captures_dir(captures_dir)
    return sorted({p for p in paths if not _under(p, write_layer)})


def refusal(paths: Iterable[Path], action: str, captures_dir: Path | None = None) -> str | None:
    """A ready-to-print refusal when ``paths`` reach the base layer, else None."""
    blocked = read_only_files(paths, captures_dir)
    if not blocked:
        return None

    from canlib.profile import active

    profile = active()
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
