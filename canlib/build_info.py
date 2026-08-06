"""Build provenance — which git checkout the running ``canair`` came from.

An installed release *is* its version: ``uv tool install`` snapshots the clone at
a release tag, so ``1.15.0`` identifies that code exactly. A run from a git
working tree (``uv run canair`` in a clone) is **not** any release — it is
whatever happens to be checked out, plus whatever is edited on top. The bare
package version silently misrepresents that: two very different builds both call
themselves ``1.15.0``.

This module closes the gap. When the executing ``canlib`` lives in a git
checkout, :func:`full_version` appends that checkout's branch and short commit as
a PEP 440-shaped local version segment::

    1.15.0                       an installed release (or no git info available)
    1.15.0+main.343b244          a clone, on branch `main`
    1.15.0+main.343b244.dirty    … with uncommitted changes to tracked files
    1.15.0+343b244               … detached HEAD (no branch to name)

:func:`full_version` is what canair **shows** (``--version``, ``canair status``,
``canair update``) and what it **records** into captures, so a reading can be
traced back to the commit that produced it. ``canlib.__version__`` stays the pure
package version — release comparisons (``canair update``) compare on that.

Everything degrades to the plain package version: no ``git`` binary, not a
checkout, an unreadable repo, or an installed snapshot all return
``__version__`` unchanged — provenance is a bonus, never a failure mode.

The git facts come from a **single** ``git status --porcelain=v2 --branch`` call
per clone, cached for the life of the process (a long ``--save`` session shells
out once, not once per capture) and read with ``--no-optional-locks`` so merely
asking canair its version never writes to the user's repo.

This module also owns the low-level ``git`` runner and HEAD describer that
``canair update`` uses, so there is one place that knows how to ask a clone about
itself.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

#: Length commits are abbreviated to (git's own default short-SHA width).
SHORT_SHA_LEN = 7

_OID_PREFIX = "# branch.oid "
_HEAD_PREFIX = "# branch.head "
#: ``branch.head`` value git reports when HEAD points at no branch.
_DETACHED = "(detached)"
#: ``branch.oid`` value git reports before the first commit exists.
_UNBORN = "(initial)"


@dataclass(frozen=True)
class GitBuild:
    """What a git working tree says about the code checked out in it.

    ``branch`` is ``None`` on a detached HEAD. ``dirty`` covers uncommitted
    changes to **tracked** files only — untracked files are ignored, since they
    don't make the recorded commit an inaccurate description of the code that
    ran (and scanning for them costs more than it tells us).
    """

    branch: str | None
    commit: str
    dirty: bool

    def local_segment(self) -> str:
        """Render as a PEP 440 local version segment, e.g. ``main.343b244.dirty``.

        The branch is sanitized to the alphanumeric-and-dot alphabet a local
        segment allows (``feature/nice-thing`` → ``feature.nice.thing``), with
        case preserved — this is provenance, so fidelity beats strict PEP 440
        normalization.
        """
        parts: list[str] = []
        branch_token = _sanitize(self.branch) if self.branch else ""
        if branch_token:
            parts.append(branch_token)
        parts.append(self.commit)
        if self.dirty:
            parts.append("dirty")
        return ".".join(parts)


def _sanitize(text: str) -> str:
    """Reduce a branch name to the alphabet a PEP 440 local segment permits."""
    return re.sub(r"[^0-9A-Za-z]+", ".", text).strip(".")


# --- Talking to git --------------------------------------------------------


def run_git(clone: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git -C <clone> <args>``, capturing output.

    Never raises: a missing ``git`` binary comes back as a non-zero result, so
    every caller can treat "git couldn't tell us" and "git said no" alike.
    """
    try:
        return subprocess.run(
            ["git", "-C", str(clone), *args],
            capture_output=True,
            text=True,
        )
    except OSError as e:
        return subprocess.CompletedProcess(
            args=list(args), returncode=127, stdout="", stderr=str(e)
        )


@cache
def git_build(clone: Path) -> GitBuild | None:
    """Read ``clone``'s branch, short commit and dirty state; ``None`` if it can't.

    One ``git status --porcelain=v2 --branch`` call answers all three. Cached per
    clone path for the process lifetime: the facts are stable for any single
    canair invocation, and this sits on the ``--version``/capture-save path.
    """
    result = run_git(
        clone,
        "--no-optional-locks",
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=no",
    )
    if result.returncode != 0:
        return None

    branch: str | None = None
    commit: str | None = None
    dirty = False
    for line in result.stdout.splitlines():
        if line.startswith(_OID_PREFIX):
            oid = line[len(_OID_PREFIX) :].strip()
            commit = oid[:SHORT_SHA_LEN] if oid and oid != _UNBORN else None
        elif line.startswith(_HEAD_PREFIX):
            head = line[len(_HEAD_PREFIX) :].strip()
            branch = None if head in ("", _DETACHED) else head
        elif line and not line.startswith("#"):
            # Any non-header line is a changed tracked path.
            dirty = True

    if commit is None:
        return None
    return GitBuild(branch=branch, commit=commit, dirty=dirty)


def is_dirty(clone: Path) -> bool:
    """True when ``clone`` has uncommitted changes to tracked files.

    Used as a safety gate before ``canair update`` checks out a release tag, so
    contributor work is never clobbered. False when git can't be queried — we
    only block on a *positive* answer.
    """
    build = git_build(clone)
    return build is not None and build.dirty


def head_label(clone: Path) -> str | None:
    """Describe ``clone``'s HEAD for display.

    The branch name when on one (e.g. ``main``), else ``detached at <tag>`` —
    preferring an exact tag name over the short commit, since that's where
    ``canair update`` leaves a clone after checking out a release. ``None`` when
    git can't be queried. The tag lookup only costs a second git call in the
    (rare) detached case.
    """
    build = git_build(clone)
    if build is None:
        return None
    if build.branch:
        return build.branch
    tag = run_git(clone, "describe", "--tags", "--exact-match", "HEAD")
    if tag.returncode == 0 and tag.stdout.strip():
        return f"detached at {tag.stdout.strip()}"
    return f"detached at {build.commit}"


# --- The running build ----------------------------------------------------


def running_clone() -> Path | None:
    """The git working tree the executing ``canlib`` package lives in, if any.

    ``None`` for an installed copy (a ``uv tool install`` snapshot or a
    site-packages install), which carries no checkout to describe.
    """
    from .install_context import running_origin, running_package_dir

    if running_origin() != "repo":
        return None
    return running_package_dir().parent


def running_build() -> GitBuild | None:
    """:class:`GitBuild` for the checkout the running code came from, if any."""
    clone = running_clone()
    return git_build(clone) if clone is not None else None


def full_version() -> str:
    """The version canair shows and records: package version + build provenance.

    Falls back to a bare ``canlib.__version__`` whenever there is no checkout to
    describe. When the package version already carries a local segment (the
    ``0+unknown`` sentinel of an uninstalled source tree) the provenance is
    appended with a ``.`` — a version may hold only one ``+``.
    """
    from . import __version__

    build = running_build()
    segment = build.local_segment() if build is not None else ""
    if not segment:
        return __version__
    separator = "." if "+" in __version__ else "+"
    return f"{__version__}{separator}{segment}"


def clear_cache() -> None:
    """Forget the cached git facts (tests; the environment doesn't change at runtime)."""
    git_build.cache_clear()
