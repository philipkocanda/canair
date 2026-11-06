"""Build provenance — which code the running ``canair`` is, and where it came from.

The bare package version cannot tell builds apart: a clean release, a clone on
``main`` with local edits and a wheel built from a feature branch all call
themselves ``1.15.0``. This module tells them apart, for two kinds of copy:

* **A run from source** (``uv run canair`` in a clone): the facts are read live
  from that checkout's git.
* **An installed copy** (``uv tool install .``, a wheel): the facts were read at
  *build* time by the hatchling hook ``hatch_build.py``, which writes a stamp
  (:data:`STAMP_FILE`) into the wheel with the commit, branch, release tag,
  dirty flag, build time and builder — a GitHub Actions run, or a local build.

:func:`full_version` appends the branch and short commit as a PEP 440-shaped
local version segment, unless the code is exactly a release (a clean tree at the
``v<version>`` tag)::

    1.15.0                       a release (or no git facts available)
    1.15.0+main.343b244          code on branch `main`, at commit 343b244
    1.15.0+main.343b244.dirty    … with uncommitted changes to tracked files
    1.15.0+343b244               … detached HEAD (no branch to name)

:func:`full_version` is what canair **shows** (``--version``, ``canair status``,
``canair update``) and what it **records** into captures, so a reading can be
traced back to the commit that produced it. :func:`build_summary` says where the
copy came from (``from source``, ``CI build …``, ``local build …``), for display
only. ``canair.__version__`` stays the pure package version — release
comparisons (``canair update``) compare on that.

Everything degrades to the plain package version: no ``git`` binary, not a
checkout, an unreadable repo, or a wheel without a stamp all return
``__version__`` unchanged — provenance is a bonus, never a failure mode.

The git facts of a checkout come from one ``git status --porcelain=v2 --branch``
call per clone (plus one ``git describe`` for a clean tree), cached for the life
of the process (a long ``--save`` session shells out once, not once per capture)
and read with ``--no-optional-locks`` so merely asking canair its version never
writes to the user's repo.

This module also owns the low-level ``git`` runner and HEAD describer that
``canair update`` uses, so there is one place that knows how to ask a clone about
itself. ``hatch_build.py`` loads this file **by path**, outside any installed
canair, so its module-level imports must stay within the standard library.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
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
    ran (and scanning for them costs more than it tells us). ``tag`` is the tag
    pointing exactly at HEAD, when one was looked up.
    """

    branch: str | None
    commit: str
    dirty: bool
    tag: str | None = None

    def is_release_of(self, version: str) -> bool:
        """True when this is a clean tree at ``version``'s release tag (``v<version>``)."""
        return not self.dirty and self.tag == f"v{version}"

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


def _head(clone: Path) -> tuple[str | None, str, bool] | None:
    """``clone``'s branch, full commit and dirty state from one ``git status`` call."""
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
            commit = oid if oid and oid != _UNBORN else None
        elif line.startswith(_HEAD_PREFIX):
            head = line[len(_HEAD_PREFIX) :].strip()
            branch = None if head in ("", _DETACHED) else head
        elif line and not line.startswith("#"):
            # Any non-header line is a changed tracked path.
            dirty = True

    if commit is None:
        return None
    return branch, commit, dirty


@cache
def git_build(clone: Path) -> GitBuild | None:
    """Read ``clone``'s branch, short commit and dirty state; ``None`` if it can't.

    One ``git status --porcelain=v2 --branch`` call answers all three. Cached per
    clone path for the process lifetime: the facts are stable for any single
    canair invocation, and this sits on the ``--version``/capture-save path.
    """
    head = _head(clone)
    if head is None:
        return None
    branch, commit, dirty = head
    return GitBuild(branch=branch, commit=commit[:SHORT_SHA_LEN], dirty=dirty)


@cache
def exact_tag(clone: Path) -> str | None:
    """The tag pointing exactly at ``clone``'s HEAD, or ``None`` (one cached git call)."""
    result = run_git(clone, "describe", "--tags", "--exact-match", "HEAD")
    tag = result.stdout.strip() if result.returncode == 0 else ""
    return tag or None


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
    return f"detached at {exact_tag(clone) or build.commit}"


# --- The build stamp -------------------------------------------------------

#: The stamp's file name inside the installed package directory (``canair/``).
STAMP_FILE = "_build_info.json"
#: Bumped when a stamp field changes meaning; a reader ignores a stamp it does not know.
STAMP_SCHEMA = 1


@dataclass(frozen=True)
class BuildStamp:
    """What the build hook recorded about the wheel an installed canair came from.

    ``builder`` is ``"ci"`` (a CI job built it) or ``"local"`` (anything else).
    The git fields are ``None`` when the build had no checkout to read, e.g. a
    wheel built from an sdist. ``commit`` is the full hash.
    """

    builder: str
    built_at: str | None = None
    commit: str | None = None
    branch: str | None = None
    tag: str | None = None
    dirty: bool | None = None
    ci_run_url: str | None = None

    def git(self) -> GitBuild | None:
        """The stamp's git facts in :class:`GitBuild` form, if it has any."""
        if not self.commit:
            return None
        return GitBuild(
            branch=self.branch,
            commit=self.commit[:SHORT_SHA_LEN],
            dirty=bool(self.dirty),
            tag=self.tag,
        )


def _ci_run(environ: Mapping[str, str]) -> tuple[bool, str | None]:
    """Whether a CI job is building, and the URL of its run when the CI names one."""
    if environ.get("GITHUB_ACTIONS") == "true":
        server = environ.get("GITHUB_SERVER_URL") or "https://github.com"
        repo = environ.get("GITHUB_REPOSITORY")
        run_id = environ.get("GITHUB_RUN_ID")
        return True, f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None
    return environ.get("CI", "").lower() in ("1", "true", "yes"), None


