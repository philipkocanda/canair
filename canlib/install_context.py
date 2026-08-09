"""Detect *how* the running ``canair`` was invoked, and whether the installed
tool copy is in sync with its source clone.

canair is typically used two ways from the same machine:

* ``uv run canair …`` from the repo root — runs the code in the **repo working
  tree** (whatever is currently checked out / edited).
* a bare ``canair …`` — runs the copy created by ``uv tool install .``, which
  lives in uv's tool venv (``~/.local/share/uv/tools/canair/…``) and is a
  *snapshot* taken at install time.

Those two can drift: you edit the repo (and its ``pyproject.toml`` version), but
the ``uv tool install`` copy still reports the version it was last installed at.
A bare ``canair`` then silently runs stale code with a stale version, while
``uv run canair`` runs the new one. This module surfaces that so ``canair
update`` (and ``canair status``) can warn about it.

It answers three questions, all without touching the network:

1. **Which copy is executing now** — the repo working tree, the uv-tool copy, or
   some other (pip/editable/site-packages) install (:func:`running_origin`).
2. **What version the uv-tool copy is pinned at** — read from its installed
   package metadata, independent of what is running (:func:`installed_tool_version`).
3. **What version the source clone would install as** — read from the clone's
   ``pyproject.toml`` (:func:`clone_version`).

:func:`describe` bundles all three into one dict, adds the running code's git
build provenance (:mod:`canlib.build_info`), and computes whether the tool copy is
**out of sync** with the clone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .profile import BundleMember

# --- Locations -------------------------------------------------------------


def uv_tools_root() -> Path:
    """Root directory uv stores installed tools under (``$XDG_DATA_HOME`` aware)."""
    data_home = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(data_home) / "uv" / "tools" / "canair"


def _resolve(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path.resolve()
    except OSError:
        return path


def running_package_dir() -> Path:
    """Directory the currently-imported ``canlib`` package lives in."""
    from . import constants

    return Path(constants.PACKAGE_DIR)


def uv_tool_site_packages() -> Path | None:
    """The uv-tool venv's ``site-packages`` containing its ``canlib`` copy, if any.

    Globs the tool venv's ``lib/python*/site-packages`` (there is exactly one
    Python version per venv). Returns ``None`` when no uv-tool install exists.
    """
    root = uv_tools_root()
    for candidate in sorted(root.glob("lib/python*/site-packages")):
        if (candidate / "canlib").is_dir():
            return candidate
    return None


def uv_receipt_directory() -> Path | None:
    """The source directory uv's tool receipt records for ``uv tool install .``."""
    receipt = uv_tools_root() / "uv-receipt.toml"
    try:
        import tomllib

        parsed = tomllib.loads(receipt.read_text())
    except (OSError, ValueError, ModuleNotFoundError):
        return None
    for req in parsed.get("tool", {}).get("requirements", []):
        directory = req.get("directory") if isinstance(req, dict) else None
        if directory:
            return Path(directory)
    return None


def source_clone() -> Path | None:
    """Locate the source git clone canair was installed from.

    Preference order:
      1. uv's tool receipt (``~/.local/share/uv/tools/canair/uv-receipt.toml``),
         which records the ``directory`` a ``uv tool install .`` came from.
      2. The package's own repo root (``canlib.constants.SCRIPT_DIR``), covering
         editable / ``uv run`` dev checkouts.
    Returns the first that is an existing git working tree, else ``None``.
    """
    candidates: list[Path] = []
    receipt = uv_receipt_directory()
    if receipt is not None:
        candidates.append(receipt)
    try:
        from .constants import SCRIPT_DIR

        candidates.append(Path(SCRIPT_DIR))
    except Exception:
        pass
    for path in candidates:
        if (path / ".git").exists():
            return path
    return None


def running_origin() -> str:
    """Classify where the executing ``canlib`` code came from.

    Returns one of:

    * ``"repo"`` — running from the source git working tree (``uv run`` /
      editable dev checkout); the package dir sits inside a git clone.
    * ``"uv-tool"`` — running the ``uv tool install`` snapshot copy.
    * ``"other"`` — some other install (pip into a venv, site-packages, …) that
      is neither a git clone nor the uv-tool copy.
    """
    pkg = _resolve(running_package_dir())
    tool_sp = _resolve(uv_tool_site_packages())
    if tool_sp is not None and pkg is not None and pkg == _resolve(tool_sp / "canlib"):
        return "uv-tool"
    if pkg is not None and (pkg.parent / ".git").exists():
        return "repo"
    return "other"


