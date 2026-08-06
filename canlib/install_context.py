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
from pathlib import Path

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