def _built_at(environ: Mapping[str, str], now: datetime | None) -> str:
    """The build time in UTC, honouring ``SOURCE_DATE_EPOCH`` for reproducible builds."""
    epoch = environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.isdigit():
        moment = datetime.fromtimestamp(int(epoch), UTC)
    else:
        moment = now or datetime.now(UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_stamp(
    root: Path, environ: Mapping[str, str], now: datetime | None = None
) -> dict[str, object]:
    """The stamp for a wheel being built from ``root`` (called by ``hatch_build.py``)."""
    ci, run_url = _ci_run(environ)
    head = _head(root)
    branch, commit, dirty = head if head is not None else (None, None, None)
    tag = exact_tag(root) if head is not None else None
    return {
        "schema": STAMP_SCHEMA,
        "builder": "ci" if ci else "local",
        "built_at": _built_at(environ, now),
        "commit": commit,
        "branch": branch,
        "tag": tag,
        "dirty": dirty,
        "ci_run_url": run_url,
    }


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_stamp(text: str) -> BuildStamp | None:
    """Read a stamp written by :func:`collect_stamp`; ``None`` if it is unusable."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("schema") != STAMP_SCHEMA:
        return None
    builder = data.get("builder")
    if not isinstance(builder, str) or builder not in ("ci", "local"):
        return None
    dirty = data.get("dirty")
    return BuildStamp(
        builder=builder,
        built_at=_optional_str(data.get("built_at")),
        commit=_optional_str(data.get("commit")),
        branch=_optional_str(data.get("branch")),
        tag=_optional_str(data.get("tag")),
        dirty=dirty if isinstance(dirty, bool) else None,
        ci_run_url=_optional_str(data.get("ci_run_url")),
    )


@cache
def _read_stamp(path: Path) -> BuildStamp | None:
    try:
        return parse_stamp(path.read_text(encoding="utf-8"))
    except OSError:
        return None


# --- The running build ----------------------------------------------------


def running_clone() -> Path | None:
    """The git working tree the executing ``canair`` package lives in, if any.

    ``None`` for an installed copy (a ``uv tool install`` snapshot or a
    site-packages install), which carries no checkout to describe.
    """
    # deferred: hatch_build.py loads this module by path without canair installed, so its top
    # level imports only the standard library (and context imports this module)
    from canair.install.context import running_origin, running_package_dir

    if running_origin() != "repo":
        return None
    return running_package_dir().parent


def running_stamp() -> BuildStamp | None:
    """The build stamp of the running copy; ``None`` from source or for an unstamped wheel.

    A run from source never reads a stamp, so a stray file in the tree cannot
    describe code it did not build.
    """
    # deferred: hatch_build.py loads this module by path without canair installed, so its top
    # level imports only the standard library (and context imports this module)
    from canair.install.context import running_package_dir

    if running_clone() is not None:
        return None
    return _read_stamp(running_package_dir() / STAMP_FILE)


def running_build() -> GitBuild | None:
    """:class:`GitBuild` for the running code: from its checkout, else from its stamp."""
    clone = running_clone()
    if clone is None:
        stamp = running_stamp()
        return stamp.git() if stamp is not None else None
    build = git_build(clone)
    if build is None or build.dirty:
        return build
    return replace(build, tag=exact_tag(clone))


def full_version() -> str:
    """The version canair shows and records: package version + build provenance.

    Falls back to a bare ``canair.__version__`` when there are no git facts, and
    when the code is exactly the release (a clean tree at the ``v<version>``
    tag). When the package version already carries a local segment (the
    ``0+unknown`` sentinel of an uninstalled source tree) the provenance is
    appended with a ``.`` — a version may hold only one ``+``.
    """
    # deferred: hatch_build.py loads this module by path without canair installed, so its top
    # level imports only the standard library
    from canair import __version__

    build = running_build()
    if build is None or build.is_release_of(__version__):
        return __version__
    segment = build.local_segment()
    separator = "." if "+" in __version__ else "+"
    return f"{__version__}{separator}{segment}"


def _display_time(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def build_origin() -> str:
    """Where the running copy came from: ``source``, ``ci``, ``local`` or ``unknown``."""
    if running_clone() is not None:
        return "source"
    stamp = running_stamp()
    return stamp.builder if stamp is not None else "unknown"


def build_summary() -> str:
    """One phrase naming where the running copy came from, e.g. ``CI build 343b244, …``."""
    origin = build_origin()
    if origin == "source":
        return "from source"
    stamp = running_stamp()
    if stamp is None:
        return "installed, no build info"
    text = "CI build" if stamp.builder == "ci" else "local build"
    if stamp.commit:
        text += f" {stamp.commit[:SHORT_SHA_LEN]}"
    if stamp.built_at:
        text += f", {_display_time(stamp.built_at)}"
    return text


def build_details() -> dict[str, object]:
    """The structured form of :func:`build_summary`, for ``--json`` output."""
    stamp = running_stamp()
    build = running_build()
    return {
        "origin": build_origin(),
        "commit": (stamp.commit if stamp else None) or (build.commit if build else None),
        "branch": build.branch if build else None,
        "tag": build.tag if build else None,
        "dirty": build.dirty if build else None,
        "built_at": stamp.built_at if stamp else None,
        "ci_run_url": stamp.ci_run_url if stamp else None,
    }


def clear_cache() -> None:
    """Forget the cached git facts and stamps (tests; they don't change at runtime)."""
    git_build.cache_clear()
    exact_tag.cache_clear()
    _read_stamp.cache_clear()