# --- Versions --------------------------------------------------------------


def running_version() -> str:
    """Version of the package that is currently executing.

    The **provenance** version (:func:`canlib.build_info.full_version`): the
    package version, plus the checkout's branch/commit when running from a git
    working tree. Version *comparisons* (the out-of-sync verdict below, the
    release check in ``canair update``) deliberately use the pure versions
    instead — a local segment says nothing about ordering.
    """
    from .build_info import full_version

    return full_version()


def _read_pyproject_version(root: Path) -> str | None:
    """Parse ``version = "…"`` from a clone's ``pyproject.toml`` (``[project]``)."""
    pyproject = root / "pyproject.toml"
    try:
        import tomllib

        data = tomllib.loads(pyproject.read_text())
    except (OSError, ValueError, ModuleNotFoundError):
        return None
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) and version else None


def clone_version(clone: Path | None) -> str | None:
    """Version the source clone would install as (its ``pyproject.toml``)."""
    if clone is None:
        return None
    return _read_pyproject_version(clone)


def installed_tool_version() -> str | None:
    """Version recorded in the uv-tool copy's installed package metadata.

    Reads the ``canair-*.dist-info`` under the tool venv's ``site-packages`` —
    independent of what is currently running — so we can compare the pinned
    tool version against the live source clone even from a ``uv run`` invocation.
    """
    sp = uv_tool_site_packages()
    if sp is None:
        return None
    for dist in sorted(sp.glob("canair-*.dist-info")):
        name = dist.name
        # canair-<version>.dist-info
        version = name[len("canair-") : -len(".dist-info")]
        if version:
            return version
    return None


# --- Summary ---------------------------------------------------------------


def bundled_profiles_are_snapshot() -> bool:
    """True when the running code is the ``uv tool install`` snapshot copy.

    The repo-bundled ``profiles/`` directory is resolved relative to the running
    ``canlib`` package (``BUNDLED_PROFILES_DIR``), so a bare ``canair`` reads the
    frozen copy baked into the uv-tool venv at install time — edits to the git
    checkout's ``profiles/`` are invisible until a reinstall. ``uv run canair``
    from the repo runs the working tree, so its bundled profiles are live.

    Contributor-facing commands use this to warn that repo profile edits won't be
    picked up.
    """
    return running_origin() == "uv-tool"


# --- Write targets ---------------------------------------------------------


def installed_snapshot_kind(path: Path) -> str | None:
    """Name the install kind if ``path`` lives in a package snapshot, else None.

    A bare ``canair`` (a ``uv tool`` / ``pipx`` / ``pip`` install) resolves the
    active profile from the package copy in ``site-packages`` — a **frozen
    snapshot** taken at install time. Writes to it appear to succeed and are
    destroyed by the next reinstall; and because the snapshot can be arbitrarily
    behind the working checkout (while simultaneously *ahead* on captures written
    by bare ``--save`` runs), contributing from it silently reverts upstream work.

    Returns e.g. ``"uv tool"`` / ``"pipx"`` / ``"installed package"``, or ``None``
    for a profile in a normal writable location.
    """
    try:
        parts = path.resolve().parts
    except OSError:
        parts = path.parts
    if not ({"site-packages", "dist-packages"} & set(parts)):
        return None
    if "uv" in parts and "tools" in parts:
        return "uv tool"
    if "pipx" in parts:
        return "pipx"
    return "installed package"


def snapshot_write_note(path: Path) -> str | None:
    """Warn that ``path`` is inside an install snapshot, and name the one fix.

    Every command that writes into a profile calls this on the file it just wrote
    (``canlib.captures.saved_banner``, the definition editors' confirmations), so
    the warning appears where the data actually landed rather than only at
    contribution time. ``None`` when the path is in a writable location, so a
    caller can simply skip a falsy note.

    The remedy is the one this process can verify: if the source clone is
    locatable, pointing the profile search path at it keeps the data inside a
    checkout that survives a reinstall (and can be contributed from). Otherwise
    the profile has to be copied somewhere writable. Plain, non-scolding wording:
    where the data went, why that is a dead end, then the single command.
    """
    kind = installed_snapshot_kind(path)
    if kind is None:
        return None
    lines = [
        f"warning: this wrote into the {kind} install snapshot, outside any checkout:",
        f"  {path}",
        "  A reinstall (canair update, uv tool install --reinstall) replaces that",
        "  directory, taking this data with it. Fix it once:",
    ]
    clone = source_clone()
    if clone is not None:
        lines.append(f"    canair config set profiles_dir {clone / 'profiles'}")
    else:
        lines.append("    canair profile adopt <name>    # copy it to ~/.config/canair/profiles/")
    return "\n".join(lines)


