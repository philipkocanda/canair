"""Running git and gh for a contribution: the step types, the upstream, the branch, the
commit, the diff and the push.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from canair.contribute.copy import _CAPTURE_MEMBER
from canair.core.paths import GITHUB_REPO as UPSTREAM_REPO

UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}.git"


UPSTREAM_OWNER = UPSTREAM_REPO.split("/")[0]


# The upstream branch contributions are based on and opened against.
BASE_BRANCH = "main"


# Workspace modes.
MODE_FORK = "fork"  # push to a fork, cross-fork PR


MODE_DIRECT = "direct"  # push to upstream directly, same-repo PR


@dataclass
class Step:
    """Result of one git/gh invocation, for reporting and JSON output."""

    cmd: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stderr or self.stdout).strip()


@dataclass
class Preflight:
    """Whether the environment can run an automated contribution."""

    gh: str | None
    git: str | None
    authenticated: bool

    @property
    def ready(self) -> bool:
        return bool(self.gh and self.git and self.authenticated)


@dataclass
class ContributionPlan:
    """Everything the command needs to act on, once resolved."""

    profile_name: str
    branch: str
    include_captures: bool
    steps: list[Step] = field(default_factory=list)


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> Step:
    """Run a subprocess, capturing output. Never raises on non-zero exit."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=({**os.environ, **env} if env else None),
    )
    return Step(cmd=cmd, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


# Clone without pulling Git-LFS blobs (raw-CAN logs) we don't need to open a PR.
_NO_LFS_ENV = {"GIT_LFS_SKIP_SMUDGE": "1"}


def preflight() -> Preflight:
    """Probe for ``gh``/``git`` and whether ``gh`` is authenticated."""
    gh = shutil.which("gh")
    git = shutil.which("git")
    authed = False
    if gh:
        # `gh auth status` exits 0 when at least one host is logged in.
        authed = _run([gh, "auth", "status"]).ok
    return Preflight(gh=gh, git=git, authenticated=authed)


def _upstream_ref(workspace: Path, git: str) -> str:
    """The base ref to branch from — prefer upstream/main, else origin/main."""
    for ref in (f"upstream/{BASE_BRANCH}", f"origin/{BASE_BRANCH}"):
        if _run([git, "-C", str(workspace), "rev-parse", "--verify", "--quiet", ref]).ok:
            return ref
    return BASE_BRANCH


def base_reader(pre: Preflight, workspace: Path):
    """Return ``read(relpath)`` giving a file's committed (base) content, or None.

    Used by the PII scan to tell which capture sessions are *already upstream*
    (so they aren't re-flagged). ``relpath`` is repo-relative, e.g.
    ``profiles/x/captures/2026-01-01.json``.
    """
    assert pre.git
    git = pre.git
    base = _upstream_ref(workspace, git)

    def read(relpath: str) -> str | None:
        res = _run([git, "-C", str(workspace), "show", f"{base}:{relpath}"])
        return res.stdout if res.ok else None

    return read


def start_branch(pre: Preflight, workspace: Path, branch: str) -> Step:
    """Create/reset ``branch`` off the current upstream base."""
    assert pre.git
    base = _upstream_ref(workspace, pre.git)
    return _run([pre.git, "-C", str(workspace), "checkout", "-B", branch, base])


def status_entries(git: str, repo: Path, *pathspec: str) -> list[tuple[str, str]]:
    """``(XY code, repo-relative path)`` for every uncommitted change in ``repo``.

    Untracked files are listed one by one. A rename or copy is reported as its
    destination; its source path is dropped.
    """
    cmd = [git, "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"]
    if pathspec:
        cmd += ["--", *pathspec]
    fields = _run(cmd).stdout.split("\0")
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":
            i += 1  # the rename/copy source follows as its own field
        entries.append((code, path))
    return entries


def commit_profile(pre: Preflight, workspace: Path, profile_name: str, message: str) -> Step:
    """Stage the profile dir and commit. A no-op commit is reported as a Step."""
    assert pre.git
    rel = f"profiles/{profile_name}"
    add = _run([pre.git, "-C", str(workspace), "add", "--", rel])
    if not add.ok:
        return add
    return _run([pre.git, "-C", str(workspace), "commit", "-o", rel, "-m", message])


def has_changes(pre: Preflight, workspace: Path, profile_name: str) -> bool:
    """True when the profile dir has any uncommitted change (worth a PR).

    Uses ``git status --porcelain`` so freshly-copied **untracked** files count
    (``git diff`` alone ignores untracked paths).
    """
    assert pre.git
    rel = f"profiles/{profile_name}"
    status = _run([pre.git, "-C", str(workspace), "status", "--porcelain", "--", rel])
    return bool(status.stdout.strip())


def changed_definition_files(pre: Preflight, workspace: Path, profile_name: str) -> list[str]:
    """Uncommitted files under ``profiles/<name>/`` outside ``captures/``, profile-relative."""
    assert pre.git
    rel = f"profiles/{profile_name}"
    res = _run(
        [pre.git, "-C", str(workspace), "status", "--porcelain", "--untracked-files=all", "--", rel]
    )
    prefix = f"{rel}/"
    files = []
    for line in res.stdout.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path.startswith(prefix):
            path = path[len(prefix) :]
        if path and not path.startswith(f"{_CAPTURE_MEMBER}/"):
            files.append(path)
    return sorted(files)


def diff_profile(pre: Preflight, workspace: Path, profile_name: str) -> str:
    """The full diff of the copied-in profile vs the upstream base.

    Stages the profile dir into git's intent-to-add index (``--intent-to-add``)
    so freshly-copied **untracked** files show up as additions, then returns the
    unified ``git diff`` — exactly the change this contribution would submit.
    ``git diff`` alone omits untracked files, so the ``-N`` step is what makes a
    brand-new profile's files appear.
    """
    assert pre.git
    rel = f"profiles/{profile_name}"
    _run([pre.git, "-C", str(workspace), "add", "--intent-to-add", "--", rel])
    return _run([pre.git, "-C", str(workspace), "diff", "--", rel]).stdout


def push_branch(pre: Preflight, workspace: Path, branch: str) -> Step:
    assert pre.git
    return _run(
        [pre.git, "-C", str(workspace), "push", "--force-with-lease", "-u", "origin", branch]
    )