@dataclass(frozen=True)
class SnapshotRisk:
    """Profile data in the install snapshot that a reinstall would destroy.

    ``missing`` files exist only in the snapshot (captures written by a bare
    ``canair … --save``, ECUs registered by a bare ``discover --register``);
    ``differing`` ones exist in both but were edited in the snapshot. Paths are
    relative to the profile root so they read as ``captures/2026-08-05.json``.
    """

    name: str
    root: Path
    missing: list[Path]
    differing: list[Path]

    @property
    def files(self) -> list[Path]:
        return sorted(self.missing + self.differing)


def snapshot_profile_risks(clone: Path | None) -> list[SnapshotRisk]:
    """Profile data that would be lost by reinstalling over the bundled snapshot.

    A reinstall replaces the whole package directory, so anything written into a
    snapshot profile is gone. The wheel is built from the clone, which makes the
    clone's ``profiles/`` the right reference for "would this file come back?" —
    a snapshot file absent there (or differing from it) is unrecoverable.

    Returns an empty list when the bundled profiles are not a snapshot (a dev
    checkout, where the profiles *are* the clone) or when there is no clone to
    compare against — the caller cannot make a claim it can't substantiate.
    """
    from .profile import BUNDLE_MEMBERS, BUNDLED_PROFILES_DIR, discover_profiles

    if clone is None or not installed_snapshot_kind(BUNDLED_PROFILES_DIR):
        return []

    reference = clone / "profiles"
    tracked = [m for m in BUNDLE_MEMBERS if m.role != "generated"]
    risks: list[SnapshotRisk] = []
    for name, root in sorted(discover_profiles(BUNDLED_PROFILES_DIR).items()):
        missing: list[Path] = []
        differing: list[Path] = []
        for member in tracked:
            for path in _member_files(root, member):
                rel = path.relative_to(root)
                other = reference / name / rel
                if not other.is_file():
                    missing.append(rel)
                elif not _same_bytes(path, other):
                    differing.append(rel)
        if missing or differing:
            risks.append(SnapshotRisk(name, root, sorted(missing), sorted(differing)))
    return risks


def _member_files(root: Path, member: BundleMember) -> list[Path]:
    """Every file a bundle member contributes, honouring its aliases."""
    for candidate in (member.name, *member.aliases):
        target = root / candidate
        if member.kind == "dir" and target.is_dir():
            return [p for p in sorted(target.rglob("*")) if p.is_file()]
        if member.kind != "dir" and target.is_file():
            return [target]
    return []


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def describe(clone: Path | None) -> dict:
    """Bundle the running/installed/clone facts and the sync verdict.

    ``clone`` is the located source clone (from ``canair update``'s
    ``_find_clone_dir``); pass ``None`` when none was found.

    The ``out_of_sync`` flag is True when a uv-tool copy exists whose pinned
    version differs from the source clone's ``pyproject.toml`` version — i.e. a
    bare ``canair`` would run different code than ``uv run canair`` in the repo.

    ``running_build`` describes the git checkout the running code came from
    (branch/commit/dirty), or is ``None`` for an installed copy — the structured
    form of the provenance suffix in ``running_version``.
    """
    from .build_info import running_build

    origin = running_origin()
    tool_version = installed_tool_version()
    src_version = clone_version(clone)

    out_of_sync = bool(
        tool_version is not None and src_version is not None and tool_version != src_version
    )

    build = running_build()
    tool_sp = uv_tool_site_packages()
    return {
        "running_origin": origin,
        "running_version": running_version(),
        "running_package_dir": str(running_package_dir()),
        "running_build": (
            {"branch": build.branch, "commit": build.commit, "dirty": build.dirty}
            if build is not None
            else None
        ),
        "clone_dir": str(clone) if clone else None,
        "clone_version": src_version,
        "tool_install_dir": str(tool_sp.parent.parent.parent) if tool_sp else None,
        "tool_version": tool_version,
        "out_of_sync": out_of_sync,
    }
